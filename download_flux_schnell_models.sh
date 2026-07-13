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

echo "[+] Downloading FLUX.1 Schnell diffusion model..."
curl -L --continue-at - \
  -o "$COMFY/models/diffusion_models/flux1-schnell.safetensors" \
  "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors"

echo "[+] Downloading CLIP-L text encoder..."
curl -L --continue-at - \
  -o "$COMFY/models/text_encoders/clip_l.safetensors" \
  "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"

echo "[+] Downloading T5XXL FP16 text encoder..."
curl -L --continue-at - \
  -o "$COMFY/models/text_encoders/t5xxl_fp16.safetensors" \
  "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors"

echo "[+] Downloading AE VAE for Flux..."
curl -L --continue-at - \
  -o "$COMFY/models/vae/ae.safetensors" \
  "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors"

echo
echo "[+] Downloads complete."
echo
echo "[+] Files:"
ls -lh "$COMFY/models/diffusion_models/"
ls -lh "$COMFY/models/text_encoders/"
ls -lh "$COMFY/models/vae/"

echo
echo "[+] Restart ComfyUI, then click Refresh in the missing models panel."
