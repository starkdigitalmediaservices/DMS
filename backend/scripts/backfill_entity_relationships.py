"""One-off backfill for the relationship-inference gap (2026-09-23).

auto_extract_relationships_from_facts runs on every new ingest, but the facts
already in the corpus predate it. This walks existing documents and runs the
same pass over them. Idempotent -- safe to re-run.

    docker compose exec -T backend sh -c "PYTHONPATH=/app python3 \
        /app/scripts/backfill_entity_relationships.py --dry-run"
    ... then drop --dry-run to commit.
"""
import argparse
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models.document import Document
from app.models.entity_node import EntityNode
from app.services.entity_graph_service import auto_extract_relationships_from_facts


async def main(dry_run: bool, tenant: str | None, limit: int | None):
    async with AsyncSessionLocal() as db:
        q = select(Document).where(Document.is_trashed == False)  # noqa: E712
        if tenant:
            q = q.where(Document.tenant_id == uuid.UUID(tenant))
        docs = list((await db.execute(q)).scalars().all())
        if limit:
            docs = docs[:limit]

        total = 0
        touched = 0
        for doc in docs:
            if doc.current_version_id is None:
                continue
            await db.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"),
                {"t": str(doc.tenant_id)},
            )
            try:
                created = await auto_extract_relationships_from_facts(
                    db, doc.tenant_id, doc.id, doc.current_version_id
                )
            except Exception as e:
                print(f"  !! {doc.title}: {type(e).__name__}: {e}")
                continue
            if created:
                touched += 1
                total += created
                print(f"  {created:>4} relationships  <-  {doc.title}")

        print(f"\n{'DRY RUN — ' if dry_run else ''}{total} relationships across {touched} documents")

        if dry_run:
            await db.rollback()
            print("rolled back; nothing written")
        else:
            await db.commit()
            print("committed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    asyncio.run(main(a.dry_run, a.tenant, a.limit))
