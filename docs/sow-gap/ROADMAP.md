# DMS v1 — Roadmap & Status

**As of:** 2026-09-08 · commit `e1af8ff` · branch `feature-kunal-DMS`
**Supersedes:** the status claims in `DEVELOPER_BRIEF.md` (written 07-Sep) and the three
companion artifacts it points to (Scope gap / Build backlog / Build design, all dated
04-Sep). Those were accurate when written; the code has moved since. This file and the
`module-<letter>-*.md` checklists next to it are re-verified directly against the source
on the date above, not copied forward from any status report.

This is the file `DEVELOPER_BRIEF.md` §1 and §5 point to and that never existed. It didn't
exist because, by the time anyone went looking for it, the eleven items it was meant to
plan turned out to already be finished — this is a closing report, not a forward plan.

---

## Headline

**Every task that is actually engineering's to do is closed.** What is left is 10 tasks,
and all 10 boil down to six external dependencies: a reference corpus, a GPU, two access
grants, one legal sign-off, and one licensing decision. No amount of further coding moves
any of them.

| | |
|---|---|
| Tasks tracked (T01–T99, all modules) | 73 |
| Done, verified in source | 63 |
| Blocked on a named external dependency | 10 |
| Not started, not blocked | 0 |
| Decisions (D1–D9) | 6 signed, 3 open (2 of the 3 are pure sign-offs, not analysis) |

---

## What closed since the 04-Sep / 07-Sep documents

Everything below was still listed as open, partial, or blocked in the Build backlog
artifact or `DEVELOPER_BRIEF.md` §5's "unblocked, start today" list. All of it is done now.

| Item | Was (04/07-Sep) | Now | Evidence |
|---|---|---|---|
| D-2 RLS fix | "needs an owner and a date" | Done | `backend/migrations/versions/0046_restricted_app_role.py`, `backend/app/database.py` (`AppSessionLocal`, `establish_tenant_context`) |
| T03 Settings screen | No admin surface, no drift guard | Done | `frontend/app/admin/settings/page.tsx`, `backend/tests/test_hardcoded_threshold_gate.py` |
| T05 Source-location coverage | Native-text PDFs only (10/738 real docs) | Done — now covers scanned documents too | `backend/app/ocr/extractor.py` (`_tesseract_word_boxes`, `_paddle_word_boxes`) |
| T41 Ingest failure alerting | Logger-level only | Done — real email to the uploader | `backend/app/services/email_service.py::send_ingestion_failure_alert` |
| T44 Scanner drop path | Not started | Done — real connector + authenticated webhook | `backend/app/services/scanner_connector.py`, `backend/app/api/v1/scanner_webhook.py`, `docs/scanner_connector_setup.md` |
| T53 Crooked-scan correction | Skew stored, not visually corrected | Done — real canvas rotate+skew transform | `frontend/components/common/RegionViewerImpl.tsx` |
| T62 Property 360 click-through | Not built | Done — reuses the shared region viewer | `frontend/app/entities/page.tsx` |
| T93 Clean-room install | Never executed by anyone but its author | Done — run for real via `kind`, 4 real chart bugs found and fixed | `helm/veritasdocs/`, `docs/AIRGAPPED_INSTALL_RUNBOOK.md` |
| T95 Marathi localisation | "A handful of keys", `lang="en"` hardcoded | Coverage now substantial (161 keys, up from ~48); the specific hardcoded-`lang` defect is fixed | `frontend/lib/i18n.tsx`, `frontend/lib/locale.ts`, `frontend/app/layout.tsx`, migration `0049_marathi_translations.py` |
| T96 Accessibility baseline | No real audit existed | Done — real axe-core suite + CI gate | `frontend/tests/a11y/`, `.github/workflows/ci.yml` |
| Table row-grouping bug (not in any prior document) | Extracted rows silently shattered on multi-column pages | Fixed | `backend/app/models/fact.py` (`row_group_id`), `backend/app/pipeline/vlm_extraction.py`, `backend/app/services/fact_service.py` |
| RLS commit-then-refresh regression (found this pass, not in any prior document) | `db.commit()` could silently drop tenant context on the next statement | Fixed in 10 call sites | `backend/app/database.py::establish_tenant_context`, `backend/tests/test_commit_then_refresh_under_rls.py` |

One caveat worth stating plainly: **T95 is "coverage now substantial," not "coverage
complete."** Only 12 of 60 frontend component/page files actually call the translation
hook — most UI strings are still English literals. The framework, the key count, and the
one specific defect named in every prior document (`layout.tsx`'s hardcoded `lang="en"`)
are all fixed; full string-by-string coverage is a larger, separate effort nobody has
scoped, not a small remaining bug.

---

## Module-by-module status

Per-module checklists live next to this file, one per SoW module, in the exact
`module-<letter>-*.md` naming `DEVELOPER_BRIEF.md` §1 already pointed at. Each checklist
lists every task in that module with a checkbox, its evidence (the file that proves it),
and — for anything still open — which assumption blocks it.

| Module | SoW ref | Status | Checklist |
|---|---|---|---|
| (a) Ingestion & Connector Framework | 3.1 | 5 done · 3 blocked (A7/A8) | [module-a-ingestion-connector-framework.md](module-a-ingestion-connector-framework.md) |
| (b) Document Processing Pipeline | 3.2 | 11 done · 3 blocked (A1, D-4) | [module-b-document-processing-pipeline.md](module-b-document-processing-pipeline.md) |
| (c) Human Verification Workbench | 3.3 | 5 of 5 done | [module-c-human-verification-workbench.md](module-c-human-verification-workbench.md) |
| (d) Entity & Knowledge Layer | 3.4 | 5 of 5 done | [module-d-entity-knowledge-layer.md](module-d-entity-knowledge-layer.md) |
| (e) Search & Q&A | 3.5 | 6 of 6 done | [module-e-search-qa.md](module-e-search-qa.md) |
| (f) Records & Versioning | 3.6 | 3 of 3 done | [module-f-records-versioning.md](module-f-records-versioning.md) |
| (g) Reports & Analytics | 3.7 | 2 of 2 done | [module-g-reports-analytics.md](module-g-reports-analytics.md) |
| (h) Governance & Audit | 3.8 | 6 done · 1 blocked (A3) | [module-h-governance-audit.md](module-h-governance-audit.md) |
| (i) Data Operations | 3.9 | 3 of 3 done | [module-i-data-operations.md](module-i-data-operations.md) |
| (j) Admin & Deployment | 3.10 | 4 done · 3 blocked (A1, A2, A5) | [module-j-admin-deployment.md](module-j-admin-deployment.md) |

Foundations and cross-cutting engineering standards (T01–T09, T95, T96, T98, T99 — the
tasks the Build backlog artifact grouped as "module 0", not tied to one SoW module) are
all done; they're covered inline in the table above rather than getting their own
`module-<letter>` file, since the brief's own naming pattern is lettered a–j only. The six
engineering standards themselves (naming, config, provenance, audit, coordinate contract,
test-suite gate) are the subject of a separate `cross-cutting-standards.md`, out of scope
for this pass — not written here.

---

## The 10 open tasks, grouped by what blocks them

No engineering task closes these. Listed so nobody mistakes silence for neglect — grouped
by the six external dependencies behind them, since several tasks share one blocker.

| Item | Waiting on | What's already true |
|---|---|---|
| T25, T31, T32, T97 | **A1** — a real reference corpus with human-verified ground truth | The pipeline that would be measured against it is finished and idle. Two documents with hand-made ground truth exist as a start, explicitly not as closure. |
| T90 (local LLM, air-gapped M5) | **A2** — a 24GB GPU | Local VLM, OCR, embeddings and rerank are all built. `airgapped.py` correctly refuses to start on the LLM/VLM surface rather than falling back to a hosted API. |
| T45, T46 (Google Drive, SharePoint) | **A8** — Google restricted-scope verification, Microsoft Entra admin consent | Neither has been requested. The connector framework (T40) needs no changes to add either once access exists. |
| T47 (NIC e-Office) | **A7** — integration access | Never requested. Gated at M4 exit by design, a fast-follow, never the sole release blocker. |
| T65 (Section 63 certificate) | **A3** — legal counsel review of the wording | The generator works today — hash, algorithm, dual signature blocks — and stamps a red DRAFT banner on every output until this clears. |
| T81 (licensing enforcement) | **A5** — the licensing model decision | Plan definitions and signed on-prem licence-file verification both exist; this is a placeholder pending a business decision, not missing code. |

D-6 (Surya's GPL licence, gating nothing today — Surya isn't currently a wired dependency)
and D-4 (the numeric accuracy tolerance, unmakeable until A1 supplies real numbers to
decide against) are the two open decisions behind this list; both need a person, not code.

---

## Documents this roadmap assumes you can find

Decision documents: `docs/decisions/D1_decision_container_model.md`, `docs/decisions/D2_tenant_isolation_security_review.md`,
`docs/decisions/D3_decision_jwt_lifetime.md`, `docs/decisions/D4_accuracy_data_summary.md`, `docs/decisions/D5_decision_confidence_calibration.md`,
`docs/decisions/D7_decision_retention_classes.md`, `docs/decisions/D8_decision_escrowed_links_in_exports.md`,
`docs/decisions/D9_drive_scope_inventory.md`. D6 has no document — it's the one item in this file with no
committed analysis to point to, because the underlying dependency (Surya) was never adopted.

Architecture: `docs/architecture/T09_technical_architecture_document.md`. Licensing assumptions: `docs/decisions/T81_licensing_assumptions.md`.

Test suite: 373 tests collected as of this commit (`docker compose exec backend pytest --collect-only -q`).
