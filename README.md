# FRIS — Federated Real-time Intelligence System
## MVP + Phase 1 (Embeddings) + Phase 2 (Accounts, LLM Query, Billing, Graph Explorer)

This is the working pre-seed build: a backend that ingests data from any
configured connector, auto-detects its schema, lets you confirm/edit field
mappings, then fuses it into a shared Neo4j entity graph with entity
resolution and conflict resolution. The frontend is a console-style control
panel (light/dark theme toggle, top nav, collapsible sidebar, breadcrumb,
table layout) for managing connectors, browsing the fused graph, and
auditing every system event.

On top of that MVP, FRIS now has: lightweight accounts + API keys, a
provider-agnostic natural-language **Ask** layer (FRIS-hosted offline default,
or bring your own OpenAI/Anthropic key, or connect Google/Azure via OAuth), a
postpaid metered usage ledger with a **Usage & Costs** page (every LLM query,
search, sync, embedding, and GB of storage is metered), and a **Graph
Explorer** with both a structured node browser and an interactive Cytoscape
visualization of entity relationships.

---

## What's implemented

**20 working connectors** (full backend integration code, not stubs):
REST API, PostgreSQL, MySQL, CSV file, Excel file, RSS feed, web scraper,
Twitter/X, LinkedIn, Custom (simple URL config or user-supplied Python fetch
function), MongoDB, Kafka Stream, SAP (via OData services), Oracle DB,
Facebook, Slack, Google Sheets, Airtable, Elasticsearch, and WebSocket
streams. Every connector ships with a `DOCS` field — visible as a "Docs"
toggle in the catalog table — explaining exactly how to configure it
(credentials needed, where to find IDs, auth setup).

Note on SAP: this connects via SAP's OData services (SAP Gateway / S/4HANA),
not classic RFC/BAPI — RFC requires SAP's proprietary NW RFC SDK, which isn't
distributable via pip. If your SAP landscape only exposes RFC, use the
Custom connector to wrap a `pyrfc`-based fetch function instead.

**Fusion engine**: entity resolution (fuzzy name matching with legal-suffix
normalization — "Acme Corp" and "Acme Corporation" fuse correctly; exact
identifier match on domain/email/SKU/registration number is decisive and
short-circuits fuzzy comparison), conflict resolution (source trust score +
recency), and full provenance tracking (which source contributed which field
value).

**Semantic search (embeddings)**: every fused entity gets a vector embedding
generated automatically at fusion time, using an offline CPU-friendly model
(`all-MiniLM-L6-v2`). Powers `/api/graph/search`, similarity search over the
graph by meaning rather than exact field match. Re-embedding is skipped for
entities whose content hasn't actually changed, to avoid wasted compute on
every sync. See "Semantic search" section below for details, including the
backfill endpoint for upgrading an existing instance.

**Logging & audit trail**: every server, network, console, app, and
data-processing event is automatically logged to a Postgres `logs` table —
no manual logging calls needed in route handlers, it's wired in via
middleware and targeted hooks in the connector/fusion lifecycle. The Logs
page in the sidebar (under "Admin") lets you filter by category, level,
free-text search, and time window, with a 30-day purge action.

**Dark/light theme toggle**: click the moon/sun icon in the top nav. The
whole console repaints via CSS variables — no per-component theme logic.
Preference persists in `localStorage`.

**Schema detection**: auto-detects field types and names from any source,
flags likely identifier/name/timestamp fields, suggests an entity type, and
returns it for user review — the user can rename fields, change types, or
remap before confirming. Matches the workflow you asked for: auto-detect,
user confirms, can edit/add.

**Accounts & API keys**: email/password signup and login (JWT sessions for
the console), plus per-account API keys (`fris_sk_...`) for programmatic
access via the `X-API-Key` header. This is attribution-grade identity — it's
what usage/billing and LLM credentials are scoped to — not yet full
multi-tenant data isolation (that's a later phase).

**Ask — natural-language query (Phase 2)**: `POST /api/query` embeds your
question, retrieves the closest fused entities, and asks an LLM to answer
grounded strictly in that retrieved context, citing entity sources. If
retrieval confidence is too low, it asks a clarifying question instead of
guessing (`needs_clarification: true`) — a seed of the planned deeper Phase 3
Q/A loop. Provider is configurable per account under **LLM Providers**:
FRIS-hosted (offline, default), OpenAI/Anthropic (bring your own API key), or
Google Gemini/Azure OpenAI (OAuth — the only two providers that require it).

**Usage & Costs (postpaid metered billing)**: every metered action — LLM
queries, semantic/graph queries, connector syncs, embeddings, and storage —
writes a usage event with its token count and cost. There's no hard blocking;
the **Usage & Costs** page shows a running ledger, a cost breakdown by event
type, and monthly invoice rollups. Pricing per event type lives in the
`pricing` table and is editable via `PUT /api/billing/pricing/{event_type}`.

**Graph Explorer**: browse fused entities in a filterable table, or click one
to render an interactive node-link visualization (Cytoscape.js, fully
offline, no CDN) of its relationships — expand further by clicking any node
in the graph.

---

## Architecture

```
backend/
  connectors/        — one file per connector type, all inherit from base.py
  core/
    fusion_engine.py          — entity resolution + conflict resolution + Neo4j writes + graph explorer reads
    connector_manager.py      — orchestrates lifecycle: create → test → detect → confirm → sync
    account_service.py        — accounts, JWT sessions, API keys
    llm_credential_service.py — per-account LLM provider credentials (API key or OAuth), encrypted at rest
    llm_service.py            — provider-agnostic LLM adapters + RAG query logic
    metering_service.py       — postpaid usage ledger + pricing
    logging_service.py        — audit trail
  api/               — FastAPI routers: auth, graph, query, providers, oauth, billing
  config.py          — centralized FRIS_* settings
  state.py           — shared service singletons (set once in main.py's lifespan)
  models/schemas.py  — API request/response models
  main.py            — FastAPI app, connector endpoints, router wiring

frontend/
  src/components/    — TopNav, Sidebar, Breadcrumb, ConnectorCatalogModal, ConnectorSetupWizard
  src/pages/         — Overview, Ask, Connectors, Entities, Graph, GraphExplorer, Billing, Providers, Account, Logs, Login
  src/utils/api.js   — API client (JWT auth header on every call)
```

---

## Running it locally

### Option A — Docker Compose (recommended, runs everything)

```bash
docker-compose up --build
```

This starts Neo4j (port 7474 browser / 7687 bolt), PostgreSQL (port 5432),
and the FastAPI backend (port 8000) together, wired to each other.

Then in a separate terminal, run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### Option B — Manual (more control, useful while developing)

**1. Start Neo4j and PostgreSQL** (via Docker or local installs):

```bash
docker run -d --name fris-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/fris_dev_password neo4j:5.26-community

docker run -d --name fris-postgres -p 5432:5432 \
  -e POSTGRES_USER=fris -e POSTGRES_PASSWORD=fris_dev_password \
  -e POSTGRES_DB=fris_control postgres:16-alpine
```

**2. Start the backend:**

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**3. Start the frontend:**

```bash
cd frontend
npm install
npm run dev
```

---

## Where to put your data files (CSV / Excel)

The CSV and Excel connectors read files from inside the **backend container**,
not from your host machine directly — so `file_path` only works if the file
is actually mounted into that container.

**Drop files into the `data/` folder at the project root** (next to
`backend/`, `frontend/`, `docker-compose.yml`). It's mounted into the
container at `/app/data`, so:

```
fris/
  data/
    prefix_supplier_map_FINAL.csv   ← put files here
  backend/
  frontend/
  docker-compose.yml
```

Then in the connector's **File Path (server)** field, use the path **as seen
inside the container**, i.e. `data/prefix_supplier_map_FINAL.csv` (relative
to `/app`) — not the host path. If you restructure or add subfolders under
`data/`, the relative path you type just needs to match what's under
`data/` on disk.

If you're running the backend without Docker (`uvicorn main:app` directly on
your machine), `file_path` is resolved relative to wherever you launched
that process from, so `data/...` still works as long as you run it from the
`fris/` project root.

---

## Semantic search (embeddings)

Every fused entity automatically gets a vector embedding generated from its
fields, using a small offline model (`all-MiniLM-L6-v2`, runs on CPU, no
external API calls). This means you can search the graph by meaning, not
just by exact field match.

```
GET /api/graph/search?q=logistics company in the gulf&top_k=10
```

This returns entities ranked by semantic similarity, even if none of them
contain the literal words "logistics" or "gulf" in a field.

**First boot will be slower than before.** The embedding model (and its
`torch` dependency) is a few hundred MB and gets downloaded on first
`docker-compose build`, then loaded into memory on every backend startup
(typically a few seconds once cached locally). For air-gapped deployments,
the model must be pre-downloaded and baked into the image before going
offline, since it cannot reach the internet to fetch weights once deployed.

**If you're upgrading an existing FRIS instance** that already has fused
entities from before this feature existed, run the backfill once so older
entities become searchable too:

```
POST /api/graph/embeddings/backfill?batch_size=100
```

Call it repeatedly (or loop it) until the response shows
`"batch_complete": true` - it processes entities without an embedding yet,
100 at a time, and is safe to call multiple times.

---

## First thing to try

1. Open the console — you'll land on **Sign up** the first time. Create an
   account (email + password); you're signed in immediately after.
2. Go to **Connectors** → **+ Add Connector**.
3. Pick **CSV File**, point `file_path` at a CSV you've placed in the
   project's `data/` folder (see above) — or extend the connector to accept
   uploads from the browser, noted as a near-term improvement below.
4. Test → Detect Schema → review the auto-detected fields → Confirm → Sync.
5. Go to **Overview** to see entity counts, or **Entities** to browse what
   got fused into the graph.
6. Add a second connector describing overlapping entities (e.g. another CSV
   with slightly different company name spellings) and sync it — watch the
   entity count *not* double, because fusion matched them.
7. Go to **Ask** and ask a question about your data in plain English —
   the FRIS-hosted offline provider works out of the box. To use OpenAI or
   Anthropic instead, add your key under **LLM Providers**.
8. Check **Usage & Costs** — the sync, and any Ask query, should already
   show up as metered events.
9. Open **Graph Explorer**, click a node, and see its relationships rendered
   as a live graph.

---

## What's intentionally NOT in this MVP

Per the lean pre-seed scope: no Apache Flink for stateful stream processing
at scale (the Kafka connector here uses a simpler drain-within-time-budget
pattern, sufficient for demos — Flink is for production-scale continuous
fusion), no Kubernetes/Terraform (single Docker Compose is enough to demo),
no air-gapped packaging, no role-based access control (the logging system
above is an audit trail, but there's no user/permission model yet), no
managed Kafka cluster setup (you bring your own broker). These are all in
the original `FRIS_Production_Build_Plan.docx` for when funded — building
them now would burn solo-founder time on infrastructure investors won't see
in a 15-minute demo.

## Near-term improvements worth doing before investor meetings

- Browser-based file upload for CSV/Excel connectors (currently expects a
  server file path — fine for your own demo data, not yet self-serve)
- A relationship-creation UI (the engine supports `create_relationship()`
  but nothing in the frontend calls it yet — useful for showing the graph
  *visually* connected, not just a flat entity list)
- A simple graph visualization (Neovis.js, mentioned in the original build
  plan) instead of raw Cypher query results as JSON

---

## Default credentials & secrets (local dev only — change before any real deployment)

- Neo4j: `neo4j` / `fris_dev_password`
- PostgreSQL: `fris` / `fris_dev_password`
- `FRIS_SECRET_KEY` (JWT signing) and `FRIS_ENCRYPTION_KEY` (encrypts stored
  BYO LLM API keys/OAuth tokens at rest) both ship with insecure dev defaults
  in `docker-compose.yml` — **replace both before any real deployment.**
- To enable Google/Azure OAuth for LLM providers, set
  `FRIS_GOOGLE_OAUTH_CLIENT_ID` / `FRIS_GOOGLE_OAUTH_CLIENT_SECRET` and/or
  `FRIS_AZURE_OAUTH_CLIENT_ID` / `FRIS_AZURE_OAUTH_CLIENT_SECRET`. Without
  these, the "Connect" button on that provider will return a clear 503 rather
  than failing silently.
