/**
 * Low-level browser actions used by the control server and scenarios.
 */
import { getPage, ensureBrowser, newTab, status as browserStatus } from "./browser.mjs";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export async function navigate(url, { waitUntil = "domcontentloaded", timeout = 60000 } = {}) {
  const page = await getPage();
  await page.goto(url, { waitUntil, timeout });
  return { ok: true, url: page.url(), title: await page.title() };
}

export async function click(selector, { timeout = 15000, button = "left" } = {}) {
  const page = await getPage();
  await page.waitForSelector(selector, { timeout, visible: true });
  await page.click(selector, { button });
  return { ok: true, selector };
}

export async function clickText(text, { timeout = 15000 } = {}) {
  const page = await getPage();
  const handle = await page.waitForFunction(
    (t) => {
      const needle = t.toLowerCase();
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
      let node;
      while ((node = walk.nextNode())) {
        const el = /** @type {HTMLElement} */ (node);
        if (!el.innerText) continue;
        const label = (el.innerText || "").trim().toLowerCase();
        if (label === needle || label.includes(needle)) {
          const style = window.getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden") continue;
          return el;
        }
      }
      return null;
    },
    { timeout },
    text
  );
  const el = await handle.asElement();
  if (!el) throw new Error(`Text not found: ${text}`);
  await el.click();
  return { ok: true, text };
}

export async function type(selector, text, { clear = true, delay = 25, timeout = 15000 } = {}) {
  const page = await getPage();
  await page.waitForSelector(selector, { timeout, visible: true });
  if (clear) {
    await page.click(selector, { clickCount: 3 });
    await page.keyboard.press("Backspace");
  }
  await page.type(selector, text, { delay });
  return { ok: true, selector, length: text.length };
}

export async function press(key) {
  const page = await getPage();
  await page.keyboard.press(key);
  return { ok: true, key };
}

export async function scroll({
  pixels = 800,
  direction = "down",
  times = 1,
  delayMs = 200,
} = {}) {
  const page = await getPage();
  const delta = direction === "up" ? -Math.abs(pixels) : Math.abs(pixels);
  const timings = [];
  for (let i = 0; i < times; i++) {
    const t0 = performance.now();
    await page.evaluate((d) => window.scrollBy({ top: d, behavior: "instant" }), delta);
    const t1 = performance.now();
    timings.push(t1 - t0);
    if (i < times - 1 && delayMs > 0) await sleep(delayMs);
  }
  const avg = timings.reduce((a, b) => a + b, 0) / Math.max(timings.length, 1);
  const pos = await page.evaluate(() => ({
    y: window.scrollY,
    max: document.documentElement.scrollHeight - window.innerHeight,
  }));
  return {
    ok: true,
    times,
    pixels: delta,
    avgScrollMs: Number(avg.toFixed(2)),
    minMs: Number(Math.min(...timings).toFixed(2)),
    maxMs: Number(Math.max(...timings).toFixed(2)),
    scrollY: pos.y,
    scrollMax: pos.max,
  };
}

export async function wait(ms = 1000) {
  await sleep(ms);
  return { ok: true, waitedMs: ms };
}

export async function waitFor(selector, { timeout = 20000 } = {}) {
  const page = await getPage();
  await page.waitForSelector(selector, { timeout, visible: true });
  return { ok: true, selector };
}

export async function evalJs(expression) {
  const page = await getPage();
  const result = await page.evaluate((expr) => {
    // eslint-disable-next-line no-eval
    return eval(expr);
  }, expression);
  return { ok: true, result };
}

export async function screenshot({ path: outPath = null, fullPage = false } = {}) {
  const page = await getPage();
  const opts = { fullPage, type: "png" };
  if (outPath) opts.path = outPath;
  const buf = await page.screenshot(opts);
  return {
    ok: true,
    path: outPath,
    bytes: buf?.length || 0,
  };
}

export async function contentInfo() {
  const page = await getPage();
  return {
    ok: true,
    url: page.url(),
    title: await page.title(),
  };
}

export async function open(url) {
  await ensureBrowser({ headless: process.env.PUPPETEER_HEADLESS === "1" });
  if (url) return navigate(url);
  return { ok: true, ...browserStatus() };
}

export async function openNewTab(url) {
  await newTab(url);
  return contentInfo();
}

/**
 * Dispatch a generic action by name.
 */
export async function runAction(action, params = {}) {
  switch (action) {
    case "status":
      return { ok: true, ...browserStatus() };
    case "open":
    case "navigate":
      return navigate(params.url || params.href || "about:blank");
    case "new_tab":
      return openNewTab(params.url);
    case "click":
      return click(params.selector, params);
    case "click_text":
      return clickText(params.text, params);
    case "type":
      return type(params.selector, params.text ?? params.value ?? "", params);
    case "press":
      return press(params.key || "Enter");
    case "scroll":
      return scroll(params);
    case "wait":
      return wait(params.ms ?? params.seconds * 1000 ?? 1000);
    case "wait_for":
      return waitFor(params.selector, params);
    case "eval":
      return evalJs(params.expression || params.code || "document.title");
    case "screenshot":
      return screenshot(params);
    case "info":
      return contentInfo();
    default:
      throw new Error(`Unknown action: ${action}`);
  }
}
