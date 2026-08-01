"""
FRIS API Server
Main FastAPI application. This is the backend the Connectors sidebar
and the rest of the FRIS frontend talk to.

Run with: uvicorn main:app --reload --port 8000
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import state
from api.auth import router as auth_router
from api.billing import router as billing_router
from api.context import router as context_router
from api.deps import get_current_account, require_role
from api.graphql import router as graphql_router
from api.mcp_server import router as mcp_router
from api.uploads import router as uploads_router
from api.graph import router as graph_router
from api.oauth import router as oauth_router
from api.providers import router as providers_router
from api.query import router as query_router
from config import settings
from connectors.registry import ConnectorRegistry
from core.account_service import AccountService
from core.connector_manager import ConnectorManager
from core.embedding_service import EmbeddingService
from core.fusion_engine import FusionEngine
from core.llm_credential_service import LLMCredentialService
from core.llm_service import LLMService
from core.logging_service import LoggingService, LogCategory, LogLevel
from core.metering_service import EventType, MeteringService
from core.storage_service import StorageService
from core.context_service import ContextService
from models.schemas import (
    CreateConnectorRequest, ConfirmSchemaRequest, SyncResponse
)


async def _connect_with_retry(connect_fn, name: str):
    """
    Retries an async connect function with a fixed delay, logging each
    attempt. Used for both Neo4j and Postgres so a slow container boot
    doesn't permanently crash the backend on the very first try.
    """
    last_error = None
    for attempt in range(1, settings.STARTUP_RETRY_ATTEMPTS + 1):
        try:
            await connect_fn()
            print(f"[startup] {name} connected on attempt {attempt}")
            return
        except Exception as e:
            last_error = e
            print(f"[startup] {name} not ready yet (attempt {attempt}/{settings.STARTUP_RETRY_ATTEMPTS}): {e}")
            await asyncio.sleep(settings.STARTUP_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"{name} failed to connect after {settings.STARTUP_RETRY_ATTEMPTS} attempts: {last_error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Embedding model load happens before anything else — it's a CPU-bound,
    # potentially slow step (first run downloads weights; later runs load
    # from local cache), and fusion_engine needs the service instance ready
    # before its first sync can generate embeddings.
    import time
    embed_start = time.monotonic()
    state.embedding_service = EmbeddingService(settings.EMBEDDING_MODEL_NAME)
    state.embedding_service.load()
    embed_duration = round(time.monotonic() - embed_start, 1)
    print(f"[startup] Embedding model '{settings.EMBEDDING_MODEL_NAME}' loaded in {embed_duration}s")

    state.fusion_engine = FusionEngine(
        settings.NEO4J_URI, settings.NEO4J_USER, settings.NEO4J_PASSWORD,
        embedding_service=state.embedding_service
    )
    await _connect_with_retry(state.fusion_engine.connect, "Neo4j")

    state.connector_manager = ConnectorManager(settings.CONTROL_DB_DSN, state.fusion_engine)
    await _connect_with_retry(state.connector_manager.connect, "PostgreSQL (control DB)")

    # Every other Postgres-backed service shares this same control DB pool
    # rather than opening its own — one control DB, one pool.
    pool = state.connector_manager._pool

    state.logging_service = LoggingService(pool)
    await state.logging_service.ensure_table()

    state.account_service = AccountService(pool)
    await state.account_service.ensure_tables()

    state.llm_credential_service = LLMCredentialService(pool)
    await state.llm_credential_service.ensure_tables()

    state.llm_service = LLMService(state.llm_credential_service)

    state.metering_service = MeteringService(pool)
    await state.metering_service.ensure_tables()

    state.storage_service = StorageService()

    state.context_service = ContextService(pool)
    await state.context_service.ensure_tables()

    await state.logging_service.log(
        category=LogCategory.SERVER, level=LogLevel.INFO,
        message="FRIS backend started successfully",
        source="main.lifespan",
        metadata={"embedding_model": settings.EMBEDDING_MODEL_NAME, "embedding_load_seconds": embed_duration}
    )

    print("FRIS backend started — fusion engine, connector manager, embeddings, accounts, "
          "LLM layer, metering, and logging ready")
    yield

    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.SERVER, level=LogLevel.INFO,
            message="FRIS backend shutting down", source="main.lifespan"
        )
    await state.connector_manager.close()
    await state.fusion_engine.close()


app = FastAPI(
    title="FRIS API",
    description="Federated Real-time Intelligence System — connector and fusion engine API",
    version="0.2.0",
    lifespan=lifespan,
    # Served under /api/* (not the FastAPI default /openapi.json) so the
    # frontend's existing /api proxy can reach it, and so the in-console
    # Documentation Center's API Reference always reflects the real,
    # currently-running endpoint set rather than a hand-maintained copy.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auto_log_requests(request, call_next):
    """
    Every inbound request from the frontend console gets logged automatically
    here — this is the CONSOLE category. No route needs to remember to log
    anything manually for basic request/response tracking.
    """
    import time
    start = time.monotonic()
    try:
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        if state.logging_service:
            level = LogLevel.ERROR if response.status_code >= 500 else (
                LogLevel.WARNING if response.status_code >= 400 else LogLevel.INFO
            )
            await state.logging_service.log(
                category=LogCategory.CONSOLE, level=level,
                message=f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)",
                source="api.middleware",
                metadata={"method": request.method, "path": request.url.path,
                          "status": response.status_code, "duration_ms": duration_ms}
            )
        return response
    except Exception as e:
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        if state.logging_service:
            await state.logging_service.log(
                category=LogCategory.CONSOLE, level=LogLevel.ERROR,
                message=f"{request.method} {request.url.path} -> unhandled exception: {e}",
                source="api.middleware",
                metadata={"method": request.method, "path": request.url.path,
                          "duration_ms": duration_ms, "error": str(e)}
            )
        raise


app.include_router(auth_router)
app.include_router(graph_router)
app.include_router(query_router)
app.include_router(providers_router)
app.include_router(oauth_router)
app.include_router(billing_router)
app.include_router(context_router)
app.include_router(graphql_router, prefix="/api/graphql")
app.include_router(mcp_router)
app.include_router(uploads_router)


# ============================================================
# Connector Catalog — what the sidebar lists
# ============================================================

@app.get("/api/connectors/available")
async def list_available_connectors():
    """
    Returns every implemented connector type, including setup docs.
    The Connectors catalog modal renders directly from this.
    """
    return {"connectors": ConnectorRegistry.list_available()}


# ============================================================
# Connector Instances — user's configured data sources
# ============================================================

@app.get("/api/connectors")
async def list_connectors(account: dict = Depends(get_current_account)):
    """List all connectors this tenant has configured."""
    configs = await state.connector_manager.list_connectors(organization_id=account["organization_id"])
    return {
        "connectors": [
            {
                "connector_id": c.connector_id,
                "connector_type": c.connector_type.value,
                "name": c.name,
                "status": c.status.value,
                "created_at": c.created_at,
                "last_synced": c.last_synced,
                "total_records_ingested": c.total_records_ingested,
                "error_message": c.error_message,
                "has_schema": c.schema is not None,
                "schema_confirmed": c.schema.user_confirmed if c.schema else False,
                "schema_drift": c.schema_drift
            }
            for c in configs
        ]
    }


@app.post("/api/connectors")
async def create_connector(request: CreateConnectorRequest, account: dict = Depends(get_current_account)):
    """
    Step 1: Create a new connector instance.
    User picks a type from the sidebar, fills in config (URL, credentials, etc.)
    """
    if not ConnectorRegistry.is_implemented(request.connector_type):
        raise HTTPException(
            status_code=400,
            detail=f"Connector type '{request.connector_type}' is not yet implemented. "
                   f"Use type 'custom' to define your own, or request this connector."
        )

    cfg = await state.connector_manager.create_connector(
        connector_type=request.connector_type,
        name=request.name,
        config=request.config,
        account_id=str(account["id"]),
        organization_id=account["organization_id"]
    )
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.APP, level=LogLevel.INFO,
            message=f"Connector created: '{request.name}' ({request.connector_type})",
            source="connectors.create", connector_id=cfg.connector_id,
            organization_id=account["organization_id"]
        )
    return {
        "connector_id": cfg.connector_id,
        "connector_type": cfg.connector_type.value,
        "name": cfg.name,
        "status": cfg.status.value
    }


@app.get("/api/connectors/{connector_id}")
async def get_connector(connector_id: str, account: dict = Depends(get_current_account)):
    cfg = await state.connector_manager.get_connector(connector_id, organization_id=account["organization_id"])
    if not cfg:
        raise HTTPException(status_code=404, detail="Connector not found")
    return {
        "connector_id": cfg.connector_id,
        "connector_type": cfg.connector_type.value,
        "name": cfg.name,
        "config": cfg.config,
        "status": cfg.status.value,
        "created_at": cfg.created_at,
        "last_synced": cfg.last_synced,
        "total_records_ingested": cfg.total_records_ingested,
        "error_message": cfg.error_message,
        "schema": state.connector_manager._schema_to_dict(cfg.schema) if cfg.schema else None,
        "schema_drift": cfg.schema_drift
    }


@app.delete("/api/connectors/{connector_id}")
async def delete_connector(connector_id: str, account: dict = Depends(require_role("owner", "admin"))):
    await state.connector_manager.delete_connector(connector_id, organization_id=account["organization_id"])
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.APP, level=LogLevel.WARNING,
            message=f"Connector deleted: {connector_id}",
            source="connectors.delete", connector_id=connector_id,
            organization_id=account["organization_id"]
        )
    return {"success": True}


# ============================================================
# Connector Lifecycle: test -> detect schema -> confirm -> sync
# ============================================================

@app.post("/api/connectors/{connector_id}/test")
async def test_connector(connector_id: str, account: dict = Depends(get_current_account)):
    """Step 2: Test the connection works before pulling any real data."""
    result = await state.connector_manager.test_connector(connector_id, organization_id=account["organization_id"])
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.NETWORK,
            level=LogLevel.INFO if result.get("success") else LogLevel.ERROR,
            message=f"Connection test for {connector_id}: {'succeeded' if result.get('success') else result.get('error', 'failed')}",
            source="connectors.test", connector_id=connector_id, metadata=result,
            organization_id=account["organization_id"]
        )
    return result


@app.post("/api/connectors/{connector_id}/detect-schema")
async def detect_schema(connector_id: str, account: dict = Depends(get_current_account)):
    """
    Step 3: Auto-detect schema from a data sample.
    Returns detected fields with suggested names/types for user review.
    """
    try:
        schema = await state.connector_manager.detect_schema(connector_id, organization_id=account["organization_id"])
        return {"success": True, "schema": schema}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/connectors/{connector_id}/confirm-schema")
async def confirm_schema(connector_id: str, request: ConfirmSchemaRequest, account: dict = Depends(get_current_account)):
    """
    Step 4: User reviews auto-detected fields and confirms/edits them.
    This is the "user confirms, can change/update/add" step you asked for.
    """
    try:
        result = await state.connector_manager.confirm_schema(
            connector_id=connector_id,
            field_updates=[f.model_dump() for f in request.field_updates],
            entity_type_override=request.entity_type_override,
            organization_id=account["organization_id"]
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/connectors/{connector_id}/sync", response_model=SyncResponse)
async def sync_connector(connector_id: str, account: dict = Depends(get_current_account)):
    """
    Step 5: Run the full sync — fetch, normalize, fuse into the graph.
    Can be called manually ("Sync now" button) or by a scheduler.
    """
    if state.logging_service:
        await state.logging_service.log(
            category=LogCategory.NETWORK, level=LogLevel.INFO,
            message=f"Sync started for connector {connector_id}: fetching from source",
            source="connectors.sync", connector_id=connector_id,
            organization_id=account["organization_id"]
        )
    result = await state.connector_manager.sync_connector(connector_id, organization_id=account["organization_id"])
    if state.logging_service:
        success = getattr(result, "success", None)
        if success is None and isinstance(result, dict):
            success = result.get("success")
        records = getattr(result, "records_processed", None)
        if records is None and isinstance(result, dict):
            records = result.get("records_processed")
        await state.logging_service.log(
            category=LogCategory.DATA_PROCESSING,
            level=LogLevel.INFO if success else LogLevel.ERROR,
            message=f"Fusion sync completed for {connector_id}: {records or 0} records processed",
            source="fusion_engine.sync", connector_id=connector_id,
            metadata={"records_processed": records, "success": success},
            organization_id=account["organization_id"]
        )

        drift = result.get("schema_drift") if isinstance(result, dict) else None
        if drift:
            await state.logging_service.log(
                category=LogCategory.SCHEMA_DRIFT, level=LogLevel.WARNING,
                message=f"Schema drift detected on connector {connector_id}: "
                        f"{len(drift.get('new_fields', []))} new field(s), "
                        f"{len(drift.get('missing_fields', []))} missing field(s)",
                source="connectors.sync", connector_id=connector_id,
                metadata=drift, organization_id=account["organization_id"]
            )

    # Metering: ingestion + embedding events, attributed to whoever owns the connector.
    if isinstance(result, dict) and result.get("success"):
        account_id = await state.connector_manager.get_connector_account_id(connector_id)
        if account_id:
            records_fetched = result.get("records_fetched", 0)
            fusion_result = result.get("fusion_result", {})
            entities_touched = fusion_result.get("entities_created", 0) + fusion_result.get("entities_merged", 0)
            await state.metering_service.record(account_id, EventType.INGESTION, quantity=records_fetched)
            if entities_touched:
                await state.metering_service.record(account_id, EventType.EMBEDDING, quantity=entities_touched)

    return result


@app.get("/api/connectors/{connector_id}/sync-history")
async def sync_history(connector_id: str, limit: int = 20, account: dict = Depends(get_current_account)):
    history = await state.connector_manager.get_sync_history(
        connector_id, limit=limit, organization_id=account["organization_id"]
    )
    return {"history": history}


# ============================================================
# Custom Connector Helper
# ============================================================

@app.get("/api/connectors/custom/template")
async def get_custom_connector_template():
    """Returns the starter code template for advanced custom connectors."""
    from connectors.custom import CustomConnector
    return {"template": CustomConnector.FETCH_CODE_TEMPLATE}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fris-api"}


# ============================================================
# Logs — audit trail for the admin team
# Every server, network, console, app, and data_processing event,
# auto-logged and persisted to Postgres. Filterable from the UI.
# ============================================================

@app.get("/api/logs")
async def get_logs(
    category: Optional[str] = None,
    level: Optional[str] = None,
    connector_id: Optional[str] = None,
    search: Optional[str] = None,
    since_hours: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
    account: dict = Depends(get_current_account)
):
    from datetime import datetime, timedelta, timezone
    since = None
    if since_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return await state.logging_service.query(
        category=category, level=level, connector_id=connector_id,
        search=search, since=since, limit=limit, offset=offset,
        organization_id=account["organization_id"]
    )


@app.get("/api/logs/counts")
async def get_log_counts(account: dict = Depends(get_current_account)):
    """Powers the filter chip badges (category counts, level counts) in the Logs page — this tenant only."""
    categories = await state.logging_service.get_category_counts(organization_id=account["organization_id"])
    levels = await state.logging_service.get_level_counts(organization_id=account["organization_id"])
    return {"categories": categories, "levels": levels}


@app.delete("/api/logs/purge")
async def purge_logs(older_than_days: int = 30, account: dict = Depends(require_role("owner", "admin"))):
    """Admin action: delete logs older than N days for this tenant. Default 30."""
    deleted = await state.logging_service.purge_older_than(older_than_days, organization_id=account["organization_id"])
    await state.logging_service.log(
        category=LogCategory.APP, level=LogLevel.WARNING,
        message=f"Log purge: removed {deleted} entries older than {older_than_days} days",
        organization_id=account["organization_id"],
        source="logs.purge"
    )
    return {"deleted": deleted}
