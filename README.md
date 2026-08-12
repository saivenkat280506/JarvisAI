# J.A.R.V.I.S.

**Just A Rather Very Intelligent System** — local AI desktop assistant with voice, OS automation, and WhatsApp drafting.

## Launch for demo / judges

1. Double-click **`JARVIS.bat`** (or the Desktop shortcut **J.A.R.V.I.S.**)
2. Wait until the console says **SYSTEMS ONLINE**
3. Use the desktop window
4. Run the same launcher again to **stop** everything (toggle)

```bash
# Or from a terminal in this folder:
npm run jarvis          # toggle start/stop
npm run jarvis:start    # force start
npm run jarvis:stop     # force stop
```

## What it runs

| Component | Port | Role |
|-----------|------|------|
| FastAPI backend | 8000 | Brain, tools, STT/TTS, automation |
| Next.js UI | 3000 | Chat + orb interface |
| Electron (optional) | — | Desktop window shell |

## Demo commands

- `Hello Jarvis, introduce yourself`
- `What time is it?`
- `Open notepad` / `Close notepad`
- `Tell me a joke`
- `Set volume to 40`
- `send message to +91XXXXXXXXXX your text` → Jarvis asks; say `yes` to send

See **PRESENTATION_DEMO.md** for a full judge script.

## Project layout

- `backend/` — Python FastAPI core
- `app/` + `components/` — Next.js UI
- `electron-main.js` — desktop shell
- `scripts/Jarvis-Launcher.ps1` — one-click start/stop launcher
- `browser-automation/` — Puppeteer control plane

## Dev (manual)

```bash
# Terminal 1
cd backend
.\.venv\Scripts\python.exe main.py

# Terminal 2
npm run dev

# Optional desktop shell
npm run desktop
```
