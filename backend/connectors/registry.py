"""
FRIS Connector Registry
Central registry that the sidebar UI reads from.
Every connector class registers itself here with its metadata.
Adding a new connector type later = write the class + add one line here.
"""

from typing import Dict, List, Optional, Type

from .base import BaseConnector, ConnectorType
from .rest_api import RestApiConnector
from .databases import PostgreSQLConnector, MySQLConnector
from .files import CSVConnector, ExcelConnector
from .social_web import RSSFeedConnector, WebScraperConnector, TwitterXConnector, LinkedInConnector
from .custom import CustomConnector
from .mongodb import MongoDBConnector
from .kafka_stream import KafkaStreamConnector
from .sap import SAPConnector
from .oracle import OracleConnector
from .platform_apis import (
    FacebookConnector, SlackConnector, GoogleSheetsConnector,
    AirtableConnector, ElasticsearchConnector
)
from .websocket_stream import WebSocketConnector
from .documents import DocumentConnector
from .warehouses import SnowflakeConnector, BigQueryConnector
from .crm import SalesforceConnector, HubSpotConnector


class ConnectorRegistry:
    """
    Single source of truth for "what connectors exist".
    The /api/connectors/available endpoint reads straight from this.
    """

    _registry: Dict[ConnectorType, Type[BaseConnector]] = {
        ConnectorType.REST_API: RestApiConnector,
        ConnectorType.POSTGRESQL: PostgreSQLConnector,
        ConnectorType.MYSQL: MySQLConnector,
        ConnectorType.CSV_FILE: CSVConnector,
        ConnectorType.EXCEL_FILE: ExcelConnector,
        ConnectorType.RSS_FEED: RSSFeedConnector,
        ConnectorType.WEB_SCRAPER: WebScraperConnector,
        ConnectorType.TWITTER_X: TwitterXConnector,
        ConnectorType.LINKEDIN: LinkedInConnector,
        ConnectorType.MONGODB: MongoDBConnector,
        ConnectorType.KAFKA_STREAM: KafkaStreamConnector,
        ConnectorType.SAP: SAPConnector,
        ConnectorType.ORACLE: OracleConnector,
        ConnectorType.FACEBOOK: FacebookConnector,
        ConnectorType.SLACK: SlackConnector,
        ConnectorType.GOOGLE_SHEETS: GoogleSheetsConnector,
        ConnectorType.AIRTABLE: AirtableConnector,
        ConnectorType.ELASTICSEARCH: ElasticsearchConnector,
        ConnectorType.WEBSOCKET: WebSocketConnector,
        ConnectorType.SNOWFLAKE: SnowflakeConnector,
        ConnectorType.BIGQUERY: BigQueryConnector,
        ConnectorType.SALESFORCE: SalesforceConnector,
        ConnectorType.HUBSPOT: HubSpotConnector,
        ConnectorType.CUSTOM: CustomConnector,
        ConnectorType.DOCUMENT: DocumentConnector,
    }

    @classmethod
    def get_connector_class(cls, connector_type: ConnectorType) -> Optional[Type[BaseConnector]]:
        return cls._registry.get(connector_type)

    @classmethod
    def create_connector(cls, config) -> BaseConnector:
        """Factory: instantiate the right connector class from a config object."""
        connector_class = cls.get_connector_class(config.connector_type)
        if not connector_class:
            raise ValueError(f"Unknown connector type: {config.connector_type}")
        return connector_class(config)

    @classmethod
    def list_available(cls) -> List[Dict]:
        """
        Returns metadata for every implemented connector type, including the
        docs field used by the catalog's Docs column.
        """
        available = []
        for connector_type, connector_class in cls._registry.items():
            available.append({
                "type": connector_type.value,
                "display_name": getattr(connector_class, "DISPLAY_NAME", connector_type.value),
                "description": getattr(connector_class, "DESCRIPTION", ""),
                "icon": getattr(connector_class, "ICON", "🔌"),
                "config_schema": getattr(connector_class, "CONFIG_SCHEMA", {}),
                "docs": getattr(connector_class, "DOCS", ""),
                "status": "available"
            })
        return available

    @classmethod
    def is_implemented(cls, connector_type: str) -> bool:
        try:
            return ConnectorType(connector_type) in cls._registry
        except ValueError:
            return False
