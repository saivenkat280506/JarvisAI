"""
tool_executor.py — Async Execution Engine with Feedback Loop
===========================================================
Validates and executes tools based on LLM decisions with async support and failure recovery.
"""

import asyncio
import json
from executor.tools_registry import get_tool
from brain.safety import validate_action
from brain.memory import save_memory
from executor.task_manager import task_manager
from executor.agent_loop import agent_loop
from executor.error_handler import log_error

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
        return False, f"Tool '{intent}' not found in registry."
    
    # 3. Define Execution Wrapper for Task Manager
    async def _run():
        try:
            # Most of our tools are currently sync, wrap them in to_thread
            success, result = await asyncio.to_thread(tool_func, params)
            
            save_memory("last_result", result)
            save_memory("last_status", "success" if success else "failure")
            
            if not success:
                # Log the actual error internally
                log_error(intent, Exception(result))
                
                # --- Failure Recovery Logic ---
                if intent == "send_whatsapp":
                    print("[Executor] WhatsApp send failed. Falling back to opening app.")
                    from executor.open_app import open_app
                    f_success, f_msg = await asyncio.to_thread(open_app, "whatsapp")
                    if f_success:
                        return True, "I couldn't send the message automatically, but I've opened WhatsApp for you."
                
                if intent != "search_browser":
                    print("[Executor] Tool failed. Falling back to browser search.")
                    from executor.automation import search_google
                    s_success, s_msg = await asyncio.to_thread(search_google, params.get("query", "the request"))
                    if s_success:
                        return True, "Automatic tool failed, but I've searched the web for you instead."
                
                # Final fallback: add to retry queue in agent_loop
                agent_loop.add_to_retry_queue(
                    lambda: _run(), 
                    {"name": intent, "params": params}
                )
                return False, result
            
            return success, result

        except Exception as e:
            save_memory("last_status", "exception")
            log_error(intent, e)
            return False, f"Execution error in {intent}: {str(e)}"

    # 4. Handle Blocking vs Background
    if background:
        task_id = await task_manager.start_task(_run(), name=f"BG_{intent}")
        return True, f"Task started in background (ID: {task_id})."
    else:
        return await _run()

