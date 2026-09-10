"""coordinator feature package. Public surface: create_coordinator_agent.

The coordinator is the network root: it wires every enabled subagent in as a tool and
hands off to the composer for the final reply. Importing it pulls in all sibling
feature packages (registration side effects)."""

from lib.agents.coordinator.agent import create_coordinator_agent, subagent_names

__all__ = ["create_coordinator_agent", "subagent_names"]
