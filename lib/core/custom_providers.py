"""Named model servers (``config/providers.yaml``): custom providers for model strings.

A model string is ``provider/model``. Besides the built-in providers (``openai``,
``anthropic``, ``openrouter``, …, see ``lib.core.llm``), a provider can be a **named server**
defined here — typically a self-hosted OpenAI-compatible endpoint (vLLM, Ollama):

    # config/providers.yaml
    gpu-server:
      type: openai-compat
      base_url: http://192.168.0.211:8000/v1
      api_key_env: OPENAI_COMPAT_API_KEY      # optional; omitted → a placeholder key

so ``gpu-server/RedHatAI/Qwen3.8-27B-INT4`` means "that model, on that machine". Several
local models are then just several servers (or several model ids on one server), and the
model string alone says where a reply comes from. Server addresses live in this one shared
file, so model profiles and eval sweeps name only models.

The file is optional (no custom providers when absent), re-read when it changes (mtime),
and may not redefine a built-in provider. Its path can be overridden with
``PROVIDERS_CONFIG``. API keys never live here — only the name of the env var holding one.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PROVIDERS_PATH = Path("config/providers.yaml")

# Server types a custom provider can have (each maps to a builder in lib.core.llm).
SUPPORTED_TYPES = ("openai-compat",)

# Sent when a server declares no api_key_env: OpenAI-compatible clients require some key,
# and unauthenticated local servers (vLLM's default) accept any value.
PLACEHOLDER_API_KEY = "EMPTY"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_KEYS = {"type", "base_url", "api_key_env", "description"}


class ProvidersConfigError(ValueError):
    """A malformed ``config/providers.yaml``."""


@dataclass(frozen=True)
class CustomProvider:
    name: str
    type: str
    base_url: str
    api_key_env: str | None = None
    description: str = ""

    def api_key(self) -> str:
        if self.api_key_env:
            val = os.getenv(self.api_key_env)
            if val and val.strip():
                return val.strip()
        return PLACEHOLDER_API_KEY


def parse_providers(data, reserved: set[str]) -> dict[str, CustomProvider]:
    """Validate the parsed YAML mapping. ``reserved`` are the built-in provider names."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ProvidersConfigError("providers config: top level must be a mapping of name → server")
    out: dict[str, CustomProvider] = {}
    for name, raw in data.items():
        name = str(name)
        where = f"providers config '{name}'"
        if not _NAME_RE.match(name):
            raise ProvidersConfigError(f"{where}: name must be lowercase letters, digits, '.', '_' or '-' (no '/')")
        if name in reserved:
            raise ProvidersConfigError(f"{where}: '{name}' is a built-in provider and cannot be redefined")
        if not isinstance(raw, dict):
            raise ProvidersConfigError(f"{where}: expected a mapping with type/base_url")
        unknown = set(raw) - _KEYS
        if unknown:
            raise ProvidersConfigError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(_KEYS)}")
        ptype = raw.get("type")
        if ptype not in SUPPORTED_TYPES:
            raise ProvidersConfigError(f"{where}: type must be one of {list(SUPPORTED_TYPES)}, got {ptype!r}")
        base_url = str(raw.get("base_url") or "").strip()
        if not base_url:
            raise ProvidersConfigError(f"{where}: base_url is required")
        out[name] = CustomProvider(
            name=name,
            type=ptype,
            base_url=base_url,
            api_key_env=(str(raw["api_key_env"]).strip() or None) if raw.get("api_key_env") else None,
            description=str(raw.get("description") or ""),
        )
    return out


def providers_path() -> Path:
    raw = os.getenv("PROVIDERS_CONFIG")
    return Path(raw.strip()) if raw and raw.strip() else DEFAULT_PROVIDERS_PATH


_CACHE: dict[Path, tuple[float, dict[str, CustomProvider]]] = {}


def load_providers(reserved: set[str]) -> dict[str, CustomProvider]:
    """The custom providers from :func:`providers_path` ({} when the file is absent)."""
    path = providers_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with path.open() as f:
        providers = parse_providers(yaml.safe_load(f), reserved)
    _CACHE[path] = (mtime, providers)
    logger.info("Loaded %d custom model provider(s) from %s", len(providers), path)
    return providers
