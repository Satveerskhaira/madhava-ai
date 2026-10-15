# Framework notes (verified October 2026)

Versions verified working together: `google-adk[a2a]` 2.12, `a2a-sdk` 1.2.2, `mcp` 2.3, `cel-python` 0.5, Python 3.12/3.13. These APIs move quickly; if a version differs, probe it first (`inspect.signature`, a small script) before writing code against it.

## Google ADK 2: workflow graphs

```python
from google.adk.workflow import START, Workflow
from google.adk.events.event import Event
from google.adk.runners import InMemoryRunner

async def intake(ctx, node_input):            # first node gets the user Content as node_input
    req = ctx.state.get("request") or parse(node_input)   # or pass state_delta={"request": ...} to run_async
    return Event(state={"bb": bb.model_dump(mode="json")}, output="observed")

async def verify(ctx):
    bb = Blackboard.model_validate(ctx.state["bb"])
    route = "gap" if ... else "done"
    return Event(state={"bb": ...}, route=route, output={"route": route})

async def remember(ctx):
    return Event(output=explanation.model_dump(mode="json"), message=explanation.answer)   # message -> text content

wf = Workflow(name="planner", edges=[
    (START, intake, understand, plan, act, verify),
    (verify, {"gap": act, "done": synthesize}),         # routing map; back edges (cycles) are allowed
    (synthesize, remember),
])
runner = InMemoryRunner(node=wf, app_name="app")        # note: node=, not agent=
session = await runner.session_service.create_session(app_name="app", user_id="u")
async for ev in runner.run_async(user_id="u", session_id=session.id, state_delta={"request": {...}}):
    ev.output, ev.actions.route
```

- `Event(state=..., route=..., message=...)` are shorthand for `actions.state_delta`, `actions.route` and `content`.
- Function nodes bind parameters from state by default; use the `ctx` (and `node_input`) parameters and read `ctx.state` directly.
- Keep state JSON-serialisable (pydantic `model_dump(mode="json")`).
- `google.adk.agents` still offers `SequentialAgent`, `ParallelAgent`, `LoopAgent` and `LlmAgent`. Use the Workflow when you need routed edges and bounded loops.

## Domain agents as ADK agents over A2A

```python
from google.adk.agents import BaseAgent
from pydantic import ConfigDict

class AdkDomainAgent(BaseAgent):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    core: Any = None
    async def _run_async_impl(self, ctx):
        text = "".join(p.text or "" for p in ctx.user_content.parts)
        report = await self.core.handle(DomainTask.model_validate_json(text))
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=report.model_dump_json())]))

from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.protobuf.json_format import ParseDict
from a2a.types.a2a_pb2 import AgentCard
card = ParseDict(card_dict, AgentCard(), ignore_unknown_fields=True)
app = to_a2a(agent, host="127.0.0.1", port=8101, agent_card=card)   # Starlette app; run with uvicorn
```

- The card is served at `/.well-known/agent-card.json` (not `/.well-known/agent.json`).
- A2A 1.0 card JSON uses `supportedInterfaces: [{url, protocolBinding: "JSONRPC", protocolVersion: "1.0"}]`, plus `skills: [{id, name, description, tags, examples}]`.
- **Origin check:** clients reject a card whose RPC URL origin differs from where it was fetched. Pass the same `host` to `to_a2a` that clients use (`127.0.0.1` vs `localhost` matters).

## A2A client (a2a-sdk 1.x, protobuf types)

```python
from a2a.client import create_client
from a2a.helpers import new_text_message, get_stream_response_text
from a2a.types.a2a_pb2 import SendMessageRequest, ROLE_USER

client = create_client("http://127.0.0.1:8101")      # may be sync or awaitable; handle both
msg = new_text_message(task.model_dump_json(), role=ROLE_USER)
async for resp in client.send_message(SendMessageRequest(message=msg)):
    kind = resp.WhichOneof("payload")                 # task | status_update | artifact_update | message
    text = get_stream_response_text(resp)             # take the artifact_update text as the result
```

`RemoteA2aAgent(name=..., agent_card="http://.../.well-known/agent-card.json")` also works inside an ADK runner, but it emits the result twice (status and artifact). The direct client is simpler for fan-out.

## MCP 2.x

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

A tool returning a list arrives as `structured_content` `{"result": [...]}`; a dict arrives as the dict.

## CEL (cel-python 0.5)

```python
import celpy
env = celpy.Environment()
prog = env.program(env.compile(expr))                # compile once per rule version; cache by expression
result = prog.evaluate({"record": celpy.json_to_cel(record_map), "reference": celpy.json_to_cel(reference_map)})
```

- Comparing with `None` raises `CELEvalError` (`no such overload`). Omit missing values from the maps and guard with `has()`.
- `has(a.b.c)` needs the map `a.b` to exist: build every intermediate map for declared paths, even when the leaf is missing.
- Integers and doubles do not mix (`int * double` fails); wrap numeric inputs in `double()`.
- `evaluate` may raise or return a `CELEvalError`; handle both.
- Macros `exists`, `all`, `has` and `size()` work; there is no `min`/`max`, so use the ternary `a < b ? a : b`.

## Models (optional)

`google.adk.models.LLMRegistry.new_llm("gemini-2.5-flash")` returns a `BaseLlm`. Other providers go through `google.adk.models.lite_llm.LiteLlm`, which needs `google-adk[extensions]`. Call it with `generate_content_async(LlmRequest(model=..., contents=[...], config=GenerateContentConfig(system_instruction=..., temperature=0)))`.

For tests, subclass `BaseLlm`, implement `generate_content_async` to yield `LlmResponse(content=...)`, and construct it with `model="stub"`.
