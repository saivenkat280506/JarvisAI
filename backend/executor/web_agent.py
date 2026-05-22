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
    use_vision: bool = True,
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
    use_vision   : whether to send screenshots to the vision LLM
    """
    import pyautogui
    import mss
    from PIL import Image
    from io import BytesIO
    import base64
    from langchain_groq import ChatGroq
    from langchain_core.messages import SystemMessage, HumanMessage
    from executor.os_agent import (
        get_os_virtual_dom,
        execute_os_action,
        capture_screenshot_b64,
    )

    clear_stop()

    # Build LLMs (same as os_agent.run_os_agent)
    llm_vision = None
    if use_vision:
        try:
            llm_vision = ChatGroq(
                model_name="llama-3.2-11b-vision-preview",
                temperature=0.1,
                max_tokens=512,
            )
        except Exception:
            use_vision = False

    llm_text = ChatGroq(model_name="llama-3.1-8b-instant", temperature=0.1)

    system_prompt = f"""You are JARVIS, an autonomous OS-level agent with FULL CONTROL of a Windows PC.
Task: {task}

You can SEE the screen via screenshots. Issue ONE command per turn.

COMMANDS:
CLICK(x, y) | DOUBLE_CLICK(x, y) | RIGHT_CLICK(x, y)
TYPE("text") | TYPE_SLOW("text") | PRESS("key") | HOTKEY("ctrl","t")
SCROLL(x, y, clicks) | MOVE(x, y) | WAIT(seconds)
OPEN_URL("https://...") | RUN_CMD("cmd") | SCREENSHOT()
DONE("summary")

RULES:
- Output ONLY the single raw command. No explanation.
- Always end with DONE("...") when the task is complete.
- Be precise. Look at UI tree coords.
"""

    messages = [SystemMessage(content=system_prompt)]
    action_history: list[dict] = []

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

    for step in range(1, max_steps + 1):
        if is_stop_requested():
            sse = await _emit(step, "STOPPED", "Agent stopped by user.", "stopped")
            yield sse
            return

        # Let screen settle
        await asyncio.sleep(0.6)

        # Observation
        ui_tree = await asyncio.to_thread(get_os_virtual_dom)
        obs_text = (
            f"Step {step}/{max_steps}. Task: {task}\n\n"
            f"UI Tree:\n{ui_tree}\n\n"
            f"Recent actions: {json.dumps(action_history[-4:]) if action_history else 'None'}\n\n"
            "Issue your NEXT command."
        )

        # LLM call (in thread to avoid blocking event loop)
        try:
            if use_vision and llm_vision:
                screenshot_b64 = await asyncio.to_thread(capture_screenshot_b64)
                obs_msg = HumanMessage(content=[
                    {"type": "text", "text": obs_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                ])
                messages.append(obs_msg)
                response = await asyncio.to_thread(llm_vision.invoke, messages)
            else:
                obs_msg = HumanMessage(content=obs_text)
                messages.append(obs_msg)
                response = await asyncio.to_thread(llm_text.invoke, messages)
        except Exception as exc:
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

        # Execute action
        result = await asyncio.to_thread(execute_os_action, cmd)
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
