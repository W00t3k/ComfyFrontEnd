#!/usr/bin/env python3
"""Video Studio — browser UI for Wan 2.2 video generation via ComfyUI."""

import asyncio
import json
import logging
import mimetypes
import os
import random
import threading
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    import aiohttp
except ImportError:
    aiohttp = None

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_DIR = Path.home() / "AI/ComfyUI"
OUTPUT_DIR = COMFY_DIR / "output"
VIDEO_HISTORY_FILE = COMFY_DIR / "video_history.jsonl"
PORT = 8192

MODEL_FILE = "wan2.2_ti2v_5B_fp16.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE_FILE = "wan2.2_vae.safetensors"

NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，"
            "低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，"
            "毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")

RESOLUTIONS = [
    ("848x480",  "Landscape 480p · fast", 848, 480),
    ("480x848",  "Portrait 480p · fast", 480, 848),
    ("1280x704", "Landscape 720p · slow", 1280, 704),
    ("704x1280", "Portrait 720p · slow", 704, 1280),
    ("640x640",  "Square", 640, 640),
]

LENGTHS = [
    (33,  "1.4 s"),
    (49,  "2 s"),
    (81,  "3.4 s"),
    (121, "5 s"),
]

RANDOM_MOTION_PROMPTS = [
    "a golden retriever running through shallow ocean waves at sunset, water splashing in slow motion, cinematic",
    "steam rising from a coffee cup on a rainy windowsill, rain drops sliding down glass, shallow depth of field",
    "a woman's hair blowing in the wind on a cliff overlooking the sea, golden hour, slow camera push in",
    "neon signs reflecting in a puddle as a motorcycle drives through, night city, cinematic slow motion",
    "autumn leaves falling in a sunlit forest, camera slowly tracking forward, dust motes in light beams",
    "a chef flipping vegetables in a flaming wok, sparks and steam, dramatic kitchen lighting, slow motion",
    "waves crashing against a lighthouse in a storm, dramatic sky, spray frozen mid-air, cinematic",
    "a hummingbird hovering at a red flower, wings in motion blur, macro detail, morning light",
    "city timelapse at dusk, car light trails streaming through an intersection, buildings lighting up",
    "a paper boat drifting down a rain gutter stream, low angle, shallow focus, overcast soft light",
]

jobs = {}
jobs_lock = threading.Lock()


def models_available():
    return all([
        (COMFY_DIR / "models/diffusion_models" / MODEL_FILE).exists(),
        (COMFY_DIR / "models/text_encoders" / TEXT_ENCODER).exists(),
        (COMFY_DIR / "models/vae" / VAE_FILE).exists(),
    ])


def build_workflow(prompt, width, height, length, steps, seed, start_image=None):
    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": MODEL_FILE, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_FILE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": NEGATIVE}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
    }
    latent_inputs = {"vae": ["3", 0], "width": width, "height": height,
                     "length": length, "batch_size": 1}
    if start_image:
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        latent_inputs["start_image"] = ["10", 0]
    wf["7"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs}
    wf["8"] = {"class_type": "KSampler", "inputs": {
        "model": ["6", 0], "positive": ["4", 0], "negative": ["5", 0],
        "latent_image": ["7", 0], "seed": seed, "steps": steps, "cfg": 5.0,
        "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}}
    wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
    wf["11"] = {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 24.0}}
    wf["12"] = {"class_type": "SaveVideo", "inputs": {
        "video": ["11", 0], "filename_prefix": "video/wan22",
        "format": "mp4", "codec": "h264"}}
    return wf


def comfy_queue(workflow, client_id):
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(COMFY_URL + "/prompt", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["prompt_id"]


async def _progress_socket(job_id, client_id, ready):
    """Read per-step sampling progress from ComfyUI's WebSocket."""
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
                        if kind == "progress" and data.get("max"):
                            value, maximum = data.get("value", 0), data["max"]
                            job["step"] = value
                            job["max_steps"] = maximum
                            job["progress"] = round(100 * value / maximum, 1)
                            job["phase"] = f"Sampling {value}/{maximum}"
                        elif kind == "executing" and data.get("node") is not None:
                            job["phase"] = "Processing node " + str(data["node"])
    except Exception as exc:
        ready.set()
        logging.debug("Progress WebSocket ended: %s", exc)


def progress_socket(job_id, client_id, ready):
    asyncio.run(_progress_socket(job_id, client_id, ready))


def run_job(job_id, req):
    seed = req.get("seed") or random.randint(0, 2**32 - 1)
    wf = build_workflow(req["prompt"], req["width"], req["height"],
                        req["length"], req["steps"], seed, req.get("start_image"))

    client_id = str(uuid.uuid4())
    if aiohttp is not None:
        ready = threading.Event()
        threading.Thread(target=progress_socket,
                         args=(job_id, client_id, ready), daemon=True).start()
        ready.wait(timeout=10)

    try:
        pid = comfy_queue(wf, client_id)
    except Exception as e:
        with jobs_lock:
            jobs[job_id].update(status="error", error=str(e))
        return

    with jobs_lock:
        jobs[job_id].update(status="running", prompt_id=pid, seed=seed,
                            started=time.time(), progress=0, phase="Model loading")

    for _ in range(2700):
        time.sleep(4)
        try:
            with urllib.request.urlopen(COMFY_URL + "/history/" + pid, timeout=30) as r:
                history = json.loads(r.read())
        except Exception:
            continue
        item = history.get(pid)
        if not item:
            continue
        st = item.get("status", {})
        if st.get("status_str") == "error":
            msg = "ComfyUI error"
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    msg = f"{m[1].get('node_type')}: {m[1].get('exception_message', '')[:300]}"
            with jobs_lock:
                jobs[job_id].update(status="error", error=msg)
            return
        filename = None
        for node_output in item.get("outputs", {}).values():
            for key in ("images", "video", "gifs"):
                for out in node_output.get(key, []):
                    if out.get("filename", "").endswith(".mp4"):
                        filename = out["filename"]
        if not filename:
            with jobs_lock:
                jobs[job_id].update(status="error",
                                    error="ComfyUI finished but produced no video file")
            return
        with jobs_lock:
            jobs[job_id].update(status="done", filename=filename,
                                elapsed=int(time.time() - jobs[job_id]["started"]))
        try:
            entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "prompt": req["prompt"], "seed": seed,
                     "width": req["width"], "height": req["height"],
                     "length": req["length"], "steps": req["steps"]}
            with open(VIDEO_HISTORY_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        return

    with jobs_lock:
        jobs[job_id].update(status="error", error="Timed out")


def list_videos():
    vids = []
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.rglob("*.mp4"):
            vids.append({"name": f.name,
                         "subfolder": str(f.parent.relative_to(OUTPUT_DIR)) if f.parent != OUTPUT_DIR else "",
                         "mtime": f.stat().st_mtime, "size": f.stat().st_size})
    vids.sort(key=lambda x: x["mtime"], reverse=True)
    return vids


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Video Studio</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#080808; --surface:#111; --surface2:#181818; --border:#252525; --border2:#333;
  --text:#f0f0f0; --text2:#999; --text3:#555;
  --accent:#f76f8e; --accent2:#fa9db4; --accent-glow:rgba(247,111,142,0.18);
  --success:#34d399; --error:#f87171; --radius:10px; --radius-sm:6px;
}
html,body{height:100%}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-size:14px;line-height:1.5;display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:14px;padding:0 20px;height:52px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px}
.logo-icon{width:28px;height:28px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px}
.server-badge{display:flex;align-items:center;gap:6px;padding:4px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:20px;font-size:12px;color:var(--text2)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--text3);transition:background .3s}
.dot.online{background:var(--success);box-shadow:0 0 6px var(--success)}
.dot.error{background:var(--error)}
header .spacer{flex:1}
.header-btn{padding:6px 14px;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text2);font-size:13px;cursor:pointer;text-decoration:none}
.header-btn:hover{border-color:var(--border2);color:var(--text)}
.app{display:flex;flex:1;overflow:hidden}
.sidebar{width:300px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:20px}
.section-label{font-size:10px;font-weight:700;letter-spacing:.12em;color:var(--text3);text-transform:uppercase;margin-bottom:8px}
.opt-list{display:flex;flex-direction:column;gap:6px}
.opt-card{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;transition:all .15s}
.opt-card:hover{border-color:var(--border2);background:var(--surface2)}
.opt-card.selected{border-color:var(--accent);background:var(--accent-glow)}
.opt-radio{width:14px;height:14px;border-radius:50%;border:2px solid var(--border2);flex-shrink:0}
.opt-card.selected .opt-radio{border-color:var(--accent);background:var(--accent)}
.opt-name{font-size:13px;font-weight:600}
.opt-desc{font-size:11px;color:var(--text2)}
.pill-row{display:flex;gap:6px;flex-wrap:wrap}
.pill{padding:6px 12px;border:1px solid var(--border);border-radius:20px;background:transparent;color:var(--text2);font-size:12px;cursor:pointer;transition:all .15s}
.pill:hover{border-color:var(--border2);color:var(--text)}
.pill.selected{border-color:var(--accent);color:var(--accent);background:var(--accent-glow)}
.quality-row{display:flex;align-items:center;gap:10px}
.quality-label{font-size:11px;color:var(--text3);white-space:nowrap}
input[type=range]{-webkit-appearance:none;flex:1;height:3px;background:var(--border2);border-radius:3px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--accent);cursor:pointer;box-shadow:0 0 0 3px var(--accent-glow)}
.steps-val{font-size:12px;color:var(--accent);font-weight:600;min-width:28px;text-align:right}
.drop-zone{border:1px dashed var(--border2);border-radius:var(--radius-sm);padding:14px;text-align:center;font-size:12px;color:var(--text3);cursor:pointer;transition:all .15s}
.drop-zone:hover{border-color:var(--accent);color:var(--text2)}
.drop-zone.has-img{border-style:solid;border-color:var(--accent)}
.drop-zone img{max-width:100%;max-height:120px;border-radius:4px;margin-top:8px}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.prompt-area{padding:16px 24px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0}
.prompt-toolbar{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.toolbar-btn{padding:5px 12px;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text2);font-size:12px;cursor:pointer}
.toolbar-btn:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-glow)}
textarea#prompt{width:100%;min-height:90px;max-height:180px;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:14px;font-family:inherit;padding:12px 14px;resize:vertical;line-height:1.6}
textarea#prompt:focus{outline:none;border-color:var(--accent)}
textarea#prompt::placeholder{color:var(--text3)}
.action-row{display:flex;gap:10px;margin-top:12px;align-items:center}
.btn-generate{padding:10px 28px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:var(--radius-sm);color:#fff;font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 0 20px rgba(247,111,142,.3)}
.btn-generate:hover{transform:translateY(-1px)}
.btn-generate:disabled{opacity:.5;cursor:not-allowed;transform:none}
.hint{font-size:12px;color:var(--text3)}
.progress-wrap{display:none;padding:10px 24px 0;flex-shrink:0}
.progress-wrap.visible{display:block}
.progress-track{height:3px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-fill{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:3px;transition:width .4s}
.progress-text{font-size:12px;color:var(--text2);margin-top:6px;display:flex;align-items:center;gap:8px}
.spinner{width:12px;height:12px;border:2px solid var(--border2);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.gallery-wrap{flex:1;overflow-y:auto;padding:20px 24px}
.gallery-header{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.gallery-header h2{font-size:13px;font-weight:600;color:var(--text2)}
#gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.vid-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;transition:all .15s}
.vid-card:hover{border-color:var(--border2)}
.vid-card video{width:100%;display:block;background:#000;aspect-ratio:16/9;object-fit:contain}
.vid-meta{padding:8px 10px;display:flex;justify-content:space-between;align-items:center}
.vid-name{font-size:11px;color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.vid-actions{display:flex;gap:8px;flex-shrink:0;margin-left:8px}
.vid-actions a,.vid-actions button{font-size:11px;color:var(--accent);background:none;border:none;cursor:pointer;text-decoration:none}
.empty-state{grid-column:1/-1;text-align:center;padding:60px 20px;color:var(--text3);font-size:13px}
.warn-banner{background:rgba(251,191,36,.1);border:1px solid #fbbf24;color:#fbbf24;padding:10px 24px;font-size:13px;display:none}
.warn-banner.visible{display:block}
#toast-container{position:fixed;bottom:20px;right:20px;z-index:300;display:flex;flex-direction:column;gap:8px}
.toast{padding:10px 16px;background:var(--surface);border:1px solid var(--border2);border-radius:var(--radius-sm);font-size:13px}
.toast.error{border-color:var(--error);color:var(--error)}
.toast.success{border-color:var(--success);color:var(--success)}
</style>
</head>
<body>

<header>
  <div class="logo"><div class="logo-icon">▶</div>Video Studio</div>
  <div class="server-badge"><div class="dot" id="server-dot"></div><span id="server-status">checking…</span></div>
  <div class="spacer"></div>
  <a class="header-btn" href="http://192.168.2.69:8189">Hub</a>
  <a class="header-btn" href="http://192.168.2.69:8190">Images</a>
</header>

<div class="warn-banner" id="model-warn">Wan 2.2 models not downloaded yet — run ./download_wan22_video_models.sh</div>

<div class="app">
  <aside class="sidebar">
    <div>
      <div class="section-label">Mode</div>
      <div class="opt-list" id="mode-list">
        <div class="opt-card selected" data-mode="t2v" onclick="setMode('t2v',this)">
          <div class="opt-radio"></div>
          <div><div class="opt-name">Text to Video</div><div class="opt-desc">Describe scene + motion</div></div>
        </div>
        <div class="opt-card" data-mode="i2v" onclick="setMode('i2v',this)">
          <div class="opt-radio"></div>
          <div><div class="opt-name">Image to Video</div><div class="opt-desc">Animate an existing image</div></div>
        </div>
      </div>
    </div>

    <div id="img-section" style="display:none">
      <div class="section-label">Start Image</div>
      <div class="drop-zone" id="drop-zone" onclick="document.getElementById('img-input').click()">
        <span id="drop-text">Click or drop an image here</span>
        <img id="drop-preview" style="display:none">
      </div>
      <input type="file" id="img-input" accept="image/*" style="display:none" onchange="uploadImage(this.files[0])">
    </div>

    <div>
      <div class="section-label">Resolution</div>
      <div class="opt-list" id="res-list"></div>
    </div>

    <div>
      <div class="section-label">Length · 24 fps</div>
      <div class="pill-row" id="len-list"></div>
    </div>

    <div>
      <div class="section-label">Quality</div>
      <div class="quality-row">
        <span class="quality-label">Draft</span>
        <input type="range" id="steps-slider" min="6" max="30" value="20">
        <span class="quality-label">Max</span>
        <span class="steps-val" id="steps-val">20</span>
      </div>
    </div>
  </aside>

  <main class="main">
    <div class="prompt-area">
      <div class="prompt-toolbar">
        <button class="toolbar-btn" onclick="randomPrompt()">🎲 Random</button>
        <span class="hint">Tip: describe the motion, not just the scene — "waves crashing", "camera pushes in", "hair blowing in wind"</span>
      </div>
      <textarea id="prompt" placeholder="Describe your video — scene AND motion…"></textarea>
      <div class="action-row">
        <button class="btn-generate" id="gen-btn" onclick="generate()">Generate Video</button>
        <span class="hint">Takes several minutes on Mac</span>
      </div>
    </div>

    <div class="progress-wrap" id="prog-wrap">
      <div class="progress-track"><div class="progress-fill" id="prog-fill"></div></div>
      <div class="progress-text"><span class="spinner"></span><span id="prog-text">Queuing…</span></div>
    </div>

    <div class="gallery-wrap">
      <div class="gallery-header"><h2>Videos</h2><span class="hint" id="vid-count"></span></div>
      <div id="gallery"><div class="empty-state">Generate something to see results here.</div></div>
    </div>
  </main>
</div>

<div id="toast-container"></div>

<script>
let mode = 't2v';
let startImage = null;
let resolution = null;
let vidLength = 49;
let pollTimer = null;
let genStart = 0;

const RESOLUTIONS = __RESOLUTIONS__;
const LENGTHS = __LENGTHS__;

document.addEventListener('DOMContentLoaded', () => {
  buildResList();
  buildLenList();
  const slider = document.getElementById('steps-slider');
  slider.addEventListener('input', () => document.getElementById('steps-val').textContent = slider.value);
  checkServer();
  refreshGallery();
  setInterval(checkServer, 10000);
  setInterval(refreshGallery, 8000);
  const dz = document.getElementById('drop-zone');
  dz.addEventListener('dragover', e => e.preventDefault());
  dz.addEventListener('drop', e => { e.preventDefault(); if (e.dataTransfer.files[0]) uploadImage(e.dataTransfer.files[0]); });
});

function buildResList() {
  const el = document.getElementById('res-list');
  RESOLUTIONS.forEach((r, i) => {
    const card = document.createElement('div');
    card.className = 'opt-card' + (i === 0 ? ' selected' : '');
    card.innerHTML = `<div class="opt-radio"></div><div><div class="opt-name">${r[0]}</div><div class="opt-desc">${r[1]}</div></div>`;
    card.onclick = () => {
      document.querySelectorAll('#res-list .opt-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      resolution = [r[2], r[3]];
    };
    el.appendChild(card);
    if (i === 0) resolution = [r[2], r[3]];
  });
}

function buildLenList() {
  const el = document.getElementById('len-list');
  LENGTHS.forEach(([frames, label]) => {
    const pill = document.createElement('button');
    pill.className = 'pill' + (frames === 49 ? ' selected' : '');
    pill.textContent = label;
    pill.onclick = () => {
      document.querySelectorAll('#len-list .pill').forEach(p => p.classList.remove('selected'));
      pill.classList.add('selected');
      vidLength = frames;
    };
    el.appendChild(pill);
  });
}

function setMode(m, card) {
  mode = m;
  document.querySelectorAll('#mode-list .opt-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  document.getElementById('img-section').style.display = m === 'i2v' ? '' : 'none';
}

async function uploadImage(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('image', file);
  try {
    const r = await fetch('/api/upload', {method: 'POST', body: fd});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    startImage = d.name;
    const dz = document.getElementById('drop-zone');
    dz.classList.add('has-img');
    document.getElementById('drop-text').textContent = d.name;
    const prev = document.getElementById('drop-preview');
    prev.src = URL.createObjectURL(file);
    prev.style.display = '';
  } catch (e) { showToast('Upload failed: ' + e.message, 'error'); }
}

async function checkServer() {
  const dot = document.getElementById('server-dot');
  const status = document.getElementById('server-status');
  try {
    const r = await fetch('/api/server_status');
    const d = await r.json();
    dot.className = d.online ? 'dot online' : 'dot error';
    status.textContent = d.online ? 'Connected' : 'ComfyUI offline';
    document.getElementById('model-warn').classList.toggle('visible', !d.models);
  } catch {
    dot.className = 'dot error';
    status.textContent = 'Error';
  }
}

async function randomPrompt() {
  const r = await fetch('/api/random_prompt');
  const d = await r.json();
  document.getElementById('prompt').value = d.prompt;
}

async function generate() {
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) { document.getElementById('prompt').focus(); return; }
  if (mode === 'i2v' && !startImage) { showToast('Upload a start image first', 'error'); return; }

  const body = {
    prompt,
    width: resolution[0], height: resolution[1],
    length: vidLength,
    steps: parseInt(document.getElementById('steps-slider').value),
    start_image: mode === 'i2v' ? startImage : null,
  };

  setGenerating(true);
  try {
    const r = await fetch('/api/generate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); setGenerating(false); return; }
    pollJob(d.job_id);
  } catch (e) {
    showToast('Server error: ' + e.message, 'error');
    setGenerating(false);
  }
}

function setGenerating(on) {
  document.getElementById('gen-btn').disabled = on;
  document.getElementById('prog-wrap').classList.toggle('visible', on);
  if (on) { genStart = Date.now(); document.getElementById('prog-fill').style.width = '0%'; }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  const fill = document.getElementById('prog-fill');
  const text = document.getElementById('prog-text');
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/job/' + jobId);
      const d = await r.json();
      const secs = Math.round((Date.now() - genStart) / 1000);
      if (d.status === 'done') {
        fill.style.width = '100%';
        text.textContent = `Done in ${d.elapsed || secs}s`;
        clearInterval(pollTimer);
        showToast('Video ready', 'success');
        setTimeout(() => setGenerating(false), 1500);
        refreshGallery();
      } else if (d.status === 'error') {
        text.textContent = 'Error: ' + (d.error || 'unknown');
        clearInterval(pollTimer);
        setGenerating(false);
        showToast(d.error || 'Generation failed', 'error');
      } else {
        const pct = Number.isFinite(d.progress) ? d.progress : 0;
        // Real per-step % once sampling starts; soft bar during model load.
        const width = pct > 0 ? pct : Math.min(30, secs / 4);
        fill.style.width = width + '%';
        const phase = d.phase || 'Model loading';
        const stepInfo = d.max_steps ? ` · ${d.step}/${d.max_steps} steps` : '';
        text.textContent = `${phase}${stepInfo} · ${Math.round(width)}% · ${secs}s elapsed`;
      }
    } catch {}
  }, 3000);
}

async function refreshGallery() {
  try {
    const r = await fetch('/api/videos');
    const vids = await r.json();
    document.getElementById('vid-count').textContent = vids.length ? vids.length + ' videos' : '';
    const g = document.getElementById('gallery');
    if (!vids.length) { g.innerHTML = '<div class="empty-state">Generate something to see results here.</div>'; return; }
    const existing = new Set([...g.querySelectorAll('.vid-card')].map(c => c.dataset.name));
    const wanted = new Set(vids.map(v => v.name));
    if (existing.size === wanted.size && [...wanted].every(n => existing.has(n))) return;
    g.innerHTML = '';
    vids.forEach(v => {
      const path = (v.subfolder ? v.subfolder + '/' : '') + v.name;
      const card = document.createElement('div');
      card.className = 'vid-card';
      card.dataset.name = v.name;
      card.innerHTML = `
        <video src="/videos/${encodeURI(path)}" controls loop muted playsinline preload="metadata"></video>
        <div class="vid-meta">
          <span class="vid-name">${v.name}</span>
          <span class="vid-actions">
            <a href="/videos/${encodeURI(path)}" download>Download</a>
            <button onclick="delVideo('${path.replace(/'/g, "\\'")}')">Delete</button>
          </span>
        </div>`;
      g.appendChild(card);
    });
  } catch {}
}

async function delVideo(path) {
  if (!confirm('Delete ' + path + '?')) return;
  const r = await fetch('/api/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path})
  });
  const d = await r.json();
  if (d.error) showToast(d.error, 'error'); else { showToast('Deleted', 'success'); refreshGallery(); }
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + (type || '');
  t.textContent = msg;
  document.getElementById('toast-container').appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
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

    def _safe_output_path(self, rel):
        p = (OUTPUT_DIR / rel).resolve()
        try:
            p.relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            return None
        return p

    def _serve_ranged(self, p, ctype):
        """Stream a file with HTTP Range support so browsers can seek video."""
        size = p.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng and rng.startswith("bytes="):
            spec = rng[len("bytes="):].split(",")[0].strip()
            s, _, e = spec.partition("-")
            try:
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                elif e:  # suffix range: last N bytes
                    start = max(0, size - int(e))
                end = min(end, size - 1)
                if start > end:
                    raise ValueError
                partial = True
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            page = HTML.replace("__RESOLUTIONS__", json.dumps(RESOLUTIONS)) \
                       .replace("__LENGTHS__", json.dumps(LENGTHS))
            data = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/server_status":
            online = False
            try:
                with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=5):
                    online = True
            except Exception:
                pass
            self._json({"online": online, "models": models_available()})
        elif self.path == "/api/videos":
            self._json(list_videos())
        elif self.path == "/api/random_prompt":
            self._json({"prompt": random.choice(RANDOM_MOTION_PROMPTS)})
        elif self.path.startswith("/api/job/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with jobs_lock:
                job = dict(jobs.get(job_id, {"status": "unknown"}))
            self._json(job)
        elif self.path.startswith("/videos/"):
            rel = urllib.request.url2pathname(self.path[len("/videos/"):])
            p = self._safe_output_path(rel)
            if not p or not p.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            self._serve_ranged(p, ctype)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if self.path == "/api/generate":
            try:
                req = json.loads(self.rfile.read(length))
            except Exception:
                self._json({"error": "bad json"}, 400)
                return
            if not models_available():
                self._json({"error": "Wan 2.2 models not downloaded — run ./download_wan22_video_models.sh"})
                return
            job_id = str(uuid.uuid4())[:8]
            with jobs_lock:
                jobs[job_id] = {"status": "queued"}
            threading.Thread(target=run_job, args=(job_id, req), daemon=True).start()
            self._json({"job_id": job_id})
        elif self.path == "/api/upload":
            # Forward multipart body straight to ComfyUI's upload endpoint
            body = self.rfile.read(length)
            req = urllib.request.Request(
                COMFY_URL + "/upload/image", data=body,
                headers={"Content-Type": self.headers.get("Content-Type", "")})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self._json(json.loads(r.read()))
            except Exception as e:
                self._json({"error": str(e)}, 502)
        elif self.path == "/api/delete":
            try:
                req = json.loads(self.rfile.read(length))
                p = self._safe_output_path(req.get("path", ""))
                if p and p.is_file() and p.suffix == ".mp4":
                    p.unlink()
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)


class ThreadingHTTPServer(HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address), daemon=True)
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[+] Video Studio running at http://192.168.2.69:{PORT}")
    server.serve_forever()
