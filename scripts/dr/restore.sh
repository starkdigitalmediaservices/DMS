#!/usr/bin/env bash
#
# Restore a DMS backup produced by backup.sh.
#
#   ./scripts/dr/restore.sh <backup-dir> [--target-db NAME] [--target-bucket-prefix PREFIX]
#
# Restoring into the live database is destructive and requires typing the
# confirmation phrase. Restoring into a scratch database is the default-safe
# way to rehearse a recovery, and is how the procedure is tested without
# touching production data:
#
#   ./scripts/dr/restore.sh backups/<stamp> --target-db docsearch_dr_test
#
set -euo pipefail

cd "$(dirname "$0")/../.."

BACKUP="${1:-}"
[ -z "$BACKUP" ] && { echo "usage: $0 <backup-dir> [--target-db NAME]"; exit 2; }
shift

TARGET_DB="docsearch"
TARGET_PREFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target-db) TARGET_DB="$2"; shift 2 ;;
    --target-bucket-prefix) TARGET_PREFIX="$2"; shift 2 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

for f in manifest.json roles.sql database.dump; do
  [ -f "$BACKUP/$f" ] || { echo "ABORT: $BACKUP/$f missing — not a complete backup"; exit 2; }
done

echo "Restoring $BACKUP"
echo "  target database: $TARGET_DB"
python3 -c "
import json;m=json.load(open('$BACKUP/manifest.json'))
print('  taken:', m['created_utc'], '| schema:', m['alembic_version'])
print('  expects:', m['row_counts'])"
echo

if [ "$TARGET_DB" = "docsearch" ]; then
  # Overwriting the live database is the one genuinely irreversible action in
  # this script, so it asks for a phrase rather than a y/n that is easy to
  # hit by reflex.
  echo "*** This OVERWRITES the live 'docsearch' database. ***"
  read -r -p "Type 'restore production' to continue: " CONFIRM
  [ "$CONFIRM" = "restore production" ] || { echo "Aborted."; exit 1; }
  echo "Stopping writers so nothing commits mid-restore..."
  docker compose stop backend worker beat >/dev/null 2>&1 || true
fi

# --- 1. Roles ---------------------------------------------------------------
# Idempotent: roles are cluster-wide and usually already exist, so "already
# exists" here is success, not failure. Skipping this entirely is what leaves
# a restored cluster without dms_app — see backup.sh's note on why that
# silently disables tenant isolation.
echo "  [1/4] cluster roles"
docker compose exec -T postgres psql -U docsearch -d postgres \
  < "$BACKUP/roles.sql" >/dev/null 2>&1 || true

# --- 2. Database ------------------------------------------------------------
echo "  [2/4] database -> $TARGET_DB"
docker compose exec -T postgres psql -U docsearch -d postgres \
  -c "DROP DATABASE IF EXISTS $TARGET_DB WITH (FORCE)" >/dev/null
docker compose exec -T postgres psql -U docsearch -d postgres \
  -c "CREATE DATABASE $TARGET_DB OWNER docsearch" >/dev/null
# --no-owner/--no-privileges keep the restore working when the target cluster's
# role set differs; grants are reapplied from roles.sql above.
docker compose exec -T postgres pg_restore -U docsearch -d "$TARGET_DB" \
  --no-owner --no-privileges < "$BACKUP/database.dump" >/dev/null 2>&1 || true

# --- 3. Objects -------------------------------------------------------------
echo "  [3/4] object store"
for dir in "$BACKUP"/objects/*/; do
  [ -d "$dir" ] || continue
  BUCKET="$(basename "$dir")"
  DEST="${TARGET_PREFIX}${BUCKET}"
  echo -n "        $BUCKET -> $DEST "
  docker compose exec -T minio sh -c "rm -rf /tmp/dr-restore" >/dev/null 2>&1 || true
  docker compose cp "$dir" "minio:/tmp/dr-restore" >/dev/null 2>&1
  docker compose exec -T minio sh -c "
    mc alias set local http://localhost:9000 \
      \${MINIO_ROOT_USER:-minioadmin} \${MINIO_ROOT_PASSWORD:-minioadmin} >/dev/null 2>&1
    mc mb --ignore-existing local/$DEST >/dev/null 2>&1
    mc mirror --overwrite --quiet /tmp/dr-restore local/$DEST >/dev/null 2>&1
    rm -rf /tmp/dr-restore
  "
  echo "done"
done

# --- 4. Verify --------------------------------------------------------------
echo "  [4/4] verifying"
python3 scripts/dr/verify.py --manifest "$BACKUP/manifest.json" \
  --database "$TARGET_DB" --bucket-prefix "$TARGET_PREFIX"
STATUS=$?

if [ "$TARGET_DB" = "docsearch" ]; then
  echo "Restarting services..."
  docker compose start backend worker beat >/dev/null 2>&1 || true
fi

exit $STATUS
