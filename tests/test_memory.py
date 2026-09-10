"""Unit tests for the long-term memory store (lib/memory.py)."""
from lib.domain.memory import MemoryStore


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
    from lib.domain.memory import _select_backend, MemoryStore
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


# -- summary() TTL cache on the proxy (keeps it off the per-turn hot path) ------------
class _CountingBackend:
    """Fake backend counting summary() reads; add/update/delete return trivially."""
    def __init__(self):
        self.summary_calls = 0
        self._value = "V0"

    def summary(self, cap=40, user_id=None):
        self.summary_calls += 1
        return self._value

    def add(self, *a, **k):
        self._value = "V1"
        return {}

    def update(self, *a, **k):
        return {}

    def delete(self, *a, **k):
        return True


def _proxy_with(backend, monkeypatch):
    from lib.domain.memory import _MemoryProxy
    p = _MemoryProxy()
    p._impl = backend  # bypass lazy backend selection
    return p


def test_summary_cache_serves_from_cache_within_ttl(monkeypatch):
    monkeypatch.setenv("MEMORY_SUMMARY_CACHE_TTL_SECONDS", "60")
    be = _CountingBackend()
    p = _proxy_with(be, monkeypatch)
    assert p.summary() == "V0"
    assert p.summary() == "V0"
    assert be.summary_calls == 1  # second read served from cache


def test_summary_cache_invalidated_on_write(monkeypatch):
    monkeypatch.setenv("MEMORY_SUMMARY_CACHE_TTL_SECONDS", "60")
    be = _CountingBackend()
    p = _proxy_with(be, monkeypatch)
    assert p.summary() == "V0"
    p.add("something new")           # write must invalidate the cache
    assert p.summary() == "V1"       # fresh read reflects the write
    assert be.summary_calls == 2


def test_summary_cache_disabled_with_zero_ttl(monkeypatch):
    monkeypatch.setenv("MEMORY_SUMMARY_CACHE_TTL_SECONDS", "0")
    be = _CountingBackend()
    p = _proxy_with(be, monkeypatch)
    p.summary(); p.summary()
    assert be.summary_calls == 2  # no caching → every call hits the store
