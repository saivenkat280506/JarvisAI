"""
mcp_clients — The only sanctioned way for JARVIS backend code to execute
external actions (shell, GUI, system control).

Every capability is an MCP tool call against a registered stdio server
(see registry.py). Direct subprocess / osascript / pyautogui calls in
backend code are prohibited by the canonical brief; this package is the
single auditable choke point (every call is logged to logs/mcp_audit.jsonl).
"""

from .protocol import MCPError, StdioMCPServer
from .registry import call, get_client, shutdown_all

__all__ = ["MCPError", "StdioMCPServer", "call", "get_client", "shutdown_all"]
