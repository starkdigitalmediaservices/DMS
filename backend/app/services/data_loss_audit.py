"""TS2 — data-loss audit: word-level, not block-level, verification that
every word OCR actually read survives into what gets stored and served
back out.

Adapted from a colleague's separate waqf-digitization project (see
docs/planning/TS_backlog_colleague_features.md). Their system reconstructs a rendered
document and checks the OCR'd word survives into the viewer/Markdown/xlsx
export. This system doesn't have an equivalent single "rendered document"
— search results, chat citations, and the click-through viewer are all
served from doc_dg_chunks, so that IS the effective "viewer" here: this
audit compares the OCR provider's raw per-page text (app/ocr/extractor.py's
`pages`, already computed fresh during ingestion, before it's chunked and
discarded — no re-OCR needed) against the chunk content actually about to
be stored, right there in the same ingestion pass.

Word-level rather than block/chunk-count on purpose: the chunker
legitimately reflows text across token-window boundaries (and, upstream,
TS1 legitimately reflows Fact values across page/table boundaries) —
comparing word membership rather than block structure is what tolerates
correct restructuring while still catching a word actually going missing.

T78/T77 (export/report) aren't audited here: unlike the source project,
those exports are curated structured-field packages (entity 360 records,
linked facts), not a full-text reconstruction — auditing "does the xlsx
contain every OCR word" would be a category error for them. The relevant
surfaces for word-level prose fidelity in this system are chunk-derived:
search, chat citations, and the workbench viewer.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Plain \w alone is WRONG for Devanagari: Python's Unicode word-character
# class covers base letters (category Lo) but excludes combining marks
# like matras/anusvara (category Mc/Mn) — "वाशिम" would split into
# व/श/म at every matra boundary, same class of bug T75 fixed for tsvector
# indexing. ऀ-ॿ is the full Devanagari Unicode block (letters
# and combining marks together), added alongside \w rather than replacing
# it so Latin/numeric text keeps working exactly as before.
_WORD_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)

# A handful of missed words is noise (tokenizer edge cases at a chunk's
# token-decode boundary, stray OCR punctuation-only artifacts) — this
# exists to catch a chunking/pipeline bug dropping real content, not to
# fire on every document.
DEFAULT_MIN_ABSOLUTE_TOLERANCE = 2
DEFAULT_LOSS_RATIO_TOLERANCE = 0.005


def tokenize_words(text: str) -> List[str]:
    if not text:
        return []
    return [w.lower() for w in _WORD_RE.findall(text)]


@dataclass
class DataLossAuditResult:
    total_words: int
    missing_count: int
    loss_ratio: float
    passed: bool
    missing_sample: List[Dict[str, Any]] = field(default_factory=list)  # [{"word":..., "page_number":...}], capped


def audit_pages_vs_chunks(
    pages: List[Dict[str, Any]],
    chunk_contents: List[str],
    sample_cap: int = 20,
) -> DataLossAuditResult:
    """`pages` is the OCR provider's raw per-page output (extractor.py's
    shape: {"page_number", "text", "extraction_failed", ...}). `chunk_contents`
    is the .content of every Chunk about to be (or already) stored for
    this document version. Pages flagged extraction_failed are skipped —
    their "text" is a synthetic placeholder, not real OCR content, so
    auditing it would be meaningless."""
    chunk_word_set = set()
    for content in chunk_contents:
        chunk_word_set.update(tokenize_words(content))

    total_words = 0
    missing_sample: List[Dict[str, Any]] = []
    missing_count = 0

    for page in pages:
        if page.get("extraction_failed"):
            continue
        page_number = page.get("page_number", 1)
        for word in tokenize_words(page.get("text", "")):
            total_words += 1
            if word not in chunk_word_set:
                missing_count += 1
                if len(missing_sample) < sample_cap:
                    missing_sample.append({"word": word, "page_number": page_number})

    loss_ratio = (missing_count / total_words) if total_words else 0.0
    tolerance = max(DEFAULT_MIN_ABSOLUTE_TOLERANCE, total_words * DEFAULT_LOSS_RATIO_TOLERANCE)
    passed = missing_count <= tolerance

    return DataLossAuditResult(
        total_words=total_words,
        missing_count=missing_count,
        loss_ratio=loss_ratio,
        passed=passed,
        missing_sample=missing_sample,
    )
