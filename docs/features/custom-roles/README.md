# Custom roles — hand-over guide

Read this first, then `TASKS.md`. Written 2026-09-25 against `feature-kunal-DMS` @ `f3a7ba1`.

## What we are building

Today every user has one of six fixed roles (`records_officer`, `operator`,
`department_head`, `legal_counsel`, `it_admin`, `auditor`), hard-coded in ~52
API checks and a frontend table. Management wants:

- A new organisation (tenant) starts **clean**: one role, **Admin**.
- Admin creates **departments**, **users**, and **their own roles**.
- Each role = a set of ticked **permissions** + one switch: **own departments only** or **all departments**.
- Used by many entities (WAQF and others), each with its own internal departments (Accounts, Legal…).

## The model — two separate axes, never mix them

| Axis | Decides | Where it lives |
|---|---|---|
| **Role** | *what* a user may do (review, delete, manage users…) | NEW `iam_dg_roles` table, per tenant |
| **Department** | *which documents* a user sees | EXISTING — `iam_dg_departments`, members, folder grants (migration `0053`) |

Department document access is **already built and enforced in Postgres (RLS)**:
admin grants folders to a department, members see only those folders (and
subfolders). Search, chat and export respect it. It **fails closed**. Do not
rebuild it. The only change there: "sees all departments" stops being a
hard-coded role list (`TENANT_WIDE_ROLES`) and becomes the role's
`all_departments` flag.

## Rules (hard — do not break)

1. **Git: ask before any write.** No commit, branch, push, checkout, merge unless the person you work for says so. Teammates force-push this remote.
2. **Never edit an existing migration** (`0001`–`0055`). New work = new migration. Check `ls backend/migrations/versions | tail` for the next number (was `0056` when written).
3. **Fail closed.** Unknown permission, missing role, role from another tenant → deny (403), never allow. Same rule `0053` uses.
4. **Permissions are a fixed list in code.** Admin ticks from it; admin cannot invent new ones (a permission means nothing unless code checks it).
5. **Admin role is locked**: cannot be edited, renamed or deleted; the tenant's last Admin user cannot be removed, deactivated or moved to another role.
6. **No privilege escalation**: a non-Admin with `users.manage` may only assign roles whose permissions are a subset of their own; only an Admin may assign the Admin role or grant `all_departments`.
7. **Check permissions live** — read the role from the DB each request. Do not trust the `role` claim in the JWT (it lives up to 15 min after a change).
8. **Every existing user keeps exactly the access they have today** after the migration (backfill maps each old persona onto an equal role).
9. Impact first: before editing, list call sites/tests a change touches; if something surprises you, stop and ask.

## Where things are

- Role checks (backend): `backend/app/deps.py:55` `require_role` — used 52× in `backend/app/api/v1/*.py`. Full list with the permission each becomes: `TASKS.md` → R3 table.
- Department scope: `backend/app/services/department_service.py:24-25` (role sets), `:222` `apply_request_scope`, called from `backend/app/deps.py:89`.
- Review screen role sets: `backend/app/services/review_service.py:65-68`.
- User admin: `backend/app/services/user_admin_service.py:25` (`ASSIGNABLE_ROLES`), `:116-126` (last-admin guard).
- Signup (creates tenant + first user as `it_admin`): `backend/app/services/auth_service.py:46,79`; token: `:101-131`.
- System actor lookup by role: `backend/app/services/document_service.py:654`.
- Frontend matrix: `frontend/lib/permissions.ts` (`MATRIX`, `can`, `useRole`) — the `Action` names there ARE the permission keys.
- Frontend screens: `frontend/app/admin/{users,departments}/page.tsx`.
- New tables get `dms_app` grants automatically (default privileges, migration `0046:92`). New tenant tables still need RLS — copy the pattern in `0050_enable_rls_missing_tables.py`.

## Running

```bash
docker compose up -d                                   # whole stack
docker compose exec -T backend alembic upgrade head    # after adding a migration
docker compose exec -T backend python -m pytest tests/ -q --no-cov   # full suite
docker compose exec -T backend python -m pytest tests/test_roles*.py -q --no-cov
docker compose restart frontend                        # frontend is a prod build, ~1-2 min
```
Frontend http://localhost:3000 · API docs http://localhost:8000/api/docs

## How to pick up (for the next developer's Claude)

1. Read this file, then `TASKS.md` top section **"Where we left off"**.
2. Take the first task whose status is `todo` and whose "Needs" are all `done`.
3. Before coding: re-check the file:line refs in that task (code moves); report the impact; start.
4. Finish = the task's **Done when** list passes. Tick subtasks `[x]`, set status `done`.
5. **Update "Where we left off"** at the end of every session — what is done, what is half-done (with file names), what is next. This is the only hand-over channel.
