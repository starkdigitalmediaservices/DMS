"""T22 — VLM extraction path.

The chunker (T05) keeps word boxes for running text, but a scanned
register — multi-column tables, ditto marks, entries that wrap across two
pages — needs an actual look at the page image to read correctly, not just
a text dump. This module renders each page, asks a vision-language model
to read it against a registered Template's field schema (T24), runs the
result through the four record handlers (T26-T29) where the template says
to, and writes one Fact + FactRegion per field per row so every value is
click-through-able back to the exact spot it was read from (T06 contract).

Two-page spread joins (Handler 1, T26 — a register entry split across
facing pages, matched by serial number) are wired via Template.layout ==
"spread" and _extract_spread_facts(). The left/right field convention it
reads is a best-effort invention, not modeled on a real scanned spread —
no seeded template exists yet (T25 stays blocked on A1, no reference
corpus) — flagged for revalidation once one is available, not a
confirmed real-world shape. A page pair that can't be reconciled by
serial number OR bbox position (TS1, see table_stitch.py) writes a
field_name="_join_mismatch" Fact for a human to look at, same
reused-facts pattern T30 used for marginalia.

TS1 also stitches the generic (non-spread) path vertically: consecutive
pages whose extracted field coverage is evidence-equivalent (or, for the
genuinely ambiguous cases, adjudicated by a narrow local-LLM call) get
concatenated into one logical segment BEFORE continuation-merge runs, so
a continuation row that happens to start a fresh page merges into the
entry above it instead of becoming a stray, disconnected row. See
_stitch_vertical_segments() and app/pipeline/table_stitch.py.

Document classification (T23) is now a separate stage
(app/services/classification_service.py) that runs unconditionally at
ingest and persists its result on the document, rather than the ad-hoc
unpersisted match this module used to do inline. worker.py calls it first
and passes the resolved template in here.

Best-effort by design: a VLM failure never fails ingestion. Search and
chunk indexing must never wait on this (Section 3.5), so every call site
wraps this module in a try/except and logs, it doesn't raise.
"""
import hashlib
import io
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import get_vlm_provider, get_llm_provider
from app.config import settings
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.page import DocumentPage
from app.models.template import Template
from app.pipeline.handlers.blob_cell_parser import parse_blob_cell
from app.pipeline.handlers.continuation_merge import merge_continuation_rows
from app.pipeline.handlers.ditto_chain import expand_ditto_chains
from app.pipeline import table_stitch
from app.services.template_service import classify_confidence
from app.services import table_shape_service
from app.services.extraction_archive_service import compute_vlm_cache_key, get_cached_vlm_response, record_vlm_response, overwrite_vlm_response
from app.services.config_service import get_float

logger = logging.getLogger(__name__)

RENDERABLE_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "bmp", "webp", "tiff"}

# Higher than the default page-render resolution (150) — a spread's
# right-hand band is routinely a dense, heavily-abbreviated wide table,
# and low resolution makes tightly-packed columns harder for the OCR
# engine to distinguish reliably. See _extract_spread_facts.
SPREAD_RENDER_RESOLUTION = 300

# Per-template field_schema convention this module reads (on top of T24's
# name/type/required/validation keys), all optional:
#   "role": "serial"             — the row-number column continuation-merge keys off
#   "role": "continuation_text"  — text column(s) a continuation row's text merges into
#   "ditto_eligible": true       — this column may carry a ditto mark to expand
#   "role": "chain_anchor"       — TS5, opt-in and unused by any currently-registered
#                                   template: a genuine change in this column resets
#                                   every ditto_eligible column's chain, not just its
#                                   own (see ditto_chain.py's own docstring for why
#                                   this is never inferred automatically)
#   "type": "blob"               — free-text cell to run through parse_blob_cell
#   "role": "page_header"        — printed ONCE in the page's header area (e.g. a
#                                   Maharashtra 7/12 form's "गाव/ता./जि." village/
#                                   taluka/district line above the row table), never
#                                   repeated per table row. Found live 2026-09-04:
#                                   without this, a field like "village" was asked
#                                   for on every row like an ordinary column, so the
#                                   model — unable to find it inside any row — filled
#                                   it with the nearest table cell instead (a serial
#                                   number, a column abbreviation) rather than the
#                                   real header text a few lines above. Excluded from
#                                   the row-per-row prompt schema and from
#                                   full_schema_fields (TS1's stitch-coverage
#                                   denominator, extract_facts_for_document) since it
#                                   can never appear inside a row's field set either
#                                   way. Only wired for the single_page path
#                                   (extract_facts_for_document) — _extract_spread_facts
#                                   is itself an unvalidated, A1-blocked mechanism (see
#                                   its own docstring) that no registered template uses
#                                   yet, so it isn't worth compounding here.




def _render_pdf_page_png(file_bytes: bytes, page_number: int, resolution: int = 150) -> Optional[tuple[bytes, float, float, float]]:
    """Returns (png_bytes, width, height, rotation) for one 1-indexed PDF page, or None."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        if page_number - 1 >= len(pdf.pages):
            return None
        page = pdf.pages[page_number - 1]
        img = page.to_image(resolution=resolution).original
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        rotation = float(getattr(page, "rotation", 0) or 0)
        return buf.getvalue(), float(page.width), float(page.height), rotation


def _render_image_page_png(file_bytes: bytes) -> tuple[bytes, float, float, float]:
    from PIL import Image
    img = Image.open(io.BytesIO(file_bytes))
    width, height = img.size
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), float(width), float(height), 0.0


def _build_extraction_prompt(field_schema: List[dict]) -> str:
    header_field_schema = [f for f in field_schema if f.get("role") == "page_header"]
    row_field_schema = [f for f in field_schema if f.get("role") != "page_header"]

    schema_for_prompt = [
        {"name": f["name"], "type": f.get("type", "string"), "required": f.get("required", False)}
        for f in row_field_schema
    ]
    header_schema_for_prompt = [
        {"name": f["name"], "type": f.get("type", "string"), "required": f.get("required", False)}
        for f in header_field_schema
    ]

    header_json_contract = (
        ', "page_header": { "<field_name>": {"value": <string|number|null>, '
        '"bbox": [x0,y0,x1,y1], "confidence": <0.0-1.0>, "is_handwritten": <bool>}, ... }'
        if header_field_schema else ""
    )
    header_rule = (
        "- \"page_header\" fields are printed ONCE on this page, in the form's header "
        "area (e.g. a \"Village: X  Taluka: Y  District: Z\" line printed once above "
        "the row table) — never repeated per table row, and never sourced from a table "
        "cell, row number, or column abbreviation below the header. Extract each into "
        "the top-level \"page_header\" object exactly once. If a page_header field "
        "genuinely is not visible on this page (e.g. a continuation page with no "
        "repeated header line), omit its key from \"page_header\" entirely rather than "
        "guessing a nearby table value.\n"
        if header_field_schema else ""
    )
    header_schema_block = (
        f"Page-header field schema (once per page, in the header area — never a "
        f"row column):\n{json.dumps(header_schema_for_prompt, ensure_ascii=False)}\n\n"
        if header_field_schema else ""
    )

    return (
        "You are reading one page of a scanned government register. Return ONLY valid JSON "
        "(no markdown fences) shaped exactly like this:\n"
        '{"rows": [ { "<field_name>": {"value": <string|number|null>, '
        '"bbox": [x0,y0,x1,y1], "confidence": <0.0-1.0>, "is_handwritten": <bool>}, ... } ]'
        f'{header_json_contract}, '
        '"marginalia": [ {"text": <string>, "bbox": [x0,y0,x1,y1]}, ... ]}\n\n'
        "Rules:\n"
        "- One entry in \"rows\" per data row visible on this page (a page with a single "
        "form, not a table, still produces exactly one row). A table row where most or all "
        "cells just show a placeholder mark for 'not applicable' (e.g. \"..\", \"--\", \"-\") "
        "is still a real row — include it with those literal marks as values, do not skip it. "
        "Every printed row matters for matching this page's rows against a continuation page.\n"
        "- bbox is [x0,y0,x1,y1], each a fraction from 0.0 to 1.0 of the page's width/height. "
        "The ORIGIN is the page's TOP-LEFT corner: x grows right, y grows DOWN.\n"
        "- If a cell literally contains a ditto mark (e.g. \",,\", '\"', \"do\", \"-do-\") "
        "meaning 'same as the row above', return that literal mark as the value — do not "
        "resolve it to the value above yourself.\n"
        "- If a row's serial number is blank because the row is just wrapped continuation "
        "text from the entry above, return \"\" for that field.\n"
        "- If a field from the schema is not present anywhere on this page, omit its key "
        "from that row entirely rather than guessing a value.\n"
        "- Never invent a row or a value that is not visibly written on the page.\n"
        "- \"is_handwritten\" is true when THAT SPECIFIC VALUE is handwritten (pen/pencil "
        "ink) rather than printed/typed — set it per field, not per page; a printed form "
        "with one handwritten entry has is_handwritten=false on every other field.\n"
        f"{header_rule}"
        "- \"marginalia\": any handwritten note, stamp annotation, or remark on the page "
        "that is NOT an answer to one of the schema's fields (e.g. a note in the margin, "
        "an interlineation, a struck-through correction) — each with its own bbox. Do not "
        "put marginalia text into a field's value.\n\n"
        f"{header_schema_block}"
        # chandra_provider.py's _extract_field_names() recovers the row
        # field list by exact-matching this literal "Field schema:\n"
        # marker with rfind() and json.loads()-ing everything after it —
        # Chandra doesn't consume the rest of this prompt as instructions
        # at all (see that provider's module docstring). This marker text
        # and its trailing JSON array MUST stay the last thing in the
        # prompt, verbatim, with nothing appended after it, or Chandra's
        # column-to-field mapping silently gets zero fields and returns
        # empty rows for every page (a real regression hit live
        # 2026-09-04 while adding page_header support: renaming this to
        # "Field schema (per data row):" and appending the header schema
        # block after it broke the marker match).
        f"Field schema:\n{json.dumps(schema_for_prompt, ensure_ascii=False)}"
    )


def _valid_bbox(bbox: Any) -> bool:
    """A field the VLM has no value for should have its key omitted
    entirely (the prompt says so), but a model doesn't always comply —
    observed live on a real 1973 gazette's right-hand page: a field with
    no value came back as {"bbox": [null, null, null, null], ...} instead
    of being omitted. `not src_field.get("bbox")` treats a 4-null list as
    truthy (it's a non-empty list), so the null slips past that guard and
    crashes float(None) downstream with an empty exception message. Every
    coordinate must be present and a real number, not just the list."""
    return (
        isinstance(bbox, (list, tuple))
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) for v in bbox)
    )


def _parse_vlm_response(raw: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    # strict=False — observed live on a real multi-line register cell
    # ("Trees\nValuation Rs. 30."): the model emitted a literal raw
    # newline byte inside the JSON string instead of escaping it as \n.
    # That's invalid per the JSON spec, and Python's default strict mode
    # rejects it outright — one un-escaped newline in one cell then loses
    # every row on the page, not just that cell. strict=False is the
    # documented, narrow escape hatch for exactly this: it still requires
    # valid JSON structure everywhere else, it only stops treating a raw
    # control character inside a string as a hard parse error.
    data = json.loads(text.strip(), strict=False)
    rows = data.get("rows", [])
    marginalia = data.get("marginalia", [])
    page_header = data.get("page_header", {})
    return (
        rows if isinstance(rows, list) else [],
        marginalia if isinstance(marginalia, list) else [],
        page_header if isinstance(page_header, dict) else {},
    )


def _find_role_field(field_schema: List[dict], role: str) -> Optional[str]:
    for f in field_schema:
        if f.get("role") == role:
            return f["name"]
    return None


async def _call_vlm_cached(db: AsyncSession, vlm, file_hash: str, page_number: int, image_bytes: bytes, prompt: str) -> str:
    """TS3 — a page asked with the exact same prompt (same template, same
    field subset) never re-spends a Gemini call. The cache key includes
    the prompt itself, so a left-half vs right-half spread call (or a
    template's field_schema changing) naturally gets its own cache entry
    rather than colliding. It also includes the configured VLM provider
    (settings.ai_vlm_provider), so switching providers never replays a
    stale response cached under the old one — same convention as the
    OCR archive's ocr_engine key."""
    cache_key = compute_vlm_cache_key(file_hash, page_number, prompt, settings.ai_vlm_provider)
    try:
        cached = await get_cached_vlm_response(db, cache_key)
    except Exception as e:
        logger.warning(f"TS3 VLM cache lookup failed for page {page_number}: {e}")
        cached = None
    if cached is not None:
        logger.info(f"TS3 VLM cache hit for page {page_number} (key {cache_key[:12]}...)")
        return cached

    raw = await vlm.extract_structured(image_bytes, prompt)
    try:
        await record_vlm_response(db, cache_key, raw)
    except Exception as e:
        logger.warning(f"TS3 VLM cache write failed for page {page_number}: {e}")
    return raw


VLM_PARSE_RETRY_ATTEMPTS = 3


async def _call_vlm_with_parse_retry(
    db: AsyncSession, vlm, file_hash: str, page_number: int, image_bytes: bytes, prompt: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """T31/T32 follow-up (documented in docs/testing/T31_T32_regression_corpus_notes.md):
    on a real 1973 gazette's dense left-hand page, the VLM returned
    malformed JSON at a different position on 3 separate live attempts —
    not the two structural bugs already fixed (null bbox, raw newline),
    just model flakiness on a wide/dense table. Same class of failure
    table_stitch.adjudicate_structure() already retries for the
    adjudication LLM; this applies the identical pattern here.

    Retrying via _call_vlm_cached alone would do nothing: the cache key
    is a pure function of (file_hash, page_number, prompt), so a retry
    would just replay the same bad cached response forever. The first
    attempt uses the cache as normal (fast path for the common case, a
    response that parsed fine the first time); a retry after a parse
    failure bypasses the cache and asks the model again for a fresh
    sample, then overwrites the bad cache entry with a good one so later
    runs benefit too.
    """
    last_error: Optional[Exception] = None
    for attempt in range(VLM_PARSE_RETRY_ATTEMPTS):
        raw = (
            await _call_vlm_cached(db, vlm, file_hash, page_number, image_bytes, prompt)
            if attempt == 0
            else await vlm.extract_structured(image_bytes, prompt)
        )
        try:
            rows, marginalia, page_header = _parse_vlm_response(raw)
        except Exception as e:
            # Not just json.JSONDecodeError: a malformed-but-valid-JSON
            # reply (e.g. a bare list instead of the expected object) can
            # also raise AttributeError/TypeError inside _parse_vlm_response
            # — any of these means "unusable response, worth a retry."
            last_error = e
            logger.warning(
                f"VLM response unparseable for page {page_number}, "
                f"attempt {attempt + 1}/{VLM_PARSE_RETRY_ATTEMPTS}: {e}"
            )
            continue

        if attempt > 0:
            try:
                cache_key = compute_vlm_cache_key(file_hash, page_number, prompt, settings.ai_vlm_provider)
                await overwrite_vlm_response(db, cache_key, raw)
            except Exception as e:
                logger.warning(f"VLM cache rewrite failed for page {page_number}: {e}")
        return rows, marginalia, page_header

    logger.error(
        f"VLM response unparseable for page {page_number} after "
        f"{VLM_PARSE_RETRY_ATTEMPTS} attempts, giving up: {last_error}"
    )
    return [], [], {}


async def _get_or_create_page(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, version_id: UUID,
    page_number: int, width: float, height: float, rotation: float,
) -> DocumentPage:
    res = await db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id,
            DocumentPage.version_id == version_id,
            DocumentPage.page_number == page_number,
        )
    )
    page = res.scalar_one_or_none()
    if page:
        return page
    page = DocumentPage(
        tenant_id=tenant_id, document_id=document_id, version_id=version_id,
        page_number=page_number, width=width, height=height,
        rotation=rotation, skew=0.0,  # deskew detection is a separate, not-yet-built pass
    )
    db.add(page)
    await db.flush()
    return page


async def _extract_spread_facts(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, version_id: UUID,
    file_bytes: bytes, ext: str, vlm, field_schema: List[dict], max_pages: int,
) -> int:
    """T26 — join_spread() wiring: a spread-layout register's entry runs
    across two facing pages, matched and merged by serial number.

    The left/right convention this reads from field_schema (a field's
    "half": "left"|"right" key; the role:"serial" field is asked on both
    sides, since join_spread() matches on it) is an invented mechanism,
    not modeled on a real scanned spread — no seeded template exists yet
    (T25 stays blocked on A1, no reference corpus). Treat this as a
    best-effort capability to revalidate against real layouts once one
    is available, not a confirmed real-world shape.

    Deliberately does not run continuation-merge, ditto-chain, or
    blob-cell parsing on top of this — stacking those handlers on an
    unvalidated layout guess would compound one speculative assumption
    on another. A page-pair the serial number AND bbox position both
    can't reconcile writes a Fact+FactRegion under the
    field_name="_join_mismatch" sentinel (same reused-facts pattern T30
    used for marginalia), anchored to the left page's full extent —
    there's no finer-grained region for a whole-pair structural mismatch.

    TS1 — row matching goes through table_stitch.join_rows_horizontally()
    rather than the plain exact-set join_spread() it used to: an exact
    serial match still joins directly, but a row present on only one side
    (e.g. one waqf entry lists two properties, so the right-hand band has
    an extra line) is now reconciled by bbox vertical position instead of
    failing the whole page pair. A pair with genuinely no shared serials
    at all — or leftovers with no usable bbox to place them by — still
    goes to _join_mismatch exactly as before.

    T26 real-scan validation checklist (blocked on A1 — no reference
    corpus exists yet; written so validation is a slot-in, not a re-design,
    once one real spread-layout document is available):
      1. Does a template's field_schema actually need per-field "half"
         tagging, or does the real register put every field on a
         predictable side (e.g. "left" = columns 1-N, "right" = the
         rest) that could be inferred from column order instead?
      2. CONFIRMED 2026-09-07, read-only re-run against the real
         waqf_gazette_1973_spread_FIXED.pdf (District Aurangabad, 1973 —
         the one real document currently matched to this template):
         role:"serial" is NOT printed on the right-hand band at all — 17
         of 18 right-side rows came back the ditto placeholder "..", one
         came back a stray leaked village name ("Kanadgaon"), zero came
         back a real serial number. So yes, the "ask serial on both
         sides" prompt assumption is wrong for this real layout — but
         it's already harmless: _looks_like_a_key_value's majority check
         (see its docstring, and test_join_rows_horizontally_ditto_marks_
         and_place_names_are_not_keys / ..._single_stray_value_is_still_
         structural_absence in test_table_stitch.py) correctly recognizes
         this as "no real keys on this side," not a genuine conflict, and
         falls through to the equal-row-count positional fallback, which
         paired all 18 rows correctly (join status "ok", zero
         _join_mismatch). The synthetic tests already modeling exactly
         this ditto/stray-value shape turned out to accurately predict
         real scan behavior. Left unchanged: still asking for serial on
         the right side is mildly wasteful (one extra field slot, one
         occasional noisy value that gets filtered out) but not wrong
         enough to justify a prompt change on an N=1 sample — a second
         real spread would need to confirm this generalizes before
         trimming the right-side prompt to skip serial entirely.
      3. Does bbox vertical position (the TS1 fallback) actually line up
         between two facing pages in a real bound-register scan, given
         that the left/right pages are photographed separately and may
         have different skew/crop/margin — or does it need a per-page
         normalisation step first? Not directly exercised by the
         2026-09-07 re-run above — that page pair joined via the
         EQUAL-COUNT rank-order fallback (table_stitch.join_rows_
         horizontally's zero-anchor path), not real bbox-position
         matching, so this item is still open.
      4. Sample size: one confirmed real spread is enough to catch a
         structurally wrong assumption, but not enough to trust the
         _join_mismatch rate as a real-world number — treat an initial
         validation as "does this break," not "is this accurate." Still
         true after the 2026-09-07 finding above: N=1 real document,
         1 real page-pair confirmed working — a genuine positive result,
         not a closed investigation.
    """
    serial_field = _find_role_field(field_schema, "serial")
    if not serial_field:
        logger.warning("T26 spread template has no role:'serial' field — cannot pair pages by serial, skipping")
        return 0

    left_fields = [f for f in field_schema if f.get("half") == "left" or f["name"] == serial_field]
    right_fields = [f for f in field_schema if f.get("half") == "right" or f["name"] == serial_field]
    left_prompt = _build_extraction_prompt(left_fields)
    right_prompt = _build_extraction_prompt(right_fields)
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    facts_written = 0

    for left_page_number in range(1, max_pages, 2):
        right_page_number = left_page_number + 1
        if right_page_number > max_pages:
            logger.warning(f"T26 spread template: page {left_page_number} has no matching right-hand page, skipping")
            break

        try:
            if ext == "pdf":
                # Rendered at a higher resolution than the default 150 —
                # a spread's right-hand band is routinely a dense,
                # heavily-abbreviated wide table (seen live: an 11-column
                # band packed with ditto marks). Live-tested 2026-09-03:
                # the OCR engine's own table detection was inconsistent
                # page-to-page on that exact content at 150 DPI (15 rows
                # detected one call, 0 the next, same input) — more
                # pixels to distinguish those tightly-packed columns is a
                # real, low-risk lever to try before accepting that as
                # unfixable.
                left_rendered = _render_pdf_page_png(file_bytes, left_page_number, resolution=SPREAD_RENDER_RESOLUTION)
                right_rendered = _render_pdf_page_png(file_bytes, right_page_number, resolution=SPREAD_RENDER_RESOLUTION)
            else:
                # A standalone image is one page — a spread needs two, so
                # there's nothing to pair for a non-PDF upload.
                left_rendered = right_rendered = None
            if left_rendered is None or right_rendered is None:
                continue
            left_png, left_w, left_h, left_rot = left_rendered
            right_png, right_w, right_h, right_rot = right_rendered

            left_rows, _, _ = await _call_vlm_with_parse_retry(db, vlm, file_hash, left_page_number, left_png, left_prompt)
            right_rows, _, _ = await _call_vlm_with_parse_retry(db, vlm, file_hash, right_page_number, right_png, right_prompt)
            if not left_rows or not right_rows:
                continue

            result = table_stitch.join_rows_horizontally(left_rows, right_rows, serial_field)

            left_page = await _get_or_create_page(db, tenant_id, document_id, version_id, left_page_number, left_w, left_h, left_rot)
            # Fetched here, before the needs_review branch below, so a
            # join-mismatch fact can carry both pages' regions — a reviewer
            # in the Workbench's Join Mismatches queue needs to see left
            # AND right to pair the halves, the same reason
            # _write_stitch_ambiguous_fact writes both of its pages' regions.
            right_page = await _get_or_create_page(db, tenant_id, document_id, version_id, right_page_number, right_w, right_h, right_rot)

            if result.status == "needs_review":
                fact = Fact(
                    tenant_id=tenant_id, document_id=document_id, version_id=version_id,
                    field_name="_join_mismatch",
                    value={"reason": result.reason, "left_page": left_page_number, "right_page": right_page_number},
                    confidence=None, is_handwritten=False, status="in_review",
                )
                db.add(fact)
                await db.flush()
                db.add(FactRegion(tenant_id=tenant_id, fact_id=fact.id, page_id=left_page.id, x0=0.0, y0=0.0, x1=1.0, y1=1.0))
                db.add(FactRegion(tenant_id=tenant_id, fact_id=fact.id, page_id=right_page.id, x0=0.0, y0=0.0, x1=1.0, y1=1.0))
                facts_written += 1
                continue

            # A leftover row on one side can be positionally grouped under a
            # single row on the other (e.g. one waqf entry spanning two
            # property rows) — result.pairs then repeats that shared row
            # object across several pairs. Write each side's fields once
            # per DISTINCT row object, not once per pair, or the shared
            # side's facts get duplicated once per extra row it groups with.
            written_left_ids: set = set()
            written_right_ids: set = set()
            for left_raw_row, right_raw_row in result.pairs:
                # One joined pair = one logical row (see Fact.row_group_id).
                row_group_id = uuid4()
                for field_def in field_schema:
                    field_name = field_def["name"]
                    if field_name == serial_field:
                        continue

                    half = field_def.get("half")
                    raw_row = left_raw_row if half == "left" else right_raw_row
                    written_ids = written_left_ids if half == "left" else written_right_ids
                    row_identity = id(raw_row)
                    if (field_name, row_identity) in written_ids:
                        continue
                    written_ids.add((field_name, row_identity))
                    src_field = raw_row.get(field_name)
                    if not isinstance(src_field, dict) or not _valid_bbox(src_field.get("bbox")):
                        continue
                    value = src_field.get("value")
                    if value is None or value == "":
                        continue

                    src_confidence = src_field.get("confidence")
                    src_confidence = float(src_confidence) if src_confidence is not None else None
                    src_is_handwritten = bool(src_field.get("is_handwritten"))

                    fact = Fact(
                        tenant_id=tenant_id, document_id=document_id, version_id=version_id,
                        field_name=field_name,
                        value=value if isinstance(value, (dict, list)) else {"v": value},
                        confidence=src_confidence,
                        is_handwritten=src_is_handwritten,
                        status=classify_confidence(field_def, src_confidence, is_handwritten=src_is_handwritten),
                        row_group_id=row_group_id,
                    )
                    db.add(fact)
                    await db.flush()
                    x0, y0, x1, y1 = src_field["bbox"]
                    db.add(FactRegion(
                        tenant_id=tenant_id, fact_id=fact.id,
                        page_id=(left_page if half == "left" else right_page).id,
                        x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
                    ))
                    facts_written += 1

        except Exception as e:
            logger.warning(f"T26 spread extraction failed on pages {left_page_number}-{right_page_number}: {e}")
            continue

    return facts_written


ADJUDICATION_CONFIDENCE_THRESHOLD = 0.6

# Live bug, 2026-09-04: Chandra's /convert (chandra_provider.py) isn't
# instruction-following — it maps whatever table structure IT detected on
# a page onto our field list purely by column ORDER, with no idea some
# pages (a document's own cover/notice page, say) aren't this template's
# table at all. Confirmed live: a Wakf gazette's non-tabular cover page
# still produced a "row", with a Marathi legal-notice paragraph split
# across just 3 of the template's 14 fields (sr_no/wakf_name/sect) —
# nowhere close to the ~12-14 fields a genuine row of this table
# populates. Below this fraction of the template's row-level fields
# actually populated, a row is far more likely mis-mapped page content
# than a genuinely sparse real entry — forced into review rather than
# either silently trusted as ordinary machine-confidence data or
# silently discarded (a real row that happens to be this sparse still
# gets a human's eyes, never lost).
ROW_COVERAGE_REVIEW_THRESHOLD = 0.4


async def _write_stitch_ambiguous_fact(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, version_id: UUID,
    shape_hash: str, prev_pe: Dict[str, Any], pe: Dict[str, Any],
) -> None:
    """TS4 — skips writing a duplicate review item if this exact shape is
    already sitting unresolved in the queue (a batch of documents sharing
    one recurring ambiguous layout shouldn't flood the queue with N
    identical items before a human answers the first one)."""
    existing = await db.execute(
        select(Fact).where(
            Fact.tenant_id == tenant_id,
            Fact.field_name == "_stitch_ambiguous",
            Fact.status == "in_review",
            Fact.value["shape_hash"].astext == shape_hash,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        return

    fact = Fact(
        tenant_id=tenant_id, document_id=document_id, version_id=version_id,
        field_name="_stitch_ambiguous",
        value={
            "shape_hash": shape_hash,
            "page_a": prev_pe["page_number"],
            "page_b": pe["page_number"],
            "reason": "field-set overlap is neither clearly the same table continuing nor clearly disjoint column bands, and no confident answer was available",
        },
        confidence=None, is_handwritten=False, status="in_review",
    )
    db.add(fact)
    await db.flush()
    # Both pages get a full-page region so a reviewer can see page A and
    # page B side by side — previously only page A was ever recorded here
    # (page B lived only as a bare number in `value.page_b`), so even a
    # frontend built to compare the two pages had nothing to render for
    # the second one.
    db.add(FactRegion(tenant_id=tenant_id, fact_id=fact.id, page_id=prev_pe["page"].id, x0=0.0, y0=0.0, x1=1.0, y1=1.0))
    db.add(FactRegion(tenant_id=tenant_id, fact_id=fact.id, page_id=pe["page"].id, x0=0.0, y0=0.0, x1=1.0, y1=1.0))


async def _stitch_vertical_segments(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID,
    page_extractions: List[Dict[str, Any]],
    full_schema_fields: frozenset,
    exclude_fields: frozenset,
) -> List[List[Dict[str, Any]]]:
    """TS1 — groups consecutive per-page extractions into logical table
    segments (a segment = one or more pages that are the same table,
    continuing downward). A wide table split across pages no longer loses
    its tail: today, each page's continuation-merge only ever sees that
    one page's rows (see extract_facts_for_document's docstring history),
    so a page-1-ending / page-2-starting continuation row was silently
    treated as its own bogus record. This closes that gap.

    Evidence-certain decisions (see table_stitch.decide_relation) apply
    directly. Ambiguous pairs check the shape-hash cache first, then fall
    back to a narrow LLM adjudication call — never both attempted for the
    same shape twice. Adjudication unavailable (air-gapped, no local LLM,
    malformed response) or below-confidence defaults to NOT stitching —
    the safe, current behavior — rather than guessing, but (TS4) also
    writes a "_stitch_ambiguous" Fact so a human can answer it via
    POST /facts/{fact_id}/resolve-stitch-ambiguity. That answer is
    cached with decided_by='human' (table_shape_service.py), which
    outranks any future LLM guess for the same shape and is applied
    automatically to every future document with it — closing the loop
    the shape-hash cache's decided_by='human' column was built for but,
    before TS4, had no write path to ever reach.

    Deliberately vertical-only: a horizontal (sideways) relation detected
    here is left as separate segments, not auto-merged. Horizontal joins
    stay the explicit, template-declared "spread" layout path
    (_extract_spread_facts) — blending auto-detected sideways merges into
    every template would risk pairing two genuinely unrelated pages that
    happen to have complementary field coverage.
    """
    if len(page_extractions) <= 1:
        return [[pe] for pe in page_extractions]

    # T03 — sourced from sys_dg_config (migration 0041), falling back to
    # table_stitch's own module constants if the row is ever missing.
    vertical_threshold = await get_float(
        "table_stitch_vertical_similarity_threshold", table_stitch.VERTICAL_FIELD_SET_SIMILARITY_THRESHOLD
    )
    horizontal_min_coverage = await get_float(
        "table_stitch_horizontal_min_coverage", table_stitch.HORIZONTAL_MIN_COMBINED_COVERAGE
    )
    adjudication_confidence_threshold = await get_float(
        "table_stitch_adjudication_confidence_threshold", ADJUDICATION_CONFIDENCE_THRESHOLD
    )

    segments: List[List[Dict[str, Any]]] = [[page_extractions[0]]]
    for pe in page_extractions[1:]:
        prev_pe = segments[-1][-1]
        fields_a = table_stitch.field_set(prev_pe["rows"], exclude=exclude_fields)
        fields_b = table_stitch.field_set(pe["rows"], exclude=exclude_fields)
        decision = table_stitch.decide_relation(fields_a, fields_b, full_schema_fields, vertical_threshold, horizontal_min_coverage)
        relation = decision.relation

        if not decision.certain:
            s_hash = table_stitch.shape_hash(fields_a, fields_b)
            cached = await table_shape_service.get_cached_shape_decision(db, s_hash)
            if cached:
                relation = cached.relation
            else:
                llm = None
                try:
                    llm = get_llm_provider()
                except Exception as e:
                    logger.info(f"TS1 adjudication unavailable, treating page {pe['page_number']} as a new segment: {e}")
                verdict = await table_stitch.adjudicate_structure(llm, fields_a, fields_b, full_schema_fields) if llm else None
                if verdict and verdict.relation != "unrelated" and verdict.confidence >= adjudication_confidence_threshold:
                    relation = verdict.relation
                    await table_shape_service.record_shape_decision(db, s_hash, relation, decided_by="llm", confidence=verdict.confidence)
                else:
                    relation = "unrelated"
                    await _write_stitch_ambiguous_fact(db, tenant_id, document_id, version_id, s_hash, prev_pe, pe)

        if relation == "vertical":
            segments[-1].append(pe)
        else:
            segments.append([pe])

    return segments


# Chars of OCR text on a page below which "the VLM returned nothing" is
# treated as a genuinely blank page rather than a lost extraction.
# Deliberately low: a blank scan OCRs to ~0 chars, so anything above this
# means there WAS content the VLM should have seen.
VLM_EMPTY_PAGE_MIN_OCR_CHARS = 20


async def extract_facts_for_document(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID,
    file_bytes: bytes,
    filename: str,
    pages_text: List[dict],
    template: Optional[Template] = None,
    failed_pages_out: Optional[List[int]] = None,
    empty_pages_out: Optional[List[int]] = None,
) -> int:
    """Runs VLM extraction across a document's pages.
    Returns the number of facts written.

    `failed_pages_out`, when given, is filled with the page numbers whose
    VLM call RAISED (provider quota/timeout/connection error) rather than
    returning an unusable-but-parseable response. Live bug, 2026-09-22:
    those pages were caught per-page, logged at WARNING and skipped, and
    nothing downstream ever learned -- worker.py derives
    pages_failed_count from the OCR pages list alone, so a document that
    lost most of its pages at the VLM stage still finished as
    status="indexed" with pages_failed_count=0. Reporting them lets the
    caller record real data loss instead of silently shipping a partial
    extraction as a complete one.

    A page that legitimately yields no rows (a cover page, a blank) is
    NOT a failure and is deliberately not reported here.

    `empty_pages_out`, when given, is filled with the page numbers where
    the VLM returned a well-formed but EMPTY result for a page OCR did
    find text on. Live regression, 2026-09-22 (juni_masjid): nothing
    raised -- all 16 pages had a cached provider response -- but 8 came
    back as {"rows": [], "marginalia": []}, indistinguishable from a
    genuinely blank page. Those 8 silently cost 129 table facts and 183
    marginalia while the document still reported complete. OCR text on
    the page is the evidence something was there to extract; a truly
    blank page has neither and is correctly not reported."""
    vlm = get_vlm_provider()
    if vlm is None:
        return 0

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in RENDERABLE_EXTENSIONS:
        return 0

    if template:
        field_schema = template.field_schema
    else:
        field_schema = [
            {"name": "village_name", "type": "text", "description": "Village name (गाव)"},
            {"name": "taluka", "type": "text", "description": "Taluka (तालुका)"},
            {"name": "district", "type": "text", "description": "District (जिल्हा)"},
            {"name": "survey_number", "type": "text", "description": "Survey / Gut number (भूमापन क्रमांक)"},
            {"name": "landowners", "type": "text", "description": "Landowners (खातेदार / मालकाचे नाव)"},
        ]
    max_pages = min(len(pages_text), settings.vlm_max_pages_per_document)

    if template and template.layout == "spread":
        return await _extract_spread_facts(db, tenant_id, document_id, version_id, file_bytes, ext, vlm, field_schema, max_pages)

    prompt = _build_extraction_prompt(field_schema)
    serial_field = _find_role_field(field_schema, "serial")
    continuation_text_fields = [f["name"] for f in field_schema if f.get("role") == "continuation_text"]
    ditto_fields = [f["name"] for f in field_schema if f.get("ditto_eligible")]
    chain_anchor_field = _find_role_field(field_schema, "chain_anchor")
    blob_fields = [f["name"] for f in field_schema if f.get("type") == "blob"]
    header_fields = [f for f in field_schema if f.get("role") == "page_header"]
    # page_header fields never appear inside a row's field set either way
    # (they're asked for once per page, not per row) -- excluded here so
    # TS1's stitch-coverage comparison (decide_relation) isn't judged
    # against a denominator it can never actually reach.
    full_schema_fields = frozenset(f["name"] for f in field_schema if f.get("role") != "page_header")
    stitch_exclude_fields = frozenset({serial_field}) if serial_field else frozenset()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    facts_written = 0

    # Phase A — collect every page's raw VLM extraction. No Facts written
    # yet: whether page N's rows belong to the same table segment as page
    # N+1's isn't decided until every page in the run has been read.
    page_extractions: List[Dict[str, Any]] = []
    for page_number in range(1, max_pages + 1):
        try:
            if ext == "pdf":
                rendered = _render_pdf_page_png(file_bytes, page_number)
                if rendered is None:
                    continue
                png_bytes, width, height, rotation = rendered
            else:
                if page_number > 1:
                    break
                png_bytes, width, height, rotation = _render_image_page_png(file_bytes)

            rows, marginalia, page_header = await _call_vlm_with_parse_retry(db, vlm, file_hash, page_number, png_bytes, prompt)
            if not rows and not marginalia and not page_header:
                if empty_pages_out is not None:
                    ocr_page = pages_text[page_number - 1] if page_number <= len(pages_text) else {}
                    ocr_chars = len(((ocr_page or {}).get("text") or "").strip())
                    if ocr_chars >= VLM_EMPTY_PAGE_MIN_OCR_CHARS:
                        logger.warning(
                            f"T22 VLM returned no rows for page {page_number} of {filename}, "
                            f"but OCR found {ocr_chars} chars of text there -- extraction incomplete"
                        )
                        empty_pages_out.append(page_number)
                continue

            page = await _get_or_create_page(db, tenant_id, document_id, version_id, page_number, width, height, rotation)
            page_extractions.append({
                "page_number": page_number, "rows": rows, "marginalia": marginalia,
                "page_header": page_header, "page": page,
            })

        except Exception as e:
            # str(e) alone can be empty (httpx.ReadTimeout and friends
            # often carry no message, just a type) -- observed live on
            # Wardha.pdf: "T22 VLM extraction failed on page 5 of
            # Wardha.pdf: " with nothing after the colon, hiding whether
            # it was a timeout, a connection error, or something else.
            logger.warning(f"T22 VLM extraction failed on page {page_number} of {filename}: {type(e).__name__}: {e}")
            if failed_pages_out is not None:
                failed_pages_out.append(page_number)
            continue

    # Idempotency guard, deliberately placed HERE and not before Phase A.
    #
    # Re-running extraction against a document that already holds Facts
    # INSERTs a second full set rather than replacing them -- the hazard
    # scripts/retry_wardha_extraction.py has warned about in a docstring
    # since 2026-09-02 ("Delete existing doc_dg_facts rows for the document
    # first"), enforced nowhere until now.
    #
    # The first version of this guard purged BEFORE Phase A ran, and cost a
    # real document its extraction on 2026-09-23: every page failed on
    # provider quota, nothing replaced what had been deleted, and
    # juni_masjid went from 392 facts to the 8 a human had verified. Purging
    # only once Phase A has actually produced something means a total
    # provider outage now leaves the existing extraction untouched.
    #
    # Residual risk, stated rather than hidden: a PARTIAL extraction (some
    # pages succeeded, some failed) still replaces a fuller previous run.
    # That is visible rather than silent -- failed and empty pages are
    # reported via failed_pages_out/empty_pages_out and recorded on the
    # document -- but it is not prevented here.
    #
    # Human-verified facts are always preserved: re-extraction must never
    # destroy a reviewer's sign-off.
    if page_extractions:
        await db.execute(
            delete(Fact).where(
                Fact.document_id == document_id,
                Fact.version_id == version_id,
                Fact.verified_by_actor_id.is_(None),
            )
        )
    else:
        logger.warning(
            f"T22 extraction produced nothing for {filename} — leaving the "
            f"existing {'facts' if failed_pages_out else 'extraction'} in place "
            f"rather than replacing it with an empty result"
        )

    # Phase B (TS1) — decide which consecutive pages are the same table
    # continuing downward, so a continuation row that starts a fresh page
    # merges into the entry it actually continues rather than becoming a
    # stray half-record.
    segments = await _stitch_vertical_segments(db, tenant_id, document_id, version_id, page_extractions, full_schema_fields, stitch_exclude_fields)

    # Phase C — per segment: concatenate its pages' rows in reading order,
    # then run the existing continuation-merge/ditto/blob-parse handlers
    # over that combined list exactly as before, just no longer bounded
    # to a single page. Each source row still remembers its own physical
    # page for FactRegion linkage (region_page_map), since a merged fact
    # can now legitimately span more than one page.
    for segment in segments:
        rows_all: List[Dict[str, Any]] = []
        region_page_map: List[DocumentPage] = []
        for pe in segment:
            for row in pe["rows"]:
                rows_all.append(row)
                region_page_map.append(pe["page"])

        if serial_field:
            plain_rows = [
                {k: (v.get("value") if isinstance(v, dict) else v) for k, v in row.items()}
                for row in rows_all
            ]
            for plain, row in zip(plain_rows, rows_all):
                plain["no"] = plain.pop(serial_field, None)
            try:
                merged_plain = merge_continuation_rows(plain_rows, text_columns=continuation_text_fields or None)
            except ValueError as e:
                logger.warning(f"T22/TS1 continuation-merge skipped for a segment starting at page {segment[0]['page_number']}: {e}")
                merged_plain = [{**p, "_source_row_indices": [i]} for i, p in enumerate(plain_rows)]
        else:
            merged_plain = [{**{k: (v.get("value") if isinstance(v, dict) else v) for k, v in row.items()}, "_source_row_indices": [i]} for i, row in enumerate(rows_all)]

        if ditto_fields:
            try:
                merged_plain = expand_ditto_chains(merged_plain, ditto_fields, chain_anchor_column=chain_anchor_field)
            except Exception as e:
                logger.warning(f"T22 ditto-chain expansion skipped for a segment starting at page {segment[0]['page_number']}: {e}")

        for merged_row in merged_plain:
            source_indices = merged_row.get("_source_row_indices", [])
            if not source_indices:
                continue

            # Real row identity, known here and nowhere else downstream —
            # every Fact this merged_row writes shares it, so
            # get_table_view_for_document can group by it exactly instead
            # of guessing from FactRegion.y0 (see Fact.row_group_id).
            row_group_id = uuid4()

            # T22 quality gate (see ROW_COVERAGE_REVIEW_THRESHOLD) — how
            # much of this template's row-level schema this merged row
            # actually populated, regardless of per-field confidence.
            populated_field_count = sum(
                1 for name in full_schema_fields
                if merged_row.get("no" if name == serial_field else name) not in (None, "")
            )
            row_is_low_coverage = (
                bool(full_schema_fields)
                and (populated_field_count / len(full_schema_fields)) < ROW_COVERAGE_REVIEW_THRESHOLD
            )

            for field_def in field_schema:
                field_name = field_def["name"]
                if field_def.get("role") == "page_header":
                    continue
                row_key = "no" if field_name == serial_field else field_name
                if row_key not in merged_row:
                    continue
                value = merged_row[row_key]
                if value is None or value == "":
                    continue

                confidences = []
                region_boxes = []  # (bbox, DocumentPage) — a stitched fact can span pages
                field_is_handwritten = False
                for src_idx in source_indices:
                    if src_idx >= len(rows_all):
                        continue
                    src_field = rows_all[src_idx].get(field_name)
                    if isinstance(src_field, dict) and _valid_bbox(src_field.get("bbox")):
                        region_boxes.append((src_field["bbox"], region_page_map[src_idx]))
                        if src_field.get("confidence") is not None:
                            confidences.append(float(src_field["confidence"]))
                        if src_field.get("is_handwritten"):
                            field_is_handwritten = True
                if not region_boxes:
                    continue

                fact_value: Any = value
                if field_name in blob_fields and isinstance(value, str):
                    parsed = parse_blob_cell(value)
                    fact_value = {
                        "raw_text": parsed.raw_text,
                        "survey_numbers": parsed.survey_numbers,
                        "cts": parsed.cts,
                        "area_sqm": parsed.area_sqm,
                        "built_sqm": parsed.built_sqm,
                        "open_space_sqm": parsed.open_space_sqm,
                        "flags": parsed.flags,
                    }

                fact_confidence = (sum(confidences) / len(confidences)) if confidences else None
                fact_status = classify_confidence(field_def, fact_confidence, is_handwritten=field_is_handwritten)
                if row_is_low_coverage:
                    fact_status = "in_review"

                # TS5 — ditto-filled fields carry both the resolved value
                # and the literal mark that was actually read; a mark
                # with a broken chain (no valid value above to copy) is
                # never silently guessed — it's forced into review instead
                # of whatever confidence banding would otherwise apply.
                ditto_verbatim = merged_row.get("_ditto_verbatim", {}).get(field_name)
                is_unresolved_ditto = field_name in merged_row.get("_unresolved_ditto_columns", [])
                if ditto_verbatim is not None:
                    fact_value = {
                        "v": fact_value,
                        "verbatim": ditto_verbatim,
                        "was_ditto_filled": not is_unresolved_ditto,
                    }
                    if is_unresolved_ditto:
                        fact_value["ditto_unresolved"] = True
                        fact_status = "in_review"

                fact = Fact(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                    field_name=field_name,
                    value=fact_value if isinstance(fact_value, (dict, list)) else {"v": fact_value},
                    confidence=fact_confidence,
                    is_handwritten=field_is_handwritten,
                    status=fact_status,
                    row_group_id=row_group_id,
                )
                db.add(fact)
                await db.flush()

                for box, box_page in region_boxes:
                    x0, y0, x1, y1 = box
                    db.add(FactRegion(
                        tenant_id=tenant_id, fact_id=fact.id, page_id=box_page.id,
                        x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
                    ))
                facts_written += 1

        # page_header fields (village/taluka/district on a 7/12 record,
        # etc.) never appear inside merged_plain's row keys -- they're
        # asked for once per page, in the header area, not per row (see
        # the "role": "page_header" convention note above) -- so they're
        # written separately here instead of via the per-row loop above.
        # One Fact per header field per segment, sourced from the first
        # page in the segment that actually reported it; a continuation
        # page legitimately omits it (the prompt says so), so later pages
        # are a fallback, not an error.
        for field_def in header_fields:
            field_name = field_def["name"]
            src_field = None
            src_page = None
            for pe in segment:
                candidate = pe.get("page_header", {}).get(field_name)
                if isinstance(candidate, dict) and _valid_bbox(candidate.get("bbox")):
                    src_field = candidate
                    src_page = pe["page"]
                    break
            if src_field is None:
                continue
            value = src_field.get("value")
            if value is None or value == "":
                continue

            header_confidence = src_field.get("confidence")
            header_confidence = float(header_confidence) if header_confidence is not None else None
            header_is_handwritten = bool(src_field.get("is_handwritten"))

            fact = Fact(
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
                field_name=field_name,
                value=value if isinstance(value, (dict, list)) else {"v": value},
                confidence=header_confidence,
                is_handwritten=header_is_handwritten,
                status=classify_confidence(field_def, header_confidence, is_handwritten=header_is_handwritten),
            )
            db.add(fact)
            await db.flush()
            x0, y0, x1, y1 = src_field["bbox"]
            db.add(FactRegion(
                tenant_id=tenant_id, fact_id=fact.id, page_id=src_page.id,
                x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
            ))
            facts_written += 1

        # T30 — marginalia: handwritten notes that aren't an answer to any
        # schema field. Reuses Fact+FactRegion (T06's click-through contract)
        # rather than a separate table; field_name="_marginalia" is the
        # sentinel get_adjudication_queue's 'marginalia' category filters on.
        # Always in_review — there's no confidence band for "is this note
        # important," a human reads every one. Stays per-original-page:
        # a marginal note belongs to the physical page it's written on
        # regardless of table stitching.
        for pe in segment:
            for note in pe["marginalia"]:
                text = note.get("text")
                bbox = note.get("bbox")
                if not text or not _valid_bbox(bbox):
                    continue
                fact = Fact(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                    field_name="_marginalia",
                    value={"v": text},
                    confidence=None,
                    is_handwritten=True,
                    status="in_review",
                )
                db.add(fact)
                await db.flush()
                x0, y0, x1, y1 = bbox
                db.add(FactRegion(
                    tenant_id=tenant_id, fact_id=fact.id, page_id=pe["page"].id,
                    x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
                ))
                facts_written += 1

    return facts_written
