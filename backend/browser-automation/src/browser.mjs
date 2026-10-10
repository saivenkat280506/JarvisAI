/**
 * JARVIS browser launcher (macOS).
 *
 * Default: dedicated clone under chrome-profile-data/ so daily Chrome
 * can stay open (no SingletonLock fight on ~/Library/Application Support/Google/Chrome).
 *
 * Env:
 *   CHROME_USE_REAL_PROFILE=0     (default) use chrome-profile-data or user-data
 *   CHROME_USE_REAL_PROFILE=1     opt into live Chrome User Data; a busy profile
 *                                 falls back to the isolated JARVIS profile
 *   CHROME_PROFILE_DIRECTORY      default Default
 *   CHROME_USER_DATA              override user-data path (real or clone)
 *   CHROME_DEBUG_PORT=9222        try attach first if Chrome already debugging
 *   CHROME_FALLBACK_ON_LOCK=1     (default) if real profile stays locked, use clone
 *   CHROME_PATH                   Google Chrome binary
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const CLONE_USER_DATA = path.join(ROOT, "chrome-profile-data");
const FALLBACK_USER_DATA = path.join(ROOT, "user-data");

let browser = null;
let page = null;
let requestedProfile = null;
let launchMeta = { mode: "unknown", userDataDir: null, profile: null };

function chromeExe() {
  const candidates = [
    process.env.CHROME_PATH,
    process.env.PUPPETEER_EXECUTABLE_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    path.join(os.homedir(), "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
  ];
  for (const p of candidates.filter(Boolean)) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function realChromeUserData() {
  // Only the true daily profile — do not honor CHROME_USER_DATA here when it
  // points at our clone (that path is handled separately).
  if (process.env.CHROME_REAL_USER_DATA) return process.env.CHROME_REAL_USER_DATA;
  return path.join(os.homedir(), "Library", "Application Support", "Google", "Chrome");
}

function isolatedUserData() {
  const override = process.env.CHROME_USER_DATA;
  if (override) {
    const norm = path.resolve(override);
    const real = path.resolve(realChromeUserData());
    // If someone points CHROME_USER_DATA at the live profile while not in
    // real-profile mode, still prefer the clone so we don't lock daily Chrome.
    if (norm.toLowerCase() !== real.toLowerCase()) {
      return norm;
    }
  }
  if (fs.existsSync(path.join(CLONE_USER_DATA, "Default"))) {
    return CLONE_USER_DATA;
  }
  return FALLBACK_USER_DATA;
}

function wantsRealProfile() {
  const v = (process.env.CHROME_USE_REAL_PROFILE || "0").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no" && v !== "clone";
}

async function tryConnectDebugPort() {
  const port = process.env.CHROME_DEBUG_PORT || "9222";
  const url = `http://127.0.0.1:${port}`;
  try {
    const b = await puppeteer.connect({
      browserURL: url,
      defaultViewport: null,
    });
    console.log(`[browser] Connected to existing Chrome on ${url}`);
    launchMeta = {
      mode: "chrome-debug-attach",
      userDataDir: realChromeUserData(),
      profile: process.env.CHROME_PROFILE_DIRECTORY || "Default",
    };
    return b;
  } catch {
    return null;
  }
}

export function getUserDataDir() {
  return launchMeta.userDataDir || isolatedUserData();
}

export async function configureProfile({ userDataDir, profileDir, useRealProfile = true } = {}) {
  const next = {
    userDataDir: userDataDir ? path.resolve(userDataDir) : realChromeUserData(),
    profileDir: profileDir || "Default",
    useRealProfile: Boolean(useRealProfile),
  };
  if (JSON.stringify(next) === JSON.stringify(requestedProfile)) return { ok: true };
  await closeBrowser();
  requestedProfile = next;
  return { ok: true, profile: next.profileDir };
}

async function launchWithUserData(userDataDir, { headless, useReal, profileDir }) {
  const exe = chromeExe();
  const winW = Number(process.env.CHROME_WINDOW_WIDTH || 1280);
  const winH = Number(process.env.CHROME_WINDOW_HEIGHT || 800);
  const args = [
    `--window-size=${winW},${winH}`,
    "--window-position=80,60",
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--disable-infobars",
    "--autoplay-policy=no-user-gesture-required",
    "--no-first-run",
    "--password-store=basic",
    "--disable-features=MediaRouter,TranslateUI",
    "--use-fake-ui-for-media-stream",
    // Keep media playing when window is not focused
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
  ];
  if (useReal || fs.existsSync(path.join(userDataDir, profileDir))) {
    args.push(`--profile-directory=${profileDir}`);
  }

  launchMeta = {
    mode: useReal ? "chrome-real-profile" : "chrome-cloned-or-isolated",
    userDataDir,
    profile: profileDir,
  };

  console.log(
    `[browser] Launch mode=${launchMeta.mode} profile=${profileDir} data=${userDataDir} window=${winW}x${winH}`
  );

  const launchOpts = {
    headless: headless || process.env.PUPPETEER_HEADLESS === "1",
    defaultViewport: { width: winW, height: winH, deviceScaleFactor: 1 },
    userDataDir,
    args,
    ignoreDefaultArgs: ["--enable-automation"],
  };
  if (exe) {
    launchOpts.executablePath = exe;
  } else {
    throw new Error(
      "Google Chrome not found. Install it in /Applications or set CHROME_PATH."
    );
  }

  return puppeteer.launch(launchOpts);
}

function isLockError(err) {
  const msg = String(err?.message || err);
  return /already running|in use|SingletonLock|userDataDir|profile is/i.test(msg);
}

export async function ensureBrowser({ headless = false } = {}) {
  if (browser && browser.connected) {
    if (!page || page.isClosed()) {
      const pages = await browser.pages();
      page = pages[0] || (await browser.newPage());
    }
    return { browser, page };
  }

  // 1) Prefer attach if user already started Chrome with --remote-debugging-port
  browser = requestedProfile || process.env.CHROME_PROFILE_EMAIL
    ? null
    : await tryConnectDebugPort();

  if (!browser) {
    const useReal = requestedProfile?.useRealProfile ?? wantsRealProfile();
    const profileDir = requestedProfile?.profileDir || process.env.CHROME_PROFILE_DIRECTORY || "Default";
    const fallbackOnLock =
      (process.env.CHROME_FALLBACK_ON_LOCK || "1").trim() !== "0";

    let userDataDir = useReal
      ? (requestedProfile?.userDataDir || realChromeUserData())
      : (requestedProfile?.userDataDir || isolatedUserData());
    let tryingReal = useReal;

    // Ensure target dir exists for clone mode
    if (!tryingReal) {
      try {
        fs.mkdirSync(path.join(userDataDir, profileDir), { recursive: true });
      } catch {
        /* ignore */
      }
    }

    const attempt = async (dir, real) => {
      return launchWithUserData(dir, {
        headless,
        useReal: real,
        profileDir,
      });
    };

    try {
      browser = await attempt(userDataDir, tryingReal);
      console.log("[browser] Launched OK");
    } catch (err) {
      console.error("[browser] Launch error:", String(err?.message || err));
      if (!isLockError(err)) throw err;

      if (tryingReal && fallbackOnLock) {
        const cloneDir = isolatedUserData();
        console.warn(
          `[browser] Real Chrome profile is in use. Falling back to isolated profile: ${cloneDir}`
        );
        try {
          fs.mkdirSync(path.join(cloneDir, profileDir), { recursive: true });
        } catch {
          /* ignore */
        }
        browser = await attempt(cloneDir, false);
      } else {
        throw new Error(
          "The selected JARVIS Chrome profile is already in use. Close its browser window and retry.\n" +
          String(err?.message || err)
        );
      }
    }
  }

  const pages = await browser.pages();
  page = (await pickBestPage(pages)) || (await browser.newPage());

  // Stealth-ish: hide webdriver before any navigation
  try {
    const client = await page.createCDPSession();
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = window.chrome || { runtime: {} };
      `,
    });
  } catch {
    /* ignore */
  }

  // Ensure window is windowed (not maximized / fullscreen)
  try {
    const winW = Number(process.env.CHROME_WINDOW_WIDTH || 1280);
    const winH = Number(process.env.CHROME_WINDOW_HEIGHT || 800);
    const session = await page.createCDPSession();
    const { windowId } = await session.send("Browser.getWindowForTarget");
    await session.send("Browser.setWindowBounds", {
      windowId,
      bounds: {
        windowState: "normal",
        width: winW,
        height: winH,
        left: 80,
        top: 60,
      },
    });
    await page.setViewport({ width: winW, height: winH, deviceScaleFactor: 1 });
  } catch (e) {
    console.warn("[browser] Could not force normal window bounds:", e?.message || e);
  }
  try {
    await page.bringToFront();
  } catch {
    /* ignore */
  }

  try {
    await page.evaluateOnNewDocument(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });
  } catch {
    /* ignore */
  }

  browser.on("disconnected", () => {
    browser = null;
    page = null;
  });

  return { browser, page };
}

/** Prefer a real content tab over leftover about:blank tabs. */
async function pickBestPage(pages) {
  const open = (pages || []).filter((p) => p && !p.isClosed());
  if (!open.length) return null;

  // Prefer YouTube / Music / active content
  const scored = [];
  for (const p of open) {
    let url = "";
    try {
      url = p.url() || "";
    } catch {
      url = "";
    }
    let score = 0;
    if (/music\.youtube\.com/i.test(url)) score += 100;
    if (/youtube\.com/i.test(url)) score += 80;
    if (/spotify\.com|google\./i.test(url)) score += 40;
    if (url && !/^about:blank$/i.test(url) && !/^chrome:\/\//i.test(url)) score += 20;
    if (page && p === page) score += 5;
    scored.push({ p, score, url });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.length ? scored[0].p : null;
}

export function setActivePage(p) {
  if (p && !p.isClosed()) page = p;
}

/** The page the last navigate()/click landed on — without getPage()'s tab hop. */
export function activePage() {
  try {
    return page && !page.isClosed() ? page : null;
  } catch {
    return null;
  }
}

export async function getPage() {
  const ctx = await ensureBrowser({ headless: process.env.PUPPETEER_HEADLESS === "1" });
  try {
    const pages = await ctx.browser.pages();
    const best = await pickBestPage(pages);
    if (best) {
      // If stored page is blank/closed but another tab has content, switch
      let curUrl = "";
      try {
        curUrl = page && !page.isClosed() ? page.url() : "";
      } catch {
        curUrl = "";
      }
      if (
        !page ||
        page.isClosed() ||
        /^about:blank$/i.test(curUrl) ||
        best !== page
      ) {
        const bestUrl = best.url() || "";
        if (
          !page ||
          page.isClosed() ||
          /^about:blank$/i.test(curUrl) ||
          (/music\.youtube|youtube\.com/i.test(bestUrl) && !/music\.youtube|youtube\.com/i.test(curUrl))
        ) {
          page = best;
        }
      }
    }
  } catch {
    /* keep ctx.page */
  }
  if (!page || page.isClosed()) {
    page = await ctx.browser.newPage();
  }
  return page;
}

export async function newTab(url) {
  const { browser: b } = await ensureBrowser({
    headless: process.env.PUPPETEER_HEADLESS === "1",
  });
  const p = await b.newPage();
  if (url) await p.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  page = p;
  return p;
}

export async function replaceWithNewTab(url, oldPage = null) {
  const { browser: b } = await ensureBrowser({
    headless: process.env.PUPPETEER_HEADLESS === "1",
  });
  const prev = oldPage && !oldPage.isClosed() ? oldPage : page;

  if (prev && !prev.isClosed()) {
    try {
      await prev.evaluate(() => {
        document.querySelectorAll("video, audio").forEach((m) => {
          try {
            m.muted = true;
            m.volume = 0;
            m.pause();
          } catch {
            /* ignore */
          }
        });
      });
    } catch {
      /* ignore */
    }
  }

  const p = await b.newPage();
  await p.bringToFront();
  if (url) await p.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  page = p;

  if (prev && !prev.isClosed() && prev !== p) {
    try {
      await prev.close({ runBeforeUnload: false });
    } catch {
      /* ignore */
    }
  }
  return p;
}

export async function closeBrowser() {
  if (browser) {
    try {
      if (launchMeta.mode === "chrome-debug-attach") {
        browser.disconnect();
      } else {
        await browser.close();
      }
    } catch {
      /* ignore */
    }
  }
  browser = null;
  page = null;
}

export async function setWindowBounds(bounds = {}) {
  const ctx = await ensureBrowser({ headless: process.env.PUPPETEER_HEADLESS === "1" });
  const target = page && !page.isClosed() ? page : ctx.page;
  if (!target) return { ok: false, error: "No browser page" };

  const left = Math.round(Number(bounds.left ?? bounds.x ?? 40));
  const top = Math.round(Number(bounds.top ?? bounds.y ?? 40));
  const width = Math.max(480, Math.round(Number(bounds.width || 900)));
  const height = Math.max(420, Math.round(Number(bounds.height || 800)));

  try {
    await target.bringToFront();
  } catch {
    /* ignore */
  }

  try {
    const session = await target.createCDPSession();
    const { windowId } = await session.send("Browser.getWindowForTarget");
    await session.send("Browser.setWindowBounds", {
      windowId,
      bounds: {
        windowState: "normal",
        left,
        top,
        width,
        height,
      },
    });
    await target.setViewport({ width, height, deviceScaleFactor: 1 }).catch(() => {});
    return { ok: true, left, top, width, height };
  } catch (err) {
    return { ok: false, error: String(err?.message || err) };
  }
}

export function status() {
  return {
    connected: !!(browser && browser.connected),
    mode: launchMeta.mode,
    userDataDir: launchMeta.userDataDir,
    profile: launchMeta.profile,
    url: page && !page.isClosed() ? page.url() : null,
  };
}
