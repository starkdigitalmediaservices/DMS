"""department scoping enforced by Row-Level Security

T50 promised that department-scoped personas (records_officer, operator,
department_head) only see the projects (folders) granted to their
department. Until now that was a single Python filter on GET /folders:
the document list, document detail and every sub-route, the folder tree,
folder detail, search, chat and export all ignored it -- verified live
2026-09-23, an operator could open any document in the tenant by id.

Instead of adding the same filter to dozens of queries (and missing the
next one someone writes), scope is enforced in Postgres as RESTRICTIVE
policies, AND-ed with the existing tenant_isolation_policy:

  app.dept_scoped      '0' for tenant-wide access, '1' for department-scoped
  app.scope_folder_ids comma-separated folder ids the caller may see:
                       every granted folder plus all its descendants
  app.current_user_id  the caller, for their own root-level uploads

All three are set per request by deps.get_tenant_db (see
department_service.apply_request_scope). The policies FAIL CLOSED: only an
explicit app.dept_scoped = '0' grants tenant-wide visibility, so a
dms_app session that sets a tenant but forgets scope sees no documents
rather than all of them. (The first draft passed everything when the
setting was absent, and /search/stream -- which opens its own session --
leaked the whole tenant to scoped users through exactly that gap.) The
worker and migration connections run as the superuser and bypass RLS.

Visibility for a scoped caller:
  folders    id in scope, or parent in scope (so a subfolder created
             inside a granted folder is visible in the same request)
  documents  folder in scope, or a root-level document they uploaded
  chunks, pages, facts, metadata_items, document_versions
             only when the parent document is visible

Revision ID: 0053_department_scope_rls
Revises: 0052_scan_doc_translation
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0053_department_scope_rls'
down_revision: Union[str, None] = '0052_scan_doc_translation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOCUMENT_CHILD_TABLES = [
    'doc_dg_chunks',
    'doc_dg_pages',
    'doc_dg_facts',
    'doc_dg_metadata_items',
    'doc_dg_document_versions',
]

UNSCOPED = "coalesce(current_setting('app.dept_scoped', true), '') = '0'"
SCOPE_IDS = "string_to_array(coalesce(current_setting('app.scope_folder_ids', true), ''), ',')::uuid[]"
CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(f"""
        CREATE POLICY department_scope_policy ON doc_dg_folders AS RESTRICTIVE
        USING ({UNSCOPED} OR id = ANY({SCOPE_IDS}) OR parent_id = ANY({SCOPE_IDS}))
        WITH CHECK ({UNSCOPED} OR id = ANY({SCOPE_IDS}) OR parent_id = ANY({SCOPE_IDS}))
    """)

    doc_visible = (
        f"{UNSCOPED} OR folder_id = ANY({SCOPE_IDS}) "
        f"OR (folder_id IS NULL AND created_by = {CURRENT_USER})"
    )
    op.execute(f"""
        CREATE POLICY department_scope_policy ON doc_dg_documents AS RESTRICTIVE
        USING ({doc_visible})
        WITH CHECK ({doc_visible})
    """)

    # The EXISTS subquery is itself subject to doc_dg_documents' policies,
    # so a child row is visible exactly when its document is.
    for table in DOCUMENT_CHILD_TABLES:
        child_visible = f"{UNSCOPED} OR EXISTS (SELECT 1 FROM doc_dg_documents d WHERE d.id = {table}.document_id)"
        op.execute(f"""
            CREATE POLICY department_scope_policy ON {table} AS RESTRICTIVE
            USING ({child_visible})
            WITH CHECK ({child_visible})
        """)


def downgrade() -> None:
    for table in ['doc_dg_folders', 'doc_dg_documents', *DOCUMENT_CHILD_TABLES]:
        op.execute(f"DROP POLICY IF EXISTS department_scope_policy ON {table}")
