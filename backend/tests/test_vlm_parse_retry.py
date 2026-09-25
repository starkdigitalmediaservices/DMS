"""T31/T32 follow-up (see docs/testing/T31_T32_regression_corpus_notes.md): a real 1973
gazette's dense left-hand page returned malformed JSON on 3 separate live
attempts, at a different position each time -- model flakiness, not a
parsing bug. _call_vlm_with_parse_retry() retries the VLM call itself
(bypassing the cache after attempt 1, since the cache key is a pure
function of file+page+prompt and would otherwise just replay the same bad
response forever) up to VLM_PARSE_RETRY_ATTEMPTS times."""
import json
import uuid

import pytest
from unittest.mock import AsyncMock

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.pipeline.vlm_extraction import extract_facts_for_document, VLM_PARSE_RETRY_ATTEMPTS
from sqlalchemy import select


class FakeTemplate:
    def __init__(self, field_schema, layout="single"):
        self.field_schema = field_schema
        self.layout = layout


SCHEMA = [{"name": "owner_name", "type": "text"}]
GOOD_RESPONSE = json.dumps({
    "rows": [{"owner_name": {"value": "Priya Sharma", "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.9}}],
    "marginalia": [],
})
MALFORMED_RESPONSE = '{"rows": [{"owner_name": {"value": "Trees\nValuation'  # truncated, unbalanced brace


def _one_page_pdf_bytes(seed: str = "") -> bytes:
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 720, f"Register page {seed}")
    c.showPage()
    c.save()
    return buf.getvalue()


async def _make_doc(db):
    tenant_id = uuid.uuid4()
    tenant = Tenant(id=tenant_id, name=f"RetryTest Tenant {uuid.uuid4().hex[:6]}")
    user = User(id=uuid.uuid4(), tenant_id=tenant_id, email=f"retrytest_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw")
    db.add_all([tenant, user])
    await db.flush()
    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="retry.pdf", status="indexed")
    version = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="retry.pdf",
    )
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version.id
    await db.flush()
    return tenant_id, doc.id, version.id


@pytest.mark.asyncio
async def test_malformed_first_response_retries_and_succeeds(monkeypatch):
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _one_page_pdf_bytes("retry-success")

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)
            template = FakeTemplate(SCHEMA)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [MALFORMED_RESPONSE, GOOD_RESPONSE]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "retry.pdf", pages_text=[{}], template=template,
            )
            await db.commit()

            assert written == 1
            assert vlm.extract_structured.call_count == 2  # one bad, one retry that succeeded
            res = await db.execute(select(Fact).where(Fact.document_id == doc_id, Fact.field_name == "owner_name"))
            assert res.scalar_one().value["v"] == "Priya Sharma"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_all_attempts_malformed_gives_up_without_crashing(monkeypatch):
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _one_page_pdf_bytes("retry-exhausted")

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)
            template = FakeTemplate(SCHEMA)

            vlm = AsyncMock()
            vlm.extract_structured.side_effect = [MALFORMED_RESPONSE] * VLM_PARSE_RETRY_ATTEMPTS
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            written = await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "retry.pdf", pages_text=[{}], template=template,
            )
            await db.commit()

            assert written == 0  # never crashed -- degraded to "nothing extracted", matching T22's best-effort contract
            assert vlm.extract_structured.call_count == VLM_PARSE_RETRY_ATTEMPTS
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_successful_retry_rewrites_the_cache(monkeypatch):
    """A retry that succeeds must overwrite the original bad cache entry --
    otherwise every later run of the same page keeps hitting the same
    malformed response and paying the retry cost forever."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf_bytes = _one_page_pdf_bytes("retry-cache-fix")

    async with AsyncSessionLocal() as db:
        try:
            tenant_id, doc_id, version_id = await _make_doc(db)
            template = FakeTemplate(SCHEMA)

            first_vlm = AsyncMock()
            first_vlm.extract_structured.side_effect = [MALFORMED_RESPONSE, GOOD_RESPONSE]
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: first_vlm)

            await extract_facts_for_document(
                db, tenant_id, doc_id, version_id, pdf_bytes, "retry.pdf", pages_text=[{}], template=template,
            )
            await db.commit()
            assert first_vlm.extract_structured.call_count == 2

            # Fresh document, same file bytes -> same cache key. A second
            # VLM mock that would return something different if actually
            # called proves the corrected response, not the original bad
            # one, is what got cached.
            tenant_id_2, doc_id_2, version_id_2 = await _make_doc(db)
            second_vlm = AsyncMock()
            second_vlm.extract_structured.return_value = json.dumps({
                "rows": [{"owner_name": {"value": "Should Not Be Used", "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.9}}],
                "marginalia": [],
            })
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: second_vlm)

            written_2 = await extract_facts_for_document(
                db, tenant_id_2, doc_id_2, version_id_2, pdf_bytes, "retry.pdf", pages_text=[{}], template=template,
            )
            await db.commit()

            assert written_2 == 1
            assert second_vlm.extract_structured.call_count == 0  # cache hit on the CORRECTED response
            res = await db.execute(select(Fact).where(Fact.document_id == doc_id_2, Fact.field_name == "owner_name"))
            assert res.scalar_one().value["v"] == "Priya Sharma"  # not "Should Not Be Used"
        finally:
            await db.rollback()
            await db.close()
