"""composer feature package. Public surface: create_composer_agent.

The composer is the coordinator's handoff target — a tool-less agent that writes the
final user-facing reply (in Polish) from the data the subagents gathered."""

from lib.agents.composer.agent import create_composer_agent

__all__ = ["create_composer_agent"]
