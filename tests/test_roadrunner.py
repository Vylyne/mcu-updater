"""Roadrunner direct-USB maintenance discovery and control."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.discovery import roadrunner

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-0123456789ABCDEFGHJKMNPQRS"


def _info(*, provisioned: bool = False, serial: str = UNPROVISIONED) -> dict[str, object]:
    return {
        "protocol": 1,
        "provisioned": provisioned,
        "store_state": 1 if provisioned else 0,
        "transport": 1,
        "led_order": 1,
        "model": "roadrunner-v1",
        "fw_version": "dev",
        "serial": serial,
        "flash_uid": "50543165187A4D1C",
    }


def _candidate(paths, fake_root: Path, monkeypatch) -> Path:
    entry = fake_root / "bus" / f"usb-Vylyne_Roadrunner_{UNPROVISIONED}-if00"
    entry.touch()
    tty = fake_root / "tty" / "ttyACM0" / "device"
    tty.mkdir(parents=True)
    monkeypatch.setattr(roadrunner.os.path, "realpath", lambda path, **_kwargs: str(fake_root / "ttyACM0"))
    usb_root = fake_root / "usb" / "1-3"
    usb_root.mkdir(parents=True)
    for name, value in {
        "idVendor": "2e8a\n",
        "idProduct": "000a\n",
        "manufacturer": "Vylyne\n",
        "product": "Roadrunner\n",
        "serial": "not-an-identity\n",
    }.items():
        (usb_root / name).write_text(value, encoding="utf-8")
    topology = roadrunner.usb.UsbDevice(
        name="1-3", path=str(usb_root), vendor_id="2e8a", product_id="000a",
        product="Roadrunner", manufacturer="Vylyne", serial="not-an-identity", speed="12", ports=0,
    )
    monkeypatch.setattr(roadrunner.usb, "collect", lambda _paths: [topology])
    monkeypatch.setattr(
        roadrunner.usb,
        "device_for_tty",
        lambda inventory, _paths, _tty: inventory[0],
    )
    return entry


def test_discovery_accepts_only_confirmed_unprovisioned_roadrunner(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info())

    found = roadrunner.discover(paths)

    assert len(found) == 1
    assert found[0].serial == UNPROVISIONED
    assert found[0].port == str(fake_root / "ttyACM0")
    assert found[0].topology.name == "1-3"
    assert not hasattr(found[0], "flash_uid")


@pytest.mark.parametrize(
    "bad",
    [
        {"protocol": 2},
        {"model": "not-roadrunner"},
        {"provisioned": True, "serial": PROVISIONED},
        {"serial": "RR-UNPROVISIONED-AAAAAAAAAAAAAAAA"},
    ],
)
def test_discovery_refuses_unconfirmed_info(paths, fake_root, monkeypatch, bad):
    _candidate(paths, fake_root, monkeypatch)
    data = _info()
    data.update(bad)
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: data)
    assert roadrunner.discover(paths) == []


def test_helper_json_failure_is_distinct_from_a_bad_info_response(paths, monkeypatch):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, '{"error":"bad_crc"}\n', "")

    monkeypatch.setattr(roadrunner.subprocess, "run", run)
    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner._helper(paths, "info", "/dev/ttyACM0")
    assert exc.value.code == "roadrunner_helper"
    assert exc.value.data["error"] == "bad_crc"


def test_helper_rejects_a_bad_wire_crc_before_emitting_json():
    spec = importlib.util.spec_from_file_location(
        "roadrunner_usb", Path(__file__).parents[1] / "scripts" / "roadrunner_usb.py"
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class Port:
        def read(self, count):
            return {5: b"RR\x01\x81\x01", 1: b"\x00"}.get(count, b"")

    with pytest.raises(helper.ProtocolError, match="CRC"):
        helper.read_response(Port(), 1)


def test_explicit_probe_reports_invalid_info_not_no_candidate(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    bad_info = _info()
    bad_info["model"] = "wrong-model"
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: bad_info)
    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.find_untracked(paths, UNPROVISIONED)
    assert exc.value.code == "roadrunner_invalid_probe"


def test_provision_reenumerates_on_the_same_transient_topology(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    initial = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    expected = "RR-0123456789ABCDEFGHJKMNPQRS"
    calls: list[tuple[str, str, str | None]] = []

    def helper(_paths, operation, port, uuid_hex=None):
        calls.append((operation, port, uuid_hex))
        if operation == "provision":
            return {"serial": expected}
        return _info(provisioned=True, serial=expected)

    monkeypatch.setattr(roadrunner, "_helper", helper)
    monkeypatch.setattr(
        roadrunner,
        "_await_same_topology",
        lambda *_args, **_kwargs: roadrunner.RoadrunnerDevice(expected, "/dev/ttyACM1", _topology()),
    )
    result = roadrunner.provision_roadrunner(paths, initial, bytes(range(16)))
    assert result.serial == expected
    assert calls == [("provision", "/dev/ttyACM0", "000102030405060708090a0b0c0d0e0f")]


def test_agent_provisions_once_without_tracking(paths, monkeypatch):
    api = Api(paths)
    original = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    result = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM1", _topology())
    generated: list[int] = []
    calls: list[object] = []
    monkeypatch.setattr(roadrunner, "find_untracked", lambda _paths, serial: original)
    monkeypatch.setattr(
        roadrunner,
        "provision_roadrunner",
        lambda _paths, device, uuid: calls.append((device, uuid)) or result,
    )
    monkeypatch.setattr(
        "mcu_updater.agent.methods.status.secrets.token_bytes",
        lambda size: generated.append(size) or bytes(range(size)),
    )

    response = api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})

    assert response == {"serial": PROVISIONED, "prior_serial": UNPROVISIONED, "state": "provisioned"}
    assert generated == [16]
    assert calls == [(original, bytes(range(16)))]
    assert api.registry().find_types_for_serial(PROVISIONED) == []


def test_agent_refuses_ambiguous_candidate_before_writing(paths, monkeypatch):
    api = Api(paths)
    wrote: list[bool] = []
    monkeypatch.setattr(
        roadrunner,
        "find_untracked",
        lambda *_args: (_ for _ in ()).throw(
            roadrunner._error("roadrunner_ambiguous", "ambiguous")
        ),
    )
    monkeypatch.setattr(roadrunner, "provision_roadrunner", lambda *_args: wrote.append(True))
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.data["code"] == "roadrunner_ambiguous"
    assert wrote == []


def test_agent_does_not_retry_after_a_provision_timeout(paths, monkeypatch):
    api = Api(paths)
    original = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    writes: list[bool] = []
    monkeypatch.setattr(roadrunner, "find_untracked", lambda *_args: original)
    monkeypatch.setattr(
        roadrunner,
        "provision_roadrunner",
        lambda *_args: writes.append(True)
        or (_ for _ in ()).throw(roadrunner._error("roadrunner_timeout", "timed out")),
    )
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.data["code"] == "roadrunner_timeout"
    assert writes == [True]


def test_agent_clear_returns_to_unprovisioned_without_tracking(paths, monkeypatch):
    api = Api(paths)
    original = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    cleared = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM1", _topology())
    monkeypatch.setattr(roadrunner, "find_provisioned", lambda *_args: original)
    monkeypatch.setattr(roadrunner, "clear_roadrunner", lambda *_args: cleared)
    response = api.dispatch("fw.roadrunner.clear", {"serial": PROVISIONED})
    assert response == {"serial": UNPROVISIONED, "prior_serial": PROVISIONED, "state": "unprovisioned"}
    assert api.registry().find_types_for_serial(UNPROVISIONED) == []


def _topology():
    return roadrunner.usb.UsbDevice(
        name="1-3", path="/sys/bus/usb/devices/1-3", vendor_id="2e8a", product_id="000a",
        product="Roadrunner", manufacturer="Vylyne", serial="diagnostic", speed="12", ports=0,
    )
