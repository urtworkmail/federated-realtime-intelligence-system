"""
FRIS MCP (Model Context Protocol) server — HTTP transport.

Exposes the tenant-scoped FRIS read surface (core/mcp_tools.py) to agentic
MCP clients over a single JSON-RPC 2.0 endpoint at POST /api/mcp, matching
MCP's Streamable-HTTP request/response shape for stateless tool calls.

Why HTTP JSON-RPC rather than the stdio SDK transport: FRIS is a hosted,
multi-tenant service. Each request carries its own tenant identity via the
standard X-API-Key header (or a Bearer session), authenticated by the very
same get_current_account dependency every REST and GraphQL request uses — so
there is no second, weaker-authed surface, and the shared, already-connected
fusion_engine / Neo4j pool is reused rather than re-established per client.
Desktop MCP clients that need stdio can front this with the thin bridge in
mcp_stdio.py.

Implemented MCP methods: initialize, ping, tools/list, tools/call, plus the
notifications/initialized notification. Only read tools are exposed.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import get_current_account
from core import mcp_tools

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# MCP protocol version this server implements. Echoed back on initialize unless
# the client requests a version we recognize.
SUPPORTED_PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {"name": "fris-intelligence", "version": "0.2.0"}


def _rpc_error(request_id: Any, code: int, message: str, http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def _rpc_result(request_id: Any, result: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


@router.get("")
async def mcp_descriptor(account: dict = Depends(get_current_account)):
    """
    Lightweight, authenticated descriptor of this MCP server — handy for humans
    and for clients that probe with GET before opening a JSON-RPC session.
    """
    return {
        "protocol": "mcp",
        "transport": "streamable-http-jsonrpc",
        "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "endpoint": "/api/mcp",
        "auth": "X-API-Key header (programmatic) or Bearer session token",
        "tools": [t["name"] for t in mcp_tools.list_tools()],
    }


@router.post("")
async def mcp_rpc(payload: Dict[str, Any], account: dict = Depends(get_current_account)):
    """
    Single JSON-RPC 2.0 entrypoint for the MCP protocol. Auth (and therefore the
    tenant boundary) is enforced by get_current_account before any method runs.
    """
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _rpc_error(payload.get("id") if isinstance(payload, dict) else None,
                          -32600, "Invalid Request: expected JSON-RPC 2.0")

    method = payload.get("method")
    request_id = payload.get("id")
    params = payload.get("params") or {}

    # Notifications (no id) — acknowledge with 202 and no body, per JSON-RPC.
    if request_id is None:
        return JSONResponse(status_code=202, content=None)

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _rpc_result(request_id, {
            "protocolVersion": requested or SUPPORTED_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })

    if method == "ping":
        return _rpc_result(request_id, {})

    if method == "tools/list":
        return _rpc_result(request_id, {"tools": mcp_tools.list_tools()})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return _rpc_error(request_id, -32602, "Invalid params: 'name' is required")
        try:
            result = await mcp_tools.call_tool(account, tool_name, arguments)
        except mcp_tools.MCPToolError as e:
            # Tool-level failures are returned as an MCP tool result with isError,
            # not a JSON-RPC protocol error, so the model can read and react to them.
            return _rpc_result(request_id, {
                "content": [{"type": "text", "text": str(e)}],
                "isError": True,
            })
        except Exception as e:  # noqa: BLE001 — surface unexpected failures as tool errors, not 500s
            return _rpc_result(request_id, {
                "content": [{"type": "text", "text": f"Tool execution failed: {e}"}],
                "isError": True,
            })

        import json
        return _rpc_result(request_id, {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "structuredContent": result,
            "isError": False,
        })

    return _rpc_error(request_id, -32601, f"Method not found: {method}")
