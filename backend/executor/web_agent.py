"""
web_agent.py — Streaming Autonomous Agent with Live WebSocket Progress
=======================================================================
Wraps the existing run_os_agent loop and broadcasts each step as a
structured WebSocket event so the frontend overlay can show real-time
progress: current action, result, step count, and completion status.

WebSocket events emitted:
  { "type": "agent_step", "step": N, "total": N, "action": "...", "result": "...", "status": "running" }
  { "type": "agent_step", "step": N, "total": N, "action": "DONE", "result": "...", "status": "done" }
  { "type": "agent_step", "step": 0, "action": "STOPPED", "result": "...", "status": "stopped" }

SSE stream (for /agent/run endpoint) also yields the same dicts as JSON lines.
"""

import asyncio
import time
import re
import json
import threading
from typing import AsyncGenerator, Callable, Optional

# ── Stop flag ─────────────────────────────────────────────────────────────────
_stop_requested = threading.Event()


def request_stop():
    """Signal the running agent to stop after the current step."""
    _stop_requested.set()


def clear_stop():
    _stop_requested.clear()


def is_stop_requested() -> bool:
    return _stop_requested.is_set()


# ── Streaming agent wrapper ────────────────────────────────────────────────────

async def run_web_agent_streaming(
    task: str,
    broadcast_fn: Callable[[dict], None],
    max_steps: int = 15,
    use_vision: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Async generator that runs the OS agent step-by-step.
    After each step it:
      1. Emits a WebSocket event via broadcast_fn (for live overlay)
      2. Yields an SSE chunk (for the HTTP response stream)

    Parameters
    ----------
    task         : natural-language task description
    broadcast_fn : coroutine that broadcasts a dict to all WS clients
    max_steps    : hard cap on number of agent steps (default 15)
    use_vision   : whether to begin with a screenshot (screenshots are otherwise on demand)
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from brain.llm_provider import ChatLLM, VISION_MODEL
    from executor.os_agent import (
        get_os_virtual_dom,
        execute_os_action,
        capture_screenshot_b64,
    )
    from mcp_clients.macos import gui_screen_size

    clear_stop()

    # Build LLMs (same as os_agent.run_os_agent)
    llm_vision = None
    if use_vision:
        try:
            llm_vision = ChatLLM(
                temperature=0.1,
                max_tokens=512,
                model=VISION_MODEL,
            )
        except Exception:
            use_vision = False

    llm_text = ChatLLM(temperature=0.1)

    host_os = "macOS"
    mod = "command"
    system_prompt = f"""You are JARVIS, an autonomous OS-level agent with FULL CONTROL of a {host_os} machine.
Task: {task}

Start with text observations: the accessibility/UI tree and browser MCP snapshots.
Request a screenshot with NEED_VISION("reason") only when those observations
cannot answer the next step. Issue ONE command per turn.

COMMANDS:
CLICK(x, y) | DOUBLE_CLICK(x, y) | RIGHT_CLICK(x, y)
TYPE("text") | TYPE_SLOW("text") | PRESS("key") | HOTKEY("{mod}","t")
SCROLL(x, y, clicks) | MOVE(x, y) | WAIT(seconds)
OPEN_URL("https://...") | RUN_CMD("cmd")
BROWSER("snapshot", {{}}) | BROWSER("navigate", {{"url":"https://..."}})
NEED_VISION("reason")
DONE("summary")

RULES:
- Output ONLY the single raw command. No explanation.
- Prefer accessibility and browser snapshot data before coordinates.
- Do not request screenshots when text observations answer the next step.
- Always end with DONE("...") when the task is complete.
- Be precise. Look at UI tree coords.
"""

    messages = [SystemMessage(content=system_prompt)]
    action_history: list[dict] = []
    vision_requested = bool(use_vision)
    previous_action_failed = False

    async def _emit(step: int, action: str, result: str, status: str):
        payload = {
            "type": "agent_step",
            "step": step,
            "total": max_steps,
            "action": action,
            "result": result,
            "status": status,
            "task": task,
        }
        # WebSocket broadcast (for overlay)
        try:
            await broadcast_fn(payload)
        except Exception:
            pass
        # SSE yield
        return f"data: {json.dumps(payload)}\n\n"

    async def _emit_computer_use(step: int, screenshot_b64: str) -> str | None:
        """Forward an on-demand screenshot and observed pointer trail to the UI."""
        if not screenshot_b64 or len(screenshot_b64) > 8 * 1024 * 1024:
            return None

        try:
            screen = await asyncio.to_thread(gui_screen_size)
            screen_width = int(screen.get("width", 0))
            screen_height = int(screen.get("height", 0))
            if screen_width <= 0 or screen_height <= 0:
                raise ValueError("Screen dimensions are unavailable")
        except Exception:
            screen_width = screen_height = 0

        pointer_steps = []
        pointer_command = re.compile(
            r"^(CLICK|DOUBLE_CLICK|RIGHT_CLICK|MOVE|SCROLL)\s*\(\s*(-?\d+)\s*,\s*(-?\d+)",
            re.IGNORECASE,
        )
        for action in action_history:
            match = pointer_command.match(str(action.get("command", "")).strip())
            if not match:
                continue
            x, y = int(match.group(2)), int(match.group(3))
            pointer_steps.append({
                "id": f"step-{action['step']}",
                "action": match.group(1).replace("_", " ").title(),
                "target": f"Screen position ({x}, {y})",
                "x": x,
                "y": y,
            })

        payload = {
            "type": "computer_use",
            "step": step,
            "url": "macOS screen",
            "screenshot": f"data:image/png;base64,{screenshot_b64}",
            "screen": {"width": screen_width, "height": screen_height},
            "steps": pointer_steps[-5:],
            "activeIndex": min(4, len(pointer_steps[-5:]) - 1),
        }
        try:
            await broadcast_fn(payload)
        except Exception:
            pass
        return f"data: {json.dumps(payload)}\n\n"

    run_started = {"type": "agent_run", "status": "running", "task": task}
    try:
        await broadcast_fn(run_started)
    except Exception:
        pass
    yield f"data: {json.dumps(run_started)}\n\n"

    for step in range(1, max_steps + 1):
        if is_stop_requested():
            sse = await _emit(step, "STOPPED", "Agent stopped by user.", "stopped")
            yield sse
            return

        # Let screen settle
        await asyncio.sleep(0.6)

        # Observation
        try:
            ui_tree = await asyncio.to_thread(get_os_virtual_dom)
        except Exception as exc:
            ui_tree = f"Accessibility/UI tree unavailable: {exc}"
            previous_action_failed = True
        obs_text = (
            f"Step {step}/{max_steps}. Task: {task}\n\n"
            f"UI Tree:\n{ui_tree}\n\n"
            f"Recent actions: {json.dumps(action_history[-4:]) if action_history else 'None'}\n\n"
            "Issue your NEXT command."
        )

        # LLM call (in thread to avoid blocking event loop)
        if (vision_requested or previous_action_failed) and llm_vision is None:
            try:
                llm_vision = await asyncio.to_thread(
                    ChatLLM, temperature=0.1, max_tokens=512, model=VISION_MODEL
                )
            except Exception:
                llm_vision = None

        used_vision = bool(llm_vision and (vision_requested or previous_action_failed))
        try:
            if used_vision:
                screenshot_b64 = await asyncio.to_thread(capture_screenshot_b64)
                computer_use_event = await _emit_computer_use(step, screenshot_b64)
                if computer_use_event:
                    yield computer_use_event
                obs_msg = HumanMessage(content=[
                    {"type": "text", "text": obs_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                ])
                messages.append(obs_msg)
                response = await asyncio.to_thread(llm_vision.invoke, messages)
                vision_requested = False
                previous_action_failed = False
            else:
                obs_msg = HumanMessage(content=obs_text)
                messages.append(obs_msg)
                response = await asyncio.to_thread(llm_text.invoke, messages)
                vision_requested = False
                previous_action_failed = False
        except Exception as exc:
            if used_vision:
                sanitized: list = []
                for message in messages:
                    if isinstance(message, HumanMessage) and isinstance(message.content, list):
                        text_part = next(
                            (part.get("text", "") for part in message.content
                             if part.get("type") == "text"), ""
                        )
                        sanitized.append(HumanMessage(content=text_part))
                    else:
                        sanitized.append(message)
                sanitized.append(HumanMessage(content=(
                    f"{obs_text}\n\nScreenshot unavailable; continue using text observations."
                )))
                try:
                    response = await asyncio.to_thread(llm_text.invoke, sanitized)
                    messages = sanitized
                    vision_requested = False
                    previous_action_failed = False
                except Exception as text_exc:
                    sse = await _emit(step, "ERROR", f"LLM error: {text_exc}", "error")
                    yield sse
                    return
            else:
                sse = await _emit(step, "ERROR", f"LLM error: {exc}", "error")
                yield sse
                return

        cmd = response.content.strip().replace("```", "").split("\n")[0].strip()
        messages.append(response)

        print(f"[WebAgent] Step {step}: {cmd}")

        # Emit step START so overlay shows current action immediately
        sse_start = await _emit(step, cmd, "executing...", "running")
        yield sse_start

        # DONE?
        if cmd.startswith("DONE"):
            match = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
            summary = match.group(1) if match else "Task completed."
            sse = await _emit(step, "DONE", summary, "done")
            yield sse
            return

        if cmd.startswith("NEED_VISION"):
            vision_requested = True
            result = await asyncio.to_thread(execute_os_action, cmd)
            action_history.append({"step": step, "command": cmd, "result": result})
            sse = await _emit(step, "NEED_VISION", result, "running")
            yield sse
            continue

        # Execute action
        result = await asyncio.to_thread(execute_os_action, cmd)
        previous_action_failed = any(marker in result.lower() for marker in ("failed", "error", "unavailable"))
        if previous_action_failed:
            vision_requested = True
        action_history.append({"step": step, "command": cmd, "result": result})
        messages.append(HumanMessage(content=f"Action result: {result}"))

        print(f"[WebAgent] Result: {result}")

        # Emit step RESULT
        sse_result = await _emit(step, cmd, result, "running")
        yield sse_result

        if "TASK_COMPLETE" in result:
            sse = await _emit(step, "DONE", result.replace("TASK_COMPLETE: ", ""), "done")
            yield sse
            return

    # Max steps reached
    sse = await _emit(max_steps, "MAX_STEPS", "Reached maximum step limit.", "done")
    yield sse
