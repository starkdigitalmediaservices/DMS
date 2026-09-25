"""T25/T31 — register the real "Form A" (no-property Wakfs list) template
found in two real, previously-uncatalogued documents already in the
biznesskd07@gmail.com tenant (Ambajogai.pdf, Aurangabad-Shia.pdf), then
manually classify and VLM-extract both against it.

Discovery (2026-09-02, reading the real rendered pages directly): both
documents are the same "Maharashtra State Board Of Wakfs" 1973/74
Marathwada gazette family as the already-registered spread template
(df0aaa26-...), but their bulk content is "Part A" -- wakfs with no
property, or property too small to need listing (see each document's own
cover note). Part A rows are fully self-contained on ONE page (8 columns:
serial+village, wakf name, sect, object, wakf name (col 5), creation
date, deed details, mutawalli) -- there is no facing/continuation page
for them, unlike the existing template's Part B/C rows, which DO spread
across two pages for the additional property columns (village, survey
no, area, assessment, boundaries, ...). That's why classification never
matched: the existing template is layout='spread' and assumes every row
needs a right-half page, which is wrong for the ~80% of rows that are
Part A.

This registers a NEW layout='single_page' template using the identical
first-8-field schema as the existing template's left half (same names,
so a future merge into one smarter template is easy), and runs it
against both documents.

Known, documented limitation (not solved here): Part B/C rows (a
minority -- e.g. 7/36 institutions in Aurangabad-Shia per its own
Consolidated Abstract page) will still only get their first 8 columns
extracted correctly against this template; their columns 9-19 (property
detail on the continuation page) are not captured by this pass. Flagged
here rather than guessed at, matching this project's established
practice for structural gaps (see docs/testing/T31_T32_regression_corpus_notes.md).

Usage (inside the backend container):
    python3 scripts/register_waqf_gazette_form_a.py
"""
import asyncio
import hashlib
import logging
import sys
import uuid

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.services.template_service import create_template
from app.services.classification_service import manually_classify_document
from app.services.extraction_archive_service import get_cached_ocr
from app.services.storage_service import download_file
from app.pipeline.vlm_extraction import extract_facts_for_document
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TENANT_ID = uuid.UUID("de7bbd90-72a9-4beb-9aec-e54ce58ee7e3")
ACTOR_ID = uuid.UUID("17c7fd71-9fec-4eb9-958b-6046a99b0b8d")  # biznesskd07@gmail.com, it_admin

FIELD_SCHEMA = [
    {"name": "sr_no", "type": "string", "required": True, "role": "serial"},
    {"name": "wakf_name", "type": "string", "required": True},
    {"name": "sect", "type": "string", "required": False, "ditto_eligible": True},
    {"name": "object", "type": "string", "required": False, "ditto_eligible": True},
    {"name": "wakf_name_col5", "type": "string", "required": False, "ditto_eligible": True},
    {"name": "creation_date", "type": "string", "required": False, "ditto_eligible": True},
    {"name": "deed_details", "type": "string", "required": False, "ditto_eligible": True},
    {"name": "mutawalli_name", "type": "string", "required": False, "ditto_eligible": True},
]

DOCS = [
    ("664a731b-31ca-40db-8c4c-81a82da8f240", "Ambajogai (1).pdf"),
    ("ab867aa4-9304-4b9f-a0c2-557b110c81ea", "Aurangabad-Shia.pdf"),
]


async def main():
    async with AsyncSessionLocal() as db:
        template = await create_template(
            db, form_type="Maharashtra State Wakf Gazette Register (Form A, no-property Wakfs)",
            era_label="Marathwada Region Gazette, 1973-1974",
            field_schema=FIELD_SCHEMA, layout="single_page",
            actor_id=ACTOR_ID, tenant_id=TENANT_ID,
        )
        await db.commit()
        await db.refresh(template)
        logger.info(f"Registered template {template.id}: {template.form_type} | {template.era_label}")

        for doc_id_str, filename in DOCS:
            doc_id = uuid.UUID(doc_id_str)
            doc = await db.get(Document, doc_id)
            version = await db.get(DocumentVersion, doc.current_version_id)

            file_bytes = await download_file(version.s3_path)
            content_hash = hashlib.sha256(file_bytes).hexdigest()
            pages = await get_cached_ocr(db, content_hash, settings.ai_ocr_provider)
            if pages is None:
                logger.error(f"No cached OCR for {filename} (hash {content_hash[:12]}...) -- skipping, would need a fresh OCR pass")
                continue
            logger.info(f"{filename}: {len(pages)} cached OCR pages found")

            classified = await manually_classify_document(db, TENANT_ID, doc_id, template.id, ACTOR_ID)
            logger.info(f"{filename}: classification_status={classified.classification_status}")

            facts_count = await extract_facts_for_document(
                db, TENANT_ID, doc_id, doc.current_version_id, file_bytes, filename, pages, template,
            )
            await db.commit()
            logger.info(f"{filename}: VLM extraction wrote {facts_count} facts")


if __name__ == "__main__":
    asyncio.run(main())
