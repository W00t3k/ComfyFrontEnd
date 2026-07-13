# ComfyFrontEnd

Browser studios layered on top of a local [ComfyUI](https://github.com/comfy-org/ComfyUI)
backend — image, video, and a hub — plus model-download and CLI helper scripts.
Built and tuned for Apple Silicon (M-series, MPS).

## Services

| Studio | Port | File | What it does |
|--------|------|------|--------------|
| Image  | 8190 | `webui.py`      | FLUX.2 Klein / FLUX.1 Schnell / Dev / Qwen — text-to-image, styles, enhance, gallery |
| Video  | 8192 | `videoui.py`    | Wan 2.2 — text-to-video, image-to-video, real per-step progress |
| Hub    | 8189 | `studio_hub.py` | Landing page linking all studios + recent-output grid |

The ComfyUI backend itself runs on port 8188.

## Quick start

```bash
# 1. Put this repo's scripts next to a ComfyUI checkout (or set COMFY_DIR).
# 2. Download models for whatever you want to run:
./download_flux2_models.sh          # FLUX.2 Klein (image)
./download_wan22_video_models.sh    # Wan 2.2 (video)

# 3. Launch backend + all studios:
./run.sh
```

Then open the hub at `http://<host>:8189`.

## Layout

- `webui.py` / `videoui.py` / `studio_hub.py` — the three studio servers
- `run.sh` — launches the ComfyUI backend + all studios (incl. Music Studio if present)
- `download_*.sh` — resumable model downloads
- `generate_flux.sh` / `generate_video.sh` / `comfy_tool.sh` — CLI generation helpers
- `tasks/todo.md` — roadmap / known issues

## Notes

- All services bind `0.0.0.0` with no auth — keep them on a trusted LAN.
- FLUX.2 Dev is intentionally not downloaded: MPS cannot transfer its Float8 tensors.
- Models, output, and logs are gitignored.
