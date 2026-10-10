"""
mcp_servers — The ONLY place in the backend allowed to spawn processes,
invoke AppleScript/osascript, or drive the GUI (pyautogui).

Each module is an MCP stdio server process (newline-delimited JSON-RPC,
handshake in mcp_clients/protocol.py, shared loop in _server.py):

  mcp_servers.shell_server  — run_command, read/write_file, list_dir,
                              open_path, process control
  mcp_servers.macos_server  — AppleScript actions (macos_native) + gui_* tools

Backend code talks to these exclusively through mcp_clients.
"""
