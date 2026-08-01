"""
FRIS API Models
Pydantic schemas for request/response validation on every endpoint.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateConnectorRequest(BaseModel):
    connector_type: str = Field(..., description="One of the registered connector types")
    name: str = Field(..., description="User-friendly name, e.g. 'Our CRM'")
    config: Dict[str, Any] = Field(..., description="Type-specific configuration")


class TestConnectorResponse(BaseModel):
    success: bool
    message: str
    latency_ms: int
    extra: Optional[Dict[str, Any]] = None


class FieldUpdateRequest(BaseModel):
    raw_name: str
    user_confirmed_name: Optional[str] = None
    user_confirmed_type: Optional[str] = None
    mapped_to_entity_property: Optional[str] = None


class ConfirmSchemaRequest(BaseModel):
    field_updates: List[FieldUpdateRequest]
    entity_type_override: Optional[str] = None


class ConnectorResponse(BaseModel):
    connector_id: str
    connector_type: str
    name: str
    status: str
    created_at: datetime
    last_synced: Optional[datetime] = None
    total_records_ingested: int
    error_message: Optional[str] = None
    has_schema: bool = False
    schema_confirmed: bool = False


class AvailableConnectorResponse(BaseModel):
    type: str
    display_name: str
    description: str
    icon: str
    config_schema: Dict[str, Any]
    docs: str = ""
    status: str  # "available"


class GraphQueryRequest(BaseModel):
    cypher: str
    params: Optional[Dict[str, Any]] = None


class NaturalLanguageQueryRequest(BaseModel):
    query: str = Field(..., description="Plain English query about the fused data")


class SyncResponse(BaseModel):
    success: bool
    connector_id: Optional[str] = None
    records_fetched: Optional[int] = None
    fusion_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
