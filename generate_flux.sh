#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"
COMFY_URL="http://192.168.2.69:8188"
OUTPUT_DIR="$COMFY/output"
UPSCALE_DIR="$COMFY/upscaled"

mkdir -p "$OUTPUT_DIR"
mkdir -p "$UPSCALE_DIR"

echo
echo "Flux Image Generator"
echo "--------------------"
echo
echo "Model:"
echo "1) FLUX.1 Dev  — max realism, photographic quality, slow [default]"
echo "2) FLUX.1 Schnell — fast drafts, lower detail"
echo

read -r -p "Model [1]: " MODEL_CHOICE

case "$MODEL_CHOICE" in
  2)
    MODEL_FILE="flux1-schnell.safetensors"
    MODEL_LABEL="Schnell"
    DEFAULT_STEPS=4
    MAX_STEPS=12
    USE_GUIDANCE=false
    FILENAME_PREFIX="flux_schnell"
    ;;
  *)
    MODEL_FILE="flux1-dev.safetensors"
    MODEL_LABEL="Dev (Realism)"
    DEFAULT_STEPS=25
    MAX_STEPS=40
    USE_GUIDANCE=true
    FILENAME_PREFIX="flux_dev"
    ;;
esac

# Check model exists
if [ ! -f "$COMFY/models/diffusion_models/$MODEL_FILE" ]; then
  echo
  echo "[!] Model not found: $COMFY/models/diffusion_models/$MODEL_FILE"
  if [ "$MODEL_CHOICE" != "2" ]; then
    echo "[!] Download FLUX.1 Dev first: ./download_flux_dev_models.sh"
  else
    echo "[!] Download Schnell first: ./download_flux_schnell_models.sh"
  fi
  exit 1
fi

# LoRA support (Dev only)
LORA_FILE="$COMFY/models/loras/flux_realism_lora.safetensors"
USE_LORA=false
if [ "$USE_GUIDANCE" = true ] && [ -f "$LORA_FILE" ]; then
  echo
  echo "[+] Realism LoRA detected. Apply it?"
  echo "    Boosts photorealism further. Strength 0.8 recommended."
  read -r -p "Use Realism LoRA? Y/n: " USE_LORA_INPUT
  if [[ ! "$USE_LORA_INPUT" =~ ^[Nn]$ ]]; then
    USE_LORA=true
    read -r -p "LoRA strength [0.8]: " LORA_STRENGTH_INPUT
    LORA_STRENGTH="${LORA_STRENGTH_INPUT:-0.8}"
  fi
fi

echo
read -r -p "Image prompt: " USER_PROMPT

if [ -z "$USER_PROMPT" ]; then
  USER_PROMPT="a person sitting at a cafe table near a window, afternoon light"
fi

echo
echo "Style:"
echo "1) Photo realistic [default] — 35mm documentary, natural light, film grain"
echo "2) Cinematic — film still, dramatic light, shallow DOF, anamorphic"
echo "3) Studio portrait — controlled lighting, sharp, commercial quality"
echo "4) Street photography — candid, motion, gritty, harsh light"
echo "5) Product photo — studio, clean bg, reflections, commercial"
echo "6) Cyberpunk realistic — neon, monitors, practical light, grimy"
echo "7) Cartoon"
echo "8) Anime"
echo

read -r -p "Style [1]: " STYLE_CHOICE

case "$STYLE_CHOICE" in
  2)
    STYLE_PREFIX="cinematic 35mm film still, anamorphic lens, dramatic practical lighting, rich shadows, shallow depth of field, film grain, movie color grade, photorealistic"
    ;;
  3)
    STYLE_PREFIX="professional studio portrait photography, controlled softbox lighting, razor-sharp focus, natural skin texture, pores visible, photorealistic, 85mm lens"
    ;;
  4)
    STYLE_PREFIX="candid street photography, 35mm f/2 lens, harsh midday light, motion blur, gritty urban, raw unedited JPEG look, photojournalism, real world imperfection"
    ;;
  5)
    STYLE_PREFIX="professional product photography, studio lighting, perfect sharp focus, clean white background, realistic reflections and shadows, commercial photo, 100mm macro"
    ;;
  6)
    STYLE_PREFIX="realistic documentary photograph, cybersecurity researcher workspace, glowing monitors, terminal windows, tangled cables, dim room, practical neon glow, 35mm film grain, photojournalism"
    ;;
  7)
    STYLE_PREFIX="clean cartoon illustration, expressive shapes, polished digital art, colorful, charming, smooth outlines"
    ;;
  8)
    STYLE_PREFIX="high quality anime illustration, detailed background, expressive lighting, clean linework, cinematic composition"
    ;;
  *)
    # Default: maximum realism
    STYLE_PREFIX="RAW photo, 35mm f/1.8 lens, natural available light, realistic skin texture, real-world imperfection, slight motion, photojournalism documentary style, film grain, no HDR, no AI look, photorealistic"
    ;;
esac

echo
echo "Quality:"
if [ "$MODEL_LABEL" = "Schnell" ]; then
  echo "1) Fast, 512px, 4 steps"
  echo "2) Balanced, 768px, 4 steps"
  echo "3) Quality, 1024px, 8 steps [default]"
  echo "4) High quality, 1024px, 12 steps"
else
  echo "1) Draft, 512px, 15 steps"
  echo "2) Balanced, 768px, 20 steps"
  echo "3) Quality, 1024px, 25 steps [default]"
  echo "4) Max quality, 1024px, 35 steps"
fi
echo

read -r -p "Quality [3]: " QUALITY_CHOICE

if [ "$MODEL_LABEL" = "Schnell" ]; then
  case "$QUALITY_CHOICE" in
    1) BASE_SIZE=512;  STEPS=4  ;;
    2) BASE_SIZE=768;  STEPS=4  ;;
    4) BASE_SIZE=1024; STEPS=12 ;;
    *) BASE_SIZE=1024; STEPS=8  ;;
  esac
else
  case "$QUALITY_CHOICE" in
    1) BASE_SIZE=512;  STEPS=15 ;;
    2) BASE_SIZE=768;  STEPS=20 ;;
    4) BASE_SIZE=1024; STEPS=35 ;;
    *) BASE_SIZE=1024; STEPS=25 ;;
  esac
fi

echo
echo "Aspect ratio:"
echo "1) Square [default]"
echo "2) Wide (16:9)"
echo "3) Portrait (9:16)"
echo "4) Wide crop (3:2)"
echo

read -r -p "Aspect [1]: " ASPECT_CHOICE

case "$ASPECT_CHOICE" in
  2)
    if   [ "$BASE_SIZE" -eq 512 ];  then WIDTH=768;  HEIGHT=448
    elif [ "$BASE_SIZE" -eq 768 ];  then WIDTH=1024; HEIGHT=576
    else                                  WIDTH=1344; HEIGHT=768; fi
    ;;
  3)
    if   [ "$BASE_SIZE" -eq 512 ];  then WIDTH=448;  HEIGHT=768
    elif [ "$BASE_SIZE" -eq 768 ];  then WIDTH=576;  HEIGHT=1024
    else                                  WIDTH=768;  HEIGHT=1344; fi
    ;;
  4)
    if   [ "$BASE_SIZE" -eq 512 ];  then WIDTH=768;  HEIGHT=512
    elif [ "$BASE_SIZE" -eq 768 ];  then WIDTH=1152; HEIGHT=768
    else                                  WIDTH=1536; HEIGHT=1024; fi
    ;;
  *)
    WIDTH="$BASE_SIZE"
    HEIGHT="$BASE_SIZE"
    ;;
esac

if [ "$USE_GUIDANCE" = true ]; then
  echo
  read -r -p "Guidance scale [3.5] (3.0=loose, 3.5=balanced, 4.5=strict): " GUIDANCE_INPUT
  GUIDANCE="${GUIDANCE_INPUT:-3.5}"
else
  GUIDANCE="1.0"
fi

echo
read -r -p "Upscale after generation to 5K? y/N: " DO_UPSCALE

PROMPT="$STYLE_PREFIX, $USER_PROMPT"

echo
echo "[+] Model:    $MODEL_LABEL ($MODEL_FILE)"
echo "[+] Server:   $COMFY_URL"
echo "[+] Prompt:   $PROMPT"
echo "[+] Size:     ${WIDTH}x${HEIGHT}"
echo "[+] Steps:    $STEPS"
if [ "$USE_GUIDANCE" = true ]; then
  echo "[+] Guidance: $GUIDANCE"
fi
if [ "$USE_LORA" = true ]; then
  echo "[+] LoRA:     flux_realism_lora.safetensors @ $LORA_STRENGTH"
fi
echo

python3 - <<PY
import json
import time
import uuid
import urllib.request
import urllib.error
import sys

COMFY_URL = "$COMFY_URL"
TEXT_PROMPT = """$PROMPT"""
WIDTH = int("$WIDTH")
HEIGHT = int("$HEIGHT")
STEPS = int("$STEPS")
USE_GUIDANCE = "$USE_GUIDANCE" == "true"
GUIDANCE = float("$GUIDANCE")
USE_LORA = "$USE_LORA" == "true"
LORA_STRENGTH = float("${LORA_STRENGTH:-0.8}")
MODEL_FILE = "$MODEL_FILE"
FILENAME_PREFIX = "$FILENAME_PREFIX"

client_id = str(uuid.uuid4())
seed = int(time.time())

# Build workflow
workflow = {}

# UNET loader
if USE_LORA:
    # Load model then apply LoRA
    workflow["1"] = {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": MODEL_FILE,
            "weight_dtype": "default"
        }
    }
    workflow["10"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["1", 0],
            "lora_name": "flux_realism_lora.safetensors",
            "strength_model": LORA_STRENGTH
        }
    }
    model_ref = ["10", 0]
else:
    workflow["1"] = {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": MODEL_FILE,
            "weight_dtype": "default"
        }
    }
    model_ref = ["1", 0]

# Text encoders
workflow["2"] = {
    "class_type": "DualCLIPLoader",
    "inputs": {
        "clip_name1": "clip_l.safetensors",
        "clip_name2": "t5xxl_fp16.safetensors",
        "type": "flux"
    }
}

# Positive conditioning
workflow["3"] = {
    "class_type": "CLIPTextEncode",
    "inputs": {
        "clip": ["2", 0],
        "text": TEXT_PROMPT
    }
}

# Negative (empty for flux)
workflow["4"] = {
    "class_type": "CLIPTextEncode",
    "inputs": {
        "clip": ["2", 0],
        "text": ""
    }
}

# Latent image
workflow["5"] = {
    "class_type": "EmptySD3LatentImage",
    "inputs": {
        "width": WIDTH,
        "height": HEIGHT,
        "batch_size": 1
    }
}

# Apply FluxGuidance for Dev model (critical for quality)
if USE_GUIDANCE:
    workflow["11"] = {
        "class_type": "FluxGuidance",
        "inputs": {
            "conditioning": ["3", 0],
            "guidance": GUIDANCE
        }
    }
    positive_ref = ["11", 0]
else:
    positive_ref = ["3", 0]

# Sampler
workflow["6"] = {
    "class_type": "KSampler",
    "inputs": {
        "model": model_ref,
        "positive": positive_ref,
        "negative": ["4", 0],
        "latent_image": ["5", 0],
        "seed": seed,
        "steps": STEPS,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "denoise": 1.0
    }
}

# VAE
workflow["7"] = {
    "class_type": "VAELoader",
    "inputs": {"vae_name": "ae.safetensors"}
}
workflow["8"] = {
    "class_type": "VAEDecode",
    "inputs": {
        "samples": ["6", 0],
        "vae": ["7", 0]
    }
}
workflow["9"] = {
    "class_type": "SaveImage",
    "inputs": {
        "images": ["8", 0],
        "filename_prefix": FILENAME_PREFIX
    }
}

payload = {"prompt": workflow, "client_id": client_id}

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
except Exception as e:
    print("[!] Could not reach ComfyUI:", e)
    print("[!] Make sure this is running in another terminal:")
    print("    ~/AI/ComfyUI/run.sh")
    sys.exit(1)

prompt_id = result.get("prompt_id")
print("[+] Queued prompt:", prompt_id)
print("[+] Seed:", seed)

if not prompt_id:
    print(json.dumps(result, indent=2))
    sys.exit(1)

print("[+] Waiting for output (Dev model takes longer — worth it)...")

for i in range(900):
    time.sleep(2)

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
            print(json.dumps(status, indent=2))
            sys.exit(1)

        print("[+] Generation done.")

        outputs = item.get("outputs", {})
        for node_id, node_output in outputs.items():
            for img in node_output.get("images", []):
                print("[+] Output image:")
                print("    filename:", img.get("filename"))
                print("    subfolder:", img.get("subfolder"))

        break
else:
    print("[!] Timed out waiting for ComfyUI.")
    sys.exit(1)
PY


echo
echo "[+] Finding newest PNG in: $OUTPUT_DIR"

NEWEST="$(find "$OUTPUT_DIR" -type f -name "*.png" -size +1k -print0 | xargs -0 stat -f "%m %N" 2>/dev/null | sort -nr | head -1 | cut -d" " -f2- || true)"

if [ -z "$NEWEST" ]; then
  echo "[!] No PNG found."
  find "$OUTPUT_DIR" -maxdepth 2 -type f -print | tail -20
  exit 1
fi

echo "[+] Newest image: $NEWEST"

echo
echo "[+] Opening image..."
open "$NEWEST"

DOWNLOAD_DIR="$HOME/Downloads/ComfyUI-Images"
mkdir -p "$DOWNLOAD_DIR"

FINAL_COPY="$DOWNLOAD_DIR/$(basename "$NEWEST")"
cp "$NEWEST" "$FINAL_COPY"

echo "[+] Copied to: $FINAL_COPY"

FILENAME="$(basename "$NEWEST")"
VIEW_URL="$COMFY_URL/view?filename=$FILENAME&type=output"
echo "[+] Browser URL: $VIEW_URL"

if [[ "$DO_UPSCALE" =~ ^[Yy]$ ]]; then
  BASENAME="$(basename "$NEWEST" .png)"
  UPSCALED="$UPSCALE_DIR/${BASENAME}_5k.png"

  echo
  echo "[+] Upscaling to 5K with sips..."
  sips -Z 5120 "$NEWEST" --out "$UPSCALED" >/dev/null
  echo "[+] Upscaled: $UPSCALED"
  open "$UPSCALED"
fi

echo
echo "[+] Done."
