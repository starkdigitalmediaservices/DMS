"""folder granted directly to one user (custom roles R14, decision D3)

Until now folder scope came only through departments
(iam_dg_department_folders + members). This adds a per-user grant for the
case "share this one folder with this one person" without inventing a
one-person department. department_service.list_user_scope_folder_ids
unions it in; the migration 0053 RLS policies are unchanged because they
only read app.scope_folder_ids, which that function computes.

Revision ID: 0057_user_folder_grants
Revises: 0056_custom_roles
Create Date: 2026-09-25 00:00:01.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0057_user_folder_grants'
down_revision: Union[str, None] = '0056_custom_roles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'iam_dg_user_folders',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('folder_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('doc_dg_folders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by_actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'folder_id', name='uq_iam_dg_user_folders'),
    )
    op.create_index('idx_iam_dg_user_folders_tenant_id', 'iam_dg_user_folders', ['tenant_id'])
    op.create_index('idx_iam_dg_user_folders_user_id', 'iam_dg_user_folders', ['user_id'])
    op.create_index('idx_iam_dg_user_folders_folder_id', 'iam_dg_user_folders', ['folder_id'])

    op.execute("ALTER TABLE iam_dg_user_folders ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iam_dg_user_folders FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON iam_dg_user_folders
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON iam_dg_user_folders")
    op.drop_table('iam_dg_user_folders')
