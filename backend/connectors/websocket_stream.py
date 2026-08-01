"""
FRIS WebSocket Stream Connector
Connects to a WebSocket endpoint and drains incoming JSON messages within
a time budget. Same streaming semantics as the Kafka connector — there is
no fixed "all records", so fetch_all() collects whatever arrives live.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import websockets

from .base import BaseConnector, ConnectorConfig, ConnectorType


class WebSocketConnector(BaseConnector):
    """
    Config keys:
        ws_url: str               - required, e.g. "wss://stream.example.com/feed"
        subscribe_message: dict   - optional, JSON message sent right after connecting
                                     (common pattern for subscribing to a channel)
        headers: dict             - optional, e.g. for auth tokens
        drain_timeout_seconds: int - optional, how long fetch_all listens, default 10
    """

    CONNECTOR_TYPE = ConnectorType.WEBSOCKET
    DISPLAY_NAME = "WebSocket Stream"
    DESCRIPTION = "Real-time data over WebSocket connections"
    ICON = "🔄"
    DOCS = (
        "1. Provide the ws_url (use wss:// for secure connections, ws:// for plain).\n"
        "2. Many WebSocket APIs require a subscribe message sent immediately after connecting "
        "to start receiving data — if yours does, provide it as subscribe_message (JSON object, "
        "FRIS will send it as the first frame after connecting).\n"
        "3. If the endpoint requires auth headers (e.g. an API key), add them under headers.\n"
        "4. Since this is a live stream, fetch_all() listens for drain_timeout_seconds (default "
        "10s) and collects whatever messages arrive in that window — it does not represent "
        "a fixed historical dataset like a database table would."
    )

    CONFIG_SCHEMA = {
        "required": ["ws_url"],
        "optional": ["subscribe_message", "headers", "drain_timeout_seconds"],
        "defaults": {"drain_timeout_seconds": 10}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config

    async def connect(self) -> bool:
        # Connections are opened per-operation since this is a short-lived
        # polling-style fetch pattern, matching the Kafka connector's approach.
        return True

    async def test_connection(self) -> Dict[str, Any]:
        try:
            start = time.time()
            extra_headers = self.cfg.get("headers") or {}
            async with websockets.connect(self.cfg["ws_url"], additional_headers=extra_headers) as ws:
                if self.cfg.get("subscribe_message"):
                    await ws.send(json.dumps(self.cfg["subscribe_message"]))
                # Wait briefly to confirm the connection is actually live and responsive
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    pass  # connection succeeded even if no message arrived yet — that's fine
            latency_ms = int((time.time() - start) * 1000)
            return {"success": True, "message": "Connected successfully", "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self._drain(limit=limit, timeout_seconds=5)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        timeout = self.cfg.get("drain_timeout_seconds", 10)
        return await self._drain(limit=10000, timeout_seconds=timeout)

    async def _drain(self, limit: int, timeout_seconds: int) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        extra_headers = self.cfg.get("headers") or {}
        async with websockets.connect(self.cfg["ws_url"], additional_headers=extra_headers) as ws:
            if self.cfg.get("subscribe_message"):
                await ws.send(json.dumps(self.cfg["subscribe_message"]))

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and len(records) < limit:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
                elif isinstance(payload, list):
                    records.extend(p for p in payload if isinstance(p, dict))
        return records

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        pass
