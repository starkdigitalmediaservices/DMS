"""T67/D-8 — the verified-layer boundary.

"Exports, certificates and legal derivation consume only human-verified
edges. Enforce at the query layer, not per call site." This module is
that one query layer: the one place T78 (export) and T65 (Section 63
certificate, still blocked on A3) are expected to read entity edges and
facts from, so an escrowed link's confirmation status can never go
missing or get silently coerced to look verified by one call site
forgetting to check it.

D-8 (signed 2026-08-24) settled the policy this enforces:
  - mode="general_export": verified edges/facts pass through unchanged.
    Escrowed (unconfirmed) ones ARE included, but every one carries
    confirmation_status="unconfirmed_machine_suggested" — never
    indistinguishable from a verified fact.
  - mode="certificate": escrowed edges/facts are excluded entirely. A
    certificate is a stronger legal assertion than an export listing; it
    has no room for a "this might not be true" footnote.

Nothing calls this yet — T78 (general export) and T65 (certificate, still
blocked on A3, see docs/decisions/D8_decision_escrowed_links_in_exports.md) haven't been
built. This exists now so that when they are, the enforcement point
already exists and can't be skipped or reimplemented inconsistently.
"""
from typing import Any, Dict, List, Literal
from uuid import UUID

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_edge import EntityEdge
from app.models.fact import Fact

ExportMode = Literal["general_export", "certificate"]


def _edge_confirmation_status(edge: EntityEdge) -> str:
    return "human_verified" if edge.status == "verified" else "unconfirmed_machine_suggested"


def _fact_confirmation_status(fact: Fact) -> str:
    return "human_verified" if fact.status == "verified" else "unconfirmed_machine_suggested"


async def get_edges_for_export(
    db: AsyncSession, tenant_id: UUID, node_id: UUID, mode: ExportMode,
) -> List[Dict[str, Any]]:
    """Every edge touching one entity node, filtered/labeled per D-8."""
    res = await db.execute(
        select(EntityEdge).where(
            EntityEdge.tenant_id == tenant_id,
            or_(
                EntityEdge.source_node_id == node_id,
                and_(EntityEdge.target_type == "entity", EntityEdge.target_node_id == node_id),
            ),
        )
    )
    edges = list({e.id: e for e in res.scalars().all()}.values())

    out = []
    for e in edges:
        is_verified = e.status == "verified"
        if not is_verified and mode == "certificate":
            continue
        out.append({
            "edge_id": str(e.id),
            "edge_type": e.edge_type,
            "tier": e.tier,
            "source_node_id": str(e.source_node_id),
            "target_type": e.target_type,
            "target_node_id": str(e.target_node_id) if e.target_node_id else None,
            "target_fact_id": str(e.target_fact_id) if e.target_fact_id else None,
            "confidence": e.confidence,
            "confirmation_status": _edge_confirmation_status(e),
        })
    return out


async def get_facts_for_export(
    db: AsyncSession, tenant_id: UUID, document_id: UUID, mode: ExportMode,
) -> List[Dict[str, Any]]:
    """Every fact extracted from one document, filtered/labeled per D-8.
    Mirrors get_edges_for_export using T20's fact status instead of an
    edge's — the same boundary, applied to the other kind of evidence."""
    res = await db.execute(
        select(Fact).where(Fact.tenant_id == tenant_id, Fact.document_id == document_id)
    )
    facts = list(res.scalars().all())

    out = []
    for f in facts:
        is_verified = f.status == "verified"
        if not is_verified and mode == "certificate":
            continue
        out.append({
            "fact_id": str(f.id),
            "field_name": f.field_name,
            "value": f.value,
            "confidence": f.confidence,
            "confirmation_status": _fact_confirmation_status(f),
        })
    return out
