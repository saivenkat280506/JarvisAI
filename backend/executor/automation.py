"""
automation.py — Lightweight Automation Suite
===========================================
Provides simple system and browser automation without heavy dependencies.
Now features autonomous screen recording for WhatsApp messaging, and
iterative search logging and summarization via Notepad.
"""

import subprocess
import os
import re
import json
import csv
import ctypes
from ctypes import wintypes
import webbrowser
import urllib.parse
import time
from difflib import get_close_matches
import pyautogui
pyautogui.FAILSAFE = False
from pywinauto import Application, keyboard
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings

try:
    import cv2
    import numpy as np
    from mss import mss
    import threading
    HAS_RECORDING_DEPS = True
except ImportError:
    HAS_RECORDING_DEPS = False

try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False

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
            print("[ScreenRecorder] Missing dependencies (cv2, numpy, mss). Cannot record.")
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
            with mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                width = monitor["width"]
                height = monitor["height"]
                
                # Setup MP4 codec and VideoWriter
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(self.filename, fourcc, self.fps, (width, height))
                
                last_time = time.time()
                interval = 1.0 / self.fps
                
                while self.recording:
                    now = time.time()
                    elapsed = now - last_time
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
                        
                    img = np.array(sct.grab(monitor))
                    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                    out.write(frame)
                    last_time = time.time()
                    
                out.release()
        except Exception as e:
            print(f"[ScreenRecorder] Error during capture: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CORE AUTOMATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def open_browser(manual_path=None):
    """Opens the Arc browser or fallback."""
    if manual_path:
        arc_path = manual_path
    else:
        user_home = os.path.expanduser("~")
        arc_path = os.path.join(user_home, "AppData", "Local", "Programs", "Arc", "Arc.exe")
    
    if os.path.exists(arc_path):
        try:
            subprocess.Popen([arc_path], shell=False)
            return True, "Successfully opened Arc browser."
        except Exception as e:
            return False, f"Failed to open Arc browser: {str(e)}"
    
    try:
        webbrowser.open("about:blank")
        return True, "Arc browser not found. Opened default browser as fallback."
    except Exception as e:
        return False, f"Failed to open any browser: {str(e)}"

# ── WhatsApp Desktop (WhatsApp.Root / WinUI) ──────────────────────────────────
# This Store build exposes almost no UIA Edit controls. The reliable flow is:
# open app → wait until loaded → open the saved chat → clear → type → send.

_CONTACTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_contacts.json")
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_SW_RESTORE = 9
_SW_SHOW = 5
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_BROWSER_WINDOW_CLASSES = (
    "Chrome_WidgetWin_0",
    "Chrome_WidgetWin_1",
    "MozillaWindowClass",
)


def _ensure_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def _whatsapp_pids() -> set:
    pids = set()
    try:
        res = subprocess.run(
            "tasklist /fo csv /nh",
            shell=True,
            capture_output=True,
            text=True,
        )
        for row in csv.reader(res.stdout.splitlines()):
            if len(row) < 2:
                continue
            name = row[0].strip().strip('"')
            if "whatsapp" in name.lower():
                try:
                    pids.add(int(row[1].strip().strip('"')))
                except ValueError:
                    continue
    except Exception as exc:
        print(f"[WhatsApp] Could not list processes: {exc}")
    return pids


def _window_info(hwnd: int) -> dict:
    length = _user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, title_buf, length + 1)
    cls_buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, cls_buf, 256)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": int(hwnd),
        "title": title_buf.value,
        "class": cls_buf.value,
        "pid": pid.value,
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "w": rect.right - rect.left,
        "h": rect.bottom - rect.top,
        "visible": bool(_user32.IsWindowVisible(hwnd)),
        "iconic": bool(_user32.IsIconic(hwnd)),
    }


def _is_browser_whatsapp(info: dict) -> bool:
    title = (info.get("title") or "").lower()
    cls = info.get("class") or ""
    if cls in _BROWSER_WINDOW_CLASSES or cls.startswith("Chrome_WidgetWin"):
        return True
    if "whatsapp web" in title:
        return True
    if re.match(r"^\(\d+\)\s*whatsapp", title):
        return True
    return False


def _is_desktop_whatsapp_candidate(info: dict, pids: set) -> bool:
    if info["w"] < 400 or info["h"] < 300:
        return False
    if _is_browser_whatsapp(info):
        return False
    title = (info.get("title") or "").lower()
    cls = info.get("class") or ""
    if info["pid"] in pids and cls == "WinUIDesktopWin32WindowClass":
        return True
    if info["pid"] in pids and "whatsapp" in title:
        return True
    if cls == "WinUIDesktopWin32WindowClass" and "whatsapp" in title:
        return True
    if title == "whatsapp" or title.startswith("whatsapp "):
        return True
    return False


def _find_desktop_whatsapp() -> dict | None:
    pids = _whatsapp_pids()
    found = []

    def _cb(hwnd, _lparam):
        try:
            info = _window_info(hwnd)
            if _is_desktop_whatsapp_candidate(info, pids):
                found.append(info)
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    if not found:
        return None

    def _score(info):
        score = info["w"] * info["h"]
        if info["class"] == "WinUIDesktopWin32WindowClass":
            score += 10_000_000
        if info["pid"] in pids:
            score += 5_000_000
        if info["visible"]:
            score += 1_000
        return score

    return max(found, key=_score)


def _focus_hwnd(hwnd: int) -> bool:
    if _user32.GetForegroundWindow() == hwnd and not _user32.IsIconic(hwnd):
        return True
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    else:
        _user32.ShowWindow(hwnd, _SW_SHOW)
    _user32.BringWindowToTop(hwnd)
    if _user32.SetForegroundWindow(hwnd):
        time.sleep(0.25)
        return True
    # Last resort only: attach input. Do not tap Alt — that steals the composer.
    fg = _user32.GetForegroundWindow()
    fg_tid = _user32.GetWindowThreadProcessId(fg, None)
    cur_tid = _kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
    _user32.BringWindowToTop(hwnd)
    _user32.SetForegroundWindow(hwnd)
    if attached:
        _user32.AttachThreadInput(cur_tid, fg_tid, False)
    time.sleep(0.25)
    return True


def _wait_for_whatsapp_window(timeout: float = 25.0, focus: bool = True) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = _find_desktop_whatsapp()
        if info:
            if focus:
                _focus_hwnd(info["hwnd"])
                info = _window_info(info["hwnd"])
            if info["w"] >= 400 and info["h"] >= 300:
                print(
                    f"[WhatsApp] Ready: title={info['title']!r} "
                    f"class={info['class']!r} {info['w']}x{info['h']}"
                )
                return info
        time.sleep(0.4)
    raise RuntimeError("WhatsApp Desktop did not finish loading.")


def open_whatsapp():
    """Open WhatsApp Desktop, or focus it if it is already running."""
    _ensure_dpi_aware()
    if _whatsapp_pids():
        info = _find_desktop_whatsapp()
        if info:
            _focus_hwnd(info["hwnd"])
            return True, "WhatsApp is already open, sir."
    try:
        os.startfile("whatsapp:")
        return True, "Successfully opened WhatsApp."
    except Exception:
        pass
    try:
        subprocess.Popen(
            "powershell -Command \"Start-Process 'explorer.exe' "
            "'shell:AppsFolder\\\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App'\"",
            shell=True,
        )
        return True, "Successfully opened WhatsApp UWP."
    except Exception as e:
        return False, f"Failed to open WhatsApp: {str(e)}"


def _focus_whatsapp_window():
    """Find and focus the Desktop WhatsApp window. Returns True if focused."""
    try:
        info = _find_desktop_whatsapp()
        if not info:
            return False
        _focus_hwnd(info["hwnd"])
        print(f"[WhatsApp] Focused window: '{info['title']}'")
        return True
    except Exception as e:
        print(f"[WhatsApp] Could not focus WhatsApp window: {e}")
        return False


def _focus_browser_window():
    """Focus a browser window that might host WhatsApp Web. Unused by Desktop send."""
    for title_pattern in [".*WhatsApp.*Web.*", ".*Chrome.*", ".*Edge.*", ".*Arc.*", ".*Firefox.*"]:
        try:
            app = Application(backend="uia").connect(title_re=title_pattern, timeout=3)
            win = app.top_window()
            win.set_focus()
            time.sleep(0.3)
            print(f"[WhatsApp] Focused browser: '{win.window_text()}'")
            return True
        except Exception:
            continue
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


def _clipboard_get() -> str:
    if not HAS_CLIPBOARD:
        return ""
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def _clipboard_set(text: str):
    if not HAS_CLIPBOARD:
        return
    try:
        pyperclip.copy(text)
    except Exception:
        pass


def _paste_text(text: str):
    """Paste via clipboard — reliable for +phone numbers and special chars."""
    if HAS_CLIPBOARD:
        try:
            pyperclip.copy(text)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            return
        except Exception as e:
            print(f"[WhatsApp] Clipboard paste failed, typing instead: {e}")
    for ch in text:
        if ch == "+":
            pyautogui.hotkey("shift", "=")
        else:
            pyautogui.write(ch, interval=0.03)
        time.sleep(0.02)


def _open_chat_by_protocol(digits: str):
    uri = f"whatsapp://send?phone={digits}"
    print(f"[WhatsApp] Opening chat via {uri}")
    try:
        os.startfile(uri)
        return
    except Exception as exc:
        print(f"[WhatsApp] os.startfile protocol failed: {exc}")
    subprocess.Popen(f'start "" "{uri}"', shell=True)


def _composer_point():
    """Message box sits just above the taskbar in the chat pane."""
    sw, sh = pyautogui.size()
    return int(sw * 0.50), int(sh - 88)


def _send_point():
    sw, sh = pyautogui.size()
    return int(sw - 58), int(sh - 88)


def _click_composer(info: dict = None):
    x, y = _composer_point()
    print(f"[WhatsApp] Clicking composer at ({x}, {y})")
    pyautogui.click(x, y)
    time.sleep(0.25)


def _click_send_button(info: dict = None):
    x, y = _send_point()
    print(f"[WhatsApp] Clicking send at ({x}, {y})")
    pyautogui.click(x, y)
    time.sleep(0.25)


def _composer_band_image():
    sw, sh = pyautogui.size()
    left = int(sw * 0.28)
    top = int(sh - 130)
    width = int(sw * 0.71)
    height = 80
    return pyautogui.screenshot(region=(left, top, width, height))


def _images_differ(before, after, threshold: float = 1.8) -> bool:
    import numpy as np
    a = np.asarray(before, dtype=np.int16)
    b = np.asarray(after, dtype=np.int16)
    if a.shape != b.shape:
        return True
    return float(np.mean(np.abs(a - b))) > threshold


def _clear_and_type(message: str):
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.08)
    pyautogui.press("backspace")
    time.sleep(0.12)
    _paste_text(message)
    time.sleep(0.35)


def _type_into_composer(message: str, empty_band, click_first: bool = False):
    if click_first:
        _click_composer()
    _clear_and_type(message)
    typed_band = _composer_band_image()
    if _images_differ(empty_band, typed_band):
        return True, typed_band
    print("[WhatsApp] Composer pixels did not change after typing")
    return False, typed_band


def _search_chat_by_number(phone: str):
    """In-app number search if the protocol did not land in the chat composer."""
    print(f"[WhatsApp] Searching in-app for {phone}")
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.6)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    _paste_text(phone)
    time.sleep(1.3)
    pyautogui.press("enter")
    time.sleep(0.8)
    pyautogui.press("escape")
    time.sleep(0.35)


def prepare_whatsapp_message(name_or_number: str, message: str = "", number: str = ""):
    """
    Open WhatsApp, wait until it loads, open the saved contact's chat,
    clear the message box, type the message, and send it.
    """
    _ensure_dpi_aware()
    ok, phone, label, err = resolve_whatsapp_number(name_or_number, number)
    if not ok:
        return False, err

    digits = re.sub(r"\D", "", phone)
    msg_text = (message or "").strip()
    print(f"[WhatsApp] Target: {label} ({digits}), Message: '{msg_text}'")

    try:
        launched = False
        if not _whatsapp_pids():
            print("[WhatsApp] Opening Desktop app...")
            opened, open_msg = open_whatsapp()
            if not opened:
                return False, open_msg
            launched = True
        else:
            print("[WhatsApp] Desktop app already running.")

        print("[WhatsApp] Waiting until WhatsApp has loaded...")
        print(f"[WhatsApp] Screen size for clicks: {pyautogui.size()}")
        _wait_for_whatsapp_window(timeout=30.0 if launched else 10.0, focus=True)

        print(f"[WhatsApp] Loading chat for {label}...")
        _open_chat_by_protocol(digits)
        time.sleep(3.8 if launched else 3.0)
        # Do not Alt-focus after the protocol: it already focuses the composer.
        info = _wait_for_whatsapp_window(timeout=12.0, focus=False)
        time.sleep(0.4)

        if not msg_text:
            return True, f"Opened WhatsApp chat for {label}, sir."

        # Protocol focuses the composer. Do not click first — a DPI-wrong
        # click steals focus into the taskbar or chat list.
        print(f"[WhatsApp] Clearing input and typing: '{msg_text}'")
        empty_band = _composer_band_image()
        typed, typed_band = _type_into_composer(msg_text, empty_band, click_first=False)
        if not typed:
            print("[WhatsApp] Protocol focus missed the box; clicking composer...")
            typed, typed_band = _type_into_composer(msg_text, empty_band, click_first=True)
        if not typed:
            print("[WhatsApp] Chat composer was not ready; searching by number...")
            _focus_hwnd(info["hwnd"])
            _search_chat_by_number(digits)
            info = _wait_for_whatsapp_window(timeout=8.0)
            _focus_hwnd(info["hwnd"])
            time.sleep(0.5)
            empty_band = _composer_band_image()
            typed, typed_band = _type_into_composer(msg_text, empty_band, click_first=True)
        if not typed:
            return False, (
                "WhatsApp opened and the chat was requested, but the message box "
                "did not accept the text."
            )

        print("[WhatsApp] Clicking the send button...")
        _click_send_button()
        time.sleep(0.8)
        sent_band = _composer_band_image()
        if not _images_differ(typed_band, sent_band, threshold=2.5):
            print("[WhatsApp] Send button did not clear the box; pressing Enter...")
            pyautogui.press("enter")
            time.sleep(0.8)
            sent_band = _composer_band_image()
        if not _images_differ(typed_band, sent_band, threshold=2.5):
            return False, (
                "WhatsApp send could not be verified: the message box still looks typed."
            )
        return True, "WhatsApp message sent, sir."
    except Exception as e:
        print(f"[WhatsApp] Error: {e}")
        return False, f"Failed to send WhatsApp message: {str(e)}"


def confirm_send_whatsapp_message():
    """Retry the last saved WhatsApp request instead of blindly pressing Enter."""
    try:
        from brain.memory import get_memory
        pending = get_memory("pending_whatsapp") or {}
        last = get_memory("last_whatsapp_request") or {}
        src = pending if (pending or {}).get("message") else last
        message = (src or {}).get("message") or ""
        if (src or {}).get("all_contacts") and message:
            return send_whatsapp_to_saved_contacts(message)
        name = (src or {}).get("name") or (src or {}).get("contact") or ""
        number = (src or {}).get("number") or ""
        if not message or not (name or number):
            return False, "There is no WhatsApp message waiting to be sent, sir."
        return prepare_whatsapp_message(name, message, number)
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
    """WhatsApp messaging entrypoint — resolve number, open chat, type and send."""
    return prepare_whatsapp_message(name, message, number=number)


def send_whatsapp_to_saved_contacts(message: str) -> tuple:
    """
    Send one message to every WhatsApp-allowed saved individual.
    Never uses the open chat, never groups, never unsaved numbers.
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
        ok, result = prepare_whatsapp_message(label, msg, number=phone)
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
    launches Notepad showing the file on screen, reads the data, summarizes using Groq LLM,
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
    
    # Save directly to a text file on the Desktop so Notepad opens it natively
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    filepath = os.path.join(desktop, "jarvis_research.txt")
    
    try:
        # 1. Write the research file directly
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(findings)
            
        # 2. Launch Notepad with the file path natively
        print("[SearchNotepad] Launching Notepad with pre-populated file...")
        subprocess.Popen(["notepad.exe", filepath], shell=False)
        
        # 3. Leave it open on screen for 3 seconds for the user to see the high-tech log!
        time.sleep(3.0)
        
        # 4. Read back contents directly from the file (100% reliable, zero focus issues!)
        with open(filepath, "r", encoding="utf-8") as f:
            notepad_content = f.read()
            
        # 5. Call Groq LLM to summarize the findings
        import httpx
        groq_key = settings.GROQ_API_KEY
        if groq_key:
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {
                        "role": "system", 
                        "content": (
                            "You are J.A.R.V.I.S. Summarize the provided Notepad research findings "
                            "elegantly and wittily in 2-3 spoken sentences. Sound helpful and dry. Do not repeat the prompt."
                        )
                    },
                    {"role": "user", "content": notepad_content}
                ],
                "temperature": 0.3,
                "max_tokens": 200
            }
            with httpx.Client(timeout=10.0) as client:
                r = client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {groq_key}"}, json=payload)
                summary_text = r.json()["choices"][0]["message"]["content"].strip()
        else:
            summary_text = f"Here is the collected intelligence: {res1[:150]}..."
            
    except Exception as e:
        print(f"[SearchNotepad] Error during Notepad workflow: {e}")
        summary_text = f"I retrieved the search results, sir, but encountered a minor issue preparing the summary: {e}"
    finally:
        # 6. Close Notepad cleanly without saving dialogs
        os.system("taskkill /f /im notepad.exe")
        
        # 7. Delete the temporary file from the desktop so we don't leave clutter!
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
            
    # Open default browser with Google Search so they have the browser search open as requested!
    search_google(query)
    
    return True, f"I have run an iterative search, logged it in Notepad, and closed Notepad as requested. Here is the summary, sir: {summary_text}"

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
        webbrowser.open(f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en")
        return True, "Opening latest news for you."

def play_youtube(song):
    """Opens YouTube search for the song."""
    if not song or not song.strip():
        return False, "Empty song name."
    try:
        query = urllib.parse.quote_plus(song)
        url = f"https://www.youtube.com/results?search_query={query}"
        webbrowser.open(url)
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
        webbrowser.open(url)
        return True, f"Opened Google search for: {query}"
    except Exception as e:
        return False, f"Failed to open Google search: {str(e)}"

def smart_search(query: str):
    """
    Robust 3-tier search:
      1. DuckDuckGo Instant Answer API
      2. DuckDuckGo HTML regex scraping
      3. Direct Groq LLM knowledge answer (always works)
    """
    import httpx
    import re
    from html import unescape

    if not query or not query.strip():
        return False, "Empty search query."

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
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

    # ── Tier 3: Direct Groq LLM answer ──────────────────────────────────────
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return False, "I wasn't able to find an answer right now, sir."
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are J.A.R.V.I.S. Answer the user's question concisely in 2-4 sentences. "
                    "No bullet points. No markdown. Sound like an intelligent assistant."
                ),
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.3,
        "max_tokens": 220,
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload,
            )
            r.raise_for_status()
            answer = r.json()["choices"][0]["message"]["content"].strip()
            return True, answer
    except Exception as exc:
        print(f"[SmartSearch] Groq fallback error: {exc}")
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
            webbrowser.open(music_url)
            return True, f"Started playing {song} on YouTube Music."
        else:
            music_url = f"https://music.youtube.com/search?q={query}"
            webbrowser.open(music_url)
            return True, f"Opened YouTube Music search for {song}."
    except Exception as e:
        return False, f"Failed to play on YouTube Music: {str(e)}"
