# JARVIS Build Plan v2 — Constraint-Driven Architecture

This replaces the general research pass with a plan locked to your actual constraints: RAM-efficient, English-only, low-GPU, no third-party APIs, MCP-only tool layer, shell-first execution, self-healing retry loop, and a minimal notch-style UI.

---

## 1. Reasoning Model — RAM Budget First

Unified memory on M1 Pro is shared between OS, JARVIS's own processes (STT/TTS/orchestrator), and the model — so the model can't be sized as if it owned the machine.

**Two-tier model strategy** (this is what production router/executor splits look like, scaled to one laptop):

| Tier | Model | Size (Q4_K_M) | Role |
|---|---|---|---|
| Resident (always loaded) | **Qwen3 4B** (or `qwen3.5:4b`) | ~2.5GB | Intent routing, quick replies, simple commands — stays hot so response starts instantly |
| On-demand (loaded only when needed, unloaded after) | **Qwen3 8B** or **14B** depending on free RAM | ~5.5GB / ~9GB | Complex reasoning, multi-step planning, code generation — loaded via Ollama only when the router escalates, freed afterward |

Rule of thumb: an 8B model at Q4_K_M needs ~5.5GB; keep the resident model small enough that Ollama + STT + TTS + your FastAPI backend + normal daily apps (browser, editor) still fit comfortably. If you're on 16GB total, stay at 4B resident / 8B on-demand. If you're on 32GB, you can push the on-demand tier to 14B. Check `ollama ps` and Activity Monitor before committing to a tier — don't guess.

Qwen3 is the right family here regardless of tier: Apache 2.0 (no licensing friction), strong tool-calling, and it's what most 2026 local-agent guides converge on for this exact "assistant that also needs headroom for everything else" use case.

---

## 2. STT — English-only, so optimize for that

Since multilingual coverage (Whisper's main advantage) isn't needed, the better pick is the model built specifically for English speed/accuracy:

- **Parakeet TDT (via Parakeet.cpp or sherpa-onnx)** — beats Whisper large-v3 on English accuracy at roughly a quarter of the size, INT8 ONNX (~630MB), and the newer Parakeet.cpp port runs natively on Apple Silicon GPU via Metal (~27ms encoder inference for 10s of audio — about 96x faster than CPU-only). This is the primary pick.
- Keep **whisper.cpp small model** installed as a silent fallback only — if Parakeet's confidence is low on a given utterance (background noise, unclear audio), retry through whisper.cpp before giving up. You don't need this running by default; it's a safety net, not a second always-on process.

---

## 3. TTS — Low GPU, high efficiency, emotion is optional

Given the GPU-usage constraint, split TTS into two paths instead of forcing one model to do everything:

- **Primary voice (cloned, used for real responses): Chatterbox** — MIT license, ~0.5B params, runs on CPU or 4GB VRAM, zero-shot cloning from ~5s of reference audio, has an emotion-exaggeration dial you can leave near-zero since emotion is a low priority for you. **Force it to run in CPU mode**, not Metal — this is the key move for your GPU constraint, since it keeps the GPU free for whatever Ollama is doing at the same time, and Chatterbox is light enough that CPU-only is still fast enough for conversational use.
- **Optional lightweight path (quick acknowledgements, system sounds): Kokoro-82M** — Apache 2.0, 82M params, the most CPU-efficient TTS model available, but no voice cloning (fixed voices only). Not required, but worth having if you want near-zero-cost "got it" / "on it" style filler responses without invoking the heavier cloned-voice model for every short utterance.

---

## 4. Computer Control Replaces All App APIs (confirmed)

No WhatsApp API, no Notion API, no Spotify API — everything goes through the Accessibility-API control layer you've already decided on. This section is unchanged from before, just confirmed: **macos-use** (Python, Accessibility-API driven, reads screen state via Apple's Accessibility tree, no computer-vision model needed for standard macOS apps) is the core; **OmniParser** stays as the vision-based fallback specifically for apps with non-standard rendering (Blender's custom UI). Blender itself should still go through its native `bpy` Python scripting API rather than click simulation — this isn't computer control, it's a proper API, and it's far more reliable, so it's the one exception worth keeping.

Music control uses native macOS media-control hooks (`nowplaying-cli`-style, as in the open-source `music-tracker` project) — no Spotify API, no OAuth.

---

## 5. Clean Architecture (fresh project)

Layered, each layer replaceable independently:

```
┌─────────────────────────────────────────────┐
│  Notch UI (SwiftUI)  — presentation only     │
└───────────────────┬───────────────────────────┘
                     │ WebSocket
┌───────────────────▼───────────────────────────┐
│  Orchestrator (FastAPI)                        │
│  - Intent router (resident 4B model)           │
│  - Retry/escalation state machine              │
│  - Failure logger                              │
└───────────────────┬───────────────────────────┘
                     │ MCP (stdio/local)
┌───────────────────▼───────────────────────────┐
│  MCP Tool Layer — every capability is a tool   │
│  shell · computer-control · browser · memory   │
│  · tts · stt · skills                          │
└───────────────────┬───────────────────────────┘
                     │
┌───────────────────▼───────────────────────────┐
│  Execution surfaces                             │
│  Shell / Accessibility API / Browser / Files    │
└─────────────────────────────────────────────────┘
```

The orchestrator never calls a shell command or an app directly — it always goes through an MCP tool call, even for its own first-party tools. This keeps the "only MCP servers" rule honest and means any tool (including ones you build) is swappable without touching the orchestrator's code.

---

## 6. Memory, Failure Log, Skills, Personality

Four separate concerns, don't collapse them into one blob:

- **Memory (conversational/factual persistence):** **Letta (MemGPT)**, self-hosted, backed by local SQLite/Postgres — not their cloud offering. This is the standard 2026 pick for exactly your use case: single long-running personal agent with self-editing memory blocks. Wrap it as an MCP tool the orchestrator queries/updates.
- **Failure log:** a structured local JSON/SQLite log — every failed tool call records the intent, the attempted approach, the error, and the eventual resolution (or lack of one). This isn't a product to install; it's a table you write to from the retry loop (see §8). Worth building this early since the "search online and fix it" step depends on having a clean error signal to search from.
- **Skills:** a folder of self-contained instruction files (one skill = one markdown file describing how to do a repeatable task, in the same spirit as Claude's own Skills system) that the orchestrator loads on demand rather than keeping in the always-loaded system prompt. Keeps the resident model's context small and fast, while letting JARVIS "learn" new repeatable procedures over time — you or JARVIS itself can write a new skill file after solving a problem for the first time.
- **Personality (two separate files, not one):**
  - `agent-personality.md` — JARVIS's own voice/tone/behavior rules (how formal, how much it explains itself, humor level) — static, you write this once and refine it.
  - `user-profile.md` — what JARVIS has learned about you (preferences, routines, how you like tasks approached) — grows over time, effectively a subset of what Letta stores, surfaced separately so it's easy to audit/edit directly.

---

## 7. Latency Budget

Different tasks get different latency contracts — don't optimize everything to the same bar:

- **Conversational turn (STT → routing → short reply → TTS starts speaking):** target under ~1–1.5s to first audio. This is why the resident 4B model matters — it must never need to be loaded from disk mid-conversation.
- **Simple tool actions (open app, play music, check something):** under ~3s total, since these still feel like "talking to it."
- **Complex/long tasks (build a project, multi-step research, code generation):** no fixed ceiling — but JARVIS should immediately acknowledge ("on it, this'll take a few minutes") within the conversational-turn budget above, then work asynchronously and report back. Never make the user wait in silence for a task it's obviously going to take a while.
- Practically: stream TTS sentence-by-sentence as the LLM generates, don't wait for the full response before speaking — this alone does more for perceived latency than any model swap.

---

## 8. Self-Healing Retry Loop (escalating, not just repeating)

This is the core reliability pattern, and it needs escalating strategies, not the same action retried blindly:

1. **Attempt 1 — direct execution** via the planned tool (shell command or computer-control action).
2. **Attempt 2 — different approach** if attempt 1 fails: e.g., if a shell command fails, try the computer-control path for the same goal (or vice versa); if a specific CLI flag fails, try an alternate syntax. The router model should be prompted with the actual error text, not just "try again."
3. **Attempt 3 — search online for the fix.** Use a local web-search MCP tool (fetch-only, read-only — this is an outbound fetch, which is allowed under your "no upload" rule since you're pulling information in) to search the exact error message, then retry with what it found.
4. **Give up and report clearly** — log the full attempt chain to the failure log, and tell you plainly what was tried and what's still broken, rather than silently failing or pretending it worked.

Log every step of this chain (attempt, approach, result) to the failure log from §6 — over time this becomes the raw material for new skill files, since a repeated multi-step fix is exactly the kind of thing worth turning into a reusable skill.

---

## 9. Shell-First, Computer-Control Fallback

Confirmed ordering for every action JARVIS takes:

1. **Try shell first.** A local shell-execution MCP tool (**Desktop Commander MCP** is the most mature open-source option — ~25 tools covering command execution, process management, file operations, all local, stdio transport, no cloud call) should be the default path for anything scriptable: file operations, checking status, running installed CLIs, git, package managers, etc.
2. **Fall back to computer control** only when the target has no CLI/scriptable surface — GUI-only apps like WhatsApp Desktop, or visual confirmation steps.

Security note on Desktop Commander specifically: keep the human-approval prompt on for anything beyond read-only commands, and scope `allowedDirectories` tightly — it has the same blast radius as your own shell account, which is exactly why it's useful, but also why it shouldn't be given unattended write access everywhere by default.

---

## 10. MCP-Only Tool Layer

Every capability above gets wrapped as a local MCP server rather than an embedded SDK call, so the orchestrator only ever speaks one protocol:

| Capability | MCP server |
|---|---|
| Shell execution | Desktop Commander MCP (local, stdio) |
| Computer control | mac-computer-use (native Swift MCP server for Accessibility/Core Graphics control) |
| Browser | browser-use's built-in local MCP server mode (stdio, no Browserbase cloud) or Playwright MCP as an alternative |
| Memory | Letta wrapped as a local MCP tool |
| Web search (for the self-healing loop) | any local/self-hosted search MCP that does fetch-only, no account required |
| Music | a thin custom MCP wrapping `nowplaying-cli` |
| Files/notes | local filesystem MCP server (read/write scoped to specific folders) |

This also gives you a clean audit surface: since everything is an MCP tool call, you can log every single action JARVIS takes in one place regardless of which layer it touched.

---

## 11. UI — Notch-Style Companion, Not a Dashboard

You want something in the spirit of Apple's Dynamic Island, living in the MacBook's physical notch, not a sci-fi HUD. The right approach is to build on top of the existing open-source notch-app pattern rather than starting from zero:

- **boring.notch** — the original open-source (MIT) SwiftUI notch app: turns the camera-housing notch into an interactive surface with a HUD replacement, calendar, file shelf. This is the cleanest reference codebase for "how do you build a native, animated, expand-from-the-notch SwiftUI app."
- **Atoll** — a more full-featured fork of boring.notch (media, system stats, clipboard, timers) — useful as a second reference if you want to see how others extended the base pattern.
- **Vibe Notch (formerly "Claude Island")** — directly relevant: an existing open-source notch app built specifically to show AI agent session status (live session monitoring, permission approvals, chat history) in Dynamic-Island style. This is the closest existing example to what you're describing for JARVIS and worth reading end-to-end before writing your own.

**Recommendation:** fork/reference boring.notch's SwiftUI animation and window-management code (how it expands from the notch, handles hover/click states) and replace its media-control content with JARVIS's own: idle state shows a small waveform/status dot, expanded state shows the current task or a compact chat view, with permission-approval prompts (for anything requiring your confirmation) surfaced the same way Vibe Notch does it for coding-agent sessions. Keep it minimal — status, waveform, current task, approval prompts. No 3D visualizations, no sci-fi chrome.

---

## 12. Fetch-Only, No Upload Without Permission

Concrete enforcement, not just a principle:

- Every MCP tool that touches the network gets classified at setup as **fetch-only** (reads/searches, e.g. the self-healing web-search step) or **read+write** (sends/uploads data somewhere). Fetch-only tools run without confirmation; read+write tools require an explicit per-action approval — surfaced as a prompt in the notch UI, matching the Vibe Notch approval-prompt pattern above.
- OS-level outbound firewall allowlist (Application Firewall or a `pf` ruleset) denies outbound by default for JARVIS's backend and its child processes, with narrow exceptions only for the specific fetch-only tools you've approved.
- Local-only backends throughout: Letta against local SQLite/Postgres, no cloud memory service; TTS/STT fully offline; shell and computer-control tools never touch the network themselves.

---

## 13. Still Missing (to build later, not in the initial scope)

Everything above gets JARVIS to reactive request/response. These are the gaps that remain before it's a genuine "full Jarvis, scaled to a laptop" — grouped by what kind of gap each is. Not required now; listed so nothing gets forgotten.

**Activation & conversation flow**
- Wake-word detection — a tiny always-on local model (e.g. openWakeWord-style) that triggers the heavier STT pipeline only when needed, instead of STT running constantly.
- Barge-in / interruption — voice activity detection so JARVIS stops speaking and starts listening if you talk over it, instead of talking through you.
- Conversation boundary logic — deciding when a session ends vs. continues, so context doesn't get stale or lost.

**Awareness — the biggest gap overall**
- Vision/screen understanding — a vision-capable local model for "what's on my screen" / "read this" / "what does this error mean," separate from OmniParser (which is for control, not general Q&A about what's visible).
- Proactive/ambient behavior — a background scheduler watching calendar, battery, system state, deciding when something's worth surfacing unprompted, instead of JARVIS only ever responding to a request.
- Focused-app/context awareness — knowing what app/window is active so "reply to this" or "summarize this" resolves without you specifying every time.
- Personal file search — a lightweight local semantic index over your own notes/documents, separate from the constant-fact-store (that one's for objective facts, this is for your own material).

**Reliability & lifecycle**
- Process supervision — a watchdog (launchd is the native macOS fit) to restart Ollama/MCP servers/STT/TTS if one dies, so JARVIS doesn't silently go deaf or mute.
- Startup sequencing — what launches at login, in what order, and what happens if a dependency isn't ready.
- Success log, not just failure log — a record of what JARVIS did each day, both for trust/audit and for spotting patterns worth turning into skills.
- Battery/thermal awareness — a policy for throttling or pausing wake-word listening / dropping to the smaller model tier on battery power, since an always-on STT+LLM+TTS stack drains a laptop fast.

**Trust & safety boundaries**
- Dry-run / undo for destructive actions — the retry loop (§8) handles failure, but there's no rollback story yet for actions that "succeed" but shouldn't have (wrong file deleted, wrong message sent).
- (Speaker verification intentionally excluded for now — not required at this stage.)

**Housekeeping**
- Central config — one source of truth for model names, ports, tool toggles, thresholds, instead of settings scattered across every component.
- A small regression test set — a fixed list of representative intents to re-run after any change, so a tweak's side effects show up without manually re-testing by voice each time.

---

## 14. App Shell & Embedded Terminal — Native, Not Electron

You floated Electron or a native Mac app for the shell surface. Given everything else in this plan (native SwiftUI notch/orb, RAM discipline, low-GPU-contention TTS), **native (SwiftUI + AppKit) is the right call over Electron**, for one concrete reason: Electron ships its own Chromium + Node runtime per app — real memory overhead sitting alongside Ollama, STT, and TTS on the same 16–32GB you're already budgeting in §1. A native app shares the OS's frameworks instead of bundling its own browser.

- **Embedded terminal: SwiftTerm** (`migueldeicaza/SwiftTerm`) — a full VT100/xterm-compatible terminal emulator library for Swift, already used inside real shipping Mac apps including **CodeEdit** (an open-source, native VS Code-style editor — worth a look as a second reference for "native Mac dev-tool UI done well"). This gives you a real, full-featured terminal embedded directly in the JARVIS window, not a fake output pane.
- **Behavior:** every shell command from the MCP shell tool (Desktop Commander, §9) streams its output into this embedded terminal view inside the app — never spawns Terminal.app. Two display modes: a **compact strip** (last few lines, expands on click, matches the notch's expand-from-orb interaction from §11) for routine commands, and a **notification** (via macOS's native notification center, not an interruption) only when something needs your attention — a failure, a command that's still running after N seconds, or a step that needs approval per §12. Silent by default; interrupts only when it matters.
- If Electron's ecosystem maturity (xterm.js + node-pty is extremely well-trodden) ever outweighs the RAM cost for you, that's the fallback — but it works against §1's RAM budget and §7's latency goals directly, so treat it as a deliberate tradeoff, not a default.

## 15. Quick Actions

Small, frequent, low-latency utility intents — these should route through the fast resident model (§1) and shell-first execution (§9), not the heavier on-demand model:

- **Screenshot** — macOS's native `screencapture` CLI, shell-first, no third-party library needed.
- **Screen recording** — `screencapture -v` for basic capture, or **ScreenCaptureKit** (Apple's native framework) directly from the app if you want start/stop control, live preview, or audio-inclusive recording beyond the CLI's options.
- **Folder/file creation** — plain shell (`mkdir`, `touch`, template file writes) via Desktop Commander; no separate tool needed, this is exactly what shell-first execution is for.
- General pattern: anything in this category is a one-shot shell command with no multi-step reasoning required — keep these on the fast path so they never feel like they're waiting on "thinking."

## 16. New Project Scaffolding — First Real Proactive Skill

This is the concrete example that fills the "proactive/ambient behavior" gap flagged in §13 — worth building early precisely because it's a clean, well-scoped first instance of JARVIS noticing something and offering, rather than waiting to be asked outright.

**Trigger:** heuristics like a new unfamiliar project-shaped request, or you mentioning something that sounds like new work rather than continuing existing work — the router model asks "Are we working on a new project, sir?" only when its confidence is genuinely ambiguous, not on every message (an unnecessary confirmation prompt on obvious continuations would violate the low-friction/low-latency goal from §7).

**On confirmation, the scaffolding skill (§6 — this becomes your first real skill file) does, in order:**
1. Ask (or infer from context) the project type — Python/backend, frontend, full-stack, data/ML, etc. — and the project name.
2. Create the folder under a fixed `~/Projects/<name>` root via shell (Desktop Commander).
3. Scaffold structure based on type — this is a small set of templates, not a generic "guess everything" generator:
   - **Python backend:** `venv` created and dependencies installed (`requirements.txt` or `pyproject.toml` seeded with sensible defaults for the stated stack), `app/`, `db/`, `tests/`, `.env.example`, `.gitignore`.
   - **Frontend:** standard scaffold via the relevant CLI (`npm create vite@latest`, etc.) rather than hand-writing boilerplate — shell out to the tool that already does this well.
   - **Full-stack:** `frontend/`, `backend/`, `database/` as siblings, each scaffolded per its own template above.
   - Always include a `temp/` folder (scratch space) and a root `README.md` stub.
4. Initialize git (`git init`) if not already a repo.
5. **Open VS Code on the new folder** — shell command `code <path>`, which is the simplest reliable way to do this (VS Code's CLI, not the Accessibility layer — this is a case where a clean CLI exists, so §9's shell-first rule applies directly).
6. Report back concisely what was created — don't just silently finish; a short spoken/notch summary ("Set up the FastAPI backend with a virtual environment and opened it in VS Code, sir") closes the loop.

Keep the template set small and hand-curated at first (2–3 project types) rather than trying to cover everything — expand it only as you actually hit new project shapes, consistent with §6's "skills grow from real repeated tasks" philosophy.

---

## Revisiting the Original 10 Repos

Against these tighter constraints (MCP-only, no redundant orchestration frameworks, clean architecture), here's what still earns a place:

- **browser-use** — keep, via its local MCP server mode.
- **Supabase** — keep, but **self-hosted only** (local Docker), as the Postgres backend behind Letta's memory store or the failure-log table — not their cloud product.
- **Stirling-PDF** — keep as an optional local MCP-wrapped tool if/when a "handle this PDF" intent comes up; not core.
- **crawl4ai** — keep specifically for step 3 of the self-healing loop (§8) — turning a raw error-message search into clean markdown the router model can act on.
- **Langflow, Dify, open-webui, Maxun, Coolify, OpenHands** — **drop from the core build.** These solve problems you've already solved differently: Langflow/Dify are orchestration frameworks competing with your own FastAPI orchestrator (redundant under "clean architecture, MCP-only"); open-webui is a chat UI you're replacing with the notch UI; Maxun's no-code scraping is redundant with crawl4ai; Coolify is deployment infra with no role in a single-machine local assistant; OpenHands is a full autonomous coding agent — much heavier than what your shell-first + computer-control fallback already covers, and would compete with your own retry loop rather than complement it.

### New repos this pass surfaced, worth adding to the shortlist
- **Desktop Commander MCP** (`wonderwhy-er/DesktopCommanderMCP`) — shell-first execution layer, §9.
- **macos-use** / **mac-computer-use** — computer-control MCP layer, §4/§10.
- **Letta** (`letta-ai/letta`) — memory layer, §6.
- **Chatterbox** (`resemble-ai/chatterbox`) — cloned voice TTS, §3.
- **Parakeet.cpp** / **sherpa-onnx** (NVIDIA Parakeet models) — English STT, §2.
- **boring.notch** (`TheBoringWorks/boring.notch`) and **Vibe Notch** — UI reference, §11.
- **music-tracker / "Noted"** — local media-control pattern for music, §4.
