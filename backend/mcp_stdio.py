"""
FRIS MCP stdio bridge (optional) — for desktop MCP clients (e.g. Claude Desktop)
that speak the stdio transport rather than HTTP.

This is a thin proxy: it advertises the same FRIS tool catalog (core/mcp_tools.py)
over stdio, and forwards every tool call to the running FRIS backend's HTTP MCP
endpoint (api/mcp_server.py) using an API key. All tenant scoping and auth stay
on the server — this process holds only an API key, never a DB connection — so
the stdio surface is exactly as tenant-isolated as the HTTP one.

Run:  FRIS_MCP_URL=http://localhost:8000/api/mcp \
      FRIS_API_KEY=<your-api-key> \
      python mcp_stdio.py

Register in a desktop MCP client's config as a stdio server invoking that command.
Requires the optional `mcp` package (see requirements.txt); if it isn't installed,
this script prints an install hint and exits (the FRIS backend itself does not
depend on it).
"""

import asyncio
import json
import os
import sys

import httpx

from core.mcp_tools import TOOL_DEFINITIONS

FRIS_MCP_URL = os.environ.get("FRIS_MCP_URL", "http://localhost:8000/api/mcp")
FRIS_API_KEY = os.environ.get("FRIS_API_KEY", "")


async def _forward_call(name: str, arguments: dict) -> str:
    """POST a tools/call to the FRIS HTTP MCP endpoint and return the text content."""
    if not FRIS_API_KEY:
        return "FRIS_API_KEY is not set — cannot authenticate to the FRIS backend."
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(FRIS_MCP_URL, json=body, headers={"X-API-Key": FRIS_API_KEY})
    if resp.status_code >= 400:
        return f"FRIS backend returned HTTP {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    if "error" in data:
        return f"MCP error: {data['error'].get('message', data['error'])}"
    content = data.get("result", {}).get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(data.get("result", {}))


async def _run():
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types
    except ImportError:
        print(
            "The 'mcp' package is not installed. Install it to use the stdio bridge:\n"
            "    pip install 'mcp>=1.2.0,<2.0.0'",
            file=sys.stderr,
        )
        sys.exit(1)

    server = Server("fris-intelligence")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
            for t in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        text = await _forward_call(name, arguments or {})
        return [types.TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_run())
