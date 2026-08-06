"""
music_services.py — Music Playback & Music Control for JARVIS
=============================================================
Playback services: local garage, YouTube Search, YouTube Music, Spotify.
Music control: play / stop / pause / resume / volume / mute / status.
"""

import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import pygame

LOCAL_MUSIC_DIR = Path(__file__).resolve().parent.parent / "local_music"
GARAGE_TRACK = LOCAL_MUSIC_DIR / "garage_music.mp3"

_mixer_lock = threading.Lock()
_local_paused = False
_music_volume = 80          # 0–100 for local music playback
_music_muted = False
_music_muted_level = 80     # restore level after unmute
_current_track = ""


def _ensure_mixer():
    if not pygame.mixer.get_init():
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.mixer.init()


def _apply_music_volume():
    """Apply stored music volume to pygame mixer (0.0–1.0 scale)."""
    if not pygame.mixer.get_init():
        return
    level = 0.0 if _music_muted else max(0.0, min(1.0, _music_volume / 100.0))
    pygame.mixer.music.set_volume(level)


def get_music_volume() -> int:
    return _music_volume


def set_music_volume(level: int) -> tuple[bool, str]:
    global _music_volume, _music_muted
    _music_volume = max(0, min(100, int(level)))
    _music_muted = False
    with _mixer_lock:
        _apply_music_volume()
    return True, f"Music volume set to {_music_volume}, sir."


def adjust_music_volume(delta: int) -> tuple[bool, str]:
    global _music_volume, _music_muted
    _music_muted = False
    _music_volume = max(0, min(100, _music_volume + int(delta)))
    with _mixer_lock:
        _apply_music_volume()
    direction = "increased" if delta > 0 else "decreased"
    return True, f"Music volume {direction} to {_music_volume}, sir."


def mute_music() -> tuple[bool, str]:
    global _music_muted, _music_muted_level
    _music_muted_level = _music_volume
    _music_muted = True
    with _mixer_lock:
        _apply_music_volume()
    return True, "Music muted, sir."


def unmute_music() -> tuple[bool, str]:
    global _music_muted, _music_volume
    _music_muted = False
    _music_volume = _music_muted_level
    with _mixer_lock:
        _apply_music_volume()
    return True, f"Music unmuted at {_music_volume}, sir."


def stop_local_music() -> bool:
    """Stop background local music playback."""
    global _local_paused, _current_track
    with _mixer_lock:
        _local_paused = False
        _current_track = ""
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
            return True
    return False


def pause_local_music() -> tuple[bool, str]:
    global _local_paused
    with _mixer_lock:
        if not pygame.mixer.get_init() or _local_paused:
            if _local_paused:
                return True, "Music is already paused, sir."
            return False, "No music is playing, sir."
        if not pygame.mixer.music.get_busy():
            return False, "No music is playing, sir."
        pygame.mixer.music.pause()
        _local_paused = True
    return True, "Music paused, sir."


def resume_after_speech():
    """Resume local music if it was paused while JARVIS was speaking."""
    global _local_paused
    with _mixer_lock:
        if not _local_paused or not pygame.mixer.get_init():
            return
        pygame.mixer.music.unpause()
        _local_paused = False
        _apply_music_volume()


def pause_for_listening() -> bool:
    """Pause music so the mic does not pick up playback during STT."""
    global _local_paused
    with _mixer_lock:
        if not pygame.mixer.get_init() or _local_paused:
            return False
        if not pygame.mixer.music.get_busy():
            return False
        pygame.mixer.music.pause()
        _local_paused = True
        return True


def resume_after_listening():
    """Resume music after voice listening if it was paused for STT."""
    resume_after_speech()


def resume_local_music() -> tuple[bool, str]:
    global _local_paused
    with _mixer_lock:
        if not pygame.mixer.get_init():
            return False, "No music to resume, sir."
        if not _local_paused:
            if pygame.mixer.music.get_busy():
                return True, "Music is already playing, sir."
            return False, "No paused music found, sir."
        pygame.mixer.music.unpause()
        _local_paused = False
        _apply_music_volume()
    return True, "Music resumed, sir."


def is_local_music_playing() -> bool:
    if not pygame.mixer.get_init():
        return False
    return pygame.mixer.music.get_busy() and not _local_paused


def get_music_status() -> tuple[bool, str]:
    if _local_paused:
        state = "paused"
    elif is_local_music_playing():
        state = "playing"
    elif pygame.mixer.get_init() and pygame.mixer.music.get_busy():
        state = "paused"
    else:
        state = "stopped"

    track = _current_track or "garage music" if state != "stopped" else "none"
    vol_label = "muted" if _music_muted else str(_music_volume)
    return True, f"Music is {state}. Track: {track}. Volume: {vol_label}, sir."


def _start_garage_track() -> bool:
    """Load and start the garage track; returns True only if playback is active."""
    global _local_paused, _current_track

    _ensure_mixer()
    _local_paused = False
    _current_track = GARAGE_TRACK.stem.replace("_", " ")
    pygame.mixer.music.stop()
    pygame.mixer.music.unload()
    pygame.mixer.music.load(str(GARAGE_TRACK))
    _apply_music_volume()
    pygame.mixer.music.play()
    time.sleep(0.15)
    return pygame.mixer.music.get_busy()


def play_local_garage() -> tuple[bool, str]:
    """Play the default garage track silently in the background — no player window."""
    if not GARAGE_TRACK.exists():
        return False, "Local garage track not found, sir."

    try:
        with _mixer_lock:
            if _start_garage_track():
                return True, "Playing garage music, sir."

            # Retry once after reinitializing the mixer (Windows/SDL can miss the first start).
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            _start_garage_track()
            if pygame.mixer.music.get_busy():
                return True, "Playing garage music, sir."

        return False, "Music playback failed to start, sir."
    except Exception as exc:
        return False, f"Failed to play local music: {exc}"


def restart_music() -> tuple[bool, str]:
    """Restart the current local track from the beginning."""
    if not GARAGE_TRACK.exists():
        return False, "No local track available, sir."
    return play_local_garage()


def play_youtube_search(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to search for, sir."
    try:
        query = urllib.parse.quote_plus(song.strip())
        url = f"https://www.youtube.com/results?search_query={query}"
        webbrowser.open(url)
        return True, f"Opened YouTube search for {song.strip()}, sir."
    except Exception as exc:
        return False, f"Failed to open YouTube: {exc}"


def play_youtube_music(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to play, sir."
    try:
        query = urllib.parse.quote_plus(song.strip())
        url = f"https://www.youtube.com/results?search_query={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        vid_ids = re.findall(r'"videoId":"([^"]{11})"', html)
        if vid_ids:
            music_url = f"https://music.youtube.com/watch?v={vid_ids[0]}"
            webbrowser.open(music_url)
            return True, f"Playing {song.strip()} on YouTube Music, sir."

        music_url = f"https://music.youtube.com/search?q={query}"
        webbrowser.open(music_url)
        return True, f"Opened YouTube Music search for {song.strip()}, sir."
    except Exception as exc:
        return False, f"Failed to play on YouTube Music: {exc}"


def play_spotify(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to search for, sir."
    try:
        query = urllib.parse.quote(song.strip())
        url = f"https://open.spotify.com/search/{query}"
        webbrowser.open(url)
        return True, f"Searching for {song.strip()} on Spotify, sir."
    except Exception as exc:
        return False, f"Failed to open Spotify: {exc}"


def music_control(params: dict) -> tuple[bool, str]:
    """
    Unified music control handler.
    Actions: stop | pause | resume | restart | status |
             volume_set | volume_up | volume_down | mute | unmute
    """
    action = (params.get("action") or "").lower()

    if action == "stop":
        if stop_local_music():
            return True, "Music stopped, sir."
        return False, "No music was playing, sir."

    if action == "pause":
        return pause_local_music()

    if action == "resume":
        return resume_local_music()

    if action == "restart":
        return restart_music()

    if action == "status":
        return get_music_status()

    if action == "volume_set":
        level = params.get("level")
        if level is None:
            return False, "Please specify a music volume between 0 and 100, sir."
        return set_music_volume(int(level))

    if action == "volume_up":
        amount = int(params.get("amount", 10))
        return adjust_music_volume(abs(amount))

    if action == "volume_down":
        amount = int(params.get("amount", 10))
        return adjust_music_volume(-abs(amount))

    if action == "mute":
        return mute_music()

    if action == "unmute":
        return unmute_music()

    return False, "Unknown music control action, sir."