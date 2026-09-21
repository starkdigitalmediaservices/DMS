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

## The WORM archive — verified state

`docsearch-archive` has Object Lock **enabled** and versioning **enabled**,
confirmed 2026-09-21 via the S3 API:

```
get_object_lock_configuration -> {'ObjectLockEnabled': 'Enabled'}
get_bucket_versioning         -> 'Enabled'
```

Immutability was tested live, not inferred. Writing an object with
`ObjectLockMode=COMPLIANCE`, then attempting to permanently remove that
version:

```
delete_object(Key, VersionId) -> ClientError: InvalidRequest, "Object is WORM"
```

A plain `delete_object` (no VersionId) creates a delete marker and the locked
version survives, which is correct S3 behaviour. An overwrite creates a *new
version* rather than replacing the locked one — Object Lock protects versions,
not the key name.

**Do not be misled by `mc retention info`**, which reports "Object locking is
not enabled" for this bucket. That command describes the bucket's *default
retention rule*, not whether Object Lock is enabled. No default rule is set
here, and that is deliberate: `archive_file_with_retention()` applies retention
per object using the document's own retention class, which a single blanket
bucket-wide period would override. (This distinction cost a false "WORM is not
enforced" finding earlier on 2026-09-21 — check
`get_object_lock_configuration`, not `mc retention info`.)

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
application dies with the application, exactly when you need it. It runs from
the host's cron, through `scripts/dr/cron-backup.sh`.

**Installed on this host, 02:15 daily:**

```cron
15 2 * * * DMS_BACKUP_KEEP=7 DMS_BACKUP_MIN_FREE_MB=3000 /path/to/DMS/scripts/dr/cron-backup.sh
```

`cron-backup.sh` exists rather than calling `backup.sh` directly because cron
introduces failure modes an interactive shell does not, and each one silently
breaks unattended backups:

| | Why the wrapper handles it |
|---|---|
| **PATH** | cron runs with a minimal PATH and no profile, so `docker` is often not found — the job "runs" nightly and produces nothing |
| **CWD** | cron starts in `$HOME`, not the repo |
| **Overlap** | a run takes ~5.5 min; `flock` stops a manual run colliding with the scheduled one (a skip logs and exits 0 — it is not a failure) |
| **Disk** | refuses to start below `DMS_BACKUP_MIN_FREE_MB`. This host runs ~89% full; an unguarded nightly 320 MB write fills the root filesystem and takes the database down. The backup job becoming the outage is a bad trade |
| **Silence** | cron mails output nobody reads, so everything lands in `backups/backup.log` |

Tunables: `DMS_BACKUP_DIR`, `DMS_BACKUP_KEEP` (default 7),
`DMS_BACKUP_MIN_FREE_MB` (default 3000), `DMS_BACKUP_LOG`,
`DMS_BACKUP_REQUIRE_MOUNT`.

### Moving backups to a separate volume

**As of 2026-09-21 this host has no second volume.** One disk (`sda`, 238.5 GB):
`sda1` → `/`, `sda2` → 1 GB EFI, `sda3` → swap. No unmounted partitions, no
network mounts in `fstab`, `/media/stark` and `/mnt` empty. Backups therefore
sit on the same filesystem as the data they protect — one disk failure loses
both. **This is the largest remaining gap in the DR story**, and it needs
hardware, not code.

Once real storage exists, point the job at it and turn on the mount check:

```cron
15 2 * * * DMS_BACKUP_DIR=/mnt/backup/dms DMS_BACKUP_REQUIRE_MOUNT=1 \
           DMS_BACKUP_KEEP=7 /path/to/DMS/scripts/dr/cron-backup.sh
```

`DMS_BACKUP_REQUIRE_MOUNT=1` is not optional paranoia. An external disk or
network share that fails to mount leaves an ordinary empty directory at the
same path, so the backup writes happily to the root filesystem instead —
quietly filling the disk it was moved off, while the operator believes backups
are landing elsewhere. Both the "backups are offsite" and the "root has space"
assumptions are false at once, and nothing says so until something breaks. With
the flag set, the job refuses to run and logs `BACKUP-FAILED`.

A backup that fails its own verification is **kept and renamed**
`<stamp>.UNVERIFIED` rather than deleted — the evidence is worth more than the
disk. Pruning only ever removes directories matching the exact timestamp shape
this script writes, so anything else under `backups/` is untouched.

### Monitoring

```bash
tail -f backups/backup.log              # watch a run
grep BACKUP-FAILED backups/backup.log   # the string to alert on
```

`BACKUP-FAILED` is emitted for every abort — docker missing, postgres down,
insufficient disk, or failed verification. Nothing here alerts you; wire that
grep into whatever you already watch.

### Verified on install, 2026-09-21

Exercised under `env -i` (cron's stripped environment), not just interactively:

```
disk guard      refused to start, logged BACKUP-FAILED, exit 1
overlap lock    second concurrent run skipped cleanly, exit 0
full run        320 MB in 5m12s, all verification checks PASS
prune           KEEP=1 -> pruned=2 retained=1, newest kept, disk reclaimed
prune safety    an unrelated directory under backups/ was not matched
```

`backups/` is gitignored. **A backup that never leaves the machine is not a
backup** — ship `backups/<stamp>/` to separate storage (different host, object
storage with versioning, or offline media). Nothing here does that for you, and
on this host it matters twice over: the backups currently sit on the same 89%-
full root filesystem as the data they protect.

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
- **The WORM archive is immutable, but a backup of it is not.** The bucket's
  Object Lock only binds inside MinIO; once `backup.sh` copies those objects
  out they are ordinary files on disk, and anyone with the backup can alter
  them. Treat the archive's evidentiary weight as resting on the live bucket,
  and give backup storage its own retention controls if the backup is meant to
  carry the same guarantee.
- **Single-machine assumption** — scripts drive `docker compose`. A managed
  Postgres or real S3 needs the equivalent commands against those endpoints.
- **Restore is all-or-nothing per database.** No per-tenant restore.
