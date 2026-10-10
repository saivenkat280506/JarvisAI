#!/bin/zsh
# Demo: slow scroll (5s gap) → YouTube Music play
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

echo "=== JARVIS Browser Demo ==="
echo "1) Scroll (5 second gaps)"
echo "2) Play on YouTube Music"
echo

"$ROOT/backend/.venv/bin/python" - <<'PY'
from executor.browser_puppeteer import linkedin_browser_demo
ok, msg = linkedin_browser_demo({
    "song": "AC/DC Back in Black",
    "times": 4,
    "pixels": 350,
    "service": "youtube_music",
})
print("OK" if ok else "PARTIAL", msg)
PY

echo
echo "Demo finished. Browser window left open."
