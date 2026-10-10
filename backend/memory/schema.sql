PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    intent TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
    ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS tool_call_log (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    intent TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_text TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_call_time ON tool_call_log(started_at);

CREATE TABLE IF NOT EXISTS failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    intent TEXT NOT NULL,
    approach TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL,
    attempt_chain_json TEXT NOT NULL DEFAULT '[]',
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failure_time ON failure_log(created_at);

CREATE TABLE IF NOT EXISTS skills_registry (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
