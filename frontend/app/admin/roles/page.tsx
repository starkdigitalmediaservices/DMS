"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ShieldCheck,
  Plus,
  Pencil,
  Trash2,
  Loader2,
  AlertCircle,
  Check,
  X,
  Lock,
  Globe2,
  UserCog,
} from "lucide-react";
import { api } from "@/lib/api";
import { useRole } from "@/lib/permissions";
import { Button } from "@/components/ui/Button";
import { NoAccess } from "@/components/common/NoAccess";
import type { AdminRole, PermissionGroup, RoleTemplate } from "@/types";

// Custom roles (docs/features/custom-roles/, R10). The server enforces every
// rule shown here (Admin locked, unique names, no deleting a role in use, a
// non-Admin can only grant what they hold); the screen just explains them.

interface Draft {
  id: string | null; // null = new role
  name: string;
  permissions: Set<string>;
  allDepartments: boolean;
}

const inputClass =
  "mt-1 w-full rounded-lg border border-[#c4c7c5] px-3 py-2 text-sm text-[#1f1f1f] bg-white focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40";

export default function RolesAdminPage() {
  const { can: roleCan, ready: roleReady, isAdmin } = useRole();
  const canManage = roleCan("roles.manage");
  const canSeeUsers = roleCan("users.manage");

  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [catalogue, setCatalogue] = useState<PermissionGroup[]>([]);
  const [templates, setTemplates] = useState<RoleTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const labelByKey = useMemo(() => {
    const m = new Map<string, string>();
    catalogue.forEach((g) => g.permissions.forEach((p) => m.set(p.key, p.label)));
    return m;
  }, [catalogue]);
  const totalPermissions = labelByKey.size;

  const loadAll = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    setError("");
    try {
      const [roleList, cat, tpl] = await Promise.all([api.roles.list(), api.roles.permissions(), api.roles.templates()]);
      setRoles(roleList);
      setCatalogue(cat);
      setTemplates(tpl);
    } catch (err: any) {
      setError(err.message || "Failed to load roles");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!roleReady) return;
    if (canManage) loadAll();
    else setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleReady, canManage]);

  const startNew = () => {
    setActionError("");
    setNotice("");
    setDraft({ id: null, name: "", permissions: new Set(), allDepartments: false });
  };

  const startEdit = (r: AdminRole) => {
    setActionError("");
    setNotice("");
    setDraft({ id: r.id, name: r.name, permissions: new Set(r.permissions), allDepartments: r.all_departments });
  };

  const applyTemplate = (key: string) => {
    const t = templates.find((x) => x.key === key);
    if (!t || !draft) return;
    setDraft({
      ...draft,
      name: draft.name.trim() ? draft.name : t.name,
      permissions: new Set(t.permissions),
      // Only an Admin may grant all departments; others keep it off.
      allDepartments: isAdmin ? t.all_departments : false,
    });
  };

  const toggle = (key: string) => {
    if (!draft) return;
    const next = new Set(draft.permissions);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setDraft({ ...draft, permissions: next });
  };

  const toggleGroup = (group: PermissionGroup, on: boolean) => {
    if (!draft) return;
    const next = new Set(draft.permissions);
    group.permissions.forEach((p) => (on ? next.add(p.key) : next.delete(p.key)));
    setDraft({ ...draft, permissions: next });
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    const name = draft.name.trim();
    if (!name) {
      setActionError("Give the role a name.");
      return;
    }
    setSaving(true);
    setActionError("");
    setNotice("");
    const body = { name, permissions: Array.from(draft.permissions).sort(), all_departments: draft.allDepartments };
    try {
      if (draft.id) await api.roles.update(draft.id, body);
      else await api.roles.create(body);
      setNotice(draft.id ? `Role "${name}" saved. It applies to its users from their next click.` : `Role "${name}" created.`);
      setDraft(null);
      await loadAll(false);
    } catch (err: any) {
      setActionError(err.message || "Failed to save role");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (r: AdminRole) => {
    if (!confirm(`Delete role "${r.name}"? This can't be undone.`)) return;
    setBusyId(r.id);
    setActionError("");
    setNotice("");
    try {
      await api.roles.delete(r.id);
      setNotice(`Role "${r.name}" deleted.`);
      if (draft?.id === r.id) setDraft(null);
      await loadAll(false);
    } catch (err: any) {
      setActionError(err.message || "Failed to delete role");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="h-16 px-6 flex items-center justify-between border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/admin"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Admin</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1]" />
          <h1 className="text-lg font-bold text-[#1f1f1f] flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-[#0d2e5c]" />
            Roles
          </h1>
        </div>
        {canManage && (
          <div className="flex items-center gap-2">
            {canSeeUsers && (
              <Link
                href="/admin/users"
                className="flex items-center gap-2 text-sm text-[#0d2e5c] font-semibold px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
              >
                <UserCog className="w-4 h-4" />
                <span>Users</span>
              </Link>
            )}
            <Button variant="primary" size="sm" onClick={startNew} aria-controls="role-editor">
              <Plus className="w-4 h-4 mr-2" aria-hidden="true" />
              New role
            </Button>
          </div>
        )}
      </header>

      <main className="max-w-[1100px] mx-auto p-6 md:p-8 space-y-6">
        {roleReady && !canManage ? (
          <NoAccess message="Your role doesn't include managing roles." />
        ) : (
          <>
            <p className="text-sm text-[#444746]">
              A role decides <strong>what</strong> someone can do. <strong>Which</strong> documents they see comes from
              their departments and folders shared with them — unless the role can see all departments. The Admin role
              is locked and always has everything.
              {!isAdmin && " You can only give out permissions your own role has."}
            </p>

            {actionError && (
              <div role="alert" className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
                <span className="flex-1">{actionError}</span>
                <button type="button" onClick={() => setActionError("")} aria-label="Dismiss error" className="p-1 rounded hover:bg-red-100">
                  <X className="w-4 h-4" aria-hidden="true" />
                </button>
              </div>
            )}
            {notice && (
              <div role="status" className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-green-50 border border-green-200 text-sm text-green-800">
                <Check className="w-4 h-4 shrink-0" aria-hidden="true" />
                {notice}
              </div>
            )}

            {draft && (
              <form
                id="role-editor"
                onSubmit={save}
                aria-labelledby="role-editor-title"
                className="bg-white border border-[#0d2e5c]/30 rounded-xl p-5 space-y-5"
              >
                <div className="flex items-start justify-between gap-4">
                  <h2 id="role-editor-title" className="font-bold text-[#1f1f1f]">
                    {draft.id ? `Edit role` : "New role"}
                  </h2>
                  <button type="button" onClick={() => setDraft(null)} aria-label="Close editor" className="p-1.5 rounded-lg hover:bg-[#f0f4f9]">
                    <X className="w-4 h-4" aria-hidden="true" />
                  </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="role-name" className="text-xs font-semibold text-[#444746]">
                      Role name
                    </label>
                    <input
                      id="role-name"
                      type="text"
                      required
                      maxLength={80}
                      className={inputClass}
                      placeholder="e.g. Accounts Clerk"
                      value={draft.name}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    />
                  </div>
                  {!draft.id && templates.length > 0 && (
                    <div>
                      <label htmlFor="role-template" className="text-xs font-semibold text-[#444746]">
                        Start from a template (optional)
                      </label>
                      <select
                        id="role-template"
                        className={inputClass}
                        defaultValue=""
                        onChange={(e) => applyTemplate(e.target.value)}
                      >
                        <option value="">Blank — tick permissions yourself</option>
                        {templates.map((t) => (
                          <option key={t.key} value={t.key}>
                            {t.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>

                <label
                  htmlFor="role-all-departments"
                  aria-label="Can see all departments"
                  className={`flex items-start gap-3 rounded-lg border p-3 ${
                    isAdmin ? "border-[#c4c7c5] cursor-pointer" : "border-[#e1e3e1] bg-[#f8f9fa] opacity-70"
                  }`}
                >
                  <input
                    id="role-all-departments"
                    type="checkbox"
                    className="mt-0.5"
                    checked={draft.allDepartments}
                    disabled={!isAdmin}
                    onChange={(e) => setDraft({ ...draft, allDepartments: e.target.checked })}
                  />
                  <span className="text-sm">
                    <span className="font-semibold flex items-center gap-1.5">
                      <Globe2 className="w-4 h-4 text-[#0d2e5c]" aria-hidden="true" />
                      Can see all departments
                    </span>
                    <span className="text-xs text-[#444746]">
                      Off: users see only folders from their departments or shared with them. On: every folder in the
                      organization. {isAdmin ? "Give this only to roles like an auditor." : "Only an Admin can turn this on."}
                    </span>
                  </span>
                </label>

                <fieldset className="space-y-4">
                  <legend className="text-xs font-semibold text-[#444746]">
                    What this role can do ({draft.permissions.size} of {totalPermissions})
                  </legend>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {catalogue.map((g) => {
                      const onCount = g.permissions.filter((p) => draft.permissions.has(p.key)).length;
                      const allOn = onCount === g.permissions.length;
                      return (
                        <div key={g.group} className="rounded-lg border border-[#e1e3e1] p-3 space-y-2">
                          <div className="flex items-center justify-between">
                            <h3 className="text-xs font-semibold uppercase tracking-wider text-[#444746]">{g.group}</h3>
                            <button
                              type="button"
                              onClick={() => toggleGroup(g, !allOn)}
                              className="text-xs font-semibold text-[#0d2e5c] hover:underline"
                            >
                              {allOn ? "Clear" : "Select all"}
                            </button>
                          </div>
                          {g.permissions.map((p) => (
                            <label key={p.key} htmlFor={`perm-${p.key}`} className="flex items-start gap-2 text-sm cursor-pointer">
                              <input
                                id={`perm-${p.key}`}
                                type="checkbox"
                                className="mt-0.5"
                                checked={draft.permissions.has(p.key)}
                                onChange={() => toggle(p.key)}
                              />
                              <span>{p.label}</span>
                            </label>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </fieldset>

                <div className="flex items-center justify-end gap-2">
                  <Button type="button" variant="secondary" size="sm" onClick={() => setDraft(null)}>
                    Cancel
                  </Button>
                  <Button type="submit" variant="primary" size="sm" loading={saving}>
                    {draft.id ? "Save role" : "Create role"}
                  </Button>
                </div>
              </form>
            )}

            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3">
                <Loader2 className="w-8 h-8 text-[#0d2e5c] animate-spin" aria-hidden="true" />
                <p className="text-sm text-[#444746]">Loading roles...</p>
              </div>
            ) : error ? (
              <div role="alert" className="rounded-xl p-6 bg-red-50 border border-red-200 text-red-700 flex items-center gap-3">
                <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
                <div>
                  <p className="font-semibold">Error loading roles</p>
                  <p className="text-sm">{error}</p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => loadAll()} className="ml-auto">
                  Retry
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {roles.map((r) => (
                  <section
                    key={r.id}
                    aria-labelledby={`role-${r.id}-title`}
                    className={`bg-white border rounded-xl p-5 space-y-3 ${
                      draft?.id === r.id ? "border-[#0d2e5c]" : "border-[#e1e3e1]"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 id={`role-${r.id}-title`} className="font-bold text-[#1f1f1f] flex items-center gap-2">
                          {r.is_system && <Lock className="w-4 h-4 text-[#444746]" aria-label="Locked" />}
                          <span className="truncate">{r.name}</span>
                        </h2>
                        <p className="text-xs text-[#444746]">
                          {r.user_count} user{r.user_count === 1 ? "" : "s"} ·{" "}
                          {r.is_system ? "everything" : `${r.permissions.length} of ${totalPermissions} permissions`}
                        </p>
                      </div>
                      {!r.is_system && (
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={() => startEdit(r)}
                            aria-label={`Edit role ${r.name}`}
                            className="p-2 rounded-lg text-[#444746] hover:text-[#0d2e5c] hover:bg-[#f0f4f9]"
                          >
                            <Pencil className="w-4 h-4" aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            onClick={() => remove(r)}
                            disabled={busyId === r.id || r.user_count > 0}
                            title={r.user_count > 0 ? "Move its users to another role first" : undefined}
                            aria-label={`Delete role ${r.name}`}
                            className="p-2 rounded-lg text-[#444746] hover:text-red-700 hover:bg-red-50 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-[#444746]"
                          >
                            {busyId === r.id ? (
                              <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                            ) : (
                              <Trash2 className="w-4 h-4" aria-hidden="true" />
                            )}
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {r.is_system && (
                        <span className="px-2 py-0.5 rounded-full text-xs bg-[#0d2e5c] text-white">Locked · can&apos;t be changed</span>
                      )}
                      {r.all_departments && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-amber-50 text-amber-900 border border-amber-300">
                          <Globe2 className="w-3 h-3" aria-hidden="true" /> All departments
                        </span>
                      )}
                      {!r.is_system &&
                        r.permissions.map((k) => (
                          <span
                            key={k}
                            className="px-2 py-0.5 rounded-full text-xs bg-[#edf2fc] text-[#0d2e5c] border border-[#0d2e5c]/15"
                          >
                            {labelByKey.get(k) || k}
                          </span>
                        ))}
                      {!r.is_system && r.permissions.length === 0 && (
                        <span className="text-xs text-[#444746]">No special permissions — can upload, view, search and chat.</span>
                      )}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
