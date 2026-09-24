"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { FileText, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ReviewBox } from "@/types";
import ScanViewer from "@/components/review/ScanViewer";

export interface EvidenceItem {
  factId: string;
  /** "Waqf name: Takia Jhanda Shah ..." */
  label: string;
  /** Pages the value was read from (from the 360 view) -- used when the
   *  fact itself has no recorded position. */
  pages: number[];
}

export interface EvidenceRequest {
  documentId: string;
  documentTitle: string;
  items: EvidenceItem[];
  /** Page to open on; defaults to the first item's page. */
  startPage?: number;
}

interface Props {
  evidence: EvidenceRequest;
  /** Roles that may correct values get a link to the Workbench. */
  canOpenWorkbench: boolean;
  entityId: string;
  onClose: () => void;
}

const NO_BLOCKS: never[] = [];

/** Read-only proof of where values came from: only the original page(s),
 *  each value outlined. No extracted table, no editing, for any role. Uses
 *  the review page-image endpoint (same tenant/department checks) and the
 *  Workbench scan view -- no second PDF renderer. */
export default function EvidenceViewer({ evidence, canOpenWorkbench, entityId, onClose }: Props) {
  const [regions, setRegions] = useState<Record<string, ReviewBox[]> | null>(null);
  const [page, setPage] = useState<number>(evidence.startPage || evidence.items[0]?.pages[0] || 1);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Where each value sits on the page (fetched once per value).
  useEffect(() => {
    let cancelled = false;
    Promise.all(
      evidence.items.map((it) =>
        api.facts.get(it.factId)
          .then((f: any) => {
            const all: ReviewBox[] = (f.regions || []).map((r: any) => ({ page: r.page_number, x: r.x0, y: r.y0, w: r.x1 - r.x0, h: r.y1 - r.y0 }));
            // A whole-page region is a placeholder, not a position: keep its
            // page (so the page still opens) but don't outline the page.
            const real = all.filter((b) => b.w * b.h < 0.95);
            return [it.factId, real.length ? real : all.map((b) => ({ ...b, w: 0, h: 0 }))] as const;
          })
          .catch(() => [it.factId, [] as ReviewBox[]] as const)
      )
    ).then((pairs) => {
      if (!cancelled) setRegions(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [evidence]);

  // Only the pages these values are on -- no others.
  const pages = useMemo(() => {
    const set = new Set<number>();
    for (const it of evidence.items) {
      const boxes = regions?.[it.factId] || [];
      (boxes.length ? boxes.map((b) => b.page) : it.pages).forEach((p) => set.add(p));
    }
    if (!set.size) set.add(page);
    return Array.from(set).sort((a, b) => a - b);
  }, [evidence, regions, page]);

  useEffect(() => {
    if (regions && !pages.includes(page)) setPage(pages[0]);
  }, [regions, pages, page]);

  const onThisPage = evidence.items.filter((it) => {
    const boxes = regions?.[it.factId] || [];
    return boxes.length ? boxes.some((b) => b.page === page) : it.pages.includes(page);
  });
  // memoised: a new array each render would re-trigger the viewer's
  // scroll-to-highlight on every hover
  const boxesHere = useMemo(
    () => evidence.items.flatMap((it) => (regions?.[it.factId] || []).filter((b) => b.page === page && b.w > 0 && b.h > 0)),
    [evidence, regions, page]
  );
  const unplaced = regions ? onThisPage.filter((it) => !(regions[it.factId] || []).some((b) => b.page === page && b.w > 0 && b.h > 0)) : [];

  // Esc closes; focus the dialog so screen readers land in it.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const first = onThisPage[0] || evidence.items[0];
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-2 sm:p-6" data-testid="evidence-viewer">
      <div className="absolute inset-0" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={`Source: ${evidence.documentTitle}, page ${page}`}
        className="relative flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <header className="flex flex-wrap items-start gap-3 border-b border-[#e1e3e1] px-4 py-3">
          <FileText className="mt-0.5 h-5 w-5 shrink-0 text-[#0d2e5c]" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-bold text-[#1f1f1f]">
              {evidence.documentTitle} <span className="font-normal text-[#444746]">· page {page}</span>
            </h2>
            <p className="truncate text-xs text-[#444746]" data-testid="evidence-values">
              {onThisPage.length > 1 ? `${onThisPage.length} values on this page: ` : ""}
              {(onThisPage.length ? onThisPage : [first]).map((it) => it.label).join("  ·  ")}
            </p>
            {unplaced.length > 0 && (
              <p className="mt-0.5 text-xs text-amber-800">Exact position not recorded — the value is on this page, but it can&apos;t be outlined.</p>
            )}
          </div>
          {canOpenWorkbench && first && (
            <Link href={`/workbench?doc=${encodeURIComponent(evidence.documentId)}&fact=${encodeURIComponent(first.factId)}&from=entity&entity=${encodeURIComponent(entityId)}`}
              className="shrink-0 text-xs font-semibold text-[#0d2e5c] hover:underline">
              Open in Workbench
            </Link>
          )}
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close (Esc)"
            className="shrink-0 rounded-full p-1.5 text-[#444746] hover:bg-[#f0f4f9]">
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 bg-[#e9ecef] p-2">
          {!regions ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-[#444746]">
              <Loader2 className="h-4 w-4 animate-spin" /> Finding the value on the page…
            </div>
          ) : (
            <ScanViewer
              documentId={evidence.documentId}
              page={page}
              pageCount={Math.max(...pages)}
              onPageChange={setPage}
              pageList={pages}
              blocks={NO_BLOCKS}
              selected={null}
              hovered={null}
              onBoxClick={() => {}}
              focusBoxes={boxesHere}
              focusLabel="this value"
            />
          )}
        </div>
      </div>
    </div>
  );
}
