"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  SlidersHorizontal,
  Pencil,
  Check,
  X,
  Loader2,
  AlertCircle,
  ShieldAlert,
  Clock,
} from "lucide-react";
import { api } from "@/lib/api";
import { useRole } from "@/lib/permissions";
import type { SysConfigItem } from "@/types";

function formatUpdatedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function SettingsAdminPage() {
  const [rows, setRows] = useState<SysConfigItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [editError, setEditError] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  // useRole() reads the cached profile after mount (not during render —
  // that caused a real hydration mismatch here, React #418/#423).
  const { can: roleCan, ready: roleReady } = useRole();
  const isAdmin = roleCan("config.manage");

  const fetchConfig = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.admin.getConfig();
      setRows(data.sort((a, b) => a.key.localeCompare(b.key)));
    } catch (err: any) {
      setError(err.message || "Failed to load settings");
    } finally {
      setLoading(false);
    }
  };

  // GET /admin/config is it_admin-only server-side too, so don't fire a
  // request that can only come back 403 for any other role.
  useEffect(() => {
    if (!roleReady) return;
    if (isAdmin) fetchConfig();
    else setLoading(false);
  }, [roleReady, isAdmin]);

  const startEdit = (row: SysConfigItem) => {
    setEditingKey(row.key);
    setEditValue(String(row.value));
    setEditError("");
    setNotice("");
  };

  const cancelEdit = () => {
    setEditingKey(null);
    setEditValue("");
    setEditError("");
  };

  const saveEdit = async (key: string) => {
    const parsed = Number(editValue);
    if (editValue.trim() === "" || Number.isNaN(parsed)) {
      setEditError("Enter a number.");
      return;
    }
    setEditError("");
    setSaving(true);
    try {
      const updated = await api.admin.updateConfig(key, parsed);
      setRows((prev) => prev.map((r) => (r.key === key ? updated : r)));
      setEditingKey(null);
      setNotice(`Updated ${key} — takes effect on the next request (the read cache clears immediately).`);
    } catch (err: any) {
      setEditError(err.message || "Failed to save");
    } finally {
      setSaving(false);
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
            <SlidersHorizontal className="w-5 h-5 text-[#0d2e5c]" />
            Settings
          </h1>
        </div>
      </header>

      <main className="max-w-[900px] mx-auto p-6 md:p-8 space-y-6">
        <p className="text-sm text-[#444746]">
          Every threshold and limit the pipeline reads at runtime — search relevance, table-stitch
          confidence, dedup similarity, and the rest. These are <strong>global</strong>, not scoped to your
          organization: a change here applies to every tenant on this deployment the next time the
          affected code path runs. Adding a brand-new threshold still needs a migration (so it has a
          default for deployments that haven&apos;t set it) — this screen only edits ones that already exist.
        </p>

        {roleReady && !isAdmin && (
          <div className="glass rounded-xl p-4 flex items-center gap-3 text-amber-700 bg-amber-50 border border-amber-200">
            <ShieldAlert className="w-5 h-5 shrink-0" />
            <p className="text-sm">
              Viewing and changing these settings requires the IT Admin role.
            </p>
          </div>
        )}

        {notice && (
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-green-50 border border-green-200 text-sm text-green-700">
            <Check className="w-4 h-4 shrink-0" />
            {notice}
          </div>
        )}

        {!isAdmin ? null : loading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <Loader2 className="w-8 h-8 text-[#0d2e5c] animate-spin" />
            <p className="text-sm text-[#444746]">Loading settings...</p>
          </div>
        ) : error ? (
          <div className="glass rounded-xl p-6 bg-red-50 border border-red-200 text-red-700 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div>
              <p className="font-semibold">Error loading settings</p>
              <p className="text-sm opacity-90">{error}</p>
            </div>
          </div>
        ) : (
          <div className="bg-white border border-[#e1e3e1] rounded-xl overflow-hidden">
            <div className="divide-y divide-[#e1e3e1]">
              {rows.map((row) => {
                const isEditing = editingKey === row.key;
                return (
                  <div key={row.key} className="px-5 py-3.5 flex items-start gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-mono text-[#0d2e5c] truncate">{row.key}</div>
                      <div className="text-sm text-[#444746] mt-0.5">{row.description}</div>
                      <div className="flex items-center gap-1 text-[10px] text-[#444746] mt-1">
                        <Clock className="w-3 h-3" />
                        <span>updated {formatUpdatedAt(row.updated_at)}</span>
                      </div>
                      {isEditing && editError && (
                        <p className="text-xs text-red-600 mt-1">{editError}</p>
                      )}
                    </div>

                    <div className="shrink-0 flex items-center gap-1.5 pt-0.5">
                      {isEditing ? (
                        <>
                          <input
                            type="number"
                            step="any"
                            ref={(el) => el?.focus()}
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") saveEdit(row.key);
                              if (e.key === "Escape") cancelEdit();
                            }}
                            className="w-28 text-sm font-mono px-2.5 py-1.5 rounded-lg border border-[#0d2e5c] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/30 text-right"
                          />
                          <button
                            onClick={() => saveEdit(row.key)}
                            disabled={saving}
                            className="p-2 rounded-lg text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                            title="Save"
                          >
                            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                          </button>
                          <button
                            onClick={cancelEdit}
                            disabled={saving}
                            className="p-2 rounded-lg text-[#444746] hover:bg-[#f0f4f9] disabled:opacity-50"
                            title="Cancel"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        </>
                      ) : (
                        <>
                          <span className="text-sm font-mono font-semibold px-2.5 py-1.5 rounded-lg bg-[#f0f4f9] text-[#1f1f1f] min-w-[4.5rem] text-right">
                            {row.value}
                          </span>
                          {isAdmin && (
                            <button
                              onClick={() => startEdit(row)}
                              className="p-2 rounded-lg text-[#444746] hover:text-[#0d2e5c] hover:bg-[#f0f4f9] transition-colors"
                              title="Edit"
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
