"""Local SQLite persistence for conversations and audit records."""

from .db import (
    add_message,
    current_conversation_id,
    create_conversation,
    get_user_profile,
    init_db,
    list_recent_messages,
    list_skills,
    list_user_profile,
    log_failure,
    log_tool_call,
    set_current_conversation,
    set_user_profile,
    upsert_skill,
)

__all__ = [
    "add_message", "current_conversation_id", "create_conversation", "init_db",
    "get_user_profile", "list_recent_messages", "list_skills", "list_user_profile",
    "log_failure", "log_tool_call", "set_current_conversation", "set_user_profile",
    "upsert_skill",
]
