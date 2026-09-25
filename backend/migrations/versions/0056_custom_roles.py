"""custom roles: per-tenant iam_dg_roles, users.role_id, backfill

Management ruled 2026-09-25 that the six fixed T50 personas give way to
roles each tenant's Admin defines (docs/features/custom-roles/). This
migration only adds the data; nothing reads role_id until R2/R3 switch
the require_role() gates over, so behaviour is unchanged by it alone.

Backfill keeps every user's access exactly as it is today:
  - every tenant gets one locked Admin role (is_system: holds every
    permission implicitly, sees all departments); it_admin users move to it
  - every other persona in use in a tenant becomes a role with the same
    name, the permissions its require_role() gates grant today, and the
    same department scope (department_service.TENANT_WIDE_ROLES)

The persona table below is a frozen snapshot on purpose -- migrations must
not import app code that will keep changing. The live copy used for
"start from template" is app/permissions.py (R2).

The old user_role enum column stays untouched; R13 retires it.

Revision ID: 0056_custom_roles
Revises: 0055_review_screen
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0056_custom_roles'
down_revision: Union[str, None] = '0055_review_screen'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REVIEWER = [
    "facts.review", "documents.classify", "records.create", "entities.edit",
    "corpus.calibrate", "review.read", "review.edit",
]

# persona -> (role name, all_departments, permissions). Derived from the
# require_role() gates at f3a7ba1; see TASKS.md R3 for the gate table.
_PERSONAS = {
    "records_officer": ("Records Officer", False, _REVIEWER + [
        "review.verify", "content.deletePermanent", "export.report", "certificate.section63",
    ]),
    "operator": ("Operator", False, _REVIEWER + ["export.report"]),
    "department_head": ("Department Head", False, [
        "review.read", "content.deletePermanent", "certificate.section63", "billing.view",
    ]),
    "legal_counsel": ("Legal Counsel", True, [
        "review.read", "export.report", "certificate.section63",
    ]),
    "auditor": ("Auditor", True, [
        "review.read", "export.report", "certificate.section63", "audit.integrity", "billing.view",
    ]),
}


def upgrade() -> None:
    op.create_table(
        'iam_dg_roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('all_departments', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('permissions', postgresql.ARRAY(sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_by_actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_iam_dg_roles_tenant_id', 'iam_dg_roles', ['tenant_id'])
    op.execute("CREATE UNIQUE INDEX uq_iam_dg_roles_tenant_name ON iam_dg_roles (tenant_id, lower(name))")
    # At most one locked Admin role per tenant.
    op.execute("CREATE UNIQUE INDEX uq_iam_dg_roles_tenant_system ON iam_dg_roles (tenant_id) WHERE is_system")

    op.execute("ALTER TABLE iam_dg_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iam_dg_roles FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON iam_dg_roles
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
    """)

    op.add_column('iam_dg_users', sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_iam_dg_users_role', 'iam_dg_users', 'iam_dg_roles', ['role_id'], ['id'])
    op.create_index('idx_iam_dg_users_role_id', 'iam_dg_users', ['role_id'])

    # Backfill. Runs as the migration superuser, so RLS does not filter it.
    op.execute("""
        INSERT INTO iam_dg_roles (id, tenant_id, name, is_system, all_departments, permissions)
        SELECT gen_random_uuid(), t.id, 'Admin', true, true, '{}'
        FROM iam_dg_tenants t
    """)
    op.execute("""
        UPDATE iam_dg_users u SET role_id = r.id
        FROM iam_dg_roles r
        WHERE r.tenant_id = u.tenant_id AND r.is_system
          AND u.role IN ('it_admin', 'admin')
    """)
    # 'user' was retired onto operator by 0054; mapped again defensively.
    legacy = {"operator": ("operator", "user")}
    for persona, (name, all_depts, perms) in _PERSONAS.items():
        values = legacy.get(persona, (persona,))
        in_list = ", ".join(f"'{v}'" for v in values)
        perms_sql = "ARRAY[" + ", ".join(f"'{p}'" for p in perms) + "]::text[]"
        op.execute(f"""
            INSERT INTO iam_dg_roles (id, tenant_id, name, is_system, all_departments, permissions)
            SELECT gen_random_uuid(), t.tenant_id, '{name}', false, {str(all_depts).lower()}, {perms_sql}
            FROM (SELECT DISTINCT tenant_id FROM iam_dg_users WHERE role IN ({in_list})) t
        """)
        op.execute(f"""
            UPDATE iam_dg_users u SET role_id = r.id
            FROM iam_dg_roles r
            WHERE r.tenant_id = u.tenant_id AND r.name = '{name}' AND NOT r.is_system
              AND u.role IN ({in_list})
        """)


def downgrade() -> None:
    op.drop_index('idx_iam_dg_users_role_id', table_name='iam_dg_users')
    op.drop_constraint('fk_iam_dg_users_role', 'iam_dg_users', type_='foreignkey')
    op.drop_column('iam_dg_users', 'role_id')
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON iam_dg_roles")
    op.drop_table('iam_dg_roles')
