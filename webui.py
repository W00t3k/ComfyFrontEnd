#!/usr/bin/env python3
"""ComfyUI Web Studio — browser-based generation interface."""

import json
import argparse
import asyncio
import logging
import aiohttp
import mimetypes
import os
import random
import threading
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_DIR = Path(os.environ.get("COMFY_DIR", Path(__file__).resolve().parent))
OUTPUT_DIR = COMFY_DIR / "output"
HISTORY_FILE = COMFY_DIR / "generation_history.jsonl"
PORT = int(os.environ.get("IMAGE_STUDIO_PORT", "8190"))
HOST = os.environ.get("IMAGE_STUDIO_HOST", "0.0.0.0")
DEBUG = os.environ.get("STUDIO_DEBUG", "0").lower() in ("1", "true", "yes", "on")

jobs = {}
jobs_lock = threading.Lock()
downloads = {}
downloads_lock = threading.Lock()

MODELS = {
    "flux2_klein": {
        "label": "FLUX.2 Klein 4B",
        "desc": "Modern fast default · 4 steps",
        "file": "diffusion_models/flux-2-klein-4b.safetensors",
        "default_steps": 4,
        "architecture": "flux2_klein",
        "use_guidance": False,
        "prefix": "flux2_klein",
        "clip_type": "flux2",
        "vae": "flux2-vae.safetensors",
        "clip1": "qwen_3_4b_fp4_flux2.safetensors",
        "clip2": None,
        "clip_loader": "CLIPLoader",
    },
    "flux_schnell": {
        "label": "FLUX.1 Schnell",
        "desc": "Fast drafts · 4 steps",
        "file": "diffusion_models/flux1-schnell.safetensors",
        "default_steps": 4,
        "use_guidance": False,
        "prefix": "flux_schnell",
        "clip_type": "flux",
        "vae": "ae.safetensors",
        "clip1": "clip_l.safetensors",
        "clip2": "t5xxl_fp16.safetensors",
        "clip_loader": "DualCLIPLoader",
    },
    "flux_dev": {
        "label": "FLUX.1 Dev",
        "desc": "High-quality realism · 25 steps",
        "file": "diffusion_models/flux1-dev.safetensors",
        "default_steps": 25,
        "use_guidance": True,
        "guidance": 3.5,
        "prefix": "flux_dev",
        "clip_type": "flux",
        "vae": "ae.safetensors",
        "clip1": "clip_l.safetensors",
        "clip2": "t5xxl_fp16.safetensors",
        "clip_loader": "DualCLIPLoader",
    },
    "qwen": {
        "label": "Qwen Image 2512",
        "desc": "Alternative arch · 6 steps",
        "file": "diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors",
        "default_steps": 6,
        "use_guidance": False,
        "prefix": "qwen_image",
        "clip_type": "qwen_image",
        "vae": "qwen_image_vae.safetensors",
        "clip1": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "clip2": None,
        "clip_loader": "CLIPLoader",
        "lora": "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
    },
}

STYLES = [
    ("lifelike",   "⚡ Super Lifelike",    ""),  # dynamic — built at enhance time
    ("realistic",  "Photo Realistic",     "RAW photo, 35mm f/1.8 lens, natural available light, realistic skin texture, real-world imperfection, photojournalism documentary style, subtle film grain, photorealistic"),
    ("cinematic",  "Cinematic",           "cinematic 35mm film still, anamorphic lens, dramatic practical lighting, rich shadows, shallow depth of field, film grain, movie color grade, photorealistic"),
    ("portrait",   "Studio Portrait",     "professional studio portrait, controlled softbox lighting, razor-sharp focus, natural skin texture, pores visible, photorealistic, 85mm f/1.4 lens"),
    ("street",     "Street Photography",  "candid street photography, Leica M, 35mm f/2, harsh available light, motion, gritty urban, raw unedited look, photojournalism"),
    ("golden",     "Golden Hour",         "golden hour photography, warm directional sunlight, long shadows, lens flare, 50mm f/1.4, Kodak Portra 400, photorealistic"),
    ("lowlight",   "Low Light / Neon",    "low light photography, practical neon reflections, ISO 3200, 35mm f/1.4, visible grain, dark moody atmosphere, photorealistic"),
    ("product",    "Product Photo",       "professional product photography, studio softbox lighting, sharp focus, clean background, realistic reflections and shadows, 100mm macro, commercial photo"),
    ("cyberpunk",  "Cyberpunk Realistic", "realistic documentary photograph, glowing monitors, terminal windows, tangled cables, dim room with practical neon glow, 35mm film grain, photojournalism"),
]

STYLE_MAP = {k: (label, prefix) for k, label, prefix in STYLES}

RANDOM_SUBJECTS = [
    "a weathered fisherman mending nets on a fog-covered dock at dawn",
    "an elderly woman tending a flower stall in a narrow European alley, overcast light",
    "a street musician playing saxophone in a rain-soaked New Orleans street at night",
    "a lone surfer carrying a board through beach mist at sunrise",
    "a chef plating food in a busy restaurant kitchen, steam and motion",
    "a mechanic working under a vintage car in a cluttered garage, oil-stained hands",
    "a child chasing pigeons in a sun-drenched Rome piazza",
    "a construction worker on a steel beam high above a foggy city skyline",
    "an old neon-lit diner counter at 2am, coffee cup and cigarette smoke",
    "a Japanese salaryman sleeping on a packed Tokyo subway car",
    "a beekeeper lifting a honeycomb frame, bees swarming in golden afternoon light",
    "a shepherd moving a flock across a misty Scottish hillside",
    "a boxer resting in the corner of a ring between rounds, sweat and exhaustion",
    "a street food vendor in Bangkok, wok flames blazing at dusk",
    "a bookshop owner reading behind a towering stack of old novels, dust motes in light",
    "a woman running through a rainstorm in a city at night, headlights reflecting on wet asphalt",
    "a tattoo artist at work in a dimly lit studio, extreme close up on hands",
    "a farmer's market stall at golden hour, vivid vegetables and weathered hands",
    "a surfer underwater, light refracting through a breaking wave",
    "an abandoned amusement park at dusk, peeling paint and overgrown grass",
    "a jazz club interior, dim light, smoke, bassist performing on stage",
    "a polar explorer in a blizzard, face obscured, wind-blown snow",
    "a grandmother and granddaughter cooking together in a small sunlit kitchen",
    "a long-haul trucker at a desert rest stop, vast sky behind",
    "a monk walking through misty mountain temple steps at dawn",
]

# Camera/lens/film combos keyed by detected subject type
_CAMERA_PROFILES = {
    "person":    [
        ("Sony A7R V", "85mm f/1.4 G Master", "Kodak Portra 400"),
        ("Leica M11", "50mm Summilux f/1.4", "Kodak Tri-X 400"),
        ("Fujifilm GFX 100S", "110mm f/2", "Fujifilm Pro 400H"),
    ],
    "street":    [
        ("Leica M11", "35mm Summicron f/2", "Kodak Tri-X 400"),
        ("Ricoh GR IIIx", "40mm f/2.8", "Ilford HP5"),
        ("Fujifilm X-Pro3", "23mm f/1.4", "Kodak Ultramax 400"),
    ],
    "nature":    [
        ("Nikon Z9", "24mm f/1.8 S", "Fujifilm Velvia 50"),
        ("Canon EOS R5", "16-35mm f/2.8L", "Kodak Ektar 100"),
        ("Sony A1", "20mm f/1.8 G", "Fujifilm Provia 100F"),
    ],
    "food":      [
        ("Canon EOS R5", "100mm macro f/2.8L", "Kodak Portra 160"),
        ("Sony A7R V", "90mm macro f/2.8", "Fujifilm Pro 400H"),
    ],
    "architecture": [
        ("Phase One IQ4", "23mm f/5.6 Rodenstock", "Kodak Ektar 100"),
        ("Nikon Z9", "14-24mm f/2.8 S", "Fujifilm Velvia 50"),
    ],
    "default":   [
        ("Fujifilm GFX 100S", "55mm f/1.7", "Kodak Portra 160"),
        ("Sony A7R V", "50mm f/1.2 GM", "Kodak Portra 400"),
        ("Leica Q3", "28mm f/1.7 Summilux", "Kodak Tri-X 400"),
    ],
}

_LIGHTING_PHRASES = [
    "golden hour directional light", "soft overcast diffused light",
    "dramatic window light from the left", "harsh midday sun",
    "warm tungsten practical lighting", "blue-hour ambient light",
    "dappled shade through trees", "stormy flat grey light",
]

_SUBJECT_KEYWORDS = {
    "person": ["man", "woman", "person", "people", "child", "boy", "girl", "worker",
               "chef", "musician", "monk", "soldier", "farmer", "boxer", "grandmother"],
    "street": ["street", "city", "urban", "alley", "market", "diner", "neon", "rain",
               "subway", "tokyo", "paris", "london", "new york", "bangkok"],
    "nature": ["forest", "mountain", "ocean", "beach", "sunrise", "sunset", "fog",
               "wilderness", "field", "valley", "river", "lake", "cliff", "wave"],
    "food":   ["food", "dish", "coffee", "bread", "fruit", "vegetable", "plate",
               "bowl", "cooking", "kitchen", "cafe"],
    "architecture": ["building", "bridge", "cathedral", "tower", "temple", "skyline",
                     "interior", "facade", "corridor", "staircase"],
}


def detect_subject_type(prompt: str) -> str:
    p = prompt.lower()
    for stype, keywords in _SUBJECT_KEYWORDS.items():
        if any(k in p for k in keywords):
            return stype
    return "default"


def enhance_prompt(prompt: str) -> str:
    """Expand a simple prompt into a rich photography description."""
    stype = detect_subject_type(prompt)
    profiles = _CAMERA_PROFILES.get(stype, _CAMERA_PROFILES["default"])
    camera, lens, film = random.choice(profiles)
    lighting = random.choice(_LIGHTING_PHRASES)

    technical = (
        f"shot on {camera}, {lens} lens, {film} film simulation, "
        f"{lighting}, shallow depth of field, natural bokeh, "
        f"realistic skin texture and surface detail, subtle film grain, "
        f"photorealistic, no AI artifacts, no HDR look, "
        f"documentary photography style, 8K resolution"
    )
    return f"{prompt}, {technical}"


def model_files(mid):
    """Return every file required to actually generate with a model."""
    m = MODELS.get(mid, {})
    if not m:
        return []
    files = [m["file"], f'text_encoders/{m["clip1"]}', f'vae/{m["vae"]}']
    if m.get("clip2"):
        files.append(f'text_encoders/{m["clip2"]}')
    if m.get("lora"):
        files.append(f'loras/{m["lora"]}')
    return files


def model_missing(mid):
    return [rel for rel in model_files(mid)
            if not (COMFY_DIR / "models" / rel).is_file()
            or (COMFY_DIR / "models" / rel).stat().st_size < 1024]


def model_available(mid):
    return bool(model_files(mid)) and not model_missing(mid)


DOWNLOADERS = {
    "flux2_klein": "download_flux2_models.sh",
    "flux_schnell": "download_flux_schnell_models.sh",
    "flux_dev": "download_flux_dev_models.sh",
    "qwen": "download_qwen_image_2512.sh",
}


def run_download(mid):
    script = COMFY_DIR / DOWNLOADERS[mid]
    with downloads_lock:
        downloads[mid] = {"status": "running", "log": [], "started": time.time()}
    try:
        proc = subprocess.Popen(
            [str(script)], cwd=COMFY_DIR, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "COMFY": str(COMFY_DIR)},
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            with downloads_lock:
                downloads[mid]["log"] = (downloads[mid]["log"] + [line.rstrip()])[-80:]
        code = proc.wait()
        with downloads_lock:
            downloads[mid]["status"] = "done" if code == 0 else "error"
            downloads[mid]["exit_code"] = code
            downloads[mid]["missing"] = model_missing(mid)
    except Exception as exc:
        logging.exception("Model download failed: %s", mid)
        with downloads_lock:
            downloads[mid].update(status="error", error=str(exc))


def build_workflow(mid, prompt, width, height, steps, seed, guidance=3.5):
    m = MODELS[mid]
    if m.get("architecture", "").startswith("flux2_"):
        return build_flux2_workflow(mid, prompt, width, height, steps, seed, guidance)
    unet = m["file"].split("/")[-1]
    wf = {}

    wf["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}}
    model_ref = ["1", 0]

    if m.get("lora"):
        wf["10"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["1", 0], "lora_name": m["lora"], "strength_model": 1.0}
        }
        model_ref = ["10", 0]

    if m["clip_loader"] == "DualCLIPLoader":
        wf["2"] = {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": m["clip1"], "clip_name2": m["clip2"], "type": m["clip_type"]
        }}
    else:
        wf["2"] = {"class_type": "CLIPLoader", "inputs": {
            "clip_name": m["clip1"], "type": m["clip_type"], "device": "default"
        }}

    wf["3"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}}
    wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}}
    wf["5"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}

    positive_ref = ["3", 0]
    if m.get("use_guidance"):
        wf["11"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["3", 0], "guidance": guidance}}
        positive_ref = ["11", 0]

    wf["6"] = {"class_type": "KSampler", "inputs": {
        "model": model_ref,
        "positive": positive_ref,
        "negative": ["4", 0],
        "latent_image": ["5", 0],
        "seed": seed,
        "steps": steps,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "denoise": 1.0,
    }}

    wf["7"] = {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}}
    wf["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["7", 0]}}
    wf["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": m["prefix"]}}
    return wf


def build_flux2_workflow(mid, prompt, width, height, steps, seed, guidance=4.0):
    """Native FLUX.2 graph based on the official ComfyUI workflow templates."""
    m = MODELS[mid]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": m["file"].split("/")[-1], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": m["clip1"], "type": "flux2", "device": "default"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "5": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "Flux2Scheduler", "inputs": {
            "steps": steps, "width": width, "height": height}},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["11", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {
            "images": ["12", 0], "filename_prefix": m["prefix"]}},
    }
    if m["architecture"] == "flux2_dev":
        wf["14"] = {"class_type": "FluxGuidance", "inputs": {
            "conditioning": ["3", 0], "guidance": guidance}}
        wf["9"] = {"class_type": "BasicGuider", "inputs": {
            "model": ["1", 0], "conditioning": ["14", 0]}}
    else:
        wf["15"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
        wf["9"] = {"class_type": "CFGGuider", "inputs": {
            "model": ["1", 0], "positive": ["3", 0], "negative": ["15", 0], "cfg": 1.0}}
    wf["10"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["6", 0], "guider": ["9", 0], "sampler": ["7", 0],
        "sigmas": ["8", 0], "latent_image": ["5", 0]}}
    return wf


def comfy_queue(workflow, client_id):
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(COMFY_URL + "/prompt", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["prompt_id"]


def comfy_status(prompt_id):
    with urllib.request.urlopen(COMFY_URL + "/history/" + prompt_id, timeout=10) as r:
        h = json.loads(r.read())
    return h.get(prompt_id)


async def _progress_socket(job_id, client_id, ready):
    ws_url = COMFY_URL.replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/ws?clientId=" + urllib.parse.quote(client_id)
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(ws_url, heartbeat=20) as ws:
                ready.set()
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(message.data)
                    kind, data = event.get("type"), event.get("data", {})
                    with jobs_lock:
                        job = jobs.get(job_id)
                        if not job or job.get("status") in ("done", "error"):
                            return
                        prompt_ids = job.get("prompt_ids", [])
                        prompt_id = data.get("prompt_id")
                        index = prompt_ids.index(prompt_id) if prompt_id in prompt_ids else job.get("done", 0)
                        total = max(1, job.get("total", 1))
                        if kind == "progress" and data.get("max"):
                            value, maximum = data.get("value", 0), data["max"]
                            job["step"] = value
                            job["max_steps"] = maximum
                            job["phase"] = f"Sampling step {value}/{maximum}"
                            job["progress"] = round(100 * (index + value / maximum) / total, 1)
                        elif kind == "executing" and data.get("node") is not None:
                            job["phase"] = "Loading/processing node " + str(data["node"])
    except Exception as exc:
        ready.set()
        if DEBUG:
            logging.warning("Progress WebSocket ended: %s", exc)


def progress_socket(job_id, client_id, ready):
    asyncio.run(_progress_socket(job_id, client_id, ready))


def run_job(job_id, requests):
    with jobs_lock:
        jobs[job_id]["status"] = "queued"

    client_id = str(uuid.uuid4())
    socket_ready = threading.Event()
    threading.Thread(target=progress_socket, args=(job_id, client_id, socket_ready), daemon=True).start()
    socket_ready.wait(timeout=10)

    prompt_ids = []
    seeds_used = []
    for req in requests:
        seed = req.get("seed") or random.randint(0, 2**32 - 1)
        seeds_used.append(seed)
        wf = build_workflow(
            req["model"], req["prompt"],
            req["width"], req["height"], req["steps"],
            seed, req.get("guidance", 3.5)
        )
        try:
            pid = comfy_queue(wf, client_id)
            prompt_ids.append(pid)
        except Exception as e:
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)
            return

    with jobs_lock:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["prompt_ids"] = prompt_ids
        jobs[job_id]["seeds"] = seeds_used
        jobs[job_id]["client_id"] = client_id
        jobs[job_id]["progress"] = 0
        jobs[job_id]["phase"] = "Model loading"

    remaining = set(prompt_ids)
    for _ in range(1800):
        time.sleep(2)
        if not remaining:
            break
        for pid in list(remaining):
            try:
                item = comfy_status(pid)
                if item:
                    st = item.get("status", {})
                    if st.get("status_str") == "error":
                        messages = st.get("messages", [])
                        detail = "ComfyUI reported an error"
                        if DEBUG and messages:
                            detail = json.dumps(messages[-1], default=str)[:2000]
                        with jobs_lock:
                            jobs[job_id]["status"] = "error"
                            jobs[job_id]["error"] = detail
                        return
                    remaining.discard(pid)
                    with jobs_lock:
                        jobs[job_id]["done"] = len(prompt_ids) - len(remaining)
            except Exception:
                pass

    if remaining:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Timed out"
        return

    with jobs_lock:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["done"] = len(prompt_ids)

    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prompt": requests[0]["prompt"],
            "model": requests[0]["model"],
            "seeds": seeds_used,
            "width": requests[0]["width"],
            "height": requests[0]["height"],
            "steps": requests[0]["steps"],
        }
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def list_images():
    imgs = []
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.rglob("*.png"):
            if f.stat().st_size > 1024:
                imgs.append({"name": f.name, "mtime": f.stat().st_mtime, "size": f.stat().st_size})
    imgs.sort(key=lambda x: x["mtime"], reverse=True)
    return imgs


def load_history():
    if not HISTORY_FILE.exists():
        return []
    entries = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return list(reversed(entries))[:50]


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ComfyUI Studio</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
/* ---- SHAKE ---- */
@keyframes shake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-5px)} 40%,80%{transform:translateX(5px)} }
.shake { animation: shake 0.35s ease; border-color: var(--error) !important; }
/* ---- TOAST ---- */
#toast-container { position:fixed; bottom:20px; right:20px; z-index:300; display:flex; flex-direction:column; gap:8px; pointer-events:none; }
.toast { padding:10px 16px; background:var(--surface); border:1px solid var(--border2); border-radius:var(--radius-sm); font-size:13px; color:var(--text); box-shadow:0 4px 20px rgba(0,0,0,0.5); animation:toastIn .2s ease; }
@keyframes toastIn { from{opacity:0;transform:translateX(30px)} to{opacity:1;transform:none} }
.toast.success { border-color:var(--success); color:var(--success); }
.toast.error   { border-color:var(--error);   color:var(--error);   }

:root {
  --bg: #080808;
  --surface: #111;
  --surface2: #181818;
  --surface3: #202020;
  --border: #252525;
  --border2: #333;
  --text: #f0f0f0;
  --text2: #999;
  --text3: #555;
  --accent: #7c6ff7;
  --accent2: #a78bfa;
  --accent-glow: rgba(124,111,247,0.18);
  --success: #34d399;
  --error: #f87171;
  --warn: #fbbf24;
  --sidebar: 300px;
  --radius: 10px;
  --radius-sm: 6px;
}

html, body { height: 100%; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* HEADER */
header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 20px;
  height: 52px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  z-index: 20;
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.02em;
  color: var(--text);
}
.logo-icon {
  width: 28px;
  height: 28px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
}
.server-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 20px;
  font-size: 12px;
  color: var(--text2);
}
.dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--text3);
  transition: background 0.3s;
}
.dot.online { background: var(--success); box-shadow: 0 0 6px var(--success); }
.dot.error { background: var(--error); }

header .spacer { flex: 1; }
.header-btn {
  padding: 6px 14px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text2);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.header-btn:hover { border-color: var(--border2); color: var(--text); }
.header-btn.active { border-color: var(--accent); color: var(--accent); background: var(--accent-glow); }

/* LAYOUT */
.app {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* SIDEBAR */
.sidebar {
  width: var(--sidebar);
  flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sidebar::-webkit-scrollbar { width: 4px; }
.sidebar::-webkit-scrollbar-track { background: transparent; }
.sidebar::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; }

.section-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--text3);
  text-transform: uppercase;
  margin-bottom: 8px;
}

/* MODEL CARDS */
.model-list { display: flex; flex-direction: column; gap: 6px; }
.model-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}
.model-card:hover:not(.unavailable) { border-color: var(--border2); background: var(--surface2); }
.model-card.selected { border-color: var(--accent); background: var(--accent-glow); }
.model-card.unavailable { opacity: 0.4; cursor: not-allowed; }
.model-radio {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 2px solid var(--border2);
  flex-shrink: 0;
  transition: all 0.15s;
}
.model-card.selected .model-radio {
  border-color: var(--accent);
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent-glow);
}
.model-info { flex: 1; min-width: 0; }
.model-name { font-size: 13px; font-weight: 600; color: var(--text); }
.model-desc { font-size: 11px; color: var(--text2); margin-top: 1px; }
.avail-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.avail-dot.ok { background: var(--success); }
.avail-dot.missing { background: var(--text3); }

/* GALLERY TOOLBAR */
.gallery-toolbar { display:flex; align-items:center; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
.filter-tabs { display:flex; gap:4px; }
.filter-tab { padding:4px 12px; border:1px solid var(--border); border-radius:20px; background:transparent; color:var(--text2); font-size:12px; cursor:pointer; transition:all .15s; }
.filter-tab:hover { border-color:var(--border2); color:var(--text); }
.filter-tab.active { border-color:var(--accent); color:var(--accent); background:var(--accent-glow); }
.sort-sel { background:var(--surface2); border:1px solid var(--border); border-radius:var(--radius-sm); color:var(--text2); padding:4px 8px; font-size:12px; cursor:pointer; }
.sort-sel:focus { outline:none; border-color:var(--accent); }
.toolbar-spacer { flex:1; }
.btn-select { padding:5px 12px; border:1px solid var(--border); border-radius:var(--radius-sm); background:transparent; color:var(--text2); font-size:12px; cursor:pointer; transition:all .15s; }
.btn-select:hover { border-color:var(--border2); color:var(--text); }
.btn-select.active { border-color:var(--accent); color:var(--accent); background:var(--accent-glow); }
.btn-bulk-del { padding:5px 12px; border:1px solid var(--error); border-radius:var(--radius-sm); background:rgba(248,113,113,0.1); color:var(--error); font-size:12px; cursor:pointer; display:none; transition:all .15s; }
.btn-bulk-del:hover { background:rgba(248,113,113,0.2); }

/* CARD OVERLAY */
.img-overlay { position:absolute; inset:0 0 32px 0; background:rgba(0,0,0,0.72); opacity:0; transition:opacity .15s; display:flex; align-items:center; justify-content:center; gap:10px; border-radius:var(--radius) var(--radius) 0 0; pointer-events:none; }
.img-card:hover .img-overlay { opacity:1; }
.img-action { pointer-events:auto; width:38px; height:38px; display:flex; align-items:center; justify-content:center; background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.18); border-radius:50%; color:#fff; font-size:15px; cursor:pointer; text-decoration:none; transition:all .15s; }
.img-action:hover { background:rgba(255,255,255,0.25); transform:scale(1.1); }
.img-action.del:hover { background:rgba(248,113,113,0.35); border-color:var(--error); }

/* SELECT CHECKBOX */
.select-cb { position:absolute; top:8px; left:8px; width:20px; height:20px; border:2px solid rgba(255,255,255,0.6); border-radius:4px; background:rgba(0,0,0,0.5); z-index:2; display:flex; align-items:center; justify-content:center; transition:all .15s; }
.select-cb.checked { background:var(--accent); border-color:var(--accent); }
.select-cb.checked::after { content:'✓'; color:#fff; font-size:11px; font-weight:700; }
.img-card.sel-active { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-glow); }

/* LIGHTBOX NAV */
.lb-nav { position:absolute; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.15); border-radius:50%; width:48px; height:48px; display:flex; align-items:center; justify-content:center; color:#fff; cursor:pointer; font-size:22px; transition:all .15s; z-index:1; }
.lb-nav:hover { background:rgba(255,255,255,0.22); }
#lb-prev { left:20px; }
#lb-next { right:20px; }
.lb-nav.hidden { opacity:0; pointer-events:none; }

/* DANGER BTN */
.btn-danger { padding:8px 16px; background:rgba(248,113,113,0.1); border:1px solid var(--error); border-radius:var(--radius-sm); color:var(--error); font-size:13px; cursor:pointer; transition:all .15s; }
.btn-danger:hover { background:rgba(248,113,113,0.2); }

/* STYLE SELECT */
.style-select {
  width: 100%;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 8px 10px;
  font-size: 13px;
  appearance: none;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 30px;
}
.style-select:focus { outline: none; border-color: var(--accent); }
.style-select option { background: var(--surface2); }

/* QUALITY SLIDER */
.quality-row { display: flex; align-items: center; gap: 10px; }
.quality-label { font-size: 11px; color: var(--text3); white-space: nowrap; }
input[type=range] {
  -webkit-appearance: none;
  flex: 1;
  height: 3px;
  background: var(--border2);
  border-radius: 3px;
  outline: none;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.steps-val {
  font-size: 12px;
  color: var(--accent);
  font-weight: 600;
  min-width: 28px;
  text-align: right;
}

/* ASPECT */
.aspect-row { display: flex; gap: 8px; }
.aspect-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 8px 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--surface2);
  color: var(--text2);
  font-size: 11px;
}
.aspect-btn:hover { border-color: var(--border2); }
.aspect-btn.selected { border-color: var(--accent); color: var(--accent); background: var(--accent-glow); }
.aspect-icon {
  background: currentColor;
  border-radius: 2px;
}
.aspect-icon.sq  { width: 22px; height: 22px; }
.aspect-icon.wd  { width: 30px; height: 18px; }
.aspect-icon.pt  { width: 18px; height: 30px; }

/* BATCH + SEED */
.row2 { display: flex; gap: 8px; }
.field { display: flex; flex-direction: column; gap: 5px; flex: 1; }
.field label { font-size: 11px; color: var(--text3); }
.field input[type=number], .field input[type=text] {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
  width: 100%;
}
.field input:focus { outline: none; border-color: var(--accent); }

.seed-row { display: flex; gap: 6px; align-items: flex-end; }
.seed-row input { flex: 1; }
.lock-btn {
  padding: 7px 10px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text2);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  transition: all 0.15s;
  flex-shrink: 0;
}
.lock-btn:hover { border-color: var(--border2); }
.lock-btn.locked { border-color: var(--accent); color: var(--accent); background: var(--accent-glow); }

/* GUIDANCE */
.guidance-row { display: flex; flex-direction: column; gap: 6px; }
.guidance-inner { display: flex; align-items: center; gap: 10px; }
.guidance-val { font-size: 12px; color: var(--accent); font-weight: 600; min-width: 28px; }

/* MAIN */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
}

.prompt-area {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  flex-shrink: 0;
}
.prompt-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.toolbar-btn {
  padding: 5px 12px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text2);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.toolbar-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-glow); }
#enhance-status { font-size: 11px; color: var(--text3); }
textarea#prompt {
  width: 100%;
  min-height: 90px;
  max-height: 180px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 14px;
  font-family: inherit;
  padding: 12px 14px;
  resize: vertical;
  line-height: 1.6;
  transition: border-color 0.15s;
}
textarea#prompt:focus { outline: none; border-color: var(--accent); }
textarea#prompt::placeholder { color: var(--text3); }

.action-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
  align-items: center;
}
.btn-generate {
  padding: 10px 28px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border: none;
  border-radius: var(--radius-sm);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  letter-spacing: 0.01em;
  box-shadow: 0 0 20px rgba(124,111,247,0.3);
}
.btn-generate:hover { transform: translateY(-1px); box-shadow: 0 0 28px rgba(124,111,247,0.45); }
.btn-generate:active { transform: translateY(0); }
.btn-generate:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.btn-secondary {
  padding: 10px 16px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text2);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-secondary:hover { border-color: var(--border2); color: var(--text); }

/* PROGRESS */
.progress-bar-wrap {
  display: none;
  padding: 10px 24px 0;
  flex-shrink: 0;
}
.progress-bar-wrap.visible { display: block; }
.progress-track {
  height: 3px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
  position: relative;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  border-radius: 3px;
  transition: width 0.4s ease;
  position: relative;
}
.progress-fill.active {
  width: 100% !important;
  background: repeating-linear-gradient(110deg, var(--accent) 0 18px, var(--accent2) 18px 36px);
  background-size: 72px 100%;
  animation: progress-active 1s linear infinite;
}
@keyframes progress-active { to { background-position: 72px 0; } }
.progress-fill::after {
  content: '';
  position: absolute;
  right: 0; top: 0;
  width: 60px; height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.4));
  animation: shimmer 1.2s infinite;
}
@keyframes shimmer { 0%{opacity:0} 50%{opacity:1} 100%{opacity:0} }
.progress-text {
  font-size: 12px;
  color: var(--text2);
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.spinner {
  width: 12px; height: 12px;
  border: 2px solid var(--border2);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* GALLERY */
.gallery-wrap {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 20px 24px;
}
.gallery-wrap::-webkit-scrollbar { width: 6px; }
.gallery-wrap::-webkit-scrollbar-track { background: transparent; }
.gallery-wrap::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; }
.gallery-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.gallery-header h2 { font-size: 13px; font-weight: 600; color: var(--text2); }
.img-count { font-size: 12px; color: var(--text3); }
#gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}
.img-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}
.img-card:hover { transform: translateY(-2px); border-color: var(--border2); box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
.img-card img {
  width: 100%;
  display: block;
  aspect-ratio: 1;
  object-fit: cover;
}
.img-card-meta {
  padding: 7px 9px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.img-card-name { font-size: 10px; color: var(--text3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
.img-card-size { font-size: 10px; color: var(--text3); flex-shrink: 0; margin-left: 6px; }
.img-new-badge {
  position: absolute;
  top: 7px; left: 7px;
  background: var(--accent);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.empty-state {
  grid-column: 1/-1;
  text-align: center;
  padding: 60px 20px;
  color: var(--text3);
  font-size: 13px;
}

/* LIGHTBOX */
#lb {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.92);
  z-index: 100;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 14px;
}
#lb.open { display: flex; }
#lb img { max-width: 90vw; max-height: 82vh; border-radius: var(--radius); object-fit: contain; }
#lb-name { font-size: 12px; color: var(--text3); }
#lb-actions { display: flex; gap: 8px; }
#lb-dl {
  padding: 8px 20px;
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: var(--radius-sm);
  color: var(--text2);
  text-decoration: none;
  font-size: 13px;
  transition: all 0.15s;
  cursor: pointer;
}
#lb-dl:hover { color: var(--text); border-color: var(--accent); }
.lb-close {
  position: fixed; top: 18px; right: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 50%;
  width: 34px; height: 34px;
  display: flex; align-items: center; justify-content: center;
  color: var(--text2);
  cursor: pointer;
  font-size: 16px;
  transition: all 0.15s;
}
.lb-close:hover { border-color: var(--border2); color: var(--text); }

/* HISTORY PANEL */
#hist-panel {
  position: fixed;
  top: 52px; right: 0; bottom: 0;
  width: 420px;
  background: var(--surface);
  border-left: 1px solid var(--border);
  transform: translateX(100%);
  transition: transform 0.25s ease;
  z-index: 30;
  display: flex;
  flex-direction: column;
}
#hist-panel.open { transform: translateX(0); }
.hist-header {
  padding: 16px 18px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hist-header h2 { font-size: 14px; font-weight: 600; }
.hist-list { flex: 1; overflow-y: auto; padding: 10px; }
.hist-item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.hist-item:hover { border-color: var(--border2); background: var(--surface2); }
.hist-item-top { display: flex; gap: 8px; align-items: center; margin-bottom: 5px; }
.hist-tag {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 10px;
  font-weight: 600;
}
.hist-tag.flux_schnell { background: rgba(99,102,241,0.2); color: #818cf8; }
.hist-tag.flux_dev { background: rgba(124,58,237,0.2); color: #a78bfa; }
.hist-tag.qwen { background: rgba(16,185,129,0.2); color: #34d399; }
.hist-ts { font-size: 10px; color: var(--text3); margin-left: auto; }
.hist-prompt { font-size: 12px; color: var(--text2); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.hist-meta { font-size: 10px; color: var(--text3); margin-top: 4px; }
.hist-replay {
  font-size: 11px;
  color: var(--accent);
  cursor: pointer;
  margin-top: 6px;
  display: inline-block;
}
.hist-replay:hover { text-decoration: underline; }
.hist-empty { text-align: center; padding: 40px 20px; color: var(--text3); font-size: 13px; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">⬡</div>
    ComfyUI Studio
  </div>
  <div class="server-badge">
    <div class="dot" id="server-dot"></div>
    <span id="server-status">checking...</span>
  </div>
  <div class="spacer"></div>
  <a class="header-btn" id="hub-link" href="#" style="text-decoration:none">Hub</a>
  <a class="header-btn" id="video-link" href="#" style="text-decoration:none">Video</a>
  <button class="header-btn" id="hist-toggle-btn" onclick="toggleHistory()">History</button>
</header>

<div class="app">
  <aside class="sidebar">

    <div>
      <div class="section-label">Model</div>
      <div class="model-list" id="model-list"></div>
    </div>

    <div>
      <div class="section-label">Style</div>
      <select class="style-select" id="style-select"></select>
    </div>

    <div>
      <div class="section-label">Quality</div>
      <div class="quality-row">
        <span class="quality-label">Fast</span>
        <input type="range" id="steps-slider" min="1" max="40" value="4">
        <span class="quality-label">Max</span>
        <span class="steps-val" id="steps-val">4</span>
      </div>
    </div>

    <div>
      <div class="section-label">Aspect Ratio</div>
      <div class="aspect-row">
        <button class="aspect-btn selected" onclick="setAspect('square',this)">
          <span class="aspect-icon sq"></span>Square
        </button>
        <button class="aspect-btn" onclick="setAspect('wide',this)">
          <span class="aspect-icon wd"></span>Wide
        </button>
        <button class="aspect-btn" onclick="setAspect('portrait',this)">
          <span class="aspect-icon pt"></span>Portrait
        </button>
      </div>
    </div>

    <div id="guidance-section" style="display:none">
      <div class="section-label">Guidance Scale</div>
      <div class="guidance-row">
        <div class="guidance-inner">
          <span class="quality-label">Loose</span>
          <input type="range" id="guidance-slider" min="1" max="6" step="0.1" value="3.5">
          <span class="quality-label">Strict</span>
          <span class="guidance-val" id="guidance-val">3.5</span>
        </div>
      </div>
    </div>

    <div>
      <div class="section-label">Batch &amp; Seed</div>
      <div class="row2">
        <div class="field">
          <label>Images</label>
          <input type="number" id="batch-count" value="1" min="1" max="9">
        </div>
        <div class="field">
          <label>Seed (blank = random)</label>
          <div class="seed-row">
            <input type="text" id="seed-input" placeholder="random">
            <button class="lock-btn" id="lock-btn" onclick="toggleLock()" title="Lock seed">🔒</button>
          </div>
        </div>
      </div>
    </div>

  </aside>

  <main class="main">
    <div class="prompt-area">
      <div class="prompt-toolbar">
        <button class="toolbar-btn" onclick="randomPrompt()" title="Roll a random subject">🎲 Random</button>
        <button class="toolbar-btn" id="enhance-btn" onclick="enhancePrompt()" title="Expand your prompt with camera, lens, film details">✨ Enhance</button>
        <span id="enhance-status"></span>
      </div>
      <textarea id="prompt" placeholder="Describe your image… hit ✨ Enhance to expand it with camera + film details"></textarea>
      <div class="action-row">
        <button class="btn-generate" id="gen-btn" onclick="generate()">Generate</button>
        <button class="btn-secondary" onclick="compare()">Compare Models</button>
        <button class="btn-secondary" onclick="clearGallery()">Clear</button>
      </div>
    </div>

    <div class="progress-bar-wrap" id="prog-wrap">
      <div class="progress-track">
        <div class="progress-fill" id="prog-fill" style="width:0%"></div>
      </div>
      <div class="progress-text">
        <span class="spinner"></span>
        <span id="prog-text">Queuing…</span>
      </div>
    </div>

    <div class="gallery-wrap">
      <div class="gallery-toolbar">
        <div class="filter-tabs">
          <button class="filter-tab active" onclick="setFilter('all',this)">All</button>
          <button class="filter-tab" onclick="setFilter('flux_dev',this)">Flux Dev</button>
          <button class="filter-tab" onclick="setFilter('flux2_klein',this)">Klein</button>
          <button class="filter-tab" onclick="setFilter('flux_schnell',this)">Schnell</button>
          <button class="filter-tab" onclick="setFilter('qwen_image',this)">Qwen</button>
        </div>
        <select class="sort-sel" onchange="setSort(this.value)">
          <option value="newest">Newest</option>
          <option value="oldest">Oldest</option>
        </select>
        <span class="toolbar-spacer"></span>
        <span class="img-count" id="img-count" style="font-size:12px;color:var(--text3);"></span>
        <button class="btn-select" id="select-btn" onclick="toggleSelectMode()">Select</button>
        <button class="btn-bulk-del" id="bulk-del-btn" onclick="bulkDelete()">Delete selected</button>
        <button class="btn-secondary" onclick="clearGallery()" style="font-size:12px;padding:5px 12px;">Clear view</button>
      </div>
      <div id="gallery"><div class="empty-state">Generate something to see results here.</div></div>
    </div>
  </main>
</div>

<!-- Lightbox -->
<div id="lb" onclick="if(event.target===this)closeLb()">
  <button class="lb-close" onclick="closeLb()">✕</button>
  <button class="lb-nav hidden" id="lb-prev" onclick="lbNav(-1)">&#8249;</button>
  <img id="lb-img" src="" alt="">
  <button class="lb-nav hidden" id="lb-next" onclick="lbNav(1)">&#8250;</button>
  <span id="lb-name"></span>
  <div id="lb-actions">
    <a id="lb-dl" href="#" download class="btn-secondary">Download</a>
    <button class="btn-danger" onclick="deleteLbImage()">Delete</button>
  </div>
</div>

<div id="toast-container"></div>

<!-- History Panel -->
<div id="hist-panel">
  <div class="hist-header">
    <h2>Generation History</h2>
    <button class="header-btn" onclick="toggleHistory()">Close</button>
  </div>
  <div class="hist-list" id="hist-list"></div>
</div>

<script>
// State
let selectedModel = null;
let seedLocked = false;
let aspect = 'square';
let allImages = [];
let knownImages = new Set();
let newlyArrived = new Set();
let firstGalleryLoad = true;
let filterModel = 'all';
let sortOrder = 'newest';
let lbImages = [];
let lbIndex = -1;
let selectMode = false;
let selectedSet = new Set();
let currentJobId = null;
let pollTimer = null;

const STYLE_PREFIXES = __STYLE_MAP__;
const MODELS_META = __MODELS_META__;

// Init
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('hub-link').href = `${location.protocol}//${location.hostname}:8189`;
  document.getElementById('video-link').href = `${location.protocol}//${location.hostname}:8192`;
  buildModelList();
  buildStyleSelect();
  setupSliders();
  checkServer();
  refreshGallery();
  setInterval(checkServer, 10000);
  setInterval(refreshGallery, 4000);
});

function buildModelList() {
  const el = document.getElementById('model-list');
  MODELS_META.forEach((m, i) => {
    const card = document.createElement('div');
    card.className = 'model-card' + (m.available ? '' : ' unavailable');
    card.dataset.id = m.id;
    card.innerHTML = `
      <div class="model-radio"></div>
      <div class="model-info">
        <div class="model-name">${m.label}</div>
        <div class="model-desc">${m.desc}</div>
      </div>
      <div class="avail-dot ${m.available ? 'ok' : 'missing'}" title="${m.available ? 'Available' : 'Missing: ' + m.missing.join(', ')}"></div>
    `;
    if (m.available) {
      card.onclick = () => selectModel(m.id, card);
      if (!selectedModel) selectModel(m.id, card);
    } else if (m.downloadable) {
      card.classList.remove('unavailable');
      card.title = 'Click to download missing model files';
      card.onclick = () => downloadModel(m.id, card);
    }
    el.appendChild(card);
  });
}

async function downloadModel(id, card) {
  if (!confirm('Download the missing files for this model? Large models can use 20–40 GB.')) return;
  card.style.opacity = '.65';
  try {
    const r = await fetch('/api/download/' + encodeURIComponent(id), {method: 'POST'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Download could not start');
    showToast('Download started. You can keep this page open.', 'success');
    const timer = setInterval(async () => {
      const status = await fetch('/api/download/' + encodeURIComponent(id)).then(x => x.json());
      if (status.status === 'done') { clearInterval(timer); location.reload(); }
      if (status.status === 'error') {
        clearInterval(timer); card.style.opacity = '';
        showToast(status.error || (status.log || []).slice(-1)[0] || 'Download failed', 'error');
      }
    }, 3000);
  } catch (e) {
    card.style.opacity = '';
    showToast(e.message, 'error');
  }
}

function selectModel(id, card) {
  document.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  selectedModel = id;

  const m = MODELS_META.find(x => x.id === id);
  const slider = document.getElementById('steps-slider');
  slider.value = m.default_steps;
  document.getElementById('steps-val').textContent = m.default_steps;

  const guidanceSection = document.getElementById('guidance-section');
  guidanceSection.style.display = m.use_guidance ? '' : 'none';
}

function buildStyleSelect() {
  const sel = document.getElementById('style-select');
  Object.entries(STYLE_PREFIXES).forEach(([k, v]) => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = v.label;
    sel.appendChild(opt);
  });
}

function setupSliders() {
  const slider = document.getElementById('steps-slider');
  const val = document.getElementById('steps-val');
  slider.addEventListener('input', () => val.textContent = slider.value);

  const gSlider = document.getElementById('guidance-slider');
  const gVal = document.getElementById('guidance-val');
  gSlider.addEventListener('input', () => gVal.textContent = parseFloat(gSlider.value).toFixed(1));
}

function setAspect(a, btn) {
  aspect = a;
  document.querySelectorAll('.aspect-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
}

function toggleLock() {
  seedLocked = !seedLocked;
  const btn = document.getElementById('lock-btn');
  btn.classList.toggle('locked', seedLocked);
  btn.title = seedLocked ? 'Seed locked — same seed every run' : 'Lock seed';
}

async function checkServer() {
  const dot = document.getElementById('server-dot');
  const status = document.getElementById('server-status');
  try {
    const r = await fetch('/api/server_status');
    const d = await r.json();
    if (d.online) {
      dot.className = 'dot online';
      status.textContent = 'Connected';
    } else {
      dot.className = 'dot error';
      status.textContent = 'ComfyUI offline';
    }
  } catch {
    dot.className = 'dot error';
    status.textContent = 'Error';
  }
}

function getDimensions() {
  const size = 1024;
  if (aspect === 'wide')    return [1344, 768];
  if (aspect === 'portrait') return [768, 1344];
  return [size, size];
}

async function randomPrompt() {
  try {
    const r = await fetch('/api/random_prompt');
    const d = await r.json();
    document.getElementById('prompt').value = d.prompt;
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

async function enhancePrompt() {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) {
    const ta = document.getElementById('prompt');
    ta.classList.add('shake'); ta.focus();
    setTimeout(() => ta.classList.remove('shake'), 400);
    return;
  }
  const btn = document.getElementById('enhance-btn');
  const status = document.getElementById('enhance-status');
  btn.disabled = true;
  status.textContent = 'Enhancing…';
  try {
    const r = await fetch('/api/enhance', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt})
    });
    const d = await r.json();
    if (d.enhanced) {
      document.getElementById('prompt').value = d.enhanced;
      status.textContent = '✓ Enhanced';
      setTimeout(() => status.textContent = '', 3000);
    }
  } catch(e) { status.textContent = 'Error'; }
  btn.disabled = false;
}

async function generate(overrides={}) {
  if (!selectedModel) { showToast('Select a model first', 'error'); return; }
  const prompt = overrides.prompt || document.getElementById('prompt').value.trim();
  if (!prompt) {
    const ta = document.getElementById('prompt');
    ta.classList.add('shake');
    ta.focus();
    setTimeout(() => ta.classList.remove('shake'), 400);
    return;
  }

  const styleKey = document.getElementById('style-select').value;
  let fullPrompt;
  if (styleKey === 'lifelike') {
    // Enhance on the fly for Super Lifelike
    try {
      const r = await fetch('/api/enhance', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt})
      });
      const d = await r.json();
      fullPrompt = d.enhanced || prompt;
    } catch { fullPrompt = prompt; }
  } else {
    const stylePrefix = STYLE_PREFIXES[styleKey]?.prefix || '';
    fullPrompt = stylePrefix ? `${stylePrefix}, ${prompt}` : prompt;
  }

  const steps = parseInt(document.getElementById('steps-slider').value);
  const guidance = parseFloat(document.getElementById('guidance-slider').value);
  const batch = parseInt(document.getElementById('batch-count').value) || 1;
  const seedRaw = document.getElementById('seed-input').value.trim();
  const seed = seedRaw ? parseInt(seedRaw) : null;
  const [width, height] = getDimensions();

  const models = overrides.models || [overrides.model || selectedModel];

  const requests = [];
  for (const model of models) {
    for (let i = 0; i < (models.length > 1 ? 1 : batch); i++) {
      requests.push({ model, prompt: fullPrompt, width, height, steps, seed, guidance });
    }
  }

  setGenerating(true);

  try {
    const r = await fetch('/api/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({requests})
    });
    const d = await r.json();
    if (d.error) { showToast('Error: ' + d.error, 'error'); setGenerating(false); return; }
    currentJobId = d.job_id;
    pollJob(d.job_id, requests.length);
  } catch (e) {
    showToast('Could not reach server: ' + e.message, 'error');
    setGenerating(false);
  }
}

function compare() {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) {
    const ta = document.getElementById('prompt');
    ta.classList.add('shake'); ta.focus();
    setTimeout(() => ta.classList.remove('shake'), 400);
    return;
  }
  const available = MODELS_META.filter(m => m.available).map(m => m.id);
  if (available.length < 2) { showToast('Need at least 2 models downloaded to compare', 'error'); return; }
  generate({models: available, prompt});
}

function pollJob(jobId, total) {
  if (pollTimer) clearInterval(pollTimer);
  const progFill = document.getElementById('prog-fill');
  const progText = document.getElementById('prog-text');
  const startedAt = Date.now();

  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/job/' + jobId);
      const d = await r.json();
      const done = d.done || 0;
      const pct = Number.isFinite(d.progress) ? d.progress : (total > 0 ? Math.round((done / total) * 100) : 0);
      progFill.style.width = pct + '%';

      if (d.status === 'done') {
        progFill.classList.remove('active');
        progText.textContent = `Done! ${total} image${total!==1?'s':''} generated.`;
        progFill.style.width = '100%';
        clearInterval(pollTimer);
        setTimeout(() => setGenerating(false), 1800);
        if (d.seeds && d.seeds.length > 0) {
          const s = d.seeds[0];
          document.getElementById('seed-input').value = s;
          if (!seedLocked) {
            document.getElementById('seed-input').placeholder = `last: ${s}`;
          }
        }
      } else if (d.status === 'error') {
        progFill.classList.remove('active');
        progText.textContent = 'Error: ' + (d.error || 'unknown');
        clearInterval(pollTimer);
        setGenerating(false);
      } else {
        progFill.classList.remove('active');
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = String(elapsed % 60).padStart(2, '0');
        const phase = d.phase || 'Model loading';
        const step = d.max_steps ? ` · ${d.step}/${d.max_steps} steps` : '';
        progText.textContent = `${phase}${step} · ${pct.toFixed(0)}% · ${minutes}:${seconds} elapsed`;
      }
    } catch { }
  }, 1500);
}

function setGenerating(on) {
  document.getElementById('gen-btn').disabled = on;
  document.getElementById('prog-wrap').classList.toggle('visible', on);
  if (!on) {
    document.getElementById('prog-fill').classList.remove('active');
    document.getElementById('prog-fill').style.width = '0%';
    document.getElementById('prog-text').textContent = 'Queuing…';
  }
}

// Gallery
function showToast(msg, type = 'info') {
  const c = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = 'toast' + (type !== 'info' ? ' ' + type : '');
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 2400);
}

function setFilter(model, btn) {
  filterModel = model;
  document.querySelectorAll('.filter-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderGallery();
}

function setSort(val) {
  sortOrder = val;
  renderGallery();
}

function getFilteredImages() {
  let imgs = [...allImages];
  if (filterModel !== 'all') imgs = imgs.filter(img => img.name.startsWith(filterModel));
  if (sortOrder === 'oldest') imgs.reverse();
  return imgs;
}

function renderGallery() {
  const images = getFilteredImages();
  lbImages = images;
  const gallery = document.getElementById('gallery');
  document.getElementById('img-count').textContent = images.length + ' image' + (images.length !== 1 ? 's' : '');

  if (images.length === 0) {
    gallery.innerHTML = '<div class="empty-state">No images' + (filterModel !== 'all' ? ' for this filter' : '') + '.</div>';
    return;
  }

  gallery.innerHTML = images.map((img, idx) => {
    const isNew = newlyArrived.has(img.name);
    const isSel = selectedSet.has(img.name);
    const kb = Math.round(img.size / 1024);
    const enc = encodeURIComponent(img.name);
    const safeName = img.name.replace(/'/g, "\\'");
    return `<div class="img-card${isSel ? ' sel-active' : ''}" data-name="${img.name}" onclick="${selectMode ? `toggleCardSelect('${safeName}')` : `openLb(${idx})`}">
      ${isNew ? '<div class="img-new-badge">New</div>' : ''}
      ${selectMode ? `<div class="select-cb${isSel ? ' checked' : ''}"></div>` : ''}
      <img src="/img/${enc}" loading="lazy" alt="${img.name}">
      <div class="img-overlay">
        <button class="img-action" onclick="event.stopPropagation();openLb(${idx})" title="View">&#128269;</button>
        <a class="img-action" href="/img/${enc}" download="${img.name}" onclick="event.stopPropagation()" title="Download">&#8595;</a>
        <button class="img-action del" onclick="event.stopPropagation();deleteImage('${safeName}')" title="Delete">&#128465;</button>
      </div>
      <div class="img-card-meta">
        <span class="img-card-name" title="${img.name}">${img.name}</span>
        <span class="img-card-size">${kb}KB</span>
      </div>
    </div>`;
  }).join('');
}

async function refreshGallery() {
  try {
    const r = await fetch('/api/images');
    const data = await r.json();
    const brandNew = data.filter(img => !knownImages.has(img.name));
    brandNew.forEach(img => knownImages.add(img.name));
    newlyArrived = firstGalleryLoad ? new Set() : new Set(brandNew.map(i => i.name));
    firstGalleryLoad = false;
    allImages = data;
    renderGallery();
  } catch {}
}

function clearGallery() {
  knownImages.clear();
  newlyArrived.clear();
  firstGalleryLoad = true;
  refreshGallery();
}

// Select mode
function toggleSelectMode() {
  selectMode = !selectMode;
  selectedSet.clear();
  const btn = document.getElementById('select-btn');
  const dBtn = document.getElementById('bulk-del-btn');
  btn.classList.toggle('active', selectMode);
  btn.textContent = selectMode ? 'Cancel' : 'Select';
  dBtn.style.display = 'none';
  renderGallery();
}

function toggleCardSelect(name) {
  if (selectedSet.has(name)) selectedSet.delete(name);
  else selectedSet.add(name);
  const dBtn = document.getElementById('bulk-del-btn');
  dBtn.textContent = `Delete ${selectedSet.size} selected`;
  dBtn.style.display = selectedSet.size > 0 ? '' : 'none';
  renderGallery();
}

async function bulkDelete() {
  if (selectedSet.size === 0) return;
  const toDelete = [...selectedSet];
  if (!confirm(`Permanently delete ${toDelete.length} image${toDelete.length > 1 ? 's' : ''}?`)) return;
  let deleted = 0;
  for (const name of toDelete) {
    try {
      const r = await fetch('/api/image/' + encodeURIComponent(name), {method: 'DELETE'});
      if (r.ok) { deleted++; knownImages.delete(name); }
    } catch {}
  }
  showToast(`Deleted ${deleted} image${deleted !== 1 ? 's' : ''}`, 'success');
  selectMode = false;
  selectedSet.clear();
  document.getElementById('select-btn').classList.remove('active');
  document.getElementById('select-btn').textContent = 'Select';
  document.getElementById('bulk-del-btn').style.display = 'none';
  await refreshGallery();
}

async function deleteImage(name) {
  if (!confirm(`Delete ${name}?`)) return;
  try {
    const r = await fetch('/api/image/' + encodeURIComponent(name), {method: 'DELETE'});
    if (r.ok) {
      knownImages.delete(name);
      allImages = allImages.filter(img => img.name !== name);
      selectedSet.delete(name);
      showToast('Deleted', 'success');
      // Close lb if we deleted the open image
      const lbName = document.getElementById('lb-name').textContent;
      if (lbName === name) closeLb();
      renderGallery();
    } else {
      showToast('Delete failed', 'error');
    }
  } catch { showToast('Delete failed', 'error'); }
}

async function deleteLbImage() {
  const name = document.getElementById('lb-name').textContent;
  if (name) await deleteImage(name);
}

// Lightbox
function openLb(idx) {
  lbIndex = idx;
  const img = lbImages[idx];
  if (!img) return;
  const src = '/img/' + encodeURIComponent(img.name);
  document.getElementById('lb-img').src = src;
  document.getElementById('lb-name').textContent = img.name;
  document.getElementById('lb-dl').href = src;
  document.getElementById('lb-dl').download = img.name;
  document.getElementById('lb').classList.add('open');
  document.getElementById('lb-prev').classList.toggle('hidden', idx <= 0);
  document.getElementById('lb-next').classList.toggle('hidden', idx >= lbImages.length - 1);
}
function closeLb() {
  document.getElementById('lb').classList.remove('open');
  document.getElementById('lb-img').src = '';
}
function lbNav(dir) {
  const next = lbIndex + dir;
  if (next >= 0 && next < lbImages.length) openLb(next);
}
document.addEventListener('keydown', e => {
  const lbOpen = document.getElementById('lb').classList.contains('open');
  if (lbOpen) {
    if (e.key === 'ArrowLeft')  { lbNav(-1); return; }
    if (e.key === 'ArrowRight') { lbNav(1);  return; }
    if (e.key === 'Escape') { closeLb(); return; }
  }
  if (e.key === 'Escape' && document.getElementById('hist-panel').classList.contains('open')) toggleHistory();
});

// History
async function toggleHistory() {
  const panel = document.getElementById('hist-panel');
  const btn = document.getElementById('hist-toggle-btn');
  const open = panel.classList.toggle('open');
  btn.classList.toggle('active', open);
  if (open) await loadHistory();
}

async function loadHistory() {
  const list = document.getElementById('hist-list');
  try {
    const r = await fetch('/api/history');
    const entries = await r.json();
    if (entries.length === 0) {
      list.innerHTML = '<div class="hist-empty">No history yet.</div>';
      return;
    }
    list.innerHTML = entries.map((e, i) => {
      const seeds = e.seeds || [];
      const seed = seeds[0] || '?';
      const ts = (e.ts || '').replace('T', ' ').replace('Z', '').slice(0, 16);
      const tagClass = (e.model || 'flux_schnell').replace('.', '_');
      const modelLabel = MODELS_META.find(m=>m.id===e.model)?.label || e.model || 'Unknown';
      return `
        <div class="hist-item">
          <div class="hist-item-top">
            <span class="hist-tag ${tagClass}">${modelLabel}</span>
            <span class="hist-ts">${ts}</span>
          </div>
          <div class="hist-prompt">${e.prompt || ''}</div>
          <div class="hist-meta">${e.width||'?'}×${e.height||'?'} · ${e.steps||'?'} steps · seed ${seed}</div>
          <span class="hist-replay" onclick="replayEntry(${JSON.stringify(e).replace(/"/g,'&quot;')})">↩ Replay</span>
        </div>
      `;
    }).join('');
  } catch {
    list.innerHTML = '<div class="hist-empty">Could not load history.</div>';
  }
}

function replayEntry(e) {
  document.getElementById('prompt').value = e.prompt || '';
  const seeds = e.seeds || [];
  if (seeds.length > 0) document.getElementById('seed-input').value = seeds[0];
  const steps = e.steps || 8;
  document.getElementById('steps-slider').value = steps;
  document.getElementById('steps-val').textContent = steps;
  if (e.model) {
    const card = document.querySelector(`.model-card[data-id="${e.model}"]`);
    if (card && !card.classList.contains('unavailable')) selectModel(e.model, card);
  }
  toggleHistory();
  window.scrollTo(0, 0);
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if DEBUG:
            logging.info("%s - %s", self.client_address[0], fmt % args)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, body):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(b))
        self.end_headers()
        self.wfile.write(b)

    def serve_file(self, path):
        mime, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", ""):
            models_meta = [
                {
                    "id": mid,
                    "label": m["label"],
                    "desc": m["desc"],
                    "available": model_available(mid),
                    "missing": model_missing(mid),
                    "downloadable": mid in DOWNLOADERS,
                    "default_steps": m["default_steps"],
                    "use_guidance": m.get("use_guidance", False),
                }
                for mid, m in MODELS.items()
            ]
            style_map = {k: {"label": label, "prefix": prefix} for k, label, prefix in STYLES}
            html = HTML.replace("__MODELS_META__", json.dumps(models_meta))
            html = html.replace("__STYLE_MAP__", json.dumps(style_map))
            self.send_html(html)

        elif path == "/api/server_status":
            try:
                with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=3) as response:
                    stats = json.loads(response.read())
                self.send_json({"online": True, "backend": COMFY_URL,
                                "system": stats if DEBUG else None})
            except Exception as exc:
                self.send_json({"online": False, "backend": COMFY_URL,
                                "error": str(exc) if DEBUG else "Backend unavailable"})

        elif path == "/api/debug":
            self.send_json({
                "debug": DEBUG, "backend": COMFY_URL, "studio": {"host": HOST, "port": PORT},
                "models": {mid: {"ready": model_available(mid), "missing": model_missing(mid)}
                           for mid in MODELS},
                "downloads": downloads,
            })

        elif path.startswith("/api/download/"):
            mid = path.removeprefix("/api/download/")
            with downloads_lock:
                state = dict(downloads.get(mid, {"status": "idle"}))
            state["missing"] = model_missing(mid) if mid in MODELS else []
            self.send_json(state)

        elif path == "/api/images":
            self.send_json(list_images())

        elif path == "/api/history":
            self.send_json(load_history())

        elif path == "/api/random_prompt":
            self.send_json({"prompt": random.choice(RANDOM_SUBJECTS)})

        elif path.startswith("/api/job/"):
            job_id = path[9:]
            with jobs_lock:
                job = jobs.get(job_id, {})
            self.send_json(dict(job))

        elif path.startswith("/img/"):
            name = urllib.parse.unquote(path[5:])
            target = (OUTPUT_DIR / name).resolve()
            try:
                target.relative_to(OUTPUT_DIR.resolve())
            except ValueError:
                self.send_error(403)
                return
            if target.exists() and target.is_file():
                self.serve_file(target)
            else:
                self.send_error(404)

        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/generate":
            body = self.read_body()
            requests_list = body.get("requests", [])
            if not requests_list:
                self.send_json({"error": "no requests"}, 400)
                return
            invalid = [r.get("model") for r in requests_list
                       if r.get("model") not in MODELS or not model_available(r.get("model"))]
            if invalid:
                self.send_json({"error": "model unavailable", "models": invalid}, 409)
                return

            job_id = str(uuid.uuid4())
            with jobs_lock:
                jobs[job_id] = {
                    "status": "pending",
                    "total": len(requests_list),
                    "done": 0,
                    "seeds": [],
                    "error": None,
                }

            t = threading.Thread(target=run_job, args=(job_id, requests_list), daemon=True)
            t.start()
            self.send_json({"job_id": job_id})

        elif path == "/api/enhance":
            body = self.read_body()
            raw = body.get("prompt", "").strip()
            if not raw:
                self.send_json({"error": "no prompt"}, 400)
                return
            enhanced = enhance_prompt(raw)
            self.send_json({"enhanced": enhanced})

        elif path.startswith("/api/download/"):
            mid = path.removeprefix("/api/download/")
            if mid not in DOWNLOADERS:
                self.send_json({"error": "unknown model"}, 404)
                return
            if mid == "flux_dev" and not os.environ.get("HF_TOKEN"):
                self.send_json({"error": "FLUX Dev is gated; restart with HF_TOKEN set"}, 409)
                return
            with downloads_lock:
                if downloads.get(mid, {}).get("status") == "running":
                    self.send_json(downloads[mid], 202)
                    return
            threading.Thread(target=run_download, args=(mid,), daemon=True).start()
            self.send_json({"status": "starting", "model": mid}, 202)

        else:
            self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/image/"):
            name = urllib.parse.unquote(path[11:])
            if "/" in name or "\\" in name or name.startswith("."):
                self.send_error(400)
                return
            target = OUTPUT_DIR / name
            try:
                target.resolve().relative_to(OUTPUT_DIR.resolve())
            except ValueError:
                self.send_error(403)
                return
            if target.exists() and target.is_file() and target.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                target.unlink()
                self.send_json({"deleted": name})
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ComfyUI Image Studio")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--debug", action="store_true", default=DEBUG)
    args = parser.parse_args()
    HOST, PORT, DEBUG = args.host, args.port, args.debug
    logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[+] ComfyUI Studio → http://{HOST}:{PORT}")
    print(f"[+] ComfyUI backend → {COMFY_URL}")
    print(f"[+] Images from → {OUTPUT_DIR}")
    print(f"[+] Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Stopped.")
    finally:
        server.server_close()
