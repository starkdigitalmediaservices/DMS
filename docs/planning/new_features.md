# New Features — 20–21 Aug 2026

Reference for everything built and fixed since the last handoff: new
ingestion connectors, the bugs found in a full end-to-end pass, and the
UI additions that go with them. Also published as a formatted page:
https://claude.ai/code/artifact/e95d037e-9e38-4b5d-9ba8-2aa1eb1f4886

---

## Quick reference

| What | Details |
|---|---|
| Watched folder (local) | `/home/stark/Work Space/DMS/connector_inbox/watched_folder/` |
| Stark Drive (auto-sync) | `/home/stark/Stark Drive /` (note the trailing space — it's part of the real folder name) |
| SFTP | host `192.168.30.65`, port `2222`, user `connector`, pass `connector123`, folder `/upload` |
| Email-in | `cd "/home/stark/Work Space/DMS" && python3 scripts/send_demo_email.py "/path/to/file"` |

---

## Stability pass — 6 blocking bugs fixed

A full end-to-end pass through the app (not assumed working, actually
exercised) surfaced six pre-existing issues that would have broken a
demo at the first click. All fixed and re-verified.

| # | Bug | Would have broken |
|---|-----|--------------------|
| 1 | Login crashed (500) — `users.hashed_password` vs actual `password_hash` column | Login, immediately |
| 2 | Sign-up crashed (500) — `user_role` enum name mismatch + `audit_logs` missing columns | Creating any new account |
| 3 | Manual upload would have crashed (500) — `documents`/`document_versions` missing several ORM-expected columns | Manual upload — the single most-used step |
| 4 | Folder creation, chat, permissions would have crashed — missing columns | Any folder or AI Chat interaction |
| 5 | Chat session creation crashed (500) — `chat_sessions.created_at` had no real DB default | Opening AI Chat |
| 6 | AI Summary Card silently failing on every search — Groq had deprecated the configured model | The AI Summary headline moment, with no visible error |

Also fixed along the way: a `/health` endpoint that froze the entire
backend (blocking model-load call not on a thread executor), an SFTP
container that came up unable to write to its own upload folder, and a
frontend session-refresh bug that silently stalled instead of logging
out on failure.

---

## New ingestion connectors

All four converge on the same shared `ingest_bytes()` entry point —
identical duplicate detection (by file content, not filename), storage,
chunking, and search indexing, regardless of which one a file came
through. See "Why one pipeline" at the bottom for the reasoning.

### Watched folder — Phase B

A folder on the server DMS checks every 5 seconds. Drop a file in, it's
searchable in DMS shortly after — no upload step.

- Path: `/home/stark/Work Space/DMS/connector_inbox/watched_folder/`
- A file is only picked up once its size stops changing **and** it's
  been untouched for 10+ seconds, so a half-copied file is never
  ingested truncated.
- Subfolders are mirrored as real, nested DMS folders — a dropped
  `Legal/Contracts/2026/` structure becomes a real folder tree, not a
  flattened filename.
- Handled files move into a hidden `processed/` folder so they're never
  re-ingested; any now-empty folder shell left behind is cleaned up
  automatically.
- **Why it matters:** every DMS on the market requires someone to
  remember to open the app and upload — that step gets skipped. This
  removes it entirely; the team keeps doing exactly what it already
  does.

### Stark Drive auto-sync

Google Drive / OneDrive–style sync, but for a folder already in daily
use, not a dedicated test inbox.

- Path: `/home/stark/Stark Drive /`
- Same detection and subfolder-mirroring logic as the watched folder,
  but its bookkeeping (which files are already handled) lives in a
  separate, hidden location — nothing extra ever appears inside the
  real folder.
- **Why it matters:** watched-folder proved the mechanism; this makes
  it live on a folder someone actually works in day to day.

### SFTP connector — Phase C

Same automatic pickup, but for a device that isn't this computer — a
vendor, another office, anyone without access to this machine's disk.

- Host `192.168.30.65`, port `2222`, user `connector`, pass
  `connector123`, remote folder `/upload`, polls every ~8s.
- Connect from another machine: `sftp://connector@192.168.30.65:2222/upload`
- Same subfolder-mirroring as the watched folder.
- **Why it matters:** almost any enterprise system (accounting
  software, a scanner, a vendor's own tooling) can push a file over
  SFTP — a decades-old, universally supported standard. No filesystem
  access to the server and no custom integration required.

### Email-in connector — Phase D

Send a file as an email attachment; it appears in DMS. No login, no
app, no training.

- Demo mailbox: `connector@dms.local` (a local test mail server, not
  real internet email — the demo never depends on real delivery being
  fast or reliable).
- Run: `python3 scripts/send_demo_email.py` (sends a default demo PDF) or
  `python3 scripts/send_demo_email.py "/path/to/file"` (sends any file).
- Checked every 10 seconds; attachments are extracted and the email is
  marked read so it's never processed twice.
- A file that's already in the Drive from earlier gets silently skipped
  as a duplicate — the send still succeeds, but nothing new appears.
  Use a never-before-uploaded file to see a visibly new result.
- **Why it matters:** the lowest bar to entry of the three — everyone
  already knows how to attach a file to an email.

---

## Interface additions

### "Connect a device" button

The SFTP and email details above, available from inside DMS itself —
no need to ask an engineer for credentials.

- Where: Drive page → **+ New** → **Connect a device**
- Opens a panel with two tabs (*Folder / SFTP* and *Email*), each field
  one click to copy.
- **Why it matters:** turns "email me the SFTP password" into a
  self-service action inside the product.

### Real folders on drag-and-drop upload (fixed)

Dropping a folder from the file system onto the Drive page now creates
a real, navigable folder inside DMS. Previously the folder structure
got flattened into the filename itself (`Kunal 2/report.pdf` as a flat
file title instead of `report.pdf` inside a real "Kunal 2" folder) —
fixed to build the actual nested folder tree with clean filenames.

### Wider file format support (fixed)

Code and config files were being silently rejected on upload. Added
~30 previously-unsupported formats (`.py`, `.js`, `.ts`, `.sql`,
`.yaml`, `.html`, `.log`, and more), all handled by the existing
plain-text extraction path — no new parser needed. Archive formats
like `.zip` were deliberately left out; there's no unpacking logic yet,
so a zip would just get indexed as unreadable binary noise.

---

## Why one pipeline, not four

Manual upload, watched folder, Stark Drive, SFTP, and email all end at
the same shared ingestion function — identical dedup, storage,
chunking, and indexing, no matter which door the file came through.
That's the answer if a client asks "what's the difference between these
ways in?" — there isn't one, past the door. A file emailed in is
exactly as searchable, exactly as secure, exactly as fast to find as
one dragged onto the page.
