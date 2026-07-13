# ComfyUI Studio — improvement TODO

Audited: image (webui.py :8190), video (videoui.py :8192), hub (studio_hub.py :8189),
gallery.py (dead), music (~/AI/MusicStudio :8191), run.sh, download/generate scripts.
Stack verified running: backend 0.21.1, MPS on, all 4 studios reachable.

## P0 — correctness / security

- [ ] **Path traversal in image serving.** `webui.py` `/img/` (GET) and `gallery.py` `/img/`,`/thumb/`
      join user input to `OUTPUT_DIR` with no `relative_to()` guard — unlike the DELETE handler and
      videoui/hub which DO guard. Add the same resolve+relative_to check to every GET file handler.
- [ ] **No auth on 0.0.0.0.** All 4 services bind every interface with zero auth (already in
      TODO_STUDIO follow-up). Anyone on the LAN can generate/delete/download-trigger. Add a shared
      token header or bind backend calls to localhost + front a single authed proxy.
- [ ] **Music studio not auto-started.** `run.sh` starts image/video/hub + backend but NOT music;
      it lives in a separate repo with its own supervisor. After reboot only 3/4 come up and the hub
      shows music "offline". Either add a music start_service line to run.sh or document that
      `~/AI/MusicStudio/run_music_studio.sh` must run separately. (Stale PID file also pointed at a
      dead process — the supervisor should clear it on exit.)

## P1 — dead code / wasted resources

- [ ] **Delete gallery.py.** Binds :8189 — same port as studio_hub.py → conflict if both launch.
      Superseded by the hub + webui's built-in gallery. Not started by anything. Remove it.
- [ ] **Reclaim 44 GB of unusable FLUX.2 Dev weights.** `flux2_dev_fp8mixed.safetensors` (33 GB) +
      `mistral_3_small_flux2_fp4_mixed.safetensors` (11 GB) are downloaded but FLUX.2 Dev is
      intentionally hidden (MPS can't transfer its Float8 tensors — per TODO_STUDIO). `download_flux2_models.sh`
      still pulls them every run. Drop the Dev half of that script and delete the files, or gate behind a flag.
- [ ] **Dead `flux2_dev` references in webui.py.** Present in `DOWNLOADERS` and `build_flux2_workflow`'s
      `architecture == "flux2_dev"` branch, but there's no `flux2_dev` entry in `MODELS` — unreachable.
      Remove, or add the model entry if it should be selectable.
- [ ] **Remove committed `.bak` files:** `generate_flux.sh.bak`, `download_flux_schnell_models.sh.bak`.

## P2 — robustness / maintainability

- [ ] **Put the studio code under git.** webui/videoui/studio_hub/run.sh/download scripts are all
      untracked (`??`). One bad edit = no recovery. Add a `.gitignore` for models/output/logs/venv and
      commit the code.
- [ ] **Hardcoded LAN IP `192.168.2.69`.** Baked into videoui.py (COMFY_URL + header links) and
      studio_hub.py. TODO_STUDIO claims nav hostnames were de-hardcoded, but these remain — breaks on
      IP change / different host. Use relative URLs + `location.hostname` like webui.py already does,
      and read COMFY_URL from env.
- [ ] **Files served via full `read_bytes()` into memory.** webui/videoui/hub/gallery load the entire
      file before sending — fine for images, wasteful for multi-MB videos and blocks the worker thread.
      Stream in chunks; add `Content-Type`/range support for video seeking.
- [ ] **`list_images()` / `/img/<name>` key by basename only** while `rglob` walks subfolders. Two files
      with the same name in different subfolders collide, and `/img/<name>` can't resolve a nested file.
      Key by path relative to OUTPUT_DIR (matches what hub/videoui already do).
- [ ] **Three copies of `ThreadingHTTPServer`.** studio_hub.py and videoui.py each reimplement it;
      webui.py imports the stdlib one. Consolidate into one shared helper module.
- [ ] **Video progress is fake.** videoui.py shows a time-based soft bar (`secs/4`) because it polls
      `/history` only. webui.py already has a real WebSocket progress reader — reuse it for per-step %.

## P3 — nice to have

- [ ] **SHA-256 manifests for downloads** (already in TODO_STUDIO follow-up) — verify after download,
      resume/redownload on mismatch.
- [ ] **Shared MODELS config.** Image model list lives only in webui.py; CLI `generate_flux.sh` and
      `comfy_tool.sh` duplicate model paths/steps. Single source of truth (JSON) consumed by both.
- [ ] **Health/restart supervision** for the studios (run.sh starts once, no auto-restart on crash),
      matching the music studio's restart loop.
