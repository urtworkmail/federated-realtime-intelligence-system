"""
FRIS SAP Connector
Connects to SAP via OData services (SAP Gateway / SAP S/4HANA Cloud OData APIs).

Note on approach: SAP's classic RFC protocol requires SAP's proprietary
NW RFC SDK (distributed under SAP license, not available via pip), so a
generic RFC connector can't be shipped without that licensed binary.
OData is SAP's standard modern integration layer — exposed by S/4HANA,
SAP Gateway, and Business Technology Platform — and works over plain
HTTPS with no proprietary SDK required, so that's what this implements.
If your SAP landscape only exposes classic RFC/BAPI, the Custom connector
can wrap your own pyrfc-based fetch function instead.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseConnector, ConnectorConfig, ConnectorType


class SAPConnector(BaseConnector):
    """
    Config keys:
        base_url: str        - required, e.g. "https://your-sap-host:8000/sap/opu/odata/sap"
        service: str         - required, OData service name, e.g. "ZFRIS_SRV"
        entity_set: str      - required, e.g. "Companies" or "SalesOrders"
        username: str        - required
        password: str        - required
        odata_version: str   - optional, "v2" | "v4", default "v2"
        select_fields: str   - optional, OData $select clause to limit fields
        timeout_seconds: int - optional, default 30
    """

    CONNECTOR_TYPE = ConnectorType.SAP
    DISPLAY_NAME = "SAP"
    DESCRIPTION = "Enterprise resource planning data from SAP (via OData services)"
    ICON = "🏢"
    DOCS = (
        "1. This connector uses SAP's OData services (SAP Gateway / S/4HANA), not classic RFC — "
        "ask your SAP Basis team for the OData service URL, which usually looks like "
        "https://<host>:<port>/sap/opu/odata/sap/<SERVICE_NAME>.\n"
        "2. Enter the service name and the specific entity_set you want to ingest "
        "(e.g. 'BusinessPartnerSet', 'SalesOrderSet') — your Basis team can list available "
        "entity sets via the service's $metadata document.\n"
        "3. Provide an SAP user with read authorization for that service (a dedicated "
        "service/communication user is recommended over a personal login).\n"
        "4. If your landscape only exposes classic RFC/BAPI (no OData layer), use the Custom "
        "connector instead and wrap a pyrfc-based fetch function — RFC requires SAP's "
        "proprietary NW RFC SDK which FRIS does not bundle."
    )

    CONFIG_SCHEMA = {
        "required": ["base_url", "service", "entity_set", "username", "password"],
        "optional": ["odata_version", "select_fields", "timeout_seconds"],
        "defaults": {"odata_version": "v2", "timeout_seconds": 30}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _entity_url(self) -> str:
        return (
            self.cfg["base_url"].rstrip("/") + "/" +
            self.cfg["service"].strip("/") + "/" +
            self.cfg["entity_set"].strip("/")
        )

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            auth=(self.cfg["username"], self.cfg["password"]),
            timeout=self.cfg.get("timeout_seconds", 30),
            headers={"Accept": "application/json"}
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            params = {"$top": "1", "$format": "json"}
            response = await self._client.get(self._entity_url(), params=params)
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code < 400:
                return {"success": True, "message": f"Connected (HTTP {response.status_code})", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: {response.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    def _extract_records(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        # OData v2 wraps results in d.results, v4 uses "value" directly
        if "d" in payload and "results" in payload.get("d", {}):
            return payload["d"]["results"]
        if "value" in payload:
            return payload["value"]
        return []

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        params = {"$top": str(limit), "$format": "json"}
        if self.cfg.get("select_fields"):
            params["$select"] = self.cfg["select_fields"]
        response = await self._client.get(self._entity_url(), params=params)
        response.raise_for_status()
        return self._extract_records(response.json())[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        all_records: List[Dict[str, Any]] = []
        skip = 0
        page_size = 1000
        while True:
            params = {"$top": str(page_size), "$skip": str(skip), "$format": "json"}
            if self.cfg.get("select_fields"):
                params["$select"] = self.cfg["select_fields"]
            response = await self._client.get(self._entity_url(), params=params)
            response.raise_for_status()
            records = self._extract_records(response.json())
            if not records:
                break
            all_records.extend(records)
            if len(records) < page_size:
                break
            skip += page_size
        return all_records

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
