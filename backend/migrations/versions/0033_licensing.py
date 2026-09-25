"""T81 — licensing enforcement: subscription metering (SaaS), signed capacity
license (on-prem/air-gapped)

Backlog: "Licensing enforcement — subscription metering for SaaS, node and
GPU capacity licence on-prem." Blocked on A5 (licensing model decision) —
no tier names, limits, or enforcement mechanism were ever specified by the
business. Per the user's explicit instruction (2026-08-26), this migration
and the service built on top of it implement a REASONABLE DEFAULT model,
not the real one — every number here is a placeholder pending real
business sign-off. See docs/decisions/T81_licensing_assumptions.md for
the full list of assumptions and what to change once the real model is
decided.

Plan definitions themselves (limits per tier) live in code
(app/services/license_service.py: PLAN_DEFINITIONS) rather than a DB
table, since they're a vendor-controlled price sheet, not tenant-editable
data — same reasoning as T02's config table NOT covering things that
aren't genuinely runtime-tunable. billing_dg_subscription only tracks
which plan a tenant is assigned to and its trial/period state. Usage
(document count, storage bytes) is computed live from the existing
Document/DocumentVersion tables at check time rather than duplicated into
a counter column, to avoid a whole class of increment/decrement drift
bugs on delete/trash/restore.

billing_dg_license is deployment-wide (on-prem is a single install, not
multi-tenant SaaS), holding the currently-installed signed capacity
license so status can be shown without re-reading the license file off
disk on every request.

Revision ID: 0033_licensing
Revises: 0032_i18n_translations
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = '0033_licensing'
down_revision: Union[str, None] = '0032_i18n_translations'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'billing_dg_subscription',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), primary_key=True),
        sa.Column('plan_key', sa.String(), nullable=False, server_default='trial'),
        sa.Column('status', sa.String(), nullable=False, server_default='trialing'),
        sa.Column('trial_ends_at', sa.DateTime(), nullable=True),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_check_constraint(
        'ck_billing_dg_subscription_status',
        'billing_dg_subscription',
        "status IN ('trialing', 'active', 'expired', 'canceled')",
    )

    op.create_table(
        'billing_dg_license',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('raw_payload', JSONB, nullable=False),
        sa.Column('signature_b64', sa.Text(), nullable=False),
        sa.Column('is_valid', sa.Boolean(), nullable=False),
        sa.Column('invalid_reason', sa.Text(), nullable=True),
        sa.Column('installed_by', UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id'), nullable=True),
        sa.Column('installed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Backfill: every existing tenant predates this feature — give each a
    # 30-day trial starting now rather than retroactively (would falsely
    # flag long-lived dev/test tenants as immediately over trial).
    op.execute("""
        INSERT INTO billing_dg_subscription (tenant_id, plan_key, status, trial_ends_at, created_at, updated_at)
        SELECT id, 'trial', 'trialing', NOW() + INTERVAL '30 days', NOW(), NOW()
        FROM iam_dg_tenants
    """)


def downgrade() -> None:
    op.drop_table('billing_dg_license')
    op.drop_table('billing_dg_subscription')
