"""
browser_puppeteer.py — High-level JARVIS tools backed by Puppeteer.
==================================================================
Intents / helpers:
  - browser_action (generic)
  - youtube / youtube music play
  - spotify login + search
  - scroll speed test
  - click / type / scroll primitives
"""

from __future__ import annotations

from typing import Any

from executor.puppeteer_client import command, tool_result


def browser_action(params: dict | None = None) -> tuple[bool, str]:
    """
    Generic entry: params.action + extra fields.
    Example: {"action": "youtube_play", "query": "AC/DC Back in Black"}
    """
    params = params or {}
    action = params.get("action") or params.get("op") or "status"
    extra = {k: v for k, v in params.items() if k not in ("action", "op")}
    result = command(action, **extra)
    return tool_result(result)


def youtube_play_puppeteer(song: str, service: str = "youtube") -> tuple[bool, str]:
    action = "youtube_music_play" if "music" in (service or "").lower() else "youtube_play"
    result = command(action, query=song or "AC/DC Back in Black", song=song)
    return tool_result(result, default_ok_message=f"Playing {song} on YouTube.")


def spotify_login_puppeteer(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "spotify_login",
        timeout=120.0,
        email=params.get("email", ""),
        password=params.get("password", ""),
    )
    # Partial success: page opened but needs credentials
    if result.get("needsCredentials"):
        return True, result.get("message", "Spotify login page opened.")
    return tool_result(result, default_ok_message="Spotify login flow completed.")


def spotify_search_puppeteer(query: str, play: bool = True) -> tuple[bool, str]:
    result = command("spotify_search", timeout=120.0, query=query, play=play)
    return tool_result(result, default_ok_message=f"Spotify search for {query} opened.")


def scroll_test_puppeteer(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "scroll_test",
        timeout=120.0,
        url=params.get("url") or "https://en.wikipedia.org/wiki/AC/DC",
        pixels=int(params.get("pixels") or 900),
        times=int(params.get("times") or 8),
        delayMs=int(params.get("delayMs") or params.get("delay_ms") or 150),
    )
    return tool_result(result, default_ok_message="Scroll speed test complete.")


def browser_click(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    if params.get("text"):
        result = command("click_text", text=params["text"])
    else:
        result = command("click", selector=params.get("selector", "body"))
    return tool_result(result, default_ok_message="Clicked.")


def browser_type(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "type",
        selector=params.get("selector", "input"),
        text=params.get("text", ""),
    )
    return tool_result(result, default_ok_message="Typed.")


def browser_scroll(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "scroll",
        pixels=int(params.get("pixels") or 800),
        direction=params.get("direction") or "down",
        times=int(params.get("times") or 1),
        delayMs=int(params.get("delayMs") or 100),
    )
    return tool_result(result, default_ok_message="Scrolled.")


def browser_navigate(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    url = params.get("url") or params.get("href") or "about:blank"
    result = command("navigate", url=url)
    return tool_result(result, default_ok_message=f"Opened {url}.")


# Registry-friendly lambdas receive params dict
def play_youtube_search_pp(params: dict) -> tuple[bool, str]:
    return youtube_play_puppeteer(params.get("song") or params.get("query") or "", service="youtube")


def play_youtube_music_pp(params: dict) -> tuple[bool, str]:
    return youtube_play_puppeteer(
        params.get("song") or params.get("query") or "", service="youtube_music"
    )


def play_spotify_pp(params: dict) -> tuple[bool, str]:
    song = params.get("song") or params.get("query") or ""
    # Ensure session if possible, then search+play
    login_res = command("spotify_login", timeout=90.0)
    if login_res.get("needsCredentials") and not song:
        return True, login_res.get("message", "Spotify login page opened.")
    if song:
        return spotify_search_puppeteer(song, play=True)
    return tool_result(login_res, default_ok_message="Spotify ready.")
