"""
FRIS GraphQL API — Strawberry GraphQL (MIT licensed) wrapping the exact same
tenant-scoped fusion_engine.py / context_service.py reads and writes the
REST routers in api/graph.py and api/context.py already use. No query logic
is duplicated here — every resolver calls the same service methods REST
does, just shaped for GraphQL clients (agents, dashboards) that want to
compose a single request across entities/explain/context instead of
multiple REST round-trips.

Auth: context_getter below runs get_current_account through FastAPI's
normal dependency injection (strawberry's FastAPI integration supports this
natively), so every GraphQL request is authenticated exactly like every
REST request — there is no second, weaker-authed query surface here. Every
resolver reads organization_id from that same authenticated account and
scopes its query the same way api/graph.py does.
"""

from typing import List, Optional

import strawberry
from fastapi import Depends
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON

import state
from api.deps import get_current_account


@strawberry.type
class Entity:
    fris_id: str
    entity_type: str
    properties: JSON


@strawberry.type
class FieldExplanation:
    field: str
    value: Optional[str]
    contributing_source: Optional[str]
    contributing_source_name: Optional[str]
    source_trust_score: Optional[float]
    recorded_at: Optional[str]


@strawberry.type
class ContributingSource:
    connector_id: str
    connector_name: str


@strawberry.type
class EntityExplanation:
    fris_id: str
    entity_type: Optional[str]
    contributing_sources: List[ContributingSource]
    fields: List[FieldExplanation]
    match_confidence: Optional[float]
    needs_review: bool
    review_action: Optional[str]


@strawberry.type
class ContextDefinition:
    entity_type: str
    property_name: str
    canonical_field: str
    description: Optional[str]


@strawberry.type
class GraphStats:
    total_entities: int
    total_relationships: int


def _require_role(account: dict, *allowed_roles: str):
    if account.get("role") not in allowed_roles:
        raise PermissionError(
            f"This action requires one of roles {list(allowed_roles)}, account has role '{account.get('role')}'"
        )


@strawberry.type
class Query:
    @strawberry.field
    async def entities(self, info: strawberry.Info, entity_type: Optional[str] = None, limit: int = 100) -> List[Entity]:
        account = info.context["account"]
        params = {"organization_id": account["organization_id"], "limit": limit}
        if entity_type:
            query = "MATCH (e:Entity {entity_type: $type, organization_id: $organization_id}) RETURN e LIMIT $limit"
            params["type"] = entity_type
        else:
            query = "MATCH (e:Entity {organization_id: $organization_id}) RETURN e LIMIT $limit"
        results = await state.fusion_engine.query_graph(query, params)
        return [
            Entity(
                fris_id=r["e"]["fris_id"], entity_type=r["e"]["entity_type"],
                properties={k: v for k, v in r["e"].items() if k != "embedding"}
            )
            for r in results
        ]

    @strawberry.field
    async def entity(self, info: strawberry.Info, fris_id: str) -> Optional[Entity]:
        account = info.context["account"]
        query = "MATCH (e:Entity {fris_id: $fris_id, organization_id: $organization_id}) RETURN e"
        results = await state.fusion_engine.query_graph(query, {"fris_id": fris_id, "organization_id": account["organization_id"]})
        if not results:
            return None
        e = results[0]["e"]
        return Entity(fris_id=e["fris_id"], entity_type=e["entity_type"], properties={k: v for k, v in e.items() if k != "embedding"})

    @strawberry.field
    async def explain_entity(self, info: strawberry.Info, fris_id: str) -> Optional[EntityExplanation]:
        account = info.context["account"]
        explanation = await state.fusion_engine.get_entity_explanation(fris_id, account["organization_id"])
        if not explanation:
            return None

        connector_names = {}
        for source_id in explanation.get("contributing_sources", []):
            if source_id in connector_names:
                continue
            cfg = await state.connector_manager.get_connector(source_id, organization_id=account["organization_id"])
            connector_names[source_id] = cfg.name if cfg else source_id

        return EntityExplanation(
            fris_id=explanation["fris_id"],
            entity_type=explanation.get("entity_type"),
            contributing_sources=[
                ContributingSource(connector_id=sid, connector_name=connector_names.get(sid, sid))
                for sid in explanation.get("contributing_sources", [])
            ],
            fields=[
                FieldExplanation(
                    field=f["field"], value=str(f["value"]) if f["value"] is not None else None,
                    contributing_source=f.get("contributing_source"),
                    contributing_source_name=connector_names.get(f.get("contributing_source"), f.get("contributing_source")),
                    source_trust_score=f.get("source_trust_score"),
                    recorded_at=f.get("recorded_at"),
                )
                for f in explanation.get("fields", [])
            ],
            match_confidence=explanation.get("match_confidence"),
            needs_review=explanation.get("needs_review", False),
            review_action=explanation.get("review_action"),
        )

    @strawberry.field
    async def context_definitions(self, info: strawberry.Info, entity_type: Optional[str] = None) -> List[ContextDefinition]:
        account = info.context["account"]
        definitions = await state.context_service.list_definitions(
            organization_id=account["organization_id"], entity_type=entity_type
        )
        return [
            ContextDefinition(
                entity_type=d["entity_type"], property_name=d["property_name"],
                canonical_field=d["canonical_field"], description=d.get("description")
            )
            for d in definitions
        ]

    @strawberry.field
    async def graph_stats(self, info: strawberry.Info) -> GraphStats:
        account = info.context["account"]
        stats = await state.fusion_engine.get_stats(organization_id=account["organization_id"])
        return GraphStats(total_entities=stats["total_entities"], total_relationships=stats["total_relationships"])


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def review_entity(self, info: strawberry.Info, fris_id: str, action: str) -> bool:
        account = info.context["account"]
        if action not in ("confirm", "reject"):
            raise ValueError("action must be 'confirm' or 'reject'")
        return await state.fusion_engine.resolve_review(fris_id, account["organization_id"], action)

    @strawberry.mutation
    async def upsert_context_definition(
        self, info: strawberry.Info, entity_type: str, property_name: str,
        canonical_field: str, description: Optional[str] = None
    ) -> ContextDefinition:
        account = info.context["account"]
        _require_role(account, "owner", "admin")
        d = await state.context_service.upsert_definition(
            organization_id=account["organization_id"], entity_type=entity_type,
            property_name=property_name, canonical_field=canonical_field, description=description
        )
        return ContextDefinition(
            entity_type=d["entity_type"], property_name=d["property_name"],
            canonical_field=d["canonical_field"], description=d.get("description")
        )


async def get_context(account: dict = Depends(get_current_account)) -> dict:
    """
    Run through FastAPI's Depends the same way every REST router does —
    this is what makes GraphQL auth identical to REST rather than a
    parallel, potentially weaker path.
    """
    return {"account": account}


schema = strawberry.Schema(query=Query, mutation=Mutation)
# Mounted at /api/graphql via app.include_router(router, prefix="/api/graphql") in main.py.
router = GraphQLRouter(schema, context_getter=get_context)
