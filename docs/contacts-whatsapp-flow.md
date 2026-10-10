# Contacts Resolution + WhatsApp Search Flow

Design companion to [build-plan-v2 §4 — Computer Control](build-plan-v2.md#4-computer-control-replaces-all-app-apis-confirmed). It sits *before* the WhatsApp vision loop: resolve the person first, open the chat second, and only then let anything touch a text field.

Line references are to the tree as of 2026-10-04.

---

## 1. Contacts source + local cache

Both write to the same SQLite tables; lookup never cares which source a row came from.

| Source | Store | State |
| --- | --- | --- |
| Curated JSON (`backend/executor/whatsapp_contacts.json`) | seeded into `contacts` by `_seed_whatsapp_json` (`brain/memory_store.py:132`) | exists |
| SQLite `contacts` + `contact_aliases` (`brain/memory_store.py:62`) | the real cache: `name` (UNIQUE, NOCASE), `phone`, `allow_whatsapp`, `notes`; `alias → contact_id` | exists |
| `whatsapp_allowlist()` (`brain/memory_store.py:192`) | rows with `allow_whatsapp = 1` only | exists |
| macOS Contacts.app via `osascript` | — | **missing** |
| Spoken teach-in ("remember Sathish's number is X") | — | **missing** — `remember` routes to `remember_fact` (`brain/memory_store.py:242`), which writes the `facts` table, not `contacts` |

**Sync from macOS Contacts (recommended).** `osascript` pulls name/number pairs on demand ("refresh contacts") and on a schedule (launchd), caching into `contacts` / `contact_aliases`. AppleScript is too slow to hit live on every voice command — hence the cache. Two rules:

- **Sync must not grant permission.** `allow_whatsapp` is a consent flag, not a cache flag: a sync inserts rows with `allow_whatsapp = 0`, so `resolve_whatsapp_number` (`executor/automation.py:286`) keeps refusing them until the user explicitly allows messaging that person. Anything else silently widens the blast radius the allowlist exists to contain.
- **No live queries in the hot path.** Voice command → SQLite only; a failed/stale sync degrades to the last cached rows.

**Manual teach-in.** "remember Sathish's number is X" should upsert `contacts` (canonical name + `allow_whatsapp = 1`, because the user is naming the person for WhatsApp) plus a `contact_aliases` row for STT spellings (Satish, Sadeesh). The JSON file then becomes seed-only and is retired as a live source — today the JSON is applied *second* and overwrites allowlist rows inside `_load_whatsapp_contacts` (`executor/automation.py:239`) — two sources that can disagree, with the file winning.

---

## 2. Name extraction + fuzzy match

Router extracts the name entity (`_split_contact_and_message`, `brain/router.py:124`), then resolution ladder:

```
"search for sathish"
  → exact contacts.name (NOCASE)          → use
  → exact contact_aliases.alias            → use
  → fuzzy over name + aliases              →
        one strong match   (score ≥ T)     → use
        ≥2 plausible matches              → ask "Which Sathish, sir?"
        no match                          → "I don't have a number for X, sir."
                                             → ask for the number → teach-in saves it
```

- Today's fuzzy is `difflib.get_close_matches(cutoff=0.55)` inside `_split_contact_and_message`, which always returns *one* match — it cannot express ambiguity. Move to a ranked score (trigram / Levenshtein) over `name` and `aliases` so ties are detectable.
- The ambiguity branch is the point of the exercise: it stores the candidate list in memory for one turn ("which Sathish, sir?") and only commits on the answer.
- **Never fall through to the vision loop with a guessed number.** Unresolved target is a spoken refusal + offer to save the number, exactly like the existing `resolve_whatsapp_number` placeholder/block handling (`executor/automation.py:300`–`325`).
- STT echo already gets one pass: `_strip_trailing_contact` (`brain/router.py:185`) drops a differently-spelled repeat of the contact at the end of a message ("hi. satish" → "hi").

---

## 3. Opening the chat — primary path (deep link, what runs today)

```
resolved number from contacts
  → whatsapp://send?phone=<digits>&text=<draft>     (_open_chat_by_protocol, automation.py:424)
  → screenshot → verify: chat header = contact AND compose box holds the draft
  → opened_via = "deeplink"
  → narrate: "Sathish's chat is opened, sir."
```

This is what `prepare_whatsapp_message` (`executor/automation.py:557`) already does. It lands straight on the chat, **no search field is ever touched**, and send stays frozen (`WHATSAPP_SEND_FROZEN`, `automation.py:137`) — the draft sits in the compose box and is never sent.

### 3b. Fallback — search-field path (only when the deep link fails)

Built as a fallback, not a replacement. If the protocol URL doesn't open the chat (app not registered, WhatsApp version quirk), resolve → search:

```
resolved number from contacts
  → copy to clipboard (never typed digit-by-digit: faster, no digit typos)
  → open/focus WhatsApp (Tier 1 AppleScript / already focused)
  → click the SEARCH field
  → cmd+V the number
  → screenshot → verify a matching chat result appeared
  → click the result to open the chat
  → clear the search field (select-all + delete, or Escape) — only at this point
  → re-screenshot → verify search field empty AND correct chat now open
  → opened_via = "search"
  → narrate: "Sathish's chat is opened, sir."
```

---

## 4. Conditional clear — `if opened_via == "search"`

The clear step is about *residue*, not about *which branch of code* ran, so it is one conditional, not a second code path:

```python
if opened_via == "search":
    clear_search_field()   # scoped to the search AX element, see §5
```

| `opened_via` | Search field used? | Residue? | Clear? |
| --- | --- | --- | --- |
| `deeplink` (`wa.me` / protocol URL) | no | none | skip |
| `sidebar` (pinned chat clicked directly) | no | none | skip |
| `search` (number pasted into search) | yes | the number sits there | required |

Building it as one conditional keeps the paths in sync: if a fourth way to open a chat appears later, it only has to declare its `opened_via`.

---

## 5. Field isolation (search bar vs compose box)

WhatsApp Desktop has two distinct text fields. Mixing them up is the failure mode this section exists to prevent:

- Every field-touching step names the **accessibility element it targets** — search bar or compose box — and a fresh screenshot is taken **before** the next instruction is issued.
- `clear_search_field()` is scoped to the search element only. It never runs against the compose box: that is how you clear a message you already typed by accident.
- Before any typing step (e.g. "type hi sathish…"), verify by screenshot that the compose box is the focused element and is empty — if it already holds a draft, stop and ask rather than append.
- Confirm-by-screenshot discipline from the vision loop is what makes this safe; a step is not "done" because the keystrokes were sent, it is done because the screenshot shows the intended state.

---

## 6. Verification

- **Routing:** `backend/tests/test_whatsapp_routing.py` — covers `send` / `text` / `type` / `write` phrasings, STT name variants, all-contacts refusal, and negatives ("type the report into the terminal" must not route to WhatsApp). Run: `backend/.venv/bin/python tests/test_whatsapp_routing.py`.
- **To add:** resolution-ladder tests (exact → alias → fuzzy → ambiguous → none), an `opened_via`-driven clear/no-clear test, and a dry-run mode for §3b that stops before the first click.
- **Manual acceptance:** say "can you just type hi to sathish in whatsapp" → chat opens with `hi` in the compose box, search bar untouched, send untouched.

---

## 7. Open decisions

1. Does contact sync exist at all in v1, or is the curated JSON + teach-in enough for now? (Today only the JSON exists.)
2. Fuzzy threshold and how many candidates count as "plausible" before asking.
3. Is §3b (search fallback) worth building while the deep link works? My read: not yet — build it the first time `_open_chat_by_protocol` fails to land on the chat, and keep `opened_via` in the return tuple from day one so the conditional in §4 is already exercised.
