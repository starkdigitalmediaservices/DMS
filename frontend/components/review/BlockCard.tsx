"use client";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, CircleDashed, History, Plus, RotateCcw, Trash2, AlertTriangle, Layers } from "lucide-react";
import type { ReviewBlock, ReviewCell, ReviewRow } from "@/types";
import type { FocusTarget } from "./ScanViewer";
import { BLOCK_TYPE_STYLE, STATUS_CHIP, STATUS_LABEL } from "./reviewStyles";

export type ReviewFilter = "all" | "low_confidence" | "flagged" | "unverified";

export interface BlockActions {
  editCell: (block: ReviewBlock, row: ReviewRow, col: number, value: string) => Promise<boolean>;
  editText: (block: ReviewBlock, text: string) => Promise<boolean>;
  revert: (target: { block_id: string; row_id?: string; col?: number }) => void;
  addRow: (block: ReviewBlock, afterRowId: string | null) => void;
  deleteRow: (block: ReviewBlock, row: ReviewRow) => void;
  addBlock: (afterBlock: ReviewBlock | null) => void;
  deleteBlock: (block: ReviewBlock) => void;
  verifyRow: (block: ReviewBlock, row: ReviewRow, verified: boolean) => void;
  verifyBlock: (block: ReviewBlock, verified: boolean) => void;
  showHistory: (target: { block_id: string; row_id?: string; col?: number; label: string }) => void;
  /** Open the row as a vertical field list (no sideways scrolling). */
  openRowDetail: (block: ReviewBlock, row: ReviewRow) => void;
}

interface Props {
  block: ReviewBlock;
  editMode: boolean;
  canVerify: boolean;
  busy: boolean;
  filter: ReviewFilter;
  selected: FocusTarget | null;
  onSelect: (t: FocusTarget) => void;
  onHover: (t: FocusTarget | null) => void;
  actions: BlockActions;
}

function StatusBadge({ status }: { status: ReviewBlock["status"] }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold ${STATUS_CHIP[status]}`}>
      {status === "VERIFIED" ? <CheckCircle2 className="w-3 h-3" aria-hidden="true" /> : null}
      {STATUS_LABEL[status]}
    </span>
  );
}

/** 1,2,3,5,7,8 -> "1–3, 5, 7–8": a 68-page register's header stays one line. */
function pageRanges(pages: number[]): string {
  const out: string[] = [];
  const sorted = Array.from(new Set(pages)).sort((a, b) => a - b);
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i];
    while (i + 1 < sorted.length && sorted[i + 1] === sorted[i] + 1) i++;
    out.push(start === sorted[i] ? String(start) : `${start}–${sorted[i]}`);
  }
  return out.join(", ");
}

function rowMatches(row: ReviewRow, filter: ReviewFilter): boolean {
  if (filter === "all") return true;
  if (filter === "low_confidence") return row.cells.some((c) => c.low_confidence);
  if (filter === "flagged") return row.flags.length > 0 || row.cells.some((c) => c.fact_status === "in_review");
  return row.status !== "VERIFIED";
}

export function blockMatches(block: ReviewBlock, filter: ReviewFilter): boolean {
  if (filter === "all") return true;
  if (block.type === "table") return (block.rows || []).some((r) => !r.deleted && rowMatches(r, filter));
  if (filter === "low_confidence") return block.confidence !== null && block.confidence < 0.6;
  if (filter === "flagged") return block.flags.length > 0;
  return block.status !== "VERIFIED";
}

/** One cell's inline editor: Enter saves, Shift+Enter is a new line, Esc
 *  cancels, blur saves, Tab / Shift+Tab save and move to the next / previous cell. */
export function CellEditor({ initial, label, onCommit, onCancel, onTab }: {
  initial: string; label: string;
  onCommit: (v: string) => void; onCancel: () => void; onTab: (v: string, back: boolean) => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };
  return (
    <textarea
      ref={ref}
      aria-label={label}
      value={value}
      rows={Math.max(1, value.split("\n").length)}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          finish(() => onCommit(value));
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(onCancel);
        } else if (e.key === "Tab") {
          e.preventDefault();
          finish(() => onTab(value, e.shiftKey));
        }
      }}
      onBlur={() => finish(() => onCommit(value))}
      className="w-full min-w-[6rem] resize-none rounded border-2 border-[#0d2e5c] bg-white px-1.5 py-1 text-sm focus:outline-none"
    />
  );
}

function CellView({ cell }: { cell: ReviewCell }) {
  return (
    <span className="whitespace-pre-wrap break-words">
      {cell.text || <span className="text-[#5f6368] italic">empty</span>}
    </span>
  );
}

interface TableRowProps {
  block: ReviewBlock;
  row: ReviewRow;
  rowIdx: number;
  headers: string[];
  /** undefined: row not selected; null: row selected; n: cell n selected */
  selectedCol: number | null | undefined;
  editingCol: number | null;
  editMode: boolean;
  canVerify: boolean;
  busy: boolean;
  actions: BlockActions;
  onSelect: (t: FocusTarget) => void;
  onHover: (t: FocusTarget | null) => void;
  openCell: (row: ReviewRow, col: number) => void;
  setEditing: (e: { rowId: string; col: number } | null) => void;
  moveFrom: (rowIdx: number, col: number, back: boolean) => void;
}

/** One table row. Memoised: a 68-page register has ~6,000 cells, and
 *  re-rendering all of them on every selection, hover or keystroke made the
 *  screen lag; only rows whose own props change re-render now. Rows also
 *  use content-visibility:auto so the browser skips laying out and painting
 *  the thousands that are off screen. */
const TableRow = memo(function TableRow({
  block, row, rowIdx, headers, selectedCol, editingCol, editMode, canVerify, busy, actions,
  onSelect, onHover, openCell, setEditing, moveFrom,
}: TableRowProps) {
  return (
    <tr
      key={row.id}
      id={`row-${block.id}-${row.id}`}
      data-testid="review-row"
      onMouseEnter={() => onHover({ blockId: block.id, rowId: row.id })}
      onMouseLeave={() => onHover(null)}
      className={`group align-top border-b border-[#f0f0f0] [content-visibility:auto] [contain-intrinsic-size:auto_80px] ${selectedCol !== undefined ? "bg-[#e8f0fe]" : ""} ${row.deleted ? "opacity-60" : ""}`}
    >
      <td className="px-1 py-1.5 text-[11px] text-[#5f6368]">
        <button type="button" className="hover:underline font-semibold text-[#0d2e5c]"
          title={`Open row ${rowIdx + 1} as a field list${row.page ? ` (from page ${row.page})` : " (added row)"}`}
          aria-label={`Open row ${rowIdx + 1} details`}
          onClick={() => {
            onSelect({ blockId: block.id, rowId: row.id });
            actions.openRowDetail(block, row);
          }}>
          {rowIdx + 1}
        </button>
        {row.added && <span className="block text-[9px] font-bold text-amber-700">added</span>}
        {row.flags.includes("stitched") && <span title="Continues on another page"><Layers className="w-3 h-3 text-[#5f6368]" aria-label="stitched across pages" /></span>}
      </td>
      {row.cells.map((cell, col) => {
        const isEditing = editingCol === col;
        const cellSelected = selectedCol === col;
        return (
          <td
            key={col}
            data-testid="review-cell"
            data-edited={cell.edited ? "true" : "false"}
            onMouseEnter={() => cell.regions.length && onHover({ blockId: block.id, rowId: row.id, col })}
            className={`relative px-2 py-1.5 min-w-[8rem] max-w-[22rem] ${
              cell.edited ? "bg-amber-50 border-l-2 border-amber-400" : cell.low_confidence ? "bg-orange-50/60 border-l-2 border-dashed border-orange-300" : ""
            } ${cellSelected ? "outline outline-2 outline-[#0d2e5c] -outline-offset-2" : ""} ${row.deleted ? "line-through" : ""}`}
          >
            {isEditing ? (
              <CellEditor
                initial={cell.text}
                label={`Edit ${headers[col] || `column ${col + 1}`}, row ${rowIdx + 1}`}
                onCancel={() => setEditing(null)}
                onCommit={async (v) => {
                  setEditing(null);
                  if (v !== cell.text) await actions.editCell(block, row, col, v);
                }}
                onTab={async (v, back) => {
                  const ok = v === cell.text ? true : await actions.editCell(block, row, col, v);
                  if (ok) moveFrom(rowIdx, col, back);
                  else setEditing(null);
                }}
              />
            ) : (
              <button
                type="button"
                onClick={() => openCell(row, col)}
                title={cell.low_confidence ? `Low confidence (${Math.round((cell.confidence || 0) * 100)}%)` : undefined}
                className={`w-full text-left ${editMode && !row.deleted ? "cursor-text" : "cursor-pointer"}`}
              >
                <CellView cell={cell} />
              </button>
            )}
            {!isEditing && (cell.edited || cell.low_confidence || cell.history_count > 0) && (
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                {cell.edited && <span className="text-[9px] font-bold uppercase text-amber-800 bg-amber-100 px-1 rounded">edited</span>}
                {cell.low_confidence && !cell.edited && (
                  <span className="inline-flex items-center gap-0.5 text-[9px] text-orange-800"><AlertTriangle className="w-2.5 h-2.5" aria-hidden="true" />low</span>
                )}
                {cell.edited && cell.revertable && editMode && (
                  <button type="button" disabled={busy} title={`Undo — original OCR: "${cell.original}"`}
                    aria-label={`Undo edit, restore original OCR value ${cell.original}`}
                    onClick={() => actions.revert({ block_id: block.id, row_id: row.id, col })}
                    className="text-amber-800 hover:text-amber-950 disabled:opacity-40">
                    <RotateCcw className="w-3 h-3" />
                  </button>
                )}
                {cell.edited && cell.changed_elsewhere && (
                  <span className="text-[9px] text-[#5f6368]" title={`Original OCR: "${cell.original}"`}>changed in Workbench</span>
                )}
                {cell.history_count > 0 && (
                  <button type="button" aria-label="Show change history for this cell" title="Change history"
                    onClick={() => actions.showHistory({ block_id: block.id, row_id: row.id, col, label: `${headers[col]}, row ${rowIdx + 1}` })}
                    className="text-[#5f6368] hover:text-[#1f1f1f]">
                    <History className="w-3 h-3" />
                  </button>
                )}
              </div>
            )}
          </td>
        );
      })}
      <td className="px-1 py-1.5 text-right whitespace-nowrap">
        <span className={`inline-block mr-1 text-[9px] font-bold px-1.5 py-0.5 rounded border ${STATUS_CHIP[row.status]}`}>{STATUS_LABEL[row.status]}</span>
        <div className="inline-flex gap-0.5 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-within:opacity-100">
          {row.deleted ? (
            editMode && (
              <button type="button" disabled={busy} onClick={() => actions.revert({ block_id: block.id, row_id: row.id })}
                className="text-[11px] font-semibold text-[#0d2e5c] hover:underline">Restore</button>
            )
          ) : (
            <>
              {canVerify && editMode && (
                <button type="button" disabled={busy} aria-label={row.status === "VERIFIED" ? "Mark row unverified" : "Mark row verified"}
                  title={row.status === "VERIFIED" ? "Unverify row" : "Verify row"}
                  onClick={() => actions.verifyRow(block, row, row.status !== "VERIFIED")}
                  className="p-1 rounded hover:bg-green-50 text-green-700 disabled:opacity-40">
                  {row.status === "VERIFIED" ? <CircleDashed className="w-3.5 h-3.5" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                </button>
              )}
              {editMode && (
                <>
                  <button type="button" disabled={busy} aria-label="Insert row below" title="Insert row below"
                    onClick={() => actions.addRow(block, row.id)} className="p-1 rounded hover:bg-[#f0f4f9] disabled:opacity-40">
                    <Plus className="w-3.5 h-3.5" />
                  </button>
                  <button type="button" disabled={busy} aria-label="Delete row" title="Delete row"
                    onClick={() => actions.deleteRow(block, row)} className="p-1 rounded hover:bg-red-50 text-red-700 disabled:opacity-40">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </>
              )}
              {row.history_count > 0 && (
                <button type="button" aria-label="Row history" title="Row history"
                  onClick={() => actions.showHistory({ block_id: block.id, row_id: row.id, label: `row ${rowIdx + 1}` })}
                  className="p-1 rounded hover:bg-[#f0f4f9]">
                  <History className="w-3.5 h-3.5" />
                </button>
              )}
            </>
          )}
        </div>
      </td>
    </tr>
  );
});

function TableBody({ block, editMode, canVerify, busy, filter, selected, onSelect, onHover, actions }: Props) {
  const [editing, setEditing] = useState<{ rowId: string; col: number } | null>(null);
  const rows = useMemo(() => block.rows || [], [block.rows]);
  const headers = useMemo(() => block.headers || [], [block.headers]);
  const visibleRows = rows.filter((r) => r.deleted || rowMatches(r, filter));

  const openCell = useCallback((row: ReviewRow, col: number) => {
    onSelect({ blockId: block.id, rowId: row.id, col });
    if (editMode && !row.deleted && !busy) setEditing({ rowId: row.id, col });
  }, [block.id, editMode, busy, onSelect]);

  const moveFrom = useCallback((rowIdx: number, col: number, back: boolean) => {
    const live = rows.filter((r) => !r.deleted);
    const pos = live.findIndex((r) => r.id === rows[rowIdx].id);
    let r = pos;
    let c = col + (back ? -1 : 1);
    if (c >= headers.length) { r += 1; c = 0; }
    if (c < 0) { r -= 1; c = headers.length - 1; }
    const next = live[r];
    if (next) {
      setEditing({ rowId: next.id, col: c });
      onSelect({ blockId: block.id, rowId: next.id, col: c });
    } else {
      setEditing(null);
    }
  }, [rows, headers.length, block.id, onSelect]);

  return (
    // Its own bounded scroll box, so both scrollbars are always on screen --
    // a long register's horizontal scrollbar used to sit thousands of pixels
    // down, under the last row. `relative` also keeps absolutely-positioned
    // descendants (the sr-only header label) from widening the page.
    <div className="relative overflow-auto max-h-[calc(var(--list-h,70vh)-4.5rem)] min-h-[10rem] rounded-lg border border-[#f0f0f0]" data-testid="table-scroll">
      <table className="min-w-full text-sm border-collapse">
        <thead className="sticky top-0 z-10 bg-white shadow-[0_1px_0_#e1e3e1]">
          <tr>
            <th scope="col" className="w-8 px-1 py-1.5 text-[11px] text-[#5f6368] font-semibold text-left border-b border-[#e1e3e1]">#</th>
            {headers.map((h) => (
              <th key={h} scope="col" className="px-2 py-1.5 text-[11px] text-[#5f6368] font-semibold text-left border-b border-[#e1e3e1] whitespace-nowrap">{h}</th>
            ))}
            <th scope="col" className="w-28 px-1 py-1.5 border-b border-[#e1e3e1]"><span className="sr-only">Row actions</span></th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row) => {
            const rowIdx = rows.indexOf(row);
            const rowSelected = selected?.blockId === block.id && selected?.rowId === row.id;
            return (
              <TableRow
                key={row.id}
                block={block}
                row={row}
                rowIdx={rowIdx}
                headers={headers}
                selectedCol={rowSelected ? selected?.col ?? null : undefined}
                editingCol={editing?.rowId === row.id ? editing.col : null}
                editMode={editMode}
                canVerify={canVerify}
                busy={busy}
                actions={actions}
                onSelect={onSelect}
                onHover={onHover}
                openCell={openCell}
                setEditing={setEditing}
                moveFrom={moveFrom}
              />
            );
          })}
        </tbody>
      </table>
      {visibleRows.length === 0 && <p className="px-2 py-3 text-xs text-[#5f6368]">No rows match this filter.</p>}
      {editMode && (
        <button type="button" disabled={busy} onClick={() => actions.addRow(block, null)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-[#0d2e5c] hover:underline disabled:opacity-40">
          <Plus className="w-3.5 h-3.5" /> Add row at end
        </button>
      )}
    </div>
  );
}

function TextBody({ block, editMode, busy, actions }: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(block.text || "");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (editing) ref.current?.focus();
  }, [editing]);
  useEffect(() => setValue(block.text || ""), [block.text]);

  const save = async () => {
    if (value !== block.text && !(await actions.editText(block, value))) return;
    setEditing(false);
  };

  if (editing) {
    return (
      <div>
        <textarea
          ref={ref}
          aria-label={`Edit ${block.type}`}
          value={value}
          rows={Math.max(3, value.split("\n").length)}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
            if (e.key === "Escape") { e.preventDefault(); setValue(block.text || ""); setEditing(false); }
          }}
          className="w-full rounded-lg border-2 border-[#0d2e5c] p-2 text-sm focus:outline-none"
        />
        <p className="text-[11px] text-[#5f6368]">Ctrl+Enter to save · Esc to cancel</p>
      </div>
    );
  }
  const Tag = block.type === "heading" ? "h3" : "p";
  return (
    <button type="button" disabled={!editMode || block.deleted || busy} onClick={() => setEditing(true)}
      className={`w-full text-left rounded ${editMode && !block.deleted ? "cursor-text hover:bg-[#f8f9fa]" : "cursor-default"} ${block.edited ? "bg-amber-50 border-l-2 border-amber-400 pl-2" : ""}`}>
      <Tag className={`whitespace-pre-wrap ${block.type === "heading" ? "font-bold text-base" : "text-sm"} ${block.deleted ? "line-through" : ""}`}>
        {block.text || <span className="text-[#5f6368] italic">empty</span>}
      </Tag>
    </button>
  );
}

function BlockCard(props: Props) {
  const { block, editMode, canVerify, busy, selected, onSelect, onHover, actions } = props;
  const style = BLOCK_TYPE_STYLE[block.type];
  const isSelected = selected?.blockId === block.id && !selected.rowId;
  const isText = block.type === "heading" || block.type === "paragraph";

  return (
    <article
      id={`card-${block.id}`}
      data-testid="review-card"
      data-block-id={block.id}
      aria-label={`Block ${block.order + 1}: ${block.title || block.type}`}
      onMouseEnter={() => onHover({ blockId: block.id })}
      onMouseLeave={() => onHover(null)}
      className={`rounded-xl border bg-white p-3 transition-shadow ${isSelected ? "ring-2 ring-[#0d2e5c] shadow-md" : "border-[#e1e3e1]"} ${block.deleted ? "opacity-70" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-2 mb-2">
        <button type="button" onClick={() => onSelect({ blockId: block.id })}
          className="flex items-center gap-2 text-left" title={block.source_pages.length ? `Pages ${pageRanges(block.source_pages)}` : undefined}>
          <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold text-white ${style.labelBg}`}>{block.order + 1}</span>
          <span className={`px-2 py-0.5 rounded-full border text-[11px] font-semibold ${style.chip}`}>{block.type}</span>
          {block.title && <span className="text-sm font-semibold text-[#1f1f1f]">{block.title}</span>}
        </button>
        <StatusBadge status={block.status} />
        {block.added && <span className="text-[10px] font-bold uppercase text-amber-800 bg-amber-100 px-1.5 rounded">added</span>}
        {block.deleted && <span className="text-[10px] font-bold uppercase text-red-800 bg-red-100 px-1.5 rounded">deleted</span>}
        {block.source_pages.length > 0 && <span className="text-[11px] text-[#5f6368]">p. {pageRanges(block.source_pages)}</span>}
        {block.confidence !== null && <span className="text-[11px] text-[#5f6368]">{Math.round(block.confidence * 100)}% conf.</span>}
        <div className="ml-auto flex items-center gap-1">
          {isText && block.edited && block.revertable && editMode && (
            <button type="button" disabled={busy} title={`Undo — original OCR: "${block.original}"`} aria-label="Undo text edit"
              onClick={() => actions.revert({ block_id: block.id })} className="p-1 rounded hover:bg-amber-50 text-amber-800 disabled:opacity-40">
              <RotateCcw className="w-4 h-4" />
            </button>
          )}
          {isText && canVerify && editMode && !block.deleted && (
            <button type="button" disabled={busy} onClick={() => actions.verifyBlock(block, block.status !== "VERIFIED")}
              className="px-2 py-0.5 rounded-lg text-[11px] font-semibold border border-green-300 text-green-800 hover:bg-green-50 disabled:opacity-40">
              {block.status === "VERIFIED" ? "Unverify" : "Verify"}
            </button>
          )}
          {block.history_count > 0 && (
            <button type="button" aria-label="Block history" title="Change history"
              onClick={() => actions.showHistory({ block_id: block.id, label: block.title || block.type })} className="p-1 rounded hover:bg-[#f0f4f9]">
              <History className="w-4 h-4" />
            </button>
          )}
          {editMode && (block.deleted ? (
            <button type="button" disabled={busy} onClick={() => actions.revert({ block_id: block.id })}
              className="text-[11px] font-semibold text-[#0d2e5c] hover:underline">Restore</button>
          ) : (
            <button type="button" disabled={busy} aria-label="Delete block" title="Delete block"
              onClick={() => actions.deleteBlock(block)} className="p-1 rounded hover:bg-red-50 text-red-700 disabled:opacity-40">
              <Trash2 className="w-4 h-4" />
            </button>
          ))}
        </div>
      </header>

      {block.type === "table" ? <TableBody {...props} /> : isText ? <TextBody {...props} /> : (
        <p className="text-xs text-[#5f6368] italic">Image region — nothing to transcribe.</p>
      )}

      {editMode && (
        <button type="button" disabled={busy} onClick={() => actions.addBlock(block)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-[#0d2e5c] hover:underline disabled:opacity-40">
          <Plus className="w-3.5 h-3.5" /> Add text block below
        </button>
      )}
    </article>
  );
}

// Memoised so hover changes elsewhere on the screen don't re-render every card.
export default memo(BlockCard);
