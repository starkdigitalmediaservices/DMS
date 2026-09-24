import React from "react";
import Link from "next/link";
import { FileText, Download, User, Calendar, Tag, Eye, FileSearch } from "lucide-react";
import { Card } from "../ui/Card";
import type { SearchResult } from "@/types";
import { Badge } from "../ui/Badge";

interface ResultCardProps {
  result: SearchResult;
  onPreview?: (result: SearchResult) => void;
  reranked?: boolean;
  grounded?: boolean;
}

export const ResultCard: React.FC<ResultCardProps> = ({ result, onPreview, reranked = true, grounded = true }) => {
  const createMarkup = (html: string) => {
    return { __html: html };
  };

  const getFileExtension = (filename: string): string => {
    const parts = filename.split(".");
    return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : "";
  };

  const ext = getFileExtension(result.document_name);

  const getIconColor = (extension: string) => {
    if (extension === "pdf") return "text-red-500 bg-red-500/10 border-red-500/20";
    if (extension === "txt") return "text-blue-500 bg-blue-500/10 border-blue-500/20";
    if (extension === "docx" || extension === "doc") return "text-success bg-success/10 border-success/20";
    return "text-primary bg-primary/10 border-primary/20";
  };

  const getMatchGlow = (score: number) => {
    const pct = Math.round(score * 100);
    if (pct > 80) return "text-success bg-success/10 border-success/20 shadow-[0_0_15px_rgba(16,185,129,0.15)]";
    if (pct > 50) return "text-yellow-500 bg-yellow-500/10 border-yellow-500/20";
    if (pct > 20) return "text-primary bg-primary/10 border-primary/20";
    return "text-textMuted bg-surface border-borderDark";
  };

  const getProgressBarColor = (score: number) => {
    const pct = Math.round(score * 100);
    if (pct > 80) return "bg-success";
    if (pct > 50) return "bg-yellow-500";
    if (pct > 20) return "bg-primary";
    return "bg-textMuted";
  };

  const matchPct = Math.round(result.score * 100);

  // Extract custom metadata items
  const metadata = (result.metadata || {}) as Record<string, any>;
  const author = metadata.author as string | undefined;
  const date = metadata.date as string | undefined;

  const isLowFocus = !grounded || !reranked;

  return (
    <Card 
      glow={!isLowFocus} 
      className={`group animate-fadeIn flex flex-col gap-4 relative transition-all duration-300 ${
        isLowFocus 
          ? "opacity-60 bg-[#f8f9fa]/90 border-dashed border-[#d3d7dc] shadow-none hover:opacity-100 hover:bg-white hover:border-[#0d2e5c]/40" 
          : "hover:border-primary/50"
      }`}
    >
      {/* File Title and Page */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3 overflow-hidden">
          <div className={`p-2.5 rounded-lg border transition-colors ${getIconColor(ext)} ${isLowFocus ? "opacity-70" : ""}`}>
            <FileText className="w-5 h-5" />
          </div>
          <div className="flex flex-col min-w-0">
            <h4 
              className={`font-bold text-base truncate transition-colors ${
                isLowFocus ? "text-[#747775] group-hover:text-primary" : "text-textMain group-hover:text-primary"
              }`} 
              title={result.document_name}
            >
              {result.document_name}
            </h4>
            
            {/* Author and Date if present */}
            {(author || date) && (
              <div className="flex items-center gap-3 mt-1 text-xs text-textMuted flex-wrap">
                {author && (
                  <span className="flex items-center gap-1">
                    <User className="w-3.5 h-3.5" />
                    {author}
                  </span>
                )}
                {date && (
                  <span className="flex items-center gap-1">
                    <Calendar className="w-3.5 h-3.5" />
                    {date}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
        
        {result.page_number && (
          <Badge status="default" className="shrink-0 ml-2 opacity-80">
            Page {result.page_number}
          </Badge>
        )}
      </div>

      {/* Snippet Content */}
      <div 
        className={`text-sm leading-relaxed p-4 rounded-xl border italic transition-all duration-300 ${
          isLowFocus 
            ? "text-[#747775] bg-white/60 border-[#e1e3e1]/60 group-hover:text-textMuted group-hover:bg-surface/30" 
            : "text-textMuted bg-surface/20 border-borderDark/30 group-hover:bg-surface/30 group-hover:border-primary/10"
        }`}
        dangerouslySetInnerHTML={createMarkup(result.snippet)}
      />

      {/* Score Progress Bar, Preview and Download */}
      <div className="flex items-center justify-between mt-auto pt-3 border-t border-borderDark/40">
        <div className="flex items-center gap-3">
          {!reranked ? (
            <span
              className="text-xs font-medium px-2.5 py-0.5 rounded-full border text-[#747775] bg-[#edf2fc] border-[#d3d7dc]"
              title="AI ranking was unavailable for this search — this result is unranked, not scored."
            >
              Unranked — relevance unknown
            </span>
          ) : !grounded ? (
            <span
              className="text-xs font-semibold px-2.5 py-0.5 rounded-full border text-amber-800 bg-amber-500/10 border-amber-500/30"
              title="The AI reviewed this excerpt and did not find information actually answering your query — treat this match score as unreliable."
            >
              Possibly not relevant
            </span>
          ) : (
            <>
              <div className="w-24 h-2 bg-surface rounded-full overflow-hidden" title={`Relevance: ${matchPct}%`}>
                <div
                  className={`h-full rounded-full transition-all duration-500 ${getProgressBarColor(result.score)}`}
                  style={{ width: `${Math.max(5, matchPct)}%` }}
                />
              </div>
              <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full border transition-all duration-300 ${getMatchGlow(result.score)}`}>
                {matchPct}% Match
              </span>
            </>
          )}
        </div>

        {/* Action Buttons: Preview shown directly to the left side of Download button */}
        <div className="flex items-center gap-2">
          {onPreview && (
            <button
              onClick={() => onPreview(result)}
              className="flex items-center gap-1.5 text-xs font-semibold text-[#0d2e5c] hover:text-[#0945a5] bg-[#edf2fc] hover:bg-[#c2e7ff] px-3 py-1.5 rounded-lg border border-[#d3d7dc] transition-all cursor-pointer shadow-xs opacity-90 group-hover:opacity-100"
              title="Preview document"
            >
              <Eye className="w-3.5 h-3.5" />
              <span>Preview</span>
            </button>
          )}

          {/* Opens the review screen on the page this hit matched. */}
          <Link
            href={`/review?doc=${encodeURIComponent(result.document_id)}&page=${result.page_number || 1}`}
            className="flex items-center gap-1.5 text-xs font-semibold text-[#0d2e5c] hover:text-[#0945a5] bg-white hover:bg-[#edf2fc] px-3 py-1.5 rounded-lg border border-[#d3d7dc] transition-all"
            title={result.page_number ? `Review — opens on page ${result.page_number}` : "Review"}
          >
            <FileSearch className="w-3.5 h-3.5" />
            <span>Review</span>
          </Link>

          {result.download_url && (
            <a 
              href={result.download_url} 
              target="_blank" 
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-xs font-semibold text-primary hover:text-indigo-400 bg-primary/10 hover:bg-primary/20 px-3 py-1.5 rounded-lg border border-primary/20 transition-all"
              title="Download document"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download</span>
            </a>
          )}
        </div>
      </div>
    </Card>
  );
};
