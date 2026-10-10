"""Computer-vision wiring tests (capture → images → vision model → intents).

Hermetic — no live Ollama calls. Verifies multimodal message conversion
(images actually reach the Ollama payload), the vision-model config, and
the screenshot/see_screen router intents that make vision reachable from chat.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage  # noqa: E402

from brain.router import route_command  # noqa: E402
from llm import ollama_client as oc  # noqa: E402


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


PNG_B64 = "iVBORw0KGgoAAAANSUhEUg=="
DATA_URI = f"data:image/png;base64,{PNG_B64}"


def main():
    # ── 1. multimodal LangChain message → images survive conversion ───────
    msg = HumanMessage(content=[
        {"type": "text", "text": "What is on this screen?"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ])
    converted = oc.to_ollama_messages([msg])
    check("multimodal message converts", len(converted) == 1)
    check("text part preserved", converted[0]["content"] == "What is on this screen?")
    check("image attached as base64",
          converted[0].get("images") == [PNG_B64])
    check("data URI prefix stripped", not converted[0]["images"][0].startswith("data:"))

    # ── 2. image_url given as plain string ────────────────────────────────
    msg2 = HumanMessage(content=[
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": DATA_URI},
    ])
    c2 = oc.to_ollama_messages([msg2])
    check("string image_url handled", c2[0].get("images") == [PNG_B64])

    # ── 3. text-only messages unchanged (no images key) ───────────────────
    plain = oc.to_ollama_messages([HumanMessage(content="hello")])
    check("text-only has no images key", "images" not in plain[0])
    check("string input unchanged", oc.to_ollama_messages("hi")
          == [{"role": "user", "content": "hi"}])

    # ── 4. dict (OpenAI-style) messages with list content ─────────────────
    d = oc.to_ollama_messages([{"role": "user", "content": [
        {"type": "text", "text": "see this"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]}])
    check("dict message keeps images", d[0].get("images") == [PNG_B64])
    check("dict text joined", d[0]["content"] == "see this")

    # ── 5. non-string scalar content still stringifies ────────────────────
    check("scalar content stringified",
          oc.to_ollama_messages([{"role": "user", "content": 42}])[0]["content"] == "42")

    # ── 6. vision model config ────────────────────────────────────────────
    check("VISION_MODEL exported", isinstance(oc.VISION_MODEL, str) and oc.VISION_MODEL)
    check("vision model is not the text-only chat model",
          oc.VISION_MODEL != "qwen3:4b")
    check("describe exposes vision tier", "vision:" in oc.describe())

    # ── 7. router: screenshot reachable from chat ─────────────────────────
    for phrase in ("take a screenshot", "grab a screenshot",
                   "capture my screen", "screen capture", "print screen",
                   "screenshot"):
        intent, _ = route_command(phrase)
        check(f"routes screenshot: {phrase!r}", intent == "screenshot")

    # ── 8. router: see_screen (vision describe) ───────────────────────────
    for phrase in ("what do you see on my screen", "what's on my screen",
                   "describe my screen", "can you see my screen",
                   "look at the screen"):
        intent, _ = route_command(phrase)
        check(f"routes see_screen: {phrase!r}", intent == "see_screen")

    # ── 9. regressions: existing intents still win ────────────────────────
    check("qa unchanged", route_command("what is the capital of France")[0] == "qa")
    check("joke unchanged", route_command("tell me a joke")[0] == "joke")
    check("whatsapp routing unchanged",
          route_command("type hi to sathish in whatsapp")[0] == "send_whatsapp")
    check("file search unchanged",
          route_command("find the README file")[0] == "search_files")

    # ── 10. os_agent vision client uses the vision tier ───────────────────
    from executor import os_agent  # noqa: E402
    vision_llm = os_agent._make_vision_llm()
    check("os_agent vision client uses VISION_MODEL",
          vision_llm.model == oc.VISION_MODEL)
    check("os_agent vision client is not the text-only model",
          vision_llm.model != "qwen3:4b")

    # ── 11. verify_via_screenshot: OCR path + vision fallback ─────────────
    import io as _io  # noqa: E402
    import base64 as _b64  # noqa: E402

    from PIL import Image  # noqa: E402
    import executor.task_runner as tr  # noqa: E402

    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), (20, 20, 20)).save(buf, format="PNG")
    png_b64 = _b64.b64encode(buf.getvalue()).decode("ascii")

    class _TesseractMissing(Exception):
        pass

    class FakeOCR:
        @staticmethod
        def image_to_string(img):
            return "Jarvis says the message was sent"

    class FakeBrokenOCR:
        @staticmethod
        def image_to_string(img):
            raise _TesseractMissing("tesseract is not installed")

    orig_capture = tr.mcp_gui.gui_screenshot_region
    orig_ocr = tr.pytesseract
    orig_vision = tr._vision_text_visible
    try:
        tr.mcp_gui.gui_screenshot_region = lambda *a, **k: png_b64

        tr.pytesseract = FakeOCR
        check("OCR hit returns True", tr.verify_via_screenshot("message was sent"))
        check("OCR miss returns False", not tr.verify_via_screenshot("zebra"))

        tr.pytesseract = FakeBrokenOCR
        tr._vision_text_visible = lambda t, img: "sent" in t
        check("OCR failure falls back to vision model",
              tr.verify_via_screenshot("message was sent"))
        check("vision fallback honors model answer",
              not tr.verify_via_screenshot("zebra"))

        tr.pytesseract = None
        tr._vision_text_visible = lambda t, img: t == "ping"
        check("missing OCR module uses vision model", tr.verify_via_screenshot("ping"))
        check("vision-only False propagates", not tr.verify_via_screenshot("pong"))

        # capture failure short-circuits to False (no exception)
        tr.mcp_gui.gui_screenshot_region = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no screen"))
        check("capture failure returns False", not tr.verify_via_screenshot("x"))
    finally:
        tr.mcp_gui.gui_screenshot_region = orig_capture
        tr.pytesseract = orig_ocr
        tr._vision_text_visible = orig_vision

    print("ALL OK")


if __name__ == "__main__":
    main()
