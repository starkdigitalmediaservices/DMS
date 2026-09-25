"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  UserCog,
  UserPlus,
  Loader2,
  AlertCircle,
  Check,
  Copy,
  KeyRound,
  X,
  Building2,
  ShieldCheck,
  Folder as FolderIcon,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useRole, roleLabel } from "@/lib/permissions";
import { Button } from "@/components/ui/Button";
import { NoAccess } from "@/components/common/NoAccess";
import type { AdminRole, AdminUser, CreatedAdminUser, FolderTreeNode } from "@/types";

interface FlatFolder {
  id: string;
  path: string;
}

function flattenTree(nodes: FolderTreeNode[], prefix = ""): FlatFolder[] {
  const out: FlatFolder[] = [];
  for (const n of nodes) {
    const path = prefix ? `${prefix} / ${n.name}` : n.name;
    out.push({ id: n.id, path });
    const kids = n.subfolders || n.children || [];
    if (kids.length) out.push(...flattenTree(kids, path));
  }
  return out;
}

const userRoleName = (u: AdminUser) => u.role_name || roleLabel(u.role);

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

const inputClass =
  "mt-1 w-full rounded-lg border border-[#c4c7c5] px-3 py-2 text-sm text-[#1f1f1f] bg-white focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40";

export default function UsersAdminPage() {
  const { can: roleCan, ready: roleReady, userId: myUserId } = useRole();
  const canManage = roleCan("users.manage");
  const canShareFolders = roleCan("departments.manage");
  const canManageRoles = roleCan("roles.manage");

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [folders, setFolders] = useState<FlatFolder[]>([]);
  const [folderPick, setFolderPick] = useState<Record<string, string>>({});
  const [folderBusy, setFolderBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // Per-row role change state
  const [savingId, setSavingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);

  // Add-user form
  const [showAdd, setShowAdd] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newRoleId, setNewRoleId] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  // One-time temp password — held only in component state, never persisted.
  const [created, setCreated] = useState<CreatedAdminUser | null>(null);
  const [copied, setCopied] = useState(false);

  const fetchUsers = async () => {
    setLoading(true);
    setError("");
    try {
      const [data, roleList, tree] = await Promise.all([
        api.users.list(),
        api.roles.list(),
        canShareFolders ? api.folders.getTree() : Promise.resolve([]),
      ]);
      setUsers([...data].sort((a, b) => a.email.localeCompare(b.email)));
      setRoles(roleList);
      setFolders(flattenTree(tree || []));
    } catch (err: any) {
      setError(err.message || "Failed to load users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!roleReady) return;
    if (canManage) fetchUsers();
    else setLoading(false);
  }, [roleReady, canManage]);

  const changeRole = async (u: AdminUser, roleId: string) => {
    if (!roleId || roleId === u.role_id) return;
    setSavingId(u.id);
    setRowError(null);
    setNotice("");
    try {
      const updated = await api.users.update(u.id, { role_id: roleId });
      setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, ...updated } : x)));
      setNotice(`${u.email} is now ${userRoleName(updated)}. It applies from their next click.`);
    } catch (err: any) {
      // e.g. 400 "can't change your own role" / 403 delegation guard — show the
      // server's own detail; the select snaps back since state wasn't changed.
      setRowError({ id: u.id, message: err.message || "Failed to change role" });
    } finally {
      setSavingId(null);
    }
  };

  const resetAddForm = () => {
    setNewEmail("");
    setNewName("");
    setNewRoleId("");
    setCreateError("");
  };

  const shareFolder = async (u: AdminUser) => {
    const folderId = folderPick[u.id];
    if (!folderId) return;
    setFolderBusy(`share:${u.id}`);
    setRowError(null);
    setNotice("");
    try {
      await api.users.shareFolder(u.id, folderId);
      setFolderPick((p) => ({ ...p, [u.id]: "" }));
      setNotice(`Folder shared with ${u.email}.`);
      await fetchUsers();
    } catch (err: any) {
      setRowError({ id: u.id, message: err.message || "Failed to share folder" });
    } finally {
      setFolderBusy(null);
    }
  };

  const unshareFolder = async (u: AdminUser, folderId: string, name: string) => {
    setFolderBusy(`unshare:${u.id}:${folderId}`);
    setRowError(null);
    setNotice("");
    try {
      await api.users.unshareFolder(u.id, folderId);
      setNotice(`"${name}" is no longer shared with ${u.email}.`);
      await fetchUsers();
    } catch (err: any) {
      setRowError({ id: u.id, message: err.message || "Failed to stop sharing" });
    } finally {
      setFolderBusy(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError("");
    setNotice("");
    if (!newEmail.trim() || newName.trim().length < 2 || !newRoleId) {
      setCreateError("Enter an email, a full name (at least 2 characters) and a role.");
      return;
    }
    setCreating(true);
    try {
      const res = await api.users.create({ email: newEmail.trim(), full_name: newName.trim(), role_id: newRoleId });
      setCreated(res);
      setCopied(false);
      setShowAdd(false);
      resetAddForm();
      await fetchUsers();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        setCreateError(err.message && err.message !== "Request failed" ? err.message : "A user with this email already exists.");
      } else {
        setCreateError(err.message || "Failed to create user");
      }
    } finally {
      setCreating(false);
    }
  };

  const copyPassword = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.temp_password);
      setCopied(true);
    } catch {
      setCopied(false);
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
            <UserCog className="w-5 h-5 text-[#0d2e5c]" />
            Users &amp; Roles
          </h1>
        </div>

        {canManage && (
          <div className="flex items-center gap-2">
            {canManageRoles && (
              <Link
                href="/admin/roles"
                className="flex items-center gap-2 text-sm text-[#0d2e5c] font-semibold px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
              >
                <ShieldCheck className="w-4 h-4" />
                <span>Roles</span>
              </Link>
            )}
            <Link
              href="/admin/departments"
              className="flex items-center gap-2 text-sm text-[#0d2e5c] font-semibold px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
            >
              <Building2 className="w-4 h-4" />
              <span>Departments</span>
            </Link>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                resetAddForm();
                setShowAdd((v) => !v);
              }}
              aria-expanded={showAdd}
              aria-controls="add-user-form"
            >
              <UserPlus className="w-4 h-4 mr-2" />
              Add user
            </Button>
          </div>
        )}
      </header>

      <main className="max-w-[1100px] mx-auto p-6 md:p-8 space-y-6">
        {roleReady && !canManage ? (
          <NoAccess message="Your role doesn't include managing users." />
        ) : (
          <>
            <p className="text-sm text-[#444746]">
              Everyone in your organization. A role decides what someone can do; which folders they see comes
              from their departments (manage those on the{" "}
              <Link href="/admin/departments" className="font-semibold text-[#0d2e5c] underline">
                Departments
              </Link>{" "}
              page) plus any folder shared with them directly below — unless their role can see all departments.
            </p>

            {created && (
              <section
                aria-labelledby="temp-pw-title"
                className="rounded-xl border border-emerald-300 bg-emerald-50 p-5 space-y-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <h2 id="temp-pw-title" className="font-bold text-emerald-900 flex items-center gap-2">
                    <KeyRound className="w-4 h-4" aria-hidden="true" />
                    {created.email} was added as {userRoleName(created)}
                  </h2>
                  <button
                    type="button"
                    onClick={() => setCreated(null)}
                    aria-label="Dismiss temporary password"
                    className="p-1.5 rounded-lg text-emerald-900 hover:bg-emerald-100"
                  >
                    <X className="w-4 h-4" aria-hidden="true" />
                  </button>
                </div>
                <p className="text-sm text-emerald-900">
                  Temporary password — <strong>shown only this once</strong>. Share it with the user over a secure
                  channel (not the same email thread as their username); they can change it from their profile after
                  signing in.
                </p>
                <div className="flex items-center gap-2 flex-wrap">
                  <code
                    aria-label="Temporary password"
                    className="px-3 py-2 rounded-lg bg-white border border-emerald-300 font-mono text-sm text-[#1f1f1f] select-all break-all"
                  >
                    {created.temp_password}
                  </code>
                  <Button type="button" variant="secondary" size="sm" onClick={copyPassword}>
                    {copied ? <Check className="w-4 h-4 mr-1.5" aria-hidden="true" /> : <Copy className="w-4 h-4 mr-1.5" aria-hidden="true" />}
                    {copied ? "Copied" : "Copy"}
                  </Button>
                  <span role="status" aria-live="polite" className="sr-only">
                    {copied ? "Password copied to clipboard" : ""}
                  </span>
                </div>
              </section>
            )}

            {showAdd && (
              <form
                id="add-user-form"
                onSubmit={handleCreate}
                aria-labelledby="add-user-title"
                className="bg-white border border-[#e1e3e1] rounded-xl p-5 space-y-4"
              >
                <h2 id="add-user-title" className="font-bold text-[#1f1f1f]">
                  Add user
                </h2>
                {createError && (
                  <div role="alert" className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2">
                    {createError}
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label htmlFor="new-user-email" className="text-xs font-semibold text-[#444746]">
                      Email
                    </label>
                    <input
                      id="new-user-email"
                      type="email"
                      required
                      autoComplete="off"
                      className={inputClass}
                      value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                    />
                  </div>
                  <div>
                    <label htmlFor="new-user-name" className="text-xs font-semibold text-[#444746]">
                      Full name
                    </label>
                    <input
                      id="new-user-name"
                      type="text"
                      required
                      minLength={2}
                      maxLength={50}
                      autoComplete="off"
                      className={inputClass}
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                    />
                  </div>
                  <div>
                    <label htmlFor="new-user-role" className="text-xs font-semibold text-[#444746]">
                      Role
                    </label>
                    <select
                      id="new-user-role"
                      required
                      className={inputClass}
                      value={newRoleId}
                      onChange={(e) => setNewRoleId(e.target.value)}
                    >
                      <option value="" disabled>
                        Choose a role…
                      </option>
                      {roles.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex items-center justify-end gap-2">
                  <Button type="button" variant="secondary" size="sm" onClick={() => setShowAdd(false)}>
                    Cancel
                  </Button>
                  <Button type="submit" variant="primary" size="sm" loading={creating}>
                    Create user
                  </Button>
                </div>
              </form>
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
                <p className="text-sm text-[#444746]">Loading users...</p>
              </div>
            ) : error ? (
              <div role="alert" className="rounded-xl p-6 bg-red-50 border border-red-200 text-red-700 flex items-center gap-3">
                <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
                <div>
                  <p className="font-semibold">Error loading users</p>
                  <p className="text-sm">{error}</p>
                </div>
                <Button size="sm" variant="secondary" onClick={fetchUsers} className="ml-auto">
                  Retry
                </Button>
              </div>
            ) : (
              <div className="bg-white border border-[#e1e3e1] rounded-xl overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">Users in your organization</caption>
                  <thead className="bg-[#f8f9fa] text-left text-xs uppercase tracking-wider text-[#444746]">
                    <tr>
                      <th scope="col" className="px-4 py-3 font-semibold">User</th>
                      <th scope="col" className="px-4 py-3 font-semibold">Role</th>
                      <th scope="col" className="px-4 py-3 font-semibold">Folder access</th>
                      <th scope="col" className="px-4 py-3 font-semibold">Added</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#e1e3e1]">
                    {users.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-8 text-center text-[#444746]">
                          No users found.
                        </td>
                      </tr>
                    )}
                    {users.map((u) => {
                      const isSelf = !!myUserId && u.id === myUserId;
                      const selectValue = u.role_id ?? "";
                      const sharedIds = new Set((u.folders || []).map((f) => f.folder_id));
                      const shareable = folders.filter((f) => !sharedIds.has(f.id));
                      return (
                        <tr key={u.id} className="align-top">
                          <td className="px-4 py-3">
                            <div className="font-semibold text-[#1f1f1f]">
                              {u.full_name}
                              {isSelf && <span className="ml-2 text-xs font-normal text-[#444746]">(you)</span>}
                            </div>
                            <div className="text-xs text-[#444746]">{u.email}</div>
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <select
                                aria-label={`Role for ${u.email}`}
                                value={selectValue}
                                disabled={savingId === u.id || isSelf}
                                title={isSelf ? "You can't change your own role" : undefined}
                                onChange={(e) => changeRole(u, e.target.value)}
                                className="rounded-lg border border-[#c4c7c5] px-2 py-1.5 text-sm text-[#1f1f1f] bg-white disabled:bg-[#f0f4f9] disabled:text-[#444746] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                              >
                                {selectValue === "" && (
                                  <option value="" disabled>
                                    {userRoleName(u)} (not yet moved to a role)
                                  </option>
                                )}
                                {roles.map((r) => (
                                  <option key={r.id} value={r.id}>
                                    {r.name}
                                  </option>
                                ))}
                              </select>
                              {savingId === u.id && (
                                <Loader2 className="w-4 h-4 animate-spin text-[#0d2e5c]" aria-label="Saving role" />
                              )}
                            </div>
                            {rowError?.id === u.id && (
                              <p role="alert" className="text-xs text-red-700 mt-1 max-w-xs">
                                {rowError.message}
                              </p>
                            )}
                          </td>
                          <td className="px-4 py-3 space-y-2">
                            {u.departments.length === 0 && (u.folders || []).length === 0 ? (
                              <span className="text-xs text-[#444746]">—</span>
                            ) : (
                              <div className="flex flex-wrap gap-1.5">
                                {u.departments.map((d) => (
                                  <span
                                    key={d.id}
                                    title="Through this department"
                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-[#edf2fc] text-[#0d2e5c] border border-[#0d2e5c]/15"
                                  >
                                    <Building2 className="w-3 h-3" aria-hidden="true" />
                                    {d.name}
                                  </span>
                                ))}
                                {(u.folders || []).map((f) => (
                                  <span
                                    key={f.folder_id}
                                    title="Shared with this user directly"
                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-amber-50 text-amber-900 border border-amber-300"
                                  >
                                    <FolderIcon className="w-3 h-3" aria-hidden="true" />
                                    {f.name}
                                    {canShareFolders && (
                                      <button
                                        type="button"
                                        onClick={() => unshareFolder(u, f.folder_id, f.name)}
                                        disabled={folderBusy === `unshare:${u.id}:${f.folder_id}`}
                                        aria-label={`Stop sharing ${f.name} with ${u.email}`}
                                        className="ml-0.5 rounded hover:bg-amber-100 disabled:opacity-50"
                                      >
                                        <X className="w-3 h-3" aria-hidden="true" />
                                      </button>
                                    )}
                                  </span>
                                ))}
                              </div>
                            )}
                            {canShareFolders && shareable.length > 0 && (
                              <div className="flex items-center gap-1.5">
                                <select
                                  aria-label={`Share a folder with ${u.email}`}
                                  value={folderPick[u.id] ?? ""}
                                  onChange={(e) => setFolderPick((p) => ({ ...p, [u.id]: e.target.value }))}
                                  className="min-w-0 max-w-[14rem] rounded-lg border border-[#c4c7c5] px-2 py-1 text-xs text-[#1f1f1f] bg-white focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                                >
                                  <option value="">Share a folder…</option>
                                  {shareable.map((f) => (
                                    <option key={f.id} value={f.id}>
                                      {f.path}
                                    </option>
                                  ))}
                                </select>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="secondary"
                                  disabled={!folderPick[u.id]}
                                  loading={folderBusy === `share:${u.id}`}
                                  onClick={() => shareFolder(u)}
                                >
                                  Share
                                </Button>
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-3 text-xs text-[#444746] whitespace-nowrap">{formatDate(u.created_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
