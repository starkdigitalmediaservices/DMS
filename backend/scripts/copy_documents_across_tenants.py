"""Copy whole documents (file + pages + chunks + facts + regions) from one
tenant to another, then infer entity relationships in the target.

Written 2026-09-23 for a specific, narrow need: the admin account
(biznesskd07 / Kunal's Organization) holds the rich extractions but its
documents predate Fact.row_group_id, so entity-relationship inference can
produce nothing there. The demo tenant has documents WITH row identity.
Copying those documents gives the admin account a working relationship graph
without re-ingesting, which would need VLM provider quota nobody has.

This duplicates documents across tenants, which is NOT a normal operation --
the same file then exists in two tenants. Everything it creates is placed in
a single named folder in the target so it can be identified and deleted
cleanly. Run with --dry-run first.

    docker compose exec -T backend sh -c "PYTHONPATH=/app python3 \
      /app/scripts/copy_documents_across_tenants.py --dry-run"
"""
import argparse
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.folder import Folder
from app.models.page import DocumentPage
from app.models.user import User
from app.services import storage_service
from app.services.entity_graph_service import auto_extract_relationships_from_facts

SOURCE_TENANT = uuid.UUID("11aa05cf-9950-461b-87f0-ef2b2e383b4a")
TARGET_TENANT = uuid.UUID("de7bbd90-72a9-4beb-9aec-e54ce58ee7e3")
TITLES = ["Ambajogai.pdf", "2a_spread_join_SUCCESS__spread_join_test_fixture.pdf"]
FOLDER_NAME = "Copied from demo tenant (entity showcase)"


async def _target_actor(db):
    res = await db.execute(
        select(User).where(User.tenant_id == TARGET_TENANT,
                           User.email == "biznesskd07@gmail.com").limit(1))
    user = res.scalar_one_or_none()
    if user is None:
        res = await db.execute(select(User).where(User.tenant_id == TARGET_TENANT).limit(1))
        user = res.scalar_one_or_none()
    return user


async def copy_one(db, src_doc: Document, folder_id, actor_id, dry_run: bool) -> dict:
    src_ver = await db.get(DocumentVersion, src_doc.current_version_id)

    new_doc_id = uuid.uuid4()
    new_ver_id = uuid.uuid4()
    new_key = f"{TARGET_TENANT}/{new_doc_id}/{new_ver_id}/{src_ver.original_filename}"

    # copy the object first -- a DB row pointing at a missing file is worse
    # than no row at all
    if not dry_run:
        data = await storage_service.download_file(src_ver.s3_path)
        await storage_service.upload_file(data, new_key, src_doc.mime_type or "application/pdf")

    new_doc = Document(
        id=new_doc_id, tenant_id=TARGET_TENANT, title=src_doc.title,
        status=src_doc.status, mime_type=src_doc.mime_type, doc_type=src_doc.doc_type,
        folder_id=folder_id, created_by=actor_id,
        retention_class=src_doc.retention_class,
        classification_status=src_doc.classification_status,
        matched_template_id=src_doc.matched_template_id,
        pages_total_count=src_doc.pages_total_count,
        pages_failed_count=src_doc.pages_failed_count,
        data_loss_words_missing=src_doc.data_loss_words_missing,
        data_loss_details=src_doc.data_loss_details,
        page_furniture_candidates=src_doc.page_furniture_candidates,
    )
    new_ver = DocumentVersion(
        id=new_ver_id, tenant_id=TARGET_TENANT, document_id=new_doc_id,
        version_number=1, s3_path=new_key, file_hash=src_ver.file_hash,
        file_size_bytes=src_ver.file_size_bytes,
        original_filename=src_ver.original_filename,
    )
    if not dry_run:
        db.add_all([new_doc, new_ver])
        await db.flush()
        new_doc.current_version_id = new_ver_id
        await db.flush()

    # pages -- keep a map so FactRegions can be re-pointed
    page_map = {}
    src_pages = (await db.execute(select(DocumentPage).where(
        DocumentPage.document_id == src_doc.id))).scalars().all()
    for p in src_pages:
        np_id = uuid.uuid4()
        page_map[p.id] = np_id
        if not dry_run:
            db.add(DocumentPage(
                id=np_id, tenant_id=TARGET_TENANT, document_id=new_doc_id,
                version_id=new_ver_id, page_number=p.page_number,
                width=p.width, height=p.height, rotation=p.rotation, skew=p.skew))

    src_chunks = (await db.execute(select(Chunk).where(
        Chunk.document_id == src_doc.id))).scalars().all()
    for c in src_chunks:
        if not dry_run:
            db.add(Chunk(
                id=uuid.uuid4(), tenant_id=TARGET_TENANT, document_id=new_doc_id,
                version_id=new_ver_id, content=c.content, embedding=c.embedding,
                chunk_metadata=c.chunk_metadata, page_number=c.page_number,
                chunk_index=c.chunk_index, s3_path=new_key))

    # facts -- row_group_id is a bare grouping uuid, not an FK, but remap it
    # so the two tenants' groups can never be confused with each other
    row_group_map = {}
    fact_map = {}
    src_facts = (await db.execute(select(Fact).where(
        Fact.document_id == src_doc.id))).scalars().all()
    for f in src_facts:
        nf_id = uuid.uuid4()
        fact_map[f.id] = nf_id
        rg = None
        if f.row_group_id is not None:
            rg = row_group_map.setdefault(f.row_group_id, uuid.uuid4())
        if not dry_run:
            db.add(Fact(
                id=nf_id, tenant_id=TARGET_TENANT, document_id=new_doc_id,
                version_id=new_ver_id, field_name=f.field_name, value=f.value,
                confidence=f.confidence, status=f.status,
                is_handwritten=f.is_handwritten, row_group_id=rg))
    if not dry_run:
        await db.flush()

    regions = 0
    src_regions = (await db.execute(
        select(FactRegion).join(Fact, Fact.id == FactRegion.fact_id)
        .where(Fact.document_id == src_doc.id))).scalars().all()
    for r in src_regions:
        if r.fact_id not in fact_map or r.page_id not in page_map:
            continue
        regions += 1
        if not dry_run:
            db.add(FactRegion(
                id=uuid.uuid4(), tenant_id=TARGET_TENANT,
                fact_id=fact_map[r.fact_id], page_id=page_map[r.page_id],
                x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1))
    if not dry_run:
        await db.flush()

    return {"title": src_doc.title, "new_doc_id": str(new_doc_id),
            "pages": len(page_map), "chunks": len(src_chunks),
            "facts": len(fact_map), "regions": regions}


async def main(dry_run: bool):
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"),
                         {"t": str(TARGET_TENANT)})
        actor = await _target_actor(db)
        if actor is None:
            print("no user in target tenant; aborting"); return
        print(f"target actor: {actor.email}")

        folder_id = None
        if not dry_run:
            folder = Folder(id=uuid.uuid4(), tenant_id=TARGET_TENANT,
                            name=FOLDER_NAME, parent_id=None, created_by=actor.id)
            db.add(folder); await db.flush()
            folder_id = folder.id
            print(f"created folder: {FOLDER_NAME} ({folder_id})")

        for title in TITLES:
            await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"),
                             {"t": str(SOURCE_TENANT)})
            src = (await db.execute(select(Document).where(
                Document.tenant_id == SOURCE_TENANT, Document.title == title,
                Document.is_trashed == False))).scalars().first()  # noqa: E712
            if src is None:
                print(f"  !! source not found: {title}"); continue
            await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"),
                             {"t": str(TARGET_TENANT)})
            info = await copy_one(db, src, folder_id, actor.id, dry_run)
            print(f"  {info['title']}: pages={info['pages']} chunks={info['chunks']} "
                  f"facts={info['facts']} regions={info['regions']}")

        if dry_run:
            await db.rollback()
            print("\nDRY RUN — rolled back, nothing written")
            return

        await db.commit()
        print("\ncopied and committed; inferring relationships in target...")

        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, false)"),
                         {"t": str(TARGET_TENANT)})
        docs = (await db.execute(select(Document).where(
            Document.tenant_id == TARGET_TENANT, Document.folder_id == folder_id))).scalars().all()
        total = 0
        for d in docs:
            total += await auto_extract_relationships_from_facts(
                db, TARGET_TENANT, d.id, d.current_version_id)
        await db.commit()
        print(f"inferred {total} relationships in the target tenant")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.dry_run))
