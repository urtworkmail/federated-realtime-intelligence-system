"""
FRIS Natural-Language Query API (Phase 2, seeds Phase 3).
RAG flow: embed query -> semantic search over the fused graph -> build
context -> chosen LLM provider generates a grounded answer. Metered per call.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import state
from api.deps import get_current_account
from core.logging_service import LogCategory, LogLevel
from core.metering_service import EventType

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    query: str
    entity_type: Optional[str] = None
    provider: Optional[str] = None
    top_k: int = 8


@router.post("")
async def run_query(request: QueryRequest, account: dict = Depends(get_current_account)):
    if not state.embedding_service or not state.embedding_service.is_loaded:
        raise HTTPException(status_code=503, detail="Embedding service not ready")

    context_entities = await state.fusion_engine.query_similar_entities(
        query_text=request.query, organization_id=account["organization_id"],
        entity_type=request.entity_type, top_k=request.top_k
    )

    # Living context layer: tell the model which fused field is authoritative
    # per entity type before it answers, rather than guessing between
    # similarly-named fields across sources.
    canonical_definitions = await state.context_service.list_definitions(
        organization_id=account["organization_id"], entity_type=request.entity_type
    )

    result = await state.llm_service.answer(
        account_id=str(account["id"]), query=request.query,
        context_entities=context_entities, provider=request.provider,
        canonical_definitions=canonical_definitions
    )

    if not result.get("needs_clarification"):
        await state.metering_service.record(
            account_id=str(account["id"]), event_type=EventType.LLM_QUERY,
            quantity=1, tokens_override=result["tokens"]["total"],
            metadata={"provider": result["provider"], "query": request.query}
        )

    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING, level=LogLevel.INFO,
            message=f"NL query: \"{request.query}\" -> "
                    f"{'clarification requested' if result.get('needs_clarification') else 'answered'}",
            source="query.run", metadata={"account_id": str(account["id"])}
        )

    return result
