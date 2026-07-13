#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"
COMFY_URL="http://192.168.2.69:8188"

INPUT_DIR="$COMFY/input"
OUTPUT_DIR="$COMFY/output"
UPSCALE_DIR="$COMFY/upscaled"
DOWNLOAD_DIR="$HOME/Downloads/ComfyUI-Images"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$UPSCALE_DIR" "$DOWNLOAD_DIR"

clean_path() {
  local p="$1"
  p="${p%\"}"
  p="${p#\"}"
  p="${p%\'}"
  p="${p#\'}"
  echo "$p"
}

latest_png() {
  find "$OUTPUT_DIR" "$UPSCALE_DIR" -type f -name "*.png" -size +1k -print0 2>/dev/null \
    | xargs -0 stat -f "%m %N" 2>/dev/null \
    | sort -nr \
    | head -1 \
    | cut -d" " -f2-
}

check_server() {
  if ! curl -s "$COMFY_URL/system_stats" >/dev/null 2>&1; then
    echo
    echo "[!] Could not reach ComfyUI at:"
    echo "    $COMFY_URL"
    echo
    echo "[!] Start it in another terminal:"
    echo "    ~/AI/ComfyUI/run.sh"
    exit 1
  fi
}

choose_model() {
  echo
  echo "Model:"
  echo "1) Flux Schnell, stable/default"
  echo "2) Qwen Image 2512, if installed"
  echo

  read -r -p "Model [1]: " MODEL_CHOICE

  case "$MODEL_CHOICE" in
    2)
      MODEL="qwen"
      ;;
    *)
      MODEL="flux"
      ;;
  esac

  echo
  echo "[+] Using model: $MODEL"
}

check_model_files() {
  if [ "$MODEL" = "qwen" ]; then
    REQUIRED=(
      "$COMFY/models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"
      "$COMFY/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
      "$COMFY/models/vae/qwen_image_vae.safetensors"
      "$COMFY/models/loras/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors"
    )
  else
    REQUIRED=(
      "$COMFY/models/diffusion_models/flux1-schnell.safetensors"
      "$COMFY/models/text_encoders/clip_l.safetensors"
      "$COMFY/models/text_encoders/t5xxl_fp16.safetensors"
      "$COMFY/models/vae/ae.safetensors"
    )
  fi

  for f in "${REQUIRED[@]}"; do
    if [ ! -s "$f" ]; then
      echo
      echo "[!] Missing required model file:"
      echo "    $f"
      if [ "$MODEL" = "qwen" ]; then
        echo
        echo "[!] Run this first:"
        echo "    ~/AI/ComfyUI/download_qwen_image_2512.sh"
      fi
      exit 1
    fi
  done
}

upload_image() {
  echo
  read -r -p "Path to image on this Mac, or drag image here: " SRC
  SRC="$(clean_path "$SRC")"

  if [ ! -f "$SRC" ]; then
    echo "[!] File not found:"
    echo "    $SRC"
    exit 1
  fi

  BASE="$(basename "$SRC")"
  SAFE="$(echo "$BASE" | tr ' ' '_' | tr -cd '[:alnum:]_.-')"

  cp "$SRC" "$INPUT_DIR/$SAFE"

  echo
  echo "[+] Uploaded/copied image into ComfyUI input:"
  echo "    $INPUT_DIR/$SAFE"

  IMAGE_NAME="$SAFE"
}

copy_open_url() {
  local img="$1"

  if [ -z "$img" ] || [ ! -f "$img" ]; then
    echo "[!] No image found."
    exit 1
  fi

  echo
  echo "[+] Latest image:"
  echo "    $img"

  echo
  echo "[+] Opening image..."
  open "$img"

  FINAL_COPY="$DOWNLOAD_DIR/$(basename "$img")"
  cp "$img" "$FINAL_COPY"

  echo
  echo "[+] Copied to Downloads:"
  echo "    $FINAL_COPY"

  FILENAME="$(basename "$img")"

  if [[ "$img" == "$OUTPUT_DIR"* ]]; then
    echo
    echo "[+] Browser view URL:"
    echo "    $COMFY_URL/view?filename=$FILENAME&type=output"
  fi
}

choose_style() {
  echo
  echo "Style:"
  echo "1) Photo realistic [default]"
  echo "2) Cinematic"
  echo "3) Cartoon"
  echo "4) Anime"
  echo "5) Product photo"
  echo "6) Hacker / cyberpunk realistic"
  echo

  read -r -p "Style [1]: " STYLE_CHOICE

  case "$STYLE_CHOICE" in
    2) STYLE_PREFIX="cinematic realistic photograph, dramatic natural lighting, shallow depth of field, film still, rich shadows, realistic textures" ;;
    3) STYLE_PREFIX="clean cartoon illustration, polished digital art, expressive shapes, colorful, charming, smooth outlines" ;;
    4) STYLE_PREFIX="high quality anime illustration, detailed background, expressive lighting, clean linework, cinematic composition" ;;
    5) STYLE_PREFIX="professional product photography, studio lighting, sharp focus, clean background, realistic reflections, commercial photo" ;;
    6) STYLE_PREFIX="realistic documentary photograph of a cybersecurity researcher workspace, glowing monitors, terminal windows, messy cables, natural shadows, 35mm photography, film grain" ;;
    *) STYLE_PREFIX="realistic candid 35mm photograph, natural light, realistic shadows, shallow depth of field, slight film grain, imperfect real-world composition, documentary photography" ;;
  esac
}

choose_quality_size() {
  echo
  echo "Quality:"
  echo "1) Fast, 512px, 4 steps"
  echo "2) Balanced, 768px, 4 steps"
  echo "3) Quality, 1024px, 8 steps [default]"
  echo "4) High quality, 1024px, 12 steps"
  echo

  read -r -p "Quality [3]: " QUALITY_CHOICE

  case "$QUALITY_CHOICE" in
    1) BASE_SIZE=512; STEPS=4 ;;
    2) BASE_SIZE=768; STEPS=4 ;;
    4) BASE_SIZE=1024; STEPS=12 ;;
    *) BASE_SIZE=1024; STEPS=8 ;;
  esac

  echo
  echo "Aspect ratio:"
  echo "1) Square [default]"
  echo "2) Wide"
  echo "3) Portrait"
  echo

  read -r -p "Aspect [1]: " ASPECT_CHOICE

  case "$ASPECT_CHOICE" in
    2)
      if [ "$BASE_SIZE" -eq 512 ]; then WIDTH=768; HEIGHT=448
      elif [ "$BASE_SIZE" -eq 768 ]; then WIDTH=1024; HEIGHT=576
      else WIDTH=1344; HEIGHT=768
      fi
      ;;
    3)
      if [ "$BASE_SIZE" -eq 512 ]; then WIDTH=448; HEIGHT=768
      elif [ "$BASE_SIZE" -eq 768 ]; then WIDTH=576; HEIGHT=1024
      else WIDTH=768; HEIGHT=1344
      fi
      ;;
    *)
      WIDTH="$BASE_SIZE"
      HEIGHT="$BASE_SIZE"
      ;;
  esac
}

HISTORY_FILE="$HOME/AI/ComfyUI/generation_history.jsonl"
export HISTORY_FILE

submit_prompt() {
  python3 - <<'PY'
import json, os, sys, time, uuid, random, urllib.request, urllib.error

COMFY_URL = os.environ["COMFY_URL"]
workflow = json.loads(os.environ["WORKFLOW_JSON"])
batch_count = int(os.environ.get("BATCH_COUNT", "1"))
seeds_file = os.environ.get("SEEDS_FILE", "")
locked_seed = os.environ.get("LOCKED_SEED", "")

def find_seed_node(wf):
    for node_id, node in wf.items():
        if "seed" in node.get("inputs", {}):
            return node_id
    return None

def queue_one(wf, idx):
    wf = json.loads(json.dumps(wf))
    seed_node = find_seed_node(wf)
    if seed_node:
        if locked_seed and idx == 0:
            seed = int(locked_seed)
        else:
            seed = random.randint(0, 2**32 - 1)
        wf[seed_node]["inputs"]["seed"] = seed
    else:
        seed = 0
    payload = {"prompt": wf, "client_id": str(uuid.uuid4())}
    req = urllib.request.Request(
        COMFY_URL + "/prompt",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print("[!] HTTP error:", e.code, e.read().decode("utf-8"))
        sys.exit(1)
    except Exception as e:
        print("[!] Could not reach ComfyUI:", e)
        print("[!] Make sure this is running:  ~/AI/ComfyUI/run.sh")
        sys.exit(1)
    pid = result.get("prompt_id")
    if not pid:
        print(json.dumps(result, indent=2))
        sys.exit(1)
    print(f"[+] Queued {idx+1}/{batch_count}: {pid}  seed={seed}")
    return pid, seed

pairs = [queue_one(workflow, i) for i in range(batch_count)]
prompt_ids = [p for p, _ in pairs]
seeds_used = [s for _, s in pairs]

if seeds_file:
    with open(seeds_file, "w") as f:
        f.write("\n".join(str(s) for s in seeds_used))

remaining = set(prompt_ids)
print(f"[+] Waiting for {batch_count} image(s)...")

for _ in range(1800):
    time.sleep(2)
    if not remaining:
        break
    try:
        for pid in list(remaining):
            with urllib.request.urlopen(COMFY_URL + "/history/" + pid, timeout=30) as r:
                history = json.loads(r.read().decode("utf-8"))
            if pid in history:
                status = history[pid].get("status", {})
                if status.get("status_str") == "error":
                    print("[!] Error on", pid)
                    print(json.dumps(status, indent=2))
                    sys.exit(1)
                remaining.discard(pid)
                print(f"[+] Done {batch_count - len(remaining)}/{batch_count}")
    except Exception:
        continue

if remaining:
    print("[!] Timed out waiting for ComfyUI.")
    sys.exit(1)

print(f"[+] All {batch_count} image(s) complete.")
PY
}

log_generation() {
  local prompt="$1"
  local model="$2"
  local seeds="$3"
  local width="$4"
  local height="$5"
  local steps="$6"
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  python3 -c "
import json, sys
entry = {
  'ts': '$ts',
  'prompt': '''$prompt''',
  'model': '$model',
  'seeds': [int(s) for s in '$seeds'.split() if s],
  'width': $width,
  'height': $height,
  'steps': $steps
}
with open('$HISTORY_FILE', 'a') as f:
    f.write(json.dumps(entry) + '\n')
"
}

view_history() {
  if [ ! -f "$HISTORY_FILE" ] || [ ! -s "$HISTORY_FILE" ]; then
    echo
    echo "[!] No history yet. Generate some images first."
    return
  fi

  echo
  echo "Recent generations (newest first):"
  echo

  python3 - <<'PY'
import json

HISTORY_FILE = __import__('os').environ["HISTORY_FILE"]

with open(HISTORY_FILE) as f:
    lines = [l.strip() for l in f if l.strip()]

entries = []
for l in lines:
    try:
        entries.append(json.loads(l))
    except Exception:
        pass

entries = list(reversed(entries))[:20]

for i, e in enumerate(entries):
    seeds = e.get("seeds", [])
    seed_str = str(seeds[0]) if seeds else "?"
    print(f"  [{i+1:2d}] {e.get('ts','?')[:16]}  {e.get('model','?'):12s}  {e.get('width','?')}x{e.get('height','?')}  {e.get('steps','?')}steps  seed={seed_str}")
    print(f"       {e.get('prompt','')[:90]}")
    print()
PY

  read -r -p "Replay entry # (or Enter to skip): " REPLAY_NUM

  if [ -z "$REPLAY_NUM" ]; then
    return
  fi

  python3 - <<PY
import json, os

HISTORY_FILE = "$HISTORY_FILE"
idx = int("$REPLAY_NUM") - 1

with open(HISTORY_FILE) as f:
    lines = [l.strip() for l in f if l.strip()]

entries = list(reversed([json.loads(l) for l in lines if l.strip()]))

if idx < 0 or idx >= len(entries):
    print("[!] Invalid selection.")
    raise SystemExit(1)

e = entries[idx]
print(f"REPLAY_PROMPT={json.dumps(e.get('prompt',''))}")
print(f"REPLAY_MODEL={e.get('model','flux')}")
print(f"REPLAY_WIDTH={e.get('width', 1024)}")
print(f"REPLAY_HEIGHT={e.get('height', 1024)}")
print(f"REPLAY_STEPS={e.get('steps', 8)}")
seeds = e.get('seeds', [0])
print(f"REPLAY_SEED={seeds[0]}")
PY
}

compare_models() {
  check_server

  local flux_ok=true
  local qwen_ok=true

  [ ! -f "$COMFY/models/diffusion_models/flux1-schnell.safetensors" ] && flux_ok=false
  [ ! -f "$COMFY/models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors" ] && qwen_ok=false

  if [ "$flux_ok" = false ] && [ "$qwen_ok" = false ]; then
    echo "[!] No models found. Download at least one."
    return
  fi

  echo
  read -r -p "Prompt to compare: " CMP_PROMPT
  [ -z "$CMP_PROMPT" ] && CMP_PROMPT="a photorealistic portrait of a person in natural light"

  BASE_SIZE=1024
  WIDTH=1024
  HEIGHT=1024
  STEPS=8

  if [ "$flux_ok" = true ]; then
    echo
    echo "[+] Generating with Flux Schnell..."
    MODEL=flux
    BATCH_COUNT=1
    SEEDS_FILE="$(mktemp)"
    export MODEL PROMPT="realistic candid 35mm photograph, natural light, $CMP_PROMPT" WIDTH HEIGHT STEPS COMFY_URL BATCH_COUNT SEEDS_FILE
    export WORKFLOW_JSON
    WORKFLOW_JSON="$(make_txt2img_workflow)"
    BATCH_MARKER_F="$(mktemp)"
    submit_prompt
    FLUX_IMG="$(find "$OUTPUT_DIR" -type f -name "*.png" -size +1k -newer "$BATCH_MARKER_F" -print0 2>/dev/null | xargs -0 stat -f "%m %N" 2>/dev/null | sort -nr | head -1 | cut -d" " -f2- || true)"
    rm -f "$BATCH_MARKER_F" "$SEEDS_FILE"
  fi

  if [ "$qwen_ok" = true ]; then
    echo
    echo "[+] Generating with Qwen Image 2512..."
    MODEL=qwen
    BATCH_COUNT=1
    SEEDS_FILE="$(mktemp)"
    export MODEL PROMPT="realistic candid 35mm photograph, natural light, $CMP_PROMPT" WIDTH HEIGHT STEPS COMFY_URL BATCH_COUNT SEEDS_FILE
    export WORKFLOW_JSON
    WORKFLOW_JSON="$(make_txt2img_workflow)"
    BATCH_MARKER_Q="$(mktemp)"
    submit_prompt
    QWEN_IMG="$(find "$OUTPUT_DIR" -type f -name "*.png" -size +1k -newer "$BATCH_MARKER_Q" -print0 2>/dev/null | xargs -0 stat -f "%m %N" 2>/dev/null | sort -nr | head -1 | cut -d" " -f2- || true)"
    rm -f "$BATCH_MARKER_Q" "$SEEDS_FILE"
  fi

  echo
  echo "[+] Opening both for comparison..."
  [ -n "$FLUX_IMG" ] && open "$FLUX_IMG"
  [ -n "$QWEN_IMG" ] && open "$QWEN_IMG"
}

make_txt2img_workflow() {
  python3 - <<'PY'
import json, os, time

model = os.environ["MODEL"]
prompt = os.environ["PROMPT"]
width = int(os.environ["WIDTH"])
height = int(os.environ["HEIGHT"])
steps = int(os.environ["STEPS"])
seed = int(time.time())

if model == "qwen":
    if steps > 8:
        steps = 8

    workflow = {
      "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2512_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
      "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors", "strength_model": 1.0}},
      "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
      "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
      "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}},
      "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
      "7": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
      "8": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
      "9": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["8", 0]}},
      "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "qwen_image"}}
    }
else:
    workflow = {
      "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-schnell.safetensors", "weight_dtype": "default"}},
      "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp16.safetensors", "type": "flux"}},
      "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
      "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
      "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
      "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
      "7": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
      "8": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["7", 0]}},
      "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "flux_schnell"}}
    }

print(json.dumps(workflow))
PY
}

make_img2img_workflow() {
  python3 - <<'PY'
import json, os, time

workflow = {
  "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-schnell.safetensors", "weight_dtype": "default"}},
  "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp16.safetensors", "type": "flux"}},
  "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": os.environ["PROMPT"]}},
  "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
  "5": {"class_type": "LoadImage", "inputs": {"image": os.environ["IMAGE_NAME"]}},
  "6": {"class_type": "ImageScale", "inputs": {"image": ["5", 0], "upscale_method": "lanczos", "width": int(os.environ["WIDTH"]), "height": int(os.environ["HEIGHT"]), "crop": "center"}},
  "7": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
  "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["7", 0]}},
  "9": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["8", 0], "seed": int(time.time()), "steps": int(os.environ["STEPS"]), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": float(os.environ["DENOISE"])}},
  "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["7", 0]}},
  "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": os.environ.get("PREFIX", "edited_flux")}}
}

print(json.dumps(workflow))
PY
}

generate_image() {
  check_server
  check_model_files

  if [ -f "$COMFY/.locked_seed" ]; then
    LOCKED_SEED="$(cat "$COMFY/.locked_seed")"
    echo
    echo "[+] Locked seed active: $LOCKED_SEED (first image will use this seed)"
    read -r -p "    Keep it? Y/n: " KEEP_LOCK
    if [[ "$KEEP_LOCK" =~ ^[Nn]$ ]]; then
      rm -f "$COMFY/.locked_seed"
      unset LOCKED_SEED
    fi
  fi

  echo
  read -r -p "Image prompt: " USER_PROMPT
  [ -z "$USER_PROMPT" ] && USER_PROMPT="a ceramic coffee mug on a wooden desk near a window"

  choose_style
  choose_quality_size

  echo
  read -r -p "How many images? [1]: " BATCH_INPUT
  BATCH_COUNT="${BATCH_INPUT:-1}"
  if ! [[ "$BATCH_COUNT" =~ ^[0-9]+$ ]] || [ "$BATCH_COUNT" -lt 1 ]; then
    BATCH_COUNT=1
  fi

  echo
  read -r -p "Upscale after generation to 5K? y/N: " DO_UPSCALE

  SEEDS_FILE="$(mktemp)"
  export MODEL PROMPT="$STYLE_PREFIX, $USER_PROMPT" WIDTH HEIGHT STEPS COMFY_URL BATCH_COUNT SEEDS_FILE
  [ -n "${LOCKED_SEED:-}" ] && export LOCKED_SEED
  export WORKFLOW_JSON
  WORKFLOW_JSON="$(make_txt2img_workflow)"

  BATCH_MARKER="$(mktemp)"
  submit_prompt

  SEEDS_USED=""
  [ -f "$SEEDS_FILE" ] && SEEDS_USED="$(cat "$SEEDS_FILE")"
  rm -f "$SEEDS_FILE"

  log_generation "$STYLE_PREFIX, $USER_PROMPT" "$MODEL" "$SEEDS_USED" "$WIDTH" "$HEIGHT" "$STEPS"

  if [ -n "$SEEDS_USED" ]; then
    FIRST_SEED="$(echo "$SEEDS_USED" | head -1)"
    echo
    echo "[+] Seeds used: $SEEDS_USED"
    echo "[+] Lock first seed for next run? (rerun exact image with tweaks)"
    read -r -p "Lock seed $FIRST_SEED? y/N: " DO_LOCK
    if [[ "$DO_LOCK" =~ ^[Yy]$ ]]; then
      echo "$FIRST_SEED" > "$COMFY/.locked_seed"
      echo "[+] Seed locked: $FIRST_SEED"
    else
      rm -f "$COMFY/.locked_seed"
    fi
  fi

  echo
  echo "[+] Opening all $BATCH_COUNT image(s)..."
  find "$OUTPUT_DIR" -type f -name "*.png" -size +1k -newer "$BATCH_MARKER" -print0 2>/dev/null \
    | xargs -0 stat -f "%m %N" 2>/dev/null \
    | sort -nr \
    | head -"$BATCH_COUNT" \
    | cut -d" " -f2- \
    | while read -r img; do
        copy_open_url "$img"
        if [[ "$DO_UPSCALE" =~ ^[Yy]$ ]]; then
          upscale_image "$img"
        fi
      done

  rm -f "$BATCH_MARKER"
  unset LOCKED_SEED
}

edit_image() {
  check_server

  if [ "$MODEL" != "flux" ]; then
    echo
    echo "[!] Upload/edit currently uses Flux img2img."
    echo "[!] Re-run and choose Flux."
    exit 1
  fi

  check_model_files
  upload_image

  echo
  echo "What do you want to do?"
  echo "1) Make it more photo realistic"
  echo "2) Make it cinematic"
  echo "3) Make it cartoon"
  echo "4) Make it anime"
  echo "5) Make it cyberpunk / hacker style"
  echo "6) Clean it up / enhance it"
  echo "7) Custom instruction"
  echo

  read -r -p "Choice [1]: " TASK_CHOICE

  case "$TASK_CHOICE" in
    2) TASK_PROMPT="make this image look like a cinematic realistic photograph, dramatic natural lighting, shallow depth of field, film still, realistic shadows, detailed textures" ;;
    3) TASK_PROMPT="transform this image into a clean cartoon illustration, polished digital art, expressive shapes, colorful, smooth outlines" ;;
    4) TASK_PROMPT="transform this image into a high quality anime illustration, clean linework, detailed background, cinematic composition" ;;
    5) TASK_PROMPT="transform this image into a realistic cyberpunk hacker scene, glowing monitors, terminal windows, dark room, neon reflections, cinematic lighting, realistic shadows" ;;
    6) TASK_PROMPT="enhance this image while preserving the original subject and composition, improve lighting, sharpness, realism, detail, natural colors, realistic texture" ;;
    7) read -r -p "Custom edit instruction: " TASK_PROMPT ;;
    *) TASK_PROMPT="make this image more photo realistic while preserving the original subject and composition, natural lighting, realistic shadows, detailed texture, 35mm photograph, subtle film grain" ;;
  esac

  echo
  echo "Edit strength:"
  echo "1) Subtle"
  echo "2) Medium [default]"
  echo "3) Strong"
  echo "4) Very strong"
  read -r -p "Strength [2]: " STRENGTH_CHOICE

  case "$STRENGTH_CHOICE" in
    1) DENOISE="0.30" ;;
    3) DENOISE="0.70" ;;
    4) DENOISE="0.90" ;;
    *) DENOISE="0.50" ;;
  esac

  WIDTH=768
  HEIGHT=768
  STEPS=8
  PREFIX="edited_flux"

  export IMAGE_NAME PROMPT="$TASK_PROMPT" WIDTH HEIGHT STEPS DENOISE PREFIX COMFY_URL
  export WORKFLOW_JSON
  WORKFLOW_JSON="$(make_img2img_workflow)"

  submit_prompt

  IMG="$(latest_png)"
  copy_open_url "$IMG"
}

restore_image() {
  check_server

  if [ "$MODEL" != "flux" ]; then
    echo
    echo "[!] Restore currently uses Flux img2img."
    echo "[!] Re-run and choose Flux."
    exit 1
  fi

  check_model_files
  upload_image

  PROMPT="restore this old photograph, preserve the original people and composition, improve clarity, remove noise, improve contrast, natural realistic photo restoration, subtle detail recovery"
  WIDTH=1024
  HEIGHT=1024
  STEPS=8
  DENOISE="0.40"
  PREFIX="restored_flux"

  export IMAGE_NAME PROMPT WIDTH HEIGHT STEPS DENOISE PREFIX COMFY_URL
  export WORKFLOW_JSON
  WORKFLOW_JSON="$(make_img2img_workflow)"

  submit_prompt

  IMG="$(latest_png)"
  copy_open_url "$IMG"
}

upscale_image() {
  local src="$1"

  if [ -z "$src" ]; then
    upload_image
    src="$INPUT_DIR/$IMAGE_NAME"
  fi

  echo
  echo "Upscale size:"
  echo "1) 2K"
  echo "2) 4K"
  echo "3) 5K [default]"
  read -r -p "Upscale [3]: " UPSCALE_CHOICE

  base="$(basename "$src")"
  base="${base%.*}"

  case "$UPSCALE_CHOICE" in
    1) out="$UPSCALE_DIR/${base}_2k.png"; sips -Z 2048 "$src" --out "$out" >/dev/null ;;
    2) out="$UPSCALE_DIR/${base}_4k.png"; sips -Z 4096 "$src" --out "$out" >/dev/null ;;
    *) out="$UPSCALE_DIR/${base}_5k.png"; sips -Z 5120 "$src" --out "$out" >/dev/null ;;
  esac

  copy_open_url "$out"
}

open_latest() {
  IMG="$(latest_png)"
  copy_open_url "$IMG"
}

copy_latest() {
  IMG="$(latest_png)"
  if [ -z "$IMG" ] || [ ! -f "$IMG" ]; then
    echo "[!] No latest image found."
    exit 1
  fi

  cp "$IMG" "$DOWNLOAD_DIR/$(basename "$IMG")"
  echo "[+] Copied to:"
  echo "    $DOWNLOAD_DIR/$(basename "$IMG")"
}

print_latest_url() {
  IMG="$(latest_png)"

  if [ -z "$IMG" ] || [ ! -f "$IMG" ]; then
    echo "[!] No latest image found."
    exit 1
  fi

  if [[ "$IMG" != "$OUTPUT_DIR"* ]]; then
    echo "[!] Latest image is not inside ComfyUI output:"
    echo "    $IMG"
    exit 1
  fi

  FILENAME="$(basename "$IMG")"

  echo
  echo "[+] Latest image:"
  echo "    $IMG"
  echo
  echo "[+] Browser view URL:"
  echo "    $COMFY_URL/view?filename=$FILENAME&type=output"
}

package_for_macbook() {
  DEST="$HOME/Desktop/ComfyUI-macbook-copy.zip"

  echo
  echo "[+] Packaging ComfyUI for MacBook..."
  echo "    $DEST"

  rm -f "$DEST"

  cd "$HOME/AI"

  zip -r "$DEST" ComfyUI \
    -x "ComfyUI/venv/*" \
    -x "ComfyUI/.git/*" \
    -x "ComfyUI/output/*" \
    -x "ComfyUI/input/*" \
    -x "ComfyUI/temp/*" \
    -x "ComfyUI/__pycache__/*" \
    -x "ComfyUI/**/__pycache__/*" \
    -x "ComfyUI/.DS_Store" \
    -x "ComfyUI/**/.DS_Store"

  echo
  echo "[+] Done:"
  du -h "$DEST"
}

main_menu() {
  echo
  echo "ComfyUI Mega Tool"
  echo "-----------------"

  choose_model

  echo
  echo "1)  Generate new image"
  echo "2)  Upload/local image path -> edit/transform"
  echo "3)  Upload/local image path -> restore old photo"
  echo "4)  Upload/local image path -> upscale"
  echo "5)  Open latest output"
  echo "6)  Copy latest output to Downloads"
  echo "7)  Print browser URL"
  echo "8)  Package ComfyUI for MacBook"
  echo "9)  Compare models (same prompt, Flux vs Qwen)"
  echo "10) View/replay history"
  echo "11) Quit"
  echo

  read -r -p "Choice: " CHOICE

  case "$CHOICE" in
    1)  generate_image ;;
    2)  edit_image ;;
    3)  restore_image ;;
    4)  upload_image; upscale_image "$INPUT_DIR/$IMAGE_NAME" ;;
    5)  open_latest ;;
    6)  copy_latest ;;
    7)  print_latest_url ;;
    8)  package_for_macbook ;;
    9)  compare_models ;;
    10) view_history ;;
    11) exit 0 ;;
    *)  echo "[!] Invalid choice." ;;
  esac
}

main_menu
