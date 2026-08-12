"""
agent_loop.py — Background Monitoring Agent
============================================
Continuously checks for failed tasks, handles retries, and manages scheduled items.
"""

import asyncio
import time
from executor.task_manager import task_manager


class AgentLoop:
    def __init__(self):
        self.is_running = False
        self.retry_queue = []  # List of (task_id, attempt_count, metadata)

    async def run(self):
        """The main background loop."""
        self.is_running = True
        print("[AgentLoop] Background monitor started.")
        
        while self.is_running:
            try:
                from services.runtime_state import flags
                if flags.shutdown_requested:
                    break
                await self._check_and_retry_tasks()
                await asyncio.sleep(2)
            except Exception as e:
                print(f"[AgentLoop Error] {e}")
                await asyncio.sleep(5)

        print("[AgentLoop] Stopped.")

    async def _check_and_retry_tasks(self):
        """Scans retry queue and re-executes failed tools from the registry."""
        if not self.retry_queue:
            return

        print(f"[AgentLoop] Processing {len(self.retry_queue)} pending retries...")
        
        current_queue = list(self.retry_queue)
        self.retry_queue.clear()

        for task_info in current_queue:
            task_id, attempt, meta = task_info
            
            if attempt < 2:
                print(f"[AgentLoop] Retrying task {task_id} (Attempt {attempt+1}/2)...")
                await asyncio.sleep(1 * (attempt + 1))
                
                # Re-create the tool call from the registry at retry time
                intent = meta.get("name", "")
                params = meta.get("params", {})
                try:
                    from executor.tools_registry import get_tool
                    tool_func = get_tool(intent)
                    if tool_func:
                        success, result = await asyncio.to_thread(tool_func, params)
                        if success:
                            print(f"[AgentLoop] Retry succeeded for {intent}")
                            continue
                        else:
                            print(f"[AgentLoop] Retry failed for {intent}: {result}")
                    else:
                        print(f"[AgentLoop] Tool '{intent}' not found in registry for retry.")
                except Exception as e:
                    print(f"[AgentLoop] Retry exception for {intent}: {e}")
                
                # Re-queue with incremented attempt count
                self.retry_queue.append((task_id, attempt + 1, meta))
            else:
                print(f"[AgentLoop] Task {task_id} failed after max retries.")
                try:
                    from tts.pocket_tts import speak
                    await asyncio.to_thread(speak, "Task failed, sir.")
                except Exception:
                    pass

    def add_to_retry_queue(self, metadata: dict):
        """Adds a failed task to the retry queue using metadata only."""
        self.retry_queue.append(("retry_" + str(int(time.time())), 0, metadata))

# Global instance
agent_loop = AgentLoop()

