// Generates form fields per connector type. Keeps field definitions here
// so adding a new connector's UI is one block, matching its backend CONFIG_SCHEMA.

const FIELD_DEFS = {
  rest_api: [
    { key: 'base_url', label: 'Base URL', type: 'text', placeholder: 'https://api.example.com', required: true },
    { key: 'endpoint', label: 'Endpoint', type: 'text', placeholder: '/v1/companies', required: true },
    { key: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'bearer', 'api_key_header', 'api_key_query', 'basic'], default: 'none' },
    { key: 'auth_token', label: 'Bearer Token', type: 'password', showIf: { auth_type: 'bearer' } },
    { key: 'api_key', label: 'API Key', type: 'password', showIf: { auth_type: ['api_key_header', 'api_key_query'] } },
    { key: 'username', label: 'Username', type: 'text', showIf: { auth_type: 'basic' } },
    { key: 'password', label: 'Password', type: 'password', showIf: { auth_type: 'basic' } },
    { key: 'pagination_type', label: 'Pagination', type: 'select', options: ['none', 'page', 'cursor', 'offset'], default: 'none' },
    { key: 'data_path', label: 'Data Path (optional)', type: 'text', placeholder: 'data.items' },
  ],
  postgresql: [
    { key: 'host', label: 'Host', type: 'text', placeholder: 'db.example.com', required: true },
    { key: 'port', label: 'Port', type: 'text', placeholder: '5432', default: '5432' },
    { key: 'database', label: 'Database', type: 'text', required: true },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
    { key: 'table', label: 'Table', type: 'text', required: true },
    { key: 'schema', label: 'Schema', type: 'text', default: 'public' },
  ],
  mysql: [
    { key: 'host', label: 'Host', type: 'text', required: true },
    { key: 'port', label: 'Port', type: 'text', default: '3306' },
    { key: 'database', label: 'Database', type: 'text', required: true },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
    { key: 'table', label: 'Table', type: 'text', required: true },
  ],
  csv_file: [
    { key: 'file_content', label: 'Upload CSV file', type: 'file', accept: '.csv' },
    { key: 'file_name', type: 'hidden' },
    { key: 'storage_object_id', type: 'hidden' },
    { key: 'file_path', label: 'Or file path (server)', type: 'text', placeholder: '/data/companies.csv' },
    { key: 'has_header', label: 'Has Header Row', type: 'select', options: ['true', 'false'], default: 'true' },
  ],
  excel_file: [
    { key: 'file_content', label: 'Upload Excel file', type: 'file', accept: '.xlsx,.xls' },
    { key: 'file_name', type: 'hidden' },
    { key: 'storage_object_id', type: 'hidden' },
    { key: 'file_path', label: 'Or file path (server)', type: 'text', placeholder: '/data/companies.xlsx' },
    { key: 'sheet_name', label: 'Sheet Name (optional)', type: 'text', placeholder: 'leave blank for first sheet' },
  ],
  document: [
    { key: 'file_content', label: 'Upload PDF or .eml email', type: 'file', accept: '.pdf,.eml' },
    { key: 'file_name', type: 'hidden' },
    { key: 'storage_object_id', type: 'hidden' },
  ],
  rss_feed: [
    { key: 'feed_url', label: 'Feed URL', type: 'text', placeholder: 'https://news.example.com/feed', required: true },
    { key: 'max_items', label: 'Max Items', type: 'text', default: '100' },
  ],
  web_scraper: [
    { key: 'url', label: 'Page URL', type: 'text', required: true },
    { key: 'record_selector', label: 'Record CSS Selector', type: 'text', placeholder: '.company-card', required: true },
    { key: 'field_selectors', label: 'Field Selectors (JSON)', type: 'textarea', placeholder: '{"name": "h3", "sector": ".sector"}', required: true },
  ],
  twitter_x: [
    { key: 'bearer_token', label: 'X API Bearer Token', type: 'password', required: true },
    { key: 'query', label: 'Search Query', type: 'text', placeholder: 'Pakistan startup -is:retweet', required: true },
    { key: 'max_results', label: 'Max Results', type: 'text', default: '100' },
  ],
  linkedin: [
    { key: 'rapidapi_key', label: 'RapidAPI Key', type: 'password', required: true },
    { key: 'search_type', label: 'Search Type', type: 'select', options: ['company', 'person', 'posts'], default: 'company' },
    { key: 'keywords', label: 'Keywords (comma separated)', type: 'text', placeholder: 'fintech, Pakistan' },
  ],
  custom: [
    { key: 'mode', label: 'Mode', type: 'select', options: ['simple', 'advanced'], default: 'simple' },
    { key: 'fetch_url', label: 'Fetch URL', type: 'text', showIf: { mode: 'simple' } },
    { key: 'method', label: 'HTTP Method', type: 'select', options: ['GET', 'POST'], default: 'GET', showIf: { mode: 'simple' } },
    { key: 'data_path', label: 'Data Path (optional)', type: 'text', showIf: { mode: 'simple' } },
    { key: 'fetch_code', label: 'Python Fetch Function', type: 'code', showIf: { mode: 'advanced' } },
    { key: 'entity_type', label: 'Entity Type Name', type: 'text', placeholder: 'e.g. Shipment, Vendor, Asset' },
  ],
  mongodb: [
    { key: 'connection_string', label: 'Connection String', type: 'password', placeholder: 'mongodb://user:pass@host:27017', required: true },
    { key: 'database', label: 'Database', type: 'text', required: true },
    { key: 'collection', label: 'Collection', type: 'text', required: true },
    { key: 'query_filter', label: 'Query Filter (JSON, optional)', type: 'textarea', placeholder: '{"status": "active"}' },
  ],
  kafka_stream: [
    { key: 'bootstrap_servers', label: 'Bootstrap Servers', type: 'text', placeholder: 'broker1:9092,broker2:9092', required: true },
    { key: 'topic', label: 'Topic', type: 'text', required: true },
    { key: 'group_id', label: 'Consumer Group ID', type: 'text', required: true },
    { key: 'security_protocol', label: 'Security Protocol', type: 'select', options: ['PLAINTEXT', 'SASL_SSL', 'SSL'], default: 'PLAINTEXT' },
    { key: 'sasl_mechanism', label: 'SASL Mechanism', type: 'text', showIf: { security_protocol: 'SASL_SSL' } },
    { key: 'sasl_username', label: 'SASL Username', type: 'text', showIf: { security_protocol: 'SASL_SSL' } },
    { key: 'sasl_password', label: 'SASL Password', type: 'password', showIf: { security_protocol: 'SASL_SSL' } },
    { key: 'auto_offset_reset', label: 'Start From', type: 'select', options: ['latest', 'earliest'], default: 'latest' },
  ],
  sap: [
    { key: 'base_url', label: 'OData Base URL', type: 'text', placeholder: 'https://your-sap-host:8000/sap/opu/odata/sap', required: true },
    { key: 'service', label: 'Service Name', type: 'text', placeholder: 'ZFRIS_SRV', required: true },
    { key: 'entity_set', label: 'Entity Set', type: 'text', placeholder: 'BusinessPartnerSet', required: true },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
    { key: 'select_fields', label: 'Select Fields (optional)', type: 'text', placeholder: 'Id,Name,Country' },
  ],
  oracle: [
    { key: 'host', label: 'Host', type: 'text', required: true },
    { key: 'port', label: 'Port', type: 'text', default: '1521' },
    { key: 'service_name', label: 'Service Name', type: 'text', placeholder: 'ORCLPDB1', required: true },
    { key: 'username', label: 'Username', type: 'text', required: true },
    { key: 'password', label: 'Password', type: 'password', required: true },
    { key: 'table_or_query', label: 'Table or SQL Query', type: 'text', placeholder: 'CUSTOMERS', required: true },
    { key: 'updated_column', label: 'Updated Column (optional)', type: 'text' },
  ],
  facebook: [
    { key: 'access_token', label: 'Access Token', type: 'password', required: true },
    { key: 'object_id', label: 'Page / Group ID', type: 'text', placeholder: 'me, or a Page ID', required: true },
    { key: 'edge', label: 'Edge', type: 'select', options: ['posts', 'feed', 'events', 'photos'], default: 'posts' },
    { key: 'fields', label: 'Fields (optional)', type: 'text', placeholder: 'id,message,created_time' },
  ],
  slack: [
    { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: 'xoxb-...', required: true },
    { key: 'channel_id', label: 'Channel ID', type: 'text', required: true },
    { key: 'oldest', label: 'Oldest Timestamp (optional)', type: 'text' },
  ],
  google_sheets: [
    { key: 'spreadsheet_id', label: 'Spreadsheet ID', type: 'text', required: true },
    { key: 'sheet_range', label: 'Sheet & Range', type: 'text', placeholder: 'Sheet1!A1:Z1000', required: true },
    { key: 'api_key', label: 'API Key (public sheets)', type: 'password' },
    { key: 'access_token', label: 'OAuth Access Token (private sheets)', type: 'password' },
    { key: 'has_header_row', label: 'Has Header Row', type: 'select', options: ['true', 'false'], default: 'true' },
  ],
  airtable: [
    { key: 'api_key', label: 'Personal Access Token', type: 'password', required: true },
    { key: 'base_id', label: 'Base ID', type: 'text', placeholder: 'app...', required: true },
    { key: 'table_name', label: 'Table Name', type: 'text', required: true },
    { key: 'view', label: 'View (optional)', type: 'text' },
  ],
  elasticsearch: [
    { key: 'base_url', label: 'Cluster URL', type: 'text', placeholder: 'https://your-cluster:9200', required: true },
    { key: 'index', label: 'Index', type: 'text', required: true },
    { key: 'username', label: 'Username (basic auth)', type: 'text' },
    { key: 'password', label: 'Password (basic auth)', type: 'password' },
    { key: 'api_key', label: 'API Key (alternative to basic auth)', type: 'password' },
    { key: 'query', label: 'Query DSL (JSON, optional)', type: 'textarea', placeholder: '{"match_all": {}}' },
    { key: 'updated_field', label: 'Updated Field (optional)', type: 'text' },
  ],
  websocket: [
    { key: 'ws_url', label: 'WebSocket URL', type: 'text', placeholder: 'wss://stream.example.com/feed', required: true },
    { key: 'subscribe_message', label: 'Subscribe Message (JSON, optional)', type: 'textarea', placeholder: '{"type": "subscribe", "channel": "trades"}' },
    { key: 'drain_timeout_seconds', label: 'Listen Window (seconds)', type: 'text', default: '10' },
  ],
}

export function getFieldDefs(connectorType) {
  return FIELD_DEFS[connectorType] || []
}

export function shouldShowField(field, formValues) {
  if (!field.showIf) return true
  return Object.entries(field.showIf).every(([key, expected]) => {
    const current = formValues[key] ?? ''
    if (Array.isArray(expected)) return expected.includes(current)
    return current === expected
  })
}
