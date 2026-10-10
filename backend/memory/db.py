"""SQLite WAL store shared by chat, tools, failures, skills, and profile."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DB_PATH = Path(
    os.environ.get("JARVIS_DB_PATH")
    or os.environ.get("JARVIS_MEMORY_DB")
    or BACKEND_DIR / "data" / "jarvis_memory.db"
)
_LOCK = threading.RLock()
_READY = False
_CURRENT_CONVERSATION: ContextVar[str | None] = ContextVar(
    "jarvis_conversation_id", default=None
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ensure_schema() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()
        _READY = True


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    _ensure_schema()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the schema and WAL mode before serving requests."""
    _ensure_schema()


def set_current_conversation(conversation_id: str | None) -> None:
    _CURRENT_CONVERSATION.set(conversation_id)


def current_conversation_id() -> str | None:
    return _CURRENT_CONVERSATION.get()


def create_conversation(title: str = "") -> str:
    conversation_id = str(uuid.uuid4())
    now = _now()
    with _connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, title[:200], now, now),
        )
    return conversation_id


def add_message(conversation_id: str, role: str, content: str,
                intent: str | None = None) -> int:
    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError(f"Unsupported message role: {role}")
    now = _now()
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, intent, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, intent, now),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                     (now, conversation_id))
        return int(cursor.lastrowid)


def list_recent_messages(limit: int = 8, *,
                        exclude_conversation_id: str | None = None) -> list[dict]:
    """Read a small, chronological user/assistant history across sessions."""
    limit = max(1, min(int(limit), 50))
    with _connection() as conn:
        rows = conn.execute(
            "SELECT m.conversation_id, m.role, m.content, m.intent, m.created_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.role IN ('user', 'assistant') "
            "AND (? IS NULL OR m.conversation_id != ?) "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
            (exclude_conversation_id, exclude_conversation_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def log_tool_call(*, conversation_id: str | None, intent: str, tool_name: str,
                  input_data: dict, output_text: str, success: bool,
                  started_at: str, latency_ms: float) -> str:
    call_id = str(uuid.uuid4())
    with _connection() as conn:
        conn.execute(
            "INSERT INTO tool_call_log "
            "(id, conversation_id, intent, tool_name, input_json, output_text, success, "
            "started_at, completed_at, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, conversation_id, intent, tool_name,
             json.dumps(input_data, ensure_ascii=False, default=str), output_text,
             int(bool(success)), started_at, _now(), max(0.0, float(latency_ms))),
        )
    return call_id


def log_failure(*, conversation_id: str | None, intent: str, error: str,
                approach: str = "", attempt_chain: list | None = None,
                resolved: bool = False) -> int:
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO failure_log "
            "(conversation_id, intent, approach, error, attempt_chain_json, resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, intent, approach, str(error)[:4000],
             json.dumps(attempt_chain or [], ensure_ascii=False, default=str),
             int(bool(resolved)), _now()),
        )
        return int(cursor.lastrowid)


def upsert_skill(name: str, path: str, description: str = "", enabled: bool = True) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO skills_registry (name, path, description, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET path=excluded.path, "
            "description=excluded.description, enabled=excluded.enabled, "
            "updated_at=excluded.updated_at",
            (name, path, description, int(bool(enabled)), _now()),
        )


def list_skills(*, enabled_only: bool = False) -> list[dict]:
    """Return the locally registered skills, optionally filtering disabled ones."""
    sql = "SELECT name, path, description, enabled, updated_at FROM skills_registry"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY name COLLATE NOCASE"
    with _connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def set_user_profile(key: str, value) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO user_profile (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
            "updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False, default=str), _now()),
        )


def get_user_profile(key: str, default=None):
    """Read a durable profile value, returning default when no value exists."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT value_json FROM user_profile WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        return default


def list_user_profile() -> dict:
    """Read the durable profile as key/value pairs."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT key, value_json FROM user_profile ORDER BY key COLLATE NOCASE"
        ).fetchall()
    profile = {}
    for row in rows:
        try:
            profile[row["key"]] = json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            continue
    return profile
