"""One-off, read-only-effect verification: does the new VLM parse-retry
loop (_call_vlm_with_parse_retry, added for the T31/T32 follow-up) recover
the real gazette's previously-known-failing left-hand page?

Runs extract_facts_for_document() inside a DB transaction that is ALWAYS
rolled back at the end -- never commits, so this cannot mutate the real
tenant's real data. Prints what got extracted so the result can be judged
by eye against docs/testing/T31_T32_regression_corpus_notes.md's documented failure.

Usage (inside the backend container):
    python3 scripts/verify_gazette_retry_live.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.document_version import DocumentVersion  # noqa: E402
from app.models.template import Template  # noqa: E402
from app.models.fact import Fact  # noqa: E402
from app.services.storage_service import download_file  # noqa: E402
from app.pipeline.vlm_extraction import extract_facts_for_document  # noqa: E402
from sqlalchemy import select  # noqa: E402

GAZETTE_DOC_ID = "139cd522-099e-4642-8199-10b6f6610694"


async def main():
    async with AsyncSessionLocal() as db:
        try:
            doc = await db.get(Document, GAZETTE_DOC_ID)
            version = await db.get(DocumentVersion, doc.current_version_id)
            template = await db.get(Template, doc.matched_template_id)
            print(f"Document: {doc.title}, template layout={template.layout}, pages_total={doc.pages_total_count}")

            file_bytes = await download_file(version.s3_path)
            print(f"Downloaded {len(file_bytes)} bytes")

            pages_text = [{}] * doc.pages_total_count

            written = await extract_facts_for_document(
                db, doc.tenant_id, doc.id, version.id, file_bytes, doc.title, pages_text, template,
            )
            print(f"\nfacts_written this run (uncommitted): {written}")

            # Facts are visible in this same session pre-rollback via db.add(),
            # but they aren't queryable via a fresh SELECT until flushed --
            # extract_facts_for_document flushes internally, so this SELECT
            # sees them.
            res = await db.execute(
                select(Fact.field_name, Fact.value).where(
                    Fact.document_id == doc.id, Fact.version_id == version.id,
                ).order_by(Fact.field_name)
            )
            rows = res.all()
            print(f"\nTotal Fact rows visible for this document+version after this run: {len(rows)}")
            join_mismatches = [r for r in rows if r[0] == "_join_mismatch"]
            real_fields = [r for r in rows if r[0] != "_join_mismatch"]
            print(f"  _join_mismatch rows: {len(join_mismatches)}")
            print(f"  real field facts: {len(real_fields)}")
            field_names = sorted({r[0] for r in real_fields})
            print(f"  distinct field names recovered: {field_names}")
        finally:
            await db.rollback()
            print("\nRolled back -- no changes persisted to the real tenant.")


asyncio.run(main())
