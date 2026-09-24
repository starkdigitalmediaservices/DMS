"""Review corrections kept in step with search (app/services/review_search.py).

Real Postgres. The review service's own session is used, so the chunk rows
and their generated tsvector columns are exactly what search queries.
"""
import uuid

import pytest
from sqlalchemy import select, text, update

from app.database import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.page import DocumentPage
from app.models.tenant import Tenant
from app.models.user import User
from app.services import review_search
from app.services import review_service as rs

TABLE = "b-facts-table"


async def _setup(db):
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"rsearch {uuid.uuid4().hex[:6]}"))
    await db.flush()
    db.add(User(id=actor_id, tenant_id=tenant_id, email=f"rs_{uuid.uuid4().hex[:6]}@test.com", hashed_password="pw"))
    doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, title="register.pdf", status="indexed", pages_total_count=1)
    version = DocumentVersion(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_number=1,
                              s3_path="x/register.pdf", file_hash="h", file_size_bytes=1, original_filename="register.pdf")
    db.add_all([doc, version])
    await db.flush()
    doc.current_version_id = version.id
    page = DocumentPage(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
                        page_number=1, width=600, height=800)
    db.add(page)
    # the original OCR chunk -- must never be modified by review sync
    db.add(Chunk(document_id=doc.id, version_id=version.id, tenant_id=tenant_id, content="Survey 12/3 Abdul Rahim",
                 page_number=1, chunk_index=0, embedding=None, chunk_metadata={}, s3_path="x/register.pdf"))
    await db.flush()
    row = uuid.uuid4()
    facts = []
    for name, value, x in (("owner_name", "Abdul Rahim", 0.1), ("survey_number", "12/3", 0.5)):
        f = Fact(id=uuid.uuid4(), tenant_id=tenant_id, document_id=doc.id, version_id=version.id,
                 field_name=name, value={"v": value}, confidence=0.9, status="machine", row_group_id=row)
        db.add(f)
        await db.flush()
        db.add(FactRegion(id=uuid.uuid4(), tenant_id=tenant_id, fact_id=f.id, page_id=page.id, x0=x, y0=0.2, x1=x + 0.3, y1=0.25))
        facts.append(f)
    await db.flush()
    return tenant_id, actor_id, doc.id


async def _review_chunks(db, doc_id):
    res = await db.execute(select(Chunk).where(Chunk.document_id == doc_id, Chunk.chunk_metadata["source"].astext == "review_edit"))
    return list(res.scalars().all())


async def _matches(db, doc_id, word):
    return (await db.execute(text(
        "SELECT count(*) FROM doc_dg_chunks WHERE document_id = :d AND content_tsv_simple @@ plainto_tsquery('simple', :w)"
    ), {"d": doc_id, "w": word})).scalar_one()


def test_desired_chunks_cover_only_corrected_blocks_and_rows():
    doc = {"blocks": [
        {"id": "t", "type": "table", "headers": ["owner_name", "Village"], "source_pages": [2], "rows": [
            {"id": "r1", "page": 3, "cells": [{"text": "A", "edited": False}, {"text": "B", "edited": True}]},
            {"id": "r2", "page": 3, "cells": [{"text": "C", "edited": False}, {"text": "D", "edited": False}]},
            {"id": "r3", "page": 4, "added": True, "cells": [{"text": "E", "edited": False}, {"text": "", "edited": False}]},
            {"id": "r4", "deleted": True, "cells": [{"text": "F", "edited": True}]},
        ]},
        {"id": "p1", "type": "paragraph", "source_pages": [1], "text": "fixed text", "edited": True},
        {"id": "p2", "type": "paragraph", "source_pages": [1], "text": "untouched", "edited": False},
        {"id": "p3", "type": "heading", "source_pages": [1], "text": "new heading", "added": True},
        {"id": "p4", "type": "paragraph", "source_pages": [1], "text": "gone", "edited": True, "deleted": True},
    ]}
    want = review_search.desired_chunks(doc)
    assert want == {
        "t": ("Owner name: A; Village: B\nOwner name: E", 4),
        "p1": ("fixed text", 1),
        "p3": ("new heading", 1),
    }


@pytest.mark.asyncio
async def test_one_chunk_per_block_upserted_and_removed_on_revert_old_reading_kept():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id = await _setup(db)
            d = await rs.get_review_document(db, tenant_id, doc_id, "it_admin")
            row_id = d["blocks"][0]["rows"][0]["id"]

            d = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", d["version"], TABLE, row_id, 1, "12/8")
            chunks = await _review_chunks(db, doc_id)
            assert len(chunks) == 1 and "Survey number: 12/8" in chunks[0].content and chunks[0].embedding is None

            # a second correction in the same block updates that one chunk
            d = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", d["version"], TABLE, row_id, 0, "Abdul Rahman")
            chunks = await _review_chunks(db, doc_id)
            assert len(chunks) == 1 and "Owner name: Abdul Rahman" in chunks[0].content

            # both readings are findable: corrected (review chunk) and original (OCR chunk)
            assert await _matches(db, doc_id, "12/8") == 1
            assert await _matches(db, doc_id, "12/3") == 1
            original = (await db.execute(select(Chunk).where(Chunk.document_id == doc_id, Chunk.chunk_index == 0))).scalars().one()
            assert original.content == "Survey 12/3 Abdul Rahim"

            # undoing one cell keeps the chunk (still a correction), undoing the last removes it
            d = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", d["version"], TABLE, row_id, 1)
            chunks = await _review_chunks(db, doc_id)
            assert len(chunks) == 1 and "12/8" not in chunks[0].content
            d = await rs.revert(db, tenant_id, doc_id, actor_id, "operator", d["version"], TABLE, row_id, 0)
            assert await _review_chunks(db, doc_id) == []
            assert d["is_clean"]
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_added_text_is_searchable_and_revert_all_clears_every_review_chunk():
    async with AsyncSessionLocal() as db:
        try:
            tenant_id, actor_id, doc_id = await _setup(db)
            d = await rs.get_review_document(db, tenant_id, doc_id, "it_admin")
            d = await rs.add_block(db, tenant_id, doc_id, actor_id, "operator", d["version"], "paragraph",
                                   "Missed stamp Tahsildar Ambajogai", None, 1)
            d = await rs.edit_cell(db, tenant_id, doc_id, actor_id, "operator", d["version"], TABLE,
                                   d["blocks"][0]["rows"][0]["id"], 1, "99")
            assert len(await _review_chunks(db, doc_id)) == 2
            assert await _matches(db, doc_id, "Tahsildar") == 1

            d = await rs.revert_all(db, tenant_id, doc_id, actor_id, "it_admin", d["version"])
            assert await _review_chunks(db, doc_id) == []
            assert await _matches(db, doc_id, "12/3") == 1  # original reading still indexed
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_embedding_is_only_stored_if_the_text_is_unchanged():
    """A slow embed of an older correction must not overwrite a newer one."""
    async with AsyncSessionLocal() as db:
        tenant_id, _, doc_id = await _setup(db)
        chunk = Chunk(document_id=doc_id, version_id=(await db.get(Document, doc_id)).current_version_id,
                      tenant_id=tenant_id, content="Owner name: A", page_number=1, chunk_index=1_000_000,
                      embedding=None, chunk_metadata={"source": "review_edit", "block_id": "t"}, s3_path="x")
        db.add(chunk)
        await db.commit()
        chunk_id = str(chunk.id)

        async def embed_while_text_changes(texts):
            # the reviewer saves a newer correction while this embed runs
            await db.execute(update(Chunk).where(Chunk.id == chunk.id).values(content="Owner name: B"))
            return [[0.1] * 1024 for _ in texts]

        done, missing = await review_search.embed_chunks(db, [chunk_id], embed_while_text_changes)
        assert (done, missing) == (0, [])
        refreshed = (await db.execute(select(Chunk.embedding).where(Chunk.id == chunk.id))).scalar_one()
        assert refreshed is None

        async def embed(texts):
            return [[0.2] * 1024 for _ in texts]

        done, missing = await review_search.embed_chunks(db, [chunk_id, str(uuid.uuid4())], embed)
        assert done == 1 and len(missing) == 1
        stored = (await db.execute(select(Chunk.embedding).where(Chunk.id == chunk.id))).scalar_one()
        assert stored is not None and abs(float(stored[0]) - 0.2) < 1e-6
