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
    load_config,
    plan_window,
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


def _run(store, calls, *, enabled=True, window_pairs=3, max_sentences=5):
    async def fake_compress(old, evicted, max_s):
        calls.append((old, [m["content"] for m in evicted], max_s))
        return f"SUM({old}|{'+'.join(m['content'] for m in evicted)})"

    cfg = WindowConfig(enabled=enabled, window_pairs=window_pairs, max_sentences=max_sentences)
    return asyncio.run(
        build_chat_history("repl", store=store, config=cfg, compressor=fake_compress)
    )


# -------------------------------------------------------------------- assembly tests
def test_disabled_returns_full_history_verbatim():
    store = FakeStore(_pairs(5), summary="ignored", summary_upto=4)
    calls = []
    msgs = _run(store, calls, enabled=False)
    assert calls == [] and store.saved == []
    assert [m.content for m in msgs] == [m["content"] for m in _pairs(5)]
    assert all(m.role != "system" for m in msgs)


def test_within_window_no_compression():
    store = FakeStore(_pairs(3))  # exactly the window
    calls = []
    msgs = _run(store, calls)
    assert calls == [] and store.saved == []
    assert [m.content for m in msgs] == ["u0", "a0", "u1", "a1", "u2", "a2"]


def test_eviction_folds_oldest_pair_and_prepends_summary():
    store = FakeStore(_pairs(4))  # 4 pairs, window 3 → oldest pair evicted
    calls = []
    msgs = _run(store, calls)

    # compressor called once with the evicted oldest pair
    assert calls == [("", ["u0", "a0"], 5)]
    assert store.saved == [("SUM(|u0+a0)", 2)]

    # first message is the summary (system role), then the last 3 pairs verbatim
    assert msgs[0].role == "system"
    assert "SUM(|u0+a0)" in msgs[0].content
    assert [m.content for m in msgs[1:]] == ["u1", "a1", "u2", "a2", "u3", "a3"]


def test_existing_summary_no_new_eviction_still_prepended():
    # 3 pairs, already summarised 2 msgs (upto==boundary) → no fold, summary kept
    store = FakeStore(_pairs(3), summary="wcześniej", summary_upto=0)
    # force boundary==upto: window 3 with 3 pairs → boundary 0, upto 0, no fold
    calls = []
    msgs = _run(store, calls)
    assert calls == [] and store.saved == []
    assert msgs[0].role == "system" and "wcześniej" in msgs[0].content
    assert [m.content for m in msgs[1:]] == ["u0", "a0", "u1", "a1", "u2", "a2"]


def test_incremental_builds_on_prior_summary():
    # 5 pairs, prior summary covered first 2 msgs → fold the next pair onto it
    store = FakeStore(_pairs(5), summary="S0", summary_upto=2)
    calls = []
    msgs = _run(store, calls)
    assert calls == [("S0", ["u1", "a1"], 5)]
    assert store.saved == [("SUM(S0|u1+a1)", 4)]
    assert [m.content for m in msgs[1:]] == ["u2", "a2", "u3", "a3", "u4", "a4"]


def test_compressor_failure_keeps_turn_and_shows_unfolded_verbatim():
    store = FakeStore(_pairs(4), summary="", summary_upto=0)

    async def boom(old, evicted, max_s):
        raise RuntimeError("llm down")

    cfg = WindowConfig(enabled=True, window_pairs=3, max_sentences=5)
    msgs = asyncio.run(
        build_chat_history("repl", store=store, config=cfg, compressor=boom)
    )
    # nothing persisted, pointer untouched
    assert store.saved == [] and store.load_summary("repl") == ("", 0)
    # no summary message (still empty); un-summarised messages shown verbatim from upto=0
    assert all(m.role != "system" for m in msgs)
    assert [m.content for m in msgs] == [m["content"] for m in _pairs(4)]


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
    """Drive real turns against the real JSON store with a fake compressor, asserting
    the window slides and the running summary is folded one evicted pair at a time.

    Faithful to the engine's order per turn: build context from history-so-far, then
    record the new pair (build_chat_history is called BEFORE the turn's reply is stored).
    """
    store = ConversationStore(path=tmp_path / "conversations.json")
    cfg = WindowConfig(enabled=True, window_pairs=2, max_sentences=5)
    calls = []  # (old_summary, [evicted contents]) per compressor invocation

    async def fake_compress(old, evicted, _max):
        calls.append((old, [m["content"] for m in evicted]))
        folded = "+".join(m["content"] for m in evicted)
        return f"{old}>{folded}" if old else folded

    def turn(i):
        msgs = asyncio.run(
            build_chat_history("repl", store=store, config=cfg, compressor=fake_compress)
        )
        store.record_turn("repl", f"u{i}", f"a{i}")
        return msgs

    # Turns 0,1: history fits in the 2-pair window → no compression yet.
    assert [m.content for m in turn(0)] == []
    assert [m.content for m in turn(1)] == ["u0", "a0"]
    assert calls == [] and store.load_summary("repl") == ("", 0)

    # Turn 2: 2 pairs present, still within window → no fold, all verbatim.
    assert [m.content for m in turn(2)] == ["u0", "a0", "u1", "a1"]
    assert calls == []

    # Turn 3: 3 pairs present → oldest pair (u0,a0) folds into the summary.
    m3 = turn(3)
    assert calls == [("", ["u0", "a0"])]
    assert store.load_summary("repl") == ("u0+a0", 2)
    assert m3[0].role == "system" and "u0+a0" in m3[0].content
    assert [m.content for m in m3[1:]] == ["u1", "a1", "u2", "a2"]  # last 2 pairs

    # Turn 4: next pair (u1,a1) folds ONTO the existing summary (incremental).
    m4 = turn(4)
    assert calls[-1] == ("u0+a0", ["u1", "a1"])
    assert store.load_summary("repl") == ("u0+a0>u1+a1", 4)
    assert [m.content for m in m4[1:]] == ["u2", "a2", "u3", "a3"]

    # Turn 5: one more eviction; exactly one compressor call per turn past the window.
    m5 = turn(5)
    assert calls[-1] == ("u0+a0>u1+a1", ["u2", "a2"])
    assert store.load_summary("repl") == ("u0+a0>u1+a1>u2+a2", 6)
    assert [m.content for m in m5[1:]] == ["u3", "a3", "u4", "a4"]

    # Three evictions total (turns 3,4,5); window never exceeded 2 verbatim pairs.
    assert len(calls) == 3
