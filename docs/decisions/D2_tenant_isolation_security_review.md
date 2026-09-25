# D-2 — Tenant isolation security review

**Backlog item:** "Ratify or change SaaS tenant isolation, row-level versus schema-per-tenant. The code already committed to row-level; open item 3 asks for a security review that has not happened." Owner: Tech Lead + Security.

This is that review. Conducted 2026-09-02 by directly querying the real running database and reading the real code — not just reading migration comments and assuming they reflect reality, which turned out to matter a great deal (see Finding 1).

## Summary

The row-level-isolation architecture decision is sound. Two classes of problem were found:

1. **An actual, exploitable cross-tenant data leak in the entity graph API** (Finding 4) — found, and (unlike the RLS findings, which are an infrastructure/ops decision for the Tech Lead) **fixed and live-tested the same day**, since this was a real active vulnerability, not an architecture question needing sign-off first.
2. **The database-level enforcement layer (Postgres Row-Level Security) is completely non-functional in this deployment**, for two independent, compounding reasons (Findings 1-2). Real tenant isolation today depends **entirely** on manually-written `tenant_id` checks in application code, with no safety net if one is ever missed or written wrong — which has already happened three times for real now: `cleanup_expired_trashed_items` (found and fixed 2026-08-27, deleted a different tenant's folder during a live test), and the two entity-edges bugs found and fixed in this review.

Every *other* application-level check audited (33 single-object lookups, all other bulk queries against Document/Folder/Fact/Record) was found correct.

## Finding 1 (critical): RLS policies exist but are silently bypassed — the app connects as a Postgres superuser

`0003_enable_rls.py` and five later migrations (`0010`, `0014`, `0018`, `0019`, `0022`) carefully built out Row-Level Security across the schema as new tenant-scoped tables were added. Confirmed live against the real database: **17 tables have a real, correctly-written `tenant_isolation_policy`**, e.g.:

```sql
-- doc_dg_documents
(tenant_id = (NULLIF(current_setting('app.current_tenant_id', true), ''))::uuid)
```

This looks like exactly the right defense-in-depth design. It does nothing, because:

```sql
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
--   rolname  | rolsuper | rolbypassrls
-- -----------+----------+--------------
--  docsearch | t        | t
```

The application's database role (`docsearch`, from `POSTGRES_USER` in `docker-compose.yml`) is a genuine Postgres **superuser**, and superusers unconditionally bypass RLS — `FORCE ROW LEVEL SECURITY` (which every one of these policies correctly uses) still doesn't apply to an actual superuser, only to non-superuser table owners. This isn't a deliberate dev-mode shortcut with a documented production plan — no restricted role is defined anywhere in the codebase (checked all migrations and `docker-compose.yml`). It's what the official `postgres` Docker image does automatically when you set `POSTGRES_USER`, and nobody has since created and switched to a properly restricted role.

**Live-verified the fix actually works**, with a throwaway non-superuser test role (created, tested, dropped — no permanent change made):

```
Total documents, connected as the superuser (today's real behavior): 1374
Same query, non-superuser role, no tenant context set:                  0   (correct default-deny)
Same query, non-superuser role, tenant context = a real tenant:        35   (exactly matches that tenant's real document count)
```

RLS works exactly as designed the moment the connection isn't a superuser. This is a config fix, not a redesign.

## Finding 2 (critical, compounding): even where RLS is "enabled," no API route ever sets the tenant context it depends on

RLS decides what to allow based on the Postgres session setting `app.current_tenant_id`. Three different functions exist in this codebase to open a DB session:

- `database.py::get_db()` — the plain version. **Never sets `app.current_tenant_id`.**
- `database.py::get_db_with_tenant(tenant_id=None)` — sets it only if a tenant_id is passed in. **Defined, never called anywhere in the codebase.**
- `deps.py::get_tenant_db()` — requires an authenticated user and correctly derives tenant_id from the JWT. **Defined, never called anywhere in the codebase.**

Every one of the 15 route files in `backend/app/api/v1/` uses plain `Depends(get_db)`. Grepped for `get_tenant_db` usage across every API route: **zero matches.** So even if Finding 1 were fixed today by switching to a non-superuser role, every authenticated API request would suddenly see **zero rows** on any RLS-protected table (the correct, fail-safe default-deny behavior of RLS with no context set — data would stop being visible, not leak) rather than working correctly, until every route is confirmed to actually establish tenant context.

`backend/app/tasks/worker.py` (the Celery ingestion pipeline) does call `set_config('app.current_tenant_id', ...)` directly — but only in 2 of its several tenant-touching code paths (the main ingestion transaction and its failure-handling block), not consistently across every function that touches tenant data there either.

**Net effect: RLS, as built, is not wired into any real request-handling path.** It's correct, tested-as-working infrastructure sitting completely disconnected from the application.

## Finding 3: `iam_dg_users` and `billing_dg_subscription` have a `tenant_id` column but no RLS policy at all

Separate from Findings 1-2 (which affect tables that DO have policies), these two tables were never given a `tenant_isolation_policy` in the first place, confirmed via `pg_class.relrowsecurity = false`. `billing_dg_subscription` looks like a straightforward oversight — same treatment as the other 17 tables would be enough.

`iam_dg_users` is more nuanced and needs a real design decision, not a blind policy add: the login flow (`POST /auth/login`) must look a user up **by email alone**, before any tenant is known — that's structurally incompatible with a per-request RLS policy scoped to `app.current_tenant_id`, since there is no tenant context to set yet at that point in the flow. A correct fix likely needs a dedicated, narrowly-scoped path for that one pre-authentication lookup (e.g. a separate role/connection with a policy that only allows `SELECT ... WHERE email = current_setting('app.login_lookup_email')`, or accepting that this one query is a deliberate, audited RLS exception) rather than a blanket policy that would either break login or need to be bypassed the same way the current superuser role bypasses everything.

## Finding 4: application-level `tenant_id` checks (the layer everything currently depends on)

Audited directly:

**Single-object lookups (`await db.get(Model, id)`), 33 call sites across `backend/app/services/`:** every call site touching tenant-owned data (`Fact`, `Document`, `Folder`, `EntityEdge`, `EntityNode`, `Record`, `Department`) correctly follows with `if not X or X.tenant_id != tenant_id: raise HTTPException(404)`. Call sites without a tenant check (`Template`, `TableShapeDecision`, `FieldTrustSignal`, `OCRArchive`, `VLMArchive`, a subscription looked up by the caller's own tenant_id) are legitimately global or content-addressed resources with no tenant_id column to check — not gaps.

**Bulk queries (`select(...)` against tenant-owned models):**

**One confirmed, exploitable finding — found and fixed 2026-09-02.** `entity_graph_service.create_edge()` (`backend/app/services/entity_graph_service.py`) accepted `source_node_id`, `target_node_id`, `target_fact_id`, `evidence_fact_id` as raw UUIDs and never checked any of them belonged to the caller's `tenant_id` before inserting the edge — exposed directly as `POST /api/v1/entities/edges` (`backend/app/api/v1/entities.py`), which passed the request body's UUID fields straight through with no validation. Downstream, `entity_360_service.get_entity_360_view()` fetched the *edges* touching a node with a correct `tenant_id` filter, but then resolved whatever entities/facts/documents those edges *point at* with none at all:

```python
res = await db.execute(select(EntityNode).where(EntityNode.id.in_(other_node_ids)))
res = await db.execute(select(Fact).where(Fact.id.in_(fact_ids)))
res2 = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
```

**Concrete exploit** (both stages verified in the code path, not theoretical): a user in Tenant A calls `POST /api/v1/entities/edges` on their own entity node, setting `target_fact_id` to a Fact ID belonging to Tenant B (a UUID they've guessed, enumerated, or obtained some other way). No error — the edge was created. Viewing that node's Entity 360 view (`GET /api/v1/entities/{node_id}`) then fetched and returned Tenant B's fact content and source document metadata to a Tenant A user.

**Fixed on both sides**, same `if not X or X.tenant_id != tenant_id: raise HTTPException(404)` pattern already used correctly everywhere else in the codebase:
- Write side: `create_edge()` now verifies `source_node_id`, `target_node_id`/`target_fact_id` (whichever `target_type` requires), and `evidence_fact_id` (if provided) each resolve to a row owned by the caller's tenant before the edge is created.
- Read side: `get_entity_360_view()`'s node/fact/document lookups now filter by `tenant_id` too — real defense in depth, since a pre-existing bad edge (or any future write path with the same class of bug) must still be caught here independently of the write-side fix.

**Live-verified**: 6 new regression tests (`backend/tests/test_entity_graph_service.py`, `backend/tests/test_entity_360_service.py`) — 4 proving `create_edge()` now rejects every cross-tenant ID (`target_fact_id`, `target_node_id`, `evidence_fact_id`, `source_node_id`), 1 proving the read side never returns another tenant's fact content even when a bad edge is inserted directly (bypassing `create_edge()` entirely, to test the read-side fix independently). Confirmed the read-side test genuinely catches the regression by temporarily reverting the fix and watching it fail, then restoring it. Full backend suite green after the fix, no regressions.

**Checked and confirmed clean** (every other bulk `select()` against Document/Folder/Fact/Record found in `backend/app/services/` and `backend/app/tasks/worker.py`): `document_service.py` (list/get/update, all paths), `folder_service.py` (list/get/rename), `fact_service.py` (fact + source document lookup for the click-through viewer), `classification_service.py` (unclassified queue), `chat_service.py` (citation resolution), `connector_ingest_service.py` (folder-path resolution), `certificate_service.py`. `worker.py`'s three internal `select(Document).where(Document.id == document_id)` lookups take `document_id` from the Celery task's own queued arguments — set by the same upload flow that already validated tenant ownership when the document was created — not from an external caller at that point, so they're a different (and here, safe) trust boundary than the entities-API finding above.

## Recommendation (not a substitute for the actual sign-off this item asks for)

**Finding 4 (entity-edges cross-tenant leak) is fixed, tested, and live-verified** — see above. That one was a real, live, active bug, not an architecture question needing sign-off first, so it didn't wait for this review's conclusion the way the RLS findings below should.

**For the RLS gap (Findings 1-3), two independent, both-necessary fixes** to make the already-built RLS layer actually functional — these are infrastructure/ops decisions genuinely worth the Tech Lead + Security sign-off this D-2 item asks for, not applied here:

1. Create a real, restricted Postgres role for the application (`NOSUPERUSER NOBYPASSRLS`, granted only the DML it needs) and switch `POSTGRES_URL`/`docker-compose.yml` to use it instead of `docsearch`.
2. Wire tenant-context-setting into the actual request path every route uses — either make plain `get_db()` itself set it (deriving tenant_id from the authenticated request context it already has access to at call time) or replace all 15 route files' `Depends(get_db)` with `Depends(get_tenant_db)`, and fill the same gap in `worker.py`'s remaining tenant-touching code paths.
3. Add a `tenant_isolation_policy` to `billing_dg_subscription` (straightforward); design the narrower fix `iam_dg_users` needs for pre-authentication email lookup (not straightforward — needs its own decision, see Finding 3).

Until 1-2 land, RLS provides **zero actual protection** in this deployment — it's real, tested, correctly-designed infrastructure that isn't connected to anything. Every other application-level `tenant_id` check audited (single-object lookups and bulk queries, both) was found correct — the entity-edges bug is the one exception, not representative of the codebase's general standard here.
