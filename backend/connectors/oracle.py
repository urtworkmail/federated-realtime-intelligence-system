"""
FRIS Oracle DB Connector
Connects to Oracle databases using python-oracledb in its async (thin) mode
- no Oracle Instant Client install required, pure Python driver.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import oracledb

from .base import BaseConnector, ConnectorConfig, ConnectorType


class OracleConnector(BaseConnector):
    """
    Config keys:
        host: str             - required
        port: int             - optional, default 1521
        service_name: str     - required, e.g. "ORCLPDB1"
        username: str         - required
        password: str         - required
        table_or_query: str   - required, table name or full SELECT statement
        updated_column: str   - optional, column name for incremental sync
    """

    CONNECTOR_TYPE = ConnectorType.ORACLE
    DISPLAY_NAME = "Oracle DB"
    DESCRIPTION = "Connect to Oracle databases"
    ICON = "🔶"
    DOCS = (
        "1. Provide the host, port (default 1521), and service_name of your Oracle instance "
        "(your DBA can confirm this from the tnsnames.ora entry or connect string).\n"
        "2. Enter credentials for a read-only user scoped to the schema you want FRIS to access.\n"
        "3. table_or_query can be a plain table name (e.g. 'CUSTOMERS') or a full SELECT "
        "statement for more control (e.g. 'SELECT * FROM SALES WHERE REGION = ''EMEA''').\n"
        "4. Uses python-oracledb's thin mode — no Oracle Instant Client installation needed "
        "on the FRIS side."
    )

    CONFIG_SCHEMA = {
        "required": ["host", "service_name", "username", "password", "table_or_query"],
        "optional": ["port", "updated_column"],
        "defaults": {"port": 1521}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._pool: Optional[oracledb.AsyncConnectionPool] = None

    def _dsn(self) -> str:
        return oracledb.makedsn(
            self.cfg["host"], self.cfg.get("port", 1521), service_name=self.cfg["service_name"]
        )

    async def connect(self) -> bool:
        self._pool = oracledb.create_pool_async(
            user=self.cfg["username"], password=self.cfg["password"],
            dsn=self._dsn(), min=1, max=4
        )
        return True

    def _base_query(self) -> str:
        raw = self.cfg["table_or_query"].strip()
        if raw.upper().startswith("SELECT"):
            return raw
        return f"SELECT * FROM {raw}"

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._pool:
                await self.connect()
            start = time.time()
            async with self._pool.acquire() as conn:
                cursor = conn.cursor()
                await cursor.execute("SELECT 1 FROM DUAL")
                await cursor.fetchone()
            latency_ms = int((time.time() - start) * 1000)
            return {"success": True, "message": "Connected successfully", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def _run_query(self, query: str) -> List[Dict[str, Any]]:
        if not self._pool:
            await self.connect()
        async with self._pool.acquire() as conn:
            cursor = conn.cursor()
            await cursor.execute(query)
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM ({self._base_query()}) WHERE ROWNUM <= {limit}"
        return await self._run_query(query)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        query = self._base_query()
        if since and self.cfg.get("updated_column"):
            ts = since.strftime("%Y-%m-%d %H:%M:%S")
            connector = "WHERE" if "WHERE" not in query.upper() else "AND"
            query = f"{query} {connector} {self.cfg['updated_column']} >= TO_TIMESTAMP('{ts}', 'YYYY-MM-DD HH24:MI:SS')"
        return await self._run_query(query)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._pool:
            await self._pool.close()
