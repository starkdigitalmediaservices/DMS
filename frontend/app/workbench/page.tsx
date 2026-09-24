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
  X,
  Info,
  FileText,
  ArrowUpDown,
  ArrowLeftRight,
  Ban,
  ShieldAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { useI18n } from "@/lib/i18n";
import { useRole } from "@/lib/permissions";
import { sentinelLabel } from "@/lib/factLabels";
import { confidenceInfo } from "@/lib/fieldLabels";
import WorkbenchTabs from "@/components/workbench/WorkbenchTabs";
import ReviewDocumentsView from "@/components/workbench/ReviewDocumentsView";
import ReviewScreen from "@/components/review/ReviewScreen";

function openInDocumentHref(fact: { document_id: string; fact_id: string }): string {
  return `/workbench?doc=${encodeURIComponent(fact.document_id)}&fact=${encodeURIComponent(fact.fact_id)}&from=queue`;
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
}

function formatValue(value: any): string {
  if (value && typeof value === "object" && "page_a" in value && "page_b" in value) {
    return `Page ${value.page_a} and page ${value.page_b}`;
  }
  if (value && typeof value === "object" && "v" in value) return value.v === null || value.v === "" ? "(empty)" : String(value.v);
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const GUIDE_KEY = "check_guide_dismissed";

/** The list of values the computer wasn't sure about, one at a time. */
function QueueWorkbench() {
  const { t } = useI18n();
  const router = useRouter();
  // Everyone can read the list; only reviewer roles can act on it.
  const { can: roleCan, ready: roleReady } = useRole();
  const canReview = roleCan("facts.review");
  const [category, setCategory] = useState<Category>("low_confidence");
  const [facts, setFacts] = useState<QueueFact[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [categoryCounts, setCategoryCounts] = useState<Partial<Record<Category, number>>>({});
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    try {
      setShowGuide(localStorage.getItem(GUIDE_KEY) !== "1");
    } catch {
      setShowGuide(true);
    }
  }, []);
  const dismissGuide = () => {
    setShowGuide(false);
    try {
      localStorage.setItem(GUIDE_KEY, "1");
    } catch {}
  };

  const fieldLabel = useCallback((fieldName: string): string => sentinelLabel(fieldName, t), [t]);
  const sureText = useCallback((confidence: number | null) => {
    const c = confidenceInfo(confidence);
    if (c.level === "unknown") return t("check.sure.unknown", "Not rated");
    const word = c.level === "high" ? t("check.sure.high", "High") : c.level === "medium" ? t("check.sure.medium", "Medium") : t("check.sure.low", "Low");
    return `${word} · ${c.pct}% ${t("check.sure.suffix", "sure")}`;
  }, [t]);

  const categoryTabs: CategoryTab[] = useMemo(() => [
    {
      key: "low_confidence",
      label: t("check.tab.unsure", "Unsure values"),
      description: t("check.tab.unsure_desc", "Values the computer read but isn't sure about. The least sure are at the top."),
    },
    {
      key: "handwritten",
      label: t("check.tab.handwritten", "Handwritten"),
      description: t("check.tab.handwritten_desc", "Values read from handwriting, which the computer gets wrong more often than print."),
    },
    {
      key: "marginalia",
      label: t("check.tab.margin", "Notes in the margin"),
      description: t("check.tab.margin_desc", "Handwritten notes, stamps or remarks found outside the table, usually in the margins."),
    },
    {
      key: "join_mismatch",
      label: t("check.tab.join", "Rows that didn't line up"),
      description: t("check.tab.join_desc", "Registers printed across two facing pages, where the computer couldn't match a left-page row with its right-page half."),
    },
    {
      key: "stitch_ambiguous",
      label: t("check.tab.stitch", "Table continues on next page?"),
      description: t("check.tab.stitch_desc", "The computer couldn't tell if a table carries on onto the next page. Your answer is remembered for every future document laid out the same way."),
    },
  ], [t]);

  // Below `lg` the list and the detail panel stack into one column, so
  // picking an item leaves the panel off-screen -- scroll it into view there
  // (on desktop it's already beside the list).
  const selectedCardRef = useRef<HTMLDivElement>(null);
  const selectFact = (idx: number) => {
    setSelectedIndex(idx);
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
      selectedCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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
      setError(e?.message || "Could not load the list");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue(category);
  }, [category, loadQueue]);

  // One cheap call per tab (only the count matters) so every tab shows its
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
        setNotice(t("check.done.correct", "Marked as correct. It has been removed from this list."));
      }
      if (action === "mark_handwritten") {
        await api.facts.markHandwritten(selected.fact_id);
        setNotice(t("check.done.handwritten", "Marked as handwritten. It is now under the Handwritten tab."));
      }
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "That didn't work. Please try again.");
    } finally {
      setActionLoading(false);
    }
  };

  // A "does the table continue?" item gets its own answer, not a plain
  // confirm: the answer itself is what gets remembered for future documents.
  const resolveAmbiguity = async (relation: "vertical" | "horizontal" | "unrelated") => {
    if (!selected || !canReview) return;
    setActionLoading(true);
    setError("");
    setNotice("");
    try {
      await api.facts.resolveStitchAmbiguity(selected.fact_id, relation);
      setNotice(t("check.done.stitch", "Answer saved. It will be used for future documents laid out the same way."));
      await loadQueue(category);
    } catch (e: any) {
      setError(e?.message || "That didn't work. Please try again.");
    } finally {
      setActionLoading(false);
    }
  };

  // Keyboard: arrows move, O shows it on the page, C takes/leaves it,
  // Enter/A marks correct, H marks handwritten. Ignored while typing.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, facts.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if ((e.key === "o" || e.key === "O") && facts[selectedIndex]) {
        router.push(openInDocumentHref(facts[selectedIndex]));
      } else if (!canReview) {
        return;
      } else if (e.key === "c" || e.key === "C") {
        doAction(facts[selectedIndex]?.claimed_by_actor_id ? "release" : "claim");
      } else if (category === "stitch_ambiguous") {
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

  const activeTab = categoryTabs.find((tab) => tab.key === category);
  const claimedByMe = !!selected?.claimed_by_actor_id && selected.claimed_by_actor_id === currentUserId;
  const claimedByOther = !!selected?.claimed_by_actor_id && selected.claimed_by_actor_id !== currentUserId;

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="min-h-16 px-3 sm:px-6 py-2 flex flex-wrap items-center justify-between gap-2 border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex flex-wrap items-center gap-2 sm:gap-4 min-w-0">
          <Link
            href="/drive"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-2 sm:px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9] shrink-0"
          >
            <ArrowLeft className="w-4 h-4" />
            <span className="hidden sm:inline">{t("common.back", "Back to Drive")}</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1] hidden sm:block" />
          <h1 className="text-base sm:text-lg font-bold text-[#1f1f1f] flex items-center gap-2 truncate">
            <ShieldCheck className="w-5 h-5 text-[#0d2e5c] shrink-0" aria-hidden="true" />
            <span className="truncate">{t("check.title", "Check extracted data")}</span>
          </h1>
          <WorkbenchTabs active="queue" />
        </div>
        <div className="relative hidden lg:block">
          <button
            type="button"
            aria-expanded={showShortcuts}
            onClick={() => setShowShortcuts((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-[#444746] px-2.5 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <Keyboard className="w-4 h-4" aria-hidden="true" /> {t("check.shortcuts", "Keyboard shortcuts")}
          </button>
          {showShortcuts && (
            <div className="absolute right-0 mt-1 w-72 rounded-xl border border-[#e1e3e1] bg-white shadow-lg p-3 text-xs z-30">
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
                <dt className="font-mono font-bold">&uarr; &darr;</dt><dd>{t("check.key.move", "Move up and down the list")}</dd>
                <dt className="font-mono font-bold">O</dt><dd>{t("check.key.open", "Show it on the page")}</dd>
                {canReview && <>
                  <dt className="font-mono font-bold">Enter</dt><dd>{t("check.key.correct", "Mark as correct")}</dd>
                  <dt className="font-mono font-bold">H</dt><dd>{t("check.key.handwritten", "Mark as handwritten")}</dd>
                  <dt className="font-mono font-bold">C</dt><dd>{t("check.key.claim", "Tell others you're checking it")}</dd>
                </>}
              </dl>
            </div>
          )}
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-3 sm:px-6 py-4 sm:py-6 grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 sm:gap-6">
        <div className="min-w-0">
          {showGuide && (
            <section aria-label={t("check.guide.title", "How this works")} className="mb-4 relative rounded-2xl border border-[#c2d6f5] bg-[#f3f7fd] p-4 pr-10">
              <h2 className="text-sm font-bold text-[#0d2e5c] mb-2">{t("check.guide.title", "How this works")}</h2>
              <ol className="list-decimal pl-5 space-y-1 text-sm text-[#1f1f1f]">
                <li>{t("check.guide.1", "The computer read these values from scanned pages but isn't sure it read them right.")}</li>
                <li>{t("check.guide.2", "Pick one, then press “Show on page” to compare it with the original scan.")}</li>
                <li>{t("check.guide.3", "If it's right, press “Correct”. If it's wrong, fix it on the page view.")}</li>
              </ol>
              <button type="button" onClick={dismissGuide} aria-label={t("check.guide.hide", "Hide this help")}
                className="absolute top-3 right-3 p-1 rounded hover:bg-white/70 text-[#444746]">
                <X className="w-4 h-4" />
              </button>
            </section>
          )}

          <div role="tablist" aria-label={t("check.tabs_label", "What to check")} className="flex flex-wrap gap-2 mb-2">
            {categoryTabs.map((tab) => (
              <button
                key={tab.key}
                role="tab"
                id={`tab-${tab.key}`}
                aria-selected={category === tab.key}
                aria-controls="queue-panel"
                tabIndex={category === tab.key ? 0 : -1}
                onClick={() => setCategory(tab.key)}
                className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-bold border transition-colors ${
                  category === tab.key
                    ? "bg-[#0d2e5c] text-white border-[#0d2e5c]"
                    : "bg-white text-[#444746] border-[#e1e3e1] hover:border-[#0d2e5c]"
                }`}
              >
                {tab.label}
                <span
                  aria-label={`${categoryCounts[tab.key] ?? 0} ${t("check.items", "items")}`}
                  className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                    category === tab.key ? "bg-white/20" : "bg-[#f0f4f9] text-[#444746]"
                  }`}
                >
                  {categoryCounts[tab.key] ?? "…"}
                </span>
              </button>
            ))}
          </div>
          <p className="flex items-start gap-1.5 text-sm text-[#444746] mb-4">
            <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            {activeTab?.description}
          </p>

          {roleReady && !canReview && (
            <div role="status" className="mb-4 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-amber-50 border border-amber-200 text-sm text-amber-800">
              <ShieldAlert className="w-4 h-4 shrink-0" aria-hidden="true" />
              {t("check.read_only", "You can look at these values, but your role can't mark or change them.")}
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
              <span className="text-sm font-semibold text-[#1f1f1f]">
                {total} {total === 1 ? t("check.item_to_check", "item to check") : t("check.items_to_check", "items to check")}
              </span>
              {loading && <Loader2 className="w-4 h-4 animate-spin text-[#0d2e5c]" aria-label="Loading" />}
            </div>

            {!loading && facts.length === 0 && (
              <div className="px-5 py-10 text-center text-sm text-[#444746]">
                {t("check.empty", "Nothing to check here right now.")}
              </div>
            )}

            <div className="divide-y divide-[#e1e3e1]">
              {facts.map((fact, idx) => {
                const c = confidenceInfo(fact.confidence);
                const isMine = !!fact.claimed_by_actor_id && fact.claimed_by_actor_id === currentUserId;
                const isOthers = !!fact.claimed_by_actor_id && fact.claimed_by_actor_id !== currentUserId;
                return (
                  <button
                    key={fact.fact_id}
                    type="button"
                    onClick={() => selectFact(idx)}
                    aria-pressed={idx === selectedIndex}
                    className={`w-full text-left flex items-center justify-between gap-4 px-5 py-3 transition-colors ${
                      idx === selectedIndex ? "bg-[#e8f0fe]" : "hover:bg-[#f8f9fa]"
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-[#1f1f1f] truncate">{fieldLabel(fact.field_name)}</div>
                      <div className="text-sm text-[#444746] truncate">{formatValue(fact.value)}</div>
                      {fact.document_title && (
                        <div className="flex items-center gap-1 text-[11px] text-[#444746] truncate mt-0.5">
                          <FileText className="w-3 h-3 shrink-0" aria-hidden="true" />
                          <span className="truncate">{fact.document_title}</span>
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {(isMine || isOthers) && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-[#444746]">
                          <Lock className="w-3.5 h-3.5" aria-hidden="true" />
                          {isMine ? t("check.claim.you", "You're checking") : t("check.claim.other", "Someone is checking")}
                        </span>
                      )}
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap ${c.className}`}>
                        {sureText(fact.confidence)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </Card>
        </div>

        <div className="space-y-6 lg:sticky lg:top-24 lg:h-[calc(100vh-7rem)] lg:overflow-y-auto pr-2 pb-6 min-w-0">
          <Card ref={selectedCardRef} className="bg-white border border-[#e1e3e1] scroll-mt-20">
            {!selected ? (
              <p className="text-sm text-[#444746]">{t("check.pick_one", "Pick an item from the list to see it here.")}</p>
            ) : (
              <div className="space-y-3">
                {selected.document_title && (
                  <div className="flex items-center gap-1.5 text-xs text-[#444746]">
                    <FileText className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    <span className="truncate">{selected.document_title}</span>
                  </div>
                )}
                <h2 className="text-base font-bold text-[#1f1f1f]">{fieldLabel(selected.field_name)}</h2>
                {category === "stitch_ambiguous" ? (
                  <p className="text-sm text-[#1f1f1f]">
                    {t("check.stitch.question_a", "Does the table on page")} {selected.value?.page_a ?? "?"}{" "}
                    {t("check.stitch.question_b", "carry on onto page")} {selected.value?.page_b ?? "?"}?
                  </p>
                ) : (
                  <>
                    <div>
                      <div className="text-xs text-[#444746] font-semibold">{t("check.read", "What the computer read")}</div>
                      <div className="text-base text-[#1f1f1f] break-words">{formatValue(selected.value)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-[#444746] font-semibold">{t("check.how_sure", "How sure it is")}</div>
                      <span className={`inline-block mt-0.5 text-xs font-semibold px-2 py-0.5 rounded-full border ${confidenceInfo(selected.confidence).className}`}>
                        {sureText(selected.confidence)}
                      </span>
                    </div>
                  </>
                )}
                {selected.is_handwritten && (
                  <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
                    {t("check.is_handwritten", "This value is handwritten.")}
                  </div>
                )}
                {claimedByOther && (
                  <div className="text-xs text-[#444746] bg-[#f0f4f9] rounded-lg px-2.5 py-1.5">
                    {t("check.claim.other_long", "Someone else is already checking this one. You may want to pick another.")}
                  </div>
                )}

                <div className="flex flex-col gap-2 pt-1">
                  <Link
                    href={openInDocumentHref(selected)}
                    className="inline-flex items-center justify-center rounded-lg bg-[#0d2e5c] px-3 h-10 text-sm font-semibold text-white hover:bg-[#0945a5]"
                    title={t("check.show_on_page_hint", "Opens the scanned page with this value outlined, so you can compare (key: O)")}
                  >
                    <FileText className="w-4 h-4 mr-1.5" aria-hidden="true" />
                    {t("check.show_on_page", "Show on page")}
                  </Link>
                  {canReview && (category === "stitch_ambiguous" ? (
                    <>
                      <Button size="sm" className="h-10" loading={actionLoading} onClick={() => resolveAmbiguity("vertical")}>
                        <ArrowUpDown className="w-4 h-4 mr-1.5" aria-hidden="true" />
                        {t("check.stitch.yes", "Yes, it's the same table")}
                      </Button>
                      <Button size="sm" className="h-10" variant="secondary" loading={actionLoading} onClick={() => resolveAmbiguity("horizontal")}>
                        <ArrowLeftRight className="w-4 h-4 mr-1.5" aria-hidden="true" />
                        {t("check.stitch.side", "It's the right half of a wide table")}
                      </Button>
                      <Button size="sm" className="h-10" variant="secondary" loading={actionLoading} onClick={() => resolveAmbiguity("unrelated")}>
                        <Ban className="w-4 h-4 mr-1.5" aria-hidden="true" />
                        {t("check.stitch.no", "No, it's a different table")}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button size="sm" className="h-10" loading={actionLoading} onClick={() => doAction("confirm")}>
                        <CheckCircle2 className="w-4 h-4 mr-1.5" aria-hidden="true" />
                        {t("check.correct", "Correct")}
                      </Button>
                      <p className="text-[11px] text-[#444746] -mt-1">
                        {t("check.correct_hint", "Only if the value matches the scan exactly. To fix a wrong value, use “Show on page”.")}
                      </p>
                      {!selected.is_handwritten && (
                        <Button size="sm" className="h-10" variant="secondary" loading={actionLoading} onClick={() => doAction("mark_handwritten")}>
                          <PenLine className="w-4 h-4 mr-1.5" aria-hidden="true" />
                          {t("check.mark_handwritten", "This is handwritten")}
                        </Button>
                      )}
                    </>
                  ))}
                  {canReview && !claimedByOther && (
                    <button
                      type="button"
                      disabled={actionLoading}
                      onClick={() => doAction(claimedByMe ? "release" : "claim")}
                      className="inline-flex items-center justify-center gap-1.5 text-xs text-[#0d2e5c] hover:underline disabled:opacity-40 py-1"
                      title={t("check.claim_hint", "Lets other reviewers know you're working on this, so they skip it")}
                    >
                      {claimedByMe ? <Unlock className="w-3.5 h-3.5" aria-hidden="true" /> : <Lock className="w-3.5 h-3.5" aria-hidden="true" />}
                      {claimedByMe ? t("check.release", "I've stopped checking this") : t("check.claim", "I'm checking this (others will skip it)")}
                    </button>
                  )}
                </div>
              </div>
            )}
          </Card>
        </div>
      </main>
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
    const from = params.get("from");
    const entity = params.get("entity");
    const fromQueue = from === "queue";
    const fromEntity = from === "entity" && !!entity;
    return (
      <ReviewScreen
        key={doc}
        documentId={doc}
        initialPage={Math.max(1, parseInt(params.get("page") || "1", 10) || 1)}
        focusFactId={params.get("fact")}
        // Arriving to check one value (from the list, or from Entity 360's
        // "Show on page") opens the focused single-item view.
        showQueueItem={(fromQueue || fromEntity) && !!params.get("fact")}
        backHref={fromEntity ? `/entities?node=${encodeURIComponent(entity!)}` : fromQueue ? "/workbench" : "/workbench?tab=documents"}
        backLabel={fromEntity ? "Back to the entity" : fromQueue ? "Back to the list" : "Back to documents"}
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
