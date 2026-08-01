"""
FRIS Graph API — fused-graph queries, semantic search, and the Graph
Explorer's structured browse + visualization endpoints. Every route is
scoped to the authenticated account's organization (account["organization_id"])
so one tenant can never read another tenant's entities, relationships, or
review queue.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import state
from api.deps import get_current_account, require_role
from core.logging_service import LogCategory, LogLevel
from core.metering_service import EventType
from models.schemas import GraphQueryRequest

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/stats")
async def graph_stats(account: dict = Depends(get_current_account)):
    """Overview stats for the dashboard: entity counts, relationship counts — this tenant only."""
    return await state.fusion_engine.get_stats(organization_id=account["organization_id"])


@router.post("/query")
async def query_graph(request: GraphQueryRequest, account: dict = Depends(require_role("owner", "admin"))):
    """
    Run a raw Cypher query directly against the fused graph. This is an
    owner/admin-only power feature: arbitrary Cypher cannot be safely
    auto-scoped to one tenant the way every other endpoint here is, so
    access is restricted by role and every call is audit-logged.
    """
    try:
        results = await state.fusion_engine.query_graph(request.cypher, request.params)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    await state.metering_service.record(str(account["id"]), EventType.GRAPH_QUERY, quantity=1)
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING, level=LogLevel.WARNING,
            message=f"Raw Cypher query executed by {account['email']} (role={account['role']}) — "
                    f"this endpoint is not tenant-scoped, unlike every other graph read",
            source="graph.query_graph", organization_id=account["organization_id"],
            metadata={"cypher": request.cypher, "account_id": str(account["id"])}
        )
    return {"success": True, "results": results, "count": len(results)}


@router.get("/entities")
async def list_entities(entity_type: Optional[str] = None, limit: int = 100, account: dict = Depends(get_current_account)):
    """Browse fused entities, optionally filtered by type — this tenant only."""
    params = {"organization_id": account["organization_id"], "limit": limit}
    if entity_type:
        query = "MATCH (e:Entity {entity_type: $type, organization_id: $organization_id}) RETURN e LIMIT $limit"
        params["type"] = entity_type
    else:
        query = "MATCH (e:Entity {organization_id: $organization_id}) RETURN e LIMIT $limit"

    results = await state.fusion_engine.query_graph(query, params)
    return {"entities": [r["e"] for r in results], "count": len(results)}


@router.get("/entities/{fris_id}")
async def get_entity(fris_id: str, account: dict = Depends(get_current_account)):
    """Get a single fused entity with full provenance (which sources contributed what) — this tenant only."""
    query = "MATCH (e:Entity {fris_id: $fris_id, organization_id: $organization_id}) RETURN e"
    results = await state.fusion_engine.query_graph(query, {"fris_id": fris_id, "organization_id": account["organization_id"]})
    if not results:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity = results[0]["e"]
    if "_field_provenance" in entity and isinstance(entity["_field_provenance"], str):
        entity["_field_provenance"] = json.loads(entity["_field_provenance"])
    if "_sources" in entity and isinstance(entity["_sources"], str):
        entity["_sources"] = json.loads(entity["_sources"])

    return {"entity": entity}


@router.get("/entities/{fris_id}/explain")
async def explain_entity(fris_id: str, account: dict = Depends(get_current_account)):
    """
    Full decision trail for one fused entity: which connector contributed
    each field's winning value, the trust score and timestamp behind that
    decision, and the entity's overall match confidence / review status.
    This is the answer to "why does the graph say this" — provenance that
    already existed in fusion_engine.py, made queryable rather than buried.
    """
    explanation = await state.fusion_engine.get_entity_explanation(fris_id, account["organization_id"])
    if not explanation:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Best-effort enrichment: resolve connector_id -> human-readable name so
    # the response reads as "PostgreSQL CRM said X", not a bare UUID. Missing
    # connectors (deleted since) are left as the raw ID rather than failing.
    connector_names: dict = {}
    for source_id in explanation.get("contributing_sources", []):
        if source_id in connector_names:
            continue
        cfg = await state.connector_manager.get_connector(source_id, organization_id=account["organization_id"])
        connector_names[source_id] = cfg.name if cfg else source_id

    explanation["contributing_sources"] = [
        {"connector_id": sid, "connector_name": connector_names.get(sid, sid)}
        for sid in explanation.get("contributing_sources", [])
    ]
    for field in explanation.get("fields", []):
        sid = field.get("contributing_source")
        if sid:
            field["contributing_source_name"] = connector_names.get(sid, sid)

    return explanation


# ============================================================
# Entity Resolution Review Queue — low-confidence merges surfaced
# for a human to confirm/reject rather than silently auto-merged.
# ============================================================

class ReviewActionRequest(BaseModel):
    action: str  # "confirm" or "reject"


@router.get("/review-queue")
async def review_queue(limit: int = 100, offset: int = 0, account: dict = Depends(get_current_account)):
    """Entities whose most recent fusion merge fell in the borderline-confidence band — this tenant only."""
    return await state.fusion_engine.get_review_queue(
        organization_id=account["organization_id"], limit=limit, offset=offset
    )


@router.post("/entities/{fris_id}/review")
async def review_entity(fris_id: str, request: ReviewActionRequest, account: dict = Depends(get_current_account)):
    """Confirm or reject a flagged merge."""
    if request.action not in ("confirm", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'confirm' or 'reject'")
    found = await state.fusion_engine.resolve_review(fris_id, account["organization_id"], request.action)
    if not found:
        raise HTTPException(status_code=404, detail="Entity not found")
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING, level=LogLevel.INFO,
            message=f"Entity {fris_id[:8]}… merge review: {request.action}ed by {account['email']}",
            source="graph.review_entity", organization_id=account["organization_id"],
            metadata={"fris_id": fris_id, "action": request.action}
        )
    return {"success": True}


# ============================================================
# Semantic Search — Phase 1, now consumed by Phase 2's /api/query too.
# ============================================================

@router.get("/search")
async def semantic_search(
    q: str,
    entity_type: Optional[str] = None,
    top_k: int = 10,
    account: dict = Depends(get_current_account),
):
    """Semantic similarity search over fused entities — this tenant only."""
    if not state.embedding_service or not state.embedding_service.is_loaded:
        raise HTTPException(status_code=503, detail="Embedding service not ready")

    results = await state.fusion_engine.query_similar_entities(
        query_text=q, organization_id=account["organization_id"], entity_type=entity_type, top_k=top_k
    )
    await state.metering_service.record(str(account["id"]), EventType.SEMANTIC_SEARCH, quantity=1)
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING, level=LogLevel.INFO,
            message=f"Semantic search: \"{q}\" -> {len(results)} result(s)",
            source="graph.semantic_search", organization_id=account["organization_id"],
            metadata={"query": q, "entity_type": entity_type, "result_count": len(results)}
        )
    return {"query": q, "results": results}


@router.post("/embeddings/backfill")
async def backfill_embeddings(batch_size: int = 100, account: dict = Depends(require_role("owner", "admin"))):
    """Generates embeddings for any fused entity in this tenant that doesn't have one yet. Safe to call repeatedly."""
    if not state.embedding_service or not state.embedding_service.is_loaded:
        raise HTTPException(status_code=503, detail="Embedding service not ready")

    result = await state.fusion_engine.backfill_embeddings(batch_size=batch_size, organization_id=account["organization_id"])
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING, level=LogLevel.INFO,
            message=f"Embedding backfill: {result['entities_embedded_this_batch']} entities embedded "
                    f"({'complete' if result['batch_complete'] else 'more remaining'})",
            source="graph.backfill_embeddings", organization_id=account["organization_id"], metadata=result
        )
    return result


# ============================================================
# Graph Explorer — structured browse + visualization
# ============================================================

@router.get("/nodes")
async def browse_nodes(
    entity_type: Optional[str] = None, text: Optional[str] = None,
    limit: int = 50, offset: int = 0, account: dict = Depends(get_current_account)
):
    """Paged, filterable node table — the Graph Explorer's list view — this tenant only."""
    return await state.fusion_engine.search_nodes(
        organization_id=account["organization_id"], entity_type=entity_type, text=text, limit=limit, offset=offset
    )


@router.get("/neighbors/{fris_id}")
async def neighbors(fris_id: str, depth: int = 1, account: dict = Depends(get_current_account)):
    """Relationship expansion around one node — click-to-expand in the explorer — this tenant only."""
    return await state.fusion_engine.get_neighbors(fris_id, organization_id=account["organization_id"], depth=depth)


@router.get("/subgraph")
async def subgraph(fris_id: str, depth: int = 2, account: dict = Depends(get_current_account)):
    """{nodes, edges} payload shaped for the Cytoscape visualizer — this tenant only."""
    return await state.fusion_engine.get_subgraph(fris_id, organization_id=account["organization_id"], depth=depth)
