"""Public client for the predicate sandbox — the contract seam over a unix socket.

This is the **public** half of the ``watch-sandbox`` change: the code by which the runtime
talks to the isolated evaluator. The evaluator *server* and all of its hardening
(container, network, seccomp, Starlark runtime) live privately under the git-ignored
``sandbox/`` directory; only this client and the wire protocol below are public.

Wire protocol (unix domain socket at ``WATCH_SANDBOX_SOCKET``):

- One JSON request object per connection, terminated by a newline (``\\n``); the server
  replies with one JSON object terminated by a newline.
- Request:  ``{"mode": "evaluate" | "validate", "code": <str>, "data": <object>}``
  (``data`` is ignored for ``validate``).
- Response: ``{"ok": true, "result": <bool|null>}`` on success (``result`` is ``null`` for
  ``validate``), or ``{"ok": false, "error": <str>}`` on failure (compile/runtime error).

Two faces of the same contract:

- **runtime** (``evaluate``): returns a ``bool``; ANY failure — bad code, runtime error,
  or an unreachable/timed-out sandbox — raises, and the runner maps that to ``unknown``
  (no fire, no state change). The error text is intentionally discarded here.
- **authoring** (``validate`` / ``evaluate_checked``): returns the error **text** so the
  ``watch-authoring`` pipeline can feed it to the repair model.

``SandboxClient`` implements the ``PredicateEvaluator`` protocol, so it is a drop-in
replacement for the in-process stub in the scheduler runner.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from lib.runtime.watch_evaluator import PredicateEvaluator, default_evaluator

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 5.0


class SandboxError(RuntimeError):
    """Raised when the sandbox reports a predicate failure or is unreachable."""


class SandboxClient:
    """Talks the predicate-sandbox contract over a unix socket. Implements
    ``PredicateEvaluator`` (``evaluate(code, data) -> bool``) plus the authoring-facing
    ``validate`` / ``evaluate_checked`` methods that surface error text."""

    def __init__(self, socket_path: str, timeout: float = DEFAULT_TIMEOUT_S):
        self.socket_path = socket_path
        self.timeout = timeout

    async def _request(self, payload: dict) -> dict:
        """Send one request and read one response. Raises SandboxError on any transport
        problem (unreachable, timeout, malformed reply)."""
        line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as e:
            raise SandboxError(f"sandbox unreachable at {self.socket_path}: {e}") from e
        try:
            writer.write(line)
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise SandboxError(f"sandbox I/O error: {e}") from e
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        if not raw:
            raise SandboxError("sandbox closed the connection without a reply")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise SandboxError(f"sandbox returned a malformed reply: {e}") from e

    # -- runtime face (drop-in for the runner) --------------------------------
    async def evaluate(self, code: str, data: dict) -> bool:
        """Runtime evaluation: return a bool, or RAISE on any failure (the runner maps
        an exception to ``unknown``). Error text is discarded here on purpose."""
        resp = await self._request({"mode": "evaluate", "code": code, "data": data})
        if resp.get("ok"):
            return bool(resp.get("result"))
        raise SandboxError(str(resp.get("error") or "sandbox evaluation failed"))

    # -- authoring face (error text surfaced) ---------------------------------
    async def validate(self, code: str) -> tuple[bool, Optional[str]]:
        """Authoring: compile-check ``code``. Returns ``(ok, error_or_None)``. An
        unreachable sandbox is reported as ``(False, <message>)`` rather than raising, so
        the authoring pipeline can surface it."""
        try:
            resp = await self._request({"mode": "validate", "code": code})
        except SandboxError as e:
            return False, str(e)
        return bool(resp.get("ok")), (None if resp.get("ok") else str(resp.get("error") or "invalid"))

    async def evaluate_checked(self, code: str, data: dict) -> tuple[Optional[bool], Optional[str]]:
        """Authoring smoke-test: returns ``(result_or_None, error_or_None)``."""
        try:
            resp = await self._request({"mode": "evaluate", "code": code, "data": data})
        except SandboxError as e:
            return None, str(e)
        if resp.get("ok"):
            return bool(resp.get("result")), None
        return None, str(resp.get("error") or "evaluation failed")


def resolve_evaluator() -> PredicateEvaluator:
    """Pick the evaluator the runner should use: the real sandbox client when
    ``WATCH_SANDBOX_SOCKET`` is configured, otherwise the in-process stub (dev/test)."""
    path = os.getenv("WATCH_SANDBOX_SOCKET")
    if path and path.strip():
        logger.info("Watch evaluator: sandbox client at %s", path)
        return SandboxClient(path.strip())
    logger.info("Watch evaluator: in-process STUB (WATCH_SANDBOX_SOCKET unset)")
    return default_evaluator
