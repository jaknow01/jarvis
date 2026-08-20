"""Tests that device secrets (ip/local_key/dev_id) are never exposed to the model,
and that name-based device resolution works."""
import types

from lib.smart_device import SmartDevice
from lib import tools


def _device(name="Telewizor"):
    return SmartDevice(
        name=name, dev_id="bfsecret123", ip="192.168.0.131",
        local_key="TOPSECRETKEY0000", room="living_room", zones=["entertainment_zone"],
    )


def test_describe_as_json_hides_secrets():
    di = _device().describe_as_json()
    assert set(di.keys()) == {"name", "room", "zones"}
    for secret in ("ip", "local_key", "dev_id", "port", "version"):
        assert secret not in di


def test_get_status_device_info_has_no_secrets():
    dev = _device()
    # translate a fake raw status without touching the network
    out = dev._translate_status({"dps": {"20": True, "21": "white", "22": 40}})
    assert "local_key" not in out["device_info"]
    assert "ip" not in out["device_info"]
    assert out["device_info"] == {"name": "Telewizor", "room": "living_room", "zones": ["entertainment_zone"]}


def _ctx(devices):
    return types.SimpleNamespace(context=types.SimpleNamespace(devices=devices))


def test_resolve_devices_exact_and_case_insensitive():
    reg = {"Telewizor": _device("Telewizor"), "Łóżko": _device("Łóżko")}
    ctx = _ctx(reg)
    found, unknown = tools._resolve_devices(ctx, ["Telewizor", "łóżko"])
    assert [d.name for d in found] == ["Telewizor", "Łóżko"]
    assert unknown == []


def test_resolve_devices_reports_unknown():
    ctx = _ctx({"Telewizor": _device("Telewizor")})
    found, unknown = tools._resolve_devices(ctx, ["Telewizor", "Ghost"])
    assert [d.name for d in found] == ["Telewizor"]
    assert unknown == ["Ghost"]


def test_resolve_devices_empty_registry():
    ctx = _ctx({})
    found, unknown = tools._resolve_devices(ctx, ["Telewizor"])
    assert found == []
    assert unknown == ["Telewizor"]
