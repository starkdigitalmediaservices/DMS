"""TS6 — page-furniture detection by position-stability: flags a
running header/footer candidate because it recurs at a stable vertical
position across pages, never because it merely repeats. Detection only
— nothing here deletes or filters anything from chunks/search; that's
deliberate (see module docstring below for why repetition alone is
unsafe).

No suppression pipeline exists anywhere in this codebase to retrofit
(confirmed by search before building this — see
docs/planning/TS_backlog_colleague_features.md TS6), so this is new infrastructure,
not a bug fix. Position is approximated from a LINE'S ORDER within its
own page's text rather than a literal pixel Y-coordinate: no OCR
provider in this pipeline (pdfplumber/tesseract/paddleocr) currently
persists per-line bounding boxes past extraction (only flat page text
survives into `pages`), and extending three providers to emit real
coordinates is a materially larger change than this item's own scope.
Line order is still a real, available positional signal — the Nth line
of M on a page reliably sits near the top or bottom of that page for
any reasonably-formatted document — just an approximation of pixel
position rather than the thing itself.

Repetition-only filtering has a real documented failure mode: content
that legitimately repeats at a DIFFERENT depth on every page (e.g. a
court filing's cause-title appearing after a variable amount of prior
text) looks identical to a stable running header by content alone, but
its position is unstable — checking for stable position, not just
repeated content, is what tells them apart.
"""
from typing import Any, Dict, List, Tuple

DEFAULT_MIN_OCCURRENCES = 3
DEFAULT_MAX_POSITION_SPREAD = 0.15
DEFAULT_MARGIN = 0.10
MIN_LINE_LENGTH = 3


def _split_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_line(line: str) -> str:
    return " ".join(line.split()).lower()


def detect_page_furniture(
    pages: List[Dict[str, Any]],
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    max_position_spread: float = DEFAULT_MAX_POSITION_SPREAD,
    margin: float = DEFAULT_MARGIN,
) -> List[Dict[str, Any]]:
    """Only lines already sitting within the top/bottom `margin` fraction
    of their own page are even considered — a repeated line of genuine
    body text never gets flagged no matter how often it recurs, since a
    running header/footer only ever means something printed in the
    margin, and only ever means something that recurs across SEPARATE
    pages — not repeated lines within one page (a table's own repeated
    cell content, e.g. TS5's ditto marks, is a real, different
    phenomenon this must not be confused with; caught by testing this
    against a real scanned register page whose ditto column happened to
    read as a same-page false positive before this dedup existed).
    `pages` is extractor.py's shape: {"page_number", "text",
    "extraction_failed"}."""
    # normalized text -> {page_number: (position, verbatim)} -- at most
    # one occurrence recorded per page, so a line repeated many times on
    # a single page never inflates occurrence_count.
    occurrences: Dict[str, Dict[int, Tuple[float, str]]] = {}

    for page in pages:
        if page.get("extraction_failed"):
            continue
        page_number = page.get("page_number", 0)
        lines = _split_lines(page.get("text", ""))
        n = len(lines)
        if n < 2:
            continue
        for idx, line in enumerate(lines):
            position = idx / (n - 1)
            if margin < position < 1 - margin:
                continue
            normalized = _normalize_line(line)
            if len(normalized) < MIN_LINE_LENGTH:
                continue
            by_page = occurrences.setdefault(normalized, {})
            if page_number not in by_page:
                by_page[page_number] = (position, line)

    candidates: List[Dict[str, Any]] = []
    for normalized, by_page in occurrences.items():
        if len(by_page) < min_occurrences:
            continue
        pages_sorted = sorted(by_page.items())  # [(page_number, (position, verbatim)), ...]
        positions = [pos for _, (pos, _) in pages_sorted]
        spread = max(positions) - min(positions)
        if spread > max_position_spread:
            continue
        avg_position = sum(positions) / len(positions)
        candidates.append({
            "text": pages_sorted[0][1][1],
            "occurrence_count": len(pages_sorted),
            "pages": [p for p, _ in pages_sorted],
            "avg_position": round(avg_position, 4),
            "position_spread": round(spread, 4),
            "zone": "top" if avg_position < 0.5 else "bottom",
        })

    candidates.sort(key=lambda c: c["occurrence_count"], reverse=True)
    return candidates
