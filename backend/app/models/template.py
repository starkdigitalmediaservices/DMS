from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, ForeignKey
from datetime import datetime
from typing import Any, Optional
import uuid

from app.database import Base


class Template(Base):
    """A statutory form definition, keyed by form type x era (Section 12, T24).

    field_schema is a list of per-field dicts — name, type, required, and
    that field's own validation (pattern/min/max/allowed_values) — driven
    by data, not code, so a new form era never requires a deploy. Validation
    is written per template, never once for everything (Section 12).

    tenant_id is nullable: templates seeded before this column existed are
    NULL and stay readable by every tenant (a form's layout doesn't vary
    per tenant); a template created through the API now (see
    template_service.create_template) is scoped to its creating tenant, the
    only shape doc_dg_templates' RLS WITH CHECK actually allows on INSERT.

    T26 — layout='spread' marks a register whose entries run across two
    facing pages; each field_schema entry then also carries a "half":
    "left"|"right" key saying which page it prints on (the serial/role
    field is expected on both and needs neither). This convention is
    invented, not modeled on a real scanned spread — no seeded template
    exists yet (T25 stays blocked on A1, no reference corpus) — so treat
    it as a best-effort mechanism to revalidate against real Waqf-register
    layouts once one is available, not a confirmed real-world shape.
    """

    __tablename__ = "doc_dg_templates"
    __table_args__ = (
        UniqueConstraint("form_type", "era_label", name="uq_doc_dg_templates_form_era"),
        CheckConstraint("layout IN ('single_page', 'spread')", name="ck_doc_dg_templates_layout"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True, nullable=True)
    form_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    era_label: Mapped[str] = mapped_column(String, nullable=False)
    field_schema: Mapped[Any] = mapped_column(JSONB, nullable=False)
    layout: Mapped[str] = mapped_column(Text, nullable=False, server_default="single_page")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
