"""Watch predicate authoring pipeline: normalize -> validate -> repair -> smoke-test.

All of this is **authoring-time** (one-off, when a watch is created) — the runtime poll
loop runs already-validated code with zero authoring cost. The pipeline turns an
LLM-written Starlark predicate into a validated one and, at the end, evaluates it against a
freshly-fetched sample so the user can confirm what it currently returns before arming.

Steps:
1. **normalize** (deterministic, no LLM): strip markdown fences and drop no-op ``f``
   prefixes — the most common mechanical Python-isms a model emits.
2. **validate** through the evaluator (``validate`` mode = the sandbox in prod, the stub in
   dev). On failure the interpreter's error text is captured.
3. **repair** (only on failure): a dedicated coder model (``LLM_BY_AGENT["watch_coder"]``)
   is given the broken code + the error + the intent + the Starlark rules, and returns a
   fix. The loop is bounded by ``WATCH_AUTHORING_MAX_REPAIRS`` and **fails closed** — if it
   never validates, no watch is armed.
4. **smoke-test**: fetch the source once and evaluate the validated code against it, so the
   caller can show the user the concrete current result.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from llama_index.core.llms import ChatMessage

from lib.core import LLM_BY_AGENT
from lib.agents.scheduler_agent.watch_sources import fetch_source

logger = logging.getLogger(__name__)

CODER_AGENT = "watch_coder"
DEFAULT_MAX_REPAIRS = 3

# Shared rules describing the Starlark surface — given to the repair model (and worth
# mirroring in the generator's guidance) so repairs don't reintroduce forbidden constructs.
STARLARK_RULES = (
    "The predicate must be written in STARLARK (a deterministic, hermetic Python dialect), "
    "as a single function `def check(data) -> bool` returning a boolean.\n"
    "Allowed: if/elif/else, for, comprehensions, and/or/not, comparisons, arithmetic, "
    "string methods, dict/list access, the helper `get(data, \"a.b.c\", default)` for safe "
    "nested access, and standard functions (len, min, max, sorted, any, all, ...).\n"
    "NOT allowed (Starlark lacks them): import, try/except, f-strings, while loops, "
    "recursion, classes, generators, or any I/O. `data` is the fetched source dict.\n"
    "CHANGE DETECTION: `data[\"_previous\"]` holds the PREVIOUS poll's data (or None on the "
    "first poll). To fire on a change/increase, compare current vs previous and guard the "
    "None, e.g. prev = get(data, \"_previous\"); if prev == None: return False; then compare "
    "get(data, \"x\", 0) to get(prev, \"x\", 0).\n"
    "Output ONLY the Starlark code for `def check(data)`, no prose, no markdown fences."
)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$")
_FSTRING_NOOP_RE = re.compile(r"""(?<![a-zA-Z0-9_])[fF](?=(['"]))""")


def normalize(code: str) -> str:
    """Deterministically fix the most common, mechanically-safe Python-isms (no LLM).

    - strip a surrounding markdown code fence (```python ... ```),
    - drop the ``f`` prefix from f-strings that contain no ``{`` placeholder (safe),
    - trim surrounding whitespace.
    Semantics are preserved; anything requiring real logic change is left to the repair model.
    """
    if code is None:
        return ""
    s = code.strip()
    # strip one leading + one trailing markdown fence, if present
    if s.startswith("```"):
        s = _FENCE_RE.sub("", s)
        s = _FENCE_RE.sub("", s)
    s = s.strip()

    # drop no-op f-prefixes (f"abc" -> "abc") without touching real interpolations
    def _deff(m: re.Match) -> str:
        quote = m.group(1)
        body = s[m.end() + 1:]          # skip the opening quote itself
        end = body.find(quote)
        segment = body[:end] if end != -1 else body
        return "" if "{" not in segment else m.group(0)

    s = _FSTRING_NOOP_RE.sub(_deff, s)
    return s.strip()


def _extract_code(text: str) -> str:
    """Pull code out of a model reply (strip fences/prose) via the same normalizer."""
    return normalize(text or "")


def max_repairs() -> int:
    raw = os.getenv("WATCH_AUTHORING_MAX_REPAIRS")
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_REPAIRS
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return DEFAULT_MAX_REPAIRS


async def llm_repair(code: str, error: str, intent: str) -> str:
    """Ask the dedicated coder model to fix ``code`` given the interpreter ``error`` and the
    user's ``intent``. Returns the repaired (normalized) code."""
    llm = LLM_BY_AGENT[CODER_AGENT]()
    system = "You fix a broken watch predicate so it compiles under Starlark.\n" + STARLARK_RULES
    user = (
        f"Intent (what the condition should detect):\n{intent or '(not provided)'}\n\n"
        f"Current code:\n{code}\n\n"
        f"The sandbox rejected it with this error:\n{error}\n\n"
        "Return the corrected `def check(data)` only."
    )
    resp = await llm.achat(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]
    )
    return _extract_code(str(resp.message.content or ""))


# The runtime semantics the explainer must convey but cannot read from the code alone.
def _watch_runtime_facts(repeat: bool = False) -> str:
    common = (
        "How the watch runs (fixed by the runtime, NOT visible in the code): the predicate is "
        "evaluated every `poll_interval` seconds against a freshly fetched data sample; the user "
        "is notified at the moment the predicate flips from false to true. The previous poll's "
        "reading is available to the predicate as data[\"_previous\"] (None on the first poll), "
        "so it can detect changes. "
    )
    if repeat:
        return common + (
            "This is a REPEATING watch: it stays armed after firing and notifies AGAIN on every "
            "future false->true edge (it does not notify while the condition stays continuously "
            "true — only on each fresh transition)."
        )
    return common + (
        "The user is notified EXACTLY ONCE, at the first false->true transition, and then the "
        "watch turns itself off (it does NOT keep firing and does NOT re-arm)."
    )


def truncate_sample(data, *, max_chars: int = 1200):
    """A size-bounded preview of fetched source data for grounding the model.

    Returns the data unchanged if its JSON form is small; otherwise a shallow, truncated
    view (top-level keys with truncated string reprs) so a big feed like fpl.live does not
    blow up a prompt. Best-effort and never raises."""
    import json

    try:
        s = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(data)[:max_chars]
    if len(s) <= max_chars:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            r = str(v)
            out[k] = r if len(r) <= 120 else r[:120] + "…"
        return {"_truncated": True, **out}
    return s[:max_chars] + "…"


async def llm_explain(
    code: str, intent: str, source: str, params: Optional[dict], poll_interval: int,
    sample=None, repeat: bool = False,
) -> str:
    """Ask the coder model to describe, in plain user-facing Polish, what the VALIDATED
    predicate actually does over time — read from the code, not from the intent.

    This is the human-review gate: the description lets the user catch a predicate whose
    *logic* diverges from what they asked for (a semantic bug the compile-only validation
    cannot see) before the watch is armed. The coder is given the original intent precisely
    so it can flag a divergence between the code's behaviour and that intent.
    """
    llm = LLM_BY_AGENT[CODER_AGENT]()
    system = (
        "You explain a watch predicate to a non-technical user IN POLISH, so they can confirm "
        "it does what they meant before it is armed. Describe what the code ACTUALLY does when "
        "run against live data — the trigger condition, any thresholds/gates/time-windows it "
        "reads, and how often it is checked. Base this on the CODE, which is the source of "
        "truth; use the stated intent only to judge whether the code matches it. If the code's "
        "behaviour diverges from the intent (e.g. it would fire far more often than the user "
        "wants, or ignores a limit they asked for), say so plainly in a final sentence starting "
        "with 'Uwaga:'. Be concrete and short (2-4 sentences). No code, no markdown.\n\n"
        + _watch_runtime_facts(repeat)
    )
    sample_block = (
        f"\nLive sample of the data the predicate runs against (real keys/values now):\n{sample}\n"
        if sample is not None else ""
    )
    user = (
        f"User's intent (what they asked for):\n{intent or '(not provided)'}\n\n"
        f"Data source: {source}   params: {params or {}}   poll_interval: {poll_interval}s\n"
        f"{sample_block}\n"
        f"Validated predicate:\n{code}\n\n"
        "Explain to the user what this watch will do, and flag any divergence from the intent."
    )
    resp = await llm.achat(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]
    )
    return str(resp.message.content or "").strip()


@dataclass
class AuthoringResult:
    ok: bool
    code: str                       # final (validated) code, or the last attempt on failure
    error: Optional[str]            # last validation error when not ok
    attempts: int                   # number of repair rounds used
    smoke_state: Optional[str] = None    # "ok" | "unknown" (source read) — set after validation
    smoke_result: Optional[bool] = None  # predicate result on the sample (when smoke_state == ok)
    smoke_error: Optional[str] = None
    explanation: Optional[str] = None    # plain-language description of the predicate (review gate)
    sample: Optional[object] = None      # size-bounded preview of the fetched source data


async def validate_and_repair(
    code: str,
    intent: str,
    evaluator,
    *,
    repair: Callable[[str, str, str], Awaitable[str]] = llm_repair,
    attempts_cap: Optional[int] = None,
) -> AuthoringResult:
    """Normalize, then validate through the evaluator, repairing on failure up to the cap.

    Fails closed: if the code never validates within the cap, returns ``ok=False`` (the
    caller must not arm the watch). ``repair`` is injectable for testing."""
    cap = attempts_cap if attempts_cap is not None else max_repairs()
    code = normalize(code)
    ok, err = await evaluator.validate(code)
    attempts = 0
    while not ok and attempts < cap:
        attempts += 1
        try:
            repaired = await repair(code, err or "", intent)
        except Exception as e:  # noqa: BLE001 - a repair failure means fail-closed, not a crash
            logger.warning("watch predicate repair failed on attempt %d: %s", attempts, e)
            return AuthoringResult(ok=False, code=code, error=f"repair failed: {e}", attempts=attempts)
        code = normalize(repaired)
        ok, err = await evaluator.validate(code)
    return AuthoringResult(ok=ok, code=code, error=(None if ok else err), attempts=attempts)


async def smoke_test(
    source: str, params: Optional[dict], code: str, evaluator
) -> tuple[str, Optional[bool], Optional[str], Optional[object]]:
    """Fetch the source once and evaluate ``code`` against it. Returns
    ``(state, result_or_None, error_or_None, sample_or_None)`` where state is "ok"/"unknown".
    The sample is a size-bounded preview of the fetched data (None when unavailable)."""
    state, data = await fetch_source(source, params)
    if state != "ok":
        return "unknown", None, "source data is currently unavailable", None
    # Inject the change-detection baseline key the runtime will provide (None on first poll),
    # so predicates that read data["_previous"] validate/smoke-run and the model sees the key.
    sample_data = {**(data or {}), "_previous": None}
    result, err = await evaluator.evaluate_checked(code, sample_data)
    return "ok", result, err, truncate_sample(sample_data)


async def author_watch(
    check_code: str,
    intent: str,
    source: str,
    params: Optional[dict],
    evaluator,
    *,
    poll_interval: int = 0,
    repeat: bool = False,
    repair: Callable[[str, str, str], Awaitable[str]] = llm_repair,
    attempts_cap: Optional[int] = None,
) -> AuthoringResult:
    """Full authoring pass: validate+repair, smoke-test the sample, then (best-effort)
    author a plain-language explanation of the predicate for the user-review gate."""
    res = await validate_and_repair(
        check_code, intent, evaluator, repair=repair, attempts_cap=attempts_cap
    )
    if not res.ok:
        return res
    res.smoke_state, res.smoke_result, res.smoke_error, res.sample = await smoke_test(
        source, params, res.code, evaluator
    )
    # Review gate: a plain-language description of the validated predicate for the user to
    # confirm. Advisory only (unlike validation, which fails closed) — a failure here must
    # never block a correctly-validated watch, so it degrades to no explanation. Called by
    # module name so tests can monkeypatch it without a live model. The live data sample is
    # passed so the explanation (and any 'Uwaga:' divergence flag) is grounded in real keys.
    try:
        res.explanation = await llm_explain(
            res.code, intent, source, params, poll_interval, sample=res.sample, repeat=repeat
        )
    except Exception as e:  # noqa: BLE001 - explanation is best-effort, never fatal
        logger.warning("watch predicate explanation failed: %s", e)
        res.explanation = None
    return res
