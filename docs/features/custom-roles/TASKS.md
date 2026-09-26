# Custom roles — task list

Status values: `todo` · `doing` · `done` · `blocked`. Read `README.md` first.

## Where we left off

> **2026-09-25** — R0 decisions all settled (D5 sign-off still owed by product owner, does not block code). **R1 done**: `backend/migrations/versions/0056_custom_roles.py`, `backend/app/models/role.py`, `User.role_id` in `app/models/user.py`, `Role` registered in `app/models/__init__.py`, `iam_dg_roles` added to cleanup lists in `tests/conftest.py` + `scripts/purge_test_tenants.py`. Verified: upgrade + downgrade + backfill (6 personas across 2 tenants mapped correctly), RLS hides roles with no tenant set. Design note: Admin role stores NO permissions — `is_system` means all, so future permissions reach Admin automatically. **R2 done** (full suite 486 pass; 2 failures are external APIs — Cohere key rejected, live Groq gazette match — unrelated): `backend/app/permissions.py` (21 keys in 4 groups + `ROLE_TEMPLATES` + `role_grants`), `deps.load_live_access` (one LEFT JOIN, ignores a role from another tenant), `deps.require_permission(key)` (typo → error at startup), new optional fields on `TokenPayload` (`schemas/auth.py`). `load_live_role` kept for `/auth/refresh`. Tests: `tests/test_roles_permissions.py` (16). **R3 done** (full suite 489 pass, same 2 external-API failures): all 51 `require_role` gates in `app/api/v1/*` → `require_permission` (count was 51, not 52). `review_service` role tuples → `*_PERMISSION` keys + `permissions.grants()`; `review.py` passes the live `TokenPayload`. **Bridge (remove in R13):** a user with no `role_id` gets their old persona's exact access via `permissions.legacy_access` — needed because signup (R5) and the user screen (R7) don't set `role_id` yet. E2E test proves a role edit applies on the next request. `require_role` still defined in `deps.py`, unused. Frontend still reads `role` strings — unchanged until R9. **R4 done** (full suite 490 pass; only failures are the 2 external-API tests — Cohere is a trial key capped at 1000 calls/month, every full run spends some): `department_service.apply_request_scope` takes live access or persona string and calls `permissions.sees_all_departments`; `TENANT_WIDE_ROLES`/`DEPARTMENT_SCOPED_ROLES` removed; callers `deps.get_tenant_db` + `api/v1/search.py:97` pass the whole `TokenPayload`. System actor (`document_service._resolve_policy_actor`) = Admin-role holder or legacy `it_admin`. E2E test: Accounts clerk sees only Billing; flag on → sees all; off → hidden again. Not done in R4: `department_service.list_departments` still shows the legacy `role` column for members (do in R7 with role names). **R5 done**: `auth_service.sign_up` creates the tenant's single locked Admin role and sets the founder's `role_id`; `tests/test_signup_role.py` asserts exactly one role. **R6 done**: `app/services/role_service.py` + `app/api/v1/roles.py` (registered in `router.py`): `GET /roles` (roles.manage OR users.manage — the Users screen needs it), `GET /roles/permissions` (grouped catalogue), `GET /roles/templates`, `POST/PATCH/DELETE /roles`. Guards: Admin locked (409), name unique case-insensitive (409), unknown key (422), role in use can't be deleted (409 with count), other tenant's role 404, escalation guard on create/edit/delete for non-Admins (before AND after state). Every change audit-logged (`role.create/update/delete`). Tests: `tests/test_roles_api.py` (15).
>
> **Lead half (R1–R6) complete.**
>
> **R7 done**: `/users` create/update take `role_id` (must be this tenant's role → else 422); legacy `role` string still accepted for the pre-R11 screen (sets enum, clears `role_id`). Guards: own role 400; last Admin 400 (defence in depth — unreachable via API since only an Admin can change an Admin); delegate may only assign roles within own permissions, never Admin / all-departments, and can't change an Admin's role. List returns `role_id`, `role_name`, `is_admin`, `folders`. Department member list shows `role_name`. Test in `test_department_scope.py:274` updated for new wording ("last Admin"). Tests: `tests/test_users_roles_api.py`.
> **R8 done**: `/auth/me` returns `role_id`, `role_name`, `is_admin`, `all_departments`, `permissions` (Admin's spelled out in full).
> **R14 backend done**: migration `0057_user_folder_grants.py` (`iam_dg_user_folders`, RLS), `UserFolder` model, `department_service.grant_user_folder / revoke_user_folder / user_folders_by_user`, scope query unions it; `POST/DELETE /users/{id}/folders[/{folder_id}]` gated `departments.manage`; audit `user.grant_folder/revoke_folder`. UI part goes with R11.
> **R16 done**: see table in R16. One open item (document list download links).
>
> Full backend suite after R7–R16: **523 passed**, only the 2 external-API failures.
>
> **R9 done**: `frontend/lib/permissions.ts` rewritten — `canWith(profile, action)` reads `profile.permissions` / `is_admin` from `/auth/me`; old table kept only as `LEGACY_MATRIX` for a profile cached before this change (remove in R13). `useRole()` API unchanged (+ `roleName`, `isAdmin`, `allDepartments`), so the 12 screens using it were untouched. `/auth/me` refreshes on window focus (throttled 30 s). Role names shown from server in header (`DriveTopHeader`), profile, departments page.
> **R10 done**: new `frontend/app/admin/roles/page.tsx` — role cards (Admin shown locked, user counts, all-departments badge, permission chips), editor with grouped permission grid + select-all per group, "Start from a template", all-departments switch (disabled for non-Admins), delete disabled while users hold the role. Linked from Admin panel, header menu, Users page. **Open:** labels are English only — no `t()` keys / Marathi yet.
> **R11 done**: `frontend/app/admin/users/page.tsx` — role dropdowns list tenant roles (`role_id`); "Folder access" column shows department chips + directly-shared folder chips (× to stop sharing) + "Share a folder…" picker (only with `departments.manage`). `lib/api.ts`: `api.roles.*`, `api.users.shareFolder/unshareFolder`. Types in `frontend/types/index.ts`.
> ⚠️ **Frontend build gotcha:** `docker compose restart frontend` runs `next build`, and an ESLint *error* (e.g. `jsx-a11y/label-has-associated-control`) fails the build and takes the frontend DOWN. Run `docker compose exec -T frontend npx --no-install next lint` and `... tsc --noEmit -p tsconfig.json` BEFORE restarting. (`node_modules` exists only inside the container.)
>
> **2026-09-26 — browser walk-through done (headless Chrome driving the real UI, fresh tenant `walk.admin.1790438662@veritasdocs-roles-walk.com`), all passed:** sign-up → Roles shows only a locked Admin (no edit button); Admin created 2 roles on the Roles screen (Auditor template sets all-departments on), 2 users on Users, 2 departments + members + folder grants on Departments; clerk sees only Billing (drive + API), gets 403 on `/roles` and `/users` and "You don't have access" on the Roles screen — even with a token claiming `it_admin` (live role wins); delete disabled while the role is in use; Admin ticks all-departments → clerk sees Case Files on the next request with the same token, open screen picks it up on focus; unticked → hidden again; legal officer logs in for real with the temp password, sees only Case Files; Admin shares "Audit 2025" with them alone on Users → they can open it, its parent Billing stays 404. No console errors. Fixed the same day: Users screen no longer offers "Share a folder…" to anyone whose role already sees all departments (Admin, or the role switch on) — shows an "All folders" badge instead (`app/admin/users/page.tsx`, `seesAll`). Still worth a short human click-through for look and feel.
> **Test harness fix (2026-09-26):** `tests/conftest.py` `cleanup_connections_after_test` now waits for pending API-log writes before disposing engines. Without it, writes stranded on a finished test's event loop stayed `idle in transaction` and the session-end tenant purge hung forever (custom-roles tests: 11 min + hang → 65 s).
>
> **Not yet done:** R10 i18n (Marathi); R12 frontend e2e (optional); R13 clean-up (remove legacy bridge, JWT role claim, enum column); R15 docs + scope change note; R16 open item (list download links). **Next: R13.**
> _(Update this block at the end of every session: done / half-done with file names / next.)_

## Overview

| ID | Task | Needs | Status | Owner |
|---|---|---|---|---|
| R0 | Decisions | — | done | |
| R1 | Roles table + migration + backfill | R0 | done | |
| R2 | Permission catalogue + live check | R1 | done | |
| R3 | Swap the 51 API role checks | R2 | done | |
| R4 | Department scope reads the role flag | R2 | done | |
| R5 | Signup creates the Admin role | R1 | done | |
| R6 | Roles API (CRUD + guards) | R2 | done | |
| R7 | Users API uses role_id | R6 | done | |
| R8 | `/auth/me` returns permissions | R2 | done | |
| R9 | Frontend: permissions from server | R8 | done | |
| R10 | Frontend: Roles screen | R6, R9 | done (i18n open) | |
| R11 | Frontend: role picker on Users | R7, R10 | done | |
| R12 | Tests | alongside each task | todo | |
| R13 | Clean-up (old enum, JWT claim) | all above | todo | |
| R14 | Folder granted to one user | R4 | done | |
| R16 | Audit coverage for upload/view/search/chat | — | done (1 open item) | |
| R15 | Docs + scope change note | all above | todo | |

Suggested split: lead builds **R1–R6** (model, security, backend guards). Junior takes **R7–R11** (API wiring + UI) and **R15**. R12 is done by whoever does the task.

---

## R0 — Decisions (settle before R1)

Defaults in **bold** — used if nobody objects.

- [x] D1 Offer the six old roles as copy-able templates? **DECIDED 2026-09-25: Yes** (keeps SoW T50 personas one click away).
- [x] D2 Can Admin grant `users.manage` to others? **DECIDED 2026-09-25: Yes, with the escalation guard** (README rule 6).
- [x] D3 Folder granted directly to one user? **DECIDED 2026-09-25: Yes, in this build** — R14 is now in scope.
- [x] D4 Upload / view / search / chat as permissions? **DECIDED 2026-09-25: No — but every one must be audit-logged.** Already logged today: `document.create` (`document_service.py:134`), `document.view` (`:346`), `search.query` (`search_service.py:1117,1173,1422`), `chat.message` (`chat_service`). R16 closes the gaps.
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
- [ ] Extend `load_live_role` (`deps.py`, already runs once per request) to LEFT JOIN `iam_dg_roles` and return role_id, name, is_system, all_departments, permissions; `get_current_user` copies them onto `TokenPayload` (new optional fields). No extra query, no cache needed.
- [ ] `require_permission(key)` in `deps.py`: 403 if no role or key not granted (`is_system` = all). Unknown key at import time → raise (catches typos).
- [ ] R4 reuses the same loaded fields.

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

## R14 — Folder granted to one user

In scope (D3). New table `iam_dg_user_folders (user_id, folder_id)`; `list_user_scope_folder_ids` unions it in. RLS policies unchanged (they read `app.scope_folder_ids`).

## R16 — Audit coverage for upload / view / search / chat

D4: these stay open to every user, so the audit log is the control.

- [ ] Confirm each path writes `log_action`: single upload, bulk upload (`documents.py:33`), connector ingests (SFTP / watched folder / email — actor = connector user), document detail, page image / preview / download (presigned URL), review screen open, export download, search (all 3 paths), chat message.
- [ ] Add `log_action` where missing. Details: document id, folder id; for search/chat the query text is already stored — keep it.
- [ ] Admin can read these in the existing audit screen (filter by user + action).

**Done when:** a table in this task lists every path → action name → file:line, none missing.

**Result (2026-09-25):**

| Path | Action logged | Where |
|---|---|---|
| Single upload | `document.create` | `document_service.upload_document` |
| Bulk upload | `document.create` (per file) | same function, called from `document_service.py:167` |
| Connectors (SFTP / watched folder / email) | `document.create` | `connector_ingest_service.py:143` → same function |
| Document detail (+ its download link) | `document.view` | `document_service.get_document` |
| Review screen open | `review.open` | **added** `api/v1/review.py` `get_review` |
| Search (3 paths) | `search.query` | `search_service.py:1117,1173,1422` |
| Chat message | `chat.message` | `chat_service` |
| Entity export / summary report | yes | `export_service.py:230`, `report_service.py:151` |
| Review page image | — (covered by `review.open`) | `api/v1/review.py` page image |
| Fact source view | — (fact belongs to a viewed document) | `fact_service.get_fact_with_regions` |

**Open item:** `GET /documents` (list) returns a presigned download link for every row (`document_service.list_documents`), so a file can be fetched without a `document.view` entry. Logging every list row would flood the log. Options: drop the URL from the list and fetch it via detail (logged), or log one `document.list` per request. Needs a decision.

## R15 — Docs + scope change note

- [ ] Update `docs/review-screen.md` (roles line), `docs/BACKEND_ENDPOINTS.md`, this folder.
- [ ] One-page change note for product owner: SoW T50 six personas → admin-defined roles (D5).
