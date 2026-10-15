"""rulegraph: an SDK-agnostic kernel for agents that explain decisions.

A planner routes a question through a taxonomy to executors and checks every
observed outcome is explained. Executors are domain agents (planners scoped to
one taxonomy branch) or rule-domain investigators that search a rules/policy
graph, read only the record and reference fields their rules need, evaluate
CEL rule cards deterministically and return findings with evidence.

Everything domain-specific lives in a knowledge pack (pack.yaml) plus a small
RecordAdapter. Agent SDKs and protocols (Google ADK, A2A, MCP, others) plug in
through rulegraph.adapters and are never imported by the kernel itself.
"""

from .agent.factory import core_planner, domain_agent, domain_agents
from .agent.investigator import Investigator
from .agent.language import DeterministicLanguage, ModelLanguage
from .agent.planner import Executor, Planner
from .knowledge import Knowledge
from .settings import Settings
from .tools.toolbox import RecordAdapter, RecordView, Toolbox

__version__ = "0.1.0"
__all__ = [
    "Executor", "Planner", "Investigator", "Knowledge", "Settings", "Toolbox", "RecordAdapter", "RecordView",
    "DeterministicLanguage", "ModelLanguage", "core_planner", "domain_agent", "domain_agents",
]
