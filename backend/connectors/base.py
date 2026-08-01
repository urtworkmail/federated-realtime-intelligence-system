"""
FRIS Base Connector
Every connector inherits from this. Defines the contract:
- connect() → establish connection
- test() → verify it works  
- fetch() → pull raw data
- detect_schema() → auto-detect fields and types
- normalize() → convert to FRIS canonical format
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class ConnectorType(str, Enum):
    REST_API = "rest_api"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    CSV_FILE = "csv_file"
    EXCEL_FILE = "excel_file"
    KAFKA_STREAM = "kafka_stream"
    WEBSOCKET = "websocket"
    WEB_SCRAPER = "web_scraper"
    TWITTER_X = "twitter_x"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    RSS_FEED = "rss_feed"
    SAP = "sap"
    ORACLE = "oracle"
    MONGODB = "mongodb"
    ELASTICSEARCH = "elasticsearch"
    GOOGLE_SHEETS = "google_sheets"
    AIRTABLE = "airtable"
    SLACK = "slack"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    SALESFORCE = "salesforce"
    HUBSPOT = "hubspot"
    CUSTOM = "custom"
    DOCUMENT = "document"


class ConnectorStatus(str, Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"
    PAUSED = "paused"
    SYNCING = "syncing"


class FieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"
    ARRAY = "array"
    UNKNOWN = "unknown"


@dataclass
class DetectedField:
    """A single field detected from a data source"""
    raw_name: str                          # original field name from source
    suggested_name: str                    # cleaned, suggested name
    field_type: FieldType                  # detected type
    sample_values: List[Any]              # first 3-5 sample values
    null_count: int = 0                    # how many nulls detected
    is_identifier: bool = False           # looks like an ID/key field
    is_entity_name: bool = False          # looks like a name field
    is_timestamp: bool = False            # looks like a date/time field
    confidence: float = 1.0              # detection confidence 0-1
    user_confirmed_name: Optional[str] = None    # after user edits
    user_confirmed_type: Optional[FieldType] = None
    mapped_to_entity_property: Optional[str] = None  # what FRIS property this maps to


@dataclass
class SchemaDetectionResult:
    """Result of auto-detecting schema from a data source"""
    connector_id: str
    detected_at: datetime
    fields: List[DetectedField]
    sample_row_count: int
    suggested_entity_type: str           # e.g. "Company", "Person", "Transaction"
    confidence: float
    raw_sample: List[Dict[str, Any]]    # first 5 rows raw
    user_confirmed: bool = False
    user_modified: bool = False


@dataclass
class NormalizedRecord:
    """FRIS canonical record format — what every connector outputs"""
    source_connector_id: str
    source_record_id: str               # original ID from source
    entity_type: str                    # "Company", "Person", etc.
    properties: Dict[str, Any]         # all fields as key-value
    raw_data: Dict[str, Any]           # original untouched data
    ingested_at: datetime = field(default_factory=datetime.utcnow)
    fris_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    confidence: float = 1.0


@dataclass
class ConnectorConfig:
    """Configuration stored per connector instance"""
    connector_id: str
    connector_type: ConnectorType
    name: str                           # user-given name e.g. "Our CRM"
    config: Dict[str, Any]            # type-specific config (credentials etc)
    status: ConnectorStatus = ConnectorStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_synced: Optional[datetime] = None
    schema: Optional[SchemaDetectionResult] = None
    sync_interval_seconds: int = 300   # default 5 min
    total_records_ingested: int = 0
    error_message: Optional[str] = None
    schema_drift: Optional[Dict[str, Any]] = None  # set by ConnectorManager._detect_schema_drift, None if none detected


class BaseConnector(ABC):
    """
    Abstract base class all FRIS connectors inherit from.
    Implement these 5 methods and your connector works with the full system.
    """

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self.connector_id = config.connector_id

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the data source.
        Returns True if successful, raises on failure.
        """
        pass

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """
        Test if connection works without pulling full data.
        Returns: {"success": bool, "message": str, "latency_ms": int}
        """
        pass

    @abstractmethod
    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch a small sample of records for schema detection.
        Never fetches full dataset — just enough to detect structure.
        """
        pass

    @abstractmethod
    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Fetch all records, optionally only those updated since a timestamp.
        Used for full sync and incremental sync.
        """
        pass

    async def detect_schema(self) -> SchemaDetectionResult:
        """
        Auto-detect schema from sample data and store it on this connector's
        config so subsequent normalize()/sync() calls use it automatically.
        This runs on all connectors — no need to override unless source
        has a native schema API (like PostgreSQL information_schema).
        """
        sample = await self.fetch_sample(limit=20)
        schema = SchemaDetector.detect(
            connector_id=self.connector_id,
            records=sample,
            connector_type=self.config.connector_type
        )
        self.config.schema = schema
        return schema

    async def normalize(self, raw_records: List[Dict[str, Any]]) -> List[NormalizedRecord]:
        """
        Convert raw source records to FRIS canonical format.
        Uses the confirmed schema mapping if available, else best-effort.
        """
        if not self.config.schema or not self.config.schema.user_confirmed:
            # Auto-normalize using detected schema
            return AutoNormalizer.normalize(
                records=raw_records,
                connector_id=self.connector_id,
                schema=self.config.schema
            )

        # Use user-confirmed field mappings
        return ConfirmedNormalizer.normalize(
            records=raw_records,
            connector_id=self.connector_id,
            schema=self.config.schema
        )

    async def sync(self) -> Dict[str, Any]:
        """
        Full sync cycle: fetch → normalize → return for graph ingestion.
        Called by the scheduler or manually triggered.
        """
        try:
            since = self.config.last_synced
            raw = await self.fetch_all(since=since)
            normalized = await self.normalize(raw)
            return {
                "success": True,
                "connector_id": self.connector_id,
                "records_fetched": len(raw),
                "records_normalized": len(normalized),
                "normalized_records": normalized,
                "synced_at": datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                "success": False,
                "connector_id": self.connector_id,
                "error": str(e),
                "synced_at": datetime.utcnow().isoformat()
            }


class SchemaDetector:
    """
    Inspects raw records and auto-detects field types, names, and entity hints.
    This is what runs when a user first connects a source.
    """

    IDENTIFIER_PATTERNS = ["id", "_id", "uuid", "key", "code", "ref", "pk"]
    NAME_PATTERNS = ["name", "title", "label", "display", "full_name", "company"]
    TIMESTAMP_PATTERNS = ["created", "updated", "date", "time", "at", "timestamp", "when"]

    @classmethod
    def detect(
        cls,
        connector_id: str,
        records: List[Dict[str, Any]],
        connector_type: ConnectorType
    ) -> SchemaDetectionResult:

        if not records:
            return SchemaDetectionResult(
                connector_id=connector_id,
                detected_at=datetime.utcnow(),
                fields=[],
                sample_row_count=0,
                suggested_entity_type="Unknown",
                confidence=0.0,
                raw_sample=[]
            )

        fields = []
        all_keys = set()
        for record in records:
            all_keys.update(record.keys())

        for key in all_keys:
            values = [r.get(key) for r in records if key in r]
            non_null = [v for v in values if v is not None]
            null_count = len(values) - len(non_null)

            detected_field = DetectedField(
                raw_name=key,
                suggested_name=cls._clean_field_name(key),
                field_type=cls._detect_type(non_null),
                sample_values=non_null[:5],
                null_count=null_count,
                is_identifier=cls._is_identifier(key),
                is_entity_name=cls._is_name_field(key),
                is_timestamp=cls._is_timestamp(key, non_null),
                confidence=cls._type_confidence(non_null)
            )
            fields.append(detected_field)

        entity_type = cls._suggest_entity_type(fields, connector_type)

        return SchemaDetectionResult(
            connector_id=connector_id,
            detected_at=datetime.utcnow(),
            fields=fields,
            sample_row_count=len(records),
            suggested_entity_type=entity_type,
            confidence=cls._overall_confidence(fields),
            raw_sample=records[:5]
        )

    @classmethod
    def _clean_field_name(cls, name: str) -> str:
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        cleaned = re.sub(r'_+', '_', cleaned)
        return cleaned.strip('_').lower()

    @classmethod
    def _detect_type(cls, values: List[Any]) -> FieldType:
        if not values:
            return FieldType.UNKNOWN

        sample = values[:10]

        # Check for bool
        if all(isinstance(v, bool) for v in sample):
            return FieldType.BOOLEAN

        # Check for number (native types)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in sample):
            return FieldType.NUMBER

        # Check for dict/list
        if all(isinstance(v, dict) for v in sample):
            return FieldType.JSON
        if all(isinstance(v, list) for v in sample):
            return FieldType.ARRAY

        # Check string subtypes — CSV/file/scraper sources deliver everything
        # as text, so "120" needs to be recognized as a number, not a string.
        if all(isinstance(v, str) for v in sample):
            if cls._looks_numeric(sample):
                return FieldType.NUMBER
            if cls._looks_boolean(sample):
                return FieldType.BOOLEAN
            return cls._detect_string_subtype(sample)

        return FieldType.STRING

    @classmethod
    def _looks_numeric(cls, values: List[str]) -> bool:
        if not values:
            return False
        numeric_count = 0
        for v in values:
            stripped = v.strip().replace(",", "")
            try:
                float(stripped)
                numeric_count += 1
            except (ValueError, TypeError):
                pass
        return numeric_count == len(values)

    @classmethod
    def _looks_boolean(cls, values: List[str]) -> bool:
        bool_strings = {"true", "false", "yes", "no", "1", "0"}
        return all(v.strip().lower() in bool_strings for v in values) and len(set(v.strip().lower() for v in values)) <= 2

    @classmethod
    def _detect_string_subtype(cls, values: List[str]) -> FieldType:
        import re
        date_patterns = [
            r'^\d{4}-\d{2}-\d{2}$',
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',
            r'^\d{2}/\d{2}/\d{4}$'
        ]
        datetime_count = 0
        for v in values[:5]:
            for pat in date_patterns:
                if re.match(pat, str(v)):
                    datetime_count += 1
                    break

        if datetime_count >= len(values[:5]) * 0.8:
            if 'T' in str(values[0]) or ':' in str(values[0]):
                return FieldType.DATETIME
            return FieldType.DATE

        return FieldType.STRING

    @classmethod
    def _is_identifier(cls, name: str) -> bool:
        name_lower = name.lower()
        return any(pat in name_lower for pat in cls.IDENTIFIER_PATTERNS)

    @classmethod
    def _is_name_field(cls, name: str) -> bool:
        name_lower = name.lower()
        # An identifier field (e.g. "company_id") should never also be flagged
        # as a name field just because it contains "company" or "id" overlaps.
        if cls._is_identifier(name):
            return False
        return any(pat in name_lower for pat in cls.NAME_PATTERNS)

    @classmethod
    def _is_timestamp(cls, name: str, values: List[Any]) -> bool:
        name_lower = name.lower()
        name_match = any(pat in name_lower for pat in cls.TIMESTAMP_PATTERNS)
        if name_match:
            return True
        if values and isinstance(values[0], str):
            import re
            return bool(re.match(r'^\d{4}-\d{2}-\d{2}', str(values[0])))
        return False

    @classmethod
    def _suggest_entity_type(cls, fields: List[DetectedField], connector_type: ConnectorType) -> str:
        field_names = [f.suggested_name for f in fields]
        all_names = ' '.join(field_names).lower()

        if any(w in all_names for w in ['company', 'organization', 'org', 'business', 'firm']):
            return 'Organization'
        if any(w in all_names for w in ['person', 'employee', 'user', 'customer', 'contact', 'name', 'email']):
            return 'Person'
        if any(w in all_names for w in ['transaction', 'payment', 'invoice', 'order', 'deal', 'amount']):
            return 'Transaction'
        if any(w in all_names for w in ['product', 'item', 'sku', 'inventory']):
            return 'Product'
        if any(w in all_names for w in ['event', 'log', 'activity', 'action']):
            return 'Event'

        type_defaults = {
            ConnectorType.TWITTER_X: 'SocialPost',
            ConnectorType.LINKEDIN: 'Person',
            ConnectorType.FACEBOOK: 'SocialPost',
            ConnectorType.RSS_FEED: 'Article',
        }
        return type_defaults.get(connector_type, 'Entity')

    @classmethod
    def _type_confidence(cls, values: List[Any]) -> float:
        if not values:
            return 0.0
        return min(len(values) / 10, 1.0)

    @classmethod
    def _overall_confidence(cls, fields: List[DetectedField]) -> float:
        if not fields:
            return 0.0
        return sum(f.confidence for f in fields) / len(fields)


class AutoNormalizer:
    @staticmethod
    def normalize(
        records: List[Dict[str, Any]],
        connector_id: str,
        schema: Optional[SchemaDetectionResult]
    ) -> List[NormalizedRecord]:
        normalized = []
        entity_type = schema.suggested_entity_type if schema else "Entity"

        id_field = None
        if schema:
            id_fields = [f for f in schema.fields if f.is_identifier]
            if id_fields:
                id_field = id_fields[0].raw_name

        for record in records:
            source_id = str(record.get(id_field, str(uuid.uuid4()))) if id_field else str(uuid.uuid4())
            normalized.append(NormalizedRecord(
                source_connector_id=connector_id,
                source_record_id=source_id,
                entity_type=entity_type,
                properties={k: v for k, v in record.items()},
                raw_data=record
            ))
        return normalized


class ConfirmedNormalizer:
    @staticmethod
    def normalize(
        records: List[Dict[str, Any]],
        connector_id: str,
        schema: SchemaDetectionResult
    ) -> List[NormalizedRecord]:
        normalized = []
        field_map = {}
        id_field = None

        for f in schema.fields:
            confirmed_name = f.user_confirmed_name or f.suggested_name
            field_map[f.raw_name] = confirmed_name
            if f.is_identifier:
                id_field = f.raw_name

        entity_type = schema.suggested_entity_type

        for record in records:
            properties = {}
            for raw_key, value in record.items():
                mapped_key = field_map.get(raw_key, raw_key)
                properties[mapped_key] = value

            source_id = str(record.get(id_field, str(uuid.uuid4()))) if id_field else str(uuid.uuid4())

            normalized.append(NormalizedRecord(
                source_connector_id=connector_id,
                source_record_id=source_id,
                entity_type=entity_type,
                properties=properties,
                raw_data=record
            ))

        return normalized
