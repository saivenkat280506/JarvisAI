# Jarvis restore snapshot

**Date:** 2026-08-15  
**Label:** `jarvis-snapshot-2026-08-15`  
**Git tag:** `jarvis-snapshot-2026-08-15`  
**Commit:** `8f822a4976facd259b3b12d44b7c6c616da39436`

This is a known-good restore point **before** the memory-database / performance integration pass.

## What this version includes

- WhatsApp Desktop send: open app → wait for load → open saved chat by phone protocol → clear → type → green send
- Saved contacts only (Sathish, Ashrith, Laxman, Nishanth + Sathish speech aliases)
- Router fixes for Watsapp / Satish / Sadeesh / placeholder `+91...`
- No “just open WhatsApp” fake-success fallback
- Existing music, volume, browser/Puppeteer, STT, TTS paths

## How to revert

```text
git checkout jarvis-snapshot-2026-08-15
```

Or reset the branch (destroys later local commits):

```text
git reset --hard jarvis-snapshot-2026-08-15
```

Inspect only:

```text
git show jarvis-snapshot-2026-08-15
```

Work after this tag (memory store, all-contacts, latency) is on `main` and can be discarded by checking out the tag above.
