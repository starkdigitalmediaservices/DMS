# Client Demo Script

A literal, read-aloud script for presenting the project — technical and
business framing woven together at each step. Weighted toward what's
new (the ingestion connectors, the security model in action) rather
than re-explaining product basics you already know cold.

Total run time: ~12-15 minutes if you do everything below; ~8 minutes
if you cut to the "Tight version" bookmarked at each section.

---

## Before you start

- [ ] `docker compose ps` — confirm all 10 containers say "Up"
- [ ] Log in as `biznesskd07@gmail.com` in one browser tab
- [ ] Have a file manager window pre-connected to the SFTP folder
- [ ] Have a terminal open, already `cd`'d into the project folder
- [ ] Pick 2-3 files you have **not** uploaded through any channel yet
      tonight, so nothing gets silently skipped as a duplicate on stage
- [ ] Second browser tab ready at the signup page, for the security beat

---

## 1. Open — one line, then move

> "I'm going to show you this live, not slides. Three things: how a
> document gets into the system without anyone having to think about
> it, how you find it again in seconds, and how it's kept separate from
> everyone else's data. Let's start with the part most systems get
> wrong."

*(Don't linger here — the opening line's job is to get you off the
title screen and into the product in under ten seconds.)*

---

## 2. Getting documents in — the part that's new

**Say:**
> "Most document systems assume one thing: that someone will remember
> to open the app and upload a file. In practice, that step gets
> skipped constantly — documents pile up 'to add later,' and later
> never comes. So we built this so a document gets in the moment it
> exists, no matter which system produced it or who's holding it."

### 2a. Auto-sync a real folder

**Do:** Drag a pre-picked file into `/home/stark/Stark Drive /` on
screen.

**Say:**
> "This is a folder on my computer I already use — nothing special
> about it. Watch."

*(while it processes, ~20-30 sec)*
> "No upload button, no login on this folder. DMS checks it
> automatically. In about twenty seconds this shows up fully indexed
> and searchable, same as a manual upload — because under the hood, it
> literally goes through the exact same pipeline."

**Do:** Switch to Drive, refresh, point at the file.

### 2b. Bring in a device that isn't this one

**Say:**
> "That folder only works because it's on this machine. What about a
> vendor, or another office, with no access to your server at all?"

**Do:** Click **+ New → Connect a device** → **Folder / SFTP** tab.

> "This is self-service — anyone on the team can open this panel and
> hand these details to an outside vendor themselves. No engineer has
> to issue credentials by hand."

**Do:** Switch to the pre-connected file manager window, drag a file
into the SFTP folder, then refresh Drive to show it land.

### 2c. Email — zero setup, zero training

**Say:**
> "And this is the one I think matters most, because it needs nothing —
> no client install, no server access, no training. Everyone already
> knows how to attach a file to an email."

**Do:** Click the **Email** tab in the same panel, point at the
address. Then, in the pre-positioned terminal:
```bash
cd "/home/stark/Work Space/DMS"
python3 scripts/send_demo_email.py "/path/to/a/fresh/file.pdf"
```
> "I just sent that as an email a second ago. Give it about ten
> seconds..."

**Do:** Refresh Drive, show it appear.

**Say, closing this section:**
> "Three completely different ways in — a folder, another server, an
> email — and every single one lands in the same place, searchable the
> same way, in about the same twenty seconds. Whatever already produces
> your documents today, this absorbs it without changing how your team
> works."

**Tight version:** if you're short on time, do only 2c (email) — it's
the fastest to set up on stage and the most visually convincing, since
the file is obviously arriving from "outside."

---

## 3. Finding it again — ask, don't search

**Say:**
> "Now the other half. Traditional systems make you remember the exact
> filename or folder. Here, you just ask."

**Do:** Type a natural-language question about one of the documents
you just added. Let the AI Summary Card render.

> "That's not a list of files to go open and read yourself — that's a
> synthesized answer, generated from the actual document content, with
> the exact source cited underneath. If you don't trust an AI's answer
> on faith, you don't have to — click through to the source page and
> verify it in one click."

**Do:** Click into a cited result, show the in-browser preview.

> "No download required to check a citation. That builds trust in the
> answer, which is the whole point of putting AI in front of your
> documents in the first place."

**Business framing (say if the audience is non-technical):**
> "The reason this matters commercially: your team stops spending time
> searching and starts spending time on the actual answer. That's the
> return on this, not a feature checkbox."

---

## 4. Multi-tenant security — the 30-second trust builder

**Say:**
> "One more thing, and this is the fastest way I can prove data
> isolation to you, rather than just claim it."

**Do:** Switch to the second browser tab, sign up a brand-new
organization live. Search for a term you know exists in the first
org's documents.

> "Zero results. Not because we filtered it in the application code —
> it's enforced at the database level, so there's no code path that
> could accidentally leak across organizations. If you're running this
> across departments or client accounts, this is the guarantee that
> matters."

---

## 4b. Administration — who runs the system, and who sees what

Everything in this section lives under **profile menu (top-right avatar)
→ Administration**. It has five entries: **Admin Panel, Users & Roles,
Departments, Form Templates, Settings**. The whole section is visible
**only to IT Admin** — log in as any other role and it isn't in the
menu at all, and typing the address shows a "no access" screen.

**Before the meeting (in addition to the checklist at the top):**
- [ ] Second browser window, **incognito**, logged in as
      `test.operator@veritasdocs-rbac-test.com` / `RbacTest@2026`
- [ ] On **Departments**, grant "Revenue Records Dept" one folder with
      real documents in it (right now it only has `best`, 3 files)
- [ ] ⚠️ **Personal files are in this organization's Drive.** The
      Drive and the Workbench's *unclassified* list include a salary
      slip, several resumes, a third party's CV and test files
      (`broken_test.pdf`, `App.test.tsx`…). Move them to the Bin — or
      demo from a department-scoped login (the operator only sees its
      granted folder) — before sharing your screen

**Say, opening this section:**
> "So far you've seen what an ordinary user does. Now the other side:
> the person who runs the system. Everything an IT administrator needs
> is in one place, and — importantly — nobody else can even see it."

**Do:** Click the avatar → point at the **Administration** heading
and the five links under it.

### 4b-1. Admin Panel — the health dashboard

**What it is:** a live dashboard for *your organization only* — it
never shows another organization's numbers. Two tabs.

- **DMS Analytics tab**
  - Headline tiles: **Total Users, Tenants** (always 1 — it's your
    organization), **Documents, Trashed, Folders, Storage, AI Chunks**
    (the searchable pieces documents are split into), **Chat Sessions,
    Audit Logs**
  - **Document Processing Status** — how many documents are indexed,
    still processing, or failed
  - **File Types Distribution** — PDFs vs images vs Office files, by
    count and size
  - **Upload Timeline (Last 30 Days)** — uploads per day
  - **Top Uploaders** — who uploaded how much (documents and size)
  - **Storage Per Tenant** — your organization's documents, storage
    and user count on one line
  - **Recent Audit Activity** — the latest recorded actions: who did
    what, when, from which IP
- **API Analytics tab** — how the system itself is behaving
  - **Total Calls, Calls Today, Avg Response** time, **Error Rate**
  - Calls by HTTP method, status-code distribution
  - **Top Endpoints by Volume** and **Slowest Endpoints** — where the
    load and the delays are
  - **Recent API Calls** — the last requests, with status and timing

**Say:**
> "This is the administrator's health check. How much is stored, what's
> still processing, what's failing, and a live trail of who did what.
> The second tab is for the technical team — response times and error
> rates, so problems show up here before users report them."

### 4b-2. Users & Roles — who is who

**What it is:** every person in the organization, their role, and
their department(s).

- **Change a role:** pick from the dropdown on a row — it takes effect
  on that person's **very next click**, not at their next login
- **Add user:** email, name, role → the system generates a **one-time
  temporary password**, shown once with a Copy button. It is never
  shown again; the new user changes it from their profile
- **Safety guards:** your own row is locked (you can't change your own
  role), and the system refuses to demote the last IT Admin — so an
  organization can never lock itself out

**The six roles, in one breath each:**
| Role | What they do | What they see |
|---|---|---|
| **IT Admin** | Runs the system, manages users, departments, templates, settings | Everything |
| **Records Officer** | Reviews extracted data, can delete, issues Section 63 certificates | Their department's folders |
| **Operator** | Day-to-day data review and correction | Their department's folders |
| **Department Head** | Oversees the department, can delete, sees billing | Their department's folders |
| **Legal Counsel** | Read-only across everything, certificates, exports | Everything, read-only |
| **Auditor** | Read-only, runs the audit-integrity check | Everything, read-only |

**Do:** Click **Add user**, fill in a demo person (use an address like
`demo.clerk@example.com`), choose **Operator**, create → show the
one-time password.

**Say:**
> "Adding a person takes ten seconds. They get a temporary password
> once, it's never stored where anyone can read it back, and their role
> decides everything they can do — enforced by the server, not by
> hiding buttons."

### 4b-3. Departments — who sees which documents

**What it is:** a department is a group of people, granted access to
specific **projects (folders)**. Members of department-scoped roles
(Records Officer, Operator, Department Head) see **only** their
department's folders — plus anything they uploaded themselves. A grant
covers the folder **and everything inside it**.

- Create / delete a department
- **Add / remove members** — pick from the user list
- **Grant / revoke folders** — pick from the folder tree

**Do — the strongest moment of the demo:**
1. Show the Operator's window: they see only the granted folder.
2. In the Admin window, **revoke** that folder.
3. Refresh the Operator's window — it's gone. Search for something
   that was in it — nothing comes back.

**Say:**
> "Access is decided by the database itself, not by the screen. Search,
> the AI assistant, exports, direct links — none of them can reach a
> folder your department hasn't been granted. And it fails safe: if a
> developer ever forgets a rule, people see *less*, never more."

### 4b-4. Form Templates — teaching the system your forms

**What it is:** a template tells the system what a particular kind of
document looks like, so it can **recognise** it and **pull out each
field automatically** — including tables that run across pages.
Without a matching template, a document is still stored and searchable,
but stays *unclassified* and no field-by-field extraction happens.

Each template has:
- **Form type** — e.g. *Maharashtra State Wakf Gazette Register*
- **Era label** — which law/period the form belongs to, e.g. *BPT Act
  1950 / Waqf Act 1995* — the same form changed across decades
- **Layout** — `single_page`, or `spread` (one entry printed across
  two facing pages, which the system joins back together)
- **Fields** — the columns to read, each with a type (text, number, or
  free-text "blob" such as an area written as "1 ha 25 are") and an
  optional role that drives the special handlers: `serial` (the row
  number, used to join spreads), `chain_anchor` (for ditto marks —
  "same as above"), `continuation_text` (rows that continue onto the
  next page)

**The five real templates to talk about:**
1. Waqf Institution Registration File — BPT Act 1950 / Waqf Act 1995
2. Maharashtra State Wakf Gazette Register — Aurangabad Gazette, 1973 (19 fields, spread)
3. Gazette Register, Form A (no-property Wakfs) — Marathwada, 1973-74
4. Gazette Register, Form B (Property Assessment) — Wardha, 2004
5. Maharashtra Gaon Namuna 7/12 Survey Record — Pune village land record

**Say:**
> "This is how the system learns a new kind of register. You describe
> the form once — its columns, whether an entry spans two pages — and
> from then on every scan of that form is recognised and read field by
> field. The Gazette register from 1973 is a two-page spread; the system
> joins each entry across both pages, and if the two halves don't
> agree, it stops and asks a person instead of guessing."

### 4b-5. Settings — tuning without a developer

**What it is:** the system's adjustable thresholds, editable in place
(pencil → change → save), each with a plain description. Currently 18,
for example:
- **Trash retention days** (30) — how long deleted items are kept
  before permanent removal
- **Search relevance threshold** and **search cache time** — how
  strict search is, and how long answers are reused
- **Duplicate similarity threshold** (0.92) — how alike two documents
  must be to be flagged as possible duplicates
- **Chunk size / overlap** — how documents are split for AI search
- **Table-stitch thresholds** — how confident the system must be
  before joining table rows across pages

**Say:**
> "Everything that's a judgement call — how long to keep deleted files,
> how strict search is, when to flag a duplicate — is a setting here,
> not a line of code. Your IT team adjusts it; no developer, no
> redeploy."

*(Don't change a value live — just open the pencil on one row and
cancel.)*

**Tight version:** Users & Roles → Departments → the live revoke
(4b-3). That's the whole trust story in about three minutes; skip the
Admin Panel, Templates and Settings unless asked.

---

## 5. One more trust signal — no vendor lock-in

**Do:** Open the `.env` config file (or just describe it if you'd
rather not show raw config on screen).

**Say:**
> "Every AI role here — the model that answers your questions, the
> embeddings, the reranker — is swappable with a one-line change. This
> isn't theoretical: during final testing last night, the AI provider
> we were using deprecated the exact model we had configured. Fixing
> production impact was a one-line change, not an emergency migration.
> You're never hostage to one AI vendor's pricing or uptime."

---

## Business positioning (reference — pull from as needed, don't read verbatim)

### The one-liner
> "Instead of your team searching through folders and filenames, they
> ask a question in plain language — and get back a direct answer,
> with the exact source document and page cited, in seconds. And
> documents get in on their own, however they already arrive."

### USP, ranked by what a client actually cares about
1. **Zero-effort intake** — folder, SFTP, or email, all converging on
   one pipeline; nothing requires a workflow change from your team.
2. **Ask, don't search** — grounded, cited answers, not a ranked list
   of files to open yourself.
3. **True multi-tenant isolation** — database-enforced, not an
   application check a bug could bypass.
4. **No AI vendor lock-in** — every AI role is configuration, not code.
5. **Trilingual out of the box** — English, Hindi, Marathi, one query
   surface.
6. **Transparent retrieval** — the system shows *how* it found an
   answer, not a black box.

### Traditional DMS vs. this system

| Traditional DMS | This system |
|---|---|
| One ingestion path, manual only | Folder, SFTP, or email — same pipeline, same guarantees |
| Find by browsing folders/filenames | Ask a question, get a cited answer |
| Search quality is fixed | Layered pipeline — each layer independently upgradeable |
| Usually single-tenant or weak isolation | Database-enforced multi-tenant isolation |
| Locked to one AI vendor (if any) | Swap providers via config, no rebuild |

### What it costs
> "At moderate volume — 100k documents, 50k searches a month — we're
> estimating roughly $250-450/month all-in, infra plus metered AI cost.
> Caching and candidate-filtering are built into the pipeline
> specifically to keep that from scaling linearly as usage grows."

---

## Anticipated Q&A

**"Is that email address real / does it use our actual mailbox?"**
> "For the demo we're running a local test mailbox so it doesn't depend
> on real internet delivery being fast in front of you. In your actual
> deployment it points at your real company mailbox — same mechanism,
> different address."

**"Can different people have different connector credentials?"**
> "Today it's one shared connector account per deployment. Per-user
> credentials with individual permissions is a natural next step, not
> a rebuild — happy to scope that with you directly."

**"Does it handle folders-inside-folders, or only flat files?"**
> "Full nested folder structures — drop a folder with subfolders in,
> and it recreates that exact structure as real folders in DMS, not
> flattened filenames." *(Demo live if you have an extra minute — drag
> a folder with one subfolder inside instead of a single file.)*

**"How is this different from just using ChatGPT on our files?"**
> "A general chatbot doesn't have persistent, tenant-isolated storage,
> doesn't cite the specific source page for every claim, and doesn't
> give you a search layer you can inspect and tune. This is retrieval
> with generation on top — not generation with search bolted on."

**"What happens if the AI gets something wrong?"**
> "Every claim in an answer must cite a specific document and page, and
> it's checked: a claim without a valid source is dropped, and a number
> that doesn't appear in the cited passage is dropped too. If nothing
> survives that check, the system refuses to answer rather than guess —
> 'no passage, no answer'."

**"Can this run fully on our own infrastructure?"**
> "The core stack — database, storage, search — already runs fully
> self-hosted. The language model and reranking calls go to external
> AI APIs by default today, but because every AI role is
> provider-abstracted, pointing them at locally-hosted models is a
> configuration change, not a redesign."

**"What file types are supported?"**
> "PDF, Word, Excel, PowerPoint, plain text, CSV, RTF, images with OCR
> — and roughly thirty code/config formats added recently, since teams
> increasingly store more than office documents."

**"Is this production-ready, or a prototype?"**
> "Core platform — ingestion, hybrid search, multi-tenant security,
> the human-review workbench, tamper-evident audit trails, and six
> role-based personas with department-level access — is built, and the
> backend runs 412 automated tests. What's still open is mostly on
> your side: the reference document set to measure accuracy against, a
> GPU for fully offline operation, and sign-off on the Section 63
> certificate wording. I'd rather say that directly than have you find
> the gap yourself later."

---

## Closing line

> "The short version: documents get in without anyone having to think
> about it, they get found by asking rather than browsing, and your
> data never touches anyone else's. That's the product."

---

## Appendix — testing each role (rehearsal checklist, not read aloud)

Use this before the demo to confirm every role behaves as described in
4b. Everything here was verified against the live system on 23-Sep.

### Setup (once, ~5 minutes)

1. Open **http://localhost:3000/login** — a normal window for yourself
   and an **incognito window** for the role under test, so both stay
   logged in.
2. Every test account's password is **`RbacTest@2026`**. Your own
   account (`biznesskd07@gmail.com`) is the IT Admin.
3. As IT Admin: **Profile menu → Administration → Departments** →
   grant **"Revenue Records Dept"** a folder with real documents (it
   only has `best`, 3 files, by default).
4. Move one unimportant document to the **Bin**, so there is something
   to test permanent delete on.

### 1. IT Admin — `biznesskd07@gmail.com`

**Use:** runs the system — users, departments, form templates,
settings; sees everything.

| Try | Expected |
|---|---|
| Open the profile menu | **Administration** section with 5 links |
| Users & Roles → **Add user** | One-time temporary password shown with Copy |
| Change your **own** role | Not possible — your row is locked |
| Departments → revoke a folder, refresh the Operator's window | Folder disappears for the Operator immediately |
| Admin Panel | 45 documents, 7 users; Top Uploaders shows only you |
| Drive | All folders (Personal, Scanned Documents, …) |

### 2. Operator — `test.operator@veritasdocs-rbac-test.com`

**Use:** day-to-day clerk who checks and corrects what the machine
read — only within their department.

| Try | Expected |
|---|---|
| Drive | **Only** the department's folders (`best` + whatever you granted) |
| Search / AI Chat for something in another folder | No results from outside the department |
| Workbench | Review buttons shown (claim, confirm, correct, bulk-confirm) |
| Bin | **No** "Empty Bin", no permanent delete |
| Profile menu | **No** Administration section |
| Go to `localhost:3000/admin/users` directly | "You don't have access" screen |

### 3. Records Officer — `test.records_officer@veritasdocs-rbac-test.com`

**Use:** owns the department's official records — reviews, corrects,
deletes, certifies.

| Try | Expected |
|---|---|
| Drive | Department-only, same as the Operator |
| Workbench | Review buttons shown |
| Bin | Permanent delete and **Empty Bin available** (the difference from Operator) |
| Profile menu | No Administration section |

### 4. Department Head — `test.department_head@veritasdocs-rbac-test.com`

**Use:** oversees the department and approves removals; doesn't do
data entry.

| Try | Expected |
|---|---|
| Drive | Department-only |
| Workbench | **Read-only notice**, no confirm/correct buttons |
| Bin | Permanent delete **available** |
| Profile menu | No Administration section |

### 5. Legal Counsel — `test.legal_counsel@veritasdocs-rbac-test.com`

**Use:** lawyer gathering evidence across all departments; read-only.

| Try | Expected |
|---|---|
| Drive | **All** folders |
| Workbench | Read-only notice |
| Bin | No permanent delete |
| Profile menu | No Administration section |

### 6. Auditor — `test.auditor@veritasdocs-rbac-test.com`

**Use:** checks records weren't tampered with; reads everything,
changes nothing.

| Try | Expected |
|---|---|
| Drive | **All** folders |
| Workbench | Read-only notice |
| Bin | No permanent delete |
| Profile menu | No Administration section |

### Permissions with no button yet — test from a terminal

The audit-integrity check, billing and user list are enforced by the
server but have no screen for most roles. **200** = allowed,
**403** = blocked.

```bash
# 1. Log in as the role to test (change the email)
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"test.auditor@veritasdocs-rbac-test.com","password":"RbacTest@2026"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Audit-integrity check
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/governance/audit-integrity

# 3. Billing
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/billing/subscription

# 4. User list
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/users
```

| Check | Allowed (200) | Blocked (403) |
|---|---|---|
| Audit integrity | Auditor, IT Admin | Operator, Records Officer, Dept Head, Legal Counsel |
| Billing | Dept Head, Auditor, IT Admin | Operator, Records Officer, Legal Counsel |
| User list | IT Admin | everyone else |

### Two live-change tests (the most convincing)

1. **Revoke:** Operator logged in in one window; as Admin, remove a
   folder on Departments; refresh the Operator's window — it's gone.
2. **Change role:** as Admin, change the Operator to **Auditor** on
   Users & Roles; on the Operator's next click they see all folders and
   lose the review buttons. **Change it back afterwards** so the test
   accounts stay as listed.
