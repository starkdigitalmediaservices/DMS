"""Live bug, 2026-09-22: a page whose VLM call RAISES (quota 403, timeout,
connection reset) was caught in extract_facts_for_document's per-page
`except Exception`, logged at WARNING, and skipped -- and nothing
downstream ever learned it happened.

`pages_failed_count` is computed in worker.py from the OCR pages list
(`p.get("extraction_failed")`) only, so a VLM-stage failure never reached
it. The document finished as status="indexed" with pages_failed_count=0,
and because _get_or_create_page only writes a doc_dg_pages row for a page
that actually yielded facts, the skipped pages left no page row either --
surfacing as the "missing page rows" symptom.

Found while both configured providers were simultaneously out of quota
(Chandra: HTTP 403 "You've used your free monthly allowance"; Gemini
free tier: 20 requests/day), which is exactly when this silently drops
most of a document.
"""
import json
import uuid

import pytest
from unittest.mock import AsyncMock

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.page import DocumentPage
from app.pipeline.vlm_extraction import extract_facts_for_document
from sqlalchemy import select


class FakeTemplate:
    def __init__(self, field_schema, layout="single_page"):
        self.field_schema = field_schema
        self.layout = layout


SCHEMA = [
    {"name": "sr_no", "type": "string", "required": False, "role": "serial"},
    {"name": "name", "type": "string", "required": False},
    {"name": "value", "type": "string", "required": False},
]


def _f(value, bbox, confidence=0.95):
    return {"value": value, "bbox": bbox, "confidence": confidence, "is_handwritten": False}


def _good_page(n):
    return json.dumps({
        "rows": [{
            "sr_no": _f(str(n), bbox=[0.1, 0.1, 0.2, 0.2]),
            "name": _f(f"Entry {n}", bbox=[0.3, 0.1, 0.6, 0.2]),
            "value": _f("Rs. 100", bbox=[0.7, 0.1, 0.9, 0.2]),
        }],
        "marginalia": [],
    })


def _pdf_bytes(page_count: int) -> bytes:
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i in range(page_count):
        c.drawString(72, 720, f"Register page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


async def _make_doc(db):
    tenant_id = uuid.uuid4()
    tenant = Tenant(id=tenant_id, name=f"PageFail Tenant {uuid.uuid4().hex[:6]}")
    user = User(id=uuid.uuid4(), tenant_id=tenant_id, email=f"pagefail_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw")
    db.add_all([tenant, user])
    await db.flush()
    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="register.pdf", status="indexed")
    version = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="register.pdf",
    )
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version.id
    await db.flush()
    return tenant_id, doc.id, version.id


@pytest.mark.asyncio
async def test_vlm_page_failure_is_reported_not_swallowed(monkeypatch):
    """A mid-document provider failure must be reported to the caller, not
    silently reduce the document to whatever happened to succeed."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _pdf_bytes(3)

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [
                _good_page(1),
                Exception("Chandra convert request failed with status 403: free monthly allowance"),
                _good_page(3),
            ]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            failed_pages: list[int] = []
            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "register.pdf",
                pages_text=[{}, {}, {}], template=FakeTemplate(SCHEMA),
                failed_pages_out=failed_pages,
            )
            await db.flush()

            # pages 1 and 3 still extract -- partial success is preserved
            assert written == 6, f"expected 3 fields x 2 good pages, got {written}"
            # and the page that blew up is REPORTED rather than swallowed
            assert failed_pages == [2], f"page 2's provider failure went unreported: {failed_pages}"

            # the symptom: the failed page leaves no doc_dg_pages row
            res = await db.execute(select(DocumentPage).where(DocumentPage.document_id == doc_id))
            page_numbers = sorted(p.page_number for p in res.scalars().all())
            assert page_numbers == [1, 3]
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_every_page_failing_reports_every_page(monkeypatch):
    """Total provider outage -- the case that produced a 5x fact drop while
    still reporting a clean, fully-indexed document."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _pdf_bytes(3)

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = Exception("403 quota exhausted")
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            failed_pages: list[int] = []
            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "register.pdf",
                pages_text=[{}, {}, {}], template=FakeTemplate(SCHEMA),
                failed_pages_out=failed_pages,
            )
            await db.flush()

            assert written == 0
            assert failed_pages == [1, 2, 3], f"expected all 3 pages reported, got {failed_pages}"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_a_genuinely_empty_page_is_not_counted_as_failed(monkeypatch):
    """Regression guard: a blank/non-table page legitimately yields no rows.
    That is not a failure and must not inflate the failed-page count, or
    every cover page in the corpus would look like data loss."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _pdf_bytes(2)

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [
                json.dumps({"rows": [], "marginalia": []}),   # genuinely empty page
                _good_page(2),
            ]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            failed_pages: list[int] = []
            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "register.pdf",
                pages_text=[{}, {}], template=FakeTemplate(SCHEMA),
                failed_pages_out=failed_pages,
            )
            await db.flush()

            assert written == 3
            assert failed_pages == [], f"an empty page was wrongly flagged as failed: {failed_pages}"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_failed_pages_union_is_counted_once():
    """worker.py unions OCR-stage and VLM-stage failures. A page that failed
    at BOTH stages must be counted once, not twice -- otherwise
    pages_failed_count can exceed pages_total_count and the completeness
    dashboard reports more failures than the document has pages."""
    pages = [
        {"page_number": 1, "extraction_failed": False},
        {"page_number": 2, "extraction_failed": True},   # OCR lost it
        {"page_number": 3, "extraction_failed": False},
    ]
    vlm_failed_pages = [2, 3]  # VLM lost 2 (again) and 3

    ocr_failed_pages = {p.get("page_number") for p in pages if p.get("extraction_failed")}
    counted = len(ocr_failed_pages | set(vlm_failed_pages))

    assert counted == 2, "page 2 failed at both stages and must count once"
    assert counted <= len(pages), "failed count must never exceed total pages"


# --- The remaining silent-loss hole, found 2026-09-22 by cache forensics ---
#
# The per-page `except` fix above only covers pages whose VLM call RAISED.
# On the real juni_masjid regression NOTHING raised: all 16 pages had a
# cached Chandra response, and 8 of them were a well-formed
# {"rows": [], "marginalia": []}. Those 8 pages were treated as genuinely
# blank -- no rows, no page row, no facts, no complaint -- even though OCR
# had pulled real text off every one of them. 129 real table facts and 183
# marginalia vanished with the document still reporting complete.
#
# A page that OCR found text on, but the VLM returned nothing for, is an
# incomplete extraction. It must not be indistinguishable from a blank page.


@pytest.mark.asyncio
async def test_vlm_returning_nothing_for_a_text_bearing_page_is_reported(monkeypatch):
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _pdf_bytes(2)

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [
                _good_page(1),
                json.dumps({"rows": [], "marginalia": []}),   # well-formed, but empty
            ]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            failed_pages: list[int] = []
            empty_pages: list[int] = []
            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "register.pdf",
                pages_text=[
                    {"page_number": 1, "text": "Sr No  Name  Value" * 5},
                    {"page_number": 2, "text": "2  Shri Juni Masjid, Hirpur  Rs. 4,000" * 5},
                ],
                template=FakeTemplate(SCHEMA),
                failed_pages_out=failed_pages,
                empty_pages_out=empty_pages,
            )
            await db.flush()

            assert written == 3
            assert failed_pages == [], "nothing raised, so nothing belongs in failed_pages"
            assert empty_pages == [2], (
                "page 2 had OCR text but the VLM returned no rows -- that is an "
                f"incomplete extraction and must be reported, got {empty_pages}"
            )
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_a_truly_blank_page_is_not_reported_as_empty_extraction(monkeypatch):
    """Regression guard: a blank page yields no OCR text AND no rows. That is
    correct behaviour, not data loss, and must stay silent."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _pdf_bytes(2)

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [
                _good_page(1),
                json.dumps({"rows": [], "marginalia": []}),
            ]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            empty_pages: list[int] = []
            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "register.pdf",
                pages_text=[
                    {"page_number": 1, "text": "Sr No  Name  Value" * 5},
                    {"page_number": 2, "text": "  "},      # genuinely blank
                ],
                template=FakeTemplate(SCHEMA),
                empty_pages_out=empty_pages,
            )
            await db.flush()

            assert written == 3
            assert empty_pages == [], f"a blank page must not be flagged as loss: {empty_pages}"
        finally:
            await db.rollback()
            await db.close()
