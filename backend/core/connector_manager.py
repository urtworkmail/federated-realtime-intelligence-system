"""
FRIS Connector Manager
Orchestrates the full connector lifecycle:
  create -> test -> detect schema -> user confirms -> sync -> fuse into graph
Stores connector configs and state in PostgreSQL (the FRIS control plane DB,
separate from any client source DBs being connected to).
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

from connectors.base import (
    ConnectorConfig, ConnectorStatus, ConnectorType,
    DetectedField, FieldType, SchemaDetectionResult
)
from connectors.registry import ConnectorRegistry
from core.fusion_engine import FusionEngine


class ConnectorManager:
    """
    The orchestration layer between the API, the connector instances,
    and the fusion engine. This is what the Connectors sidebar talks to.
    """

    def __init__(self, control_db_dsn: str, fusion_engine: FusionEngine):
        self.control_db_dsn = control_db_dsn
        self.fusion_engine = fusion_engine
        self._pool: Optional[asyncpg.Pool] = None
        self._active_instances: Dict[str, Any] = {}  # connector_id -> live connector instance

    async def connect(self):
        self._pool = await asyncpg.create_pool(self.control_db_dsn, min_size=2, max_size=10)
        await self._ensure_tables()

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def _ensure_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS connectors (
                    connector_id UUID PRIMARY KEY,
                    connector_type VARCHAR(50) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    config JSONB NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT now(),
                    last_synced TIMESTAMPTZ,
                    schema_detection JSONB,
                    sync_interval_seconds INTEGER DEFAULT 300,
                    total_records_ingested INTEGER DEFAULT 0,
                    error_message TEXT
                )
            """)
            # Latest detected schema drift (or NULL if none) — set on every
            # sync by _detect_schema_drift, read by the Connectors page badge.
            await conn.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS schema_drift JSONB")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    id SERIAL PRIMARY KEY,
                    connector_id UUID REFERENCES connectors(connector_id),
                    synced_at TIMESTAMPTZ DEFAULT now(),
                    success BOOLEAN,
                    records_fetched INTEGER,
                    records_normalized INTEGER,
                    entities_created INTEGER,
                    entities_merged INTEGER,
                    conflicts_resolved INTEGER,
                    error_message TEXT
                )
            """)

    # ---------- Connector CRUD ----------

    async def create_connector(
        self,
        connector_type: str,
        name: str,
        config: Dict[str, Any],
        account_id: Optional[str] = None,
        organization_id: Optional[str] = None
    ) -> ConnectorConfig:
        connector_id = str(uuid.uuid4())
        cfg = ConnectorConfig(
            connector_id=connector_id,
            connector_type=ConnectorType(connector_type),
            name=name,
            config=config,
            status=ConnectorStatus.PENDING
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO connectors (connector_id, connector_type, name, config, status, account_id, organization_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                uuid.UUID(connector_id), connector_type, name, json.dumps(config), cfg.status.value,
                uuid.UUID(account_id) if account_id else None,
                uuid.UUID(organization_id) if organization_id else None
            )

        return cfg

    async def get_connector_account_id(self, connector_id: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT account_id FROM connectors WHERE connector_id = $1", uuid.UUID(connector_id)
            )
        return str(row["account_id"]) if row and row["account_id"] else None

    async def get_connector_organization_id(self, connector_id: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT organization_id FROM connectors WHERE connector_id = $1", uuid.UUID(connector_id)
            )
        return str(row["organization_id"]) if row and row["organization_id"] else None

    async def get_connector(self, connector_id: str, organization_id: Optional[str] = None) -> Optional[ConnectorConfig]:
        """
        organization_id, when passed, enforces the tenant boundary: a
        connector belonging to a different organization is treated as not
        found rather than returned, so callers can't accidentally leak
        another tenant's connector by ID alone.
        """
        async with self._pool.acquire() as conn:
            if organization_id:
                row = await conn.fetchrow(
                    "SELECT * FROM connectors WHERE connector_id = $1 AND organization_id = $2",
                    uuid.UUID(connector_id), uuid.UUID(organization_id)
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM connectors WHERE connector_id = $1", uuid.UUID(connector_id)
                )
        if not row:
            return None
        return self._row_to_config(row)

    async def list_connectors(self, organization_id: Optional[str] = None) -> List[ConnectorConfig]:
        async with self._pool.acquire() as conn:
            if organization_id:
                rows = await conn.fetch(
                    "SELECT * FROM connectors WHERE organization_id = $1 ORDER BY created_at DESC",
                    uuid.UUID(organization_id)
                )
            else:
                rows = await conn.fetch("SELECT * FROM connectors ORDER BY created_at DESC")
        return [self._row_to_config(row) for row in rows]

    async def update_connector_status(
        self, connector_id: str, status: ConnectorStatus, error_message: Optional[str] = None
    ):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE connectors SET status = $1, error_message = $2 WHERE connector_id = $3",
                status.value, error_message, uuid.UUID(connector_id)
            )

    async def update_schema(self, connector_id: str, schema: SchemaDetectionResult):
        schema_dict = self._schema_to_dict(schema)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE connectors SET schema_detection = $1 WHERE connector_id = $2",
                json.dumps(schema_dict), uuid.UUID(connector_id)
            )

    async def delete_connector(self, connector_id: str, organization_id: Optional[str] = None):
        async with self._pool.acquire() as conn:
            if organization_id:
                await conn.execute(
                    "DELETE FROM connectors WHERE connector_id = $1 AND organization_id = $2",
                    uuid.UUID(connector_id), uuid.UUID(organization_id)
                )
            else:
                await conn.execute(
                    "DELETE FROM connectors WHERE connector_id = $1", uuid.UUID(connector_id)
                )
        self._active_instances.pop(connector_id, None)

    def _row_to_config(self, row) -> ConnectorConfig:
        config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        schema = None
        if row["schema_detection"]:
            schema_dict = json.loads(row["schema_detection"]) if isinstance(row["schema_detection"], str) else row["schema_detection"]
            schema = self._dict_to_schema(schema_dict)

        schema_drift = row["schema_drift"]
        if isinstance(schema_drift, str):
            schema_drift = json.loads(schema_drift)

        return ConnectorConfig(
            connector_id=str(row["connector_id"]),
            connector_type=ConnectorType(row["connector_type"]),
            name=row["name"],
            config=config,
            status=ConnectorStatus(row["status"]),
            created_at=row["created_at"],
            last_synced=row["last_synced"],
            schema=schema,
            sync_interval_seconds=row["sync_interval_seconds"],
            total_records_ingested=row["total_records_ingested"],
            error_message=row["error_message"],
            schema_drift=schema_drift
        )

    def _schema_to_dict(self, schema: SchemaDetectionResult) -> Dict:
        return {
            "connector_id": schema.connector_id,
            "detected_at": schema.detected_at.isoformat(),
            "fields": [
                {
                    "raw_name": f.raw_name,
                    "suggested_name": f.suggested_name,
                    "field_type": f.field_type.value,
                    "sample_values": [str(v) for v in f.sample_values],
                    "null_count": f.null_count,
                    "is_identifier": f.is_identifier,
                    "is_entity_name": f.is_entity_name,
                    "is_timestamp": f.is_timestamp,
                    "confidence": f.confidence,
                    "user_confirmed_name": f.user_confirmed_name,
                    "user_confirmed_type": f.user_confirmed_type.value if f.user_confirmed_type else None,
                    "mapped_to_entity_property": f.mapped_to_entity_property
                }
                for f in schema.fields
            ],
            "sample_row_count": schema.sample_row_count,
            "suggested_entity_type": schema.suggested_entity_type,
            "confidence": schema.confidence,
            "raw_sample": schema.raw_sample,
            "user_confirmed": schema.user_confirmed,
            "user_modified": schema.user_modified
        }

    def _dict_to_schema(self, d: Dict) -> SchemaDetectionResult:
        fields = [
            DetectedField(
                raw_name=f["raw_name"],
                suggested_name=f["suggested_name"],
                field_type=FieldType(f["field_type"]),
                sample_values=f["sample_values"],
                null_count=f["null_count"],
                is_identifier=f["is_identifier"],
                is_entity_name=f["is_entity_name"],
                is_timestamp=f["is_timestamp"],
                confidence=f["confidence"],
                user_confirmed_name=f.get("user_confirmed_name"),
                user_confirmed_type=FieldType(f["user_confirmed_type"]) if f.get("user_confirmed_type") else None,
                mapped_to_entity_property=f.get("mapped_to_entity_property")
            )
            for f in d["fields"]
        ]
        return SchemaDetectionResult(
            connector_id=d["connector_id"],
            detected_at=datetime.fromisoformat(d["detected_at"]),
            fields=fields,
            sample_row_count=d["sample_row_count"],
            suggested_entity_type=d["suggested_entity_type"],
            confidence=d["confidence"],
            raw_sample=d["raw_sample"],
            user_confirmed=d["user_confirmed"],
            user_modified=d["user_modified"]
        )

    # ---------- Lifecycle operations ----------

    def _get_instance(self, cfg: ConnectorConfig):
        """Get or create a live connector instance for this config."""
        if cfg.connector_id not in self._active_instances:
            self._active_instances[cfg.connector_id] = ConnectorRegistry.create_connector(cfg)
        return self._active_instances[cfg.connector_id]

    async def test_connector(self, connector_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        cfg = await self.get_connector(connector_id, organization_id=organization_id)
        if not cfg:
            return {"success": False, "message": "Connector not found"}

        instance = self._get_instance(cfg)
        result = await instance.test_connection()

        new_status = ConnectorStatus.CONNECTED if result["success"] else ConnectorStatus.FAILED
        await self.update_connector_status(
            connector_id, new_status,
            error_message=None if result["success"] else result.get("message")
        )
        return result

    async def detect_schema(self, connector_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Step 2 of the flow: auto-detect schema and return it for user confirmation.
        Frontend shows this in a "review fields" UI.
        """
        cfg = await self.get_connector(connector_id, organization_id=organization_id)
        if not cfg:
            raise ValueError("Connector not found")

        instance = self._get_instance(cfg)
        schema = await instance.detect_schema()
        await self.update_schema(connector_id, schema)

        return self._schema_to_dict(schema)

    async def confirm_schema(
        self,
        connector_id: str,
        field_updates: List[Dict[str, Any]],
        entity_type_override: Optional[str] = None,
        organization_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Step 3: user has reviewed the auto-detected schema and made edits.
        field_updates: [{raw_name, user_confirmed_name, user_confirmed_type, mapped_to_entity_property}]
        """
        cfg = await self.get_connector(connector_id, organization_id=organization_id)
        if not cfg or not cfg.schema:
            raise ValueError("Connector or schema not found")

        schema = cfg.schema
        updates_by_name = {u["raw_name"]: u for u in field_updates}

        for field in schema.fields:
            if field.raw_name in updates_by_name:
                update = updates_by_name[field.raw_name]
                if update.get("user_confirmed_name"):
                    field.user_confirmed_name = update["user_confirmed_name"]
                if update.get("user_confirmed_type"):
                    field.user_confirmed_type = FieldType(update["user_confirmed_type"])
                if update.get("mapped_to_entity_property"):
                    field.mapped_to_entity_property = update["mapped_to_entity_property"]

        if entity_type_override:
            schema.suggested_entity_type = entity_type_override

        schema.user_confirmed = True
        schema.user_modified = True

        await self.update_schema(connector_id, schema)
        return {"success": True, "schema": self._schema_to_dict(schema)}

    async def sync_connector(self, connector_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Full sync: fetch from source -> normalize -> fuse into graph.
        This is what runs on schedule, or when user clicks "Sync now".
        """
        cfg = await self.get_connector(connector_id, organization_id=organization_id)
        if not cfg:
            return {"success": False, "error": "Connector not found"}

        # Fall back to the connector's own recorded organization if the
        # caller didn't scope the lookup (e.g. a scheduler running without
        # request-level tenant context) — fusion must always be tenant-tagged.
        tenant_id = organization_id or await self.get_connector_organization_id(connector_id)

        await self.update_connector_status(connector_id, ConnectorStatus.SYNCING)
        instance = self._get_instance(cfg)

        try:
            sync_result = await instance.sync()

            if not sync_result["success"]:
                await self.update_connector_status(
                    connector_id, ConnectorStatus.FAILED, error_message=sync_result.get("error")
                )
                await self._log_sync(connector_id, success=False, error=sync_result.get("error"))
                return sync_result

            normalized_records = sync_result["normalized_records"]
            fusion_result = await self.fusion_engine.fuse_batch(normalized_records, organization_id=tenant_id)

            drift = self._detect_schema_drift(cfg.schema, normalized_records)

            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE connectors SET status = $1, last_synced = now(), "
                    "total_records_ingested = total_records_ingested + $2, error_message = NULL, "
                    "schema_drift = $3 "
                    "WHERE connector_id = $4",
                    ConnectorStatus.CONNECTED.value, len(normalized_records),
                    json.dumps(drift) if drift else None, uuid.UUID(connector_id)
                )

            await self._log_sync(
                connector_id, success=True,
                records_fetched=sync_result["records_fetched"],
                records_normalized=sync_result["records_normalized"],
                entities_created=fusion_result["entities_created"],
                entities_merged=fusion_result["entities_merged"],
                conflicts_resolved=fusion_result["conflicts_resolved"]
            )

            return {
                "success": True,
                "connector_id": connector_id,
                "records_fetched": sync_result["records_fetched"],
                "fusion_result": fusion_result,
                "schema_drift": drift
            }

        except Exception as e:
            await self.update_connector_status(connector_id, ConnectorStatus.FAILED, error_message=str(e))
            await self._log_sync(connector_id, success=False, error=str(e))
            return {"success": False, "error": str(e)}

    def _detect_schema_drift(
        self, schema: Optional[SchemaDetectionResult], normalized_records: List[Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Compares the field names actually present in this sync's raw source
        data against the last user-confirmed schema. Schema detection only
        runs once, at connector setup — without this check, a source that
        adds/removes/renames fields after that point would keep ingesting
        silently, with nobody noticing until the mismatch surfaces as bad
        data downstream. Only runs against a confirmed schema — an
        unconfirmed one is still being set up, so drift is meaningless.
        """
        if not schema or not schema.user_confirmed or not normalized_records:
            return None

        known_fields = {f.raw_name for f in schema.fields}
        seen_fields: set = set()
        for record in normalized_records:
            seen_fields.update(record.raw_data.keys())

        new_fields = sorted(seen_fields - known_fields)
        # A field is "missing" only if literally no record in this batch has
        # it — a field that's merely null on some rows isn't drift.
        missing_fields = sorted(known_fields - seen_fields)

        if not new_fields and not missing_fields:
            return None

        return {
            "detected_at": datetime.utcnow().isoformat(),
            "new_fields": new_fields,
            "missing_fields": missing_fields,
        }

    async def _log_sync(
        self, connector_id: str, success: bool,
        records_fetched: int = 0, records_normalized: int = 0,
        entities_created: int = 0, entities_merged: int = 0,
        conflicts_resolved: int = 0, error: Optional[str] = None
    ):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sync_history "
                "(connector_id, success, records_fetched, records_normalized, "
                " entities_created, entities_merged, conflicts_resolved, error_message) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                uuid.UUID(connector_id), success, records_fetched, records_normalized,
                entities_created, entities_merged, conflicts_resolved, error
            )

    async def get_sync_history(
        self, connector_id: str, limit: int = 20, organization_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        # Sync history is looked up by connector_id, so verify the connector
        # itself belongs to this tenant first rather than joining every call —
        # a connector_id from another organization must yield nothing.
        if organization_id and not await self.get_connector(connector_id, organization_id=organization_id):
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM sync_history WHERE connector_id = $1 "
                "ORDER BY synced_at DESC LIMIT $2",
                uuid.UUID(connector_id), limit
            )
        return [dict(row) for row in rows]
