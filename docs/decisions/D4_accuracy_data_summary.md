# D-4 — Accuracy data for setting the M1 tolerance number

**Backlog item:** "M1 accuracy tolerance numbers — 'within agreed tolerance' has none
today." Owner: Product owner + QA. Blocks: M1 exit. **This document does not set that
number** — that's a real business risk-tolerance call for Product/QA to make, not
something to invent unilaterally. What it does is give them the actual, current, real
numbers to decide against, instead of nothing.

Compiled 2026-09-02 from `backend/scripts/accuracy_baseline.py`, run live against real
extracted data in a real tenant — not synthetic test data.

## What's already in the system today (an engineering default, not a business one)

`backend/app/services/template_service.py` already has a confidence-score threshold
controlling whether an extracted field auto-commits or goes to human review:

- **`auto_commit: 0.85`** — a field with model confidence ≥ 0.85 is written as
  `status: machine`, no human review required before it's used.
- **`review_floor: 0.5`** — informational tier boundary; everything below
  `auto_commit` goes to the `in_review` human queue regardless.

This is a **confidence threshold** (how sure the model claims to be), not an
**accuracy tolerance** (how often the model is actually correct, verified against
ground truth). These are related but different numbers — a model can be consistently
overconfident or underconfident. D-4 is asking for the second one.

## The real numbers today

5 real documents, hand-verified by directly reading the rendered scan pages and
comparing against what the system extracted:

| Document | Form type | Facts extracted | Ground-truth recall |
|---|---|---|---|
| `registration_file_juni_masjid...pdf` | Waqf Institution Registration File | 392 | 6/6 (100%) |
| `Wardha.pdf` | Gazette Form B (Property Assessment) | 1358 | 7/7 (100%) |
| `Aurangabad-Shia.pdf` | Gazette Form A (no-property Wakfs) | 170 | 4/4 (100%) |
| `Ambajogai (1).pdf` | Gazette Form A (no-property Wakfs) | 401 | 6/6 (100%) |
| `waqf_gazette_1973_spread_FIXED.pdf` | Gazette Register (spread, 19-column) | — | **Known failing** |

**4/5 documents at 100% recall on every hand-checked field (23/23 fields total).**
This is recall on a small, hand-picked verification sample per document (4-7 fields
each) — not full-document precision/recall across every single extracted fact. See
"What this isn't" below.

## Known, real failure modes (each actually observed on a real document, not hypothetical)

1. **Spread row-matching completeness** (the one failing document above): on a dense,
   two-page-facing table, the system correctly detects when it can't confidently join
   a row across the page break and flags it for human review (`_join_mismatch`)
   rather than guessing — but doesn't yet reliably recover every row's correct
   pairing. Rate on the one confirmed real dense sample: incomplete, not yet
   quantified as a stable percentage (needs more real samples to know if it's typical
   of this document or the form type generally).
2. **Ditto-chain corruption from an obscured seed cell**: found 2026-09-02 on
   `Ambajogai (1).pdf` — a physical ink stamp overlapping one row's cell caused a
   misread that then correctly-per-its-own-logic propagated down all 15 "ditto" rows
   on that page. A single obscured cell can corrupt many rows' worth of one field,
   not just its own row. Rate: 1 field, 1 page, out of 5 documents checked — real but
   so far isolated to documents with a stamp physically overlapping row 1.
3. **VLM run-to-run variance**: the same page re-extracted by the model can produce a
   different row count or text on a second pass. This is why `accuracy_baseline.py`
   checks recall ("was the correct value found somewhere") rather than exact-set
   equality — asserting the full extracted set matches exactly would be flaky by
   construction, not a meaningful accuracy signal.

## What this isn't

- **Not the official A1 reference corpus.** A1 (still an open, externally-owned
  blocker) is a human-verified ground-truth set built and maintained independently of
  engineering, across a representative document sample. This is 5 real documents one
  person hand-checked across two sessions — a real, useful starter, but not
  statistically representative (no handwritten-heavy sample, no damaged/torn-page
  sample, limited form-type variety).
- **Not full-document precision.** Each document has hundreds of extracted facts
  (170-1358); ground truth only re-verifies 4-7 hand-picked fields per document. A
  field that was never checked could be wrong without showing up in this recall
  number. This measures "does the system get the fields we specifically checked
  right," not "what fraction of everything it extracted is correct."
- **Not a pass/fail bar.** That's exactly what's missing and exactly what D-4 asks
  someone to set.

## Suggested framing for the actual decision (not a recommendation on the number itself)

Given the real data above, useful questions for Product/QA to answer rather than
"what's the number":
- Is 100% recall on a small hand-checked sample per document a meaningful signal at
  all, or does the tolerance need to be defined against full-document precision/
  recall once a larger corpus exists — i.e., is this data even the right shape to set
  a tolerance against?
- Should the two known failure modes above (spread row-matching, stamp-obscured ditto
  corruption) count against the tolerance number, or be tracked as separate, named
  exceptions with their own acceptance criteria?
- Does the tolerance apply uniformly across all form types, or does a newly-registered
  template (like the two Wakf gazette Form A/B templates added this session) get a
  grace period before its accuracy counts toward the M1 bar?
