# JARVIS Browser Automation (Puppeteer, macOS)

Advanced browser control for JARVIS: click, type, scroll, YouTube, Spotify, and web search.

JARVIS keeps an isolated local copy of the selected Chrome profile so browser automation can use its signed-in session without taking over daily Chrome. Choose `user@example.com` or `svtech06@gmail.com` in JARVIS Settings.

## Architecture

```
Python (backend/executor/puppeteer_client.py)
        │  HTTP JSON  POST /command
        ▼
Node control plane  (src/server.mjs  :3920)
        │
        ▼
Puppeteer + chrome-profile-data/  (dedicated JARVIS profile)
```

The application reaches the browser control plane through the audited browser
MCP server. It accepts loopback requests only, rejects browser-originated POSTs,
and exposes an allowlist without generic JavaScript execution or WhatsApp sends.

Daily Chrome stays open. Profile copies live in the ignored `backend/browser-automation/chrome-profile-data/` directory and are created when first selected. The sync script refuses to copy a profile while Chrome has files open in it.

## Start manually (optional)

```bash
cd backend/browser-automation
PUPPETEER_SKIP_DOWNLOAD=true npm install
npm start
```

JARVIS auto-starts the server on first browser tool use.

Refresh the currently selected source profile (Chrome should be closed):

```bash
npm run sync-profile
```

## Commands

`POST http://127.0.0.1:3920/command`

```json
{ "action": "youtube_play", "query": "AC/DC Back in Black" }
{ "action": "youtube_music_play", "query": "AC/DC Back in Black" }
{ "action": "spotify_login", "email": "...", "password": "..." }
{ "action": "spotify_search", "query": "AC/DC", "play": true }
{ "action": "scroll_test", "url": "https://en.wikipedia.org/wiki/AC/DC", "times": 8 }
{ "action": "navigate", "url": "https://example.com" }
```

## Env

| Variable | Purpose |
|----------|---------|
| `CHROME_PATH` | Google Chrome binary (auto-detected on macOS) |
| `SPOTIFY_EMAIL` / `SPOTIFY_PASSWORD` | Optional automated Spotify login |
| `PUPPETEER_PORT` | Default `3920` |
| `PUPPETEER_HEADLESS` | Set `1` for headless |
| `CHROME_PROFILE_DIRECTORY` | Chrome profile folder to sync or use (selected automatically by email in JARVIS Settings) |
| `CHROME_USE_REAL_PROFILE` | Internal launch mode; JARVIS uses an isolated account-specific copy |

## JARVIS voice / chat intents

- “Play Back in Black on YouTube”
- “Play Back in Black on YouTube Music”
- “Log in to Spotify”
- “Play Back in Black on Spotify”
- “Scroll speed test”
- “Run the demo” — scroll Wikipedia, then play on YouTube Music

### CLI one-shot

```bash
cd backend/browser-automation
chmod +x run_linkedin_demo.sh
./run_linkedin_demo.sh
```
