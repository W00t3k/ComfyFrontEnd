#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"

echo "[+] Using ComfyUI path: $COMFY"

if [ ! -d "$COMFY" ]; then
  echo "[!] ComfyUI folder not found at: $COMFY"
  echo "    Edit COMFY in this script if your ComfyUI path is different."
  exit 1
fi

echo "[+] Creating model folders..."
mkdir -p "$COMFY/models/diffusion_models"
mkdir -p "$COMFY/models/text_encoders"
mkdir -p "$COMFY/models/vae"

echo "[+] Downloading Wan 2.2 TI2V 5B diffusion model (~10 GB)..."
curl -L --continue-at - \
  -o "$COMFY/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"

echo "[+] Downloading UMT5-XXL FP8 text encoder (~6.7 GB)..."
curl -L --continue-at - \
  -o "$COMFY/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"

echo "[+] Downloading Wan 2.2 VAE (~1.4 GB)..."
curl -L --continue-at - \
  -o "$COMFY/models/vae/wan2.2_vae.safetensors" \
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors"

echo
echo "[+] Downloads complete."
echo
echo "[+] Files:"
ls -lh "$COMFY/models/diffusion_models/" | grep -i wan || true
ls -lh "$COMFY/models/text_encoders/" | grep -i umt5 || true
ls -lh "$COMFY/models/vae/" | grep -i wan || true

echo
echo "[+] Restart ComfyUI, then run ./generate_video.sh"
