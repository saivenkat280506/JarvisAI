#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is required. Install Ollama, start it, then run this script again." >&2
  exit 1
fi

models=("qwen3:4b")
if [[ "${1:-}" == "--planning" ]]; then
  models+=("qwen3:8b")
fi

for model in "${models[@]}"; do
  echo "Pulling local Ollama model: ${model}"
  ollama pull "${model}"
done

backend_python="$script_dir/../backend/.venv/bin/python"
if [[ ! -x "$backend_python" ]]; then
  echo "Create backend/.venv and install backend/requirements.txt before downloading local STT." >&2
  exit 1
fi

echo "Preparing local English STT model: base.en"
"$backend_python" - <<'PY'
from faster_whisper import WhisperModel

# Loading the model downloads its weights once into the local Hugging Face cache.
WhisperModel("base.en", device="cpu", compute_type="int8")
PY

echo "Local models ready: ${models[*]} + faster-whisper base.en"
