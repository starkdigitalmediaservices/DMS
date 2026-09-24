"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, CircleDashed, History, RotateCcw, X } from "lucide-react";
import type { ReviewBlock, ReviewRow } from "@/types";
import type { FocusTarget } from "./ScanViewer";
import { CellEditor, type BlockActions } from "./BlockCard";
import { STATUS_CHIP, STATUS_LABEL } from "./reviewStyles";
import { humanFieldName } from "@/lib/fieldLabels";

interface Props {
  block: ReviewBlock;
  row: ReviewRow;
  editMode: boolean;
  canVerify: boolean;
  busy: boolean;
  actions: BlockActions;
  onSelect: (t: FocusTarget) => void;
  onHover: (t: FocusTarget | null) => void;
  onNavigate: (row: ReviewRow) => void;
  onClose: () => void;
}

/** One table row as a vertical field list: label -> value. For wide
 *  registers (10+ columns) this replaces sideways scrolling entirely, and
 *  every field keeps the table's edit / undo / history / scan-highlight. */
export default function RowDetailPanel({ block, row, editMode, canVerify, busy, actions, onSelect, onHover, onNavigate, onClose }: Props) {
  const headers = block.headers || [];
  const liveRows = (block.rows || []).filter((r) => !r.deleted);
  const pos = liveRows.findIndex((r) => r.id === row.id);
  const rowNumber = (block.rows || []).findIndex((r) => r.id === row.id) + 1;
  const [editingCol, setEditingCol] = useState<number | null>(null);

  useEffect(() => setEditingCol(null), [row.id]);

  const go = (delta: number) => {
    const next = liveRows[pos + delta];
    if (next) onNavigate(next);
  };

  return (
    <section aria-label={`Row ${rowNumber} details`} data-testid="row-detail" className="rounded-xl border-2 border-[#0d2e5c] bg-white p-3 max-h-[45vh] overflow-y-auto">
      <header className="flex flex-wrap items-center gap-2 mb-2 sticky -top-3 -mx-3 -mt-3 px-3 pt-3 pb-2 bg-white border-b border-[#f0f0f0] z-10">
        <h2 className="text-sm font-bold">Row {rowNumber}</h2>
        {row.page && <span className="text-xs text-[#5f6368]">from page {row.page}</span>}
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_CHIP[row.status]}`}>{STATUS_LABEL[row.status]}</span>
        {row.added && <span className="text-[10px] font-bold uppercase text-amber-800 bg-amber-100 px-1.5 rounded">added</span>}
        <div className="ml-auto flex items-center gap-1">
          {canVerify && editMode && !row.deleted && (
            <button type="button" disabled={busy} onClick={() => actions.verifyRow(block, row, row.status !== "VERIFIED")}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-green-300 text-green-800 text-xs font-semibold hover:bg-green-50 disabled:opacity-40">
              {row.status === "VERIFIED" ? <CircleDashed className="w-3.5 h-3.5" aria-hidden="true" /> : <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />}
              {row.status === "VERIFIED" ? "Remove checked mark" : "Mark row as checked"}
            </button>
          )}
          <button type="button" aria-label="Previous row" disabled={pos <= 0} onClick={() => go(-1)} className="p-1 rounded hover:bg-[#f0f4f9] disabled:opacity-40">
            <ChevronUp className="w-4 h-4" />
          </button>
          <button type="button" aria-label="Next row" disabled={pos < 0 || pos >= liveRows.length - 1} onClick={() => go(1)} className="p-1 rounded hover:bg-[#f0f4f9] disabled:opacity-40">
            <ChevronDown className="w-4 h-4" />
          </button>
          <button type="button" aria-label="Close row details" onClick={onClose} className="p-1 rounded hover:bg-[#f0f4f9]">
            <X className="w-4 h-4" />
          </button>
        </div>
      </header>

      <dl className="divide-y divide-[#f0f0f0]">
        {row.cells.map((cell, col) => (
          <div
            key={col}
            data-testid="row-detail-field"
            data-edited={cell.edited ? "true" : "false"}
            onMouseEnter={() => onHover({ blockId: block.id, rowId: row.id, col })}
            onMouseLeave={() => onHover(null)}
            className={`grid grid-cols-[minmax(7rem,32%)_1fr] gap-3 py-1.5 px-1 ${cell.edited ? "bg-amber-50" : cell.low_confidence ? "bg-orange-50/60" : ""}`}
          >
            <dt className="text-xs font-semibold text-[#444746] break-words pt-0.5">{humanFieldName(headers[col] || "") || `Column ${col + 1}`}</dt>
            <dd className="text-sm min-w-0">
              {editingCol === col ? (
                <CellEditor
                  initial={cell.text}
                  label={`Edit ${humanFieldName(headers[col] || "") || `column ${col + 1}`}`}
                  onCancel={() => setEditingCol(null)}
                  onCommit={async (v) => {
                    setEditingCol(null);
                    if (v !== cell.text) await actions.editCell(block, row, col, v);
                  }}
                  onTab={async (v, back) => {
                    const ok = v === cell.text ? true : await actions.editCell(block, row, col, v);
                    const next = col + (back ? -1 : 1);
                    setEditingCol(ok && next >= 0 && next < row.cells.length ? next : null);
                  }}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => onSelect({ blockId: block.id, rowId: row.id, col })}
                  onDoubleClick={() => { if (editMode && !row.deleted && !busy) setEditingCol(col); }}
                  onKeyDown={(e) => {
                    if (editMode && !row.deleted && !busy && (e.key === "Enter" || e.key === "F2")) { e.preventDefault(); setEditingCol(col); }
                  }}
                  title={editMode && !row.deleted ? "Double-click to correct" : undefined}
                  className={`w-full text-left whitespace-pre-wrap break-words ${editMode && !row.deleted ? "cursor-text" : "cursor-pointer"}`}
                >
                  {cell.text || <span className="text-[#5f6368] italic">blank</span>}
                </button>
              )}
              {editingCol !== col && (cell.edited || cell.low_confidence || cell.history_count > 0) && (
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  {cell.edited && <span className="text-[9px] font-bold uppercase text-amber-800 bg-amber-100 px-1 rounded">corrected</span>}
                  {cell.edited && <span className="text-[11px] text-[#5f6368]">the computer read: “{cell.original || "blank"}”</span>}
                  {cell.low_confidence && !cell.edited && (
                    <span className="inline-flex items-center gap-0.5 text-[11px] text-orange-800">
                      <AlertTriangle className="w-3 h-3" aria-hidden="true" /> computer unsure ({Math.round((cell.confidence || 0) * 100)}% sure)
                    </span>
                  )}
                  {cell.edited && cell.revertable && editMode && (
                    <button type="button" disabled={busy} aria-label={`Undo edit, restore original OCR value ${cell.original}`}
                      title={`Undo — put back what the computer read: "${cell.original}"`}
                      onClick={() => actions.revert({ block_id: block.id, row_id: row.id, col })}
                      className="text-amber-800 hover:text-amber-950 disabled:opacity-40">
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {cell.history_count > 0 && (
                    <button type="button" aria-label={`Show change history for ${humanFieldName(headers[col] || "")}`}
                      onClick={() => actions.showHistory({ block_id: block.id, row_id: row.id, col, label: `${humanFieldName(headers[col] || "")}, row ${rowNumber}` })}
                      className="text-[#5f6368] hover:text-[#1f1f1f]">
                      <History className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
