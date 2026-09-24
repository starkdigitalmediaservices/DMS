"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Minus, Plus, Maximize } from "lucide-react";
import { api } from "@/lib/api";
import type { ReviewBlock, ReviewBox } from "@/types";
import { BLOCK_TYPE_STYLE } from "./reviewStyles";

export interface FocusTarget {
  blockId: string;
  rowId?: string;
  col?: number;
}

interface Props {
  documentId: string;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  blocks: ReviewBlock[];
  selected: FocusTarget | null;
  hovered: FocusTarget | null;
  onBoxClick: (target: FocusTarget) => void;
}

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;

function union(boxes: ReviewBox[]): Omit<ReviewBox, "page"> | null {
  if (!boxes.length) return null;
  const x0 = Math.min(...boxes.map((b) => b.x));
  const y0 = Math.min(...boxes.map((b) => b.y));
  const x1 = Math.max(...boxes.map((b) => b.x + b.w));
  const y1 = Math.max(...boxes.map((b) => b.y + b.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

const pct = (b: Omit<ReviewBox, "page">) => ({
  left: `${b.x * 100}%`,
  top: `${b.y * 100}%`,
  width: `${b.w * 100}%`,
  height: `${b.h * 100}%`,
});

/** Left pane: the scanned page with every block's box drawn over it. */
export default function ScanViewer({ documentId, page, pageCount, onPageChange, blocks, selected, hovered, onBoxClick }: Props) {
  const [zoom, setZoom] = useState(1);
  const [src, setSrc] = useState<string | null>(null);
  // Overlays are positioned in % of the image, so nothing can be scrolled
  // to until the image has its real height.
  const [imgLoaded, setImgLoaded] = useState(false);
  const [error, setError] = useState("");
  const cache = useRef(new Map<number, string>());
  const scroller = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setImgLoaded(false);
    const cached = cache.current.get(page);
    if (cached) {
      setSrc(cached);
      return;
    }
    setSrc(null);
    api.review
      .pageImage(documentId, page)
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        cache.current.set(page, url);
        setSrc(url);
      })
      .catch((e) => !cancelled && setError(e?.message || "Could not load this page"));
    return () => {
      cancelled = true;
    };
  }, [documentId, page]);

  useEffect(() => {
    const urls = cache.current;
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  // Everything to draw on this page: block outlines, then row boxes built
  // from the cells' own regions on THIS page (a stitched row highlights the
  // part that is actually on the page being shown).
  const overlays = useMemo(() => {
    const out: { key: string; target: FocusTarget; box: Omit<ReviewBox, "page">; kind: "block" | "row" | "cell"; block: ReviewBlock; label?: string }[] = [];
    for (const block of blocks) {
      if (block.deleted) continue;
      const bb = block.bbox?.[String(page)];
      if (bb) out.push({ key: `b-${block.id}`, target: { blockId: block.id }, box: bb, kind: "block", block, label: String(block.order + 1) });
      for (const row of block.rows || []) {
        if (row.deleted) continue;
        const regions = row.cells.flatMap((c) => c.regions || []).filter((r) => r.page === page);
        const rb = union(regions);
        if (rb) out.push({ key: `r-${block.id}-${row.id}`, target: { blockId: block.id, rowId: row.id }, box: rb, kind: "row", block });
      }
    }
    return out;
  }, [blocks, page]);

  const focusedCellBox = useMemo(() => {
    const t = selected?.col !== undefined ? selected : hovered?.col !== undefined ? hovered : null;
    if (!t) return null;
    const cell = blocks.find((b) => b.id === t.blockId)?.rows?.find((r) => r.id === t.rowId)?.cells[t.col!];
    const onPage = (cell?.regions || []).filter((r) => r.page === page);
    return union(onPage);
  }, [blocks, selected, hovered, page]);

  // A block box lights up only when the block itself is selected/hovered;
  // with a row selected, only that row's box does.
  const isActive = (t: FocusTarget, ref: FocusTarget | null) =>
    !!ref && ref.blockId === t.blockId && (t.rowId === undefined ? ref.rowId === undefined : ref.rowId === t.rowId);

  // Scroll the selected box into view when selection comes from the card list.
  useEffect(() => {
    if (!selected || !scroller.current || !imgLoaded) return;
    const el = scroller.current.querySelector<HTMLElement>(
      selected.rowId ? `[data-overlay="r-${selected.blockId}-${selected.rowId}"]` : `[data-overlay="b-${selected.blockId}"]`
    );
    el?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [selected, page, imgLoaded]);

  const setZoomClamped = useCallback((z: number) => setZoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(z * 100) / 100))), []);

  const onPointerDown = (e: React.PointerEvent) => {
    if (!scroller.current || e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, left: scroller.current.scrollLeft, top: scroller.current.scrollTop, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || !scroller.current) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    scroller.current.scrollLeft = d.left - dx;
    scroller.current.scrollTop = d.top - dy;
  };
  const endDrag = () => {
    // Keep `moved` readable by the click that follows this pointerup.
    setTimeout(() => (drag.current = null), 0);
  };
  const clickBox = (t: FocusTarget) => {
    if (drag.current?.moved) return;
    onBoxClick(t);
  };

  return (
    <section aria-label="Scanned page" className="flex flex-col h-full min-h-0 bg-[#e9ecef] rounded-xl border border-[#e1e3e1] overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 bg-white border-b border-[#e1e3e1] text-sm">
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => onPageChange(page - 1)}
            className="p-1.5 rounded-lg hover:bg-[#f0f4f9] disabled:opacity-40">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="tabular-nums text-[#444746]" data-testid="page-indicator">page {page} / {pageCount}</span>
          <button type="button" aria-label="Next page" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}
            className="p-1.5 rounded-lg hover:bg-[#f0f4f9] disabled:opacity-40">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Zoom out" disabled={zoom <= MIN_ZOOM} onClick={() => setZoomClamped(zoom - 0.2)}
            className="p-1.5 rounded-lg hover:bg-[#f0f4f9] disabled:opacity-40">
            <Minus className="w-4 h-4" />
          </button>
          <span className="w-12 text-center tabular-nums text-[#444746]" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label="Zoom in" disabled={zoom >= MAX_ZOOM} onClick={() => setZoomClamped(zoom + 0.2)}
            className="p-1.5 rounded-lg hover:bg-[#f0f4f9] disabled:opacity-40">
            <Plus className="w-4 h-4" />
          </button>
          <button type="button" aria-label="Fit to width" onClick={() => setZoom(1)} className="p-1.5 rounded-lg hover:bg-[#f0f4f9]">
            <Maximize className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div
        ref={scroller}
        className="relative flex-1 min-h-0 overflow-auto cursor-grab active:cursor-grabbing select-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        onWheel={(e) => {
          if (e.ctrlKey) {
            e.preventDefault();
            setZoomClamped(zoom - Math.sign(e.deltaY) * 0.1);
          }
        }}
      >
        {error && <p role="alert" className="p-6 text-sm text-red-700">{error}</p>}
        {!src && !error && (
          <div className="flex items-center justify-center h-full text-[#5f6368] text-sm gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading page {page}…
          </div>
        )}
        {src && (
          <div className="relative mx-auto my-3 shadow-md bg-white" style={{ width: `${zoom * 100}%`, minWidth: zoom < 1 ? undefined : "100%" }}>
            {/* eslint-disable-next-line @next/next/no-img-element -- object URL of an authenticated blob */}
            <img src={src} alt={`Scanned page ${page}`} draggable={false} className="block w-full h-auto" onLoad={() => setImgLoaded(true)} />
            {overlays.map((o) => {
              const style = BLOCK_TYPE_STYLE[o.block.type];
              const active = isActive(o.target, selected);
              const hover = isActive(o.target, hovered);
              const isRow = o.kind === "row";
              return (
                <button
                  key={o.key}
                  type="button"
                  data-overlay={o.key}
                  aria-label={isRow ? `Row on page ${page} of block ${o.block.order + 1}` : `Block ${o.block.order + 1}: ${o.block.type}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    clickBox(o.target);
                  }}
                  className={`absolute transition-colors ${isRow ? "border" : "border-2"} ${
                    active ? `${style.activeBorder} ${style.activeFill}` : hover ? `${style.border} ${style.hoverFill}` : `${style.border} ${isRow ? "border-dashed border-opacity-40" : ""} bg-transparent hover:bg-black/5`
                  }`}
                  style={{ ...pct(o.box), zIndex: isRow ? 2 : 1 }}
                >
                  {o.label && (
                    <span className={`absolute -top-5 left-0 px-1.5 text-[10px] font-bold rounded ${style.labelBg} text-white`}>
                      {o.label}
                    </span>
                  )}
                </button>
              );
            })}
            {focusedCellBox && (
              <div aria-hidden="true" className="absolute border-2 border-amber-500 bg-amber-300/30 pointer-events-none" style={{ ...pct(focusedCellBox), zIndex: 3 }} />
            )}
          </div>
        )}
      </div>
    </section>
  );
}
