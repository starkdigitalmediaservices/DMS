"use client";
import { useEffect, useState } from "react";
import { getUserProfile, PROFILE_UPDATED_EVENT } from "./auth";

// What the signed-in user may do in the UI. Custom roles (docs/features/
// custom-roles/, R9): the server sends the live permission list on every
// /auth/me (`permissions`, `is_admin`), so the UI no longer carries its own
// role table. The server is still the enforcer (it answers 403); this only
// hides or disables what the role can't do instead of letting users click
// into a 403.

export type Action =
  | "facts.review"
  | "documents.classify"
  | "records.create"
  | "entities.edit"
  | "corpus.calibrate"
  | "review.read"
  | "review.edit"
  | "review.verify"
  | "review.revertAll"
  | "content.deletePermanent"
  | "certificate.section63"
  | "export.report"
  | "audit.integrity"
  | "analytics.view"
  | "users.manage"
  | "roles.manage"
  | "departments.manage"
  | "templates.manage"
  | "config.manage"
  | "license.manage"
  | "billing.view";

// ── Legacy personas ──────────────────────────────────────────────────────
// Only for (a) a profile cached before custom roles, which has no
// `permissions` field until the next /auth/me refreshes it, and (b) showing
// an old persona string as a readable label. Removed with the enum (R13).

export const ROLES = [
  "records_officer",
  "operator",
  "department_head",
  "legal_counsel",
  "it_admin",
  "auditor",
] as const;

export type Role = (typeof ROLES)[number];

const REVIEWERS: Role[] = ["records_officer", "operator", "it_admin"];

const LEGACY_MATRIX: Partial<Record<Action, readonly Role[]>> = {
  "facts.review": REVIEWERS,
  "documents.classify": REVIEWERS,
  "records.create": REVIEWERS,
  "entities.edit": REVIEWERS,
  "corpus.calibrate": REVIEWERS,
  "review.read": ROLES,
  "review.edit": REVIEWERS,
  "review.verify": ["records_officer", "it_admin"],
  "review.revertAll": ["it_admin"],
  "content.deletePermanent": ["records_officer", "department_head", "it_admin"],
  "certificate.section63": ["records_officer", "department_head", "legal_counsel", "it_admin", "auditor"],
  "export.report": ["records_officer", "operator", "legal_counsel", "it_admin", "auditor"],
  "audit.integrity": ["auditor", "it_admin"],
  "billing.view": ["it_admin", "department_head", "auditor"],
};

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
  it_admin: "Admin",
  auditor: "Auditor",
};

/** A readable name for an old persona string. Prefer a server `role_name`. */
export function roleLabel(role: string | null | undefined): string {
  const r = displayRole(role);
  if (r) return ROLE_LABELS[r];
  return role ? role.replace(/_/g, " ") : "—";
}

// ── The check ────────────────────────────────────────────────────────────

type Profile = {
  role?: string | null;
  role_name?: string | null;
  is_admin?: boolean;
  all_departments?: boolean;
  permissions?: string[];
  user_id?: string;
  id?: string;
} | null;

/** True when the profile may perform `action`. No profile → false. */
export function canWith(profile: Profile, action: Action): boolean {
  if (!profile) return false;
  if (Array.isArray(profile.permissions)) {
    return !!profile.is_admin || profile.permissions.includes(action);
  }
  // Cached profile from before custom roles: fall back to the old table.
  const r = displayRole(profile.role);
  if (!r) return false;
  if (r === "it_admin") return true;
  return (LEGACY_MATRIX[action] ?? []).includes(r);
}

// A role an admin edits takes effect on the user's next request server-side;
// refreshing /auth/me when the tab regains focus makes the UI catch up
// without a reload. Throttled, and shared by every useRole() on the page.
const REFRESH_MIN_MS = 30_000;
let lastRefresh = 0;
let focusListeners = 0;
async function refreshProfile() {
  const now = Date.now();
  if (now - lastRefresh < REFRESH_MIN_MS || !getUserProfile()) return;
  lastRefresh = now;
  try {
    const { api } = await import("./api"); // lazy: api.ts imports auth.ts too
    await api.auth.getProfile(); // api.ts stores it and fires PROFILE_UPDATED_EVENT
  } catch (_) {
    /* offline or signed out — keep the cached profile */
  }
}
function onFocus() {
  void refreshProfile();
}

/** Current user's access, read from the cached /auth/me profile. Read after
 *  mount (localStorage doesn't exist during the server render — reading
 *  it in the render body causes a hydration mismatch), and re-read
 *  whenever the profile is refreshed. `ready` is false until the first
 *  client-side read, so callers can avoid flashing a "no access" state. */
export function useRole(): {
  role: string | null;
  roleName: string | null;
  isAdmin: boolean;
  allDepartments: boolean;
  userId: string | null;
  ready: boolean;
  can: (action: Action) => boolean;
} {
  const [profile, setProfile] = useState<Profile>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const read = () => {
      setProfile(getUserProfile());
      setReady(true);
    };
    read();
    window.addEventListener(PROFILE_UPDATED_EVENT, read);
    // Another tab logging in as someone else rewrites the same storage.
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === "user_profile") read();
    };
    window.addEventListener("storage", onStorage);
    if (focusListeners++ === 0) window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener(PROFILE_UPDATED_EVENT, read);
      window.removeEventListener("storage", onStorage);
      if (--focusListeners === 0) window.removeEventListener("focus", onFocus);
    };
  }, []);

  const role = profile?.role ?? null;
  return {
    role,
    roleName: profile?.role_name ?? (role ? roleLabel(role) : null),
    isAdmin: !!profile?.is_admin || (!Array.isArray(profile?.permissions) && displayRole(role) === "it_admin"),
    allDepartments: !!profile?.all_departments,
    // /auth/me returns user_id; older cached shapes used id.
    userId: profile?.user_id ?? profile?.id ?? null,
    ready,
    can: (action: Action) => canWith(profile, action),
  };
}
