"""
FRIS MCP (Model Context Protocol) tool surface.

This is the substance of FRIS's MCP exposure: a set of read-only tools that
agentic clients (Claude, other MCP-speaking agents) can call to reach the
fused intelligence graph. Every tool here calls the exact same tenant-scoped
fusion_engine / context_service reads that the REST routers and the GraphQL
resolvers use — it is a thin adapter over the existing read surface, not a
new, parallel, or weaker-authed query path.

Auth is NOT handled here. The transport layer (api/mcp_server.py) authenticates
the caller with the same get_current_account dependency every other FRIS
endpoint uses, then passes the resolved `account` dict (carrying the
organization_id tenant boundary and role) into call_tool(). Every tool below
scopes its query to account["organization_id"] exactly like api/graph.py does,
so an MCP client can only ever see its own tenant's data.
"""

from typing import Any, Dict, List

import state


# MCP tool definitions — name, human/agent-facing description, and a JSON-Schema
# for arguments. This is exactly the shape MCP's tools/list returns, so the
# transport layer can serve it verbatim.
TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "list_entities",
        "description": (
            "List fused entities from the FRIS intelligence graph for the caller's "
            "organization. Optionally filter by entity_type (e.g. 'Organization', "
            "'Person', 'Transaction', 'Shipment') and/or a free-text substring match "
            "across entity properties. Returns entities with their fris_id and "
            "reconciled properties."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "Filter to a single entity type."},
                "text": {"type": "string", "description": "Case-sensitive substring to match within any property value."},
                "limit": {"type": "integer", "description": "Max entities to return (default 50, max 200).", "default": 50},
                "offset": {"type": "integer", "description": "Pagination offset (default 0).", "default": 0},
            },
            "required": [],
        },
    },
    {
        "name": "get_entity",
        "description": (
            "Fetch a single fused entity by its FRIS id (fris_id), with all reconciled "
            "properties. Returns null if no such entity exists in the caller's organization."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"fris_id": {"type": "string", "description": "The FRIS entity id."}},
            "required": ["fris_id"],
        },
    },
    {
        "name": "explain_entity",
        "description": (
            "Return the full explainability trail for a fused entity: which sources "
            "contributed, per-field provenance (contributing source, trust score, "
            "timestamp), the entity-resolution match confidence, and whether the "
            "entity is flagged for human review. This is how an agent answers 'why "
            "do we believe this?' about any fact in the graph."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"fris_id": {"type": "string", "description": "The FRIS entity id to explain."}},
            "required": ["fris_id"],
        },
    },
    {
        "name": "get_entity_neighbors",
        "description": (
            "Return the subgraph around a single entity — its related entities and the "
            "relationships connecting them, up to a given depth (1-3). Useful for "
            "questions like 'what is connected to this supplier?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fris_id": {"type": "string", "description": "The center entity id."},
                "depth": {"type": "integer", "description": "Traversal depth 1-3 (default 1).", "default": 1},
            },
            "required": ["fris_id"],
        },
    },
    {
        "name": "list_context_definitions",
        "description": (
            "List the organization's living context layer — the canonical field "
            "definitions that map raw source property names to their agreed business "
            "meaning (e.g. 'Organization.revenue -> annual_revenue_usd'). Optionally "
            "filter by entity_type. Gives an agent the organization's own vocabulary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"entity_type": {"type": "string", "description": "Filter definitions to one entity type."}},
            "required": [],
        },
    },
    {
        "name": "graph_stats",
        "description": (
            "Return high-level statistics for the caller's intelligence graph: total "
            "entities and total relationships."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


class MCPToolError(Exception):
    """Raised for a bad tool name or invalid arguments — surfaced to the client as a tool error."""


def list_tools() -> List[Dict[str, Any]]:
    """Return the MCP tool catalog (served verbatim by tools/list)."""
    return TOOL_DEFINITIONS


async def _resolve_connector_names(source_ids: List[str], organization_id: str) -> Dict[str, str]:
    """Map connector ids to their human names, tenant-scoped — same logic the GraphQL explain resolver uses."""
    names: Dict[str, str] = {}
    for source_id in source_ids:
        if source_id in names:
            continue
        try:
            cfg = await state.connector_manager.get_connector(source_id, organization_id=organization_id)
            names[source_id] = cfg.name if cfg else source_id
        except Exception:
            # A malformed/legacy source id must not fail the whole explain for an
            # agent — fall back to showing the raw id as the source name.
            names[source_id] = source_id
    return names


async def call_tool(account: Dict[str, Any], name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatch an MCP tool call. `account` is the already-authenticated caller
    (from get_current_account); its organization_id scopes every read.
    Returns a JSON-serializable result dict.
    """
    org = account["organization_id"]
    args = arguments or {}

    if name == "list_entities":
        limit = max(1, min(int(args.get("limit", 50)), 200))
        result = await state.fusion_engine.search_nodes(
            organization_id=org,
            entity_type=args.get("entity_type"),
            text=args.get("text"),
            limit=limit,
            offset=max(0, int(args.get("offset", 0))),
        )
        return result

    if name == "get_entity":
        fris_id = args.get("fris_id")
        if not fris_id:
            raise MCPToolError("get_entity requires 'fris_id'")
        rows = await state.fusion_engine.query_graph(
            "MATCH (e:Entity {fris_id: $fris_id, organization_id: $organization_id}) RETURN e",
            {"fris_id": fris_id, "organization_id": org},
        )
        if not rows:
            return {"entity": None}
        e = rows[0]["e"]
        return {"entity": {
            "fris_id": e["fris_id"],
            "entity_type": e.get("entity_type"),
            "properties": {k: v for k, v in e.items() if k != "embedding"},
        }}

    if name == "explain_entity":
        fris_id = args.get("fris_id")
        if not fris_id:
            raise MCPToolError("explain_entity requires 'fris_id'")
        explanation = await state.fusion_engine.get_entity_explanation(fris_id, org)
        if not explanation:
            return {"explanation": None}
        names = await _resolve_connector_names(explanation.get("contributing_sources", []), org)
        return {"explanation": {
            "fris_id": explanation["fris_id"],
            "entity_type": explanation.get("entity_type"),
            "contributing_sources": [
                {"connector_id": sid, "connector_name": names.get(sid, sid)}
                for sid in explanation.get("contributing_sources", [])
            ],
            "fields": [
                {
                    "field": f["field"],
                    "value": None if f["value"] is None else str(f["value"]),
                    "contributing_source": f.get("contributing_source"),
                    "contributing_source_name": names.get(f.get("contributing_source"), f.get("contributing_source")),
                    "source_trust_score": f.get("source_trust_score"),
                    "recorded_at": f.get("recorded_at"),
                }
                for f in explanation.get("fields", [])
            ],
            "match_confidence": explanation.get("match_confidence"),
            "needs_review": explanation.get("needs_review", False),
            "review_action": explanation.get("review_action"),
        }}

    if name == "get_entity_neighbors":
        fris_id = args.get("fris_id")
        if not fris_id:
            raise MCPToolError("get_entity_neighbors requires 'fris_id'")
        depth = max(1, min(int(args.get("depth", 1)), 3))
        return await state.fusion_engine.get_neighbors(fris_id, organization_id=org, depth=depth)

    if name == "list_context_definitions":
        definitions = await state.context_service.list_definitions(
            organization_id=org, entity_type=args.get("entity_type")
        )
        return {"definitions": definitions}

    if name == "graph_stats":
        stats = await state.fusion_engine.get_stats(organization_id=org)
        return {
            "total_entities": stats["total_entities"],
            "total_relationships": stats["total_relationships"],
        }

    raise MCPToolError(f"Unknown tool: {name}")
