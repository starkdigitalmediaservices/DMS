"use client";
import { useEffect, useState } from "react";
import { getUserProfile, PROFILE_UPDATED_EVENT } from "./auth";

// Single source of truth for what each persona may do in the UI. Mirrors
// the backend's require_role(...) gates exactly — the server is still the
// enforcer (it answers 403), this only exists so the UI hides/disables
// what a role can't do instead of letting users click into a 403. When a
// backend gate changes, change it here and nowhere else.

export const ROLES = [
  "records_officer",
  "operator",
  "department_head",
  "legal_counsel",
  "it_admin",
  "auditor",
] as const;

export type Role = (typeof ROLES)[number];

export type Action =
  // Fact review / adjudication (confirm, claim, release, bulk-edit,
  // bulk-confirm, mark-handwritten, resolve-stitch)
  | "facts.review"
  // Document classify & dismiss-classification
  | "documents.classify"
  | "records.create"
  // Entity-graph edits: create/delete node, create/delete/confirm/revert edge, bulk
  | "entities.edit"
  | "corpus.calibrate"
  // Permanent DELETE of a document or folder
  | "content.deletePermanent"
  | "certificate.section63"
  | "export.report"
  | "audit.integrity"
  | "billing.view"
  | "license.manage"
  | "templates.manage"
  | "departments.manage"
  | "users.manage"
  | "config.manage"
  | "analytics.view";

const REVIEWERS: Role[] = ["records_officer", "operator", "it_admin"];

const MATRIX: Record<Action, readonly Role[]> = {
  "facts.review": REVIEWERS,
  "documents.classify": REVIEWERS,
  "records.create": REVIEWERS,
  "entities.edit": REVIEWERS,
  "corpus.calibrate": REVIEWERS,
  "content.deletePermanent": ["records_officer", "department_head", "it_admin"],
  "certificate.section63": ["records_officer", "department_head", "legal_counsel", "it_admin", "auditor"],
  "export.report": ["records_officer", "operator", "legal_counsel", "it_admin", "auditor"],
  "audit.integrity": ["auditor", "it_admin"],
  "billing.view": ["it_admin", "department_head", "auditor"],
  "license.manage": ["it_admin"],
  "templates.manage": ["it_admin"],
  "departments.manage": ["it_admin"],
  "users.manage": ["it_admin"],
  "config.manage": ["it_admin"],
  "analytics.view": ["it_admin"],
};

/** True when `role` may perform `action`. Unknown/missing role → false.
 *  Legacy `admin`/`user` values are deliberately NOT mapped here: the
 *  server's require_role() checks the raw role string, so granting them
 *  UI affordances would just lead to 403s. They are mapped for display
 *  only (see roleLabel). */
export function can(role: string | null | undefined, action: Action): boolean {
  if (!role) return false;
  return (MATRIX[action] as readonly string[]).includes(role);
}

/** Maps legacy values onto the persona they correspond to, for display. */
export function displayRole(role: string | null | undefined): Role | null {
  if (!role) return null;
  if (role === "admin") return "it_admin";
  if (role === "user") return "operator";
  return (ROLES as readonly string[]).includes(role) ? (role as Role) : null;
}

export const ROLE_LABELS: Record<Role, string> = {
  records_officer: "Records Officer",
  operator: "Operator",
  department_head: "Department Head",
  legal_counsel: "Legal Counsel",
  it_admin: "IT Admin",
  auditor: "Auditor",
};

export function roleLabel(role: string | null | undefined): string {
  const r = displayRole(role);
  if (r) return ROLE_LABELS[r];
  return role ? role.replace(/_/g, " ") : "—";
}

/** Current user's role, read from the cached /auth/me profile. Read after
 *  mount (localStorage doesn't exist during the server render — reading
 *  it in the render body causes a hydration mismatch), and re-read
 *  whenever the profile is refreshed, so a role change picked up by any
 *  /auth/me call propagates without a reload. `ready` is false until the
 *  first client-side read, so callers can avoid flashing a "no access"
 *  state before the role is known. */
export function useRole(): {
  role: string | null;
  userId: string | null;
  ready: boolean;
  can: (action: Action) => boolean;
} {
  const [role, setRole] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const read = () => {
      const profile = getUserProfile();
      setRole(profile?.role ?? null);
      // /auth/me returns user_id; older cached shapes used id.
      setUserId(profile?.user_id ?? profile?.id ?? null);
      setReady(true);
    };
    read();
    window.addEventListener(PROFILE_UPDATED_EVENT, read);
    // Another tab logging in as someone else rewrites the same storage.
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === "user_profile") read();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(PROFILE_UPDATED_EVENT, read);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return { role, userId, ready, can: (action: Action) => can(role, action) };
}
