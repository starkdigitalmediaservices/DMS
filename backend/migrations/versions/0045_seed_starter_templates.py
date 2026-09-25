"""T25 — seed the two starter templates as a migration, not a live-only DB row.

Both templates already existed in this dev DB, but only because they were
created by hand during live testing while building the T31/T32 regression
corpus (see docs/testing/T31_T32_regression_corpus_notes.md) — a fresh environment
(a new dev DB, CI, another deployment) would have neither, and the two real
documents in the regression corpus would fail to classify at all. This is
still not T25's real goal (a template LIBRARY seeded from an official,
human-verified form catalogue — that's still blocked on A1, no such
catalogue exists), but it closes the gap between "works on this one dev
DB" and "works anywhere this migration runs."

ON CONFLICT DO NOTHING on the (form_type, era_label) unique constraint:
safe to run against a DB where these rows already exist (this one) without
erroring or duplicating.

Revision ID: 0045_seed_starter_templates
Revises: 0044_backfill_retention
Create Date: 2026-09-01 00:00:00.000000

"""
import json
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0045_seed_starter_templates'
down_revision: Union[str, None] = '0044_backfill_retention'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WAQF_REGISTRATION_SCHEMA = [
    {"name": "sr_no", "role": "serial", "type": "string", "required": False},
    {"name": "property_description", "role": "continuation_text", "type": "string", "required": True},
    {"name": "estimated_value", "type": "string", "required": False},
]

GAZETTE_REGISTER_SCHEMA = [
    {"half": "left", "name": "sr_no", "role": "serial", "type": "string", "required": True},
    {"half": "left", "name": "wakf_name", "type": "string", "required": True},
    {"half": "left", "name": "sect", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "left", "name": "object", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "left", "name": "wakf_name_col5", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "left", "name": "creation_date", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "left", "name": "deed_details", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "left", "name": "mutawalli_name", "type": "string", "required": False, "ditto_eligible": True},
    {"half": "right", "name": "village", "type": "string", "required": False},
    {"half": "right", "name": "survey_no", "type": "string", "required": False},
    {"half": "right", "name": "area", "type": "string", "required": False},
    {"half": "right", "name": "assessment", "type": "string", "required": False},
    {"half": "right", "name": "village_situated", "type": "string", "required": False},
    {"half": "right", "name": "site", "type": "string", "required": False},
    {"half": "right", "name": "boundaries", "type": "string", "required": False},
    {"half": "right", "name": "property_details", "type": "string", "required": False},
    {"half": "right", "name": "cash_grant", "type": "string", "required": False},
    {"half": "right", "name": "estimated_income", "type": "string", "required": False},
    {"half": "right", "name": "remarks", "type": "string", "required": False},
]

TEMPLATES = [
    {
        "id": "49a7f329-89e4-402c-ab62-c84eaa158ce9",
        "form_type": "Waqf Institution Registration File",
        "era_label": "BPT Act 1950 / Waqf Act 1995",
        "layout": "single_page",
        "field_schema": WAQF_REGISTRATION_SCHEMA,
    },
    {
        "id": "df0aaa26-b58d-4eb9-a6ad-6490443f6be4",
        "form_type": "Maharashtra State Wakf Gazette Register",
        "era_label": "Government Gazette, District Aurangabad, 1973",
        "layout": "spread",
        "field_schema": GAZETTE_REGISTER_SCHEMA,
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for t in TEMPLATES:
        conn.execute(
            sa.text(
                "INSERT INTO doc_dg_templates (id, form_type, era_label, layout, field_schema) "
                "VALUES (:id, :form_type, :era_label, :layout, CAST(:field_schema AS jsonb)) "
                "ON CONFLICT (form_type, era_label) DO NOTHING"
            ),
            {
                "id": t["id"],
                "form_type": t["form_type"],
                "era_label": t["era_label"],
                "layout": t["layout"],
                "field_schema": json.dumps(t["field_schema"]),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for t in TEMPLATES:
        conn.execute(
            sa.text("DELETE FROM doc_dg_templates WHERE id = :id"),
            {"id": t["id"]},
        )
