import hashlib
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_
from sqlalchemy.orm import selectinload
from uuid import UUID

from ..database import establish_tenant_context
from ..models.document import Document
from ..models.document_version import DocumentVersion
from ..models.folder import Folder
from ..models.metadata_item import MetadataItem
from ..schemas.document import (
    DocumentUploadResponse,
    DocumentDetailResponse,
    BatchDocumentUploadResponse,
    DocumentListItem,
    DocumentUpdate,
    DriveStatsResponse,
)
from ..services.storage_service import upload_file, generate_presigned_url, delete_file, download_file, archive_file_with_retention
from ..services.audit_service import log_action
from ..services import department_service
from ..services.license_service import check_upload_allowed
from ..pipeline.ingestion import ingest_document

logger = logging.getLogger(__name__)


async def upload_document(
    file: UploadFile,
    tenant_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    folder_id: Optional[UUID] = None,
    force: bool = False,
) -> DocumentUploadResponse:
    """Upload a document to MinIO S3, create DB records, and schedule async ingestion."""
    # T81 — the metered action. Blocks new ingestion only; already-stored
    # documents stay fully readable/searchable/exportable regardless of
    # license state (see license_service module docstring).
    license_check = await check_upload_allowed(db, tenant_id)
    if not license_check.allowed:
        raise HTTPException(status_code=402, detail=license_check.reason)

    if folder_id:
        folder = await db.get(Folder, folder_id)
        if not folder or folder.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Target folder not found")

    from app.config import settings
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in settings.allowed_upload_extensions:
        raise HTTPException(status_code=400, detail=f"File type '.{ext}' is not supported")

    file_bytes = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb} MB limit")

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # T79: the hash was always stored but never compared — surface an exact
    # duplicate to the operator instead of silently re-processing it. Never
    # drop the file: `force=True` lets the operator upload it anyway.
    if not force:
        dup_stmt = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                DocumentVersion.file_hash == file_hash,
                Document.tenant_id == tenant_id,
                Document.is_trashed == False,
            )
            .limit(1)
        )
        dup_res = await db.execute(dup_stmt)
        dup_row = dup_res.first()
        if dup_row:
            existing_doc, existing_version = dup_row
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "An identical file already exists in your drive.",
                    "existing_document_id": str(existing_doc.id),
                    "existing_document_title": existing_doc.title,
                    "existing_uploaded_at": existing_version.created_at.isoformat(),
                },
            )

    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    s3_key = f"{tenant_id}/{doc_id}/{version_id}/{file.filename}"

    # Upload file to MinIO S3
    await upload_file(file_bytes, s3_key, file.content_type or "application/octet-stream")

    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        created_by=user_id,
        folder_id=folder_id,
        title=file.filename or "Unknown",
        status="pending",
    )
    db.add(doc)

    version = DocumentVersion(
        id=version_id,
        tenant_id=tenant_id,
        document_id=doc_id,
        s3_path=s3_key,
        version_number=1,
        file_hash=file_hash,
        file_size_bytes=len(file_bytes),
        original_filename=file.filename,
        uploaded_by=user_id,
    )
    db.add(version)

    await db.flush()
    doc.current_version_id = version_id
    await db.commit()
    # T96 clean-room finding — see database.py::establish_tenant_context's
    # docstring: a mid-function commit followed by db.refresh() reproducibly
    # 500'd under RLS with no tenant context visible to the follow-up SELECT.
    await establish_tenant_context(db, tenant_id)
    await db.refresh(doc)

    await log_action(db, user_id, tenant_id, "document.create", resource_type="document", resource_id=doc.id, details={"title": doc.title})

    # Schedule async ingestion
    await ingest_document(
        document_id=doc_id,
        version_id=version_id,
        s3_path=s3_key,
        tenant_id=tenant_id,
    )

    return DocumentUploadResponse(
        document_id=doc_id,
        version_id=version_id,
        title=doc.title,
        status=doc.status,
        created_at=doc.created_at,
        folder_id=doc.folder_id,
    )


async def upload_documents_bulk(
    files: List[UploadFile],
    tenant_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    folder_id: Optional[UUID] = None,
) -> BatchDocumentUploadResponse:
    """Upload multiple documents into a folder, create DB records, and schedule async ingestion."""
    uploaded_docs: List[DocumentUploadResponse] = []
    failures: List[dict] = []

    for file in files:
        try:
            doc_resp = await upload_document(file, tenant_id, user_id, db, folder_id=folder_id)
            uploaded_docs.append(doc_resp)
        except Exception as err:
            logger.error(f"Failed to upload document {file.filename}: {err}")
            failures.append({"filename": file.filename, "error": str(err)})

    return BatchDocumentUploadResponse(
        documents=uploaded_docs,
        total=len(files),
        succeeded=len(uploaded_docs),
        failed=len(failures),
        failures=failures,
    )


from ..models.metadata_item import MetadataItem

# Hard ceiling on one page of documents. This endpoint had no limit at all:
# it returned every matching row for the tenant in a single response, so a
# large drive meant an unbounded query, an unbounded payload, and a presigned
# URL minted per row. Kept generous rather than a small page size so existing
# callers (the web UI fetches a folder's contents in one go) are unaffected at
# realistic drive sizes, while the unbounded case is gone. Callers that need
# more page explicitly via limit/offset and read X-Total-Count.
MAX_DOCUMENT_PAGE_SIZE = 500


async def list_documents(
    db: AsyncSession,
    tenant_id: UUID,
    folder_id: Optional[UUID] = None,
    include_all: bool = False,
    is_starred: Optional[bool] = None,
    is_trashed: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[List[DocumentListItem], int]:
    """Returns (items, total_matching) — total is the unpaginated count, so a
    caller can tell from X-Total-Count whether it's seeing everything."""
    base_filters = [Document.tenant_id == tenant_id, Document.is_trashed == is_trashed]

    if is_starred is not None:
        base_filters.append(Document.is_starred == is_starred)
    elif not include_all and folder_id is not None:
        base_filters.append(Document.folder_id == folder_id)
    elif not include_all and folder_id is None and is_starred is None and not is_trashed:
        base_filters.append(Document.folder_id.is_(None))

    total = await db.scalar(
        select(func.count()).select_from(Document).where(*base_filters)
    ) or 0

    effective_limit = MAX_DOCUMENT_PAGE_SIZE if limit is None else max(1, min(limit, MAX_DOCUMENT_PAGE_SIZE))
    effective_offset = max(0, offset)

    stmt = (
        select(Document)
        .where(*base_filters)
        .options(
            selectinload(Document.versions),
            selectinload(Document.metadata_items.and_(MetadataItem.key.in_(["quality_flag", "quality_report"]))),
        )
        .order_by(Document.created_at.desc())
        .offset(effective_offset)
        .limit(effective_limit)
    )
    res = await db.execute(stmt)
    docs = res.scalars().all()

    if total > effective_offset + len(docs):
        logger.info(
            "list_documents: returning %d of %d matching documents (offset=%d, limit=%d) — "
            "caller should paginate via offset/limit; full count is in X-Total-Count",
            len(docs), total, effective_offset, effective_limit,
        )

    items = []
    for doc in docs:
        curr_v = next((v for v in doc.versions if v.id == doc.current_version_id), doc.versions[-1] if doc.versions else None)
        size = curr_v.file_size_bytes if curr_v else 0
        s3_path = curr_v.s3_path if curr_v else None
        url = await generate_presigned_url(s3_path) if s3_path else None

        q_flag = None
        q_warnings = []
        for m in doc.metadata_items:
            if m.key == "quality_flag" and isinstance(m.value, dict):
                q_flag = m.value.get("flag")
                if not q_warnings:
                    q_warnings = m.value.get("warnings", [])
            elif m.key == "quality_report" and isinstance(m.value, dict):
                if not q_warnings:
                    q_warnings = m.value.get("warnings", [])

        items.append(
            DocumentListItem(
                id=doc.id,
                title=doc.title,
                doc_type=doc.doc_type,
                status=doc.status,
                created_at=doc.created_at,
                folder_id=doc.folder_id,
                is_starred=doc.is_starred,
                is_trashed=doc.is_trashed,
                trashed_at=doc.trashed_at,
                file_size_bytes=size,
                current_version_id=doc.current_version_id,
                s3_path=s3_path,
                download_url=url,
                quality_flag=q_flag,
                quality_warnings=q_warnings,
            )
        )
    return items, total


async def get_document(
    document_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
    actor_id: UUID,
) -> DocumentDetailResponse:
    """Retrieve a document with its versions, presigned download link, and metadata."""
    stmt = (
        select(Document)
        .where(Document.id == document_id, Document.tenant_id == tenant_id)
        .options(
            selectinload(Document.versions),
            selectinload(Document.metadata_items).selectinload(MetadataItem.regions),
        )
    )
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or access denied",
        )

    versions = []
    for v in doc.versions:
        url = await generate_presigned_url(v.s3_path) if v.s3_path else ""
        versions.append({
            "id": str(v.id),
            "version": v.version_number,
            "s3_path": v.s3_path,
            "file_size_bytes": v.file_size_bytes,
            "download_url": url,
            "created_at": v.created_at.isoformat(),
        })

    curr_version = next(
        (v for v in versions if str(v["id"]) == str(doc.current_version_id)),
        versions[-1] if versions else None,
    )

    q_flag = None
    q_warnings = []
    meta = []
    for m in doc.metadata_items:
        meta.append({
            "key": m.key,
            "value": m.value,
            "source": m.source,
            "confidence_score": m.confidence_score,
            "regions": [
                {"page_number": r.page_number, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                for r in m.regions
            ],
        })
        if m.key == "quality_flag" and isinstance(m.value, dict):
            q_flag = m.value.get("flag")
            if not q_warnings:
                q_warnings = m.value.get("warnings", [])
        elif m.key == "quality_report" and isinstance(m.value, dict):
            if not q_warnings:
                q_warnings = m.value.get("warnings", [])

    await log_action(db, actor_id, tenant_id, "document.view", resource_type="document", resource_id=doc.id)

    return DocumentDetailResponse(
        document_id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        status=doc.status,
        created_at=doc.created_at,
        folder_id=doc.folder_id,
        is_starred=doc.is_starred,
        is_trashed=doc.is_trashed,
        trashed_at=doc.trashed_at,
        current_version=curr_version,
        metadata=meta,
        versions=versions,
        quality_flag=q_flag,
        quality_warnings=q_warnings,
        possible_duplicate_candidates=doc.possible_duplicate_candidates,
    )


async def update_document(
    db: AsyncSession,
    document_id: UUID,
    tenant_id: UUID,
    doc_in: DocumentUpdate,
    actor_id: UUID,
) -> DocumentListItem:
    stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).options(selectinload(Document.versions))
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Real bug found live 2026-09-10 (QA report): "move to root" sends
    # folder_id: null to explicitly clear the field, but `folder_id is not
    # None` treats an explicit null identically to the field being omitted
    # entirely from the request -- so a move-to-root silently did nothing:
    # no error, a 200 response, the document just stayed in its old
    # folder. `model_fields_set` is Pydantic v2's actual signal for "was
    # this key present in the client's JSON at all", which a None check
    # can never recover once parsed. A folder_id genuinely absent from the
    # request (the common case: renaming a title, not touching location)
    # still correctly leaves the field untouched either way.
    changes = {}
    if "folder_id" in doc_in.model_fields_set:
        if doc_in.folder_id is not None:
            folder = await db.get(Folder, doc_in.folder_id)
            if not folder or folder.tenant_id != tenant_id:
                raise HTTPException(status_code=404, detail="Target folder not found")
        elif department_service.request_scope_folder_ids(db) is not None and doc.created_by != actor_id:
            # Department scope only follows a root-level document back to
            # whoever uploaded it, so this move would take it out of view
            # for the caller's whole department -- RLS rejects it; explain.
            raise HTTPException(
                status_code=403,
                detail="Department-scoped users can only move documents they uploaded to the root",
            )
        doc.folder_id = doc_in.folder_id
        changes["folder_id"] = str(doc_in.folder_id) if doc_in.folder_id else None

    if doc_in.title is not None:
        doc.title = doc_in.title
        changes["title"] = doc_in.title

    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(doc)

    await log_action(db, actor_id, tenant_id, "document.update", resource_type="document", resource_id=doc.id, details=changes)

    curr_v = next((v for v in doc.versions if v.id == doc.current_version_id), doc.versions[-1] if doc.versions else None)
    size = curr_v.file_size_bytes if curr_v else 0
    s3_path = curr_v.s3_path if curr_v else None
    url = await generate_presigned_url(s3_path) if s3_path else None

    return DocumentListItem(
        id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        status=doc.status,
        created_at=doc.created_at,
        folder_id=doc.folder_id,
        is_starred=doc.is_starred,
        is_trashed=doc.is_trashed,
        trashed_at=doc.trashed_at,
        file_size_bytes=size,
        current_version_id=doc.current_version_id,
        s3_path=s3_path,
        download_url=url,
    )


async def toggle_star_document(db: AsyncSession, document_id: UUID, tenant_id: UUID, actor_id: UUID) -> DocumentListItem:
    stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).options(selectinload(Document.versions))
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.is_starred = not doc.is_starred
    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(doc)

    await log_action(db, actor_id, tenant_id, "document.star_toggle", resource_type="document", resource_id=doc.id, details={"is_starred": doc.is_starred})

    curr_v = next((v for v in doc.versions if v.id == doc.current_version_id), doc.versions[-1] if doc.versions else None)
    size = curr_v.file_size_bytes if curr_v else 0
    s3_path = curr_v.s3_path if curr_v else None
    url = await generate_presigned_url(s3_path) if s3_path else None

    return DocumentListItem(
        id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        status=doc.status,
        created_at=doc.created_at,
        folder_id=doc.folder_id,
        is_starred=doc.is_starred,
        is_trashed=doc.is_trashed,
        trashed_at=doc.trashed_at,
        file_size_bytes=size,
        current_version_id=doc.current_version_id,
        s3_path=s3_path,
        download_url=url,
    )


async def toggle_trash_document(db: AsyncSession, document_id: UUID, tenant_id: UUID, actor_id: UUID) -> DocumentListItem:
    stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).options(selectinload(Document.versions))
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.is_trashed = not doc.is_trashed
    doc.trashed_at = datetime.utcnow() if doc.is_trashed else None

    # T66/D-7 — this was the missing half of the retention engine: nothing
    # ever assigned 'operational_trash', so cleanup_expired_trashed_items'
    # class lookup treated every trashed document as the permanent default
    # class and refused to purge it, ever — the 30-day trash-purge has been
    # silently inert for every document since D-7 shipped. Only touches the
    # two states this toggle itself owns; never overwrites a class assigned
    # by something else (e.g. a future 'statutory_record' reclassification).
    if doc.is_trashed and doc.retention_class == "unclassified_permanent":
        doc.retention_class = "operational_trash"
    elif not doc.is_trashed and doc.retention_class == "operational_trash":
        doc.retention_class = "unclassified_permanent"

    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(doc)

    await log_action(db, actor_id, tenant_id, "document.trash_toggle", resource_type="document", resource_id=doc.id, details={"is_trashed": doc.is_trashed})

    curr_v = next((v for v in doc.versions if v.id == doc.current_version_id), doc.versions[-1] if doc.versions else None)
    size = curr_v.file_size_bytes if curr_v else 0
    s3_path = curr_v.s3_path if curr_v else None
    url = await generate_presigned_url(s3_path) if s3_path else None

    return DocumentListItem(
        id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        status=doc.status,
        created_at=doc.created_at,
        folder_id=doc.folder_id,
        is_starred=doc.is_starred,
        is_trashed=doc.is_trashed,
        trashed_at=doc.trashed_at,
        file_size_bytes=size,
        current_version_id=doc.current_version_id,
        s3_path=s3_path,
        download_url=url,
    )


WORM_PERMANENT_RETENTION_DAYS = 36500  # T64 — S3 Object Lock has no infinite
# option; 100 years is the standard real-world proxy for "permanent" in WORM
# archival systems. This is a storage-layer lock duration only — it does not
# affect the DB-level retention_class engine, where 'statutory_record'
# already means NULL/never-engine-purged regardless of this number.


async def archive_document_as_statutory_record(db: AsyncSession, tenant_id: UUID, document_id: UUID) -> None:
    """T64/T66 — the other missing half: a document that becomes evidence
    for a Record (T60, i.e. records_service.create_record was called with
    base_evidence_fact_id) graduates from an ordinary upload to something
    that needs WORM tamper-evident storage and permanent retention — this
    is the D-7 'statutory_record' class's own stated purpose ("explicitly
    for anything tied to a property/entity record"), but nothing ever
    actually assigned it or archived the file. Best-effort: never raises —
    called from inside create_record, and a record's own creation must
    never fail because archival storage had a problem.
    """
    try:
        stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).options(selectinload(Document.versions))
        res = await db.execute(stmt)
        doc = res.scalar_one_or_none()
        if not doc:
            return
        doc.retention_class = "statutory_record"

        curr_v = next((v for v in doc.versions if v.id == doc.current_version_id), None)
        if curr_v and curr_v.s3_path:
            file_bytes = await download_file(curr_v.s3_path)
            await archive_file_with_retention(file_bytes, curr_v.s3_path, "application/octet-stream", WORM_PERMANENT_RETENTION_DAYS)
            logger.info(f"T64 WORM-archived document {document_id} (now a statutory record)")
    except Exception as e:
        logger.warning(f"T64/T66 statutory-record archival skipped for document {document_id}: {e}")


async def delete_document_permanently(
    db: AsyncSession, document_id: UUID, tenant_id: UUID,
    actor_id: UUID, policy_version: Optional[str] = None,
) -> None:
    stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id).options(selectinload(Document.versions))
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_title = doc.title

    for v in doc.versions:
        if v.s3_path:
            try:
                await delete_file(v.s3_path)
            except Exception as e:
                logger.warning(f"Error deleting file from S3: {e}")

    # Clear current_version_id self-referential foreign key
    doc.current_version_id = None
    await db.flush()

    # Cascade delete dependent rows in database
    from app.models.chunk import Chunk
    from app.models.metadata_item import MetadataItem
    from app.models.document_version import DocumentVersion

    await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await db.execute(delete(MetadataItem).where(MetadataItem.document_id == document_id))
    await db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == document_id))

    await db.delete(doc)
    # T08/T66 — log_action before commit, not after: a permanent delete is
    # irreversible, so the audit write must land in the same transaction
    # as the delete, not a separate one that can fail after the data is
    # already gone (discovered as a real gap during T66's live testing —
    # the old commit-then-log ordering let a failed audit write leave a
    # deleted document with no audit trail at all).
    await log_action(db, actor_id, tenant_id, "document.delete", resource_type="document", resource_id=document_id, details={"title": doc_title}, policy_version=policy_version)
    await db.commit()


async def get_drive_stats(db: AsyncSession, tenant_id: UUID) -> DriveStatsResponse:
    # Folders count
    f_res = await db.execute(select(func.count(Folder.id)).where(Folder.tenant_id == tenant_id, Folder.is_trashed == False))
    total_folders = f_res.scalar() or 0

    # Documents count
    d_res = await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_trashed == False))
    total_files = d_res.scalar() or 0

    # Starred count
    s_f = await db.execute(select(func.count(Folder.id)).where(Folder.tenant_id == tenant_id, Folder.is_starred == True, Folder.is_trashed == False))
    s_d = await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_starred == True, Document.is_trashed == False))
    total_starred = (s_f.scalar() or 0) + (s_d.scalar() or 0)

    # Trashed count
    t_f = await db.execute(select(func.count(Folder.id)).where(Folder.tenant_id == tenant_id, Folder.is_trashed == True))
    t_d = await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_trashed == True))
    total_trashed = (t_f.scalar() or 0) + (t_d.scalar() or 0)

    # Total size in bytes
    v_res = await db.execute(
        select(func.sum(DocumentVersion.file_size_bytes))
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )
    total_bytes = v_res.scalar() or 0

    return DriveStatsResponse(
        total_files=total_files,
        total_folders=total_folders,
        total_starred=total_starred,
        total_trashed=total_trashed,
        total_size_bytes=total_bytes,
        total_bytes=total_bytes
    )


async def _resolve_policy_actor(db: AsyncSession, tenant_id: UUID, cache: dict) -> Optional[UUID]:
    """T66 — audit_dg_logs.actor_id is NOT NULL + FK'd to a real user row at
    the DB level, so a scheduled/policy-driven purge still needs a real
    actor to attribute the deletion to, per tenant. Prefers that tenant's
    it_admin (the tenant-wide administrative role, T50) since a
    policy-driven system action is closest in kind to what that role
    already covers; falls back to any user in the tenant if no it_admin
    exists, so a tenant that hasn't been migrated onto personas yet still
    gets its trash purged rather than silently skipped forever."""
    if tenant_id in cache:
        return cache[tenant_id]

    from app.models.role import Role
    from app.models.user import User
    # The tenant's Admin: holder of the locked system role, or -- for a user
    # not moved onto custom roles yet -- the old it_admin persona.
    res = await db.execute(
        select(User.id)
        .outerjoin(Role, Role.id == User.role_id)
        .where(User.tenant_id == tenant_id, or_(Role.is_system.is_(True), User.role == "it_admin"))
        .order_by(User.created_at)
        .limit(1)
    )
    actor_id = res.scalar_one_or_none()
    if actor_id is None:
        res = await db.execute(select(User.id).where(User.tenant_id == tenant_id).limit(1))
        actor_id = res.scalar_one_or_none()

    cache[tenant_id] = actor_id
    return actor_id


async def cleanup_expired_trashed_items(db: AsyncSession, retention_days: int = 30, tenant_id: Optional[UUID] = None) -> dict:
    """tenant_id=None sweeps every tenant — that's what the scheduled
    worker task wants (a system-wide retention janitor). A real caller
    acting on behalf of one tenant (the "Empty Bin" API) MUST pass its
    own tenant_id, or this silently purges every other tenant's expired
    trash too — confirmed live: a single-tenant Empty Bin click deleted
    an unrelated tenant's folder alongside the caller's own, because
    neither query here was ever scoped by tenant."""
    from datetime import datetime, timedelta
    from app.models.folder import Folder
    from app.models.retention_class import RetentionClass
    from app.services.folder_service import delete_folder_permanently

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    actor_cache: dict = {}

    # T66/D-7 — a document's own retention_class governs whether/when it's
    # eligible, not the flat `retention_days` arg (that still applies to
    # folders below, which stay on the pre-D-7 flat rule). A class with
    # retention_days=NULL (the default) is never engine-purged, however
    # long it's sat trashed — only 'operational_trash' has a finite period.
    class_res = await db.execute(select(RetentionClass.class_name, RetentionClass.retention_days))
    class_periods = dict(class_res.all())

    doc_conditions = [Document.is_trashed == True, Document.trashed_at.is_not(None)]
    if tenant_id is not None:
        doc_conditions.append(Document.tenant_id == tenant_id)
    doc_stmt = select(Document.id, Document.tenant_id, Document.retention_class, Document.trashed_at, Document.title).where(
        *doc_conditions,
    )
    doc_res = await db.execute(doc_stmt)
    candidate_docs = doc_res.all()

    now = datetime.utcnow()
    deleted_doc_count = 0
    protected_documents = []  # (title, retention_class) — never auto-purged, no matter the caller's retention_days
    pending_documents = []  # (title, retention_class, days_remaining) — has a finite period, just not up yet
    for d_id, t_id, retention_class, trashed_at, title in candidate_docs:
        class_days = class_periods.get(retention_class)
        
        if retention_days > 0:
            if class_days is None:
                protected_documents.append({"title": title, "retention_class": retention_class})
                continue  # permanent class, or an unrecognized one — fail safe, never purge
            if now - trashed_at < timedelta(days=class_days):
                days_remaining = class_days - (now - trashed_at).days
                pending_documents.append({"title": title, "retention_class": retention_class, "days_remaining": max(days_remaining, 0)})
                continue
                
        actor_id = await _resolve_policy_actor(db, t_id, actor_cache)
        if actor_id is None:
            logger.warning(f"Skipping purge of document {d_id}: tenant {t_id} has no user to attribute the deletion to")
            continue
        try:
            await delete_document_permanently(db, d_id, t_id, actor_id, policy_version="T66_retention_purge_v1")
            deleted_doc_count += 1
        except Exception as e:
            logger.warning(f"Error purging expired trashed document {d_id}: {e}")

    # 2. Fetch expired trashed folders (trashed_at <= cutoff)
    folder_conditions = [Folder.is_trashed == True, Folder.trashed_at.is_not(None), Folder.trashed_at <= cutoff]
    if tenant_id is not None:
        folder_conditions.append(Folder.tenant_id == tenant_id)
    folder_stmt = select(Folder.id, Folder.tenant_id).where(*folder_conditions)
    folder_res = await db.execute(folder_stmt)
    expired_folders = folder_res.all()

    deleted_folder_count = 0
    for f_id, t_id in expired_folders:
        actor_id = await _resolve_policy_actor(db, t_id, actor_cache)
        if actor_id is None:
            logger.warning(f"Skipping purge of folder {f_id}: tenant {t_id} has no user to attribute the deletion to")
            continue
        try:
            await delete_folder_permanently(db, f_id, t_id, actor_id, policy_version="T66_retention_purge_v1")
            deleted_folder_count += 1
        except Exception as e:
            logger.warning(f"Error purging expired trashed folder {f_id}: {e}")

    logger.info(f"Purged {deleted_doc_count} expired documents and {deleted_folder_count} expired folders older than {retention_days} days in Bin.")
    return {
        "deleted_documents": deleted_doc_count,
        "deleted_folders": deleted_folder_count,
        "protected_documents": protected_documents,
        "pending_documents": pending_documents,
    }


async def get_chunks_for_document(db: AsyncSession, document_id: UUID, tenant_id: UUID) -> dict:
    """List every chunk indexed for one document — real gap found live
    2026-09-09 (verification report): no endpoint exposed chunk-level data
    at all, so a citation whose `chunk_id` came back from search/chat
    could never be inspected directly (what text/page/chunk_metadata it
    actually carries) without a raw DB query. Order by chunk_index so the
    response reads in the same top-to-bottom order TextChunker produced
    it in, matching how a person would actually want to page through it.

    Deliberately excludes the embedding vector (1024 floats serialized as
    JSON would dwarf the actual useful content of the response for zero
    debugging value) — chunk_metadata, page_number and content are the
    fields a citation-debugging session actually needs."""
    from app.models.chunk import Chunk

    doc = await db.get(Document, document_id)
    if not doc or doc.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    stmt = (
        select(Chunk)
        .where(Chunk.document_id == document_id, Chunk.tenant_id == tenant_id)
        .order_by(Chunk.chunk_index)
    )
    res = await db.execute(stmt)
    chunks = res.scalars().all()

    return {
        "document_id": str(document_id),
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": str(c.id),
                "page_number": c.page_number,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "chunk_metadata": c.chunk_metadata,
            }
            for c in chunks
        ],
    }