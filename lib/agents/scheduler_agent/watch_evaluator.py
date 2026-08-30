"""Predicate evaluator contract + in-process STUB for conditional watches.

Contract (the seam shared across the three watch changes):

    async evaluate(code: str, data: dict) -> bool

The production, isolated implementation lives behind this same interface in the
``watch-sandbox`` change (a hardened container running Starlark). Here we ship only a
deterministic **in-process STUB** for development and testing.

**The stub is NOT a security boundary.** It executes the predicate as Python in a minimal
namespace and must never run untrusted code in production — that is the whole reason
``watch-sandbox`` exists. Starlark's common subset is valid Python, so a predicate written
for the real sandbox (``def check(data): return ...``) runs unchanged here during dev.

Failure semantics: the evaluator MAY raise on malformed/failing code. The runner treats any
evaluator exception as ``unknown`` (no fire, no state change), so a broken predicate never
crashes a tick.
"""
from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class PredicateEvaluator(Protocol):
    """Anything the runner can call to evaluate a predicate. Both the stub and the future
    sandbox client implement this, so the runner is agnostic to which is wired in."""

    async def evaluate(self, code: str, data: dict) -> bool: ...


def _get(data, path, default=None):
    """Injected helper: safe dotted-path access into nested dict/list data.

    Mirrors the helper the real sandbox injects, so predicates can avoid try/except and
    write ``get(data, "a.b.c", default)`` instead of chained subscripting.
    """
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return cur


# Minimal builtins exposed to predicate code — an approximation of Starlark's surface.
_SAFE_BUILTINS = {
    "len": len, "min": min, "max": max, "abs": abs, "sum": sum, "sorted": sorted,
    "any": any, "all": all, "round": round, "int": int, "float": float, "str": str,
    "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "range": range, "enumerate": enumerate, "zip": zip, "True": True, "False": False,
    "None": None,
}


class StubEvaluator:
    """Deterministic in-process evaluator. DEV/TEST ONLY — not a security boundary.

    Executes ``code`` (expected to define ``def check(data)``) in a single minimal
    namespace with the ``get`` helper available, then calls ``check(data)`` and returns
    its boolean result.
    """

    async def evaluate(self, code: str, data: dict) -> bool:
        ns: dict = {"__builtins__": _SAFE_BUILTINS, "get": _get}
        exec(code, ns)  # noqa: S102 - stub only; the real boundary is watch-sandbox
        check = ns.get("check")
        if not callable(check):
            raise ValueError("predicate must define a callable check(data)")
        return bool(check(data))


# Default evaluator the runner uses until the real sandbox client is wired in.
default_evaluator: PredicateEvaluator = StubEvaluator()
