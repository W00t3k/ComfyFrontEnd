#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"

echo
echo "FLUX.1 Dev Model Downloader"
echo "==========================="
echo
echo "FLUX.1 Dev = high-quality realism. Schnell = fast drafts."
echo "Dev is gated on HuggingFace — you must:"
echo "  1. Accept terms at: https://huggingface.co/black-forest-labs/FLUX.1-dev"
echo "  2. Create a token at: https://huggingface.co/settings/tokens"
echo

if [ ! -d "$COMFY" ]; then
  echo "[!] ComfyUI not found at: $COMFY"
  exit 1
fi

# Get HF token
if [ -n "$HF_TOKEN" ]; then
  echo "[+] Using HF_TOKEN from environment."
else
  read -r -p "HuggingFace token (hf_...): " HF_TOKEN
fi

if [ -z "$HF_TOKEN" ]; then
  echo "[!] No token provided. Cannot download gated model."
  exit 1
fi

echo
echo "[+] Creating model folders..."
mkdir -p "$COMFY/models/diffusion_models"
mkdir -p "$COMFY/models/text_encoders"
mkdir -p "$COMFY/models/vae"
mkdir -p "$COMFY/models/loras"

echo "[+] Downloading FLUX.1 Dev diffusion model (~24GB, will take a while)..."
curl -L --continue-at - \
  -H "Authorization: Bearer $HF_TOKEN" \
  -o "$COMFY/models/diffusion_models/flux1-dev.safetensors" \
  "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors"

# Text encoders — reuse from Schnell if already present
if [ -f "$COMFY/models/text_encoders/clip_l.safetensors" ]; then
  echo "[+] clip_l.safetensors already exists, skipping."
else
  echo "[+] Downloading CLIP-L text encoder..."
  curl -L --continue-at - \
    -o "$COMFY/models/text_encoders/clip_l.safetensors" \
    "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"
fi

if [ -f "$COMFY/models/text_encoders/t5xxl_fp16.safetensors" ]; then
  echo "[+] t5xxl_fp16.safetensors already exists, skipping."
else
  echo "[+] Downloading T5XXL FP16 text encoder..."
  curl -L --continue-at - \
    -o "$COMFY/models/text_encoders/t5xxl_fp16.safetensors" \
    "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors"
fi

if [ -f "$COMFY/models/vae/ae.safetensors" ]; then
  echo "[+] ae.safetensors already exists, skipping."
else
  echo "[+] Downloading AE VAE..."
  curl -L --continue-at - \
    -H "Authorization: Bearer $HF_TOKEN" \
    -o "$COMFY/models/vae/ae.safetensors" \
    "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors"
fi

echo
echo "[+] Optional: Download XLabs Realism LoRA (adds photorealism on top of Dev)?"
echo "    Requires separate HF access — model is public."
read -r -p "Download Realism LoRA? y/N: " GET_LORA

if [[ "$GET_LORA" =~ ^[Yy]$ ]]; then
  echo "[+] Downloading XLabs Realism LoRA..."
  curl -L --continue-at - \
    -o "$COMFY/models/loras/flux_realism_lora.safetensors" \
    "https://huggingface.co/XLabs-AI/flux-lora-collection/resolve/main/realism_lora.safetensors"
  echo "[+] LoRA saved to: $COMFY/models/loras/flux_realism_lora.safetensors"
fi

echo
echo "[+] Downloads complete."
echo
echo "[+] Files:"
ls -lh "$COMFY/models/diffusion_models/"
ls -lh "$COMFY/models/text_encoders/"
ls -lh "$COMFY/models/vae/"
echo
echo "[+] Restart ComfyUI, then use generate_flux.sh and choose Dev model."
