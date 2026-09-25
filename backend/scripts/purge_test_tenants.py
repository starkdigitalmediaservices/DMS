"""Delete accumulated test-fixture tenants from a DMS database.

Background: the test suite ran against the same database as the app and each
test minted its own throwaway tenant without removing it, so they piled up —
6,434 tenants and ~5,700 orphaned users by 2026-09-21, which made any global
query ("how many documents are indexed?") meaningless. The recurrence is fixed
in tests/conftest.py (it now purges whatever a session created); this script
is for clearing the backlog that accumulated before that fix.

Safety model: this is an allowlist, not a pattern match. Nothing is deleted
unless it is absent from KEEP_TENANT_IDS, and the script refuses to run if any
ID in that list is missing from the database (which would mean it is pointed at
a database it was not written for). Dry-run by default.

    # show what would go, change nothing
    docker compose exec -T backend python scripts/purge_test_tenants.py

    # actually delete
    docker compose exec -T backend python scripts/purge_test_tenants.py --apply
"""
import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text

from app.database import engine

# Tenants that must survive. Everything else is treated as disposable, so add
# to this list before running against an unfamiliar database.
KEEP_TENANT_IDS = [
    # The real, actively used account (biznesskd07@gmail.com).
    "de7bbd90-72a9-4beb-9aec-e54ce58ee7e3",
    # Second real-looking account, d.kunalstarkdigital@gmail.com.
    "76f96812-33a2-41a2-a2f3-c0e66ccfbad4",
    # admin@example.com — the seeded demo login documented in docs/running_script.md.
    "4fdb4ffe-aa12-422c-a6c9-5341f2831b96",
    # Probe tenant created during the 2026-09-21 verification session.
    "8272ea41-43c8-4a0f-b7fb-197ff03ac1dd",
]

# Children before parents, so plain DELETEs don't trip a foreign key.
TENANT_SCOPED_TABLES_CHILD_FIRST = [
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
    "iam_dg_user_folders",
    "iam_dg_departments",
    "sys_dg_corpus_calibration",
    "sys_dg_retention_classes",
    "billing_dg_subscription",
    "audit_dg_api_logs",
    # audit_dg_logs deliberately absent: append-only by DB trigger.
    "iam_dg_users",
    "iam_dg_roles",  # after users: users.role_id points here
]


async def main(apply: bool) -> int:
    async with engine.begin() as conn:
        present = {
            str(r[0])
            for r in (await conn.execute(text("SELECT id FROM iam_dg_tenants"))).fetchall()
        }

        missing = [tid for tid in KEEP_TENANT_IDS if tid not in present]
        if missing:
            print("ABORT: these KEEP_TENANT_IDS are not in this database:")
            for tid in missing:
                print(f"  {tid}")
            print("This script is probably pointed at the wrong database.")
            return 2

        candidates = sorted(present - set(KEEP_TENANT_IDS))
        if not candidates:
            print("Nothing to purge — every tenant present is on the keep list.")
            return 0

        # audit_dg_logs is append-only, enforced by a BEFORE DELETE OR UPDATE
        # trigger with no escape hatch (audit_dg_logs_block_mutation). That is
        # a deliberate tamper-evidence control — the same append-only chain
        # /governance/audit-integrity verifies — so this script does not
        # delete audit rows and does not disable the trigger. The consequence
        # is that any tenant referenced by an audit row is pinned in place by
        # fk_audit_dg_logs_actor_tenant and simply cannot be removed. Those
        # are skipped rather than worked around.
        pinned = {
            str(r[0])
            for r in (
                await conn.execute(
                    text(
                        "SELECT DISTINCT actor_tenant_id FROM audit_dg_logs "
                        "WHERE actor_tenant_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": candidates},
                )
            ).fetchall()
        }
        doomed = [tid for tid in candidates if tid not in pinned]
        if pinned:
            print(
                f"note: {len(pinned)} tenant(s) are pinned by append-only audit rows "
                "and will be left in place."
            )
        if not doomed:
            print("Nothing purgeable — every candidate is pinned by audit history.")
            return 0

        counts = {}
        for table in TENANT_SCOPED_TABLES_CHILD_FIRST:
            column = (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=:t "
                        "AND column_name IN ('tenant_id','actor_tenant_id') LIMIT 1"
                    ),
                    {"t": table},
                )
            ).scalar()
            if not column:
                continue
            counts[table] = (
                await conn.execute(
                    text(
                        f"SELECT count(*) FROM {table} "
                        f"WHERE {column} = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": doomed},
                )
            ).scalar()

        print(f"{'APPLYING' if apply else 'DRY RUN —'} purge of {len(doomed)} tenant(s)")
        print(f"keeping {len(KEEP_TENANT_IDS)}: {', '.join(KEEP_TENANT_IDS)}")
        for table, n in counts.items():
            if n:
                print(f"  {table:34} {n:>7}")
        print(f"  {'iam_dg_tenants':34} {len(doomed):>7}")

        if not apply:
            print("\nNothing changed. Re-run with --apply to delete.")
            return 0

        # documents.current_version_id and document_versions.document_id
        # reference each other; break the cycle before deleting either.
        await conn.execute(
            text(
                "UPDATE doc_dg_documents SET current_version_id = NULL "
                "WHERE tenant_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": doomed},
        )

        # Three tables carry no tenant_id of their own but hang off ones that
        # do, so a tenant-scoped DELETE alone leaves them dangling and the FK
        # rejects it. Clear them via their parent first.
        for sql in (
            "DELETE FROM chat_dg_messages WHERE session_id IN "
            "(SELECT id FROM chat_dg_sessions WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
            "DELETE FROM doc_dg_table_shape_decisions WHERE decided_by_actor_id IN "
            "(SELECT id FROM iam_dg_users WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
            "DELETE FROM billing_dg_license WHERE installed_by IN "
            "(SELECT id FROM iam_dg_users WHERE tenant_id = ANY(CAST(:ids AS uuid[])))",
        ):
            await conn.execute(text(sql), {"ids": doomed})
        for table in TENANT_SCOPED_TABLES_CHILD_FIRST:
            if table not in counts:
                continue
            column = "actor_tenant_id" if table == "audit_dg_logs" else "tenant_id"
            await conn.execute(
                text(f"DELETE FROM {table} WHERE {column} = ANY(CAST(:ids AS uuid[]))"),
                {"ids": doomed},
            )
        await conn.execute(
            text("DELETE FROM iam_dg_tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": doomed},
        )
        print(f"\nDeleted {len(doomed)} tenant(s) and their dependent rows.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
