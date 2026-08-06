/**
 * Persistent Puppeteer browser singleton for JARVIS.
 * Uses a dedicated user-data dir so logins/cookies survive restarts.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const USER_DATA = path.join(ROOT, "user-data");

let browser = null;
let page = null;

export function getUserDataDir() {
  return USER_DATA;
}

export async function ensureBrowser({ headless = false } = {}) {
  if (browser && browser.connected) {
    if (!page || page.isClosed()) {
      const pages = await browser.pages();
      page = pages[0] || (await browser.newPage());
    }
    return { browser, page };
  }

  fs.mkdirSync(USER_DATA, { recursive: true });

  browser = await puppeteer.launch({
    headless,
    defaultViewport: { width: 1400, height: 900 },
    userDataDir: USER_DATA,
    args: [
      "--start-maximized",
      "--disable-blink-features=AutomationControlled",
      "--no-default-browser-check",
      "--disable-infobars",
      "--autoplay-policy=no-user-gesture-required",
    ],
    ignoreDefaultArgs: ["--enable-automation"],
  });

  const pages = await browser.pages();
  page = pages[0] || (await browser.newPage());
  await page.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
  );
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

  browser.on("disconnected", () => {
    browser = null;
    page = null;
  });

  return { browser, page };
}

export async function getPage() {
  const ctx = await ensureBrowser({ headless: process.env.PUPPETEER_HEADLESS === "1" });
  return ctx.page;
}

export async function newTab(url) {
  const { browser: b } = await ensureBrowser({
    headless: process.env.PUPPETEER_HEADLESS === "1",
  });
  const p = await b.newPage();
  if (url) await p.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  page = p;
  return p;
}

export async function closeBrowser() {
  if (browser) {
    try {
      await browser.close();
    } catch {
      /* ignore */
    }
  }
  browser = null;
  page = null;
}

export function status() {
  return {
    connected: !!(browser && browser.connected),
    userDataDir: USER_DATA,
    url: page && !page.isClosed() ? page.url() : null,
  };
}
