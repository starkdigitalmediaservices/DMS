"""TS1 — two-axis table stitching engine.

Ported as a *concept*, not code, from a colleague's separate waqf
feature-stitching project (see docs/planning/TS_backlog_colleague_features.md) — but
adapted to how this system actually extracts data. That project detects
table structure from raw OCR (no schema up front, so it reads printed
column numbers like "(1)...(19)" off headers to know which columns a page
fragment covers). This system is schema-first (T24): a Template's
field_schema is known before extraction, and the VLM extraction prompt
(vlm_extraction.py) already omits a field's key entirely when it isn't
visible on a given page. That omission IS our evidence layer — the set of
field names that came back with a value on a page already tells us which
columns were visible there, without inventing a printed-number convention
we have no real document to confirm (T25/T31/T32 stay blocked on A1 for
exactly this reason). So: field-name-set comparison stands in for header
text/column-number comparison throughout this module.

Three layers, cheapest and most trustworthy first, same as the source
project:
  1. Evidence — field_set() reads which columns a page fragment covers,
     for free, from data extraction already produced.
  2. Decision — decide_relation() is deterministic: near-equal field sets
     mean the same table continuing downward; disjoint sets covering the
     template together mean two column-bands of one row, sideways.
     Anything in between is ambiguous, not guessed at.
  3. Adjudication — adjudicate_structure() asks a narrow, structure-only
     question (field names only, never cell content) to the local-model
     factory, only for the ambiguous leftovers, cached by shape hash so a
     recurring form layout is answered once.

The pre-adjudication veto lives in match_rows_by_key /
pair_leftovers_by_position, not in the LLM prompt: row-pairing is always
resolved deterministically (exact key match, then bbox-containment
grouping for the leftovers) before any adjudication call, and a pairing
that can't be reconciled deterministically is refused outright regardless
of what an adjudicator might say about the surrounding relation — mirrors
the source project's documented regression (an LLM asked about a
headerless continuation once guessed "sideways" and corrupted a downward
continuation into bogus columns) by never asking the question in a
context where a wrong answer could touch row pairing at all.

2026-08-28 — adjudicate_structure() additionally ports one narrow,
implementation-level fix directly from that source project's stitch_llm.py
(explicitly authorized by the user for this one piece, after a structural
comparison of both codebases showed the rest doesn't transfer — see
[[feedback_colleague_project_inspiration_only]]): retrying a failed/
unparseable adjudication call up to ADJUDICATION_ATTEMPTS times, and
stripping a reasoning model's <think> preamble before parsing JSON. Our
adjudication LLM (groq_llm_model, currently openai/gpt-oss-120b) is exactly
the class of model their comment documents hitting this failure mode on.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

VERTICAL_FIELD_SET_SIMILARITY_THRESHOLD = 0.7
HORIZONTAL_MIN_COMBINED_COVERAGE = 0.5


def field_set(rows: List[Dict[str, Any]], exclude: FrozenSet[str] = frozenset()) -> FrozenSet[str]:
    """Which field names carry a non-empty value anywhere in these rows —
    the evidence layer. `rows` are the raw VLM-extraction row dicts
    (field_name -> {"value":..., "bbox":...}), same shape vlm_extraction.py
    already works with."""
    present = set()
    for row in rows:
        for name, field_data in row.items():
            if name in exclude:
                continue
            value = field_data.get("value") if isinstance(field_data, dict) else field_data
            if value not in (None, ""):
                present.add(name)
    return frozenset(present)


def jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def shape_hash(fields_a: FrozenSet[str], fields_b: FrozenSet[str]) -> str:
    """Keys the adjudication/human-decision cache on the STRUCTURE of the
    two fragments (which fields each carries), not the document — so the
    same recurring form layout gets asked once, per the source project's
    'first document of a new type asks a few questions, the tenth asks
    none' caching pattern."""
    canonical = json.dumps({"a": sorted(fields_a), "b": sorted(fields_b)}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class DecisionResult:
    relation: str  # "vertical" | "horizontal" | "ambiguous"
    certain: bool  # True = evidence layer settled it, no adjudication needed
    reason: str


def decide_relation(
    fields_a: FrozenSet[str],
    fields_b: FrozenSet[str],
    full_schema_fields: FrozenSet[str],
    vertical_threshold: float = VERTICAL_FIELD_SET_SIMILARITY_THRESHOLD,
    horizontal_min_coverage: float = HORIZONTAL_MIN_COMBINED_COVERAGE,
) -> DecisionResult:
    """The decision layer. Pure and deterministic — never guesses; returns
    "ambiguous" rather than picking a side when the evidence doesn't
    clearly settle it, leaving that case for adjudicate_structure().

    `vertical_threshold`/`horizontal_min_coverage` default to this module's
    constants so every existing caller and test is unaffected; T03's async
    caller (vlm_extraction.py) passes the live sys_dg_config values instead
    — kept as parameters rather than an in-module config lookup so this
    stays a pure, synchronous, DB-free function."""
    if not fields_b:
        # A fragment with no field values at all (every cell blank/omitted)
        # can't be distinguished from a genuine continuation by field-set
        # alone — matches the source project's "matching column count with
        # no headers" case. Treated as a likely continuation; the caller
        # still runs it through the same row-pairing logic, so a wrong
        # guess here just produces an empty merge, not a corrupted one.
        return DecisionResult("vertical", certain=True, reason="second fragment has no field evidence — treated as a headerless continuation")

    similarity = jaccard(fields_a, fields_b)
    if similarity >= vertical_threshold:
        return DecisionResult("vertical", certain=True, reason=f"field sets {similarity:.0%} similar — same table continuing")

    overlap = fields_a & fields_b
    combined = fields_a | fields_b
    coverage = (len(combined) / len(full_schema_fields)) if full_schema_fields else 0.0
    if not overlap and coverage >= horizontal_min_coverage:
        return DecisionResult("horizontal", certain=True, reason=f"disjoint field sets together cover {coverage:.0%} of the template — column bands of one row")

    return DecisionResult("ambiguous", certain=False, reason=f"field sets partially overlap ({similarity:.0%} similar, {coverage:.0%} combined template coverage) — not clearly either")


@dataclass
class AdjudicationVerdict:
    relation: str  # "vertical" | "horizontal" | "unrelated"
    confidence: float
    raw_reason: str


ADJUDICATION_SYSTEM_PROMPT = (
    "You are deciding how two page fragments of a scanned register relate to each other. "
    "You are told ONLY the column/field names each fragment has readable values for — never "
    "the actual data. Answer strictly one of: \"vertical\" (fragment B is the same table "
    "continuing onto the next page, same columns), \"horizontal\" (fragment B holds different "
    "columns of the same rows as fragment A, e.g. a wide table split left/right), or "
    "\"unrelated\" (they don't belong together). Reply with ONLY JSON: "
    '{"relation": "vertical"|"horizontal"|"unrelated", "confidence": 0.0-1.0, "reason": "<short>"}'
)


def build_adjudication_prompt(fields_a: FrozenSet[str], fields_b: FrozenSet[str], full_schema_fields: FrozenSet[str]) -> str:
    return (
        f"Full template column set: {sorted(full_schema_fields)}\n"
        f"Fragment A has values for: {sorted(fields_a)}\n"
        f"Fragment B has values for: {sorted(fields_b)}\n"
    )


ADJUDICATION_ATTEMPTS = 3


def _braced(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else ""


def _parse_adjudication_response(raw: str) -> Optional[dict]:
    """Our adjudication LLM (groq_llm_model, currently openai/gpt-oss-120b)
    is a reasoning model: it sometimes prefixes its reply with a <think>
    block or other preamble, which makes the whole reply invalid JSON even
    when the answer inside it is perfectly good. Try the reply as-is, then
    fall back to the last brace-delimited JSON object in it, same pattern
    the source project's stitch_llm.py uses for the identical failure mode
    on the identical class of model."""
    text = (raw or "").strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    for candidate in (text, _braced(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("relation") in ("vertical", "horizontal", "unrelated"):
            return parsed
    return None


async def adjudicate_structure(llm, fields_a: FrozenSet[str], fields_b: FrozenSet[str], full_schema_fields: FrozenSet[str]) -> Optional[AdjudicationVerdict]:
    """Structure-only adjudication via the local-model factory. Returns
    None (never raises) on any failure — including AirGappedViolation,
    since a missing local LLM must degrade to 'leave ambiguous, flag for
    review' rather than take down extraction (T22's existing best-effort
    contract: a VLM/LLM failure never fails ingestion).

    Retries up to ADJUDICATION_ATTEMPTS times: the same question doesn't
    always come back readable even at temperature 0 (a reasoning model can
    return unparseable chatter on one attempt and a clean verdict on the
    next) — a single try silently turns a flaky reply into "ambiguous,
    ask a human" for a case a retry would have resolved on its own."""
    from app.ai.base import Message

    prompt = build_adjudication_prompt(fields_a, fields_b, full_schema_fields)
    for attempt in range(ADJUDICATION_ATTEMPTS):
        try:
            raw = await llm.complete(
                [Message(role="system", content=ADJUDICATION_SYSTEM_PROMPT), Message(role="user", content=prompt)],
                temperature=0.0,
                max_tokens=200,
            )
            data = _parse_adjudication_response(raw)
            if data is None:
                continue
            return AdjudicationVerdict(
                relation=data["relation"],
                confidence=float(data.get("confidence", 0.0)),
                raw_reason=str(data.get("reason", "")),
            )
        except Exception:
            continue
    return None


def match_rows_by_key(left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]], key_field: str) -> Tuple[List[Tuple[Dict, Dict]], List[Dict], List[Dict]]:
    """Exact-key matching — the reliable fast path, same idea as the
    existing join_spread() (T26) but returning leftovers instead of
    failing outright on any disagreement. Returns (matched_pairs,
    left_only, right_only)."""
    def _key_value(row: Dict[str, Any]) -> Any:
        v = row.get(key_field)
        v = v.get("value") if isinstance(v, dict) else v
        # A serial/entry-number field never holds a bare ditto mark or an
        # unrelated word — treat one as no key at all rather than a real
        # value, so two placeholder marks on both sides (e.g. both rows
        # happen to read "..") never get treated as a genuine key match.
        return v if _looks_like_a_key_value(v) else None

    left_by_key: Dict[Any, Dict] = {}
    left_only: List[Dict] = []
    for row in left_rows:
        k = _key_value(row)
        if k in (None, ""):
            left_only.append(row)
        else:
            left_by_key[k] = row

    matched: List[Tuple[Dict, Dict]] = []
    right_only: List[Dict] = []
    used_keys = set()
    for row in right_rows:
        k = _key_value(row)
        if k in (None, "") or k not in left_by_key:
            right_only.append(row)
        else:
            matched.append((left_by_key[k], row))
            used_keys.add(k)

    left_only.extend(row for k, row in left_by_key.items() if k not in used_keys)
    return matched, left_only, right_only


def _row_vertical_span(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    # A field the VLM has no value for should omit its key entirely, but
    # a model doesn't always comply — observed live on a real 1973
    # gazette page: a null-value field came back as
    # {"bbox": [null, null, null, null], ...} instead of being omitted.
    # A 4-null list is still a truthy, length-4 list, so it must be
    # checked for real numbers, not just presence/length.
    y0s, y1s = [], []
    for field_data in row.values():
        if not isinstance(field_data, dict):
            continue
        bbox = field_data.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            y0s.append(float(bbox[1]))
            y1s.append(float(bbox[3]))
    if not y0s:
        return None
    return min(y0s), max(y1s)


def pair_leftovers_by_position(left_only: List[Dict], right_only: List[Dict]) -> Optional[Dict[int, List[int]]]:
    """Positional reconciliation for rows an exact key match couldn't pair
    — e.g. one waqf entry lists two properties, so one side has an extra
    row the other doesn't. Groups by vertical bbox containment: whichever
    side has fewer leftover rows is treated as the "container" side, and
    each row on the other side is assigned to whichever container's
    vertical span its own center falls inside.

    Returns None (refuse to merge — the pre-adjudication veto) if either
    side is missing bbox data, or if any row's center doesn't fall inside
    exactly one container's span. Never guesses a pairing it isn't sure of.
    """
    if not left_only and not right_only:
        return {}
    if not left_only or not right_only:
        return None  # a genuinely unpaired remainder on only one side — nothing to contain it

    containers, contained = (left_only, right_only) if len(left_only) <= len(right_only) else (right_only, left_only)
    container_spans = [_row_vertical_span(r) for r in containers]
    if any(s is None for s in container_spans):
        return None

    groups: Dict[int, List[int]] = {i: [] for i in range(len(containers))}
    for c_idx, row in enumerate(contained):
        span = _row_vertical_span(row)
        if span is None:
            return None
        center = (span[0] + span[1]) / 2
        matches = [i for i, cs in enumerate(container_spans) if cs[0] <= center <= cs[1]]
        if len(matches) != 1:
            return None
        groups[matches[0]].append(c_idx)

    if len(left_only) <= len(right_only):
        return groups
    # Swapped above — invert back so keys are always left-side indices.
    inverted: Dict[int, List[int]] = {}
    for right_idx, left_indices in groups.items():
        for left_idx in left_indices:
            inverted.setdefault(left_idx, []).append(right_idx)
    return inverted


@dataclass
class HorizontalJoinResult:
    status: str  # "ok" | "needs_review"
    pairs: List[Tuple[Dict, Dict]] = field(default_factory=list)
    reason: str = ""


_HAS_DIGIT_RE = re.compile(r"\d")


def _looks_like_a_key_value(v: Any) -> bool:
    """Whether a value is plausibly a real serial/entry-number identifier,
    not noise. Real bug found live 2026-09-03: a register's right-hand
    band genuinely has no serial column at all — its first real column is
    village-name/ditto data — but a column-position-mapped extraction
    still lands SOME text in the field asked for matching, e.g. ".."
    (a ditto mark) or "Kanadgaon" (a place name). Neither is remotely a
    serial number, but both are non-empty strings, so a plain "is there
    text here" check can't tell them apart from a real one.

    Every serial/entry-number convention actually seen in this system's
    real documents contains a digit somewhere — plain ("180"), decorated
    ("180."), or alphanumeric ("B-175", "WB-1") — while a ditto mark is
    pure punctuation and a place name is pure letters. Requiring a digit
    is a safe, general test across all of those, without hardcoding any
    single format (e.g. "must start with a digit" would wrongly reject
    "B-175")."""
    if not isinstance(v, str) or not v.strip():
        return False
    return bool(_HAS_DIGIT_RE.search(v))


def _any_key_value(rows: List[Dict[str, Any]], key_field: str) -> bool:
    """Whether this side genuinely carries the key field, not just noise.

    Real bug found live 2026-09-03 against an actual spread document: a
    register whose right-hand band never prints the serial number at all
    (a real, expected "structural absence" this module already has a
    fallback for) had exactly ONE row where the VLM had misextracted a
    village name into the sr_no slot — 14 of 15 rows correctly blank, one
    stray non-empty value. That single value was enough for the old
    "any row has something" check to conclude "both sides have real keys,
    this is a genuine conflict," which skipped the position-based fallback
    entirely and produced a wrong refusal on a case the fallback was
    explicitly built to handle. A single noisy extraction on an otherwise-
    blank side should never look like a side "genuinely carries" the
    field — require a real majority instead of just one row.

    Extended the same day, still live against the same document: fixing
    the majority check alone wasn't enough once a different VLM's column-
    position mapping filled EVERY row on that side with something
    non-empty (ditto marks, an unrelated place name) — a real majority of
    noise still isn't evidence the field is real. Both checks now require
    the value to actually look like a key (_looks_like_a_key_value), not
    merely be non-empty."""
    real = 0
    for row in rows:
        v = row.get(key_field)
        v = v.get("value") if isinstance(v, dict) else v
        if _looks_like_a_key_value(v):
            real += 1
    return real > len(rows) / 2


def join_rows_horizontally(left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]], key_field: str) -> HorizontalJoinResult:
    """Generalizes join_spread() (T26): exact-key matches join directly;
    leftover rows on either side (uneven counts) are reconciled by bbox
    position rather than failing the whole page pair.

    A real printed gazette register often doesn't repeat the row-number
    column on a continuation band at all — every row on that side is
    "keyless" by the form's own convention, not by disagreement. That's
    a structural absence, reconciled entirely by bbox position (still
    refusing if bbox data isn't available). It's kept strictly separate
    from a genuine key CONFLICT (both sides have real, non-matching key
    values, e.g. row "1" vs row "2") — position must never override
    explicit disagreeing evidence, only ever fill in evidence that was
    never there to begin with."""
    matched, left_only, right_only = match_rows_by_key(left_rows, right_rows, key_field)

    if not matched:
        left_has_keys = _any_key_value(left_rows, key_field)
        right_has_keys = _any_key_value(right_rows, key_field)
        if left_has_keys and right_has_keys:
            # Both sides gave real key values and none of them agree —
            # a genuine conflict, not a structural absence. Never
            # positionally paper over this.
            return HorizontalJoinResult(
                status="needs_review",
                reason=f"'{key_field}' values on each side disagree entirely — no shared value between the two fragments",
            )
        groups = pair_leftovers_by_position(left_rows, right_rows)
        if groups is None:
            # Real case found live 2026-09-03, same document as the fix
            # above: bbox containment can fail even with clean, genuine
            # per-row boxes on both sides — a real scanned register's two
            # facing pages don't always keep perfectly identical row
            # heights down the page, so position drifts (row 3 landing in
            # row 2's box, row 17 in row 16's) without either side's boxes
            # being wrong. When both sides have the SAME row count and
            # neither has usable keys, equal counts is itself strong
            # evidence they're the same rows in the same top-to-bottom
            # order — for two column-bands of one wide table, that's
            # exactly what "same count" means. Rank order is a more
            # robust anchor than pixel position for this one case.
            if len(left_rows) == len(right_rows):
                pairs = list(zip(left_rows, right_rows))
                return HorizontalJoinResult(status="ok", pairs=pairs)
            return HorizontalJoinResult(
                status="needs_review",
                reason=f"no shared '{key_field}' values between the two fragments and no reliable position data to pair by",
            )
        pairs = [(left_rows[left_idx], right_rows[right_idx]) for left_idx, right_indices in groups.items() for right_idx in right_indices]
        return HorizontalJoinResult(status="ok", pairs=pairs)

    pairs = list(matched)

    if left_only or right_only:
        groups = pair_leftovers_by_position(left_only, right_only)
        if groups is None:
            return HorizontalJoinResult(
                status="needs_review",
                reason=(
                    f"{len(left_only)} row(s) on one side and {len(right_only)} on the other "
                    f"couldn't be matched by '{key_field}' and have no reliable position data to pair by"
                ),
            )
        for left_idx, right_indices in groups.items():
            for right_idx in right_indices:
                pairs.append((left_only[left_idx], right_only[right_idx]))

    return HorizontalJoinResult(status="ok", pairs=pairs)
