"""Load and validate ``config/subagents.yaml``."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from lib.core import IDENTITY_MODES

_ALLOWED = {"description", "url_env", "key_env", "identity", "persona", "max_calls_per_turn",
            "uds_env", "admin_key_env", "admin_command", "sensitive"}
_COMMAND_RE = __import__("re").compile(r"^[a-z][a-z0-9_-]{0,15}$")


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
    uds_env: Optional[str] = None        # reach the server over this Unix socket (sidecar)
    admin_key_env: Optional[str] = None  # the server's admin key — `/` commands only, never the agent
    admin_command: Optional[str] = None  # `/<command> <tool> [args]` calls the server's admin tools
    sensitive: bool = False              # its data stays out of logs, stored history and traces


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
        required = ("description", "key_env") if entry.get("uds_env") else ("description", "url_env", "key_env")
        for req in required:
            if not isinstance(entry.get(req), str) or not entry[req].strip():
                raise SubagentsConfigError(f"{name}: '{req}' is required")
        for opt in ("url_env", "uds_env", "admin_key_env", "admin_command"):
            if entry.get(opt) is not None and (not isinstance(entry[opt], str) or not entry[opt].strip()):
                raise SubagentsConfigError(f"{name}: '{opt}' must be a non-empty string")
        if entry.get("admin_key_env") and entry.get("admin_key_env") == entry.get("key_env"):
            raise SubagentsConfigError(f"{name}: admin_key_env must differ from key_env (the agent's key)")
        command = entry.get("admin_command")
        if command is not None and (not entry.get("admin_key_env") or not _COMMAND_RE.match(command)):
            raise SubagentsConfigError(f"{name}: admin_command needs admin_key_env and a short lowercase name")
        if not isinstance(entry.get("sensitive", False), bool):
            raise SubagentsConfigError(f"{name}: sensitive must be true/false")
        identity = entry.get("identity", "none")
        if identity not in IDENTITY_MODES:
            raise SubagentsConfigError(f"{name}: identity must be one of {IDENTITY_MODES}")
        budgets = entry.get("max_calls_per_turn") or {}
        if not isinstance(budgets, dict) or not all(
                isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in budgets.items()):
            raise SubagentsConfigError(f"{name}: max_calls_per_turn must map tool names to positive ints")
        out.append(SubagentConfig(name=str(name), description=entry["description"].strip(),
                                  url_env=entry.get("url_env") or "", key_env=entry["key_env"], identity=identity,
                                  persona=str(entry.get("persona") or "").strip(),
                                  max_calls_per_turn=dict(budgets), uds_env=entry.get("uds_env"),
                                  admin_key_env=entry.get("admin_key_env"),
                                  admin_command=command, sensitive=bool(entry.get("sensitive", False))))
    commands = [c.admin_command for c in out if c.admin_command]
    if len(commands) != len(set(commands)):
        raise SubagentsConfigError("admin_command names must be unique")
    return out


def load_subagents(path: Optional[Path] = None) -> list[SubagentConfig]:
    path = path or subagents_path()
    if not path.exists():
        return []
    return parse_subagents(yaml.safe_load(path.read_text(encoding="utf-8")))
