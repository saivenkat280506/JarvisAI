"""
volume_control.py — Windows System Volume (0–100)
=================================================
Precise master volume control via Windows Core Audio API.
Also manages temporary levels during garage-music sessions
(play 50% / speak 25% / listen 20% on the real Windows slider).
"""

from pycaw.utils import AudioUtilities
from pycaw.pycaw import IAudioEndpointVolume
from comtypes import CLSCTX_ALL, cast, POINTER

# Garage music session: Windows master volume targets
GARAGE_PLAY_VOLUME = 50
GARAGE_SPEAK_VOLUME = 25
GARAGE_LISTEN_VOLUME = 20

_garage_session = False
_volume_before_garage: int | None = None


def _endpoint():
    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise RuntimeError("No audio output device found.")
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    """Return current system master volume as 0–100."""
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def is_muted() -> bool:
    return bool(_endpoint().GetMute())


def set_volume(level: int) -> tuple[bool, str]:
    level = max(0, min(100, int(level)))
    vol = _endpoint()
    vol.SetMute(0, None)
    vol.SetMasterVolumeLevelScalar(level / 100.0, None)
    return True, f"Volume set to {level}, sir."


def begin_garage_volume_session(play_level: int = GARAGE_PLAY_VOLUME) -> int:
    """
    Remember prior Windows volume and set play level (default 50%).
    Returns the level applied.
    """
    global _garage_session, _volume_before_garage
    try:
        if not _garage_session:
            _volume_before_garage = get_volume()
            _garage_session = True
        set_volume(play_level)
        print(f"[Volume] Garage session ON — Windows volume {play_level}% (was {_volume_before_garage})")
        return play_level
    except Exception as exc:
        print(f"[Volume] begin_garage_volume_session failed: {exc}")
        return play_level


def set_windows_volume_safe(level: int) -> None:
    try:
        set_volume(int(level))
        print(f"[Volume] Windows master -> {int(level)}%")
    except Exception as exc:
        print(f"[Volume] set failed: {exc}")


def garage_volume_for_play() -> None:
    """Windows volume while garage track plays normally."""
    if _garage_session:
        set_windows_volume_safe(GARAGE_PLAY_VOLUME)


def garage_volume_for_speech() -> None:
    """Windows volume while JARVIS speaks over the garage track (25%)."""
    if _garage_session:
        set_windows_volume_safe(GARAGE_SPEAK_VOLUME)
    else:
        # Still lower if music is playing without session flag
        set_windows_volume_safe(GARAGE_SPEAK_VOLUME)


def garage_volume_for_listen() -> None:
    """Windows volume while listening to the user (20%)."""
    if _garage_session:
        set_windows_volume_safe(GARAGE_LISTEN_VOLUME)


def end_garage_volume_session(restore: bool = True) -> None:
    """End garage session; optionally restore pre-session Windows volume."""
    global _garage_session, _volume_before_garage
    if not _garage_session:
        return
    prev = _volume_before_garage
    _garage_session = False
    _volume_before_garage = None
    if restore and prev is not None:
        try:
            set_volume(prev)
            print(f"[Volume] Garage session OFF — restored Windows volume {prev}%")
        except Exception as exc:
            print(f"[Volume] restore failed: {exc}")


def is_garage_volume_session() -> bool:
    return _garage_session


def adjust_volume(delta: int) -> tuple[bool, str]:
    current = get_volume()
    new_level = max(0, min(100, current + int(delta)))
    vol = _endpoint()
    vol.SetMute(0, None)
    vol.SetMasterVolumeLevelScalar(new_level / 100.0, None)
    direction = "increased" if delta > 0 else "decreased"
    return True, f"Volume {direction} to {new_level}, sir."


def mute_volume() -> tuple[bool, str]:
    _endpoint().SetMute(1, None)
    return True, "Volume muted, sir."


def unmute_volume() -> tuple[bool, str]:
    vol = _endpoint()
    vol.SetMute(0, None)
    level = round(vol.GetMasterVolumeLevelScalar() * 100)
    return True, f"Volume unmuted at {level}, sir."


def volume_control(params: dict) -> tuple[bool, str]:
    """
    Unified volume handler.
    Params:
      action: get | set | up | down | mute | unmute
      level:  target 0–100 (for set)
      amount: step size (for up/down, default 10)
    """
    action = (params.get("action") or "").lower()

    try:
        if action == "get":
            muted = is_muted()
            level = get_volume()
            if muted:
                return True, f"Volume is muted. Level before mute was {level}, sir."
            return True, f"Current volume is {level}, sir."

        if action == "set":
            level = params.get("level")
            if level is None:
                return False, "Please specify a volume level between 0 and 100, sir."
            return set_volume(int(level))

        if action == "up":
            amount = int(params.get("amount", 10))
            return adjust_volume(abs(amount))

        if action == "down":
            amount = int(params.get("amount", 10))
            return adjust_volume(-abs(amount))

        if action == "mute":
            return mute_volume()

        if action == "unmute":
            return unmute_volume()

        return False, "Unknown volume action, sir."
    except Exception as exc:
        return False, f"Volume control failed: {exc}"