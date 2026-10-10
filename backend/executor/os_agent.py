                    
"""
os_agent.py — Autonomous text-first OS agent
============================================
Uses accessibility, browser MCP snapshots, and audited system tools first.
Local screenshot vision is requested only when text observations are insufficient.

This is the core of Jarvis's autonomous control system.
"""

import time
import re
import json
from langchain_core.messages import SystemMessage, HumanMessage
from brain.llm_provider import ChatLLM

from executor.sys_platform import IS_MAC, MOD_KEY, list_visible_windows
from mcp_clients import macos as mcp_gui
from mcp_clients import shell as mcp_shell

# ── Screenshot Engine ──────────────────────────────────────────────────────────

def capture_screenshot_b64(quality: int = 70) -> str:
    """Capture a screen image through the audited macOS MCP screenshot tool."""
    size = mcp_gui.gui_screen_size()
    width = max(1, min(int(size.get("width", 0)), 8192))
    height = max(1, min(int(size.get("height", 0)), 8192))
    if width < 2 or height < 2:
        raise RuntimeError("Could not read screen dimensions through macOS MCP.")
    return mcp_gui.gui_screenshot_region(0, 0, width, height)


def _make_vision_llm():
    """Vision-tier client — the chat model rejects images (HTTP 400)."""
    from brain.llm_provider import VISION_MODEL
    return ChatLLM(temperature=0.1, max_tokens=1024, model=VISION_MODEL)


def get_os_virtual_dom() -> str:
    """Scan visible macOS windows via System Events."""
    return list_visible_windows()


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
      BROWSER("snapshot", {})           — inspect the active browser through MCP
      BROWSER("navigate", {"url": "..."})
      NEED_VISION("reason")              — request one screenshot when text context is insufficient
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
                mcp_gui.gui_click(x, y)
                return f"Clicked at ({x}, {y})"
            return "CLICK requires (x, y) coordinates"
            
        # ── DOUBLE_CLICK ──
        elif cmd.startswith("DOUBLE_CLICK"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                mcp_gui.gui_double_click(x, y)
                return f"Double-clicked at ({x}, {y})"
                
        # ── RIGHT_CLICK ──
        elif cmd.startswith("RIGHT_CLICK"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                mcp_gui.gui_right_click(x, y)
                return f"Right-clicked at ({x}, {y})"
        
        # ── TYPE_SLOW (character by character for search bars etc.) ──
        elif cmd.startswith("TYPE_SLOW"):
            content = re.search(r'TYPE_SLOW\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                text = content.group(1)
                for char in text:
                    if len(char) == 1:
                        mcp_gui.gui_press(char)
                    time.sleep(0.05)
                return f"Slowly typed '{text}'"
                
        # ── TYPE ──
        elif cmd.startswith("TYPE"):
            content = re.search(r'TYPE\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                text = content.group(1)
                # Clipboard + Command+V for reliability with special chars (MCP)
                try:
                    mcp_gui.set_clipboard(text)
                    mcp_gui.gui_hotkey([MOD_KEY, "v"])
                    time.sleep(0.2)
                except Exception:
                    mcp_gui.gui_type(text, interval=0.02)
                return f"Typed '{text}'"
                    
        # ── PRESS ──
        elif cmd.startswith("PRESS"):
            key_match = re.search(r'PRESS\("(.+?)"\)', cmd)
            if key_match:
                key = key_match.group(1).lower()
                mcp_gui.gui_press(key)
                return f"Pressed '{key}'"
                
        # ── HOTKEY ──
        elif cmd.startswith("HOTKEY"):
            keys = re.findall(r'"([^"]+)"', cmd)
            if keys:
                mapped = []
                for k in keys:
                    key = k.lower()
                    if IS_MAC and key in ("ctrl", "control"):
                        key = "command"
                    if IS_MAC and key == "alt":
                        key = "option"
                    if IS_MAC and key == "win":
                        key = "command"
                    mapped.append(key)
                mcp_gui.gui_hotkey(mapped)
                return f"Pressed hotkey {mapped}"
                
        # ── SCROLL ──
        elif cmd.startswith("SCROLL"):
            nums = re.findall(r'-?\d+', cmd)
            if len(nums) >= 3:
                x, y, clicks = int(nums[0]), int(nums[1]), int(nums[2])
                mcp_gui.gui_scroll(clicks, x=x, y=y)
                return f"Scrolled {clicks} clicks at ({x}, {y})"
            elif len(nums) >= 1:
                mcp_gui.gui_scroll(int(nums[0]))
                return f"Scrolled {nums[0]} clicks"
                
        # ── MOVE ──
        elif cmd.startswith("MOVE"):
            coords = re.findall(r'\d+', cmd)
            if len(coords) >= 2:
                x, y = int(coords[0]), int(coords[1])
                mcp_gui.gui_move(x, y)
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
                if mcp_gui.open_uri(url):
                    return f"Opened URL: {url}"
                return f"Failed to open URL: {url}"
                
        # ── RUN_CMD ──
        elif cmd.startswith("RUN_CMD"):
            cmd_match = re.search(r'RUN_CMD\("(.+?)"\)', cmd)
            if cmd_match:
                shell_cmd = cmd_match.group(1)
                result = mcp_shell.run_command(shell_cmd, timeout=15)
                output = (result.get("stdout") or "").strip() or (result.get("stderr") or "").strip() or "Command completed"
                return f"Command result: {output[:500]}"

        # ── BROWSER MCP ──
        elif cmd.startswith("BROWSER"):
            match = re.search(r'BROWSER\(\s*(["\'])(.*?)\1\s*,\s*(\{.*\})\s*\)', cmd, re.DOTALL)
            if not match:
                return 'BROWSER requires BROWSER("action", {"key": "value"})'
            action = match.group(2)
            try:
                params = json.loads(match.group(3))
            except json.JSONDecodeError as exc:
                return f"BROWSER parameters must be JSON: {exc}"
            if not isinstance(params, dict):
                return "BROWSER parameters must be a JSON object"
            from mcp_clients import browser as mcp_browser
            result = mcp_browser.command(action, params)
            if result.get("ok") is False:
                return f"BROWSER_ERROR: {result.get('error') or result.get('message') or 'Browser action failed'}"
            return f"BROWSER_RESULT: {json.dumps(result, ensure_ascii=False)[:1200]}"

        # ── NEED_VISION ──
        elif cmd.startswith("NEED_VISION"):
            reason = re.search(r'NEED_VISION\(["\'](.*?)["\']\)', cmd, re.DOTALL)
            return f"VISION_REQUESTED: {reason.group(1) if reason else 'Text context was insufficient'}"
                
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

def run_os_agent(task_description: str, max_steps: int = 15, use_vision: bool = False) -> str:
    """
    Autonomous agent loop that:
    1. Reads the UI tree and browser snapshot data
    2. Requests a screenshot only when text observations are insufficient
    3. Receives the next action command
    4. Executes it
    5. Repeats until DONE or max_steps reached
    
    Returns the final summary string.
    """

    # A screenshot is opt-in per step. Keep the default observation text-first.
    llm_vision = None
    if use_vision:
        try:
            llm_vision = _make_vision_llm()
        except Exception:
            use_vision = False
    
    # Fallback text-only model
    llm_text = ChatLLM(temperature=0.1)
    
    host_os = "macOS"
    system_prompt = f"""You are JARVIS, an autonomous OS-level agent with FULL CONTROL of a {host_os} machine.
You are performing this task: {task_description}

Start with text observations: the accessibility/UI tree, browser MCP snapshots,
and shell/AppleScript MCP results. Request a screenshot with
NEED_VISION("what you need to inspect") only when those sources cannot answer
the next step. Your job: issue ONE command per turn.

AVAILABLE COMMANDS:
1.  CLICK(x, y)              — Left click at screen coordinates
2.  DOUBLE_CLICK(x, y)       — Double click at screen coordinates  
3.  RIGHT_CLICK(x, y)        — Right click at screen coordinates
4.  TYPE("text")              — Type text (uses clipboard paste - fast & reliable)
5.  TYPE_SLOW("text")         — Type text char-by-char (for search boxes that filter as you type)
6.  PRESS("key")              — Press a single key: enter, tab, escape, backspace, space, f5, etc.
7.  HOTKEY("{MOD_KEY}", "t") — Press keyboard shortcut combo
8.  SCROLL(x, y, clicks)     — Scroll at position. Negative clicks = scroll down, positive = scroll up
9.  MOVE(x, y)                — Move mouse cursor to coordinates
10. WAIT(seconds)             — Wait/pause (max 10s)
11. OPEN_URL("https://...")   — Open a URL in the default browser
12. RUN_CMD("command")        — Run a shell command
13. BROWSER("snapshot", {{}})  — Inspect visible browser controls and links via MCP
14. BROWSER("navigate", {{"url":"https://..."}}) — Navigate via browser MCP
15. NEED_VISION("reason")    — Request a screenshot for a specific visual question
16. DONE("summary")           — Finish the task with a summary

CRITICAL RULES:
- Output ONLY the single command. No explanation, no extra text.
- Prefer browser snapshot and accessibility data before coordinates.
- Screenshots are on demand; don't request one if text observations answer the next step.
- For clicking desktop controls: use CENTER coordinates from the UI tree or screenshot.
- When typing in a browser's address bar: first CLICK the address bar, then TYPE the URL, then PRESS("enter").
- For YouTube: OPEN_URL("https://www.youtube.com/results?search_query=YOUR+SEARCH") to search directly.
- For playing a video: after search results load, CLICK on the video thumbnail.
- When writing code: open the appropriate editor first, then TYPE the code.
- If something doesn't seem to work, try an alternative approach.
- ALWAYS end with DONE("description of what was accomplished") when the task is complete.
- If the task is simple (e.g., open a URL), you can do it in 1-2 steps. Don't over-complicate.
- Use RUN_CMD for system operations like creating files, running scripts, checking status.
- Be PRECISE with coordinates. Look at the UI tree element centers for accurate targeting.
- On macOS: notepad means TextEdit, explorer means Finder, cmd means Terminal, use Command not Ctrl.

SHORTCUTS YOU KNOW:
- Command+Space (Spotlight)
- {MOD_KEY}+T: New browser tab
- {MOD_KEY}+L: Focus address bar in browser
- {MOD_KEY}+W: Close current tab
- Command+Q: Close window
- {MOD_KEY}+C/V/X: Copy/Paste/Cut
- {MOD_KEY}+S: Save
- {MOD_KEY}+Z: Undo
"""
    
    messages = [SystemMessage(content=system_prompt)]
    action_history = []
    vision_requested = bool(use_vision)
    previous_action_failed = False
    
    for step in range(max_steps):
        time.sleep(0.8)  # Let screen settle
        
        # Build the observation context
        try:
            ui_tree = get_os_virtual_dom()
        except Exception as exc:
            ui_tree = f"Accessibility/UI tree unavailable: {exc}"
            previous_action_failed = True

        if (vision_requested or previous_action_failed) and llm_vision is None:
            try:
                llm_vision = _make_vision_llm()
            except Exception:
                llm_vision = None

        if llm_vision and (vision_requested or previous_action_failed):
            try:
                screenshot_b64 = capture_screenshot_b64()
                observation_msg = HumanMessage(content=[
                    {
                        "type": "text",
                        "text": (
                            f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                            f"UI Element Tree:\n{ui_tree}\n\n"
                            f"Previous actions taken: {json.dumps(action_history[-5:]) if action_history else 'None yet'}\n\n"
                            "A screenshot was requested because text observations were insufficient. "
                            "Analyze everything visible and issue your NEXT command."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}"
                        }
                    }
                ])
                messages.append(observation_msg)
                response = llm_vision.invoke(messages)
                vision_requested = False
                previous_action_failed = False
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
                vision_requested = False
                previous_action_failed = False
        else:
            observation_msg = HumanMessage(content=(
                f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                f"UI Element Tree:\n{ui_tree}\n\n"
                f"Previous actions: {json.dumps(action_history[-5:]) if action_history else 'None'}\n\n"
                "Issue your next command."
            ))
            messages.append(observation_msg)
            response = llm_text.invoke(messages)
            vision_requested = False
            previous_action_failed = False
        
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

        if cmd.startswith("NEED_VISION"):
            vision_requested = True
            result = execute_os_action(cmd)
            messages.append(HumanMessage(content=f"Action result: {result}. On the next turn, provide a screenshot if vision is available."))
            continue
        
        # Execute the action
        result = execute_os_action(cmd)
        previous_action_failed = any(marker in result.lower() for marker in ("failed", "error", "unavailable"))
        if previous_action_failed:
            vision_requested = True
        action_history.append({"step": step + 1, "command": cmd, "result": result})
        
        # Check if execute_os_action returned a TASK_COMPLETE 
        if "TASK_COMPLETE" in result:
            return result.replace("TASK_COMPLETE: ", "SUCCESS: ")
        
        # Feed result back
        messages.append(HumanMessage(content=f"Action result: {result}"))
        
        print(f"[OS Agent] Result: {result}")
    
    return "Task ended after reaching maximum steps. Partial progress may have been made."
