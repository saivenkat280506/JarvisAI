# JARVIS Browser Automation (Puppeteer)

Advanced browser control for JARVIS: click, type, scroll, login, YouTube/Spotify.

## Architecture

```
Python (backend/executor/puppeteer_client.py)
        │  HTTP JSON  POST /command
        ▼
Node control plane  (src/server.mjs  :3920)
        │
        ▼
Puppeteer + persistent user-data/  (cookies/logins survive)
```

## Start manually (optional)

```bash
cd browser-automation
npm install
npm start
```

JARVIS auto-starts the server on first browser tool use.

## Commands

`POST http://127.0.0.1:3920/command`

```json
{ "action": "youtube_play", "query": "AC/DC Back in Black" }
{ "action": "youtube_music_play", "query": "AC/DC Back in Black" }
{ "action": "spotify_login", "email": "...", "password": "..." }
{ "action": "spotify_search", "query": "AC/DC", "play": true }
{ "action": "scroll_test", "url": "https://en.wikipedia.org/wiki/AC/DC", "times": 8 }
{ "action": "navigate", "url": "https://example.com" }
{ "action": "click", "selector": "button" }
{ "action": "type", "selector": "input", "text": "hello" }
{ "action": "scroll", "pixels": 800, "times": 3 }
```

## Env

| Variable | Purpose |
|----------|---------|
| `SPOTIFY_EMAIL` / `SPOTIFY_PASSWORD` | Optional automated Spotify login |
| `PUPPETEER_PORT` | Default `3920` |
| `PUPPETEER_HEADLESS` | Set `1` for headless |

## JARVIS voice / chat intents

- “Play Back in Black on YouTube”
- “Play Back in Black on YouTube Music”
- “Log in to Spotify”
- “Play Back in Black on Spotify”
- “Scroll speed test”
