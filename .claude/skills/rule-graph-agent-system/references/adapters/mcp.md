# Adapter notes: MCP (verified October 2026)

Read only when using or changing `rulegraph.adapters.mcp` (install `rulegraph[mcp]`). Verified with `mcp` 2.3.

```python
from mcp.server.mcpserver import MCPServer           # FastMCP was renamed in mcp 2.x
server = MCPServer("name", instructions="...")

@server.tool(description="...")
def get_record_outcome(record_id: str) -> dict: ...

server.run("stdio")                                  # or "streamable-http", host=..., port=...

# in-memory testing
from mcp.client import Client
async with Client(server) as c:
    (await c.list_tools()).tools
    r = await c.call_tool("get_record_outcome", {"record_id": "REC-1"}); r.structured_content
```

- A tool returning a list arrives as `structured_content` `{"result": [...]}`; a dict arrives as the dict.
- Tool names use the pack nouns (`get_claim_outcome`, `get_order_outcome`), so name them from `pack.yaml`, not in code.
- Any MCP-capable agent or SDK (Claude, ADK, OpenAI Agents SDK, LangGraph) can use the system through this server without its own adapter.
