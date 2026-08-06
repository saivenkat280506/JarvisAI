/**
 * Spotify web automation: login + search.
 * Credentials: params or env SPOTIFY_EMAIL / SPOTIFY_PASSWORD.
 */
import { getPage } from "../browser.mjs";
import * as actions from "../actions.mjs";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function credsFrom(params = {}) {
  return {
    email: params.email || process.env.SPOTIFY_EMAIL || "",
    password: params.password || process.env.SPOTIFY_PASSWORD || "",
  };
}

export async function isLoggedIn() {
  const page = await getPage();
  const url = page.url();
  // Cookie presence / UI hints
  const state = await page.evaluate(() => {
    const body = document.body?.innerText || "";
    const hasLogin = /log in|sign up/i.test(body) && !/log out|account/i.test(body);
    const avatar = !!document.querySelector('[data-testid="user-widget-link"], [data-testid="user-widget-avatar"]');
    return { hasLogin, avatar, href: location.href };
  });
  return {
    ok: true,
    loggedIn: state.avatar || (url.includes("open.spotify.com") && !state.hasLogin && !url.includes("/login")),
    ...state,
  };
}

export async function login(params = {}) {
  const { email, password } = credsFrom(params);
  const page = await getPage();

  await actions.navigate("https://accounts.spotify.com/en/login", {
    waitUntil: "networkidle2",
    timeout: 90000,
  });
  await sleep(1500);

  // Already logged in redirect?
  if (page.url().includes("open.spotify.com") && !page.url().includes("login")) {
    return { ok: true, alreadyLoggedIn: true, url: page.url(), message: "Already logged into Spotify." };
  }

  if (!email || !password) {
    return {
      ok: false,
      needsCredentials: true,
      url: page.url(),
      message:
        "Spotify login page is open. Set SPOTIFY_EMAIL and SPOTIFY_PASSWORD in env (or pass email/password) to complete automated login.",
    };
  }

  // Cookie banners
  try {
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll("button")];
      const accept = btns.find((b) => /accept|agree|allow all/i.test(b.innerText || ""));
      if (accept) accept.click();
    });
  } catch {
    /* ignore */
  }

  // Fill login form — Spotify uses #login-username / #login-password historically;
  // also try name/data-testid variants.
  const userSel =
    'input#login-username, input[name="username"], input[data-testid="login-username"], input[type="email"], input[type="text"]';
  const passSel =
    'input#login-password, input[name="password"], input[data-testid="login-password"], input[type="password"]';

  await page.waitForSelector(userSel, { timeout: 20000 });
  await page.click(userSel, { clickCount: 3 });
  await page.type(userSel, email, { delay: 20 });
  await page.click(passSel, { clickCount: 3 });
  await page.type(passSel, password, { delay: 20 });

  // Submit
  const submitted = await page.evaluate(() => {
    const btn =
      document.querySelector('button#login-button, button[data-testid="login-button"], button[type="submit"]') ||
      [...document.querySelectorAll("button")].find((b) => /log in|sign in/i.test(b.innerText || ""));
    if (btn) {
      btn.click();
      return true;
    }
    return false;
  });
  if (!submitted) await page.keyboard.press("Enter");

  // Wait for navigation or error
  await Promise.race([
    page.waitForNavigation({ waitUntil: "networkidle2", timeout: 45000 }).catch(() => null),
    sleep(8000),
  ]);

  await sleep(2000);
  const url = page.url();
  const bodyText = await page.evaluate(() => (document.body?.innerText || "").slice(0, 500));
  const failed = /incorrect|wrong password|couldn't find|try again|error/i.test(bodyText);

  if (failed) {
    return {
      ok: false,
      url,
      message: "Spotify login appears to have failed (check credentials or captcha).",
      snippet: bodyText.slice(0, 200),
    };
  }

  // Go to web player
  if (!url.includes("open.spotify.com")) {
    await actions.navigate("https://open.spotify.com/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(2000);
  }

  const logged = await isLoggedIn();
  return {
    ok: true,
    url: page.url(),
    loggedIn: logged.loggedIn,
    message: logged.loggedIn
      ? "Spotify login successful."
      : "Login form submitted. If a captcha or 2FA appeared, complete it once in the browser window; session will be saved.",
  };
}

export async function search(query, { play = false } = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing Spotify search query");

  const page = await getPage();
  const url = `https://open.spotify.com/search/${encodeURIComponent(q)}`;
  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  await sleep(3000);

  // Dismiss cookie if needed
  try {
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll("button")];
      const accept = btns.find((b) => /accept|agree/i.test(b.innerText || ""));
      if (accept) accept.click();
    });
  } catch {
    /* ignore */
  }

  let played = null;
  if (play) {
    played = await page.evaluate(() => {
      const playBtns = [
        ...document.querySelectorAll(
          'button[data-testid="play-button"], button[aria-label*="Play"], [data-testid="top-result-card"] button'
        ),
      ];
      const btn = playBtns[0];
      if (btn) {
        btn.click();
        return { text: (btn.getAttribute("aria-label") || btn.innerText || "play").slice(0, 80) };
      }
      // double-click first row
      const row = document.querySelector('[data-testid="tracklist-row"], [role="row"]');
      if (row) {
        row.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
        return { text: (row.innerText || "").slice(0, 80) };
      }
      return null;
    });
    await sleep(2000);
  }

  const info = await actions.contentInfo();
  return {
    ok: true,
    query: q,
    played,
    ...info,
    message: play
      ? `Searched Spotify for "${q}"${played ? " and hit play." : " (play button not auto-found — page is open)."}`
      : `Opened Spotify search for "${q}".`,
  };
}
