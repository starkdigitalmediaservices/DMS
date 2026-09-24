from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from datetime import datetime
import uuid
from typing import Any, Optional

from app.database import Base


class ReviewOriginal(Base):
    """The block list exactly as extraction produced it (migration 0055).

    Immutable: a BEFORE UPDATE trigger refuses any change. Fact-backed table
    cells carry the OCR value the fact had before any human touched it, so
    a single cell can always be reverted to it."""

    __tablename__ = "doc_dg_review_originals"
    __table_args__ = (UniqueConstraint("document_id", "version_id", name="uq_doc_dg_review_originals_doc_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_documents.id", ondelete="CASCADE"))
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("doc_dg_document_versions.id", ondelete="CASCADE"), nullable=True
    )
    builder: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ReviewState(Base):
    """The reviewer's working copy of one document's blocks.

    Fact-backed cells are stored as {"fact_id": ...} only; their value is
    read live from doc_dg_facts. `version` is compared against the
    client's If-Match on every write (409 when stale)."""

    __tablename__ = "doc_dg_review_states"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_dg_documents.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    original_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_review_originals.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    blocks: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id"), nullable=True)


class ReviewAuditEntry(Base):
    """Append-only history of every review action (BEFORE UPDATE OR DELETE
    trigger). document_id has no FK on purpose -- the history outlives a
    purged document, same as audit_dg_logs."""

    __tablename__ = "doc_dg_review_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    block_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    row_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    col: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fact_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False)
