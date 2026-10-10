"""Compatibility imports for older modules; implementation lives in llm/."""

from llm.ollama_client import (
    MODEL,
    PROVIDER,
    VISION_MODEL,
    ChatLLM,
    acomplete,
    base_url,
    build_payload,
    complete,
    describe,
    headers,
    request_timeout,
    to_ollama_messages,
)

to_openai_messages = to_ollama_messages

__all__ = [
    "MODEL", "PROVIDER", "VISION_MODEL", "ChatLLM", "acomplete", "base_url",
    "build_payload", "complete", "describe", "headers", "request_timeout",
    "to_ollama_messages", "to_openai_messages",
]
