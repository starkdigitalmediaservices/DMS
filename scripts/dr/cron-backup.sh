#!/usr/bin/env bash
#
# Cron wrapper around backup.sh. Not merely `backup.sh >> log` — running from
# cron introduces failure modes that do not exist in an interactive shell, and
# each one below is here because it silently breaks unattended backups:
#
#   PATH      cron runs with a minimal PATH (typically /usr/bin:/bin) and no
#             profile, so `docker` is frequently not found. The backup then
#             "runs" nightly and produces nothing.
#   CWD       cron starts in $HOME, not the repo.
#   Overlap   a backup takes ~5.5 min; a manual run or a slow night must not
#             have two writing at once.
#   Disk      this host is 89% full. An unguarded nightly 320 MB write fills
#             the root filesystem and takes the database down with it — the
#             backup job becoming the outage is a genuinely bad trade.
#   Silence   cron mails output nobody reads. Failures are recorded in the log
#             with a marker that is greppable for alerting.
#
# Configure with environment variables (set them in the crontab line):
#   DMS_BACKUP_DIR      where backups are written   (default: <repo>/backups)
#   DMS_BACKUP_KEEP     how many to retain          (default: 7)
#   DMS_BACKUP_MIN_FREE_MB  refuse to run below this free space (default: 3000)
#   DMS_BACKUP_REQUIRE_MOUNT  set to 1 when DMS_BACKUP_DIR lives on a separate
#                             disk, USB or network share — see below
#
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

# Cron's PATH does not include the usual locations for docker/compose.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

BACKUP_DIR="${DMS_BACKUP_DIR:-$REPO/backups}"
KEEP="${DMS_BACKUP_KEEP:-7}"
MIN_FREE_MB="${DMS_BACKUP_MIN_FREE_MB:-3000}"
LOG="${DMS_BACKUP_LOG:-$REPO/backups/backup.log}"
LOCK="${DMS_BACKUP_LOCK:-/tmp/dms-backup.lock}"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; }

fail() {
    # BACKUP-FAILED is the string to alert on.
    log "BACKUP-FAILED: $*"
    exit 1
}

# --- single instance --------------------------------------------------------
exec 9>"$LOCK"
if ! flock -n 9; then
    log "SKIPPED: another backup is already running (lock $LOCK)"
    exit 0
fi

log "=== backup run starting (keep=$KEEP, dir=$BACKUP_DIR) ==="

# --- preflight --------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker not on PATH ($PATH)"

docker compose ps --status running --format '{{.Service}}' 2>/dev/null \
    | grep -qx postgres || fail "postgres container is not running — nothing to back up"

# When the backup target is meant to be a separate volume, verify it is
# actually mounted. An external disk or network share that failed to mount
# leaves an ordinary empty directory at the same path, so the backup writes
# happily to the root filesystem instead — quietly filling the disk it was
# moved off, while the operator believes backups are landing on the other
# volume. Both the "backups are offsite" and the "root has space" assumptions
# are false at once, and nothing says so until something breaks.
if [ "${DMS_BACKUP_REQUIRE_MOUNT:-0}" = "1" ]; then
    mountpoint -q "$BACKUP_DIR" \
        || fail "DMS_BACKUP_REQUIRE_MOUNT=1 but $BACKUP_DIR is not a mount point — \
the backup volume is not mounted. Refusing to write to the underlying filesystem."
    log "mount check ok ($BACKUP_DIR is a mount point)"
fi

FREE_MB=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "${FREE_MB:-0}" -lt "$MIN_FREE_MB" ]; then
    fail "only ${FREE_MB}MB free at $BACKUP_DIR, need ${MIN_FREE_MB}MB. \
Prune old backups, raise DMS_BACKUP_KEEP down, or point DMS_BACKUP_DIR at another volume."
fi
log "preflight ok (${FREE_MB}MB free)"

# --- backup -----------------------------------------------------------------
# Runs BEFORE pruning: pruning first would risk deleting the only good backup
# and then failing to produce a replacement. The space guard above is what
# keeps the transient peak safe.
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DEST="$BACKUP_DIR/$STAMP"

if ./scripts/dr/backup.sh "$DEST" >> "$LOG" 2>&1; then
    SIZE=$(du -sh "$DEST" 2>/dev/null | cut -f1)
    log "backup OK: $DEST ($SIZE)"
else
    # backup.sh exits non-zero when its own verification fails, which means
    # the artifact exists but should not be trusted. Keep it for diagnosis,
    # clearly marked, rather than deleting the evidence.
    mv "$DEST" "$DEST.UNVERIFIED" 2>/dev/null
    fail "backup.sh failed or did not verify — kept as $DEST.UNVERIFIED"
fi

# --- prune ------------------------------------------------------------------
# Only ever removes directories matching the timestamp shape this script
# writes, so an unrelated directory under BACKUP_DIR is never touched.
PRUNED=0
while IFS= read -r old; do
    rm -rf "$old" && PRUNED=$((PRUNED + 1)) && log "pruned $(basename "$old")"
done < <(find "$BACKUP_DIR" -maxdepth 1 -type d \
            -regextype posix-extended \
            -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z(\.UNVERIFIED)?$' \
            | sort | head -n -"$KEEP")

REMAINING=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name '20*Z*' | wc -l)
log "=== run complete: pruned=$PRUNED retained=$REMAINING free=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')MB ==="
