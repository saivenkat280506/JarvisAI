"""Groq planning-tier tests (user-approved deviation: think:true → Groq).

Hermetic — HTTP is faked. Verifies: routing isolation (chat never leaves
loopback Ollama), payload shape, guards, error hints, and key handling.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from llm import ollama_client as oc  # noqa: E402


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = json.dumps(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}",
                                        request=None, response=None)

    def json(self):
        return self._data


def main():
    # ── 1. configuration ───────────────────────────────────────────────────
    check("provider is groq by default", oc.PLANNING_PROVIDER == "groq")
    check("planning model is a groq id", "/" in oc.PLANNING_MODEL)
    check("planning model not an ollama :cloud tag",
          not oc.PLANNING_MODEL.lower().endswith(":cloud"))
    check("groq base is the official API", oc.groq_base().startswith(
        "https://api.groq.com/openai/v1/chat/completions"))

    # ── 2. routing isolation (§0.1 for the chat loop) ──────────────────────
    check("think=True routes to groq", oc._use_groq(True, None) is True)
    check("chat (think=False) stays local",
          oc._use_groq(False, None) is False)
    check("explicit planning model routes to groq",
          oc._use_groq(False, oc.PLANNING_MODEL) is True)
    check("loopback base enforced",
          oc.base_url().startswith("http://localhost:11434"))

    # ── 3. payload shape ───────────────────────────────────────────────────
    payload = oc.build_groq_payload(
        [{"role": "user", "content": "plan it"}], max_tokens=64)
    check("payload has model", payload["model"] == oc.PLANNING_MODEL)
    check("payload is OpenAI-shaped",
          payload["messages"] == [{"role": "user", "content": "plan it"}])
    check("no ollama-only keys",
          not any(k in payload for k in ("think", "keep_alive", "options")))
    check("stream disabled", payload["stream"] is False)

    # ── 4. complete(think=True) → groq, never Ollama ───────────────────────
    captured = {}

    def fake_groq(messages, **kwargs):
        captured.update(kwargs)
        captured["messages"] = messages
        return "PLAN_OK"

    def forbid_post(*args, **kwargs):
        raise AssertionError("chat-loop HTTP must not fire on a planning call")

    orig_groq_chat = oc._groq_chat
    orig_post = httpx.Client.post
    oc._groq_chat = fake_groq
    httpx.Client.post = forbid_post
    try:
        out = oc.complete("make a plan", think=True, model=oc.PLANNING_MODEL,
                          max_tokens=64)
        check("planning completion routed to groq", out == "PLAN_OK")
        check("planning model passed through",
              captured.get("model") == oc.PLANNING_MODEL)
    finally:
        oc._groq_chat = orig_groq_chat
        httpx.Client.post = orig_post

    # ── 5. think=False still posts to loopback Ollama ──────────────────────
    seen = {}

    def fake_local(self, url, headers=None, json=None, **kwargs):
        seen["url"] = url
        seen["json"] = json
        return FakeResponse({"message": {"content": "LOCAL_OK"}})

    httpx.Client.post = fake_local
    try:
        out = oc.complete("hello")
        check("chat stays on loopback", seen["url"].startswith(
            "http://localhost:11434"))
        check("chat payload think=false", seen["json"]["think"] is False)
        check("chat model is the local model", seen["json"]["model"] == oc.MODEL)
        check("chat answer extracted", out == "LOCAL_OK")
    finally:
        httpx.Client.post = orig_post

    # ── 6. guards ──────────────────────────────────────────────────────────
    try:
        oc.complete("x", think=True, model=oc.MODEL)
        check("think with non-planning model raises", False)
    except ValueError:
        check("think with non-planning model raises", True)

    try:
        oc.ChatLLM(think=True, model=oc.MODEL)
        check("ChatLLM guards planning model", False)
    except ValueError:
        check("ChatLLM guards planning model", True)

    # ── 7. ChatLLM(think=True) routes to groq ──────────────────────────────
    oc._groq_chat = fake_groq
    httpx.Client.post = forbid_post
    try:
        planner = oc.planning_client(max_tokens=32)
        ns = planner.invoke("plan")
        check("planning_client returns groq output", ns.content == "PLAN_OK")
        check("planning_client is think=True", planner.think is True)
    finally:
        oc._groq_chat = orig_groq_chat
        httpx.Client.post = orig_post

    # async planning route
    async def fake_agroq(messages, **kwargs):
        return "ASYNC_PLAN_OK"

    orig_agroq = oc._groq_achat
    orig_apost = httpx.AsyncClient.post
    oc._groq_achat = fake_agroq
    httpx.AsyncClient.post = forbid_post
    try:
        out = asyncio.run(oc.acomplete("plan", think=True, model=oc.PLANNING_MODEL))
        check("async planning routed to groq", out == "ASYNC_PLAN_OK")
    finally:
        oc._groq_achat = orig_agroq
        httpx.AsyncClient.post = orig_apost

    # ── 8. error hints ─────────────────────────────────────────────────────
    msg401 = oc._groq_error(FakeResponse({"error": {"message": "Invalid API Key"}},
                                         status=401))
    check("401 hints at the key", "GROQ_API_KEY" in msg401 and "401" in msg401)
    msg404 = oc._groq_error(FakeResponse(
        {"error": {"message": "model does not exist"}}, status=404))
    check("404 hints at the model", "GROQ_PLANNING_MODEL" in msg404)

    # ── 9. missing key fails with an actionable error ──────────────────────
    orig_key = oc.settings.GROQ_API_KEY
    orig_env = os.environ.pop("GROQ_API_KEY", None)
    oc.settings.GROQ_API_KEY = ""
    try:
        oc._groq_key()
        check("missing key raises actionable error", False)
    except RuntimeError as exc:
        check("missing key raises actionable error",
              "GROQ_API_KEY" in str(exc) and "JARVIS_PLANNING_PROVIDER=ollama" in str(exc))
    finally:
        oc.settings.GROQ_API_KEY = orig_key
        if orig_env is not None:
            os.environ["GROQ_API_KEY"] = orig_env

    # ── 10. empty-content recovery (gpt-oss reasoning eats max_tokens) ─────
    tight = {
        "choices": [{"message": {"role": "assistant", "content": "",
                                 "reasoning": "STEP_ONE then STEP_TWO PLAN_DONE"}}]
    }
    check("reasoning recovered when content empty",
          oc._groq_reasoning_fallback(tight) == "STEP_ONE then STEP_TWO PLAN_DONE")
    check("no fallback when no reasoning",
          oc._groq_reasoning_fallback(
              {"choices": [{"message": {"content": ""}}]}) == "")
    check("tight budget retries once",
          oc._groq_budgets(64) == [64, oc._GROQ_MIN_BUDGET])
    check("adequate budget does not retry",
          oc._groq_budgets(1400) == [1400])

    posts = []

    def fake_tight_post(self, url, headers=None, json=None, **kwargs):
        posts.append(json)
        return FakeResponse(tight)

    def fake_full_post(self, url, headers=None, json=None, **kwargs):
        posts.append(json)
        return FakeResponse({
            "choices": [{"message": {"role": "assistant", "content": "THE_PLAN",
                                     "reasoning": "internal trace"}}]
        })

    # empty first AND retry → fall back to reasoning, two requests made
    posts.clear()
    httpx.Client.post = fake_tight_post
    try:
        out = oc._groq_chat("plan", temperature=0.2, max_tokens=64,
                            timeout=10, model=oc.PLANNING_MODEL)
        check("_groq_chat falls back to reasoning after retry",
              out == "STEP_ONE then STEP_TWO PLAN_DONE")
        check("tight budget issued two requests", len(posts) == 2)
        check("retry used a workable budget",
              posts[1]["max_tokens"] == oc._GROQ_MIN_BUDGET)
    finally:
        httpx.Client.post = orig_post

    # empty first, full content on the retry → real plan, no meta trace
    sequence = [FakeResponse(tight), FakeResponse({
        "choices": [{"message": {"role": "assistant", "content": "THE_PLAN",
                                 "reasoning": "internal trace"}}]
    })]

    def fake_sequence_post(self, url, headers=None, json=None, **kwargs):
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    httpx.Client.post = fake_sequence_post
    try:
        out = oc._groq_chat("plan", temperature=0.2, max_tokens=64,
                            timeout=10, model=oc.PLANNING_MODEL)
        check("retry recovers a real plan over meta narration",
              out == "THE_PLAN")
    finally:
        httpx.Client.post = orig_post

    # adequate budget → single request, content preferred over reasoning
    posts.clear()
    httpx.Client.post = fake_full_post
    try:
        out = oc._groq_chat("plan", temperature=0.2, max_tokens=1400,
                            timeout=10, model=oc.PLANNING_MODEL)
        check("content preferred over reasoning when present", out == "THE_PLAN")
        check("adequate budget issued one request", len(posts) == 1)
        check("single request kept the caller budget",
              posts and posts[0]["max_tokens"] == 1400)
    finally:
        httpx.Client.post = orig_post

    # ── 11. ollama planning fallback still available ───────────────────────
    check("describe exposes both tiers", "planning: groq:" in oc.describe())

    print("ALL OK")


if __name__ == "__main__":
    main()
