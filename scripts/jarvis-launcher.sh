#!/usr/bin/env bash
# J.A.R.V.I.S. macOS launcher
# Starts backend + frontend + desktop window. Closing this window (or Electron)
# stops every Jarvis process.
#
#   ./scripts/jarvis-launcher.sh           # start (or stop if already running)
#   ./scripts/jarvis-launcher.sh --start
#   ./scripts/jarvis-launcher.sh --stop

set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# JARVIS.app is a script bundle; LaunchServices may start it under Rosetta.
# x86_64 Python then crashes pyautogui/rubicon (objc_msgSendSuper_stret).
if [[ "$(uname -m)" == "x86_64" && -x /usr/bin/arch ]]; then
  exec /usr/bin/arch -arm64 /usr/bin/env bash "$ROOT/scripts/jarvis-launcher.sh" "$@"
fi

BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
ELECTRON_MAIN="$FRONTEND_DIR/electron-main.js"
PUPPETEER_SERVER="$BACKEND_DIR/browser-automation/src/server.mjs"
PYTHON="$BACKEND_DIR/.venv/bin/python"
MAIN_PY="$BACKEND_DIR/main.py"
# Runtime logs live in miscellaneous/ alongside the other non-source artifacts.
LOG_DIR="$ROOT/miscellaneous/logs"
PID_FILE="$LOG_DIR/jarvis.pids"
BACKEND_LOG="$LOG_DIR/backend.log"
BACKEND_ERR="$LOG_DIR/backend.err.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
FRONTEND_ERR="$LOG_DIR/frontend.err.log"
ELECTRON_LOG="$LOG_DIR/electron.log"
DESKTOP_LOG="$LOG_DIR/desktop.log"
LAUNCHER_LOG="$LOG_DIR/launcher.log"
LAUNCHER_LOCK="$LOG_DIR/jarvis-launcher.lock"
LAUNCHER_PID_FILE="$LOG_DIR/jarvis-launcher.pid"
# Electron lives in frontend/node_modules on fresh clones; fall back to the
# legacy root node_modules on machines set up before the frontend/ split.
if [[ -d "$FRONTEND_DIR/node_modules/electron/dist/Electron.app" ]]; then
  ELECTRON_APP="$FRONTEND_DIR/node_modules/electron/dist/Electron.app"
else
  ELECTRON_APP="$ROOT/node_modules/electron/dist/Electron.app"
fi
ELECTRON_BIN="$ELECTRON_APP/Contents/MacOS/Electron"
ELECTRON_ZIP_CACHE="$HOME/Library/Caches/electron"
# Native fallback window sources are archived under miscellaneous/.
SWIFT_SRC="$ROOT/miscellaneous/desktop-launcher/jarvis-desktop-window.swift"
SWIFT_BIN="$ROOT/miscellaneous/desktop-launcher/jarvis-desktop-window"

FRONTEND_PID=""
BACKEND_PID=""
DESKTOP_PID=""

# Next.js / npm must not pop the system browser — the desktop window is the UI.
export BROWSER=none
export BROWSER_PATH=/usr/bin/true

# Command-substitution subshells must not SIGHUP background jobs on exit.
set +o huponexit 2>/dev/null || true

mkdir -p "$LOG_DIR"

# lockf holds this lock for the full desktop session. A second app click while
# startup is still underway must not launch another Next/Turbopack process tree.
LOCKED_RUN=0
if [[ "${1:-}" == "--__locked" ]]; then
  LOCKED_RUN=1
  shift
fi

# Finder / JARVIS.app launches have no TTY. Persist output so a silent
# quit can be diagnosed from miscellaneous/logs/launcher.log.
if [[ ! -t 1 ]]; then
  exec >>"$LAUNCHER_LOG" 2>&1
fi

MODE="toggle"
for arg in "$@"; do
  case "$arg" in
    --start|-Start|-start) MODE="start" ;;
    --stop|-Stop|-stop) MODE="stop" ;;
  esac
done

cyan=$'\033[36m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'; gray=$'\033[90m'; white=$'\033[97m'; reset=$'\033[0m'

banner() {
  echo ""
  echo "${cyan}  ========================================================${reset}"
  echo "${white}       J.A.R.V.I.S.  -  Just A Rather Very Intelligent System${reset}"
  echo "${cyan}       $1  |  macOS${reset}"
  echo "${cyan}  ========================================================${reset}"
  echo ""
}

step() {
  local status="$2"
  local color="$gray"
  case "$status" in
    OK) color="$green" ;;
    WAIT) color="$yellow" ;;
    ERR) color="$red" ;;
    INFO) color="$cyan" ;;
  esac
  printf "  ${color}[%-4s]${reset} %s\n" "$status" "$1" >&2
}

port_open() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

backend_healthy() {
  curl -sf --max-time 2 "http://127.0.0.1:8000/health" >/dev/null 2>&1
}

wait_http() {
  local url="$1"
  local timeout="${2:-90}"
  local i=0
  while (( i < timeout )); do
    if curl -sf --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

listen_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1
}

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 >/dev/null 2>&1 || true
    step "Freed port $port" "OK"
  fi
}

# Nothing else starts Ollama, and without it every LLM-backed answer degrades
# to the canned "I am ready and at your service, sir." fallback.
ollama_port() {
  local url="${OLLAMA_BASE_URL:-http://localhost:11434}"
  local port
  port="$(printf '%s' "$url" | sed -E 's#^[a-zA-Z]+://[^:/]+:([0-9]+).*#\1#')"
  if [[ "$port" =~ ^[0-9]+$ ]]; then
    printf '%s' "$port"
  else
    printf '11434'
  fi
}

ollama_healthy() {
  curl -sf --max-time 3 "http://127.0.0.1:$(ollama_port)/api/tags" >/dev/null 2>&1
}

ensure_ollama() {
  local port
  port="$(ollama_port)"
  if ollama_healthy; then
    step "Ollama online (:$port)" "OK"
    return 0
  fi

  step "Ollama not running — starting it..." "WAIT"
  local bin=""
  if command -v ollama >/dev/null 2>&1; then
    bin="$(command -v ollama)"
  else
    local candidate
    for candidate in /opt/homebrew/bin/ollama /usr/local/bin/ollama; do
      if [[ -x "$candidate" ]]; then
        bin="$candidate"
        break
      fi
    done
  fi

  if [[ -n "$bin" ]]; then
    nohup "$bin" serve >>"$LOG_DIR/ollama.log" 2>&1 &
  elif [[ -d /Applications/Ollama.app ]]; then
    open -a Ollama >/dev/null 2>&1 || true
  else
    step "Ollama not installed — LLM answers will use canned fallbacks" "ERR"
    return 1
  fi

  if wait_http "http://127.0.0.1:$port/api/tags" 45; then
    step "Ollama online (:$port)" "OK"
    return 0
  fi
  step "Ollama failed to start — see miscellaneous/logs/ollama.log" "ERR"
  return 1
}

save_pids() {
  printf 'backend=%s\ndesktop=%s\nstarted=%s\n' \
    "${1:-}" "${2:-}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$PID_FILE"
}

notify() {
  local msg="${1:-}"
  [[ -z "$msg" ]] && return 0
  osascript -e "display notification \"${msg}\" with title \"J.A.R.V.I.S.\"" >/dev/null 2>&1 || true
}

die() {
  local msg="${1:-J.A.R.V.I.S. failed to start}"
  step "$msg" "ERR"
  notify "Failed to start"
  if [[ ! -t 1 ]]; then
    osascript -e "display dialog \"${msg}\" buttons {\"OK\"} default button 1 with title \"J.A.R.V.I.S.\" with icon stop" >/dev/null 2>&1 || true
  fi
  exit 1
}

focus_desktop_window() {
  osascript >/dev/null 2>&1 <<'APPLESCRIPT' || true
tell application "System Events"
  set names to name of every process
  if names contains "J.A.R.V.I.S." then
    set frontmost of process "J.A.R.V.I.S." to true
  else if names contains "jarvis-desktop" then
    set frontmost of process "jarvis-desktop" to true
  else if names contains "Electron" then
    set frontmost of process "Electron" to true
  else if names contains "jarvis-desktop-window" then
    set frontmost of process "jarvis-desktop-window" to true
  end if
end tell
APPLESCRIPT
}

desktop_window_running() {
  pgrep -f "$ELECTRON_MAIN" >/dev/null 2>&1 && return 0
  pgrep -f "$ELECTRON_BIN" >/dev/null 2>&1 && return 0
  pgrep -f "$SWIFT_BIN" >/dev/null 2>&1 && return 0
  pgrep -f "jarvis-desktop-window" >/dev/null 2>&1 && return 0
  return 1
}

brand_electron_app() {
  local plist="$ELECTRON_APP/Contents/Info.plist"
  [[ -f "$plist" ]] || return 0
  /usr/libexec/PlistBuddy -c "Set :CFBundleName J.A.R.V.I.S." "$plist" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleName string J.A.R.V.I.S." "$plist" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName J.A.R.V.I.S." "$plist" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string J.A.R.V.I.S." "$plist" >/dev/null 2>&1 || true
  if [[ -f "$ROOT/jarvis_icon.icns" ]]; then
    cp "$ROOT/jarvis_icon.icns" "$ELECTRON_APP/Contents/Resources/electron.icns" 2>/dev/null || true
  fi
  xattr -cr "$ELECTRON_APP" >/dev/null 2>&1 || true
}

find_electron_zip() {
  find "$ELECTRON_ZIP_CACHE" -name 'electron-v*-darwin-*.zip' -type f 2>/dev/null | tail -n 1
}

ensure_electron() {
  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    echo "$ELECTRON_BIN"
    return 0
  fi

  step "Preparing Electron desktop runtime..." "WAIT"
  local electron_dir=""
  if [[ -d "$FRONTEND_DIR/node_modules/electron" ]]; then
    electron_dir="$FRONTEND_DIR/node_modules/electron"
  else
    electron_dir="$ROOT/node_modules/electron"
  fi
  mkdir -p "$electron_dir/dist"

  if [[ -f "$electron_dir/install.js" ]]; then
    (cd "$FRONTEND_DIR" && node "$electron_dir/install.js") >/dev/null 2>&1 || true
  fi

  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    echo "$ELECTRON_BIN"
    return 0
  fi

  # npm's extract-zip often leaves only LICENSE files on macOS. Use system unzip.
  local zip
  zip="$(find_electron_zip)"
  if [[ -z "$zip" && -f "$electron_dir/install.js" ]]; then
    (cd "$FRONTEND_DIR" && node "$electron_dir/install.js") >/dev/null 2>&1 || true
    zip="$(find_electron_zip)"
  fi
  if [[ -n "$zip" ]]; then
    rm -rf "$ELECTRON_APP"
    unzip -qo "$zip" -d "$electron_dir/dist"
    printf '%s\n' 'Electron.app/Contents/MacOS/Electron' > "$electron_dir/path.txt"
  fi

  if [[ -x "$ELECTRON_BIN" ]]; then
    brand_electron_app
    step "Electron runtime ready" "OK"
    echo "$ELECTRON_BIN"
    return 0
  fi

  step "Electron binary is not available" "ERR"
  return 1
}

ensure_native_window() {
  if [[ -x "$SWIFT_BIN" ]]; then
    echo "$SWIFT_BIN"
    return 0
  fi
  if [[ ! -f "$SWIFT_SRC" ]]; then
    return 1
  fi
  if ! command -v swiftc >/dev/null 2>&1; then
    return 1
  fi
  step "Building native macOS desktop window..." "WAIT"
  if swiftc -O -o "$SWIFT_BIN" "$SWIFT_SRC" -framework Cocoa -framework WebKit 2>"$DESKTOP_LOG"; then
    chmod +x "$SWIFT_BIN"
    step "Native desktop window ready" "OK"
    echo "$SWIFT_BIN"
    return 0
  fi
  return 1
}

start_desktop_window() {
  export JARVIS_EXTERNAL_BACKEND="1"
  pkill -f "$ELECTRON_MAIN" >/dev/null 2>&1 || true
  pkill -f "$SWIFT_BIN" >/dev/null 2>&1 || true
  if port_open 3000 && ! wait_http "http://127.0.0.1:3000" 2; then
    free_port 3000
    sleep 0.5
  fi

  rm -f "$DESKTOP_LOG"
  start_frontend
  local win_bin
  if win_bin="$(ensure_native_window 2>/dev/null)" && [[ -x "$win_bin" ]]; then
    step "Launching native macOS desktop window..." "WAIT"
    "$win_bin" "http://127.0.0.1:3000" >"$DESKTOP_LOG" 2>&1 &
    DESKTOP_PID=$!
  elif win_bin="$(ensure_electron 2>/dev/null)" && [[ -x "$win_bin" ]]; then
    step "Launching Electron desktop app..." "WAIT"
    "$win_bin" "$ELECTRON_MAIN" >"$ELECTRON_LOG" 2>&1 &
    DESKTOP_PID=$!
  fi

  sleep 1
  if kill -0 "$DESKTOP_PID" 2>/dev/null || desktop_window_running; then
    step "Desktop app launched" "OK"
    notify "Systems online"
    return 0
  fi
  step "Desktop app exited. See miscellaneous/logs/desktop.log" "ERR"
  tail -n 20 "$DESKTOP_LOG" >&2 2>/dev/null || true
  return 1
}

wait_for_desktop() {
  local pid="${DESKTOP_PID:-}"
  step "Desktop session running — close the window to stop J.A.R.V.I.S." "INFO"
  # Prefer wait(1) when the desktop process is our child. If it is not
  # (or wait returns early), poll until the window is actually gone.
  if [[ -n "$pid" ]]; then
    wait "$pid" 2>/dev/null || true
  fi
  while desktop_window_running; do
    sleep 1
  done
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    while kill -0 "$pid" 2>/dev/null; do
      sleep 1
    done
  fi
}

launcher_owner_pid() {
  [[ -f "$LAUNCHER_PID_FILE" ]] || return 1
  local owner command
  owner="$(cat "$LAUNCHER_PID_FILE" 2>/dev/null || true)"
  [[ "$owner" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$owner" 2>/dev/null || return 1
  command="$(ps -p "$owner" -o command= 2>/dev/null || true)"
  [[ "$command" == *"$ROOT/scripts/jarvis-launcher.sh"* ]] || return 1
  printf '%s\n' "$owner"
}

launcher_lock_is_held() {
  /usr/bin/lockf -s -k -t 0 "$LAUNCHER_LOCK" /usr/bin/true
  [[ "$?" -eq 75 ]]
}

remove_launcher_pid() {
  if [[ -f "$LAUNCHER_PID_FILE" ]] && [[ "$(cat "$LAUNCHER_PID_FILE" 2>/dev/null || true)" == "$$" ]]; then
    rm -f "$LAUNCHER_PID_FILE"
  fi
}

stop_launcher_session() {
  local owner attempt
  owner="$(launcher_owner_pid || true)"

  # The launcher writes its PID as soon as it acquires the lock. Cover the
  # short interval between lock acquisition and that write as well.
  if [[ "$LOCKED_RUN" != "1" ]] && [[ -z "$owner" ]] && launcher_lock_is_held; then
    for attempt in {1..20}; do
      sleep 0.1
      owner="$(launcher_owner_pid || true)"
      [[ -n "$owner" ]] && break
      launcher_lock_is_held || break
    done
  fi

  if [[ -n "$owner" ]] && [[ "$owner" != "$$" ]]; then
    kill -TERM "$owner" 2>/dev/null || true
    for attempt in {1..100}; do
      kill -0 "$owner" 2>/dev/null || return 0
      sleep 0.1
    done
    step "Launcher did not exit promptly; stopping its services directly" "WAIT"
  fi

  stop_jarvis
}

CLEANED=0

stop_jarvis() {
  step "Stopping J.A.R.V.I.S. services..." "WAIT"
  curl -sf --max-time 4 -X POST "http://127.0.0.1:8000/shutdown" >/dev/null 2>&1 && step "Backend shutdown requested" "OK" || true

  if [[ -f "$PID_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$PID_FILE" 2>/dev/null || true
    [[ -n "${desktop:-}" ]] && kill "$desktop" >/dev/null 2>&1 || true
    [[ -n "${electron:-}" ]] && kill "$electron" >/dev/null 2>&1 || true
    [[ -n "${backend:-}" ]] && kill "$backend" >/dev/null 2>&1 || true
    [[ -n "${frontend:-}" ]] && kill "$frontend" >/dev/null 2>&1 || true
    sleep 0.4
    [[ -n "${desktop:-}" ]] && kill -9 "$desktop" >/dev/null 2>&1 || true
    [[ -n "${electron:-}" ]] && kill -9 "$electron" >/dev/null 2>&1 || true
    [[ -n "${backend:-}" ]] && kill -9 "$backend" >/dev/null 2>&1 || true
    [[ -n "${frontend:-}" ]] && kill -9 "$frontend" >/dev/null 2>&1 || true
    rm -f "$PID_FILE"
  fi

  pkill -f "$BACKEND_DIR/main.py" >/dev/null 2>&1 || true
  pkill -f "next/dist/bin/next" >/dev/null 2>&1 || true
  pkill -f "$ELECTRON_MAIN" >/dev/null 2>&1 || true
  pkill -f "$ROOT/node_modules/electron" >/dev/null 2>&1 || true
  pkill -f "$FRONTEND_DIR/node_modules/electron" >/dev/null 2>&1 || true
  pkill -f "$SWIFT_BIN" >/dev/null 2>&1 || true
  pkill -f "jarvis-desktop-window" >/dev/null 2>&1 || true
  pkill -f "$PUPPETEER_SERVER" >/dev/null 2>&1 || true

  free_port 8000
  free_port 3000
  free_port 3920

  echo ""
  step "J.A.R.V.I.S. stopped. All frontend and backend processes terminated." "OK"
  echo ""
}

cleanup_on_close() {
  [[ "$CLEANED" == "1" ]] && return
  CLEANED=1
  trap - EXIT INT TERM HUP
  echo ""
  banner "Shutdown"
  stop_jarvis
  remove_launcher_pid
}

resolve_python() {
  if [[ -x "$PYTHON" ]]; then
    return 0
  fi
  local py=""
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      py="$(command -v "$candidate")"
      break
    fi
  done
  if [[ -z "$py" ]]; then
    step "Python 3 is not installed. Install with: brew install python@3.12" "ERR"
    return 1
  fi
  step "Creating Python venv with $py ..." "WAIT"
  "$py" -m venv "$BACKEND_DIR/.venv"
  "$PYTHON" -m pip install --upgrade pip wheel setuptools >/dev/null
  if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
    step "Installing Python dependencies (first run, this can take a while)..." "WAIT"
    "$PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
  fi
}

start_backend() {
  step "Starting AI backend (FastAPI :8000)..." "WAIT"
  if port_open 8000 || backend_healthy; then
    step "Port 8000 busy — stopping old backend first" "WAIT"
    curl -sf --max-time 3 -X POST "http://127.0.0.1:8000/shutdown" >/dev/null 2>&1 || true
    pkill -f "$BACKEND_DIR/main.py" >/dev/null 2>&1 || true
    free_port 8000
    sleep 1
  fi

  rm -f "$BACKEND_LOG" "$BACKEND_ERR"
  if [[ ! -x "$PYTHON" ]]; then
    die "Python venv missing: $PYTHON"
  fi

  (
    cd "$BACKEND_DIR"
    export PYTHONUNBUFFERED=1
    exec "$PYTHON" "$MAIN_PY"
  ) >"$BACKEND_LOG" 2>"$BACKEND_ERR" &
  local spawn_pid=$!
  BACKEND_PID="$spawn_pid"

  local i=0
  while (( i < 120 )); do
    if backend_healthy; then
      local listen
      listen="$(listen_pid 8000)"
      BACKEND_PID="${listen:-$spawn_pid}"
      step "Backend online  (PID $BACKEND_PID)" "OK"
      return 0
    fi
    if ! kill -0 "$spawn_pid" 2>/dev/null && ! backend_healthy; then
      tail -n 25 "$BACKEND_ERR" >&2 2>/dev/null || true
      die "Backend process exited early. See miscellaneous/logs/backend.err.log"
    fi
    sleep 1
    i=$((i + 1))
  done
  tail -n 20 "$BACKEND_ERR" >&2 2>/dev/null || true
  die "Backend failed to start. See miscellaneous/logs/backend.err.log"
}

start_frontend() {
  if port_open 3000; then
    if wait_http "http://127.0.0.1:3000" 8; then
      FRONTEND_PID="$(listen_pid 3000)"
      step "Frontend already online (port 3000 PID ${FRONTEND_PID:-unknown})" "OK"
      return 0
    fi
    step "Port 3000 occupied but UI not responding — restarting" "WAIT"
    free_port 3000
    sleep 1
  fi

  step "Starting UI (Next.js :3000)..." "WAIT"
  rm -f "$FRONTEND_LOG" "$FRONTEND_ERR"
  (
    cd "$FRONTEND_DIR"
    export BROWSER=none
    export BROWSER_PATH=/usr/bin/true
    exec npm run dev
  ) >"$FRONTEND_LOG" 2>"$FRONTEND_ERR" &
  FRONTEND_PID=$!
  if ! wait_http "http://127.0.0.1:3000" 120; then
    tail -n 15 "$FRONTEND_ERR" >&2 2>/dev/null || true
    die "Frontend failed to start. See miscellaneous/logs/frontend.err.log"
  fi
  step "Frontend online (PID $FRONTEND_PID)" "OK"
}

start_jarvis() {
  resolve_python || exit 1
  if [[ ! -f "$MAIN_PY" ]]; then
    die "Backend entry missing: $MAIN_PY"
  fi
  if ! command -v npm >/dev/null 2>&1; then
    die "npm not found in PATH"
  fi

  if [[ ! -d "$FRONTEND_DIR/node_modules" && ! -d "$ROOT/node_modules" ]]; then
    step "Installing frontend dependencies..." "WAIT"
    (cd "$FRONTEND_DIR" && npm install)
  fi

  step "Project root: $ROOT" "INFO"
  step "CPU $(uname -m)" "INFO"
  step "Starting all services..." "WAIT"

  # Model server first: the backend answers with a canned line if Ollama is down.
  ensure_ollama || true

  start_backend

  echo ""
  notify "Starting J.A.R.V.I.S...."
  if ! start_desktop_window; then
    die "Desktop window failed to start"
  fi
  focus_desktop_window

  save_pids "$BACKEND_PID" "$DESKTOP_PID"

  echo ""
  echo "${cyan}  --------------------------------------------------------${reset}"
  echo "${green}   SYSTEMS ONLINE${reset}"
  echo "${white}   Desktop window is the UI — the browser is not used.${reset}"
  echo "${white}   API:     http://127.0.0.1:8000/health${reset}"
  echo "${gray}   Logs:    $LOG_DIR${reset}"
  echo ""
  echo "${yellow}   Close the Jarvis window (or this terminal) to STOP everything.${reset}"
  echo "${cyan}  --------------------------------------------------------${reset}"
  echo ""

  wait_for_desktop
}

if [[ "$MODE" == "stop" ]]; then
  banner "Shutdown"
  stop_launcher_session
  remove_launcher_pid
  exit 0
fi

if [[ "$MODE" == "start" ]] && port_open 8000 && desktop_window_running; then
  banner "Already running"
  step "J.A.R.V.I.S. is already online — bringing the desktop window forward" "OK"
  focus_desktop_window
  exit 0
fi

if [[ "$MODE" == "toggle" ]] && port_open 8000; then
  owner="$(launcher_owner_pid || true)"
  if [[ -n "$owner" ]] && ! desktop_window_running; then
    step "J.A.R.V.I.S. startup is already in progress; ignoring this duplicate launch" "INFO"
  else
    banner "Shutdown"
    stop_launcher_session
    remove_launcher_pid
  fi
  exit 0
fi

if [[ "$LOCKED_RUN" != "1" ]]; then
  /usr/bin/lockf -s -k -t 0 "$LAUNCHER_LOCK" "$ROOT/scripts/jarvis-launcher.sh" --__locked "$@"
  lock_status=$?
  if [[ "$lock_status" -eq 75 ]]; then
    if [[ "$MODE" == "start" ]] && backend_healthy && desktop_window_running; then
      banner "Already running"
      step "J.A.R.V.I.S. is already online — bringing the desktop window forward" "OK"
      focus_desktop_window
    else
      step "J.A.R.V.I.S. startup is already in progress; ignoring this duplicate launch" "INFO"
    fi
    exit 0
  fi
  exit "$lock_status"
fi

trap cleanup_on_close EXIT
trap 'exit 0' INT TERM HUP
if [[ "$LOCKED_RUN" == "1" ]]; then
  printf '%s\n' "$$" > "$LAUNCHER_PID_FILE"
fi
banner "Launcher"
start_jarvis
