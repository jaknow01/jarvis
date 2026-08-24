"""Unit tests for the conversation-history store (lib/domain/conversation.py).

JSON backend only — no live database required."""
import json

from lib.domain.conversation import ConversationStore


def _store(tmp_path):
    return ConversationStore(path=tmp_path / "conversations.json")


def test_empty_store_has_no_history(tmp_path):
    s = _store(tmp_path)
    assert s.load_history("repl") == []


def test_first_turn_creates_conversation_and_two_messages(tmp_path):
    s = _store(tmp_path)
    s.record_turn("repl", "Cześć", "Witaj")
    h = s.load_history("repl")
    assert [(m["role"], m["content"]) for m in h] == [
        ("user", "Cześć"), ("assistant", "Witaj")
    ]


def test_history_is_ordered_across_turns(tmp_path):
    s = _store(tmp_path)
    s.record_turn("repl", "Cześć", "Witaj")
    s.record_turn("repl", "Pogoda?", "Słonecznie")
    h = s.load_history("repl")
    assert [(m["role"], m["content"]) for m in h] == [
        ("user", "Cześć"), ("assistant", "Witaj"),
        ("user", "Pogoda?"), ("assistant", "Słonecznie"),
    ]


def test_separate_channels_stay_isolated(tmp_path):
    s = _store(tmp_path)
    s.record_turn("repl", "a", "b")
    s.record_turn("messenger:1", "c", "d")
    assert [m["content"] for m in s.load_history("repl")] == ["a", "b"]
    assert [m["content"] for m in s.load_history("messenger:1")] == ["c", "d"]


def test_persists_across_instances(tmp_path):
    _store(tmp_path).record_turn("repl", "x", "y", trace_id="tr-1")
    reopened = ConversationStore(path=tmp_path / "conversations.json")
    h = reopened.load_history("repl")
    assert [m["content"] for m in h] == ["x", "y"]


def test_assistant_message_carries_trace_id(tmp_path):
    s = _store(tmp_path)
    s.record_turn("repl", "q", "a", trace_id="trace-42")
    raw = json.loads((tmp_path / "conversations.json").read_text(encoding="utf-8"))
    msgs = raw["conversations"]["repl"]["messages"]
    assert msgs[0]["role"] == "user" and msgs[0]["mlflow_trace_id"] == ""
    assert msgs[1]["role"] == "assistant" and msgs[1]["mlflow_trace_id"] == "trace-42"


def test_backend_selection_defaults_to_json_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from lib.domain.conversation import _select_backend, ConversationStore
    assert isinstance(_select_backend(), ConversationStore)
