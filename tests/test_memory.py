"""Unit tests for the long-term memory store (lib/memory.py)."""
from lib.memory import MemoryStore


def _store(tmp_path):
    return MemoryStore(path=tmp_path / "memory.json")


def test_empty_store_reads_and_summarizes(tmp_path):
    s = _store(tmp_path)
    assert s.all() == []
    assert s.summary() == ""


def test_add_creates_entry_with_metadata(tmp_path):
    s = _store(tmp_path)
    e = s.add("Prefers Polish for replies", category="preferences")
    assert e["id"].startswith("mem_")
    assert e["text"] == "Prefers Polish for replies"
    assert e["category"] == "preferences"
    assert e["source"] == "user"
    assert e["confidence"] == "high"
    assert e["created_at"] and e["updated_at"]
    assert len(s.all()) == 1


def test_add_persists_across_instances(tmp_path):
    _store(tmp_path).add("Home is in Warsaw", category="facts")
    reopened = MemoryStore(path=tmp_path / "memory.json")
    assert len(reopened.all()) == 1
    assert reopened.by_category("facts")[0]["text"] == "Home is in Warsaw"


def test_exact_duplicate_in_category_is_deduped(tmp_path):
    s = _store(tmp_path)
    first = s.add("Fast walker", category="habits")
    again = s.add("fast walker", category="habits")  # case-insensitive same text
    assert again["id"] == first["id"]
    assert len(s.all()) == 1


def test_same_text_different_category_is_not_deduped(tmp_path):
    s = _store(tmp_path)
    s.add("Coffee", category="preferences")
    s.add("Coffee", category="facts")
    assert len(s.all()) == 2


def test_empty_text_rejected(tmp_path):
    s = _store(tmp_path)
    try:
        s.add("   ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_update_changes_only_given_fields(tmp_path):
    s = _store(tmp_path)
    e = s.add("Likes blue", category="preferences", confidence="low")
    upd = s.update(e["id"], text="Likes deep blue", confidence="high")
    assert upd["text"] == "Likes deep blue"
    assert upd["confidence"] == "high"
    assert upd["category"] == "preferences"  # unchanged
    assert s.update("mem_nope", text="x") is None


def test_delete_removes_entry(tmp_path):
    s = _store(tmp_path)
    e = s.add("temp", category="facts")
    assert s.delete(e["id"]) is True
    assert s.all() == []
    assert s.delete(e["id"]) is False


def test_backend_selection_defaults_to_json_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from lib.memory import _select_backend, MemoryStore
    assert isinstance(_select_backend(), MemoryStore)


def test_summary_groups_by_category_and_tags_inferred(tmp_path):
    s = _store(tmp_path)
    s.add("Prefers transit", category="preferences", source="user")
    s.add("Probably a night owl", category="habits", source="inferred")
    summary = s.summary()
    assert "[preferences]" in summary
    assert "[habits]" in summary
    assert "Prefers transit" in summary
    assert "(inferred)" in summary
