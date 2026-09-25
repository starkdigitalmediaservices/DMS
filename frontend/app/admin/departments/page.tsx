"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Building2,
  Plus,
  Trash2,
  Loader2,
  AlertCircle,
  Check,
  X,
  Users,
  Folder as FolderIcon,
  UserCog,
} from "lucide-react";
import { api } from "@/lib/api";
import { useRole, roleLabel } from "@/lib/permissions";
import { Button } from "@/components/ui/Button";
import { NoAccess } from "@/components/common/NoAccess";
import type { AdminUser, Department, FolderTreeNode } from "@/types";

interface FlatFolder {
  id: string;
  name: string;
  path: string;
  depth: number;
}

// GET /folders/tree returns nested { id, name, parent_id, color, subfolders }.
function flattenTree(nodes: FolderTreeNode[], depth = 0, prefix = ""): FlatFolder[] {
  const out: FlatFolder[] = [];
  for (const n of nodes) {
    const path = prefix ? `${prefix} / ${n.name}` : n.name;
    out.push({ id: n.id, name: n.name, path, depth });
    const kids = n.subfolders || n.children || [];
    if (kids.length) out.push(...flattenTree(kids, depth + 1, path));
  }
  return out;
}

const selectClass =
  "flex-1 min-w-0 rounded-lg border border-[#c4c7c5] px-2 py-1.5 text-sm text-[#1f1f1f] bg-white focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40";

export default function DepartmentsAdminPage() {
  const { can: roleCan, ready: roleReady } = useRole();
  const canManage = roleCan("departments.manage");

  const [departments, setDepartments] = useState<Department[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [folders, setFolders] = useState<FlatFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");

  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  // Key of the in-flight action, e.g. "member:<deptId>:<userId>", so only
  // that one control shows a spinner/disables.
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // Per-department picker selections
  const [memberPick, setMemberPick] = useState<Record<string, string>>({});
  const [folderPick, setFolderPick] = useState<Record<string, string>>({});

  const loadAll = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    setError("");
    try {
      const [depts, userList, tree] = await Promise.all([
        api.departments.list(),
        api.users.list(),
        api.folders.getTree(),
      ]);
      setDepartments([...depts].sort((a, b) => a.name.localeCompare(b.name)));
      setUsers([...userList].sort((a, b) => a.email.localeCompare(b.email)));
      setFolders(flattenTree(tree || []));
    } catch (err: any) {
      setError(err.message || "Failed to load departments");
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

  const folderPathById = useMemo(() => {
    const m = new Map<string, string>();
    folders.forEach((f) => m.set(f.id, f.path));
    return m;
  }, [folders]);

  // Runs one mutation, then re-reads everything (membership changes show up
  // on the Users page too, and a grant's folder name comes from the server).
  const runAction = async (key: string, fn: () => Promise<unknown>, successMsg: string) => {
    setBusyKey(key);
    setActionError("");
    setNotice("");
    try {
      await fn();
      setNotice(successMsg);
      await loadAll(false);
    } catch (err: any) {
      setActionError(err.message || "Action failed");
    } finally {
      setBusyKey(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setActionError("");
    setNotice("");
    try {
      await api.departments.create(name);
      setNewName("");
      setNotice(`Department "${name}" created.`);
      await loadAll(false);
    } catch (err: any) {
      setActionError(err.message || "Failed to create department");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = (d: Department) => {
    if (
      !confirm(
        `Delete department "${d.name}"? Its ${d.members.length} member(s) lose access to its ${d.folders.length} granted folder(s). Users and folders themselves are not deleted.`
      )
    )
      return;
    runAction(`dept:${d.id}`, () => api.departments.delete(d.id), `Department "${d.name}" deleted.`);
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
            <Building2 className="w-5 h-5 text-[#0d2e5c]" />
            Departments
          </h1>
        </div>
        {canManage && (
          <Link
            href="/admin/users"
            className="flex items-center gap-2 text-sm text-[#0d2e5c] font-semibold px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <UserCog className="w-4 h-4" />
            <span>Users &amp; Roles</span>
          </Link>
        )}
      </header>

      <main className="max-w-[1100px] mx-auto p-6 md:p-8 space-y-6">
        {roleReady && !canManage ? (
          <NoAccess message="Your role doesn't include managing departments." />
        ) : (
          <>
            <p className="text-sm text-[#444746]">
              A department groups users and the folders they can see. Users see only folders granted to one of their
              departments or shared with them directly (plus their own root-level uploads) — unless their role can see
              all departments.
            </p>

            <form
              onSubmit={handleCreate}
              aria-labelledby="new-dept-title"
              className="bg-white border border-[#e1e3e1] rounded-xl p-5 flex flex-col md:flex-row md:items-end gap-3"
            >
              <div className="flex-1">
                <h2 id="new-dept-title" className="sr-only">
                  Create department
                </h2>
                <label htmlFor="new-dept-name" className="text-xs font-semibold text-[#444746]">
                  New department name
                </label>
                <input
                  id="new-dept-name"
                  type="text"
                  required
                  className="mt-1 w-full rounded-lg border border-[#c4c7c5] px-3 py-2 text-sm text-[#1f1f1f] bg-white focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                  placeholder="e.g. Land Records"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                />
              </div>
              <Button type="submit" variant="primary" size="md" loading={creating} disabled={!newName.trim()}>
                <Plus className="w-4 h-4 mr-2" aria-hidden="true" />
                Create department
              </Button>
            </form>

            {actionError && (
              <div role="alert" className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
                <span className="flex-1">{actionError}</span>
                <button
                  type="button"
                  onClick={() => setActionError("")}
                  aria-label="Dismiss error"
                  className="p-1 rounded hover:bg-red-100"
                >
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

            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3">
                <Loader2 className="w-8 h-8 text-[#0d2e5c] animate-spin" aria-hidden="true" />
                <p className="text-sm text-[#444746]">Loading departments...</p>
              </div>
            ) : error ? (
              <div role="alert" className="rounded-xl p-6 bg-red-50 border border-red-200 text-red-700 flex items-center gap-3">
                <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
                <div>
                  <p className="font-semibold">Error loading departments</p>
                  <p className="text-sm">{error}</p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => loadAll()} className="ml-auto">
                  Retry
                </Button>
              </div>
            ) : departments.length === 0 ? (
              <div className="rounded-xl p-10 text-center space-y-2 bg-white border border-[#e1e3e1]">
                <Building2 className="w-8 h-8 text-[#444746] mx-auto" aria-hidden="true" />
                <p className="text-sm text-[#444746]">No departments yet. Create one above.</p>
              </div>
            ) : (
              <div className="space-y-4">
                {departments.map((d) => {
                  const memberIds = new Set(d.members.map((m) => m.user_id));
                  const grantedIds = new Set(d.folders.map((f) => f.folder_id));
                  const availableUsers = users.filter((u) => !memberIds.has(u.id));
                  const availableFolders = folders.filter((f) => !grantedIds.has(f.id));
                  const mPick = memberPick[d.id] ?? "";
                  const fPick = folderPick[d.id] ?? "";
                  return (
                    <section
                      key={d.id}
                      aria-labelledby={`dept-${d.id}-title`}
                      className="bg-white border border-[#e1e3e1] rounded-xl p-5 space-y-4"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <h2 id={`dept-${d.id}-title`} className="font-bold text-[#1f1f1f]">
                            {d.name}
                          </h2>
                          <p className="text-xs text-[#444746]">
                            {d.members.length} member{d.members.length === 1 ? "" : "s"} · {d.folders.length} folder
                            {d.folders.length === 1 ? "" : "s"}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDelete(d)}
                          disabled={busyKey === `dept:${d.id}`}
                          aria-label={`Delete department ${d.name}`}
                          className="p-2 rounded-lg text-[#444746] hover:text-red-700 hover:bg-red-50 disabled:opacity-50"
                        >
                          {busyKey === `dept:${d.id}` ? (
                            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                          ) : (
                            <Trash2 className="w-4 h-4" aria-hidden="true" />
                          )}
                        </button>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        {/* Members */}
                        <div className="space-y-2">
                          <h3 className="text-xs font-semibold uppercase tracking-wider text-[#444746] flex items-center gap-1.5">
                            <Users className="w-3.5 h-3.5" aria-hidden="true" /> Members
                          </h3>
                          {d.members.length === 0 ? (
                            <p className="text-xs text-[#444746]">No members yet.</p>
                          ) : (
                            <ul className="space-y-1.5">
                              {d.members.map((m) => {
                                const key = `member:${d.id}:${m.user_id}`;
                                return (
                                  <li
                                    key={m.user_id}
                                    className="flex items-center justify-between gap-2 rounded-lg bg-[#f8f9fa] border border-[#e1e3e1] px-3 py-1.5"
                                  >
                                    <div className="min-w-0">
                                      <div className="text-sm text-[#1f1f1f] truncate">{m.full_name || m.email}</div>
                                      <div className="text-xs text-[#444746] truncate">
                                        {m.email} · {m.role_name || roleLabel(m.role)}
                                      </div>
                                    </div>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        runAction(
                                          key,
                                          () => api.departments.removeMember(d.id, m.user_id),
                                          `Removed ${m.email} from ${d.name}.`
                                        )
                                      }
                                      disabled={busyKey === key}
                                      aria-label={`Remove ${m.email} from ${d.name}`}
                                      className="p-1.5 rounded-md text-[#444746] hover:text-red-700 hover:bg-red-50 disabled:opacity-50 shrink-0"
                                    >
                                      {busyKey === key ? (
                                        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                                      ) : (
                                        <X className="w-3.5 h-3.5" aria-hidden="true" />
                                      )}
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                          <div className="flex items-center gap-2 pt-1">
                            <select
                              aria-label={`User to add to ${d.name}`}
                              value={mPick}
                              onChange={(e) => setMemberPick((p) => ({ ...p, [d.id]: e.target.value }))}
                              className={selectClass}
                              disabled={availableUsers.length === 0}
                            >
                              <option value="">
                                {availableUsers.length === 0 ? "Everyone is already a member" : "Choose a user…"}
                              </option>
                              {availableUsers.map((u) => (
                                <option key={u.id} value={u.id}>
                                  {u.full_name} ({u.email}) — {u.role_name || roleLabel(u.role)}
                                </option>
                              ))}
                            </select>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              disabled={!mPick}
                              loading={busyKey === `add-member:${d.id}`}
                              onClick={() => {
                                const u = users.find((x) => x.id === mPick);
                                runAction(
                                  `add-member:${d.id}`,
                                  () => api.departments.addMember(d.id, mPick),
                                  `Added ${u?.email ?? "user"} to ${d.name}.`
                                ).then(() => setMemberPick((p) => ({ ...p, [d.id]: "" })));
                              }}
                            >
                              Add member
                            </Button>
                          </div>
                        </div>

                        {/* Folder grants */}
                        <div className="space-y-2">
                          <h3 className="text-xs font-semibold uppercase tracking-wider text-[#444746] flex items-center gap-1.5">
                            <FolderIcon className="w-3.5 h-3.5" aria-hidden="true" /> Folders granted
                          </h3>
                          {d.folders.length === 0 ? (
                            <p className="text-xs text-[#444746]">No folders granted yet.</p>
                          ) : (
                            <ul className="space-y-1.5">
                              {d.folders.map((f) => {
                                const key = `folder:${d.id}:${f.folder_id}`;
                                const label = folderPathById.get(f.folder_id) || f.name;
                                return (
                                  <li
                                    key={f.folder_id}
                                    className="flex items-center justify-between gap-2 rounded-lg bg-[#f8f9fa] border border-[#e1e3e1] px-3 py-1.5"
                                  >
                                    <span className="text-sm text-[#1f1f1f] truncate" title={label}>
                                      {label}
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        runAction(
                                          key,
                                          () => api.departments.revokeFolder(d.id, f.folder_id),
                                          `Revoked "${f.name}" from ${d.name}.`
                                        )
                                      }
                                      disabled={busyKey === key}
                                      aria-label={`Revoke folder ${f.name} from ${d.name}`}
                                      className="p-1.5 rounded-md text-[#444746] hover:text-red-700 hover:bg-red-50 disabled:opacity-50 shrink-0"
                                    >
                                      {busyKey === key ? (
                                        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                                      ) : (
                                        <X className="w-3.5 h-3.5" aria-hidden="true" />
                                      )}
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                          <div className="flex items-center gap-2 pt-1">
                            <select
                              aria-label={`Folder to grant to ${d.name}`}
                              value={fPick}
                              onChange={(e) => setFolderPick((p) => ({ ...p, [d.id]: e.target.value }))}
                              className={selectClass}
                              disabled={availableFolders.length === 0}
                            >
                              <option value="">
                                {folders.length === 0
                                  ? "No folders exist yet"
                                  : availableFolders.length === 0
                                  ? "All folders already granted"
                                  : "Choose a folder…"}
                              </option>
                              {availableFolders.map((f) => (
                                <option key={f.id} value={f.id}>
                                  {`${"  ".repeat(f.depth)}${f.depth ? "└ " : ""}${f.name}`}
                                </option>
                              ))}
                            </select>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              disabled={!fPick}
                              loading={busyKey === `grant-folder:${d.id}`}
                              onClick={() => {
                                const f = folders.find((x) => x.id === fPick);
                                runAction(
                                  `grant-folder:${d.id}`,
                                  () => api.departments.grantFolder(d.id, fPick),
                                  `Granted "${f?.name ?? "folder"}" to ${d.name}.`
                                ).then(() => setFolderPick((p) => ({ ...p, [d.id]: "" })));
                              }}
                            >
                              Grant folder
                            </Button>
                          </div>
                        </div>
                      </div>
                    </section>
                  );
                })}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
