# Custom roles — task list

Status values: `todo` · `doing` · `done` · `blocked`. Read `README.md` first.

## Where we left off

> **2026-09-25** — Plan written. Nothing built. Next: settle R0 decisions, then R1.
> _(Update this block at the end of every session: done / half-done with file names / next.)_

## Overview

| ID | Task | Needs | Status | Owner |
|---|---|---|---|---|
| R0 | Decisions | — | todo | |
| R1 | Roles table + migration + backfill | R0 | todo | |
| R2 | Permission catalogue + live check | R1 | todo | |
| R3 | Swap the 52 API role checks | R2 | todo | |
| R4 | Department scope reads the role flag | R2 | todo | |
| R5 | Signup creates the Admin role | R1 | todo | |
| R6 | Roles API (CRUD + guards) | R2 | todo | |
| R7 | Users API uses role_id | R6 | todo | |
| R8 | `/auth/me` returns permissions | R2 | todo | |
| R9 | Frontend: permissions from server | R8 | todo | |
| R10 | Frontend: Roles screen | R6, R9 | todo | |
| R11 | Frontend: role picker on Users | R7, R10 | todo | |
| R12 | Tests | alongside each task | todo | |
| R13 | Clean-up (old enum, JWT claim) | all above | todo | |
| R14 | Optional: folder granted to one user | R0 | todo | |
| R15 | Docs + scope change note | all above | todo | |

Suggested split: lead builds **R1–R6** (model, security, backend guards). Junior takes **R7–R11** (API wiring + UI) and **R15**. R12 is done by whoever does the task.

---

## R0 — Decisions (settle before R1)

Defaults in **bold** — used if nobody objects.

- [ ] D1 Offer today's six roles as copy-able templates? **Yes** (keeps SoW T50 personas available).
- [ ] D2 Can Admin grant `users.manage` to others (e.g. a department head adds staff)? **Yes, with the escalation guard** (README rule 6).
- [ ] D3 Folder granted directly to one user (not via department)? **Later** — R14, optional.
- [ ] D4 Should upload / view / search / chat become permissions? **No** — today every user may, and *which* documents is decided by department scope.
- [ ] D5 Scope sign-off: SoW T50 names six fixed personas; this replaces them. **Needs written OK from product owner** before release.

## R1 — Roles table + migration + backfill

Files: new `backend/migrations/versions/0056_custom_roles.py`, new `backend/app/models/role.py`, `backend/app/models/user.py`, `backend/app/models/__init__.py`.

- [ ] Table `iam_dg_roles`: `id` uuid pk, `tenant_id` fk, `name` varchar(80), `is_system` bool (true only for Admin), `all_departments` bool default **false**, `permissions` text[] default `{}`, `created_at`, `updated_at`, `created_by` nullable. `UNIQUE(tenant_id, lower(name))`.
- [ ] RLS: enable + force + `tenant_isolation_policy` exactly like `0050_enable_rls_missing_tables.py`.
- [ ] `iam_dg_users.role_id` uuid fk → `iam_dg_roles.id`, nullable for now, index.
- [ ] Backfill per tenant: create `Admin` (is_system, all permissions, all_departments). For each other persona used in that tenant create a role with the same permissions and scope it has today (table in R2). Set every user's `role_id`. `it_admin` → Admin.
- [ ] Keep the old `role` enum column untouched (dropped in R13).
- [ ] `downgrade()` drops the column and the table.

**Done when:** `alembic upgrade head` on the local DB, every user has a `role_id`, `SELECT` as `dms_app` with no tenant set returns 0 roles.

## R2 — Permission catalogue + live check

Files: new `backend/app/permissions.py`, `backend/app/deps.py`.

- [ ] `PERMISSIONS` — the fixed list (keys = frontend `Action` names in `frontend/lib/permissions.ts`), each with group + English label:

| Group | Keys |
|---|---|
| Review | `facts.review`, `documents.classify`, `records.create`, `entities.edit`, `corpus.calibrate`, `review.read`, `review.edit`, `review.verify`, `review.revertAll` |
| Documents | `content.deletePermanent` |
| Reports | `export.report`, `certificate.section63`, `audit.integrity`, `analytics.view` |
| Administration | `users.manage`, `roles.manage` (NEW), `departments.manage`, `templates.manage`, `config.manage`, `license.manage`, `billing.view` |

- [ ] `PERSONA_TEMPLATES` — the six old personas as permission sets + `all_departments`. Build them from today's gates (R3 table) and `department_service.TENANT_WIDE_ROLES` (`it_admin`, `auditor`, `legal_counsel` = all departments). Used by R1 backfill and D1 templates.
- [ ] `require_permission(key)` in `deps.py`: loads the user's role from the DB by `sub` + `tenant_id` (one query), 403 if role missing, other tenant, or key not in role. Unknown key at import time → raise (catches typos).
- [ ] Put the loaded role on the request (so R4 reuses it, no second query).
- [ ] No cache at first. Add Redis cache only if measured slow; then invalidate on role edit.

**Done when:** unit tests: allowed / denied / unknown key / role of other tenant / user with no role → 403.

## R3 — Swap the 52 API role checks

Mechanical: `Depends(require_role(...))` → `Depends(require_permission("key"))`. One file per commit-sized step.

| File:line (at `f3a7ba1`) | Endpoint | Key |
|---|---|---|
| `admin.py:30, :207` | admin analytics, API analytics | `analytics.view` |
| `admin.py:370, :385` | list/update config | `config.manage` |
| `billing.py:16` | subscription status | `billing.view` |
| `billing.py:40, :64` | license status / install | `license.manage` |
| `departments.py:27,36,48,63,76,89,101` | all department endpoints | `departments.manage` |
| `documents.py:80, :92` | classify / dismiss classification | `documents.classify` |
| `documents.py:215, :229` | cleanup trash / delete document | `content.deletePermanent` |
| `folders.py:107` | delete folder permanently | `content.deletePermanent` |
| `entities.py:34,74,111,122,154,166,180,198` | all entity-graph edits | `entities.edit` |
| `export.py:17, :37` | entity export / summary report | `export.report` |
| `facts.py:48,66,80,101,113,125,138,151` | all fact review actions | `facts.review` |
| `governance.py:33` | audit integrity | `audit.integrity` |
| `governance.py:45` (multi-line) | Section 63 certificate | `certificate.section63` |
| `governance.py:83` | calibrate corpus | `corpus.calibrate` |
| `records.py:25` | create record | `records.create` |
| `review.py:22-25` | review read/edit/verify/revert-all | `review.read` / `review.edit` / `review.verify` / `review.revertAll` |
| `templates.py:36,50,65` | template CRUD | `templates.manage` |
| `users.py:28,37,53` | user admin | `users.manage` |

- [ ] Swap every row. Re-grep first: `grep -rn "require_role" backend/app` — count must reach 0 (except the definition, removed in R13).
- [ ] `review_service.py:65-68` role tuples → removed; `review.py` uses the four keys.

**Done when:** `grep require_role backend/app/api` is empty; full suite green.

## R4 — Department scope reads the role flag

Files: `backend/app/services/department_service.py:24-25, :222-237`, `backend/app/deps.py:89`, `backend/app/services/document_service.py:654`.

- [ ] `apply_request_scope(...)` takes `all_departments: bool` instead of `role: str`; `TENANT_WIDE_ROLES` / `DEPARTMENT_SCOPED_ROLES` removed.
- [ ] `deps.get_tenant_db` passes the flag from the role loaded in R2. No role → scoped (fail closed).
- [ ] `department_service.py:97` (member list shows role) → show role name via `role_id`.
- [ ] `document_service.py:654` system actor: pick a user whose role `is_system` (Admin) instead of `role == "it_admin"`.
- [ ] Do NOT touch migration `0053` or its policies.

**Done when:** a role with `all_departments=false` sees only granted folders; `true` sees all; existing `test_department_scope.py` + `test_rls_enforcement.py` green.

## R5 — Signup creates the Admin role

Files: `backend/app/services/auth_service.py:46-102`.

- [ ] New tenant → create `Admin` role (is_system, all permissions, all_departments) → first user `role_id` = it.
- [ ] Nothing else created: no departments, no other roles, no templates copied (clean slate).

**Done when:** sign up a fresh account → exactly one role, one user, Admin.

## R6 — Roles API

Files: new `backend/app/api/v1/roles.py`, new `backend/app/services/role_service.py`, `backend/app/api/v1/router.py`.

- [ ] `GET /roles` (with user count per role), `POST /roles`, `PATCH /roles/{id}`, `DELETE /roles/{id}` — gated `roles.manage`.
- [ ] `GET /roles/permissions` — the catalogue (groups + labels) for the UI grid.
- [ ] `GET /roles/templates` + create-from-template (only if D1 = yes).
- [ ] Guards: Admin role immutable (409); name unique per tenant; unknown permission key → 422; delete a role still assigned to users → 409 with the count; non-Admin cannot create/edit a role with permissions they lack or with `all_departments` (403).
- [ ] Every change → `log_action` audit entry (actor, before, after).

**Done when:** tests for each guard; audit rows written.

## R7 — Users API uses role_id

Files: `backend/app/services/user_admin_service.py`, `backend/app/api/v1/users.py`, schemas.

- [ ] Create/update user takes `role_id` (must be a role of the same tenant); `ASSIGNABLE_ROLES` / `_validate_role` removed.
- [ ] Last-admin guard (`:116-126`) → "last user with the Admin role"; also blocks deactivation.
- [ ] Escalation guard (README rule 6).
- [ ] List returns `role_id` + `role_name`.

**Done when:** tests: assign, cross-tenant role rejected, last admin protected, escalation blocked.

## R8 — `/auth/me` returns permissions

Files: `backend/app/api/v1/auth.py:29`, its response schema.

- [ ] Add `role_id`, `role_name`, `permissions: string[]`, `all_departments`, `is_admin`. Keep old `role` field until R13 (frontend still reads it during transition).

## R9 — Frontend: permissions from server

Files: `frontend/lib/permissions.ts`, `frontend/lib/auth.ts`.

- [ ] `can(action)` checks `profile.permissions` instead of `MATRIX`. Remove `MATRIX`, `ROLES`, `ROLE_LABELS`, `displayRole`; `roleLabel` → `profile.role_name`.
- [ ] Update every caller (list at writing time): `app/admin/{page,users,departments,settings,templates}`, `app/profile`, `app/entities`, `app/drive`, `app/workbench`, `components/review/ReviewScreen.tsx`, `components/drive/{DocumentPreviewModal,DriveTopHeader}.tsx`, `components/chat/{PersistentChatPanel,RightSideChatDrawer}.tsx`. Re-grep: `grep -rn "useRole\|can(\|roleLabel" frontend/app frontend/components`.
- [ ] Refresh `/auth/me` on window focus so a changed role shows without re-login.

**Done when:** a user whose role loses `facts.review` no longer sees review buttons after refocus; server still 403s.

## R10 — Frontend: Roles screen

Files: new `frontend/app/admin/roles/page.tsx`, admin nav, `frontend/lib/api.ts`, `frontend/lib/i18n.tsx` (EN/HI/MR keys).

- [ ] List: name, users count, all-departments badge, Admin shown locked.
- [ ] Create/edit: name, permission grid grouped (from `GET /roles/permissions`), switch "Can see all departments" (off by default, Admin-only).
- [ ] Delete: blocked with message when users still hold it.
- [ ] "Start from template" (if D1 = yes).
- [ ] Style like `admin/departments/page.tsx`.

## R11 — Frontend: role picker on Users

Files: `frontend/app/admin/users/page.tsx`.

- [ ] Replace fixed role dropdown with roles from `GET /roles`. Show role name in the table.

## R12 — Tests

- [ ] Fixtures (`backend/tests/conftest.py`): helper to create a role + user by permission set. ~20 test files create users with `role=` — move them to the helper.
- [ ] New `tests/test_roles_*.py`: catalogue, live revoke (change role → next request denied), escalation, last admin, scope flag, fail-closed (user with no role).
- [ ] Frontend e2e (optional): admin creates role, assigns, user sees the change.

## R13 — Clean-up

- [ ] Remove `require_role`, `UserRole` usage, `role` claim from JWT (`auth_service.py:114-131`), old `role` field in `/auth/me`.
- [ ] Migration: `role_id` NOT NULL; stop writing the enum column (Postgres can't drop enum values — leave type, drop column if nothing reads it).

## R14 — Optional: folder granted to one user

Only if D3 = yes. New table `iam_dg_user_folders (user_id, folder_id)`; `list_user_scope_folder_ids` unions it in. RLS policies unchanged (they read `app.scope_folder_ids`).

## R15 — Docs + scope change note

- [ ] Update `docs/review-screen.md` (roles line), `docs/BACKEND_ENDPOINTS.md`, this folder.
- [ ] One-page change note for product owner: SoW T50 six personas → admin-defined roles (D5).
