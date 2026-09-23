"use client";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  User,
  Mail,
  Calendar,
  ShieldCheck,
  FileText,
  Folder,
  HardDrive,
  LogOut,
  ArrowLeft,
  KeyRound,
  BarChart2,
  PieChart,
  Loader2,
  AlertCircle
} from "lucide-react";
import { api } from "@/lib/api";
import { roleLabel } from "@/lib/permissions";
import { onKeyActivate } from "@/lib/a11y";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

interface FileTypeCount {
  extension: string;
  count: number;
  size_bytes: number;
}


interface UserProfile {
  user_id: string;
  full_name: string;
  email: string;
  role: string;
  tenant_id: string;
  tenant_name: string;
  created_at: string;
  total_files: number;
  total_folders: number;
  total_size_bytes: number;
  total_chunks: number;
  file_types_breakdown: FileTypeCount[];
}

export default function ProfilePage() {
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedExt, setSelectedExt] = useState<string | null>(null);
  const [extDocs, setExtDocs] = useState<any[]>([]);
  const [loadingExtDocs, setLoadingExtDocs] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changePasswordError, setChangePasswordError] = useState("");
  const [changePasswordLoading, setChangePasswordLoading] = useState(false);
  const [changePasswordSuccess, setChangePasswordSuccess] = useState(false);

  const closeChangePasswordModal = () => {
    setShowChangePassword(false);
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setChangePasswordError("");
    setChangePasswordSuccess(false);
  };

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    setChangePasswordError("");
    if (newPassword.length < 8) {
      setChangePasswordError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setChangePasswordError("New password and confirmation don't match.");
      return;
    }
    if (newPassword === currentPassword) {
      setChangePasswordError("New password must be different from your current password.");
      return;
    }
    setChangePasswordLoading(true);
    try {
      await api.auth.changePassword(currentPassword, newPassword);
      setChangePasswordSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err: any) {
      setChangePasswordError(err?.message || "Failed to change password. Check your current password and try again.");
    } finally {
      setChangePasswordLoading(false);
    }
  };

  useEffect(() => {
    fetchProfile();
  }, []);

  const handleSelectExtension = async (ext: string) => {
    const extClean = ext.toLowerCase().replace(".", "");
    setSelectedExt(extClean);
    setLoadingExtDocs(true);
    try {
      const allDocs = await api.documents.list({ is_trashed: false });
      const filtered = allDocs.filter((d: any) => {
        const dExt = (d.title.split(".").pop() || "").toLowerCase();
        return dExt === extClean;
      });
      setExtDocs(filtered);
    } catch (err) {
      console.error("Failed to load documents for extension:", err);
    } finally {
      setLoadingExtDocs(false);
    }
  };

  const fetchProfile = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.auth.getProfile();
      setProfile(data);
    } catch (err: any) {
      setError(err.message || "Failed to load profile details");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    api.auth.logout();
  };

  const formatSize = (bytes: number): string => {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  return (
    <div className="h-screen overflow-y-auto bg-background text-textMain">
      {/* Header Bar */}
      <header className="h-16 px-6 flex items-center justify-between border-b border-borderDark/60 bg-surface/50 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/drive"
            className="flex items-center gap-2 text-sm text-textMuted hover:text-textMain transition-colors px-3 py-1.5 rounded-lg hover:bg-white/5"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Drive</span>
          </Link>
          <div className="h-4 w-px bg-borderDark/60" />
          <h1 className="text-lg font-bold text-textMain">User Profile & Analytics</h1>
        </div>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => setShowLogoutConfirm(true)}
          className="text-red-400 border-red-500/20 hover:bg-red-500/10 hover:border-red-500/30"
        >
          <LogOut className="w-4 h-4 mr-2" />
          <span>Log Out</span>
        </Button>
      </header>

      <main className="max-w-6xl mx-auto p-6 md:p-8 space-y-8">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
            <p className="text-sm text-textMuted">Loading user profile & analytics...</p>
          </div>
        ) : error ? (
          <Card className="p-6 bg-red-500/10 border-red-500/20 text-red-500 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div>
              <p className="font-semibold">Error Loading Profile</p>
              <p className="text-sm opacity-90">{error}</p>
            </div>
            <Button size="sm" variant="secondary" onClick={fetchProfile} className="ml-auto">
              Retry
            </Button>
          </Card>
        ) : profile ? (
          <>
            {/* User Profile Banner Card */}
            <Card glow className="p-6 md:p-8 relative overflow-hidden">
              <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl pointer-events-none" />

              <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
                <div className="flex items-center gap-5">
                  <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary to-secondary text-white font-bold text-2xl flex items-center justify-center shadow-lg shadow-primary/20 shrink-0">
                    {profile.full_name
                      .split(" ")
                      .map((n) => n[0])
                      .join("")
                      .toUpperCase()
                      .slice(0, 2) || "U"}
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center gap-3 flex-wrap">
                      <h2 className="text-2xl font-bold text-textMain">{profile.full_name}</h2>
                      <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider bg-primary/10 text-primary border border-primary/20">
                        {roleLabel(profile.role)}
                      </span>
                    </div>

                    <div className="flex items-center gap-4 text-sm text-textMuted flex-wrap">
                      <span className="flex items-center gap-1.5">
                        <Mail className="w-4 h-4 text-textMuted" />
                        {profile.email}
                      </span>
                      <span className="flex items-center gap-1.5">
                        <Calendar className="w-4 h-4 text-textMuted" />
                        Joined {profile.created_at}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3 w-full md:w-auto">
                  <Button variant="secondary" size="md" className="w-full md:w-auto" onClick={() => setShowChangePassword(true)}>
                    <KeyRound className="w-4 h-4 mr-2 text-primary" />
                    <span>Change Password</span>
                  </Button>
                </div>
              </div>
            </Card>

            {/* Analytics Overview Cards Grid */}
            <div className="space-y-4">
              <h3 className="text-lg font-bold text-textMain flex items-center gap-2">
                <BarChart2 className="w-5 h-5 text-primary" />
                <span>Live Drive Analytics</span>
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card className="p-5 border-borderDark/80 hover:border-primary/40 transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-textMuted">Total Files</span>
                    <div className="p-2 rounded-xl bg-blue-500/10 text-blue-400">
                      <FileText className="w-5 h-5" />
                    </div>
                  </div>
                  <div className="mt-4">
                    <p className="text-3xl font-extrabold text-textMain">{profile.total_files.toLocaleString()}</p>
                    <p className="text-xs text-textMuted mt-1">Uploaded documents in vault</p>
                  </div>
                </Card>

                <Card className="p-5 border-borderDark/80 hover:border-primary/40 transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-textMuted">Storage Used</span>
                    <div className="p-2 rounded-xl bg-orange-500/10 text-orange-400">
                      <HardDrive className="w-5 h-5" />
                    </div>
                  </div>
                  <div className="mt-4">
                    <p className="text-3xl font-extrabold text-textMain">{formatSize(profile.total_size_bytes)}</p>
                    <p className="text-xs text-textMuted mt-1">Total document storage occupied</p>
                  </div>
                </Card>

                <Card className="p-5 border-borderDark/80 hover:border-primary/40 transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-textMuted">Folders</span>
                    <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400">
                      <Folder className="w-5 h-5" />
                    </div>
                  </div>
                  <div className="mt-4">
                    <p className="text-3xl font-extrabold text-textMain">{profile.total_folders.toLocaleString()}</p>
                    <p className="text-xs text-textMuted mt-1">Organized directory folders</p>
                  </div>
                </Card>
              </div>
            </div>

            {/* Document Format Breakdown */}
            <Card className="p-6 md:p-8 space-y-6">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-textMain flex items-center gap-2">
                  <PieChart className="w-5 h-5 text-primary" />
                  <span>File Formats Distribution</span>
                </h3>
                <span className="text-xs text-textMuted">Click any format to view all matching files</span>
              </div>

              {profile.file_types_breakdown && profile.file_types_breakdown.length > 0 ? (
                <div className="max-h-[360px] overflow-y-auto pr-2 space-y-4 custom-scrollbar">
                  {profile.file_types_breakdown.map((item, idx) => {
                    const pct = profile.total_files > 0 ? Math.round((item.count / profile.total_files) * 100) : 0;
                    const extUpper = (item.extension || "OTHER").toUpperCase();

                    // T96 — bg-*-500/400 shades don't clear WCAG 4.5:1
                    // against the badge's fixed white text for most of
                    // this palette (e.g. red-500 measured 3.76:1 live).
                    // -600/-700 reliably do, across this whole family.
                    const colorMap: Record<string, string> = {
                      PDF: "bg-red-700",
                      DOCX: "bg-blue-700",
                      DOC: "bg-blue-600",
                      XLSX: "bg-emerald-700",
                      XLS: "bg-emerald-600",
                      CSV: "bg-teal-700",
                      PPTX: "bg-amber-700",
                      PPT: "bg-amber-600",
                      TXT: "bg-purple-700",
                      JSON: "bg-cyan-700",
                      MD: "bg-indigo-700",
                      RTF: "bg-pink-700",
                      PNG: "bg-sky-700",
                      JPG: "bg-indigo-700",
                      JPEG: "bg-indigo-700",
                    };

                    const bgColorClass = colorMap[extUpper] || "bg-gray-700";

                    return (
                      <div
                        key={idx}
                        role="button"
                        tabIndex={0}
                        onClick={() => handleSelectExtension(item.extension)}
                        onKeyDown={onKeyActivate(() => handleSelectExtension(item.extension))}
                        className="space-y-1.5 p-2 rounded-xl hover:bg-surface/80 transition-all cursor-pointer group border border-transparent hover:border-primary/20"
                        title={`Click to view all .${item.extension} files`}
                      >
                        <div className="flex items-center justify-between text-sm">
                          <div className="flex items-center gap-2.5">
                            <span className={`px-2.5 py-0.5 rounded text-xs font-bold text-white shadow-xs group-hover:scale-105 transition-transform ${bgColorClass}`}>
                              {extUpper}
                            </span>
                            <span className="font-semibold text-textMain group-hover:text-primary transition-colors">{item.count} files</span>
                          </div>
                          <span className="text-xs text-textMuted font-mono">
                            {formatSize(item.size_bytes)} ({pct}%)
                          </span>
                        </div>
                        <div className="w-full bg-surface/80 rounded-full h-2 overflow-hidden border border-borderDark/40">
                          <div
                            className={`${bgColorClass} h-full rounded-full transition-all duration-500`}
                            style={{ width: `${Math.max(2, pct)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-8 text-textMuted text-sm">
                  No documents uploaded yet. Upload files to view extension analytics.
                </div>
              )}
            </Card>

            {/* Filtered Files Modal */}
            {selectedExt && (
              <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fadeIn">
                <Card glow className="p-6 max-w-2xl w-full space-y-4 max-h-[85vh] flex flex-col">
                  <div className="flex items-center justify-between border-b border-borderDark/60 pb-3">
                    <div className="flex items-center gap-2.5">
                      <div className="px-3 py-1 rounded-lg bg-primary/20 text-primary font-bold text-xs uppercase tracking-wider border border-primary/30">
                        .{selectedExt}
                      </div>
                      <h4 className="text-lg font-bold text-textMain">
                        All .{selectedExt.toUpperCase()} Files ({extDocs.length})
                      </h4>
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => setSelectedExt(null)}>
                      Close
                    </Button>
                  </div>

                  <div className="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
                    {loadingExtDocs ? (
                      <div className="flex items-center justify-center py-12 gap-2 text-textMuted text-sm">
                        <Loader2 className="w-4 h-4 animate-spin text-primary" />
                        <span>Fetching .{selectedExt} documents...</span>
                      </div>
                    ) : extDocs.length === 0 ? (
                      <div className="text-center py-12 text-sm text-textMuted">
                        No .{selectedExt} documents found.
                      </div>
                    ) : (
                      extDocs.map((doc) => (
                        <div
                          key={doc.id}
                          className="p-3.5 rounded-xl bg-surface/50 border border-borderDark/60 hover:border-primary/40 flex items-center justify-between gap-4 transition-all"
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <FileText className="w-5 h-5 text-primary shrink-0" />
                            <div className="min-w-0">
                              <p className="font-semibold text-sm text-textMain truncate">{doc.title}</p>
                              <p className="text-xs text-textMuted">
                                {formatSize(doc.file_size_bytes || 0)} • Created {new Date(doc.created_at).toLocaleDateString()}
                              </p>
                            </div>
                          </div>

                          <Link
                            href="/drive"
                            className="px-3 py-1.5 bg-primary/10 hover:bg-primary/20 text-primary text-xs font-semibold rounded-lg border border-primary/20 transition-all shrink-0"
                          >
                            Open in Drive
                          </Link>
                        </div>
                      ))
                    )}
                  </div>
                </Card>
              </div>
            )}

            {/* Logout Modal Confirmation */}
            {showLogoutConfirm && (
              <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fadeIn">
                <Card glow className="p-6 max-w-md w-full space-y-4">
                  <h4 className="text-lg font-bold text-textMain flex items-center gap-2">
                    <LogOut className="w-5 h-5 text-red-500" />
                    <span>Confirm Log Out</span>
                  </h4>
                  <p className="text-sm text-textMuted">
                    Are you sure you want to end your session? You will need to sign in again to access your drive.
                  </p>
                  <div className="flex items-center justify-end gap-3 pt-2">
                    <Button variant="secondary" size="md" onClick={() => setShowLogoutConfirm(false)}>
                      Cancel
                    </Button>
                    <Button variant="primary" size="md" onClick={handleLogout} className="bg-red-600 hover:bg-red-700">
                      Log Out
                    </Button>
                  </div>
                </Card>
              </div>
            )}

            {/* Change Password Modal — in-place, authenticated change.
                Previously this button just linked to /forgot-password,
                forcing an unnecessary email round-trip for a user who's
                already logged in and knows their current password. */}
            {showChangePassword && (
              <div
                role="presentation"
                className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fadeIn"
                onClick={closeChangePasswordModal}
              >
                <Card
                  glow
                  className="p-6 max-w-md w-full space-y-4"
                  onClick={(e) => e.stopPropagation()}
                >
                  <h4 className="text-lg font-bold text-textMain flex items-center gap-2">
                    <KeyRound className="w-5 h-5 text-primary" />
                    <span>Change Password</span>
                  </h4>

                  {changePasswordSuccess ? (
                    <>
                      <p className="text-sm text-emerald-400">
                        Your password has been changed successfully.
                      </p>
                      <div className="flex justify-end pt-2">
                        <Button variant="primary" size="md" onClick={closeChangePasswordModal}>
                          Done
                        </Button>
                      </div>
                    </>
                  ) : (
                    <form onSubmit={handleChangePassword} className="space-y-3">
                      <div>
                        <label htmlFor="current-password-input" className="text-xs font-medium text-textMuted mb-1 block">Current password</label>
                        <input
                          id="current-password-input"
                          type="password"
                          value={currentPassword}
                          onChange={(e) => setCurrentPassword(e.target.value)}
                          autoComplete="current-password"
                          required
                          className="w-full px-3 py-2 rounded-lg bg-surface border border-borderDark text-sm text-textMain focus:outline-none focus:ring-2 focus:ring-primary/40"
                        />
                      </div>
                      <div>
                        <label htmlFor="new-password-input" className="text-xs font-medium text-textMuted mb-1 block">New password</label>
                        <input
                          id="new-password-input"
                          type="password"
                          value={newPassword}
                          onChange={(e) => setNewPassword(e.target.value)}
                          autoComplete="new-password"
                          required
                          minLength={8}
                          className="w-full px-3 py-2 rounded-lg bg-surface border border-borderDark text-sm text-textMain focus:outline-none focus:ring-2 focus:ring-primary/40"
                        />
                      </div>
                      <div>
                        <label htmlFor="confirm-password-input" className="text-xs font-medium text-textMuted mb-1 block">Confirm new password</label>
                        <input
                          id="confirm-password-input"
                          type="password"
                          value={confirmPassword}
                          onChange={(e) => setConfirmPassword(e.target.value)}
                          autoComplete="new-password"
                          required
                          minLength={8}
                          className="w-full px-3 py-2 rounded-lg bg-surface border border-borderDark text-sm text-textMain focus:outline-none focus:ring-2 focus:ring-primary/40"
                        />
                      </div>

                      {changePasswordError && (
                        <div className="flex items-center gap-2 text-xs text-red-400">
                          <AlertCircle className="w-4 h-4 shrink-0" />
                          <span>{changePasswordError}</span>
                        </div>
                      )}

                      <div className="flex items-center justify-end gap-3 pt-2">
                        <Button type="button" variant="secondary" size="md" onClick={closeChangePasswordModal}>
                          Cancel
                        </Button>
                        <Button type="submit" variant="primary" size="md" loading={changePasswordLoading}>
                          Change Password
                        </Button>
                      </div>
                    </form>
                  )}
                </Card>
              </div>
            )}
          </>
        ) : null}
      </main>
    </div>
  );
}
