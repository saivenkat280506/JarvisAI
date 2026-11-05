"""
automation.py — Lightweight Automation Suite
===========================================
Provides simple system and browser automation without heavy dependencies.
WhatsApp via the native macOS app (whatsapp:// + AppleScript).
Search logs open in TextEdit.
"""

import os
import re
import json
import csv
import base64
import urllib.parse
import time
from difflib import get_close_matches
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config import settings
from executor.sys_platform import (
    MOD_KEY,
    desktop_dir,
    focus_application,
    get_clipboard,
    is_app_running,
    keystroke_in_app,
    kill_named,
    open_application,
    open_uri,
    open_url,
    osascript,
    pids_matching,
    set_clipboard,
    window_bounds,
)
from mcp_clients import macos as mcp_gui
from mcp_clients import shell as mcp_shell

try:
    import cv2
    import numpy as np
    import threading
    HAS_RECORDING_DEPS = True
except ImportError:
    HAS_RECORDING_DEPS = False

# ══════════════════════════════════════════════════════════════════════════════
# SCREEN RECORDER COMPONENT
# ══════════════════════════════════════════════════════════════════════════════

class ScreenRecorder:
    def __init__(self, filename, fps=8.0):
        self.filename = filename
        self.fps = fps
        self.recording = False
        self.thread = None
        
    def start(self):
        if not HAS_RECORDING_DEPS:
            print("[ScreenRecorder] Missing dependencies (cv2, numpy). Cannot record.")
            return
        self.recording = True
        self.thread = threading.Thread(target=self._record_loop, name="ScreenRecorderLoop")
        self.thread.daemon = True
        self.thread.start()
        print(f"[ScreenRecorder] Started recording to {self.filename}")
        
    def stop(self):
        self.recording = False
        if self.thread:
            self.thread.join()
        print(f"[ScreenRecorder] Stopped recording and saved to {self.filename}")
        
    def _record_loop(self):
        try:
            size = mcp_gui.gui_screen_size()
            width = max(1, int(size.get("width", 0)))
            height = max(1, int(size.get("height", 0)))
            if width < 2 or height < 2:
                raise RuntimeError("Could not read screen dimensions through macOS MCP")

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = None
            interval = 1.0 / max(float(self.fps), 0.1)
            try:
                while self.recording:
                    started = time.monotonic()
                    payload = mcp_gui.gui_screenshot_region(0, 0, width, height)
                    encoded = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
                    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    if frame is None:
                        raise RuntimeError("macOS MCP returned an invalid screenshot")
                    frame_h, frame_w = frame.shape[:2]
                    if out is None:
                        out = cv2.VideoWriter(self.filename, fourcc, self.fps, (frame_w, frame_h))
                    out.write(frame)
                    remaining = interval - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
            finally:
                if out is not None:
                    out.release()
        except Exception as e:
            print(f"[ScreenRecorder] Error during capture: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CORE AUTOMATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def open_browser(manual_path=None):
    """Opens Arc, Chrome, or the default browser."""
    if manual_path and os.path.exists(manual_path):
        try:
            result = mcp_shell.open_path(manual_path)
            if result.get("ok"):
                return True, "Successfully opened browser."
            return False, f"Failed to open browser: {result.get('stderr') or 'open failed'}"
        except Exception as e:
            return False, f"Failed to open browser: {str(e)}"

    for app in ("Arc", "Google Chrome", "Safari"):
        ok, msg = open_application(app)
        if ok:
            return True, msg
    try:
        open_url("about:blank")
        return True, "Opened default browser as fallback."
    except Exception as e:
        return False, f"Failed to open any browser: {str(e)}"

# ── WhatsApp Desktop (macOS) ─────────────────────────────────────────────────
# Native WhatsApp.app via whatsapp:// URL scheme. Sending is direct for saved
# contacts; the allowlist remains the contact safety boundary.

_CONTACTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_contacts.json")


def _whatsapp_pids() -> set:
    return pids_matching("WhatsApp")


def _find_desktop_whatsapp() -> dict | None:
    if not _whatsapp_pids() and not is_app_running("WhatsApp"):
        return None
    bounds = window_bounds("WhatsApp")
    if not bounds:
        return None
    return {
        "title": bounds.get("title") or "WhatsApp",
        "left": bounds["left"],
        "top": bounds["top"],
        "right": bounds["right"],
        "bottom": bounds["bottom"],
        "w": bounds["width"],
        "h": bounds["height"],
        "visible": True,
    }


def _focus_whatsapp_window() -> bool:
    ok, _ = focus_application("WhatsApp")
    time.sleep(0.2)
    return ok


def _screen_size():
    try:
        size = mcp_gui.gui_screen_size()
        return int(size["width"]), int(size["height"])
    except Exception:
        return 1440, 900


def _fallback_screen_info() -> dict:
    sw, sh = _screen_size()
    return {
        "title": "WhatsApp",
        "left": 0,
        "top": 28,
        "right": sw,
        "bottom": sh,
        "w": sw,
        "h": sh - 28,
        "visible": True,
    }


def _wait_for_whatsapp_window(timeout: float = 25.0, focus: bool = True) -> dict:
    deadline = time.time() + timeout
    pid_since: float | None = None
    while time.time() < deadline:
        if focus:
            _focus_whatsapp_window()
        running = bool(_whatsapp_pids() or is_app_running("WhatsApp"))
        if running:
            if pid_since is None:
                pid_since = time.time()
            info = _find_desktop_whatsapp()
            if info and info["w"] >= 400 and info["h"] >= 300:
                print(f"[WhatsApp] Ready: title={info['title']!r} {info['w']}x{info['h']}")
                return info
            if time.time() - pid_since >= 1.2:
                print("[WhatsApp] App is running; continuing without Accessibility window bounds.")
                return _fallback_screen_info()
        time.sleep(0.35)
    if _whatsapp_pids() or is_app_running("WhatsApp"):
        return _fallback_screen_info()
    raise RuntimeError("WhatsApp Desktop did not finish loading.")


def open_whatsapp():
    """Open WhatsApp.app, or focus it if it is already running."""
    if _whatsapp_pids() or is_app_running("WhatsApp"):
        _focus_whatsapp_window()
        return True, "WhatsApp is already open, sir."
    ok, msg = open_application("WhatsApp")
    if ok:
        return True, "Successfully opened WhatsApp."
    if open_uri("whatsapp://"):
        return True, "Successfully opened WhatsApp."
    return False, msg


def _focus_browser_window():
    """Focus a browser that might host WhatsApp Web."""
    for app in ("Google Chrome", "Arc", "Safari", "Firefox", "Microsoft Edge"):
        ok, _ = focus_application(app)
        if ok:
            print(f"[WhatsApp] Focused browser: {app}")
            return True
    print("[WhatsApp] Could not focus any browser window")
    return False


def _load_whatsapp_contacts() -> dict:
    """name(lower) -> phone. JSON file plus DB allowlist; never groups."""
    contacts: dict[str, str] = {}
    try:
        from brain.memory_store import whatsapp_allowlist
        contacts.update(whatsapp_allowlist())
    except Exception:
        pass
    try:
        if os.path.exists(_CONTACTS_PATH):
            with open(_CONTACTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, val in data.items():
                    name = str(key).strip().lower()
                    phone = str(val).strip()
                    if name and phone:
                        contacts[name] = phone
    except Exception as e:
        print(f"[WhatsApp] Failed to load contacts: {e}")
    return contacts


def normalize_phone_number(value: str) -> str:
    """Keep leading + and digits only. Strip spaces/dashes/parens."""
    if not value:
        return ""
    raw = str(value).strip()
    if "..." in raw or "…" in raw:
        return ""
    has_plus = raw.lstrip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return ("+" + digits) if has_plus or len(digits) >= 10 else digits


def looks_like_phone_number(value: str) -> bool:
    if not value:
        return False
    raw = str(value).strip()
    if "..." in raw or "…" in raw:
        return False
    digits = re.sub(r"\D", "", raw)
    return len(digits) >= 10


def resolve_whatsapp_number(name_or_number: str, number: str = "") -> tuple:
    """
    Resolve a WhatsApp target to a saved phone number.
    Placeholder numbers such as '+91...' are ignored so the contact name is used.

    Returns: (ok: bool, phone: str, label: str, error: str)
    """
    contacts = _load_whatsapp_contacts()
    saved_numbers = {
        normalize_phone_number(saved)
        for saved in contacts.values()
        if looks_like_phone_number(normalize_phone_number(saved))
    }

    explicit = normalize_phone_number(number) if number else ""
    if explicit and looks_like_phone_number(explicit):
        if explicit not in saved_numbers:
            return False, "", explicit, (
                f"The WhatsApp number {explicit} is not in the saved contacts database. "
                "I will not open or message an unlisted contact."
            )
        label = (name_or_number or explicit).strip() or explicit
        return True, explicit, label, ""

    target = (name_or_number or "").strip()
    if not target:
        return False, "", "", "No contact or phone number provided."

    blocked = {
        "all", "everyone", "everybody", "group", "the group",
        "broadcast", "all contacts", "every contact", "each contact",
        "my contacts", "saved contacts",
    }
    if target.lower() in blocked or re.search(
        r"\b(?:group|everyone|everybody|broadcast)\b", target, re.I
    ):
        return False, "", target, (
            "I will not send to groups, everyone on WhatsApp, or an unnamed chat. "
            "Name a saved contact, or say 'all contacts' with an explicit message."
        )

    if looks_like_phone_number(target):
        phone = normalize_phone_number(target)
        if phone in saved_numbers:
            return True, phone, phone, ""
        return False, "", phone, (
            f"The WhatsApp number {phone} is not in the saved contacts database. "
            "I will not open or message an unlisted contact."
        )

    key = target.lower()
    if key in contacts:
        phone = normalize_phone_number(contacts[key])
        if looks_like_phone_number(phone):
            return True, phone, target, ""

    # Speech often drops or swaps a letter (Satish/Sathish, Sadeesh, Nishant).
    # Accept several close names only when they all resolve to the same number.
    close = get_close_matches(key, list(contacts.keys()), n=3, cutoff=0.55)
    if close:
        phones = {
            normalize_phone_number(contacts[name])
            for name in close
            if looks_like_phone_number(normalize_phone_number(contacts[name]))
        }
        if len(phones) == 1:
            return True, next(iter(phones)), close[0], ""

    matches = [(n, p) for n, p in contacts.items() if key in n or n in key]
    unique_phones = {
        normalize_phone_number(p)
        for _, p in matches
        if looks_like_phone_number(normalize_phone_number(p))
    }
    if len(unique_phones) == 1:
        return True, next(iter(unique_phones)), matches[0][0], ""
    if len(matches) > 1:
        names = ", ".join(m[0] for m in matches[:5])
        return False, "", target, (
            f"Multiple contacts match '{target}' ({names}). "
            "Please say the saved contact name clearly."
        )

    return False, "", target, (
        f"I do not have a saved phone number for '{target}'. "
        "Please use a saved contact so I can open the correct chat."
    )


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _refuse_non_sathish(name_or_number: str, number: str, phone: str, label: str) -> str | None:
    # Compatibility hook for older callers. The saved-contact allowlist above
    # is now the contact safety boundary.
    return None


def _clipboard_get() -> str:
    try:
        return get_clipboard() or ""
    except Exception:
        return ""


def _clipboard_set(text: str):
    try:
        set_clipboard(text)
    except Exception:
        pass


def _paste_text(text: str):
    """Paste via MCP clipboard + Command-V. Fallback is character typing."""
    _clipboard_set(text)
    time.sleep(0.12)
    if keystroke_in_app("WhatsApp", "v", command=True):
        return
    try:
        mcp_gui.gui_hotkey([MOD_KEY, "v"])
        return
    except Exception as e:
        print(f"[WhatsApp] Clipboard paste failed, typing instead: {e}")
    mcp_gui.gui_type(text, interval=0.03)


def _open_chat_by_protocol(digits: str, message: str = ""):
    """
    Open a WhatsApp chat. If message is set, WhatsApp fills the composer.
    This does not send. Keyboard injection is not used.
    """
    from executor.sys_platform import run_open

    encoded = urllib.parse.quote(message) if message else ""
    uris = [
        f"whatsapp://send?phone={digits}" + (f"&text={encoded}" if encoded else ""),
        f"https://wa.me/{digits}" + (f"?text={encoded}" if encoded else ""),
    ]
    print(
        f"[WhatsApp] Opening WhatsApp chat via URL (text={'yes' if message else 'no'}, send=no)"
    )
    first = uris[0]
    result = run_open("-a", "WhatsApp", first)
    if result.returncode != 0:
        open_uri(first)
    return first


def _composer_rect(info: dict | None = None) -> tuple[int, int, int, int]:
    """Composer band inside the WhatsApp window (not the full screen)."""
    info = info or _find_desktop_whatsapp() or {}
    left = int(info.get("left", 0) + max(180, info.get("w", 900) * 0.32))
    width = int(max(240, info.get("w", 900) * 0.64))
    height = 72
    top = int(info.get("bottom", _screen_size()[1]) - height - 12)
    return left, top, width, height


def _composer_point(info: dict | None = None):
    left, top, width, height = _composer_rect(info)
    return int(left + width * 0.45), int(top + height * 0.55)


def _send_point(info: dict | None = None):
    info = info or _find_desktop_whatsapp() or {}
    return int(info.get("right", _screen_size()[0]) - 28), int(info.get("bottom", _screen_size()[1]) - 36)


def _click_composer(info: dict = None):
    x, y = _composer_point(info)
    print(f"[WhatsApp] Clicking composer at ({x}, {y})")
    mcp_gui.gui_click(x, y)
    time.sleep(0.25)


def _click_send_button(info: dict = None):
    x, y = _send_point(info)
    print(f"[WhatsApp] Clicking send at ({x}, {y})")
    mcp_gui.gui_click(x, y)
    time.sleep(0.25)


def _composer_band_image(info: dict | None = None):
    left, top, width, height = _composer_rect(info)
    try:
        import base64 as _b64
        import io as _io
        from PIL import Image
        payload = mcp_gui.gui_screenshot_region(left, top, width, height)
        return Image.open(_io.BytesIO(_b64.b64decode(payload)))
    except Exception as exc:
        print(f"[WhatsApp] Composer screenshot skipped: {exc}")
        return None


def _chat_ocr(info: dict | None = None) -> str:
    """Read only the conversation area; intentionally excludes the composer."""
    if not info:
        return ""
    try:
        from PIL import Image
        import base64 as _b64
        import io as _io
        import pytesseract

        left = int(info.get("left", 0))
        top = int(info.get("top", 0) + 90)
        width = int(info.get("w", 900))
        height = int(max(240, info.get("h", 700) - 170))
        payload = mcp_gui.gui_screenshot_region(left, top, width, height)
        image = Image.open(_io.BytesIO(_b64.b64decode(payload)))
        ocr = pytesseract.image_to_string(image, config="--psm 6")
        return re.sub(r"[^a-z0-9]+", " ", ocr.lower()).strip()
    except Exception as exc:
        print(f"[WhatsApp] Post-send verification unavailable: {type(exc).__name__}: {exc}")
        return ""


def _message_visible_in_chat(
    message: str,
    info: dict | None = None,
    before_text: str = "",
) -> bool:
    """Require a newly appearing message, not an older matching message."""
    target = re.sub(r"[^a-z0-9]+", " ", (message or "").lower()).strip()
    after_text = _chat_ocr(info)
    if not target or not after_text or target not in after_text:
        print("[WhatsApp] Sent message was not found in conversation OCR.")
        return False
    # A matching message already present before the send is not proof that the
    # current request was delivered.
    if target in before_text and after_text.count(target) <= before_text.count(target):
        print("[WhatsApp] Matching older message found; delivery is unverified.")
        return False
    print("[WhatsApp] Newly sent message text verified in conversation.")
    return True


def _images_differ(before, after, threshold: float = 1.8) -> bool:
    import numpy as np
    a = np.asarray(before, dtype=np.int16)
    b = np.asarray(after, dtype=np.int16)
    if a.shape != b.shape:
        return True
    return float(np.mean(np.abs(a - b))) > threshold


def _clear_and_type(message: str):
    keystroke_in_app("WhatsApp", "a", command=True)
    time.sleep(0.08)
    mcp_gui.gui_press("backspace")
    time.sleep(0.12)
    _paste_text(message)
    time.sleep(0.35)


def _type_into_composer(message: str, empty_band, click_first: bool = False, info: dict | None = None):
    if click_first:
        _click_composer(info)
    _clear_and_type(message)
    typed_band = _composer_band_image(info)
    if empty_band is None or typed_band is None:
        return True, typed_band
    if _images_differ(empty_band, typed_band):
        return True, typed_band
    print("[WhatsApp] Composer pixels did not change after typing")
    return False, typed_band


def _search_chat_by_number(phone: str):
    """In-app number search if the protocol did not land in the chat composer."""
    print(f"[WhatsApp] Searching in-app for {phone}")
    _focus_whatsapp_window()
    if not keystroke_in_app("WhatsApp", "n", command=True):
        mcp_gui.gui_hotkey([MOD_KEY, "n"])
    time.sleep(0.6)
    _paste_text(phone)
    time.sleep(1.1)
    mcp_gui.gui_press("enter")
    time.sleep(0.7)
    mcp_gui.gui_press("escape")
    time.sleep(0.3)


def _press_whatsapp_send() -> bool:
    if keystroke_in_app("WhatsApp", "return"):
        return True
    mcp_gui.gui_press("enter")
    return True


def _send_via_whatsapp_web(phone: str, label: str, message: str) -> tuple[bool, str]:
    """Legacy fallback hook; native WhatsApp.app is the active send path."""
    return False, "The native WhatsApp application is required for confirmed sends."


def prepare_whatsapp_message(
    name_or_number: str,
    message: str = "",
    number: str = "",
    *,
    send: bool = False,
):
    """
    Open WhatsApp.app, open the allowed chat, and type a draft.
    Sending is direct when requested by the caller. Only saved individual
    contacts are allowed.
    """
    ok, phone, label, err = resolve_whatsapp_number(name_or_number, number)
    if not ok:
        return False, err

    refused = _refuse_non_sathish(name_or_number, number, phone, label)
    if refused:
        print(f"[WhatsApp] Blocked target label={label!r} phone={phone!r}")
        return False, refused

    digits = _digits(phone)
    msg_text = (message or "").strip()
    print(f"[WhatsApp] Target: {label} ({digits}), draft: '{msg_text}' send={send}")

    try:
        launched = False
        if not _whatsapp_pids() and not is_app_running("WhatsApp"):
            print("[WhatsApp] Opening macOS WhatsApp.app...")
            opened, open_msg = open_whatsapp()
            if not opened:
                return False, open_msg
            launched = True
        else:
            print("[WhatsApp] Desktop app already running.")

        print("[WhatsApp] Waiting for WhatsApp.app...")
        deadline = time.time() + (12.0 if launched else 3.0)
        while time.time() < deadline:
            if _whatsapp_pids():
                break
            time.sleep(0.15)
        _focus_whatsapp_window()
        time.sleep(0.25)

        print(f"[WhatsApp] Opening {label}'s chat...")
        _open_chat_by_protocol(digits, msg_text)
        time.sleep(1.15 if launched else 0.55)
        _focus_whatsapp_window()
        time.sleep(0.2)

        if not msg_text:
            return True, f"Opened {label}'s WhatsApp chat."

        if not send:
            return True, f"Opened {label}'s chat and put the draft in the message box."
        info = _wait_for_whatsapp_window(timeout=4.0, focus=False)
        # The URL already fills the composer. Replace it once, then send, to
        # avoid the extra screenshot/settle cycle that caused most of the lag.
        _, typed_band = _type_into_composer(msg_text, None, click_first=True, info=info)
        before_chat_text = _chat_ocr(info)

        print("[WhatsApp] Pressing Return to send...")
        _press_whatsapp_send()
        time.sleep(0.35)
        sent_band = _composer_band_image(info)
        if typed_band is not None and sent_band is not None and not _images_differ(typed_band, sent_band, threshold=2.0):
            print("[WhatsApp] Return did not clear the box; clicking send...")
            _click_send_button(info)
            time.sleep(0.35)
            sent_band = _composer_band_image(info)
        if typed_band is not None and sent_band is not None and not _images_differ(typed_band, sent_band, threshold=2.0):
            return False, (
                "WhatsApp send could not be verified: the message box still looks typed."
            )
        # Clearing the composer is not delivery proof. Check the conversation
        # itself before reporting success; this prevents false "sent" replies
        # when WhatsApp is on the wrong window or the send key is ignored.
        verified = False
        for attempt in range(3):
            time.sleep(0.45 if attempt == 0 else 0.65)
            if _message_visible_in_chat(msg_text, info, before_text=before_chat_text):
                verified = True
                break
        if not verified:
            return False, (
                "WhatsApp opened the chat, but I could not verify that the message was sent. "
                "The message may still be in the composer; please check WhatsApp."
            )
        return True, "WhatsApp message sent and verified, sir."
    except Exception as e:
        print(f"[WhatsApp] Error: {e}")
        return False, f"Failed to open {label}'s WhatsApp chat: {str(e)}"


def confirm_send_whatsapp_message():
    """Send only a request that is still pending explicit user confirmation."""
    try:
        from brain.memory import get_memory
        pending = get_memory("pending_whatsapp") or {}
        src = pending if isinstance(pending, dict) else {}
        if not src.get("awaiting_confirm"):
            return False, "There is no WhatsApp message waiting for confirmation, sir."
        message = (src or {}).get("message") or ""
        # Consume this approval before touching WhatsApp. A failed or partial send
        # must require a fresh request and confirmation before it can be retried.
        from brain.memory import save_memory
        save_memory("pending_whatsapp", None)
        if (src or {}).get("all_contacts") and message:
            return send_whatsapp_to_saved_contacts(message, confirmed=True)
        name = (src or {}).get("name") or (src or {}).get("contact") or ""
        number = (src or {}).get("number") or ""
        if not message or not (name or number):
            return False, "There is no WhatsApp message waiting to be sent, sir."
        return prepare_whatsapp_message(name, message, number, send=True)
    except Exception as e:
        return False, f"Failed to send WhatsApp message: {str(e)}"


def cancel_whatsapp_draft():
    """Cancel a queued WhatsApp send without sending keystrokes into a random window."""
    try:
        from brain.memory import save_memory
        save_memory("pending_whatsapp", None)
        save_memory("last_whatsapp_request", None)
        return True, "WhatsApp send cancelled. Nothing was sent, sir."
    except Exception as e:
        return False, f"Could not cancel the WhatsApp send: {str(e)}"


def send_whatsapp_message(name, message, number: str = "", auto_send: bool = True):
    """Open a saved WhatsApp contact and send the requested message directly."""
    ok, result = prepare_whatsapp_message(
        name,
        message,
        number=number,
        send=bool(auto_send),
    )
    if ok:
        from brain.memory import save_memory
        save_memory("last_whatsapp_request", None)
        save_memory("pending_whatsapp", None)
    return ok, result


def send_whatsapp_to_saved_contacts(message: str, *, confirmed: bool = False) -> tuple:
    """
    Send one confirmed message to every saved individual contact.
    """
    msg = (message or "").strip()
    if not msg:
        return False, "I need the message text before I send to saved contacts, sir."
    try:
        from brain.memory_store import unique_whatsapp_targets
        targets = unique_whatsapp_targets()
    except Exception:
        contacts = _load_whatsapp_contacts()
        seen = {}
        for name, phone in contacts.items():
            if phone and phone not in seen:
                seen[phone] = name
        targets = [(name, phone) for phone, name in seen.items()]

    if not targets:
        return False, "There are no saved individual WhatsApp contacts to message, sir."

    sent = []
    failed = []
    for label, phone in targets:
        print(f"[WhatsApp] All-contacts target {label} ({phone})")
        ok, result = prepare_whatsapp_message(label, msg, number=phone, send=True)
        if ok:
            sent.append(label)
        else:
            failed.append(f"{label}: {result}")
        time.sleep(1.6)

    if sent and not failed:
        return True, f"Sent to {len(sent)} saved contacts: {', '.join(sent)}, sir."
    if sent and failed:
        return False, (
            f"Sent to {len(sent)} ({', '.join(sent)}), but failed for "
            f"{len(failed)}: {'; '.join(failed)}"
        )
    return False, "Could not send to any saved contact. " + "; ".join(failed)

# ══════════════════════════════════════════════════════════════════════════════
# SEARCH & LOGGING AUTOMATION FLOWS
# ══════════════════════════════════════════════════════════════════════════════

def search_and_summarize_in_notepad(query: str):
    """
    Iteratively searches DuckDuckGo, writes findings directly to a temporary file on the desktop,
    launches TextEdit showing the file on screen, summarizes with the local Ollama model,
    closes Notepad, and deletes the temp file safely.
    """
    print(f"[SearchNotepad] Performing first search for '{query}'...")
    success1, res1 = smart_search(query)
    if not success1:
        res1 = "No initial search results fetched."
        
    detailed_query = f"{query} detailed breakdown summary"
    print(f"[SearchNotepad] Performing iterative search for '{detailed_query}'...")
    success2, res2 = smart_search(detailed_query)
    if not success2:
        res2 = "No additional details fetched."
        
    # Format the logged text nicely
    findings = (
        f"=== J.A.R.V.I.S. SECURE INTEL SEARCH LOG ===\n"
        f"TARGET: {query}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"============================================\n\n"
        f"--- PHASE 1 FINDINGS ---\n{res1}\n\n"
        f"--- PHASE 2 FINDINGS ---\n{res2}\n\n"
        f"=== END OF DATA LOG ===\n"
    )
    
    # Save directly to a text file on the Desktop so the editor opens it natively
    desktop = desktop_dir()
    filepath = os.path.join(desktop, "jarvis_research.txt")
    
    try:
        # 1. Write the research file directly
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(findings)
            
        # 2. Launch the platform text editor with the file path natively
        print("[SearchNotepad] Launching TextEdit with pre-populated file...")
        from executor.sys_platform import run_open
        run_open("-a", "TextEdit", filepath)
        
        # 3. Leave it open on screen for 3 seconds for the user to see the high-tech log!
        time.sleep(3.0)
        
        # 4. Read back contents directly from the file (100% reliable, zero focus issues!)
        with open(filepath, "r", encoding="utf-8") as f:
            notepad_content = f.read()
            
        # 5. Call the local LLM to summarize the findings
        from brain.llm_provider import complete as llm_complete
        summary_text = llm_complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are J.A.R.V.I.S. Summarize the provided Notepad research findings "
                        "elegantly and wittily in 2-3 spoken sentences. Sound helpful and dry. Do not repeat the prompt."
                    ),
                },
                {"role": "user", "content": notepad_content},
            ],
            temperature=0.3,
            max_tokens=200,
            timeout=15.0,
        )
            
    except Exception as e:
        print(f"[SearchNotepad] Error during Notepad workflow: {e}")
        summary_text = f"I retrieved the search results, sir, but encountered a minor issue preparing the summary: {e}"
    finally:
        # 6. Close the editor cleanly without saving dialogs
        try:
            osascript('tell application "TextEdit" to quit')
        except Exception:
            kill_named(["TextEdit"])
        
        # 7. Delete the temporary file from the desktop so we don't leave clutter!
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
            
    # Open default browser with Google Search so they have the browser search open as requested!
    search_google(query)
    
    return True, f"I have run an iterative search, logged it in TextEdit, and closed TextEdit as requested. Here is the summary, sir: {summary_text}"

# ══════════════════════════════════════════════════════════════════════════════
# EXISTING SYSTEM AUTOMATIONS
# ══════════════════════════════════════════════════════════════════════════════

def read_news_headlines(query: str):
    """Fetches top 3 headlines and summaries using Google News RSS."""
    import urllib.request
    import xml.etree.ElementTree as ET
    import html
    import re
    
    try:
        query = query.strip() or "top stories"
        encoded = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        
        with urllib.request.urlopen(req, timeout=7) as resp:
            xml_data = resp.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        
        output = []
        for i, item in enumerate(items[:3]):
            title = item.find("title").text.strip()
            title = re.sub(r' - [^-]+$', '', title)
            
            description = item.find("description").text or ""
            clean_desc = re.sub(r'<[^>]+>', '', description)
            clean_desc = html.unescape(clean_desc)
            summary = clean_desc.split(". ")[0].strip()
            if len(summary) > 100:
                summary = summary[:97] + "..."
                
            output.append(f"{i+1}. {title} — {summary}")
            
        if output:
            final_report = "Here are the latest headlines:\n" + "\n".join(output)
            return True, final_report
            
        raise Exception("No news found")
    except Exception as e:
        import urllib.parse
        encoded = urllib.parse.quote_plus(query + " news")
        open_url(f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en")
        return True, "Opening latest news for you."

def play_youtube(song):
    """Opens YouTube search for the song."""
    if not song or not song.strip():
        return False, "Empty song name."
    try:
        query = urllib.parse.quote_plus(song)
        url = f"https://www.youtube.com/results?search_query={query}"
        open_url(url)
        return True, f"Opened YouTube search for {song}."
    except Exception as e:
        return False, f"Failed to open YouTube: {str(e)}"

def search_google(query):
    """Opens Google search for the query."""
    if not query or not query.strip():
        return False, "Empty search query."
    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_query}"
        open_url(url)
        return True, f"Opened Google search for: {query}"
    except Exception as e:
        return False, f"Failed to open Google search: {str(e)}"

def smart_search(query: str):
    """
    Robust 3-tier search:
      1. DuckDuckGo Instant Answer API
      2. DuckDuckGo HTML regex scraping
      3. Local Ollama answer when fetch returns no useful snippets
    """
    import httpx
    import re
    from html import unescape

    if not query or not query.strip():
        return False, "Empty search query."

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    }

    # ── Tier 1: DDG Instant Answer API ──────────────────────────────────────
    try:
        api_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_html=1&skip_disambig=1"
        )
        with httpx.Client(headers=headers, timeout=6.0, follow_redirects=True) as client:
            data = client.get(api_url).json()

        abstract = data.get("AbstractText", "").strip()
        answer = data.get("Answer", "").strip()
        if abstract:
            return True, abstract
        if answer:
            return True, answer

        snippets = [
            t.get("Text", "")
            for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ]
        if snippets:
            combined = "\n".join(f"- {s}" for s in snippets[:3])
            return True, f"Based on my search, here is what I found:\n{combined}"
    except Exception as ex:
        print(f"[SmartSearch] DDG API failed: {ex}")

    # ── Tier 2: DDG HTML regex scraping ─────────────────────────────────────
    try:
        html_url = (
            "https://html.duckduckgo.com/html/?q="
            + urllib.parse.quote_plus(query)
        )
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            html_text = client.get(html_url).text

        raw_snippets = re.findall(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|span|div)>',
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        clean = [
            unescape(re.sub(r"<[^>]+>", "", s)).strip()
            for s in raw_snippets
        ]
        clean = [c for c in clean if len(c) > 20][:3]
        if clean:
            return True, "Based on my search:\n" + "\n".join(f"- {c}" for c in clean)
    except Exception as ex:
        print(f"[SmartSearch] DDG HTML scrape failed: {ex}")

    # ── Tier 3: Direct LLM answer ───────────────────────────────────────────
    from brain.llm_provider import complete as llm_complete

    try:
        answer = llm_complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are J.A.R.V.I.S. Answer the user's question concisely in 2-4 sentences. "
                        "No bullet points. No markdown. Sound like an intelligent assistant."
                    ),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=220,
            timeout=20.0,
        )
        if not answer:
            raise ValueError("empty completion")
        return True, answer
    except Exception as exc:
        print(f"[SmartSearch] LLM fallback error: {exc}")
        return False, "I wasn't able to fetch an answer right now, sir."

def play_yt_music(song):
    """Opens YT Music and attempts to play first result natively without UI simulation."""
    import urllib.request
    import urllib.parse
    import re
    if not song or not song.strip():
        return False, "Empty song name."
    try:
        query = urllib.parse.quote_plus(song)
        url = f"https://www.youtube.com/results?search_query={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            
        vid_ids = re.findall(r'"videoId":"([^"]{11})"', html)
        if vid_ids:
            first_vid = vid_ids[0]
            music_url = f"https://music.youtube.com/watch?v={first_vid}"
            open_url(music_url)
            return True, f"Started playing {song} on YouTube Music."
        else:
            music_url = f"https://music.youtube.com/search?q={query}"
            open_url(music_url)
            return True, f"Opened YouTube Music search for {song}."
    except Exception as e:
        return False, f"Failed to play on YouTube Music: {str(e)}"
