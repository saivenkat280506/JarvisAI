/**
 * Google search + slow visible scroll (Puppeteer).
 */
import * as actions from "../actions.mjs";

export async function searchAndScroll({
  query,
  times = 4,
  pixels = 380,
  delayMs = 5000,
  settleMs = 900,
} = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing search query");

  const url = `https://www.google.com/search?q=${encodeURIComponent(q)}`;
  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await actions.wait(1500);

  // Dismiss consent if present
  try {
    await actions.runAction("click_text", { text: "Accept all" });
  } catch {
    /* ignore */
  }
  try {
    await actions.runAction("click_text", { text: "I agree" });
  } catch {
    /* ignore */
  }
  await actions.wait(800);

  const scroll = await actions.scroll({
    pixels,
    direction: "down",
    times,
    delayMs,
    behavior: "smooth",
    settleMs,
  });

  const info = await actions.contentInfo();
  return {
    ok: true,
    query: q,
    ...scroll,
    ...info,
    message: `Opened Google for "${q}" and scrolled ${times} times with ${delayMs}ms gaps.`,
  };
}
