"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertCircle, ArrowLeft, FileSearch, Loader2, RefreshCw, RotateCcw, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import ScanViewer, { type FocusTarget } from "@/components/review/ScanViewer";
import BlockCard, { blockMatches, type BlockActions, type ReviewFilter } from "@/components/review/BlockCard";
import QueueItemCard from "@/components/review/QueueItemCard";
import { humanFieldName } from "@/lib/fieldLabels";
import RowDetailPanel from "@/components/review/RowDetailPanel";
import { useRole } from "@/lib/permissions";
import type { ReviewBlock, ReviewBox, ReviewDocument, ReviewHistoryEntry, ReviewRow } from "@/types";

type Banner = { kind: "error" | "conflict"; message: string } | null;

const FILTERS: { key: ReviewFilter; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "low_confidence", label: "Computer unsure" },
  { key: "flagged", label: "Needs attention" },
  { key: "unverified", label: "Not checked yet" },
];

const NO_BLOCKS: ReviewBlock[] = [];

const HISTORY_ACTION: Record<string, string> = {
  edit_cell: "Changed",
  edit_text: "Changed",
  revert_cell: "Undone (back to what the computer read)",
  revert_text: "Undone (back to what the computer read)",
  revert_all: "All changes undone",
  add_row: "Row added",
  delete_row: "Row removed",
  revert_delete_row: "Removed row put back",
  add_block: "Text added",
  delete_block: "Text removed",
  revert_delete_block: "Removed text put back",
  verify: "Marked as checked",
  unverify: "Check mark removed",
  verify_row: "Row marked as checked",
  unverify_row: "Row check mark removed",
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === "" ? "(empty)" : v;
  return JSON.stringify(v);
}

function DigitisedText({ blocks }: { blocks: ReviewBlock[] }) {
  return (
    <div data-testid="digitised-text" className="bg-white rounded-xl border border-[#e1e3e1] p-5 space-y-4 text-sm leading-relaxed">
      {blocks.filter((b) => !b.deleted).map((b) =>
        b.type === "heading" ? (
          <h2 key={b.id} className="text-lg font-bold">{b.text}</h2>
        ) : b.type === "paragraph" ? (
          <p key={b.id} className="whitespace-pre-wrap">{b.text}</p>
        ) : b.type === "table" ? (
          <div key={b.id} className="overflow-x-auto">
            {b.title && <h3 className="font-semibold mb-1">{b.title}</h3>}
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr>{(b.headers || []).map((h) => <th key={h} scope="col" className="border border-[#e1e3e1] bg-[#f8f9fa] px-2 py-1 text-left">{humanFieldName(h)}</th>)}</tr>
              </thead>
              <tbody>
                {(b.rows || []).filter((r) => !r.deleted).map((r) => (
                  <tr key={r.id}>{r.cells.map((c, i) => <td key={i} className="border border-[#e1e3e1] px-2 py-1 whitespace-pre-wrap">{c.text}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null
      )}
      {blocks.length === 0 && <p className="text-[#5f6368]">No extracted content.</p>}
    </div>
  );
}

export interface ReviewScreenProps {
  documentId: string;
  initialPage?: number;
  /** Open with this fact's cell selected (and its page shown) -- used when
   *  arriving from a Workbench queue item. */
  focusFactId?: string | null;
  /** Arrived from a Workbench queue item: pin it above the blocks with its actions. */
  showQueueItem?: boolean;
  backHref: string;
  backLabel: string;
}

/** The document review view: scan on the left, extracted blocks on the right. */
export default function ReviewScreen({ documentId, initialPage = 1, focusFactId, showQueueItem = false, backHref, backLabel }: ReviewScreenProps) {
  const { can: roleCan } = useRole();
  const router = useRouter();

  const [doc, setDoc] = useState<ReviewDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [page, setPage] = useState(initialPage);
  // No edit toggle: anyone allowed to edit corrects a value by
  // double-clicking it (single click only selects / highlights).
  const [tab, setTab] = useState<"sections" | "text">("sections");
  const [filter, setFilter] = useState<ReviewFilter>("all");
  const [selected, setSelected] = useState<FocusTarget | null>(null);
  const [hovered, setHovered] = useState<FocusTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<Banner>(null);
  const [confirmRevertAll, setConfirmRevertAll] = useState(false);
  const [history, setHistory] = useState<{ label: string; entries: ReviewHistoryEntry[] | null } | null>(null);
  // Row opened as a vertical field list (ids only: the row itself is always
  // read from the latest document, so it shows exactly what was saved).
  const [detail, setDetail] = useState<{ blockId: string; rowId: string } | null>(null);
  const [addAfter, setAddAfter] = useState<{ block: ReviewBlock | null; type: "heading" | "paragraph"; text: string } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // Height of the scrolling block list, so a table's own scroll box can be
  // sized to fit inside it and keep its horizontal scrollbar on screen.
  const [listH, setListH] = useState(0);
  useEffect(() => {
    const el = listRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setListH(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [doc]);

  // Scan / extracted-text split, in % of the width for the scan; remembered
  // per browser (a convenience, so storage failures are ignored).
  const splitRef = useRef<HTMLElement>(null);
  const [split, setSplit] = useState(50);
  useEffect(() => {
    try {
      const saved = parseFloat(localStorage.getItem("review_split") || "");
      if (saved >= 25 && saved <= 75) setSplit(saved);
    } catch {}
  }, []);
  const setSplitSaved = useCallback((v: number) => {
    const clamped = Math.min(75, Math.max(25, v));
    setSplit(clamped);
    try {
      localStorage.setItem("review_split", String(clamped));
    } catch {}
  }, []);
  const startSplitDrag = (e: React.PointerEvent) => {
    const main = splitRef.current;
    if (!main) return;
    e.preventDefault();
    const rect = main.getBoundingClientRect();
    const move = (ev: PointerEvent) => setSplitSaved(((ev.clientX - rect.left) / rect.width) * 100);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const d = await api.review.get(documentId);
      setDoc(d);
      setBanner(null);
    } catch (e: any) {
      setLoadError(e instanceof ApiError && e.status === 404 ? "Document not found, or you don't have access to it." : e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
      return;
    }
    if (documentId) load();
  }, [documentId, load, router]);

  const pageCount = doc?.page_count || 1;
  const editMode = !!doc?.permissions.can_edit;

  /** Runs one mutation; on success shows exactly what the server saved. */
  const run = useCallback(async (fn: (version: number) => Promise<ReviewDocument>): Promise<boolean> => {
    if (!doc) return false;
    setBusy(true);
    try {
      const next = await fn(doc.version);
      setDoc(next);
      setBanner(null);
      if (next.revert_all?.skipped.length) {
        setBanner({ kind: "error", message: `${next.revert_all.skipped.length} value(s) were changed outside the review screen since and were left as they are.` });
      }
      return true;
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        setBanner({ kind: "conflict", message: e.message || "Someone else changed this document. Reload to see the latest." });
      } else {
        setBanner({ kind: "error", message: e?.message || "Your change couldn't be saved. Please try again." });
      }
      return false;
    } finally {
      setBusy(false);
    }
  }, [doc]);

  const select = useCallback((target: FocusTarget, fromScan = false) => {
    setSelected(target);
    if (!doc) return;
    const block = doc.blocks.find((b) => b.id === target.blockId);
    if (!fromScan && block) {
      // A card on another page switches the scan to that page.
      const row = target.rowId ? block.rows?.find((r) => r.id === target.rowId) : undefined;
      const cell = row && target.col !== undefined ? row.cells[target.col] : undefined;
      const targetPage = cell?.regions[0]?.page || row?.page || (block.source_pages.includes(page) ? page : block.source_pages[0]);
      if (targetPage && targetPage !== page) setPage(targetPage);
    }
    if (fromScan) {
      if (filter !== "all") setFilter("all");
      setTab("sections");
      requestAnimationFrame(() => {
        const el = document.getElementById(target.rowId ? `row-${target.blockId}-${target.rowId}` : `card-${target.blockId}`);
        el?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
  }, [doc, page, filter]);

  const actions: BlockActions = useMemo(() => ({
    editCell: (block, row, col, value) => run((v) => api.review.editCell(documentId, v, block.id, row.id, col, value, row.cells[col].fact_version)),
    editText: (block, text) => run((v) => api.review.editText(documentId, v, block.id, text)),
    revert: (target) => { run((v) => api.review.revert(documentId, v, target)); },
    addRow: (block, afterRowId) => { run((v) => api.review.addRow(documentId, v, block.id, afterRowId)); },
    deleteRow: (block, row: ReviewRow) => { run((v) => api.review.deleteRow(documentId, v, block.id, row.id)); },
    addBlock: (afterBlock) => setAddAfter({ block: afterBlock, type: "paragraph", text: "" }),
    deleteBlock: (block) => { run((v) => api.review.deleteBlock(documentId, v, block.id)); },
    verifyRow: (block, row, verified) => {
      const factVersions = Object.fromEntries(row.cells.filter((c) => c.fact_id && c.fact_version !== undefined).map((c) => [c.fact_id!, c.fact_version!]));
      run((v) => api.review.verify(documentId, v, { block_id: block.id, row_id: row.id, verified, fact_versions: factVersions }));
    },
    verifyBlock: (block, verified) => { run((v) => api.review.verify(documentId, v, { block_id: block.id, verified })); },
    openRowDetail: (block, row) => setDetail({ blockId: block.id, rowId: row.id }),
    showHistory: async ({ block_id, row_id, col, label }) => {
      setHistory({ label, entries: null });
      try {
        const res = await api.review.history(documentId, block_id, row_id, col);
        setHistory({ label, entries: res.entries });
      } catch (e: any) {
        setHistory(null);
        setBanner({ kind: "error", message: e?.message || "Could not load history" });
      }
    },
  }), [documentId, run]);


  const detailBlock = detail ? doc?.blocks.find((b) => b.id === detail.blockId) : undefined;
  const detailRow = detail ? detailBlock?.rows?.find((r) => r.id === detail.rowId) : undefined;

  const visibleBlocks = useMemo(() => (doc?.blocks || []).filter((b) => b.deleted || blockMatches(b, filter)), [doc, filter]);

  // Where the queue item was read from (outlined in amber on the scan), and
  // whether it is a table cell here at all -- margin notes, join mismatches
  // and continuation questions are not, so the scan goes to their page.
  const [focusBoxes, setFocusBoxes] = useState<ReviewBox[]>([]);
  const [focusIsCell, setFocusIsCell] = useState<boolean | null>(null);
  const [focusCell, setFocusCell] = useState<{ blockId: string; rowId: string; col: number } | null>(null);
  // Arriving from the list: show only that one item (card + its outline on
  // the scan); the rest of the document is one click away.
  const [showWhole, setShowWhole] = useState(false);
  const focusMode = showQueueItem && !!focusFactId && !showWhole;
  useEffect(() => {
    if (focusIsCell === false && focusBoxes.length) setPage(focusBoxes[0].page);
  }, [focusIsCell, focusBoxes]);

  // Arriving from a queue item: select that fact's cell once, show its page,
  // and scroll its row into view.
  const focused = useRef(false);
  useEffect(() => {
    if (!doc || !focusFactId || focused.current) return;
    focused.current = true;
    for (const block of doc.blocks) {
      for (const row of block.rows || []) {
        const col = row.cells.findIndex((c) => c.fact_id === focusFactId);
        if (col >= 0) {
          setFocusIsCell(true);
          setFocusCell({ blockId: block.id, rowId: row.id, col });
          select({ blockId: block.id, rowId: row.id, col });
          setTimeout(() => document.getElementById(`row-${block.id}-${row.id}`)?.scrollIntoView({ block: "center" }), 150);
          return;
        }
      }
    }
    setFocusIsCell(false);
  }, [doc, focusFactId, select]);

  const focusCellLive = (() => {
    if (!focusCell || !doc) return null;
    const block = doc.blocks.find((b) => b.id === focusCell.blockId);
    const row = block?.rows?.find((r) => r.id === focusCell.rowId);
    const cell = row?.cells[focusCell.col];
    return block && row && cell ? { block, row, col: focusCell.col, cell } : null;
  })();

  if (!documentId) {
    return <p className="p-8 text-sm">No document selected. <Link href={backHref} className="underline">{backLabel}</Link></p>;
  }

  return (
    <div className="h-screen flex flex-col bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="min-h-14 px-3 sm:px-5 py-2 flex flex-wrap items-center gap-2 sm:gap-3 border-b border-[#e1e3e1]/60 bg-white sticky top-0 z-20">
        <Link href={backHref} className="flex items-center gap-1.5 text-sm text-[#444746] hover:text-[#1f1f1f] px-2 py-1.5 rounded-lg hover:bg-[#f0f4f9]">
          <ArrowLeft className="w-4 h-4" /> <span className="hidden sm:inline">{backLabel}</span>
        </Link>
        <h1 className="flex items-center gap-2 text-base font-bold truncate min-w-0">
          <FileSearch className="w-5 h-5 text-[#0d2e5c] shrink-0" aria-hidden="true" />
          <span className="truncate">{doc?.title || "Review"}</span>
        </h1>
        {doc && (
          <>
            <span className="text-xs text-[#5f6368]">{doc.page_count} {doc.page_count === 1 ? "page" : "pages"}</span>
            <span data-testid="review-badge" className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${doc.is_clean ? "bg-[#f0f4f9] border-[#e1e3e1] text-[#444746]" : "bg-amber-50 border-amber-300 text-amber-900"}`}>
              {doc.is_clean ? "No corrections yet" : `${doc.edit_count} ${doc.edit_count === 1 ? "correction" : "corrections"}`}
            </span>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          {doc?.permissions.can_revert_all && !focusMode && (
            <button type="button" disabled={busy || doc.is_clean} onClick={() => setConfirmRevertAll(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border border-red-300 text-red-800 hover:bg-red-50 disabled:opacity-40">
              <RotateCcw className="w-3.5 h-3.5" /> Undo all changes
            </button>
          )}
        </div>
      </header>

      {banner && (
        <div role="alert" className={`mx-3 sm:mx-5 mt-3 flex items-center gap-2 px-4 py-2.5 rounded-xl border text-sm ${banner.kind === "conflict" ? "bg-amber-50 border-amber-300 text-amber-900" : "bg-red-50 border-red-200 text-red-700"}`}>
          <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
          <span className="flex-1">{banner.message}</span>
          {banner.kind === "conflict" && (
            <button type="button" onClick={load} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-900 text-white text-xs font-bold">
              <RefreshCw className="w-3.5 h-3.5" /> Reload
            </button>
          )}
          <button type="button" aria-label="Dismiss" onClick={() => setBanner(null)} className="p-1 rounded hover:bg-black/5"><X className="w-4 h-4" /></button>
        </div>
      )}

      {loading && !doc ? (
        <div className="flex-1 flex items-center justify-center gap-2 text-sm text-[#5f6368]"><Loader2 className="w-4 h-4 animate-spin" /> Loading review…</div>
      ) : loadError ? (
        <p role="alert" className="p-8 text-sm text-red-700">{loadError}</p>
      ) : doc ? (
        <main
          ref={splitRef}
          className="flex-1 min-h-0 grid grid-cols-1 lg:[grid-template-columns:var(--split)_12px_1fr] gap-3 lg:gap-0 p-3 sm:p-4 overflow-y-auto lg:overflow-hidden"
          style={{ ["--split" as any]: `${split}%` }}
        >
          <div className="h-[60vh] lg:h-full min-h-0 min-w-0">
            <ScanViewer documentId={documentId} page={Math.min(page, pageCount)} pageCount={pageCount} onPageChange={setPage}
              blocks={focusMode ? NO_BLOCKS : doc.blocks} selected={selected} hovered={hovered} onBoxClick={(t) => select(t, true)}
              focusBoxes={focusBoxes} />
          </div>

          {/* Drag to give the scan or the extracted text more room (desktop). */}
          <button
            type="button"
            aria-label={`Resize: scan takes ${Math.round(split)}% of the width. Drag, or use the left and right arrow keys.`}
            onPointerDown={startSplitDrag}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") setSplitSaved(split - 5);
              if (e.key === "ArrowRight") setSplitSaved(split + 5);
            }}
            className="hidden lg:flex items-center justify-center cursor-col-resize group focus:outline-none touch-none"
          >
            <span aria-hidden="true" className="h-12 w-1 rounded-full bg-[#c4c7c5] group-hover:bg-[#0d2e5c] group-focus:bg-[#0d2e5c]" />
          </button>

          <section aria-label="Extracted content" className="flex flex-col min-h-0 min-w-0">
            {showQueueItem && focusFactId && (
              <div className="mb-2">
                <QueueItemCard
                  factId={focusFactId} docVersion={doc?.version} canReview={roleCan("facts.review")}
                  onRegions={setFocusBoxes} onChanged={load} backHref={backHref}
                  cell={focusCellLive ? { text: focusCellLive.cell.text, original: focusCellLive.cell.original, edited: focusCellLive.cell.edited, revertable: focusCellLive.cell.revertable } : null}
                  onSaveCell={focusCellLive ? (v) => actions.editCell(focusCellLive.block, focusCellLive.row, focusCellLive.col, v) : undefined}
                  onUndoCell={focusCellLive ? () => actions.revert({ block_id: focusCellLive.block.id, row_id: focusCellLive.row.id, col: focusCellLive.col }) : undefined}
                />
                <button type="button" onClick={() => setShowWhole((v) => !v)}
                  className="mt-2 text-xs font-semibold text-[#0d2e5c] hover:underline">
                  {focusMode ? "Show the whole document" : "Show only this item"}
                </button>
              </div>
            )}
            {!focusMode && (<>
            {detailBlock && detailRow && tab === "sections" && (
              <div className="mb-2">
                <RowDetailPanel
                  block={detailBlock}
                  row={detailRow}
                  editMode={editMode}
                  canVerify={doc?.permissions.can_verify ?? false}
                  busy={busy}
                  actions={actions}
                  onSelect={select}
                  onHover={setHovered}
                  onNavigate={(r) => {
                    setDetail({ blockId: detailBlock.id, rowId: r.id });
                    select({ blockId: detailBlock.id, rowId: r.id });
                    document.getElementById(`row-${detailBlock.id}-${r.id}`)?.scrollIntoView({ block: "nearest" });
                  }}
                  onClose={() => setDetail(null)}
                />
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <div role="tablist" aria-label="View" className="flex gap-1">
                {(["sections", "text"] as const).map((k) => (
                  <button key={k} role="tab" aria-selected={tab === k} type="button" onClick={() => setTab(k)}
                    className={`px-3 py-1.5 rounded-full text-xs font-bold border ${tab === k ? "bg-[#0d2e5c] text-white border-[#0d2e5c]" : "bg-white text-[#444746] border-[#e1e3e1]"}`}>
                    {k === "sections" ? "Extracted values" : "Clean copy (read only)"}
                  </button>
                ))}
              </div>
              {tab === "sections" && (
                <label className="ml-auto flex items-center gap-1.5 text-xs text-[#444746]">
                  Show
                  <select value={filter} onChange={(e) => setFilter(e.target.value as ReviewFilter)}
                    className="rounded-lg border border-[#e1e3e1] bg-white px-2 py-1 text-xs">
                    {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                  </select>
                </label>
              )}
            </div>

            <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto space-y-3 pr-1"
              style={listH ? ({ ["--list-h" as any]: `${listH}px` } as React.CSSProperties) : undefined}>
              {tab === "text" ? (
                <DigitisedText blocks={doc.blocks} />
              ) : (
                <>
                  {editMode && doc.blocks.length > 0 && (
                    <p className="text-xs text-[#444746]">Double-click any value or text to correct it. Press Enter to save, Esc to cancel.</p>
                  )}
                  {doc.blocks.length === 0 && (
                    <p className="text-sm text-[#5f6368] bg-white rounded-xl border border-[#e1e3e1] p-4">
                      No extracted blocks for this document yet (it has no template extraction). You can still add text blocks in edit mode.
                    </p>
                  )}
                  {visibleBlocks.map((block) => (
                    <BlockCard key={block.id} block={block} editMode={editMode} canVerify={doc.permissions.can_verify} busy={busy}
                      filter={filter} selected={selected} onSelect={select} onHover={setHovered} actions={actions} />
                  ))}
                  {editMode && (
                    <button type="button" disabled={busy} onClick={() => setAddAfter({ block: null, type: "paragraph", text: "" })}
                      className="w-full py-2 rounded-xl border-2 border-dashed border-[#c4c7c5] text-xs font-semibold text-[#0d2e5c] hover:bg-white disabled:opacity-40">
                      + Add missing text at the end
                    </button>
                  )}
                </>
              )}
            </div>
            </>)}
          </section>
        </main>
      ) : null}

      <ConfirmModal
        isOpen={confirmRevertAll}
        title="Undo all changes?"
        message={`This puts the whole document back exactly as the computer first read it, removing all ${doc?.edit_count ?? 0} correction(s). A record of every change is kept.`}
        type="danger"
        confirmText="Undo all changes"
        onClose={() => setConfirmRevertAll(false)}
        onConfirm={() => {
          setConfirmRevertAll(false);
          run((v) => api.review.revertAll(documentId, v));
        }}
      />

      {history && (
        <div role="dialog" aria-modal="true" aria-label="Change history" className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-bold">History — {history.label}</h2>
              <button type="button" aria-label="Close" onClick={() => setHistory(null)} className="p-1 rounded hover:bg-[#f0f4f9]"><X className="w-4 h-4" /></button>
            </div>
            {!history.entries ? (
              <p className="text-sm text-[#5f6368] flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</p>
            ) : history.entries.length === 0 ? (
              <p className="text-sm text-[#5f6368]">No changes recorded.</p>
            ) : (
              <ol className="space-y-2">
                {history.entries.map((h) => (
                  <li key={h.id} className="text-sm border-b border-[#f0f0f0] pb-2">
                    <div className="flex justify-between gap-2 text-xs text-[#5f6368]">
                      <span className="font-semibold text-[#1f1f1f]">{HISTORY_ACTION[h.action] || h.action.replace(/_/g, " ")}</span>
                      <time dateTime={h.created_at}>{new Date(h.created_at + "Z").toLocaleString()}</time>
                    </div>
                    <div className="text-xs text-[#5f6368]">by {h.user_name || h.user_id}</div>
                    {(h.old_value !== null || h.new_value !== null) && h.action !== "revert_all" && (
                      <div className="mt-1 text-xs">
                        <span className="line-through text-red-700">{formatValue(h.old_value)}</span>
                        {" → "}
                        <span className="text-green-800">{formatValue(h.new_value)}</span>
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}

      {addAfter && (
        <div role="dialog" aria-modal="true" aria-label="Add text block" className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <form
            className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const a = addAfter;
              const ok = await run((v) => api.review.addBlock(documentId, v, a.type, a.text, a.block?.id ?? null, page));
              if (ok) setAddAfter(null);
            }}
          >
            <h2 className="font-bold">{addAfter.block ? `Add text below block ${addAfter.block.order + 1}` : "Add text block at end"}</h2>
            <p className="text-xs text-[#5f6368]">For text the OCR missed. It will be marked “added”.</p>
            <fieldset className="flex gap-4 text-sm">
              <legend className="sr-only">Block type</legend>
              {(["paragraph", "heading"] as const).map((k) => (
                <label key={k} className="flex items-center gap-1.5">
                  <input type="radio" name="block-type" checked={addAfter.type === k} onChange={() => setAddAfter({ ...addAfter, type: k })} />
                  {k}
                </label>
              ))}
            </fieldset>
            <label className="block text-sm">
              <span className="sr-only">Text</span>
              <textarea value={addAfter.text} rows={4} onChange={(e) => setAddAfter({ ...addAfter, text: e.target.value })}
                className="w-full rounded-lg border border-[#c4c7c5] p-2 text-sm" placeholder="Text…" />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setAddAfter(null)} className="px-3 py-1.5 rounded-lg text-sm border border-[#e1e3e1]">Cancel</button>
              <button type="submit" disabled={busy || !addAfter.text.trim()} className="px-3 py-1.5 rounded-lg text-sm font-bold bg-[#0d2e5c] text-white disabled:opacity-40">Add</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
