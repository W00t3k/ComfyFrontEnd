#!/usr/bin/env python3
"""AI Studio Hub — super page linking Image, Video, Music studios + ComfyUI."""

import json
import mimetypes
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HOST_IP = "192.168.2.69"
PORT = 8189

COMFY_DIR = Path.home() / "AI/ComfyUI"
OUTPUT_DIR = COMFY_DIR / "output"
MUSIC_OUTPUT = Path.home() / "AI/MusicStudio/output"

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

STUDIOS = [
    {"id": "images", "name": "Image Studio", "icon": "⬡", "color": "#7c6ff7",
     "desc": "Flux Dev / Schnell / Qwen — text to image",
     "url": f"http://{HOST_IP}:8190", "check": f"http://{HOST_IP}:8190/api/server_status"},
    {"id": "video", "name": "Video Studio", "icon": "▶", "color": "#f76f8e",
     "desc": "Wan 2.2 — text to video, image to video",
     "url": f"http://{HOST_IP}:8192", "check": f"http://{HOST_IP}:8192/api/server_status"},
    {"id": "music", "name": "Music Studio", "icon": "♫", "color": "#34d399",
     "desc": "Stems, generation, voice, mastering",
     "url": f"http://{HOST_IP}:8191", "check": f"http://{HOST_IP}:8191/"},
    {"id": "comfy", "name": "ComfyUI Raw", "icon": "⚙", "color": "#fbbf24",
     "desc": "Node graph — full control backend",
     "url": f"http://{HOST_IP}:8188", "check": f"http://{HOST_IP}:8188/system_stats"},
]


def check_url(url):
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


def recent_media():
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
    return items[:24]


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Studio Hub</title>
<style>
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
:root {
  --bg:#080808; --surface:#111; --surface2:#181818; --border:#252525; --border2:#333;
  --text:#f0f0f0; --text2:#999; --text3:#555;
  --success:#34d399; --error:#f87171; --radius:12px; --radius-sm:6px;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-size:14px;line-height:1.5;min-height:100vh}
.wrap{max-width:1100px;margin:0 auto;padding:40px 24px}
h1{font-size:26px;font-weight:800;letter-spacing:-.03em;margin-bottom:4px}
.sub{color:var(--text2);font-size:14px;margin-bottom:32px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-bottom:44px}
.card{display:block;text-decoration:none;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;transition:all .18s;position:relative}
.card:hover{transform:translateY(-3px);border-color:var(--border2);box-shadow:0 10px 30px rgba(0,0,0,.45)}
.card-icon{width:42px;height:42px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:12px;color:#fff}
.card-name{font-size:16px;font-weight:700;margin-bottom:2px}
.card-desc{font-size:12px;color:var(--text2)}
.card-status{position:absolute;top:16px;right:16px;display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text3)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--text3)}
.dot.online{background:var(--success);box-shadow:0 0 6px var(--success)}
.dot.offline{background:var(--error)}
.section-h{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.section-h h2{font-size:14px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.1em}
.count{font-size:12px;color:var(--text3)}
.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.media-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);overflow:hidden;transition:all .15s}
.media-card:hover{border-color:var(--border2);transform:translateY(-2px)}
.media-card img,.media-card video{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#000}
.media-card .audio-tile{width:100%;aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;background:linear-gradient(135deg,#0d2b21,#0a1a14);font-size:30px}
.media-card audio{width:100%;height:30px}
.media-label{padding:6px 8px;font-size:10px;color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kind-badge{position:absolute;top:6px;left:6px;font-size:9px;font-weight:700;padding:2px 6px;border-radius:8px;text-transform:uppercase;letter-spacing:.05em}
.media-card{position:relative}
.kind-badge.image{background:rgba(124,111,247,.85);color:#fff}
.kind-badge.video{background:rgba(247,111,142,.85);color:#fff}
.kind-badge.audio{background:rgba(52,211,153,.85);color:#04150e}
.empty{color:var(--text3);font-size:13px;padding:30px 0}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI Studio Hub</h1>
  <div class="sub">One place for everything — pick a studio or browse recent output.</div>

  <div class="grid" id="studios"></div>

  <div class="section-h"><h2>Recent Output</h2><span class="count" id="media-count"></span></div>
  <div class="media-grid" id="media"></div>
</div>

<script>
const STUDIOS = __STUDIOS__;

function render() {
  const g = document.getElementById('studios');
  g.innerHTML = '';
  STUDIOS.forEach(s => {
    const a = document.createElement('a');
    a.className = 'card';
    a.href = s.url;
    a.target = '_blank';
    a.innerHTML = `
      <div class="card-status"><span id="st-${s.id}-txt">…</span><div class="dot" id="st-${s.id}"></div></div>
      <div class="card-icon" style="background:${s.color}">${s.icon}</div>
      <div class="card-name">${s.name}</div>
      <div class="card-desc">${s.desc}</div>`;
    g.appendChild(a);
  });
}

async function checkStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    STUDIOS.forEach(s => {
      const dot = document.getElementById('st-' + s.id);
      const txt = document.getElementById('st-' + s.id + '-txt');
      const on = d[s.id];
      dot.className = 'dot ' + (on ? 'online' : 'offline');
      txt.textContent = on ? 'online' : 'offline';
    });
  } catch {}
}

async function loadMedia() {
  try {
    const r = await fetch('/api/recent');
    const items = await r.json();
    document.getElementById('media-count').textContent = items.length ? items.length + ' items' : '';
    const g = document.getElementById('media');
    g.innerHTML = items.length ? '' : '<div class="empty">Nothing generated yet.</div>';
    items.forEach(m => {
      const card = document.createElement('div');
      card.className = 'media-card';
      let inner;
      const src = '/media/' + m.kind + '/' + encodeURI(m.path);
      if (m.kind === 'image') inner = `<img src="${src}" loading="lazy">`;
      else if (m.kind === 'video') inner = `<video src="${src}" muted loop playsinline preload="metadata" onmouseover="this.play()" onmouseout="this.pause()"></video>`;
      else inner = `<div class="audio-tile">♫<audio src="${src}" controls preload="none"></audio></div>`;
      card.innerHTML = `<span class="kind-badge ${m.kind}">${m.kind}</span>${inner}<div class="media-label">${m.name}</div>`;
      g.appendChild(card);
    });
  } catch {}
}

render();
checkStatus();
loadMedia();
setInterval(checkStatus, 10000);
setInterval(loadMedia, 15000);
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
            page = HTML.replace("__STUDIOS__", json.dumps(
                [{k: s[k] for k in ("id", "name", "icon", "color", "desc", "url")} for s in STUDIOS]))
            data = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/status":
            self._json({s["id"]: check_url(s["check"]) for s in STUDIOS})
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
            if not str(p).startswith(str(root.resolve())) or not p.is_file():
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


class ThreadingHTTPServer(HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        threading.Thread(target=self._handle, args=(request, client_address), daemon=True).start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[+] AI Studio Hub running at http://{HOST_IP}:{PORT}")
    server.serve_forever()
