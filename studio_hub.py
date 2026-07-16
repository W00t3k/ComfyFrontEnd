#!/usr/bin/env python3
"""Mac Mini Board — one page for every service on this box + a quiet watchdog.

A single watchdog thread is the source of truth: it polls each service on an
interval, applies a fail-grace so brief blips don't flap, auto-restarts a downed
service with backoff (then gives up quietly), and records every *state change* to
an events log. HTTP requests just serve the cached state, so the page is instant
and every viewer sees the same status.
"""

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.request
import mimetypes
from collections import deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

PORT = 8189
COMFY_DIR = Path(os.environ.get("COMFY_DIR", Path(__file__).resolve().parent))
OUTPUT_DIR = COMFY_DIR / "output"
MUSIC_OUTPUT = Path.home() / "AI/MusicStudio/output"
DATA_DIR = COMFY_DIR / "data"
STATE_FILE = DATA_DIR / "boxdash-state.json"
EVENTS_FILE = DATA_DIR / "boxdash-events.jsonl"
RUN_SH = COMFY_DIR / "run.sh"

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

# Watchdog tuning — quiet by design.
POLL_INTERVAL = 30          # seconds between rounds
FAIL_THRESHOLD = 3          # consecutive fails before a service is declared down
RESTART_MAX = 3             # restart attempts before giving up (quietly)
RESTART_BACKOFF = 20        # base seconds; grows per attempt

# Auto-restart was requested; flip to 0 for alert/log-only.
AUTO_RESTART = os.environ.get("BOXDASH_AUTORESTART", "1") == "1"


def _box_ip():
    """Best-effort primary LAN IP (SABnzbd binds this, not localhost)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


BOX_IP = _box_ip()

# Each service: how to check it (server-side, always via an IP that works from the
# box), how to link it (client builds the URL from its own hostname + port), and how
# to restart it. `restart` is an argv list or None. run.sh is idempotent: it only
# (re)starts whatever ComfyUI service is missing, so every comfy service shares it.
RUN_COMFY = ["/bin/bash", str(RUN_SH)]

SERVICES = [
    {"id": "plex", "name": "Plex", "cat": "Media", "icon": "▶", "color": "#e5a00d",
     "port": 32400, "path": "/web", "check_host": "127.0.0.1", "check_path": "/identity",
     "restart": ["open", "-a", "Plex Media Server"], "desc": "Media server"},
    {"id": "radarr", "name": "Radarr", "cat": "Media", "icon": "🎬", "color": "#ffc230",
     "port": 7878, "path": "/", "check_host": "127.0.0.1", "check_path": "/ping",
     "restart": ["open", "-a", "Radarr"], "desc": "Movie manager"},
    {"id": "sabnzbd", "name": "SABnzbd", "cat": "Downloads", "icon": "⬇", "color": "#fac026",
     "port": 8080, "path": "/", "check_host": BOX_IP, "check_path": "/",
     "restart": ["open", "-a", "SABnzbd"], "desc": "Usenet downloader"},
    {"id": "ollama", "name": "Ollama", "cat": "AI", "icon": "🦙", "color": "#7c6ff7",
     "port": 11434, "path": "/", "check_host": "127.0.0.1", "check_path": "/api/version",
     "restart": ["brew", "services", "restart", "ollama"], "desc": "Local LLM runtime"},
    {"id": "openwebui", "name": "Open WebUI", "cat": "AI", "icon": "◲", "color": "#38bdf8",
     "port": 8081, "path": "/", "check_host": "127.0.0.1", "check_path": "/",
     "restart": None, "desc": "Chat UI for local models"},
    {"id": "comfyui", "name": "ComfyUI", "cat": "AI", "icon": "⚙", "color": "#fbbf24",
     "port": 8188, "path": "/", "check_host": "127.0.0.1", "check_path": "/system_stats",
     "restart": RUN_COMFY, "desc": "Diffusion backend"},
    {"id": "images", "name": "Image Studio", "cat": "Studios", "icon": "⬡", "color": "#7c6ff7",
     "port": 8190, "path": "/", "check_host": "127.0.0.1", "check_path": "/api/server_status",
     "restart": RUN_COMFY, "desc": "Text to image"},
    {"id": "video", "name": "Video Studio", "cat": "Studios", "icon": "🎞", "color": "#f76f8e",
     "port": 8192, "path": "/", "check_host": "127.0.0.1", "check_path": "/api/server_status",
     "restart": RUN_COMFY, "desc": "Text / image to video"},
    {"id": "music", "name": "Music Studio", "cat": "Studios", "icon": "♫", "color": "#34d399",
     "port": 8191, "path": "/", "check_host": "127.0.0.1", "check_path": "/",
     "restart": RUN_COMFY, "desc": "Stems, generation, mastering"},
    {"id": "facefusion", "name": "Face Studio", "cat": "Studios", "icon": "☺", "color": "#f472b6",
     "port": 7860, "path": "/", "check_host": "127.0.0.1", "check_path": "/",
     "restart": ["/bin/bash", str(Path.home() / "AI/facefusion/run.sh")],
     "desc": "Face swap (consenting subjects)"},
    {"id": "magic", "name": "Magic", "cat": "Apps", "icon": "✦", "color": "#c084fc",
     "port": 8443, "path": "/", "scheme": "https", "check_host": "127.0.0.1",
     "check_path": "/", "restart": None, "desc": "Magic app"},
    {"id": "cockpit", "name": "Adam's Cockpit", "cat": "Apps", "icon": "❤", "color": "#fb7185",
     "port": 8787, "path": "/mdr", "check_host": "127.0.0.1", "check_path": "/mdr",
     "restart": ["/bin/bash", str(Path.home() / "Apps/adam-cockpit/run.sh")],
     "desc": "Personal health cockpit (MDR)"},
]

SERVICE_BY_ID = {s["id"]: s for s in SERVICES}

# Live watchdog state, keyed by service id.
state_lock = threading.Lock()
STATE = {
    s["id"]: {"status": "unknown", "fails": 0, "latency_ms": None,
              "since": time.time(), "restarts": 0, "last_restart": 0.0,
              "scheme": s.get("scheme", "http"), "hist": deque(maxlen=60)}
    for s in SERVICES
}
BOX = {}
_net_prev = None  # (ts, bytes_recv, bytes_sent) for network rate


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(sid, kind, detail=""):
    entry = {"ts": _now_iso(), "service": sid, "event": kind, "detail": detail}
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


_TLS_CTX = ssl.create_default_context()
_TLS_CTX.check_hostname = False
_TLS_CTX.verify_mode = ssl.CERT_NONE  # self-signed home-lab apps


def _try_scheme(scheme, svc):
    url = f"{scheme}://{svc['check_host']}:{svc['port']}{svc['check_path']}"
    ctx = _TLS_CTX if scheme == "https" else None
    start = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4, context=ctx):
            pass
        return True, int((time.time() - start) * 1000)
    except urllib.error.HTTPError:
        # 401/403/404 etc. still means the service answered.
        return True, int((time.time() - start) * 1000)
    except Exception:
        return False, None


def check_service(svc):
    """Return (ok, latency_ms, working_scheme).

    Tries the configured scheme first, then the other, so a service is reported
    correctly (and linked correctly) whether it actually speaks http or https.
    """
    preferred = svc.get("scheme", "http")
    order = [preferred, "http" if preferred == "https" else "https"]
    for scheme in order:
        ok, latency = _try_scheme(scheme, svc)
        if ok:
            return True, latency, scheme
    return False, None, preferred


def attempt_restart(svc):
    cmd = svc.get("restart")
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception as exc:
        log_event(svc["id"], "restart_error", str(exc))
        return False


def sample_cpu_and_procs(n=8):
    """One 0.5s window: per-core CPU + top procs by CPU and by memory (psutil)."""
    if psutil is None:
        return None, [], []
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)  # prime
        except Exception:
            pass
    percpu = psutil.cpu_percent(interval=0.5, percpu=True)
    ncpu = len(percpu) or 1
    rows = []
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / ncpu  # normalize to whole-machine %
            rows.append({"pid": p.pid, "name": (p.info.get("name") or "?")[:22],
                         "cpu": round(cpu, 1), "mem": p.memory_info().rss})
        except Exception:
            pass
    top_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:n]
    top_mem = sorted(rows, key=lambda r: r["mem"], reverse=True)[:n]
    return [round(c, 1) for c in percpu], top_cpu, top_mem


def read_box_stats():
    global _net_prev
    load1, load5, load15 = os.getloadavg()
    du = shutil.disk_usage("/")
    mem_total = None
    mem_free = None
    try:
        mem_total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
    except Exception:
        pass
    try:
        page = 4096
        out = subprocess.check_output(["vm_stat"]).decode()
        free = spec = inactive = 0
        for line in out.splitlines():
            if "page size of" in line:
                for tok in line.split():
                    if tok.isdigit():
                        page = int(tok)
            if line.startswith("Pages free:"):
                free = int(line.split(":")[1].strip().rstrip("."))
            elif line.startswith("Pages speculative:"):
                spec = int(line.split(":")[1].strip().rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive = int(line.split(":")[1].strip().rstrip("."))
        mem_free = (free + spec + inactive) * page
    except Exception:
        pass
    uptime_s = None
    try:
        bt = subprocess.check_output(["sysctl", "-n", "kern.boottime"]).decode()
        sec = int(bt.split("sec = ")[1].split(",")[0])
        uptime_s = int(time.time() - sec)
    except Exception:
        pass
    cpu_cores, top, top_mem = sample_cpu_and_procs()
    swap_used = swap_total = None
    net_rx = net_tx = None
    mem_detail = None
    if psutil is not None:
        try:
            sw = psutil.swap_memory()
            swap_used, swap_total = sw.used, sw.total
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            mem_detail = {k: getattr(vm, k) for k in
                          ("total", "available", "used", "free", "active",
                           "inactive", "wired") if hasattr(vm, k)}
            mem_total = vm.total
            mem_free = vm.available
        except Exception:
            pass
        try:
            now = time.time()
            io = psutil.net_io_counters()
            if _net_prev:
                dt = max(0.1, now - _net_prev[0])
                net_rx = int((io.bytes_recv - _net_prev[1]) / dt)
                net_tx = int((io.bytes_sent - _net_prev[2]) / dt)
            _net_prev = (now, io.bytes_recv, io.bytes_sent)
        except Exception:
            pass
    return {
        "load": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "cpus": os.cpu_count(),
        "cpu_cores": cpu_cores,
        "cpu_pct": round(sum(cpu_cores) / len(cpu_cores), 1) if cpu_cores else None,
        "top": top, "top_mem": top_mem,
        "disk_total": du.total, "disk_free": du.free,
        "mem_total": mem_total, "mem_free": mem_free, "mem_detail": mem_detail,
        "swap_used": swap_used, "swap_total": swap_total,
        "net_rx": net_rx, "net_tx": net_tx,
        "uptime_s": uptime_s,
    }


def watchdog_loop():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        for svc in SERVICES:
            sid = svc["id"]
            ok, latency, scheme = check_service(svc)
            with state_lock:
                st = STATE[sid]
                prev = st["status"]
                st["latency_ms"] = latency
                st["hist"].append(latency if latency is not None else 0)
                if ok:
                    st["scheme"] = scheme
                if ok:
                    st["fails"] = 0
                    if prev in ("offline", "restarting", "failed", "unknown"):
                        if prev in ("offline", "restarting", "failed"):
                            log_event(sid, "recovered")
                        st["status"] = "online"
                        st["since"] = time.time()
                        st["restarts"] = 0
                    elif prev != "online":
                        st["status"] = "online"
                        st["since"] = time.time()
                else:
                    st["fails"] += 1
                    if prev in ("online", "unknown") and st["fails"] >= FAIL_THRESHOLD:
                        st["status"] = "offline"
                        st["since"] = time.time()
                        log_event(sid, "down", f"{st['fails']} consecutive failures")
                    # Restart logic runs while offline/restarting and under the cap.
                    if (AUTO_RESTART and st["status"] in ("offline", "restarting")
                            and svc.get("restart") and st["restarts"] < RESTART_MAX):
                        backoff = RESTART_BACKOFF * (st["restarts"] + 1)
                        if time.time() - st["last_restart"] >= backoff:
                            st["restarts"] += 1
                            st["last_restart"] = time.time()
                            st["status"] = "restarting"
                            log_event(sid, "restart",
                                      f"attempt {st['restarts']}/{RESTART_MAX}")
                            attempt_restart(svc)
                    elif (st["status"] in ("offline", "restarting")
                          and st["restarts"] >= RESTART_MAX):
                        if prev != "failed":
                            log_event(sid, "gave_up",
                                      f"still down after {RESTART_MAX} restarts")
                        st["status"] = "failed"
        with state_lock:
            BOX.clear()
            BOX.update(read_box_stats())
            snapshot = {"ts": _now_iso(),
                        "services": {sid: dict(v) for sid, v in STATE.items()},
                        "box": dict(BOX)}
        try:
            STATE_FILE.write_text(json.dumps(snapshot, indent=2))
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


def recent_events(limit=25):
    if not EVENTS_FILE.exists():
        return []
    try:
        lines = EVENTS_FILE.read_text().splitlines()
    except Exception:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return list(reversed(out))


def recent_media(limit=18):
    items = []
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.rglob("*.png"):
            if f.stat().st_size > 1024:
                items.append({"kind": "image", "name": f.name,
                              "path": str(f.relative_to(OUTPUT_DIR)),
                              "mtime": f.stat().st_mtime})
        for f in OUTPUT_DIR.rglob("*.mp4"):
            items.append({"kind": "video", "name": f.name,
                          "path": str(f.relative_to(OUTPUT_DIR)),
                          "mtime": f.stat().st_mtime})
    if MUSIC_OUTPUT.exists():
        for f in MUSIC_OUTPUT.rglob("*"):
            if f.suffix.lower() in AUDIO_EXTS and f.is_file():
                items.append({"kind": "audio", "name": f.name,
                              "path": str(f.relative_to(MUSIC_OUTPUT)),
                              "mtime": f.stat().st_mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def pid_on_port(port):
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
            stderr=subprocess.DEVNULL).decode().split()
        return int(out[0]) if out else None
    except Exception:
        return None


def service_detail(sid):
    svc = SERVICE_BY_ID.get(sid)
    if not svc:
        return {"error": "unknown service"}
    with state_lock:
        st = STATE[sid]
        hist = list(st["hist"])
        info = {"id": sid, "name": svc["name"], "cat": svc["cat"], "desc": svc["desc"],
                "port": svc["port"], "status": st["status"], "scheme": st.get("scheme", "http"),
                "restarts": st["restarts"], "since": st["since"],
                "restartable": bool(svc.get("restart"))}
    proc = None
    pid = pid_on_port(svc["port"])
    if pid and psutil is not None:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                ncpu = psutil.cpu_count() or 1
                proc = {
                    "pid": pid,
                    "name": p.name(),
                    "cpu": round(p.cpu_percent(interval=0.3) / ncpu, 1),
                    "mem": p.memory_info().rss,
                    "threads": p.num_threads(),
                    "uptime_s": int(time.time() - p.create_time()),
                    "cmd": " ".join(p.cmdline()[:4])[:120],
                }
        except Exception:
            proc = None
    events = [e for e in recent_events(200) if e.get("service") == sid][:12]
    return {"service": info, "proc": proc, "hist": hist, "events": events}


def services_payload():
    with state_lock:
        svcs = []
        for s in SERVICES:
            st = STATE[s["id"]]
            svcs.append({
                "id": s["id"], "name": s["name"], "cat": s["cat"],
                "icon": s["icon"], "color": s["color"], "desc": s["desc"],
                "port": s["port"], "path": s["path"],
                "scheme": st.get("scheme", s.get("scheme", "http")),
                "restartable": bool(s.get("restart")),
                "status": st["status"], "latency_ms": st["latency_ms"],
                "since": st["since"], "restarts": st["restarts"],
            })
        box = dict(BOX)
    return {"services": svcs, "box": box, "events": recent_events()}


ACTIVITY_SOURCES = [
    ("video", "Video Studio", "#f76f8e", "🎞", 8192),
    ("images", "Image Studio", "#7c6ff7", "⬡", 8190),
]


def activity_payload():
    out = []
    for sid, name, color, icon, port in ACTIVITY_SOURCES:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/activity" % port, timeout=2) as r:
                a = json.loads(r.read())
        except Exception:
            continue
        if a.get("active"):
            out.append({"id": sid, "name": name, "color": color, "icon": icon,
                        "phase": a.get("phase"), "progress": a.get("progress"),
                        "step": a.get("step"), "max_steps": a.get("max_steps"),
                        "prompt": a.get("prompt", "")})
    return out


def clear_events():
    try:
        EVENTS_FILE.write_text("")
        return True
    except Exception:
        return False


# ---- System Overview (modal) data ----
_GPU_STATIC = None
_LF_CACHE = {"ts": 0.0, "files": []}
_SYS_PREV = {}  # net/disk io counters for rate calc


def gpu_stats():
    global _GPU_STATIC
    util = mem_inuse = tiler = renderer = None
    try:
        out = subprocess.check_output(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
                                      stderr=subprocess.DEVNULL, timeout=3).decode(errors="ignore")
        m = re.search(r'"Device Utilization %"=(\d+)', out)
        util = int(m.group(1)) if m else None
        m = re.search(r'"Tiler Utilization %"=(\d+)', out)
        tiler = int(m.group(1)) if m else None
        m = re.search(r'"Renderer Utilization %"=(\d+)', out)
        renderer = int(m.group(1)) if m else None
        m = re.search(r'"In use system memory"=(\d+)', out)
        mem_inuse = int(m.group(1)) if m else None
    except Exception:
        pass
    if _GPU_STATIC is None:
        chip = cores = None
        try:
            sp = subprocess.check_output(["system_profiler", "SPDisplaysDataType"],
                                         stderr=subprocess.DEVNULL, timeout=8).decode(errors="ignore")
            cm = re.search(r"Chipset Model: (.+)", sp)
            chip = cm.group(1).strip() if cm else None
            cc = re.search(r"Total Number of Cores: (\d+)", sp)
            cores = int(cc.group(1)) if cc else None
        except Exception:
            pass
        _GPU_STATIC = {"chip": chip, "cores": cores}
    return {"util": util, "tiler": tiler, "renderer": renderer,
            "mem_inuse": mem_inuse, **_GPU_STATIC}


def largest_files(n=14, min_bytes=2_000_000_000):
    if time.time() - _LF_CACHE["ts"] < 300 and _LF_CACHE["files"]:
        return _LF_CACHE["files"]
    files = []
    try:
        out = subprocess.check_output(["mdfind", "kMDItemFSSize > %d" % min_bytes],
                                      stderr=subprocess.DEVNULL, timeout=8).decode(errors="ignore").splitlines()
        for path in out[:500]:
            try:
                sz = os.stat(path).st_size
                files.append({"path": path, "name": os.path.basename(path), "size": sz})
            except Exception:
                pass
        files.sort(key=lambda f: f["size"], reverse=True)
        files = files[:n]
        _LF_CACHE.update(ts=time.time(), files=files)
    except Exception:
        pass
    return _LF_CACHE["files"]


def disk_list():
    """Real user-facing volumes only: the boot container (/) and any /Volumes/*.
    Uses shutil so the boot volume reports true container usage, not the sealed
    read-only system snapshot that psutil reports for '/'."""
    out = []
    if psutil is None:
        return out
    seen = set()
    for p in psutil.disk_partitions(all=False):
        mp = p.mountpoint
        if mp != "/" and not mp.startswith("/Volumes/"):
            continue
        if mp in seen:
            continue
        try:
            u = shutil.disk_usage(mp)
        except Exception:
            continue
        if u.total < 1_000_000_000:
            continue
        seen.add(mp)
        label = "Macintosh HD" if mp == "/" else os.path.basename(mp)
        out.append({"mount": mp, "label": label, "device": p.device,
                    "total": u.total, "used": u.used, "free": u.free,
                    "pct": round(u.used / u.total * 100, 1)})
    return out


def system_payload():
    cores, top_cpu, top_mem = sample_cpu_and_procs(n=10)
    now = time.time()
    vm = sw = None
    freq = None
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
        except Exception:
            pass
        try:
            sw = psutil.swap_memory()
        except Exception:
            pass
        try:
            f = psutil.cpu_freq()
            freq = round(f.current) if f else None
        except Exception:
            pass
    # net + disk io rates
    net_rx = net_tx = dio_r = dio_w = None
    if psutil is not None:
        try:
            nio = psutil.net_io_counters()
            dio = psutil.disk_io_counters()
            prev = _SYS_PREV.get("t")
            if prev:
                dt = max(0.1, now - prev)
                net_rx = int((nio.bytes_recv - _SYS_PREV["nrx"]) / dt)
                net_tx = int((nio.bytes_sent - _SYS_PREV["ntx"]) / dt)
                if dio:
                    dio_r = int((dio.read_bytes - _SYS_PREV["dr"]) / dt)
                    dio_w = int((dio.write_bytes - _SYS_PREV["dw"]) / dt)
            _SYS_PREV.update(t=now, nrx=nio.bytes_recv, ntx=nio.bytes_sent,
                             dr=dio.read_bytes if dio else 0, dw=dio.write_bytes if dio else 0)
        except Exception:
            pass
    uptime_s = None
    try:
        bt = subprocess.check_output(["sysctl", "-n", "kern.boottime"]).decode()
        uptime_s = int(time.time() - int(bt.split("sec = ")[1].split(",")[0]))
    except Exception:
        pass
    return {
        "cpu": {"cores": cores, "pct": round(sum(cores) / len(cores), 1) if cores else None,
                "load": [round(x, 2) for x in os.getloadavg()], "ncpu": os.cpu_count(),
                "freq_mhz": freq},
        "gpu": gpu_stats(),
        "mem": {"total": vm.total if vm else None, "used": vm.used if vm else None,
                "available": vm.available if vm else None,
                "wired": getattr(vm, "wired", None) if vm else None,
                "pct": vm.percent if vm else None},
        "swap": {"used": sw.used if sw else None, "total": sw.total if sw else None,
                 "pct": sw.percent if sw else None},
        "disks": disk_list(),
        "net": {"rx": net_rx, "tx": net_tx},
        "diskio": {"read": dio_r, "write": dio_w},
        "top_cpu": top_cpu, "top_mem": top_mem,
        "largest": largest_files(),
        "uptime_s": uptime_s,
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mac Mini Board</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2306080c'/%3E%3Ccircle cx='32' cy='32' r='6' fill='%2337e6d4'/%3E%3Ccircle cx='32' cy='32' r='14' fill='none' stroke='%2337e6d4' stroke-width='2.5' opacity='0.6'/%3E%3Ccircle cx='32' cy='32' r='22' fill='none' stroke='%2337e6d4' stroke-width='2' opacity='0.28'/%3E%3C/svg%3E">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#06080c; --panel:#0d1119; --panel2:#11161f; --line:#1b2430; --line2:#26323f;
  --ink:#e8eef5; --ink2:#8a97a6; --ink3:#4a5563;
  --accent:#37e6d4; --accent-dim:#1e8a80;
  --ok:#3ad07f; --warn:#f4b740; --crit:#f2604f;
  --mono:ui-monospace,"SF Mono",Menlo,"Cascadia Code",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
}
html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5;overflow-x:hidden}
#wave{position:fixed;inset:0;z-index:0;display:block}
.wrap{position:relative;z-index:1;max-width:1240px;margin:0 auto;padding:28px 22px 64px}

/* header */
.head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:11px}
.brand .glyph{width:34px;height:34px;border-radius:9px;background:radial-gradient(circle at 40% 35%,var(--accent),var(--accent-dim));display:flex;align-items:center;justify-content:center;color:#04110f;font-weight:800;box-shadow:0 0 22px rgba(55,230,212,.35)}
.brand h1{font-size:20px;font-weight:800;letter-spacing:-.02em}
.brand h1 span{color:var(--accent)}
.pill{font-family:var(--mono);font-size:12px;color:var(--ink2);background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:4px 12px}
.pill b{color:var(--ok)}
.head .sp{flex:1}
.tick{font-family:var(--mono);font-size:11px;color:var(--ink3);display:flex;align-items:center;gap:6px}
.tick i{width:6px;height:6px;border-radius:50%;background:var(--accent);box-shadow:0 0 8px var(--accent);animation:beat 2s infinite}
@keyframes beat{50%{opacity:.3}}

/* vitals */
.vitals{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:11px;margin-bottom:6px}
.vital{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:13px;padding:13px 15px;cursor:pointer;transition:border-color .15s,transform .1s;position:relative}
.vital:hover{border-color:var(--line2)}
.vital.active{border-color:var(--accent)}
.vital.active::after{content:"";position:absolute;left:50%;bottom:-7px;width:12px;height:12px;background:var(--panel2);border-left:1px solid var(--accent);border-top:1px solid var(--accent);transform:translateX(-50%) rotate(45deg)}
.vital .k{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);margin-bottom:5px;display:flex;justify-content:space-between}
.vital .k .caret{opacity:.4;transition:transform .2s}
.vital.active .k .caret{transform:rotate(180deg);opacity:.9;color:var(--accent)}
/* collapsible vital detail */
.vdetail{overflow:hidden;max-height:0;opacity:0;transition:max-height .3s ease,opacity .2s,margin .3s}
.vdetail.open{max-height:700px;opacity:1;margin:2px 0 8px}
.vdetail-inner{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--accent);border-radius:13px;padding:16px 18px}
.vdetail h3{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);margin-bottom:10px}
.vd-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.vd-grid{grid-template-columns:1fr}}
/* btop-style graphics */
.graph{width:100%;height:64px;display:block;border-radius:8px;background:#0a0e14;border:1px solid var(--line)}
.graph-lg{height:96px}
.graph-wrap{position:relative}
.graph-cap{position:absolute;top:6px;left:9px;font-family:var(--mono);font-size:10px;color:var(--ink3);letter-spacing:.05em}
.graph-cur{position:absolute;top:6px;right:9px;font-family:var(--mono);font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}
.cm{display:flex;align-items:center;gap:8px;margin:3px 0;font-family:var(--mono);font-size:10px}
.cm-l{color:var(--ink3);width:24px;flex-shrink:0}
.cm-t{flex:1;height:9px;border-radius:3px;background:#0a0e14;overflow:hidden;position:relative}
.cm-t::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 4px,rgba(6,8,12,.85) 4px 5px);pointer-events:none}
.cm-f{display:block;height:100%;background:linear-gradient(90deg,var(--ok),var(--warn) 68%,var(--crit));transition:width .5s}
.cm-v{width:34px;text-align:right;color:var(--ink2);flex-shrink:0;font-variant-numeric:tabular-nums}
.bigmeter{height:14px;border-radius:5px;background:#0a0e14;overflow:hidden;position:relative;margin-top:4px}
.bigmeter::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 6px,rgba(6,8,12,.8) 6px 7px)}
.bigmeter>span{display:block;height:100%;background:linear-gradient(90deg,var(--ok),var(--warn) 70%,var(--crit));transition:width .5s}
.corewrap{display:grid;grid-template-columns:1fr 1fr;gap:2px 18px}
@media(max-width:640px){.corewrap{grid-template-columns:1fr}}
.vital .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
.vital .v small{font-size:12px;color:var(--ink2);font-weight:400}
.meter{height:4px;border-radius:3px;background:#0a0e14;margin-top:9px;overflow:hidden}
.meter>span{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent-dim),var(--accent));transition:width .5s}
.meter>span.hot{background:linear-gradient(90deg,#8a6a12,var(--warn))}
.meter>span.crit{background:linear-gradient(90deg,#7a271f,var(--crit))}

/* cover flow */
.stage-wrap{margin:30px 0 8px}
.stage-title{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.stage-title h2{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3)}
.stage-title .hint{font-family:var(--mono);font-size:10px;color:var(--ink3);margin-left:auto}
.stage{position:relative;height:340px;perspective:1600px;overflow:hidden;user-select:none}
.flow{position:absolute;inset:0;transform-style:preserve-3d}
.cf{position:absolute;top:50%;left:50%;width:230px;height:264px;margin:-132px 0 0 -115px;
  background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line2);
  border-radius:18px;overflow:hidden;cursor:pointer;transition:transform .5s cubic-bezier(.22,1,.36,1),opacity .5s,box-shadow .3s;
  display:flex;flex-direction:column;backface-visibility:hidden}
.cf .cover{position:relative;height:140px;overflow:hidden;flex-shrink:0}
.cf .cover svg{position:absolute;inset:0;width:100%;height:100%;display:block}
.cf .cover::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 55%,var(--panel) 100%)}
.cf .cat{position:absolute;top:11px;left:13px;z-index:2;font-family:var(--mono);font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:rgba(255,255,255,.82);text-shadow:0 1px 4px rgba(0,0,0,.5)}
.cf .cf-open{position:absolute;top:9px;right:9px;z-index:3;width:28px;height:28px;border-radius:50%;border:1px solid rgba(255,255,255,.25);background:rgba(3,5,8,.5);color:#fff;font-size:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;opacity:0;transition:all .15s;backdrop-filter:blur(4px)}
.cf.center .cf-open{opacity:1}
.cf .cf-open:hover{border-color:var(--accent);color:var(--accent);background:rgba(3,5,8,.8)}
.cf .body{padding:0 18px 18px;display:flex;flex-direction:column;flex:1;position:relative;z-index:2;margin-top:-6px}
.cf .nm{font-size:19px;font-weight:800;letter-spacing:-.01em}
.cf .ds{font-size:12px;color:var(--ink2);margin-top:3px;flex:1}
.cf .ft{display:flex;align-items:center;justify-content:space-between;margin-top:10px;font-family:var(--mono);font-size:11px}
.cf .stat{display:flex;align-items:center;gap:7px;font-weight:700;text-transform:capitalize}
.cf .dot{width:9px;height:9px;border-radius:50%;background:var(--ink3);flex-shrink:0}
.dot.online{background:var(--ok);box-shadow:0 0 9px var(--ok)}
.dot.offline,.dot.failed{background:var(--crit);box-shadow:0 0 9px var(--crit)}
.dot.restarting{background:var(--warn);box-shadow:0 0 9px var(--warn);animation:beat 1s infinite}
.st-online{color:var(--ok)}.st-offline,.st-failed{color:var(--crit)}.st-restarting{color:var(--warn)}.st-unknown{color:var(--ink3)}
.cf .port{color:var(--ink3)}
.cf.center{box-shadow:0 30px 70px rgba(0,0,0,.6),0 0 0 1px var(--accent-dim)}
.cf.center .nm{color:#fff}
.cf .reflect{position:absolute;left:0;right:0;bottom:-46%;height:44%;border-radius:18px;
  background:linear-gradient(180deg,rgba(255,255,255,.05),transparent);transform:scaleY(-1);opacity:.5;pointer-events:none}
.flow-nav{position:absolute;top:50%;transform:translateY(-50%);z-index:5;width:42px;height:42px;border-radius:50%;
  background:rgba(13,17,25,.8);border:1px solid var(--line2);color:var(--ink);font-size:18px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px);transition:all .15s}
.flow-nav:hover{border-color:var(--accent);color:var(--accent)}
#fprev{left:6px}#fnext{right:6px}
.dots{display:flex;gap:6px;justify-content:center;margin-top:14px}
.dots i{width:6px;height:6px;border-radius:50%;background:var(--line2);cursor:pointer;transition:all .2s}
.dots i.on{background:var(--accent);width:18px;border-radius:3px}

/* lower panels */
.cols{display:grid;grid-template-columns:1fr;gap:18px;margin-top:34px}
@media(min-width:900px){.cols{grid-template-columns:1.25fr 1fr}}
.panel{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:15px;padding:18px}
.panel h2{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--ink3);text-transform:uppercase;letter-spacing:.14em;margin-bottom:14px}
.ev{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line);font-size:12px}
.ev:last-child{border-bottom:none}
.ev .et{font-family:var(--mono);color:var(--ink3);font-size:11px;white-space:nowrap}
.ev .es{font-weight:700;min-width:82px}
.ev .ek{font-family:var(--mono);padding:1px 7px;border-radius:7px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.ek.down,.ek.gave_up,.ek.restart_error{background:rgba(242,96,79,.16);color:var(--crit)}
.ek.recovered{background:rgba(58,208,127,.16);color:var(--ok)}
.ek.restart{background:rgba(244,183,64,.16);color:var(--warn)}
.ev .ed{color:var(--ink3)}
.empty{color:var(--ink3);font-size:12px;padding:14px 0}
.media{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:7px}
.media a{display:block;border-radius:9px;overflow:hidden;border:1px solid var(--line);aspect-ratio:1;background:#000}
.media img,.media video{width:100%;height:100%;object-fit:cover;display:block}
.media .aud{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:22px;background:linear-gradient(135deg,#0d2b21,#0a1a14)}
@media(prefers-reduced-motion:reduce){.cf{transition:none}.tick i,.dot.restarting{animation:none}}

/* ---- btop-style detail drawer ---- */
#scrim{position:fixed;inset:0;z-index:40;background:rgba(3,5,8,.72);backdrop-filter:blur(3px);opacity:0;pointer-events:none;transition:opacity .2s}
#scrim.open{opacity:1;pointer-events:auto}
#detail{position:fixed;top:0;right:0;bottom:0;z-index:41;width:min(560px,94vw);background:var(--bg);border-left:1px solid var(--line2);
  transform:translateX(100%);transition:transform .28s cubic-bezier(.22,1,.36,1);display:flex;flex-direction:column;box-shadow:-30px 0 80px rgba(0,0,0,.6)}
#detail.open{transform:none}
.d-head{display:flex;align-items:center;gap:12px;padding:16px 18px;border-bottom:1px solid var(--line)}
.d-ic{width:40px;height:40px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.d-ttl{font-size:17px;font-weight:800}
.d-sub{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.d-head .sp{flex:1}
.d-btn{font-family:var(--mono);font-size:12px;padding:6px 12px;border-radius:8px;border:1px solid var(--line2);background:var(--panel);color:var(--ink);cursor:pointer;transition:all .15s}
.d-btn:hover{border-color:var(--accent);color:var(--accent)}
.d-btn.warn:hover{border-color:var(--warn);color:var(--warn)}
.d-body{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:18px}
.d-sec h3{font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);margin-bottom:9px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-family:var(--mono);font-size:12px}
.kv .k{color:var(--ink3)}.kv .v{color:var(--ink);text-align:right;font-variant-numeric:tabular-nums}
.spark{width:100%;height:44px;display:block}
.cores{display:grid;grid-template-columns:repeat(auto-fit,minmax(60px,1fr));gap:6px}
.core{font-family:var(--mono);font-size:10px;color:var(--ink3)}
.core .cbar{height:5px;border-radius:3px;background:#0a0e14;margin-top:3px;overflow:hidden}
.core .cbar>span{display:block;height:100%;background:var(--ok)}
.core .cbar>span.hot{background:var(--warn)}.core .cbar>span.crit{background:var(--crit)}
.gauges{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.gauge{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.gauge .gk{font-family:var(--mono);font-size:10px;color:var(--ink3);text-transform:uppercase;letter-spacing:.08em}
.gauge .gv{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.gauge .gv small{font-size:11px;color:var(--ink2);font-weight:400}
.proc-tbl{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px}
.proc-tbl th{text-align:left;color:var(--ink3);font-weight:600;padding:3px 0;border-bottom:1px solid var(--line)}
.proc-tbl td{padding:3px 0;border-bottom:1px solid var(--line);color:var(--ink2)}
.proc-tbl td.n{color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.proc-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.proc-tbl .cpuhot{color:var(--warn)}.proc-tbl .cpucrit{color:var(--crit)}
/* system button, event controls, activity */
.sysbtn{font-family:var(--mono);font-size:12px;padding:7px 13px;border-radius:9px;border:1px solid var(--line2);background:var(--panel);color:var(--ink);cursor:pointer;transition:all .15s;margin-right:12px}
.sysbtn:hover{border-color:var(--accent);color:var(--accent);box-shadow:0 0 16px rgba(55,230,212,.2)}
.panel-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.panel-head h2{margin:0}
.panel-ctl{margin-left:auto;display:flex;gap:6px}
.panel-ctl select,.mini-btn{font-family:var(--mono);font-size:11px;padding:4px 9px;border-radius:7px;border:1px solid var(--line2);background:var(--panel);color:var(--ink2);cursor:pointer}
.panel-ctl select:hover,.mini-btn:hover{border-color:var(--accent);color:var(--accent)}
#activity{display:flex;flex-direction:column;gap:8px;margin:2px 0 6px}
.act{display:flex;align-items:center;gap:12px;background:linear-gradient(90deg,var(--panel),var(--panel2));border:1px solid var(--accent-dim);border-radius:12px;padding:11px 15px}
.act .ai{width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.act .an{font-weight:700;font-size:13px}
.act .ap{font-family:var(--mono);font-size:11px;color:var(--ink2)}
.act .abar{flex:1;height:6px;border-radius:3px;background:#0a0e14;overflow:hidden;position:relative;min-width:80px}
.act .abar>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent-dim),var(--accent));transition:width .5s}
.act .abar>span.indet{width:35%!important;animation:slide 1.3s infinite;background:repeating-linear-gradient(90deg,var(--accent-dim) 0 10px,var(--accent) 10px 20px)}
@keyframes slide{0%{transform:translateX(-120%)}100%{transform:translateX(360%)}}
.act .apct{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--accent);min-width:40px;text-align:right}
/* system modal */
#sysmodal{position:fixed;inset:0;z-index:60;background:rgba(3,5,8,.92);backdrop-filter:blur(4px);opacity:0;pointer-events:none;transition:opacity .2s;overflow-y:auto}
#sysmodal.open{opacity:1;pointer-events:auto}
.sys-wrap{max-width:1240px;margin:0 auto;padding:26px 24px 60px}
.sys-head{display:flex;align-items:center;gap:12px;margin-bottom:20px;position:sticky;top:0;background:rgba(3,5,8,.6);backdrop-filter:blur(6px);padding:6px 0;z-index:2}
.sys-head h2{font-size:20px;font-weight:800}
.sys-head .chip{font-family:var(--mono);font-size:11px;color:var(--ink2);border:1px solid var(--line2);border-radius:20px;padding:3px 11px}
.sys-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
@media(max-width:820px){.sys-grid{grid-template-columns:1fr}}
.sys-card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:15px;padding:16px 18px}
.sys-card.span2{grid-column:1/-1}
.sys-card h3{font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);margin-bottom:11px;display:flex;justify-content:space-between}
.sys-card h3 .big{font-size:15px;font-weight:800;color:var(--ink);letter-spacing:0}
.disk-row{margin:9px 0}
.disk-row .dl{display:flex;justify-content:space-between;font-family:var(--mono);font-size:11px;color:var(--ink2);margin-bottom:3px}
.file-row{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-size:11.5px;padding:4px 0;border-bottom:1px solid var(--line)}
.file-row:last-child{border-bottom:none}
.file-row .fn{flex:1;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.file-row .fp{color:var(--ink3);font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:230px}
.file-row .fs{color:var(--accent);font-variant-numeric:tabular-nums;flex-shrink:0}
.del-btn{flex-shrink:0;background:none;border:1px solid var(--line2);border-radius:6px;color:var(--ink3);cursor:pointer;font-size:12px;padding:2px 7px;transition:all .15s}
.del-btn:hover{border-color:var(--crit);color:var(--crit);background:rgba(242,96,79,.12)}
.gpu-big{display:flex;align-items:center;gap:16px}
.gpu-ring{--p:0;width:96px;height:96px;border-radius:50%;flex-shrink:0;background:conic-gradient(var(--accent) calc(var(--p)*1%),#0a0e14 0);display:flex;align-items:center;justify-content:center;position:relative}
.gpu-ring::before{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--panel)}
.gpu-ring b{position:relative;font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.sys-close{margin-left:auto;font-family:var(--mono);font-size:13px;padding:7px 14px;border-radius:9px;border:1px solid var(--line2);background:var(--panel);color:var(--ink);cursor:pointer}
.sys-close:hover{border-color:var(--crit);color:var(--crit)}
</style>
</head>
<body>
<canvas id="wave"></canvas>
<div class="wrap">
  <div class="head">
    <div class="brand"><div class="glyph">◧</div><h1>Mac Mini<span> Board</span></h1></div>
    <span class="pill"><b id="pill-up">–</b> <span id="pill-tot">/ –</span> up</span>
    <div class="sp"></div>
    <button class="sysbtn" onclick="openSystem()">◱ System</button>
    <div class="tick"><i></i><span id="tick">live · 5s</span></div>
  </div>

  <div id="activity"></div>

  <div class="vitals" id="vitals"></div>
  <div class="vdetail" id="vdetail"><div class="vdetail-inner" id="vdetail-inner"></div></div>

  <div class="stage-wrap">
    <div class="stage-title"><h2>Services</h2><span class="hint">← → scroll · click to open</span></div>
    <div class="stage" id="stage">
      <button class="flow-nav" id="fprev" aria-label="Previous">&#8249;</button>
      <div class="flow" id="flow"></div>
      <button class="flow-nav" id="fnext" aria-label="Next">&#8250;</button>
    </div>
    <div class="dots" id="dots"></div>
  </div>

  <div class="cols">
    <div class="panel">
      <div class="panel-head">
        <h2>Watchdog Events</h2>
        <div class="panel-ctl">
          <select id="ev-sort" onchange="renderEvents()">
            <option value="newest">Newest</option>
            <option value="oldest">Oldest</option>
            <option value="service">By service</option>
            <option value="down">Problems first</option>
          </select>
          <button class="mini-btn" onclick="clearEvents()">Clear</button>
        </div>
      </div>
      <div id="events"><div class="empty">No state changes yet — all quiet.</div></div>
    </div>
    <div class="panel">
      <h2>Recent Output</h2>
      <div class="media" id="mediaGrid"></div>
    </div>
  </div>
</div>

<div id="scrim" onclick="closeDetail()"></div>
<aside id="detail" aria-label="Service detail">
  <div class="d-head">
    <div class="d-ic" id="d-ic"></div>
    <div><div class="d-ttl" id="d-ttl">–</div><div class="d-sub" id="d-sub"></div></div>
    <div class="sp"></div>
    <button class="d-btn" id="d-open" onclick="openFromDetail()">Open ↗</button>
    <button class="d-btn warn" id="d-restart" onclick="restartFromDetail()" style="display:none">Restart</button>
    <button class="d-btn" onclick="closeDetail()">✕</button>
  </div>
  <div class="d-body" id="d-body"></div>
</aside>

<div id="sysmodal">
  <div class="sys-wrap">
    <div class="sys-head">
      <h2>System Overview</h2>
      <span class="chip" id="sys-chip">–</span>
      <button class="sys-close" onclick="closeSystem()">✕ Close</button>
    </div>
    <div class="sys-grid" id="sys-grid"></div>
  </div>
</div>

<script>
const CATS=["Media","Downloads","AI","Studios","Apps"];
let SVCS=[],SVMAP={},center=0;

function fmtBytes(b){if(b==null)return"–";const u=["B","KB","MB","GB","TB"];let i=0,n=b;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(n<10&&i>0?1:0)+u[i]}
function fmtDur(s){if(s==null)return"–";const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);if(d)return d+"d "+h+"h";if(h)return h+"h "+m+"m";return m+"m"}

/* ---------- wave / particle background ---------- */
(function(){
  const c=document.getElementById("wave"),x=c.getContext("2d");
  const reduce=matchMedia("(prefers-reduced-motion:reduce)").matches;
  let w,h,parts=[];
  function size(){w=c.width=innerWidth*devicePixelRatio;h=c.height=innerHeight*devicePixelRatio;
    c.style.width=innerWidth+"px";c.style.height=innerHeight+"px";
    const n=Math.min(120,Math.floor(innerWidth/14));
    parts=[];for(let i=0;i<n;i++)parts.push({x:Math.random()*w,y:Math.random()*h,
      sp:.15+Math.random()*.4,amp:8+Math.random()*22,ph:Math.random()*Math.PI*2,r:(0.6+Math.random()*1.6)*devicePixelRatio});
  }
  size();addEventListener("resize",size);
  let t=0;
  function frame(){
    t+=reduce?0:0.006;
    x.clearRect(0,0,w,h);
    // flowing wave guide lines
    for(let k=0;k<3;k++){
      x.beginPath();
      const yb=h*(0.35+k*0.2), amp=(18+k*10)*devicePixelRatio;
      for(let px=0;px<=w;px+=14*devicePixelRatio){
        const y=yb+Math.sin(px*0.004+t*1.4+k)*amp+Math.sin(px*0.011-t)*amp*0.4;
        px===0?x.moveTo(px,y):x.lineTo(px,y);
      }
      x.strokeStyle="rgba(55,230,212,"+(0.05-k*0.012)+")";x.lineWidth=1*devicePixelRatio;x.stroke();
    }
    // particles riding the wave
    for(const p of parts){
      p.x+=p.sp*devicePixelRatio; if(p.x>w+20)p.x=-20;
      const y=p.y+Math.sin(p.x*0.006+t*2+p.ph)*p.amp*devicePixelRatio;
      x.beginPath();x.arc(p.x,y,p.r,0,7);
      x.fillStyle="rgba(55,230,212,0.5)";x.fill();
    }
    requestAnimationFrame(frame);
  }
  frame();
})();

/* ---------- generative cover art (self-contained SVG per service) ---------- */
function hexPts(cx,cy,r){let p=[];for(let i=0;i<6;i++){const a=Math.PI/6+i*Math.PI/3;p.push((cx+r*Math.cos(a)).toFixed(1)+","+(cy+r*Math.sin(a)).toFixed(1));}return p.join(" ");}
function coverArt(s){
  const c=s.color,uid="cg-"+s.id;let m="";
  switch(s.id){
    case "plex": case "video":
      m=`${[...Array(6)].map((_,i)=>`<rect x="${26+i*3.2}" y="34" width="2" height="72" fill="#fff" opacity=".07"/>`).join("")}
         <path d="M96 44 L154 72 L96 100 Z" fill="${c}" opacity=".92"/>
         <path d="M96 44 L154 72 L96 100 Z" fill="none" stroke="#fff" stroke-opacity=".25" stroke-width="1.5"/>`;break;
    case "radarr":
      m=`<g transform="translate(112,70)"><circle r="47" fill="none" stroke="${c}" stroke-width="6" opacity=".85"/>
         <circle r="11" fill="${c}"/>${[0,72,144,216,288].map(a=>{const x=30*Math.cos(a*Math.PI/180),y=30*Math.sin(a*Math.PI/180);return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7" fill="none" stroke="${c}" stroke-width="4" opacity=".7"/>`;}).join("")}</g>`;break;
    case "sabnzbd":
      m=`<g stroke="${c}" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round">
         <path d="M92 38 L116 58 L140 38" opacity=".35"/><path d="M92 57 L116 77 L140 57" opacity=".65"/><path d="M92 76 L116 96 L140 76"/></g>
         <rect x="84" y="106" width="64" height="6" rx="3" fill="${c}" opacity=".9"/>`;break;
    case "ollama": case "comfyui": {
      const pts=[[52,46],[112,32],[168,58],[74,98],[150,104],[116,72]];
      const edges=[[0,1],[1,2],[0,5],[1,5],[2,4],[3,5],[4,5],[3,0]];
      const node=s.id==="comfyui"
        ? p=>`<rect x="${p[0]-11}" y="${p[1]-8}" width="22" height="16" rx="4" fill="#0b0f16" stroke="${c}" stroke-width="2.5"/>`
        : p=>`<circle cx="${p[0]}" cy="${p[1]}" r="9" fill="#0b0f16" stroke="${c}" stroke-width="2.5"/>`;
      m=`${edges.map(([a,b])=>`<line x1="${pts[a][0]}" y1="${pts[a][1]}" x2="${pts[b][0]}" y2="${pts[b][1]}" stroke="${c}" stroke-width="1.5" opacity=".4"/>`).join("")}
         ${pts.map(node).join("")}`;break;}
    case "images":
      m=`<polygon points="${hexPts(96,64,34)}" fill="none" stroke="${c}" stroke-width="3" opacity=".8"/>
         <polygon points="${hexPts(134,82,26)}" fill="${c}" fill-opacity=".14" stroke="${c}" stroke-width="2.5" opacity=".7"/>
         <polygon points="${hexPts(120,50,18)}" fill="none" stroke="#fff" stroke-opacity=".22" stroke-width="2"/>`;break;
    case "music":
      m=`${[...Array(12)].map((_,i)=>{const h=16+Math.abs(Math.sin(i*0.9+1))*74;return `<rect x="${26+i*15}" y="${(112-h).toFixed(1)}" width="8" height="${h.toFixed(1)}" rx="4" fill="${c}" opacity="${(.5+(i%3)*0.16).toFixed(2)}"/>`;}).join("")}`;break;
    case "magic":
      m=`<g transform="translate(112,70)"><path d="M0 -46 C6 -12 12 -6 46 0 C12 6 6 12 0 46 C-6 12 -12 6 -46 0 C-12 -6 -6 -12 0 -46 Z" fill="${c}" opacity=".9"/>
         <circle cx="-52" cy="-30" r="4" fill="#fff" opacity=".8"/><circle cx="54" cy="20" r="5" fill="${c}"/><circle cx="30" cy="-44" r="3" fill="#fff" opacity=".7"/></g>`;break;
    default: /* hub / radar */
      m=`<g transform="translate(96,70)"><circle r="22" fill="none" stroke="${c}" stroke-width="2" opacity=".5"/>
         <circle r="42" fill="none" stroke="${c}" stroke-width="2" opacity=".32"/><circle r="62" fill="none" stroke="${c}" stroke-width="2" opacity=".18"/>
         <line x1="0" y1="0" x2="54" y2="-30" stroke="${c}" stroke-width="2.5"/><circle r="5" fill="${c}"/></g>`;
  }
  return `<svg viewBox="0 0 230 140" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">
    <defs><radialGradient id="${uid}" cx="32%" cy="26%" r="95%">
      <stop offset="0%" stop-color="${c}" stop-opacity=".5"/><stop offset="52%" stop-color="${c}" stop-opacity=".12"/><stop offset="100%" stop-color="${c}" stop-opacity="0"/>
    </radialGradient></defs>
    <rect width="230" height="140" fill="#0b0f16"/><rect width="230" height="140" fill="url(#${uid})"/>
    ${[...Array(8)].map((_,i)=>`<line x1="0" y1="${i*20}" x2="230" y2="${i*20}" stroke="#fff" stroke-opacity=".025"/>`).join("")}
    ${m}</svg>`;
}

/* ---------- cover flow ---------- */
function buildFlow(){
  const flow=document.getElementById("flow");flow.innerHTML="";
  SVCS.forEach((s,i)=>{
    const el=document.createElement("div");el.className="cf";el.dataset.i=i;
    el.innerHTML=`
      <div class="cover">${coverArt(s)}<span class="cat">${s.cat}</span>
        <button class="cf-open" title="Open ${s.name}">↗</button></div>
      <div class="body">
        <div class="nm">${s.name}</div>
        <div class="ds">${s.desc}</div>
        <div class="ft">
          <span class="stat st-${s.status}"><span class="dot ${s.status}"></span>${s.status}</span>
          <span class="port">:${s.port}</span>
        </div>
      </div>`;
    el.onclick=()=>{ if(i===center){openDetail(s);} else {center=i;layout();} };
    const ob=el.querySelector(".cf-open");
    if(ob)ob.onclick=(e)=>{e.stopPropagation();openSvc(s);};
    flow.appendChild(el);
  });
  const dots=document.getElementById("dots");dots.innerHTML="";
  SVCS.forEach((s,i)=>{const d=document.createElement("i");if(i===center)d.className="on";d.onclick=()=>{center=i;layout();};dots.appendChild(d);});
  layout();
}
function layout(){
  const cards=document.querySelectorAll(".cf");
  cards.forEach((el,i)=>{
    const off=i-center;const a=Math.abs(off);
    const tx=off*150 - Math.sign(off)*20*Math.min(a,1);
    const ry=off===0?0:(off<0?38:-38);
    const tz=off===0?60:-Math.min(a,4)*80;
    el.style.transform=`translateX(${tx}px) translateZ(${tz}px) rotateY(${ry}deg)`;
    el.style.opacity=a>4?0:1-a*0.12;
    el.style.zIndex=100-a;
    el.classList.toggle("center",off===0);
  });
  document.querySelectorAll(".dots i").forEach((d,i)=>d.className=i===center?"on":"");
}
function openSvc(s){ if(s.id==="hub")return; const url=`${s.scheme}://${location.hostname}:${s.port}${s.path}`; window.open(url,"_blank"); }

/* ---------- btop-style detail drawer ---------- */
let detailSvc=null;
function sparkline(hist){
  if(!hist||!hist.length)return '<div class="d-sub">no samples yet</div>';
  const max=Math.max(10,...hist),W=520,H=44,n=hist.length;
  const pts=hist.map((v,i)=>`${(i/(n-1||1)*W).toFixed(1)},${(H-2-v/max*(H-6)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/>
    <polyline points="0,${H} ${pts} ${W},${H}" fill="var(--accent)" fill-opacity=".08" stroke="none"/></svg>`;
}
/* ---- btop-style graph kit ---- */
const HIST={cpu:[],mem:[],rx:[],tx:[]};
function pushHist(k,v){const a=HIST[k];a.push(v==null?0:v);if(a.length>90)a.shift();}
function areaGraph(data,color,opt){
  opt=opt||{};const w=560,h=opt.h||64,max=opt.max||Math.max(1,...data);
  if(!data||data.length<2)return `<div class="graph-wrap"><div class="graph ${opt.lg?'graph-lg':''}"></div><span class="graph-cap">${opt.cap||""}</span><span class="graph-cur" style="color:${color}">collecting…</span></div>`;
  const n=data.length,X=i=>(i/(n-1)*w),Y=v=>h-1-(Math.min(v,max)/max)*(h-3);
  const line=data.map((v,i)=>`${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const uid="ag"+Math.random().toString(36).slice(2,7);
  return `<div class="graph-wrap">
    <svg class="graph ${opt.lg?'graph-lg':''}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".5"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <polygon points="0,${h} ${line} ${w},${h}" fill="url(#${uid})"/>
      <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
    </svg>
    <span class="graph-cap">${opt.cap||""}</span>
    <span class="graph-cur" style="color:${color}">${opt.cur!=null?opt.cur:""}</span></div>`;
}
function coreMeters(cores){
  if(!cores||!cores.length)return "";
  return `<div class="corewrap">${cores.map((c,i)=>{const cl=c>=85?"crit":c>=55?"hot":"";
    return `<div class="cm"><span class="cm-l">c${i}</span><div class="cm-t"><span class="cm-f ${cl}" style="width:${Math.min(100,c)}%"></span></div><span class="cm-v">${c.toFixed(0)}%</span></div>`;}).join("")}</div>`;
}
function bigMeter(pct){const cl=pct>=90?"crit":pct>=70?"hot":"";return `<div class="bigmeter"><span class="${cl}" style="width:${Math.min(100,pct||0)}%"></span></div>`;}
function procRows(top,ncpu){
  if(!top||!top.length)return "";
  return `<table class="proc-tbl"><thead><tr><th>process</th><th style="text-align:right">cpu%</th><th style="text-align:right">mem</th></tr></thead><tbody>
    ${top.map(p=>{const cl=p.cpu>=50?"cpucrit":p.cpu>=20?"cpuhot":"";
      return `<tr><td class="n" title="pid ${p.pid}">${p.name}</td><td class="num ${cl}">${p.cpu.toFixed(1)}</td><td class="num">${fmtBytes(p.mem)}</td></tr>`;}).join("")}
    </tbody></table>`;
}
async function openDetail(s){
  detailSvc=s;
  document.getElementById("d-ic").style.background=s.color+"22";
  document.getElementById("d-ic").style.color=s.color;
  document.getElementById("d-ic").textContent=s.icon;
  document.getElementById("d-ttl").textContent=s.name;
  document.getElementById("d-sub").textContent=`${s.cat} · ${s.scheme}://…:${s.port}`;
  document.getElementById("d-open").style.display=s.id==="hub"?"none":"";
  document.getElementById("scrim").classList.add("open");
  document.getElementById("detail").classList.add("open");
  document.getElementById("d-body").innerHTML='<div class="d-sub">loading…</div>';
  try{
    const d=await(await fetch("/api/detail?id="+encodeURIComponent(s.id))).json();
    renderDetail(d);
  }catch(e){document.getElementById("d-body").innerHTML='<div class="d-sub">could not load detail</div>';}
}
function renderDetail(d){
  const s=d.service,p=d.proc;
  document.getElementById("d-restart").style.display=s.restartable?"":"none";
  const procBlock=p?`<div class="kv">
      <span class="k">pid</span><span class="v">${p.pid}</span>
      <span class="k">cpu</span><span class="v">${p.cpu}%</span>
      <span class="k">memory</span><span class="v">${fmtBytes(p.mem)}</span>
      <span class="k">threads</span><span class="v">${p.threads}</span>
      <span class="k">uptime</span><span class="v">${fmtDur(p.uptime_s)}</span>
      <span class="k">command</span><span class="v" style="text-align:left;word-break:break-all">${p.cmd||"–"}</span>
    </div>`:`<div class="d-sub">no local process on :${s.port} (remote or app-managed)</div>`;
  const evBlock=d.events&&d.events.length?d.events.map(e=>{
    const t=(e.ts||"").replace("T"," ").replace("Z","").slice(5,16);
    return `<div class="ev"><span class="et">${t}</span><span class="ek ${e.event}">${e.event.replace(/_/g," ")}</span><span class="ed">${e.detail||""}</span></div>`;}).join(""):'<div class="d-sub">no events for this service</div>';
  document.getElementById("d-body").innerHTML=`
    <div class="d-sec"><h3>Status · ${s.status}</h3>
      <div class="kv"><span class="k">restarts</span><span class="v">${s.restarts}</span>
      <span class="k">endpoint</span><span class="v">${s.scheme}://…:${s.port}</span></div>
    </div>
    <div class="d-sec"><h3>Process</h3>${procBlock}</div>
    <div class="d-sec"><h3>Recent events</h3>${evBlock}</div>`;
}
function closeDetail(){detailSvc=null;document.getElementById("scrim").classList.remove("open");document.getElementById("detail").classList.remove("open");}
function openFromDetail(){if(detailSvc)openSvc(detailSvc);}
async function restartFromDetail(){
  if(!detailSvc)return;
  const btn=document.getElementById("d-restart");btn.textContent="Restarting…";btn.disabled=true;
  try{await fetch("/api/restart?id="+encodeURIComponent(detailSvc.id),{method:"POST"});}catch(e){}
  setTimeout(()=>{btn.textContent="Restart";btn.disabled=false;if(detailSvc)openDetail(detailSvc);},1500);
}
function move(dir){ center=Math.max(0,Math.min(SVCS.length-1,center+dir)); layout(); }
document.getElementById("fprev").onclick=()=>move(-1);
document.getElementById("fnext").onclick=()=>move(1);
addEventListener("keydown",e=>{if(e.key==="ArrowLeft")move(-1);if(e.key==="ArrowRight")move(1);
  if(e.key==="Escape"){if(document.getElementById("sysmodal").classList.contains("open")){closeSystem();return;}closeDetail();return;}
  if(e.key==="Enter"&&SVCS[center])openDetail(SVCS[center]);});
let wheelLock=0;
document.getElementById("stage").addEventListener("wheel",e=>{e.preventDefault();
  const now=Date.now();if(now-wheelLock<220)return;wheelLock=now;move(e.deltaY>0||e.deltaX>0?1:-1);},{passive:false});

/* ---------- data ---------- */
function order(list){return [...list].sort((a,b)=>CATS.indexOf(a.cat)-CATS.indexOf(b.cat)||a.name.localeCompare(b.name));}
function renderVitals(b,up,tot){
  const memUsed=(b.mem_total&&b.mem_free)?b.mem_total-b.mem_free:null;
  const memPct=memUsed?Math.round(memUsed/b.mem_total*100):0;
  const diskUsed=(b.disk_total&&b.disk_free)?b.disk_total-b.disk_free:null;
  const diskPct=diskUsed?Math.round(diskUsed/b.disk_total*100):0;
  const loadPct=(b.load&&b.cpus)?Math.min(100,Math.round(b.load[0]/b.cpus*100)):0;
  const cls=p=>p>=90?"crit":p>=70?"hot":"";
  const A=k=>openVital===k?" active":"";
  const car='<span class="caret">▾</span>';
  document.getElementById("vitals").innerHTML=`
    <div class="vital${A('services')}" onclick="toggleVital('services')"><div class="k">Services ${car}</div><div class="v">${up}<small> / ${tot} up</small></div><div class="meter"><span class="${up<tot?'hot':''}" style="width:${tot?up/tot*100:0}%"></span></div></div>
    <div class="vital${A('load')}" onclick="toggleVital('load')"><div class="k">Load 1m ${car}</div><div class="v">${b.load?b.load[0]:"–"}<small> · ${b.cpus||"?"} cpu</small></div><div class="meter"><span class="${cls(loadPct)}" style="width:${loadPct}%"></span></div></div>
    <div class="vital${A('memory')}" onclick="toggleVital('memory')"><div class="k">Memory ${car}</div><div class="v">${fmtBytes(memUsed)}<small> / ${fmtBytes(b.mem_total)}</small></div><div class="meter"><span class="${cls(memPct)}" style="width:${memPct}%"></span></div></div>
    <div class="vital${A('disk')}" onclick="toggleVital('disk')"><div class="k">Disk free ${car}</div><div class="v">${fmtBytes(b.disk_free)}<small> / ${fmtBytes(b.disk_total)}</small></div><div class="meter"><span class="${cls(diskPct)}" style="width:${diskPct}%"></span></div></div>
    <div class="vital${A('uptime')}" onclick="toggleVital('uptime')"><div class="k">Uptime ${car}</div><div class="v">${fmtDur(b.uptime_s)}</div></div>`;
  syncVitalDetail();
}
let openVital=null;
function toggleVital(k){openVital=(openVital===k)?null:k;
  document.querySelectorAll(".vital").forEach(el=>el.classList.remove("active"));
  syncVitalDetail();
  // re-mark active tile
  const idx={services:0,load:1,memory:2,disk:3,uptime:4}[openVital];
  if(idx!=null)document.querySelectorAll(".vital")[idx]?.classList.add("active");
}
function procRowsMem(top){
  if(!top||!top.length)return '<div class="d-sub">–</div>';
  return `<table class="proc-tbl"><thead><tr><th>process</th><th style="text-align:right">mem</th><th style="text-align:right">cpu%</th></tr></thead><tbody>
    ${top.map(p=>`<tr><td class="n" title="pid ${p.pid}">${p.name}</td><td class="num">${fmtBytes(p.mem)}</td><td class="num">${p.cpu.toFixed(1)}</td></tr>`).join("")}</tbody></table>`;
}
function syncVitalDetail(){
  const wrap=document.getElementById("vdetail");
  if(!openVital){wrap.classList.remove("open");return;}
  wrap.classList.add("open");
  document.getElementById("vdetail-inner").innerHTML=renderVitalDetail(openVital,window.__box||{});
}
function renderVitalDetail(kind,b){
  if(kind==="services"){
    const rows=SVCS.map(s=>`<tr><td class="n"><span class="dot ${s.status}" style="display:inline-block;margin-right:7px"></span>${s.name}</td><td class="num st-${s.status}">${s.status}</td></tr>`).join("");
    return `<h3>All services · ${SVCS.filter(s=>s.status==="online").length}/${SVCS.length} up</h3>
      <table class="proc-tbl"><thead><tr><th>service</th><th style="text-align:right">status</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  if(kind==="load"){
    const rxMax=Math.max(1,...HIST.rx,...HIST.tx);
    return `<h3>CPU ${b.cpu_pct??"–"}% · load ${(b.load||[]).join(" / ")||"–"} · ${b.cpus||"?"} cores</h3>
      ${areaGraph(HIST.cpu,"var(--accent)",{h:96,lg:true,max:100,cap:"CPU %",cur:(b.cpu_pct??"–")+"%"})}
      <div class="vd-grid" style="margin-top:14px">
        <div>${coreMeters(b.cpu_cores)}</div>
        <div>
          <h3>Network</h3>
          ${areaGraph(HIST.rx,"#3ad07f",{h:40,max:rxMax,cap:"↓ rx",cur:fmtBytes(b.net_rx)+"/s"})}
          <div style="height:6px"></div>
          ${areaGraph(HIST.tx,"#f4b740",{h:40,max:rxMax,cap:"↑ tx",cur:fmtBytes(b.net_tx)+"/s"})}
        </div>
      </div>
      <h3 style="margin-top:14px">Top by CPU</h3>${procRows(b.top,b.cpus)}`;
  }
  if(kind==="memory"){
    const m=b.mem_detail||{};const g=k=>m[k]!=null?fmtBytes(m[k]):"–";
    const memPct=(b.mem_total&&b.mem_free)?Math.round((b.mem_total-b.mem_free)/b.mem_total*100):0;
    const swapPct=b.swap_total?Math.round((b.swap_used||0)/b.swap_total*100):0;
    return `<h3>Memory ${memPct}% used</h3>
      ${areaGraph(HIST.mem,"#c084fc",{h:80,lg:true,max:100,cap:"MEM %",cur:memPct+"%"})}
      <div class="vd-grid" style="margin-top:14px">
        <div class="kv">
          <span class="k">total</span><span class="v">${g("total")}</span>
          <span class="k">used</span><span class="v">${g("used")}</span>
          <span class="k">available</span><span class="v">${g("available")}</span>
          <span class="k">wired</span><span class="v">${g("wired")}</span>
          <span class="k">active</span><span class="v">${g("active")}</span>
          <span class="k">inactive</span><span class="v">${g("inactive")}</span>
          <span class="k">free</span><span class="v">${g("free")}</span>
          <span class="k">swap</span><span class="v">${fmtBytes(b.swap_used)} / ${fmtBytes(b.swap_total)}</span>
        </div>
        <div>
          <div class="cm"><span class="cm-l" style="width:34px">mem</span><div class="cm-t"><span class="cm-f ${memPct>=90?'crit':memPct>=70?'hot':''}" style="width:${memPct}%"></span></div><span class="cm-v">${memPct}%</span></div>
          <div class="cm"><span class="cm-l" style="width:34px">swap</span><div class="cm-t"><span class="cm-f ${swapPct>=90?'crit':swapPct>=70?'hot':''}" style="width:${swapPct}%"></span></div><span class="cm-v">${swapPct}%</span></div>
          <h3 style="margin-top:12px">Top by memory</h3>${procRowsMem(b.top_mem)}
        </div>
      </div>`;
  }
  if(kind==="disk"){
    const used=(b.disk_total&&b.disk_free)?b.disk_total-b.disk_free:null;
    const pct=used?Math.round(used/b.disk_total*100):0;
    return `<h3>Disk · / volume · ${pct}% used</h3>
      <div class="kv"><span class="k">total</span><span class="v">${fmtBytes(b.disk_total)}</span>
      <span class="k">used</span><span class="v">${fmtBytes(used)}</span>
      <span class="k">free</span><span class="v">${fmtBytes(b.disk_free)}</span></div>
      ${bigMeter(pct)}`;
  }
  if(kind==="uptime"){
    const boot=b.uptime_s!=null?new Date(Date.now()-b.uptime_s*1000).toLocaleString():"–";
    return `<h3>Uptime</h3>
      <div class="kv"><span class="k">up for</span><span class="v">${fmtDur(b.uptime_s)}</span>
      <span class="k">booted</span><span class="v">${boot}</span>
      <span class="k">load 1/5/15</span><span class="v">${(b.load||[]).join(" / ")||"–"}</span>
      <span class="k">services up</span><span class="v">${SVCS.filter(s=>s.status==="online").length} / ${SVCS.length}</span></div>`;
  }
  return "";
}
let allEvents=[];
function renderEvents(){
  const ev=document.getElementById("events");
  let list=[...allEvents];  // server returns newest-first
  const sort=(document.getElementById("ev-sort")||{}).value||"newest";
  const bad=new Set(["down","gave_up","restart_error","restart"]);
  if(sort==="oldest")list.reverse();
  else if(sort==="service")list.sort((a,b)=>(a.service||"").localeCompare(b.service||"")||(b.ts||"").localeCompare(a.ts||""));
  else if(sort==="down")list.sort((a,b)=>(bad.has(b.event)?1:0)-(bad.has(a.event)?1:0));
  if(!list.length){ev.innerHTML='<div class="empty">No state changes yet — all quiet.</div>';return;}
  ev.innerHTML=list.map(e=>{
    const t=(e.ts||"").replace("T"," ").replace("Z","").slice(5,16);
    const nm=(SVMAP[e.service]||{}).name||e.service;
    return `<div class="ev"><span class="et">${t}</span><span class="es">${nm}</span><span class="ek ${e.event}">${e.event.replace(/_/g," ")}</span><span class="ed">${e.detail||""}</span></div>`;
  }).join("");
}
async function clearEvents(){
  if(!confirm("Clear all watchdog events?"))return;
  try{await fetch("/api/events/clear",{method:"POST"});}catch(e){}
  allEvents=[];renderEvents();
}
async function tick(){
  try{
    const d=await(await fetch("/api/status")).json();
    SVCS=order(d.services);SVMAP={};SVCS.forEach(s=>SVMAP[s.id]=s);
    const up=SVCS.filter(s=>s.status==="online").length;
    document.getElementById("pill-up").textContent=up;
    document.getElementById("pill-tot").textContent="/ "+SVCS.length;
    window.__box=d.box||{};
    const bb=d.box||{};
    pushHist("cpu",bb.cpu_pct);
    pushHist("mem",(bb.mem_total&&bb.mem_free)?Math.round((bb.mem_total-bb.mem_free)/bb.mem_total*100):0);
    pushHist("rx",bb.net_rx);pushHist("tx",bb.net_tx);
    renderVitals(d.box||{},up,SVCS.length);
    if(detailSvc){const fresh=SVMAP[detailSvc.id];if(fresh)detailSvc=fresh;}
    if(document.querySelectorAll(".cf").length!==SVCS.length){center=Math.min(center,SVCS.length-1);buildFlow();}
    else{ // update status in place without rebuilding (keeps flow position)
      document.querySelectorAll(".cf").forEach((el,i)=>{
        const s=SVCS[i];if(!s)return;
        const stat=el.querySelector(".stat");
        stat.className="stat st-"+s.status;
        stat.innerHTML=`<span class="dot ${s.status}"></span>${s.status}`;
      });
    }
    allEvents=d.events||[];renderEvents();
  }catch(e){}
}
async function media(){
  try{
    const items=await(await fetch("/api/recent")).json();
    const g=document.getElementById("mediaGrid");
    if(!items.length){g.innerHTML='<div class="empty">Nothing generated yet.</div>';return;}
    g.innerHTML=items.map(m=>{
      const src="/media/"+m.kind+"/"+encodeURI(m.path);
      let inner=m.kind==="image"?`<img src="${src}" loading="lazy">`
        :m.kind==="video"?`<video src="${src}" muted playsinline preload="metadata"></video>`
        :`<div class="aud">♫</div>`;
      return `<a href="${src}" target="_blank" title="${m.name}">${inner}</a>`;
    }).join("");
  }catch(e){}
}
/* ---------- system overview modal ---------- */
let sysTimer=null;
const SYS={cpu:[],gpu:[],mem:[],rx:[],tx:[]};
function pushCap(a,v){a.push(v==null?0:v);if(a.length>90)a.shift();}
function openSystem(){document.getElementById("sysmodal").classList.add("open");sysTick();if(sysTimer)clearInterval(sysTimer);sysTimer=setInterval(sysTick,2500);}
function closeSystem(){document.getElementById("sysmodal").classList.remove("open");if(sysTimer){clearInterval(sysTimer);sysTimer=null;}}
async function sysTick(){
  let d;try{d=await(await fetch("/api/system")).json();}catch(e){return;}
  pushCap(SYS.cpu,d.cpu.pct);pushCap(SYS.gpu,d.gpu.util);pushCap(SYS.mem,d.mem.pct);pushCap(SYS.rx,d.net.rx);pushCap(SYS.tx,d.net.tx);
  document.getElementById("sys-chip").textContent=`${d.gpu.chip||"Mac"} · ${d.cpu.ncpu} CPU · ${d.gpu.cores||"?"}-core GPU · up ${fmtDur(d.uptime_s)}`;
  renderSystem(d);
}
function renderSystem(d){
  const rxMax=Math.max(1,...SYS.rx,...SYS.tx);
  const g=d.gpu||{},mem=d.mem||{},sw=d.swap||{};
  const disks=(d.disks||[]).map(dk=>{const cl=dk.pct>=90?"crit":dk.pct>=70?"hot":"";
    return `<div class="disk-row"><div class="dl"><span>${dk.label||dk.mount}</span><span>${fmtBytes(dk.free)} free · ${dk.pct}%</span></div><div class="bigmeter" style="height:10px"><span class="${cl}" style="width:${dk.pct}%"></span></div></div>`;}).join("")||'<div class="d-sub">–</div>';
  const files=(d.largest||[]).map(f=>`<div class="file-row" data-path="${encodeURIComponent(f.path)}"><span class="fn" title="${f.path}">${f.name}</span><span class="fp">${f.path.replace(/\/[^/]*$/,"")}</span><span class="fs">${fmtBytes(f.size)}</span><button class="del-btn" title="Delete file" onclick='deleteFile(${JSON.stringify(f.path)},${f.size})'>🗑</button></div>`).join("")||'<div class="d-sub">none over 2 GB</div>';
  document.getElementById("sys-grid").innerHTML=`
    <div class="sys-card span2"><h3>CPU <span class="big">${d.cpu.pct??"–"}%</span></h3>
      ${areaGraph(SYS.cpu,"var(--accent)",{h:80,lg:true,max:100,cap:"CPU %",cur:(d.cpu.pct??"–")+"%"})}
      <div style="margin-top:12px">${coreMeters(d.cpu.cores)}</div>
      <div class="d-sub" style="margin-top:8px">load ${(d.cpu.load||[]).join(" / ")}</div>
    </div>
    <div class="sys-card"><h3>GPU <span class="big">${g.util??"–"}%</span></h3>
      <div class="gpu-big">
        <div class="gpu-ring" style="--p:${g.util||0}"><b>${g.util??"–"}%</b></div>
        <div class="kv" style="flex:1">
          <span class="k">chip</span><span class="v">${g.chip||"–"}</span>
          <span class="k">gpu cores</span><span class="v">${g.cores??"–"}</span>
          <span class="k">gpu memory</span><span class="v">${fmtBytes(g.mem_inuse)}</span>
          <span class="k">tiler</span><span class="v">${g.tiler??"–"}%</span>
          <span class="k">renderer</span><span class="v">${g.renderer??"–"}%</span>
        </div>
      </div>
      ${areaGraph(SYS.gpu,"#c084fc",{h:44,max:100,cap:"GPU %",cur:(g.util??"–")+"%"})}
    </div>
    <div class="sys-card"><h3>Memory <span class="big">${mem.pct??"–"}%</span></h3>
      ${areaGraph(SYS.mem,"#38bdf8",{h:44,max:100,cap:"MEM %",cur:(mem.pct??"–")+"%"})}
      <div class="kv" style="margin-top:10px">
        <span class="k">used</span><span class="v">${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}</span>
        <span class="k">available</span><span class="v">${fmtBytes(mem.available)}</span>
        <span class="k">wired</span><span class="v">${fmtBytes(mem.wired)}</span>
        <span class="k">swap</span><span class="v">${fmtBytes(sw.used)} / ${fmtBytes(sw.total)} (${sw.pct??0}%)</span>
      </div>
    </div>
    <div class="sys-card"><h3>Disks</h3>${disks}</div>
    <div class="sys-card"><h3>Network &amp; Disk I/O</h3>
      ${areaGraph(SYS.rx,"#3ad07f",{h:40,max:rxMax,cap:"net ↓",cur:fmtBytes(d.net.rx)+"/s"})}
      <div style="height:6px"></div>
      ${areaGraph(SYS.tx,"#f4b740",{h:40,max:rxMax,cap:"net ↑",cur:fmtBytes(d.net.tx)+"/s"})}
      <div class="kv" style="margin-top:10px"><span class="k">disk read</span><span class="v">${fmtBytes(d.diskio.read)}/s</span>
      <span class="k">disk write</span><span class="v">${fmtBytes(d.diskio.write)}/s</span></div>
    </div>
    <div class="sys-card"><h3>Top by CPU</h3>${procRows(d.top_cpu)}</div>
    <div class="sys-card"><h3>Top by memory</h3>${procRowsMem(d.top_mem)}</div>
    <div class="sys-card span2"><h3>Largest files <span class="big" style="font-size:11px;color:var(--ink3)">Spotlight · &gt; 2 GB</span></h3>${files}</div>`;
}

async function deleteFile(path,size){
  if(!confirm(`Permanently delete this file?\n\n${path}\n${fmtBytes(size)}\n\nThis cannot be undone.`))return;
  try{
    const r=await fetch("/api/delete-file",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path})});
    const d=await r.json();
    if(d.ok){const row=document.querySelector(`.file-row[data-path="${encodeURIComponent(path)}"]`);if(row)row.remove();}
    else alert("Delete failed: "+(d.error||"unknown"));
  }catch(e){alert("Delete failed: "+e.message);}
}
async function activity(){
  try{
    const items=await(await fetch("/api/activity")).json();
    const el=document.getElementById("activity");
    if(!items||!items.length){el.innerHTML="";return;}
    el.innerHTML=items.map(a=>{
      // Only show a real bar once sampling reports >0; before that (model
      // loading) show an animated indeterminate bar so it clearly reads busy.
      const pct=(Number.isFinite(a.progress)&&a.progress>0)?a.progress:null;
      const bar=pct!=null?`<span style="width:${pct}%"></span>`:`<span class="indet"></span>`;
      const step=a.max_steps?` · ${a.step}/${a.max_steps}`:"";
      const pr=a.prompt?" · "+a.prompt.slice(0,64):"";
      return `<div class="act"><div class="ai" style="background:${a.color}22;color:${a.color}">${a.icon}</div>
        <div style="min-width:0"><div class="an">${a.name} — working</div><div class="ap">${(a.phase||"")}${step}${pr}</div></div>
        <div class="abar">${bar}</div><div class="apct">${pct!=null?pct.toFixed(0)+"%":"…"}</div></div>`;
    }).join("");
  }catch(e){}
}
tick();media();activity();
setInterval(tick,5000);
setInterval(media,15000);
setInterval(activity,2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            data = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/ping":
            self._json({"ok": True})
        elif self.path == "/api/status":
            self._json(services_payload())
        elif self.path == "/api/system":
            self._json(system_payload())
        elif self.path == "/api/activity":
            self._json(activity_payload())
        elif self.path.startswith("/api/detail"):
            from urllib.parse import urlparse, parse_qs
            sid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            self._json(service_detail(sid))
        elif self.path == "/api/recent":
            self._json(recent_media())
        elif self.path.startswith("/media/"):
            parts = self.path[len("/media/"):].split("/", 1)
            if len(parts) != 2:
                self._json({"error": "bad path"}, 400)
                return
            kind, rel = parts[0], urllib.request.url2pathname(parts[1])
            root = MUSIC_OUTPUT if kind == "audio" else OUTPUT_DIR
            p = (root / rel).resolve()
            try:
                p.relative_to(root.resolve())
            except ValueError:
                self._json({"error": "forbidden"}, 403)
                return
            if not p.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            data = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/events/clear":
            self._json({"ok": clear_events()})
            return
        if self.path == "/api/delete-file":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                self._json({"error": "bad request"}, 400)
                return
            raw = body.get("path", "")
            p = Path(raw)
            # Safety: must be an existing regular file (not a symlink or dir),
            # under the user's home or an external /Volumes mount.
            try:
                is_link = p.is_symlink()
                rp = p.resolve()
                allowed = (str(rp).startswith(str(Path.home()) + "/")
                           or str(rp).startswith("/Volumes/"))
                if is_link or not rp.is_file() or not allowed:
                    self._json({"error": "not allowed"}, 400)
                    return
                rp.unlink()
                _LF_CACHE["ts"] = 0.0  # force largest-files rescan
                log_event("system", "file_deleted", str(rp))
                self._json({"ok": True})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if self.path.startswith("/api/restart"):
            from urllib.parse import urlparse, parse_qs
            sid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            svc = SERVICE_BY_ID.get(sid)
            if not svc:
                self._json({"error": "unknown service"}, 404)
                return
            if not svc.get("restart"):
                self._json({"error": "service has no restart command"}, 400)
                return
            log_event(sid, "restart", "manual (dashboard)")
            ok = attempt_restart(svc)
            with state_lock:
                STATE[sid]["restarts"] += 1
            self._json({"ok": ok})
        else:
            self._json({"error": "not found"}, 404)


class ThreadingHTTPServer(HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        threading.Thread(target=self._handle, args=(request, client_address),
                         daemon=True).start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


if __name__ == "__main__":
    threading.Thread(target=watchdog_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[+] Mac Mini Board → http://{BOX_IP}:{PORT}  (watchdog: "
          f"{'auto-restart' if AUTO_RESTART else 'alert-only'}, poll {POLL_INTERVAL}s)")
    server.serve_forever()
