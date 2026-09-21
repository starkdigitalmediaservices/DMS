#!/usr/bin/env bash
#
# Full DMS backup: Postgres (schema + data + cluster roles) and the MinIO
# object store, captured together with a manifest describing exactly what was
# taken and verified afterwards.
#
#   ./scripts/dr/backup.sh [destination-dir]
#
# Default destination is ./backups/<UTC timestamp>.
#
# ORDERING IS LOAD-BEARING — Postgres is dumped BEFORE the object store.
#
#   An upload writes the file to S3 and only then commits the database row
#   (document_service.upload_document: upload_file() runs well before
#   db.commit()). So "S3 write happens-before DB commit" is an invariant, and
#   it decides which order is safe:
#
#     Postgres first (T1), objects second (T2 > T1):
#       every row in the dump was committed by T1, so its object was written
#       before T1, so it is certainly inside a mirror taken at T2. Anything
#       uploaded between T1 and T2 becomes an orphaned object, which restores
#       harmlessly and is invisible in the UI.
#
#     Objects first, Postgres second:
#       a file uploaded between the two snapshots lands its object OUTSIDE the
#       mirror but its row INSIDE the dump — a document that exists, appears in
#       the drive, and 404s on download. Silent data loss.
#
#   The cost of the safe order is orphaned blobs. That is the right trade.
#
set -euo pipefail

cd "$(dirname "$0")/../.."

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DEST="${1:-backups/$STAMP}"
BUCKETS=(docsearch-documents docsearch-archive)

mkdir -p "$DEST/objects"
echo "DMS backup -> $DEST"

# --- 1. Cluster roles -------------------------------------------------------
# pg_dump of a single database does NOT include roles; they live at cluster
# level. This install has a restricted, non-superuser role (dms_app) that the
# API connects as, and Row-Level Security is only a real boundary because that
# role cannot bypass it. Restore the database without this file and either the
# app cannot connect at all, or somebody "fixes" it by pointing the app at the
# superuser and tenant isolation is silently gone.
echo "  [1/4] cluster roles"
docker compose exec -T postgres pg_dumpall -U docsearch --roles-only \
  > "$DEST/roles.sql"

# --- 2. Database ------------------------------------------------------------
# Custom format (-Fc): compressed, and restorable selectively with pg_restore.
echo "  [2/4] database"
docker compose exec -T postgres pg_dump -U docsearch -d docsearch -Fc \
  > "$DEST/database.dump"

# --- 3. Object store --------------------------------------------------------
# Taken AFTER the database, per the ordering note above.
# The MinIO image ships mc but no tar, so the bucket is mirrored to a scratch
# directory inside the container and lifted out with `docker compose cp`
# rather than streamed through an archive.
echo "  [3/4] object store"
for b in "${BUCKETS[@]}"; do
  echo -n "        $b "
  docker compose exec -T minio sh -c "
    rm -rf /tmp/dr-$b
    mc alias set local http://localhost:9000 \
      \${MINIO_ROOT_USER:-minioadmin} \${MINIO_ROOT_PASSWORD:-minioadmin} >/dev/null 2>&1
    mc mirror --overwrite --quiet local/$b /tmp/dr-$b >/dev/null 2>&1 || true
    mkdir -p /tmp/dr-$b
  "
  docker compose cp "minio:/tmp/dr-$b" "$DEST/objects/$b" >/dev/null 2>&1
  docker compose exec -T minio sh -c "rm -rf /tmp/dr-$b" >/dev/null 2>&1 || true
  echo "($(find "$DEST/objects/$b" -type f 2>/dev/null | wc -l) objects)"
done

# --- 4. Manifest ------------------------------------------------------------
# Records what this backup should contain, so a restore can be checked against
# intent rather than just "it didn't error".
echo "  [4/4] manifest"
ALEMBIC=$(docker compose exec -T postgres psql -U docsearch -d docsearch -tAc \
  "SELECT version_num FROM alembic_version" | tr -d '\r')
COUNTS=$(docker compose exec -T postgres psql -U docsearch -d docsearch -tAc "
  SELECT json_build_object(
    'tenants',   (SELECT count(*) FROM iam_dg_tenants),
    'users',     (SELECT count(*) FROM iam_dg_users),
    'documents', (SELECT count(*) FROM doc_dg_documents),
    'versions',  (SELECT count(*) FROM doc_dg_document_versions),
    'chunks',    (SELECT count(*) FROM doc_dg_chunks),
    'facts',     (SELECT count(*) FROM doc_dg_facts),
    'audit_logs',(SELECT count(*) FROM audit_dg_logs)
  )" | tr -d '\r')

cat > "$DEST/manifest.json" <<JSON
{
  "created_utc": "$STAMP",
  "alembic_version": "$ALEMBIC",
  "row_counts": $COUNTS,
  "buckets": ["${BUCKETS[0]}", "${BUCKETS[1]}"],
  "order": "postgres-then-objects",
  "files": {
    "roles": "roles.sql",
    "database": "database.dump",
    "objects": "objects/"
  }
}
JSON

du -sh "$DEST" | sed 's/^/  total: /'
echo
echo "Verifying backup is internally consistent..."
python3 scripts/dr/verify.py --manifest "$DEST/manifest.json" --live
echo
echo "Backup complete: $DEST"
echo "Restore with: ./scripts/dr/restore.sh $DEST"
