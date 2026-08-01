"""
FRIS Logging Service
Every meaningful event in the backend gets written here: server lifecycle,
network/API calls, connector sync activity, data fusion operations, and
errors. Nothing is in-memory only — every entry is persisted to Postgres
so the Logs page in the console reflects the real history, survives
restarts, and can be filtered/searched.

Categories map directly to what you asked for:
  SERVER          - startup, shutdown, connection retries, crashes
  NETWORK         - outbound HTTP calls connectors make to external sources
  CONSOLE         - inbound API requests from the frontend (the "console")
  APP             - application-level events (connector created, schema confirmed)
  DATA_PROCESSING - fusion engine activity (entity resolution, conflicts, sync results)
  SCHEMA_DRIFT    - a connector's source schema changed since the user last confirmed it
  DATA_BOUNDARY   - a request sent tenant data outside the deployment boundary (e.g. a BYO-LLM call)
"""

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import asyncpg


class LogCategory(str, Enum):
    SERVER = "server"
    NETWORK = "network"
    CONSOLE = "console"
    APP = "app"
    DATA_PROCESSING = "data_processing"
    SCHEMA_DRIFT = "schema_drift"
    DATA_BOUNDARY = "data_boundary"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LoggingService:
    """
    Thin wrapper around a Postgres table. Shares the connection pool with
    ConnectorManager rather than opening a second pool — one DB, one place
    logs and connector config both live.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_table(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id UUID PRIMARY KEY,
                    occurred_at TIMESTAMPTZ DEFAULT now(),
                    category VARCHAR(30) NOT NULL,
                    level VARCHAR(20) NOT NULL,
                    message TEXT NOT NULL,
                    source VARCHAR(255),
                    connector_id UUID,
                    metadata JSONB
                )
            """)
            # organization_id is added by account_service.ensure_tables (it
            # owns tenancy columns across tables); this table just consumes it.
            # Indices for the filter combinations the Logs page will use
            await conn.execute("CREATE INDEX IF NOT EXISTS logs_category_idx ON logs (category)")
            await conn.execute("CREATE INDEX IF NOT EXISTS logs_level_idx ON logs (level)")
            await conn.execute("CREATE INDEX IF NOT EXISTS logs_occurred_at_idx ON logs (occurred_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS logs_connector_id_idx ON logs (connector_id)")

    async def log(
        self,
        category: LogCategory,
        level: LogLevel,
        message: str,
        source: Optional[str] = None,
        connector_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        organization_id: Optional[str] = None
    ):
        """
        Write one log entry. Never raises — a logging failure should never
        take down the request/operation that triggered it.

        organization_id is optional because some events (server startup/
        shutdown) happen outside any tenant context — those show up as
        platform-level entries every tenant's Logs page filters out.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO logs (id, category, level, message, source, connector_id, metadata, organization_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    uuid.uuid4(),
                    category.value if isinstance(category, LogCategory) else category,
                    level.value if isinstance(level, LogLevel) else level,
                    message,
                    source,
                    uuid.UUID(connector_id) if connector_id else None,
                    json.dumps(metadata) if metadata else None,
                    uuid.UUID(organization_id) if organization_id else None
                )
        except Exception as e:
            # Last resort: at least surface it on stdout so it's not silently lost
            print(f"[logging_service] FAILED to write log entry: {e} — original message was: {message}")

    async def query(
        self,
        category: Optional[str] = None,
        level: Optional[str] = None,
        connector_id: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 200,
        offset: int = 0,
        organization_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Filtered log retrieval for the Logs page. Supports combining any
        number of filters — category, level, connector, free-text search,
        and a time window. organization_id scopes results to one tenant —
        every caller from an authenticated route must pass it, so one
        organization's Logs page can never show another's events.
        """
        conditions = []
        params = []
        idx = 1

        if organization_id:
            conditions.append(f"organization_id = ${idx}")
            params.append(uuid.UUID(organization_id))
            idx += 1
        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if level:
            conditions.append(f"level = ${idx}")
            params.append(level)
            idx += 1
        if connector_id:
            conditions.append(f"connector_id = ${idx}")
            params.append(uuid.UUID(connector_id))
            idx += 1
        if search:
            conditions.append(f"(message ILIKE ${idx} OR source ILIKE ${idx})")
            params.append(f"%{search}%")
            idx += 1
        if since:
            conditions.append(f"occurred_at >= ${idx}")
            params.append(since)
            idx += 1

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_query = f"SELECT count(*) FROM logs {where_sql}"
        data_query = (
            f"SELECT * FROM logs {where_sql} "
            f"ORDER BY occurred_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(count_query, *params)
            rows = await conn.fetch(data_query, *params, limit, offset)

        entries = []
        for row in rows:
            entry = dict(row)
            entry["id"] = str(entry["id"])
            entry["connector_id"] = str(entry["connector_id"]) if entry["connector_id"] else None
            if entry["metadata"] and isinstance(entry["metadata"], str):
                try:
                    entry["metadata"] = json.loads(entry["metadata"])
                except json.JSONDecodeError:
                    pass
            entries.append(entry)

        return {"total": total, "entries": entries, "limit": limit, "offset": offset}

    async def get_category_counts(self, organization_id: Optional[str] = None) -> Dict[str, int]:
        """Quick counts per category — powers the filter chip badges in the UI."""
        async with self._pool.acquire() as conn:
            if organization_id:
                rows = await conn.fetch(
                    "SELECT category, count(*) as c FROM logs WHERE organization_id = $1 GROUP BY category",
                    uuid.UUID(organization_id)
                )
            else:
                rows = await conn.fetch(
                    "SELECT category, count(*) as c FROM logs GROUP BY category"
                )
        return {row["category"]: row["c"] for row in rows}

    async def get_level_counts(self, since: Optional[datetime] = None, organization_id: Optional[str] = None) -> Dict[str, int]:
        conditions = []
        params = []
        idx = 1
        if organization_id:
            conditions.append(f"organization_id = ${idx}")
            params.append(uuid.UUID(organization_id))
            idx += 1
        if since:
            conditions.append(f"occurred_at >= ${idx}")
            params.append(since)
            idx += 1
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT level, count(*) as c FROM logs {where_sql} GROUP BY level", *params
            )
        return {row["level"]: row["c"] for row in rows}

    async def purge_older_than(self, days: int, organization_id: Optional[str] = None) -> int:
        """Retention helper — not auto-scheduled yet, exposed for an admin action.
        organization_id scopes the purge to one tenant's logs only, when provided."""
        async with self._pool.acquire() as conn:
            if organization_id:
                result = await conn.execute(
                    "DELETE FROM logs WHERE occurred_at < now() - ($1 || ' days')::interval "
                    "AND organization_id = $2",
                    str(days), uuid.UUID(organization_id)
                )
            else:
                result = await conn.execute(
                    "DELETE FROM logs WHERE occurred_at < now() - ($1 || ' days')::interval",
                    str(days)
                )
        # asyncpg returns "DELETE <n>"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0
