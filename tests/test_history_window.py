"""Unit tests for the rolling-window context engineering (lib/runtime/history_window.py).

Pure windowing math is tested directly; the assembly path is driven with an in-memory
fake store and a fake (async) compressor, so no network / LLM / database is touched.
Async entry points are run via ``asyncio.run`` (the project has no pytest-asyncio)."""
import asyncio

from lib.domain.conversation import ConversationStore
from lib.runtime.history_window import (
    DEFAULT_MAX_SENTENCES,
    DEFAULT_WINDOW_PAIRS,
    WindowConfig,
    _format_exchange,
    build_chat_history,
    fold_summary,
    load_config,
    plan_window,
    schedule_summary_fold,
)


# --------------------------------------------------------------------------- plan math
def test_plan_no_history():
    p = plan_window(0, 0, window_pairs=3)
    assert not p.has_fold and p.verbatim_start == 0 and p.new_upto == 0


def test_plan_within_window_keeps_all_verbatim():
    # 3 pairs = 6 messages, window of 3 → nothing aged out
    p = plan_window(6, 0, window_pairs=3)
    assert not p.has_fold and p.verbatim_start == 0


def test_plan_evicts_one_pair_past_window():
    # 4 pairs = 8 messages, window 3 → the oldest pair [0,2) folds
    p = plan_window(8, 0, window_pairs=3)
    assert (p.fold_start, p.fold_end) == (0, 2)
    assert p.verbatim_start == 2 and p.new_upto == 2


def test_plan_incremental_next_turn():
    # already summarised 2 messages; now 5 pairs → fold the next pair [2,4)
    p = plan_window(10, 2, window_pairs=3)
    assert (p.fold_start, p.fold_end) == (2, 4)
    assert p.verbatim_start == 4 and p.new_upto == 4


def test_plan_zero_window_summarises_everything():
    p = plan_window(4, 0, window_pairs=0)
    assert (p.fold_start, p.fold_end) == (0, 4)
    assert p.verbatim_start == 4 and p.new_upto == 4


def test_plan_grown_window_does_not_refold_or_duplicate():
    # window widened so boundary(2) < already-summarised(6): no fold, no re-showing
    p = plan_window(8, 6, window_pairs=3)
    assert not p.has_fold and p.new_upto == 6 and p.verbatim_start == 6


# ------------------------------------------------------------------- assembly fakes
class FakeStore:
    def __init__(self, history, summary="", summary_upto=0):
        self._history = history
        self._summary = summary
        self._upto = summary_upto
        self.saved = []  # records (summary, upto) on each save

    def load_history(self, _key):
        return list(self._history)

    def load_summary(self, _key):
        return self._summary, self._upto

    def save_summary(self, _key, summary, upto):
        self._summary, self._upto = summary, upto
        self.saved.append((summary, upto))


def _pairs(n):
    """n user/assistant pairs as flat {role,content} dicts: u0,a0,u1,a1,…"""
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"u{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def _build(store, *, enabled=True, window_pairs=3, max_sentences=5):
    """Assemble the (read-only, LLM-free) chat history for one turn."""
    cfg = WindowConfig(enabled=enabled, window_pairs=window_pairs, max_sentences=max_sentences)
    return asyncio.run(build_chat_history("repl", store=store, config=cfg))


def _fold(store, calls, *, enabled=True, window_pairs=3, max_sentences=5):
    """Run the background fold once against ``store`` with a fake compressor."""
    async def fake_compress(old, evicted, max_s):
        calls.append((old, [m["content"] for m in evicted], max_s))
        return f"SUM({old}|{'+'.join(m['content'] for m in evicted)})"

    cfg = WindowConfig(enabled=enabled, window_pairs=window_pairs, max_sentences=max_sentences)
    return asyncio.run(
        fold_summary("repl", store=store, config=cfg, compressor=fake_compress)
    )


# ---------------------------------------------- assembly (build_chat_history) is LLM-free
def test_build_disabled_returns_full_history_verbatim():
    store = FakeStore(_pairs(5), summary="ignored", summary_upto=4)
    msgs = _build(store, enabled=False)
    assert [m.content for m in msgs] == [m["content"] for m in _pairs(5)]
    assert all(m.role != "system" for m in msgs)


def test_build_within_window_shows_all_verbatim():
    store = FakeStore(_pairs(3))  # exactly the window, nothing summarised
    msgs = _build(store)
    assert store.saved == []  # build never writes
    assert [m.content for m in msgs] == ["u0", "a0", "u1", "a1", "u2", "a2"]


def test_build_never_folds_shows_pending_eviction_verbatim():
    # 4 pairs, window 3, nothing folded yet → build shows ALL 4 pairs verbatim, no summary,
    # and crucially does NOT compress (that is the background fold's job now).
    store = FakeStore(_pairs(4), summary="", summary_upto=0)
    msgs = _build(store)
    assert store.saved == []
    assert all(m.role != "system" for m in msgs)
    assert [m.content for m in msgs] == [m["content"] for m in _pairs(4)]


def test_build_prepends_summary_and_shows_tail_from_upto():
    # summary already covers first 2 msgs → build shows summary + history[2:]
    store = FakeStore(_pairs(4), summary="S0", summary_upto=2)
    msgs = _build(store)
    assert store.saved == []
    assert msgs[0].role == "system" and "S0" in msgs[0].content
    assert [m.content for m in msgs[1:]] == ["u1", "a1", "u2", "a2", "u3", "a3"]


# ------------------------------------------------------- background fold (fold_summary)
def test_fold_folds_oldest_pair_and_saves():
    store = FakeStore(_pairs(4))  # 4 pairs, window 3 → oldest pair evicted
    calls = []
    assert _fold(store, calls) is True
    assert calls == [("", ["u0", "a0"], 5)]
    assert store.saved == [("SUM(|u0+a0)", 2)]


def test_fold_noop_within_window():
    store = FakeStore(_pairs(3))
    calls = []
    assert _fold(store, calls) is False
    assert calls == [] and store.saved == []


def test_fold_incremental_builds_on_prior_summary():
    store = FakeStore(_pairs(5), summary="S0", summary_upto=2)
    calls = []
    assert _fold(store, calls) is True
    assert calls == [("S0", ["u1", "a1"], 5)]
    assert store.saved == [("SUM(S0|u1+a1)", 4)]


def test_fold_disabled_is_noop():
    store = FakeStore(_pairs(4))
    calls = []
    assert _fold(store, calls, enabled=False) is False
    assert calls == [] and store.saved == []


def test_fold_failure_leaves_pointer_untouched():
    store = FakeStore(_pairs(4), summary="", summary_upto=0)

    async def boom(old, evicted, max_s):
        raise RuntimeError("llm down")

    cfg = WindowConfig(enabled=True, window_pairs=3, max_sentences=5)
    ok = asyncio.run(fold_summary("repl", store=store, config=cfg, compressor=boom))
    assert ok is False
    # nothing persisted, pointer untouched → build still shows everything verbatim next turn
    assert store.saved == [] and store.load_summary("repl") == ("", 0)


def test_schedule_summary_fold_coalesces_per_conversation():
    # Two schedules for the same channel while the first is still running must share one
    # task (a running fold reads fresh history and covers everything).
    store = FakeStore(_pairs(4))
    gate = asyncio.Event()

    async def slow_compress(old, evicted, max_s):
        await gate.wait()
        return "S"

    cfg = WindowConfig(enabled=True, window_pairs=3, max_sentences=5)

    async def scenario():
        t1 = schedule_summary_fold("chanA", store=store, config=cfg, compressor=slow_compress)
        t2 = schedule_summary_fold("chanA", store=store, config=cfg, compressor=slow_compress)
        assert t1 is t2  # coalesced onto the same in-flight task
        gate.set()
        await t1

    asyncio.run(scenario())
    assert store.saved == [("S", 2)]  # exactly one fold ran


# ------------------------------------------------------------------- config parsing
_HISTORY_ENV = (
    "HISTORY_COMPRESSION_ENABLED",
    "HISTORY_WINDOW_PAIRS",
    "HISTORY_SUMMARY_MAX_SENTENCES",
)


def _clear_history_env(monkeypatch):
    for name in _HISTORY_ENV:
        monkeypatch.delenv(name, raising=False)


def test_config_defaults(monkeypatch):
    _clear_history_env(monkeypatch)
    cfg = load_config()
    assert cfg == WindowConfig(True, DEFAULT_WINDOW_PAIRS, DEFAULT_MAX_SENTENCES)


def test_config_reads_env(monkeypatch):
    _clear_history_env(monkeypatch)
    monkeypatch.setenv("HISTORY_WINDOW_PAIRS", "5")
    monkeypatch.setenv("HISTORY_SUMMARY_MAX_SENTENCES", "8")
    cfg = load_config()
    assert (cfg.window_pairs, cfg.max_sentences) == (5, 8)


def test_config_master_switch_falsy_values(monkeypatch):
    _clear_history_env(monkeypatch)
    for val in ("0", "false", "no", "off", "disabled", "OFF"):
        monkeypatch.setenv("HISTORY_COMPRESSION_ENABLED", val)
        assert load_config().enabled is False, val
    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv("HISTORY_COMPRESSION_ENABLED", val)
        assert load_config().enabled is True, val


def test_config_empty_string_falls_back_to_default(monkeypatch):
    _clear_history_env(monkeypatch)
    monkeypatch.setenv("HISTORY_WINDOW_PAIRS", "")
    assert load_config().window_pairs == DEFAULT_WINDOW_PAIRS


def test_config_invalid_int_falls_back_to_default(monkeypatch):
    _clear_history_env(monkeypatch)
    monkeypatch.setenv("HISTORY_WINDOW_PAIRS", "notanumber")
    assert load_config().window_pairs == DEFAULT_WINDOW_PAIRS


def test_config_clamps_to_minimums(monkeypatch):
    _clear_history_env(monkeypatch)
    monkeypatch.setenv("HISTORY_WINDOW_PAIRS", "-3")        # min 0
    monkeypatch.setenv("HISTORY_SUMMARY_MAX_SENTENCES", "0")  # min 1
    cfg = load_config()
    assert cfg.window_pairs == 0 and cfg.max_sentences == 1


# ------------------------------------------------------------------ exchange format
def test_format_exchange_labels_roles_in_polish():
    text = _format_exchange(
        [{"role": "user", "content": "hej"}, {"role": "assistant", "content": "cześć"}]
    )
    assert text == "Użytkownik: hej\nAsystent: cześć"


# ---------------------------------------------------- multi-turn end-to-end simulation
def test_multi_turn_window_slides_and_summary_grows_incrementally(tmp_path):
    """Drive real turns against the real JSON store with a fake compressor, asserting the
    window slides and the running summary is folded one evicted pair at a time.

    Faithful to the engine's NEW order per turn: build context (LLM-free) from history-so-far,
    record the new pair, THEN fold off the critical path. Folding pair X at the END of turn X
    leaves the same summary state entering turn X+1's build as folding it at the START of turn
    X+1 did before — so the per-turn *display* is identical, the compressor call just no longer
    blocks the reply. Here the fold is awaited synchronously to make the ordering assertable.
    """
    store = ConversationStore(path=tmp_path / "conversations.json")
    cfg = WindowConfig(enabled=True, window_pairs=2, max_sentences=5)
    calls = []  # (old_summary, [evicted contents]) per compressor invocation

    async def fake_compress(old, evicted, _max):
        calls.append((old, [m["content"] for m in evicted]))
        folded = "+".join(m["content"] for m in evicted)
        return f"{old}>{folded}" if old else folded

    def turn(i):
        # 1) build context (no LLM), 2) record the turn, 3) fold in the background (awaited)
        msgs = asyncio.run(build_chat_history("repl", store=store, config=cfg))
        store.record_turn("repl", f"u{i}", f"a{i}")
        asyncio.run(fold_summary("repl", store=store, config=cfg, compressor=fake_compress))
        return msgs

    # Turns 0,1: history fits in the 2-pair window → nothing folded yet.
    assert [m.content for m in turn(0)] == []
    assert [m.content for m in turn(1)] == ["u0", "a0"]
    assert calls == [] and store.load_summary("repl") == ("", 0)

    # Turn 2: build (before its fold) still shows all verbatim; the fold at the END of
    # turn 2 evicts the oldest pair (u0,a0) — the first compression happens here now.
    assert [m.content for m in turn(2)] == ["u0", "a0", "u1", "a1"]
    assert calls == [("", ["u0", "a0"])]
    assert store.load_summary("repl") == ("u0+a0", 2)

    # Turn 3: build shows summary(u0+a0, folded at end of turn 2) + last 2 pairs.
    m3 = turn(3)
    assert store.load_summary("repl") == ("u0+a0>u1+a1", 4)  # end-of-turn-3 fold state
    assert m3[0].role == "system" and "u0+a0" in m3[0].content
    assert [m.content for m in m3[1:]] == ["u1", "a1", "u2", "a2"]  # last 2 pairs

    # Turn 4: display identical to the old (synchronous) behaviour.
    m4 = turn(4)
    assert store.load_summary("repl") == ("u0+a0>u1+a1>u2+a2", 6)
    assert [m.content for m in m4[1:]] == ["u2", "a2", "u3", "a3"]

    # Turn 5: exactly one compressor call per turn past the window.
    m5 = turn(5)
    assert store.load_summary("repl") == ("u0+a0>u1+a1>u2+a2>u3+a3", 8)
    assert [m.content for m in m5[1:]] == ["u3", "a3", "u4", "a4"]

    # One eviction folded per turn from turn 2 onward (turns 2,3,4,5); window never
    # exceeded 2 verbatim pairs.
    assert len(calls) == 4
