"""
memory_store.py — Local long-term memory for Jarvis
===================================================
SQLite on disk. No network, no embedding model, no vector server.

Why SQLite (not Chroma/Pinecone/LangGraph Store) for a personal desktop
assistant:
  - lookups are microseconds
  - exact contacts/tasks cannot be "almost" the wrong person
  - one file, easy to back up or delete
  - FTS5 covers "what did I tell you about X" without an embedding model

WhatsApp recipients NEVER come from fuzzy/vector search. Only rows with
allow_whatsapp=1 and a real phone number are messaged.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_DIR, "..", "data", "jarvis_memory.db")
CONTACTS_JSON = os.path.join(_DIR, "..", "executor", "whatsapp_contacts.json")

_GROUPISH = re.compile(
    r"\b(group|official|college|class|team|work|office|company|dept|"
    r"department|org|organization|community|channel|broadcast|family\s+group)\b",
    re.I,
)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> str:
    return os.environ.get("JARVIS_MEMORY_DB") or DEFAULT_DB


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    path = _db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE,
            phone TEXT NOT NULL DEFAULT '',
            allow_whatsapp INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
        CREATE TABLE IF NOT EXISTS contact_aliases (
            alias TEXT PRIMARY KEY COLLATE NOCASE,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY,
            key TEXT NOT NULL COLLATE NOCASE,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            updated_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
            key, value, content='facts', content_rowid='id'
        );
        """
    )
    _conn = conn
    return conn


def init_store() -> None:
    """Create tables and seed WhatsApp allowlist from the contacts JSON."""
    with _lock:
        conn = _connect()
        _seed_whatsapp_json(conn)


def _seed_whatsapp_json(conn: sqlite3.Connection) -> None:
    if not os.path.exists(CONTACTS_JSON):
        return
    try:
        with open(CONTACTS_JSON, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print(f"[MemoryStore] Could not seed contacts JSON: {exc}")
        return
    if not isinstance(data, dict):
        return
    by_phone: dict[str, list[str]] = {}
    for raw_name, raw_phone in data.items():
        name = str(raw_name).strip().lower()
        phone = str(raw_phone).strip()
        if not name or not phone:
            continue
        by_phone.setdefault(phone, []).append(name)
    now = _now()
    for phone, names in by_phone.items():
        canonical = names[0]
        for preferred in ("sathish", "ashrith", "laxman", "nishanth"):
            if preferred in names:
                canonical = preferred
                break
        row = conn.execute(
            "SELECT id FROM contacts WHERE phone = ? AND allow_whatsapp = 1",
            (phone,),
        ).fetchone()
        if row:
            contact_id = row["id"]
        else:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE name = ?", (canonical,)
            ).fetchone()
            if existing:
                contact_id = existing["id"]
                conn.execute(
                    "UPDATE contacts SET phone = ?, allow_whatsapp = 1, updated_at = ? WHERE id = ?",
                    (phone, now, contact_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO contacts (name, phone, allow_whatsapp, notes, updated_at) "
                    "VALUES (?, ?, 1, '', ?)",
                    (canonical, phone, now),
                )
                contact_id = cur.lastrowid
        for alias in names:
            conn.execute(
                "INSERT OR IGNORE INTO contact_aliases (alias, contact_id) VALUES (?, ?)",
                (alias, contact_id),
            )
    conn.commit()


def _is_groupish(name: str) -> bool:
    return bool(_GROUPISH.search(name or ""))


def whatsapp_allowlist() -> dict[str, str]:
    """alias/name -> phone. Only allow_whatsapp individuals with a real number."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT c.id, c.name, c.phone FROM contacts c WHERE c.allow_whatsapp = 1"
        ).fetchall()
        aliases = conn.execute("SELECT alias, contact_id FROM contact_aliases").fetchall()
    by_id = {
        row["id"]: (row["name"], row["phone"])
        for row in rows
        if row["phone"] and not _is_groupish(row["name"])
    }
    out: dict[str, str] = {}
    for contact_id, (name, phone) in by_id.items():
        out[name.lower()] = phone
    for row in aliases:
        pair = by_id.get(row["contact_id"])
        if pair:
            out[str(row["alias"]).lower()] = pair[1]
    # JSON file remains the hard allowlist even if the DB is empty/corrupt.
    try:
        if os.path.exists(CONTACTS_JSON):
            with open(CONTACTS_JSON, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                for name, phone in data.items():
                    key = str(name).strip().lower()
                    if key and phone and not _is_groupish(key):
                        out[key] = str(phone).strip()
    except Exception:
        pass
    return out


def unique_whatsapp_targets() -> list[tuple[str, str]]:
    """One (display_name, phone) per number. Never groups."""
    allow = whatsapp_allowlist()
    by_phone: dict[str, str] = {}
    preferred = ("sathish", "ashrith", "laxman", "nishanth")
    for name, phone in allow.items():
        if _is_groupish(name):
            continue
        if phone not in by_phone:
            by_phone[phone] = name
        elif name in preferred:
            by_phone[phone] = name
    return [(name, phone) for phone, name in by_phone.items()]


def remember_fact(key: str, value: str) -> tuple[bool, str]:
    key = (key or "").strip().lower()
    value = (value or "").strip()
    if not key or not value:
        return False, "I need both a name for the fact and the information, sir."
    now = _now()
    with _lock:
        conn = _connect()
        existing = conn.execute("SELECT id FROM facts WHERE key = ?", (key,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE facts SET value = ?, updated_at = ? WHERE id = ?",
                (value, now, existing["id"]),
            )
            fact_id = existing["id"]
            conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (fact_id,))
        else:
            cur = conn.execute(
                "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
            fact_id = cur.lastrowid
        conn.execute(
            "INSERT INTO facts_fts (rowid, key, value) VALUES (?, ?, ?)",
            (fact_id, key, value),
        )
        conn.commit()
    return True, f"Remembered: {key} is {value}."


def add_task(title: str) -> tuple[bool, str]:
    title = (title or "").strip()
    if not title:
        return False, "I need a task title, sir."
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'open', ?)",
            (title, _now()),
        )
        conn.commit()
    return True, f"Task added: {title}."


def list_tasks(status: str = "open") -> tuple[bool, str]:
    with _lock:
        conn = _connect()
        if status == "all":
            rows = conn.execute(
                "SELECT id, title, status FROM tasks ORDER BY id DESC LIMIT 20"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, status FROM tasks WHERE status = ? ORDER BY id DESC LIMIT 20",
                (status,),
            ).fetchall()
    if not rows:
        return True, "You have no open tasks, sir."
    lines = [f"{row['id']}. {row['title']} ({row['status']})" for row in rows]
    return True, "Tasks: " + "; ".join(lines)


def complete_task(query: str) -> tuple[bool, str]:
    query = (query or "").strip()
    if not query:
        return False, "Which task should I complete, sir?"
    with _lock:
        conn = _connect()
        row = None
        if query.isdigit():
            row = conn.execute("SELECT id, title FROM tasks WHERE id = ?", (int(query),)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id, title FROM tasks WHERE status = 'open' AND title LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{query}%",),
            ).fetchone()
        if row is None:
            return False, f"I could not find an open task matching '{query}'."
        conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        conn.commit()
    return True, f"Marked done: {row['title']}."


def list_remembered_contacts() -> tuple[bool, str]:
    targets = unique_whatsapp_targets()
    if not targets:
        return True, "There are no WhatsApp-allowed contacts saved, sir."
    parts = [f"{name} ({phone})" for name, phone in targets]
    return True, "WhatsApp-allowed contacts: " + "; ".join(parts) + "."


def recall(query: str) -> tuple[bool, str]:
    """Instant local recall. No LLM."""
    q = (query or "").strip()
    if not q:
        return False, "What should I look up, sir?"

    low = q.lower()
    if re.search(r"\b(contacts?|whatsapp\s+list)\b", low):
        return list_remembered_contacts()
    if re.search(r"\btasks?\b", low):
        return list_tasks("open")

    with _lock:
        conn = _connect()
        exact = conn.execute(
            "SELECT key, value FROM facts WHERE key = ?", (low,)
        ).fetchone()
        if exact:
            return True, f"{exact['key']}: {exact['value']}"

        fts_q = " ".join(re.findall(r"[a-zA-Z0-9]+", low))
        hits = []
        if fts_q:
            try:
                hits = conn.execute(
                    "SELECT f.key, f.value FROM facts_fts "
                    "JOIN facts f ON f.id = facts_fts.rowid "
                    "WHERE facts_fts MATCH ? LIMIT 5",
                    (fts_q,),
                ).fetchall()
            except sqlite3.OperationalError:
                hits = conn.execute(
                    "SELECT key, value FROM facts WHERE key LIKE ? OR value LIKE ? LIMIT 5",
                    (f"%{low}%", f"%{low}%"),
                ).fetchall()
        if not hits:
            hits = conn.execute(
                "SELECT key, value FROM facts WHERE key LIKE ? OR value LIKE ? LIMIT 5",
                (f"%{low}%", f"%{low}%"),
            ).fetchall()

    allow = whatsapp_allowlist()
    if low in allow:
        return True, f"{low}'s WhatsApp number is {allow[low]}."
    for name, phone in allow.items():
        if name in low or low in name:
            return True, f"{name}'s WhatsApp number is {phone}."

    if hits:
        lines = [f"{row['key']}: {row['value']}" for row in hits]
        return True, "From memory: " + "; ".join(lines)
    return False, f"I do not have anything saved about '{q}', sir."


def try_fast_answer(text: str) -> str | None:
    """Return a spoken answer if this is clearly a memory lookup. None = use LLM."""
    low = (text or "").strip().lower()
    if not low:
        return None

    explicit = re.match(
        r"^(?:what do you remember(?: about)?|do you remember|"
        r"what did i (?:tell|say)(?: you)?(?: about)?|"
        r"remind me what|what(?:'s| is) my)\s+(.+)$",
        low,
    )
    if explicit:
        ok, msg = recall(explicit.group(1).strip())
        return msg

    who = re.match(r"^(?:who is|who's)\s+(.+)$", low)
    if who:
        ok, msg = recall(who.group(1).strip())
        if ok:
            return msg

    if re.search(r"\b(?:list|show|what are)\s+(?:my\s+)?(?:saved\s+)?contacts\b", low):
        return list_remembered_contacts()[1]
    if re.search(r"\b(?:list|show|what are)\s+(?:my\s+)?tasks\b", low):
        return list_tasks("open")[1]
    return None


def context_snippet(limit: int = 8) -> str:
    """Tiny block for the LLM router — keep short for latency."""
    with _lock:
        conn = _connect()
        facts = conn.execute(
            "SELECT key, value FROM facts ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT title FROM tasks WHERE status = 'open' ORDER BY id DESC LIMIT 5"
        ).fetchall()
    targets = unique_whatsapp_targets()
    parts = []
    if targets:
        parts.append("WhatsApp-allowed: " + ", ".join(name for name, _ in targets))
    if tasks:
        parts.append("Open tasks: " + ", ".join(row["title"] for row in tasks))
    if facts:
        parts.append(
            "Facts: " + "; ".join(f"{row['key']}={row['value']}" for row in facts)
        )
    return " | ".join(parts)


def parse_remember_text(text: str) -> tuple[str, str]:
    """Split 'remember that X is Y' / 'remember X: Y'."""
    raw = (text or "").strip()
    raw = re.sub(r"^(?:please\s+)?(?:remember|note|save|don't forget)\s+", "", raw, flags=re.I)
    raw = re.sub(r"^that\s+", "", raw, flags=re.I)
    m = re.match(r"(.+?)\s+(?:is|are|=|:)\s+(.+)$", raw, re.I)
    if m:
        return m.group(1).strip(" .,;:"), m.group(2).strip()
    parts = raw.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return raw, ""
