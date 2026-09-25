# VeritasDocs v1 — Task Status Report
**Against code at branch `dev` · reviewed 01-Sep-2026**

---

## Legend
- ✅ **Complete** — implemented, enforced, and tested
- 🟡 **Partial** — exists in code but has gaps, missing tests, or known caveats
- ❌ **Pending** — not started or only a stub/placeholder

---

## 0 · Foundations & Engineering Standards

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T01 | Rename tables/FKs/indexes to `{module}_dg_*` standard | ✅ Complete | Migration `0008_dg_naming_standard.py` exists; tables use `iam_dg_*`, `doc_dg_*`, `entity_dg_*`, `audit_dg_*`, `record_dg_*`, `sys_dg_*` throughout |
| T02 | Create `sys_dg_config` with typed, cached accessor | ✅ Complete | `models/sys_config.py`, `config_service.py` with 60s TTL cache and `get_int`/`get_float` helpers; seeded by migration `0009` |
| T03 | Move hardcoded thresholds into config | 🟡 Partial | `config_service.py` accessor exists; many thresholds (`relevance_threshold`, `rrf_k`, `chunk_size`, `embed_batch`, `trigram_threshold`) are seeded in migrations 0009, 0028, 0030. Some values still fall back to hard-coded defaults in calling code (e.g. `get_float("entity_dedup_similarity_threshold", 0.45)`) — the fallback path means the table isn't strictly required, which is the right pattern, but not every threshold from the backlog list is confirmed seeded |
| T04 | Add provenance columns — page number, region coordinates, non-null at write time | ✅ Complete | `models/fact_region.py` with `x0/y0/x1/y1` (0–1 fractions); DB trigger in migration `0010` enforces non-null regions at write time; `Fact.regions` is a list relationship (multi-region support) |
| T05 | Stop discarding word boxes in the chunker | 🟡 Partial | `page.py` model stores `width`, `height`, `rotation`, `skew`; VLM extraction writes `FactRegion` per field. However the OCR extractor still discards word-level bounding boxes for the pdfplumber path — words are extracted but `bbox` per word is not persisted to `FactRegion` for non-VLM documents |
| T06 | Define coordinate contract (D-2) | ✅ Complete | `docs/decisions/T06_decision_region_format.md` is **signed 2026-08-24**. Origin top-left, y grows down, 0–1 normalised fractions, per-page rotation/skew, list of regions. Unblocks T04/T05/workbench |
| T07 | Audit events on every mutating document/folder path, plus view and download | 🟡 Partial | `audit_service.log_action()` is called from fact verification, entity graph, export, records, classification, governance. Coverage is wide but not exhaustive — e.g. folder moves, document trash/restore, and direct downloads are not verified to log in code |
| T08 | Require actor identity at service boundary; reject anonymous mutations | ✅ Complete | `log_action()` raises `ValueError` if `actor_id is None`; same guard in every service function that mutates (`confirm_fact`, `bulk_confirm_facts`, `create_node`, `add_amendment`, etc.) |
| T09 | D-2 — Technical Architecture Document | ✅ Complete | `docs/architecture/T09_technical_architecture_document.md` exists (5 KB) |
| T95 | Localisation — EN + MR, translations table, Devanagari fonts, language switcher | 🟡 Partial | `i18n_service.py` + `models/translation.py` + migration `0032_i18n_translations.py` exists. API route at `/i18n/{locale}`. `User.locale` field present. Frontend language switcher — not confirmed present in frontend components |
| T96 | Accessibility — GIGW 3.0 / WCAG 2.1 AA baseline audit | ❌ Pending | No accessibility audit or automated CI checks found in frontend or `.github` |
| T98 | Set CI coverage threshold at today's number and raise as tests land | 🟡 Partial | `pytest.ini` has `--cov-fail-under=56`. Threshold exists but 56% is already set; automatic raising as tests land is not automated |
| T99 | Fix 30-day JWT lifetime | ✅ Complete | `config.py`: `jwt_access_token_expire_minutes: int = 15`, `jwt_refresh_token_expire_days: int = 7`. D-3 signed 2026-08-25 |

---

## (a) Ingestion & Connector Framework

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T40 | Connector abstraction layer — one ingestion contract | 🟡 Partial | `connector_ingest_service.ingest_bytes()` is a shared entry point used by watched-folder, SFTP and email connectors. However the "abstraction" is a shared function call, not a formal interface/protocol — a new connector just calls `ingest_bytes()` directly, which works but is not a typed contract |
| T41 | Mandatory-on-ingest set: PDF/A-2b, provenance record, idempotent retry, failure alerting | 🟡 Partial | SHA-256 dedup check (`already_ingested`) is in all connectors; idempotent upload exists in `document_service.py`. **PDF/A-2b conversion is not implemented** — original file is stored but no PDF/A rendition is created. Failure alerting is logger-level only, no alerting integration |
| T42 | Watched folders and FTP/SFTP poller | ✅ Complete | `watched_folder_connector.py` (231 lines, recursive, stability grace, folder-path mirroring) and `sftp_connector.py` (210 lines, paramiko, stability guard) both exist |
| T43 | Email-in via dedicated mailbox | ✅ Complete | `email_connector.py` (IMAP polling) and `email_webhook.py` (inbound parse) exist |
| T44 | Scanner integration — TWAIN or network-scan folder drop | ❌ Pending | Not found in codebase. Backlog says "network-scan folder drop" — the watched folder connector could cover this if pointed at a scan-to-folder path, but no specific scanner integration or TWAIN code exists |
| T45 | Google Drive connector | ❌ Pending | No code found; gated on A8 |
| T46 | SharePoint connector | ❌ Pending | No code found; gated on A8 |
| T47 | NIC e-Office connector | ❌ Pending | No code found; gated on A7 |

---

## (b) Document Processing Pipeline

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T20 | Real per-field confidence (not hardcoded 0.9) | 🟡 Partial | VLM extraction (`vlm_extraction.py`) calls `classify_confidence()` from `template_service.py` which maps VLM-returned confidence strings to floats. D-5 (confidence calibration policy) is **signed** (`docs/decisions/D5_decision_confidence_calibration.md` exists). However the confidence band mapping is per-field in the template, which is the right place — but only VLM-path facts get real confidence; non-VLM (chunk-only) facts carry no confidence |
| T21 | Marathi and Devanagari OCR | ✅ Complete | `extractor.py` uses `TESSERACT_LANG = "eng+hin+mar"` and PaddleOCR with `lang="mr"`. Both paths are wired |
| T22 | VLM extraction path | ✅ Complete | `pipeline/vlm_extraction.py` (728 lines); Gemini and OpenRouter VLM providers wired; template-driven field extraction; all four handlers invoked; `FactRegion` written per field |
| T23 | Document classification stage + unclassified queue | ✅ Complete | `classification_service.py` with LLM-based template matching, `classify_document()`, `list_unclassified_documents()`, `manually_classify_document()`, `dismiss_document_classification()` |
| T24 | Template registry | ✅ Complete | `models/template.py`, `template_service.py`, `templates.py` API, migrations `0011` and `0031` (spread layout field added) |
| T25 | Seed templates — Basmath Form A (1974), Washim Form B (2004) | ❌ Pending | No seeded templates found in codebase (migrations or scripts). Blocked on A1 (no reference corpus). `vlm_extraction.py` itself notes "no seeded template exists yet (T25 stays blocked on A1)" |
| T26 | Handler 1 — multi-page spread join | 🟡 Partial | `handlers/spread_join.py` exists; wired in `vlm_extraction.py` for `template.layout == "spread"`. Code notes: "left/right field convention is a best-effort invention, not modeled on a real scanned spread" — no real-world validation yet. Mismatched spreads write `_join_mismatch` Facts correctly |
| T27 | Handler 2 — ditto-chain expansion | ✅ Complete | `handlers/ditto_chain.py` (4316 bytes) with per-column chain expansion, chain-break rules, inherited-value labelling. Integration tested in `test_ts5_ditto_integration.py` |
| T28 | Handler 3 — continuation-row merge | ✅ Complete | `handlers/continuation_merge.py`; blank-serial rows at page top merge backward; facts correctly get list of regions across two pages |
| T29 | Handler 4 — blob-cell parse | ✅ Complete | `handlers/blob_cell_parser.py`; survey/CTS numbers, area unit normalisation (sqm, sqft, ha, are, guntha); "Akker" flagged for human rather than guessed |
| T30 | Handwritten and degraded policy | ✅ Complete | `Fact.is_handwritten` column; `mark_fact_handwritten()` in verification service; `_marginalia` sentinel Facts written by VLM extraction; `bulk_confirm_facts` excludes `is_handwritten=True` rows unconditionally |
| T31 | Regression corpus seeded from Waqf ground truth | ❌ Pending | `docs/testing/T31_T32_regression_corpus_notes.md` exists (planning doc). No actual sample documents with human-verified ground truth in codebase. Blocked on A1 |
| T32 | Accuracy baseline report | ❌ Pending | Same as T31 — no ground-truth corpus, no accuracy numbers. Blocked on A1 and D-4 (accuracy tolerance not yet agreed) |
| T33 | Fix silent OCR failure (extraction_failed flag) | ✅ Complete | `extractor.py` lines 102, 139–142, 158: `failed = not text.strip()` correctly sets `extraction_failed=True` when OCR yields empty text. D-3 notes this was fixed in same commit as JWT fix |

---

## (c) Human Verification Workbench

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T51 | Two-lane model + per-row state machine (`machine → in_review → verified`) | ✅ Complete | `Fact.status` enum enforced at DB level; `fact_verification_service.py` with `confirm_fact()`, `claim_fact()`, `release_fact()`, `bulk_confirm_facts()`, `get_adjudication_queue()`. State transitions enforced at service layer, not just UI |
| T52 | Adjudication queues (marginalia, join mismatches, low-confidence, handwritten) with claim/release | ✅ Complete | `get_adjudication_queue()` with categories: `low_confidence`, `handwritten`, `marginalia`, `join_mismatch`, `stitch_ambiguous`. Claim/release locking present |
| T53 | Click-through from field to highlighted source region (viewer for rotated/skewed scans) | 🟡 Partial | **Backend is complete**: `fact_service.get_fact_with_regions()` returns regions with page rotation/skew, presigned document URL. **Frontend viewer not implemented** — the workbench page (`frontend/app/workbench/page.tsx`) shows the fact list and actions but has no side-by-side page image viewer with highlighted region. The "Eye" icon is imported but the scan-image panel with region overlay is absent |
| T54 | Operator productivity — keyboard-first navigation, batch accept above configurable threshold | ✅ Complete | Keyboard shortcuts in `workbench/page.tsx` (↑/↓, A/Enter=confirm, C=claim, R=release, H=handwritten); `bulk_confirm_facts()` with configurable threshold read from `sys_dg_config`; batch accept UI in workbench page |
| T55 | Hard-rule tests — no promotion without actor; no handwritten verified without confirmation | ✅ Complete | `confirm_fact()` raises `ValueError` if `actor_id is None`; `bulk_confirm_facts()` hard-excludes `is_handwritten=True`; only `in_review` facts can reach `verified`; tested in `test_fact_verification.py` (26 KB) |

---

## (d) Entity & Knowledge Layer

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T10 | Property-graph schema (`entity_dg_nodes`, `entity_dg_edges`) | ✅ Complete | `models/entity_node.py`, `models/entity_edge.py` with tier (1–4), status, confidence, creating actor/policy, evidence pointer; migrations `0014`, `0015`, `0016`, `0017`, `0040` |
| T56 | Tiered linking — tiers 1–2 auto-commit, tier 3 escrowed, tier 4 human-only | ✅ Complete | `entity_graph_service.py` with `AUTO_COMMIT_TIERS = {1,2}`, `ESCROW_TIERS = {3,4}`; `confirm_edge()`, tier-4 always held regardless of confidence |
| T57 | Bulk threshold confirmation with full audit trail | ✅ Complete | `bulk_confirm_edges()` records actor, threshold, corpus folder, policy version, batch ID on every affected edge and in audit log. Blocked by `is_corpus_calibrated()` check (T59) |
| T58 | Link reversibility with clean cascade; machine labels permanent | ✅ Complete | `revert_bulk_confirm()` uses batch_id to undo; machine label (`created_by_actor_id` vs `created_by_policy_version`) preserved forever |
| T59 | Per-corpus threshold calibration protocol before bulk acceptance | ✅ Complete | `corpus_calibration_service.py` with `calibrate_corpus()`, `is_corpus_calibrated()`; both `bulk_confirm_edges()` and `bulk_confirm_facts()` call calibration check |

---

## (e) Search & Q&A

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T70 | Uncited-answer refusal gate | ✅ Complete | `_generate_grounded_answer()` in `search_service.py`: drops claims with no valid source citation; returns `(None, [], False)` when excerpts don't support the query; hard rule, not a setting |
| T71 | Citation click-through to highlighted page region | 🟡 Partial | Backend: `Citation` schema carries `fact_id`, `chunk_id`, `page_number`, `document_id`. `fact_service.get_fact_with_regions()` returns the data needed. **Frontend click-through not implemented** — search results show citations by page number but no inline region highlight viewer |
| T72 | Add pg_trgm leg | ✅ Complete | Migration `0029_trigram_search_index.py` adds GIN trigram index. Migration `0030_trigram_threshold_config.py` seeds threshold in `sys_dg_config`. Search service has a trigram search leg (confirmed by `test_search_edge.py` tests) |
| T73 | Search over extracted structured records (not only chunk text) | ❌ Pending | No evidence in `search_service.py` of queries against `record_dg_records` or `doc_dg_facts` tables. Search is still chunk-text only |
| T74 | Immediate metadata-level findability on ingest | ✅ Complete | `_find_pending_title_matches()` in `search_service.py` queries documents at `status IN ('pending', 'processing')` by title, returns them with a "still processing" snippet |
| T75 | Devanagari indexing correctness | ✅ Complete | Migration `0013_devanagari_tsvector.py` adds `content_tsv_simple` generated column using `'simple'` config. Search queries both vectors |

---

## (f) Records & Versioning

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T60 | Amendment chains — base record, amendments, deterministically re-derivable current state | ✅ Complete | `models/record.py`, `models/record_amendment.py`, `records_service.py` with `create_record()`, `add_amendment()`, `get_original_state()`, `get_current_state()` (derived, never stored), `get_full_history()`. Tested in `test_records_service.py` |
| T61 | Legal-status layer — in force, set aside, under stay, superseded | ✅ Complete | `VALID_LEGAL_STATUSES` enforced; `list_records_by_legal_status()` and `get_legal_status_summary()` derive status from amendment chain using `DISTINCT ON` SQL — never a stored mutable value |
| T62 | Entity and Property 360 view | 🟡 Partial | `entity_360_service.py` (5 KB) exists with `get_entity_360_view()`. Backend returns linked entities, facts, and records. **Frontend 360 view page** at `frontend/app/entities/page.tsx` (19 KB) exists — but source click-through (T53's viewer) is absent from the entity view as well |

---

## (g) Reports & Analytics

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T76 | Completeness and reconciliation dashboard | ✅ Complete | `completeness_service.py` (220 lines) with missing fields, failed pages, unverified rows, machine-vs-verified split, confidence distribution, drill-through. `completeness/` frontend page exists |
| T77 | On-demand summary report generation, exportable, unverified data flagged | ✅ Complete | `report_service.py` (168 lines); builds deterministic narrative summary (no LLM); unverified data always tagged with `[UNVERIFIED — machine-suggested, not human-confirmed]`; audited; multi-format |

---

## (h) Governance & Audit

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T34 | Drop the dead `permissions` table or wire into T50 | ✅ Complete | Migration `0012_drop_dead_permissions_table.py` exists |
| T50 | RBAC for six personas | ✅ Complete | `UserRole` enum in `models/user.py`: `records_officer`, `operator`, `department_head`, `legal_counsel`, `it_admin`, `auditor`. `models/department.py` + `department_service.py` for department-scoped RBAC. Migration `0022_personas_departments.py` |
| T63 | Tamper-evident audit — append-only enforcement, hash chains, integrity checker | ✅ Complete | `audit_service.py` with sha-256 chained events per-tenant; advisory lock prevents chain forks; `verify_chain_integrity()` walks and recomputes; DB append-only enforcement via grants in migration `0020_audit_hash_chain.py`. Tested in `test_certificate.py` and `test_data_loss_audit.py` |
| T64 | WORM archival storage with retention lock | 🟡 Partial | `storage_service.py` has `archive_file_with_retention()` using S3 Object Lock `COMPLIANCE` mode. Config has `s3_archive_bucket_name`. **Not wired into the ingest pipeline** — no call site in `document_service.py` or the Celery worker actually invokes `archive_file_with_retention()` during upload |
| T65 | Section 63 certificate generation | 🟡 Partial | `certificate_service.py` generates a PDF with hash, algorithm, dual signature blocks. **Prominently marked DRAFT** — assumption A3 (legal counsel review) is still open. Certificate cannot be used as an evidentiary instrument until A3 closes |
| T66 | Retention policy engine per record class | 🟡 Partial | `models/retention_class.py` and migration `0024_retention_classes.py` define the class schema. `docs/decisions/D7_decision_retention_classes.md` is signed. **The purge engine itself is not built** — no background task actually acts on `retention_days` to purge records |
| T67 | Verified-layer boundary enforced at query layer | ✅ Complete | `export_service.py` and `report_service.py` use `gather_evidence_package()` with `mode="certificate"` to exclude unconfirmed edges/facts, or `mode="general_export"` to include them with `confirmation_status` label. `docs/decisions/D8_decision_escrowed_links_in_exports.md` signed. Enforced in the service layer, not per call site |

---

## (i) Data Operations

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T78 | Export — CSV, JSON, XLSX, PDF, PDF/A, audited | ✅ Complete | `export_service.py` (246 lines); all five formats; audited with content hash; `generate_export()` requires `actor_id`. API route at `export.py` |
| T79 | Duplicate detection — hash-based + fuzzy matching | 🟡 Partial | Hash-based exact dedup: `already_ingested()` is called by connectors. Fuzzy matching: `duplicate_service.find_fuzzy_duplicates()` exists using embedding cosine similarity. **Not integrated into the main upload path** — `document_service.upload_document()` does not call it; it's on-demand via API only |
| T80 | Bulk edit — preview, per-row audit, bounded undo, cannot mark verified | ✅ Complete | `bulk_edit_facts()` with `dry_run=True` preview path; per-row audit via `log_action`; `revert_bulk_edit_batch()` using audit log as source of truth; status always demoted to `in_review` on edit, never `verified`. Frontend in workbench page with checkbox selection, preview, apply, undo |

---

## (j) Admin & Deployment

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| T81 | Licensing enforcement — SaaS subscription metering, on-prem capacity licence | 🟡 Partial | `license_service.py` (12 KB) with `PLAN_DEFINITIONS`, on-prem signed licence file verification. `docs/decisions/T81_licensing_assumptions.md` exists. Gated on A5 (licensing model decision) — template/placeholder pending real sign-off |
| T90 | Local model provider — Qwen2.5-VL-7B on 24 GB GPU | 🟡 Partial | `ai/providers/qwen_vlm_provider.py` exists (5 KB) — Qwen VLM wired. PaddleOCR wired for local OCR. BGE-M3 for local embeddings/reranking. **LLM has no local provider** — every LLM call goes to an external API. `airgapped.py` explicitly states "LLM and VLM have no local provider yet (T90, not built), so air-gapped mode fails closed on those." Gated on A2 (GPU availability) |
| T91 | API-versus-local toggle that fails closed | ✅ Complete | `airgapped.py` with `enforce_local()` raises `AirGappedViolation` rather than silently falling back. `egress_guard.py` patches httpx transport to block known external AI hosts when `air_gapped=True` |
| T92 | Egress-zero verification script and CI coverage | 🟡 Partial | `test_egress_guard.py` and `test_air_gapped_toggle.py` exist. **CI egress monitoring** (the "CI job asserts zero outbound connections" from the build design) is not confirmed — tests mock the network rather than running with real network blocked |
| T93 | Helm charts and air-gapped install runbook | 🟡 Partial | `helm/veritasdocs/` directory exists. **Runbook not confirmed complete** — backlog exit criterion ("someone who did not build it installs from the runbook") has not been executed |
| T94 | Project / Collection container level | ✅ Complete | Closed by decision D-1 (signed 2026-08-24). `docs/decisions/T94_closure_note.md` documents the resolution — department-scoped RBAC over the existing folder tree satisfies the requirement without a new container abstraction |
| T97 | Performance pass on real corpus volumes | ❌ Pending | No evidence in codebase |

---

## Decisions Status

| ID | Decision | Status |
|----|----------|--------|
| D-1 | Container model | ✅ Signed 2026-08-24 — keep recursive folders, RBAC groups for scoping |
| D-2 | SaaS tenant isolation ratification | ❌ Pending — security review not confirmed done |
| D-3 | JWT access-token lifetime | ✅ Signed 2026-08-25 — 15 min access / 7-day refresh |
| D-4 | M1 accuracy tolerance numbers | ❌ Pending — no ground truth corpus yet |
| D-5 | Confidence calibration policy | ✅ Signed — `docs/decisions/D5_decision_confidence_calibration.md` exists |
| D-6 | Surya GPL inclusion decision | ❌ Pending — legal hasn't signed off |
| D-7 | Retention classes and defaults | ✅ Signed — `docs/decisions/D7_decision_retention_classes.md` exists |
| D-8 | Escrowed links in evidence exports | ✅ Signed — `docs/decisions/D8_decision_escrowed_links_in_exports.md` exists |
| D-9 | Scope of built drive product | ❌ Pending — not formally documented |

---

## Key Defects Called Out in Build Design (Section 15)

| Defect | Status |
|--------|--------|
| Silent OCR failure (`extraction_failed` not set) | ✅ Fixed — T33 complete |
| JWT 30-day access token | ✅ Fixed — T99/D-3 complete |

---

## Summary by Wave

| Wave | Description | Status |
|------|-------------|--------|
| **W0** Foundations | T01–T09, T95–T98 | **Mostly complete** — T95 partial, T96 pending, T98 partial |
| **W1** Pipeline + Connectors | T20–T33, T40–T47 | **Partial** — T22–T30 done; T25/T31/T32 blocked on A1; T44–T47 pending |
| **W2** Workbench | T50–T55 | **Mostly complete** — T53 backend done, frontend scan viewer missing |
| **W3** Evidence | T56–T67, T60–T62 | **Mostly complete** — T64 not wired to ingest; T65 draft pending A3; T66 engine not built; T62 360-view click-through incomplete |
| **W4** Surfaces | T70–T80 | **Mostly complete** — T71 frontend click-through missing; T73 record search not built |
| **W5** Shipping | T90–T97 | **Partial** — T90 LLM-local not built; T93 runbook not validated; T97 pending |
