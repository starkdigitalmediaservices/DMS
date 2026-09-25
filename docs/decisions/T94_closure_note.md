# T94 — Closure note: resolved by D-1, no container level built

Status: **closed — 2026-08-25, no code change required**
Reference: docs/planning/backlog.txt T94 ("Project / Collection container level — does not exist today", depends on D-1); [[docs/decisions/D1_decision_container_model.md]]; backend/app/models/department.py

---

## What T94 originally asked for

Backlog T94: *"Project / Collection container level — does not exist today."* Its only dependency is D-1.

## Why it's closed, not built

D-1 (signed 2026-08-24) decided **against** a fixed two-level Workspace → Project container, keeping the existing arbitrary-depth recursive folder hierarchy instead. D-1's own text settles what T94 would have delivered:

> "**T50** — RBAC for six personas; department scope is defined as an RBAC group over projects/folders, not a container depth level."

That is not a future intention — it is already built. `backend/app/models/department.py` (T50) implements exactly this: `Department` (a named group of users) and `DepartmentFolder` (one project/folder a department is granted scope over, independent of where that folder sits in the tree), with `department_service.py` providing `grant_department_folder()` / `user_has_folder_scope()` on top of the existing folder hierarchy. Its own docstring quotes D-1 directly.

So "project/collection scoping" — the actual capability behind T94's one-line description — already exists, delivered through T50 over the existing folder tree rather than through a new container abstraction. Building a separate two-level container now would reintroduce exactly the regression D-1 rejected (retrofitting a stricter model onto working, demo-tested recursive folders) for a capability that's already served.

## Decision

Close T94 with no new code. The backlog line is satisfied by D-1 + T50, not by a new artifact.

## What would reopen this

If a future requirement needs a "Project" as a first-class object distinct from a folder — e.g. project-level metadata, a dedicated dashboard, or reporting that doesn't map cleanly onto "a folder some department has scope over" — that would be a new, explicit ask, not a continuation of T94's original one-line description, and would need its own decision written down before building.
