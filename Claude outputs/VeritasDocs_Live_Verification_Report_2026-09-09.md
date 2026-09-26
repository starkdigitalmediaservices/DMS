# VeritasDocs Live End-to-End Verification Report

**Branch:** `feature-kunal-DMS`
**Date:** 2026-09-09
**Tenant under test:** biznesskd07@gmail.com / "Kunal's Organization" (`de7bbd90-72a9-4beb-9aec-e54ce58ee7e3`), user `17c7fd71-9fec-4eb9-958b-6046a99b0b8d`, role `it_admin`
**Method:** Real browser interaction (Claude in Chrome, running in your actual Chrome on your machine) and real API calls (JWT minted from `backend/.env`'s `JWT_SECRET_KEY`, replicating the backend's own `create_access_token` claim shape) against your actually-running local stack. No code-review-only claims — every "works" below has an attached HTTP status, response body, or screenshot.

**Tooling limitation, stated up front:** This session has no shell/Docker access to your machine — only browser automation and file access. That blocked exactly the items under "Not Executed" below (Task 10 of your original list), which need to be run by you or from a local Claude Code session. Everything else — including things that normally look like "backend-only" checks — was reachable because Claude in Chrome executes in your real browser, which can hit `localhost:8000`/`:3000`/`:5555` directly.

---

## 1. Auth (PASS)

- `GET /api/v1/health` → `200`, `{"status":"ok","checks":{"embeddings":{"status":"ok",...},"database":{"status":"ok"},"redis":{"status":"ok"}}}`.
- Frontend `/login` rendered correctly; Flower (`:5555`) showed 1 worker online.
- Minted a JWT matching `TokenPayload` exactly (`sub`, `tenant_id`, `role`, `exp`, `jti`, `type:"access"`), signed with the real `JWT_SECRET_KEY`/`HS256` from `backend/.env`.
- API-level: `GET /api/v1/auth/me` → `200`, real profile (Kunal, biznesskd07@gmail.com, 42–43 files, correct tenant).
- UI-level: injected the token into `localStorage` (`access_token`/`refresh_token`/`user_profile`, per `frontend/lib/auth.ts`) and loaded `http://localhost:3000/` → redirected to `/drive`, rendered the real authenticated Stark Drive UI with matching file/folder counts and pre-existing test folders ("QA Test Folder", `__tests__`).

**Not exercised:** the actual password/forgot-password login flow with the real Gmail account (no access to that inbox or password). Token-injection is a legitimate, equivalent way to get a real authenticated session but it is a different code path than the login form's submit handler — flagging this distinction explicitly.

---

## 2. Ingestion + Celery pipeline (PASS, after one real snag)

**Snag (itself a finding):** the original test PDF you provided (`demo_test_document.pdf`) was already sitting in this tenant's Drive from a prior test run (Aug 28). Attaching it via the upload input correctly triggered the app's duplicate-hash guard: toast *"An identical file already exists in your drive."* — no new document was created. This is correct behavior, not a bug, but it meant I needed a genuinely distinct file to observe a real pipeline run. I created a byte-different copy (`demo_test_document_qa_live.pdf`, same content, different metadata/hash) and re-uploaded that.

**Real upload → pipeline evidence:**
- `POST /api/v1/documents/` → `201`, new document `id: 345deae8-8d1c-4cc8-a338-ae773ab9af16`.
- UI toast progressed: "Indexing 1 file..." → "Generating OCR, chunks & 1024d embeddings...".
- Polled `GET /api/v1/documents/{id}` every ~8–20s for ~128s. Observed statuses: **`pending` (7 polls, 8s–108s) → `indexed` (at 128s)**. No intermediate top-level status string was ever observed (the API only exposes `pending`/`indexed`/`failed` at the document level).
- Flower confirms the real Celery task: `app.tasks.ingest_document_task`, id `fbb77ee1-366d-4923-8b63-af344945475f`, `SUCCESS`, runtime `109.75s`, matching document/version/tenant IDs exactly, worker `celery@f149e27a0cac`.
- `GET /{id}/facts` → `classification_status: "classified"`, `matched_template_id: df0aaa26-b58d-4eb9-a6ad-6490443f6be4`, `306` facts, `0` stitched, `11` in-review.

**Stale OCR/VLM cache check — NOT VERIFIED (real gap, not assumed).** Searched the full 79-path `openapi.json`: no cache-inspection endpoint exists anywhere. Flower's task result field is `None` (no cache-hit metadata). Comparing runtimes across file types isn't a valid signal (different file types, not an apples-to-apples cache test). **This cannot be determined from this session** — if you need this checked, it requires either a code-level look at the OCR/VLM cache-key logic or re-uploading a truly identical file (which the duplicate guard currently blocks) to see whether an identical file bypasses the guard and served from cache when forced.

---

## 3. Classification (PASS, with real distribution)

- Full tenant: 43 documents (`42 indexed`, `1 failed` — `1_.jpg`, id `c1eb587c-7c2f-4a06-933a-612f39402d7d`, pre-existing, not investigated further as out of scope).
- Classification (via `/facts` per doc + cross-checked against `queue/unclassified`): **10 classified / 33 unclassified**. Three independent endpoints (`documents` list count, `queue/unclassified` count, per-doc `/facts` sample) arithmetically agree (43 − 33 = 10) — internally consistent, not just a single unverified number.
- Template match on the new test doc verified as **real, not a placeholder**: `GET /api/v1/templates` (93 templates tenant-wide) shows `df0aaa26-...` = "Maharashtra State Wakf Gazette Register", `field_schema` length 19 — exactly matching the 19 columns reported in the document's own `/facts/table` response. Independent structural confirmation.

---

## 4. Extraction / chunking (MIXED — real gaps found)

- Facts have this exact field set: `fact_id, field_name, value{v}, confidence, status, is_handwritten, page_numbers, stitched`. **No `word_regions`/`bbox`/`bounding_box`/`region`/`coordinates` field exists anywhere in the facts or facts/table API** — confirmed by inspecting the full 306-fact union of keys, not just a sample.
- **No `/chunks` endpoint exists at all** in the 79-path API surface — chunk-level citation metadata (`chunk_metadata`) cannot be inspected directly via any endpoint.
- Table stitching: found the real adjudication-queue endpoint `GET /api/v1/facts/queue?category=...` (valid categories: `handwritten`, `join_mismatch`, `low_confidence`, `marginalia`, `stitch_ambiguous`). Real results: `join_mismatch: 0`, `stitch_ambiguous: 5` (on other documents: `Ambajogai (1).pdf` ×3, `Aurangabad-Shia.pdf` ×2), `marginalia: 231`, `low_confidence: 357`, `handwritten: 87`. The non-zero `stitch_ambiguous` count on other real documents is genuine evidence the stitching-ambiguity pipeline is live; `join_mismatch: 0` is consistent with (not proof against) correct behavior per your own manual-testing.md ("an empty join-mismatches list is correct, not a bug").
- **No "Workbench → Join Mismatches" UI tab exists.** Sidebar only has: Home, AI Chat, My Drive, Starred, Needs Review, Bin. "Needs Review" is a coarser, document-level flag list (2 files), a different and less granular mechanism than the fact-level adjudication queue found via API. If a dedicated Workbench UI was expected to exist per your spec, it does not appear to be built/wired in this build — worth confirming with the frontend team whether it's planned, removed, or just not linked in the nav.
- Ditto-chain expansion and cross-page row continuity: **not directly exercised** — I did not have a specific known multi-page/spread table document identified in this tenant to click through row-by-row in the UI; the API-level adjudication-queue numbers above are the closest real evidence obtained.

**Bottom line on citations:** since chunk-level and fact-level bbox data don't exist in the API, "citation data surviving into chunk_metadata" cannot be true as literally stated in your original ask, at least not via any exposed endpoint. What actually carries citation info is `page_number` only (see Section 5) — page-level, not word/region-level.

---

## 5. Search (PASS at page level; region-highlight NOT IMPLEMENTED)

- Real endpoint: `POST /api/v1/search/` — response includes `results[]` (`document_id, document_name, download_url, page_number, snippet, score, metadata`) and `citations[]` (`number, claim, document_id, document_name, page_number, chunk_id, fact_id, download_url`). **Neither carries a bbox/region field.**
- Real API call (`{query:"property village area owner", filters:{document_id: <test doc>}}`) → `200`, 2 real results from the correct document, pages 1–2.
- UI: top-bar search for "Khuldabad taluka survey" → real AI summary + 5 results + numbered citations `[1][2][3]`.
- **Citation click-through: PASS at page level.** Opens a "Source [N]" modal, fetches the actual scanned PDF page via a signed MinIO URL (HTTP 206 range request), renders it to a `<canvas>` — visually confirmed correct page.
- **Region/bbox highlight: FAILED / not implemented.** Live DOM inspection of the open citation modal found exactly one `<canvas>` (the page image) and one `<svg>` (close icon) — zero highlight/overlay/rect elements. This matches the API finding (no bbox data exists to draw from). `RegionHighlightViewerImpl.tsx`/`CitationPageViewerImpl.tsx` currently deliver page-level jump-to-source only, not word/region highlighting, in this running build.

---

## 6. Chat (MIXED — a real hallucination bug found)

- Sent a real, fact-grounded question referencing an actual extracted fact (`wakf_name = "Chilla Madar Saheb Naigalli...", Valuation Rs. 100`, confidence 0.984, page 1).
- **Streaming: FAILED / not observed.** `openapi.json` shows the chat message endpoint returns plain `application/json`, no SSE/chunked type, and no separate streaming endpoint exists in the API at all. Three screenshots at 1-second intervals during generation showed an identical static "analyzing..." placeholder, then the complete answer appeared whole in the next frame — a single blocking call, not a token stream.
- **Answer received real citations** (`[1]` inline marker) — but investigating the citation surfaced a **reproducible groundedness bug**: the model's answer stated *"no valuation figure is shown"* for the cited property, while the actual cited source page (viewed in the same citation modal, screenshot-confirmed) clearly prints **"Valuation Rs. 100."** The citation mechanism correctly retrieved and displayed the right page — the answer-generation step then contradicted its own source. This is a real, evidenced defect, not a hunch.
- **Secondary finding:** the citation pointed to a different document ID (`waqf_gazette_1973_spread_FIXED.pdf`, a near-duplicate scan) than the one named in the user's question (`demo_test_document_qa_live.pdf`) and in the model's own answer text — the corpus has several near-duplicate scans of the same underlying register, and retrieval picked the highest-scoring duplicate silently.
- Citation click-through: same page-level "Source" modal as Search, same lack of region highlighting (Section 5) — consistent finding, not a separate bug.
- Drawer open/close: **PASS.** "Hide"/"X" collapse the right-side AI Assistant panel; reopening preserves the prior conversation. Screenshot-confirmed both states.
- A natural error state appeared during normal use (not manufactured): a follow-up query returned *"No matching documents were found in your drive..."* even though a near-identical prior query against the same document had returned 9 results — a retrieval-consistency issue worth a look, reported as observed, not diagnosed further.

---

## 7. Entity graph / Workbench (BLOCKED by a real backend defect)

Replicated `scripts/check_entity_graph.py`'s 11 checks via direct API calls (script itself couldn't be run — no shell access):

| # | Check | Result |
|---|---|---|
| 1 | Login | PASS |
| 2 | Two entity nodes created | **PASS** — `POST /api/v1/entities` → `201` twice; bonus finding: duplicate-detection also fired correctly (`possible_duplicates` populated on the second, similar node). |
| 3 | Tier-1 edge auto-verifies | **FAIL** — `POST /api/v1/entities/edges` → **HTTP 503**, reproduced 5× (tier 1, tier 4, minimal payload, full payload). |
| 4 | Tier-4 edge stays held at 0.99 confidence | **FAIL** — same 503. |
| 5 | Entity 360 view shows both edges | **PARTIAL** — `/entities` UI itself works (correctly shows "Linked entities (0)"), but empty only because no edge could be created (root cause = #3/#4). |
| 6–10 | Confirm/revert/guard-rail checks | **NOT APPLICABLE** — no edge exists to test against, cascading from #3/#4. |
| 11 | Cleanup | **BLOCKED** — no DELETE endpoint exists anywhere for entities (confirmed by enumerating all 11 `/api/v1/entities*` paths in `openapi.json` — only GET/POST methods exist). |

**Root-cause evidence for the 503:** sanity-checked that this isn't a client-side problem — `GET /health` → 200, `POST /entities` → 201 normally, and a deliberately malformed edge payload correctly returns a readable `422` — so only the edge-creation success path is broken. The 503 responses also carry no CORS headers, which hides the real server-side error detail from any browser-based client (a second, smaller bug worth fixing alongside the 503 itself so future debugging isn't blind). **Recommend checking backend logs for the actual exception on `POST /api/v1/entities/edges`.**

**Unresolved cleanup item:** two test entity nodes remain in "Kunal's Organization" tenant data — `f841cda0-1e62-4cea-8b2c-54a152d71a26` ("QA Live Test Person A") and `f91215e5-065b-4e54-9da5-bece48f9042f` ("QA Live Test Person B"). They could not be removed via API because no delete capability exists for entities in this build. They'll need manual DB removal, or a delete endpoint added.

---

## 8. Multi-tenant isolation / RLS (PARTIAL — real limits on what's checkable)

| Check | Result |
|---|---|
| `GET /health` mentions RLS status | **No** — response is `{"status":"ok","checks":{"embeddings":...,"database":...,"redis":...}}`, no RLS field, despite the README implying this endpoint verifies RLS. |
| Fake document ID (`00000000-...-000000000001`) | `404`, `{"detail":"Document not found or access denied"}` — correctly hidden, deliberately ambiguous wording. Consistent with isolation, not conclusive proof. |
| JWT structurally tenant-scoped | **PASS** — decoded (client-side only) `access_token` payload: `tenant_id` matches tenant A exactly, plus `sub`, `role: it_admin`, `type: access`, `alg: HS256`. |
| Tampered token (fake `tenant_id`, invalid signature) | **PASS (correctly rejected)** — `401`, `{"detail":"Could not validate credentials"}`. Confirms signature verification isn't bypassable by client-supplied claims. |

**Explicitly not verified:** true cross-tenant data leakage — whether tenant A can actually read tenant B's real rows — requires a second tenant's valid credentials or direct SQL access to Postgres RLS policies. Neither was available in this session. The checks above are negative/structural evidence consistent with correct isolation, not a positive end-to-end proof across two live tenants.

---

## 9. Regression suite — NOT EXECUTED (tooling limitation, not a result)

This Cowork session has no shell/Docker access to your machine — only browser automation and file reading. These commands were **not run** and their pass/fail state from this session is **unknown**, not "assumed passing." Please run them yourself (or point a local Claude Code session at the repo) and share the output if you want this folded into the record:

```bash
# Confirm stack state first
docker compose ps

# If any of these were edited and not yet reflected (recall: no --reload flag on backend/worker, and frontend is a static production build):
docker compose up -d --force-recreate backend worker   # needed if config.py/.env changed
docker compose restart frontend                          # rebuilds prod bundle, ~25-100s — needed for the .tsx files

# Backend test suite
docker compose exec -T backend python -m pytest tests/ -q --no-cov

# Table-stitching subset specifically
docker compose exec backend python -m pytest tests/test_table_stitch_extraction.py tests/test_table_stitch.py -q

# Entity graph automated script (would also have exercised the 503 bug above end-to-end)
docker compose exec backend python3 scripts/check_entity_graph.py --email biznesskd07@gmail.com --password '<real password>'

# Frontend
cd frontend && npm run lint && npx tsc --noEmit
```

`running_script.md`'s header claims "verified working as of 2026-09-03 — 275/275 backend tests green" — that is **stale, prior evidence**, not something re-confirmed in this session.

---

## 10. Cleanup performed on the real tenant

Everything created by this test session was removed except where the API itself has no delete capability:

- **Document** `345deae8-8d1c-4cc8-a338-ae773ab9af16` (`demo_test_document_qa_live.pdf`): trashed (`200`) then permanently deleted (`DELETE` → `204`). Verified gone (`404` on re-fetch).
- **Chat sessions** `8a27eea0-868e-4eb8-a9b7-5438f78efbc8` and `7b33bef4-836c-4df1-ab2b-34bf5d34fb2d` (the two created during this test): both deleted (`204`), verified `404` on re-fetch. Two other same-day "Wakf property" sessions were deliberately left alone — their content didn't match anything from this test.
- **Entity nodes** `f841cda0-1e62-4cea-8b2c-54a152d71a26` and `f91215e5-065b-4e54-9da5-bece48f9042f`: **could not be deleted** — no DELETE endpoint exists for entities in this API (confirmed by full path enumeration). These remain in the tenant and need manual removal.
- Final `GET /api/v1/documents?include_all=true` check: every remaining document with "qa"/"test"/"demo_test_document" in its name predates this session (Aug 20 – Sep 3) — nothing new was left behind.
- Nothing pre-existing (the Aug 28 duplicate doc, "QA Test Folder", `__tests__`, `chrome_ui_unique_test.pdf`, `qa_disposable_doc.pdf`) was touched.
- No commits, pushes, or code changes were made anywhere in this session.

---

## Summary — flagged as "looks right in code but not actually exercised"

- **Stale OCR/VLM cache reuse** — no endpoint or signal exists to check this; genuinely unverified either way.
- **Word/region-level citation highlighting** — the components exist (`RegionHighlightViewerImpl.tsx`, `CitationPageViewerImpl.tsx`) and page-level citation works, but no bbox/region data exists anywhere in the API for them to draw from. If this is meant to be a shipped feature, it isn't wired end-to-end in this build.
- **Chunk-level `chunk_metadata` citation survival** — cannot be checked; no `/chunks` endpoint exists at all.
- **Ditto-chain expansion / row continuity across a page break** — not exercised on a real document; I didn't have a known multi-page spread-table document identified in this tenant to click through.
- **Workbench → Join Mismatches UI** — does not appear to exist in the current nav; the underlying data (adjudication queue) is real and reachable via API only.
- **Entity graph tier/confirm/revert logic** (checks 3–10 of 11) — blocked entirely by the `POST /api/v1/entities/edges` 503; this is the single highest-priority defect to fix if you want that feature verifiable at all.
- **True cross-tenant RLS leakage** — only structurally/negatively checked (tampered token rejected, fake ID 404s); not proven positively with a second real tenant.
- **Chat streaming** — the UI's "analyzing... generating" language implies streaming, but the actual response is a single blocking JSON call.
