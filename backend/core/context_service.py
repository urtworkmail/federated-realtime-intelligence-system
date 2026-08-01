"""
FRIS Context Service — the "living context layer" a16z's "Your Data Agents
Need Context" thesis argues every AI agent needs: canonical business
definitions per entity type, so an agent (or a human) asking "what's the
revenue" gets a definitive answer about which fused field is authoritative,
instead of guessing between several similarly-named candidates.

This is deliberately a thin, tenant-scoped key-value layer rather than a
full ontology engine — the goal is to turn "1 FTE per 50-100 entity types"
of tribal-knowledge governance overhead into a UI workflow any account can
maintain, not to build a semantic-web reasoner.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg


class ContextService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS context_definitions (
                    id UUID PRIMARY KEY,
                    organization_id UUID NOT NULL,
                    entity_type VARCHAR(100) NOT NULL,
                    property_name VARCHAR(255) NOT NULL,
                    canonical_field VARCHAR(255) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (organization_id, entity_type, property_name)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS context_definitions_org_type_idx "
                "ON context_definitions (organization_id, entity_type)"
            )

    async def upsert_definition(
        self,
        organization_id: str,
        entity_type: str,
        property_name: str,
        canonical_field: str,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO context_definitions "
                "(id, organization_id, entity_type, property_name, canonical_field, description) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (organization_id, entity_type, property_name) "
                "DO UPDATE SET canonical_field = $5, description = $6, updated_at = now() "
                "RETURNING id, organization_id, entity_type, property_name, canonical_field, description, "
                "          created_at, updated_at",
                uuid.uuid4(), uuid.UUID(organization_id), entity_type, property_name,
                canonical_field, description
            )
        return self._row_to_dict(row)

    async def list_definitions(
        self, organization_id: str, entity_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            if entity_type:
                rows = await conn.fetch(
                    "SELECT * FROM context_definitions WHERE organization_id = $1 AND entity_type = $2 "
                    "ORDER BY entity_type, property_name",
                    uuid.UUID(organization_id), entity_type
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM context_definitions WHERE organization_id = $1 "
                    "ORDER BY entity_type, property_name",
                    uuid.UUID(organization_id)
                )
        return [self._row_to_dict(r) for r in rows]

    async def delete_definition(self, organization_id: str, entity_type: str, property_name: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM context_definitions WHERE organization_id = $1 AND entity_type = $2 AND property_name = $3",
                uuid.UUID(organization_id), entity_type, property_name
            )
        return result != "DELETE 0"

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = dict(row)
        d["id"] = str(d["id"])
        d["organization_id"] = str(d["organization_id"])
        return d
