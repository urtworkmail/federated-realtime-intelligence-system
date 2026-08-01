"""
FRIS Database Connectors
PostgreSQL and MySQL — connects, introspects schema natively, pulls data.
Uses native schema inspection (information_schema) so detection is exact, not guessed.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import (
    BaseConnector, ConnectorConfig, ConnectorType,
    DetectedField, FieldType, SchemaDetectionResult
)


# Native DB type → FRIS FieldType mapping
PG_TYPE_MAP = {
    "integer": FieldType.NUMBER, "bigint": FieldType.NUMBER,
    "smallint": FieldType.NUMBER, "numeric": FieldType.NUMBER,
    "real": FieldType.NUMBER, "double precision": FieldType.NUMBER,
    "text": FieldType.STRING, "varchar": FieldType.STRING,
    "character varying": FieldType.STRING, "char": FieldType.STRING,
    "boolean": FieldType.BOOLEAN,
    "date": FieldType.DATE,
    "timestamp": FieldType.DATETIME, "timestamptz": FieldType.DATETIME,
    "timestamp without time zone": FieldType.DATETIME,
    "timestamp with time zone": FieldType.DATETIME,
    "json": FieldType.JSON, "jsonb": FieldType.JSON,
    "array": FieldType.ARRAY,
    "uuid": FieldType.STRING,
}

MYSQL_TYPE_MAP = {
    "int": FieldType.NUMBER, "bigint": FieldType.NUMBER,
    "tinyint": FieldType.NUMBER, "smallint": FieldType.NUMBER,
    "mediumint": FieldType.NUMBER, "float": FieldType.NUMBER,
    "double": FieldType.NUMBER, "decimal": FieldType.NUMBER,
    "varchar": FieldType.STRING, "text": FieldType.STRING,
    "longtext": FieldType.STRING, "mediumtext": FieldType.STRING,
    "char": FieldType.STRING, "enum": FieldType.STRING,
    "tinyint(1)": FieldType.BOOLEAN,
    "date": FieldType.DATE,
    "datetime": FieldType.DATETIME, "timestamp": FieldType.DATETIME,
    "json": FieldType.JSON,
}


class PostgreSQLConnector(BaseConnector):
    """
    PostgreSQL connector.
    Config keys:
        host: str
        port: int (default 5432)
        database: str
        username: str
        password: str
        table: str              - which table to read from
        schema: str             - postgres schema, default "public"
        where_clause: str       - optional SQL WHERE clause filter
        incremental_column: str - column to use for incremental sync (e.g. "updated_at")
        ssl_mode: str           - "disable" | "require" | "verify-full"
    """

    CONNECTOR_TYPE = ConnectorType.POSTGRESQL
    DISPLAY_NAME = "PostgreSQL"
    DESCRIPTION = "Connect to a PostgreSQL database table"
    ICON = "🐘"
    DOCS = (
        "1. Provide host, port (default 5432), database name, username, and password for "
        "a read-only connection.\n"
        "2. Set table_or_query to either a table name (e.g. 'customers') or a full SELECT "
        "statement for more control over which columns/rows are pulled.\n"
        "3. Optionally set updated_column to a timestamp column to enable incremental sync — "
        "only rows updated since the last sync are pulled."
    )

    CONFIG_SCHEMA = {
        "required": ["host", "database", "username", "password", "table"],
        "optional": ["port", "schema", "where_clause", "incremental_column", "ssl_mode"],
        "defaults": {"port": 5432, "schema": "public", "ssl_mode": "require"}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._pool = None

    def _build_dsn(self) -> str:
        cfg = self.cfg
        ssl = cfg.get("ssl_mode", "require")
        return (
            f"postgresql://{cfg['username']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg.get('port', 5432)}"
            f"/{cfg['database']}?sslmode={ssl}"
        )

    async def connect(self) -> bool:
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._build_dsn(), min_size=1, max_size=5)
            return True
        except ImportError:
            raise RuntimeError("asyncpg not installed. Run: pip install asyncpg")

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            if not self._pool:
                await self.connect()
            start = time.time()
            async with self._pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"Connected to PostgreSQL",
                "latency_ms": latency_ms,
                "server_version": version.split(" ")[1] if version else "unknown"
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def detect_schema(self) -> SchemaDetectionResult:
        """Override to use native PostgreSQL schema introspection — more accurate than guessing."""
        if not self._pool:
            await self.connect()

        schema_name = self.cfg.get("schema", "public")
        table_name = self.cfg["table"]

        async with self._pool.acquire() as conn:
            # Get column info from information_schema
            columns = await conn.fetch("""
                SELECT
                    column_name,
                    data_type,
                    is_nullable,
                    column_default,
                    character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """, schema_name, table_name)

            # Get primary key columns
            pk_columns = await conn.fetch("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_schema = $1
                    AND tc.table_name = $2
                    AND tc.constraint_type = 'PRIMARY KEY'
            """, schema_name, table_name)
            pk_cols = {row["column_name"] for row in pk_columns}

            # Get sample data
            sample_rows = await conn.fetch(
                f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT 5'
            )
            sample_dicts = [dict(row) for row in sample_rows]

        fields = []
        for col in columns:
            col_name = col["column_name"]
            db_type = col["data_type"].lower()
            fris_type = PG_TYPE_MAP.get(db_type, FieldType.STRING)

            sample_vals = [str(row.get(col_name)) for row in sample_dicts if row.get(col_name) is not None]

            field = DetectedField(
                raw_name=col_name,
                suggested_name=col_name.lower(),
                field_type=fris_type,
                sample_values=sample_vals[:5],
                null_count=0,
                is_identifier=col_name in pk_cols,
                is_entity_name="name" in col_name.lower() or "title" in col_name.lower(),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0  # native introspection = high confidence
            )
            fields.append(field)

        from .base import SchemaDetector
        entity_type = SchemaDetector._suggest_entity_type(fields, ConnectorType.POSTGRESQL)

        result = SchemaDetectionResult(
            connector_id=self.connector_id,
            detected_at=datetime.utcnow(),
            fields=fields,
            sample_row_count=len(sample_dicts),
            suggested_entity_type=entity_type,
            confidence=1.0,
            raw_sample=sample_dicts
        )
        self.config.schema = result
        return result

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._pool:
            await self.connect()
        schema = self.cfg.get("schema", "public")
        table = self.cfg["table"]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f'SELECT * FROM "{schema}"."{table}" LIMIT $1', limit)
        return [dict(row) for row in rows]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._pool:
            await self.connect()

        schema = self.cfg.get("schema", "public")
        table = self.cfg["table"]
        where_parts = []
        args = []

        if self.cfg.get("where_clause"):
            where_parts.append(self.cfg["where_clause"])

        if since and self.cfg.get("incremental_column"):
            args.append(since)
            where_parts.append(f'"{self.cfg["incremental_column"]}" > ${len(args)}')

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f'SELECT * FROM "{schema}"."{table}" {where_sql}'

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


class MySQLConnector(BaseConnector):
    """
    MySQL / MariaDB connector.
    Config keys: host, port (3306), database, username, password, table,
                 where_clause, incremental_column, ssl_ca
    """

    CONNECTOR_TYPE = ConnectorType.MYSQL
    DISPLAY_NAME = "MySQL / MariaDB"
    DESCRIPTION = "Connect to a MySQL or MariaDB database table"
    ICON = "🐬"
    DOCS = (
        "1. Provide host, port (default 3306), database name, username, and password.\n"
        "2. Set table_or_query to a table name or a full SELECT statement.\n"
        "3. Optionally set updated_column for incremental sync on subsequent runs."
    )

    CONFIG_SCHEMA = {
        "required": ["host", "database", "username", "password", "table"],
        "optional": ["port", "where_clause", "incremental_column", "ssl_ca"],
        "defaults": {"port": 3306}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._conn = None

    async def connect(self) -> bool:
        try:
            import aiomysql
            self._conn = await aiomysql.create_pool(
                host=self.cfg["host"],
                port=self.cfg.get("port", 3306),
                db=self.cfg["database"],
                user=self.cfg["username"],
                password=self.cfg["password"],
                minsize=1, maxsize=5,
                autocommit=True
            )
            return True
        except ImportError:
            raise RuntimeError("aiomysql not installed. Run: pip install aiomysql")

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            if not self._conn:
                await self.connect()
            start = time.time()
            async with self._conn.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT VERSION()")
                    version = await cur.fetchone()
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": "Connected to MySQL",
                "latency_ms": latency_ms,
                "server_version": version[0] if version else "unknown"
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def detect_schema(self) -> SchemaDetectionResult:
        if not self._conn:
            await self.connect()

        table = self.cfg["table"]
        db = self.cfg["database"]

        async with self._conn.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                """, (db, table))
                columns = await cur.fetchall()

                await cur.execute(f"SELECT * FROM `{table}` LIMIT 5")
                sample_rows = await cur.fetchall()

        import aiomysql
        fields = []
        for col in columns:
            col_name = col["COLUMN_NAME"]
            db_type = col["DATA_TYPE"].lower()
            fris_type = MYSQL_TYPE_MAP.get(db_type, FieldType.STRING)
            sample_vals = [str(row.get(col_name)) for row in sample_rows if row.get(col_name) is not None]

            fields.append(DetectedField(
                raw_name=col_name,
                suggested_name=col_name.lower(),
                field_type=fris_type,
                sample_values=sample_vals[:5],
                null_count=0,
                is_identifier=col.get("COLUMN_KEY") == "PRI",
                is_entity_name="name" in col_name.lower(),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0
            ))

        from .base import SchemaDetector
        entity_type = SchemaDetector._suggest_entity_type(fields, ConnectorType.MYSQL)

        result = SchemaDetectionResult(
            connector_id=self.connector_id,
            detected_at=datetime.utcnow(),
            fields=fields,
            sample_row_count=len(list(sample_rows)),
            suggested_entity_type=entity_type,
            confidence=1.0,
            raw_sample=[dict(r) for r in sample_rows]
        )
        self.config.schema = result
        return result

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._conn:
            await self.connect()
        import aiomysql
        table = self.cfg["table"]
        async with self._conn.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f"SELECT * FROM `{table}` LIMIT %s", (limit,))
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._conn:
            await self.connect()
        import aiomysql
        table = self.cfg["table"]
        where_parts = []
        args = []

        if self.cfg.get("where_clause"):
            where_parts.append(self.cfg["where_clause"])
        if since and self.cfg.get("incremental_column"):
            where_parts.append(f"`{self.cfg['incremental_column']}` > %s")
            args.append(since)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f"SELECT * FROM `{table}` {where_sql}"

        async with self._conn.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, args)
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
