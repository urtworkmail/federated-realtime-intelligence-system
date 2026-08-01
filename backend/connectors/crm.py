"""
FRIS CRM Connectors
Salesforce and HubSpot — pull CRM objects (accounts/companies, contacts,
opportunities/deals) with native field-schema introspection and incremental sync.

Both are pure HTTP (httpx) — no vendor SDK dependency. Records are flattened to
plain dicts so the standard SchemaDetector/normalizer path applies, and standard
CRM objects map onto FRIS canonical entity types (Organization / Person / Transaction).
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import (
    BaseConnector, ConnectorConfig, ConnectorType,
    DetectedField, FieldType, SchemaDetectionResult, SchemaDetector
)


# Standard-object → FRIS canonical entity type
SALESFORCE_ENTITY_MAP = {
    "account": "Organization",
    "contact": "Person",
    "lead": "Person",
    "opportunity": "Transaction",
    "case": "Event",
    "campaign": "Event",
}

HUBSPOT_ENTITY_MAP = {
    "companies": "Organization",
    "contacts": "Person",
    "deals": "Transaction",
    "tickets": "Event",
}

# Salesforce describe() field type → FRIS FieldType
SALESFORCE_TYPE_MAP = {
    "id": FieldType.STRING, "reference": FieldType.STRING,
    "string": FieldType.STRING, "textarea": FieldType.STRING,
    "phone": FieldType.STRING, "email": FieldType.STRING,
    "url": FieldType.STRING, "picklist": FieldType.STRING,
    "multipicklist": FieldType.STRING, "combobox": FieldType.STRING,
    "encryptedstring": FieldType.STRING, "address": FieldType.JSON,
    "double": FieldType.NUMBER, "int": FieldType.NUMBER,
    "currency": FieldType.NUMBER, "percent": FieldType.NUMBER,
    "boolean": FieldType.BOOLEAN,
    "date": FieldType.DATE,
    "datetime": FieldType.DATETIME,
    "time": FieldType.STRING,
    "base64": FieldType.STRING, "anyType": FieldType.STRING,
}

# HubSpot property type → FRIS FieldType
HUBSPOT_TYPE_MAP = {
    "number": FieldType.NUMBER,
    "bool": FieldType.BOOLEAN,
    "date": FieldType.DATE,
    "datetime": FieldType.DATETIME,
    "string": FieldType.STRING,
    "enumeration": FieldType.STRING,
    "phone_number": FieldType.STRING,
    "json": FieldType.JSON,
}


class SalesforceConnector(BaseConnector):
    """
    Salesforce CRM connector (REST + SOQL).
    Config keys:
        object: str             - SObject to ingest, e.g. "Account", "Contact", "Opportunity"
        api_version: str        - e.g. "v59.0" (default)
        # --- Auth option A: direct token ---
        access_token: str       - a valid OAuth access token
        instance_url: str       - e.g. "https://yourorg.my.salesforce.com"
        # --- Auth option B: OAuth username-password flow ---
        login_url: str          - "https://login.salesforce.com" (or test. for sandboxes)
        client_id: str          - connected-app consumer key
        client_secret: str      - connected-app consumer secret
        username: str
        password: str
        security_token: str     - appended to password per Salesforce OAuth password flow
        # --- Optional ---
        fields: list            - explicit field list; if omitted, all queryable fields are used
        where_clause: str       - extra SOQL WHERE filter
        incremental_column: str - defaults to "SystemModstamp"
    """

    CONNECTOR_TYPE = ConnectorType.SALESFORCE
    DISPLAY_NAME = "Salesforce"
    DESCRIPTION = "Connect to Salesforce CRM objects (Account, Contact, Opportunity, Lead)"
    ICON = "☁️"
    DOCS = (
        "1. Choose the object to ingest (Account, Contact, Opportunity, Lead, …).\n"
        "2. Authenticate one of two ways: (A) paste a valid access_token + instance_url, or "
        "(B) provide login_url, client_id, client_secret, username, password, and security_token "
        "for the OAuth username-password flow.\n"
        "3. Field schema is read natively from the object's describe() metadata, so types are "
        "exact. By default all queryable fields are pulled; set fields to limit them.\n"
        "4. Incremental sync uses SystemModstamp by default — only records changed since the "
        "last sync are pulled."
    )

    CONFIG_SCHEMA = {
        "required": ["object"],
        "optional": [
            "api_version", "access_token", "instance_url", "login_url",
            "client_id", "client_secret", "username", "password", "security_token",
            "fields", "where_clause", "incremental_column"
        ],
        "defaults": {
            "api_version": "v59.0",
            "login_url": "https://login.salesforce.com",
            "incremental_column": "SystemModstamp",
        }
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = self.cfg.get("access_token")
        self._instance_url: Optional[str] = self.cfg.get("instance_url")

    def _api_version(self) -> str:
        return self.cfg.get("api_version", "v59.0")

    async def _ensure_client(self):
        if not self._client:
            self._client = httpx.AsyncClient(timeout=self.cfg.get("timeout_seconds", 60), follow_redirects=True)

    async def _authenticate(self):
        """Obtain access_token + instance_url. No-op if a direct token was supplied."""
        if self._access_token and self._instance_url:
            return
        await self._ensure_client()
        login_url = self.cfg.get("login_url", "https://login.salesforce.com").rstrip("/")
        password = f"{self.cfg.get('password', '')}{self.cfg.get('security_token', '')}"
        resp = await self._client.post(
            f"{login_url}/services/oauth2/token",
            data={
                "grant_type": "password",
                "client_id": self.cfg["client_id"],
                "client_secret": self.cfg["client_secret"],
                "username": self.cfg["username"],
                "password": password,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._access_token = payload["access_token"]
        self._instance_url = payload["instance_url"]

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    def _base(self) -> str:
        return f"{self._instance_url.rstrip('/')}/services/data/{self._api_version()}"

    async def connect(self) -> bool:
        await self._ensure_client()
        await self._authenticate()
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            start = time.time()
            await self.connect()
            resp = await self._client.get(f"{self._base()}/limits", headers=self._auth_headers())
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code < 400:
                return {"success": True, "message": "Connected to Salesforce", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def _describe_fields(self) -> List[Dict[str, Any]]:
        await self.connect()
        obj = self.cfg["object"]
        resp = await self._client.get(f"{self._base()}/sobjects/{obj}/describe", headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json().get("fields", [])

    def _queryable_field_names(self, described: List[Dict[str, Any]]) -> List[str]:
        if self.cfg.get("fields"):
            return list(self.cfg["fields"])
        # All fields Salesforce will return in a SOQL SELECT (exclude compound/non-queryable)
        return [f["name"] for f in described if f.get("type") not in ("address", "location")]

    async def detect_schema(self) -> SchemaDetectionResult:
        described = await self._describe_fields()
        obj_lower = self.cfg["object"].lower()

        fields = []
        for f in described:
            name = f["name"]
            fris_type = SALESFORCE_TYPE_MAP.get(f.get("type", "string"), FieldType.STRING)
            fields.append(DetectedField(
                raw_name=name,
                suggested_name=name.lower(),
                field_type=fris_type,
                sample_values=[],
                null_count=0,
                is_identifier=(f.get("type") == "id" or name == "Id"),
                is_entity_name=f.get("nameField", False) or name.lower() in ("name", "lastname"),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0,
            ))

        entity_type = SALESFORCE_ENTITY_MAP.get(obj_lower, "Entity")
        sample = await self.fetch_sample(limit=5)
        # attach sample values
        by_name = {fld.raw_name: fld for fld in fields}
        for row in sample:
            for k, v in row.items():
                if k in by_name and v is not None and len(by_name[k].sample_values) < 5:
                    by_name[k].sample_values.append(str(v))

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

    def _strip_attributes(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in record.items() if k != "attributes"}

    async def _soql(self, query: str) -> List[Dict[str, Any]]:
        await self.connect()
        records: List[Dict[str, Any]] = []
        url = f"{self._base()}/query"
        params = {"q": query}
        while True:
            resp = await self._client.get(url, headers=self._auth_headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
            records.extend(self._strip_attributes(r) for r in data.get("records", []))
            next_url = data.get("nextRecordsUrl")
            if not next_url or data.get("done", True):
                break
            # nextRecordsUrl is an absolute API path; query it directly with no params
            url = f"{self._instance_url.rstrip('/')}{next_url}"
            params = None
        return records

    def _build_select(self, field_names: List[str]) -> str:
        obj = self.cfg["object"]
        select = ", ".join(field_names) if field_names else "FIELDS(STANDARD)"
        return f"SELECT {select} FROM {obj}"

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        described = await self._describe_fields()
        field_names = self._queryable_field_names(described)
        query = f"{self._build_select(field_names)} LIMIT {int(limit)}"
        return await self._soql(query)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        described = await self._describe_fields()
        field_names = self._queryable_field_names(described)
        where_parts = []
        if self.cfg.get("where_clause"):
            where_parts.append(self.cfg["where_clause"])
        if since:
            col = self.cfg.get("incremental_column", "SystemModstamp")
            # SOQL datetime literals are unquoted ISO-8601 with timezone
            iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            where_parts.append(f"{col} > {iso}")
        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f"{self._build_select(field_names)}{where_sql}"
        return await self._soql(query)

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()


class HubSpotConnector(BaseConnector):
    """
    HubSpot CRM connector (CRM v3 API, private-app token).
    Config keys:
        access_token: str       - private-app access token (required)
        object: str             - "companies" | "contacts" | "deals" | "tickets"
        properties: list        - explicit property list; if omitted, all are pulled
        incremental_property: str - defaults to "hs_lastmodifieddate"
        base_url: str           - defaults to "https://api.hubapi.com"
    """

    CONNECTOR_TYPE = ConnectorType.HUBSPOT
    DISPLAY_NAME = "HubSpot"
    DESCRIPTION = "Connect to HubSpot CRM objects (companies, contacts, deals)"
    ICON = "🟠"
    DOCS = (
        "1. Create a HubSpot private app and copy its access token into access_token.\n"
        "2. Choose the object to ingest: companies, contacts, or deals.\n"
        "3. Property schema is read natively from HubSpot's properties API, so field types are "
        "exact. By default all properties are pulled; set properties to limit them.\n"
        "4. Incremental sync uses hs_lastmodifieddate — only records changed since the last "
        "sync are pulled (via the CRM search API)."
    )

    CONFIG_SCHEMA = {
        "required": ["access_token", "object"],
        "optional": ["properties", "incremental_property", "base_url"],
        "defaults": {
            "incremental_property": "hs_lastmodifieddate",
            "base_url": "https://api.hubapi.com",
        }
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _base_url(self) -> str:
        return self.cfg.get("base_url", "https://api.hubapi.com").rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg['access_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _ensure_client(self):
        if not self._client:
            self._client = httpx.AsyncClient(timeout=self.cfg.get("timeout_seconds", 60), follow_redirects=True)

    async def connect(self) -> bool:
        await self._ensure_client()
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            await self._ensure_client()
            start = time.time()
            obj = self.cfg["object"]
            resp = await self._client.get(
                f"{self._base_url()}/crm/v3/objects/{obj}",
                headers=self._headers(), params={"limit": 1},
            )
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code < 400:
                return {"success": True, "message": "Connected to HubSpot", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def _get_properties(self) -> List[Dict[str, Any]]:
        await self._ensure_client()
        obj = self.cfg["object"]
        resp = await self._client.get(
            f"{self._base_url()}/crm/v3/properties/{obj}", headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _property_names(self, props: List[Dict[str, Any]]) -> List[str]:
        if self.cfg.get("properties"):
            return list(self.cfg["properties"])
        return [p["name"] for p in props]

    def _flatten(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """HubSpot returns {id, properties:{...}, createdAt, updatedAt}. Flatten to one dict."""
        flat = {"id": record.get("id")}
        flat.update(record.get("properties", {}) or {})
        if record.get("createdAt"):
            flat.setdefault("createdAt", record["createdAt"])
        if record.get("updatedAt"):
            flat.setdefault("updatedAt", record["updatedAt"])
        return flat

    async def detect_schema(self) -> SchemaDetectionResult:
        props = await self._get_properties()
        obj_lower = self.cfg["object"].lower()

        fields = [DetectedField(
            raw_name="id",
            suggested_name="id",
            field_type=FieldType.STRING,
            sample_values=[],
            null_count=0,
            is_identifier=True,
            is_entity_name=False,
            is_timestamp=False,
            confidence=1.0,
        )]
        for p in props:
            name = p["name"]
            fris_type = HUBSPOT_TYPE_MAP.get(p.get("type", "string"), FieldType.STRING)
            fields.append(DetectedField(
                raw_name=name,
                suggested_name=name.lower(),
                field_type=fris_type,
                sample_values=[],
                null_count=0,
                is_identifier=SchemaDetector._is_identifier(name),
                is_entity_name=SchemaDetector._is_name_field(name),
                is_timestamp=fris_type in (FieldType.DATE, FieldType.DATETIME),
                confidence=1.0,
            ))

        entity_type = HUBSPOT_ENTITY_MAP.get(obj_lower, "Entity")
        sample = await self.fetch_sample(limit=5)
        by_name = {fld.raw_name: fld for fld in fields}
        for row in sample:
            for k, v in row.items():
                if k in by_name and v is not None and len(by_name[k].sample_values) < 5:
                    by_name[k].sample_values.append(str(v))

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
        await self._ensure_client()
        props = await self._get_properties()
        obj = self.cfg["object"]
        resp = await self._client.get(
            f"{self._base_url()}/crm/v3/objects/{obj}",
            headers=self._headers(),
            params={"limit": min(int(limit), 100), "properties": ",".join(self._property_names(props))},
        )
        resp.raise_for_status()
        return [self._flatten(r) for r in resp.json().get("results", [])]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        await self._ensure_client()
        props = await self._get_properties()
        prop_names = self._property_names(props)
        obj = self.cfg["object"]
        records: List[Dict[str, Any]] = []

        if since:
            # Incremental: CRM search API with a lastmodified filter (epoch millis)
            incr = self.cfg.get("incremental_property", "hs_lastmodifieddate")
            since_ms = int(since.timestamp() * 1000)
            after: Optional[str] = None
            while True:
                body: Dict[str, Any] = {
                    "filterGroups": [{"filters": [
                        {"propertyName": incr, "operator": "GT", "value": str(since_ms)}
                    ]}],
                    "properties": prop_names,
                    "limit": 100,
                }
                if after:
                    body["after"] = after
                resp = await self._client.post(
                    f"{self._base_url()}/crm/v3/objects/{obj}/search",
                    headers=self._headers(), json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                records.extend(self._flatten(r) for r in data.get("results", []))
                after = data.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break
            return records

        # Full sync: paginate the list endpoint
        after = None
        while True:
            params = {"limit": 100, "properties": ",".join(prop_names)}
            if after:
                params["after"] = after
            resp = await self._client.get(
                f"{self._base_url()}/crm/v3/objects/{obj}",
                headers=self._headers(), params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            records.extend(self._flatten(r) for r in data.get("results", []))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return records

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
