#!/usr/bin/env python3
"""TS2 — spot-check the data-loss audit against an already-ingested
document.

The automatic check (app/services/data_loss_audit.py, wired into
app/tasks/worker.py) runs for free on every new upload, since it compares
OCR's freshly-computed page text against chunks in the same ingestion
pass — no re-OCR needed. This script does NOT have that luxury: raw OCR
page text isn't archived anywhere for an already-ingested document (that
archive is TS3, not built yet — see docs/planning/TS_backlog_colleague_features.md), so
checking a historical document means re-running OCR here. That's a real
cost this incurs deliberately, not a shortcut; once TS3 lands, point this
at the archive instead of re-OCRing.

Usage:
    python3 scripts/audit_data_loss.py --document-id <uuid>
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uuid import UUID

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.ocr.factory import get_ocr_provider
from app.services.data_loss_audit import audit_pages_vs_chunks
from app.services.storage_service import download_file


async def audit_document(document_id: UUID) -> None:
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, document_id)
        if not doc:
            print(f"No document {document_id}")
            return
        version = await db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
        if not version:
            print(f"Document {document_id} has no current version")
            return

        res = await db.execute(select(Chunk.content).where(Chunk.document_id == document_id))
        chunk_contents = [c for (c,) in res.all()]
        if not chunk_contents:
            print(f"Document {document_id} has no stored chunks — nothing to audit against")
            return

        print(f"Re-running OCR on '{doc.title}' ({document_id}) to reconstruct the original page text...")
        file_bytes = await download_file(version.s3_path)
        ocr = get_ocr_provider()
        pages = await ocr.extract_pages(file_bytes, version.original_filename or doc.title)

        result = audit_pages_vs_chunks(pages, chunk_contents)
        status = "PASSED" if result.passed else "FAILED"
        print(f"\n{status} — {result.missing_count}/{result.total_words} words missing ({result.loss_ratio:.2%})")
        if result.missing_sample:
            print("Sample of missing words:")
            for item in result.missing_sample:
                print(f"  page {item['page_number']}: {item['word']!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--document-id", required=True, type=UUID)
    args = parser.parse_args()
    asyncio.run(audit_document(args.document_id))
