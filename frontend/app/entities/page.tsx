"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Network,
  Loader2,
  AlertCircle,
  CheckCircle2,
  History,
  FileText,
  Info,
  Undo2,
  X,
  ShieldCheck,
  ShieldQuestion,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import RegionHighlightViewer from "@/components/drive/RegionHighlightViewer";
import { useI18n } from "@/lib/i18n";
import { useRole } from "@/lib/permissions";
import { humanFieldName } from "@/lib/fieldLabels";
import EvidenceViewer, { type EvidenceRequest } from "@/components/entities/EvidenceViewer";

interface FieldProvenance {
  kind: "base" | "amendment";
  evidence_fact_id: string | null;
  amendment_id?: string | null;
  amendment_type?: string;
  effective_date?: string;
}

interface RecordView {
  record_id: string;
  record_type: string;
  current: { fields: Record<string, any>; legal_status: string; field_provenance: Record<string, FieldProvenance> };
  original: { fields: Record<string, any>; legal_status: string };
}

interface LinkedEntity {
  edge_id: string;
  edge_type: string;
  tier: number;
  status: string;
  confidence: number | null;
  direction: "outgoing" | "incoming";
  other_node: { id: string; entity_type: string; label: string };
  /** The document value this link was read from. */
  evidence: FactRef | null;
}

interface FactRef {
  fact_id: string;
  field_name: string;
  value: any;
  document_id: string;
  document_title: string | null;
  page_numbers: number[];
}

interface AppearsIn {
  document_id: string;
  document_title: string | null;
  pages: number[];
  linked: boolean;
  mentions: { fact_id: string; field_name: string; value: any; page_numbers: number[]; how: "linked" | "same_name" }[];
}

/** Proof for one value: the evidence viewer opens on its page only. */
function evidenceFor(fact: FactRef): EvidenceRequest {
  return {
    documentId: fact.document_id,
    documentTitle: fact.document_title || "Document",
    items: [{ factId: fact.fact_id, label: `${humanFieldName(fact.field_name)}: ${formatValue(fact.value)}`, pages: fact.page_numbers }],
  };
}

/** Proof for a document in "Appears in": its mention pages only. */
function evidenceForDocument(a: AppearsIn, startFactId?: string): EvidenceRequest {
  const start = a.mentions.find((m) => m.fact_id === startFactId) || a.mentions[0];
  return {
    documentId: a.document_id,
    documentTitle: a.document_title || "Document",
    items: a.mentions.map((m) => ({ factId: m.fact_id, label: `${humanFieldName(m.field_name)}: ${formatValue(m.value)}`, pages: m.page_numbers })),
    startPage: start?.page_numbers[0],
  };
}

function pagesText(pages: number[]): string {
  if (!pages.length) return "";
  return pages.length === 1 ? `p. ${pages[0]}` : `pp. ${pages.slice(0, 8).join(", ")}${pages.length > 8 ? "…" : ""}`;
}

interface LinkedFact {
  edge_id: string;
  edge_type: string;
  tier: number;
  status: string;
  confidence: number | null;
  direction: "outgoing" | "incoming";
  /** Set when this value is the evidence behind a relationship. */
  via: { edge_type: string; other_label: string } | null;
  fact: FactRef;
}

interface Entity360 {
  node: { id: string; entity_type: string; label: string; attributes: Record<string, any> };
  records: RecordView[];
  linked_entities: LinkedEntity[];
  linked_facts: LinkedFact[];
  appears_in: AppearsIn[];
}

interface RecordHistory {
  base: { fields: Record<string, any>; record_type: string; evidence_fact_id: string | null; created_at: string };
  amendments: {
    id: string;
    amendment_type: string;
    effective_date: string;
    field_changes: Record<string, any>;
    legal_status: string | null;
    evidence_fact_id: string;
  }[];
}

function formatValue(v: any): string {
  if (v === null || v === undefined) return "—";
  // Extracted values are stored as {"v": ...}; show the value, not the wrapper.
  if (typeof v === "object" && !Array.isArray(v) && "v" in v) return formatValue(v.v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// Raw enum/attribute keys previously rendered verbatim ("claims_ownership",
// "born_date") — this is a generic humanizer, not a hardcoded per-type map,
// since new edge types and attribute keys get added without frontend changes.
function humanize(raw: string): string {
  return raw
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const STATUS_INFO: Record<string, { label: string; tooltip: string; className: string; icon: React.ReactNode }> = {
  machine: {
    label: "Auto-linked",
    tooltip: "Created automatically at high confidence (tier 1-2). Permanent — cannot be confirmed or reverted, only ever removed by editing the source data.",
    className: "bg-[#f0f4f9] text-[#444746]",
    icon: null,
  },
  held: {
    label: "Needs confirmation",
    tooltip: "Created automatically at lower confidence (tier 3-4). Not usable as evidence until a human confirms it.",
    className: "bg-amber-50 text-amber-700",
    icon: <ShieldQuestion className="w-3 h-3" />,
  },
  verified: {
    label: "Confirmed",
    tooltip: "A human has confirmed this link. Usable as evidence.",
    className: "bg-green-50 text-green-700",
    icon: <ShieldCheck className="w-3 h-3" />,
  },
};

function EdgeStatusBadge({ status }: { status: string }) {
  const info = STATUS_INFO[status] || { label: status, tooltip: "", className: "bg-[#f0f4f9] text-[#444746]", icon: null };
  return (
    <span
      title={info.tooltip}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold cursor-help ${info.className}`}
    >
      {info.icon}
      {info.label}
    </span>
  );
}

function TierBadge({ tier }: { tier: number }) {
  const tooltip = tier <= 2
    ? `Tier ${tier} — auto-linked at high confidence`
    : `Tier ${tier} — needs a human to confirm before it counts as evidence`;
  return (
    <span title={tooltip} className="cursor-help text-[#747775]">
      tier {tier}
    </span>
  );
}

export default function Entity360Page() {
  const { t } = useI18n();
  // Confirm/revert of held/verified edges is an entity-graph edit —
  // reviewer roles only; everyone else just reads the 360 view.
  const { can: roleCan } = useRole();
  const canEditGraph = roleCan("entities.edit");
  const [nodeId, setNodeId] = useState("");
  const [data, setData] = useState<Entity360 | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [nameQuery, setNameQuery] = useState("");
  const [searchResults, setSearchResults] = useState<{ id: string; entity_type: string; label: string }[] | null>(null);
  const [searching, setSearching] = useState(false);

  const searchByName = async () => {
    const q = nameQuery.trim();
    if (!q) {
      setError("Type a name to search first.");
      return;
    }
    setSearching(true);
    setError("");
    setSearchResults(null);
    try {
      const result = await api.entities.search(q);
      setSearchResults(result.results);
    } catch (e: any) {
      setError(e?.message || "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const [historyRecordId, setHistoryRecordId] = useState<string | null>(null);
  const [history, setHistory] = useState<RecordHistory | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [viewingFactId, setViewingFactId] = useState<string | null>(null);
  // "Show on page" opens read-only proof on top of this page -- closing it
  // leaves the page (and its scroll position) exactly as it was.
  const [evidence, setEvidence] = useState<EvidenceRequest | null>(null);
  const canOpenWorkbench = roleCan("facts.review");
  const [edgeActionLoading, setEdgeActionLoading] = useState<string | null>(null);

  // Clicking a linked entity's "View" replaced the whole 360 view with no
  // way back except the top-level "Back to Drive," which lost all context
  // — found live-testing this session. A simple id stack fixes it.
  const [navHistory, setNavHistory] = useState<{ id: string; label: string }[]>([]);

  const load = async (id?: string, pushHistory = true) => {
    const target = (id ?? nodeId).trim();
    if (!target) {
      setError("Enter an entity node ID first.");
      return;
    }
    if (pushHistory && data && data.node.id !== target) {
      setNavHistory((prev) => [...prev, { id: data.node.id, label: data.node.label }]);
    }
    setNodeId(target);
    setLoading(true);
    setError("");
    setData(null);
    try {
      const result = await api.entities.get360(target);
      setData(result);
    } catch (e: any) {
      setError(e?.message || "Failed to load this entity");
    } finally {
      setLoading(false);
    }
  };

  // /entities?node=<id> opens that entity directly -- the Workbench's
  // "Back to entity" link from a "Show on page" lands here.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("node");
    if (id) load(id, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const goBack = () => {
    const prev = navHistory[navHistory.length - 1];
    if (!prev) return;
    setNavHistory((h) => h.slice(0, -1));
    load(prev.id, false);
  };

  const confirmEdge = async (edgeId: string) => {
    setEdgeActionLoading(edgeId);
    setError("");
    try {
      await api.entities.confirmEdge(edgeId);
      await load(nodeId, false);
    } catch (e: any) {
      setError(e?.message || "Failed to confirm this link");
    } finally {
      setEdgeActionLoading(null);
    }
  };

  const revertEdge = async (edgeId: string) => {
    setEdgeActionLoading(edgeId);
    setError("");
    try {
      await api.entities.revertEdge(edgeId);
      await load(nodeId, false);
    } catch (e: any) {
      setError(e?.message || "Failed to revert this confirmation");
    } finally {
      setEdgeActionLoading(null);
    }
  };

  const openHistory = async (recordId: string) => {
    setHistoryRecordId(recordId);
    setHistory(null);
    setHistoryLoading(true);
    try {
      const result = await api.records.getHistory(recordId);
      setHistory(result);
    } catch (e: any) {
      setError(e?.message || "Failed to load record history");
      setHistoryRecordId(null);
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <div className="h-screen overflow-y-auto bg-[#f8f9fa] text-[#1f1f1f]">
      <header className="h-16 px-6 flex items-center justify-between border-b border-[#e1e3e1]/60 bg-white/80 backdrop-blur-md sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/drive"
            className="flex items-center gap-2 text-sm text-[#444746] hover:text-[#1f1f1f] transition-colors px-3 py-1.5 rounded-lg hover:bg-[#f0f4f9]"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>{t("common.back", "Back to Drive")}</span>
          </Link>
          <div className="h-5 w-px bg-[#e1e3e1]" />
          <h1 className="text-lg font-bold text-[#1f1f1f] flex items-center gap-2">
            <Network className="w-5 h-5 text-[#0d2e5c]" />
            {t("entities.title", "Entity 360")}
          </h1>
          {navHistory.length > 0 && (
            <button
              onClick={goBack}
              className="flex items-center gap-1.5 text-sm text-[#0d2e5c] hover:underline px-2 py-1"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to {navHistory[navHistory.length - 1].label}
            </button>
          )}
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-6">
        {!data && !loading && (
          <div className="mb-4 flex items-start gap-3 px-4 py-3 rounded-xl bg-[#e8f0fe] border border-[#c2e7ff] text-sm text-[#001d35]">
            <Info className="w-4 h-4 shrink-0 mt-0.5" />
            <p>
              An <b>entity</b> is a real-world person, property, or institution the system has recognized across
              documents. Search for one by name below, or open one from a document&apos;s extracted fields to see
              every record, document, and other entity it&apos;s linked to.
            </p>
          </div>
        )}
        <Card className="bg-white border border-[#e1e3e1] mb-4">
          <p className="text-xs font-bold text-[#747775] mb-2">Find an entity by name</p>
          <div className="flex gap-2">
            <input
              type="text"
              aria-label="Search entities by name"
              placeholder="Search a person, property, or institution name…"
              value={nameQuery}
              onChange={(e) => setNameQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && searchByName()}
              className="flex-1 text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] focus:outline-none focus:ring-2 focus:ring-[#0d2e5c]/40"
            />
            <Button size="sm" loading={searching} onClick={searchByName}>
              Search
            </Button>
          </div>
          {searchResults && (
            <div className="mt-3 flex flex-col gap-1.5">
              {searchResults.length === 0 && <p className="text-sm text-[#747775]">No entities match that name.</p>}
              {searchResults.map((r) => (
                <button
                  key={r.id}
                  onClick={() => load(r.id)}
                  className="text-left text-sm px-3 py-2 rounded-lg border border-[#e1e3e1] hover:bg-[#f0f4f9] flex items-center justify-between"
                >
                  <span className="font-medium">{r.label}</span>
                  <span className="text-[10px] uppercase tracking-wide text-[#747775]">{humanize(r.entity_type)}</span>
                </button>
              ))}
            </div>
          )}
        </Card>

        {error && (
          <div className="mb-6 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-[#0d2e5c]" />
          </div>
        )}

        {data && (
          <div className="flex flex-col gap-6">
            <Card className="bg-white border border-[#e1e3e1]">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-bold">{data.node.label}</h2>
                  <p className="text-xs text-[#747775] uppercase tracking-wide mt-1">{humanize(data.node.entity_type)}</p>
                </div>
              </div>
              {Object.keys(data.node.attributes || {}).length > 0 && (
                <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                  {Object.entries(data.node.attributes).map(([k, v]) => (
                    <div key={k}>
                      <span className="text-[#747775]">{humanize(k)}: </span>
                      <span className="font-medium">{formatValue(v)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card className="bg-white border border-[#e1e3e1]">
              <h3 className="text-sm font-bold mb-4">Records ({data.records.length})</h3>
              {data.records.length === 0 && <p className="text-sm text-[#747775]">No records for this entity.</p>}
              <div className="flex flex-col gap-4">
                {data.records.map((r) => (
                  <div key={r.record_id} className="border border-[#e1e3e1] rounded-xl p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <span className="text-sm font-bold">{r.record_type}</span>
                        <span className="ml-2 text-xs text-[#747775]">status: {r.current.legal_status}</span>
                      </div>
                      <button
                        onClick={() => openHistory(r.record_id)}
                        className="flex items-center gap-1 text-xs font-bold text-[#0d2e5c] hover:underline"
                      >
                        <History className="w-3.5 h-3.5" /> View history
                      </button>
                    </div>
                    <table className="w-full text-xs mt-2">
                      <tbody>
                        {Object.entries(r.current.fields).map(([field, value]) => {
                          const prov = r.current.field_provenance[field];
                          return (
                            <tr key={field} className="border-t border-[#f0f4f9]">
                              <td className="py-1.5 pr-3 text-[#747775] align-top w-1/3">{field}</td>
                              <td className="py-1.5 pr-3 font-medium align-top">{formatValue(value)}</td>
                              <td className="py-1.5 text-right align-top">
                                {prov?.evidence_fact_id && (
                                  <button
                                    onClick={() => setViewingFactId(prov.evidence_fact_id!)}
                                    className="text-[10px] font-bold text-[#0d2e5c] hover:underline flex items-center gap-1 ml-auto"
                                  >
                                    <FileText className="w-3 h-3" /> source
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="bg-white border border-[#e1e3e1]">
              <h3 className="text-sm font-bold mb-1">Linked entities ({data.linked_entities.length})</h3>
              <p className="text-[10px] text-[#444746] mb-3">Other people, properties, or institutions connected to this one.</p>
              {data.linked_entities.length === 0 && <p className="text-sm text-[#747775]">No linked entities.</p>}
              <div className="flex flex-col gap-2">
                {data.linked_entities.map((e) => (
                  <div key={e.edge_id} className="flex items-center justify-between border border-[#e1e3e1] rounded-lg px-3 py-2 text-xs gap-2">
                    <div className="min-w-0">
                      <span className="font-bold">{humanize(e.edge_type)}</span>
                      <span className="ml-2 text-[#747775]">
                        {e.direction === "outgoing" ? "→" : "←"} {e.other_node.label} ({humanize(e.other_node.entity_type)})
                      </span>
                      <span className="ml-2">
                        <TierBadge tier={e.tier} />
                        {e.confidence != null ? ` · ${Math.round(e.confidence * 100)}%` : ""}
                      </span>
                      {e.evidence && (
                        <span className="block mt-0.5 text-[11px] text-[#444746]">
                          Found in {e.evidence.document_title || "a document"}{e.evidence.page_numbers.length ? `, ${pagesText(e.evidence.page_numbers)}` : ""}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {e.evidence && (
                        <button type="button" onClick={() => setEvidence(evidenceFor(e.evidence!))} className="flex items-center gap-1 font-bold text-[#0d2e5c] hover:underline"
                          title="See the original page where this link was read, with the value outlined">
                          <FileText className="w-3 h-3" /> Show on page
                        </button>
                      )}
                      <EdgeStatusBadge status={e.status} />
                      {canEditGraph && e.status === "held" && (
                        <button
                          onClick={() => confirmEdge(e.edge_id)}
                          disabled={edgeActionLoading === e.edge_id}
                          className="flex items-center gap-1 font-bold text-emerald-700 hover:underline disabled:opacity-50"
                          title="Confirm this link is correct — makes it usable as evidence"
                        >
                          {edgeActionLoading === e.edge_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                          Confirm
                        </button>
                      )}
                      {canEditGraph && e.status === "verified" && (
                        <button
                          onClick={() => revertEdge(e.edge_id)}
                          disabled={edgeActionLoading === e.edge_id}
                          className="flex items-center gap-1 font-bold text-[#747775] hover:underline disabled:opacity-50"
                          title="Undo the confirmation — sends this back to needing review"
                        >
                          {edgeActionLoading === e.edge_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Undo2 className="w-3 h-3" />}
                          Revert
                        </button>
                      )}
                      <button onClick={() => load(e.other_node.id)} className="font-bold text-[#0d2e5c] hover:underline">
                        View
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="bg-white border border-[#e1e3e1]">
              <h3 className="text-sm font-bold mb-1">Linked facts ({data.linked_facts.length})</h3>
              <p className="text-[10px] text-[#444746] mb-3">Values read from documents that are tied to this entity, including the evidence behind each link above.</p>
              {data.linked_facts.length === 0 && <p className="text-sm text-[#747775]">No linked facts.</p>}
              <div className="flex flex-col gap-2">
                {data.linked_facts.map((e) => (
                  <div key={`${e.edge_id}-${e.fact.fact_id}`} className="flex items-center justify-between border border-[#e1e3e1] rounded-lg px-3 py-2 text-xs gap-2">
                    <div className="min-w-0">
                      <span className="font-bold">{humanFieldName(e.fact.field_name)}:</span>
                      <span className="ml-1.5">{formatValue(e.fact.value)}</span>
                      <span className="block mt-0.5 text-[11px] text-[#444746]">
                        {e.fact.document_title || "unknown source"}{e.fact.page_numbers.length ? `, ${pagesText(e.fact.page_numbers)}` : ""}
                        {e.via ? ` · evidence for: ${humanize(e.via.edge_type)} ${e.direction === "outgoing" ? "→" : "←"} ${e.via.other_label}` : ` · ${humanize(e.edge_type)}`}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <EdgeStatusBadge status={e.status} />
                      {canEditGraph && e.status === "held" && (
                        <button
                          onClick={() => confirmEdge(e.edge_id)}
                          disabled={edgeActionLoading === e.edge_id}
                          className="flex items-center gap-1 font-bold text-emerald-700 hover:underline disabled:opacity-50"
                          title="Confirm this link is correct — makes it usable as evidence"
                        >
                          {edgeActionLoading === e.edge_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                          Confirm
                        </button>
                      )}
                      {canEditGraph && e.status === "verified" && (
                        <button
                          onClick={() => revertEdge(e.edge_id)}
                          disabled={edgeActionLoading === e.edge_id}
                          className="flex items-center gap-1 font-bold text-[#747775] hover:underline disabled:opacity-50"
                          title="Undo the confirmation — sends this back to needing review"
                        >
                          {edgeActionLoading === e.edge_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Undo2 className="w-3 h-3" />}
                          Revert
                        </button>
                      )}
                      <button type="button" onClick={() => setEvidence(evidenceFor(e.fact))} className="flex items-center gap-1 font-bold text-[#0d2e5c] hover:underline"
                        title="See the original page with this value outlined">
                        <FileText className="w-3 h-3" /> Show on page
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="bg-white border border-[#e1e3e1]">
              <h3 className="text-sm font-bold mb-1">Appears in ({data.appears_in.length} {data.appears_in.length === 1 ? "document" : "documents"})</h3>
              <p className="text-[10px] text-[#444746] mb-3">
                Every document that mentions this entity: through the links above, or where a value reads exactly as its name.
              </p>
              {data.appears_in.length === 0 && <p className="text-sm text-[#747775]">Not found in any document you can open.</p>}
              <div className="flex flex-col gap-2">
                {data.appears_in.map((a) => (
                  <details key={a.document_id} className="border border-[#e1e3e1] rounded-lg px-3 py-2 text-xs group">
                    <summary className="flex items-center justify-between gap-2 cursor-pointer list-none">
                      <span className="min-w-0">
                        <span className="font-bold">{a.document_title || "Untitled document"}</span>
                        <span className="ml-2 text-[#444746]">
                          {a.mentions.length} {a.mentions.length === 1 ? "mention" : "mentions"}{a.pages.length ? ` · ${pagesText(a.pages)}` : ""}
                        </span>
                        {!a.linked && (
                          <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-900 text-[10px] font-semibold"
                            title="Not linked to this entity yet -- a value in this document reads exactly as its name">
                            same name only
                          </span>
                        )}
                      </span>
                      <button type="button" onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); setEvidence(evidenceForDocument(a)); }}
                        className="shrink-0 flex items-center gap-1 font-bold text-[#0d2e5c] hover:underline">
                        <FileText className="w-3 h-3" /> Show on page
                      </button>
                    </summary>
                    <ul className="mt-2 flex flex-col gap-1 border-t border-[#f0f0f0] pt-2">
                      {a.mentions.map((m) => (
                        <li key={m.fact_id} className="flex items-center justify-between gap-2">
                          <span className="min-w-0 truncate">
                            <span className="text-[#444746]">{pagesText(m.page_numbers) || "page unknown"} · {humanFieldName(m.field_name)}:</span>{" "}
                            {formatValue(m.value)}
                          </span>
                          <button type="button" onClick={() => setEvidence(evidenceForDocument(a, m.fact_id))}
                            className="shrink-0 font-semibold text-[#0d2e5c] hover:underline">Show</button>
                        </li>
                      ))}
                    </ul>
                  </details>
                ))}
              </div>
            </Card>
          </div>
        )}

        {historyRecordId && (
          <div role="presentation" className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs" onClick={() => setHistoryRecordId(null)}>
            <div
              role="presentation"
              className="w-full max-w-2xl max-h-[80vh] overflow-y-auto bg-white border border-[#e1e3e1] rounded-3xl shadow-2xl text-[#1f1f1f]"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-6 pt-6 pb-3 sticky top-0 bg-white">
                <h3 className="text-lg font-bold">Record history</h3>
                <button onClick={() => setHistoryRecordId(null)} className="p-1.5 text-[#747775] hover:text-[#1f1f1f] rounded-full hover:bg-[#f0f4f9]">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="px-6 pb-6">
                {historyLoading ? (
                  <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-[#0d2e5c]" /></div>
                ) : history ? (
                  <div className="flex flex-col gap-4">
                    <div className="border border-[#e1e3e1] rounded-xl p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold uppercase tracking-wide text-[#747775]">Base entry</span>
                        <span className="text-[10px] text-[#747775]">{history.base.created_at}</span>
                      </div>
                      <table className="w-full text-xs">
                        <tbody>
                          {Object.entries(history.base.fields).map(([field, value]) => (
                            <tr key={field} className="border-t border-[#f0f4f9]">
                              <td className="py-1.5 pr-3 text-[#747775] w-1/3">{field}</td>
                              <td className="py-1.5 font-medium">{formatValue(value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {history.base.evidence_fact_id && (
                        <button
                          onClick={() => setViewingFactId(history.base.evidence_fact_id!)}
                          className="mt-2 text-[10px] font-bold text-[#0d2e5c] hover:underline flex items-center gap-1"
                        >
                          <FileText className="w-3 h-3" /> source
                        </button>
                      )}
                    </div>

                    {history.amendments.length === 0 && (
                      <p className="text-sm text-[#747775] text-center">No amendments — this record hasn&apos;t changed since it was entered.</p>
                    )}
                    {history.amendments.map((a) => (
                      <div key={a.id} className="border border-amber-200 bg-amber-50/40 rounded-xl p-4">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs font-bold uppercase tracking-wide text-amber-700">{a.amendment_type}</span>
                          <span className="text-[10px] text-[#747775]">effective {a.effective_date}</span>
                        </div>
                        <table className="w-full text-xs">
                          <tbody>
                            {Object.entries(a.field_changes).map(([field, value]) => (
                              <tr key={field} className="border-t border-amber-100">
                                <td className="py-1.5 pr-3 text-[#747775] w-1/3">{field}</td>
                                <td className="py-1.5 font-medium">{formatValue(value)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {a.legal_status && <p className="text-xs mt-2">legal status → <span className="font-bold">{a.legal_status}</span></p>}
                        <button
                          onClick={() => setViewingFactId(a.evidence_fact_id)}
                          className="mt-2 text-[10px] font-bold text-[#0d2e5c] hover:underline flex items-center gap-1"
                        >
                          <FileText className="w-3 h-3" /> source
                        </button>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        )}

        {evidence && data && (
          <EvidenceViewer evidence={evidence} canOpenWorkbench={canOpenWorkbench} entityId={data.node.id} onClose={() => setEvidence(null)} />
        )}

        {viewingFactId && (
          <div role="presentation" className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs" onClick={() => setViewingFactId(null)}>
            <div
              role="presentation"
              className="w-full max-w-3xl max-h-[85vh] overflow-y-auto bg-white border border-[#e1e3e1] rounded-3xl shadow-2xl text-[#1f1f1f] p-6"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-bold">Source</h3>
                <button onClick={() => setViewingFactId(null)} className="p-1.5 text-[#747775] hover:text-[#1f1f1f] rounded-full hover:bg-[#f0f4f9]">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <RegionHighlightViewer factId={viewingFactId} renderWidth={640} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
