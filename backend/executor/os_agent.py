                    
"""
os_agent.py — Autonomous Vision-Powered OS Agent
=================================================
Uses screenshot-based vision (Groq multimodal) to SEE the screen,
then issues precise actions to control the entire Windows desktop.

This is the core of Jarvis's autonomous control system.
"""

import pyautogui
import pywinauto
import time
import re
import os
import subprocess
import base64
import json
import mss
from io import BytesIO
from PIL import Image
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

# Safety: disable fail-safe so mouse can reach corners
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1

# ── Screenshot Engine ──────────────────────────────────────────────────────────

def capture_screenshot_b64(quality: int = 70) -> str:
    """Capture the full screen, compress, and return base64-encoded JPEG."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        # Resize for faster LLM processing while keeping enough detail
        img.thumbnail((1280, 720))
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def get_os_virtual_dom() -> str:
    """
    Scans the Desktop using pywinauto UIAutomation to create a lightweight 
    'Virtual DOM' of visible windows and their child elements.
    """
    try:
        desktop = pywinauto.Desktop(backend="uia")
        windows = desktop.windows(visible_only=True)
        
        dom_lines = []
        for i, win in enumerate(windows):
            title = win.window_text()
            if not title or "Jarvis" in title or "Taskbar" in title:
                continue
            
            rect = win.rectangle()
            if rect.width() < 50 or rect.height() < 50:
                continue
                
            dom_lines.append(f"[{i}] WINDOW: '{title}' at ({rect.left},{rect.top},{rect.right},{rect.bottom})")
            
            try:
                children = win.children()
                for c_idx, child in enumerate(children[:20]):
                    c_title = child.window_text()
                    c_type = child.friendly_class_name()
                    c_rect = child.rectangle()
                    if c_title and c_rect.width() > 10:
                        cx = (c_rect.left + c_rect.right) // 2
                        cy = (c_rect.top + c_rect.bottom) // 2
                        dom_lines.append(
                            f"    [{i}.{c_idx}] {c_type}: '{c_title}' "
                            f"center=({cx},{cy}) bounds=({c_rect.left},{c_rect.top},{c_rect.right},{c_rect.bottom})"
                        )
            except:
                pass
                
        return "\n".join(dom_lines) if dom_lines else "No major windows visible on screen."
    except Exception as e:
        return f"Error scanning UI: {e}"


# ── Action Executor ────────────────────────────────────────────────────────────

def execute_os_action(action_str: str) -> str:
    """
    Execute a command string from the LLM.
    Supported commands:
      CLICK(x, y)
      DOUBLE_CLICK(x, y)
      RIGHT_CLICK(x, y)
      TYPE("text")
      TYPE_SLOW("text")                 — types character-by-character with delay
      HOTKEY("ctrl", "t")
      PRESS("enter")
      SCROLL(x, y, clicks)             — scroll at position (negative = down)
      MOVE(x, y)                        — move mouse
      WAIT(seconds)
      OPEN_URL("https://...")           — opens URL in default browser
      RUN_CMD("command string")         — runs a shell command
      SCREENSHOT()                      — returns that a screenshot was taken
      DONE("summary of what was done")
    """
    cmd = action_str.strip()
    try:
        # ── CLICK ──
        if cmd.startswith("CLICK"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                pyautogui.click(x, y)
                return f"Clicked at ({x}, {y})"
            return "CLICK requires (x, y) coordinates"
            
        # ── DOUBLE_CLICK ──
        elif cmd.startswith("DOUBLE_CLICK"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                pyautogui.doubleClick(x, y)
                return f"Double-clicked at ({x}, {y})"
                
        # ── RIGHT_CLICK ──
        elif cmd.startswith("RIGHT_CLICK"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                pyautogui.rightClick(x, y)
                return f"Right-clicked at ({x}, {y})"
        
        # ── TYPE_SLOW (character by character for search bars etc.) ──
        elif cmd.startswith("TYPE_SLOW"):
            content = re.search(r'TYPE_SLOW\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                text = content.group(1)
                for char in text:
                    pyautogui.press(char) if len(char) == 1 else None
                    time.sleep(0.05)
                return f"Slowly typed '{text}'"
                
        # ── TYPE ──
        elif cmd.startswith("TYPE"):
            content = re.search(r'TYPE\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                text = content.group(1)
                # Use pyperclip + ctrl+v for reliability with special chars
                try:
                    import pyperclip
                    pyperclip.copy(text)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.2)
                    return f"Typed '{text}'"
                except ImportError:
                    pyautogui.write(text, interval=0.02)
                    return f"Typed '{text}'"
                    
        # ── PRESS ──
        elif cmd.startswith("PRESS"):
            key_match = re.search(r'PRESS\("(.+?)"\)', cmd)
            if key_match:
                key = key_match.group(1).lower()
                pyautogui.press(key)
                return f"Pressed '{key}'"
                
        # ── HOTKEY ──
        elif cmd.startswith("HOTKEY"):
            keys = re.findall(r'"([^"]+)"', cmd)
            if keys:
                pyautogui.hotkey(*[k.lower() for k in keys])
                return f"Pressed hotkey {keys}"
                
        # ── SCROLL ──
        elif cmd.startswith("SCROLL"):
            nums = re.findall(r'-?\d+', cmd)
            if len(nums) >= 3:
                x, y, clicks = int(nums[0]), int(nums[1]), int(nums[2])
                pyautogui.scroll(clicks, x=x, y=y)
                return f"Scrolled {clicks} clicks at ({x}, {y})"
            elif len(nums) >= 1:
                pyautogui.scroll(int(nums[0]))
                return f"Scrolled {nums[0]} clicks"
                
        # ── MOVE ──
        elif cmd.startswith("MOVE"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                pyautogui.moveTo(x, y)
                return f"Moved mouse to ({x}, {y})"
                
        # ── WAIT ──
        elif cmd.startswith("WAIT"):
            secs = re.findall(r'[\d.]+', cmd)
            wait_time = float(secs[0]) if secs else 2
            wait_time = min(wait_time, 10)  # Cap at 10 seconds
            time.sleep(wait_time)
            return f"Waited {wait_time}s"
            
        # ── OPEN_URL ──
        elif cmd.startswith("OPEN_URL"):
            url_match = re.search(r'OPEN_URL\("(.+?)"\)', cmd)
            if url_match:
                url = url_match.group(1)
                import webbrowser
                webbrowser.open(url)
                return f"Opened URL: {url}"
                
        # ── RUN_CMD ──
        elif cmd.startswith("RUN_CMD"):
            cmd_match = re.search(r'RUN_CMD\("(.+?)"\)', cmd)
            if cmd_match:
                shell_cmd = cmd_match.group(1)
                result = subprocess.run(shell_cmd, shell=True, capture_output=True, text=True, timeout=15)
                output = result.stdout.strip() or result.stderr.strip() or "Command completed"
                return f"Command result: {output[:500]}"
                
        # ── SCREENSHOT ──
        elif cmd.startswith("SCREENSHOT"):
            return "Screenshot captured — analyzing screen state"
            
        # ── DONE ──
        elif cmd.startswith("DONE"):
            msg = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
            return f"TASK_COMPLETE: {msg.group(1) if msg else 'Task finished'}"
            
        return f"Unknown command format: {cmd}"
    except Exception as e:
        return f"Action failed: {e}"


# ── Main Agent Loop ────────────────────────────────────────────────────────────

def run_os_agent(task_description: str, max_steps: int = 15, use_vision: bool = True) -> str:
    """
    Autonomous agent loop that:
    1. Takes a screenshot of the screen (vision mode) OR reads the UI tree
    2. Sends it to the LLM with the task description
    3. Receives the next action command
    4. Executes it
    5. Repeats until DONE or max_steps reached
    
    Returns the final summary string.
    """
    
    # Use vision model for screenshot-based control
    if use_vision:
        try:
            llm_vision = ChatGroq(
                model_name="llama-3.2-11b-vision-preview",
                temperature=0.1,
                max_tokens=1024,
            )
        except Exception:
            use_vision = False
    
    # Fallback text-only model
    llm_text = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0.1)
    
    system_prompt = f"""You are JARVIS, an autonomous OS-level agent with FULL CONTROL of a Windows PC.
You are performing this task: {task_description}

You can SEE the screen via screenshots and a UI element tree.
Your job: issue ONE command per turn to accomplish the task step-by-step.

AVAILABLE COMMANDS:
1.  CLICK(x, y)              — Left click at screen coordinates
2.  DOUBLE_CLICK(x, y)       — Double click at screen coordinates  
3.  RIGHT_CLICK(x, y)        — Right click at screen coordinates
4.  TYPE("text")              — Type text (uses clipboard paste - fast & reliable)
5.  TYPE_SLOW("text")         — Type text char-by-char (for search boxes that filter as you type)
6.  PRESS("key")              — Press a single key: enter, tab, escape, backspace, space, f5, etc.
7.  HOTKEY("ctrl", "t")       — Press keyboard shortcut combo
8.  SCROLL(x, y, clicks)     — Scroll at position. Negative clicks = scroll down, positive = scroll up
9.  MOVE(x, y)                — Move mouse cursor to coordinates
10. WAIT(seconds)             — Wait/pause (max 10s)
11. OPEN_URL("https://...")   — Open a URL in the default browser
12. RUN_CMD("command")        — Run a shell/PowerShell command
13. SCREENSHOT()              — Take a fresh screenshot (to check current state)
14. DONE("summary")           — Finish the task with a summary

CRITICAL RULES:
- Output ONLY the single command. No explanation, no extra text.
- For clicking buttons/links: use the CENTER coordinates from the UI tree or estimate from the screenshot.
- When typing in a browser's address bar: first CLICK the address bar, then TYPE the URL, then PRESS("enter").
- For YouTube: OPEN_URL("https://www.youtube.com/results?search_query=YOUR+SEARCH") to search directly.
- For playing a video: after search results load, CLICK on the video thumbnail.
- When writing code: open the appropriate editor first, then TYPE the code.
- If something doesn't seem to work, try an alternative approach.
- ALWAYS end with DONE("description of what was accomplished") when the task is complete.
- If the task is simple (e.g., open a URL), you can do it in 1-2 steps. Don't over-complicate.
- Use RUN_CMD for system operations like creating files, running scripts, checking status.
- Be PRECISE with coordinates. Look at the UI tree element centers for accurate targeting.

SHORTCUTS YOU KNOW:
- Win+R: Run dialog
- Ctrl+T: New browser tab
- Ctrl+L / F6: Focus address bar in browser
- Ctrl+W: Close current tab
- Alt+F4: Close window
- Win+E: Open File Explorer
- Ctrl+C/V/X: Copy/Paste/Cut
- Ctrl+S: Save
- Ctrl+Z: Undo
"""
    
    messages = [SystemMessage(content=system_prompt)]
    action_history = []
    
    for step in range(max_steps):
        time.sleep(0.8)  # Let screen settle
        
        # Build the observation context
        ui_tree = get_os_virtual_dom()
        
        if use_vision:
            try:
                screenshot_b64 = capture_screenshot_b64()
                observation_msg = HumanMessage(content=[
                    {
                        "type": "text",
                        "text": (
                            f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                            f"UI Element Tree:\n{ui_tree}\n\n"
                            f"Previous actions taken: {json.dumps(action_history[-5:]) if action_history else 'None yet'}\n\n"
                            "I've attached a screenshot of the current screen. "
                            "Analyze everything visible and issue your NEXT command."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_b64}"
                        }
                    }
                ])
                messages.append(observation_msg)
                response = llm_vision.invoke(messages)
            except Exception as e:
                 # Create a copy of messages and sanitize for text-only model
                sanitized_messages = []
                for m in messages:
                    if isinstance(m, HumanMessage) and isinstance(m.content, list):
                        # Extract text part
                        text_content = next((item["text"] for item in m.content if item.get("type") == "text"), "")
                        sanitized_messages.append(HumanMessage(content=f"[Screenshot Data Stripped] {text_content}"))
                    else:
                        sanitized_messages.append(m)
                
                observation_msg = HumanMessage(content=(
                    f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                    f"UI Element Tree:\n{ui_tree}\n\n"
                    f"Previous actions: {json.dumps(action_history[-5:]) if action_history else 'None'}\n\n"
                    "Issue your next command."
                ))
                sanitized_messages.append(observation_msg)
                response = llm_text.invoke(sanitized_messages)
        else:
            observation_msg = HumanMessage(content=(
                f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                f"UI Element Tree:\n{ui_tree}\n\n"
                f"Previous actions: {json.dumps(action_history[-5:]) if action_history else 'None'}\n\n"
                "Issue your next command."
            ))
            messages.append(observation_msg)
            response = llm_text.invoke(messages)
        
        cmd = response.content.strip()
        # Clean up any markdown or extra text
        cmd = cmd.replace("```", "").strip()
        # Take only the first line if multiple
        if "\n" in cmd:
            cmd = cmd.split("\n")[0].strip()
        
        messages.append(response)
        
        print(f"[OS Agent] Step {step + 1}: {cmd}")
        
        # Check for DONE
        if cmd.startswith("DONE"):
            msg = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
            summary = msg.group(1) if msg else "Task completed"
            return f"SUCCESS: {summary}"
        
        # Execute the action
        result = execute_os_action(cmd)
        action_history.append({"step": step + 1, "command": cmd, "result": result})
        
        # Check if execute_os_action returned a TASK_COMPLETE 
        if "TASK_COMPLETE" in result:
            return result.replace("TASK_COMPLETE: ", "SUCCESS: ")
        
        # Feed result back
        messages.append(HumanMessage(content=f"Action result: {result}"))
        
        print(f"[OS Agent] Result: {result}")
    
    return "Task ended after reaching maximum steps. Partial progress may have been made."
