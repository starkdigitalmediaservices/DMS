import io
import json
import csv
import logging
import re
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Trilingual OCR per the product's English/Hindi/Marathi coverage — requires the
# tesseract-ocr-hin and tesseract-ocr-mar language packs (see backend/Dockerfile).
TESSERACT_LANG = "eng+hin+mar"

# T90 — lazily-loaded singleton so the model loads once per process, not
# once per page. lang='mr' selects PaddleOCR's Devanagari-family
# recognition model (covers Marathi and Hindi both — see paddleocr's
# DEVANAGARI_LANGS grouping), matching this product's stated priority
# ("the product cannot currently read the script it is sold on").
# Tradeoff versus Tesseract's eng+hin+mar single pass: PaddleOCR needs one
# language pipeline per call, so pure-English pages may see lower
# accuracy here than with Tesseract's combined pass — not a bug, a
# genuine engine tradeoff, left for a caller to choose via ocr_engine.
_paddle_ocr_instance = None


def _get_paddle_ocr():
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        from paddleocr import PaddleOCR
        _paddle_ocr_instance = PaddleOCR(
            lang="mr",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # Required on this platform — PaddlePaddle 3.3.1's default PIR
            # executor errors on the oneDNN-fused instruction path for the
            # text detection model:
            # "NotImplementedError: ConvertPirAttribute2RuntimeAttribute
            #  not support [pir::ArrayAttribute<pir::DoubleAttribute>]"
            # Confirmed live: identical inputs succeed with this off,
            # fail with it on. Revisit if a future paddlepaddle release
            # fixes the oneDNN/PIR interaction.
            enable_mkldnn=False,
        )
    return _paddle_ocr_instance


def _paddle_ocr_image(pil_img) -> str:
    import numpy as np
    ocr = _get_paddle_ocr()
    arr = np.array(pil_img.convert("RGB"))
    lines = []
    for res in ocr.predict(arr):
        lines.extend(res.get("rec_texts", []))
    return "\n".join(lines)


def _tesseract_word_boxes(pil_img, lang: str) -> List[Dict[str, Any]]:
    """T05 (scanned-corpus coverage) — real per-word bounding boxes from
    Tesseract's own layout analysis, normalised to 0-1 image fractions per
    T06's coordinate contract (top-left origin). Deliberately a second,
    separate OCR pass from the plain-text image_to_string call already used
    for chunking/indexing elsewhere in this file, rather than reconstructing
    text from image_to_data's word list — that reconstruction doesn't
    reliably match image_to_string's spacing/line-join behaviour, and this
    module must never silently change the text that's actually indexed.
    The cost is one extra Tesseract pass per scanned page.
    """
    from pytesseract import Output
    import pytesseract as _pt

    w, h = pil_img.size
    if w <= 0 or h <= 0:
        return []
    data = _pt.image_to_data(pil_img, lang=lang, output_type=Output.DICT)
    words: List[Dict[str, Any]] = []
    for i in range(len(data.get("text", []))):
        if data["level"][i] != 5:  # 5 == word level in Tesseract's block/par/line/word hierarchy
            continue
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        left, top = data["left"][i], data["top"][i]
        width, height = data["width"][i], data["height"][i]
        words.append({
            "text": text,
            "x0": max(0.0, min(1.0, left / w)),
            "y0": max(0.0, min(1.0, top / h)),
            "x1": max(0.0, min(1.0, (left + width) / w)),
            "y1": max(0.0, min(1.0, (top + height) / h)),
        })
    return words


def _paddle_word_boxes(pil_img) -> List[Dict[str, Any]]:
    """T05 (scanned-corpus coverage) — PaddleOCR's return_word_box=True
    exposes real per-word pixel boxes (text_word/text_word_boxes, one
    array per detected text line) instead of only the line-level text
    _paddle_ocr_image reads. Skips the whitespace tokens PaddleOCR inserts
    between words in text_word.
    """
    import numpy as np

    ocr = _get_paddle_ocr()
    w, h = pil_img.size
    if w <= 0 or h <= 0:
        return []
    arr = np.array(pil_img.convert("RGB"))
    words: List[Dict[str, Any]] = []
    for res in ocr.predict(arr, return_word_box=True):
        for line_tokens, line_boxes in zip(res.get("text_word") or [], res.get("text_word_boxes") or []):
            for token, box in zip(line_tokens, line_boxes):
                if not token or not token.strip():
                    continue
                x0, y0, x1, y1 = (float(v) for v in box[:4])
                words.append({
                    "text": token,
                    "x0": max(0.0, min(1.0, x0 / w)),
                    "y0": max(0.0, min(1.0, y0 / h)),
                    "x1": max(0.0, min(1.0, x1 / w)),
                    "y1": max(0.0, min(1.0, y1 / h)),
                })
    return words


class _ChandraFullTextHTMLParser:
    """Strips Chandra's HTML output to plain reading-order text. Unlike
    ChandraVLMProvider's _TableHTMLParser (chandra_provider.py — table
    cells only, mapped onto a field schema for the Facts pipeline), this
    keeps every block of text on the page — paragraphs and table cells
    alike — since chunking/embedding for general search only needs
    readable text, not structure."""

    _BLOCK_TAGS = {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br"}

    def __init__(self) -> None:
        from html.parser import HTMLParser

        class _Inner(HTMLParser):
            def __init__(inner_self):
                super().__init__()
                inner_self.parts: list[str] = []

            def handle_starttag(inner_self, tag, attrs):
                if tag in self._BLOCK_TAGS:
                    inner_self.parts.append("\n")

            def handle_endtag(inner_self, tag):
                if tag in self._BLOCK_TAGS:
                    inner_self.parts.append("\n")

            def handle_data(inner_self, data):
                if data:
                    inner_self.parts.append(data)

        self._parser = _Inner()

    def feed(self, html: str) -> str:
        self._parser.feed(html)
        import re
        text = "".join(self._parser.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


_LAYOUT_BLOCK_KEYS = ("id", "block_type", "html", "bbox")


def _trim_chandra_layout(block: Any) -> Optional[Dict[str, Any]]:
    """Keeps only what a layout consumer needs from one node of Datalab's
    json tree (id, block_type, html, bbox in the page's own pixel space,
    children). Drops `images` (base64 crops of Picture blocks, can be
    megabytes per page), `polygon` (redundant with bbox) and the rest."""
    if not isinstance(block, dict):
        return None
    out = {k: block.get(k) for k in _LAYOUT_BLOCK_KEYS if k in block}
    children = [c for c in (_trim_chandra_layout(ch) for ch in block.get("children") or []) if c]
    if children:
        out["children"] = children
    return out


def _chandra_layout_from_response(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The root "Page" block of the json output, trimmed. Its bbox is the
    only statement of the pixel space every child bbox is in (Datalab
    rescales the image it was sent), so it is kept, not normalised here."""
    tree = data.get("json")
    if not isinstance(tree, dict):
        return None
    page_blocks = [c for c in tree.get("children") or [] if isinstance(c, dict)]
    if not page_blocks:
        return None
    return {"engine": "chandra", "format": "datalab_json", "page": _trim_chandra_layout(page_blocks[0])}


def _chandra_ocr_image(pil_img) -> str:
    return _chandra_ocr_image_with_layout(pil_img)[0]


def _chandra_ocr_image_with_layout(pil_img) -> Tuple[str, Optional[Dict[str, Any]]]:
    """OCR via Chandra/Datalab's /convert endpoint, called synchronously
    (this runs inside a thread via asyncio.to_thread, same as the
    tesseract/paddle engines above) — a plain httpx.Client with
    time.sleep polling, not the async client ChandraVLMProvider uses,
    since mixing an event loop into an already-threaded sync call path
    adds complexity with no upside here. Returns the whole page's plain
    readable text (paragraphs and tables both), unlike ChandraVLMProvider
    which maps only table cells onto a field schema for the Facts
    pipeline — this is the general-purpose OCR replacement for
    tesseract/paddle on handwritten/scanned pages.

    Asks for html AND json in the same call (no extra cost per page): the
    text still comes from the html, exactly as before, and the json layout
    tree (block types + bboxes) is returned alongside so it is persisted
    with the page instead of discarded — the review screen builds its
    heading/paragraph/table blocks from it."""
    import time
    import httpx
    from app.config import settings

    api_key = settings.datalab_api_key
    if not api_key:
        raise RuntimeError("DATALAB_API_KEY not configured")

    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="PNG")

    with httpx.Client(timeout=60.0) as client:
        submit = client.post(
            "https://www.datalab.to/api/v1/convert",
            headers={"X-API-Key": api_key},
            files={"file": ("page.png", buf.getvalue(), "image/png")},
            data={"output_format": "html,json", "mode": "accurate", "extras": "table_cell_bboxes"},
        )
        if submit.status_code != 200:
            raise Exception(f"Chandra convert request failed with status {submit.status_code}: {submit.text}")
        check_url = submit.json().get("request_check_url")
        if not check_url:
            raise Exception(f"Chandra convert response missing request_check_url: {submit.text}")

        elapsed = 0.0
        interval = 3.0
        timeout = 180.0
        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            poll = client.get(check_url, headers={"X-API-Key": api_key})
            if poll.status_code != 200:
                raise Exception(f"Chandra status poll failed with status {poll.status_code}: {poll.text}")
            data = poll.json()
            status = data.get("status")
            if status == "complete":
                return _ChandraFullTextHTMLParser().feed(data.get("html") or ""), _chandra_layout_from_response(data)
            if status == "failed":
                raise Exception(f"Chandra conversion failed: {data.get('error')}")
        raise Exception(f"Chandra conversion did not complete within {timeout}s")


_GROQ_OCR_PROMPT = (
    "Transcribe ALL text in this scanned document page exactly as written. "
    "Keep the original script and language (Marathi/Devanagari, Hindi, Urdu, English) -- "
    "do not translate or transliterate. Copy numbers, dates, survey/serial numbers and "
    "names character for character. Preserve reading order; put each table row on its own "
    "line with cells separated by ' | '. Include handwritten text. If something is "
    "illegible write [illegible]. Output only the transcription: no commentary, no "
    "markdown fences. If the page has no text at all, output nothing."
)

# Keys that answered "model blocked at the organization level" -- skipped
# for the rest of this process instead of costing a round trip per page.
_groq_blocked_keys: set = set()
_groq_key_cursor = 0


def _groq_ocr_image(pil_img) -> str:
    """OCR via a Groq-hosted vision model (settings.groq_vision_model),
    synchronous like _chandra_ocr_image (runs inside asyncio.to_thread).

    Groq's free on-demand tier allows roughly 7k input tokens/minute per
    organization for vision models, and an image costs ~1 token per 28x28
    pixels -- so the page is downscaled to groq_ocr_max_pixels first, and
    429s are handled by moving to the next key or honouring retry-after.
    Keys whose organization has the model blocked (403) are skipped. No
    per-word coordinates, same as chandra: page-level regions only."""
    import base64
    import time
    import httpx
    from app.config import settings

    global _groq_key_cursor
    keys = [k for k in settings.get_groq_api_keys() if k not in _groq_blocked_keys]
    if not keys:
        raise RuntimeError("No Groq API key with access to the vision model is configured")

    img = pil_img.convert("RGB")
    max_px = settings.groq_ocr_max_pixels
    if img.width * img.height > max_px:
        scale = (max_px / float(img.width * img.height)) ** 0.5
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    payload = {
        "model": settings.groq_vision_model,
        "temperature": 0,
        "max_completion_tokens": 4096,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _GROQ_OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }

    deadline = time.monotonic() + settings.groq_ocr_timeout_seconds
    last_error = ""
    with httpx.Client(timeout=120.0) as client:
        while time.monotonic() < deadline:
            keys = [k for k in settings.get_groq_api_keys() if k not in _groq_blocked_keys]
            if not keys:
                break
            waits = []
            for _ in range(len(keys)):
                key = keys[_groq_key_cursor % len(keys)]
                _groq_key_cursor += 1
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
                if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"].get("content") or ""
                    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
                    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
                    return text.strip()
                last_error = f"{resp.status_code}: {resp.text[:300]}"
                if resp.status_code == 403 and "blocked" in resp.text:
                    _groq_blocked_keys.add(key)
                    continue
                if resp.status_code == 429:
                    try:
                        waits.append(float(resp.headers.get("retry-after", "5")))
                    except ValueError:
                        waits.append(5.0)
                    continue
                if resp.status_code >= 500:
                    waits.append(3.0)
                    continue
                raise Exception(f"Groq OCR request failed with status {last_error}")
            if not waits:
                break
            time.sleep(min(min(waits) + 0.5, max(0.0, deadline - time.monotonic())))
    raise Exception(f"Groq OCR gave up: {last_error or 'no usable key'}")


def extract_pages_from_file(file_bytes: bytes, filename: str, ocr_engine: str = "tesseract") -> List[Dict[str, Any]]:
    """
    Extract structured pages and text content from various file formats:
    PDF, Word (.docx), Excel (.xlsx, .csv), PowerPoint (.pptx), Markdown (.md),
    RTF (.rtf), JSON (.json), Images (.jpg, .png, etc.), and Plain Text files.

    ocr_engine ('tesseract' | 'paddle' | 'chandra' | 'groq') only affects the
    PDF/image paths — every other format here doesn't involve OCR at all.
    """
    ext = filename.lower().split(".")[-1] if "." in filename else ""

    if ext == "pdf":
        return _extract_pdf(file_bytes, filename, ocr_engine)
    elif ext in ["docx", "doc"]:
        return _extract_docx(file_bytes)
    elif ext in ["xlsx", "xls"]:
        return _extract_excel(file_bytes)
    elif ext in ["pptx", "ppt"]:
        return _extract_pptx(file_bytes)
    elif ext == "csv":
        return _extract_csv(file_bytes)
    elif ext == "rtf":
        return _extract_rtf(file_bytes)
    elif ext == "json":
        return _extract_json(file_bytes)
    elif ext in ["jpg", "jpeg", "png", "bmp", "webp", "tiff"]:
        return _extract_image(file_bytes, filename, ocr_engine)
    else:
        return _extract_text(file_bytes)


def _extract_image(file_bytes: bytes, filename: str, ocr_engine: str = "tesseract") -> List[Dict[str, Any]]:
    text = ""
    words: List[Dict[str, Any]] = []
    layout: Optional[Dict[str, Any]] = None
    img_size = None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        img_size = img.size
        if ocr_engine == "paddle":
            text = _paddle_ocr_image(img) or ""
            words = _paddle_word_boxes(img)
        elif ocr_engine == "groq":
            text = _groq_ocr_image(img) or ""
        elif ocr_engine == "chandra":
            text, layout = _chandra_ocr_image_with_layout(img)
            text = text or ""
            # T05 — Chandra's /convert endpoint (used here in plain-text
            # mode) returns no per-word coordinates, so a chandra-OCR'd
            # image still falls back to page-level regions, same as
            # before this fix. Documented gap, not a bug.
        else:
            import pytesseract
            text = pytesseract.image_to_string(img, lang=TESSERACT_LANG) or ""
            words = _tesseract_word_boxes(img, TESSERACT_LANG)
        logger.info(f"Image OCR ({ocr_engine}) extracted {len(text)} chars from {filename}")
    except Exception as e:
        logger.warning(f"Failed to perform {ocr_engine} OCR on image file {filename}: {e}")

    text_clean = text.strip()
    tokens = [t for t in text_clean.split() if t]
    if not tokens:
        failed = True
    else:
        noise_symbols = set("()~<>|[]{}`!@#$%^&*_=+\\/")
        noise_count = 0
        for t in tokens:
            has_symbol = any(c in noise_symbols for c in t)
            is_gibberish_ascii = bool(re.match(r"^[a-zA-Z]{3,}$", t)) and not any(v in t.lower() for v in "aeiouy")
            if has_symbol or is_gibberish_ascii:
                noise_count += 1
        failed = (noise_count / len(tokens)) >= 0.5

    if failed:
        text = f"Image document: {filename}"

    page = {
        "page_number": 1,
        "text": text.strip(),
        "words": [] if failed else words,
        "bbox": {"width": float(img_size[0]), "height": float(img_size[1])} if img_size else {},
        "extraction_failed": failed
    }
    if layout and not failed:
        page["layout"] = layout
    return [page]


def _extract_pdf(file_bytes: bytes, filename: str = "", ocr_engine: str = "tesseract") -> List[Dict[str, Any]]:
    import pdfplumber
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                raw_words = page.extract_words() or []
                layout = None

                # T05 — carry word-level regions from the extractor to the
                # fact writer instead of discarding them here. Normalised
                # to 0-1 page fractions, top-left origin, per T06's signed
                # coordinate contract (pdfplumber's top/bottom are already
                # top-down, so no y-flip needed). Page-level granularity
                # (every word on the page, not sliced per-chunk) — chunking
                # re-tokenizes page text and doesn't preserve a reliable
                # char-offset mapping back to pdfplumber's word list, so a
                # per-chunk slice would be a guess; the full per-page list
                # is exact and still a real, usable region source (T04's
                # own FactRegion works at page granularity too).
                pw = float(page.width) or 1.0
                ph = float(page.height) or 1.0
                words = [
                    {
                        "text": w.get("text", ""),
                        "x0": max(0.0, min(1.0, w["x0"] / pw)),
                        "y0": max(0.0, min(1.0, w["top"] / ph)),
                        "x1": max(0.0, min(1.0, w["x1"] / pw)),
                        "y1": max(0.0, min(1.0, w["bottom"] / ph)),
                    }
                    for w in raw_words
                    if "x0" in w and "top" in w and "x1" in w and "bottom" in w
                ]

                # OCR Fallback for scanned/image PDF pages
                if not text.strip():
                    try:
                        pil_img = page.to_image(resolution=150).original
                        if ocr_engine == "paddle":
                            ocr_text = _paddle_ocr_image(pil_img) or ""
                        elif ocr_engine == "chandra":
                            ocr_text, layout = _chandra_ocr_image_with_layout(pil_img)
                            ocr_text = ocr_text or ""
                        elif ocr_engine == "groq":
                            ocr_text = _groq_ocr_image(pil_img) or ""
                        else:
                            import pytesseract
                            ocr_text = pytesseract.image_to_string(pil_img, lang=TESSERACT_LANG) or ""
                        if ocr_text.strip():
                            text = ocr_text
                            logger.info(f"{ocr_engine} OCR extracted {len(text)} chars from page {i+1} of {filename}")
                            # T05 (scanned-corpus coverage) — pdfplumber's
                            # own extract_words() above found nothing on
                            # this page (no text layer, which is exactly
                            # why we're in this OCR fallback), so `words`
                            # is still []. Replace it with real per-word
                            # boxes from the OCR engine that actually read
                            # this page, instead of leaving non-VLM facts
                            # on scanned documents stuck at page-level
                            # regions — chandra has no per-word coordinates
                            # in the mode used here, so it's left at [].
                            if ocr_engine == "paddle":
                                words = _paddle_word_boxes(pil_img)
                            elif ocr_engine not in ("chandra", "groq"):
                                words = _tesseract_word_boxes(pil_img, TESSERACT_LANG)
                    except Exception as ocr_err:
                        logger.warning(f"OCR fallback ({ocr_engine}) failed for page {i+1} of {filename}: {ocr_err}")

                failed = False
                if not text.strip():
                    text = f"Scanned page {i+1} of document {filename}"
                    failed = True

                page_out = {
                    "page_number": i + 1,
                    "text": text.strip(),
                    "words": words,
                    "bbox": {"width": float(page.width), "height": float(page.height)},
                    "extraction_failed": failed
                }
                if layout and not failed:
                    page_out["layout"] = layout
                pages.append(page_out)
    except Exception as e:
        logger.error(f"Error parsing PDF with pdfplumber: {e}")
        pages.append({
            "page_number": 1,
            "text": f"Scanned PDF document: {filename}",
            "words": [],
            "bbox": {},
            "extraction_failed": True
        })
    return pages if pages else [{"page_number": 1, "text": f"Scanned document: {filename}", "words": [], "bbox": {}, "extraction_failed": True}]


def _clean_binary_strings(file_bytes: bytes) -> str:
    import re
    raw_str = file_bytes.decode("latin-1", errors="ignore")
    strings = re.findall(r'[\x20-\x7E\t\n\r]{3,}', raw_str)
    cleaned = [s.strip() for s in strings if len(s.strip()) >= 3 and not s.strip().startswith(('PK', 'Root Entry', '\x00'))]
    return "\n".join(cleaned)


def _extract_docx(file_bytes: bytes) -> List[Dict[str, Any]]:
    pages = []
    try:
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        full_text_blocks = []
        
        # Extract paragraph text
        for p in doc.paragraphs:
            if p.text.strip():
                full_text_blocks.append(p.text.strip())

        # Extract tables text
        for table in doc.tables:
            table_lines = []
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells]
                table_lines.append(" | ".join(row_cells))
            if table_lines:
                full_text_blocks.append("\n".join(table_lines))

        full_text = "\n\n".join(full_text_blocks)
        
        # Chunk text into ~1500 character logical pages for downstream chunker
        chunk_size = 1500
        paragraphs = full_text.split("\n\n")
        current_page_text = []
        current_len = 0
        page_num = 1

        for p in paragraphs:
            current_page_text.append(p)
            current_len += len(p)
            if current_len >= chunk_size:
                pages.append({
                    "page_number": page_num,
                    "text": "\n\n".join(current_page_text),
                    "words": [],
                    "bbox": {}
                })
                page_num += 1
                current_page_text = []
                current_len = 0

        if current_page_text:
            pages.append({
                "page_number": page_num,
                "text": "\n\n".join(current_page_text),
                "words": [],
                "bbox": {}
            })
    except Exception as e:
        logger.error(f"Error parsing DOCX/DOC: {e}")
        extracted_text = _clean_binary_strings(file_bytes)
        pages.append({
            "page_number": 1,
            "text": extracted_text if extracted_text.strip() else "Document content extracted.",
            "words": [],
            "bbox": {}
        })

    return pages if pages else [{"page_number": 1, "text": "Document content", "words": [], "bbox": {}}]


def _extract_excel(file_bytes: bytes) -> List[Dict[str, Any]]:
    pages = []
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        
        for idx, sheet_name in enumerate(wb.sheetnames):
            sheet = wb[sheet_name]
            sheet_lines = [f"Sheet: {sheet_name}"]
            
            for row in sheet.iter_rows(values_only=True):
                row_vals = [str(val) if val is not None else "" for val in row]
                if any(row_vals):
                    sheet_lines.append(" | ".join(row_vals))

            sheet_text = "\n".join(sheet_lines)
            if sheet_text.strip():
                pages.append({
                    "page_number": idx + 1,
                    "text": sheet_text,
                    "words": [],
                    "bbox": {}
                })
    except Exception as e:
        logger.error(f"Error parsing Excel file: {e}")
        # Try pandas fallback
        try:
            import pandas as pd
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            for idx, sheet_name in enumerate(excel_file.sheet_names):
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                text = f"Sheet: {sheet_name}\n" + df.to_string()
                pages.append({
                    "page_number": idx + 1,
                    "text": text,
                    "words": [],
                    "bbox": {}
                })
        except Exception as inner_e:
            logger.error(f"Pandas Excel fallback failed: {inner_e}")
            pages.append({
                "page_number": 1,
                "text": file_bytes.decode("utf-8", errors="ignore"),
                "words": [],
                "bbox": {}
            })

    return pages if pages else [{"page_number": 1, "text": "Excel sheet content", "words": [], "bbox": {}}]


def _extract_pptx(file_bytes: bytes) -> List[Dict[str, Any]]:
    pages = []
    try:
        import pptx
        prs = pptx.Presentation(io.BytesIO(file_bytes))
        
        for idx, slide in enumerate(prs.slides):
            slide_text_blocks = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_text_blocks.append(shape.text.strip())
            
            slide_text = f"Slide {idx + 1}:\n" + "\n".join(slide_text_blocks)
            pages.append({
                "page_number": idx + 1,
                "text": slide_text,
                "words": [],
                "bbox": {}
            })
    except Exception as e:
        logger.error(f"Error parsing PPTX: {e}")
        pages.append({
            "page_number": 1,
            "text": file_bytes.decode("utf-8", errors="ignore"),
            "words": [],
            "bbox": {}
        })

    return pages if pages else [{"page_number": 1, "text": "Presentation content", "words": [], "bbox": {}}]


def _extract_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    pages = []
    try:
        content = file_bytes.decode("utf-8", errors="ignore")
        reader = csv.reader(content.splitlines())
        lines = []
        for row in reader:
            if any(row):
                lines.append(" | ".join(row))

        # Split into pages of 100 rows each
        page_size = 100
        for i in range(0, max(1, len(lines)), page_size):
            chunk = lines[i:i+page_size]
            pages.append({
                "page_number": (i // page_size) + 1,
                "text": "\n".join(chunk),
                "words": [],
                "bbox": {}
            })
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}")
        pages.append({
            "page_number": 1,
            "text": file_bytes.decode("utf-8", errors="ignore"),
            "words": [],
            "bbox": {}
        })

    return pages if pages else [{"page_number": 1, "text": "CSV document content", "words": [], "bbox": {}}]


def _extract_rtf(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        from striprtf.striprtf import rtf_to_text
        raw_rtf = file_bytes.decode("utf-8", errors="ignore")
        plain_text = rtf_to_text(raw_rtf)
        return [{
            "page_number": 1,
            "text": plain_text.strip(),
            "words": [],
            "bbox": {}
        }]
    except Exception as e:
        logger.error(f"Error parsing RTF: {e}")
        return [{
            "page_number": 1,
            "text": file_bytes.decode("utf-8", errors="ignore"),
            "words": [],
            "bbox": {}
        }]


def _extract_json(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        raw_text = file_bytes.decode("utf-8", errors="ignore")
        parsed_json = json.loads(raw_text)
        formatted_json = json.dumps(parsed_json, indent=2)
        return [{
            "page_number": 1,
            "text": formatted_json,
            "words": [],
            "bbox": {}
        }]
    except Exception as e:
        logger.error(f"Error parsing JSON: {e}")
        return [{
            "page_number": 1,
            "text": file_bytes.decode("utf-8", errors="ignore"),
            "words": [],
            "bbox": {}
        }]


def _extract_text(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1")
        except Exception:
            text = file_bytes.decode("utf-8", errors="ignore")

    return [{
        "page_number": 1,
        "text": text if text.strip() else "Document text",
        "words": [],
        "bbox": {}
    }]
