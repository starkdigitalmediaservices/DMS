from typing import Any, Dict, List, Optional

# Common ways a register marks "same as the row above" instead of repeating
# the value (Section 0: ditto mark). Matched case-insensitively with a
# trailing period stripped (TS5: real registers write "Do.", "Do", "DO.",
# not just the lowercase bare "do" this originally only matched — a real
# bug found by testing against an actual 1973 Maharashtra Waqf Board
# gazette table, which uses exactly this "Do." convention).
DITTO_MARKS = {",,", '"', "do", "-do-", "″", "''"}


def _normalize_mark(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized.endswith("."):
        normalized = normalized[:-1]
    return normalized


def _is_ditto(raw: Any) -> bool:
    normalized = _normalize_mark(raw)
    return normalized is not None and normalized in DITTO_MARKS


def expand_ditto_chains(rows: List[Dict[str, Any]], columns: List[str], chain_anchor_column: Optional[str] = None) -> List[Dict[str, Any]]:
    """Handler 2 — expand ditto marks per column (Section 4).

    Each column has its own chain: a ditto mark copies the nearest
    non-ditto value above it *in that column*, independent of what the
    other columns are doing in the same row.

    Per row, three things are recorded (TS5):
      _inherited_columns        — columns successfully filled from above
      _ditto_verbatim            — {column: the literal mark as read, e.g. "Do."}
      _unresolved_ditto_columns — columns whose ditto mark had nothing
                                   valid above to copy (a broken source
                                   chain — the segment's first row, or a
                                   chain reset by chain_anchor_column
                                   below). Never silently guessed; the
                                   caller flags these for human review
                                   instead of raising and losing the
                                   entire segment's ditto resolution over
                                   one broken cell.

    chain_anchor_column (opt-in, unused by any currently-registered
    template — no real register has confirmed which column, if any,
    should play this role; see docs/planning/TS_backlog_colleague_features.md TS5):
    when a template declares one, a genuine (non-ditto) change in that
    column's value resets EVERY column's chain, not just its own —
    modeling "a new anchor context starts here, don't carry the old
    context's peripheral values forward." Deliberately NOT inferred from
    a column literally named "village": the existing worked example
    below (test_ditto_chain_expands_per_column_independently) shows a
    real case where a village change should NOT reset khatedar (the same
    owner can hold land in more than one village) — a blanket rule would
    have been wrong there. Only fires when a caller explicitly opts in.

    `rows` is mutated into new dicts; the input is left untouched.
    """
    last_value: Dict[str, Any] = {}
    anchor_last_value: Any = None
    anchor_initialized = False
    out: List[Dict[str, Any]] = []

    for row in rows:
        new_row = dict(row)
        inherited: List[str] = []
        unresolved: List[str] = []
        verbatim: Dict[str, str] = {}

        if chain_anchor_column:
            anchor_raw = row.get(chain_anchor_column)
            if not _is_ditto(anchor_raw) and anchor_raw not in (None, ""):
                if anchor_initialized and anchor_raw != anchor_last_value:
                    last_value = {}  # break: a new anchor context starts here
                anchor_last_value = anchor_raw
                anchor_initialized = True

        for col in columns:
            raw = row.get(col)
            if _is_ditto(raw):
                verbatim[col] = raw
                if col in last_value:
                    new_row[col] = last_value[col]
                    inherited.append(col)
                else:
                    unresolved.append(col)
            else:
                last_value[col] = raw

        new_row["_inherited_columns"] = inherited
        new_row["_unresolved_ditto_columns"] = unresolved
        new_row["_ditto_verbatim"] = verbatim
        out.append(new_row)

    return out
