from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy import ForeignKey, Text, Boolean
from datetime import datetime
from typing import List, Optional
import uuid

from app.database import Base


class Role(Base):
    """Admin-defined role, one set per tenant (custom roles, 2026-09-25).

    A role says WHAT a user may do (`permissions`, keys from the fixed
    catalogue in app/permissions.py). WHICH documents they see is still
    department scope (migration 0053); `all_departments` is the one switch
    that lifts it, replacing the old hard-coded TENANT_WIDE_ROLES.

    `is_system` marks the tenant's locked Admin role: it holds every
    permission implicitly (so a permission added later reaches Admin
    without a data change), and it cannot be edited, renamed or deleted.
    """

    __tablename__ = "iam_dg_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    all_departments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permissions: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    created_by_actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id", ondelete="SET NULL", use_alter=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
