"""
volume_control.py — Windows System Volume (0–100)
=================================================
Precise master volume control via Windows Core Audio API.
"""

from pycaw.utils import AudioUtilities


def _endpoint():
    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise RuntimeError("No audio output device found.")
    return device.EndpointVolume


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