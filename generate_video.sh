#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"
COMFY_URL="http://192.168.2.69:8188"
OUTPUT_DIR="$COMFY/output"

mkdir -p "$OUTPUT_DIR"

MODEL_FILE="wan2.2_ti2v_5B_fp16.safetensors"
TEXT_ENCODER="umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE_FILE="wan2.2_vae.safetensors"

echo
echo "Wan 2.2 Video Generator"
echo "-----------------------"

# Check server
if ! curl -s "$COMFY_URL/system_stats" >/dev/null 2>&1; then
  echo
  echo "[!] Could not reach ComfyUI at: $COMFY_URL"
  echo "[!] Start it in another terminal: ~/AI/ComfyUI/run.sh"
  exit 1
fi

# Check models exist
for f in "models/diffusion_models/$MODEL_FILE" "models/text_encoders/$TEXT_ENCODER" "models/vae/$VAE_FILE"; do
  if [ ! -f "$COMFY/$f" ]; then
    echo
    echo "[!] Missing model: $COMFY/$f"
    echo "[!] Download first: ./download_wan22_video_models.sh"
    exit 1
  fi
done

echo
echo "Mode:"
echo "1) Text to video [default]"
echo "2) Image to video — animate an existing image"
echo

read -r -p "Mode [1]: " MODE_CHOICE

START_IMAGE=""
if [ "$MODE_CHOICE" = "2" ]; then
  echo
  read -r -p "Path to image (drag file here): " IMG_PATH
  # Strip quotes that Terminal drag-and-drop adds
  IMG_PATH="${IMG_PATH%\"}"; IMG_PATH="${IMG_PATH#\"}"
  IMG_PATH="${IMG_PATH%\'}"; IMG_PATH="${IMG_PATH#\'}"
  if [ ! -f "$IMG_PATH" ]; then
    echo "[!] File not found: $IMG_PATH"
    exit 1
  fi
  echo "[+] Uploading image to ComfyUI..."
  UPLOAD_RESP="$(curl -s -F "image=@$IMG_PATH" -F "overwrite=true" "$COMFY_URL/upload/image")"
  START_IMAGE="$(echo "$UPLOAD_RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
  echo "[+] Uploaded as: $START_IMAGE"
fi

echo
read -r -p "Video prompt (describe scene AND motion): " USER_PROMPT

if [ -z "$USER_PROMPT" ]; then
  USER_PROMPT="a golden retriever running through shallow ocean waves at sunset, water splashing, slow motion, cinematic"
fi

echo
echo "Resolution:"
echo "1) 848x480 landscape — fast [default]"
echo "2) 480x848 portrait — fast"
echo "3) 1280x704 landscape — 720p, slow on Mac"
echo "4) 704x1280 portrait — 720p, slow on Mac"
echo "5) 640x640 square"
echo

read -r -p "Resolution [1]: " RES_CHOICE

case "$RES_CHOICE" in
  2) WIDTH=480;  HEIGHT=848  ;;
  3) WIDTH=1280; HEIGHT=704  ;;
  4) WIDTH=704;  HEIGHT=1280 ;;
  5) WIDTH=640;  HEIGHT=640  ;;
  *) WIDTH=848;  HEIGHT=480  ;;
esac

echo
echo "Length (24 fps):"
echo "1) 2 seconds (49 frames) [default]"
echo "2) 3.4 seconds (81 frames)"
echo "3) 5 seconds (121 frames)"
echo "4) 1.4 seconds (33 frames) — quick test"
echo

read -r -p "Length [1]: " LEN_CHOICE

case "$LEN_CHOICE" in
  2) LENGTH=81  ;;
  3) LENGTH=121 ;;
  4) LENGTH=33  ;;
  *) LENGTH=49  ;;
esac

echo
echo "Quality:"
echo "1) Draft, 10 steps"
echo "2) Balanced, 20 steps [default]"
echo "3) Max, 30 steps"
echo

read -r -p "Quality [2]: " Q_CHOICE

case "$Q_CHOICE" in
  1) STEPS=10 ;;
  3) STEPS=30 ;;
  *) STEPS=20 ;;
esac

echo
echo "[+] Model:      Wan 2.2 TI2V 5B"
echo "[+] Server:     $COMFY_URL"
echo "[+] Prompt:     $USER_PROMPT"
echo "[+] Size:       ${WIDTH}x${HEIGHT}, $LENGTH frames @ 24fps"
echo "[+] Steps:      $STEPS"
if [ -n "$START_IMAGE" ]; then
  echo "[+] Start img:  $START_IMAGE"
fi
echo
echo "[+] Heads up: video generation on Mac takes several minutes."
echo

export WAN_PROMPT="$USER_PROMPT"
export WAN_START_IMAGE="$START_IMAGE"

python3 - <<PY
import json
import os
import time
import uuid
import urllib.request
import urllib.error
import sys

COMFY_URL = "$COMFY_URL"
TEXT_PROMPT = os.environ["WAN_PROMPT"]
START_IMAGE = os.environ.get("WAN_START_IMAGE", "")
WIDTH = int("$WIDTH")
HEIGHT = int("$HEIGHT")
LENGTH = int("$LENGTH")
STEPS = int("$STEPS")

NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，"
            "低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，"
            "毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")

client_id = str(uuid.uuid4())
seed = int(time.time())

wf = {}

wf["1"] = {"class_type": "UNETLoader",
           "inputs": {"unet_name": "$MODEL_FILE", "weight_dtype": "default"}}

wf["2"] = {"class_type": "CLIPLoader",
           "inputs": {"clip_name": "$TEXT_ENCODER", "type": "wan", "device": "default"}}

wf["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": "$VAE_FILE"}}

wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": TEXT_PROMPT}}
wf["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": NEGATIVE}}

wf["6"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}}

latent_inputs = {
    "vae": ["3", 0],
    "width": WIDTH,
    "height": HEIGHT,
    "length": LENGTH,
    "batch_size": 1,
}
if START_IMAGE:
    wf["10"] = {"class_type": "LoadImage", "inputs": {"image": START_IMAGE}}
    latent_inputs["start_image"] = ["10", 0]

wf["7"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs}

wf["8"] = {"class_type": "KSampler", "inputs": {
    "model": ["6", 0],
    "positive": ["4", 0],
    "negative": ["5", 0],
    "latent_image": ["7", 0],
    "seed": seed,
    "steps": STEPS,
    "cfg": 5.0,
    "sampler_name": "uni_pc",
    "scheduler": "simple",
    "denoise": 1.0,
}}

wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}

wf["11"] = {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 24.0}}

wf["12"] = {"class_type": "SaveVideo", "inputs": {
    "video": ["11", 0],
    "filename_prefix": "video/wan22",
    "format": "mp4",
    "codec": "h264",
}}

payload = {"prompt": wf, "client_id": client_id}

req = urllib.request.Request(
    COMFY_URL + "/prompt",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print("[!] HTTP error:", e.code)
    print(e.read().decode("utf-8"))
    sys.exit(1)

prompt_id = result.get("prompt_id")
if not prompt_id:
    print(json.dumps(result, indent=2))
    sys.exit(1)

print("[+] Queued prompt:", prompt_id)
print("[+] Seed:", seed)
print("[+] Waiting for video (several minutes on Mac, be patient)...")

start = time.time()
for i in range(2700):
    time.sleep(4)

    try:
        with urllib.request.urlopen(COMFY_URL + "/history/" + prompt_id, timeout=30) as r:
            history = json.loads(r.read().decode("utf-8"))
    except Exception:
        continue

    if prompt_id in history:
        item = history[prompt_id]
        status = item.get("status", {})

        if status.get("status_str") == "error":
            print("[!] ComfyUI reported an error:")
            for m in status.get("messages", []):
                if m[0] == "execution_error":
                    print("   ", m[1].get("node_type"), "-", m[1].get("exception_message", "")[:500])
            sys.exit(1)

        elapsed = int(time.time() - start)
        print(f"[+] Generation done in {elapsed}s.")

        for node_id, node_output in item.get("outputs", {}).items():
            for key in ("images", "video", "gifs"):
                for out in node_output.get(key, []):
                    print("[+] Output:", out.get("subfolder", ""), "/", out.get("filename"))
        break
else:
    print("[!] Timed out waiting for ComfyUI.")
    sys.exit(1)
PY

echo
echo "[+] Finding newest video in: $OUTPUT_DIR"

NEWEST="$(find "$OUTPUT_DIR" -type f -name "*.mp4" -print0 | xargs -0 stat -f "%m %N" 2>/dev/null | sort -nr | head -1 | cut -d" " -f2- || true)"

if [ -z "$NEWEST" ]; then
  echo "[!] No MP4 found."
  find "$OUTPUT_DIR" -maxdepth 2 -type f -newer /tmp -print 2>/dev/null | tail -10
  exit 1
fi

echo "[+] Newest video: $NEWEST"
echo "[+] Opening..."
open "$NEWEST"

DOWNLOAD_DIR="$HOME/Downloads/ComfyUI-Videos"
mkdir -p "$DOWNLOAD_DIR"
cp "$NEWEST" "$DOWNLOAD_DIR/$(basename "$NEWEST")"
echo "[+] Copied to: $DOWNLOAD_DIR/$(basename "$NEWEST")"

echo
echo "[+] Done."
