# Demo Prep Plan — 20/21 Aug 2026

Demo window: 12:00–2:00 PM, Fri 21-Aug-2026
Last updated: Thu 20-Aug-2026, 5:13 PM IST

---

## Status as of tonight (5:13 PM)

**Built and verified: Phase A (shared ingestion entry point), Phase B
(watched folder adapter), Phase C (SFTP adapter).** Both new adapters are
live, tested, and demo-ready.

**Phase D (email-in adapter) deferred to tomorrow**, by explicit choice —
after Phase C, the decision was to stop adding new features and instead
run a full end-to-end regression pass across the whole app, on the
reasoning that a stable 2-connector demo beats a rushed 3-connector one.
**Demo scope is now watched folder + SFTP live, not all three.** If
Phase D gets built tomorrow morning, add it back to Act 2; if not, drop
it from the script rather than mentioning it and not delivering.

**That end-to-end pass found and fixed 6 real bugs**, none related to
tonight's new adapter code — all pre-existing schema drift and a
deprecated API model, surfaced by actually exercising the app instead of
assuming it worked:

| # | Bug | Where |
|---|-----|-------|
| 1 | Login crashed (500) | `users.hashed_password` vs actual `password_hash` column |
| 2 | Sign-up crashed (500) | `user_role` enum name mismatch + `audit_logs` missing 4 columns |
| 3 | Manual upload would have crashed (500) | `documents`/`document_versions` missing several columns the ORM model expects |
| 4 | Folder creation, chat, permissions would have crashed | `folders`/`chat_sessions`/`chat_messages`/`permissions` missing columns |
| 5 | Chat session creation crashed (500) | `chat_sessions.created_at` had no real DB default despite the model assuming one |
| 6 | AI Summary Card silently failing on every search | Groq had deprecated `llama-3.3-70b-versatile`; `.env` pointed at a dead model |

Bugs #1–2 would have broken the demo at Act 1 (login). Bug #3 would have
broken Act 2 (manual upload) — the single most-used step in the whole
script. Bug #6 would have made the AI Summary Card — a headline demo
beat — silently show raw excerpts instead of a generated answer, with no
visible error. **All six are fixed and re-verified.** Five new Alembic
migrations exist now: `0004`–`0007` plus the folder/chat/permissions one.

**Also fixed along the way:** a stray port-8000 process from an
unrelated project, stale Docker images/volumes from a 10-day-old
environment, a `/api/v1/health` endpoint that froze the entire backend
for its duration (blocking embedding call not on a thread executor), and
an SFTP container whose upload directory came up root-owned (connector
user couldn't write into its own folder).

**Not yet checked:** the actual browser UI (drive page, document
preview rendering, click-through polish). Browser extension wasn't
connected tonight — this needs a manual walkthrough of Acts 1–4 per
`docs/DEMO_PRESENTATION_GUIDE.md`, still outstanding.

---

## Tonight — remaining

**Manual UI walkthrough (Acts 1–4)** — outstanding, you're doing this
yourself. Login → upload → search (check the AI Summary Card
specifically, it was broken until tonight's fix) → document preview.

**Full dry run #1** — still needed. End-to-end through Acts 1–6 plus the
watched-folder/SFTP beats (Act 2 extension: pre-stage a file in the
watched folder and drop one via an SFTP client, both should appear in
Flower within ~10s).

**Fix issues from dry run #1** — cosmetic/blocking only.

**Full dry run #2** — exactly as it will be presented, correct browser
tabs, correct click order, adapter files pre-staged.

**Code freeze.** Stop touching the repo once dry run #2 is clean.

**Sleep.**

If Phase D (email-in) still feels worth attempting tomorrow morning
during the 10:30–11:00 AM buffer, it's the same pattern as Phase C
(mail-catcher container + attachment-based ingest via the same
`connector_ingest_service.ingest_bytes` entry point) — but don't let it
eat into dry-run or standby time. A working 2-connector demo beats a
half-working 3-connector one.

---

## Tomorrow — 21-Aug-2026

**9:30–10:00 AM — Environment recheck** (no code changes)
`docker compose ps`, confirm all 8 containers healthy (postgres, redis,
minio, sftp, backend, worker, flower, frontend), restart cleanly if
anything drifted overnight.

**10:00–10:30 AM — Final solo run-through** at actual demo pace,
including the watched-folder/SFTP beats.

**10:30–11:00 AM — Buffer**
Last-minute cosmetic-only fixes if truly needed. Optional: attempt
Phase D (email-in) here only if 10:00's run-through went perfectly and
time allows — skip without hesitation otherwise.

**11:00–11:30 AM — Final tab/window setup**
`localhost:3000` (app), `localhost:5555` (Flower — live Celery task
execution), `localhost:8000/api/docs` (Swagger, for technical
questions). Silence notifications.

**11:30 AM–12:00 PM — Standby**, nothing further changes.

**12:00–2:00 PM — Demo.**
