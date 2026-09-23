import pytest_asyncio
from sqlalchemy import text

from app.database import engine, app_engine
from app.services.cache_service import close_redis


@pytest_asyncio.fixture(autouse=True)
async def cleanup_connections_after_test():
    yield
    await engine.dispose()
    # D-2 fix, 2026-09-07 — app_engine (the restricted-role connection
    # get_db/get_tenant_db actually use) is a second engine alongside the
    # one above; without disposing it too, a test that drives a real ASGI
    # request (test_email_webhook.py's is the only one) can leave a pooled
    # connection bound to that test's event loop, which the next test's
    # own loop then can't reuse ("attached to a different loop").
    await app_engine.dispose()
    await close_redis()


# Every tenant-scoped table, ordered children-before-parents so plain DELETEs
# don't trip a foreign key. iam_dg_users is last before the tenant row itself
# because audit rows reference the actor.
_TENANT_SCOPED_TABLES_CHILD_FIRST = [
    "doc_dg_fact_regions",
    "doc_dg_metadata_item_regions",
    "doc_dg_facts",
    "doc_dg_metadata_items",
    "doc_dg_chunks",
    "doc_dg_pages",
    "record_dg_amendments",
    "record_dg_records",
    "entity_dg_edges",
    "entity_dg_nodes",
    "chat_dg_sessions",
    "doc_dg_document_versions",
    "doc_dg_documents",
    "doc_dg_templates",
    "doc_dg_folders",
    "iam_dg_department_folders",
    "iam_dg_department_members",
    "iam_dg_departments",
    "sys_dg_corpus_calibration",
    "sys_dg_retention_classes",
    "billing_dg_subscription",
    "audit_dg_api_logs",
    # audit_dg_logs is deliberately absent: a BEFORE DELETE OR UPDATE trigger
    # (audit_dg_logs_block_mutation) makes it append-only, which is the same
    # tamper-evidence guarantee /governance/audit-integrity verifies. Any
    # tenant an audit row references is therefore pinned in place by
    # fk_audit_dg_logs_actor_tenant and cannot be purged — see the skip in
    # the fixture below rather than trying to delete around the trigger.
    "iam_dg_users",
]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def purge_tenants_created_by_this_session():
    """Delete every tenant this test session created, once the session ends.

    The suite runs against the same database the app does, and each test
    mints its own throwaway tenant (`Tenant(id=uuid4(), ...)`) without ever
    removing it. Nothing cleaned those up, so they accumulated on every run:
    6,409 tenants and ~3,100 orphaned users were sitting in the dev database
    by 2026-09-21, which made any global query ("how many documents are
    indexed?") meaningless until you filtered to a known-real tenant.

    Rather than rewire the suite onto a separate database (the engines are
    bound at import time, and every test builds its own fixtures anyway, so
    the isolation buys little for the churn it would cost), this snapshots
    the tenant IDs that existed BEFORE the session and removes only what
    appeared during it. A tenant that predates the run is never touched, so
    real data is safe by construction.

    Caveat worth knowing: if someone creates a tenant through the running
    app *while* the suite is executing, it looks new to this fixture and
    gets purged with the rest. Don't run the suite against a database
    someone is actively using — which was already true, since the tests
    write to it either way.
    """
    async with engine.connect() as conn:
        pre_existing = {
            row[0] for row in (await conn.execute(text("SELECT id FROM iam_dg_tenants"))).fetchall()
        }
        pre_existing_global_templates = {
            row[0] for row in (await conn.execute(
                text("SELECT id FROM doc_dg_templates WHERE tenant_id IS NULL")
            )).fetchall()
        }
    # Dispose immediately: this fixture is session-scoped, so anything left
    # in the pool here stays bound to the session's event loop, and the
    # per-test dispose above then hands the next test a connection created
    # on a loop that is already closed ("attached to a different loop").
    # Taking the snapshot and letting go of the connection avoids owning
    # any pooled state across the tests themselves.
    await engine.dispose()

    yield

    # Global templates (tenant_id NULL) belong to no tenant, so the tenant
    # purge below never reaches them -- and most tests create theirs that
    # way. 318 "... Test Form <hex>" rows had piled up in the dev database
    # by 2026-09-23, burying the 5 real templates on the admin screen.
    # Remove only the ones that appeared during this session.
    async with engine.begin() as conn:
        new_templates = [
            str(row[0]) for row in (await conn.execute(
                text("SELECT id FROM doc_dg_templates WHERE tenant_id IS NULL")
            )).fetchall()
            if row[0] not in pre_existing_global_templates
        ]
        if new_templates:
            await conn.execute(
                text("UPDATE doc_dg_documents SET matched_template_id = NULL "
                     "WHERE matched_template_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": new_templates},
            )
            await conn.execute(
                text("DELETE FROM doc_dg_templates WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": new_templates},
            )
            print(f"\n[conftest] purged {len(new_templates)} global template(s) created by this session")

    async with engine.begin() as conn:
        current = {
            row[0] for row in (await conn.execute(text("SELECT id FROM iam_dg_tenants"))).fetchall()
        }
        candidates = [str(tid) for tid in (current - pre_existing)]
        if not candidates:
            return

        # Tenants referenced by an append-only audit row cannot be deleted
        # (see the table list above). Skip them instead of failing teardown
        # — a test that wrote audit history leaves its tenant behind, which
        # is a far smaller problem than every suite run erroring out.
        pinned = {
            str(row[0])
            for row in (
                await conn.execute(
                    text(
                        "SELECT DISTINCT actor_tenant_id FROM audit_dg_logs "
                        "WHERE actor_tenant_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": candidates},
                )
            ).fetchall()
        }
        created = [tid for tid in candidates if tid not in pinned]
        if not created:
            return

        # doc_dg_documents.current_version_id and
        # doc_dg_document_versions.document_id reference each other, so
        # neither table can be deleted first while both sides are populated.
        # Break the cycle by dropping the documents' pointer into versions
        # before the ordered deletes below touch either table.
        await conn.execute(
            text(
                "UPDATE doc_dg_documents SET current_version_id = NULL "
                "WHERE tenant_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": created},
        )

        # chat_dg_messages, doc_dg_table_shape_decisions and
        # billing_dg_license carry no tenant_id of their own but reference
        # tables that do, so the tenant-scoped deletes below can't clear
        # them and the FK rejects the parent delete. Remove them via their
        # parent first.
        for sql in (
            "DELETE FROM chat_dg_messages WHERE session_id IN "
            "(SELECT id FROM chat_dg_sessions WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
            "DELETE FROM doc_dg_table_shape_decisions WHERE decided_by_actor_id IN "
            "(SELECT id FROM iam_dg_users WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
            "DELETE FROM billing_dg_license WHERE installed_by IN "
            "(SELECT id FROM iam_dg_users WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
        ):
            await conn.execute(text(sql), {"ids": created})

        # Resolve each table's tenant column from the schema rather than
        # assuming a naming convention: most use tenant_id, audit_dg_logs
        # uses actor_tenant_id, and audit_dg_api_logs (same prefix) does
        # not. Reading it back means a future column rename surfaces as a
        # missing table here, not a silently skipped delete.
        tenant_columns = {
            row[0]: row[1]
            for row in (
                await conn.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public' "
                        "AND column_name IN ('tenant_id','actor_tenant_id')"
                    )
                )
            ).fetchall()
        }

        for table in _TENANT_SCOPED_TABLES_CHILD_FIRST:
            column = tenant_columns.get(table)
            if column is None:
                continue
            await conn.execute(
                text(f"DELETE FROM {table} WHERE {column} = ANY(CAST(:ids AS uuid[]))"),
                {"ids": created},
            )
        await conn.execute(
            text("DELETE FROM iam_dg_tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": created},
        )

    print(f"\n[conftest] purged {len(created)} tenant(s); {len(pinned)} kept (pinned by append-only audit rows)")
