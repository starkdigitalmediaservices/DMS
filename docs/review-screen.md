# Review screen — hand-over note

Human verification of extracted data: the scanned page on one side, the
extracted content on the other, every correction reversible and audited.
Built 2026-09-24 on `feature-kunal-DMS` (commits `0ebaa13`..`1e14ddf`).

## What it is
- **Workbench** (`/workbench`) has two tabs:
  - **Items to check**: values the computer was unsure about. *Show on page* opens a focused view of that one item (card + outline on the scan).
  - **Whole documents**: every scanned PDF/image, with progress. Opens the full document view (`/workbench?doc=<id>`).
- **Document view**: scan (zoom, pan, pages, numbered boxes) + extracted blocks (tables, headings, paragraphs, pictures). Click selects and highlights on the scan; **double-click edits**. Undo per change, *Undo all changes* (admin), add/remove rows and text, mark rows checked, change history.
- **Entity 360**: each link, fact and *Appears in* document has *Show on page*, which opens a **read-only evidence viewer** (only the page(s), value outlined, Esc closes).
- **Drive preview**: clicking a value in *Extracted Facts* shows it outlined on the page (*Back to PDF* returns).

## How it works (key rules)
- **Facts are the single source of truth.** Table cells backed by a Fact store only `{fact_id}` in the review state; edits go through `bulk_edit_facts` (edit → `in_review`, never `verified`).
- **Immutable original**: `doc_dg_review_originals` (UPDATE blocked by trigger). Working copy: `doc_dg_review_states`. History: `doc_dg_review_audit` (append-only; each row's id + hash is also written to the hash-chained `audit_dg_logs`).
- **Concurrency**: every write sends `If-Match: <review version>`; fact cells also send the fact's `edit_version` (shared with the Workbench). Stale → 409 "please reload".
- **Undo** of a cell is refused if the fact was changed elsewhere since.
- **Roles**: records_officer edit + verify · operator edit only · it_admin also *Undo all* · auditor / legal_counsel / department_head read-only. Department scope (RLS, migration 0053) applies to every endpoint, including page images.
- **Blocks** (`review_blocks.py`): per page, best source wins — Chandra layout → word boxes (Tesseract/Paddle/native PDF) → text only. Documents with template Facts use the facts table instead.
- **Search**: one `review_edit` chunk per corrected block (updated in place, removed on undo); original OCR chunks untouched, so the old reading stays findable. Embeddings are filled by the worker after commit; the tenant search cache is cleared on changes.

## Main files
- Backend: `app/services/review_service.py`, `review_blocks.py`, `review_search.py`, `app/api/v1/review.py`, `app/models/review.py`, `entity_360_service.py`, `app/ocr/extractor.py` (keeps Chandra layout), `app/tasks/worker.py` (`embed_review_chunks_task`).
- Frontend: `components/review/*` (ReviewScreen, ScanViewer, BlockCard, RowDetailPanel, QueueItemCard), `components/workbench/*`, `components/entities/EvidenceViewer.tsx`, `app/workbench/page.tsx`, `app/entities/page.tsx`, `components/drive/DocumentPreviewModal.tsx`, `lib/fieldLabels.ts`.
- The Devanagari font is bundled in `frontend/app/fonts` (the build no longer needs internet).

## Migration and setup
1. `docker compose exec -T backend alembic upgrade head` — adds `0055_review_screen` (3 tables + `doc_dg_facts.edit_version`).
2. Restart backend **and** worker (`docker restart dms-backend-1 dms-worker-1`); rebuild frontend (`docker restart dms-frontend-1`).
3. Backfill blocks per tenant (dry run first, then without `--dry-run`):
   `docker compose exec -T backend sh -c "PYTHONPATH=/app python3 /app/scripts/backfill_review_blocks.py --tenant <tenant-id> --dry-run"`
   Idempotent; never touches a document someone has edited. `--rebuild-untouched` replaces snapshots built by an older builder.

## Tests
- Backend: `docker compose exec -T backend python -m pytest tests/test_review_*.py tests/test_entity_360_service.py -q --no-cov`
- Browser (real backend + data): `E2E_REVIEW_DOC_ID=<doc> E2E_ENTITY_ID=<entity> E2E_ACCESS_TOKEN=<token> npm run test:e2e` (in `frontend/`). The review tests edit a real value and always undo it.

## Status
- Done: review screen, Workbench integration, block builders, search sync, Entity 360 evidence, Drive click-to-source.
- Backfill: run on the `biznesskd07@gmail.com` tenant only (46 documents). Other tenants: not run.
- Export: built, then **removed on request** (revert `98fdd6b`).

## Known limits / assumptions
- Only documents OCR'd with Chandra **after** 2026-09-24 have a saved layout; older Chandra documents show text only until re-OCR'd.
- Re-uploading a new version of a document doesn't rebuild its review snapshot yet.
- The largest register (68 pages, ~6,300 values) takes ~2–5 s to open.
- New Marathi wording (`check.*` keys in `lib/i18n.tsx`) is a draft — needs native review.
- Automatic entities include junk ("Do.", names split by hyphens, fuzzy mis-matches); needs a clean-up pass.
- Open decisions: (1) deleting a fact-backed row only hides it in review; (2) "checked" is a row mark because machine values keep their label; (3) operators can "Correct" in the list but not mark rows checked.
- Known test flake: a browser test can time out when all e2e tests run back to back on a busy machine; passes when re-run.
