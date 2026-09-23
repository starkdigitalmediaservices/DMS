"""Re-run VLM extraction for one document under the CURRENTLY configured
provider, then report what changed.

Safe to re-run: extract_facts_for_document now purges its own prior machine
output for (document_id, version_id) before writing, and preserves anything a
human verified.

The VLM cache is keyed by provider, so switching AI_VLM_PROVIDER guarantees a
genuine re-extraction rather than a replay of the old provider's responses.

    docker compose exec -T backend sh -c "PYTHONPATH=/app python3 \
        /app/scripts/reextract_document.py <document_id>"
"""
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select, text

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.page import DocumentPage
from app.models.template import Template
from app.pipeline.vlm_extraction import extract_facts_for_document
from app.services.extraction_archive_service import get_cached_ocr
from app.services.storage_service import download_file


async def snapshot(db, doc_id):
    facts = (await db.execute(select(Fact).where(Fact.document_id == doc_id))).scalars().all()
    pages = (await db.execute(select(DocumentPage).where(DocumentPage.document_id == doc_id))).scalars().all()
    by_field = {}
    for f in facts:
        by_field[f.field_name] = by_field.get(f.field_name, 0) + 1
    return {
        "facts": len(facts),
        "with_row_group": sum(1 for f in facts if f.row_group_id is not None),
        "pages": sorted(p.page_number for p in pages),
        "by_field": dict(sorted(by_field.items(), key=lambda kv: -kv[1])),
    }


async def main(doc_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            print("document not found"); return
        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"),
                         {"t": str(doc.tenant_id)})
        doc = await db.get(Document, doc_id)
        ver = await db.get(DocumentVersion, doc.current_version_id)
        tpl = await db.get(Template, doc.matched_template_id) if doc.matched_template_id else None

        before = await snapshot(db, doc_id)
        print(f"document : {doc.title}")
        print(f"provider : {settings.ai_vlm_provider}")
        print(f"template : {tpl.form_type if tpl else '(none)'}")
        print(f"\nBEFORE   facts={before['facts']}  with_row_group={before['with_row_group']}")
        print(f"         pages={before['pages']}")
        print(f"         {before['by_field']}")

        data = await download_file(ver.s3_path)
        cached = await get_cached_ocr(db, ver.file_hash, settings.ai_ocr_provider)
        pages_text = cached if cached else [{} for _ in range(doc.pages_total_count or 1)]

        failed, empty = [], []
        written = await extract_facts_for_document(
            db, doc.tenant_id, doc.id, ver.id, data, doc.title, pages_text, tpl,
            failed_pages_out=failed, empty_pages_out=empty,
        )
        await db.commit()

        after = await snapshot(db, doc_id)
        print(f"\nAFTER    facts={after['facts']}  with_row_group={after['with_row_group']}")
        print(f"         pages={after['pages']}")
        print(f"         {after['by_field']}")
        print(f"\nwritten this run : {written}")
        print(f"failed pages     : {failed}")
        print(f"empty pages      : {empty}")


if __name__ == "__main__":
    asyncio.run(main(uuid.UUID(sys.argv[1])))
