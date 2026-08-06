/**
 * Scroll performance test scenario.
 */
import * as actions from "../actions.mjs";

export async function scrollSpeedTest({
  url = "https://en.wikipedia.org/wiki/AC/DC",
  pixels = 900,
  times = 8,
  delayMs = 150,
} = {}) {
  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await actions.wait(1200);

  const t0 = performance.now();
  const result = await actions.scroll({ pixels, direction: "down", times, delayMs });
  const wallMs = performance.now() - t0;

  return {
    ok: true,
    url,
    ...result,
    wallClockMs: Number(wallMs.toFixed(2)),
    scrollsPerSecond: Number(((times / wallMs) * 1000).toFixed(2)),
    message: `Scroll test: ${times}×${pixels}px, avg ${result.avgScrollMs} ms/scroll, wall ${wallMs.toFixed(0)} ms.`,
  };
}
