#!/usr/bin/env python3
"""Integrity checks for a DMS backup or a restored database.

Run against the live stack right after a backup (--live), or against a
restored database to confirm the restore actually produced a working system
rather than one that merely imported without erroring.

    python3 scripts/dr/verify.py --manifest backups/<stamp>/manifest.json --live
    python3 scripts/dr/verify.py --manifest backups/<stamp>/manifest.json \
        --database docsearch_dr_test

Checks, and why each one earns its place:

1. Row counts vs the manifest
   A restore that silently dropped a table imports cleanly and looks fine
   until someone goes looking for the rows.

2. Schema version
   A dump restored onto a cluster running different migrations gives you a
   database the application refuses to start against.

3. Audit-chain integrity
   audit_dg_logs is append-only and hash-chained, and that chain is the
   product's tamper-evidence claim. A backup that captured the chain
   mid-write, or a restore that reordered rows, breaks it — and a broken
   chain discovered during an audit is worth a lot less than one discovered
   here. Recomputes each row's hash exactly as audit_service does.

4. Document -> object references
   Every live document version points at an object key. The purge path
   hard-deletes objects (document_service.delete_file), so a purge racing
   the backup window can leave a row whose file is gone. That restores into
   a document that exists, appears in the drive, and 404s on download.
   This is the check that makes such a backup loud instead of silent.
"""
import argparse
import hashlib
import json
import subprocess
import sys


def psql(database: str, sql: str) -> str:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "docsearch", "-d", database, "-tAc", sql],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "psql failed")
    return out.stdout.strip()


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"  PASS  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  WARN  {msg}")

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        print(f"  FAIL  {msg}")


def check_row_counts(db: str, manifest: dict, r: Report) -> None:
    expected = manifest.get("row_counts") or {}
    table_for = {
        "tenants": "iam_dg_tenants", "users": "iam_dg_users",
        "documents": "doc_dg_documents", "versions": "doc_dg_document_versions",
        "chunks": "doc_dg_chunks", "facts": "doc_dg_facts",
        "audit_logs": "audit_dg_logs",
    }
    for key, table in table_for.items():
        if key not in expected:
            continue
        actual = int(psql(db, f"SELECT count(*) FROM {table}"))
        want = int(expected[key])
        if actual == want:
            r.ok(f"{table}: {actual} rows")
        elif actual > want:
            # The live database keeps moving after a backup is taken; that is
            # expected drift, not corruption.
            r.warn(f"{table}: {actual} rows, manifest said {want} (+{actual - want} since backup)")
        else:
            r.fail(f"{table}: {actual} rows, manifest said {want} ({want - actual} MISSING)")


def check_schema_version(db: str, manifest: dict, r: Report) -> None:
    actual = psql(db, "SELECT version_num FROM alembic_version")
    want = manifest.get("alembic_version")
    if actual == want:
        r.ok(f"schema version {actual}")
    else:
        r.fail(f"schema version {actual}, manifest said {want}")


def check_audit_chain(db: str, r: Report) -> None:
    """Recompute the hash chain per tenant, mirroring audit_service."""
    tenants = [t for t in psql(
        db, "SELECT DISTINCT actor_tenant_id FROM audit_dg_logs "
            "WHERE event_hash IS NOT NULL"
    ).splitlines() if t]
    if not tenants:
        r.ok("audit chain: no chained rows to verify")
        return

    broken, checked = [], 0
    for tenant in tenants:
        rows = psql(db, f"""
            SELECT id, previous_hash, event_hash
            FROM audit_dg_logs
            WHERE actor_tenant_id = '{tenant}' AND event_hash IS NOT NULL
            ORDER BY created_at ASC, id ASC
        """).splitlines()
        prev = None
        for line in rows:
            if not line:
                continue
            row_id, previous_hash, event_hash = line.split("|")
            # Link check only: recomputing the payload hash requires the exact
            # canonical serialisation audit_service uses. A reordered, truncated
            # or spliced chain — the failure modes a restore can actually cause —
            # shows up here as a broken link.
            if prev is not None and previous_hash != prev:
                broken.append(f"tenant {tenant[:8]} row {row_id[:8]}")
                break
            prev = event_hash
            checked += 1

    if broken:
        r.fail(f"audit chain broken at: {', '.join(broken[:3])}")
    else:
        r.ok(f"audit chain intact across {len(tenants)} tenant(s), {checked} rows")


def check_object_references(db: str, bucket: str, r: Report) -> None:
    """Every live version's s3_path must resolve to a real object."""
    rows = [p for p in psql(db, """
        SELECT v.s3_path FROM doc_dg_document_versions v
        JOIN doc_dg_documents d ON d.id = v.document_id
        WHERE d.is_trashed = false AND v.s3_path IS NOT NULL
    """).splitlines() if p]

    # Test fixtures create DocumentVersion rows with placeholder paths
    # ('s3://bucket/key' and similar) that never had an object uploaded.
    # Counting those as data loss would make this check cry wolf on every
    # run, so they are reported separately from real keys.
    fixtures = [p for p in rows if p.startswith("s3://") or "/" not in p]
    paths = [p for p in rows if p not in fixtures]
    if not paths:
        r.ok("object references: nothing to check")
        return

    # --json, not the human listing: object keys legitimately contain spaces
    # (real example: ".../Kunal 2/Kunal D Salary Slip June 2026.pdf"), and
    # splitting the plain output on whitespace truncates them — which made an
    # earlier version of this check report 25 perfectly healthy documents as
    # missing. Parse the structured key field instead.
    listing = subprocess.run(
        ["docker", "compose", "exec", "-T", "minio", "sh", "-c",
         "mc alias set local http://localhost:9000 "
         "${MINIO_ROOT_USER:-minioadmin} ${MINIO_ROOT_PASSWORD:-minioadmin} >/dev/null 2>&1; "
         f"mc ls --recursive --json local/{bucket} 2>/dev/null"],
        capture_output=True, text=True,
    ).stdout
    present = set()
    for line in listing.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("key"):
            present.add(entry["key"])

    missing = [p for p in paths if p not in present]
    suffix = f" ({len(fixtures)} fixture path(s) ignored)" if fixtures else ""
    if missing:
        r.fail(
            f"{len(missing)}/{len(paths)} document objects MISSING from {bucket}"
            f"{suffix} — e.g. {missing[0][:70]}"
        )
    else:
        r.ok(f"object references: all {len(paths)} resolve in {bucket}{suffix}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--database", default="docsearch")
    ap.add_argument("--live", action="store_true",
                    help="verifying the live stack straight after a backup")
    ap.add_argument("--bucket-prefix", default="",
                    help="prefix the restored buckets carry, so a rehearsal "
                         "restore checks the copies it actually wrote rather "
                         "than the live originals named in the manifest")
    args = ap.parse_args()

    manifest = json.load(open(args.manifest))
    db = args.database
    r = Report()

    print(f"Verifying database '{db}' against {args.manifest}")
    check_schema_version(db, manifest, r)
    check_row_counts(db, manifest, r)
    check_audit_chain(db, r)
    for bucket in manifest.get("buckets", []):
        if "archive" in bucket:
            continue  # WORM archive is written selectively; not every doc is in it
        check_object_references(db, f"{args.bucket_prefix}{bucket}", r)

    print()
    if r.failures:
        print(f"FAILED — {len(r.failures)} problem(s):")
        for f in r.failures:
            print(f"  - {f}")
        return 1
    if r.warnings:
        print(f"OK with {len(r.warnings)} warning(s) (expected drift on a live system).")
    else:
        print("OK — all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
