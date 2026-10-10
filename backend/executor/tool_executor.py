"""
tool_executor.py — Async Execution Engine with Feedback Loop
===========================================================
Validates and executes tools based on LLM decisions with async support and failure recovery.
"""

import asyncio
import time
from datetime import datetime, timezone
from executor.tools_registry import get_tool
from brain.safety import validate_action
from brain.memory import save_memory
from executor.task_manager import task_manager
from executor.error_handler import log_error, self_heal


def _record_tool_call(*, intent: str, params: dict, result, success: bool,
                      started_at: str, elapsed_ms: float) -> None:
    try:
        from memory.db import current_conversation_id, log_tool_call
        log_tool_call(
            conversation_id=current_conversation_id(),
            intent=intent,
            tool_name=intent,
            input_data=params,
            output_text=str(result)[:4000],
            success=success,
            started_at=started_at,
            latency_ms=elapsed_ms,
        )
    except Exception as exc:
        print(f"[Memory] Could not persist tool call {intent}: {exc}")

# Online escalation is limited to explicit, read-only search requests.
# Mutating tools (especially sends) are never retried automatically.
SELF_HEAL_INTENTS = {"search_browser", "web_search"}


def _search_alternate(params: dict) -> tuple[bool, str]:
    """Second strategy for a requested browser search: fetch-only web search."""
    query = (params.get("query") or params.get("q") or "").strip()
    if not query:
        return False, "No query was available for the alternate search strategy."
    from executor.automation import smart_search
    return smart_search(query)


def execute_search_briefing(query: str) -> tuple[bool, object]:
    """Run a browser briefing with the same logged recovery chain as search tools."""
    from executor.search_briefing import run_search_briefing

    query = (query or "").strip()
    if not query:
        return False, "Tell me what to search for, sir."

    def _attempt(approach: str, hint: str) -> tuple[bool, object]:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            if approach == "alternate_strategy":
                success, summary = _search_alternate({"query": query})
                result = {"kind": "text", "summary": summary} if success else summary
            else:
                briefing = run_search_briefing(query)
                success = bool(briefing.get("ok"))
                result = {"kind": "browser", "briefing": briefing} if success else (
                    briefing.get("error") or "Browser search failed."
                )
        except Exception as exc:
            success, result = False, f"Search execution error: {exc}"
        _record_tool_call(
            intent="search_browser",
            params={"query": query, **({"healing_hint": hint[:400]} if hint else {})},
            result=result,
            success=bool(success),
            started_at=started_at,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return bool(success), result

    return self_heal(
        "search_browser",
        _attempt,
        params={"query": query},
        use_web_search=True,
        delay=0.5,
    )


async def execute_tool(action_json: dict, background: bool = False):
    """
    Validates the JSON and executes the mapped tool.
    """
    # 1. Safety Check
    is_safe, safe_json = validate_action(action_json)
    if not is_safe:
        save_memory("last_status", "invalid_action")
        return False, "Invalid action requested. Falling back to search."
    
    intent = safe_json.get("intent")
    params = safe_json.get("parameters", {})
    
    if intent == "chat":
        return True, params.get("response", "I am here.")
        
    # 2. Resolve Tool
    tool_func = get_tool(intent)
    if not tool_func:
        save_memory("last_status", "tool_not_found")
        _record_tool_call(intent=intent or "unknown", params=params,
                          result="tool not found", success=False,
                          started_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=0)
        return False, f"Tool '{intent}' not found in registry."
    
    # 3. Define Execution Wrapper for Task Manager
    async def _run():
        def _attempt(approach: str, hint: str) -> tuple[bool, object]:
            started = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            attempt_params = dict(params)
            if hint:
                attempt_params["healing_hint"] = hint[:400]
            try:
                from services.terminal_stream import emit_tool_start, emit_tool_exit, emit_tool_line
                emit_tool_start("jarvis", intent, attempt_params)
            except Exception:
                emit_tool_start = emit_tool_exit = emit_tool_line = None

            try:
                if approach == "alternate_strategy":
                    success, result = _search_alternate(attempt_params)
                else:
                    success, result = tool_func(attempt_params)
                success = bool(success)
                if emit_tool_line and result:
                    res_lines = str(result).splitlines()[:10]
                    for r_line in res_lines:
                        emit_tool_line(r_line, stream="stdout" if success else "stderr", tool=intent)
            except Exception as exc:
                success, result = False, f"Execution error in {intent}: {exc}"
                if emit_tool_line:
                    emit_tool_line(str(exc), stream="stderr", tool=intent)

            elapsed = (time.perf_counter() - started) * 1000
            if emit_tool_exit:
                try:
                    emit_tool_exit("jarvis", intent, attempt_params, ok=success, returncode=0 if success else 1, ms=elapsed)
                except Exception:
                    pass

            _record_tool_call(
                intent=intent,
                params=attempt_params,
                result=result,
                success=success,
                started_at=started_at,
                elapsed_ms=elapsed,
            )
            return success, result

        try:
            if intent in SELF_HEAL_INTENTS:
                success, result = await asyncio.to_thread(
                    self_heal,
                    intent,
                    _attempt,
                    params=params,
                    use_web_search=True,
                    delay=0.5,
                )
            else:
                # One attempt only for ordinary and mutating tools. In
                # particular, an uncertain send is never replayed.
                success, result = await asyncio.to_thread(_attempt, "direct", "")
                if not success:
                    log_error(intent, Exception(result), params)

            save_memory("last_result", result)
            save_memory("last_status", "success" if success else "failure")
            return success, result
        except Exception as exc:
            save_memory("last_status", "exception")
            log_error(intent, exc, params)
            return False, f"Execution error in {intent}: {str(exc)}"

    # 4. Handle Blocking vs Background
    if background:
        task_id = await task_manager.start_task(_run(), name=f"BG_{intent}")
        return True, f"Task started in background (ID: {task_id})."
    else:
        return await _run()
