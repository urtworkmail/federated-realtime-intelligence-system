"""
FRIS REST API Connector
Connects to any REST API endpoint.
Supports: Bearer token, API key (header or query), Basic auth, No auth.
Auto-paginates through common pagination patterns.
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseConnector, ConnectorConfig, ConnectorType


class RestApiConnector(BaseConnector):
    """
    Universal REST API connector.
    Config keys:
        base_url: str           - required, e.g. "https://api.example.com"
        endpoint: str           - required, e.g. "/v1/companies"
        auth_type: str          - "none" | "bearer" | "api_key_header" | "api_key_query" | "basic"
        auth_token: str         - for bearer
        api_key: str            - for api_key variants
        api_key_header_name: str - for api_key_header, default "X-API-Key"
        api_key_query_param: str - for api_key_query, default "api_key"
        username: str           - for basic auth
        password: str           - for basic auth
        headers: dict           - additional headers
        params: dict            - additional query params
        data_path: str          - JSON path to records array, e.g. "data.items"
        pagination_type: str    - "none" | "page" | "cursor" | "offset"
        page_param: str         - pagination page param name, default "page"
        per_page_param: str     - default "per_page"
        per_page: int           - default 100
        cursor_param: str       - for cursor pagination
        cursor_path: str        - where to find next cursor in response
        timeout_seconds: int    - default 30
    """

    CONNECTOR_TYPE = ConnectorType.REST_API
    DISPLAY_NAME = "REST API"
    DESCRIPTION = "Connect to any REST API endpoint"
    ICON = "🔌"
    DOCS = (
        "1. Provide base_url and endpoint — these are joined together to form the request URL.\n"
        "2. Choose auth_type: none, bearer (token), api_key_header, api_key_query, or basic "
        "(username/password), and fill in the matching field.\n"
        "3. If your data isn't at the top level of the response, set data_path to the JSON key "
        "(dot notation, e.g. 'data.items') pointing to the records array.\n"
        "4. If the API paginates, set pagination_type to page, cursor, or offset and configure "
        "the matching param names — FRIS will auto-page through all results on sync."
    )

    CONFIG_SCHEMA = {
        "required": ["base_url", "endpoint"],
        "optional": [
            "auth_type", "auth_token", "api_key", "api_key_header_name",
            "api_key_query_param", "username", "password", "headers",
            "params", "data_path", "pagination_type", "page_param",
            "per_page_param", "per_page", "cursor_param", "cursor_path",
            "timeout_seconds"
        ],
        "defaults": {
            "auth_type": "none",
            "pagination_type": "none",
            "per_page": 100,
            "page_param": "page",
            "per_page_param": "per_page",
            "api_key_header_name": "X-API-Key",
            "api_key_query_param": "api_key",
            "timeout_seconds": 30
        }
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._client: Optional[httpx.AsyncClient] = None

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        # Add custom headers
        if self.cfg.get("headers"):
            headers.update(self.cfg["headers"])

        auth_type = self.cfg.get("auth_type", "none")

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.cfg.get('auth_token', '')}"
        elif auth_type == "api_key_header":
            header_name = self.cfg.get("api_key_header_name", "X-API-Key")
            headers[header_name] = self.cfg.get("api_key", "")
        elif auth_type == "basic":
            import base64
            credentials = f"{self.cfg.get('username', '')}:{self.cfg.get('password', '')}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    def _build_params(self, extra: Optional[Dict] = None) -> Dict[str, Any]:
        params = {}

        if self.cfg.get("params"):
            params.update(self.cfg["params"])

        auth_type = self.cfg.get("auth_type", "none")
        if auth_type == "api_key_query":
            param_name = self.cfg.get("api_key_query_param", "api_key")
            params[param_name] = self.cfg.get("api_key", "")

        if extra:
            params.update(extra)

        return params

    def _extract_data(self, response_json: Any) -> List[Dict[str, Any]]:
        """Extract records array from response using configured data_path"""
        data_path = self.cfg.get("data_path", "")

        if not data_path:
            # Try common patterns automatically
            if isinstance(response_json, list):
                return response_json
            if isinstance(response_json, dict):
                for key in ["data", "results", "items", "records", "rows", "content"]:
                    if key in response_json and isinstance(response_json[key], list):
                        return response_json[key]
                # Single record wrapped in object
                return [response_json]
            return []

        # Navigate the path
        current = response_json
        for key in data_path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return []

        return current if isinstance(current, list) else [current]

    def _get_next_cursor(self, response_json: Any) -> Optional[str]:
        cursor_path = self.cfg.get("cursor_path", "")
        if not cursor_path:
            # Common patterns
            for path in ["meta.next_cursor", "pagination.cursor", "next_cursor", "cursor"]:
                current = response_json
                found = True
                for key in path.split("."):
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        found = False
                        break
                if found and current:
                    return str(current)
            return None

        current = response_json
        for key in cursor_path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return str(current) if current else None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            timeout=self.cfg.get("timeout_seconds", 30),
            follow_redirects=True
        )
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            if not self._client:
                await self.connect()

            start = time.time()
            url = self.cfg["base_url"].rstrip("/") + "/" + self.cfg["endpoint"].lstrip("/")
            response = await self._client.get(
                url,
                headers=self._build_headers(),
                params=self._build_params({"limit": 1, "per_page": 1, "page": 1})
            )
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code < 400:
                return {
                    "success": True,
                    "message": f"Connected successfully (HTTP {response.status_code})",
                    "latency_ms": latency_ms,
                    "status_code": response.status_code
                }
            else:
                return {
                    "success": False,
                    "message": f"HTTP {response.status_code}: {response.text[:200]}",
                    "latency_ms": latency_ms,
                    "status_code": response.status_code
                }
        except httpx.ConnectError as e:
            return {"success": False, "message": f"Cannot connect: {str(e)}", "latency_ms": -1}
        except httpx.TimeoutException:
            return {"success": False, "message": "Connection timed out", "latency_ms": -1}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        url = self.cfg["base_url"].rstrip("/") + "/" + self.cfg["endpoint"].lstrip("/")
        params = self._build_params({
            self.cfg.get("per_page_param", "per_page"): limit,
            self.cfg.get("page_param", "page"): 1
        })

        response = await self._client.get(url, headers=self._build_headers(), params=params)
        response.raise_for_status()
        return self._extract_data(response.json())[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._client:
            await self.connect()

        pagination_type = self.cfg.get("pagination_type", "none")
        url = self.cfg["base_url"].rstrip("/") + "/" + self.cfg["endpoint"].lstrip("/")
        all_records = []

        if pagination_type == "none":
            params = self._build_params()
            if since:
                params["updated_since"] = since.isoformat()
            response = await self._client.get(url, headers=self._build_headers(), params=params)
            response.raise_for_status()
            return self._extract_data(response.json())

        elif pagination_type == "page":
            page = 1
            per_page = self.cfg.get("per_page", 100)
            while True:
                params = self._build_params({
                    self.cfg.get("page_param", "page"): page,
                    self.cfg.get("per_page_param", "per_page"): per_page
                })
                response = await self._client.get(url, headers=self._build_headers(), params=params)
                response.raise_for_status()
                records = self._extract_data(response.json())
                if not records:
                    break
                all_records.extend(records)
                if len(records) < per_page:
                    break
                page += 1
                await asyncio.sleep(0.1)  # be polite

        elif pagination_type == "cursor":
            cursor = None
            per_page = self.cfg.get("per_page", 100)
            while True:
                params = self._build_params({
                    self.cfg.get("per_page_param", "per_page"): per_page
                })
                if cursor:
                    params[self.cfg.get("cursor_param", "cursor")] = cursor
                response = await self._client.get(url, headers=self._build_headers(), params=params)
                response.raise_for_status()
                data = response.json()
                records = self._extract_data(data)
                if not records:
                    break
                all_records.extend(records)
                cursor = self._get_next_cursor(data)
                if not cursor:
                    break
                await asyncio.sleep(0.1)

        elif pagination_type == "offset":
            offset = 0
            per_page = self.cfg.get("per_page", 100)
            while True:
                params = self._build_params({"offset": offset, "limit": per_page})
                response = await self._client.get(url, headers=self._build_headers(), params=params)
                response.raise_for_status()
                records = self._extract_data(response.json())
                if not records:
                    break
                all_records.extend(records)
                if len(records) < per_page:
                    break
                offset += per_page
                await asyncio.sleep(0.1)

        return all_records

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
