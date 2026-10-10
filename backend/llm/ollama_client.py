"""The single chat-completion client used by the JARVIS backend.

Normal requests always send ``think: false`` to local Ollama (loopback-only,
hard-coded below). The planning tier (``think: true``) routes to the Groq
OpenAI-compatible API — a user-approved deviation from the local-only brief
applied to planning calls only; the ordinary chat loop never leaves Ollama.
Set ``JARVIS_PLANNING_PROVIDER=ollama`` to fall back to a local planning
model (``qwen3:8b``).
"""

from __future__ import annotations

import ipaddress
import os
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx

from config import settings

PROVIDER = "ollama"
MODEL = (os.environ.get("JARVIS_LLM_MODEL") or settings.LLM_MODEL or "qwen3:4b").strip()

# Screen-vision tier — models that accept images. The chat MODEL (qwen3:4b)
# rejects multimodal payloads with HTTP 400, so vision call sites must use this.
VISION_MODEL = (os.environ.get("JARVIS_VISION_MODEL") or settings.VISION_MODEL or "qwen3.5:4b").strip()

PLANNING_PROVIDER = (
    os.environ.get("JARVIS_PLANNING_PROVIDER") or settings.PLANNING_PROVIDER or ""
).strip().lower() or ("groq" if settings.GROQ_API_KEY else "ollama")
if PLANNING_PROVIDER not in {"groq", "ollama"}:
    raise ValueError(
        f"Unknown planning provider {PLANNING_PROVIDER!r} (expected 'groq' or 'ollama')"
    )

if PLANNING_PROVIDER == "groq":
    PLANNING_MODEL = (
        os.environ.get("JARVIS_PLANNING_MODEL")
        or settings.GROQ_PLANNING_MODEL or "openai/gpt-oss-20b"
    ).strip()
else:
    PLANNING_MODEL = (
        os.environ.get("JARVIS_PLANNING_MODEL") or settings.PLANNING_MODEL or "qwen3:8b"
    ).strip()


def _local_base() -> str:
    raw = (os.environ.get("OLLAMA_BASE_URL") or settings.OLLAMA_BASE_URL
           or "http://localhost:11434").rstrip("/")
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if (parsed.scheme != "http" or not is_loopback or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Ollama must use an unauthenticated HTTP loopback URL")
    if not parsed.port:
        return raw
    return raw


_OLLAMA_BASE = _local_base()

if not MODEL or MODEL.lower().endswith(":cloud"):
    raise ValueError("JARVIS requires a local Ollama model; cloud model tags are disabled")
if not PLANNING_MODEL:
    raise ValueError("The planning model must not be empty")
if PLANNING_PROVIDER == "ollama" and PLANNING_MODEL.lower().endswith(":cloud"):
    raise ValueError("The planning model must be a local Ollama model")


def base_url() -> str:
    return f"{_OLLAMA_BASE}/api/chat"


def headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def request_timeout() -> float:
    """Allow time for an initial local model load while keeping calls bounded."""
    override = os.environ.get("JARVIS_LLM_TIMEOUT")
    if override:
        try:
            return max(1.0, float(override))
        except ValueError:
            pass
    return 90.0


def _num_ctx() -> int:
    try:
        return max(2048, min(32768, int(settings.LLM_NUM_CTX or 8192)))
    except (TypeError, ValueError):
        return 8192


# ── Groq planning transport (think:true only) ──────────────────────────────

def _groq_key() -> str:
    key = (os.environ.get("GROQ_API_KEY") or settings.GROQ_API_KEY or "").strip()
    if not key:
        raise RuntimeError(
            "PLANNING_PROVIDER=groq but GROQ_API_KEY is not set — "
            "add it to backend/.env or set JARVIS_PLANNING_PROVIDER=ollama"
        )
    return key


def groq_base() -> str:
    raw = (
        os.environ.get("GROQ_API_BASE") or settings.GROQ_API_BASE
        or "https://api.groq.com/openai/v1"
    ).rstrip("/")
    return raw + "/chat/completions"


def groq_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_groq_key()}", "Content-Type": "application/json"}


def _use_groq(think: bool, model: str | None) -> bool:
    """Route to Groq for planning calls only; chat stays on loopback Ollama."""
    if PLANNING_PROVIDER != "groq":
        return False
    return bool(think) or (model or MODEL) == PLANNING_MODEL


def build_groq_payload(
    messages,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    model: str | None = None,
) -> dict:
    """OpenAI-style chat payload — no Ollama-only keys (think/keep_alive)."""
    selected = (model or PLANNING_MODEL).strip()
    if not selected:
        raise ValueError("The Groq planning model must not be empty")
    return {
        "model": selected,
        "messages": to_ollama_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }


def _groq_error(response: "httpx.Response") -> str:
    try:
        payload = response.json()
        message = str((payload.get("error") or {}).get("message") or payload)[:300]
    except Exception:
        message = (response.text or "")[:300]
    hint = ""
    if response.status_code in (401, 403):
        hint = " — check GROQ_API_KEY in backend/.env"
    elif response.status_code == 404:
        hint = " — check GROQ_PLANNING_MODEL (model not available on this key)"
    return f"Groq planning request failed ({response.status_code}): {message}{hint}"


# gpt-oss spends a few hundred tokens reasoning before it writes content;
# budgets below this can yield HTTP 200 with empty content.
_GROQ_MIN_BUDGET = 512


def _groq_reasoning_fallback(data: dict) -> str:
    """Last resort when even a workable budget yields no content.

    Returns the raw reasoning trace — imperfect, but a truncated plan beats
    returning an empty string to the caller.
    """
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    if isinstance(reasoning, str):
        return reasoning.strip()
    return ""


def _groq_budgets(max_tokens: int) -> list[int]:
    """Request budgets: the caller's value first, then one workable retry."""
    budget = int(max_tokens or 0)
    if 0 < budget < _GROQ_MIN_BUDGET:
        return [budget, _GROQ_MIN_BUDGET]
    return [budget]


def _groq_chat(messages, *, temperature: float, max_tokens: int,
               timeout: float | None, model: str | None) -> str:
    # Groq planning output IS reasoning; keep it raw (think=True).
    with httpx.Client(timeout=timeout or request_timeout()) as client:
        data: dict = {}
        for budget in _groq_budgets(max_tokens):
            payload = build_groq_payload(messages, temperature=temperature,
                                         max_tokens=budget, model=model)
            response = client.post(groq_base(), headers=groq_headers(), json=payload)
            if response.status_code >= 400:
                raise RuntimeError(_groq_error(response))
            data = response.json()
            text = _extract_text(data, think=True)
            if text:
                return text
        return _groq_reasoning_fallback(data)


async def _groq_achat(messages, *, temperature: float, max_tokens: int,
                      timeout: float | None, model: str | None) -> str:
    async with httpx.AsyncClient(timeout=timeout or request_timeout()) as client:
        data: dict = {}
        for budget in _groq_budgets(max_tokens):
            payload = build_groq_payload(messages, temperature=temperature,
                                         max_tokens=budget, model=model)
            response = await client.post(groq_base(), headers=groq_headers(), json=payload)
            if response.status_code >= 400:
                raise RuntimeError(_groq_error(response))
            data = response.json()
            text = _extract_text(data, think=True)
            if text:
                return text
        return _groq_reasoning_fallback(data)


def _split_multimodal(content) -> tuple[str, list[str]]:
    """Flatten OpenAI/LangChain-style content into (text, base64 images).

    Ollama messages carry images as a top-level ``images`` list of raw
    base64 strings; data-URI prefixes are stripped.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            texts.append(str(part))
            continue
        kind = part.get("type")
        if kind in ("image_url", "image"):
            if kind == "image_url":
                src = part.get("image_url", "")
                url = src.get("url", "") if isinstance(src, dict) else str(src)
            else:
                url = str(part.get("image") or part.get("url") or "")
            url = url.strip()
            if url.startswith("data:") and "," in url:
                url = url.split(",", 1)[1]
            if url:
                images.append(url)
        else:
            texts.append(str(part.get("text", part)))
    return "\n".join(t for t in texts if t), images


def to_ollama_messages(messages) -> list[dict]:
    """Convert strings, OpenAI-style dicts, or LangChain messages."""
    if messages is None:
        return []
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, dict):
        content, images = _split_multimodal(messages.get("content", ""))
        msg: dict = {"role": messages.get("role", "user"), "content": content}
        if images:
            msg["images"] = images
        return [msg]

    converted: list[dict] = []
    for message in messages:
        if isinstance(message, str):
            converted.append({"role": "user", "content": message})
            continue
        if isinstance(message, dict):
            content, images = _split_multimodal(message.get("content", ""))
            msg = {"role": message.get("role", "user"), "content": content}
            if images:
                msg["images"] = images
            converted.append(msg)
            continue
        role = getattr(message, "type", None) or message.__class__.__name__
        role = {
            "human": "user", "HumanMessage": "user",
            "ai": "assistant", "AIMessage": "assistant",
            "system": "system", "SystemMessage": "system",
        }.get(role, "user")
        content, images = _split_multimodal(getattr(message, "content", ""))
        msg = {"role": role, "content": content}
        if images:
            msg["images"] = images
        converted.append(msg)
    return converted


def build_payload(
    messages,
    *,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    model: str | None = None,
    think: bool = False,
) -> dict:
    selected_model = model or MODEL
    if selected_model.lower().endswith(":cloud"):
        raise ValueError("Cloud Ollama model tags are disabled")
    if think and selected_model != PLANNING_MODEL:
        raise ValueError("Thinking mode is reserved for the configured planning model")
    ollama_messages = to_ollama_messages(messages)
    if not think and selected_model.startswith("qwen3"):
        # Qwen3 uses internal reasoning (<think>...</think>).
        # We deliberately do NOT inject /no_think because /no_think causes
        # Qwen3 to emit its chain-of-thought in plain text without tags.
        # Allowing native <think> tags lets _extract_text cleanly strip the trace.
        max_tokens = max(max_tokens, 1024)
        if temperature > 0.35:
            temperature = 0.25
    return {
        "model": selected_model,
        "messages": ollama_messages,
        "stream": False,
        "think": bool(think),
        "keep_alive": "30m",
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": _num_ctx(),
        },
    }


_LEAK_FIRST_LINE_PATTERNS = (
    r"^okay[,.]?\s+(the user|so|let)",
    r"^let me (think|check|consider|reason|figure|analyze|craft|write|aim|go|respond)",
    r"^(first|hmm|wait|alright)[,.]?\s",
    r"^(i need to|i should|the question is|the user|we are in the role)",
    r"^in the (problem|question|statement|scenario)",
    r"^so[,.]?\s+(the|this|i|we|let)",
    # Planning narration the model writes before it settles on an answer.
    r"^we are (given|to reply|to answer|instructed|asked|responding|told)",
    r"^the user (says|is|responded|asked|replied|wants|mentioned)",
    r"^(approach|final response|final answer|revised|option|draft|plan|note)[:\s]",
    r"^let's (craft|go with|write|aim|try|consider|be|respond|reply)",
    r"^(we must|we should|we are constrained|we don't|we are not)",
)

_META_DELIBERATION_MARKERS = (
    "final response",
    "final answer:",
    "let's go with",
    "let's craft",
    "let's write",
    "the instruction says",
    "reply naturally",
    "do not greet",
    "1-3 sentences",
    "one or two short",
    "we are constrained",
    "revised idea",
    "stay in character",
    "chain of thought",
    "chain-of-thought",
)

_FINAL_ANSWER_MARKER = re.compile(
    r"(?:final\s+response(?:\s+idea[^:*)]*)?|final\s+answer|let's\s+go\s+with|answer)\s*:\s*",
    re.IGNORECASE,
)


def _looks_like_leaked_thinking(text: str) -> bool:
    """Detect Qwen3's internal reasoning leaked without <think> tags.

    The model sometimes outputs its chain-of-thought planning verbatim.
    Common patterns:
      - "Okay, the user asked …"
      - "Let me think …"
      - "First, I need to …"
      - "Wait, the instruction says …"
      - "We are in the role of JARVIS …"
      - "Hmm, …"
    These are internal reasoning steps, not spoken JARVIS answers.
    """
    import re
    if not text:
        return False
    first_line = text.split("\n", 1)[0].strip().lower()
    if any(re.match(pattern, first_line) for pattern in _LEAK_FIRST_LINE_PATTERNS):
        return True
    lowered = text.lower()
    hits = sum(1 for marker in _META_DELIBERATION_MARKERS if marker in lowered)
    return hits >= 2


def _is_clean_answer(candidate: str) -> bool:
    """True when a rescued line can be spoken as a JARVIS answer."""
    if not candidate or len(candidate) < 3 or len(candidate) > 420 or "\n" in candidate:
        return False
    lowered = candidate.lower()
    if any(marker in lowered for marker in _META_DELIBERATION_MARKERS):
        return False
    return not _looks_like_leaked_thinking(candidate)


def _salvage_final_answer(text: str) -> str:
    """Pull the settled answer out of a leaked reasoning trace, if one exists."""
    candidates = []
    for match in _FINAL_ANSWER_MARKER.finditer(text):
        tail = text[match.end():]
        line = next((ln.strip() for ln in tail.splitlines() if ln.strip()), "")
        line = line.strip().strip("\"'“”‘’").strip()
        if line:
            candidates.append(line)
    for candidate in reversed(candidates):
        if _is_clean_answer(candidate):
            return candidate
    return ""


_THINK_TAGS = (
    ("<" + "think>", "<" + "/" + "think>"),
    ("<" + "thinking>", "<" + "/" + "thinking>"),
)


def _strip_thinking(text: str) -> tuple[str, bool]:
    """Drop local reasoning tags from a completion.

    Returns ``(text, truncated)``. ``truncated`` is True when the trace opened
    but never closed, meaning the visible tail may still be unfinished reasoning.
    """
    for tag_open, tag_close in _THINK_TAGS:
        if tag_close in text:
            return text.rsplit(tag_close, 1)[-1].strip(), False
        if tag_open in text:
            return text, True
    return text, False


def _extract_text(data: dict, *, think: bool = False) -> str:
    message = data.get("message") or {}
    content = message.get("content")
    if content is None:
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content.strip()
        if not think:
            text, truncated_trace = _strip_thinking(text)
            if truncated_trace:
                # Model ran out of tokens before closing the reasoning tag.
                # Try to rescue any final conclusion or answer from the trace.
                m = re.search(r'(?:final decision|answer is|in conclusion|so)[:\s]+["\']?([^"\n\r]+)', text, re.I)
                if m:
                    return m.group(1).strip().strip('"\'')
                return _salvage_final_answer(text) or ""
            if _looks_like_leaked_thinking(text):
                return _salvage_final_answer(text) or ""
        if not think and data.get("done_reason") == "length" and not text:
            return ""
        return text
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        ).strip()
    return ""


def complete(messages, *, temperature: float = 0.25, max_tokens: int = 1024,
             timeout: float | None = None, think: bool = False,
             model: str | None = None) -> str:
    """Blocking chat completion; thinking is off unless explicitly passed.

    think=True (planning) routes to Groq when PLANNING_PROVIDER=groq; all
    other calls post to the loopback Ollama endpoint."""
    if think and (model or MODEL) != PLANNING_MODEL:
        raise ValueError("Thinking mode is reserved for the configured planning model")
    if _use_groq(think, model):
        return _groq_chat(messages, temperature=temperature, max_tokens=max_tokens,
                          timeout=timeout, model=model)
    payload = build_payload(messages, temperature=temperature, max_tokens=max_tokens,
                            think=think, model=model)
    with httpx.Client(timeout=timeout or request_timeout()) as client:
        response = client.post(base_url(), headers=headers(), json=payload)
        response.raise_for_status()
        return _extract_text(response.json(), think=think)


async def acomplete(messages, *, temperature: float = 0.25, max_tokens: int = 1024,
                    timeout: float | None = None, think: bool = False,
                    model: str | None = None) -> str:
    """Async chat completion; thinking is off unless explicitly passed."""
    if think and (model or MODEL) != PLANNING_MODEL:
        raise ValueError("Thinking mode is reserved for the configured planning model")
    if _use_groq(think, model):
        return await _groq_achat(messages, temperature=temperature,
                                 max_tokens=max_tokens, timeout=timeout, model=model)
    payload = build_payload(messages, temperature=temperature, max_tokens=max_tokens,
                            think=think, model=model)
    async with httpx.AsyncClient(timeout=timeout or request_timeout()) as client:
        response = await client.post(base_url(), headers=headers(), json=payload)
        response.raise_for_status()
        return _extract_text(response.json(), think=think)


class ChatLLM:
    """Small LangChain-compatible adapter for existing JARVIS call sites."""

    def __init__(self, *, temperature: float = 0.3, max_tokens: int = 1024,
                 model: str | None = None, think: bool = False):
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model or MODEL
        self.think = bool(think)
        if self.think and self.model != PLANNING_MODEL:
            raise ValueError("Thinking mode is reserved for the configured planning model")

    def _payload(self, messages) -> dict:
        return build_payload(
            messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            model=self.model,
            think=self.think,
        )

    def _groq_kwargs(self) -> dict:
        return {"temperature": self.temperature, "max_tokens": self.max_tokens,
                "timeout": None, "model": self.model}

    def invoke(self, messages):
        if _use_groq(self.think, self.model):
            return SimpleNamespace(
                content=_groq_chat(messages, **self._groq_kwargs()), type="ai")
        with httpx.Client(timeout=request_timeout()) as client:
            response = client.post(base_url(), headers=headers(), json=self._payload(messages))
            response.raise_for_status()
            return SimpleNamespace(content=_extract_text(response.json(), think=self.think), type="ai")

    async def ainvoke(self, messages):
        if _use_groq(self.think, self.model):
            return SimpleNamespace(
                content=await _groq_achat(messages, **self._groq_kwargs()), type="ai")
        async with httpx.AsyncClient(timeout=request_timeout()) as client:
            response = await client.post(base_url(), headers=headers(), json=self._payload(messages))
            response.raise_for_status()
            return SimpleNamespace(content=_extract_text(response.json(), think=self.think), type="ai")


def planning_client(*, temperature: float = 0.2, max_tokens: int = 1400) -> ChatLLM:
    """Create the opt-in planning client; it is never used for ordinary chat."""
    return ChatLLM(temperature=temperature, max_tokens=max_tokens,
                   model=PLANNING_MODEL, think=True)


def describe() -> str:
    planning = (f"groq:{PLANNING_MODEL}" if PLANNING_PROVIDER == "groq"
                else f"ollama:{PLANNING_MODEL}")
    return f"{PROVIDER}:{MODEL} @ {base_url()} | planning: {planning} | vision: {VISION_MODEL}"
