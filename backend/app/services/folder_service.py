from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select, text
from fastapi import HTTPException, status
from uuid import UUID
from datetime import datetime
from typing import List, Optional

from app.database import establish_tenant_context
from app.models.folder import Folder
from app.models.document import Document
from app.schemas.folder import FolderCreate, FolderUpdate, FolderResponse, FolderTreeNode
from app.services.audit_service import log_action
from app.services import department_service


async def create_folder(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    folder_in: FolderCreate
) -> FolderResponse:
    if folder_in.parent_id:
        parent = await db.get(Folder, folder_in.parent_id)
        if not parent or parent.tenant_id != tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent folder not found")
    elif department_service.request_scope_folder_ids(db) is not None:
        # RLS would reject the insert anyway (a root folder is outside any
        # department's grant); say why instead of surfacing a policy error.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department-scoped users can only create folders inside their department's projects",
        )

    folder = Folder(
        name=folder_in.name,
        parent_id=folder_in.parent_id,
        tenant_id=tenant_id,
        created_by=user_id,
        color=folder_in.color or "#1a73e8"
    )
    db.add(folder)
    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(folder)

    await log_action(db, user_id, tenant_id, "folder.create", resource_type="folder", resource_id=folder.id, details={"name": folder.name})

    return FolderResponse.model_validate(folder)


async def list_folders(
    db: AsyncSession,
    tenant_id: UUID,
    parent_id: Optional[UUID] = None,
    include_root: bool = False,
    is_starred: Optional[bool] = None,
    is_trashed: bool = False
) -> List[FolderResponse]:
    stmt = select(Folder).where(Folder.tenant_id == tenant_id, Folder.is_trashed == is_trashed)

    if is_starred is not None:
        stmt = stmt.where(Folder.is_starred == is_starred)
    elif not include_root and parent_id is not None:
        stmt = stmt.where(Folder.parent_id == parent_id)
    elif not include_root and parent_id is None and is_starred is None and not is_trashed:
        scope = department_service.request_scope_folder_ids(db)
        if scope is None:
            stmt = stmt.where(Folder.parent_id.is_(None))
        else:
            # A department can be granted a folder at any depth; for its
            # members that folder is a top-level entry, since they can't
            # see (or navigate through) the parent it sits under.
            stmt = stmt.where(or_(Folder.parent_id.is_(None), Folder.parent_id.notin_(scope)))

    stmt = stmt.order_by(Folder.name.asc())
    res = await db.execute(stmt)
    folders = res.scalars().all()
    return [FolderResponse.model_validate(f) for f in folders]


async def get_folder(db: AsyncSession, folder_id: UUID, tenant_id: UUID, actor_id: Optional[UUID] = None) -> Folder:
    stmt = select(Folder).where(Folder.id == folder_id, Folder.tenant_id == tenant_id)
    res = await db.execute(stmt)
    folder = res.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    # T07 — the document-detail path already logs "document.view"; folder
    # detail was the one asymmetric gap (list_folders/list_documents are
    # both deliberately unlogged — same noise tradeoff either way).
    if actor_id is not None:
        await log_action(db, actor_id, tenant_id, "folder.view", resource_type="folder", resource_id=folder.id)
    return folder


async def _is_descendant(db: AsyncSession, candidate_id: Optional[UUID], ancestor_id: UUID) -> bool:
    """T97 — one recursive CTE walks the whole parent_id chain from
    candidate_id up to the root in a single round trip, instead of the
    old one-query-per-level Python loop (up to 100 sequential round
    trips for a single circular-reference check on a deep hierarchy —
    D-1 kept arbitrarily deep recursive folders rather than collapsing
    to a fixed two-level container, so this chain is genuinely
    unbounded in practice, not just in theory).
    """
    if candidate_id is None:
        return False
    stmt = text("""
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id, 1 AS depth FROM doc_dg_folders WHERE id = :candidate_id
            UNION ALL
            SELECT f.id, f.parent_id, a.depth + 1
            FROM doc_dg_folders f
            JOIN ancestors a ON f.id = a.parent_id
            WHERE a.depth < 1000
        )
        SELECT EXISTS (SELECT 1 FROM ancestors WHERE id = :ancestor_id)
    """)
    res = await db.execute(stmt, {"candidate_id": str(candidate_id), "ancestor_id": str(ancestor_id)})
    return bool(res.scalar())


async def update_folder(
    db: AsyncSession,
    folder_id: UUID,
    tenant_id: UUID,
    folder_in: FolderUpdate,
    actor_id: UUID,
) -> FolderResponse:
    folder = await get_folder(db, folder_id, tenant_id)

    # Real bug found live 2026-09-10 (QA report): same fix as
    # document_service.update_document -- "move to root" sends parent_id:
    # null to explicitly clear it, but `is not None` can't tell that apart
    # from the field being omitted, so it silently did nothing. See that
    # function's comment for the full explanation.
    changes = {}
    if "parent_id" in folder_in.model_fields_set:
        if folder_in.parent_id is not None:
            if folder_in.parent_id == folder_id:
                raise HTTPException(status_code=400, detail="Folder cannot be its own parent")
            if await _is_descendant(db, folder_in.parent_id, folder_id):
                raise HTTPException(status_code=400, detail="Cannot move a folder into one of its own subfolders")
            parent = await db.get(Folder, folder_in.parent_id)
            if not parent or parent.tenant_id != tenant_id:
                raise HTTPException(status_code=404, detail="Target parent folder not found")
        folder.parent_id = folder_in.parent_id
        changes["parent_id"] = str(folder_in.parent_id) if folder_in.parent_id else None

    if folder_in.name is not None:
        folder.name = folder_in.name
        changes["name"] = folder_in.name
    if folder_in.color is not None:
        folder.color = folder_in.color
        changes["color"] = folder_in.color

    folder.updated_at = datetime.utcnow()
    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(folder)

    await log_action(db, actor_id, tenant_id, "folder.update", resource_type="folder", resource_id=folder.id, details=changes)

    return FolderResponse.model_validate(folder)


async def toggle_star_folder(db: AsyncSession, folder_id: UUID, tenant_id: UUID, actor_id: UUID) -> FolderResponse:
    folder = await get_folder(db, folder_id, tenant_id)
    folder.is_starred = not folder.is_starred
    folder.updated_at = datetime.utcnow()
    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(folder)

    await log_action(db, actor_id, tenant_id, "folder.star_toggle", resource_type="folder", resource_id=folder.id, details={"is_starred": folder.is_starred})

    return FolderResponse.model_validate(folder)


async def toggle_trash_folder(db: AsyncSession, folder_id: UUID, tenant_id: UUID, actor_id: UUID) -> FolderResponse:
    folder = await get_folder(db, folder_id, tenant_id)
    folder.is_trashed = not folder.is_trashed
    folder.trashed_at = datetime.utcnow() if folder.is_trashed else None
    folder.updated_at = datetime.utcnow()
    await db.commit()
    await establish_tenant_context(db, tenant_id)  # T96 — see database.py's docstring
    await db.refresh(folder)

    await log_action(db, actor_id, tenant_id, "folder.trash_toggle", resource_type="folder", resource_id=folder.id, details={"is_trashed": folder.is_trashed})

    return FolderResponse.model_validate(folder)


async def delete_folder_permanently(
    db: AsyncSession, folder_id: UUID, tenant_id: UUID,
    actor_id: UUID, policy_version: Optional[str] = None,
) -> None:
    from app.services.document_service import delete_document_permanently

    folder = await db.get(Folder, folder_id)
    if not folder or folder.tenant_id != tenant_id:
        return

    folder_name = folder.name

    # 1. Delete all documents in this folder
    doc_stmt = select(Document.id).where(Document.folder_id == folder_id, Document.tenant_id == tenant_id)
    doc_res = await db.execute(doc_stmt)
    doc_ids = doc_res.scalars().all()
    for d_id in doc_ids:
        await delete_document_permanently(db, d_id, tenant_id, actor_id=actor_id, policy_version=policy_version)

    # 2. Delete all subfolders recursively
    sub_stmt = select(Folder.id).where(Folder.parent_id == folder_id, Folder.tenant_id == tenant_id)
    sub_res = await db.execute(sub_stmt)
    sub_ids = sub_res.scalars().all()
    for s_id in sub_ids:
        await delete_folder_permanently(db, s_id, tenant_id, actor_id=actor_id, policy_version=policy_version)

    await db.delete(folder)
    # T08/T66 — same fix as delete_document_permanently: log before commit,
    # not after, so an irreversible delete and its audit record land in
    # one transaction.
    await log_action(db, actor_id, tenant_id, "folder.delete", resource_type="folder", resource_id=folder_id, details={"name": folder_name}, policy_version=policy_version)
    await db.commit()


async def get_folder_tree(db: AsyncSession, tenant_id: UUID) -> List[FolderTreeNode]:
    stmt = select(Folder).where(Folder.tenant_id == tenant_id, Folder.is_trashed == False).order_by(Folder.name.asc())
    res = await db.execute(stmt)
    all_folders = res.scalars().all()

    nodes = {f.id: FolderTreeNode(id=f.id, name=f.name, parent_id=f.parent_id, color=f.color, subfolders=[]) for f in all_folders}
    roots: List[FolderTreeNode] = []

    for f in all_folders:
        node = nodes[f.id]
        if f.parent_id and f.parent_id in nodes:
            nodes[f.parent_id].subfolders.append(node)
        else:
            roots.append(node)

    return roots
