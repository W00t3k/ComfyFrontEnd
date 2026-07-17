#!/bin/bash
set -Eeuo pipefail

# LTX-Video 2B (v0.9.5) — fast local video model. The T5 text encoder
# (t5xxl_fp16) is shared with the Flux models and is expected to already exist.
COMFY="${COMFY:-$(cd "$(dirname "$0")" && pwd)}"
CKPT_DIR="$COMFY/models/checkpoints"
ENC_DIR="$COMFY/models/text_encoders"
mkdir -p "$CKPT_DIR" "$ENC_DIR"

download() {
  local url="$1" out="$2" part="$2.part"
  if [[ -s "$out" ]]; then echo "[=] Already present: $(basename "$out")"; return; fi
  echo "[+] Downloading $(basename "$out")"
  curl --fail --location --retry 5 --retry-delay 5 --continue-at - --output "$part" "$url"
  mv "$part" "$out"
}

download "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors" \
  "$CKPT_DIR/ltx-video-2b-v0.9.5.safetensors"

# Shared T5 encoder (only fetched if missing).
download "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors" \
  "$ENC_DIR/t5xxl_fp16.safetensors"

echo "[+] LTX-Video ready."
