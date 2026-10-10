# J.A.R.V.I.S.

**Just A Rather Very Intelligent System** — local AI desktop assistant with voice, OS automation, and WhatsApp drafting.

Rebuilt for **macOS** (Apple Silicon / Intel). WhatsApp uses WhatsApp.app; browser automation uses Puppeteer + Google Chrome.

## Launch for demo

1. Double-click **`JARVIS.app`** on the Desktop (circular J.A.R.V.I.S. logo)
2. Wait for the **desktop window** (Electron, with a native Swift fallback). The UI does not open in Safari or Chrome.
3. Use that window — voice + chat live there
4. **Close the Jarvis window** — that stops frontend, backend, and browser automation

```bash
# From a terminal in this folder:
npm run jarvis          # toggle start/stop
npm run jarvis:start    # force start
npm run jarvis:stop     # force stop
```

JARVIS uses local Ollama inference and local English speech recognition. Install
the backend dependencies first, then prepare the model weights:

```bash
# One-time backend setup
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# Terminal 1
ollama serve

# Terminal 2
./scripts/setup_models.sh
```

The normal chat path uses `qwen3:4b` with thinking disabled. The optional
`./scripts/setup_models.sh --planning` also downloads `qwen3:8b` for explicit
planning requests. Voice input uses the English-only faster-whisper `base.en`
model on the machine; `setup_models.sh` caches its weights locally.

macOS will ask for **Accessibility** (WhatsApp keystrokes / window bounds) and **Microphone** (voice). Allow Python or Terminal under System Settings → Privacy & Security → Accessibility.

## What it runs

| Component | Port | Role |
|-----------|------|------|
| FastAPI backend | 8000 | Brain, tools, STT/TTS, automation |
| Next.js dev UI | 3000 | Local frontend served to the desktop window during development |
| Desktop window | — | Electron shell (native Swift fallback): orb and conversation thread |

## Demo commands

- `Hello Jarvis, introduce yourself`
- `What time is it?`
- `Open TextEdit` / `Close TextEdit`
- `Open Calculator`
- `Tell me a joke`
- `Set volume to 40`
- `send sathish hello` → native WhatsApp.app (saved contact required)
- `play Back in Black on YouTube Music` → Puppeteer + Google Chrome

See **docs/PRESENTATION_DEMO.md** for a full judge script.

## Project layout

```
JarvisAI/
├── frontend/             Next.js UI + Electron desktop shell
│   ├── app/              App Router pages (page.tsx, layout.tsx, globals.css)
│   ├── components/       UI components (components/ui/ = shadcn primitives)
│   ├── hooks/            React hooks (useJarvis, useBackendStatus, ...)
│   ├── lib/              shared TS helpers (clsx + tailwind-merge)
│   ├── public/           static assets
│   ├── electron-main.js  desktop window entry point (package.json "main")
│   └── package.json      JS deps + scripts (dev/build/start/desktop)
├── backend/              Python FastAPI core — brain, tools, STT/TTS
│   ├── main.py           FastAPI entry point (run with backend/.venv/bin/python)
│   ├── config.py         shared Python settings (loads backend/.env)
│   ├── api/ brain/ executor/ services/ stt/ tts/ vision/
│   ├── llm/ memory/ mcp_clients/ mcp_servers/ retry/ router/
│   ├── browser-automation/ Puppeteer control plane (Node service)
│   ├── desktop_app/      PyQt native desktop client (jarvis_app.py)
│   └── tests/            standalone tests: backend/.venv/bin/python tests/test_*.py
├── scripts/              launcher, dev runner, model setup, app bundler
├── docs/                 design notes, build plan, demo script
├── miscellaneous/        non-source artifacts kept out of the way
│   ├── desktop-launcher/ JARVIS.app bundle, JARVIS.command, Swift window source
│   ├── logs/             runtime logs (backend, frontend, launcher, ollama)
│   ├── build/            generated icon sets (gitignored)
│   ├── JARVIS.jpg        logo master used to build the app icon
│   └── jarvis_icon.icns  generated macOS icon
├── jarvis_memory.json    live short-term memory state (gitignored, read by brain/memory.py)
├── package.json          thin root proxy — delegates to frontend/ via --prefix
└── package-desktop.js    Electron packager script (run from the repo root)
```

- UI: `npm run desktop --prefix frontend` → Electron window + local Next.js dev server
- Lint: `npm run lint --prefix frontend`
- Fresh install: `cd frontend && npm install` · `cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Package desktop UI: `node package-desktop.js` (start FastAPI on port 8000 first)

## Dev (manual)

```bash
# Terminal 1
cd backend
source .venv/bin/activate
python main.py

# Terminal 2 (starts the local Next.js dev server and the desktop window)
npm run desktop --prefix frontend
```
