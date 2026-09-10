"""Admin access gating: owner + members of the `admins` group (`is_admin`).

Note: `lib.domain.__init__` exports a `principals` singleton whose name shadows the
`lib.domain.principals` submodule, so we patch the module global via `sys.modules`.
"""
import sys
import tempfile
from pathlib import Path

from lib.domain.principals import (
    PrincipalStore, is_admin, new_user_id,
    ADMINS_GROUP_ID, ADMINS_GROUP_NAME, DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, OWNER_USER_ID,
)

_PMOD = sys.modules["lib.domain.principals"]


def _store(tmp_path, monkeypatch):
    ps = PrincipalStore(tmp_path / "p.json")
    monkeypatch.setattr(_PMOD, "principals", ps)
    ps.add_group(ADMINS_GROUP_ID, ADMINS_GROUP_NAME, 100)
    ps.add_group(DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, 0)
    return ps


def test_owner_is_always_admin(tmp_path, monkeypatch):
    ps = _store(tmp_path, monkeypatch)
    ps.add_user(OWNER_USER_ID, "Owner")
    assert is_admin(OWNER_USER_ID) is True


def test_admins_group_membership_grants_access(tmp_path, monkeypatch):
    ps = _store(tmp_path, monkeypatch)
    u = new_user_id()
    ps.add_user(u, "Ala")
    assert is_admin(u) is False
    ps.add_membership(u, ADMINS_GROUP_ID)
    assert is_admin(u) is True


def test_non_admin_and_unknown(tmp_path, monkeypatch):
    ps = _store(tmp_path, monkeypatch)
    u = new_user_id()
    ps.add_user(u, "Bob")
    ps.add_membership(u, DEFAULT_GROUP_ID)  # a normal group, not admins
    assert is_admin(u) is False
    assert is_admin(None) is False
    assert is_admin("nope") is False
