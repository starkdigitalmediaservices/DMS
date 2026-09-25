"""T26/T25 — Wardha.pdf was never a spread-layout document.

Real finding (2026-09-02): Wardha.pdf has been sitting matched against
the "Maharashtra State Wakf Gazette Register" spread template
(df0aaa26-..., "Government Gazette, District Aurangabad, 1973") since
before this session, and every "spread join" attempt against it failed
(5/5 measurable page-pairs, 100% serial mismatch -- see
docs/testing/T31_T32_regression_corpus_notes.md's "Wardha.pdf" entries and
accuracy_baseline.py's WARDHA_KNOWN_ISSUE). That was read as a possible
real T26 structural gap (role:'serial' maybe not printed on both spread
halves).

Downloading and rendering the actual pages showed the real cause: this
is a completely different document. Wardha.pdf's cover page is dated
30 December 2004 ("List of Wakf properties District Warda"), published
under the Central Wakf Act 1995 -- not the 1954 Act the 1973 Aurangabad
gazette uses. Its table pages are "Form B (See Rule 5)", a single,
self-contained, single-page-wide table (14 columns: Sr.No, Name of the
Wakf Institution, Sunni/Shia, Nature & Object, Admin of Wakf, Creation of
Wakf, Boundaries, Wakf Deed Reg Deed, Movable Property, Immovable
Property, Value, Income, Tax Payable, Scheme Settlement). Confirmed by
comparing two consecutive pages (3 and 4): their Sr.No values (WB-116,
WB-18, ... vs WB-114, WB-127, ...) never overlap -- these are two
independent pages of institutions, not a left/right split of the same
rows. There is no "right half" to join against; the 100% mismatch rate
was the entirely expected result of joining two unrelated pages, not a
real spread-pairing gap.

Registers the real "Form B" template (layout=single_page) and reclassifies
Wardha.pdf against it -- the fix for a misclassification bug, not a T26
extraction-logic bug.

Usage (inside the backend container):
    python3 scripts/register_wardha_form_b.py
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
WARDHA_DOC_ID = uuid.UUID("fc4263c7-4511-46c2-9590-dbb03458e8c7")

FIELD_SCHEMA = [
    {"name": "sr_no", "type": "string", "required": True, "role": "serial"},
    {"name": "wakf_name", "type": "string", "required": True},
    {"name": "sect", "type": "string", "required": False},
    {"name": "nature_object", "type": "string", "required": False},
    {"name": "admin_of_wakf", "type": "string", "required": False},
    {"name": "creation_date", "type": "string", "required": False},
    {"name": "boundaries", "type": "string", "required": False},
    {"name": "deed_reg", "type": "string", "required": False},
    {"name": "movable_property", "type": "string", "required": False},
    {"name": "immovable_property", "type": "string", "required": False},
    {"name": "value", "type": "string", "required": False},
    {"name": "gross_income", "type": "string", "required": False},
    {"name": "tax_payable", "type": "string", "required": False},
    {"name": "scheme_settlement", "type": "string", "required": False},
]


async def main():
    async with AsyncSessionLocal() as db:
        template = await create_template(
            db, form_type="Maharashtra State Wakf Gazette Register — Form B (Property Assessment)",
            era_label="District Wardha Gazette, 2004 (Central Wakf Act 1995)",
            field_schema=FIELD_SCHEMA, layout="single_page",
            actor_id=ACTOR_ID, tenant_id=TENANT_ID,
        )
        await db.commit()
        await db.refresh(template)
        logger.info(f"Registered template {template.id}: {template.form_type} | {template.era_label}")

        doc = await db.get(Document, WARDHA_DOC_ID)
        version = await db.get(DocumentVersion, doc.current_version_id)
        file_bytes = await download_file(version.s3_path)
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        pages = await get_cached_ocr(db, content_hash, settings.ai_ocr_provider)
        if pages is None:
            logger.error(f"No cached OCR for Wardha.pdf (hash {content_hash[:12]}...) -- would need a fresh OCR pass")
            return
        logger.info(f"Wardha.pdf: {len(pages)} cached OCR pages found")

        classified = await manually_classify_document(db, TENANT_ID, WARDHA_DOC_ID, template.id, ACTOR_ID)
        logger.info(f"Wardha.pdf: classification_status={classified.classification_status}")

        facts_count = await extract_facts_for_document(
            db, TENANT_ID, WARDHA_DOC_ID, doc.current_version_id, file_bytes, "Wardha.pdf", pages, template,
        )
        await db.commit()
        logger.info(f"Wardha.pdf: VLM extraction wrote {facts_count} facts")


if __name__ == "__main__":
    asyncio.run(main())
