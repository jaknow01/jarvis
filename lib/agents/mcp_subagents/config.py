"""Load and validate ``config/subagents.yaml``."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from lib.core import IDENTITY_MODES

_ALLOWED = {"description", "url_env", "key_env", "identity", "persona", "max_calls_per_turn"}


class SubagentsConfigError(ValueError):
    """config/subagents.yaml is malformed."""


@dataclass(frozen=True)
class SubagentConfig:
    name: str
    description: str
    url_env: str
    key_env: str
    identity: str = "none"
    persona: str = ""
    max_calls_per_turn: dict[str, int] = field(default_factory=dict)


def subagents_path() -> Path:
    return Path(os.getenv("SUBAGENTS_CONFIG") or "config/subagents.yaml")


def parse_subagents(raw: Any) -> list[SubagentConfig]:
    if raw is None:
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("subagents") or {}, dict):
        raise SubagentsConfigError("expected a top-level 'subagents:' mapping")
    out = []
    for name, entry in (raw.get("subagents") or {}).items():
        if not isinstance(entry, dict):
            raise SubagentsConfigError(f"{name}: expected a mapping")
        unknown = set(entry) - _ALLOWED
        if unknown:
            raise SubagentsConfigError(f"{name}: unknown keys {sorted(unknown)}")
        for req in ("description", "url_env", "key_env"):
            if not isinstance(entry.get(req), str) or not entry[req].strip():
                raise SubagentsConfigError(f"{name}: '{req}' is required")
        identity = entry.get("identity", "none")
        if identity not in IDENTITY_MODES:
            raise SubagentsConfigError(f"{name}: identity must be one of {IDENTITY_MODES}")
        budgets = entry.get("max_calls_per_turn") or {}
        if not isinstance(budgets, dict) or not all(
                isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in budgets.items()):
            raise SubagentsConfigError(f"{name}: max_calls_per_turn must map tool names to positive ints")
        out.append(SubagentConfig(name=str(name), description=entry["description"].strip(),
                                  url_env=entry["url_env"], key_env=entry["key_env"], identity=identity,
                                  persona=str(entry.get("persona") or "").strip(),
                                  max_calls_per_turn=dict(budgets)))
    return out


def load_subagents(path: Optional[Path] = None) -> list[SubagentConfig]:
    path = path or subagents_path()
    if not path.exists():
        return []
    return parse_subagents(yaml.safe_load(path.read_text(encoding="utf-8")))
