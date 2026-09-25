from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, Text, UniqueConstraint
from datetime import datetime
from typing import Optional
import uuid

from app.database import Base


class Department(Base):
    """T50 — a named group of users, granted scope over a set of projects
    (folders). "Department scope is an RBAC group over projects, not a
    container level" — this is independent of folder nesting depth; a
    department's granted folders (DepartmentFolder) can be anywhere in
    the tree, not confined to one level.
    """

    __tablename__ = "iam_dg_departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class DepartmentMember(Base):
    __tablename__ = "iam_dg_department_members"
    __table_args__ = (UniqueConstraint("department_id", "user_id", name="uq_iam_dg_department_members"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_departments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class DepartmentFolder(Base):
    """One project (folder) a department has scope over."""

    __tablename__ = "iam_dg_department_folders"
    __table_args__ = (UniqueConstraint("department_id", "folder_id", name="uq_iam_dg_department_folders"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_departments.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_folders.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class UserFolder(Base):
    """One folder shared directly with one user (custom roles R14) -- same
    effect as a department grant, without a department. Migration 0057."""

    __tablename__ = "iam_dg_user_folders"
    __table_args__ = (UniqueConstraint("user_id", "folder_id", name="uq_iam_dg_user_folders"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_users.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_folders.id", ondelete="CASCADE"), index=True)
    created_by_actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
