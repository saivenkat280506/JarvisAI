# J.A.R.V.I.S. — Judge Presentation Guide

## One-click launch (macOS)

**Double-click:** `JARVIS.app` on the Desktop (or `JARVIS.command`)  
or from Terminal: `npm run jarvis`

The UI opens in a **desktop window** (Electron), not in a browser.

**When finished:** close the Jarvis window, or run the same launcher again — it toggles stop.

| Service | URL |
|---------|-----|
| Desktop UI | Electron window (loads http://127.0.0.1:3000 internally) |
| API health | http://127.0.0.1:8000/health |

---

## 60–90 second demo script

1. **Intro (voice or type)**  
   `Hello Jarvis, introduce yourself in one short sentence.`

2. **Intelligence**  
   `What time is it right now?`  
   `Tell me a short witty joke.`

3. **OS control**  
   `Open TextEdit.`  
   `Close TextEdit.`  
   `Open Calculator.`

4. **System**  
   `Set volume to 40.`

5. **WhatsApp (optional, careful)**  
   `send message to +91 85199 29108 Hello from Jarvis demo`  
   → Jarvis **searches by number**, types the message, **asks you**  
   → You say `yes` to send, or `no` to cancel.

6. **Music (optional)**  
   `Play music` (local garage track)

---

## Features to highlight

- Local AI assistant (Groq LLM + hybrid TTS voice)
- Voice + text chat UI
- App open/close automation (macOS `open -a` / AppleScript)
- Volume control (macOS output volume)
- WhatsApp Desktop automation with **number search** + **confirm-before-send**
- Browser automation (YouTube / search / demos)
- Real-time status orb and agent step tracking

---

## If something fails mid-demo

1. Run `JARVIS.command` once to stop, then again to start
2. Or: `npm run jarvis:stop` then `npm run jarvis:start`
3. Check `logs/backend.err.log` and `logs/frontend.err.log`

Keep WhatsApp Desktop installed and logged in for the messaging demo.

Grant **Accessibility** and **Microphone** permissions when macOS prompts.
