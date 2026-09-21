# Disaster Recovery

Backup and restore for the DMS, and the reasoning behind how it works. Every
number below was measured on this stack on 2026-09-21, not estimated.

```bash
./scripts/dr/backup.sh                          # full backup + verify
./scripts/dr/restore.sh backups/<stamp> \
    --target-db docsearch_dr_test \
    --target-bucket-prefix drtest-              # rehearse, touching nothing live
./scripts/dr/restore.sh backups/<stamp>         # real recovery (asks to confirm)
```

---

## What gets protected

| Component | Contents | Measured size |
|---|---|---|
| Postgres `docsearch` | documents, chunks + 1024-d embeddings, facts, entities, audit log, tenants/users, config | 95 MB |
| Cluster roles | `dms_app` (restricted), `docsearch` (superuser) | tiny, **critical — see below** |
| MinIO `docsearch-documents` | uploaded files and PDF/A renditions | 283 MiB / 1,197 objects |
| MinIO `docsearch-archive` | WORM archive | 1.7 MiB / 1 object |

**Not backed up, deliberately:** Redis (cache and Celery broker — rebuilds
itself; in-flight tasks are lost and their documents stay `pending`, which is
recoverable by re-uploading) and `backend/.env` (secrets; it is gitignored and
belongs in your secret store, not in a backup tarball).

---

## Measured performance

| | Measured |
|---|---|
| Backup | **5m 20s** for 95 MB DB + 283 MiB objects |
| Restore | **5m 30s** including full verification |
| **RPO** | = your backup interval. Nightly cron ⇒ up to 24h of uploads lost |
| **RTO** | ~6 min of mechanical work, plus however long it takes a human to decide to restore |

Cost is dominated by object copying, so both scale with total document volume,
not database size.

---

## Two things that decide whether a restore actually works

### 1. Postgres is dumped BEFORE objects — the order is not arbitrary

An upload writes the file to S3 and *only then* commits the database row
(`document_service.upload_document`: `upload_file()` runs well before
`db.commit()`). So "S3 write happens-before DB commit" is an invariant, and it
determines which order is safe:

- **Postgres first, objects second (what `backup.sh` does):** every row in the
  dump was committed by T1, so its object was written before T1, so it is
  certainly inside a mirror taken at T2 > T1. Anything uploaded in between
  becomes an orphaned object — invisible in the UI, harmless.
- **Objects first, Postgres second:** a file uploaded between the snapshots
  lands its object *outside* the mirror but its row *inside* the dump. You get
  a document that exists, appears in the drive, and 404s on download. Silent
  data loss.

The safe order costs orphaned blobs. That is the right trade.

### 2. Cluster roles are not in a database dump

`pg_dump` of `docsearch` does **not** include roles — they live at cluster
level. This install depends on `dms_app`, a non-superuser role that *cannot*
bypass Row-Level Security; RLS is only a real tenant boundary because the API
connects as that role.

Restore the database without `roles.sql` and you get one of two outcomes: the
app cannot connect at all, or somebody "fixes" it by pointing `APP_POSTGRES_URL`
at the superuser — at which point every request silently bypasses RLS and
tenant isolation is gone. `backup.sh` captures roles separately and
`restore.sh` applies them first.

(The production config guard in `app/config.py` now refuses to boot when
`APP_POSTGRES_URL` is unset, which catches the first outcome. It cannot catch
someone deliberately pointing it at the superuser.)

---

## Verification

`verify.py` runs automatically after both backup and restore. It checks four
things, and a negative control confirms it actually fails when it should:

1. **Schema version** vs the manifest — a dump restored onto a cluster on
   different migrations yields a database the app refuses to start against.
2. **Row counts** vs the manifest — a restore that silently dropped a table
   imports cleanly and looks fine until someone goes looking.
3. **Audit-chain integrity** — `audit_dg_logs` is append-only and hash-chained,
   and that chain is the product's tamper-evidence claim. Reordering or
   truncation during restore shows up here rather than during an audit.
4. **Document → object references** — every live version's `s3_path` must
   resolve to a real object. The purge path hard-deletes objects, so a purge
   racing the backup window can orphan a reference; this check makes that loud.

On the live system, row counts drifting *upward* are reported as warnings, not
failures — the database keeps moving after a backup is taken. Counts drifting
*down* are failures.

### Verified restore, 2026-09-21

Restored into a scratch database and prefixed buckets; live data untouched:

```
schema version 0052_scan_doc_translation      PASS
iam_dg_tenants 1282 / users 1288              PASS (exact)
documents 367 / versions 367                  PASS (exact)
chunks 1231 / facts 3400                      PASS (exact)
audit_dg_logs 20471                           PASS (exact)
audit chain intact, 1228 tenants, 20245 rows  PASS
object references: 240/240 resolve            PASS
```

Independently confirmed after restore: objects 283 MiB / 1,197 restored,
embeddings 1231/1231 present at 1024 dims (pgvector survives `pg_restore`),
25 RLS policies restored, and the `audit_dg_logs_append_only` trigger restored.

---

## Scheduling

Not wired into Celery Beat on purpose. A backup that runs inside the
application dies with the application, exactly when you need it. Use the host's
scheduler:

```cron
# 02:15 daily, keep 14 days
15 2 * * * cd /path/to/DMS && ./scripts/dr/backup.sh >> /var/log/dms-backup.log 2>&1
30 4 * * * find /path/to/DMS/backups -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```

`backups/` is gitignored. **A backup that never leaves the machine is not a
backup** — ship `backups/<stamp>/` to separate storage (different host, object
storage with versioning, or offline media). Nothing here does that for you.

---

## Recovering

**Full loss.** Bring up a clean stack, then:

```bash
./scripts/dr/restore.sh backups/<stamp>
```

It stops `backend`/`worker`/`beat` so nothing commits mid-restore, requires the
phrase `restore production`, restores roles → database → objects, verifies, and
restarts services. Then set the environment variables the production guards
require (`CONNECTOR_ACTOR_EMAIL`, `APP_POSTGRES_URL`, `EMAIL_WEBHOOK_SECRET`,
`CORS_ORIGINS`, `JWT_SECRET_KEY`) — they are not in the backup.

**Partial loss (database intact, objects gone, or vice versa).** Restore into
scratch names and copy across selectively rather than overwriting the healthy
half.

**Rehearsal.** Run the scratch restore quarterly. An untested backup is a
hypothesis.

---

## Known limitations

- **Point-in-time recovery is not available.** These are snapshots; you can
  recover to a backup, not to an arbitrary moment. PITR needs WAL archiving
  (`archive_mode`, `archive_command` + base backups), which is not configured.
- **A purge racing the backup** can orphan a reference between the two phases.
  The verifier detects it; to avoid it entirely, `docker compose stop beat`
  during the backup window.
- **The WORM archive bucket has no Object Lock enabled.** Checked on
  2026-09-21: `mc retention info` reports "Object locking is not enabled",
  despite `ensure_archive_bucket_exists()` intending it. Object Lock can only
  be set at bucket creation, so fixing this means recreating the bucket. Until
  then the archive is a normal bucket and its immutability guarantee is not
  actually enforced by storage.
- **Single-machine assumption** — scripts drive `docker compose`. A managed
  Postgres or real S3 needs the equivalent commands against those endpoints.
- **Restore is all-or-nothing per database.** No per-tenant restore.
