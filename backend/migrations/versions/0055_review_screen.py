"""review screen: immutable extraction snapshot, reviewer state, review audit

Three tables behind the human-verification ("review") screen, plus a
content version on facts that the Workbench and the review screen share.

doc_dg_review_originals  One row per (document, version): the block list
    exactly as extraction produced it, including the OCR value of every
    fact-backed table cell at snapshot time. Never updated -- a BEFORE
    UPDATE trigger refuses it. DELETE stays possible so retention purges
    and document deletion still cascade.

doc_dg_review_states  One row per document: the reviewer's working copy.
    Fact-backed cells are stored as a bare {fact_id} reference and read
    live from doc_dg_facts (the fact is the single source of truth for its
    value); only non-fact blocks/cells (reviewer-added text, added rows)
    carry text here. `version` is the optimistic-concurrency counter the
    API compares against If-Match.

doc_dg_review_audit  One append-only row per review action with the
    document/block/row/col coordinates and old/new values. BEFORE UPDATE OR
    DELETE trigger, same guarantee as audit_dg_logs. No FK to the document
    on purpose: history must outlive a purged document, exactly like
    audit_dg_logs.resource_id. entry_hash is echoed into the matching
    audit_dg_logs row so the two trails can be cross-checked.

doc_dg_facts.edit_version  Bumped on every change to a fact's value or
    review status (not on claim/release, which are courtesy locks). Both
    the Workbench and the review screen send the version they loaded; a
    mismatch is a 409, so neither silently overwrites the other.

All three new tables get tenant isolation plus migration 0053's fail-closed
department scope (visible only when the parent document is), except the
audit table, which keeps tenant isolation only -- scope is checked on the
document before any audit row is read.

Revision ID: 0055_review_screen
Revises: 0054_retire_legacy_roles
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0055_review_screen'
down_revision: Union[str, None] = '0054_retire_legacy_roles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
UNSCOPED = "coalesce(current_setting('app.dept_scoped', true), '') = '0'"


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation_policy ON {table} USING ({TENANT}) WITH CHECK ({TENANT})")


def _department_rls(table: str) -> None:
    visible = f"{UNSCOPED} OR EXISTS (SELECT 1 FROM doc_dg_documents d WHERE d.id = {table}.document_id)"
    op.execute(f"""
        CREATE POLICY department_scope_policy ON {table} AS RESTRICTIVE
        USING ({visible})
        WITH CHECK ({visible})
    """)


def upgrade() -> None:
    op.add_column('doc_dg_facts', sa.Column('edit_version', sa.Integer(), nullable=False, server_default='1'))

    op.create_table(
        'doc_dg_review_originals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('doc_dg_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('doc_dg_document_versions.id', ondelete='CASCADE'), nullable=True),
        sa.Column('builder', sa.Text(), nullable=False),
        sa.Column('blocks', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('document_id', 'version_id', name='uq_doc_dg_review_originals_doc_version'),
    )
    op.create_index('idx_doc_dg_review_originals_tenant_id', 'doc_dg_review_originals', ['tenant_id'])

    op.create_table(
        'doc_dg_review_states',
        sa.Column('document_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('doc_dg_documents.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), nullable=False),
        sa.Column('original_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('doc_dg_review_originals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('blocks', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id'), nullable=True),
    )
    op.create_index('idx_doc_dg_review_states_tenant_id', 'doc_dg_review_states', ['tenant_id'])

    op.create_table(
        'doc_dg_review_audit',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_tenants.id'), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('block_id', sa.Text(), nullable=True),
        sa.Column('row_id', sa.Text(), nullable=True),
        sa.Column('row', sa.Integer(), nullable=True),
        sa.Column('col', sa.Integer(), nullable=True),
        sa.Column('fact_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('old_value', postgresql.JSONB(), nullable=True),
        sa.Column('new_value', postgresql.JSONB(), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('iam_dg_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('entry_hash', sa.Text(), nullable=False),
    )
    op.create_index('idx_doc_dg_review_audit_doc', 'doc_dg_review_audit', ['tenant_id', 'document_id', 'created_at'])
    op.create_index('idx_doc_dg_review_audit_block', 'doc_dg_review_audit', ['document_id', 'block_id'])

    op.execute("""
        CREATE OR REPLACE FUNCTION doc_dg_review_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only/immutable: % is not permitted', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER doc_dg_review_originals_immutable
        BEFORE UPDATE ON doc_dg_review_originals
        FOR EACH ROW EXECUTE FUNCTION doc_dg_review_block_mutation()
    """)
    op.execute("""
        CREATE TRIGGER doc_dg_review_audit_append_only
        BEFORE UPDATE OR DELETE ON doc_dg_review_audit
        FOR EACH ROW EXECUTE FUNCTION doc_dg_review_block_mutation()
    """)

    for table in ('doc_dg_review_originals', 'doc_dg_review_states', 'doc_dg_review_audit'):
        _tenant_rls(table)
    for table in ('doc_dg_review_originals', 'doc_dg_review_states'):
        _department_rls(table)


def downgrade() -> None:
    for table in ('doc_dg_review_audit', 'doc_dg_review_states', 'doc_dg_review_originals'):
        op.execute(f"DROP POLICY IF EXISTS department_scope_policy ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")
    op.execute("DROP TRIGGER IF EXISTS doc_dg_review_audit_append_only ON doc_dg_review_audit")
    op.execute("DROP TRIGGER IF EXISTS doc_dg_review_originals_immutable ON doc_dg_review_originals")
    op.execute("DROP FUNCTION IF EXISTS doc_dg_review_block_mutation()")
    op.drop_table('doc_dg_review_audit')
    op.drop_table('doc_dg_review_states')
    op.drop_table('doc_dg_review_originals')
    op.drop_column('doc_dg_facts', 'edit_version')
