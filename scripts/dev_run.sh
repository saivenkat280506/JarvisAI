#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PROJECT_ROOT}/backend/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Backend virtualenv missing. Create backend/.venv and install backend/requirements.txt first." >&2
  exit 1
fi

cleanup() {
  [[ -z "${BACKEND_PID:-}" ]] || kill "${BACKEND_PID}" 2>/dev/null || true
  [[ -z "${FRONTEND_PID:-}" ]] || kill "${FRONTEND_PID}" 2>/dev/null || true
  pkill -f "${PROJECT_ROOT}/frontend/electron-main.js" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"${PYTHON}" "${PROJECT_ROOT}/backend/main.py" &
BACKEND_PID=$!
npm run desktop --prefix "${PROJECT_ROOT}/frontend" &
FRONTEND_PID=$!

echo "Backend: http://127.0.0.1:8000"
echo "Desktop: Electron window (Next.js dev server on 127.0.0.1:3000)"
echo "Press Ctrl-C to stop both processes."
wait "${BACKEND_PID}" "${FRONTEND_PID}"
