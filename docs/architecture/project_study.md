# VeritasDocs — Project Study

**Multi-Tenant AI Document Management & Search Platform**
Prepared: 20-Aug-2026 · For: client demonstration, 21-Aug-2026

This document is a complete technical study of the system: what it is, how
it works, what it's built on, how it compares to conventional document
management, and — honestly — what it doesn't do yet. It's written to
brief a presenter, not to sell; the companion file `docs/demo/demo_script.md`
handles the pitch.

---

## 1. What This Is

A web platform where an organization uploads documents (PDFs, Word,
Excel, PowerPoint, scanned images, text) and instead of browsing a folder
tree or matching keywords, users **ask questions in plain language** and
get back a synthesized answer with citations pointing to the exact page
and document it came from.

It is **multi-tenant**: multiple organizations can share one deployment
with database-enforced data isolation, not just application-level checks.

It supports **multiple ways documents get in** — manual drag-and-drop
upload today, plus two automated ingestion channels built for this
release: a watched folder and an SFTP drop point, both feeding the same
pipeline as manual upload.

---

## 2. System Architecture

```
                    ┌──────────────────────────────────┐
                    │   Next.js 14 Web Frontend         │
                    │   (Port 3000 · React 18)          │
                    └────────────────┬───────────────────┘
                                     │ REST / JSON, JWT bearer auth
                                     ▼
                    ┌──────────────────────────────────┐
                    │   FastAPI Async Backend           │
                    │   (Port 8000 · Python 3.11)       │
                    └───┬──────────┬──────────┬──────────┘
                        │          │          │
          Celery tasks  │   SQL (async)  │   S3 API (presigned)
                        ▼          ▼          ▼
              ┌──────────────┐ ┌──────────────────┐ ┌──────────────────┐
              │ Redis 7      │ │ PostgreSQL 16 +   │ │ MinIO / AWS S3   │
              │ (queue+cache)│ │ pgvector (HNSW)    │ │ (object storage) │
              └──────┬───────┘ └────────────────────┘ └──────────────────┘
                     │
                     ▼
              ┌──────────────┐        ┌──────────────────┐
              │ Celery Worker│───────▶│ Flower Dashboard  │
              │ (async parse,│        │ (Port 5555)       │
              │  embed, index)│       └──────────────────┘
              └──────────────┘

  Ingestion channels feeding the same pipeline:
  manual upload (UI)  │  watched folder (poller)  │  SFTP (poller)
```

**Eight containers in the local deployment:** `frontend`, `backend`,
`worker`, `flower`, `postgres`, `redis`, `minio`, `sftp`.

### 2.1 Why this shape

- The **frontend never talks to the database or AI providers directly** —
  everything goes through the FastAPI backend, which is the single point
  of authorization and tenant-isolation enforcement.
- **Ingestion is asynchronous.** Uploading a file returns immediately
  (`status: pending`); a Celery worker does the actual OCR/parsing/
  embedding in the background so the UI is never blocked waiting on AI
  calls. This is true whether the file arrived via drag-and-drop, the
  watched folder, or SFTP — all three hand off to the same Celery task.
- **Search is synchronous but layered for speed** — a cache check first,
  then hybrid retrieval, then reranking, then generation — so most of the
  latency is spent where it earns the most accuracy, not uniformly.

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS |
| Document preview | Mammoth.js (`.docx` → HTML), `xlsx` (spreadsheet → table), native PDF viewer |
| Backend | FastAPI 0.115, Uvicorn (ASGI), Python 3.11, async SQLAlchemy 2.0 |
| Database | PostgreSQL 16 + `pgvector` (HNSW cosine-similarity vector index) |
| Cache / queue | Redis 7 (Celery broker + search-result cache) |
| Background jobs | Celery 5.4, monitored via Flower |
| Object storage | MinIO locally (S3-API-compatible); AWS S3 in production |
| Auth | JWT (access + refresh tokens), bcrypt password hashing |
| Migrations | Alembic |
| Document parsing | `pdfplumber`, `PyPDF2`, `python-docx`, `openpyxl`, `python-pptx`, `striprtf`, Tesseract OCR |
| SFTP connector | `paramiko` |
| Rate limiting | SlowAPI |

**AI providers (all hot-swappable via `.env`, no code changes):**

| Role | Currently configured | Alternatives supported |
|---|---|---|
| LLM (summaries, metadata, chat) | Groq (`openai/gpt-oss-120b`) | OpenAI, Anthropic |
| Embeddings | BGE-M3, local, 1024-dim | OpenAI, Google Gemini, Cohere |
| Reranking | Cohere (`rerank-english-v3.0`) | BGE reranker (local) |
| OCR | `pdfplumber` (native text) | LlamaParse (layout-aware, for complex scans) |

This provider abstraction is a real architectural asset: switching from
a paid API to a locally-hosted model, or between vendors, is a config
change, not a rewrite. It's also why the system survived a live incident
during testing — see §8.

---

## 4. Core Workflow

### 4.1 Ingestion (upload → searchable)

```
File arrives (upload / watched folder / SFTP)
   │
   ▼
Hash computed (SHA-256) → stored in MinIO/S3 → DB record created (status: pending)
   │                                                    │
   ▼                                                    ▼
Celery task queued  ─────────────────────────  API returns immediately (201)
   │
   ▼
Worker: OCR/text extraction (format-specific parser)
   │
   ▼
Chunking (512 tokens, 64-token overlap, page number retained per chunk)
   │
   ▼
Embedding generation (BGE-M3, 1024-dim) per chunk
   │
   ▼
LLM metadata pass (title, author, date, type, topics, summary)
   │
   ▼
Stored in pgvector + Postgres full-text index → status: indexed
```

A document is searchable the moment it finishes this pipeline —
typically seconds for a normal document. Duplicate content (by hash) is
detected and skipped rather than re-ingested, both for manual upload
and the automated connectors.

### 4.2 Ingestion connectors (three ways in, one pipeline)

All three connectors converge on the exact same ingestion function, so a
file behaves identically no matter how it arrived — same hashing, same
storage path, same Celery hand-off:

- **Manual upload** — drag-and-drop in the UI, or the bulk-upload endpoint.
- **Watched folder** — a poller checks a designated directory every 5
  seconds. A file is only ingested once its size **and** modification
  time have been stable for 10+ seconds, so a file that's still being
  copied into the folder is never read mid-write and ingested truncated.
  Handled files move to `processed/` or `failed/` subfolders.
- **SFTP** — same pattern, polling a dedicated SFTP server every 8
  seconds, over an encrypted connection. Handles slow/interrupted network
  transfers with the same stability guard as the watched folder.

*(A fourth channel, email-in, is designed on the same pattern but not
yet built — see §9.)*

### 4.3 Retrieval (question → cited answer)

```
User query
   │
   ▼
Redis cache check ──(hit)──► return cached result in <10ms
   │ (miss)
   ▼
Query expanded into English / Hindi / Marathi (LLM), trilingual embeddings generated
   │
   ▼
Hybrid search: pgvector cosine similarity  +  PostgreSQL full-text search
   │                    (run per language)
   ▼
Reciprocal Rank Fusion merges both result sets → top ~20 candidates
   │
   ▼
Cross-encoder reranking (Cohere) scores relevance → drop anything below threshold
   │
   ▼
If nothing survives: HyDE fallback — LLM generates a hypothetical answer,
embeds THAT, re-searches. (Catches cases where user phrasing doesn't
literally match document wording.)
   │
   ▼
Top matches → LLM generates a natural-language answer, grounded ONLY in
retrieved excerpts, in the query's detected language
   │
   ▼
Response includes: AI summary, per-result snippets, page numbers,
presigned download links, and a search_mode field (vector / keyword /
vector+keyword / HyDE / failed_all) so the UI can show HOW the answer
was found
```

The system reports which retrieval mode produced a result — this is a
deliberate transparency feature, not just internal logging.

### 4.4 Multi-tenant security

- **Row-Level Security (RLS)** enforced at the PostgreSQL level, not just
  in application code — every query is scoped by `tenant_id` via a
  session-level Postgres policy (`SET LOCAL app.current_tenant_id`),
  across every tenant-owned table.
- **Object storage isolation** — S3/MinIO keys are prefixed by
  `{tenant_id}/...`, and presigned URLs are only issued after verifying
  the requesting user's tenant owns the document.
- **JWT dual-token auth** — short-lived access tokens + longer refresh
  tokens, bcrypt-hashed passwords.
- Verified live during testing: a second tenant, freshly registered,
  gets **zero results** searching for the first tenant's content — RLS
  isolation holds at the database layer, not just at the API surface.

---

## 5. What Makes the Search Meaningfully Different From Keyword Search

- **Hybrid, not either/or** — combines vector similarity (meaning) with
  full-text search (exact terms), merged via Reciprocal Rank Fusion, so
  it catches both "find the pothole complaint" (semantic) and exact
  identifiers, names, or reference numbers (lexical).
- **Reranking** — a second-pass model re-scores candidates for actual
  relevance to the query, filtering out plausible-looking-but-wrong
  vector matches before they reach the user or the LLM.
- **Grounded generation** — the AI summary is instructed to answer only
  from retrieved excerpts, and the pipeline tracks whether the answer
  was actually grounded in real content or not.
- **Trilingual by default** — every query is expanded into English,
  Hindi, and Marathi and searched across all three, so a Hindi query
  can retrieve an English-only document and vice versa. Verified live:
  a Hindi query against an English-only complaint document returned the
  correct result via the trilingual pipeline.
- **HyDE fallback** — when direct retrieval comes up empty, the system
  doesn't just say "no results." It generates a hypothetical ideal
  answer, embeds that, and searches again — meaningfully improving
  recall for oddly-phrased or abstract questions.

---

## 6. Comparison — Traditional/Local Document Management vs. This System

| | **Traditional / Local DMS** (shared drives, folder trees, SharePoint-style systems) | **This System** |
|---|---|---|
| **Finding a document** | Browse folder hierarchy, or search by filename/exact keyword match | Ask a question in plain language; get a synthesized answer with citations |
| **Search across languages** | Single language, exact-string matching | Trilingual query expansion (EN/HI/MR) out of the box |
| **What you get back** | A list of file links | An AI-generated answer, grounded in retrieved excerpts, plus the source documents |
| **Understanding "why" a result matched** | Opaque — usually just filename/date match | System reports search mode (vector/keyword/hybrid/HyDE) and a relevance score per result |
| **Ingestion** | Manual upload only, or a single fixed connector | Multiple channels (manual, watched folder, SFTP) converging on one consistent pipeline, with dedup and stability guards |
| **Multi-organization use** | Usually one deployment per organization, or shared with only folder-level permissions | True multi-tenant with database-enforced row-level isolation |
| **AI vendor dependency** | N/A (typically no AI) or hard-wired to one vendor | Provider-abstracted — swap LLM/embedding/rerank/OCR vendor via config, no code change |
| **Scaling search quality** | Search quality is fixed by whatever indexing the platform ships with | Layered pipeline (cache → hybrid retrieval → rerank → grounded generation) — each layer independently tunable/upgradable |
| **Cost model** | License/seat-based, or self-hosted with fixed infra cost regardless of usage | Infra cost + metered AI cost that scales with actual usage; documented cost-optimization built into the pipeline (caching, candidate filtering before LLM calls) |

The core distinction: traditional systems store and retrieve; this
system stores, understands, and answers.

---

## 7. Cost Model (indicative, AWS-hosted)

| Tier | Volume | Estimated monthly cost |
|---|---|---|
| Startup | 10k documents, 5k searches/mo | ~$45–80 |
| Growth | 100k documents, 50k searches/mo | ~$250–450 |
| Scale | 1M documents, 500k searches/mo | ~$1,200–2,100 |

Cost-control mechanisms built into the architecture:
- Redis caching avoids repeat AI calls entirely for repeated/identical
  queries (35–50% of typical query volume).
- Reranking narrows ~20 candidates down to the few actually sent to the
  generative LLM, cutting prompt token cost substantially.
- Every AI role can be pointed at a cheaper or self-hosted model without
  touching code — a direct lever on ongoing cost, not just a one-time
  infra decision.

*(Full breakdown in `docs/COSTING_AND_ESTIMATION.md`.)*

---

## 8. What Was Actually Verified (not just designed)

Everything below was tested end-to-end against a running instance
before this document was written — not assumed from the code:

- Login, sign-up, manual single/bulk upload, hybrid search with AI
  summary generation, chat sessions, folder creation, admin analytics.
- RLS tenant isolation (second tenant genuinely returns zero results
  for the first tenant's documents).
- Watched-folder and SFTP ingestion, including slow/interrupted-transfer
  handling and concurrent multi-file drops, verified to reach fully
  searchable state, not just "uploaded."
- Trilingual search (Hindi query retrieving an English document).
- Provider resilience in practice: mid-testing, the configured Groq
  model was discovered to have been deprecated upstream, silently
  breaking AI summary generation. Because the LLM is provider-abstracted
  config rather than hardwired, the fix was a one-line `.env` change —
  no code, no redeploy of application logic. This is the provider
  abstraction paying for itself in a real, not hypothetical, incident.

Several schema-level defects were also found and fixed during this
verification pass (mismatches between the database migrations and the
application's data models — the kind of gap that only surfaces when a
feature is actually exercised, not just read). Login, sign-up, manual
upload, folder creation, and chat were all affected before the fixes;
all are now confirmed working.

---

## 9. Known Limitations — Stated Honestly

This is presented as a working platform at MVP/demo maturity, not a
finished enterprise product. Being direct about the gap is deliberate —
it's more credible than pretending everything is finished, and it
frames the roadmap conversation.

**Built and solid:**
- Ingestion (multi-format parsing, 3 channels), hybrid search & RAG
  answering, multi-tenant RLS security, provider abstraction, basic
  admin analytics.
- **Entity/knowledge graph** — tiered entity linking across documents
  (people, properties, organizations) via a Postgres property-graph
  (`entity_dg_nodes` / `entity_dg_edges`, plain tables, not a graph
  DB), with edge confirmation/reversal and a 360-degree entity view.
  Built as part of Phase W3 (`backend/app/services/entity_graph_service.py`,
  `entity_360_service.py`, `backend/app/api/v1/entities.py`).

**Not yet built (roadmap items, not secrets — worth naming proactively):**
- **Human verification workbench** — there's no review/approval UI for
  AI-extracted data before it's treated as "confirmed." Everything the
  AI extracts today is presented as-is.
- **Record versioning** — no amendment/correction history model for
  records that change over time.
- **Governance & audit chain** — an audit log exists, but it is not yet
  tamper-evident (no hash-chaining), and only covers a subset of actions
  (auth, search, chat). Document view/edit/delete are not yet logged.
- **Full RBAC** — currently two roles (admin, user); a `permissions`
  table exists in the schema but has no code path wired to it yet.
- **Air-gapped / fully local deployment** — the current stack calls
  external AI APIs (Groq, Cohere) for LLM and reranking; local
  embeddings run today, but a fully offline profile (local LLM + OCR)
  isn't built yet.
- **Complex "hostile" document layouts** — page-spanning table joins,
  handwritten content, and heavily degraded scans are not specially
  handled; standard OCR/parsing applies.
- **Email-in ingestion channel** — designed on the same pattern as the
  watched-folder/SFTP connectors, not yet built.
- **JWT token lifetime** is currently longer than ideal for a
  production security posture (a known, flagged item — not yet
  hardened back down for this demo build).

None of these block a compelling demo of the working core — search
quality, multi-tenant isolation, and multi-channel ingestion are all
real and tested. They're listed here so nobody promises something that
isn't there yet.

---

## 10. Deployment

The entire stack runs via a single `docker-compose.yml` — one command
(`docker compose up`) brings up all 8 services with health-checked
startup ordering. Default seeded credentials, environment-driven AI
provider configuration, and a documented setup guide exist for
onboarding a new environment quickly (`docs/SETUP_GUIDE.md`).
