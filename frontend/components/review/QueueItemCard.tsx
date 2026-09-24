"use client";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeftRight, ArrowUpDown, Ban, CheckCircle2, Loader2, MapPin, PenLine } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { sentinelLabel } from "@/lib/factLabels";
import type { ReviewBox } from "@/types";

interface FactDetail {
  fact_id: string;
  field_name: string;
  value: any;
  confidence: number | null;
  status: "machine" | "in_review" | "verified";
  is_handwritten?: boolean;
  edit_version?: number;
  regions: { page_number: number; x0: number; y0: number; x1: number; y1: number }[];
}

interface Props {
  factId: string;
  /** The review document's version: any save on this screen (e.g. editing
   *  this very value in the table) bumps it, and the card reloads so its
   *  Confirm never sends a version the reviewer has already changed. */
  docVersion?: number;
  canReview: boolean;
  /** Where the item was read from, so the scan can outline it. */
  onRegions: (boxes: ReviewBox[]) => void;
  /** After confirm / handwritten / resolve: the review document changed too. */
  onChanged: () => void;
}

function display(value: any): string {
  const v = value && typeof value === "object" && "v" in value ? value.v : value;
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** The queue item a reviewer arrived with, pinned above the document's
 *  blocks: what it is, where it came from, and the same actions the queue's
 *  panel offers -- so nothing needs the old "View Source" popup. */
export default function QueueItemCard({ factId, docVersion, canReview, onRegions, onChanged }: Props) {
  const { t } = useI18n();
  const [fact, setFact] = useState<FactDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const f: FactDetail = await api.facts.get(factId);
      setFact(f);
      onRegions(
        (f.regions || []).map((r) => ({ page: r.page_number, x: r.x0, y: r.y0, w: r.x1 - r.x0, h: r.y1 - r.y0 }))
      );
    } catch (e: any) {
      setError(e instanceof ApiError && e.status === 404 ? "This queue item no longer exists." : e?.message || "Could not load the queue item");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [factId]);

  useEffect(() => {
    load();
  }, [load, docVersion]);

  const act = async (fn: () => Promise<any>, done: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      setNotice(done);
      await load();
      onChanged();
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        // Changed since this card loaded (by a colleague, or another tab):
        // show the latest value and let the reviewer decide again.
        await load();
        setError("This value was just changed, so the latest is shown above. Check it and press the button again.");
      } else {
        setError(e?.message || "Action failed");
      }
    } finally {
      setBusy(false);
    }
  };

  if (!fact) {
    return (
      <div className="rounded-xl border-2 border-amber-300 bg-amber-50/60 p-3 text-sm text-[#444746]">
        {error ? <span role="alert" className="text-red-700">{error}</span> : <><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Loading queue item…</>}
      </div>
    );
  }

  const isStitch = fact.field_name === "_stitch_ambiguous";
  const wholePage = fact.regions.length > 0 && fact.regions.every((r) => (r.x1 - r.x0) * (r.y1 - r.y0) >= 0.95);
  const pages = Array.from(new Set(fact.regions.map((r) => r.page_number))).sort((a, b) => a - b);

  return (
    <section aria-label="Queue item" data-testid="queue-item-card" className="rounded-xl border-2 border-amber-400 bg-amber-50/60 p-3">
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <span className="text-[11px] font-bold uppercase tracking-wide text-amber-900">Queue item</span>
        <span className="text-sm font-semibold text-[#1f1f1f]">{sentinelLabel(fact.field_name, t)}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full border border-[#e1e3e1] bg-white text-[#444746]">{fact.status.replace("_", " ")}</span>
        {fact.is_handwritten && <span className="text-[11px] px-2 py-0.5 rounded-full border border-amber-300 bg-white text-amber-900">handwritten</span>}
      </div>

      {isStitch ? (
        <p className="text-sm text-[#1f1f1f]">
          Page {fact.value?.page_a ?? "?"} and page {fact.value?.page_b ?? "?"} share a similar layout, but the system couldn&apos;t tell
          whether page {fact.value?.page_b ?? "?"} continues the same table, is a side-by-side spread, or is an unrelated table.
        </p>
      ) : (
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
          <span className="text-[#5f6368]">Value</span>
          <span className="break-words font-medium">{display(fact.value)}</span>
          <span className="text-[#5f6368]">Confidence</span>
          <span className="font-mono">{fact.confidence !== null ? fact.confidence.toFixed(3) : "—"}</span>
        </div>
      )}

      <p className="mt-1.5 flex items-center gap-1 text-xs text-[#444746]">
        <MapPin className="w-3.5 h-3.5 shrink-0 text-amber-700" aria-hidden="true" />
        {pages.length === 0
          ? "No source location was recorded for this item."
          : wholePage
          ? `Outlined on the scan: the whole of page ${pages.join(" and ")} (no exact position was recorded).`
          : `Outlined in amber on the scan, page ${pages.join(", ")}.`}
      </p>

      {notice && <p role="status" className="mt-2 text-xs font-semibold text-green-800 flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />{notice}</p>}
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}

      {canReview && (
        <div className="mt-2 flex flex-wrap gap-2">
          {isStitch ? (
            fact.status !== "verified" && (
              <>
                <button type="button" disabled={busy} onClick={() => act(() => api.facts.resolveStitchAmbiguity(fact.fact_id, "vertical"), "Recorded: the same table continues.")}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0d2e5c] text-white text-xs font-semibold disabled:opacity-40">
                  <ArrowUpDown className="w-3.5 h-3.5" aria-hidden="true" /> Same table continues
                </button>
                <button type="button" disabled={busy} onClick={() => act(() => api.facts.resolveStitchAmbiguity(fact.fact_id, "horizontal"), "Recorded: side-by-side spread.")}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#0d2e5c] text-[#0d2e5c] bg-white text-xs font-semibold disabled:opacity-40">
                  <ArrowLeftRight className="w-3.5 h-3.5" aria-hidden="true" /> Side-by-side spread
                </button>
                <button type="button" disabled={busy} onClick={() => act(() => api.facts.resolveStitchAmbiguity(fact.fact_id, "unrelated"), "Recorded: unrelated tables.")}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#0d2e5c] text-[#0d2e5c] bg-white text-xs font-semibold disabled:opacity-40">
                  <Ban className="w-3.5 h-3.5" aria-hidden="true" /> Unrelated
                </button>
              </>
            )
          ) : (
            <>
              {fact.status === "in_review" && (
                <button type="button" disabled={busy} onClick={() => act(() => api.facts.confirm(fact.fact_id, fact.edit_version), "Confirmed — marked as human-verified.")}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0d2e5c] text-white text-xs font-semibold disabled:opacity-40">
                  <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" /> Confirm
                </button>
              )}
              {!fact.is_handwritten && (
                <button type="button" disabled={busy} onClick={() => act(() => api.facts.markHandwritten(fact.fact_id), "Marked as handwritten.")}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#0d2e5c] text-[#0d2e5c] bg-white text-xs font-semibold disabled:opacity-40">
                  <PenLine className="w-3.5 h-3.5" aria-hidden="true" /> Mark handwritten
                </button>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
