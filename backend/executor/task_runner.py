"""
task_runner.py — Expanded Task Executor
=======================================
Handles all skill executions dispatched from the agent graph.
Now supports: YouTube, code writing, system info, screenshots, 
app management, web search, WhatsApp, file ops, and more.
"""

import os
import shutil
import subprocess
import time
import threading
import webbrowser
import urllib.parse
import urllib.request
import winreg
import xml.etree.ElementTree as ET
import socket
import re
import json
import asyncio
from executor.open_app import get_app_path

# --- 3rd Party Dependencies ---
try:
    import pyautogui
    from PIL import Image
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    from pywinauto import Application
except ImportError:
    Application = None

# --- Configuration & Flags ---
OLLAMA_ENABLED = False

def is_ollama_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1):
            return True
    except OSError:
        return False

# Initial Check
if is_ollama_running():
    print("[JARVIS] Ollama detected on port 11434. Local fallback enabled.")
    OLLAMA_ENABLED = True
else:
    print("[JARVIS] Ollama not detected. Using cloud-only (Groq) brain.")

# Track context
_last_search_query: str = ""

# ══════════════════════════════════════════════════════════════════════════════
# 1. VISION VERIFICATION (Self-Correction Logic)
# ══════════════════════════════════════════════════════════════════════════════

def verify_via_screenshot(target_text: str) -> bool:
    """
    Second-order fallback verification. If UI Tree scanning misses the bubble,
    we take a raw screenshot of the chat area and use OCR to find the text.
    """
    if not pyautogui or not pytesseract:
        return False
    
    try:
        screenshot = pyautogui.screenshot(region=(400, 200, 1500, 800))
        ocr_text = pytesseract.image_to_string(screenshot)
        clean_target = target_text.lower().strip()
        clean_ocr = ocr_text.lower().strip()
        return clean_target in clean_ocr
    except Exception as e:
        print(f"[Vision] Verification Error: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# 2. CORE AUTOMATION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def type_text(text: str) -> str:
    if not keyboard: return "ERROR: keyboard module is unavailable."
    if not text: return "No text provided to type."
    time.sleep(0.5)
    keyboard.write(text, delay=0.02)
    return f"SUCCESS: Typed '{text}'"

def press_key(key: str) -> str:
    """Press a keyboard key or hotkey combo."""
    if not keyboard: return "ERROR: keyboard module is unavailable."
    try:
        if "+" in key:
            # It's a hotkey combo like "ctrl+c"
            keyboard.send(key)
        else:
            keyboard.send(key)
        return f"SUCCESS: Pressed '{key}'"
    except Exception as e:
        return f"ERROR: Failed to press key: {e}"

async def search_web(query: str, resolved_browser_path: str = "") -> str:
    from executor.automation import search_and_summarize_in_notepad
    success, result = await asyncio.to_thread(search_and_summarize_in_notepad, query)
    return result

def play_youtube(query: str) -> str:
    """Search and play a YouTube video directly."""
    if not query:
        webbrowser.open("https://www.youtube.com")
        return "SUCCESS: Opened YouTube homepage"
    
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    webbrowser.open(search_url)
    
    # Give browser time to open, then use OS agent to click first result
    time.sleep(3)
    
    # Try to auto-click the first video result
    try:
        if pyautogui:
            # After YouTube search loads, the first video is typically in the center-upper area
            # We'll use the OS agent for the clicking part
            from executor.os_agent import run_os_agent
            result = run_os_agent(
                f"YouTube search results are showing for '{query}'. Click on the FIRST video thumbnail to play it. The video thumbnails are the large rectangular images on the left side of each result.",
                max_steps=3,
                use_vision=True
            )
            if "SUCCESS" in result:
                return f"SUCCESS: Playing '{query}' on YouTube"
    except Exception as e:
        print(f"[YouTube] Auto-play failed: {e}")
    
    return f"SUCCESS: Searched YouTube for '{query}'. Results are showing."

def read_news_headlines(query: str) -> str:
    try:
        query = query.strip() or "top stories"
        encoded = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        headlines = [item.find("title").text.strip() for item in items[:3] if item.find("title") is not None]
        cleaned_headlines = []
        for h in headlines:
            cleaned_h = re.sub(r' - [^-]+$', '', h)
            cleaned_headlines.append(cleaned_h)
        if cleaned_headlines: return f"FOUND headlines for '{query}':\n" + "\n".join(f"- {h}" for h in cleaned_headlines)
        return f"EMPTY: No news found for '{query}'."
    except Exception as e:
        return f"ERROR: Could not fetch news: {e}"

async def send_whatsapp(contact: str, message: str) -> str:
    if not contact: return "ERROR: No contact specified."
    from executor.automation import send_whatsapp_message
    import asyncio
    success, msg = await asyncio.to_thread(send_whatsapp_message, contact, message)
    return msg

def write_code(code: str, filename: str = "", language: str = "python") -> str:
    """Write code to a file and optionally open it in VS Code."""
    if not code:
        return "ERROR: No code provided to write."
    
    # Determine file extension
    ext_map = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "html": ".html", "css": ".css", "java": ".java",
        "cpp": ".cpp", "c": ".c", "rust": ".rs", "go": ".go",
        "dart": ".dart", "ruby": ".rb", "php": ".php",
    }
    ext = ext_map.get(language.lower(), ".txt")
    
    if not filename:
        filename = f"jarvis_code{ext}"
    elif not os.path.splitext(filename)[1]:
        filename += ext
    
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    filepath = os.path.join(desktop, filename)
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)
        
        # Try to open in VS Code
        try:
            subprocess.Popen(f'code "{filepath}"', shell=True)
        except:
            # Fallback: open in notepad
            subprocess.Popen(f'notepad "{filepath}"', shell=True)
        
        return f"SUCCESS: Code written to '{filename}' on Desktop and opened in editor."
    except Exception as e:
        return f"ERROR: Failed to write code: {e}"

def run_terminal_command(command: str) -> str:
    """Execute a terminal/PowerShell command and return output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip() or "Command completed successfully"
        return f"SUCCESS: {output[:1000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 30 seconds"
    except Exception as e:
        return f"ERROR: {e}"

def get_system_info() -> str:
    """Get system information."""
    import platform
    try:
        info = {
            "os": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "user": os.environ.get("USERNAME", "Unknown"),
            "hostname": platform.node(),
        }
        return f"SUCCESS: {json.dumps(info)}"
    except Exception as e:
        return f"ERROR: {e}"

def take_screenshot() -> str:
    """Take a screenshot and save to desktop."""
    try:
        from vision.capture import capture_screen_base64
        import base64
        b64 = capture_screen_base64(draw_boxes=False)
        # Save to desktop
        screenshot_path = os.path.join(os.environ["USERPROFILE"], "Desktop", "jarvis_screenshot.png")
        img_data = base64.b64decode(b64)
        with open(screenshot_path, 'wb') as f:
            f.write(img_data)
        return f"SUCCESS: Screenshot saved to Desktop as 'jarvis_screenshot.png'"
    except Exception as e:
        return f"ERROR: {e}"

def focus_window(app_name: str) -> str:
    """Bring an application window to the foreground."""
    try:
        if not Application:
            return "ERROR: pywinauto not available"
        app = Application(backend="uia").connect(title_re=f".*{app_name}.*", timeout=5)
        win = app.top_window()
        win.set_focus()
        return f"SUCCESS: Focused window for '{app_name}'"
    except Exception as e:
        return f"ERROR: Could not focus '{app_name}': {e}"

def click_ui_element(app_name: str, element_name: str) -> str:
    """Click a UI element inside a windows app by name."""
    try:
        if not Application:
            return "ERROR: pywinauto not available"
        app = Application(backend="uia").connect(title_re=f".*{app_name}.*", timeout=5)
        win = app.top_window()
        win.set_focus()
        time.sleep(0.5)
        
        element = win.child_window(title=element_name, found_index=0)
        if element.exists():
            element.click_input()
            return f"SUCCESS: Clicked '{element_name}' in '{app_name}'"
        return f"ERROR: Element '{element_name}' not found in '{app_name}'"
    except Exception as e:
        return f"ERROR: {e}"

def set_reminder(minutes: int, message: str) -> str:
    """Set a reminder that triggers after N minutes."""
    def _reminder_thread():
        time.sleep(minutes * 60)
        try:
            # Show a Windows toast notification
            from tts.pocket_tts import speak
            speak(f"Reminder: {message}")
            # Also show a system notification
            subprocess.run(
                f'powershell -Command "Add-Type -AssemblyName System.Windows.Forms; '
                f'[System.Windows.Forms.MessageBox]::Show(\'{message}\', \'JARVIS Reminder\')"',
                shell=True
            )
        except Exception as e:
            print(f"[Reminder] Error: {e}")
    
    threading.Thread(target=_reminder_thread, daemon=True).start()
    return f"SUCCESS: Reminder set for {minutes} minutes from now: '{message}'"


# ══════════════════════════════════════════════════════════════════════════════
# 3. AUTONOMOUS WEB AGENT (Browser-Use)
# ══════════════════════════════════════════════════════════════════════════════

async def run_autonomous_web_task(task_description: str) -> str:
    """
    Deep-vision web agent using browser-use.
    """
    try:
        from langchain_groq import ChatGroq
        from browser_use import Agent
        
        llm = ChatGroq(model_name="llama-3.1-8b-instant", temperature=0.1)
        agent = Agent(task=task_description, llm=llm)
        
        history = await agent.run()
        
        final_result = "Task completed successfully."
        if hasattr(history, 'final_result') and history.final_result():
            final_result = history.final_result()
        elif hasattr(history, 'history') and len(history.history) > 0:
            last_event = history.history[-1]
            if hasattr(last_event, 'text'): final_result = last_event.text
            
        return f"SUCCESS: Autonomous Web Agent reports: {final_result}"
    except Exception as e:
        return f"WEB_AGENT_ERROR: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. HELPER: App Resolver
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_app_path(app_name: str) -> str:
    # 1. Check our new registry first
    discovered = get_app_path(app_name)
    if discovered:
        return discovered
    
    # 2. Existing hardcoded logic and system fallbacks
    orig_name = app_name.lower().strip()
    app_name = orig_name.replace(".exe", "")
    aliases = {
        "browser": "chrome", "google": "chrome", "calc": "ms-calculator:", 
        "calculator": "ms-calculator:", "word": "winword", "powerpoint": "powerpnt",
        "terminal": "wt", "cmd": "cmd", "explorer": "explorer", "vscode": "code",
        "vs code": "code", "visual studio code": "code",
        "arc browser": "arc", "microsoft edge": "msedge", "edge": "msedge",
        "settings": "ms-settings:", "camera": "microsoft.windows.camera:",
        "maps": "bingmaps:", "calendar": "outlookcal:", "mail": "outlookmail:",
        "spotify": "spotify", "discord": "discord", "telegram": "telegram",
        "notepad": "notepad", "paint": "mspaint", "snipping tool": "snippingtool",
        "task manager": "taskmgr", "control panel": "control",
        "file explorer": "explorer", "files": "explorer",
    }
    target = aliases.get(app_name, app_name)
    if target.endswith(":"): return target
    
    # Check if target is already a full path
    if os.path.exists(target):
        return target
        
    # Check shutil.which
    which_path = shutil.which(target) or shutil.which(f"{target}.exe")
    return which_path if which_path else target


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAIN EXECUTOR ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def execute(groq_output: dict) -> str:
    action_type = groq_output.get("action", "")
    if action_type in ["respond", "ask"]: return ""
    skill = groq_output.get("skill", "")
    params = groq_output.get("parameters", {})
    if not skill or skill == "none": return "No action."

    try:
        # ── App Control ──
        if skill == "open_app":
            app = (params.get("app_name") or params.get("app") or "").lower().strip()
            path = _resolve_app_path(app)
            if path.endswith(":"): subprocess.Popen(f"start {path}", shell=True)
            else: subprocess.Popen(f'start "" "{path}"', shell=True)
            return f"Opened {app}."

        elif skill == "close_app":
            app = (params.get("app_name") or params.get("app") or "").lower().strip()
            os.system(f"taskkill /f /im {app}.exe")
            return f"Closed {app}."

        elif skill == "focus_window":
            app = (params.get("app_name") or params.get("app") or "").strip()
            return focus_window(app)

        elif skill == "click_ui_element":
            app = (params.get("app_name") or "").strip()
            element = (params.get("element_name") or "").strip()
            return click_ui_element(app, element)

        # ── Web & News ──
        elif skill in ("web_search", "web_task", "research_topic"):
            global _last_search_query
            query = params.get("query") or params.get("topic") or ""
            _last_search_query = query if query else _last_search_query
            
            # Note: run_autonomous_web_task should also be awaited if it's async
            if "research" in skill or params.get("autonomous", False):
                from executor.os_agent import run_os_agent
                return await asyncio.to_thread(run_os_agent, f"Search the web for '{query}' and summarize the key findings.")

            browser = params.get("browser", "").lower().strip()
            resolved = _resolve_app_path(browser) if browser else ""
            return await search_web(query, resolved)

        elif skill in ("read_headlines", "fetch_news", "read_news"):
            query = params.get("query") or params.get("topic") or _last_search_query or "top news"
            return read_news_headlines(query)

        # ── YouTube ──
        elif skill in ("play_youtube", "youtube_search", "youtube"):
            query = params.get("query") or params.get("video") or params.get("song") or ""
            return play_youtube(query)

        # ── Messaging ──
        elif skill in ("send_whatsapp", "whatsapp_send_message"):
            contact, msg = params.get("contact", ""), params.get("message", "")
            if msg:
                return await send_whatsapp(contact, msg)
            return "ERROR: Message required."

        elif skill == "whatsapp_search_contact":
            return await send_whatsapp(params.get("contact", ""), "")

        # ── System ──
        elif skill == "volume_control":
            if not keyboard: return "Keyboard unavailable."
            act = params.get("action", "up")
            if act in ("mute", "unmute"): keyboard.send("volume mute")
            elif act == "down": [keyboard.send("volume down") for _ in range(3)]
            else: [keyboard.send("volume up") for _ in range(3)]
            return "Volume updated."

        elif skill == "system_info":
            return get_system_info()

        elif skill == "screenshot":
            return take_screenshot()

        elif skill == "shutdown":
            mode = params.get("mode", "shutdown")
            if mode == "restart":
                os.system("shutdown /r /t 5")
                return "SUCCESS: Restarting in 5 seconds."
            elif mode == "sleep":
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
                return "SUCCESS: Going to sleep."
            else:
                os.system("shutdown /s /t 5")
                return "SUCCESS: Shutting down in 5 seconds."

        # ── Typing & Input ──
        elif skill == "type_text":
            return type_text(params.get("text", ""))

        elif skill == "press_key":
            key = params.get("key", "")
            return press_key(key)

        # ── Code Writing ──
        elif skill in ("write_code", "create_code"):
            code = params.get("code", "")
            filename = params.get("filename", params.get("file_name", ""))
            language = params.get("language", "python")
            return write_code(code, filename, language)

        elif skill in ("run_code", "run_terminal", "run_command"):
            command = params.get("command", params.get("query", ""))
            return run_terminal_command(command)

        # ── File System ──
        elif skill == "create_folder":
            name = params.get("folder_name") or params.get("name") or "New Folder"
            path = params.get("path") or os.path.join(os.environ["USERPROFILE"], "Desktop")
            os.makedirs(os.path.join(path, name), exist_ok=True)
            return f"SUCCESS: Created folder '{name}'."

        elif skill == "create_file":
            name = params.get("file_name") or params.get("name") or "new_file.txt"
            path = params.get("path") or os.path.join(os.environ["USERPROFILE"], "Desktop")
            content = params.get("content", "")
            filepath = os.path.join(path, name)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"SUCCESS: Created file '{name}'."

        # ── Reminders ──
        elif skill == "set_reminder":
            minutes = int(params.get("time", params.get("minutes", 5)))
            message = params.get("message", "Time's up!")
            return set_reminder(minutes, message)

        # ── Autonomous OS Agent (catch-all for complex tasks) ──
        elif skill == "autonomous_task":
            task = params.get("task", params.get("description", ""))
            from executor.os_agent import run_os_agent
            return run_os_agent(task)

        return f"Skill '{skill}' not implemented."

    except Exception as e:
        return f"Error: {e}"
