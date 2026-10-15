# SDK adapters

The kernel has no agent SDK in its core. Adapters live at the edges, and there are only three seams:

| Seam | Kernel type | What an SDK plugs in |
|---|---|---|
| Expose an agent | `await planner.ask(question, record_id, session_id=..., rules_mode=...) -> Explanation` | a tool, node, agent or server that calls `ask` and returns `exp.answer` or `exp.model_dump()` |
| Use a model for understanding and phrasing | `ModelLanguage(taxonomy, intents, complete)` where `complete: async (system: str, user: str) -> str` | any model client |
| Swap an executor | `Executor`: `name` + `async handle(DomainTask) -> DomainReport` | a remote client, or an SDK agent wrapped to take and return those models (JSON via pydantic) |

Verified API details and pitfalls for the built adapters are in `adapters/google-adk.md` and `adapters/mcp.md`; the other SDK patterns below are untested sketches until their adapters are built.

Rules for every adapter:
- Put it in an optional module or extra; nothing in the pack, record adapter or kernel core imports it.
- Keep the deterministic path working with no model (tests and evals run without one).
- The model call never decides outcomes; `ModelLanguage` validates its output against the taxonomy and intents and falls back to deterministic understanding.
- Core to domain agents stays in process unless there is a deployment reason; then wrap a client as an `Executor`.

## Google ADK (implemented, tested): `rulegraph[adk]`

```python
from rulegraph.adapters.adk import build_workflow, a2a_app, adk_completion, AdkExecutor, parse_request

workflow = build_workflow(system.core)           # START -> intake -> understand -> plan -> act -> verify
                                                 #   --gap--> act | --done--> synthesize -> remember
runner = InMemoryRunner(node=workflow, app_name="app")
# state_delta={"request": {"record_id": "ORD-1", "question": "Why?"}}

app = a2a_app(system.core, host="0.0.0.0", port=8100)   # A2A server + agent card from the taxonomy
language = ModelLanguage(kn.taxonomy, kn.procedures.intents, adk_completion("gemini-2.5-flash"))
```
`build_workflow` works for a domain agent too. `AdkExecutor(executor)` wraps any executor as an ADK `BaseAgent` that takes a DomainTask JSON message, for ADK-native hierarchies. `parse_request` accepts `"ORD-1: why?"` or JSON.

## MCP (implemented, tested): `rulegraph[mcp]`

```python
from rulegraph.adapters.mcp import build_mcp_server
server = build_mcp_server(toolbox, planner=system.core, name="orders", record_noun="order", reference_noun="terms")
server.run("stdio")      # or "streamable-http"
```
Tools: `get_<record>_outcome`, `get_<record>_fields`, `get_<reference>_slice`, `search_rules`, `get_rule`, `list_rule_history`, `diff_rule_sets`, `lookup_concept`, `search_knowledge`, and `explain_<record>` when a planner is given. Any MCP-capable agent (Claude, ADK, OpenAI Agents SDK, LangGraph) can then use the system as tools.

## Patterns for other SDKs (write and test in the project that needs them)

Write each as `adapters/<sdk>.py` in the project (or contribute to the kernel as an extra) with one test that runs a golden through it.

### LangGraph
Either one node calling `ask`, or mirror the planner steps as nodes, as the ADK workflow does:
```python
from langgraph.graph import StateGraph, START, END
async def explain(state):           # state: {"record_id", "question"}
    exp = await planner.ask(state["question"], state["record_id"])
    return {"answer": exp.answer, "explanation": exp.model_dump(mode="json")}
g = StateGraph(dict); g.add_node("explain", explain); g.add_edge(START, "explain"); g.add_edge("explain", END)
```
For a step graph: `bb = Blackboard(...)`, nodes `planner.observe/understand/plan/act`, a conditional edge on `await planner.verify(bb)` ("gap" -> reflect + act, "done" -> synthesize), keeping `bb` in state. Model: `complete` wraps `ChatModel.ainvoke([SystemMessage, HumanMessage]).content`.

### OpenAI Agents SDK
```python
from agents import Agent, function_tool
@function_tool
async def explain_record(record_id: str, question: str) -> str:
    return (await planner.ask(question, record_id)).answer
assistant = Agent(name="explainer", instructions="Use explain_record; do not invent reasons.", tools=[explain_record])
```
Model: `complete` calls the Responses or Chat Completions API with `system` and `user`.

### Claude Agent SDK
Expose via the MCP adapter (stdio or HTTP) and add the server to the agent's MCP config, or define an in-process tool:
```python
from claude_agent_sdk import tool, create_sdk_mcp_server
@tool("explain_record", "Explain why a record was decided as it was", {"record_id": str, "question": str})
async def explain_record(args):
    exp = await planner.ask(args["question"], args["record_id"])
    return {"content": [{"type": "text", "text": exp.answer}]}
server = create_sdk_mcp_server(name="explainer", tools=[explain_record])
```
Model: `complete` uses the Anthropic Messages API (`system=system, messages=[{"role": "user", "content": user}]`).

### CrewAI, Semantic Kernel, AutoGen and others
Same shape: register an async tool/function/plugin that calls `planner.ask`, and supply `complete` from the SDK's model client. If the SDK only supports sync tools, run `asyncio.run(planner.ask(...))` in a worker thread.

### Other transports between agents
If domain agents must run as separate services, write a client:
```python
class RemoteAgent:
    def __init__(self, name, url): self.name, self.url = name, url
    async def handle(self, task: DomainTask) -> DomainReport:
        r = await httpx.AsyncClient().post(f"{self.url}/tasks", json=task.model_dump(mode="json"))
        return DomainReport.model_validate(r.json())
core = core_planner(kn, toolbox, {n: RemoteAgent(n, url) for n, url in urls.items()})
```
Serve the domain agent with any web framework calling `agent.handle(DomainTask.model_validate(body))`. Keep the core's per-task deadline.
