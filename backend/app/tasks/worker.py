import asyncio
import hashlib
import json
import logging
import os
import uuid
from uuid import UUID

from celery import Celery
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from ..ai.base import Message
from ..ai.factory import get_embed_provider, get_llm_provider
from ..models.chunk import Chunk as DBChunk
from ..models.document import Document
from ..models.document_version import DocumentVersion
from ..models.metadata_item import MetadataItem
from ..models.metadata_item_region import MetadataItemRegion
from ..ocr.factory import get_ocr_provider
from ..pipeline.chunker import TextChunker
from ..services.storage_service import download_file, upload_file, convert_to_pdfa
from ..services.config_service import get_int, get_float
from ..services.extraction_archive_service import get_cached_ocr, record_ocr
from ..services import duplicate_service
from ..services.source_location_service import locate_value_in_pages
from ..config import settings

celery_app = Celery(
    "worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    broker_connection_retry_on_startup=True
)

logger = logging.getLogger(__name__)


def _new_task_db_session_factory():
    """A fresh, dedicated engine + session factory for one Celery task
    invocation. Each task runs via asyncio.run() (a brand-new event loop
    every time), but app.database's AsyncSessionLocal is bound to one
    shared, pooled engine created once at process start — a connection
    checked out during one task's event loop can be handed to a LATER
    task's different (by-then-closed) loop, raising 'RuntimeError: Event
    loop is closed'. A dedicated per-task engine, disposed at the end of
    the task, avoids that entirely.

    Real bug: fixed once (2026-07-22, commit fb6eecf) via exactly this
    pattern, then silently reverted 5 days later (2026-07-27, commit
    57b8029's "atomic ingestion pipeline" refactor) back to the shared
    AsyncSessionLocal — the refactor's author wasn't aware they were
    undoing the earlier fix. Confirmed live in worker logs: 36 real
    'Event loop is closed' tracebacks during real document ingestion.
    """
    task_engine = create_async_engine(settings.postgres_url)
    TaskSession = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)
    return task_engine, TaskSession


async def extract_metadata(text: str) -> dict:
    prompt = f"""
Extract the following metadata from the text below. 
Return ONLY a valid JSON object with these keys: "title", "author", "date", "document_type", "key_topics" (list of strings), "summary".
If a field is not found, use null or empty list.

Text snippet:
{text[:4000]}
"""
    try:
        llm = get_llm_provider()
        resp = await llm.complete([Message(role="user", content=prompt)])

        # simple cleanup for markdown json blocks
        clean_resp = resp.strip()
        if clean_resp.startswith("```json"):
            clean_resp = clean_resp[7:]
        if clean_resp.endswith("```"):
            clean_resp = clean_resp[:-3]
        return json.loads(clean_resp.strip())
    except Exception as e:
        logger.error(f"Metadata extraction failed: {e}")
        return {}


async def _ingest_document_task_async(document_id_str: str, version_id_str: str, s3_path: str, tenant_id_str: str) -> None:
    """Celery task for full ingestion pipeline with ACID Atomicity: OCR → chunk → embed → store."""
    task_engine, TaskSession = _new_task_db_session_factory()
    try:
        document_id = UUID(document_id_str)
        version_id = UUID(version_id_str)
        tenant_id = UUID(tenant_id_str)

        # 1. Download file
        file_bytes = await download_file(s3_path)
        filename = os.path.basename(s3_path)
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # 1b. T41 — PDF/A-2b rendition, mandatory-on-ingest, original kept
        # unchanged (only the pdf source format is convertible; other
        # formats — docx/xlsx/images — aren't in T41's PDF/A scope).
        # Best-effort: a failed rendition never blocks ingestion of the
        # original, which is what's actually indexed either way.
        pdfa_s3_path = None
        if filename.lower().endswith(".pdf"):
            try:
                pdfa_bytes = await convert_to_pdfa(file_bytes)
                if pdfa_bytes:
                    pdfa_key = f"{os.path.dirname(s3_path)}/pdfa_{os.path.basename(s3_path)}"
                    await upload_file(pdfa_bytes, pdfa_key, "application/pdf")
                    pdfa_s3_path = pdfa_key
                    logger.info(f"T41 PDF/A-2b rendition created for document {document_id_str}: {pdfa_key}")
                else:
                    logger.warning(f"T41 PDF/A-2b conversion returned nothing for document {document_id_str}")
            except Exception as pdfa_err:
                logger.warning(f"T41 PDF/A-2b conversion skipped for document {document_id_str}: {pdfa_err}")

        # 2. OCR — TS3: an unchanged file (by content hash) under the same
        # OCR engine is never re-OCR'd; reprocessing the same upload after a
        # chunking/parsing fix replays the archived response for free.
        pages = None
        try:
            async with TaskSession() as cache_db:
                pages = await get_cached_ocr(cache_db, file_hash, settings.ai_ocr_provider)
        except Exception as cache_err:
            logger.warning(f"TS3 OCR cache lookup failed for {document_id_str}: {cache_err}")

        if pages is not None:
            logger.info(f"TS3 OCR cache hit for document {document_id_str} (hash {file_hash[:12]}...)")
        else:
            ocr = get_ocr_provider()
            pages = await ocr.extract_pages(file_bytes, filename)
            try:
                async with TaskSession() as cache_db:
                    await record_ocr(cache_db, file_hash, settings.ai_ocr_provider, pages)
                    await cache_db.commit()
            except Exception as cache_err:
                logger.warning(f"TS3 OCR cache write failed for {document_id_str}: {cache_err}")

        # 3. Chunk
        chunk_size = await get_int("chunk_size_tokens", 512)
        chunk_overlap = await get_int("chunk_overlap_tokens", 64)
        chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = chunker.chunk_pages(pages)

        if not chunks or all(p.get("extraction_failed") for p in pages):
            raise ValueError(
                "No readable text could be extracted from this document "
                f"using the configured OCR provider (AI_OCR_PROVIDER={settings.ai_ocr_provider}). "
                "The file may be corrupt, empty, or an image quality the "
                "OCR model can't read. Local options: paddleocr. "
                "API-based options: chandra, llamaparse."
            )

        # TS2 — data-loss audit: does every word OCR read survive into the
        # chunks about to be stored (the search/chat/viewer-facing surface)?
        # Best-effort, same as every other optional pipeline stage here —
        # a failure here must never block ingestion.
        data_loss_result = None
        try:
            from app.services.data_loss_audit import audit_pages_vs_chunks
            data_loss_result = audit_pages_vs_chunks(pages, [c.content for c in chunks])
            if not data_loss_result.passed:
                logger.warning(
                    f"TS2 data-loss audit: document {document_id_str} lost "
                    f"{data_loss_result.missing_count}/{data_loss_result.total_words} words "
                    f"({data_loss_result.loss_ratio:.2%}) between OCR and stored chunks"
                )
        except Exception as audit_err:
            logger.warning(f"TS2 data-loss audit skipped for document {document_id_str}: {audit_err}")

        # TS6 — page-furniture detection: flags (never removes) running
        # headers/footers by position stability. Purely informational,
        # same best-effort contract as every other optional stage here.
        furniture_candidates = None
        try:
            from app.services.page_furniture_service import detect_page_furniture
            furniture_candidates = detect_page_furniture(pages)
            if furniture_candidates:
                logger.info(f"TS6 page-furniture: document {document_id_str} has {len(furniture_candidates)} candidate(s)")
        except Exception as furniture_err:
            logger.warning(f"TS6 page-furniture detection skipped for document {document_id_str}: {furniture_err}")

        # 4. Embed chunks (batched for local BGE-M3 model / API providers)
        embed_provider = get_embed_provider()
        EMBED_BATCH_SIZE = 20
        EMBED_BATCH_DELAY = 0.1
        MAX_RETRIES = 3

        chunk_texts = [c.content for c in chunks]
        embeddings = []
        for batch_start in range(0, len(chunk_texts), EMBED_BATCH_SIZE):
            batch = chunk_texts[batch_start:batch_start + EMBED_BATCH_SIZE]
            for attempt in range(MAX_RETRIES):
                try:
                    batch_embeddings = await embed_provider.embed(batch)
                    embeddings.extend(batch_embeddings)
                    break
                except Exception as embed_err:
                    is_rate_limit = "429" in str(embed_err)
                    try:
                        import cohere as _cohere
                        is_rate_limit = is_rate_limit or isinstance(embed_err, _cohere.TooManyRequestsError)
                    except Exception:
                        pass
                    if is_rate_limit and attempt < MAX_RETRIES - 1:
                        wait = 2 ** attempt * 5
                        logger.warning(f"Embed rate limited (attempt {attempt+1}/{MAX_RETRIES}), retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise
            if batch_start + EMBED_BATCH_SIZE < len(chunk_texts):
                await asyncio.sleep(EMBED_BATCH_DELAY)

        # 5. Extract metadata & scan quality assessment
        full_text = " ".join([p.get("text", "") for p in pages])
        meta_dict = await extract_metadata(full_text)

        quality_report = None
        ext = os.path.splitext(filename)[1].lower()
        if ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"] or (len(file_bytes) > 4 and file_bytes[:4] in [b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\x89PNG"]):
            try:
                from app.services.scanner_connector import assess_scan_quality
                quality_report = assess_scan_quality(file_bytes)
            except Exception as q_err:
                logger.warning(f"Scan quality check failed during worker ingestion: {q_err}")

        # 6. ATOMIC DATABASE TRANSACTION (All-or-Nothing Commit)
        # All database writes (chunks, metadata, document status) occur inside a single atomic transaction.
        async with TaskSession() as db:
            async with db.begin():
                await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": tenant_id_str})
                # Purge pre-existing chunks and non-quality metadata for this version/document
                await db.execute(delete(DBChunk).where(DBChunk.version_id == version_id))
                await db.execute(
                    delete(MetadataItem).where(
                        MetadataItem.document_id == document_id,
                        MetadataItem.key.not_in(["quality_flag", "quality_report"]),
                    )
                )

                # Insert chunks
                for idx, chunk in enumerate(chunks):
                    db_chunk = DBChunk(
                        document_id=document_id,
                        version_id=version_id,
                        tenant_id=tenant_id,
                        content=chunk.content,
                        page_number=chunk.page_number,
                        chunk_index=chunk.chunk_index,
                        embedding=embeddings[idx],
                        chunk_metadata={"token_count": chunk.token_count, "bbox": chunk.bbox, "word_regions": chunk.word_regions},
                        s3_path=s3_path
                    )
                    db.add(db_chunk)

                # Insert quality metadata items if quality check failed
                if quality_report and not quality_report.get("passed", True):
                    db.add(
                        MetadataItem(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            document_id=document_id,
                            key="quality_flag",
                            value={"flag": "needs_review", "warnings": quality_report.get("warnings", [])},
                            source="scanner_connector",
                            confidence_score=0.9,
                        )
                    )
                    db.add(
                        MetadataItem(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            document_id=document_id,
                            key="quality_report",
                            value=quality_report,
                            source="scanner_connector",
                            confidence_score=1.0,
                        )
                    )

                # Insert metadata items
                if meta_dict:
                    extraction_confidence = await get_float("default_extraction_confidence", 0.9)
                    for key, value in meta_dict.items():
                        db_meta = MetadataItem(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            key=key,
                            value=value if isinstance(value, (dict, list)) else {"v": value},
                            source="llm",
                            confidence_score=extraction_confidence,
                        )
                        db.add(db_meta)

                        # T05 — attribute this value back to the page + word
                        # region it came from, using the word_regions the
                        # extractor already computed for `pages` (not
                        # discarded, just never consumed until now). No
                        # region when the value can't be verbatim-located
                        # (paraphrased/reformatted by the LLM) -- that's the
                        # honest result, not a bug to work around here.
                        location = locate_value_in_pages(value, pages)
                        if location:
                            db_meta.regions.append(MetadataItemRegion(
                                tenant_id=tenant_id,
                                page_number=location["page_number"],
                                x0=location["x0"], y0=location["y0"],
                                x1=location["x1"], y1=location["y1"],
                            ))

                    if meta_dict.get("title"):
                        stmt = select(Document).where(Document.id == document_id)
                        res = await db.execute(stmt)
                        doc = res.scalar_one_or_none()
                        if doc and doc.title == "Unknown":
                            doc.title = meta_dict["title"]
                            doc.doc_type = meta_dict.get("document_type")

                # 5b. T23 — classification stage, unconditional (runs regardless of
                # whether VLM extraction is even enabled). Persists the result on
                # the document instead of the ad-hoc unpersisted match T22 used to
                # do inline. A savepoint isolates it, same reasoning as T22 below.
                template = None
                try:
                    from app.services.classification_service import classify_document
                    # Real bug found live 2026-09-09: page 1 of a multi-page
                    # gazette is routinely just the shared masthead/notice
                    # boilerplate, identical across many registered templates
                    # (different districts/years/forms) — the actual
                    # form-identifying content (e.g. "Form B (See Rule 5)"
                    # plus its column headers) only appears starting page 2.
                    # Page-1-only sampling left the LLM with nothing to
                    # distinguish templates by, and it silently matched a
                    # real document (Pune, 2004, Form B) to the wrong
                    # registered template (Aurangabad, 1973, spread layout).
                    sample_text = "\n\n".join(p.get("text", "") for p in pages[:3]) if pages else ""
                    async with db.begin_nested():
                        classified_doc = await classify_document(db, tenant_id, document_id, sample_text)
                    if classified_doc.matched_template_id:
                        from app.models.template import Template as TemplateModel
                        template = await db.get(TemplateModel, classified_doc.matched_template_id)
                except Exception as classify_err:
                    logger.warning(f"T23 classification skipped for document {document_id}: {classify_err}")

                # 5c. T22 — VLM extraction against the matched template, if any.
                # Best-effort and non-blocking: a savepoint isolates it so a failure
                # here never aborts the chunk/metadata commit above (search must
                # never wait on this, Section 3.5).
                is_scanned_image = filename.lower().rsplit(".", 1)[-1] in {"jpg", "jpeg", "png", "tiff", "bmp", "webp"}
                if template or is_scanned_image or ext == ".pdf":
                    try:
                        from app.pipeline.vlm_extraction import extract_facts_for_document
                        async with db.begin_nested():
                            facts_count = await extract_facts_for_document(
                                db, tenant_id, document_id, version_id,
                                file_bytes, filename, pages, template,
                            )
                        if facts_count:
                            tmpl_name = f"{template.form_type}/{template.era_label}" if template else "unclassified_scanned_image"
                            logger.info(
                                f"T22 VLM extraction wrote {facts_count} facts for "
                                f"document {document_id} against template {tmpl_name}"
                            )
                    except Exception as vlm_err:
                        logger.warning(f"T22 VLM extraction skipped for document {document_id}: {vlm_err}")

                    # QA report #5, 2026-09-10 — Entity 360 was always empty
                    # because nothing ever populated the entity graph outside
                    # manual API calls (see entity_graph_service.py's
                    # auto_extract_entities_from_facts docstring). Best-effort
                    # and non-blocking, same savepoint idiom as VLM extraction
                    # just above — a bad match here must never abort ingestion.
                    try:
                        from app.services.entity_graph_service import auto_extract_entities_from_facts
                        async with db.begin_nested():
                            entities_created = await auto_extract_entities_from_facts(
                                db, tenant_id, document_id, version_id,
                            )
                        if entities_created:
                            logger.info(
                                f"Entity graph auto-extraction created {entities_created} new node(s) "
                                f"for document {document_id}"
                            )
                    except Exception as entity_err:
                        logger.warning(f"Entity graph auto-extraction skipped for document {document_id}: {entity_err}")

                # T79 — fuzzy-duplicate check, now at ingest instead of only
                # on-demand. Needs this document's own chunk-0 embedding,
                # which is only available once the chunk inserts above have
                # flushed — that's why this runs here, not earlier. Best-effort
                # in its own savepoint: never blocks or fails ingestion.
                duplicate_candidates = None
                try:
                    async with db.begin_nested():
                        found = await duplicate_service.find_fuzzy_duplicates(db, tenant_id, document_id, limit=5)
                    if found:
                        duplicate_candidates = found
                        logger.info(f"T79 fuzzy-duplicate check: document {document_id} resembles {len(found)} existing document(s)")
                except Exception as dup_err:
                    logger.warning(f"T79 fuzzy-duplicate check skipped for document {document_id}: {dup_err}")

                # Update status to indexed
                stmt = select(Document).where(Document.id == document_id)
                res = await db.execute(stmt)
                doc = res.scalar_one_or_none()
                if doc:
                    doc.status = "indexed"
                    # T76 — every document gets these, not just template
                    # matches (unlike doc_dg_pages, which only T22 writes to).
                    doc.pages_total_count = len(pages)
                    doc.pages_failed_count = sum(1 for p in pages if p.get("extraction_failed"))
                    if data_loss_result:
                        doc.data_loss_words_missing = data_loss_result.missing_count
                        doc.data_loss_details = (
                            {"loss_ratio": data_loss_result.loss_ratio, "missing_sample": data_loss_result.missing_sample}
                            if data_loss_result.missing_count > 0 else None
                        )
                    if furniture_candidates:
                        doc.page_furniture_candidates = furniture_candidates
                    if duplicate_candidates:
                        doc.possible_duplicate_candidates = duplicate_candidates

                if pdfa_s3_path:
                    version_res = await db.execute(select(DocumentVersion).where(DocumentVersion.id == version_id))
                    version_row = version_res.scalar_one_or_none()
                    if version_row:
                        version_row.pdfa_s3_path = pdfa_s3_path

        try:
            from app.services.cache_service import invalidate_tenant_cache
            await invalidate_tenant_cache(tenant_id_str)
        except Exception as cache_err:
            logger.warning(f"Tenant cache invalidation warning for {tenant_id_str}: {cache_err}")

        logger.info(f"Successfully ingested document {document_id} with {len(chunks)} chunks and 1024d embeddings.")

    except Exception as e:
        logger.error(f"Ingestion failed for document {document_id_str}: {e}", exc_info=True)
        # ATOMIC CLEANUP & FAILURE RECORDING
        # If any operation fails, roll back all DB writes, purge orphaned rows, and mark document as failed.
        async with TaskSession() as db_fail:
            async with db_fail.begin():
                try:
                    await db_fail.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": tenant_id_str})
                    document_id = UUID(document_id_str)
                    version_id = UUID(version_id_str)
                    await db_fail.execute(delete(DBChunk).where(DBChunk.version_id == version_id))
                    await db_fail.execute(delete(MetadataItem).where(MetadataItem.document_id == document_id))

                    stmt = select(Document).where(Document.id == document_id)
                    res = await db_fail.execute(stmt)
                    doc = res.scalar_one_or_none()
                    if doc:
                        doc.status = "failed"

                    # T41 — real failure alerting instead of logger-only.
                    # Best-effort, outside the doc/version writes above so a
                    # notification problem never affects the failure record.
                    try:
                        from ..models.user import User
                        uploader_res = await db_fail.execute(
                            select(User.email)
                            .join(DocumentVersion, DocumentVersion.uploaded_by == User.id)
                            .where(DocumentVersion.id == version_id)
                        )
                        uploader_email = uploader_res.scalar_one_or_none()
                        if uploader_email and doc:
                            from ..services.email_service import send_ingestion_failure_alert
                            await send_ingestion_failure_alert(uploader_email, doc.title, str(e)[:300])
                    except Exception as alert_err:
                        logger.warning(f"T41 failure alert skipped for document {document_id_str}: {alert_err}")
                except Exception as inner_e:
                    logger.error(f"Failed to record ingestion failure status for {document_id_str}: {inner_e}")
    finally:
        await task_engine.dispose()


celery_app.conf.beat_schedule = {
    "cleanup-30-day-trashed-items": {
        "task": "app.tasks.cleanup_trashed_items_task",
        "schedule": 86400.0,  # Run daily (every 24 hours)
    },
}


@celery_app.task(name="app.tasks.ingest_document_task")
def ingest_document_task(document_id_str: str, version_id_str: str, s3_path: str, tenant_id_str: str) -> None:
    import asyncio
    asyncio.run(_ingest_document_task_async(document_id_str, version_id_str, s3_path, tenant_id_str))


async def _cleanup_trashed_items_async() -> None:
    from app.services.document_service import cleanup_expired_trashed_items
    task_engine, TaskSession = _new_task_db_session_factory()
    try:
        retention_days = await get_int("trash_retention_days", 30)
        async with TaskSession() as db:
            await cleanup_expired_trashed_items(db, retention_days=retention_days)
    finally:
        await task_engine.dispose()


@celery_app.task(name="app.tasks.cleanup_trashed_items_task")
def cleanup_trashed_items_task() -> None:
    import asyncio
    asyncio.run(_cleanup_trashed_items_async())