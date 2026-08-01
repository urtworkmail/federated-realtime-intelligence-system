"""
FRIS Custom Connector
Lets users define their own connector using a simple Python-like config.
Two modes:
  1. Simple: URL + headers + field mapping (no code needed)
  2. Advanced: user provides a fetch function in Python
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseConnector, ConnectorConfig, ConnectorType


class CustomConnector(BaseConnector):
    """
    Fully user-defined connector.
    Config keys (Simple mode):
        fetch_url: str              - URL to GET data from
        method: str                 - "GET" | "POST", default GET
        headers: dict               - request headers
        body: dict                  - request body for POST
        auth_type: str              - same options as REST API connector
        auth_token: str
        api_key: str
        data_path: str              - JSON path to records
        field_mapping: dict         - {source_field: fris_field} renames
        entity_type: str            - what entity this represents
        sync_interval: int          - seconds between syncs

    Config keys (Advanced mode):
        mode: "advanced"
        fetch_code: str             - Python code string defining async def fetch(config) -> list
        description: str            - what this connector does
        entity_type: str
    """

    CONNECTOR_TYPE = ConnectorType.CUSTOM
    DISPLAY_NAME = "Custom Connector"
    DESCRIPTION = "Define your own data source — simple URL config or advanced Python"
    ICON = "⚙️"
    DOCS = (
        "1. Simple mode: provide a URL and a field mapping — works like a lightweight REST "
        "connector for sources that don't fit the standard templates.\n"
        "2. Advanced mode: write a Python fetch function (template provided in the wizard) "
        "that returns a list of dicts — runs in a restricted namespace for safety. Use this "
        "for sources needing custom auth flows, multi-step API calls, or non-standard SDKs.\n"
        "3. Use this connector type when none of the built-in connectors match your source."
    )

    CONFIG_SCHEMA = {
        "required_one_of": ["fetch_url", "fetch_code"],
        "optional": [
            "method", "headers", "body", "auth_type", "auth_token",
            "api_key", "data_path", "field_mapping", "entity_type",
            "sync_interval", "mode", "description"
        ],
        "defaults": {
            "method": "GET",
            "mode": "simple",
            "entity_type": "CustomEntity",
            "sync_interval": 300
        }
    }

    # Template for users to see how to write advanced fetch code
    FETCH_CODE_TEMPLATE = '''
async def fetch(config: dict) -> list:
    """
    Fetch data from your source.
    config: the config dict you set on this connector.
    Return a list of dicts, one per record.
    """
    import httpx

    # Example: fetch from a custom API
    async with httpx.AsyncClient() as client:
        response = await client.get(
            config["fetch_url"],
            headers=config.get("headers", {})
        )
        data = response.json()

    # Transform and return records
    records = []
    for item in data.get("items", []):
        records.append({
            "id": item["id"],
            "name": item["name"],
            # ... map your fields
        })
    return records
'''.strip()

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None
        self._compiled_fetch = None

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.cfg.get("headers"):
            headers.update(self.cfg["headers"])

        auth_type = self.cfg.get("auth_type", "none")
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.cfg.get('auth_token', '')}"
        elif auth_type == "api_key_header":
            headers["X-API-Key"] = self.cfg.get("api_key", "")

        return headers

    def _extract_data(self, response_json: Any) -> List[Dict[str, Any]]:
        data_path = self.cfg.get("data_path", "")
        if not data_path:
            if isinstance(response_json, list):
                return response_json
            if isinstance(response_json, dict):
                for key in ["data", "results", "items", "records"]:
                    if key in response_json and isinstance(response_json[key], list):
                        return response_json[key]
                return [response_json]
            return []

        current = response_json
        for key in data_path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return []
        return current if isinstance(current, list) else [current]

    def _apply_field_mapping(self, records: List[Dict]) -> List[Dict]:
        field_mapping = self.cfg.get("field_mapping", {})
        if not field_mapping:
            return records

        mapped = []
        for record in records:
            new_record = {}
            for source_key, value in record.items():
                mapped_key = field_mapping.get(source_key, source_key)
                new_record[mapped_key] = value
            mapped.append(new_record)
        return mapped

    def _compile_advanced_fetch(self):
        """Compile and cache the user-provided fetch function."""
        if self._compiled_fetch:
            return self._compiled_fetch

        fetch_code = self.cfg.get("fetch_code", "")
        if not fetch_code:
            raise ValueError("No fetch_code provided for advanced custom connector")

        # Security: run in restricted namespace
        namespace = {
            "__builtins__": {
                "len": len, "str": str, "int": int, "float": float,
                "bool": bool, "list": list, "dict": dict, "print": print,
                "range": range, "enumerate": enumerate, "zip": zip,
                "map": map, "filter": filter, "sorted": sorted,
                "isinstance": isinstance, "hasattr": hasattr,
                "getattr": getattr
            }
        }

        try:
            exec(fetch_code, namespace)
            self._compiled_fetch = namespace.get("fetch")
            if not self._compiled_fetch:
                raise ValueError("fetch_code must define an async function named 'fetch'")
            return self._compiled_fetch
        except SyntaxError as e:
            raise ValueError(f"Syntax error in fetch_code: {e}")

    async def connect(self) -> bool:
        mode = self.cfg.get("mode", "simple")
        if mode == "advanced":
            self._compile_advanced_fetch()
        else:
            self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client and self.cfg.get("mode", "simple") == "simple":
                await self.connect()

            start = time.time()
            mode = self.cfg.get("mode", "simple")

            if mode == "advanced":
                fetch_fn = self._compile_advanced_fetch()
                records = await fetch_fn(self.cfg)
                latency_ms = int((time.time() - start) * 1000)
                return {
                    "success": True,
                    "message": f"Advanced fetch returned {len(records)} records",
                    "latency_ms": latency_ms,
                    "record_count": len(records)
                }
            else:
                url = self.cfg["fetch_url"]
                method = self.cfg.get("method", "GET").upper()
                if method == "POST":
                    response = await self._client.post(url, headers=self._build_headers(),
                                                        json=self.cfg.get("body", {}))
                else:
                    response = await self._client.get(url, headers=self._build_headers())

                latency_ms = int((time.time() - start) * 1000)
                if response.status_code < 400:
                    data = self._extract_data(response.json())
                    return {
                        "success": True,
                        "message": f"Connected: {len(data)} records found",
                        "latency_ms": latency_ms,
                        "sample_count": len(data)
                    }
                return {"success": False, "message": f"HTTP {response.status_code}", "latency_ms": latency_ms}

        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        records = await self.fetch_all()
        return records[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client and self.cfg.get("mode", "simple") == "simple":
            await self.connect()

        mode = self.cfg.get("mode", "simple")

        if mode == "advanced":
            fetch_fn = self._compile_advanced_fetch()
            records = await fetch_fn(self.cfg)
            return self._apply_field_mapping(records)

        url = self.cfg["fetch_url"]
        method = self.cfg.get("method", "GET").upper()

        if method == "POST":
            response = await self._client.post(url, headers=self._build_headers(),
                                                json=self.cfg.get("body", {}))
        else:
            response = await self._client.get(url, headers=self._build_headers())

        response.raise_for_status()
        records = self._extract_data(response.json())
        return self._apply_field_mapping(records)
