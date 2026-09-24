"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ShieldCheck, Search, Loader2, AlertCircle, CheckCircle2, Clock } from "lucide-react";
import { api } from "@/lib/api";
import type { ReviewDocumentSummary } from "@/types";
import WorkbenchTabs from "./WorkbenchTabs";

const PAGE_SIZE = 50;

/** Workbench "Documents" tab: every document with extracted values to check,
 *  those still needing review first, each opening in the review view. */
export default function ReviewDocumentsView() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ReviewDocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api.review
      .listDocuments(query, PAGE_SIZE, offset)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e) => !cancelled && setError(e?.message || "Failed to load documents"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [query, offset]);

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="min-h-16 px-3 sm:px-6 py-2 flex flex-wrap items-center gap-2 sm:gap-4 border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <Link href="/drive" className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] px-2 sm:px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]">
          <ArrowLeft className="w-4 h-4" />
          <span className="hidden sm:inline">Back to Drive</span>
        </Link>
        <h1 className="text-base sm:text-lg font-bold flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-[#0d2e5c]" aria-hidden="true" />
          Check extracted data
        </h1>
        <WorkbenchTabs active="documents" />
      </header>

      <main className="max-w-6xl mx-auto px-3 sm:px-6 py-4 sm:py-6">
        <p className="text-sm text-[#444746] mb-4">
          Go through a whole document page by page, comparing what the computer read with the original scan.
        </p>
        <form
          role="search"
          className="flex items-center gap-2 mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setQuery(q);
          }}
        >
          <label className="relative flex-1 max-w-md">
            <span className="sr-only">Search documents by name</span>
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#5f6368]" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search documents by name"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-[#e1e3e1] bg-white text-sm"
            />
          </label>
          <button type="submit" className="px-4 py-2 rounded-xl bg-[#0d2e5c] text-white text-sm font-semibold">Search</button>
        </form>

        {error && (
          <div role="alert" className="mb-4 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" /> {error}
          </div>
        )}

        <div className="bg-white rounded-2xl border border-[#e1e3e1] overflow-hidden">
          <div className="relative overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[#f8f9fa] text-left text-xs text-[#444746]">
                <tr>
                  <th scope="col" className="px-4 py-2.5 font-semibold">Document</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">Pages</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">Values to check</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold min-w-[10rem]">Checked by a person</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">Last checked</th>
                  <th scope="col" className="px-3 py-2.5"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-[#5f6368]"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Loading…</td></tr>
                ) : items.length === 0 ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-[#5f6368]">No documents with extracted values{query ? " match this search" : " yet"}.</td></tr>
                ) : (
                  items.map((d) => (
                    <tr key={d.document_id} data-testid="review-document-row" className="border-t border-[#f0f0f0] hover:bg-[#f8f9fa]">
                      <td className="px-4 py-3 font-medium max-w-xs truncate" title={d.title}>{d.title}</td>
                      <td className="px-3 py-3 tabular-nums">{d.page_count}</td>
                      <td className="px-3 py-3">
                        {d.in_review_count > 0 ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-50 border border-amber-300 text-amber-900 text-xs font-semibold">
                            <Clock className="w-3 h-3" aria-hidden="true" /> {d.in_review_count}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-green-800"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" /> nothing left</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-24 rounded-full bg-[#e1e3e1] overflow-hidden" role="progressbar" aria-valuenow={d.verified_pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${d.verified_pct}% verified`}>
                            <div className="h-full bg-green-600" style={{ width: `${d.verified_pct}%` }} />
                          </div>
                          <span className="text-xs text-[#444746] tabular-nums">{d.verified_count}/{d.fact_count}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3 text-xs text-[#444746]">
                        {d.last_reviewed_at ? (
                          <>
                            {new Date(d.last_reviewed_at + "Z").toLocaleDateString()}
                            {d.last_reviewed_by && <span className="block text-[#5f6368]">by {d.last_reviewed_by}</span>}
                          </>
                        ) : (
                          <span className="text-[#5f6368]">Not started yet</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-right">
                        <Link
                          href={`/workbench?doc=${encodeURIComponent(d.document_id)}&from=documents`}
                          className="inline-flex px-3 py-1.5 rounded-lg bg-[#0d2e5c] text-white text-xs font-semibold hover:bg-[#0945a5]"
                          aria-label={`Open ${d.title}`}
                        >
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between px-4 py-2.5 border-t border-[#f0f0f0] text-xs text-[#444746]">
              <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
              <div className="flex gap-2">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="px-3 py-1 rounded-lg border border-[#e1e3e1] disabled:opacity-40">Previous</button>
                <button type="button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)} className="px-3 py-1 rounded-lg border border-[#e1e3e1] disabled:opacity-40">Next</button>
              </div>
            </div>
          )}
        </div>
        <p className="mt-3 text-xs text-[#5f6368]">
          Only documents whose tables were read by the computer are listed for now. Other documents will appear here later.
        </p>
      </main>
    </div>
  );
}
