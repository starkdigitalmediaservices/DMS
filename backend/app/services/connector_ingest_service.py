"""Shared ingestion entry point for non-HTTP connector sources.

Watched folder, FTP/SFTP, and email-in adapters all call `ingest_bytes()`
below instead of duplicating upload logic. It wraps raw bytes into the
same `UploadFile` path the manual HTTP upload uses, so hashing, storage,
and the Celery hand-off behave identically no matter which source the
file came from.

Demo scope: connectors run as a single fixed actor (DEFAULT_CONNECTOR_EMAIL)
rather than resolving per-source tenant/user mapping. That mapping is real
production scope (see backlog T40), not a one-night addition.
"""
import io
import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import UploadFile
from starlette.datastructures import Headers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.document import Document
from ..models.document_version import DocumentVersion
from ..models.folder import Folder
from ..models.user import User
from .document_service import upload_document
from ..schemas.document import DocumentUploadResponse

logger = logging.getLogger(__name__)

# Was a hardcoded literal pointing at an email no seeded user actually has,
# which meant every connector (SFTP, watched-folder, email-in) failed on
# every single poll with "Connector actor not found" -- see
# settings.connector_actor_email's own docstring. Now sourced from config
# so an environment can point it at a real account instead of being broken
# out of the box.
DEFAULT_CONNECTOR_EMAIL = settings.connector_actor_email

_actor_cache: dict[str, tuple[UUID, UUID]] = {}


async def get_connector_actor(db: AsyncSession, email: str = DEFAULT_CONNECTOR_EMAIL) -> tuple[UUID, UUID]:
    """Resolve (tenant_id, user_id) for the fixed connector identity, cached per email."""
    if email in _actor_cache:
        return _actor_cache[email]

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise RuntimeError(
            f"Connector actor '{email}' not found. Sign up this user first, "
            "or point DEFAULT_CONNECTOR_EMAIL at an existing account."
        )

    identity = (user.tenant_id, user.id)
    _actor_cache[email] = identity
    return identity


async def already_ingested(db: AsyncSession, tenant_id: UUID, file_hash: str) -> bool:
    """Shared hash-based dedup check, used by every connector before ingesting a file."""
    result = await db.execute(
        select(DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.file_hash == file_hash, Document.tenant_id == tenant_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_or_create_folder_path(
    db: AsyncSession,
    tenant_id: UUID,
    segments: list[str],
    cache: Optional[dict] = None,
) -> Optional[UUID]:
    """Resolve nested folder segments (e.g. ["A", "B"]) to a folder_id, creating
    any that don't exist yet. Reused across connector sources so a subfolder in a
    watched directory / SFTP tree maps to a real, identically-nested DMS folder."""
    if cache is None:
        cache = {}

    parent_id: Optional[UUID] = None
    for name in segments:
        key = (parent_id, name)
        if key in cache:
            parent_id = cache[key]
            continue

        result = await db.execute(
            select(Folder).where(
                Folder.tenant_id == tenant_id,
                Folder.name == name,
                Folder.parent_id == parent_id,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            folder = Folder(id=uuid.uuid4(), name=name, parent_id=parent_id, tenant_id=tenant_id)
            db.add(folder)
            await db.flush()

        cache[key] = folder.id
        parent_id = folder.id

    return parent_id


async def ingest_bytes(
    content: bytes,
    filename: str,
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    content_type: str = "application/octet-stream",
    folder_id: Optional[UUID] = None,
) -> DocumentUploadResponse:
    """Ingest raw bytes from a connector source through the standard upload path.

    tenant_id/user_id are required, not resolved here -- every real caller
    (watched-folder, SFTP, email-in, the inbound webhook) already calls
    get_connector_actor() once of its own, for its dedup/folder-path checks
    before this. This function used to *also* call get_connector_actor()
    internally via a separate actor_email default, so every file was
    resolving the connector's identity twice per ingest -- redundant, and a
    real risk: found live while adding test coverage for the watched-folder
    connector, where patching only the caller's resolution (not this
    function's own internal one) left ingest_bytes silently resolving
    through the real DEFAULT_CONNECTOR_EMAIL account instead of the test's
    throwaway one, writing real documents into a real tenant. One
    resolution per file now, passed straight through."""
    upload_file = UploadFile(
        file=io.BytesIO(content),
        size=len(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )

    logger.info("Connector ingest: %s (%d bytes) as tenant=%s user=%s", filename, len(content), tenant_id, user_id)
    return await upload_document(upload_file, tenant_id, user_id, db, folder_id=folder_id)
