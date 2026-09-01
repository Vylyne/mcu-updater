"""fw.canbus.scan / fw.canbus.add / fw.canbus.remove.

The CAN counterpart to `fw.bus.scan` (the untracked-USB-serial "on bus, want
to adopt it?" view) and to `fw.serial.add`/`fw.serial.remove`. Every
subprocess this touches (`flashtool.py --query`) is faked - no real katapult
checkout and no CAN hardware is required to run in CI.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import ERR_INVALID_PARAMS, RpcError
from mcu_updater.discovery import canbus as canbus_mod


@pytest.fixture
def api(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    return Api(paths)


def _make_can_interface(fake_root, name: str) -> str:
    net_root = fake_root / "sys_class_net"
    dev_dir = net_root / name
    dev_dir.mkdir(parents=True)
    (dev_dir / "type").write_text(f"{canbus_mod.ARPHRD_CAN}\n", encoding="utf-8")
    return str(net_root)


def _make_flashtool(paths) -> None:
    os.makedirs(os.path.dirname(paths.flashtool), exist_ok=True)
    with open(paths.flashtool, "w", encoding="utf-8") as fh:
        fh.write("# fake flashtool.py, never actually executed\n")


def _fake_query_answering(uuid: str, application: str = "Klipper"):
    def fake_run_streamed(
        cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None
    ):
        if reporter is not None:
            reporter("stdout", f"Detected UUID: {uuid}, Application: {application}")
            reporter("stdout", "CANBus UUID Query Complete")
        return 0

    return fake_run_streamed


# --------------------------------------------------------------------------
# fw.canbus.scan
# --------------------------------------------------------------------------


def test_no_can_interfaces_is_reported_not_raised(api):
    res = api.dispatch("fw.canbus.scan")
    assert res["interfaces"] == []
    assert res["failures"] == []
    assert res["devices"] == []
    assert res["count"] == 0
    assert "can interface" in (res["message"] or "").lower()


def test_missing_flashtool_is_reported_not_raised(api, fake_root):
    net_root = _make_can_interface(fake_root, "can0")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)

    res = api.dispatch("fw.canbus.scan")
    assert res["interfaces"] == [{"name": "can0", "adapter": None}]
    assert res["devices"] == []
    assert "flashtool.py" in (res["message"] or "")


def test_an_unclaimed_board_is_reported_untracked(api, fake_root, monkeypatch):
    net_root = _make_can_interface(fake_root, "can0")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)
    _make_flashtool(api.paths)
    monkeypatch.setattr(canbus_mod, "run_streamed", _fake_query_answering("bcb5346fc731"))

    res = api.dispatch("fw.canbus.scan")
    assert res["count"] == 1
    device = res["devices"][0]
    assert device["uuid"] == "bcb5346fc731"
    assert device["interface"] == "can0"
    assert device["application"] == "Klipper"
    assert device["state"] == "klipper"
    assert device["tracked_by"] is None
    assert device["ignored"] is False


def test_canbus_ignore_marks_every_sighting_but_keeps_it_listed(
    api, fake_root, monkeypatch
):
    net_root = _make_can_interface(fake_root, "can0")
    _make_can_interface(fake_root, "can1")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)
    _make_flashtool(api.paths)
    monkeypatch.setattr(
        canbus_mod, "run_streamed", _fake_query_answering("bcb5346fc731")
    )

    first = api.dispatch("fw.canbus.ignore", {"uuid": "bcb5346fc731"})
    second = api.dispatch("fw.canbus.ignore", {"uuid": "bcb5346fc731"})
    devices = api.dispatch("fw.canbus.scan")["devices"]

    assert first == second == {"uuid": "bcb5346fc731", "ignored": True}
    assert len(devices) == 2
    assert all(device["ignored"] is True for device in devices)
    assert api.settings().ignored_canbus_uuids == ["bcb5346fc731"]


def test_canbus_unignore_reverses_it_and_is_idempotent(api):
    api.dispatch("fw.canbus.ignore", {"uuid": "bcb5346fc731"})

    first = api.dispatch("fw.canbus.unignore", {"uuid": "bcb5346fc731"})
    second = api.dispatch("fw.canbus.unignore", {"uuid": "bcb5346fc731"})

    assert first == second == {"uuid": "bcb5346fc731", "ignored": False}
    assert api.settings().ignored_canbus_uuids == []


@pytest.mark.parametrize("method", ["fw.canbus.ignore", "fw.canbus.unignore"])
@pytest.mark.parametrize("args", [{}, {"uuid": ""}, {"uuid": "  "}])
def test_canbus_ignore_methods_require_a_uuid(api, method, args):
    with pytest.raises(RpcError) as exc:
        api.dispatch(method, args)
    assert exc.value.code == ERR_INVALID_PARAMS


def test_canbus_ignore_announces_only_an_actual_change(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    changes: list[int] = []
    api = Api(paths, on_change=lambda: changes.append(1))

    api.dispatch("fw.canbus.ignore", {"uuid": "bcb5346fc731"})
    api.dispatch("fw.canbus.ignore", {"uuid": "bcb5346fc731"})
    api.dispatch("fw.canbus.unignore", {"uuid": "bcb5346fc731"})

    assert len(changes) == 2


@pytest.mark.skipif(os.name == "nt", reason="symlink fixtures require POSIX")
def test_scan_serializes_usb_adapter_identity(api, fake_root, monkeypatch):
    net_root = _make_can_interface(fake_root, "can7")
    usb_root = fake_root / "usb"
    adapter = usb_root / "1-2"
    adapter.mkdir(parents=True)
    (adapter / "idVendor").write_text("1d50\n", encoding="utf-8")
    (adapter / "idProduct").write_text("606f\n", encoding="utf-8")
    (adapter / "serial").write_text("ADAPTER-SERIAL\n", encoding="utf-8")
    interface = usb_root / "1-2:1.0"
    interface.mkdir()
    (fake_root / "sys_class_net" / "can7" / "device").symlink_to(
        interface, target_is_directory=True
    )
    api.paths = dataclasses.replace(
        api.paths, can_sysfs_net=net_root, usb_sysfs=str(usb_root)
    )
    _make_flashtool(api.paths)
    monkeypatch.setattr(canbus_mod, "run_streamed", _fake_query_answering("bcb5346fc731"))

    interface_info = api.dispatch("fw.canbus.scan")["interfaces"][0]

    assert interface_info["name"] == "can7"
    assert interface_info["adapter"]["serial"] == "ADAPTER-SERIAL"


def test_nothing_unclaimed_answering_is_reported_not_raised(api, fake_root, monkeypatch):
    net_root = _make_can_interface(fake_root, "can0")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)
    _make_flashtool(api.paths)

    def fake_run_streamed(
        cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None
    ):
        if reporter is not None:
            reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus_mod, "run_streamed", fake_run_streamed)

    res = api.dispatch("fw.canbus.scan")
    assert res["count"] == 0
    assert res["devices"] == []
    assert "no unclaimed" in (res["message"] or "").lower()


def test_a_tracked_uuid_is_named(api, fake_root, monkeypatch):
    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})

    net_root = _make_can_interface(fake_root, "can0")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)
    _make_flashtool(api.paths)
    monkeypatch.setattr(canbus_mod, "run_streamed", _fake_query_answering("bcb5346fc731"))

    device = api.dispatch("fw.canbus.scan")["devices"][0]
    assert device["tracked_by"] == "hexadistrofusion"


def test_one_failed_interface_keeps_other_results(api, fake_root, monkeypatch):
    net_root = _make_can_interface(fake_root, "can0")
    _make_can_interface(fake_root, "can1")
    api.paths = dataclasses.replace(api.paths, can_sysfs_net=net_root)
    _make_flashtool(api.paths)

    def fake_run_streamed(cmd, *, reporter=None, **kwargs):
        interface = cmd[cmd.index("-i") + 1]
        if interface == "can0":
            return 2
        assert reporter is not None
        reporter("stdout", "Detected UUID: bcb5346fc731, Application: Klipper")
        reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus_mod, "run_streamed", fake_run_streamed)

    res = api.dispatch("fw.canbus.scan")

    assert [device["uuid"] for device in res["devices"]] == ["bcb5346fc731"]
    assert res["failures"] == [
        {
            "interface": "can0",
            "reason": "flashtool exited unsuccessfully",
            "returncode": 2,
        }
    ]


def test_the_scan_never_raises(api, fake_root, monkeypatch):
    """Every branch must return a renderable answer - a scan that throws
    leaves the panel with an error banner and no idea what to tell the user,
    same requirement fw.dfu.scan/fw.bootsel.scan already hold themselves to."""
    for setup in (
        lambda: None,  # no interfaces at all
        lambda: dataclasses.replace(
            api.paths, can_sysfs_net=_make_can_interface(fake_root, "can0")
        ),  # interface present, no flashtool
    ):
        maybe_paths = setup()
        if maybe_paths is not None:
            api.paths = maybe_paths
        res = api.dispatch("fw.canbus.scan")
        assert set(res) >= {"interfaces", "devices", "failures", "count", "message"}


def test_the_scan_is_available_to_a_read_only_agent(api):
    caps = api.dispatch("fw.ping")["capabilities"]
    assert "fw.canbus.scan" in caps
    assert "fw.canbus.add" in caps
    assert "fw.canbus.remove" in caps
    assert "fw.canbus.ignore" in caps
    assert "fw.canbus.unignore" in caps


# --------------------------------------------------------------------------
# fw.canbus.add / fw.canbus.remove
# --------------------------------------------------------------------------


def test_canbus_add_tracks_the_uuid_and_announces_it(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    changes: list[int] = []
    api = Api(paths, on_change=lambda: changes.append(1))

    res = api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    assert res["added"] is True
    assert "bcb5346fc731" in api.registry().get("hexadistrofusion").canbus_uuids
    assert len(changes) == 1


def test_canbus_add_is_idempotent(api):
    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    again = api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    assert again["added"] is False
    assert api.registry().get("hexadistrofusion").canbus_uuids.count("bcb5346fc731") == 1


def test_canbus_add_refuses_a_uuid_tracked_under_another_type(api):
    """One board under two types would get flashed twice with different
    firmware - same reason `fw.serial.add` refuses this for by-id serials."""
    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.canbus.add", {"name": "OctopusMAXEZ", "uuid": "bcb5346fc731"})
    assert exc.value.data["code"] == "uuid_tracked_elsewhere"
    assert "hexadistrofusion" in exc.value.data["data"]["tracked_under"]


def test_canbus_add_refuses_an_unknown_type(api):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.canbus.add", {"name": "nope", "uuid": "bcb5346fc731"})
    assert exc.value.data["code"] == "unknown_type"


def test_canbus_add_has_no_is_mcu_check(api):
    """Unlike fw.serial.add, there is no bridge-chip case to guard against -
    every CAN admin responder names its own application, so nothing here
    ever refuses with `not_an_mcu`."""
    res = api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "aabbccddeeff"})
    assert res["added"] is True


@pytest.mark.parametrize("method", ["fw.canbus.add", "fw.canbus.remove"])
@pytest.mark.parametrize(
    "args", [{}, {"name": "hexadistrofusion"}, {"uuid": "X"}, {"name": " ", "uuid": "X"}]
)
def test_canbus_methods_require_both_arguments(api, method, args):
    with pytest.raises(RpcError) as exc:
        api.dispatch(method, args)
    assert exc.value.code == ERR_INVALID_PARAMS


def test_canbus_remove_reports_whether_it_acted(api):
    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    first = api.dispatch(
        "fw.canbus.remove", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"}
    )
    assert first["removed"] is True
    again = api.dispatch(
        "fw.canbus.remove", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"}
    )
    assert again["removed"] is False


def test_canbus_remove_touches_nothing_but_the_registry(api, paths):
    os.makedirs(paths.artifact_dir("hexadistrofusion"), exist_ok=True)
    binary = paths.bin_file("hexadistrofusion", "klipper")
    with open(binary, "wb") as fh:
        fh.write(b"\0" * 64)

    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})
    api.dispatch("fw.canbus.remove", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})

    assert os.path.exists(binary)
    assert "hexadistrofusion" in api.registry().names()


def test_a_mutation_preserves_comments_and_other_sections(api, paths):
    api.dispatch("fw.canbus.add", {"name": "hexadistrofusion", "uuid": "bcb5346fc731"})

    with open(paths.main_config, encoding="utf-8") as fh:
        out = fh.read()
    assert "# Representative registry for tests." in out
    assert "src/Makefile -> src-y += buffer.c" in out
    assert out.count("[type hexadistrofusion]") == 1
