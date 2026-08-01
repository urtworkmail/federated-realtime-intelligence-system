"""
FRIS Document Connector
Ingests unstructured documents (PDF, email/.eml, plain text) and extracts
entities via core/nlp_service.py — the literal a16z-thesis case: a document
becomes structured, fused graph entities instead of an opaque blob nobody's
agent can use.

Unlike every other connector, a single document doesn't have one uniform
entity_type across its rows — a contract PDF might mention three
Organizations and two People in the same file. So this connector overrides
normalize() entirely rather than going through the schema-confirmation flow
every structured connector uses: each extracted entity becomes its own
NormalizedRecord, tagged with its own entity_type and the NLP extraction's
own confidence score, which is exactly the signal fusion_engine.py's
review-queue threshold already knows how to act on.
"""

import base64
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseConnector, ConnectorConfig, ConnectorType, NormalizedRecord

_nlp_service = None


def _get_nlp_service():
    global _nlp_service
    if _nlp_service is None:
        from core.nlp_service import NLPService
        _nlp_service = NLPService()
    return _nlp_service


class DocumentConnector(BaseConnector):
    """
    Config keys:
        file_content: str        - base64-encoded file bytes (from browser upload)
        file_name: str           - original filename, used to pick a parser by extension
        storage_object_id: str   - reference into core/storage_service.py, for the Data Viewer
                                    (not used for fetching here — file_content is always populated
                                    at upload time, see api/uploads.py)
    """

    CONNECTOR_TYPE = ConnectorType.DOCUMENT
    DISPLAY_NAME = "Document (PDF / Email)"
    DESCRIPTION = "Extract entities from PDFs or emails via offline NLP — no schema mapping needed"
    ICON = "📰"
    DOCS = (
        "1. Upload a PDF or .eml email file from the browser (Data Viewer shows what was extracted).\n"
        "2. FRIS extracts entities (organizations, people, monetary amounts) offline — no external "
        "API call, so this works air-gapped.\n"
        "3. Unlike other connectors, there's no schema-confirmation step: each extracted entity is "
        "tagged with its own type and a confidence score. Low-confidence extractions land in the "
        "Entities page Review Queue instead of being silently trusted."
    )

    CONFIG_SCHEMA = {
        "required_one_of": ["file_content"],
        "optional": ["file_name", "storage_object_id"],
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._extracted: Optional[List[Dict[str, Any]]] = None
        self._text_length = 0

    def _get_file_bytes(self) -> bytes:
        content = self.cfg.get("file_content")
        if not content:
            raise ValueError("No file_content provided — upload a file first")
        return base64.b64decode(content)

    def _extract_text(self, data: bytes) -> str:
        file_name = (self.cfg.get("file_name") or "").lower()

        if file_name.endswith(".pdf"):
            return self._extract_pdf_text(data)
        if file_name.endswith(".eml"):
            return self._extract_eml_text(data)

        # Fallback: treat as plain text (also what a non-.pdf/.eml upload falls through to).
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_pdf_text(self, data: bytes) -> str:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("pypdf not installed. Run: pip install pypdf")
        import io
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _extract_eml_text(self, data: bytes) -> str:
        import email
        from email.policy import default as default_policy

        msg = email.message_from_bytes(data, policy=default_policy)
        parts = [f"Subject: {msg.get('subject', '')}", f"From: {msg.get('from', '')}", f"To: {msg.get('to', '')}"]
        body = msg.get_body(preferencelist=("plain", "html"))
        if body:
            parts.append(body.get_content())
        return "\n".join(parts)

    async def connect(self) -> bool:
        data = self._get_file_bytes()
        text = self._extract_text(data)
        self._text_length = len(text)
        self._extracted = _get_nlp_service().extract_entities(text)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            start = time.time()
            if self._extracted is None:
                await self.connect()
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"Extracted {len(self._extracted)} entities from {self._text_length} characters "
                           f"of text (backend: {_get_nlp_service().backend})",
                "latency_ms": latency_ms,
                "row_count": len(self._extracted),
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if self._extracted is None:
            await self.connect()
        return self._extracted[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if self._extracted is None:
            await self.connect()
        return self._extracted

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[NormalizedRecord]:
        """
        Overridden: bypasses the schema-confirmation normalizer every other
        connector uses. Each raw record already carries its own entity_type
        and confidence from NLP extraction — normalize it directly rather
        than forcing a single suggested_entity_type across the whole batch.
        """
        normalized = []
        for record in raw_records:
            properties = {k: v for k, v in record.items() if k not in ("entity_type", "confidence", "source_snippet")}
            normalized.append(NormalizedRecord(
                source_connector_id=self.connector_id,
                source_record_id=str(uuid.uuid4()),
                entity_type=record["entity_type"],
                properties=properties,
                raw_data=record,
                confidence=record.get("confidence", 1.0),
            ))
        return normalized
