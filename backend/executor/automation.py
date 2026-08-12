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
import webbrowser
import urllib.parse
import time
import pyautogui
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

def open_whatsapp():
    """Opens WhatsApp Desktop."""
    user_home = os.path.expanduser("~")
    whatsapp_path = os.path.join(user_home, "AppData", "Local", "WhatsApp", "WhatsApp.exe")
    
    if os.path.exists(whatsapp_path):
        try:
            subprocess.Popen([whatsapp_path], shell=False)
            return True, "Successfully opened WhatsApp Desktop."
        except Exception as e:
            return False, f"Failed to open WhatsApp Desktop: {str(e)}"
    
    # Fallback to protocol
    try:
        subprocess.run("start whatsapp:", shell=True, check=True)
        return True, "Successfully opened WhatsApp via protocol."
    except Exception as e:
        return False, f"Failed to open WhatsApp: {str(e)}"


# ── WhatsApp helpers: always search by PHONE NUMBER (never name) ──────────────

_CONTACTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_contacts.json")


def _load_whatsapp_contacts() -> dict:
    """name(lower) -> E.164-ish phone number string."""
    try:
        if os.path.exists(_CONTACTS_PATH):
            with open(_CONTACTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if v}
    except Exception as e:
        print(f"[WhatsApp] Failed to load contacts: {e}")
    return {}


def normalize_phone_number(value: str) -> str:
    """Keep leading + and digits only. Strip spaces/dashes/parens."""
    if not value:
        return ""
    raw = str(value).strip()
    # Already mostly numeric?
    has_plus = raw.lstrip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return ("+" + digits) if has_plus or len(digits) >= 10 else digits


def looks_like_phone_number(value: str) -> bool:
    if not value:
        return False
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 8


def resolve_whatsapp_number(name_or_number: str, number: str = "") -> tuple:
    """
    Resolve a WhatsApp search target to a phone number.
    ALWAYS prefers an explicit number over a contact name to avoid same-name confusion.

    Returns: (ok: bool, phone: str, label: str, error: str)
    """
    explicit = normalize_phone_number(number) if number else ""
    if explicit and looks_like_phone_number(explicit):
        label = (name_or_number or explicit).strip() or explicit
        return True, explicit, label, ""

    target = (name_or_number or "").strip()
    if not target:
        return False, "", "", "No contact or phone number provided."

    if looks_like_phone_number(target):
        phone = normalize_phone_number(target)
        return True, phone, phone, ""

    contacts = _load_whatsapp_contacts()
    key = target.lower()
    if key in contacts:
        phone = normalize_phone_number(contacts[key])
        if looks_like_phone_number(phone):
            return True, phone, target, ""

    # Partial name match (unique only)
    matches = [(n, p) for n, p in contacts.items() if key in n or n in key]
    if len(matches) == 1:
        phone = normalize_phone_number(matches[0][1])
        if looks_like_phone_number(phone):
            return True, phone, matches[0][0], ""
    if len(matches) > 1:
        names = ", ".join(m[0] for m in matches[:5])
        return False, "", target, (
            f"Multiple contacts match '{target}' ({names}). "
            "Please provide the full phone number so I search by number only."
        )

    return False, "", target, (
        f"I do not have a saved phone number for '{target}'. "
        "Please give the full number (with country code) so I can search WhatsApp by number."
    )


def _paste_text(text: str):
    """Paste via clipboard — reliable for +phone numbers and special chars."""
    if HAS_CLIPBOARD:
        try:
            pyperclip.copy(text)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            return
        except Exception as e:
            print(f"[WhatsApp] Clipboard paste failed, typing fallback: {e}")
    # Fallback: type digit-by-digit (pyautogui.write struggles with '+')
    for ch in text:
        if ch == "+":
            pyautogui.hotkey("shift", "=")
        else:
            pyautogui.write(ch, interval=0.03)
        time.sleep(0.02)


def _focus_whatsapp_search_and_type_number(phone: str):
    """
    Open/focus WhatsApp search bar and enter the PHONE NUMBER only.
    Never types a contact name — avoids same-name collisions.
    """
    # Ctrl+F opens search in WhatsApp Desktop
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.8)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.2)
    pyautogui.press("backspace")
    time.sleep(0.2)
    _paste_text(phone)
    time.sleep(2.0)
    # Open first search result (the number match)
    pyautogui.press("enter")
    time.sleep(1.5)
    # Ensure message compose box is focused (Tab / click chat area)
    pyautogui.press("escape")  # close search panel if still open
    time.sleep(0.4)
    # Click roughly into message input area (bottom center of primary screen)
    try:
        sw, sh = pyautogui.size()
        pyautogui.click(sw // 2, int(sh * 0.92))
        time.sleep(0.4)
    except Exception:
        pass


def prepare_whatsapp_message(name_or_number: str, message: str, number: str = ""):
    """
    Open WhatsApp → search by PHONE NUMBER → open chat → type message.
    Does NOT press Enter / send. Caller must confirm before send.
    """
    ok, phone, label, err = resolve_whatsapp_number(name_or_number, number)
    if not ok:
        return False, err

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    recordings_dir = os.path.join(project_dir, "whatsapp recordings")
    os.makedirs(recordings_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (label or phone))
    video_path = os.path.join(recordings_dir, f"draft_message_to_{safe}.mp4")

    recorder = ScreenRecorder(video_path, fps=8.0)
    recorder.start()
    success, msg = False, ""
    try:
        open_whatsapp()
        time.sleep(4)

        print(f"[WhatsApp] Searching by NUMBER only: {phone} (label={label})")
        _focus_whatsapp_search_and_type_number(phone)

        if message and message.strip():
            _paste_text(message.strip())
            time.sleep(0.4)
            # Intentionally do NOT press Enter — wait for user confirmation
            success, msg = True, (
                f"Draft ready for {label} ({phone}). "
                f"Message typed in the chat: \"{message.strip()}\". "
                "Should I send it? Say yes, ok, or send to deliver — or no to cancel."
            )
        else:
            success, msg = True, (
                f"Opened WhatsApp chat for {label} by number {phone}. "
                "No message body was provided."
            )
    except Exception as e:
        success, msg = False, f"Failed to prepare WhatsApp message: {str(e)}"
    finally:
        time.sleep(1.5)
        recorder.stop()

    return success, msg


def confirm_send_whatsapp_message():
    """Press Enter in the focused WhatsApp compose box to send the drafted message."""
    try:
        open_whatsapp()
        time.sleep(1.0)
        # Refocus message box
        try:
            sw, sh = pyautogui.size()
            pyautogui.click(sw // 2, int(sh * 0.92))
            time.sleep(0.3)
        except Exception:
            pass
        pyautogui.press("enter")
        time.sleep(0.5)
        return True, "Message sent, sir."
    except Exception as e:
        return False, f"Failed to send WhatsApp message: {str(e)}"


def cancel_whatsapp_draft():
    """Clear the drafted message text in the compose box without sending."""
    try:
        open_whatsapp()
        time.sleep(0.8)
        try:
            sw, sh = pyautogui.size()
            pyautogui.click(sw // 2, int(sh * 0.92))
            time.sleep(0.3)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.15)
        pyautogui.press("backspace")
        return True, "Draft cancelled. Message was not sent, sir."
    except Exception as e:
        return False, f"Could not clear draft: {str(e)}"


def send_whatsapp_message(name, message, number: str = "", auto_send: bool = False):
    """
    WhatsApp messaging entrypoint.

    Default (auto_send=False): search by NUMBER, open chat, type message, DO NOT send.
    auto_send=True: legacy immediate send (discouraged).
    """
    if not auto_send:
        return prepare_whatsapp_message(name, message, number=number)

    ok, phone, label, err = resolve_whatsapp_number(name, number)
    if not ok:
        return False, err

    success, msg = prepare_whatsapp_message(label, message, number=phone)
    if not success:
        return False, msg
    return confirm_send_whatsapp_message()

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
            return True, f"Here's what I found: {abstract}"
        if answer:
            return True, f"Here's what I found: {answer}"

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
