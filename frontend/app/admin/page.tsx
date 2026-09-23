"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Users,
  Building2,
  FileText,
  Folder,
  HardDrive,
  Cpu,
  MessageSquare,
  ShieldCheck,
  BarChart3,
  Activity,
  Globe,
  Clock,
  AlertTriangle,
  Zap,
  TrendingUp,
  Server,
  Loader2,
  AlertCircle,
  RefreshCw,
  Trash2,
  LayoutTemplate,
  SlidersHorizontal,
  UserCog,
} from "lucide-react";
import { api } from "@/lib/api";
import { useRole } from "@/lib/permissions";
import { NoAccess } from "@/components/common/NoAccess";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

// ─── Interfaces ────────────────────────────────────────

interface SystemOverview {
  total_users: number;
  total_tenants: number;
  total_documents: number;
  total_trashed: number;
  total_folders: number;
  total_storage_bytes: number;
  total_chunks: number;
  total_chat_sessions: number;
  total_audit_logs: number;
}

interface FileType {
  extension: string;
  count: number;
  size_bytes: number;
}

interface UploadTimelineItem {
  date: string;
  count: number;
}

interface TopUploader {
  full_name: string;
  email: string;
  doc_count: number;
  total_size: number;
}

interface TenantStorage {
  tenant_name: string;
  doc_count: number;
  total_size: number;
  user_count: number;
}

interface RecentActivity {
  action: string;
  resource_type: string;
  ip_address: string;
  timestamp: string | null;
}

interface DmsAnalytics {
  system_overview: SystemOverview;
  documents_by_status: Record<string, number>;
  file_types_breakdown: FileType[];
  upload_timeline: UploadTimelineItem[];
  top_uploaders: TopUploader[];
  storage_per_tenant: TenantStorage[];
  recent_activity: RecentActivity[];
}

interface ApiOverview {
  total_calls: number;
  calls_today: number;
  avg_response_time_ms: number;
  error_rate: number;
  error_count: number;
}

interface TopEndpoint {
  method: string;
  path: string;
  call_count: number;
  avg_time_ms: number;
}

interface SlowestEndpoint {
  method: string;
  path: string;
  call_count: number;
  avg_time_ms: number;
  max_time_ms: number;
}

interface ApiTimelineItem {
  hour: number;
  count: number;
}

interface RecentApiCall {
  method: string;
  path: string;
  status_code: number;
  response_time_ms: number;
  ip_address: string;
  timestamp: string | null;
}

interface ApiAnalytics {
  overview: ApiOverview;
  calls_by_method: Record<string, number>;
  calls_by_status: Record<string, number>;
  top_endpoints: TopEndpoint[];
  slowest_endpoints: SlowestEndpoint[];
  api_timeline: ApiTimelineItem[];
  recent_calls: RecentApiCall[];
}

// ─── Helpers ───────────────────────────────────────────

const formatSize = (bytes: number): string => {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

const formatTimestamp = (ts: string | null): string => {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const METHOD_COLORS: Record<string, string> = {
  GET: "bg-emerald-100 text-emerald-700 border-emerald-200",
  POST: "bg-blue-100 text-blue-700 border-blue-200",
  PATCH: "bg-amber-100 text-amber-700 border-amber-200",
  PUT: "bg-orange-100 text-orange-700 border-orange-200",
  DELETE: "bg-red-100 text-red-700 border-red-200",
};

const STATUS_COLORS: Record<string, string> = {
  "2xx": "bg-emerald-500",
  "3xx": "bg-blue-500",
  "4xx": "bg-amber-500",
  "5xx": "bg-red-500",
};

// ─── Component ─────────────────────────────────────────

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<"dms" | "api">("dms");
  const [dmsData, setDmsData] = useState<DmsAnalytics | null>(null);
  const [apiData, setApiData] = useState<ApiAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const { can: roleCan, ready: roleReady } = useRole();
  const canViewAnalytics = roleCan("analytics.view");

  const fetchData = async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const [dms, apiAnalytics] = await Promise.all([
        api.admin.getAnalytics(),
        api.admin.getApiAnalytics(),
      ]);
      setDmsData(dms);
      setApiData(apiAnalytics);
    } catch (err: any) {
      setError(err.message || "Failed to load admin analytics");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  // Analytics endpoints are it_admin-only server-side; don't fire requests
  // that can only 403 for any other role.
  useEffect(() => {
    if (!roleReady) return;
    if (canViewAnalytics) fetchData();
    else setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleReady, canViewAnalytics]);

  // ── DMS stat cards config ──
  const overviewCards = dmsData
    ? [
        {
          label: "Total Users",
          value: dmsData.system_overview.total_users,
          icon: Users,
          color: "text-blue-500",
          bg: "bg-blue-50",
        },
        {
          label: "Tenants",
          value: dmsData.system_overview.total_tenants,
          icon: Building2,
          color: "text-violet-500",
          bg: "bg-violet-50",
        },
        {
          label: "Documents",
          value: dmsData.system_overview.total_documents,
          icon: FileText,
          color: "text-emerald-500",
          bg: "bg-emerald-50",
        },
        {
          label: "Trashed",
          value: dmsData.system_overview.total_trashed,
          icon: Trash2,
          color: "text-red-500",
          bg: "bg-red-50",
        },
        {
          label: "Folders",
          value: dmsData.system_overview.total_folders,
          icon: Folder,
          color: "text-indigo-500",
          bg: "bg-indigo-50",
        },
        {
          label: "Storage",
          value: formatSize(dmsData.system_overview.total_storage_bytes),
          icon: HardDrive,
          color: "text-orange-500",
          bg: "bg-orange-50",
          isString: true,
        },
        {
          label: "AI Chunks",
          value: dmsData.system_overview.total_chunks,
          icon: Cpu,
          color: "text-teal-500",
          bg: "bg-teal-50",
        },
        {
          label: "Chat Sessions",
          value: dmsData.system_overview.total_chat_sessions,
          icon: MessageSquare,
          color: "text-pink-500",
          bg: "bg-pink-50",
        },
        {
          label: "Audit Logs",
          value: dmsData.system_overview.total_audit_logs,
          icon: ShieldCheck,
          color: "text-slate-500",
          bg: "bg-slate-50",
        },
      ]
    : [];

  // ── Upload timeline bar chart ──
  const timelineMax =
    dmsData?.upload_timeline?.reduce((m, d) => Math.max(m, d.count), 0) || 1;

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      {/* Header */}
      <header className="h-16 px-6 flex items-center justify-between border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/drive"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Drive</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1]" />
          <h1 className="text-lg font-bold text-[#1f1f1f] flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-[#0d2e5c]" />
            Admin Panel
          </h1>
        </div>

        {canViewAnalytics && (
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <Link href="/admin/users">
            <Button variant="secondary" size="sm">
              <UserCog className="w-4 h-4 mr-2" />
              <span>Users &amp; Roles</span>
            </Button>
          </Link>
          <Link href="/admin/departments">
            <Button variant="secondary" size="sm">
              <Building2 className="w-4 h-4 mr-2" />
              <span>Departments</span>
            </Button>
          </Link>
          <Link href="/admin/templates">
            <Button variant="secondary" size="sm">
              <LayoutTemplate className="w-4 h-4 mr-2" />
              <span>Form Templates</span>
            </Button>
          </Link>
          <Link href="/admin/settings">
            <Button variant="secondary" size="sm">
              <SlidersHorizontal className="w-4 h-4 mr-2" />
              <span>Settings</span>
            </Button>
          </Link>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => fetchData(true)}
            className={`${refreshing ? "animate-pulse" : ""}`}
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </Button>
        </div>
        )}
      </header>

      {roleReady && !canViewAnalytics ? (
        <main className="max-w-[900px] mx-auto p-6 md:p-8">
          <NoAccess message="The Admin Panel is only available to IT Admins." />
        </main>
      ) : (

      <main className="max-w-[1400px] mx-auto p-6 md:p-8 space-y-6 overflow-auto" style={{ maxHeight: "calc(100vh - 64px)" }}>
        {/* Tab Switcher */}
        <div className="flex gap-1 p-1 bg-[#edf2fc] rounded-xl w-fit">
          <button
            onClick={() => setActiveTab("dms")}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-all ${
              activeTab === "dms"
                ? "bg-white text-[#0d2e5c] shadow-sm"
                : "text-[#444746] hover:text-[#1f1f1f]"
            }`}
          >
            <span className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4" />
              DMS Analytics
            </span>
          </button>
          <button
            onClick={() => setActiveTab("api")}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-all ${
              activeTab === "api"
                ? "bg-white text-[#0d2e5c] shadow-sm"
                : "text-[#444746] hover:text-[#1f1f1f]"
            }`}
          >
            <span className="flex items-center gap-2">
              <Activity className="w-4 h-4" />
              API Analytics
            </span>
          </button>
        </div>

        {/* Loading / Error */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <Loader2 className="w-8 h-8 text-[#0d2e5c] animate-spin" />
            <p className="text-sm text-[#444746]">Loading admin analytics...</p>
          </div>
        ) : error ? (
          <Card className="p-6 bg-red-50 border-red-200 text-red-700 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div>
              <p className="font-semibold">Error Loading Analytics</p>
              <p className="text-sm opacity-90">{error}</p>
            </div>
            <Button size="sm" variant="secondary" onClick={() => fetchData()} className="ml-auto">
              Retry
            </Button>
          </Card>
        ) : (
          <>
            {/* ═══════════════ TAB 1: DMS ANALYTICS ═══════════════ */}
            {activeTab === "dms" && dmsData && (
              <div className="space-y-6 animate-fadeIn">
                {/* System Overview Cards */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-9 gap-3">
                  {overviewCards.map((card) => (
                    <div
                      key={card.label}
                      className="glass rounded-xl p-4 hover:shadow-md transition-all hover:-translate-y-0.5 group"
                    >
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-[10px] font-bold uppercase tracking-widest text-[#444746]">
                          {card.label}
                        </span>
                        <div className={`p-1.5 rounded-lg ${card.bg}`}>
                          <card.icon className={`w-3.5 h-3.5 ${card.color}`} />
                        </div>
                      </div>
                      <p className="text-xl font-extrabold text-[#1f1f1f]">
                        {card.isString
                          ? card.value
                          : typeof card.value === "number"
                          ? card.value.toLocaleString()
                          : card.value}
                      </p>
                    </div>
                  ))}
                </div>

                {/* Row: Doc Status + File Types */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Document Status Breakdown */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Activity className="w-4 h-4 text-[#0d2e5c]" />
                      Document Processing Status
                    </h3>
                    {Object.keys(dmsData.documents_by_status).length > 0 ? (
                      <div className="space-y-3">
                        {Object.entries(dmsData.documents_by_status).map(
                          ([status, count]) => {
                            const total = dmsData.system_overview.total_documents || 1;
                            const pct = Math.round((count / total) * 100);
                            const colorMap: Record<string, string> = {
                              processed: "bg-emerald-500",
                              pending: "bg-amber-500",
                              failed: "bg-red-500",
                              processing: "bg-blue-500",
                            };
                            const barColor = colorMap[status] || "bg-gray-400";
                            return (
                              <div key={status} className="space-y-1">
                                <div className="flex justify-between text-sm">
                                  <span className="font-medium capitalize">{status}</span>
                                  <span className="text-[#444746] font-mono text-xs">
                                    {count.toLocaleString()} ({pct}%)
                                  </span>
                                </div>
                                <div className="w-full bg-[#edf2fc] rounded-full h-2 overflow-hidden">
                                  <div
                                    className={`${barColor} h-full rounded-full transition-all duration-700`}
                                    style={{ width: `${Math.max(2, pct)}%` }}
                                  />
                                </div>
                              </div>
                            );
                          }
                        )}
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">No documents found</p>
                    )}
                  </div>

                  {/* File Types Breakdown */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <FileText className="w-4 h-4 text-[#0d2e5c]" />
                      File Types Distribution
                    </h3>
                    {dmsData.file_types_breakdown.length > 0 ? (
                      <div className="space-y-2.5 max-h-60 overflow-y-auto no-scrollbar">
                        {dmsData.file_types_breakdown.map((ft, i) => {
                          const total = dmsData.system_overview.total_documents || 1;
                          const pct = Math.round((ft.count / total) * 100);
                          const extColors: Record<string, string> = {
                            pdf: "bg-red-500",
                            docx: "bg-blue-500",
                            doc: "bg-blue-400",
                            xlsx: "bg-emerald-500",
                            xls: "bg-emerald-400",
                            csv: "bg-teal-500",
                            pptx: "bg-amber-500",
                            txt: "bg-purple-500",
                            json: "bg-cyan-500",
                            md: "bg-indigo-500",
                          };
                          const barColor = extColors[ft.extension] || "bg-gray-400";
                          return (
                            <div key={i} className="space-y-1">
                              <div className="flex items-center justify-between text-xs">
                                <div className="flex items-center gap-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold text-white ${barColor}`}
                                  >
                                    {ft.extension.toUpperCase()}
                                  </span>
                                  <span className="font-medium text-[#1f1f1f]">
                                    {ft.count} files
                                  </span>
                                </div>
                                <span className="text-[#444746] font-mono">
                                  {formatSize(ft.size_bytes)} ({pct}%)
                                </span>
                              </div>
                              <div className="w-full bg-[#edf2fc] rounded-full h-1.5 overflow-hidden">
                                <div
                                  className={`${barColor} h-full rounded-full transition-all duration-500`}
                                  style={{ width: `${Math.max(2, pct)}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">No files uploaded</p>
                    )}
                  </div>
                </div>

                {/* Upload Timeline (Bar Chart) */}
                <div className="glass rounded-xl p-6 space-y-4">
                  <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-[#0d2e5c]" />
                    Upload Timeline (Last 30 Days)
                  </h3>
                  {dmsData.upload_timeline.length > 0 ? (
                    <div className="flex items-end gap-1 h-40 pt-2">
                      {dmsData.upload_timeline.map((item, i) => {
                        const height = Math.max(4, (item.count / timelineMax) * 100);
                        const dateLabel = new Date(item.date).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                        });
                        return (
                          <div
                            key={i}
                            className="flex-1 flex flex-col items-center group relative"
                          >
                            {/* Tooltip */}
                            <div className="absolute -top-8 bg-[#1f1f1f] text-white text-[10px] px-2 py-1 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-10">
                              {dateLabel}: {item.count} uploads
                            </div>
                            <div
                              className="w-full bg-[#0d2e5c] rounded-t-sm hover:bg-[#0945a5] transition-all cursor-default"
                              style={{ height: `${height}%` }}
                            />
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-sm text-[#444746] text-center py-8">
                      No upload activity in the last 30 days
                    </p>
                  )}
                </div>

                {/* Row: Top Uploaders + Storage Per Tenant */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Top Uploaders */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Users className="w-4 h-4 text-[#0d2e5c]" />
                      Top Uploaders
                    </h3>
                    {dmsData.top_uploaders.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-[#e1e3e1]">
                              <th className="text-left py-2 px-2 font-semibold text-[#444746]">#</th>
                              <th className="text-left py-2 px-2 font-semibold text-[#444746]">User</th>
                              <th className="text-right py-2 px-2 font-semibold text-[#444746]">Docs</th>
                              <th className="text-right py-2 px-2 font-semibold text-[#444746]">Size</th>
                            </tr>
                          </thead>
                          <tbody>
                            {dmsData.top_uploaders.map((u, i) => (
                              <tr
                                key={i}
                                className="border-b border-[#e1e3e1]/50 hover:bg-[#f0f4f9] transition-colors"
                              >
                                <td className="py-2 px-2 font-bold text-[#0d2e5c]">{i + 1}</td>
                                <td className="py-2 px-2">
                                  <p className="font-medium text-[#1f1f1f]">{u.full_name}</p>
                                  <p className="text-[10px] text-[#444746]">{u.email}</p>
                                </td>
                                <td className="py-2 px-2 text-right font-mono font-semibold">
                                  {u.doc_count}
                                </td>
                                <td className="py-2 px-2 text-right font-mono text-[#444746]">
                                  {formatSize(u.total_size)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">No uploaders found</p>
                    )}
                  </div>

                  {/* Storage Per Tenant */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Building2 className="w-4 h-4 text-[#0d2e5c]" />
                      Storage Per Tenant
                    </h3>
                    {dmsData.storage_per_tenant.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-[#e1e3e1]">
                              <th className="text-left py-2 px-2 font-semibold text-[#444746]">Tenant</th>
                              <th className="text-right py-2 px-2 font-semibold text-[#444746]">Users</th>
                              <th className="text-right py-2 px-2 font-semibold text-[#444746]">Docs</th>
                              <th className="text-right py-2 px-2 font-semibold text-[#444746]">Storage</th>
                            </tr>
                          </thead>
                          <tbody>
                            {dmsData.storage_per_tenant.map((t, i) => (
                              <tr
                                key={i}
                                className="border-b border-[#e1e3e1]/50 hover:bg-[#f0f4f9] transition-colors"
                              >
                                <td className="py-2 px-2 font-medium text-[#1f1f1f]">
                                  {t.tenant_name}
                                </td>
                                <td className="py-2 px-2 text-right font-mono">{t.user_count}</td>
                                <td className="py-2 px-2 text-right font-mono">{t.doc_count}</td>
                                <td className="py-2 px-2 text-right font-mono text-[#444746]">
                                  {formatSize(t.total_size)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">No tenants found</p>
                    )}
                  </div>
                </div>

                {/* Recent Activity */}
                <div className="glass rounded-xl p-6 space-y-4">
                  <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-[#0d2e5c]" />
                    Recent Audit Activity
                  </h3>
                  {dmsData.recent_activity.length > 0 ? (
                    <div className="overflow-x-auto max-h-52 overflow-y-auto no-scrollbar">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-white">
                          <tr className="border-b border-[#e1e3e1]">
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">Action</th>
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">Resource</th>
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">IP</th>
                            <th className="text-right py-2 px-2 font-semibold text-[#444746]">Time</th>
                          </tr>
                        </thead>
                        <tbody>
                          {dmsData.recent_activity.map((a, i) => (
                            <tr
                              key={i}
                              className="border-b border-[#e1e3e1]/30 hover:bg-[#f0f4f9] transition-colors"
                            >
                              <td className="py-1.5 px-2 font-mono text-[#0d2e5c]">{a.action}</td>
                              <td className="py-1.5 px-2 capitalize text-[#444746]">
                                {a.resource_type}
                              </td>
                              <td className="py-1.5 px-2 text-[#444746]">{a.ip_address}</td>
                              <td className="py-1.5 px-2 text-right text-[#444746]">
                                {formatTimestamp(a.timestamp)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-[#444746] text-center py-4">No audit logs found</p>
                  )}
                </div>
              </div>
            )}

            {/* ═══════════════ TAB 2: API ANALYTICS ═══════════════ */}
            {activeTab === "api" && apiData && (
              <div className="space-y-6 animate-fadeIn">
                {/* API Overview Cards */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  <div className="glass rounded-xl p-5 hover:shadow-md transition-all hover:-translate-y-0.5">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-[#444746]">
                        Total Calls
                      </span>
                      <div className="p-1.5 rounded-lg bg-blue-50">
                        <Globe className="w-3.5 h-3.5 text-blue-500" />
                      </div>
                    </div>
                    <p className="text-2xl font-extrabold text-[#1f1f1f]">
                      {apiData.overview.total_calls.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-[#444746] mt-1">All time API requests</p>
                  </div>

                  <div className="glass rounded-xl p-5 hover:shadow-md transition-all hover:-translate-y-0.5">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-[#444746]">
                        Calls Today
                      </span>
                      <div className="p-1.5 rounded-lg bg-emerald-50">
                        <Zap className="w-3.5 h-3.5 text-emerald-500" />
                      </div>
                    </div>
                    <p className="text-2xl font-extrabold text-[#1f1f1f]">
                      {apiData.overview.calls_today.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-[#444746] mt-1">Requests since midnight</p>
                  </div>

                  <div className="glass rounded-xl p-5 hover:shadow-md transition-all hover:-translate-y-0.5">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-[#444746]">
                        Avg Response
                      </span>
                      <div className="p-1.5 rounded-lg bg-amber-50">
                        <Clock className="w-3.5 h-3.5 text-amber-500" />
                      </div>
                    </div>
                    <p className="text-2xl font-extrabold text-[#1f1f1f]">
                      {apiData.overview.avg_response_time_ms.toFixed(0)}
                      <span className="text-sm font-medium text-[#444746]"> ms</span>
                    </p>
                    <p className="text-[10px] text-[#444746] mt-1">Average latency</p>
                  </div>

                  <div className="glass rounded-xl p-5 hover:shadow-md transition-all hover:-translate-y-0.5">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-[#444746]">
                        Error Rate
                      </span>
                      <div className="p-1.5 rounded-lg bg-red-50">
                        <AlertTriangle className="w-3.5 h-3.5 text-red-500" />
                      </div>
                    </div>
                    <p className="text-2xl font-extrabold text-[#1f1f1f]">
                      {apiData.overview.error_rate}
                      <span className="text-sm font-medium text-[#444746]">%</span>
                    </p>
                    <p className="text-[10px] text-[#444746] mt-1">
                      {apiData.overview.error_count} errors total
                    </p>
                  </div>
                </div>

                {/* Row: Method Breakdown + Status Codes */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Calls by HTTP Method */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Server className="w-4 h-4 text-[#0d2e5c]" />
                      Calls by HTTP Method
                    </h3>
                    <div className="space-y-3">
                      {Object.entries(apiData.calls_by_method).map(([method, count]) => {
                        const total = apiData.overview.total_calls || 1;
                        const pct = Math.round((count / total) * 100);
                        const methodColors: Record<string, string> = {
                          GET: "bg-emerald-500",
                          POST: "bg-blue-500",
                          PATCH: "bg-amber-500",
                          PUT: "bg-orange-500",
                          DELETE: "bg-red-500",
                        };
                        return (
                          <div key={method} className="space-y-1">
                            <div className="flex justify-between text-sm">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold text-white ${
                                  methodColors[method] || "bg-gray-500"
                                }`}
                              >
                                {method}
                              </span>
                              <span className="text-[#444746] font-mono text-xs">
                                {count.toLocaleString()} ({pct}%)
                              </span>
                            </div>
                            <div className="w-full bg-[#edf2fc] rounded-full h-2 overflow-hidden">
                              <div
                                className={`${
                                  methodColors[method] || "bg-gray-500"
                                } h-full rounded-full transition-all duration-500`}
                                style={{ width: `${Math.max(2, pct)}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Calls by Status Code */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Activity className="w-4 h-4 text-[#0d2e5c]" />
                      Status Code Distribution
                    </h3>
                    <div className="space-y-3">
                      {Object.entries(apiData.calls_by_status).map(([group, count]) => {
                        const total = apiData.overview.total_calls || 1;
                        const pct = Math.round((count / total) * 100);
                        const labels: Record<string, string> = {
                          "2xx": "Success",
                          "3xx": "Redirect",
                          "4xx": "Client Error",
                          "5xx": "Server Error",
                        };
                        return (
                          <div key={group} className="space-y-1">
                            <div className="flex justify-between text-sm">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`w-3 h-3 rounded-full ${STATUS_COLORS[group] || "bg-gray-400"}`}
                                />
                                <span className="font-medium text-[#1f1f1f] text-xs">
                                  {group} — {labels[group] || group}
                                </span>
                              </div>
                              <span className="text-[#444746] font-mono text-xs">
                                {count.toLocaleString()} ({pct}%)
                              </span>
                            </div>
                            <div className="w-full bg-[#edf2fc] rounded-full h-2 overflow-hidden">
                              <div
                                className={`${
                                  STATUS_COLORS[group] || "bg-gray-400"
                                } h-full rounded-full transition-all duration-500`}
                                style={{ width: `${Math.max(2, pct)}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* API Timeline (24h sparkline) */}
                {apiData.api_timeline.length > 0 && (
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <TrendingUp className="w-4 h-4 text-[#0d2e5c]" />
                      API Traffic — Last 24 Hours
                    </h3>
                    <div className="flex items-end gap-1 h-32">
                      {apiData.api_timeline.map((item, i) => {
                        const maxCalls =
                          apiData.api_timeline.reduce((m, d) => Math.max(m, d.count), 0) || 1;
                        const height = Math.max(4, (item.count / maxCalls) * 100);
                        return (
                          <div
                            key={i}
                            className="flex-1 flex flex-col items-center group relative"
                          >
                            <div className="absolute -top-7 bg-[#1f1f1f] text-white text-[10px] px-2 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-10">
                              {item.hour}:00 — {item.count} calls
                            </div>
                            <div
                              className="w-full bg-[#0d2e5c] rounded-t-sm hover:bg-[#0945a5] transition-all cursor-default"
                              style={{ height: `${height}%` }}
                            />
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex justify-between text-[10px] text-[#444746]">
                      <span>{apiData.api_timeline[0]?.hour}:00</span>
                      <span>
                        {apiData.api_timeline[apiData.api_timeline.length - 1]?.hour}:00
                      </span>
                    </div>
                  </div>
                )}

                {/* Row: Top Endpoints + Slowest Endpoints */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Top Endpoints */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Zap className="w-4 h-4 text-[#0d2e5c]" />
                      Top Endpoints by Volume
                    </h3>
                    {apiData.top_endpoints.length > 0 ? (
                      <div className="overflow-x-auto max-h-64 overflow-y-auto no-scrollbar">
                        <table className="w-full text-xs">
                          <thead className="sticky top-0 bg-white">
                            <tr className="border-b border-[#e1e3e1]">
                              <th className="text-left py-2 px-1 font-semibold text-[#444746]">Method</th>
                              <th className="text-left py-2 px-1 font-semibold text-[#444746]">Path</th>
                              <th className="text-right py-2 px-1 font-semibold text-[#444746]">Calls</th>
                              <th className="text-right py-2 px-1 font-semibold text-[#444746]">Avg ms</th>
                            </tr>
                          </thead>
                          <tbody>
                            {apiData.top_endpoints.map((ep, i) => (
                              <tr
                                key={i}
                                className="border-b border-[#e1e3e1]/30 hover:bg-[#f0f4f9] transition-colors"
                              >
                                <td className="py-1.5 px-1">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                      METHOD_COLORS[ep.method] || "bg-gray-100 text-gray-600"
                                    }`}
                                  >
                                    {ep.method}
                                  </span>
                                </td>
                                <td className="py-1.5 px-1 font-mono text-[#1f1f1f] truncate max-w-[180px]">
                                  {ep.path}
                                </td>
                                <td className="py-1.5 px-1 text-right font-mono font-semibold">
                                  {ep.call_count}
                                </td>
                                <td className="py-1.5 px-1 text-right font-mono text-[#444746]">
                                  {ep.avg_time_ms.toFixed(0)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">No API calls logged</p>
                    )}
                  </div>

                  {/* Slowest Endpoints */}
                  <div className="glass rounded-xl p-6 space-y-4">
                    <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                      <Clock className="w-4 h-4 text-amber-500" />
                      Slowest Endpoints
                    </h3>
                    {apiData.slowest_endpoints.length > 0 ? (
                      <div className="overflow-x-auto max-h-64 overflow-y-auto no-scrollbar">
                        <table className="w-full text-xs">
                          <thead className="sticky top-0 bg-white">
                            <tr className="border-b border-[#e1e3e1]">
                              <th className="text-left py-2 px-1 font-semibold text-[#444746]">Method</th>
                              <th className="text-left py-2 px-1 font-semibold text-[#444746]">Path</th>
                              <th className="text-right py-2 px-1 font-semibold text-[#444746]">Avg ms</th>
                              <th className="text-right py-2 px-1 font-semibold text-[#444746]">Max ms</th>
                            </tr>
                          </thead>
                          <tbody>
                            {apiData.slowest_endpoints.map((ep, i) => (
                              <tr
                                key={i}
                                className="border-b border-[#e1e3e1]/30 hover:bg-[#f0f4f9] transition-colors"
                              >
                                <td className="py-1.5 px-1">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                      METHOD_COLORS[ep.method] || "bg-gray-100 text-gray-600"
                                    }`}
                                  >
                                    {ep.method}
                                  </span>
                                </td>
                                <td className="py-1.5 px-1 font-mono text-[#1f1f1f] truncate max-w-[180px]">
                                  {ep.path}
                                </td>
                                <td className="py-1.5 px-1 text-right font-mono font-semibold text-amber-600">
                                  {ep.avg_time_ms.toFixed(0)}
                                </td>
                                <td className="py-1.5 px-1 text-right font-mono text-red-500">
                                  {ep.max_time_ms.toFixed(0)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-[#444746] text-center py-4">
                        Not enough data for analysis
                      </p>
                    )}
                  </div>
                </div>

                {/* Recent API Calls Log */}
                <div className="glass rounded-xl p-6 space-y-4">
                  <h3 className="text-sm font-bold text-[#1f1f1f] flex items-center gap-2">
                    <Server className="w-4 h-4 text-[#0d2e5c]" />
                    Recent API Calls
                  </h3>
                  {apiData.recent_calls.length > 0 ? (
                    <div className="overflow-x-auto max-h-72 overflow-y-auto no-scrollbar">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-white z-10">
                          <tr className="border-b border-[#e1e3e1]">
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">Method</th>
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">Path</th>
                            <th className="text-center py-2 px-2 font-semibold text-[#444746]">Status</th>
                            <th className="text-right py-2 px-2 font-semibold text-[#444746]">Time</th>
                            <th className="text-left py-2 px-2 font-semibold text-[#444746]">IP</th>
                            <th className="text-right py-2 px-2 font-semibold text-[#444746]">Timestamp</th>
                          </tr>
                        </thead>
                        <tbody>
                          {apiData.recent_calls.map((call, i) => {
                            const statusColor =
                              call.status_code < 300
                                ? "text-emerald-600 bg-emerald-50"
                                : call.status_code < 400
                                ? "text-blue-600 bg-blue-50"
                                : call.status_code < 500
                                ? "text-amber-600 bg-amber-50"
                                : "text-red-600 bg-red-50";
                            return (
                              <tr
                                key={i}
                                className="border-b border-[#e1e3e1]/20 hover:bg-[#f0f4f9] transition-colors"
                              >
                                <td className="py-1.5 px-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                      METHOD_COLORS[call.method] || "bg-gray-100 text-gray-600"
                                    }`}
                                  >
                                    {call.method}
                                  </span>
                                </td>
                                <td className="py-1.5 px-2 font-mono text-[#1f1f1f] truncate max-w-[200px]">
                                  {call.path}
                                </td>
                                <td className="py-1.5 px-2 text-center">
                                  <span
                                    className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${statusColor}`}
                                  >
                                    {call.status_code}
                                  </span>
                                </td>
                                <td className="py-1.5 px-2 text-right font-mono text-[#444746]">
                                  {call.response_time_ms.toFixed(0)} ms
                                </td>
                                <td className="py-1.5 px-2 text-[#444746]">{call.ip_address}</td>
                                <td className="py-1.5 px-2 text-right text-[#444746]">
                                  {formatTimestamp(call.timestamp)}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-[#444746] text-center py-4">No API calls logged yet</p>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </main>
      )}
    </div>
  );
}
