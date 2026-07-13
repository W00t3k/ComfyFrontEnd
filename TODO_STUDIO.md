# Image Studio reliability TODO

## Done

- [x] Keep and harden the existing Image Studio GUI on port 8190.
- [x] Remove hard-coded browser navigation hostnames so LAN clients stay on the host they opened.
- [x] Use a threaded HTTP server so image requests and status checks cannot freeze the GUI.
- [x] Add `--debug` / `STUDIO_DEBUG=1` diagnostics and an `/api/debug` health endpoint.
- [x] Validate every required diffusion, encoder, VAE, and LoRA file before enabling a model.
- [x] Reject generation requests for incomplete models instead of failing deep inside ComfyUI.
- [x] Add one-click, resumable model downloads for the supported Image Studio models.
- [x] Add a safer launcher with PID/log management and an explicit restart mode.
- [x] Bind browser services to all interfaces while keeping backend calls on localhost.
- [x] Confirm Metal/MPS acceleration is available on the M4 Pro in the real runtime (PyTorch 2.12.0).
- [x] Add and verify FLUX.2 Klein 4B with its lower-memory FP4 encoder.
- [x] Compatibility-test FLUX.2 Dev; retain its files but hide it from 8190 because MPS cannot transfer its Float8 tensors.

## Follow-up

- [ ] Add authentication or a firewall rule before exposing ports 8188–8192 beyond the trusted LAN.
- [ ] Add SHA-256 manifests for downloaded model files when upstream projects publish stable hashes.
