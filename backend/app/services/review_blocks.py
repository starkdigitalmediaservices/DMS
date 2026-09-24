"""Block builders for the review screen: turn what extraction left behind
into the ordered heading / paragraph / table / image blocks the reviewer
checks against the scan.

For every page the best available source is used (so one document can mix):

  chandra        Datalab's json layout tree, persisted with the OCR result
                 since 0ebaa13 -- real block types, per-block boxes, and for
                 tables a box and confidence for every cell.
  word boxes     per-word positions from Tesseract / PaddleOCR, or the text
                 layer of a native PDF (pdfplumber): words are grouped into
                 lines and lines into paragraphs by position.
  text only      the page's plain text (OCR archive, else the search chunks)
                 as paragraph blocks outlined as the whole page and flagged
                 "no_layout" -- honest about not knowing where on the page.

Documents with template-extracted Facts keep the facts builder instead
(review_service._build_blocks_from_facts): the Fact stays the single source
of truth for those values, and mixing a second, OCR-derived copy of the same
table in would give reviewers two editable versions of one number.

Pure functions over plain dicts except build_document_blocks(), which
gathers the inputs. Nothing here writes to the database.
"""
from __future__ import annotations

import re
import statistics
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Chandra block_type -> review block type. Anything unknown is read as text.
_CHANDRA_TYPE = {
    "SectionHeader": "heading",
    "Title": "heading",
    "Table": "table",
    "TableOfContents": "table",
    "Form": "table",
    "Picture": "image",
    "Figure": "image",
    "PictureGroup": "image",
    "FigureGroup": "image",
}
_FURNITURE = {"PageHeader": "page_header", "PageFooter": "page_footer"}
# Engines in the order their archived output is preferred.
_ENGINE_PREFERENCE = ("chandra", "paddleocr", "paddle", "tesseract", "pdfplumber", "groq")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

class _Text(HTMLParser):
    """HTML -> readable text: block tags become line breaks, image alt text
    and Datalab's img-alt description are kept (that's the only text a
    Picture block has)."""

    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BREAK:
            self.parts.append("\n")
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(alt)
        if tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    text = "".join(p.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _picture_text(html: str) -> str:
    """A Picture's description, once: Datalab repeats the alt text inside an
    img-description div."""
    m = re.search(r'alt="([^"]*)"', html or "")
    return m.group(1).strip() if m else html_to_text(html)


def _box(x0: float, y0: float, x1: float, y1: float) -> Dict[str, float]:
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    return {"x": round(x0, 5), "y": round(y0, 5), "w": round(x1 - x0, 5), "h": round(y1 - y0, 5)}


def _norm(bbox: List[float], page_w: float, page_h: float) -> Optional[Dict[str, float]]:
    if not bbox or len(bbox) != 4 or not page_w or not page_h:
        return None
    return _box(bbox[0] / page_w, bbox[1] / page_h, bbox[2] / page_w, bbox[3] / page_h)


def _union(boxes: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    return _box(x0, y0, max(b["x"] + b["w"] for b in boxes), max(b["y"] + b["h"] for b in boxes))


def _text_block(block_id: str, btype: str, text: str, page: int, box: Optional[Dict[str, float]],
                flags: Optional[List[str]] = None, confidence: Optional[float] = None) -> Dict[str, Any]:
    return {
        "id": block_id, "type": btype, "title": None, "text": text,
        "source_pages": [page], "bbox": {str(page): box} if box else {},
        "confidence": confidence, "flags": flags or [],
    }


# ---------------------------------------------------------------------------
# chandra layout
# ---------------------------------------------------------------------------

class _TableCells(HTMLParser):
    """Rows of (text, data-bbox, data-confidence, is_header, colspan) out of
    one Datalab table block's HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[Dict[str, Any]]] = []
        self._cell: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append([])
            self._cell = {
                "parts": [], "header": tag == "th",
                "bbox": [float(v) for v in (a.get("data-bbox") or "").split()] or None,
                "confidence": float(a["data-confidence"]) if a.get("data-confidence") else None,
                "colspan": int(a.get("colspan") or 1) if str(a.get("colspan") or "1").isdigit() else 1,
            }
        elif tag == "br" and self._cell is not None:
            self._cell["parts"].append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._cell["text"] = re.sub(r"[ \t]+", " ", "".join(self._cell.pop("parts"))).strip()
            self.rows[-1].append(self._cell)
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["parts"].append(data)


def _chandra_table(block: Dict[str, Any], block_id: str, page: int, pw: float, ph: float) -> Optional[Dict[str, Any]]:
    parser = _TableCells()
    parser.feed(block.get("html") or "")
    raw_rows = [r for r in parser.rows if r]
    if not raw_rows:
        return None

    # A leading row of <th> cells is the header; otherwise columns are numbered.
    header_row = raw_rows[0] if all(c["header"] for c in raw_rows[0]) else None
    body = raw_rows[1:] if header_row else raw_rows
    width = max(sum(c["colspan"] for c in r) for r in raw_rows)
    if header_row:
        headers = []
        for c in header_row:
            headers += [c["text"] or f"Column {len(headers) + 1}"] + [""] * (c["colspan"] - 1)
    else:
        headers = []
    headers = (headers + [f"Column {i + 1}" for i in range(len(headers), width)])[:width]
    # Two columns with the same heading (common in registers) must stay distinct.
    seen: Dict[str, int] = {}
    for i, h in enumerate(headers):
        seen[h] = seen.get(h, 0) + 1
        if seen[h] > 1:
            headers[i] = f"{h} ({seen[h]})"

    rows = []
    for ri, r in enumerate(body):
        cells = []
        for c in r:
            box = _norm(c["bbox"], pw, ph)
            region = dict(box, page=page) if box else None
            cells.append({
                "fact_id": None, "text": c["text"], "confidence": c["confidence"],
                "bbox": region, "regions": [region] if region else [],
            })
            # a spanning cell fills the columns it covers; the extra ones are empty
            cells += [{"fact_id": None, "text": "", "confidence": None, "bbox": None, "regions": []}
                      for _ in range(c["colspan"] - 1)]
        cells = (cells + [{"fact_id": None, "text": "", "confidence": None, "bbox": None, "regions": []}
                          for _ in range(width - len(cells))])[:width]
        rows.append({"id": f"{block_id}-r{ri}", "page": page, "cells": cells, "flags": []})

    if not rows:
        return None
    confidences = [c["confidence"] for r in rows for c in r["cells"] if c["confidence"] is not None]
    box = _norm(block.get("bbox"), pw, ph)
    return {
        "id": block_id, "type": "table", "title": None, "headers": headers, "rows": rows,
        "source_pages": [page], "bbox": {str(page): box} if box else {},
        "confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "flags": [],
    }


def blocks_from_chandra_page(layout: Dict[str, Any], page_number: int) -> List[Dict[str, Any]]:
    root = (layout or {}).get("page") or {}
    rb = root.get("bbox") or []
    pw, ph = (rb[2] - rb[0], rb[3] - rb[1]) if len(rb) == 4 else (0.0, 0.0)
    out: List[Dict[str, Any]] = []
    for i, child in enumerate(root.get("children") or []):
        btype_raw = child.get("block_type") or "Text"
        block_id = f"b-p{page_number}-c{i}"
        if btype_raw in _FURNITURE:
            text = html_to_text(child.get("html") or "")
            if text:
                out.append(_text_block(block_id, "paragraph", text, page_number,
                                       _norm(child.get("bbox"), pw, ph), flags=[_FURNITURE[btype_raw]]))
            continue
        btype = _CHANDRA_TYPE.get(btype_raw, "paragraph")
        if btype == "table":
            table = _chandra_table(child, block_id, page_number, pw, ph)
            if table:
                out.append(table)
            continue
        text = _picture_text(child.get("html") or "") if btype == "image" else html_to_text(child.get("html") or "")
        if not text and btype != "image":
            continue
        out.append(_text_block(block_id, btype, text, page_number, _norm(child.get("bbox"), pw, ph)))
    return out


# ---------------------------------------------------------------------------
# word boxes -> lines -> paragraphs
# ---------------------------------------------------------------------------

def _normalise_words(words: List[Dict[str, Any]], page_bbox: Dict[str, Any]) -> List[Dict[str, float]]:
    """Two shapes exist in the archive: normalised {x0,y0,x1,y1} (T05 onward)
    and raw pdfplumber {x0,x1,top,bottom} in PDF points (older rows)."""
    pw = float((page_bbox or {}).get("width") or 0)
    ph = float((page_bbox or {}).get("height") or 0)
    out = []
    for w in words or []:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        if "top" in w and "bottom" in w:
            # raw pdfplumber word: points, top-left origin via top/bottom
            # (its own y0/y1 are bottom-origin and are ignored)
            if not pw or not ph:
                continue
            x0, x1 = w["x0"] / pw, w["x1"] / pw
            y0, y1 = w["top"] / ph, w["bottom"] / ph
        else:
            x0, y0, x1, y1 = w.get("x0"), w.get("y0"), w.get("x1"), w.get("y1")
            if None in (x0, y0, x1, y1):
                continue
        out.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return out


def blocks_from_words(words: List[Dict[str, Any]], page_bbox: Dict[str, Any], page_number: int) -> List[Dict[str, Any]]:
    ws = _normalise_words(words, page_bbox)
    if not ws:
        return []
    heights = [w["y1"] - w["y0"] for w in ws if w["y1"] > w["y0"]]
    h = statistics.median(heights) if heights else 0.012

    # 1. lines: words whose vertical centres are within half a word-height
    ws.sort(key=lambda w: ((w["y0"] + w["y1"]) / 2, w["x0"]))
    lines: List[List[Dict[str, float]]] = []
    for w in ws:
        cy = (w["y0"] + w["y1"]) / 2
        if lines and abs(cy - lines[-1][0]["_cy"]) <= h * 0.6:
            w["_cy"] = lines[-1][0]["_cy"]
            lines[-1].append(w)
        else:
            w["_cy"] = cy
            lines.append([w])

    # 2. split a line where a wide horizontal gap separates columns, so a
    #    two-column page does not read straight across both columns
    segments = []
    gap = max(h * 3, 0.04)
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        seg = [line[0]]
        for w in line[1:]:
            if w["x0"] - seg[-1]["x1"] > gap:
                segments.append(seg)
                seg = [w]
            else:
                seg.append(w)
        segments.append(seg)

    # 3. paragraphs: a segment continues the paragraph above it when it starts
    #    within ~1.2 line-heights below it and overlaps it horizontally
    paras: List[Dict[str, Any]] = []
    for seg in sorted(segments, key=lambda s: (min(w["y0"] for w in s), s[0]["x0"])):
        box = {"x0": min(w["x0"] for w in seg), "y0": min(w["y0"] for w in seg),
               "x1": max(w["x1"] for w in seg), "y1": max(w["y1"] for w in seg)}
        text = " ".join(w["text"] for w in seg)
        target = None
        for p in reversed(paras):
            vgap = box["y0"] - p["box"]["y1"]
            overlap = min(box["x1"], p["box"]["x1"]) - max(box["x0"], p["box"]["x0"])
            if -h <= vgap <= h * 1.2 and overlap > 0:
                target = p
                break
            if vgap > h * 3:
                break
        if target:
            target["lines"].append(text)
            b = target["box"]
            target["box"] = {"x0": min(b["x0"], box["x0"]), "y0": min(b["y0"], box["y0"]),
                             "x1": max(b["x1"], box["x1"]), "y1": max(b["y1"], box["y1"])}
        else:
            paras.append({"lines": [text], "box": box})

    # reading order: top to bottom, and within a band of rows, left column first
    paras.sort(key=lambda p: (round(p["box"]["y0"] / (h * 2)) if h else p["box"]["y0"], p["box"]["x0"]))
    return [
        _text_block(f"b-p{page_number}-w{i}", "paragraph", "\n".join(p["lines"]), page_number,
                    _box(p["box"]["x0"], p["box"]["y0"], p["box"]["x1"], p["box"]["y1"]))
        for i, p in enumerate(paras)
    ]


# ---------------------------------------------------------------------------
# text only
# ---------------------------------------------------------------------------

MAX_TEXT_ONLY_BLOCKS_PER_PAGE = 20


def blocks_from_text(text: str, page_number: int) -> List[Dict[str, Any]]:
    """No positions at all: split on blank lines, outline the whole page, and
    say so, rather than inventing boxes."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    # Blank-line splitting suits prose; OCR text of a register or form is
    # full of blank lines and would shatter into hundreds of fragments per
    # page (Pune.pdf: 43,497 blocks over 280 pages). Past a handful, one
    # block per page is easier to check than a wall of tiny cards.
    if len(parts) > MAX_TEXT_ONLY_BLOCKS_PER_PAGE or (not parts and (text or "").strip()):
        parts = [(text or "").strip()]
    whole = _box(0, 0, 1, 1)
    return [
        _text_block(f"b-p{page_number}-t{i}", "paragraph", p, page_number, whole, flags=["no_layout"])
        for i, p in enumerate(parts)
    ]


def _dedupe_overlapping_chunks(chunks: List[str]) -> str:
    """Search chunks overlap (chunk_overlap_tokens); stitch them back into one
    text without repeating the shared part."""
    text = ""
    for c in chunks:
        c = c or ""
        best = 0
        for k in range(min(len(text), len(c), 2000), 20, -1):
            if text.endswith(c[:k]):
                best = k
                break
        text += c[best:] if best else (("\n\n" if text else "") + c)
    return text


# ---------------------------------------------------------------------------
# document-level
# ---------------------------------------------------------------------------

def blocks_from_pages(pages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Best source per page. Returns (blocks, {source: page count})."""
    blocks: List[Dict[str, Any]] = []
    used: Dict[str, int] = {}
    for page in sorted(pages or [], key=lambda p: p.get("page_number") or 0):
        n = page.get("page_number") or 1
        if page.get("extraction_failed"):
            used["failed"] = used.get("failed", 0) + 1
            continue
        page_blocks: List[Dict[str, Any]] = []
        if page.get("layout"):
            page_blocks = blocks_from_chandra_page(page["layout"], n)
            source = "chandra"
        if not page_blocks and page.get("words"):
            page_blocks = blocks_from_words(page["words"], page.get("bbox") or {}, n)
            source = "word_boxes"
        if not page_blocks:
            page_blocks = blocks_from_text(page.get("text") or "", n)
            source = "text_only"
        if page_blocks:
            used[source] = used.get(source, 0) + 1
            blocks += page_blocks
    for order, b in enumerate(blocks):
        b["order"] = order
    return blocks, used


def builder_name(used: Dict[str, int], has_facts: bool) -> str:
    """The label a document's snapshot is stored under (and the backfill
    reports): the best source that contributed any page."""
    if has_facts:
        return "facts"
    for source in ("chandra", "word_boxes", "text_only"):
        if used.get(source):
            return source
    return "none"


async def load_ocr_pages(db: AsyncSession, file_hash: Optional[str]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """The archived OCR result for this exact file, preferring engines that
    kept more structure. The archive is content-addressed (not tenant data),
    and only reached through a document the caller was already allowed to
    open."""
    from app.models.ocr_archive import OCRArchive

    if not file_hash:
        return None, []
    res = await db.execute(select(OCRArchive.ocr_engine, OCRArchive.pages).where(OCRArchive.content_hash == file_hash))
    rows = {engine: pages for engine, pages in res.all()}
    if not rows:
        return None, []

    def score(engine: str) -> Tuple[int, int]:
        pages = rows[engine] or []
        has_layout = any(p.get("layout") for p in pages)
        has_words = any(p.get("words") for p in pages)
        rank = _ENGINE_PREFERENCE.index(engine) if engine in _ENGINE_PREFERENCE else len(_ENGINE_PREFERENCE)
        return (0 if has_layout else 1 if has_words else 2, rank)

    engine = sorted(rows, key=score)[0]
    return engine, rows[engine] or []


async def load_chunk_pages(db: AsyncSession, tenant_id: UUID, document_id: UUID, version_id: Optional[UUID]) -> List[Dict[str, Any]]:
    """Last resort when no OCR archive exists: rebuild per-page text from the
    search chunks."""
    from app.models.chunk import Chunk

    stmt = select(Chunk.page_number, Chunk.chunk_index, Chunk.content).where(
        Chunk.tenant_id == tenant_id, Chunk.document_id == document_id,
    )
    if version_id:
        stmt = stmt.where(Chunk.version_id == version_id)
    res = await db.execute(stmt.order_by(Chunk.page_number, Chunk.chunk_index))
    by_page: Dict[int, List[str]] = {}
    for page, _, content in res.all():
        by_page.setdefault(page or 1, []).append(content)
    return [{"page_number": p, "text": _dedupe_overlapping_chunks(c)} for p, c in sorted(by_page.items())]
