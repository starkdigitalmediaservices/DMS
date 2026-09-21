"""Seed the missing drive.nav.scan_document translation.

Found live 2026-09-21 while exercising the UI in Marathi: every item in
the Drive "+ New" menu localised correctly except "Scan Document", which
stayed in English because the component hardcoded the string instead of
calling t(). The component now calls t("drive.nav.scan_document", ...);
this migration supplies the key so the Marathi locale actually resolves
rather than falling back to the English default.

Revision ID: 0052_scan_doc_translation
Revises: 0051_relevance_fallback_ratio
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Kept under 32 chars — alembic_version.version_num is varchar(32); see
# 0051's note about a longer id failing the version bump.
revision: str = '0052_scan_doc_translation'
down_revision: Union[str, None] = '0051_relevance_fallback_ratio'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TRANSLATIONS = [
    ('drive.nav.scan_document', 'en', 'Scan Document'),
    ('drive.nav.scan_document', 'mr', 'दस्तऐवज स्कॅन करा'),
]


def upgrade() -> None:
    table = sa.table(
        'sys_dg_translations',
        sa.column('key', sa.String()),
        sa.column('locale', sa.String()),
        sa.column('value', sa.String()),
    )
    for key, locale, value in TRANSLATIONS:
        # Idempotent: this key was inserted by hand on the dev database
        # before the migration existed, so a plain INSERT would collide.
        op.execute(
            table.insert().from_select(
                ['key', 'locale', 'value'],
                sa.select(
                    sa.literal(key), sa.literal(locale), sa.literal(value)
                ).where(
                    ~sa.exists(
                        sa.select(sa.literal(1))
                        .select_from(table)
                        .where(table.c.key == key, table.c.locale == locale)
                    )
                ),
            )
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM sys_dg_translations WHERE key = 'drive.nav.scan_document'"
    )
