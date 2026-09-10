"""Declarative principals config: a hand-editable file that is the source of truth for
users, groups, memberships and the roster/model config, reconciled into the stores.

This is the "config to fill in the backend" surface. The DB (or JSON store) remains the
runtime store; this file is **authoritative for configuration** and is reconciled on
every startup (and via the admin ``/reload``). Editing the file and restarting/reloading
makes the stores match it.

Non-destructive carve-out (deliberate, so removing a line never nukes data): reconcile
**adds/updates** users, groups, identities; sets each user's memberships and each
group/user agent-config layer **exactly** to the file (removing stale roster overrides and
memberships). It does **not** delete user accounts, groups, memories, or identities that
are absent from the file — those are removed explicitly (admin commands / SQL). Entities in
the store but not in the file are logged as warnings.

File format (YAML; ``config/principals.yaml`` by default, override with
``PRINCIPALS_CONFIG_PATH``)::

    groups:
      dom:    { priority: 10, agents: { finance_agent: true } }
      dzieci: { priority: 5,  agents: { finance_agent: false, news_agent: openai/gpt-5-mini } }
    users:
      owner:  { name: Kuba, messenger: "2401...", groups: [dom] }
      ala:    { name: Ala,  messenger: ["2402..."], groups: [dom, dzieci],
                agents: { iot_operator: false } }

An agent value may be a bool (``enabled``), a ``provider/model`` string (``model``), or a
dict ``{enabled, model, reasoning}``. The YAML key of a user IS its ``user_id`` (so
``owner`` reconciles the migrated owner). Missing file → no-op.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from lib.domain.principals import principals, new_group_id, seed_owner
from lib.domain.agent_config import agent_config, GROUP_SCOPE, USER_SCOPE

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/principals.yaml")


def config_path() -> Path:
    return Path(os.getenv("PRINCIPALS_CONFIG_PATH") or DEFAULT_CONFIG_PATH)


def _normalize_agents(raw: dict | None) -> dict:
    """Normalize an ``agents:`` mapping to ``{name: {enabled?, model?, reasoning?}}``.

    Shorthands: a bool value → ``{enabled: bool}``; a string value → ``{model: str}``."""
    out: dict[str, dict] = {}
    for name, val in (raw or {}).items():
        if isinstance(val, bool):
            out[name] = {"enabled": val}
        elif isinstance(val, str):
            out[name] = {"model": val}
        elif isinstance(val, dict):
            out[name] = {k: val[k] for k in ("enabled", "model", "reasoning") if k in val}
        else:
            logger.warning("principals config: ignoring agent '%s' with unsupported value %r", name, val)
    return out


def load_config_file(path: Path | None = None) -> dict | None:
    """Parse the YAML config file, or None when it does not exist. Raises on malformed YAML."""
    p = Path(path) if path else config_path()
    if not p.exists():
        return None
    import yaml  # pyyaml is already a dependency
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"principals config {p}: top level must be a mapping")
    return data


def reconcile(config: dict) -> dict:
    """Apply ``config`` to the principals + agent_config stores (see module docstring for
    the authoritative/non-destructive semantics). Returns a summary of what changed."""
    summary = {"groups": 0, "users": 0, "identities": 0, "warnings": []}

    # -- groups (+ their roster config) --------------------------------------
    group_ids: dict[str, str] = {}
    for name, spec in (config.get("groups") or {}).items():
        spec = spec or {}
        priority = int(spec.get("priority", 0))
        g = principals.get_group_by_name(name)
        if g:
            principals.set_priority(g["id"], priority)
            gid = g["id"]
        else:
            gid = principals.add_group(new_group_id(), name, priority)["id"]
        group_ids[name] = gid
        agent_config.clear_scope(GROUP_SCOPE, gid)  # authoritative: file replaces the layer
        for agent, fields in _normalize_agents(spec.get("agents")).items():
            agent_config.set(GROUP_SCOPE, gid, agent, **fields)
        summary["groups"] += 1

    # -- users (identities, memberships, user-scope roster) ------------------
    for uid, spec in (config.get("users") or {}).items():
        spec = spec or {}
        principals.add_user(uid, spec.get("name"))
        summary["users"] += 1

        # identities (add only; never silently move an id already mapped elsewhere)
        raw_ids = spec.get("messenger")
        ext_ids = [raw_ids] if isinstance(raw_ids, (str, int)) else list(raw_ids or [])
        for ext in (str(e) for e in ext_ids):
            existing = principals.resolve_user("messenger", ext)
            if existing == uid:
                continue
            if existing is not None:
                summary["warnings"].append(
                    f"messenger:{ext} already maps to '{existing}', not reassigning to '{uid}'"
                )
                continue
            if principals.add_identity(uid, "messenger", ext):
                summary["identities"] += 1

        # memberships: set EXACTLY to the file's groups
        want: set[str] = set()
        for gname in (spec.get("groups") or []):
            gid = group_ids.get(gname) or (principals.get_group_by_name(gname) or {}).get("id")
            if not gid:
                summary["warnings"].append(f"user '{uid}' references unknown group '{gname}'")
                continue
            want.add(gid)
        have = {g["id"] for g in principals.groups_for_user(uid)}
        for gid in want - have:
            principals.add_membership(uid, gid)
        for gid in have - want:
            principals.remove_membership(uid, gid)

        # user-scope roster overrides (authoritative for this layer)
        agent_config.clear_scope(USER_SCOPE, uid)
        for agent, fields in _normalize_agents(spec.get("agents")).items():
            agent_config.set(USER_SCOPE, uid, agent, **fields)

    # Report store entities not covered by the file (not deleted — see module docstring).
    file_users = set((config.get("users") or {}).keys())
    for u in principals.list_users():
        if u["id"] not in file_users:
            summary["warnings"].append(f"user '{u['id']}' is in the store but not in the config file")

    return summary


def apply_config_file(path: Path | None = None) -> dict | None:
    """Load and reconcile the config file. Ensures the owner/default seed first (so the
    default group always exists), then applies the file. Returns the summary, or None when
    no file is present. Best-effort: never raises to the caller (logs and returns a summary
    with the error) so a bad file can't take the service down at startup."""
    try:
        config = load_config_file(path)
    except Exception as e:  # noqa: BLE001
        logger.error("principals config: failed to parse (%s); skipping", e)
        return {"groups": 0, "users": 0, "identities": 0, "warnings": [f"parse error: {e}"]}
    if config is None:
        return None
    seed_owner()
    try:
        summary = reconcile(config)
    except Exception as e:  # noqa: BLE001
        logger.exception("principals config: reconcile failed")
        return {"groups": 0, "users": 0, "identities": 0, "warnings": [f"reconcile error: {e}"]}
    for w in summary["warnings"]:
        logger.warning("principals config: %s", w)
    logger.info(
        "principals config applied: %d group(s), %d user(s), %d new identity(ies), %d warning(s)",
        summary["groups"], summary["users"], summary["identities"], len(summary["warnings"]),
    )
    return summary
