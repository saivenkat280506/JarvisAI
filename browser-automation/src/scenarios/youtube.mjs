/**
 * YouTube / YouTube Music playback scenarios via Puppeteer.
 */
import { getPage } from "../browser.mjs";
import * as actions from "../actions.mjs";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Play a track on YouTube (classic) — most reliable without login.
 */
export async function playYoutube(query, { service = "youtube" } = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing song query");

  const page = await getPage();
  const useMusic = String(service).toLowerCase().includes("music");

  if (useMusic) {
    // YouTube Music search
    const url = `https://music.youtube.com/search?q=${encodeURIComponent(q)}`;
    await actions.navigate(url, { waitUntil: "networkidle2", timeout: 90000 });
    await sleep(2500);

    // Dismiss consent if present
    try {
      await page.evaluate(() => {
        const btns = [...document.querySelectorAll("button, tp-yt-paper-button")];
        const accept = btns.find((b) => /accept|agree|i agree|reject all/i.test(b.innerText || ""));
        if (accept) accept.click();
      });
      await sleep(800);
    } catch {
      /* ignore */
    }

    // Click first song / playable result
    const clicked = await page.evaluate(() => {
      const candidates = [
        ...document.querySelectorAll(
          "ytmusic-responsive-list-item-renderer, ytmusic-card-shelf-renderer a, a.yt-simple-endpoint"
        ),
      ];
      for (const el of candidates) {
        const href = el.getAttribute?.("href") || el.href || "";
        const text = (el.innerText || "").toLowerCase();
        if (href.includes("watch") || href.includes("playlist") || text.includes("play")) {
          el.click();
          return { href, text: (el.innerText || "").slice(0, 120) };
        }
      }
      // fallback: first list item
      const first = document.querySelector("ytmusic-responsive-list-item-renderer");
      if (first) {
        first.click();
        return { href: "list-item", text: (first.innerText || "").slice(0, 120) };
      }
      return null;
    });

    if (!clicked) {
      // Try pressing play on first result via keyboard search path
      await page.keyboard.press("Tab");
      await sleep(200);
    }

    await sleep(3000);
    // Ensure media playing
    await page.evaluate(() => {
      const v = document.querySelector("video");
      if (v && v.paused) v.play().catch(() => {});
    });

    const info = await actions.contentInfo();
    return {
      ok: true,
      service: "youtube_music",
      query: q,
      clicked,
      ...info,
      message: `Playing "${q}" on YouTube Music.`,
    };
  }

  // Classic YouTube
  const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`;
  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  await sleep(2000);

  // Cookie / consent banners
  try {
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll("button, tp-yt-paper-button")];
      const accept = btns.find((b) =>
        /accept all|accept|i agree|reject all|got it/i.test((b.innerText || "").trim())
      );
      if (accept) accept.click();
    });
    await sleep(1000);
  } catch {
    /* ignore */
  }

  // Prefer first video result (not ad/shorts if possible)
  const pick = await page.evaluate(() => {
    const renderers = [...document.querySelectorAll("ytd-video-renderer, ytd-rich-item-renderer")];
    for (const r of renderers) {
      const a = r.querySelector("a#video-title, a#video-title-link, a#thumbnail");
      if (!a) continue;
      const href = a.href || "";
      if (!href.includes("watch?v=")) continue;
      const titleEl = r.querySelector("#video-title, a#video-title-link");
      const title = (
        titleEl?.getAttribute("title") ||
        titleEl?.textContent ||
        a.title ||
        a.getAttribute("title") ||
        a.innerText ||
        ""
      )
        .replace(/\s+/g, " ")
        .trim();
      a.click();
      return { href, title: title.slice(0, 160) };
    }
    return null;
  });

  if (!pick) throw new Error("No YouTube video results found to play");

  await page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});
  await sleep(2500);

  // Force play once (do NOT toggle 'k' twice — second press pauses)
  await page.evaluate(async () => {
    const v = document.querySelector("video");
    const playBtn = document.querySelector(
      ".ytp-large-play-button, button.ytp-play-button[data-title-no-tooltip='Play']"
    );
    // Only click play button if video is paused / button says Play
    if (playBtn && /play/i.test(playBtn.getAttribute("data-title-no-tooltip") || playBtn.getAttribute("aria-label") || "")) {
      playBtn.click();
    }
    if (v) {
      try {
        v.muted = false;
        v.volume = 1;
        if (v.paused) await v.play();
      } catch {
        try {
          v.muted = true;
          if (v.paused) await v.play();
          v.muted = false;
        } catch {
          /* ignore */
        }
      }
    }
  });
  await sleep(1500);
  // If still paused, single keyboard play
  const stillPaused = await page.evaluate(() => {
    const v = document.querySelector("video");
    return !v || v.paused;
  });
  if (stillPaused) {
    try {
      await page.keyboard.press("k");
    } catch {
      /* ignore */
    }
    await sleep(800);
  }

  const playing = await page.evaluate(() => {
    const v = document.querySelector("video");
    return v
      ? { paused: v.paused, currentTime: v.currentTime, src: v.currentSrc, volume: v.volume, muted: v.muted }
      : null;
  });

  const info = await actions.contentInfo();
  return {
    ok: true,
    service: "youtube",
    query: q,
    picked: pick,
    playing,
    ...info,
    message: `Playing "${pick.title || q}" on YouTube.`,
  };
}
