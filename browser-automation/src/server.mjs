/**
 * JARVIS Puppeteer control plane — HTTP JSON API.
 * Default: http://127.0.0.1:3920
 */
import http from "node:http";
import { closeBrowser, status as browserStatus } from "./browser.mjs";
import { runAction } from "./actions.mjs";
import { playYoutube } from "./scenarios/youtube.mjs";
import { login as spotifyLogin, search as spotifySearch, isLoggedIn as spotifyIsLoggedIn } from "./scenarios/spotify.mjs";
import { scrollSpeedTest } from "./scenarios/scroll.mjs";

const HOST = process.env.PUPPETEER_HOST || "127.0.0.1";
const PORT = Number(process.env.PUPPETEER_PORT || 3920);

function send(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(data),
    "Access-Control-Allow-Origin": "*",
  });
  res.end(data);
}

async function readJson(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

async function handleCommand(body) {
  const action = body.action || body.cmd || body.op;
  const params = body.params || body;

  switch (action) {
    case "health":
      return { ok: true, service: "jarvis-puppeteer", ...browserStatus() };

    case "youtube_play":
    case "play_youtube":
      return playYoutube(params.query || params.song || params.q, {
        service: params.service || "youtube",
      });

    case "youtube_music_play":
      return playYoutube(params.query || params.song || params.q, {
        service: "youtube_music",
      });

    case "spotify_login":
      return spotifyLogin(params);

    case "spotify_search":
      return spotifySearch(params.query || params.q || params.song, {
        play: !!params.play,
      });

    case "spotify_status":
      return spotifyIsLoggedIn();

    case "scroll_test":
      return scrollSpeedTest(params);

    case "close":
      await closeBrowser();
      return { ok: true, message: "Browser closed." };

    default:
      // Low-level primitive
      return runAction(action, params);
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    });
    return res.end();
  }

  try {
    if (req.method === "GET" && (req.url === "/" || req.url === "/health")) {
      return send(res, 200, { ok: true, service: "jarvis-puppeteer", ...browserStatus() });
    }

    if (req.method === "POST" && (req.url === "/command" || req.url === "/")) {
      const body = await readJson(req);
      const result = await handleCommand(body);
      // needsCredentials is a soft failure (page opened); still HTTP 200 for clients
      const hardFail = result.ok === false && !result.needsCredentials && result.error;
      return send(res, hardFail ? 500 : result.ok === false && !result.needsCredentials ? 400 : 200, result);
    }

    send(res, 404, { ok: false, error: "Not found. POST /command { action, ...params }" });
  } catch (err) {
    console.error("[puppeteer]", err);
    send(res, 500, { ok: false, error: String(err?.message || err) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[jarvis-puppeteer] listening on http://${HOST}:${PORT}`);
  console.log(`[jarvis-puppeteer] POST /command  e.g. { "action": "youtube_play", "query": "AC/DC Back in Black" }`);
});

process.on("SIGINT", async () => {
  await closeBrowser();
  process.exit(0);
});
process.on("SIGTERM", async () => {
  await closeBrowser();
  process.exit(0);
});
