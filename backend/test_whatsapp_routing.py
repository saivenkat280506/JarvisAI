"""Router / contact-resolve tests. Does not send any WhatsApp messages."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.router import route_command
from executor.automation import resolve_whatsapp_number


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


cases = [
    ("Open Watsapp and send Satish. Bye.", "satish", "bye"),
    ("open whatsapp and send Satish hi. Satish.", "satish", "hi"),
    ("open whatsapp and send Sadeesh hi", "sadeesh", "hi"),
    ("open whatsapp and send sathish hello from jarvis", "sathish", "hello from jarvis"),
    ("send message to sathish hi jarvis test", "sathish", "hi jarvis test"),
    ("send sathish hello", "sathish", "hello"),
    ("open whatsapp and send Satish", "satish", ""),
    ("whatsapp sathish", "sathish", ""),
    ("open whatsapp and send Satish hi", "satish", "hi"),
    ("send hi to sathish", "sathish", "hi"),
]

for text, name, message in cases:
    intent, params = route_command(text)
    check(f"{text!r} -> send_whatsapp", intent == "send_whatsapp")
    check(f"{text!r} name={params.get('name')!r}", (params or {}).get("name", "").lower() == name)
    check(f"{text!r} message={params.get('message')!r}", (params or {}).get("message", "") == message)

intent, params = route_command("open whatsapp")
check("open whatsapp is not a send", intent != "send_whatsapp")

ok, phone, label, err = resolve_whatsapp_number("sathish")
check("resolve sathish", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Sadeesh")
check(f"resolve Sadeesh -> {phone} {err}", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Satish", "+91...")
check("ignore placeholder +91...", ok and phone.endswith("8519929108"))

ok, phone, label, err = resolve_whatsapp_number("Satish", number="+91...")
check("ignore explicit placeholder", ok and phone.endswith("8519929108"))

print("\nAll routing tests passed.")
