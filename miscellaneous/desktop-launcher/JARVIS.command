#!/bin/zsh
# Double-click opens the J.A.R.V.I.S. desktop app, not a browser tab.
# Lives in miscellaneous/desktop-launcher/, so the repo root is two levels up.
cd "$(dirname "$0")/../.." || exit 1
chmod +x ./scripts/jarvis-launcher.sh 2>/dev/null
if [[ -d "./miscellaneous/desktop-launcher/JARVIS.app" && $# -eq 0 ]]; then
  open "./miscellaneous/desktop-launcher/JARVIS.app"
  exit 0
fi
exec ./scripts/jarvis-launcher.sh "$@"
