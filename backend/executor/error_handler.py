"""
error_handler.py — Self-healing retry loop + structured failure log
===================================================================
Build brief §1 / build plan §8: retries must ESCALATE, not repeat.

  Step 1 — direct execution of the planned tool.
  Step 2 — different approach: the caller may supply an `alternate` strategy
           (e.g. shell vs. GUI, alternate syntax); otherwise the same tool is
           re-planned with the actual error text attached as a healing hint.
  Step 3 — search online for the fix (fetch-only — outbound GET, no upload)
           via `search_fix()`, then retry with what it found.
  Step 4 — give up and report plainly: the whole attempt chain is written to
           the structured failure log so the user sees what was tried.

Every completed chain (resolved or not) is appended to
`backend/logs/failure_log.jsonl` — one JSON object per line with the intent,
each attempt (approach/result/error/ms), and the eventual resolution.
That log is also the raw material for writing new skill files later.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from retry import RetryState, RetryStateMachine

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = BACKEND_DIR / "logs"
FAILURE_LOG = LOGS_DIR / "failure_log.jsonl"

_LOG_LOCK = threading.Lock()

# run_once signatures: (approach, hint) -> (ok, result)
RunOnce = Callable[[str, str], tuple[bool, Any]]
# alternate signatures: (params, error) -> params  (a genuinely different plan)
Alternate = Callable[[dict, str], dict]


def _append(record: dict) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(FAILURE_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass  # the log must never break error handling


def log_error(task_name: str, error: Exception, params: dict | None = None) -> None:
    """Flat single-error log entry (kept for tool_executor's call sites)."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "error",
        "task": str(task_name),
        "error": str(error)[:800],
        **({"params": {k: str(v)[:200] for k, v in (params or {}).items()}}
           if params else {}),
    }
    _append(record)
    try:
        from memory.db import current_conversation_id, log_failure
        log_failure(conversation_id=current_conversation_id(), intent=str(task_name),
                    approach="tool_execution", error=str(error),
                    attempt_chain=[record], resolved=False)
    except Exception:
        pass


def search_fix(error_text: str, timeout: float = 6.0) -> str | None:
    """Step 3: fetch-only web search for an error message (no data uploaded).

    DuckDuckGo's HTML endpoint, read-only — returns a short snippet that can
    be fed back to the model/tool as a fix hint, or None if nothing usable."""
    query = " ".join(str(error_text).split())[:300]
    if not query:
        return None
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    import re
    # Result snippets come HTML-escaped; strip tags and unescape.
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    if not snippets:
        snippets = re.findall(r'result__snippet[^>]*>(.*?)</', html, re.S)
    for raw in snippets[:3]:
        text = re.sub(r"<[^>]+>", "", raw)
        text = (text.replace("&quot;", '"').replace("&#x27;", "'")
                    .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        text = " ".join(text.split())
        if len(text) > 30:
            return text[:600]
    return None


def self_heal(task_name: str, run_once: RunOnce, *,
              alternate: Alternate | None = None,
              params: dict | None = None,
              use_web_search: bool = False,
              delay: float = 1.0) -> tuple[bool, Any]:
    """Run direct → alternate → fetch-only web fix → reported failure.

    `run_once(approach, hint)` executes exactly one attempt. The retry state
    machine controls order; `alternate` can change parameters for a different
    step-2 strategy. Callers should use this only for safe, repeatable tools.
    """
    chain: list[dict] = []
    params = dict(params or {})
    result: Any = None
    last_error = ""
    machine = RetryStateMachine()

    def _attempt(step: int, approach: str, attempt_hint: str) -> bool:
        nonlocal result, last_error
        t0 = time.monotonic()
        try:
            ok, res = run_once(approach, attempt_hint)
        except Exception as exc:  # tool raised
            ok, res = False, str(exc)
        ms = round((time.monotonic() - t0) * 1000, 1)
        entry = {"step": step, "approach": approach, "ok": bool(ok), "ms": ms}
        if ok:
            result = res
            chain.append(entry)
            return True
        last_error = str(res) if res is not None else "unknown error"
        entry["error"] = last_error[:800]
        chain.append(entry)
        return False

    step = 0
    while not machine.complete:
        strategy = machine.next_strategy()
        if strategy is None:
            break
        step += 1

        if strategy == RetryState.DIRECT.value:
            ok = _attempt(step, "direct", "")
            machine.record(strategy, succeeded=ok, error=last_error)
            if ok:
                return True, result
            continue

        if delay:
            time.sleep(delay)

        if strategy == RetryState.ALTERNATE.value:
            if alternate is not None:
                try:
                    params = dict(alternate(params, last_error))
                except Exception:
                    pass
            params["healing_hint"] = last_error[:400]
            ok = _attempt(step, "alternate_strategy", last_error)
            machine.record(strategy, succeeded=ok, error=last_error)
            if ok:
                return _finish(task_name, params, chain, resolved_at=2,
                               result=result)
            continue

        # Online recovery receives only a generic local tool/error category,
        # never user parameters, free-form queries, credentials, or paths.
        if use_web_search:
            categories = ("timeout", "connection", "unavailable", "permission", "not found", "invalid")
            error_tags = [tag for tag in categories if tag in last_error.lower()]
            search_query = f"JARVIS {task_name.replace('_', ' ')} {' '.join(error_tags)} troubleshooting"
            fix = search_fix(search_query[:180])
        else:
            fix = None

        if fix:
            params["healing_hint"] = fix[:400]
            ok = _attempt(step, "web_search_fix", fix)
            machine.record(strategy, succeeded=ok, error=last_error)
            if ok:
                return _finish(task_name, params, chain, resolved_at=3,
                               result=result)
        else:
            last_error = last_error or "unknown error"
            chain.append({"step": step, "approach": "web_search_fix", "ok": False,
                          "error": "online lookup disabled or no usable search result"})
            machine.record(strategy, succeeded=False,
                           error="online lookup disabled or no usable search result")

    # Step 4 — report failure after the escalation policy is exhausted.
    report = (f"Tried {len([c for c in chain if c.get('approach')])} approaches "
              f"for '{task_name}' (direct → alternate → online fix); "
              f"still failing: {last_error[:300]}")
    _finish(task_name, params, chain, resolved_at=None, result=report)
    return False, report


def _finish(task_name: str, params: dict, chain: list[dict], *,
            resolved_at: int | None, result: Any) -> tuple[bool, Any]:
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "self_heal",
        "task": str(task_name),
        "resolved": resolved_at is not None,
        "resolved_at_step": resolved_at,
        "attempts": chain,
        "params": {k: str(v)[:200] for k, v in params.items()
                   if k != "healing_hint"},
    }
    _append(record)
    try:
        from memory.db import current_conversation_id, log_failure
        last_error = next((step.get("error") for step in reversed(chain)
                           if step.get("error")), "")
        log_failure(conversation_id=current_conversation_id(), intent=str(task_name),
                    approach="self_heal", error=last_error or str(result),
                    attempt_chain=chain, resolved=resolved_at is not None)
    except Exception:
        pass
    return (resolved_at is not None), result


# ── Decorators (kept: same signatures as before) ─────────────────────────

def retry_task(max_retries: int = 3, delay: float = 1.0):
    """Retry a sync function max_retries times, then log to the failure log."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    print(f"[Retry] {func.__name__} attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        time.sleep(delay)
            log_error(func.__name__, last_exception)
            return "Task failed"
        return wrapper
    return decorator


def retry_async_task(max_retries: int = 3, delay: float = 1.0):
    """Retry an async function max_retries times, then log to the failure log."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    print(f"[Retry] {func.__name__} attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
            log_error(func.__name__, last_exception)
            return "Task failed"
        return wrapper
    return decorator


if __name__ == "__main__":
    calls = {"n": 0}

    def flaky_run_once(approach: str, hint: str):
        calls["n"] += 1
        if calls["n"] < 3:
            return False, f"ValueError: bad input on {approach}"
        return True, f"ok via {approach} (hint={hint[:40]!r})"

    ok, res = self_heal("demo_task", flaky_run_once, params={"x": 1}, delay=0)
    print("RESULT:", ok, res)
    print("LOG:", FAILURE_LOG.read_text().splitlines()[-1][:400])
