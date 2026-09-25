# Manual Testing Guide — Table Stitching, Entity Graph, RBAC

Step-by-step guide for manually verifying these three features against a real running stack.
Last live-verified: 2026-08-28 (26/26 stitching tests pass, 11/11 entity graph checks pass).

---

## 1. Table Stitching (vertical continuation + horizontal spread join + ditto-chain)

**What it does:** Reconnects a table's rows when a scan splits it across a page boundary or a left/right spread, and expands shorthand "Do." (ditto) marks into their real repeated value.

### Step 1 — Run the automated test suite (fast sanity check, ~5 sec)
```
docker compose exec backend python -m pytest tests/test_table_stitch_extraction.py tests/test_table_stitch.py -q
```
**Pass condition:** all tests pass (26 at last count).

### Step 2 — Verify on a real document with a spread/continuation table
1. Log in to the app → open a document known to have a table split across pages or a left/right spread (e.g. a gazette-style register)
2. Open **Workbench → Join Mismatches** tab
   - **Pass condition:** if the system couldn't confidently join two fragments, it appears here with a stated reason (e.g. "no shared serial_village values between the two fragments") — this is correct behavior, not a bug. It must never silently guess.
3. Open the document itself and find a row that continues from the bottom of one page to the top of the next
   - **Pass condition:** the row reads as one continuous entry, not two broken fragments

### Step 3 — Verify ditto-chain expansion
1. Find a column in the original scan using "Do." (or similar shorthand) to indicate "same as above"
2. Check the extracted field value for those rows
   - **Pass condition:** every "Do." row shows the actual repeated value (e.g. "Muslim Panch Managing."), not blank and not literally "Do."

### Step 4 — Confirm via database (optional, for a technical audience)
```sql
SELECT field_name, COUNT(*), COUNT(DISTINCT value->>'v')
FROM doc_dg_facts
WHERE document_id = '<document_id>'
GROUP BY field_name;
```
**Pass condition:** row count matches the real number of rows in the source table, and distinct-value count makes sense (ditto-expanded columns will have fewer distinct values than total rows, structured-data columns should mostly be distinct).

---

## 2. Entity Graph (tiered linking, confirm/revert, audit trail)

**What it does:** Links entities (people, properties) with a confidence tier. Low-stakes links auto-verify; legally significant links always require explicit human confirmation, regardless of confidence score.

**Note:** entities are not created automatically from document processing today — they must be created explicitly (via API). There is no "browse all entities" UI, only a lookup-by-node-ID view at `/entities`.

### Step 1 — Run the automated health-check script (recommended, fully automated)
```
docker compose exec backend python3 scripts/check_entity_graph.py --email <email> --password '<password>'
```
This creates its own throwaway test nodes/edges, runs 11 checks, prints PASS/FAIL for each, and cleans up after itself automatically.

**Pass condition:** `11/11 checks passed` / `Entity graph is healthy.`

**What it checks:**
1. Login succeeds
2. Two entity nodes can be created
3. A tier-1 edge auto-verifies immediately (`status: "machine"`)
4. A tier-4 (legal) edge stays `"held"` even at 0.99 confidence — never auto-verifies
5. The Entity 360 view (`/entities` page) shows both linked edges correctly
6. Confirming a held edge succeeds → `status: "verified"`
7. Guard rail: confirming an already-machine edge is blocked (409)
8. Guard rail: reverting a machine edge is blocked (409)
9. Guard rail: double-confirming a verified edge is blocked (409)
10. Reverting a verified edge succeeds → back to `"held"`
11. Test data is cleaned up from the database automatically

### Step 2 — Manual UI check (visual confirmation)
1. Note the `node1_id` printed if you run the underlying API calls manually (or use a real node ID if you have one), or create one via:
   ```
   POST /api/v1/entities  { "entity_type": "person", "label": "Test Person" }
   ```
2. In the browser, go to `/entities` → paste the node ID → click **Load**
3. **Pass condition:** page shows the entity's label/type, its Records, Linked entities (with tier + confidence %), and Linked facts, each with a working "source" click-through to the original document region.

### Step 3 — Audit trail check
```sql
SELECT action, resource_type, created_at
FROM audit_dg_logs
WHERE action LIKE 'entity%'
ORDER BY created_at DESC LIMIT 10;
```
**Pass condition:** every create/confirm/revert action from Steps 1–2 appears with a timestamp and actor.

---

## 3. RBAC (Role-Based Access Control)

**What it does:** Six personas — records officer, operator/adjudicator, department head, legal counsel, IT admin, external auditor — each restricted to their intended scope of actions.

### Step 1 — Confirm roles exist and are enforced at login
1. Check which roles exist in the system:
   ```sql
   SELECT DISTINCT role FROM iam_dg_users;
   ```
2. Log in as a user with a non-admin role (e.g. `operator`)
3. Attempt an admin-only action (e.g. user management, or an endpoint requiring `it_admin`)
   - **Pass condition:** the action is rejected (403 Forbidden), not silently allowed

### Step 2 — Confirm role-appropriate access works
1. Log in as each role you have test credentials for
2. For each, confirm:
   - **Operator/Adjudicator** — can access Workbench, claim/resolve review items
   - **Records Officer** — can upload, edit, view documents in their department scope
   - **External Auditor** — can view documents/audit logs, but cannot edit or delete
   - **IT Admin** — can access admin/user-management screens
   - **Legal Counsel** — can view legal-status fields, confirm tier-4 entity links
   - **Department Head** — sees only their department's folder scope, not other departments'

### Step 3 — Cross-department isolation check
1. As a records officer scoped to Department A, attempt to open a document that belongs to Department B
   - **Pass condition:** access is denied or the document doesn't appear in search/listing results

### Step 4 — Audit trail check
```sql
SELECT actor_id, action, resource_type, created_at
FROM audit_dg_logs
ORDER BY created_at DESC LIMIT 20;
```
**Pass condition:** every action taken above (including denied attempts, if logged) shows the correct actor and role context.

---

## Quick reference — what "PASS" looks like at a glance

| Feature | Fastest pass/fail signal |
|---|---|
| Table stitching | `pytest` suite green + Join Mismatches tab shows real flagged cases, not silent failures |
| Entity graph | `check_entity_graph.py` → `11/11 checks passed` |
| RBAC | A non-admin role gets a real 403 on an admin action; each role sees only its intended scope |
