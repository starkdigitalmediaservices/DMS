"""move remaining legacy 'admin'/'user' rows onto the six personas

0022 migrated every existing row, but UserRole's column default stayed
'user' and the test suite kept creating 'user'/'admin' rows afterwards --
1372 + 7 of them as of 2026-09-23. No require_role gate lists either value
any more, and 'user' falls outside TENANT_WIDE_ROLES, so such an account
is a department-scoped user with no permissions. Same mapping as 0022.
The enum values themselves stay: Postgres can't drop them in place.

Revision ID: 0054_retire_legacy_roles
Revises: 0053_department_scope_rls
Create Date: 2026-09-23 00:00:01.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0054_retire_legacy_roles'
down_revision: Union[str, None] = '0053_department_scope_rls'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE iam_dg_users SET role = 'it_admin' WHERE role = 'admin'")
    op.execute("UPDATE iam_dg_users SET role = 'operator' WHERE role = 'user'")
    op.execute("ALTER TABLE iam_dg_users ALTER COLUMN role SET DEFAULT 'operator'")


def downgrade() -> None:
    # The original values aren't recoverable; only the default is restored.
    op.execute("ALTER TABLE iam_dg_users ALTER COLUMN role DROP DEFAULT")
