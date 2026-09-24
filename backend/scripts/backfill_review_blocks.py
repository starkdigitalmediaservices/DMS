"""Build review-screen blocks for existing documents, per tenant.

New documents get their blocks the first time someone opens them; this fills
in the rest ahead of time, and reports which builder each document gets:

    facts        template-extracted values (the Fact table stays the truth)
    chandra      Chandra's saved layout: headings, paragraphs, tables, pictures
    word_boxes   word positions from Tesseract / PaddleOCR / a native PDF
    text_only    plain text only, outlined as the whole page ("no_layout")
    none         nothing extractable (e.g. every page failed OCR)

Idempotent. A document whose review someone has already changed (review
version > 1) is never touched. A snapshot built earlier by an older builder
(e.g. an empty one from before these builders existed) is only replaced with
--rebuild-untouched, and only while nobody has changed it.

    docker compose exec -T backend sh -c "PYTHONPATH=/app python3 \\
        /app/scripts/backfill_review_blocks.py --tenant <tenant-uuid> --dry-run"
    ... then drop --dry-run (and add --rebuild-untouched if the report says so).
"""
import argparse
import asyncio
import collections
import logging
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import delete, select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.review import ReviewOriginal, ReviewState  # noqa: E402
from app.services import review_service  # noqa: E402


async def main(tenant: str, dry_run: bool, rebuild_untouched: bool) -> None:
    # The dev engine echoes every SQL statement; the report is the output here.
    logging.disable(logging.INFO)
    from app.database import engine as _engine  # noqa: F401  (imported for echo off)
    _engine.echo = False
    tenant_id = uuid.UUID(tenant)
    paths = collections.Counter()
    actions = collections.Counter()
    async with AsyncSessionLocal() as db:
        docs = list((await db.execute(
            select(Document).where(Document.tenant_id == tenant_id, Document.is_trashed.is_(False)).order_by(Document.title)
        )).scalars().all())
        for doc in docs:
            builder, blocks, used = await review_service.plan_document_blocks(db, doc)
            paths[builder] += 1
            state = await db.get(ReviewState, doc.id)
            original = await db.get(ReviewOriginal, state.original_id) if state else None

            if state is None:
                action = "create"
            elif state.version > 1:
                action = "skip: already reviewed"
            elif original is not None and (original.builder != builder or (not original.blocks and blocks)):
                action = "rebuild (untouched, older builder)" if rebuild_untouched else "outdated: rerun with --rebuild-untouched"
            else:
                action = "skip: up to date"
            actions[action] += 1
            pages = ", ".join(f"{k} {v}" for k, v in sorted(used.items()))
            print(f"  {builder:10} {len(blocks):5} blocks  [{pages}]  {action:40}  {doc.title[:60]}")

            if action == "create":
                await review_service.create_review(db, doc, builder, blocks)
            elif action.startswith("rebuild"):
                # Nobody has changed this review (version 1): drop the stale
                # snapshot and working copy, then build them fresh.
                await db.execute(delete(ReviewState).where(ReviewState.document_id == doc.id))
                await db.execute(delete(ReviewOriginal).where(ReviewOriginal.document_id == doc.id))
                await db.flush()
                await review_service.create_review(db, doc, builder, blocks)

        print("\nDocuments per builder:")
        for k, v in paths.most_common():
            print(f"  {k:12} {v}")
        print("Actions:")
        for k, v in actions.most_common():
            print(f"  {v:4}  {k}")

        if dry_run:
            await db.rollback()
            print("\nDRY RUN: nothing written.")
        else:
            await db.commit()
            print("\nWritten.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True, help="tenant UUID (the backfill always runs one tenant at a time)")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    ap.add_argument("--rebuild-untouched", action="store_true",
                    help="replace snapshots built by an older builder, only where nobody has changed the review")
    args = ap.parse_args()
    asyncio.run(main(args.tenant, args.dry_run, args.rebuild_untouched))
