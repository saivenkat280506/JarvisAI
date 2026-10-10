"""Two-stage intent routing: deterministic rules, then the resident Ollama model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    parameters: dict
    source: Literal["rule", "ollama"]


def route_intent(user_input: str, context: str = "") -> IntentDecision:
    """Return a normalized intent without executing any system action."""
    from brain.router import route_command

    intent, parameters = route_command(user_input)
    if intent:
        return IntentDecision(
            intent=str(intent), parameters=parameters or {}, source="rule"
        )

    from brain.llm_brain import decide_action

    decision = decide_action(user_input, context)
    intent = str(decision.get("intent") or "chat").strip()
    parameters = decision.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}
    return IntentDecision(intent=intent, parameters=parameters, source="ollama")
