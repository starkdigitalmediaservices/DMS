"""Custom roles (docs/features/custom-roles/) — the fixed permission catalogue.

A tenant's Admin builds roles by ticking keys from PERMISSIONS; nothing
else can be granted, because a permission only means something where code
checks it with deps.require_permission(key). Adding a key here without a
check that uses it grants nothing.

Keys match the frontend's `Action` names (frontend/lib/permissions.ts) so
the UI and the server agree on one vocabulary.

Upload, view, search and chat are deliberately NOT permissions (decision
D4): every user may, department scope decides which documents, and each
action is audit-logged instead.
"""
from typing import Any, Dict, List, NamedTuple, Tuple


class Permission(NamedTuple):
    key: str
    group: str
    label: str


PERMISSIONS: List[Permission] = [
    # Review
    Permission("facts.review", "Review", "Confirm, correct and bulk-edit extracted values"),
    Permission("documents.classify", "Review", "Classify documents"),
    Permission("records.create", "Review", "Create records"),
    Permission("entities.edit", "Review", "Edit the entity graph"),
    Permission("corpus.calibrate", "Review", "Calibrate confidence on a document set"),
    Permission("review.read", "Review", "Open the review screen"),
    Permission("review.edit", "Review", "Edit values on the review screen"),
    Permission("review.verify", "Review", "Mark rows as checked"),
    Permission("review.revertAll", "Review", "Undo all changes on a document"),
    # Documents
    Permission("content.deletePermanent", "Documents", "Permanently delete documents and folders"),
    # Reports
    Permission("export.report", "Reports", "Export entity reports"),
    Permission("certificate.section63", "Reports", "Generate Section 63 certificates"),
    Permission("audit.integrity", "Reports", "Check audit-log integrity"),
    Permission("analytics.view", "Reports", "View usage analytics"),
    # Administration
    Permission("users.manage", "Administration", "Add and edit users"),
    Permission("roles.manage", "Administration", "Create and edit roles"),
    Permission("departments.manage", "Administration", "Manage departments and folder access"),
    Permission("templates.manage", "Administration", "Manage extraction templates"),
    Permission("config.manage", "Administration", "Change system settings"),
    Permission("license.manage", "Administration", "Manage the licence"),
    Permission("billing.view", "Administration", "View subscription and billing"),
]

PERMISSION_KEYS = frozenset(p.key for p in PERMISSIONS)
assert len(PERMISSION_KEYS) == len(PERMISSIONS), "duplicate permission key"


class RoleTemplate(NamedTuple):
    name: str
    all_departments: bool
    permissions: List[str]


_REVIEWER = [
    "facts.review", "documents.classify", "records.create", "entities.edit",
    "corpus.calibrate", "review.read", "review.edit",
]

# "Start from template" (decision D1): the six T50 personas as they were
# gated at f3a7ba1. Migration 0056 holds a frozen copy of the same table
# for its backfill; this is the live one the Roles screen offers. IT Admin
# has no template -- its equivalent is the tenant's locked Admin role.
ROLE_TEMPLATES: Dict[str, RoleTemplate] = {
    "records_officer": RoleTemplate("Records Officer", False, _REVIEWER + [
        "review.verify", "content.deletePermanent", "export.report", "certificate.section63",
    ]),
    "operator": RoleTemplate("Operator", False, _REVIEWER + ["export.report"]),
    "department_head": RoleTemplate("Department Head", False, [
        "review.read", "content.deletePermanent", "certificate.section63", "billing.view",
    ]),
    "legal_counsel": RoleTemplate("Legal Counsel", True, [
        "review.read", "export.report", "certificate.section63",
    ]),
    "auditor": RoleTemplate("Auditor", True, [
        "review.read", "export.report", "certificate.section63", "audit.integrity", "billing.view",
    ]),
}

for _t in ROLE_TEMPLATES.values():
    _unknown = set(_t.permissions) - PERMISSION_KEYS
    assert not _unknown, f"template {_t.name} names unknown permissions {_unknown}"


def check_key(key: str) -> str:
    """Raise at import time on a typo, instead of a gate that silently
    denies everyone (or, worse, a check nobody notices never matches)."""
    if key not in PERMISSION_KEYS:
        raise ValueError(f"unknown permission key {key!r}")
    return key


def role_grants(is_system: bool, permissions: List[str] | None, key: str) -> bool:
    """The one rule: the locked Admin role holds everything; any other
    role holds exactly what is ticked; no role holds nothing."""
    if is_system:
        return True
    return key in (permissions or ())


_LEGACY_ALIASES = {"user": "operator"}


def legacy_access(persona: str | None) -> Tuple[bool, bool, List[str], str | None]:
    """(is_admin, all_departments, permissions, role name) for an old T50
    persona string. Bridges users who have no role_id yet -- new signups
    until R5, users made on the admin screen until R7 -- so switching the
    gates over (R3) changes nobody's access. Removed with the enum in R13.
    Unknown persona -> nothing."""
    p = _LEGACY_ALIASES.get(persona or "", persona or "")
    if p in ("it_admin", "admin"):
        return True, True, [], "Admin"
    t = ROLE_TEMPLATES.get(p)
    if t is None:
        return False, False, [], None
    return False, t.all_departments, list(t.permissions), t.name


def grants(access: Any, key: str) -> bool:
    """`access` is a TokenPayload (live role loaded by deps.get_current_user)
    or, from older call sites and tests, a bare persona string."""
    if isinstance(access, str):
        is_admin, _, perms, _ = legacy_access(access)
        return role_grants(is_admin, perms, key)
    return role_grants(bool(getattr(access, "is_admin", False)), getattr(access, "permissions", None), key)


def sees_all_departments(access: Any) -> bool:
    """Whether department scope (migration 0053) is lifted for this caller.
    Same input rule as grants(). Anything unrecognised is scoped: fails closed."""
    if isinstance(access, str):
        return legacy_access(access)[1]
    return bool(getattr(access, "all_departments", False))
