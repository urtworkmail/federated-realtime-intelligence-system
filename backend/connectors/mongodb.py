"""
FRIS MongoDB Connector
Connects to a MongoDB collection via Motor (the official async driver).
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from .base import BaseConnector, ConnectorConfig, ConnectorType


class MongoDBConnector(BaseConnector):
    """
    Config keys:
        connection_string: str  - required, e.g. "mongodb://user:pass@host:27017"
        database: str           - required
        collection: str         - required
        query_filter: dict      - optional, MongoDB filter document for fetch_all
        updated_field: str      - optional, field name used for incremental sync, default "updated_at"
    """

    CONNECTOR_TYPE = ConnectorType.MONGODB
    DISPLAY_NAME = "MongoDB"
    DESCRIPTION = "Connect to MongoDB collections"
    ICON = "🍃"
    DOCS = (
        "1. Get your MongoDB connection string (mongodb:// or mongodb+srv://) from your "
        "cluster's connection settings — for MongoDB Atlas this is in 'Connect > Drivers'.\n"
        "2. Enter the database name and the specific collection you want FRIS to ingest from.\n"
        "3. Optional: provide a query_filter (standard MongoDB filter JSON, e.g. "
        "{\"status\": \"active\"}) to limit which documents are pulled.\n"
        "4. Make sure your MongoDB user has read access and your IP/network allows the connection "
        "(Atlas requires whitelisting FRIS's outbound IP if not using 0.0.0.0/0)."
    )

    CONFIG_SCHEMA = {
        "required": ["connection_string", "database", "collection"],
        "optional": ["query_filter", "updated_field"],
        "defaults": {"updated_field": "updated_at"}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[AsyncIOMotorClient] = None

    def _collection(self):
        db = self._client[self.cfg["database"]]
        return db[self.cfg["collection"]]

    async def connect(self) -> bool:
        self._client = AsyncIOMotorClient(
            self.cfg["connection_string"], serverSelectionTimeoutMS=8000
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            await self._client.admin.command("ping")
            count = await self._collection().estimated_document_count()
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"Connected. Collection has ~{count} documents.",
                "latency_ms": latency_ms
            }
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        docs = await self._collection().find(self.cfg.get("query_filter") or {}).limit(limit).to_list(length=limit)
        return [self._stringify_id(d) for d in docs]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        query = dict(self.cfg.get("query_filter") or {})
        if since:
            query[self.cfg.get("updated_field", "updated_at")] = {"$gte": since}
        cursor = self._collection().find(query)
        docs = await cursor.to_list(length=None)
        return [self._stringify_id(d) for d in docs]

    @staticmethod
    def _stringify_id(doc: Dict[str, Any]) -> Dict[str, Any]:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            self._client.close()
