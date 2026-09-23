from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, Enum, UniqueConstraint
from datetime import datetime
import uuid
import enum
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.tenant import Tenant

class UserRole(str, enum.Enum):
    # T50 — six personas (Section 9/12). 'admin'/'user' are the original
    # two values, kept because Postgres enum types can't drop values
    # without recreating the type; existing rows are migrated onto the
    # new personas (admin -> it_admin, user -> operator), not left on
    # the old values.
    admin = "admin"
    user = "user"
    records_officer = "records_officer"
    operator = "operator"  # "operator/adjudicator" in the spec
    department_head = "department_head"
    legal_counsel = "legal_counsel"
    it_admin = "it_admin"
    auditor = "auditor"  # external auditor, read-only

class User(Base):
    __tablename__ = "iam_dg_users"
    __table_args__ = (UniqueConstraint("email", name="uq_iam_dg_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    email: Mapped[str] = mapped_column(index=True)
    full_name: Mapped[str] = mapped_column(default="")
    hashed_password: Mapped[str] = mapped_column("password_hash")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), default=UserRole.operator)
    locale: Mapped[str] = mapped_column(default="en")  # T95 — 'en' or 'mr'
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")