import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.auth import TokenPayload
from ...deps import get_tenant_db, require_tenant_access, require_permission
from ...services import entity_360_service, entity_graph_service

router = APIRouter(prefix="/entities", tags=["Entities"])


class EntityNodeCreate(BaseModel):
    entity_type: str
    label: str
    attributes: Optional[Dict[str, Any]] = None


class EntityEdgeCreate(BaseModel):
    edge_type: str
    tier: int
    source_node_id: uuid.UUID
    target_type: str
    target_node_id: Optional[uuid.UUID] = None
    target_fact_id: Optional[uuid.UUID] = None
    confidence: Optional[float] = None
    evidence_fact_id: Optional[uuid.UUID] = None


@router.post("", status_code=201)
async def create_entity_node_api(
    node_in: EntityNodeCreate,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    node = await entity_graph_service.create_node(
        db, tenant_id, node_in.entity_type, node_in.label, user_id, attributes=node_in.attributes,
    )
    # Never blocks or auto-merges (same contract as the document fuzzy-
    # duplicate leg, T79) — just surfaced alongside the node the caller
    # asked to create, so a possible fragmentation is visible at the one
    # moment it's cheapest to fix: right when the duplicate is created.
    possible_duplicates = await entity_graph_service.find_similar_nodes(
        db, tenant_id, node.entity_type, node.label, exclude_node_id=node.id,
    )
    return {
        "id": str(node.id), "entity_type": node.entity_type, "label": node.label, "attributes": node.attributes,
        "possible_duplicates": possible_duplicates,
    }


@router.get("/check-duplicate")
async def check_entity_duplicate_api(
    entity_type: str,
    label: str,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Same check as create_entity_node_api's possible_duplicates, but
    before creating anything — for a UI to warn a user while they're still
    typing a label, or for an operator to check without registering a
    node at all."""
    tenant_id = uuid.UUID(current_user.tenant_id)
    candidates = await entity_graph_service.find_similar_nodes(db, tenant_id, entity_type, label)
    return {"candidates": candidates}


@router.post("/edges", status_code=201)
async def create_entity_edge_api(
    edge_in: EntityEdgeCreate,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """T56 — the create-edge half of the API. create_node, confirm/revert
    and bulk-confirm/revert were all wired up, but nothing ever exposed
    entity_graph_service.create_edge itself: a real gap found while
    testing the graph end-to-end against real data, since without this
    no edge could ever exist outside a unit test's direct service call.
    created_by_actor_id is always the calling human — created_by_policy_version
    is reserved for the bulk-confirm code path, not a human create action.
    """
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    edge = await entity_graph_service.create_edge(
        db, tenant_id, edge_in.edge_type, edge_in.tier, edge_in.source_node_id, edge_in.target_type,
        target_node_id=edge_in.target_node_id,
        target_fact_id=edge_in.target_fact_id,
        confidence=edge_in.confidence,
        evidence_fact_id=edge_in.evidence_fact_id,
        created_by_actor_id=user_id,
    )
    return {
        "id": str(edge.id),
        "edge_type": edge.edge_type,
        "tier": edge.tier,
        "status": edge.status,
        "source_node_id": str(edge.source_node_id),
        "target_type": edge.target_type,
        "target_node_id": str(edge.target_node_id) if edge.target_node_id else None,
        "target_fact_id": str(edge.target_fact_id) if edge.target_fact_id else None,
        "confidence": edge.confidence,
    }


@router.delete("/edges/{edge_id}", status_code=204)
async def delete_entity_edge_api(
    edge_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    await entity_graph_service.delete_edge(db, tenant_id, edge_id, actor_id=user_id)


@router.delete("/{node_id}", status_code=204)
async def delete_entity_node_api(
    node_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    await entity_graph_service.delete_node(db, tenant_id, node_id, actor_id=user_id)


@router.get("/search")
async def search_entity_nodes_api(
    q: str,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    nodes = await entity_graph_service.search_nodes(db, tenant_id, q)
    return {"results": [{"id": str(n.id), "entity_type": n.entity_type, "label": n.label} for n in nodes]}


@router.get("/{node_id}/360")
async def get_entity_360_api(
    node_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    return await entity_360_service.get_entity_360_view(db, tenant_id, node_id)


@router.post("/edges/{edge_id}/confirm")
async def confirm_edge_api(
    edge_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    edge = await entity_graph_service.confirm_edge(db, tenant_id, edge_id, user_id)
    return {"edge_id": str(edge.id), "status": edge.status, "verified_by_actor_id": str(edge.verified_by_actor_id)}


@router.post("/edges/{edge_id}/revert")
async def revert_edge_api(
    edge_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    edge = await entity_graph_service.revert_edge(db, tenant_id, edge_id, user_id)
    return {"edge_id": str(edge.id), "status": edge.status}


@router.post("/edges/bulk-confirm")
async def bulk_confirm_edges_api(
    corpus_folder_id: uuid.UUID,
    threshold: float,
    policy_version: str,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    result = await entity_graph_service.bulk_confirm_edges(
        db, tenant_id, corpus_folder_id, threshold, user_id, policy_version,
    )
    return {
        "batch_id": str(result["batch_id"]),
        "confirmed_count": result["confirmed_count"],
        "edge_ids": [str(eid) for eid in result["edge_ids"]],
    }


@router.post("/edges/bulk-revert/{batch_id}")
async def revert_bulk_edge_batch_api(
    batch_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("entities.edit")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    result = await entity_graph_service.revert_bulk_batch(db, tenant_id, batch_id, user_id)
    return {
        "reverted_count": result["reverted_count"],
        "edge_ids": [str(eid) for eid in result["edge_ids"]],
    }
