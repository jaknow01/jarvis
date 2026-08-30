"""scheduler_agent feature package.

Public surface: ``create_scheduler_agent`` plus the conditional-watch infrastructure
(source registry + predicate evaluator/sandbox client). The watch modules live here — not
in ``runtime`` — because the source registry reuses other agents' fetchers (agents->agents)
and is consumed by both the ``create_watch`` tool (agents) and the scheduler runner
(runtime->agents). The runner imports these via this package surface.
"""

from lib.agents.scheduler_agent import tools as tools  # noqa: F401  (registration side effect)
from lib.agents.scheduler_agent.agent import create_scheduler_agent
from lib.agents.scheduler_agent.watch_sources import (
    fetch_source,
    register_source,
    is_known,
    known_sources,
)
from lib.agents.scheduler_agent.watch_evaluator import (
    PredicateEvaluator,
    StubEvaluator,
    default_evaluator,
)
from lib.agents.scheduler_agent.watch_sandbox_client import (
    SandboxClient,
    SandboxError,
    resolve_evaluator,
)

__all__ = [
    "create_scheduler_agent",
    "fetch_source",
    "register_source",
    "is_known",
    "known_sources",
    "PredicateEvaluator",
    "StubEvaluator",
    "default_evaluator",
    "SandboxClient",
    "SandboxError",
    "resolve_evaluator",
]
