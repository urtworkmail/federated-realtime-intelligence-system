"""
FRIS Metering Service — postpaid, AWS-style usage ledger.
Every metered action (LLM query, semantic/graph query, ingestion,
embedding, storage) writes one usage_events row. No hard blocking;
billing is computed from the ledger per period. Mirrors LoggingService:
shares the control DB pool, idempotent DDL, record() never raises.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import asyncpg


class EventType(str, Enum):
    LLM_QUERY = "llm_query"
    SEMANTIC_SEARCH = "semantic_search"
    GRAPH_QUERY = "graph_query"
    INGESTION = "ingestion"
    EMBEDDING = "embedding"
    STORAGE = "storage"


# Seed pricing — admin-editable via /api/billing/pricing afterwards.
DEFAULT_PRICING = {
    EventType.LLM_QUERY: {"unit": "token", "unit_cost_usd": 0.00001, "tokens_per_unit": 1},
    EventType.SEMANTIC_SEARCH: {"unit": "query", "unit_cost_usd": 0.0005, "tokens_per_unit": 50},
    EventType.GRAPH_QUERY: {"unit": "query", "unit_cost_usd": 0.0002, "tokens_per_unit": 20},
    EventType.INGESTION: {"unit": "record", "unit_cost_usd": 0.00002, "tokens_per_unit": 2},
    EventType.EMBEDDING: {"unit": "entity", "unit_cost_usd": 0.0001, "tokens_per_unit": 10},
    EventType.STORAGE: {"unit": "gb_day", "unit_cost_usd": 0.001, "tokens_per_unit": 100},
}


class MeteringService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pricing (
                    event_type VARCHAR(30) PRIMARY KEY,
                    unit VARCHAR(30) NOT NULL,
                    unit_cost_usd DOUBLE PRECISION NOT NULL,
                    tokens_per_unit DOUBLE PRECISION NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id UUID PRIMARY KEY,
                    account_id UUID NOT NULL,
                    event_type VARCHAR(30) NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL,
                    unit VARCHAR(30) NOT NULL,
                    tokens DOUBLE PRECISION NOT NULL,
                    cost_usd DOUBLE PRECISION NOT NULL,
                    metadata JSONB,
                    occurred_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS usage_events_account_time_idx ON usage_events (account_id, occurred_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS usage_events_type_idx ON usage_events (event_type)")

            for event_type, p in DEFAULT_PRICING.items():
                await conn.execute(
                    "INSERT INTO pricing (event_type, unit, unit_cost_usd, tokens_per_unit) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (event_type) DO NOTHING",
                    event_type.value, p["unit"], p["unit_cost_usd"], p["tokens_per_unit"]
                )

    async def _get_pricing(self, event_type: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM pricing WHERE event_type = $1", event_type)
        if row:
            return dict(row)
        default = DEFAULT_PRICING.get(EventType(event_type))
        return {"unit": default["unit"], "unit_cost_usd": default["unit_cost_usd"], "tokens_per_unit": default["tokens_per_unit"]}

    async def record(
        self,
        account_id: str,
        event_type: EventType,
        quantity: float = 1.0,
        tokens_override: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Never raises — a metering failure should never block the request that triggered it."""
        try:
            pricing = await self._get_pricing(event_type.value if isinstance(event_type, EventType) else event_type)
            tokens = tokens_override if tokens_override is not None else quantity * pricing["tokens_per_unit"]
            cost_usd = quantity * pricing["unit_cost_usd"]
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO usage_events (id, account_id, event_type, quantity, unit, tokens, cost_usd, metadata) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    uuid.uuid4(), uuid.UUID(account_id),
                    event_type.value if isinstance(event_type, EventType) else event_type,
                    quantity, pricing["unit"], tokens, cost_usd,
                    __import__("json").dumps(metadata) if metadata else None
                )
        except Exception as e:
            print(f"[metering_service] FAILED to record usage event: {e}")

    async def get_usage(
        self, account_id: str, event_type: Optional[str] = None,
        since: Optional[datetime] = None, limit: int = 200, offset: int = 0
    ) -> Dict[str, Any]:
        conditions = ["account_id = $1"]
        params: List[Any] = [uuid.UUID(account_id)]
        idx = 2
        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if since:
            conditions.append(f"occurred_at >= ${idx}")
            params.append(since)
            idx += 1
        where_sql = "WHERE " + " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT count(*) FROM usage_events {where_sql}", *params)
            rows = await conn.fetch(
                f"SELECT * FROM usage_events {where_sql} ORDER BY occurred_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
                *params, limit, offset
            )
        entries = []
        for row in rows:
            entry = dict(row)
            entry["id"] = str(entry["id"])
            entry["account_id"] = str(entry["account_id"])
            entries.append(entry)
        return {"total": total, "entries": entries, "limit": limit, "offset": offset}

    async def get_summary(self, account_id: str, since: Optional[datetime] = None) -> Dict[str, Any]:
        where_sql = "WHERE account_id = $1"
        params: List[Any] = [uuid.UUID(account_id)]
        if since:
            where_sql += " AND occurred_at >= $2"
            params.append(since)

        async with self._pool.acquire() as conn:
            by_type = await conn.fetch(
                f"SELECT event_type, sum(quantity) as total_quantity, sum(tokens) as total_tokens, "
                f"sum(cost_usd) as total_cost_usd, count(*) as event_count "
                f"FROM usage_events {where_sql} GROUP BY event_type ORDER BY total_cost_usd DESC",
                *params
            )
            totals = await conn.fetchrow(
                f"SELECT coalesce(sum(tokens), 0) as total_tokens, coalesce(sum(cost_usd), 0) as total_cost_usd, "
                f"count(*) as total_events FROM usage_events {where_sql}",
                *params
            )
        return {
            "by_event_type": [dict(r) for r in by_type],
            "total_tokens": totals["total_tokens"],
            "total_cost_usd": round(totals["total_cost_usd"], 6),
            "total_events": totals["total_events"],
        }

    async def get_invoices(self, account_id: str, months: int = 6) -> List[Dict[str, Any]]:
        """Monthly rollups — a lightweight 'invoice' view over the ledger, not a real billing doc."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date_trunc('month', occurred_at) as period,
                       sum(cost_usd) as total_cost_usd, sum(tokens) as total_tokens, count(*) as event_count
                FROM usage_events
                WHERE account_id = $1 AND occurred_at >= now() - ($2 || ' months')::interval
                GROUP BY period ORDER BY period DESC
                """,
                uuid.UUID(account_id), str(months)
            )
        return [dict(r) for r in rows]

    async def update_pricing(self, event_type: str, unit_cost_usd: float, tokens_per_unit: float):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pricing SET unit_cost_usd = $1, tokens_per_unit = $2 WHERE event_type = $3",
                unit_cost_usd, tokens_per_unit, event_type
            )

    async def get_pricing_table(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM pricing ORDER BY event_type")
        return [dict(r) for r in rows]
