#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

resolve_backend_dir() {
  local d
  for d in */; do
    d="${d%/}"
    case "$d" in
      Figma\ Prototype|config|expedia-listing-cli|node_modules) continue ;;
    esac
    if [[ -f "$d/main.py" && -f "$d/requirements.txt" ]]; then
      echo "$d"
      return 0
    fi
  done
  echo "No backend directory with main.py and requirements.txt found under $ROOT" >&2
  return 1
}

BACKEND="$(resolve_backend_dir)"
PORT="${PORT:-8000}"

do_build() {
  echo "[setup] Building React frontend (Figma Prototype)..."
  (cd "Figma Prototype" && npm install && npm run build)
  echo "[setup] Installing Python dependencies..."
  pip install -r "$BACKEND/requirements.txt"
}

do_start() {
  if [[ ! -f "Figma Prototype/dist/index.html" ]]; then
    echo "[start] Wanderly SPA dist missing; running frontend build..."
    do_build
  fi
  export WANDERLY_DIST_DIR="${ROOT}/Figma Prototype/dist"
  echo "[start] Wanderly SPA dist: ${WANDERLY_DIST_DIR}"
  echo "[start] Launching Wanderly server on port ${PORT}..."
  exec python3 "$BACKEND/main.py" --ui --host "${HOST:-127.0.0.1}" --port "$PORT"
}

cmd="${1:-all}"
case "$cmd" in
  build) do_build ;;
  start) do_start ;;
  all) do_build; do_start ;;
  *) echo "Usage: $0 {build|start|all}" >&2; exit 2 ;;
esac
