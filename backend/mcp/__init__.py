"""StockAI's MCP server: the read-only half of the public API, for AI clients.

Two surfaces, one catalogue. `catalog.py` declares the tools; `protocol.py`
speaks JSON-RPC over the Streamable HTTP transport; `backend/api/v1/mcp.py`
mounts it at `POST /api/v1/mcp` behind the same `sk_live_*` key, the same rate
limit and the same tenant scoping as every other public endpoint.

The stdio adapter a customer runs next to Claude Desktop (`mcp_server/`) does
not reimplement any of this: it forwards frames to that endpoint. There is one
catalogue, and it is here.
"""
