"""Prompt templates kept as individual, editable Markdown files."""

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    if Path(name).name != name or not name.endswith(".md"):
        raise ValueError("Prompt name must be a Markdown file in this directory")
    return (_PROMPT_DIR / name).read_text(encoding="utf-8").strip()
