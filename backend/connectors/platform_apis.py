"""
FRIS Platform Connectors — Facebook, Slack, Google Sheets, Airtable, Elasticsearch.
Grouped in one file since each is a thin, REST-shaped wrapper over httpx —
no heavyweight SDKs needed, each platform's REST/Graph API is called directly.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseConnector, ConnectorConfig, ConnectorType


# ============================================================
# Facebook — Graph API
# ============================================================

class FacebookConnector(BaseConnector):
    """
    Config keys:
        access_token: str   - required, a Page or User access token with the needed permissions
        object_id: str      - required, the Page ID, Group ID, or "me"
        edge: str           - required, e.g. "posts", "feed", "events"
        fields: str         - optional, comma-separated Graph API fields to request
        api_version: str    - optional, default "v21.0"
    """

    CONNECTOR_TYPE = ConnectorType.FACEBOOK
    DISPLAY_NAME = "Facebook"
    DESCRIPTION = "Pull posts, pages, and groups data from Facebook"
    ICON = "📘"
    DOCS = (
        "1. Create a Facebook App at developers.facebook.com and generate a Page or User "
        "access token with the permissions needed for the data you want (e.g. pages_read_engagement).\n"
        "2. Set object_id to the Page/Group ID you're pulling from, or 'me' for the token owner.\n"
        "3. Set edge to the Graph API edge you want (e.g. 'posts', 'feed', 'events', 'photos').\n"
        "4. Optionally restrict fields returned via the fields parameter (comma-separated, e.g. "
        "'id,message,created_time,permalink_url') to reduce payload size.\n"
        "5. Note: long-lived tokens expire (typically 60 days) — you'll need to refresh and "
        "update the connector config periodically unless using a System User token."
    )

    CONFIG_SCHEMA = {
        "required": ["access_token", "object_id", "edge"],
        "optional": ["fields", "api_version"],
        "defaults": {"api_version": "v21.0"}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _url(self) -> str:
        v = self.cfg.get("api_version", "v21.0")
        return f"https://graph.facebook.com/{v}/{self.cfg['object_id']}/{self.cfg['edge']}"

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=30)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            params = {"access_token": self.cfg["access_token"], "limit": 1}
            response = await self._client.get(self._url(), params=params)
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code < 400:
                return {"success": True, "message": "Connected successfully", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: {response.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        params = {"access_token": self.cfg["access_token"], "limit": limit}
        if self.cfg.get("fields"):
            params["fields"] = self.cfg["fields"]
        response = await self._client.get(self._url(), params=params)
        response.raise_for_status()
        return response.json().get("data", [])[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        all_records: List[Dict[str, Any]] = []
        params = {"access_token": self.cfg["access_token"], "limit": 100}
        if self.cfg.get("fields"):
            params["fields"] = self.cfg["fields"]
        if since:
            params["since"] = int(since.timestamp())
        url = self._url()
        while url:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            all_records.extend(data.get("data", []))
            next_url = data.get("paging", {}).get("next")
            url = next_url
            params = {}  # next_url already has all params embedded
        return all_records

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()


# ============================================================
# Slack — Web API
# ============================================================

class SlackConnector(BaseConnector):
    """
    Config keys:
        bot_token: str   - required, a Slack bot token (xoxb-...) with channels:history, channels:read
        channel_id: str  - required, the channel to pull messages from
        oldest: str      - optional, Slack timestamp to start from
    """

    CONNECTOR_TYPE = ConnectorType.SLACK
    DISPLAY_NAME = "Slack"
    DESCRIPTION = "Pull messages and channel data from Slack workspaces"
    ICON = "💬"
    DOCS = (
        "1. Create a Slack App at api.slack.com/apps, add the OAuth scopes "
        "channels:history and channels:read, then install it to your workspace.\n"
        "2. Use the Bot User OAuth Token (starts with xoxb-) as bot_token.\n"
        "3. Invite the bot to the channel you want to pull from, then set channel_id to that "
        "channel's ID (found in the channel details, or via the conversations.list API).\n"
        "4. Optionally set oldest to a Slack timestamp to limit how far back to pull history."
    )

    CONFIG_SCHEMA = {
        "required": ["bot_token", "channel_id"],
        "optional": ["oldest"],
        "defaults": {}
    }

    BASE_URL = "https://slack.com/api"

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=30, headers={"Authorization": f"Bearer {self.cfg['bot_token']}"}
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(f"{self.BASE_URL}/auth.test")
            latency_ms = int((time.time() - start) * 1000)
            data = response.json()
            if data.get("ok"):
                return {"success": True, "message": f"Connected as {data.get('user', 'bot')}", "latency_ms": latency_ms}
            return {"success": False, "message": f"Slack error: {data.get('error', 'unknown')}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def _fetch_history(self, limit: int, oldest: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        all_messages: List[Dict[str, Any]] = []
        cursor = None
        while True:
            params = {"channel": self.cfg["channel_id"], "limit": min(limit - len(all_messages), 200)}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor
            response = await self._client.get(f"{self.BASE_URL}/conversations.history", params=params)
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            all_messages.extend(data.get("messages", []))
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor or len(all_messages) >= limit:
                break
        return all_messages[:limit]

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self._fetch_history(limit=limit)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        oldest = str(since.timestamp()) if since else self.cfg.get("oldest")
        return await self._fetch_history(limit=10000, oldest=oldest)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()


# ============================================================
# Google Sheets — Sheets API v4
# ============================================================

class GoogleSheetsConnector(BaseConnector):
    """
    Config keys:
        spreadsheet_id: str  - required, found in the sheet's URL
        sheet_range: str     - required, e.g. "Sheet1!A1:Z1000"
        api_key: str         - one of api_key or access_token required
        access_token: str    - OAuth2 access token, for private sheets
        has_header_row: bool - optional, default true — first row becomes field names
    """

    CONNECTOR_TYPE = ConnectorType.GOOGLE_SHEETS
    DISPLAY_NAME = "Google Sheets"
    DESCRIPTION = "Connect directly to a Google Sheet"
    ICON = "📗"
    DOCS = (
        "1. Find your spreadsheet_id in the sheet's URL: "
        "docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit.\n"
        "2. Set sheet_range to the tab and range you want (e.g. 'Sheet1!A1:Z1000').\n"
        "3. For a publicly-readable sheet, generate an API key in Google Cloud Console "
        "(enable the Google Sheets API first) and use api_key.\n"
        "4. For a private sheet, use an OAuth2 access_token from a service account or user "
        "with viewer access instead of api_key.\n"
        "5. has_header_row (default true) treats the first row as field names — set to false "
        "if your sheet has no header row, and fields will be named col_1, col_2, etc."
    )

    CONFIG_SCHEMA = {
        "required": ["spreadsheet_id", "sheet_range"],
        "optional": ["api_key", "access_token", "has_header_row"],
        "defaults": {"has_header_row": True}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _url(self) -> str:
        return (
            f"https://sheets.googleapis.com/v4/spreadsheets/"
            f"{self.cfg['spreadsheet_id']}/values/{self.cfg['sheet_range']}"
        )

    def _params(self) -> Dict[str, Any]:
        if self.cfg.get("api_key"):
            return {"key": self.cfg["api_key"]}
        return {}

    def _headers(self) -> Dict[str, str]:
        if self.cfg.get("access_token"):
            return {"Authorization": f"Bearer {self.cfg['access_token']}"}
        return {}

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=30)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(self._url(), params=self._params(), headers=self._headers())
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code < 400:
                rows = len(response.json().get("values", []))
                return {"success": True, "message": f"Connected. Found {rows} row(s) in range.", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: {response.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    def _rows_to_records(self, values: List[List[Any]]) -> List[Dict[str, Any]]:
        if not values:
            return []
        if self.cfg.get("has_header_row", True):
            headers = values[0]
            data_rows = values[1:]
        else:
            headers = [f"col_{i+1}" for i in range(len(values[0]))]
            data_rows = values
        records = []
        for row in data_rows:
            record = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
            records.append(record)
        return records

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        response = await self._client.get(self._url(), params=self._params(), headers=self._headers())
        response.raise_for_status()
        records = self._rows_to_records(response.json().get("values", []))
        return records[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        response = await self._client.get(self._url(), params=self._params(), headers=self._headers())
        response.raise_for_status()
        return self._rows_to_records(response.json().get("values", []))

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()


# ============================================================
# Airtable — REST API
# ============================================================

class AirtableConnector(BaseConnector):
    """
    Config keys:
        api_key: str    - required, a Personal Access Token
        base_id: str    - required, found in Airtable API docs for your base
        table_name: str - required
        view: str       - optional, a specific view name to filter/sort by
    """

    CONNECTOR_TYPE = ConnectorType.AIRTABLE
    DISPLAY_NAME = "Airtable"
    DESCRIPTION = "Sync data from Airtable bases"
    ICON = "🗂️"
    DOCS = (
        "1. Create a Personal Access Token at airtable.com/create/tokens with read access "
        "scoped to the base you want to connect.\n"
        "2. Find your base_id (starts with 'app...') from the base's API documentation page "
        "(Help > API documentation inside Airtable).\n"
        "3. Set table_name to the exact table name as it appears in Airtable.\n"
        "4. Optionally set view to pull records in a specific view's filter/sort order."
    )

    CONFIG_SCHEMA = {
        "required": ["api_key", "base_id", "table_name"],
        "optional": ["view"],
        "defaults": {}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _url(self) -> str:
        from urllib.parse import quote
        return f"https://api.airtable.com/v0/{self.cfg['base_id']}/{quote(self.cfg['table_name'])}"

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=30, headers={"Authorization": f"Bearer {self.cfg['api_key']}"}
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            params = {"maxRecords": 1}
            if self.cfg.get("view"):
                params["view"] = self.cfg["view"]
            response = await self._client.get(self._url(), params=params)
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code < 400:
                return {"success": True, "message": "Connected successfully", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: {response.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        params = {"maxRecords": limit}
        if self.cfg.get("view"):
            params["view"] = self.cfg["view"]
        response = await self._client.get(self._url(), params=params)
        response.raise_for_status()
        records = response.json().get("records", [])
        return [self._flatten(r) for r in records][:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        all_records: List[Dict[str, Any]] = []
        offset = None
        params = {"pageSize": 100}
        if self.cfg.get("view"):
            params["view"] = self.cfg["view"]
        while True:
            call_params = dict(params)
            if offset:
                call_params["offset"] = offset
            response = await self._client.get(self._url(), params=call_params)
            response.raise_for_status()
            data = response.json()
            all_records.extend(self._flatten(r) for r in data.get("records", []))
            offset = data.get("offset")
            if not offset:
                break
        return all_records

    @staticmethod
    def _flatten(record: Dict[str, Any]) -> Dict[str, Any]:
        flat = {"id": record.get("id"), "created_time": record.get("createdTime")}
        flat.update(record.get("fields", {}))
        return flat

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()


# ============================================================
# Elasticsearch — REST API
# ============================================================

class ElasticsearchConnector(BaseConnector):
    """
    Config keys:
        base_url: str    - required, e.g. "https://my-es-cluster:9200"
        index: str       - required
        username: str    - optional, for basic auth
        password: str    - optional, for basic auth
        api_key: str     - optional, alternative to username/password
        query: dict      - optional, an Elasticsearch query DSL body, defaults to match_all
        updated_field: str - optional, field name for incremental sync (range query)
        verify_ssl: bool - optional, default true
    """

    CONNECTOR_TYPE = ConnectorType.ELASTICSEARCH
    DISPLAY_NAME = "Elasticsearch"
    DESCRIPTION = "Query and ingest from Elasticsearch indices"
    ICON = "🔍"
    DOCS = (
        "1. Enter your cluster's base_url (e.g. https://your-cluster:9200).\n"
        "2. Set index to the specific index name (or alias) you want to ingest from.\n"
        "3. Authenticate with either username/password (basic auth) or an api_key — check "
        "with your cluster admin which is enabled.\n"
        "4. Optionally provide a custom query (standard Elasticsearch Query DSL JSON) to filter "
        "documents — defaults to match_all if omitted.\n"
        "5. Set updated_field to a timestamp field name to enable incremental sync via range "
        "queries on subsequent syncs."
    )

    CONFIG_SCHEMA = {
        "required": ["base_url", "index"],
        "optional": ["username", "password", "api_key", "query", "updated_field", "verify_ssl"],
        "defaults": {"verify_ssl": True}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self) -> Dict[str, str]:
        if self.cfg.get("api_key"):
            return {"Authorization": f"ApiKey {self.cfg['api_key']}"}
        return {}

    def _auth(self):
        if self.cfg.get("username") and not self.cfg.get("api_key"):
            return (self.cfg["username"], self.cfg.get("password", ""))
        return None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=30, headers=self._headers(), auth=self._auth(),
            verify=self.cfg.get("verify_ssl", True)
        )
        return True

    def _search_url(self) -> str:
        return f"{self.cfg['base_url'].rstrip('/')}/{self.cfg['index']}/_search"

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()
            start = time.time()
            response = await self._client.get(f"{self.cfg['base_url'].rstrip('/')}/{self.cfg['index']}/_count")
            latency_ms = int((time.time() - start) * 1000)
            if response.status_code < 400:
                count = response.json().get("count", 0)
                return {"success": True, "message": f"Connected. Index has {count} documents.", "latency_ms": latency_ms}
            return {"success": False, "message": f"HTTP {response.status_code}: {response.text[:200]}", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    def _build_body(self, size: int, since: Optional[datetime] = None) -> Dict[str, Any]:
        query = self.cfg.get("query") or {"match_all": {}}
        if since and self.cfg.get("updated_field"):
            query = {
                "bool": {
                    "must": [query],
                    "filter": [{"range": {self.cfg["updated_field"]: {"gte": since.isoformat()}}}]
                }
            }
        return {"query": query, "size": size}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        response = await self._client.post(self._search_url(), json=self._build_body(limit))
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        return [self._flatten(h) for h in hits]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()
        all_records: List[Dict[str, Any]] = []
        body = self._build_body(1000, since)
        body["sort"] = ["_doc"]
        response = await self._client.post(f"{self._search_url()}?scroll=2m", json=body)
        response.raise_for_status()
        data = response.json()
        scroll_id = data.get("_scroll_id")
        hits = data.get("hits", {}).get("hits", [])
        all_records.extend(self._flatten(h) for h in hits)

        while hits:
            response = await self._client.post(
                f"{self.cfg['base_url'].rstrip('/')}/_search/scroll",
                json={"scroll": "2m", "scroll_id": scroll_id}
            )
            response.raise_for_status()
            data = response.json()
            scroll_id = data.get("_scroll_id")
            hits = data.get("hits", {}).get("hits", [])
            all_records.extend(self._flatten(h) for h in hits)

        return all_records

    @staticmethod
    def _flatten(hit: Dict[str, Any]) -> Dict[str, Any]:
        record = {"_id": hit.get("_id"), "_index": hit.get("_index")}
        record.update(hit.get("_source", {}))
        return record

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
