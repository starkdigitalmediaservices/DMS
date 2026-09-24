"""Keep search in step with review-screen corrections.

Rules agreed for the review screen:
  * One search chunk per corrected block, marked
    chunk_metadata.source = "review_edit" -- updated in place on every save
    (never one chunk per edit), and deleted as soon as that block has no
    correction left (a single revert or revert-all).
  * The original OCR chunks are never touched, so a document stays findable
    by its old reading as well as its corrected one.

The chunk text is written in the same transaction as the correction, so
keyword and fuzzy search see it at once. Its embedding (semantic search) is
computed by the worker a moment later (embed_review_chunks_task) -- a local
BGE-M3 embed costs 0.3-1.2 s warm and ~24 s cold, too slow to add to every
save. Until then the chunk is simply absent from the vector leg (which skips
NULL embeddings).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Tuple
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_version import DocumentVersion

logger = logging.getLogger(__name__)

REVIEW_SOURCE = "review_edit"
# Review chunks sort after any real chunk of the document.
_CHUNK_INDEX_BASE = 1_000_000


def _label(header: str) -> str:
    return header.replace("_", " ").strip().capitalize() if header and header == header.lower() else header


def desired_chunks(review_doc: Dict[str, Any]) -> Dict[str, Tuple[str, int]]:
    """{block_id: (text, page)} for every block that currently carries a
    correction, built from exactly what the review screen shows."""
    out: Dict[str, Tuple[str, int]] = {}
    for block in review_doc.get("blocks", []):
        if block.get("deleted"):
            continue
        page = (block.get("source_pages") or [1])[0]
        if block.get("type") == "table":
            headers = block.get("headers") or []
            lines = []
            for row in block.get("rows") or []:
                if row.get("deleted") or not (row.get("added") or any(c.get("edited") for c in row["cells"])):
                    continue
                parts = [f"{_label(h)}: {c['text']}" for h, c in zip(headers, row["cells"]) if (c.get("text") or "").strip()]
                if parts:
                    lines.append("; ".join(parts))
                    page = row.get("page") or page
            if lines:
                out[block["id"]] = ("\n".join(lines), page)
        elif block.get("edited") or block.get("added"):
            text = (block.get("text") or "").strip()
            if text:
                out[block["id"]] = (text, page)
    return out


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


async def sync_review_chunks(db: AsyncSession, doc: Document, review_doc: Dict[str, Any]) -> Tuple[List[str], bool]:
    """Upsert / delete this document's review_edit chunks to match
    review_doc. Returns (ids of chunks whose text changed -- to be embedded,
    whether the index changed at all -- to drop cached search results)."""
    want = desired_chunks(review_doc)
    res = await db.execute(
        select(Chunk).where(
            Chunk.tenant_id == doc.tenant_id,
            Chunk.document_id == doc.id,
            Chunk.chunk_metadata["source"].astext == REVIEW_SOURCE,
        )
    )
    have = {c.chunk_metadata.get("block_id"): c for c in res.scalars().all()}

    stale = [c.id for block_id, c in have.items() if block_id not in want]
    if stale:
        await db.execute(delete(Chunk).where(Chunk.id.in_(stale)))

    changed: List[str] = []
    version = None
    for i, (block_id, (text, page)) in enumerate(sorted(want.items())):
        chunk = have.get(block_id)
        if chunk is not None:
            if chunk.content != text:
                chunk.content = text
                chunk.page_number = page
                chunk.embedding = None  # stale until the worker re-embeds it
                changed.append(str(chunk.id))
            continue
        if version is None and doc.current_version_id:
            version = await db.get(DocumentVersion, doc.current_version_id)
        chunk = Chunk(
            document_id=doc.id,
            version_id=doc.current_version_id,
            tenant_id=doc.tenant_id,
            content=text,
            page_number=page,
            chunk_index=_CHUNK_INDEX_BASE + i,
            embedding=None,
            chunk_metadata={"source": REVIEW_SOURCE, "block_id": block_id},
            s3_path=version.s3_path if version else "",
        )
        db.add(chunk)
        await db.flush()
        changed.append(str(chunk.id))
    await db.flush()
    return changed, bool(changed or stale)


def enqueue_embedding(chunk_ids: List[str]) -> None:
    """Best-effort: a lost task only delays semantic search for that chunk;
    keyword and fuzzy search already have its text."""
    if not chunk_ids:
        return
    try:
        from app.tasks.worker import embed_review_chunks_task

        # Small delay so the request's own commit lands first; the task
        # also retries if it arrives before the chunk is visible.
        embed_review_chunks_task.apply_async(args=[chunk_ids], countdown=2)
    except Exception as e:  # pragma: no cover - broker outage
        logger.warning("Could not queue embedding for review chunks %s: %s", chunk_ids, e)


async def embed_chunks(db: AsyncSession, chunk_ids: List[str], embed) -> Tuple[int, List[str]]:
    """Worker side: embed each chunk's CURRENT text and store it only if the
    text is still the same when writing -- a slow embed of an older
    correction can never overwrite the embedding of a newer one.
    Returns (embedded, missing_ids)."""
    from sqlalchemy import text as sql

    ids = [UUID(i) for i in chunk_ids]
    rows = (await db.execute(select(Chunk.id, Chunk.content).where(Chunk.id.in_(ids)))).all()
    found = {str(r[0]) for r in rows}
    missing = [i for i in chunk_ids if i not in found]
    if not rows:
        return 0, missing
    vectors = await embed([r[1] for r in rows])
    done = 0
    for (chunk_id, content), vec in zip(rows, vectors):
        result = await db.execute(
            sql("UPDATE doc_dg_chunks SET embedding = CAST(:v AS vector) "
                "WHERE id = :id AND md5(content) = :h"),
            {"v": "[" + ",".join(str(float(x)) for x in vec) + "]", "id": chunk_id, "h": content_hash(content)},
        )
        done += result.rowcount or 0
    await db.commit()
    if done:
        # semantic results for this tenant just changed
        from app.services.cache_service import invalidate_tenant_cache

        tenants = (await db.execute(select(Chunk.tenant_id).where(Chunk.id.in_(ids)).distinct())).scalars().all()
        for t in tenants:
            await invalidate_tenant_cache(str(t))
    return done, missing
