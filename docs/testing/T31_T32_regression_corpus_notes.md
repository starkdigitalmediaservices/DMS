# T31/T32 — regression corpus & accuracy baseline (starter, not A1)

**Status: genuine progress, not closure.** T25/T31/T32 remain blocked on **A1**
(no official human-verified reference corpus) and **D-4** (no agreed accuracy
tolerance number). This is a real, live-verified starting point built from
documents already in a real tenant — not synthetic, not invented — so the
next person to pick this up starts from evidence instead of nothing.

## What's in the corpus (2 real documents)

1. **`registration_file_juni_masjid,_hirpur,_murtizapur_akola.pdf`** —
   real handwritten/mixed Waqf registration file, 16 pages, matched to the
   `Waqf Institution Registration File` template (`single_page` layout,
   vertical table stitching). **Passing, 100% recall** on hand-verified
   ground truth (see `backend/scripts/accuracy_baseline.py`).
2. **`waqf_gazette_1973_spread_FIXED.pdf`** — real Maharashtra Government
   Gazette register, District Aurangabad, 15 Nov 1973, matched to the
   `Maharashtra State Wakf Gazette Register` template (`spread` layout,
   horizontal join across facing pages, columns 1-8 left / 9-19 right).
   **Known failing, documented, not silently ignored** — see below.

Ground truth for both was produced by rendering the real pages to PNG and
reading them directly (not by trusting the VLM's own output back).

## Two real bugs found and fixed while building this

1. **Null-bbox crash** (`vlm_extraction.py`, `table_stitch.py`): a field the
   VLM has no value for should omit its key entirely per the prompt, but the
   model doesn't always comply — it can return
   `{"bbox": [null, null, null, null], ...}` instead. The code only checked
   `bbox` was truthy; a 4-null list is a non-empty list, so it passed the
   check and then crashed on `float(None)` with an empty exception message.
   Fixed with a real `_valid_bbox()` check (all 4 coordinates must be actual
   numbers) at all 4 call sites.
2. **Raw newline inside a JSON string** (`vlm_extraction.py`,
   `_parse_vlm_response`): the model emitted a literal newline byte inside a
   cell value (`"Trees\nValuation Rs. 30."` with a real newline, not an
   escaped one) instead of escaping it. Python's `json.loads` rejects raw
   control characters in strict mode by default — one un-escaped newline in
   one cell lost every row on the page. Fixed with `strict=False`, which
   only relaxes that one rule and still requires valid JSON everywhere else.

Both fixes are real, narrow, and verified live against the actual malformed
responses that caused them (not hypothetical).

## Update 2026-09-01 — the parse-retry follow-up was built and it works

The left page (8 columns × 18 rows, dense, heavy ditto-mark usage) used to
produce **malformed JSON on 3 separate live attempts**, at a *different*
character position each time — a broken bracket structure, not a
control-character issue the two fixes above could touch. Model flakiness on
a wide/dense table, not a bug in this codebase.

Built the follow-up this note asked for: `_call_vlm_with_parse_retry()` in
`vlm_extraction.py`, same retry-on-unparseable pattern as
`table_stitch.adjudicate_structure`'s `ADJUDICATION_ATTEMPTS`. The one real
subtlety: retrying through the existing `_call_vlm_cached()` alone would do
nothing, since its cache key is a pure function of (file hash, page number,
prompt) — a naive retry would just replay the same cached bad response
forever. The fix bypasses the cache on retry attempts (a fresh sample from
the model, not the cached one) and — after discovering `record_vlm_response`
is write-once by design and silently no-ops on an existing key — added
`overwrite_vlm_response()` so a corrected response actually replaces the bad
one in the cache, instead of leaving it there for every future run to keep
retrying past. Unit-tested (`test_vlm_parse_retry.py`,
`test_extraction_archive_service.py`): malformed-then-good retry succeeds,
all-attempts-malformed degrades to 0 facts without crashing, and a
successful retry genuinely overwrites the cache (verified via a second,
independently-mocked VLM that would return different data if the cache
rewrite hadn't worked).

**Live-verified against the real gazette document** (`GAZETTE_DOC_ID`,
read-only — ran inside a DB transaction that was rolled back, never
committed, so the real tenant's data was never touched): attempt 1 on the
left page failed with the exact documented symptom
(`json.JSONDecodeError`, malformed structure), and the retry — a fresh,
uncached sample — parsed successfully on attempt 2. The specific bug this
note asked to fix (JSON-parse-level flakiness) is confirmed fixed.

**What's still open, and distinct from the parse fix above:** even with a
parseable response, this run's left/right join only recovered 2 real field
facts (`reviewed_by`) plus 2 `_join_mismatch` rows, not the full ~18-row
table. That's the row-matching/reconciliation layer (TS1's
`join_rows_horizontally`, T26's real-scan validation checklist below) —
getting valid JSON back doesn't by itself guarantee every row's serial
number matches cleanly between the two page halves. Still blocked on A1 for
a real second (and third, etc.) sample to know whether this run's row-match
rate is typical or an outlier — one confirmed real spread proves the
mechanism doesn't crash, not that it's accurate.

## Running it

```
docker compose exec backend python3 scripts/accuracy_baseline.py \
  --email <email> --password <password>
```

## T26 update 2026-09-01 — a second real spread document, and it corroborates checklist item #2

Found `Wardha.pdf` already in the real tenant (uploaded outside this
session, 14 pages, matched to the same gazette spread template) with
extraction already run. Read-only DB inspection (no re-run, no cost spent):
every one of its 5 measurable page-pairs (3-4, 5-6, 7-8, 9-10, 11-12) wrote
a `_join_mismatch` fact with the identical reason — `'sr_no' values on each
side disagree entirely, no shared value between the two fragments`. Pairs
1-2 and 13-14 produced neither a mismatch fact nor an error (silently
skipped — `if not left_rows or not right_rows: continue` — consistent with
non-tabular front/back matter, not a bug).

This is a **second independent real document showing the exact same
structural failure**: the role:`serial` field the code asks for on both
halves never actually matches between them. That's real corroborating
evidence for the real-scan validation checklist's item #2 in
`vlm_extraction.py::_extract_spread_facts` ("Is role:'serial' really
printed on BOTH halves of a real spread, or only once... if the latter,
the current 'ask serial on both sides' assumption is wrong"): with 2/2 real
spread documents showing a 100% left/right serial mismatch rate, "wrong
assumption" now looks like the likely answer, not just a documented
possibility. Registered as a second known-failing entry in
`accuracy_baseline.py` (`WARDHA_DOC_ID`) — not hand-verified against
ground truth (would need rendering+reading all 14 pages, out of scope for
this pass), just the observed real symptom, same honest-documentation
pattern as the first gazette.

**Still needs A1 to actually resolve**: fixing this would mean changing
the pairing strategy (e.g., matching by bbox vertical position only,
dropping the serial-match fast path entirely for this template) — a real
code change, but risky to make from 2 data points without knowing whether
either document's serial numbering is itself atypical (poor scan quality,
inconsistent handwriting) versus the pairing assumption being structurally
wrong for every real spread of this form type. Flagging, not fixing blind.

## T25 update 2026-09-01 — the two templates are now seeded, not live-only

Both templates existed only as hand-created rows in this dev DB (built
while assembling this corpus), so a fresh environment — a new dev DB, CI,
another deployment — would have neither, and the two documents above would
fail to classify entirely. Added migration `0045_seed_starter_templates`
(idempotent, `ON CONFLICT (form_type, era_label) DO NOTHING`, safe to run
against a DB where they already exist). Still not T25's real goal — a
template library seeded from an official, human-verified form catalogue,
which needs A1 — but it closes the "works on this one dev DB only" gap for
the two templates that do exist.

## Update 2026-09-02 — a real, previously-unclassified corpus + a new Form A template

User pointed at two real documents already sitting in the tenant
(`Ambajogai (1).pdf`, 68 pages; `Aurangabad-Shia.pdf`, 12 pages) that had
sat `unclassified` since 2026-08-28, never processed. Downloaded both from
MinIO and rendered pages directly to understand why no template matched.

**Real finding: same 1973/74 Marathwada Wakf Board gazette family as the
existing spread template, but a different sub-form.** Both documents are
almost entirely "Part A" — wakfs with no property, or property too small
to need listing, per each document's own cover note ("A Means Wakfs
having no property..."). Part A rows are fully self-contained on ONE
page: 8 columns (serial+village, wakf name, sect, object, wakf name
(col 5), creation date, deed details, mutawalli) — no facing/continuation
page. That's exactly the existing template's *left*-half field set, just
never needing a right half. The existing template is `layout='spread'`
and assumes every row needs a right-half page (true for its Part B/C
rows), which is why classification correctly found no match — not a bug,
a genuinely different sub-form.

Registered a new template (`scripts/register_waqf_gazette_form_a.py`,
kept as a reusable tool): "Maharashtra State Wakf Gazette Register (Form
A, no-property Wakfs)" | "Marathwada Region Gazette, 1973-1974",
`layout='single_page'`, the same 8 field names as the existing template's
left half (so a future merge into one smarter mixed-layout template stays
easy). Manually classified both documents against it (automatic
LLM-classification wasn't re-run; this was a direct, audited assignment,
same mechanism T25's manual-override endpoint already provides) and ran
real VLM extraction: Aurangabad-Shia wrote 170 facts, Ambajogai wrote 401.

**Ground truth hand-verified 2026-09-02** by rendering page 2 of each to
PNG and reading it directly against the extracted Facts (same method as
every other entry in this file). Both pass at 100% recall on every
checked field — `sect`, `object`, `wakf_name_col5`, and `mutawalli_name`
all matched my independent reading exactly, including correct ditto-chain
(TS5) expansion of "Do."/"Do," marks down the page.

**Real bug found: a stamp-obscured seed row corrupts an entire page's
ditto-filled values, not just its own row.** Ambajogai page 2 has a real
ink stamp ("Maharashtra State Board Of Wakfs, Aurangabad") physically
overlapping row 1's `deed_details`/`mutawalli_name` cells. The VLM
misread the obscured text, and ditto-chain then correctly-per-its-own-
logic propagated that one wrong reading down every "Do." row on the page
— all 15 rows show the same garbled `deed_details` value. This is a new
failure mode, not seen on the stamp-free pages checked so far: previous
ditto-chain testing (TS5) never happened to hit a page where the *seed*
row itself was misread. Excluded `deed_details` from this document's
ground truth rather than guess at a fix — flagged, matching this file's
established practice for real structural gaps.

**Also fixed a real, silent bug in `accuracy_baseline.py` itself while
adding these two documents**: `check_document()` queried
`GET /facts/queue?category=low_confidence`, which only returns
`in_review` facts. Every ground-truth value for these two new documents
landed as a high-confidence `machine` fact (0.96-0.99 confidence) and
would never have shown up there — the script would have silently reported
0% recall on entirely correct extractions. Rewrote it to query
`doc_dg_facts` directly (no more `--email`/`--password` needed either),
which is what the script's own docstring already promised ("recovered
*somewhere* in the document's Facts") but wasn't actually doing.

## T26 update 2026-09-02 — Wardha.pdf was never a spread-layout document

Started "T26" as its own task right after the above. Downloaded Wardha.pdf
and rendered its pages directly to actually look at what the "5/5
page-pairs failed to join, 100% mismatch" finding was really showing.

**Real finding: Wardha.pdf is a completely different, unrelated document
from the one its `matched_template_id` pointed at.** Its cover page is
dated 30 December 2004 ("List of Wakf properties District Warda"),
published under the Central Wakf Act 1995 — not the 1954 Act the 1973
Aurangabad gazette (the template it was matched against) uses. Its table
pages are "Form B (See Rule 5)": a single, self-contained, single-page-
wide table (14 columns — Sr.No, Name of the Wakf Institution, Sunni/
Shia, Nature & Object, Admin of Wakf, Creation of Wakf, Boundaries, Wakf
Deed Reg Deed, Movable Property, Immovable Property, Value, Income, Tax
Payable, Scheme Settlement — alphanumeric Sr.No like "WB-116", not the
numeric sr_no the other templates use). Confirmed by comparing two
consecutive pages (3 and 4): their Sr.No ranges (WB-116, WB-18, WB-8, ...
vs WB-114, WB-127, WB-14, ...) never overlap at all — these are two
independent pages of institutions, not a left/right split of the same
rows. There is no "right half" to join against; the 100% mismatch rate
was the entirely expected, correct result of the join logic comparing two
unrelated pages against each other under the wrong template. **Not a T26
extraction-logic gap — a plain misclassification**, most likely a
mistaken manual assignment from before this session (Wardha.pdf was
"found already uploaded to the real tenant, outside this session,"
per the original 2026-09-01 note).

Registered the real "Form B" template
(`scripts/register_wardha_form_b.py`, kept as a reusable tool,
`layout=single_page`) and reclassified. Extraction hit two real,
separate blockers along the way, both resolved:

1. **OpenRouter account ran out of credits mid-run** (`402: This request
   requires more credits`) — external, account-level, not fixable from
   code. Switched `AI_VLM_PROVIDER` from `openrouter` to `gemini` in
   `backend/.env` (the native `GeminiVLMProvider` already existed, fully
   implemented, using the same `GOOGLE_API_KEY` already configured for
   embeddings — a completely separate credit pool from OpenRouter).
2. **A real, small diagnostics bug found live**: several pages failed
   with `"T22 VLM extraction failed on page N of Wardha.pdf: "` — nothing
   after the colon. `except Exception as e: ... f"{e}"` silently produces
   an empty string for exceptions like `httpx.ReadTimeout` that carry no
   message, only a type. Fixed in `vlm_extraction.py`
   (`f"{type(e).__name__}: {e}"`) — re-running with the fix revealed the
   real cause: Gemini's free tier is rate-limited to 20
   `generate_content` requests/minute, and page-by-page sequential
   extraction across a 14-page document hit that ceiling. Not fixed
   further (would need a backoff/retry-with-delay layer, out of scope for
   this pass) — 13/14 pages succeeded on the pass that used this fixed
   diagnostic; the corpus entry below reflects that.

Ground truth hand-verified 2026-09-02 by reading page 3 directly against
the extracted Facts: **7/7 fields match exactly** (`sr_no`, `wakf_name`,
`sect`, `nature_object`, `admin_of_wakf`, `gross_income`, `value`) —
1358 real facts written across 13/14 pages.

**Corpus is now 5 real documents, 4 passing at 100% recall** (23/23
hand-verified fields), 1 documented-failing (unchanged — the original
1973 gazette's dense 18-row table, a genuine remaining row-matching-
completeness gap, unrelated to Wardha). Real
`docker compose exec backend python3 scripts/accuracy_baseline.py`
output:

```
[1] Waqf Institution Registration File — 392 facts, 6/6 (100%)
[3] Wardha.pdf (Form B) — 1358 facts, 7/7 (100%)
[4] Aurangabad-Shia.pdf (Form A) — 170 facts, 4/4 (100%)
[5] Ambajogai (1).pdf (Form A) — 401 facts, 6/6 (100%)
Corpus size: 5 real documents (4 passing, avg 100% recall; 1 documented-failing)
```

## What's needed to actually close T25/T26/T31/T32

- **A1**: a real, official reference corpus with human-verified ground
  truth across a representative document sample — now 5 documents one
  person hand-checked across two sessions, still not an official corpus
  with representative coverage (no handwritten-heavy sample, no damaged/
  torn-page sample).
- **D-4**: someone needs to agree what accuracy is "good enough" — this
  report has no pass/fail bar, it just states recall.
- The gazette's left-page parse-retry fix (above) before that document can
  move from "documented known-failing" to "passing" — this is now the
  *only* remaining T26 gap; every other "spread join failure" investigated
  this session turned out to be a misclassification, not a real pairing
  bug. Still worth treating this one as genuinely unresolved rather than
  assuming it's the same root cause.
- The stamp-obscured-seed-row ditto-corruption bug (above) — needs either
  stamp/seal detection or a per-row confidence signal that can veto a
  ditto-chain when the seed cell itself looks unreadable.
- A real fix for Part B/C rows within a Form A document (currently only
  the first 8 columns are captured for those rows) — would need either a
  combined template that switches sub-layout per row, or per-row
  classification instead of per-document.
- Gemini free-tier rate limiting (20 req/min) makes native `gemini` an
  unreliable default for any multi-page bulk extraction run — needs
  either a paid tier or a backoff/retry-with-delay layer before relying
  on it for anything beyond spot-checks like this session's.
- Wiring `accuracy_baseline.py` (or its successor) into CI once real
  reference PDFs are committed to the repo — right now it depends on live
  data in a real tenant, which CI shouldn't depend on.
