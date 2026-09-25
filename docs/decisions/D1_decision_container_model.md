# Decision D-1 — Container model: nested folders vs. two-level Workspace → Project

Status: **signed — 2026-08-24**
Blocks (now unblocked): T10 (entity graph schema), T50 (RBAC for six personas), T94, and everything that cascades from them (T56–T59, T60–T62 records, and indirectly Governance/Export/Reports once their own gates — D-7, D-8, A3 — are separately resolved)
Owner: signed off by the project decision-maker on 2026-08-24
Reference: Build Design v0.3 §3.10; Scope Gap §12 item 3; docs/planning/backlog.txt T10 editorial note ("Settle decision D-1 first")

---

## What was being decided

The original Scope of Work (§3.10) specifies a flat two-level container model: Workspace → Project, with no deeper nesting. The code already implements arbitrary-depth recursive folders (`doc_dg_folders.parent_id` self-referential FK, unbounded nesting), built and in active demo use.

Two options were on the table:
1. Retrofit the existing folder system down to a strict two-level model, matching the original SoW exactly.
2. Keep the working recursive folder structure and record the difference as a change request rather than a defect.

## Decision

**Keep nested folders.** The existing recursive folder structure is recorded as an accepted change from the original two-level Workspace → Project spec, not something to be retrofitted or removed.

## Why

- The nested folder system is already built, tested, and has been used in live client demos this session — collapsing it to two levels would be a real regression to working, demo-critical functionality for no functional gain.
- Retrofitting a stricter container model this late would touch a broad surface (folder APIs, the Drive UI, existing customer data shape) for a change whose only justification is spec conformance, not a reported problem.

## What this unblocks

- **T10** — entity graph schema (`entity_dg_nodes`/`entity_dg_edges`) can now proceed; entity/document scoping follows the existing folder hierarchy rather than a new two-level container concept.
- **T50** — RBAC for six personas; department scope is defined as an RBAC group over projects/folders, not a container depth level.
- **T94** and anything else in the backlog referencing D-1.

## What this does NOT resolve

- D-9 (whether the Drive product itself — folder tree, star, trash, chat, offline shell — is in scope as an accepted addition, or formally out of scope) is a related but separate open item, not decided here.
- This decision does not change any existing folder API behavior — it only removes the ambiguity blocking T10/T50 from starting.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree — keep nested folders, record as accepted change | 2026-08-24 |

Signed. T10 and T50 are unblocked and can proceed.
