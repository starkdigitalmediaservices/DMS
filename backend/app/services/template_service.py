import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template
from app.services.audit_service import log_action

VALID_LAYOUTS = ("single_page", "spread")

# D-5 — conservative default for any field that doesn't declare its own
# confidence_bands: a template nobody has calibrated yet should default
# toward "ask a person," not toward "trust it."
DEFAULT_CONFIDENCE_BANDS = {"auto_commit": 0.85, "review_floor": 0.5}


def classify_confidence(field_def: Dict[str, Any], confidence: Optional[float], is_handwritten: bool = False) -> str:
    """D-5 — apply one field's confidence_bands (or the conservative global
    default) to a reported confidence score. Returns 'machine' or
    'in_review' — never 'verified', which only the human promotion step
    (T51) may write.

    No reported confidence at all is treated as the lowest-trust case:
    'in_review', not an auto-commit by default.

    T30 — a handwritten field never auto-commits, no matter how confident
    the model is: "never verified without a human" starts at extraction,
    not just at the confirm step (T55 already enforces the other half —
    bulk_confirm_facts refuses to promote one to 'verified').
    """
    if is_handwritten:
        return "in_review"
    if confidence is None:
        return "in_review"

    bands = field_def.get("confidence_bands") or DEFAULT_CONFIDENCE_BANDS
    auto_commit = bands.get("auto_commit", DEFAULT_CONFIDENCE_BANDS["auto_commit"])
    return "machine" if confidence >= auto_commit else "in_review"


@dataclass
class ValidationError:
    field: str
    reason: str


async def get_template(db: AsyncSession, form_type: str, era_label: str) -> Optional[Template]:
    res = await db.execute(
        select(Template).where(Template.form_type == form_type, Template.era_label == era_label)
    )
    return res.scalar_one_or_none()


async def list_templates(db: AsyncSession, form_type: Optional[str] = None) -> List[Template]:
    stmt = select(Template)
    if form_type:
        stmt = stmt.where(Template.form_type == form_type)
    res = await db.execute(stmt.order_by(Template.form_type, Template.era_label))
    return list(res.scalars().all())


async def get_template_by_id(db: AsyncSession, template_id: uuid.UUID) -> Template:
    template = await db.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


def _validate_field_schema(field_schema: List[Dict[str, Any]]) -> None:
    if not field_schema:
        raise HTTPException(status_code=400, detail="field_schema must have at least one field")
    seen = set()
    for field_def in field_schema:
        name = field_def.get("name") if isinstance(field_def, dict) else None
        if not name:
            raise HTTPException(status_code=400, detail="every field in field_schema needs a non-empty 'name'")
        if name in seen:
            raise HTTPException(status_code=400, detail=f"duplicate field name '{name}' in field_schema")
        seen.add(name)


async def create_template(
    db: AsyncSession, form_type: str, era_label: str, field_schema: List[Dict[str, Any]],
    layout: str, actor_id: uuid.UUID, tenant_id: uuid.UUID,
) -> Template:
    """Any tenant's it_admin can register a form type (T24). Templates are
    tenant-scoped on creation — the row's tenant_id is set to the creating
    actor's own tenant, both because that's what the audit entry needs and
    because doc_dg_templates' RLS WITH CHECK requires a real tenant_id on
    every INSERT (a NULL insert is always rejected, even though NULL rows,
    once they exist, are readable by every tenant — see the seeded
    templates predating this table's tenant_id column). This function used
    to build the Template row without ever setting tenant_id at all, so
    every create attempt failed RLS and 500'd; this docstring previously
    (incorrectly) claimed the column didn't exist."""
    if layout not in VALID_LAYOUTS:
        raise HTTPException(status_code=400, detail=f"layout must be one of {VALID_LAYOUTS}")
    _validate_field_schema(field_schema)

    template = Template(
        tenant_id=tenant_id, form_type=form_type, era_label=era_label,
        field_schema=field_schema, layout=layout,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"A template for '{form_type} | {era_label}' already exists")

    await log_action(
        db, actor_id, tenant_id, "template.create",
        resource_type="template", resource_id=template.id,
        details={"form_type": form_type, "era_label": era_label, "layout": layout},
    )
    await db.commit()
    return template


async def update_template(
    db: AsyncSession, template_id: uuid.UUID, actor_id: uuid.UUID, tenant_id: uuid.UUID,
    form_type: Optional[str] = None, era_label: Optional[str] = None,
    field_schema: Optional[List[Dict[str, Any]]] = None, layout: Optional[str] = None,
) -> Template:
    template = await get_template_by_id(db, template_id)

    if layout is not None:
        if layout not in VALID_LAYOUTS:
            raise HTTPException(status_code=400, detail=f"layout must be one of {VALID_LAYOUTS}")
        template.layout = layout
    if field_schema is not None:
        _validate_field_schema(field_schema)
        template.field_schema = field_schema
    if form_type is not None:
        template.form_type = form_type
    if era_label is not None:
        template.era_label = era_label

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"A template for '{template.form_type} | {template.era_label}' already exists")

    await log_action(
        db, actor_id, tenant_id, "template.update",
        resource_type="template", resource_id=template.id,
        details={"form_type": template.form_type, "era_label": template.era_label, "layout": template.layout},
    )
    await db.commit()
    return template


async def delete_template(db: AsyncSession, template_id: uuid.UUID, actor_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    template = await get_template_by_id(db, template_id)
    form_type, era_label = template.form_type, template.era_label
    await db.delete(template)

    await log_action(
        db, actor_id, tenant_id, "template.delete",
        resource_type="template", resource_id=template_id,
        details={"form_type": form_type, "era_label": era_label},
    )
    await db.commit()


def validate_fields(template: Template, extracted_fields: Dict[str, Any]) -> List[ValidationError]:
    """Check extracted field values against one template's per-field rules.

    Never a single validator for everything — each template's field_schema
    carries its own rules (Section 12: "written per template, never once
    for everything").
    """
    errors: List[ValidationError] = []

    for field_def in template.field_schema:
        name = field_def["name"]
        required = field_def.get("required", False)
        value = extracted_fields.get(name)

        if value is None or value == "":
            if required:
                errors.append(ValidationError(field=name, reason="required field is missing"))
            continue

        field_type = field_def.get("type")
        if field_type == "number":
            if not isinstance(value, (int, float)):
                errors.append(ValidationError(field=name, reason=f"expected a number, got {type(value).__name__}"))
                continue
            min_v = field_def.get("min")
            max_v = field_def.get("max")
            if min_v is not None and value < min_v:
                errors.append(ValidationError(field=name, reason=f"{value} is below minimum {min_v}"))
            if max_v is not None and value > max_v:
                errors.append(ValidationError(field=name, reason=f"{value} is above maximum {max_v}"))

        elif field_type == "string":
            if not isinstance(value, str):
                errors.append(ValidationError(field=name, reason=f"expected a string, got {type(value).__name__}"))
                continue
            pattern = field_def.get("pattern")
            if pattern and not re.match(pattern, value):
                errors.append(ValidationError(field=name, reason=f"'{value}' does not match required pattern {pattern}"))

        allowed_values = field_def.get("allowed_values")
        if allowed_values is not None and value not in allowed_values:
            errors.append(ValidationError(field=name, reason=f"'{value}' is not one of {allowed_values}"))

    return errors
