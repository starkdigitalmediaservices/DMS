"use client";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ShieldCheck,
  Loader2,
  CheckCircle2,
  Lock,
  Unlock,
  AlertCircle,
  Keyboard,
  PenLine,
  Pencil,
  Undo2,
  Eye,
  X,
  Info,
  FileText,
  Layers,
  SlidersHorizontal,
  ArrowUpDown,
  ArrowLeftRight,
  Ban,
  ShieldAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import RegionHighlightViewer from "@/components/drive/RegionHighlightViewer";
import type { FolderTreeNode } from "@/types";
import { useI18n } from "@/lib/i18n";
import { useRole } from "@/lib/permissions";
import WorkbenchTabs from "@/components/workbench/WorkbenchTabs";
import ReviewDocumentsView from "@/components/workbench/ReviewDocumentsView";
import ReviewScreen from "@/components/review/ReviewScreen";

function openInDocumentHref(fact: { document_id: string; fact_id: string }): string {
  return `/workbench?doc=${encodeURIComponent(fact.document_id)}&fact=${encodeURIComponent(fact.fact_id)}&from=queue`;
}

function flattenFolders(nodes: FolderTreeNode[], depth = 0): { id: string; name: string; depth: number }[] {
  const out: { id: string; name: string; depth: number }[] = [];
  for (const n of nodes) {
    out.push({ id: n.id, name: n.name, depth });
    const kids = n.subfolders || n.children || [];
    if (kids.length) out.push(...flattenFolders(kids, depth + 1));
  }
  return out;
}

interface QueueFact {
  fact_id: string;
  document_id: string;
  document_title: string | null;
  field_name: string;
  value: any;
  confidence: number | null;
  is_handwritten: boolean;
  claimed_by_actor_id: string | null;
  edit_version?: number;
}

type Category = "low_confidence" | "handwritten" | "marginalia" | "join_mismatch" | "stitch_ambiguous";

interface CategoryTab {
  key: Category;
  label: string;
  description: string;
  available: boolean;
}

function formatValue(value: any): string {
  if (value && typeof value === "object" && "page_a" in value && "page_b" in value) {
    return `Page ${value.page_a} ↔ page ${value.page_b}`;
  }
  if (value && typeof value === "object" && "v" in value) return String(value.v);
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// Bare decimals gave no sense of "is this fine or concerning" at a glance.
function confidenceBadge(confidence: number | null): { text: string; className: string } {
  if (confidence === null) return { text: "—", className: "text-[#5f6368] bg-[#f0f4f9] border-[#e1e3e1]" };
  if (confidence >= 0.8) return { text: confidence.toFixed(2), className: "text-emerald-700 bg-emerald-50 border-emerald-200" };
  if (confidence >= 0.5) return { text: confidence.toFixed(2), className: "text-amber-700 bg-amber-50 border-amber-200" };
  return { text: confidence.toFixed(2), className: "text-red-700 bg-red-50 border-red-200" };
}

function QueueWorkbench() {
  const { t } = useI18n();
  const router = useRouter();
  // Everyone can read the queues; only reviewer roles can act on them
  // (claim/release/confirm/bulk-edit/bulk-confirm/mark-handwritten/
  // resolve-stitch, plus corpus calibration) — the rest get a read-only view.
  const { can: roleCan, ready: roleReady } = useRole();
  const canReview = roleCan("facts.review");
  const canCalibrate = roleCan("corpus.calibrate");
  const [category, setCategory] = useState<Category>("low_confidence");
  const [facts, setFacts] = useState<QueueFact[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // T53 — click-through from a fact to its highlighted source region.
  const [viewingSourceFactId, setViewingSourceFactId] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [categoryCounts, setCategoryCounts] = useState<Partial<Record<Category, number>>>({});

  const fieldLabel = useCallback((fieldName: string): string => {
    if (fieldName === "_marginalia") return t("workbench.sentinel.marginalia", "Handwritten margin note");
    if (fieldName === "_join_mismatch") return t("workbench.sentinel.join_mismatch", "Table join couldn't be matched");
    if (fieldName === "_stitch_ambiguous") return t("workbench.sentinel.stitch_ambiguous", "Table continuation unclear");
    return fieldName;
  }, [t]);

  const categoryTabs: CategoryTab[] = useMemo(() => [
    {
      key: "low_confidence",
      label: t("workbench.tab.needs_review", "Needs Review"),
      description: t("workbench.tab.needs_review_desc", "Extracted fields waiting on a human decision, worst-confidence first. Margin notes, join mismatches and continuation questions each have their own tab."),
      available: true,
    },
    {
      key: "handwritten",
      label: t("workbench.tab.handwritten", "Handwritten"),
      description: t("workbench.tab.handwritten_desc", 'Fields the system read from handwriting rather than print. Excludes margin notes — see "Marginalia".'),
      available: true,
    },
    {
      key: "marginalia",
      label: t("workbench.tab.marginalia", "Marginalia"),
      description: t("workbench.tab.marginalia_desc", "Handwritten notes found outside any known field on the page (margin notes, stamps, annotations)."),
      available: true,
    },
    {
      key: "join_mismatch",
      label: t("workbench.tab.join_mismatches", "Join Mismatches"),
      description: t("workbench.tab.join_mismatches_desc", "A two-page entry the system couldn't reliably match left-to-right — needs a human to pair the halves."),
      available: true,
    },
    {
      key: "stitch_ambiguous",
      label: t("workbench.tab.continuation_unclear", "Continuation Unclear"),
      description: t("workbench.tab.continuation_unclear_desc", "A page pair the system couldn't confidently classify as the same table continuing, a side-by-side spread, or unrelated. Your answer here applies automatically to every future document with this same page shape."),
      available: true,
    },
  ], [t]);
  // Below `lg` the queue and the review panel stack into one column, so
  // picking a row leaves the panel off-screen below it — a mouse user
  // notices the highlighted row and scrolls, but on a tablet that's an easy
  // miss. Scroll it into view, but only in that stacked layout: on desktop
  // it's already visible beside the queue, so jumping there would be an
  // unwanted scroll on every click.
  const selectedCardRef = useRef<HTMLDivElement>(null);
  const selectFact = (idx: number) => {
    setSelectedIndex(idx);
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
      selectedCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const [bulkFolderId, setBulkFolderId] = useState("");
  const [bulkFolders, setBulkFolders] = useState<{ id: string; name: string; depth: number }[] | null>(null);
  const [bulkFoldersLoading, setBulkFoldersLoading] = useState(false);
  const [showBulkFolderPicker, setShowBulkFolderPicker] = useState(false);

  const openBulkFolderPicker = async () => {
    setShowBulkFolderPicker((v) => !v);
    if (bulkFolders) return;
    setBulkFoldersLoading(true);
    try {
      const tree = await api.folders.getTree();
      setBulkFolders(flattenFolders(tree));
    } catch (e: any) {
      setError(e?.message || "Failed to load folders");
    } finally {
      setBulkFoldersLoading(false);
    }
  };
  const [bulkThreshold, setBulkThreshold] = useState("0.8");
  const [bulkPolicyVersion, setBulkPolicyVersion] = useState("");
  const [bulkLoading, setBulkLoading] = useState(false);
  const [bulkResult, setBulkResult] = useState<{ confirmed_count: number; batch_id: string } | null>(null);

  // T59 — previously the only way to learn a folder wasn't calibrated was
  // to submit Bulk Confirm and get a 409 back. Look it up as soon as a
  // folder is chosen so the gate is visible before the submit attempt.
  const [calibrationStatus, setCalibrationStatus] = useState<{ calibrated: boolean; calibrated_at?: string | null; sample_size?: number | null } | null>(null);
  const [calibrationLoading, setCalibrationLoading] = useState(false);
  const [calibrateActionLoading, setCalibrateActionLoading] = useState(false);

  useEffect(() => {
    const folderId = bulkFolderId.trim();
    if (!folderId) {
      setCalibrationStatus(null);
      return;
    }
    let cancelled = false;
    setCalibrationLoading(true);
    // Debounced — bulkFolderId updates on every keystroke when typed
    // directly rather than picked via Browse.
    const timer = setTimeout(() => {
      api.governance
        .getCalibrationStatus(folderId)
        .then((status) => !cancelled && setCalibrationStatus(status))
        .catch(() => !cancelled && setCalibrationStatus(null))
        .finally(() => !cancelled && setCalibrationLoading(false));
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [bulkFolderId]);

  const calibrateThisFolder = async () => {
    const folderId = bulkFolderId.trim();
    if (!folderId) return;
    setCalibrateActionLoading(true);
    setError("");
    try {
      await api.governance.calibrateCorpus(folderId);
      const status = await api.governance.getCalibrationStatus(folderId);
      setCalibrationStatus(status);
      setNotice("Folder calibrated — Bulk Confirm is now unlocked for it.");
    } catch (e: any) {
      setError(e?.message || "Failed to calibrate this folder");
    } finally {
      setCalibrateActionLoading(false);
    }
  };

  // T80 — bulk edit: select rows, preview the change, then apply. One
  // typed value replaces every selected row's value — the common case
  // this is for (the same OCR misread recurring across many rows).
  const [selectedFactIds, setSelectedFactIds] = useState<Set<string>>(new Set());
  const [editValue, setEditValue] = useState("");
  const [editLoading, setEditLoading] = useState(false);
  const [editPreview, setEditPreview] = useState<{ changed_count: number; total: number; rows: any[] } | null>(null);
  const [editResult, setEditResult] = useState<{ batch_id: string; changed_count: number } | null>(null);

  const toggleFactSelection = (factId: string) => {
    setSelectedFactIds((prev) => {
      const next = new Set(prev);
      if (next.has(factId)) next.delete(factId);
      else next.add(factId);
      return next;
    });
    setEditPreview(null);
    setEditResult(null);
  };

  const buildEdits = () => Array.from(selectedFactIds).map((fact_id) => ({
    fact_id,
    new_value: { v: editValue },
    // A value someone changed since this queue loaded is refused (409), not overwritten.
    expected_version: facts.find((f) => f.fact_id === fact_id)?.edit_version,
  }));

  const previewBulkEdit = async () => {
    if (selectedFactIds.size === 0 || !editValue.trim()) return;
    setEditLoading(true);
    setError("");
    try {
      const result = await api.facts.bulkEdit(buildEdits(), true);
      setEditPreview(result);
    } catch (e: any) {
      setError(e?.message || "Failed to preview the bulk edit");
    } finally {
      setEditLoading(false);
    }
  };

  const applyBulkEdit = async () => {
    if (selectedFactIds.size === 0 || !editValue.trim()) return;
    setEditLoading(true);
    setError("");
    try {
      const result = await api.facts.bulkEdit(buildEdits(), false);
      setEditResult(result);
      setEditPreview(null);
      setSelectedFactIds(new Set());
      setEditValue("");
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "Bulk edit failed");
    } finally {
      setEditLoading(false);
    }
  };

  const undoBulkEdit = async () => {
    if (!editResult) return;
    setEditLoading(true);
    setError("");
    try {
      await api.facts.revertBulkEdit(editResult.batch_id);
      setNotice(`Reverted bulk edit batch ${editResult.batch_id.slice(0, 8)}…`);
      setEditResult(null);
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "Failed to revert the bulk edit");
    } finally {
      setEditLoading(false);
    }
  };

  const loadQueue = useCallback(async (cat: Category) => {
    setLoading(true);
    setError("");
    try {
      const data = await api.facts.getQueue(cat, 50, 0);
      setFacts(data.facts || []);
      setTotal(data.total || 0);
      setSelectedIndex(0);
      setCategoryCounts((prev) => ({ ...prev, [cat]: data.total || 0 }));
    } catch (e: any) {
      setError(e?.message || "Failed to load the adjudication queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue(category);
  }, [category, loadQueue]);

  // Tabs previously gave no sense of how many items were in each queue
  // until you actually clicked into it. One cheap (limit=1, only the
  // count matters) call per category, once, so every tab shows a real
  // number up front.
  useEffect(() => {
    categoryTabs.forEach((tab) => {
      api.facts.getQueue(tab.key, 1, 0)
        .then((data) => setCategoryCounts((prev) => ({ ...prev, [tab.key]: data.total || 0 })))
        .catch(() => {});
    });
    api.auth.getProfile()
      .then((p) => setCurrentUserId(p.user_id))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape previously did nothing for the View Source modal — its
  // full-screen backdrop stayed up and kept intercepting every click
  // until the X button or a backdrop click dismissed it. Confirmed live
  // this was genuinely disorienting (the near-universal "Esc closes a
  // dialog" reflex silently broke the page).
  useEffect(() => {
    if (!viewingSourceFactId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setViewingSourceFactId(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [viewingSourceFactId]);

  const selected = facts[selectedIndex] || null;

  const doAction = async (action: "claim" | "release" | "confirm" | "mark_handwritten") => {
    if (!selected || !canReview) return;
    setActionLoading(true);
    setError("");
    setNotice("");
    try {
      if (action === "claim") await api.facts.claim(selected.fact_id);
      if (action === "release") await api.facts.release(selected.fact_id);
      if (action === "confirm") {
        await api.facts.confirm(selected.fact_id, selected.edit_version);
        setNotice(`Confirmed "${selected.field_name}" — removed from queue.`);
      }
      if (action === "mark_handwritten") {
        await api.facts.markHandwritten(selected.fact_id);
        setNotice(`Marked "${selected.field_name}" as handwritten — moved to the Handwritten queue.`);
      }
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || `Failed to ${action.replace("_", " ")} this fact`);
    } finally {
      setActionLoading(false);
    }
  };

  // TS4 — answer a stitch-ambiguity item. Deliberately its own action, not
  // routed through doAction("confirm"): the generic Confirm endpoint would
  // mark the fact verified without ever recording which relation it was,
  // leaving the underlying ambiguity to resurface unresolved on every
  // future document sharing this page shape.
  const resolveAmbiguity = async (relation: "vertical" | "horizontal" | "unrelated") => {
    if (!selected || !canReview) return;
    setActionLoading(true);
    setError("");
    setNotice("");
    try {
      await api.facts.resolveStitchAmbiguity(selected.fact_id, relation);
      const relationLabel = relation === "vertical" ? "same table continuing" : relation === "horizontal" ? "a side-by-side spread" : "unrelated tables";
      setNotice(`Recorded as ${relationLabel} — applies to every future document with this page shape.`);
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "Failed to resolve this continuation");
    } finally {
      setActionLoading(false);
    }
  };

  // T54 — keyboard-first navigation: ↑/↓ move the selection, 'c' claims,
  // 'r' releases, Enter or 'a' confirms the selected fact. Ignored while
  // a text input has focus so typing into the bulk-confirm form doesn't
  // fight with queue navigation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, facts.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if ((e.key === "o" || e.key === "O") && facts[selectedIndex] && !facts[selectedIndex].field_name.startsWith("_")) {
        router.push(openInDocumentHref(facts[selectedIndex]));
      } else if (!canReview) {
        // Read-only roles: navigation only, no review shortcuts.
        return;
      } else if (e.key === "c" || e.key === "C") {
        doAction("claim");
      } else if (e.key === "r" || e.key === "R") {
        doAction("release");
      } else if (category === "stitch_ambiguous") {
        // This queue's confirm/handwritten shortcuts don't apply — its
        // review action is resolveAmbiguity(), which has no natural
        // single-key shortcut of its own (three choices, not one).
        return;
      } else if (e.key === "Enter" || e.key === "a" || e.key === "A") {
        doAction("confirm");
      } else if (e.key === "h" || e.key === "H") {
        doAction("mark_handwritten");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facts, selectedIndex, category, canReview]);

  const submitBulkConfirm = async () => {
    if (!bulkFolderId.trim() || !bulkPolicyVersion.trim()) {
      setError("Bulk confirm needs a corpus folder ID and a policy version.");
      return;
    }
    setBulkLoading(true);
    setError("");
    setBulkResult(null);
    try {
      const result = await api.facts.bulkConfirm(bulkFolderId.trim(), parseFloat(bulkThreshold), bulkPolicyVersion.trim());
      setBulkResult(result);
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "Bulk confirm failed");
    } finally {
      setBulkLoading(false);
    }
  };

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="min-h-16 px-3 sm:px-6 py-2 flex items-center justify-between gap-2 border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-2 sm:gap-4 min-w-0">
          <Link
            href="/drive"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-2 sm:px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9] shrink-0"
          >
            <ArrowLeft className="w-4 h-4" />
            <span className="hidden sm:inline">{t("common.back", "Back to Drive")}</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1] hidden sm:block" />
          <h1 className="text-base sm:text-lg font-bold text-[#1f1f1f] flex items-center gap-2 truncate">
            <ShieldCheck className="w-5 h-5 text-[#0d2e5c] shrink-0" />
            <span className="truncate">{t("workbench.title", "Verification Workbench")}</span>
          </h1>
          <WorkbenchTabs active="queue" />
        </div>
        {/* Keyboard shortcuts only mean something to a keyboard/mouse user —
            hidden below lg, the same breakpoint the layout stacks at for
            touch-oriented tablet/mobile use, where these hints just took up
            space and pushed the header into two lines. */}
        <div className="hidden lg:flex items-center gap-1.5 text-xs text-[#5f6368] shrink-0">
          <Keyboard className="w-4 h-4" />
          <span>{canReview ? <>&uarr;/&darr; navigate &middot; O open in document &middot; C claim &middot; R release &middot; Enter/A confirm &middot; H mark handwritten</> : <>&uarr;/&darr; navigate &middot; O open in document</>}</span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-3 sm:px-6 py-4 sm:py-6 grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 sm:gap-6">
        <div className="min-w-0">
          <div role="tablist" aria-label="Adjudication queues" className="flex flex-wrap gap-2 mb-2">
            {categoryTabs.map((tab) => (
              <button
                key={tab.key}
                role="tab"
                id={`tab-${tab.key}`}
                aria-selected={category === tab.key}
                aria-controls="queue-panel"
                tabIndex={category === tab.key ? 0 : -1}
                disabled={!tab.available}
                onClick={() => tab.available && setCategory(tab.key as Category)}
                title={tab.description}
                className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-bold border transition-colors ${
                  category === tab.key
                    ? "bg-[#0d2e5c] text-white border-[#0d2e5c]"
                    : tab.available
                    ? "bg-white text-[#444746] border-[#e1e3e1] hover:border-[#0d2e5c]"
                    : "bg-[#f0f4f9] text-[#9aa0a6] border-[#e1e3e1] cursor-not-allowed"
                }`}
              >
                {tab.label}
                <span
                  aria-label={`${categoryCounts[tab.key] ?? 0} items`}
                  className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                    category === tab.key ? "bg-white/20" : "bg-[#f0f4f9] text-[#5f6368]"
                  }`}
                >
                  {categoryCounts[tab.key] ?? "…"}
                </span>
              </button>
            ))}
          </div>
          <p className="flex items-center gap-1.5 text-xs text-[#5f6368] mb-4">
            <Info className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            {categoryTabs.find((t) => t.key === category)?.description}
          </p>

          {roleReady && !canReview && (
            <div role="status" className="mb-4 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-amber-50 border border-amber-200 text-sm text-amber-800">
              <ShieldAlert className="w-4 h-4 shrink-0" aria-hidden="true" />
              {t("rbac.read_only_notice", "Your role can view this queue but not review, confirm, or edit facts.")}
            </div>
          )}
          {error && (
            <div role="alert" aria-live="assertive" className="mb-4 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
              <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
              {error}
            </div>
          )}
          {notice && (
            <div role="status" aria-live="polite" className="mb-4 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-green-50 border border-green-200 text-sm text-green-700">
              <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
              {notice}
            </div>
          )}

          <Card id="queue-panel" role="tabpanel" aria-labelledby={`tab-${category}`} className="bg-white border border-[#e1e3e1] p-0 overflow-hidden">
            <div className="px-5 py-3 border-b border-[#e1e3e1] flex items-center justify-between">
              <span className="text-sm font-semibold text-[#1f1f1f]">Queue &mdash; {total} item{total === 1 ? "" : "s"}</span>
              {loading && <Loader2 className="w-4 h-4 animate-spin text-[#0d2e5c]" aria-label="Loading queue items" />}
            </div>
            {facts.length > 0 && canReview && (
              <div className="px-5 py-1.5 bg-[#fafbfc] border-b border-[#e1e3e1] text-[10px] text-[#444746] flex items-center gap-4">
                <span>&#9744; check a row to include it in <b>Bulk edit</b>, below</span>
                <span>Click a row to review it, right</span>
              </div>
            )}

            {!loading && facts.length === 0 && (
              <div className="px-5 py-10 text-center text-sm text-[#747775]">
                Nothing to review in this queue right now.
              </div>
            )}

            <div className="divide-y divide-[#e1e3e1]">
              {facts.map((fact, idx) => {
                const badge = confidenceBadge(fact.confidence);
                const isMine = !!fact.claimed_by_actor_id && fact.claimed_by_actor_id === currentUserId;
                const isOthers = !!fact.claimed_by_actor_id && fact.claimed_by_actor_id !== currentUserId;
                return (
                  <div
                    key={fact.fact_id}
                    className={`w-full flex items-center gap-3 px-5 py-3 transition-colors ${
                      idx === selectedIndex ? "bg-[#e8f0fe]" : "hover:bg-[#f8f9fa]"
                    }`}
                  >
                    {/* Padded wrapper, not the input, so the visible
                        checkbox stays compact while the tap target is
                        still finger-sized on tablet/mobile. */}
                    {canReview && (
                    <span className="shrink-0 -m-2 p-2">
                      <input
                        type="checkbox"
                        checked={selectedFactIds.has(fact.fact_id)}
                        onChange={() => toggleFactSelection(fact.fact_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="block w-4 h-4 accent-[#0d2e5c]"
                        aria-label={`Select ${fieldLabel(fact.field_name)} for bulk edit`}
                        title="Include in Bulk edit"
                      />
                    </span>
                    )}
                    <button
                      onClick={() => selectFact(idx)}
                      aria-label={`Review ${fieldLabel(fact.field_name)}, value ${formatValue(fact.value)}, confidence ${fact.confidence !== null ? (fact.confidence * 100).toFixed(0) + '%' : 'unrated'}`}
                      aria-pressed={idx === selectedIndex}
                      className="flex-1 min-w-0 text-left flex items-center justify-between gap-4 py-1"
                    >
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-[#1f1f1f] truncate">{fieldLabel(fact.field_name)}</div>
                        <div className="text-xs text-[#747775] truncate">{formatValue(fact.value)}</div>
                        {fact.document_title && (
                          <div className="flex items-center gap-1 text-[10px] text-[#444746] truncate mt-0.5">
                            <FileText className="w-3 h-3 shrink-0" aria-hidden="true" />
                            <span className="truncate">{fact.document_title}</span>
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {isMine && (
                          <span title="Claimed by you" aria-label="Claimed by you"><Lock className="w-3.5 h-3.5 text-[#0d2e5c]" aria-hidden="true" /></span>
                        )}
                        {isOthers && (
                          <span title="Claimed by another operator" aria-label="Claimed by another operator"><Lock className="w-3.5 h-3.5 text-[#444746]" aria-hidden="true" /></span>
                        )}
                        <span className={`text-xs font-mono px-1.5 py-0.5 rounded border ${badge.className}`}>
                          {badge.text}
                        </span>
                      </div>
                    </button>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        <div className="space-y-6 lg:sticky lg:top-24 lg:h-[calc(100vh-7rem)] lg:overflow-y-auto pr-2 pb-6 min-w-0">
          <Card ref={selectedCardRef} className="bg-white border border-[#e1e3e1] scroll-mt-20">
            <h2 className="text-sm font-bold text-[#1f1f1f] mb-3">{t("workbench.label.status", "Selected fact")}</h2>
            {!selected ? (
              <p className="text-sm text-[#747775]">{t("workbench.empty_queue", "Select an item from the queue on the left to review it here.")}</p>
            ) : (
              <div className="space-y-3">
                {selected.document_title && (
                  <div className="flex items-center gap-1.5 text-xs text-[#5f6368]">
                    <FileText className="w-3.5 h-3.5 shrink-0" />
                    <span className="truncate">{selected.document_title}</span>
                  </div>
                )}
                {category === "stitch_ambiguous" ? (
                  <div>
                    <div className="text-xs text-[#747775] uppercase tracking-wide font-semibold">{t("workbench.sentinel.stitch_ambiguous", "What's unclear")}</div>
                    <div className="text-sm text-[#1f1f1f]">
                      Page {selected.value?.page_a ?? "?"} and page {selected.value?.page_b ?? "?"} share a similar
                      field layout, but the system couldn&apos;t confidently tell whether page {selected.value?.page_b ?? "?"} is
                      the same table continuing, a side-by-side spread, or an unrelated table.
                    </div>
                  </div>
                ) : (
                  <>
                    <div>
                      <div className="text-xs text-[#747775] uppercase tracking-wide font-semibold">{t("workbench.label.field", "Field")}</div>
                      <div className="text-sm text-[#1f1f1f]">{fieldLabel(selected.field_name)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-[#747775] uppercase tracking-wide font-semibold">{t("workbench.label.extracted_value", "Value")}</div>
                      <div className="text-sm text-[#1f1f1f] break-words">{formatValue(selected.value)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-[#747775] uppercase tracking-wide font-semibold">{t("workbench.label.confidence", "Confidence")}</div>
                      <div className={`inline-block text-sm font-mono mt-0.5 px-2 py-0.5 rounded border ${confidenceBadge(selected.confidence).className}`}>
                        {selected.confidence?.toFixed(3) ?? "—"}
                      </div>
                    </div>
                  </>
                )}
                {selected.is_handwritten && (
                  <div className="text-xs font-bold text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
                    Handwritten source — can&apos;t be included in a threshold-based Bulk Confirm; needs individual review.
                  </div>
                )}
                {/* max-lg: below the breakpoint the layout stacks into one
                    touch-oriented column, so these get a taller tap target
                    (the default "sm" button is 32px — under the ~44px
                    minimum a finger needs) without shrinking them back down
                    on the mouse-driven two-column desktop layout. */}
                <div className="flex flex-wrap gap-2 pt-2">
                  {/* Table values open in the full document review, on this
                      cell; marginalia / join-mismatch / stitch items are not
                      table cells there, so they keep the region popup only. */}
                  {!selected.field_name.startsWith("_") && (
                    <Link
                      href={openInDocumentHref(selected)}
                      className="inline-flex items-center rounded-lg bg-[#0d2e5c] px-3 h-8 max-lg:h-11 max-lg:px-4 text-xs font-semibold text-white hover:bg-[#0945a5]"
                      title="Open the whole document with its scan, on this value (shortcut: O)"
                    >
                      <FileText className="w-3.5 h-3.5 mr-1.5" aria-hidden="true" />
                      Open in document
                    </Link>
                  )}
                  <Button size="sm" className="max-lg:h-11 max-lg:px-4" variant="secondary" onClick={() => setViewingSourceFactId(selected.fact_id)} title="See exactly where this value was read from on the original page">
                    <Eye className="w-3.5 h-3.5 mr-1.5" />
                    {t("workbench.btn.view_source", "View Source")}
                  </Button>
                  {canReview && (<>
                  <Button
                    size="sm" className="max-lg:h-11 max-lg:px-4" variant="secondary" loading={actionLoading}
                    onClick={() => doAction(selected.claimed_by_actor_id ? "release" : "claim")}
                    title={selected.claimed_by_actor_id ? "Release (shortcut: R) — let another operator claim this" : "Claim (shortcut: C) — reserve this for yourself so no one else works on it at the same time"}
                  >
                    {selected.claimed_by_actor_id ? <Unlock className="w-3.5 h-3.5 mr-1.5" /> : <Lock className="w-3.5 h-3.5 mr-1.5" />}
                    {selected.claimed_by_actor_id ? t("workbench.btn.release", "Release") : t("workbench.btn.claim", "Claim")}
                  </Button>
                  {category === "stitch_ambiguous" ? (
                    <>
                      <Button
                        size="sm" className="max-lg:h-11 max-lg:px-4" loading={actionLoading} onClick={() => resolveAmbiguity("vertical")}
                        title="The same table continues onto the next page — rows should stitch together"
                      >
                        <ArrowUpDown className="w-3.5 h-3.5 mr-1.5" />
                        Same table continues
                      </Button>
                      <Button
                        size="sm" className="max-lg:h-11 max-lg:px-4" variant="secondary" loading={actionLoading} onClick={() => resolveAmbiguity("horizontal")}
                        title="These two pages are a side-by-side spread of one wider table"
                      >
                        <ArrowLeftRight className="w-3.5 h-3.5 mr-1.5" />
                        Side-by-side spread
                      </Button>
                      <Button
                        size="sm" className="max-lg:h-11 max-lg:px-4" variant="secondary" loading={actionLoading} onClick={() => resolveAmbiguity("unrelated")}
                        title="These are two genuinely separate tables — do not stitch"
                      >
                        <Ban className="w-3.5 h-3.5 mr-1.5" />
                        Unrelated
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        size="sm" className="max-lg:h-11 max-lg:px-4" loading={actionLoading} onClick={() => doAction("confirm")}
                        title="Confirm (shortcut: Enter/A) — marks this value as human-verified and removes it from the queue"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />
                        {t("workbench.btn.confirm", "Confirm")}
                      </Button>
                      {!selected.is_handwritten && (
                        <Button
                          size="sm" className="max-lg:h-11 max-lg:px-4" variant="secondary" loading={actionLoading} onClick={() => doAction("mark_handwritten")}
                          title="Mark Handwritten (shortcut: H) — flags this as handwritten so it's excluded from threshold-based Bulk Confirm"
                        >
                          <PenLine className="w-3.5 h-3.5 mr-1.5" />
                          {t("workbench.tab.handwritten", "Mark Handwritten")}
                        </Button>
                      )}
                    </>
                  )}
                  </>)}
                </div>
              </div>
            )}
          </Card>

          {canReview && (<>
          <Card className="bg-white border-2 border-emerald-200 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-1 h-full bg-emerald-400" />
            <h2 className="text-sm font-bold text-[#1f1f1f] mb-1 flex items-center gap-1.5">
              <Layers className="w-4 h-4 text-emerald-600" />
              Confirm an entire folder at once
            </h2>
            <p className="text-xs text-[#747775] mb-3">
              Acts on <b>every field above the threshold in one folder</b> — not on anything checked in the queue
              to the left. The folder must be corpus-calibrated first, or this will be refused. Handwritten
              fields are always excluded, no matter how confident.
            </p>
            <div className="space-y-2">
              <p className="text-[11px] text-[#747775] -mt-1">
                Don&apos;t have a folder ID? Click <b>Browse</b> to pick a folder by name instead.
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  aria-label="Corpus folder ID"
                  placeholder="Click Browse to pick a folder — or paste a folder ID here"
                  value={bulkFolderId}
                  onChange={(e) => setBulkFolderId(e.target.value)}
                  className="flex-1 text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                />
                <Button variant="secondary" size="sm" loading={bulkFoldersLoading} onClick={openBulkFolderPicker}>
                  Browse
                </Button>
              </div>
              {showBulkFolderPicker && bulkFolders && (
                <div className="max-h-48 overflow-y-auto flex flex-col gap-1 border border-[#e1e3e1] rounded-lg p-2">
                  {bulkFolders.length === 0 && <p className="text-sm text-[#747775]">No folders found.</p>}
                  {bulkFolders.map((f) => (
                    <button
                      key={f.id}
                      onClick={() => {
                        setBulkFolderId(f.id);
                        setShowBulkFolderPicker(false);
                      }}
                      style={{ paddingLeft: `${8 + f.depth * 16}px` }}
                      className="text-left text-sm py-1 pr-3 rounded hover:bg-[#f0f4f9]"
                    >
                      {f.name}
                    </button>
                  ))}
                </div>
              )}
              {/* T59 — previously this state was invisible until Confirm
                  Folder was clicked and a 409 came back. */}
              {bulkFolderId.trim() && (
                calibrationLoading ? (
                  <div className="flex items-center gap-1.5 text-xs text-[#444746]">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Checking calibration status…
                  </div>
                ) : calibrationStatus?.calibrated ? (
                  <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-2.5 py-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
                    Calibrated{calibrationStatus.sample_size ? ` — sample of ${calibrationStatus.sample_size}` : ""}
                    {calibrationStatus.calibrated_at ? `, ${new Date(calibrationStatus.calibrated_at).toLocaleDateString()}` : ""}
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
                      Not calibrated — Confirm Folder will be refused until a human certifies this corpus.
                    </span>
                    {canCalibrate && (
                    <button
                      onClick={calibrateThisFolder}
                      disabled={calibrateActionLoading}
                      className="shrink-0 font-bold text-amber-800 hover:underline disabled:opacity-50"
                    >
                      {calibrateActionLoading ? "Calibrating…" : "Calibrate now"}
                    </button>
                    )}
                  </div>
                )
              )}
              <div>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  max="1"
                  aria-label="Confidence threshold, 0 to 1"
                  placeholder="Only confirm above this confidence, e.g. 0.8"
                  value={bulkThreshold}
                  onChange={(e) => setBulkThreshold(e.target.value)}
                  className="w-full text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                />
              </div>
              <div>
                <input
                  type="text"
                  aria-label="Policy version label"
                  placeholder="Label this decision, e.g. Q1-2026-review"
                  value={bulkPolicyVersion}
                  onChange={(e) => setBulkPolicyVersion(e.target.value)}
                  className="w-full text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
                />
                <p className="text-[10px] text-[#444746] mt-1">A short name for this batch, so it can be found and reverted later if needed.</p>
              </div>
              <Button size="sm" className="w-full bg-emerald-600 hover:bg-emerald-700" loading={bulkLoading} onClick={submitBulkConfirm}>
                <Layers className="w-3.5 h-3.5 mr-1.5" />
                Confirm Folder
              </Button>
            </div>
            {bulkResult && (
              <div className="mt-3 text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                Confirmed {bulkResult.confirmed_count} fact{bulkResult.confirmed_count === 1 ? "" : "s"} — batch {bulkResult.batch_id.slice(0, 8)}&hellip;
              </div>
            )}
          </Card>

          <Card className="bg-white border-2 border-amber-200 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-1 h-full bg-amber-400" />
            <h2 className="text-sm font-bold text-[#1f1f1f] mb-1 flex items-center gap-1.5">
              <SlidersHorizontal className="w-4 h-4 text-amber-600" />
              Fix a shared mistake across checked rows
            </h2>
            <p className="text-xs text-[#747775] mb-3">
              Acts on <b>whatever&apos;s checked in the queue</b> to the left — the same one typo/misread corrected
              everywhere at once. Always requires re-review afterward; this never marks a value verified on its own.
            </p>
            <div className="space-y-2">
              <div className="text-xs font-semibold text-[#444746]">
                {selectedFactIds.size} row{selectedFactIds.size === 1 ? "" : "s"} selected
              </div>
              <input
                type="text"
                aria-label="Corrected value for selected rows"
                placeholder="Corrected value"
                value={editValue}
                onChange={(e) => { setEditValue(e.target.value); setEditPreview(null); }}
                disabled={selectedFactIds.size === 0}
                className="w-full text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40 disabled:opacity-50"
              />
              <div className="flex gap-2">
                <Button
                  size="sm" variant="secondary" className="flex-1"
                  loading={editLoading} disabled={selectedFactIds.size === 0 || !editValue.trim()}
                  onClick={previewBulkEdit}
                >
                  <Eye className="w-3.5 h-3.5 mr-1.5" />
                  Preview
                </Button>
                <Button
                  size="sm" className="flex-1"
                  loading={editLoading} disabled={!editPreview || editPreview.changed_count === 0}
                  onClick={applyBulkEdit}
                >
                  <Pencil className="w-3.5 h-3.5 mr-1.5" />
                  Apply
                </Button>
              </div>
            </div>

            {editPreview && (
              <div className="mt-3 text-xs bg-[#f0f4f9] border border-[#e1e3e1] rounded-lg px-3 py-2 space-y-1 max-h-40 overflow-y-auto">
                <div className="font-semibold text-[#444746]">
                  {editPreview.changed_count} of {editPreview.total} row{editPreview.total === 1 ? "" : "s"} will change
                </div>
                {editPreview.rows.filter((r: any) => r.changed).map((r: any) => (
                  <div key={r.fact_id} className="text-[#1f1f1f]">
                    {fieldLabel(r.field_name)}: {formatValue(r.previous_value)} &rarr; {formatValue(r.new_value)}
                  </div>
                ))}
              </div>
            )}

            {editResult && (
              <div className="mt-3 flex items-center justify-between gap-2 text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                <span>
                  Edited {editResult.changed_count} fact{editResult.changed_count === 1 ? "" : "s"} — batch {editResult.batch_id.slice(0, 8)}&hellip;
                </span>
                <button onClick={undoBulkEdit} className="flex items-center gap-1 font-bold text-[#0d2e5c] hover:underline shrink-0">
                  <Undo2 className="w-3.5 h-3.5" /> Undo
                </button>
              </div>
            )}
          </Card>
          </>)}
        </div>
      </main>

      {viewingSourceFactId && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-2 sm:p-4">
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-xs"
            aria-hidden="true"
            onClick={() => setViewingSourceFactId(null)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="source-modal-title"
            className="relative z-10 w-full max-w-5xl max-h-[90vh] sm:max-h-[85vh] overflow-y-auto bg-white border border-[#e1e3e1] rounded-2xl sm:rounded-3xl shadow-2xl text-[#1f1f1f] p-4 sm:p-6"
          >
            <div className="flex items-center justify-between mb-4">
              <h3 id="source-modal-title" className="text-lg font-bold">{t("workbench.btn.view_source", "Source Document Region")}</h3>
              <button
                type="button"
                onClick={() => setViewingSourceFactId(null)}
                aria-label="Close source view dialog"
                className="p-2.5 -m-1 text-[#747775] hover:text-[#1f1f1f] rounded-full hover:bg-[#f0f4f9]"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>
            <RegionHighlightViewer factId={viewingSourceFactId} renderWidth={900} />
          </div>
        </div>
      )}
    </div>
  );
}


/** /workbench        -> the queue (triage values across documents)
 *  ?tab=documents    -> documents to review, with progress
 *  ?doc=<id>         -> one document in the review view; &page=N lands on a
 *                       page (search hits), &fact=<id> on a queue item's cell */
function WorkbenchRouter() {
  const params = useSearchParams();
  const doc = params.get("doc");
  if (doc) {
    const fromQueue = params.get("from") === "queue";
    return (
      <ReviewScreen
        key={doc}
        documentId={doc}
        initialPage={Math.max(1, parseInt(params.get("page") || "1", 10) || 1)}
        focusFactId={params.get("fact")}
        backHref={fromQueue ? "/workbench" : "/workbench?tab=documents"}
        backLabel={fromQueue ? "Back to queue" : "Back to documents"}
      />
    );
  }
  if (params.get("tab") === "documents") return <ReviewDocumentsView />;
  return <QueueWorkbench />;
}

export default function WorkbenchPage() {
  // useSearchParams needs a Suspense boundary under static export.
  return (
    <Suspense fallback={<div className="p-8 text-sm text-[#5f6368]">Loading…</div>}>
      <WorkbenchRouter />
    </Suspense>
  );
}
