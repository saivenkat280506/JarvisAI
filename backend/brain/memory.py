"""
memory.py — Simple Short-Term Memory
====================================
Stores recent interactions to provide context.
"""

import json
import os

MEMORY_FILE = "jarvis_memory.json"

def _load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"history": [], "last_contact": None, "last_song": None}

def save_memory(key, value):
    """Saves a value to memory."""
    mem = _load_memory()
    mem[key] = value
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f)

def get_memory(key):
    """Retrieves a value from memory."""
    mem = _load_memory()
    return mem.get(key)

def add_to_history(command):
    """Keeps track of last 5 commands."""
    mem = _load_memory()
    history = mem.get("history", [])
    history.append(command)
    if len(history) > 5:
        history = history[-5:]
    mem["history"] = history
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f)
