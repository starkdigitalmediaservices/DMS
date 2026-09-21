# VeritasDocs — Project Deep Dive

*A plain-language walkthrough of what this project is, the real problem it solves, everything that's been built, the technology behind it, and why every major decision was made the way it was — written so you can walk into a demo and explain any part of it with confidence.*

**Internal name:** Document Management System (DMS)
**Domain:** Government land & property records
**Languages:** English + Marathi
**Status:** All engineering complete; remaining items are external sign-offs, a hardware decision, and two paid API tiers — not code. One live provider (AI field-extraction) is paused for a credit top-up — see §8's demo note before you pick which document to show.

**At a glance:**
- **6** user roles (RBAC)
- **4** layers in the search engine
- **76+** backend functions live-tested across three independent hardening passes
- **9** table-reading features (TS1–TS9) built against a real 1973 register
- **2** scripts read: English & Devanagari
- **391** automated backend tests, green
- **Backup & disaster recovery**, with a restore rehearsed end-to-end
- **0** unfinished engineering work

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [What We Built](#2-what-we-built)
3. [How It Works](#3-how-it-works)
4. [The Tech Stack](#4-the-tech-stack)
5. [Why These Choices](#5-why-these-choices)
6. [Security, Access & Trust](#6-security-access--trust)
7. [Keeping It Running: Backup, Recovery & Scheduled Work](#7-keeping-it-running-backup-recovery--scheduled-work)
8. [What's Done, What's Still Open](#8-whats-done-whats-still-open)
9. [Proven by Testing, Not Just Claimed](#9-proven-by-testing-not-just-claimed)
10. [Demo Cheat Sheet](#10-demo-cheat-sheet)
11. [Glossary](#11-glossary)

---

## 1. The Problem

Start here if you remember nothing else: this system exists because a huge amount of legally important information in India sits on paper that is old, handwritten, and effectively unsearchable.

**What a "waqf register" actually is.** A waqf is a permanent religious or charitable endowment under Islamic law — usually land or a building — donated forever for a religious or public purpose (a mosque, a graveyard, a school). Every Indian state has a Waqf Board that is legally required to keep a register of these properties: who owns what, its boundaries, its survey number, its current legal status. This project was built around real registers like this, but the same problem — and the same solution — applies to any government or legal department drowning in old paper records: land revenue offices, municipal archives, court record rooms.

The registers studied are physically old — some from the 1970s — typed or handwritten in Marathi, photographed or scanned as-is, often with pages split across a table that runs from the bottom of one page to the top of the next, or across a left page and a right page of an open ledger. Clerks used shorthand like **"Do."** (a "ditto" mark meaning "same value as the row above") to save time writing, which a computer reading it literally would misunderstand as an actual value called "Do."

### Why this is a real, expensive problem

- **Nobody can search it.** Finding "every property owned by a certain family" today means a person manually flipping through paper registers or scanned PDFs.
- **Nothing is verifiable at a glance.** When a legal dispute arises, someone needs to prove exactly what the original register said, when it was amended, and who has looked at it since — none of which paper gives you automatically.
- **Digitizing badly is worse than not digitizing.** A naive OCR ("just read the text") scan of a legal register that silently gets a survey number wrong, or silently merges two different people's names, creates a record that looks official but is quietly incorrect — which is arguably more dangerous than the paper original.
- **It has to survive real, messy documents** — not just clean modern scans, but 50-year-old paper with stamps, handwriting in the margins, faded ink, and tables split across pages in inconsistent ways.

> The brief, in one sentence: turn decades of old, handwritten, legally significant paper into something searchable and trustworthy — without ever pretending a machine's guess is a verified fact.

---

## 2. What We Built

VeritasDocs is a document management and search platform, purpose-built for exactly this problem: it reads scanned government registers (in English or Marathi), pulls out the structured legal data field-by-field, makes a human confirm every single fact before it counts as "official," links related records together, and keeps a permanent, tamper-evident record of every action taken on the data.

It is not "OCR software" and it is not "a search box." It is closer to a full digital records office: intake, reading, understanding, human sign-off, cross-referencing, search, and audit — each one a distinct, working system, wired together.

**The five-stage pipeline:**

| Stage | Name | What happens |
|---|---|---|
| 1 | **Intake** | Documents arrive by upload, watched folder, SFTP, or email. |
| 2 | **Read & Understand** | OCR reads the text; an AI vision model maps each answer to its field on the form. |
| 3 | **Human Verifies** | A person confirms, edits, or rejects every extracted fact before it's "official." |
| 4 | **Connect & Search** | Records link to related people/properties; everything becomes searchable. |
| 5 | **Govern** | Every action is permanently logged; exports flag what's verified vs. not. |

---

## 3. How It Works

A closer look at each stage — what actually happens, and what to point at during the demo.

### Reading the document

Every uploaded page is read twice, in two different ways, working together:

- **OCR (Optical Character Recognition)** reads the raw text off the page — including Marathi/Devanagari script — entirely on our own servers, so nothing leaves the building. This matters because the eventual deployment target is government infrastructure, some of it "air-gapped" (no internet access at all).
- **A vision-language AI model (VLM)** looks at the actual *image* of the page alongside a known form template (e.g. "Waqf Registration Form A") and works out which box on the form each answer belongs in — owner name goes here, survey number goes there. This is the difference between "the page contains the text 'Ramrao Patil' somewhere" and "the owner field on row 6 says 'Ramrao Patil', and here is the exact rectangle on the scan that proves it."

Anything the system can't confidently classify — the wrong form type, an illegible page, a stray handwritten note — is routed to a human review queue rather than guessed at.

### Making sense of real, old registers (the hardest part)

Clean modern PDFs are the easy case. The genuinely hard engineering work was making this reliable against a real 1973 government register — nine separate features built specifically for that:

- **Continuation-row merge (top/bottom page splits)** — When a table's row starts at the bottom of one page and finishes at the top of the next, the system reconnects it into one correct row instead of silently losing half of it. Confirmed on real 16-page registration files, including one field rebuilt from 17 separate word-regions spanning two pages.
- **Spread-join (left/right facing-page splits) — built, and now confirmed working end-to-end on a matching case.** When a table splits across a left and right facing page, the system attempts to pair the two halves by matching a shared value (like a serial number). Its safe-refusal behavior was already proven on real documents (correctly flagging "couldn't confidently join" rather than guessing when there's no shared value). On 2026-09-10, the positive case was proven too: a synthetic dummy document was purpose-built with a genuine matching shared value across a left/right spread specifically to exercise this path, and the system correctly paired the two halves into one row. No real archival register has produced this case yet — the mechanism is now demonstrated end-to-end, but still real-world-unobserved.
- **Ditto handling ("Do." marks)** — Old clerks wrote "Do." to mean "same as above." The system fills in the real repeated value automatically, while keeping the original mark on record — nothing is silently invented.
- **Page furniture detection (stamps & headers)** — Repeated letterheads, stamps, and footers on every page are recognized and excluded, so they're never mistaken for actual data.
- **Human-answer memory** — Once a person resolves a tricky judgment call for a given table layout, the system remembers it — future documents with the same shape don't re-ask the same question.

### The human verification workbench

Nothing the AI extracts is treated as fact until a person confirms it. Every field carries a visible confidence score and lands in a review queue (low-confidence, handwritten, ambiguous table join, and so on). Clicking any extracted value jumps straight to the exact rectangle on the original scan it came from — even when that value was pieced together from two different pages. Reviewers can bulk-confirm or bulk-edit many rows at once, with a full preview and one-click undo — but a batch of documents must first be "calibrated" (a human has certified the AI's confidence numbers are actually trustworthy for that batch) before bulk actions are even allowed.

### Search that understands both languages, and both meaning and spelling

One search box combines three techniques at once: exact keyword matching, meaning-based ("semantic") matching using AI embeddings, and typo-tolerant fuzzy matching. Search in English and it finds Marathi content and vice versa. Every AI-generated answer must cite the specific document and page it came from — if it can't find real grounding, it says so instead of guessing.

### Connecting records — the entity graph

The same person or property often appears differently across documents ("Ramrao Patil" in one register, "R. B. Patil" in another). The system links these, but with tiered trust: low-risk, mechanical links commit automatically; anything legally significant — like "these are the same legal identity" — always waits for a human to confirm, no matter how confident the AI is. Legal records themselves carry full history: the original entry, plus every amendment since, each with its own legal status (in force / set aside / under stay / superseded) — you can always see the current state *and* the untouched original.

### Governance — proving nothing was tampered with

Every action in the system (confirm, edit, export, delete) is written into an audit log that is cryptographically chained — each entry includes a hash of the previous one, so altering any past entry would break the chain and be instantly detectable. Retention rules (some records can never be auto-deleted; others expire on a schedule) are enforced by the database itself, not just hidden in application code. Exports always clearly flag which data is human-verified versus still AI-suggested.

---

## 4. The Tech Stack

Every major piece of technology used, organized by the job it does — not just a list of names.

```
                 Web Browser
        Next.js 14 · React 18 · TypeScript
                      │
                      │  REST API over HTTPS, JWT-secured
                      ▼
                FastAPI Backend
              Python · async · Pydantic
                      │
        ┌─────────────┼──────────────┬───────────────┐
        ▼             ▼              ▼               ▼
  PostgreSQL      Redis +        MinIO / AWS      AI Providers
  + pgvector      Celery         S3               Gemini, Claude,
  (records,       Workers        (original         GPT, Groq,
  permissions,    (background     scans &           Cohere,
  search index)   jobs, cache)    files)            local models
```

### Frontend — what the user sees and clicks

| Technology | What it's for |
|---|---|
| **Next.js 14** | The React framework running the whole web app — page routing, server rendering for fast first loads, and a production build pipeline. |
| **React 18** | Builds the interface out of reusable components (buttons, tables, panels) that update instantly as data changes. |
| **TypeScript** | Adds type-checking to JavaScript, so a whole category of bugs (passing the wrong shape of data) gets caught before the code ever runs. |
| **Tailwind CSS** | Styles the interface without writing separate CSS files for every component — keeps the whole app visually consistent. |
| **Mammoth.js / xlsx / react-pdf** | Render Word, Excel, and PDF files directly in the browser preview — no download required to check a document. |

### Backend — the application's brain

| Technology | What it's for |
|---|---|
| **FastAPI** | The Python web framework that serves every API request — chosen for being async-native (see §5) and for generating live, interactive API documentation automatically. |
| **SQLAlchemy 2.0 (async) + Alembic** | Talks to the database in Python code instead of hand-written SQL everywhere, and tracks every schema change as a reviewable, reversible migration. |
| **Pydantic** | Validates every request and response against a strict schema — malformed data gets rejected before it touches business logic. |
| **JWT + bcrypt** | Handles login sessions (signed tokens) and password storage (one-way hashing, so even a database leak doesn't expose real passwords). |

### Data & background work

| Technology | What it's for |
|---|---|
| **PostgreSQL 16 + pgvector** | One database doing two jobs: normal relational data (users, documents, folders, audit log) *and* AI vector search — see §5 for why that matters. |
| **Celery + Redis** | Runs slow work (OCR, AI extraction, embedding) in the background so the user's screen is never frozen waiting; Redis is both the job queue and a fast answer cache. |
| **Celery Beat** | The *scheduler* that fires recurring jobs — today, the daily purge of items sitting in the recycle bin past their retention period. Worth calling out as a separate piece: a Celery worker only ever runs work that something else puts on the queue, so without Beat a "daily" task is defined but never actually happens. It runs as its own single-replica service for exactly one reason: two schedulers would fire every scheduled job twice. |
| **Flower** | A live dashboard showing every background job in flight — useful for the demo to prove processing is really happening, not staged. |
| **MinIO / AWS S3** | Stores the actual scanned files and images — large binary files don't belong inside a database. MinIO is the local stand-in; AWS S3 is the production equivalent, same interface. |

### AI — reading, understanding, and answering

| Technology | What it's for |
|---|---|
| **Gemini / OpenRouter (Claude, GPT)** | Vision-language extraction — reading a scanned form image and mapping answers to fields. Automatically falls back from one to the other if a provider is unavailable or over quota. |
| **BGE-M3 (local)** | Turns text into a 1024-number "embedding" that captures its meaning, used for semantic search — runs locally, no API cost, no data leaving the server. |
| **Cohere Rerank** | A second-pass AI that re-scores the top search candidates for real relevance, filtering out near-misses a plain vector match would keep. |
| **PaddleOCR + Tesseract** | Local, offline-capable OCR engines with genuine Marathi/Devanagari support — no cloud dependency for the basic text-reading step. |
| **Qwen2.5-VL (local, in progress)** | Being evaluated as a fully local replacement for the cloud vision model, to remove the last external dependency for true air-gapped deployments. |

### Running it all

| Technology | What it's for |
|---|---|
| **Docker Compose** | Every piece — database, cache, backend, worker, scheduler, frontend — runs the exact same way on any machine: one command starts the whole stack, which is also what makes the demo possible. |
| **Helm chart (Kubernetes)** | The production deployment path, for customers running this on a cluster rather than a single machine. The chart refuses to render at all if the shared connector identity hasn't been configured, so a broken ingestion setup fails at deploy time rather than silently at runtime. |
| **Server-Sent Events (SSE)** | How search results reach the browser *before* the AI answer is ready. The documents and the written answer arrive as two separate deliveries on one connection, so the user reads their results while the answer is still being composed — see §5. |
| **Structured application logging** | Every component logs at a configurable level, with database-statement noise separated from application messages. Search additionally records a per-phase timing breakdown, so "search felt slow" can be answered with evidence rather than a guess. |

---

## 5. Why These Choices

None of the above was picked by default. Here's the reasoning behind the decisions someone is most likely to ask about.

**Why one database for both records *and* AI search (Postgres + pgvector)?**
Many AI products run a separate specialized "vector database" alongside their normal database. We deliberately didn't: keeping everything in PostgreSQL means one system to secure, one backup strategy, one transaction guarantee — and critically, the same tenant-isolation security rule (see §6) automatically applies to AI search results too, instead of needing to be re-implemented in a second system.

**Why a vision-language model instead of "just OCR"?**
Plain OCR gives you a wall of text with no structure — you'd still have to guess which words are the answer to which question on the form. A vision-language model looks at the image itself, understands where the boxes are, and reports which value belongs in which field — and crucially, exactly where on the page it read that value from. That's what makes click-to-source verification possible at all.

**Why background workers (Celery) instead of doing everything instantly?**
Reading and understanding a scanned page can take real time. Making the user's browser sit frozen for that would be a bad product. Instead, the upload finishes instantly, a background worker does the slow work, and the screen updates from "processing" to "indexed" on its own — the Flower dashboard shown in the demo makes that background work visible.

**Why hot-swappable AI providers instead of picking one?**
Three real reasons: cost control (prices and quotas change), resilience (a provider outage or quota limit shouldn't take the product down — the system automatically falls back to a second provider), and eventually, sovereignty (a government customer may require the ability to run entirely on infrastructure they control, with zero external calls).

**Why design for "air-gapped" (fully offline) from day one?**
This product is intended to eventually run inside government facilities that may have no internet access at all. Rather than bolt that on later, the architecture already has a hard on/off switch: when air-gapped mode is enabled, any attempt to call an external AI service **fails loudly and closed** rather than silently leaking data out — described further in §6.

**Why results and the AI answer arrive separately, instead of all at once?**
A search does two different-sized jobs: finding the right documents (fast) and composing a written, cited answer about them (an extra AI round-trip, several seconds). Delivering them together means the user stares at a spinner for the length of the *slower* one. So the documents are sent the moment they exist and the answer follows on the same connection. Measured on this system: results at ~3 seconds, answer at ~4.4 seconds — and when the answer provider is throttled, that second number has been measured at 17 and even 56 seconds, while the documents still appeared in about two. The gap is biggest exactly when it matters most.

**Why the AI answer is *not* streamed word-by-word, the way a chatbot does?**
This is a deliberate refusal, and it's a good illustration of the product's priorities. The answer isn't free-form prose — the system asks the model for a set of discrete *claims*, then checks each one before showing it: every number in a claim must actually appear in the excerpt that claim cites. Claims that fail are silently dropped, and if none survive, the whole answer becomes an honest refusal. Streaming raw words would mean showing text the verifier is about to reject — i.e. briefly showing the user an unverified claim and then taking it back. That trades the grounding guarantee for the *appearance* of speed. Streaming per *verified claim* would be compatible with the design and is the logical next step; it simply needs a capability the AI providers aren't wired for yet.

**Why the second re-ranking pass costs twice the API calls, and why that stays.**
Re-ranking runs twice per search: once with the user's query, once with its cross-script counterpart (English ⇄ Marathi). That doubles the cost of the most rate-limited external call in the system, so it was explicitly tested for removal — and the test said keep it. On this Marathi corpus, four out of five plain-English test queries returned **zero results** with the single-probe version, versus real matches with both. The documents are in Marathi; an English-only relevance probe scores them near zero. Halving the API calls would have halved the latency and destroyed the search.

**Why Next.js / React for the frontend?**
It's the most widely adopted, well-supported way to build a fast, modern, type-safe web interface, with a large ecosystem for the specific things this product needs (in-browser document rendering, real-time updates) and a straightforward path to production deployment.

---

## 6. Security, Access & Trust

Legal records demand a higher bar than "the app code checks permissions." Here's what's actually enforced, and where.

**Multi-tenant isolation, enforced by the database itself.**
This is a multi-tenant platform — many separate organizations ("tenants") use the same running system, each seeing only their own data. Rather than trusting every line of application code to remember to filter by tenant, PostgreSQL's own **Row-Level Security** is used: the database is configured so a query simply cannot return another tenant's rows, no matter what the application code asks for. A mistake in a single API endpoint can't leak another organization's records, because the database itself is the last line of defense — not just the first.

**Six real roles, checked on every action.**
Records Officer, Operator, Department Head, Legal Counsel, IT Admin, and Auditor — each with different permissions, enforced on the server for every single action, not just hidden or greyed-out in the interface. Departments can additionally be scoped to only the folders they're authorized to see.

**Human-in-the-loop by design.**
Nothing the AI produces is presented as fact until a person confirms it — this is enforced in code, not policy: the functions that promote a value to "verified" hard-reject the action if there's no logged-in human actor behind it, and handwritten/ambiguous fields specifically cannot be bulk-approved at all.

**Tamper-evident audit trail.**
Every mutating action is written to an append-only, hash-chained log — each entry cryptographically includes the previous one, so any attempt to quietly edit history breaks the chain and is detectable on demand.

**The system refuses to start insecurely in production.**
Security controls that depend on someone remembering to set a value are not controls. Five settings are now validated at startup, and in production the application *refuses to boot* rather than run degraded:

| If this is wrong… | …what would silently happen | Now |
|---|---|---|
| The restricted database role isn't configured | Every request connects as the database superuser, which **bypasses Row-Level Security entirely** — tenant isolation would be off, with only a log line to say so | Refuses to start |
| The inbound-email secret is still the shipped default | That secret is the *only* authentication on the endpoint that ingests documents from email — anyone who had read the source could post documents into the tenant | Refuses to start |
| Browser origins are left wide open (`*`) | Any website could drive the API using a logged-in user's session | Refuses to start |
| The login-token signing key is weak or short | Login tokens become forgeable | Refuses to start |
| The scanner endpoint's secret is still the default | Anyone could submit scans as any tenant | Refuses to start |

The failure is loud and names the exact variable. This is the opposite of the usual pattern, where the insecure configuration is the one that quietly works.

**Shared credentials are no longer readable by every user.**
The connector-information screen used to return the SFTP password in plain text to *any* signed-in user of *any* role — a shared service credential that grants write access to the folder every tenant's ingestion pipeline reads from. A read-only user in one organisation could read it straight out of the API and drop files that arrive as ingested documents. It's now returned only to an IT Admin; everyone else sees the connection details they need and is told who to ask.

**The evidence archive is genuinely immutable, and that was tested, not assumed.**
Documents archived for evidentiary retention go into a separate store with S3 Object Lock in COMPLIANCE mode. This was verified by attempting the attack rather than trusting the setting: writing a locked object and then trying to permanently destroy it returns *"Object is WORM"* and fails. A normal delete leaves the locked version intact behind a delete marker; an overwrite creates a new version rather than replacing the protected one. Worth knowing for anyone auditing this: the usual command-line check for this (`mc retention info`) reports something *different* — the bucket's optional default retention rule — and reads as "not enabled" even when Object Lock is on. Retention here is set per document from its own retention class, which is why no blanket bucket-wide rule exists.

> **Found & fixed, not just designed:** A real cross-tenant data-isolation gap was found during hardening testing and fixed before this stage — see §9 for exactly what happened and why it matters that it was caught. Isolation has since been re-verified a second way: a genuine second tenant was created and used to attempt reading the first tenant's real documents, search results, and entity records directly — every attempt correctly came back empty, not just structurally rejected. That's a positive proof, not only a negative one.

---

## 7. Keeping It Running: Backup, Recovery & Scheduled Work

A system holding statutory records has to answer two operational questions that have nothing to do with features: *what happens when the machine dies*, and *what happens to work nobody is watching*.

### Backup and disaster recovery

Three scripts (`scripts/dr/`) plus a runbook (`docs/DISASTER_RECOVERY.md`). What they protect:

| Component | What's in it | Size here |
|---|---|---|
| PostgreSQL | documents, text chunks and their AI embeddings, extracted facts, the entity graph, the audit log, users and permissions | 95 MB |
| Database roles | the restricted, non-superuser login the application connects as | tiny — and critical, see below |
| Object storage | the actual scanned files and their archival PDF/A renditions | 283 MB / 1,197 objects |
| Evidence archive | the write-once retention store | 1.7 MB |

**Measured: backup 5m20s, restore 5m30s.** Recovery time is therefore about six minutes of mechanical work; how much data you could lose is simply your backup interval — nightly means up to a day.

Two details decide whether a restore actually produces a working system, and both were genuinely surprising:

**1. The database is backed up *before* the files, and that order is not arbitrary.** When a document is uploaded, the file is written to storage *first* and the database row is committed *after*. That single fact determines the safe order. Back up the database first and every row it contains is guaranteed to have had its file written earlier — so a file snapshot taken afterwards necessarily includes it. Do it the other way round and any document uploaded between the two snapshots ends up with its row inside the backup but its file outside: a document that exists, appears in the drive, and returns "not found" when opened. Silent data loss. The safe order costs a few orphaned files instead, which are invisible and harmless.

**2. Database roles are not inside a database backup.** They live at the cluster level, so a standard database dump silently excludes them. This system depends on a restricted role that *cannot* bypass Row-Level Security — that's precisely what makes tenant isolation real. Restore without it and you get one of two outcomes: the application can't connect at all, or somebody "fixes" it by pointing the app at the superuser — at which point every request bypasses tenant isolation and nothing says so. Roles are captured separately and restored first.

**Verification is part of the process, not a separate chore.** Every backup and every restore automatically checks four things: the schema version matches, every table's row count matches what the backup recorded, the audit hash-chain is still intact end-to-end, and every document's stored file actually exists. That last check matters because the retention purge permanently deletes files — a purge running during the backup window could otherwise leave a reference to a file that's gone.

**The restore was rehearsed, not assumed.** Restoring into a scratch database and separate storage, with live data untouched: every row count exact (1,282 tenants, 367 documents, 20,471 audit entries), audit chain intact across 1,228 tenants and 20,245 entries, all 240 document references resolving, all 1,231 embeddings present at full dimension, all 25 tenant-isolation policies restored, and the append-only audit protection still in place. A deliberate negative test confirmed the verifier actually fails when files really are missing — so a passing check means something.

**Scheduled nightly, with the guards an unattended job needs.** A backup runs at 02:15 through a wrapper that handles the things that quietly break scheduled jobs: the scheduler's minimal environment often can't find Docker (the job then "runs" nightly and produces nothing); a lock prevents a manual run colliding with the scheduled one; and — because this machine's disk sits at 89% full — it refuses to start below a free-space floor rather than filling the disk and taking the database down with it. A backup that fails its own verification is *kept* and renamed rather than deleted, because the evidence is worth more than the disk space.

> **The honest gap:** the backups currently sit on the same physical disk as the data they protect, because this machine has only one. That is a copy, not a backup — a single disk failure loses both. The job is one configuration line away from writing elsewhere, and includes a guard that refuses to run if the intended volume isn't actually mounted (an unmounted external drive otherwise looks like an ordinary empty folder, and the backup quietly fills the disk it was supposed to be moved off). Point-in-time recovery — rewinding to an arbitrary moment rather than to a nightly snapshot — is also not configured.

### Scheduled work

The recycle bin has a retention policy: items sit there for a set period, then are permanently purged. That schedule was *defined* but had never once actually run, because nothing was starting the scheduler process — a worker only executes what something else queues. That's now a running service, verified end-to-end by watching a job fire, reach the queue, and be executed. It exists in both the single-machine and Kubernetes deployments.

---

## 8. What's Done, What's Still Open

Every substantial piece of engineering scoped for this build is complete. What remains is a short list of items that need a decision or an external party — not more code.

**Legend:** ✅ Built end-to-end · 🟡 Mostly built, small known gap · ⛔ Blocked on a decision or outside access — not engineering

| Area | Status | Notes |
|---|---|---|
| Reading & extraction | 🟡 Mostly built | OCR, VLM field extraction, classification, ditto marks, continuation-row merge, and handwriting handling are all confirmed working against real registers. One piece — left/right spread-join across facing pages — is fully coded, correctly refuses to guess on a mismatch, and its positive (successful-join) case is now confirmed end-to-end on a purpose-built dummy document; not yet observed on a real register, see §9. A real document-classification bug (a scanned register silently matched to the wrong registered form template, from a different district and year) was found and fixed this pass — see §9. |
| Human verification workbench | ✅ Built | Queues, confidence scores, calibration gate, bulk actions with undo, click-through to the exact source rectangle. A navigation gap — the workbench page itself was fully built but had no link anywhere in the app to reach it — was found and fixed this pass; it's reachable from the main sidebar now. |
| Search & Q&A | ✅ Built | Hybrid search, bilingual, cited/grounded AI answers. Near-duplicate detection now runs at search time, not just at upload: when the corpus holds two rescans of the same underlying document, search and chat collapse them to a single citation instead of confusingly citing whichever copy happened to score higher — confirmed live against a real duplicate pair already in the system. Results now reach the screen before the written answer (§5), and the two slowest repeated steps are cached, cutting fixed pipeline work from ~2.8s to ~0.4s. Remaining search time is almost entirely waiting on two external AI services whose free tiers throttle — a paid-tier decision, not engineering (§9). |
| Entity graph & legal records | ✅ Built | Tiered-trust linking, full amendment history, legal status tracking. A real API gap — no way to delete a mistaken entity or link once created — was found and fixed this pass, live-verified end to end (create, link, view, delete). |
| Governance & audit | 🟡 Mostly built | Tamper-evident log, exports, completeness dashboard all live. The formal legal certificate is intentionally marked draft, pending legal counsel's sign-off on its wording — not an engineering gap. The health-check endpoint now actually verifies tenant isolation is enforceable, not just reporting database/cache status. The write-once evidence archive's immutability has now been proven by attempting to destroy a locked record and being refused (§6). |
| Backup & disaster recovery | ✅ Built | Full backup of database, roles and files with automatic integrity verification; restore rehearsed end-to-end with live data untouched; scheduled nightly with disk, overlap and environment guards. One real gap stated plainly: backups currently share a disk with the data, because this machine has one — see §7. |
| Operations & observability | ✅ Built | Scheduled jobs actually run (they previously never did). Application logging was entirely unconfigured — every informational log line in the codebase was being silently discarded — and is now configurable, with a per-phase search timing breakdown. The AI model is pre-loaded at startup, so the first search after a deploy no longer pays a ~17-second model load. |
| Access control & language | ✅ Built | Six-role RBAC, department scoping, full Marathi translation alongside English. |
| Ingestion connectors | 🟡 Mostly built | Upload, watched folders, SFTP, email-in and scanner intake all work today — and all of them are *newly* working: every one was silently non-functional until this pass, see §9. Each is now proven end-to-end by dropping a real file and watching it index. Google Drive / SharePoint / a government e-Office connector are ready to build but are waiting on those third parties to grant API access. One design limit worth stating: every connector files documents under a single shared identity rather than routing to the tenant the file was meant for — deliberate scope, not an oversight. |
| Fully offline ("air-gapped") mode | 🟡 Mostly built | OCR, embeddings, and re-ranking already run 100% locally. The one remaining external call is the AI vision-extraction step — a local replacement model has been evaluated and the hardware requirement is now known; it's a hardware purchase decision, not unsolved engineering. |
| Formal accuracy benchmark | ⛔ Blocked | Needs a real reference set of documents with independently verified-correct answers to score against — that reference set doesn't exist yet. |
| Business & legal sign-offs | ⛔ Blocked | Final licensing numbers and one open-source license question are drafted and ready, waiting on a business/legal decision-maker. |

> **For whoever is running the demo:** the cloud vision-extraction provider (§4, "reading" step) is currently paused pending an API credit top-up — a cost-control choice, not a bug. Every already-processed document (the ones in the demo account today) is completely unaffected: its extracted facts, Workbench queue entries, search, and chat all work exactly as normal. The one thing to avoid live is uploading a **brand-new** document and expecting structured field extraction from it — it will index and become fully text-searchable, just without the field-by-field extraction step, until the provider is re-enabled. Pick an already-processed document (e.g. the Wardha register, with 1,358 extracted facts) for anything that needs to show extraction, the Workbench, or entity linking.

**One line for the room, if asked directly:** there is no unstarted or unblocked engineering work left — everything outstanding is either a third party's access grant, a hardware budget decision, a human sign-off, a paid API tier, or (for live extraction specifically, today) a credit top-up.

**If asked what would be worked on next, in order:** paid tiers for the two AI services that currently throttle search; moving backups onto separate storage; a GPU for fully-local extraction; and per-tenant routing for the ingestion connectors. All four are decisions with a known cost, not open engineering questions.

---

## 9. Proven by Testing, Not Just Claimed

Every feature in this document was tested live, against a real account with real data — not just reviewed on paper. That process found real bugs, which is exactly the point of doing it this way.

In one hardening pass alone, all 76 core backend functions were exercised end-to-end against a live account. That pass found and fixed five real issues:

1. **Cross-tenant data leak in "Empty Bin."** The most serious finding: emptying your own recycle bin could, under a missing check, delete another customer's trashed files too. Fixed, and now has a dedicated automated test guarding against it forever.
2. **"Empty Bin" silently doing nothing.** For documents without an explicit retention policy, the delete action would silently no-op while claiming success. Now it honestly reports what was deleted and what's protected, and why.
3. **Low-contrast warning messages.** Several status banners were nearly invisible against the app's background. Fixed, then proactively re-checked across the rest of the app for the same class of issue.

A second, independent security review specifically targeted cross-tenant data isolation and found a separate, genuine gap: it was possible to link one organization's records to another organization's private data through the entity graph, with no ownership check at all. That was confirmed as real, fixed at the source, and locked in with new automated tests before it could ever reach a customer.

**Table-stitching, checked directly against the live database (2026-09-02).** Rather than trust the automated test suite alone, every stitching handler was cross-checked against real documents already processed in the running system:
- **Ditto-chain expansion:** 334 real "Do." marks correctly expanded to their real inherited value, across 124 real documents — including a real 68-page gazette register — plus 82 cases correctly flagged as unresolved rather than guessed.
- **Continuation-row merge:** confirmed on two real 16-page registration files, with one field reconstructed from 17 separate regions spanning a real page boundary.
- **Spread-join:** every real document that ever matched the spread-layout template has produced a correctly-flagged refusal ("no shared value between the two fragments") — the safe-refusal behavior is real and proven. On 2026-09-10 the positive case was proven too, on a purpose-built dummy document engineered with a genuine matching shared value across the spread: the two halves were correctly paired into one row. Still not yet demonstrated on real archival data — called out honestly in §8 rather than presented as fully proven end-to-end on real documents.

**A second live hardening pass, against real production data (2026-09-09/10).** A separate, independent round of end-to-end testing — real API calls, real documents already in the account, a real second tenant created specifically to test isolation — found and fixed nine more issues:

1. **A document silently matched to the wrong form template.** Classification only ever looked at a document's first page, which is often generic boilerplate shared across many different registered forms. A real 280-page register was silently matched to a template from a different district and decade, corrupting every field it extracted. Fixed by giving classification more of the document to look at, and by teaching it to trust the document's own content over the name of the office that happened to issue it.
2. **A chat answer that contradicted its own citation.** Asked about a specific field on a specific table row, the assistant said the field wasn't present — while the page it cited plainly showed it. The root cause: the system was only pulling in one field of a multi-field table row for exact-ID-style questions, not for natural-language ones. Fixed so every question style gets the full row.
3. **Near-duplicate documents producing confusing citations.** Two rescans of the same underlying register could cause a citation to point at a different copy than the one actually discussed. Fixed at the search layer itself — the system now recognizes true rescans (the same near-duplicate-detection logic already used at upload time) and always cites from a single, consistent copy.
4. **No way to delete a mistaken entity link.** The entity graph could create person/property links but never delete one, even a mistaken test one. Closed, audited the same way every other mutating action is.
5. **A missing security header on error responses.** A genuine server error wasn't returning the header browsers need to read error details cross-origin — invisible in normal use, but it would have made a real production incident harder to diagnose from the browser console. Fixed.
6. **The Workbench had no link to it.** The verification queue page itself was fully built and worked correctly, but nothing in the app's navigation pointed at it — it was unreachable except by typing its address directly. Fixed; it's in the main sidebar now.
7. **The health-check endpoint didn't check the thing it implied it checked.** It reported database/cache status but said nothing about tenant isolation. Now it does — see §6.
8. **A configuration value that existed in code but not in the settings screen.** A newly added search-relevance threshold was readable in code but had never been registered in the admin Settings screen the way every other threshold is — invisible and uneditable. Fixed.
9. **Handwriting detection silently always said "no."** One of the two document-reading providers never actually checked whether a value was handwritten — it always reported "no" regardless of the real answer, which matters because handwritten fields are treated differently in the verification workbench (§3). Improved to use the provider's real handwriting-detection signal instead.

Every one of these was found by actually running the system — real logins, real searches, real documents, a real second tenant — not by reading the code and assuming it was correct. Full regression suite (380+ automated tests) stayed green throughout.

**A third hardening pass, plus production-readiness (2026-09-21).** The widest pass yet: every API endpoint exercised, every ingestion path driven with real files, a second real tenant used to attack isolation, and the whole thing re-run through a browser. It found that several features believed complete had never actually worked.

**The big one: every file-ingestion connector was silently dead.**
SFTP, watched folders, email-in and scanner intake all attribute their documents to one shared identity — and that identity was a hard-coded email address belonging to no account in the database. Every connector failed on *every* poll cycle with "connector actor not found". The feature had been demonstrated by other means and nobody had watched a file actually land. Now configurable, pointed at a real account, and each path proven by dropping a real file and watching it index: SFTP through its own background loop, watched folders, email-in with a real MIME message and attachment, and scanner intake through the actual browser UI.

**Four more that had never worked at all:**

1. **Scanned uploads always failed with a server error.** The endpoint that receives scans ran without the database tenant context set, so the very first thing it tried — creating the "Scanned Documents" folder — was rejected by the tenant-isolation rules. Two separate bugs were stacked here: it also called the ingestion routine without passing who the document belonged to. In the browser this surfaced as *"Failed to ingest scan. Check connection and try again."* — which sent anyone hitting it to investigate their network, the one thing that wasn't wrong.
2. **The scanner's folder watcher could never ingest a single file.** Same missing-arguments bug in a second place. It converted a multi-page TIFF to PDF correctly, then crashed on the final step, every time.
3. **Scans could be filed under the wrong account, silently.** The scanner accepts a header naming which user a scan belongs to. That lookup ran without the narrow permission it needs, so it always found nobody and quietly fell back to the default identity — a caller saying "file this as Ayesha" got it filed as someone else, with no error. Proven fixed by filing a scan as a *different* tenant and confirming it landed there.
4. **Creating a document template always failed.** The code claimed in a comment that templates had no tenant column; a migration had since added one, with isolation rules attached. Every creation attempt was rejected by the database. Nothing in the test suite covered it.

**A folder watcher pointed at a folder that didn't exist.** The deployment file bound the user's own `Stark Drive` folder with a stray space before the colon, so the system created and watched a directory literally named `"Stark Drive "` — with a trailing space. Anything dropped into the real folder would never have been ingested. One character; found by listing the disk, not by reading the code.

**Three production-security gaps that would only have surfaced in production:** the restricted database role being unset would have disabled tenant isolation with only a log line to say so; the inbound-email secret shipped as a public default while that endpoint was enabled by default; browser origins were unrestricted. All three now prevent startup (§6). Separately, the shared SFTP password was being handed to any signed-in user of any role.

**Scheduled jobs had never run, and logs were being thrown away.** The daily retention purge was defined but no scheduler process existed to fire it. And nothing in the application had ever configured logging, so the default level silently discarded *every* informational message in the entire codebase — including the connector messages that would have revealed the failures above years earlier. Both fixed; the second is why the rest of this list was findable.

**The test suite was quietly corrupting its own environment.** Each test created a throwaway organisation and never removed it: 6,434 accumulated, along with ~5,700 orphaned users and thousands of stray documents. Any question like "how many documents are indexed?" was meaningless. The suite now cleans up after itself, and the backlog was cleared — carefully, because ~1,264 of those organisations *cannot* be deleted: they're referenced by append-only audit entries, and the audit log correctly refuses to be edited. That's the tamper-evidence guarantee working as designed, so they were left in place rather than worked around.

**Two optimisations were tested and rejected — which is the point of testing them.**
- *Halving the search AI calls* by dropping the cross-script re-ranking probe: four of five plain-English queries returned **zero results** without it (§5). It would have looked like a 2× speedup and destroyed search.
- *Running re-ranking locally* to remove an external dependency: the bundled local model ran over ten minutes on twenty real text chunks, at 194% CPU and 4.5 GB of memory, versus roughly half a second for the network call. Not viable on this hardware.

**One of our own findings turned out to be wrong, and is corrected here.** An earlier report in this pass stated the evidence archive had no write-once protection enabled. It does. The check used reports a *different* setting — the bucket's optional default retention rule — and reads as "not enabled" even when Object Lock is on. Verified properly by trying to destroy a locked record and being refused (§6). Recording the correction rather than quietly deleting the claim is the same standard applied to everything else here.

Full regression suite — **391 tests — green throughout.** Two tests that call a live AI service now *skip* with a stated reason when that service is rate-limited, instead of reporting a third party's quota as a code failure; a suite that goes red for someone else's billing teaches people to ignore red suites.

> **Why this section exists:** the point of naming these bugs and gaps isn't to advertise flaws — it's that this is the standard of scrutiny this kind of system is held to before it touches a real legal record: find it yourself, report it honestly, and prove it can't silently happen again.

---

## 10. Demo Cheat Sheet

Likely questions from the room, answered in one breath.

**"How does search stay fast as documents pile up?"**
PostgreSQL's pgvector extension uses an HNSW index — a search structure built specifically so that finding the closest matches among millions of entries stays fast, instead of slowing down in a straight line with data size.

**"How do you know the AI isn't making things up?"**
Every answer must cite its source document and page. If it can't find real grounding for a claim, it explicitly refuses to answer rather than guess — that's enforced in code, not a setting someone could turn off.

**"What happens if a reviewer makes a mistake?"**
Every action is logged and reversible. Bulk edits have one-click rollback, and the audit trail itself can never be silently altered after the fact.

**"Is our data ever sent outside our own systems?"**
OCR runs entirely on local infrastructure today. The one component still calling an external AI service is the structured field-extraction step — and that's precisely the piece the local-model research (§4, §5) is aimed at replacing.

**"Can this run fully offline, with zero internet access?"**
That's the explicit design target. The system already has a hard switch that fails an operation closed — rather than silently falling back to the internet — the moment air-gapped mode is turned on.

**"What's the single biggest open risk?"**
No dedicated GPU yet for local AI extraction, so that one step still depends on a cloud model. The hardware requirement to fix that is now precisely known — it's a budget decision, not an unknown.

**"Can you show a table that was split across a left and right page getting stitched together?"**
Be straight about this one rather than attempt it live: that specific case (a two-page facing-page split) is built and its safety behavior is proven — it correctly refuses to guess rather than produce a wrong answer — but a real successful join hasn't been demonstrated yet. What *can* be shown live and is fully proven: a value split top-to-bottom across a page boundary being correctly reconnected, and "Do." ditto marks being correctly expanded — both confirmed on real registers.

**"Can I upload a new document right now and see it fully processed?"**
It'll index and become fully text-searchable immediately — but the field-by-field extraction step (Workbench entries, structured facts) is paused right now pending an AI-provider credit top-up, a cost-control choice, not a limitation. Every document already in the account was processed before that pause and is completely unaffected — use one of those (the Wardha register, with 1,358 extracted facts, is the best one to point at) for anything that needs to show extraction or the Workbench.

**"What happens if this server dies tomorrow?"**
There's a tested answer, not a hopeful one. A nightly backup covers the database, the files, and the database roles, and it verifies itself — row counts, the audit chain, and that every document's file really exists. Restoring takes about five and a half minutes, and that restore has been rehearsed end-to-end into a scratch copy with live data untouched. Be straight about the one gap if asked: on this demo machine the backups sit on the same disk as the data, because it only has one. Moving them is a single configuration line.

**"Why do the search results appear before the written answer?"**
Because they're ready first. Finding the documents is fast; composing a cited answer about them costs an extra AI round-trip. Rather than make you wait for the slower half, the documents are sent as soon as they exist and the answer follows on the same connection — results at about three seconds, answer at about four and a half.

**"Why doesn't the answer stream in word-by-word like ChatGPT?"**
Deliberate. The answer is assembled from individual claims that are each *checked* before being shown — every number in a claim has to appear in the excerpt it cites, and claims that fail are dropped. Streaming raw words would mean showing you text the checker is about to reject and then taking it back. We'd rather be a second slower than briefly show an unverified claim.

**"Search sometimes feels slower than other times — why?"**
Honest answer: two of the AI services are on free tiers with hard per-minute limits, and roughly five searches a minute is enough to hit one. When that happens the system doesn't fail — it degrades — but it waits. The fixed pipeline cost has been cut to under half a second; almost all remaining time is those external calls. Paid tiers are a purchasing decision, and the system already has the measurements to justify it.

**"What if the same document was scanned twice and both copies ended up in the system?"**
Handled two ways: at upload, the system flags likely rescans for a human to resolve. At search/chat time, if two near-identical copies both come up for the same question, the system now automatically cites from just one of them consistently, instead of the answer and its citation pointing at two different copies of the same content — confirmed live against a real duplicate pair already in the account.

---

## 11. Glossary

Every term used above, defined plainly, so nothing in a follow-up question catches you off guard.

| Term | Definition |
|---|---|
| **Waqf** | A permanent Islamic charitable/religious endowment, usually land — the real-world record type this system was purpose-built to digitize. |
| **OCR** | Optical Character Recognition — software that reads text out of a scanned image. |
| **VLM** | Vision-Language Model — an AI that looks at an image (not just text) and can reason about what's in it, like which box on a form a handwritten answer belongs in. |
| **RAG** | Retrieval-Augmented Generation — having an AI answer a question using real retrieved documents as its source, instead of relying only on what it memorized during training. |
| **Embedding / Vector search** | Converting text into a list of numbers that captures its meaning, so "search by meaning" (not just exact keywords) becomes possible. |
| **Multi-tenant** | One running system serving many separate customer organizations ("tenants"), each seeing only their own data. |
| **Row-Level Security (RLS)** | A PostgreSQL feature that enforces tenant isolation inside the database itself, so even a coding mistake in the app can't leak another tenant's data. |
| **RBAC** | Role-Based Access Control — permissions are attached to a named role (like "Auditor"), and every user is assigned one. |
| **Audit hash chain** | A log where each entry cryptographically references the one before it, so silently editing past history becomes mathematically detectable. |
| **Celery / background worker** | A system for running slow tasks (like reading a scanned page) outside the main request, so the user's screen is never frozen waiting. |
| **HNSW index** | A search-index structure that keeps "find the closest matches" fast even as the number of records grows into the millions. |
| **JWT** | JSON Web Token — a signed, tamper-proof login credential the browser holds after signing in. |
| **Air-gapped** | Running with zero connection to the outside internet — a hard requirement for some government deployments. |
| **Ditto mark** | Old shorthand ("Do.") clerks wrote meaning "same value as the row above" — handled explicitly rather than mistaken for real data. |
| **Table stitching** | Automatically reconnecting a table row that's split across a page boundary back into one correct row. Two forms: top-to-bottom across consecutive pages (proven on real registers) and left/right across a facing-page spread (safe-refusal proven on real documents; successful-join case now proven end-to-end on a purpose-built dummy document, not yet on a real register). |
| **Entity graph** | A network linking the same real-world person, property, or organization across multiple separate documents. |
| **Confidence score** | A number the AI attaches to each extracted value showing how sure it is — used to route uncertain answers to a human instead of guessing. |
| **Human-in-the-loop** | A design rule that a person must explicitly confirm an AI's output before it's treated as an official fact. |
| **Celery Beat** | The scheduler that fires recurring jobs on time. Distinct from a worker, which only runs what something else has already queued — without Beat, a "daily" job is defined but never happens. |
| **SSE (Server-Sent Events)** | A way for the server to push several pieces of one response to the browser as they become ready, over a single connection — used here to show search results before the AI answer is finished. |
| **Object Lock / WORM** | Write-Once-Read-Many storage. A record written under COMPLIANCE-mode lock cannot be altered or deleted by anyone — including the account owner — until its retention period expires. |
| **RPO / RTO** | Recovery Point Objective (how much recent work a failure could lose — here, your backup interval) and Recovery Time Objective (how long restoring takes — here, about six minutes). |
| **Rate limit / throttling** | A cap on how many requests an external service accepts per minute. Exceeding it doesn't break the system, but it waits — the main reason search timing varies. |
| **Re-ranking probe** | A second pass that re-scores search candidates for real relevance. This system runs two per search, one in each script, because an English-only probe scores Marathi documents near zero. |

---

*Prepared for internal demo use · every claim above reflects what has been live-verified against the running system.*
