"""Pure retry-loop control flow. Execution and network access stay elsewhere."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RetryState(str, Enum):
    READY = "ready"
    DIRECT = "direct"
    ALTERNATE = "alternate"
    WEB_SEARCH = "web_search"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class Attempt:
    strategy: str
    succeeded: bool
    error: str = ""


@dataclass
class RetryStateMachine:
    """Select direct → alternate → fetch-only search → failure, without actions."""

    attempts: list[Attempt] = field(default_factory=list)
    state: RetryState = RetryState.READY

    def next_strategy(self) -> str | None:
        if self.state in {RetryState.SUCCEEDED, RetryState.FAILED}:
            return None
        if not self.attempts:
            self.state = RetryState.DIRECT
            return "direct"
        if self.state is RetryState.DIRECT:
            self.state = RetryState.ALTERNATE
            return "alternate"
        if self.state is RetryState.ALTERNATE:
            self.state = RetryState.WEB_SEARCH
            return "web_search"
        if self.state is RetryState.WEB_SEARCH:
            self.state = RetryState.FAILED
            return None
        return None

    def record(self, strategy: str, *, succeeded: bool, error: str = "") -> None:
        expected = self.state.value
        if strategy != expected:
            raise ValueError(f"Expected retry strategy '{expected}', got '{strategy}'")
        self.attempts.append(Attempt(strategy, bool(succeeded), str(error)[:800]))
        if succeeded:
            self.state = RetryState.SUCCEEDED

    @property
    def complete(self) -> bool:
        return self.state in {RetryState.SUCCEEDED, RetryState.FAILED}
