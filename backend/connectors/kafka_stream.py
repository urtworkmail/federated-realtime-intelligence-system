"""
FRIS Kafka Stream Connector
Consumes from a Kafka topic using aiokafka. Unlike the other connectors,
this is a streaming source — fetch_all() drains whatever is currently
available up to a time budget rather than pulling a fixed historical set,
since Kafka topics are continuous streams, not static datasets.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaConsumer

from .base import BaseConnector, ConnectorConfig, ConnectorType


class KafkaStreamConnector(BaseConnector):
    """
    Config keys:
        bootstrap_servers: str  - required, e.g. "broker1:9092,broker2:9092"
        topic: str              - required
        group_id: str           - required, consumer group id (FRIS uses this to track offset)
        security_protocol: str  - optional, "PLAINTEXT" | "SASL_SSL" | "SSL", default "PLAINTEXT"
        sasl_mechanism: str     - optional, e.g. "PLAIN", required if SASL_SSL
        sasl_username: str      - optional
        sasl_password: str      - optional
        auto_offset_reset: str  - optional, "earliest" | "latest", default "latest"
        drain_timeout_seconds: int - optional, how long fetch_all waits for new messages, default 10
    """

    CONNECTOR_TYPE = ConnectorType.KAFKA_STREAM
    DISPLAY_NAME = "Kafka Stream"
    DESCRIPTION = "Real-time event streaming from Kafka topics"
    ICON = "🌊"
    DOCS = (
        "1. Provide your Kafka bootstrap_servers (comma-separated broker addresses).\n"
        "2. Specify the topic to consume from and a group_id — FRIS uses the consumer group "
        "to track its position in the topic, so use a unique group_id per connector instance.\n"
        "3. If your cluster requires authentication, set security_protocol to SASL_SSL and "
        "provide sasl_mechanism, sasl_username, sasl_password (e.g. for Confluent Cloud).\n"
        "4. Set auto_offset_reset to 'earliest' to backfill from the start of the topic, or "
        "'latest' (default) to only ingest new messages going forward.\n"
        "5. Messages are expected to be JSON-encoded; non-JSON messages are skipped and logged."
    )

    CONFIG_SCHEMA = {
        "required": ["bootstrap_servers", "topic", "group_id"],
        "optional": [
            "security_protocol", "sasl_mechanism", "sasl_username",
            "sasl_password", "auto_offset_reset", "drain_timeout_seconds"
        ],
        "defaults": {
            "security_protocol": "PLAINTEXT",
            "auto_offset_reset": "latest",
            "drain_timeout_seconds": 10
        }
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config

    def _consumer_kwargs(self) -> Dict[str, Any]:
        kwargs = {
            "bootstrap_servers": self.cfg["bootstrap_servers"],
            "group_id": self.cfg["group_id"],
            "auto_offset_reset": self.cfg.get("auto_offset_reset", "latest"),
            "security_protocol": self.cfg.get("security_protocol", "PLAINTEXT"),
            "enable_auto_commit": True,
        }
        if self.cfg.get("security_protocol") == "SASL_SSL":
            kwargs.update({
                "sasl_mechanism": self.cfg.get("sasl_mechanism", "PLAIN"),
                "sasl_plain_username": self.cfg.get("sasl_username"),
                "sasl_plain_password": self.cfg.get("sasl_password"),
            })
        return kwargs

    async def connect(self) -> bool:
        # Connection is established per-operation (test/fetch) since aiokafka
        # consumers are short-lived for our polling-style fetch pattern.
        return True

    async def test_connection(self) -> Dict[str, Any]:
        consumer = AIOKafkaConsumer(self.cfg["topic"], **self._consumer_kwargs())
        try:
            start = time.time()
            await asyncio.wait_for(consumer.start(), timeout=10)
            partitions = consumer.partitions_for_topic(self.cfg["topic"])
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"Connected. Topic '{self.cfg['topic']}' has {len(partitions or [])} partition(s).",
                "latency_ms": latency_ms
            }
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "latency_ms": -1}
        finally:
            await consumer.stop()

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self._drain(limit=limit, timeout_seconds=5)

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        timeout = self.cfg.get("drain_timeout_seconds", 10)
        return await self._drain(limit=10000, timeout_seconds=timeout)

    async def _drain(self, limit: int, timeout_seconds: int) -> List[Dict[str, Any]]:
        """
        Streaming sources don't have a fixed "all records" — this pulls
        whatever is currently available within a time budget, which is the
        correct fetch_all semantics for a continuous topic.
        """
        consumer = AIOKafkaConsumer(self.cfg["topic"], **self._consumer_kwargs())
        records: List[Dict[str, Any]] = []
        await consumer.start()
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and len(records) < limit:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    msg = await asyncio.wait_for(consumer.getone(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    payload = json.loads(msg.value.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(payload, dict):
                    payload.setdefault("_kafka_offset", msg.offset)
                    payload.setdefault("_kafka_partition", msg.partition)
                    records.append(payload)
        finally:
            await consumer.stop()
        return records

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        pass
