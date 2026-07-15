#!/bin/bash
set -Eeuo pipefail

COMFY="${COMFY_DIR:-$(cd "$(dirname "$0")" && pwd)}"
HOST="${STUDIO_HOST:-0.0.0.0}"
BACKEND_HOST="${COMFY_HOST:-0.0.0.0}"
PORT="${COMFY_PORT:-8188}"
PYTHON="$COMFY/venv/bin/python"
RUN_DIR="$COMFY/.studio-run"
LOG_DIR="$COMFY/logs"
DEBUG=0

if [[ "${1:-}" == "--debug" ]]; then DEBUG=1; shift; fi
if [[ "${1:-}" == "--restart" ]]; then
  if [[ -f "$RUN_DIR/pids" ]]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$RUN_DIR/pids"
  fi
  shift
fi

[[ -x "$PYTHON" ]] || { echo "[!] Missing virtual environment: $PYTHON"; exit 1; }
mkdir -p "$RUN_DIR" "$LOG_DIR"
: > "$RUN_DIR/pids"

cleanup() {
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$RUN_DIR/pids"
}
trap cleanup EXIT INT TERM

start_service() {
  local name="$1" script="$2" port="$3"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[=] $name already listening on $port"
    return
  fi
  echo "[+] Starting $name on http://$HOST:$port"
  local service_args=()
  if [[ "$DEBUG" == 1 && "$script" == "webui.py" ]]; then service_args+=(--debug); fi
  if (( ${#service_args[@]} )); then
    STUDIO_DEBUG="$DEBUG" IMAGE_STUDIO_HOST="$HOST" COMFY_URL="http://127.0.0.1:$PORT" \
      "$PYTHON" "$COMFY/$script" "${service_args[@]}" >> "$LOG_DIR/$name.log" 2>&1 &
  else
    STUDIO_DEBUG="$DEBUG" IMAGE_STUDIO_HOST="$HOST" COMFY_URL="http://127.0.0.1:$PORT" \
      "$PYTHON" "$COMFY/$script" >> "$LOG_DIR/$name.log" 2>&1 &
  fi
  echo $! >> "$RUN_DIR/pids"
}

start_service image-studio webui.py 8190
start_service video-studio videoui.py 8192
start_service studio-hub studio_hub.py 8189

# Music Studio lives in a separate repo with its own venv + supervisor.
MUSIC_LAUNCHER="$HOME/AI/MusicStudio/run_music_studio.sh"
if [[ -x "$MUSIC_LAUNCHER" ]]; then
  if lsof -nP -iTCP:8191 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[=] music-studio already listening on 8191"
  else
    echo "[+] Starting music-studio on http://$HOST:8191"
    nohup "$MUSIC_LAUNCHER" >> "$LOG_DIR/music-studio.log" 2>&1 &
    echo $! >> "$RUN_DIR/pids"
  fi
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[=] ComfyUI backend already listening on $PORT"
  echo "[+] Image Studio: http://127.0.0.1:8190"
  echo "[+] Nothing else to start."
  trap - EXIT INT TERM
  exit 0
fi

echo "[+] Starting ComfyUI backend on $BACKEND_HOST:$PORT"
echo "[+] Logs: $LOG_DIR"
# fp16 attention overflows to NaN on Apple MPS for Wan video models (glowing-blob
# output). Force fp32 + split cross-attention for numerically stable video decode.
# Dropped --cache-none so the 17GB model set isn't reloaded from disk every job.
args=(main.py --listen "$BACKEND_HOST" --port "$PORT" --preview-method none \
      --force-fp32 --use-split-cross-attention)
if [[ "$DEBUG" == 1 ]]; then args+=(--verbose DEBUG); fi
cd "$COMFY"
exec "$PYTHON" "${args[@]}"
