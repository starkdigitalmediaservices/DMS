"""Retry Wardha.pdf's VLM extraction using the native Gemini provider
(AI_VLM_PROVIDER switched from openrouter to gemini 2026-09-02 after the
OpenRouter account ran out of credits mid-extraction -- see
register_wardha_form_b.py and docs/testing/T31_T32_regression_corpus_notes.md).
Template registration + classification already succeeded and persisted;
this only retries the extraction step.

CAUTION if reusing this pattern for another document/incident:
extract_facts_for_document() has no idempotency guard -- re-running it
against a document that already has Facts from a prior (even partial)
run INSERTs duplicates rather than replacing them. Delete existing
doc_dg_facts rows for the document first (fact_regions cascade) if
retrying after a partial success, same as this session had to do live:
    DELETE FROM doc_dg_facts WHERE document_id = '<uuid>';

Usage (inside the backend container):
    python3 scripts/retry_wardha_extraction.py
"""
import asyncio
import hashlib
import logging
import sys
import uuid

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.services.extraction_archive_service import get_cached_ocr
from app.services.storage_service import download_file
from app.pipeline.vlm_extraction import extract_facts_for_document
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.template import Template
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TENANT_ID = uuid.UUID("de7bbd90-72a9-4beb-9aec-e54ce58ee7e3")
WARDHA_DOC_ID = uuid.UUID("fc4263c7-4511-46c2-9590-dbb03458e8c7")


async def main():
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, WARDHA_DOC_ID)
        template = await db.get(Template, doc.matched_template_id)
        logger.info(f"Wardha.pdf matched template: {template.form_type} | {template.era_label}")

        version = await db.get(DocumentVersion, doc.current_version_id)
        file_bytes = await download_file(version.s3_path)
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        pages = await get_cached_ocr(db, content_hash, settings.ai_ocr_provider)
        logger.info(f"Wardha.pdf: {len(pages)} cached OCR pages found")

        facts_count = await extract_facts_for_document(
            db, TENANT_ID, WARDHA_DOC_ID, doc.current_version_id, file_bytes, "Wardha.pdf", pages, template,
        )
        await db.commit()
        logger.info(f"Wardha.pdf: VLM extraction wrote {facts_count} facts")


if __name__ == "__main__":
    asyncio.run(main())
