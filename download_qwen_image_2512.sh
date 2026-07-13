#!/bin/bash
set -e

COMFY="$HOME/AI/ComfyUI"
cd "$COMFY"

mkdir -p models/diffusion_models
mkdir -p models/text_encoders
mkdir -p models/vae
mkdir -p models/loras

source venv/bin/activate 2>/dev/null || true
python3 -m pip install -U huggingface_hub

echo "[+] Downloading Qwen Image 2512 model files..."

python3 - <<'PY'
from huggingface_hub import hf_hub_download

downloads = [
    (
        "Comfy-Org/Qwen-Image_ComfyUI",
        "split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors",
        "models/diffusion_models",
    ),
    (
        "Comfy-Org/Qwen-Image_ComfyUI",
        "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "models/text_encoders",
    ),
    (
        "Comfy-Org/Qwen-Image_ComfyUI",
        "split_files/vae/qwen_image_vae.safetensors",
        "models/vae",
    ),
    (
        "lightx2v/Qwen-Image-2512-Lightning",
        "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
        "models/loras",
    ),
]

for repo_id, filename, local_dir in downloads:
    print(f"[+] {repo_id} :: {filename}")
    hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        local_dir_use_symlinks=False,
    )

print("[+] Done.")
PY

echo
echo "[+] Verifying Qwen files:"
du -h models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
du -h models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
du -h models/vae/qwen_image_vae.safetensors
du -h models/loras/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors
