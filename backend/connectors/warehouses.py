"""
FRIS Cloud Data Warehouse Connectors
Snowflake and BigQuery — connects, introspects schema natively via
INFORMATION_SCHEMA, pulls data with optional incremental sync.

Both underlying drivers are synchronous, so every blocking call is bridged
to the async connector contract via asyncio.to_thread — the connector stays
non-blocking for the FastAPI event loop exactly like the async DB drivers do.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import (
    BaseConnector, ConnectorConfig, ConnectorType,
    DetectedField, FieldType, SchemaDetectionResult, SchemaDetector
)


# Snowflake INFORMATION_SCHEMA.COLUMNS DATA_TYPE → FRIS FieldType
SNOWFLAKE_TYPE_MAP = {
    "NUMBER": FieldType.NUMBER, "DECIMAL": FieldType.NUMBER,
    "NUMERIC": FieldType.NUMBER, "INT": FieldType.NUMBER,
    "INTEGER": FieldType.NUMBER, "BIGINT": FieldType.NUMBER,
    "SMALLINT": FieldType.NUMBER, "TINYINT": FieldType.NUMBER,
    "BYTEINT": FieldType.NUMBER, "FLOAT": FieldType.NUMBER,
    "FLOAT4": FieldType.NUMBER, "FLOAT8": FieldType.NUMBER,
    "DOUBLE": FieldType.NUMBER, "DOUBLE PRECISION": FieldType.NUMBER,
    "REAL": FieldType.NUMBER,
    "TEXT": FieldType.STRING, "VARCHAR": FieldType.STRING,
    "CHAR": FieldType.STRING, "CHARACTER": FieldType.STRING,
    "STRING": FieldType.STRING,
    "BOOLEAN": FieldType.BOOLEAN,
    "DATE": FieldType.DATE,
    "DATETIME": FieldType.DATETIME,
    "TIMESTAMP": FieldType.DATETIME, "TIMESTAMP_NTZ": FieldType.DATETIME,
    "TIMESTAMP_LTZ": FieldType.DATETIME, "TIMESTAMP_TZ": FieldType.DATETIME,
    "VARIANT": FieldType.JSON, "OBJECT": FieldType.JSON,
    "ARRAY": FieldType.ARRAY,
}

# BigQuery INFORMATION_SCHEMA.COLUMNS data_type → FRIS FieldType
BIGQUERY_TYPE_MAP = {
    "INT64": FieldType.NUMBER, "INTEGER": FieldType.NUMBER,
    "NUMERIC": FieldType.NUMBER, "BIGNUMERIC": FieldType.NUMBER,
    "FLOAT64": FieldType.NUMBER, "FLOAT": FieldType.NUMBER,
    "STRING": FieldType.STRING, "BYTES": FieldType.STRING,
    "BOOL": FieldType.BOOLEAN, "BOOLEAN": FieldType.BOOLEAN,
    "DATE": FieldType.DATE,
    "DATETIME": FieldType.DATETIME, "TIMESTAMP": FieldType.DATETIME,
    "TIME": FieldType.STRING,
    "STRUCT": FieldType.JSON, "RECORD": FieldType.JSON, "JSON": FieldType.JSON,
    "ARRAY": FieldType.ARRAY,
    "GEOGRAPHY": FieldType.STRING,
}


def _base_type(data_type: str) -> str:
    """Strip parameters/precision — 'NUMBER(38,0)' → 'NUMBER', 'VARCHAR(16777216)' → 'VARCHAR'."""
    return data_type.split("(")[0].strip().upper()


class SnowflakeConnector(BaseConnector):
    """
    Snowflake cloud data warehouse connector.
    Config keys:
        account: str            - Snowflake account identifier (e.g. "ab12345.eu-west-1")
        user: str
        password: str           - password auth (either this or private_key)
        private_key: str        - PEM private key for key-pair auth (alternative to password)
        private_key_passphrase: str - optional passphrase for the private key
        warehouse: str          - compute warehouse to use
        database: str           - required
        schema: str             - required, e.g. "PUBLIC"
        role: str               - optional role to assume
        table: str              - table/view to read from
        where_clause: str       - optional SQL WHERE filter
        incremental_column: str - timestamp column for incremental sync
    """

    CONNECTOR_TYPE = ConnectorType.SNOWFLAKE
    DISPLAY_NAME = "Snowflake"
    DESCRIPTION = "Connect to a Snowflake cloud data warehouse table or view"
    ICON = "❄️"
    DOCS = (
        "1. Provide your Snowflake account identifier, user, and either a password or a "
        "PEM private_key (key-pair auth is recommended for production/service accounts).\n"
        "2. Set warehouse, database, and schema. Optionally set role to assume a specific "
        "access role.\n"
        "3. Set table to the table or view to ingest. Schema is introspected natively from "
        "INFORMATION_SCHEMA, so field types are exact rather than guessed.\n"
        "4. Optionally set incremental_column to a timestamp column (e.g. 'UPDATED_AT') to "
        "pull only rows changed since the last sync."
    )

    CONFIG_SCHEMA = {
        "required": ["account", "user", "database", "schema", "table"],
        "optional": [
            "password", "private_key", "private_key_passphrase", "warehouse",
            "role", "where_clause", "incremental_column"
        ],
        "defaults": {}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config

    def _connect_params(self) -> Dict[str, Any]:
        cfg = self.cfg
        params: Dict[str, Any] = {
            "account": cfg["account"],
            "user": cfg["user"],
            "database": cfg["database"],
            "schema": cfg["schema"],
        }
        if cfg.get("warehouse"):
            params["warehouse"] = cfg["warehouse"]
        if cfg.get("role"):
            params["role"] = cfg["role"]

        if cfg.get("private_key"):
            params["private_key"] = self._load_private_key()
        elif cfg.get("password"):
            params["password"] = cfg["password"]
        else:
            raise RuntimeError("Snowflake connector requires either 'password' or 'private_key'")
        return params

    def _load_private_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        passphrase = self.cfg.get("private_key_passphrase")
        key_bytes = self.cfg["private_key"].encode() if isinstance(self.cfg["private_key"], str) else self.cfg["private_key"]
        pkey = serialization.load_pem_private_key(
            key_bytes,
            password=passphrase.encode() if passphrase else None,
            backend=default_backend(),
        )
        return pkey.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def _get_connection(self):
        try:
            import snowflake.connector
        except ImportError:
            raise RuntimeError(
                "snowflake-connector-python not installed. Run: pip install snowflake-connector-python"
            )
        return snowflake.connector.connect(**self._connect_params())

    def _run_query(self, query: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
        """Blocking: open connection, run a DictCursor query, return list of dicts."""
        from snowflake.connector import DictCursor
        conn = self._get_connection()
        try:
            cur = conn.cursor(DictCursor)
            try:
                cur.execute(query, params or [])
                return [dict(row) for row in cur.fetchall()]
            finally:
                cur.close()
        finally:
            conn.close()

    def _qualified_table(self) -> str:
        return f'"{self.cfg["database"]}"."{self.cfg["schema"]}"."{self.cfg["table"]}"'

    async def connect(self) -> bool:
        # Warehouse connections are opened per-query (short-lived) rather than pooled,
        # matching how Snowflake sessions are typically used. Validate config here.
        await asyncio.to_thread(lambda: self._connect_params())
        return True

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            start = time.time()
            rows = await asyncio.to_thread(self._run_query, "SELECT CURRENT_VERSION() AS V")
            latency_ms = int((time.time() - start) * 1000)
            version = rows[0].get("V") if rows else "unknown"
            return {
                "success": True,
                "message": "Connected to Snowflake",
                "latency_ms": latency_ms,
                "server_version": version,
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def detect_schema(self) -> SchemaDetectionResult:
        """Native introspection via INFORMATION_SCHEMA — exact types, not guessed."""
        cols_query = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_catalog = %s AND table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """
        columns = await asyncio.to_thread(
            self._run_query, cols_query,
            [self.cfg["database"], self.cfg["schema"], self.cfg["table"]],
        )
        sample = await self.fetch_sample(limit=5)

        fields = []
        for col in columns:
            col_name = col["COLUMN_NAME"]
            fris_type = SNOWFLAKE_TYPE_MAP.get(_base_type(col["DATA_TYPE"]), FieldType.STRING)
            sample_vals = [str(r.get(col_name)) for r in sample if r.get(col_name) is not None]
            fields.append(DetectedField(
                raw_name=col_name,
                suggested_name=col_name.lower(),
                field_type=fris_type,
                sample_values=sample_vals[:5],
                null_count=0,
                is_identifier=SchemaDetector._is_identifier(col_name),
                is_entity_name=SchemaDetector._is_name_field(col_name),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0,
            ))

        entity_type = SchemaDetector._suggest_entity_type(fields, ConnectorType.SNOWFLAKE)
        result = SchemaDetectionResult(
            connector_id=self.connector_id,
            detected_at=datetime.utcnow(),
            fields=fields,
            sample_row_count=len(sample),
            suggested_entity_type=entity_type,
            confidence=1.0,
            raw_sample=sample,
        )
        self.config.schema = result
        return result

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {self._qualified_table()} LIMIT {int(limit)}"
        return await asyncio.to_thread(self._run_query, query)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        where_parts = []
        params: list = []
        if self.cfg.get("where_clause"):
            where_parts.append(self.cfg["where_clause"])
        if since and self.cfg.get("incremental_column"):
            where_parts.append(f'"{self.cfg["incremental_column"]}" > %s')
            params.append(since)
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f"SELECT * FROM {self._qualified_table()} {where_sql}"
        return await asyncio.to_thread(self._run_query, query, params)


class BigQueryConnector(BaseConnector):
    """
    Google BigQuery cloud data warehouse connector.
    Config keys:
        project_id: str              - GCP project id (required)
        dataset: str                 - BigQuery dataset (required)
        table: str                   - table/view to read (required)
        credentials_json: str/dict   - service-account key JSON (string or dict).
                                       If omitted, Application Default Credentials are used.
        location: str                - dataset location, e.g. "US", "EU"
        where_clause: str            - optional SQL WHERE filter
        incremental_column: str      - timestamp column for incremental sync
    """

    CONNECTOR_TYPE = ConnectorType.BIGQUERY
    DISPLAY_NAME = "Google BigQuery"
    DESCRIPTION = "Connect to a Google BigQuery dataset table or view"
    ICON = "🔷"
    DOCS = (
        "1. Provide project_id, dataset, and table.\n"
        "2. Provide credentials_json (a service-account key, as JSON text or object). If your "
        "environment already has Application Default Credentials, you may omit it.\n"
        "3. Optionally set location to your dataset's region (e.g. 'US' or 'EU').\n"
        "4. Schema is introspected natively from INFORMATION_SCHEMA. Optionally set "
        "incremental_column to a timestamp column to pull only rows changed since last sync."
    )

    CONFIG_SCHEMA = {
        "required": ["project_id", "dataset", "table"],
        "optional": ["credentials_json", "location", "where_clause", "incremental_column"],
        "defaults": {}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config

    def _get_client(self):
        try:
            from google.cloud import bigquery
        except ImportError:
            raise RuntimeError(
                "google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery"
            )

        creds = self.cfg.get("credentials_json")
        kwargs: Dict[str, Any] = {"project": self.cfg["project_id"]}
        if self.cfg.get("location"):
            kwargs["location"] = self.cfg["location"]

        if creds:
            import json
            from google.oauth2 import service_account
            info = json.loads(creds) if isinstance(creds, str) else creds
            kwargs["credentials"] = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(**kwargs)

    def _run_query(self, query: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
        """Blocking: run a query and return rows as plain dicts."""
        from google.cloud import bigquery
        client = self._get_client()
        job_config = None
        if params:
            job_config = bigquery.QueryJobConfig(query_parameters=params)
        job = client.query(query, job_config=job_config)
        rows = job.result()
        out = []
        for row in rows:
            d = dict(row.items())
            # Coerce non-JSON-native values (datetime, Decimal, etc.) to str for downstream safety
            out.append({k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                        for k, v in d.items()})
        return out

    def _qualified_table(self) -> str:
        return f"`{self.cfg['project_id']}.{self.cfg['dataset']}.{self.cfg['table']}`"

    async def connect(self) -> bool:
        await asyncio.to_thread(self._get_client)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            start = time.time()
            await asyncio.to_thread(self._run_query, "SELECT 1 AS ok")
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": "Connected to BigQuery",
                "latency_ms": latency_ms,
                "server_version": "bigquery",
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def detect_schema(self) -> SchemaDetectionResult:
        cols_query = f"""
            SELECT column_name, data_type
            FROM `{self.cfg['project_id']}.{self.cfg['dataset']}.INFORMATION_SCHEMA.COLUMNS`
            WHERE table_name = @table
            ORDER BY ordinal_position
        """
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("table", "STRING", self.cfg["table"])]
        columns = await asyncio.to_thread(self._run_query, cols_query, params)
        sample = await self.fetch_sample(limit=5)

        fields = []
        for col in columns:
            col_name = col["column_name"]
            fris_type = BIGQUERY_TYPE_MAP.get(_base_type(col["data_type"]), FieldType.STRING)
            sample_vals = [str(r.get(col_name)) for r in sample if r.get(col_name) is not None]
            fields.append(DetectedField(
                raw_name=col_name,
                suggested_name=col_name.lower(),
                field_type=fris_type,
                sample_values=sample_vals[:5],
                null_count=0,
                is_identifier=SchemaDetector._is_identifier(col_name),
                is_entity_name=SchemaDetector._is_name_field(col_name),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0,
            ))

        entity_type = SchemaDetector._suggest_entity_type(fields, ConnectorType.BIGQUERY)
        result = SchemaDetectionResult(
            connector_id=self.connector_id,
            detected_at=datetime.utcnow(),
            fields=fields,
            sample_row_count=len(sample),
            suggested_entity_type=entity_type,
            confidence=1.0,
            raw_sample=sample,
        )
        self.config.schema = result
        return result

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {self._qualified_table()} LIMIT {int(limit)}"
        return await asyncio.to_thread(self._run_query, query)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        from google.cloud import bigquery
        where_parts = []
        params: list = []
        if self.cfg.get("where_clause"):
            where_parts.append(self.cfg["where_clause"])
        if since and self.cfg.get("incremental_column"):
            where_parts.append(f"`{self.cfg['incremental_column']}` > @since")
            params.append(bigquery.ScalarQueryParameter("since", "TIMESTAMP", since))
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f"SELECT * FROM {self._qualified_table()} {where_sql}"
        return await asyncio.to_thread(self._run_query, query, params)
