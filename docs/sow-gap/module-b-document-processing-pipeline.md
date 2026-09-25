# Module (b) — Document Processing Pipeline

SoW 3.2 · Status as of 2026-09-08, commit `e1af8ff` · See [ROADMAP.md](ROADMAP.md) for the whole-system picture.

The product's core IP. The four hostile-page-layout handlers below are what the SoW calls
the highest-priority engineering work in the whole product.

- [x] **T20** — Real per-field confidence, not a hardcoded `0.9`
  Evidence: `classify_confidence()` applies per-field D-5 bands; a field with no reported confidence falls to the lowest-trust band, not to 0.9.

- [x] **T21** — Marathi and Devanagari OCR
  Evidence: Tesseract `eng+hin+mar`; PaddleOCR `lang="mr"` (`backend/app/ocr/extractor.py`).

- [x] **T22** — VLM extraction path
  Evidence: `backend/app/pipeline/vlm_extraction.py`. Gemini, OpenRouter, Chandra, Qwen, plus a fallback wrapper; writes a region per field.

- [x] **T23** — Classification stage plus unclassified queue
  Evidence: LLM template matching with a list/manual-classify/dismiss flow.

- [x] **T24** — Template registry
  Evidence: model, service, API; migrations `0011`, `0031`.

- [ ] **T25** — Seed templates from real statutory forms — **blocked on A1**
  Migration `0045` seeds starters and two real forms are registered by script. The SoW's named Basmath (1974) and Washim (2004) forms still need the official corpus.

- [x] **T26** — Handler 1, multi-page spread join
  Evidence: `join_spread` — refuses rather than guesses when the two halves disagree on serials. Proven live against a real 1973 Gazette spread (commit `07748a5`), not just a unit test.

- [x] **T27** — Handler 2, ditto-chain expansion
  Evidence: per-column chains, break rules, every inherited value labelled as inherited.

- [x] **T28** — Handler 3, continuation-row merge
  Evidence: blank-serial rows at a page top merge backward; the resulting fact carries regions on both pages.

- [x] **T29** — Handler 4, blob-cell parse
  Evidence: survey/CTS numbers and area units parsed; ambiguous units (e.g. "Akker") flagged for a human rather than guessed.

- [x] **T30** — Handwritten and degraded-page policy
  Evidence: `is_handwritten`, `_marginalia` sentinel facts; bulk confirm excludes handwritten rows unconditionally.

- [ ] **T31** — Regression corpus seeded from ground truth — **blocked on A1**
  Two real documents with hand-made ground truth exist, documented as a start, explicitly not as closure.

- [ ] **T32** — Accuracy baseline report — **blocked on A1, D-4**
  `accuracy_baseline.py` runs against real extracted data; `docs/decisions/D4_accuracy_data_summary.md` supplies real numbers. The tolerance decision itself (D-4) is Product's call and is unmade.

- [x] **T33** — Fix silent OCR failure
  Evidence: `backend/app/ocr/extractor.py:102,139-142,158`.

**Also fixed this pass, not in the original SoW task list:** extracted table rows were
silently shattered or wrongly merged on multi-column page layouts (real Wardha.pdf bug).
Fixed via `Fact.row_group_id`, persisting the real row grouping at extraction time instead
of reconstructing it heuristically from y-coordinates. See `backend/app/models/fact.py`,
`backend/app/pipeline/vlm_extraction.py`, `backend/app/services/fact_service.py`.

**Open items in this module:** 3, all external (T25, T31, T32 — see [ROADMAP.md](ROADMAP.md#the-10-open-tasks-grouped-by-what-blocks-them)).
