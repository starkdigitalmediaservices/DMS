"""extract_facts_for_document had no idempotency guard: re-running it against
a document that already held Facts INSERTed a second full set rather than
replacing them. The hazard was known and documented in
scripts/retry_wardha_extraction.py's docstring ("Delete existing doc_dg_facts
rows for the document first"), but the guard lived only in that comment -- any
re-ingest, retry or backfill that forgot it silently doubled the document.

This blocked re-ingesting the pre-row_group_id documents (Wardha, Ambajogai,
juni_masjid, Aurangabad-Shia), which is the only way to give them the row
identity that entity-relationship inference needs.

Human-verified facts are deliberately NOT purged: a re-extraction must never
destroy work a reviewer has signed off on.
"""
import json
import uuid

import pytest
from unittest.mock import AsyncMock
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.pipeline.vlm_extraction import extract_facts_for_document


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


def _page():
    return json.dumps({
        "rows": [{
            "sr_no": _f("1", bbox=[0.1, 0.1, 0.2, 0.2]),
            "name": _f("Abdul Rahman Sheikh", bbox=[0.3, 0.1, 0.6, 0.2]),
            "value": _f("Rs. 100", bbox=[0.7, 0.1, 0.9, 0.2]),
        }],
        "marginalia": [],
    })


def _pdf_bytes():
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 720, "Register page 1")
    c.showPage()
    c.save()
    return buf.getvalue()


async def _make_doc(db):
    tid = uuid.uuid4()
    db.add_all([
        Tenant(id=tid, name=f"Idem Tenant {uuid.uuid4().hex[:6]}"),
        User(id=uuid.uuid4(), tenant_id=tid, email=f"idem_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw"),
    ])
    await db.flush()
    doc = Document(id=uuid.uuid4(), tenant_id=tid, title="register.pdf", status="indexed")
    ver = DocumentVersion(
        id=uuid.uuid4(), tenant_id=tid, document_id=doc.id, version_number=1, s3_path="x",
        file_hash=uuid.uuid4().hex, file_size_bytes=1, original_filename="register.pdf",
    )
    db.add_all([doc, ver])
    await db.flush()
    doc.current_version_id = ver.id
    await db.flush()
    return tid, doc.id, ver.id


@pytest.mark.asyncio
async def test_re_extraction_replaces_rather_than_duplicates(monkeypatch):
    import app.pipeline.vlm_extraction as vlm_mod
    pdf = _pdf_bytes()

    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.return_value = _page()
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            first = await extract_facts_for_document(
                db, tid, did, vid, pdf, "register.pdf", [{}], FakeTemplate(SCHEMA))
            await db.flush()
            assert first == 3

            second = await extract_facts_for_document(
                db, tid, did, vid, pdf, "register.pdf", [{}], FakeTemplate(SCHEMA))
            await db.flush()
            assert second == 3

            total = (await db.execute(select(Fact).where(Fact.document_id == did))).scalars().all()
            assert len(total) == 3, (
                f"re-extraction duplicated facts: expected 3 after two runs, got {len(total)}"
            )
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_human_verified_facts_survive_re_extraction(monkeypatch):
    """A reviewer's sign-off must never be destroyed by a re-run."""
    import app.pipeline.vlm_extraction as vlm_mod
    pdf = _pdf_bytes()

    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)
            actor = (await db.execute(select(User).where(User.tenant_id == tid))).scalars().first()

            verified = Fact(
                id=uuid.uuid4(), tenant_id=tid, document_id=did, version_id=vid,
                field_name="name", value={"v": "Human Confirmed Name"}, confidence=1.0,
                status="verified", verified_by_actor_id=actor.id,
            )
            db.add(verified)
            await db.flush()
            verified_id = verified.id

            vlm = AsyncMock()
            vlm.extract_structured.return_value = _page()
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            await extract_facts_for_document(
                db, tid, did, vid, pdf, "register.pdf", [{}], FakeTemplate(SCHEMA))
            await db.flush()

            still_there = (await db.execute(select(Fact).where(Fact.id == verified_id))).scalar_one_or_none()
            assert still_there is not None, "a human-verified fact was destroyed by re-extraction"
            assert still_there.value["v"] == "Human Confirmed Name"
        finally:
            await db.rollback()
            await db.close()


@pytest.mark.asyncio
async def test_total_provider_failure_does_not_destroy_existing_facts(monkeypatch):
    """The regression this guard was rewritten for, 2026-09-23.

    The first version purged before extraction ran. A re-extraction of
    juni_masjid hit provider quota on all 16 pages, wrote nothing, and the
    document dropped from 392 facts to the 8 a human had verified -- the
    prior extraction deleted by a guard whose replacement never arrived.

    A total provider outage must leave existing facts alone.
    """
    import app.pipeline.vlm_extraction as vlm_mod
    pdf = _pdf_bytes()

    async with AsyncSessionLocal() as db:
        try:
            tid, did, vid = await _make_doc(db)

            vlm = AsyncMock()
            vlm.extract_structured.return_value = _page()
            monkeypatch.setattr(vlm_mod, "get_vlm_provider", lambda: vlm)

            await extract_facts_for_document(
                db, tid, did, vid, pdf, "register.pdf", [{}], FakeTemplate(SCHEMA))
            await db.flush()
            before = (await db.execute(select(Fact).where(Fact.document_id == did))).scalars().all()
            assert len(before) == 3

            # now every page fails, exactly as the quota outage did.
            # The archive cache must miss for the provider to be reached at
            # all -- in the real incident AI_VLM_PROVIDER had been switched,
            # and the cache key includes the provider, so nothing replayed.
            async def _always_miss(db_, cache_key):
                return None
            monkeypatch.setattr(vlm_mod, "get_cached_vlm_response", _always_miss)

            vlm.extract_structured.side_effect = Exception(
                "429 quota exhausted for this project")

            failed: list[int] = []
            written = await extract_facts_for_document(
                db, tid, did, vid, pdf, "register.pdf", [{}], FakeTemplate(SCHEMA),
                failed_pages_out=failed)
            await db.flush()

            assert written == 0
            assert failed == [1]

            after = (await db.execute(select(Fact).where(Fact.document_id == did))).scalars().all()
            assert len(after) == 3, (
                f"a failed re-extraction destroyed {3 - len(after)} existing facts "
                "-- the guard purged without a replacement"
            )
        finally:
            await db.rollback()
            await db.close()
