#!/bin/bash
set -Eeuo pipefail

COMFY="${COMFY:-$(cd "$(dirname "$0")" && pwd)}"
BASE="https://huggingface.co/Comfy-Org"

mkdir -p "$COMFY/models/diffusion_models" "$COMFY/models/text_encoders" "$COMFY/models/vae"

download() {
  local url="$1" output="$2"
  local partial="$output.part"
  if [[ -s "$output" ]]; then echo "[=] Already present: $(basename "$output")"; return; fi
  echo "[+] Downloading $(basename "$output")"
  curl --fail --location --retry 5 --retry-delay 5 --continue-at - --output "$partial" "$url"
  mv "$partial" "$output"
}

# Fast modern default: ~12 GB using the official FP4 encoder.
download "$BASE/flux2-klein/resolve/main/split_files/diffusion_models/flux-2-klein-4b.safetensors" \
  "$COMFY/models/diffusion_models/flux-2-klein-4b.safetensors"
download "$BASE/flux2-klein/resolve/main/split_files/text_encoders/qwen_3_4b_fp4_flux2.safetensors" \
  "$COMFY/models/text_encoders/qwen_3_4b_fp4_flux2.safetensors"
download "$BASE/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors" \
  "$COMFY/models/vae/flux2-vae.safetensors"

# NOTE: FLUX.2 Dev (flux2_dev_fp8mixed + mistral encoder, ~44 GB) is intentionally
# NOT downloaded — MPS cannot transfer its Float8 tensors, so it is unusable on this
# Mac and is hidden from the Image Studio. Re-add here only if running on CUDA.

echo "[+] FLUX.2 Klein download complete."
