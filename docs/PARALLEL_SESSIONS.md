# Running two Claude sessions on this repo

Written after 2026-09-23, when two sessions worked the same checkout for a day.
It cost roughly three hours: mixed commits, a 30-minute database deadlock, and
six aborted test runs. None of it was hard to avoid once the failure modes were
understood.

## The three things that actually collide

Only the first is solved by a git worktree. Be clear about which problem you
are fixing.

### 1. The working tree — solved by a worktree

Both sessions edit the same files. `git status` interleaves two people's work,
and neither can commit without sweeping up the other's half-finished changes.

On 09-23 `backend/app/api/v1/router.py` ended up holding one session's scanner
removal and the other's new `users` router. Neither could commit it: committing
it without the untracked `users.py` would have left the repo importing a module
that wasn't there. Three frontend files hit the same deadlock.

```bash
# second session works here instead
git worktree add ../DMS-rbac -b feature-rbac
cd ../DMS-rbac
```

Each worktree has its own checkout, its own branch and its own index. Both share
one `.git`, so commits and branches are visible to both immediately. Merge when
each side is green.

```bash
git worktree list
git worktree remove ../DMS-rbac      # when finished
```

### 2. The Docker stack — NOT solved by a worktree

`docker-compose.yml` sets no `name:`, so Compose derives the project name from
the directory. A worktree in `../DMS-rbac` therefore becomes a *separate*
project that tries to bind the same host ports (3000, 8000, 5433, 6379, 9000/1,
5555, 2222, 3025/3143) and fails.

Pick one:

- **Share one stack** (simplest, and what the ports assume). Both sessions point
  at the same containers. Fine for editing; see §3 for the cost.
- **Run a second stack**, if you need true isolation:

  ```bash
  # in the worktree
  export COMPOSE_PROJECT_NAME=dms-rbac
  # and override every published port, e.g. 3100/8100/5533/6479/...
  docker compose up -d
  ```

  Budget for it: the backend alone holds ~8 GB once BGE-M3 is warm, so two full
  stacks need roughly 16-18 GB before Postgres and MinIO. On a 23 GB machine
  that is tight but workable.

### 3. The database and the test suite — NOT solved by a worktree

This is the one that actually hurt, and no amount of file isolation fixes it
while both sessions share a stack.

**Never run `pytest tests/` from two sessions at once.** `conftest.py`'s
`purge_tenants_created_by_this_session` ends with

```sql
DELETE FROM iam_dg_tenants WHERE id = ANY(CAST(:ids AS uuid[]))
```

against the shared database. Each run tracks only the tenants *it* created, but
there is no guard against deleting rows another run is actively holding locks
on. Two overlapping runs produce lock waits measured in tens of minutes, and
failures that look like real regressions but are just the other run deleting
your fixtures mid-test. On 09-23 exactly one `F` appeared at 34% for precisely
this reason.

Coordinate instead: one session runs the suite, messages the other when it is
done. Targeted files that create no tenants (pure unit tests) are safe to run
any time.

## Stopping a test run properly

`timeout 300 docker compose exec -T backend pytest ...` kills the **docker-exec
client on the host**, not the pytest process inside the container. The run keeps
going, keeps holding locks, and you will tell the other session it is stopped
when it is not. That happened on 09-23.

```bash
# confirm what is actually running in there
docker compose exec -T backend sh -c "ps -eo pid,args | grep '[p]ytest'"
# then kill it inside the container
docker compose exec -T backend sh -c "pkill -f 'pytest tests/'"
```

## Before you commit in a shared tree

```bash
git add <only your paths>          # never `git add -A`
git diff --cached --stat           # confirm nothing of theirs crept in
```

`git rm` stages immediately, so deletions from an earlier task may already be in
your index — check before every commit, and `git restore --staged <path>` to
drop anything that isn't yours.

If a single file genuinely holds both sessions' edits, do not split it by hand.
Decide who commits it whole, have them describe both changes in the message, and
have the other verify their hunks afterwards.
