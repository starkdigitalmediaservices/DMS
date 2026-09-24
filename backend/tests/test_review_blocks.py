"""Review-screen block builders (app/services/review_blocks.py).

Inputs mirror what is really in doc_dg_ocr_archive: a Chandra layout page
shaped like the 2026-09-24 live Washim probe, raw pdfplumber word dicts
(points, top/bottom) as older archive rows hold them, and normalised T05
word boxes.
"""
import hashlib
import uuid

import pytest

from app.database import AsyncSessionLocal
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.ocr_archive import OCRArchive
from app.models.tenant import Tenant
from app.models.user import User
from app.services import review_blocks as rb
from app.services import review_service as rs

TABLE_HTML = (
    '<table><tr><th data-bbox="100 800 700 820">पृष्ठे</th><th data-bbox="700 800 1400 820">पृष्ठे</th>'
    '<th data-bbox="1400 800 1500 820">Value</th></tr>'
    '<tr><td data-bbox="100 820 700 1000" data-confidence="0.99">भाग एक</td>'
    '<td colspan="2" data-bbox="700 820 1500 1000" data-confidence="0.41">भाग एक-अ</td></tr></table>'
)

LAYOUT = {"engine": "chandra", "format": "datalab_json", "page": {
    "block_type": "Page", "bbox": [0, 0, 1540, 2184], "children": [
        {"block_type": "PageHeader", "bbox": [130, 67, 1421, 111], "html": "<p>औ. भाग १-३६२</p>"},
        {"block_type": "Picture", "bbox": [717, 141, 802, 292],
         "html": '<img alt="State Emblem of India" src="x.jpg"/><div class="img-alt">State Emblem of India</div>'},
        {"block_type": "SectionHeader", "bbox": [177, 316, 1347, 395], "html": "<h1>महाराष्ट्र शासन राजपत्र</h1>"},
        {"block_type": "Text", "bbox": [103, 535, 1436, 578], "html": "<p>क्र. ५३ ]<br/>गुरुवार</p>"},
        {"block_type": "Text", "bbox": [0, 0, 10, 10], "html": "<p>  </p>"},
        {"block_type": "Table", "bbox": [100, 800, 1500, 1000], "html": TABLE_HTML},
    ]}}


def test_chandra_page_gives_typed_blocks_with_normalised_boxes():
    blocks = rb.blocks_from_chandra_page(LAYOUT, 1)
    assert [b["type"] for b in blocks] == ["paragraph", "image", "heading", "paragraph", "table"]
    header, picture, heading, text, table = blocks
    assert header["flags"] == ["page_header"]
    assert picture["text"] == "State Emblem of India"  # described once, not twice
    assert heading["text"] == "महाराष्ट्र शासन राजपत्र"
    assert text["text"] == "क्र. ५३ ]\nगुरुवार"
    assert heading["bbox"]["1"] == {"x": round(177 / 1540, 5), "y": round(316 / 2184, 5),
                                    "w": round((1347 - 177) / 1540, 5), "h": round((395 - 316) / 2184, 5)}
    # empty text blocks are dropped, not shown as blank cards
    assert len([b for b in blocks if b["type"] == "paragraph"]) == 2


def test_chandra_table_cells_keep_their_own_box_confidence_and_spans():
    table = rb.blocks_from_chandra_page(LAYOUT, 3)[-1]
    assert table["headers"] == ["पृष्ठे", "पृष्ठे (2)", "Value"]  # duplicate headings stay distinct
    row = table["rows"][0]
    assert [c["text"] for c in row["cells"]] == ["भाग एक", "भाग एक-अ", ""]  # colspan fills with an empty cell
    first = row["cells"][0]
    assert first["confidence"] == 0.99 and first["bbox"]["page"] == 3
    assert first["bbox"]["x"] == round(100 / 1540, 5) and first["regions"] == [first["bbox"]]
    assert table["confidence"] == round((0.99 + 0.41) / 2, 4)


def test_raw_pdfplumber_words_are_normalised_and_grouped_into_paragraphs():
    page = {"width": 600.0, "height": 800.0}
    words = [  # raw pdfplumber: points, top-left via top/bottom, bottom-origin y0/y1 ignored
        {"text": "First", "x0": 60, "x1": 90, "top": 100, "bottom": 110, "y0": 700, "y1": 690},
        {"text": "line", "x0": 95, "x1": 115, "top": 100, "bottom": 110, "y0": 700, "y1": 690},
        {"text": "second", "x0": 60, "x1": 100, "top": 112, "bottom": 122, "y0": 688, "y1": 678},
        {"text": "Far", "x0": 60, "x1": 80, "top": 400, "bottom": 410, "y0": 400, "y1": 390},
    ]
    blocks = rb.blocks_from_words(words, page, 2)
    assert [b["text"] for b in blocks] == ["First line\nsecond", "Far"]
    box = blocks[0]["bbox"]["2"]
    assert box["x"] == round(60 / 600, 5) and box["y"] == round(100 / 800, 5)


def test_two_columns_are_not_read_straight_across():
    words = [
        {"text": "left-a", "x0": 0.05, "y0": 0.10, "x1": 0.20, "y1": 0.115},
        {"text": "right-a", "x0": 0.60, "y0": 0.10, "x1": 0.80, "y1": 0.115},
        {"text": "left-b", "x0": 0.05, "y0": 0.118, "x1": 0.20, "y1": 0.133},
        {"text": "right-b", "x0": 0.60, "y0": 0.118, "x1": 0.80, "y1": 0.133},
    ]
    texts = [b["text"] for b in rb.blocks_from_words(words, {}, 1)]
    assert texts == ["left-a\nleft-b", "right-a\nright-b"]


def test_text_only_is_flagged_and_never_shattered():
    blocks = rb.blocks_from_text("One.\n\nTwo.", 4)
    assert [b["text"] for b in blocks] == ["One.", "Two."]
    assert all(b["flags"] == ["no_layout"] and b["bbox"]["4"]["w"] == 1 for b in blocks)
    many = "\n\n".join(f"row {i}" for i in range(rb.MAX_TEXT_ONLY_BLOCKS_PER_PAGE + 5))
    assert len(rb.blocks_from_text(many, 1)) == 1


def test_best_source_per_page_and_failed_pages_skipped():
    pages = [
        {"page_number": 1, "layout": LAYOUT, "text": "x"},
        {"page_number": 2, "words": [{"text": "hi", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.12}], "text": "hi"},
        {"page_number": 3, "text": "plain"},
        {"page_number": 4, "text": "Scanned page 4", "extraction_failed": True},
    ]
    blocks, used = rb.blocks_from_pages(pages)
    assert used == {"chandra": 1, "word_boxes": 1, "text_only": 1, "failed": 1}
    assert [b["order"] for b in blocks] == list(range(len(blocks)))
    assert rb.builder_name(used, has_facts=False) == "chandra"
    assert rb.builder_name(used, has_facts=True) == "facts"


def test_overlapping_search_chunks_are_stitched_without_repeats():
    a = "The Board of Wakfs published the list of properties in the gazette"
    b = "properties in the gazette on the 22nd of May."
    assert rb._dedupe_overlapping_chunks([a, b]) == a + " on the 22nd of May."


@pytest.mark.asyncio
async def test_review_document_built_from_archived_chandra_layout_and_edits_non_fact_cell():
    """Through the real service: no Facts, an archived Chandra result for
    this exact file -> chandra blocks; a table cell edit + revert works and
    the cell keeps its box and confidence."""
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
            db.add(Tenant(id=tenant_id, name=f"blocks {uuid.uuid4().hex[:6]}"))
            await db.flush()
            db.add(User(id=actor_id, tenant_id=tenant_id, email=f"b_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw"))
            file_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="gazette.pdf", status="indexed", pages_total_count=1)
            version = DocumentVersion(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1,
                                      s3_path="x/gazette.pdf", file_hash=file_hash, file_size_bytes=1,
                                      original_filename="gazette.pdf")
            db.add_all([doc, version])
            await db.flush()
            doc.current_version_id = version.id
            db.add(OCRArchive(content_hash=file_hash, ocr_engine="chandra",
                              pages=[{"page_number": 1, "text": "t", "layout": LAYOUT}]))
            await db.flush()

            out = await rs.get_review_document(db, tenant_id, doc.id, "it_admin")
            assert out["builder"] == "chandra"
            table = next(b for b in out["blocks"] if b["type"] == "table")
            cell = table["rows"][0]["cells"][1]
            assert cell["low_confidence"] is True and cell["bbox"]["page"] == 1  # 0.41 < 0.6, box kept

            out = await rs.edit_cell(db, tenant_id, doc.id, actor_id, "operator", out["version"],
                                     table["id"], table["rows"][0]["id"], 1, "भाग एक-ब")
            cell = next(b for b in out["blocks"] if b["type"] == "table")["rows"][0]["cells"][1]
            assert cell["edited"] and cell["original"] == "भाग एक-अ" and cell["bbox"]["page"] == 1
            out = await rs.revert(db, tenant_id, doc.id, actor_id, "operator", out["version"],
                                  table["id"], table["rows"][0]["id"], 1)
            assert out["is_clean"]

            heading = next(b for b in out["blocks"] if b["type"] == "heading")
            out = await rs.edit_text_block(db, tenant_id, doc.id, actor_id, "operator", out["version"], heading["id"], "राजपत्र")
            assert next(b for b in out["blocks"] if b["id"] == heading["id"])["edited"]
        finally:
            await db.rollback()
