import { useState, useEffect } from 'react'
import { api } from '../utils/api'

const SECTIONS = [
  { id: 'architecture', label: 'Architecture' },
  { id: 'api-reference', label: 'API Reference' },
  { id: 'connectors', label: 'Connector Development Guide' },
  { id: 'security', label: 'Security & Compliance Model' },
  { id: 'changelog', label: 'Changelog' },
]

function Architecture() {
  return (
    <div className="docs-content">
      <h2>Architecture Overview</h2>
      <p>
        FRIS is a graph-based data fusion engine: heterogeneous sources are ingested through a
        common connector interface, normalized into a canonical record format, reconciled through
        entity resolution and conflict resolution, and written into a shared Neo4j entity graph.
        Every fused fact carries full provenance — which source contributed it, when, and at what
        trust score — so downstream consumers (dashboards, the Ask assistant, GraphQL/REST clients)
        query one calibrated graph instead of reconciling sources themselves.
      </p>
      <pre className="docs-code">{`backend/
  connectors/        one file per connector type, all inherit connectors/base.py
  core/
    fusion_engine.py          entity resolution, conflict resolution, Neo4j writes,
                               provenance, explainability, review queue
    connector_manager.py      lifecycle: create -> test -> detect schema -> confirm -> sync
                               (+ schema drift detection on every sync)
    account_service.py        accounts, organizations (tenants), roles, JWT sessions, API keys
    context_service.py        living context layer — canonical field definitions per entity type
    llm_credential_service.py per-account LLM provider credentials, encrypted at rest
    llm_service.py            provider-agnostic LLM adapters + RAG query logic
    storage_service.py        tenant-scoped, encrypted-at-rest object storage
    metering_service.py       postpaid usage ledger + pricing
    logging_service.py        tenant-scoped audit trail
  api/               FastAPI routers: auth, graph, query, providers, oauth, billing, context
  config.py          centralized FRIS_* settings, fails fast on insecure prod secrets
  state.py           shared service singletons
  main.py            FastAPI app, connector endpoints, router wiring

frontend/
  src/components/    TopNav, Sidebar, Breadcrumb, ConnectorCatalogModal, ConnectorSetupWizard
  src/pages/         Overview, Ask, Connectors, Entities, Context, Graph, GraphExplorer,
                     Billing, Providers, Account, Logs, Docs, Login
  src/utils/api.js   API client (JWT/API-key auth header on every call)`}</pre>
      <h3>Tenancy model</h3>
      <p>
        Every account belongs to exactly one <strong>organization</strong> — the tenant boundary.
        Every connector, log entry, context definition, and fused graph entity carries an
        <code>organization_id</code>, and every read/write path in <code>fusion_engine.py</code>
        filters on it. A fris_id or connector_id from another organization is treated as not found,
        not as a permission error — so the boundary can't leak which IDs exist elsewhere.
      </p>
      <h3>Processing tiers</h3>
      <p>
        The same engine core serves four processing tiers from one codebase: continuous streaming
        (Kafka/WebSocket connectors), micro-batch, scheduled batch, and on-demand API — the
        connector framework abstracts the difference, so calibration work done for one vertical or
        deployment model carries over to the others.
      </p>
    </div>
  )
}

function ApiReference() {
  const [spec, setSpec] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getOpenApiSpec().then(setSpec).catch(() => setError(true))
  }, [])

  if (error) return <div className="docs-content"><p className="helper-text">Couldn't load the live API spec from the running backend.</p></div>
  if (!spec) return <div className="docs-content"><p className="helper-text">Loading live API reference…</p></div>

  const paths = Object.entries(spec.paths || {})
  const byTag = {}
  for (const [path, methods] of paths) {
    for (const [method, details] of Object.entries(methods)) {
      const tag = (details.tags && details.tags[0]) || 'other'
      byTag[tag] = byTag[tag] || []
      byTag[tag].push({ path, method: method.toUpperCase(), summary: details.summary || details.description || '' })
    }
  }

  return (
    <div className="docs-content">
      <h2>API Reference</h2>
      <p>
        Generated live from this backend's OpenAPI schema (<code>GET /api/openapi.json</code>) —
        this list always matches the endpoints actually running, not a hand-maintained copy that
        drifts. Interactive Swagger UI is also available directly at <code>/api/docs</code>.
      </p>
      {Object.entries(byTag).sort(([a], [b]) => a.localeCompare(b)).map(([tag, endpoints]) => (
        <div key={tag} style={{ marginBottom: 20 }}>
          <h3 style={{ textTransform: 'capitalize' }}>{tag}</h3>
          <table className="console-table">
            <thead><tr><th>Method</th><th>Path</th><th>Summary</th></tr></thead>
            <tbody>
              {endpoints.map(e => (
                <tr key={`${e.method}-${e.path}`}>
                  <td><span className={`badge badge-${e.method === 'GET' ? 'neutral' : 'success'}`}>{e.method}</span></td>
                  <td><code>{e.path}</code></td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function ConnectorGuide() {
  return (
    <div className="docs-content">
      <h2>Connector Development Guide</h2>
      <p>
        Every connector implements the abstract base class in <code>connectors/base.py</code>.
        Five methods are required:
      </p>
      <pre className="docs-code">{`class BaseConnector(ABC):
    async def connect(self) -> bool: ...
    async def test_connection(self) -> Dict[str, Any]: ...
    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]: ...
    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]: ...
    # detect_schema() and normalize() have default implementations —
    # override only if your source has a native schema API.`}</pre>
      <p>
        Once those are implemented, your connector plugs into the full lifecycle automatically:
        <code>create → test → detect-schema → confirm-schema → sync</code>, schema drift detection,
        entity-resolution fusion, embeddings, and the review queue all work without any
        connector-specific code.
      </p>
      <h3>No-phone-home requirement</h3>
      <p>
        FRIS deploys air-gapped in some environments. A connector may call whatever external
        endpoint the user configures it to (that's its job), but must never call a FRIS-operated
        or third-party endpoint the user didn't explicitly configure. Every connector's <code>DOCS</code>
        field (shown as the "Docs" toggle in the catalog) should state plainly what network calls
        it makes and to where.
      </p>
      <h3>Registering a new connector type</h3>
      <p>
        Add the type to <code>ConnectorType</code> in <code>connectors/base.py</code>, implement the
        class, and register it in <code>connectors/registry.py</code>. The Connectors catalog and
        setup wizard pick it up automatically — no frontend changes needed for a standard connector.
      </p>
    </div>
  )
}

function Security() {
  return (
    <div className="docs-content">
      <h2>Security &amp; Compliance Model</h2>
      <h3>Tenant isolation</h3>
      <p>
        Every account belongs to one organization (tenant). Every graph entity, connector, log
        entry, and context definition carries an <code>organization_id</code>, enforced at the query
        layer in <code>fusion_engine.py</code>, <code>connector_manager.py</code>, and
        <code>logging_service.py</code> — not just at the API layer. A request scoped to one
        organization cannot read, modify, or enumerate another organization's data via any endpoint.
      </p>
      <h3>Authentication &amp; RBAC</h3>
      <p>
        Console sessions use JWT (<code>Authorization: Bearer</code>); programmatic access uses
        per-account API keys (<code>X-API-Key</code>, prefix <code>fris_sk_</code>, bcrypt-hashed at
        rest — the plaintext key is shown exactly once, at creation). Every account has a role
        (<code>owner</code>, <code>admin</code>, <code>member</code>, <code>viewer</code>) enforced by
        the <code>require_role</code> dependency on privileged endpoints (deleting connectors, purging
        logs, editing context definitions, raw Cypher queries, pricing changes).
      </p>
      <h3>Encryption at rest</h3>
      <p>
        BYO LLM provider credentials (API keys and OAuth tokens) are encrypted at rest via Fernet
        using <code>FRIS_ENCRYPTION_KEY</code>. Tenant-uploaded files (<code>storage_service.py</code>)
        are encrypted at rest with the same mechanism, stored under a per-organization path.
      </p>
      <h3>Secrets hardening</h3>
      <p>
        <code>config.py</code> refuses to start when <code>FRIS_ENV=production</code> and
        <code>FRIS_SECRET_KEY</code> / <code>FRIS_ENCRYPTION_KEY</code> still equal the documented
        insecure development defaults.
      </p>
      <h3>Data boundary / provenance</h3>
      <p>
        Client data stays within the deployment boundary by default — FRIS does not retain or
        centrally process client data. When a request does cross that boundary (a BYO cloud LLM
        call), it only happens because that organization's own account explicitly configured that
        credential, never FRIS-side aggregation. Every fused fact's decision trail (which source,
        what trust score, when) is queryable via <code>GET /api/graph/entities/{'{fris_id}'}/explain</code> —
        provenance is provable on request, not just asserted.
      </p>
      <h3>Air-gapped compatibility</h3>
      <p>
        The embedding model and offline LLM fallback run entirely on CPU with no external API
        calls by default. No component in this codebase requires internet access at runtime unless
        an account explicitly configures a cloud connector or BYO LLM provider.
      </p>
    </div>
  )
}

function Changelog() {
  return (
    <div className="docs-content">
      <h2>Changelog</h2>
      <ul className="docs-changelog">
        <li><strong>Trust &amp; Tenancy Foundation</strong> — organizations (tenants), RBAC roles, full
          multi-tenant data isolation across the graph and control plane, secrets fail-fast,
          entity-resolution confidence scoring with a review queue.</li>
        <li><strong>Explainability &amp; Governance</strong> — schema drift detection, per-entity
          explain/provenance API, the living context layer (canonical field definitions), and this
          Documentation Center.</li>
      </ul>
    </div>
  )
}

export default function DocsPage() {
  const [section, setSection] = useState('architecture')

  return (
    <div className="docs-layout">
      <div className="docs-nav">
        {SECTIONS.map(s => (
          <div
            key={s.id}
            className={`sidebar-item ${section === s.id ? 'active' : ''}`}
            onClick={() => setSection(s.id)}
          >
            {s.label}
          </div>
        ))}
      </div>
      <div className="console-panel" style={{ flex: 1 }}>
        <div className="console-panel-body">
          {section === 'architecture' && <Architecture />}
          {section === 'api-reference' && <ApiReference />}
          {section === 'connectors' && <ConnectorGuide />}
          {section === 'security' && <Security />}
          {section === 'changelog' && <Changelog />}
        </div>
      </div>
    </div>
  )
}
