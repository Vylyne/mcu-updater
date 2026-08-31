"""discovery/canbus.py: parsing flashtool's `--query` output, sysfs interface
enumeration, and the subprocess wrapper - all faked, no real CAN hardware or
real flashtool.py required to run in CI.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.discovery import canbus

# --------------------------------------------------------------------------
# parsing flashtool's own stdout - pure function, no subprocess at all
# --------------------------------------------------------------------------


def test_parses_a_klipper_and_a_katapult_line():
    transcript = [
        "Querying for uuids...",
        "Detected UUID: bcb5346fc731, Application: Klipper",
        "Detected UUID: 1a2b3c4d5e6f, Application: Katapult",
        "CANBus UUID Query Complete",
    ]
    sightings = canbus.parse_query_output(transcript, "can0")
    assert [(s.uuid, s.application, s.state, s.interface) for s in sightings] == [
        ("bcb5346fc731", "Klipper", "klipper", "can0"),
        ("1a2b3c4d5e6f", "Katapult", "katapult", "can0"),
    ]


def test_uuid_is_lowercased_even_if_flashtool_prints_upper_case():
    transcript = ["Detected UUID: BCB5346FC731, Application: Klipper"]
    sightings = canbus.parse_query_output(transcript, "can0")
    assert sightings[0].uuid == "bcb5346fc731"


def test_an_unknown_application_still_reads_as_running_something():
    """Per discovery.spec's own rule: "anything else" than a bootloader name
    means an application is running, not an absence. flashtool's "Unknown"
    bucket is exactly that - a CAN node it cannot name, not a node that isn't
    there."""
    transcript = ["Detected UUID: deadbeef0000, Application: Unknown"]
    sightings = canbus.parse_query_output(transcript, "can0")
    assert sightings[0].state == "klipper"


def test_unrelated_output_lines_are_ignored():
    transcript = [
        "Sending query for unassigned CAN nodes...",
        "",
        "some completely unrelated line",
    ]
    assert canbus.parse_query_output(transcript, "can0") == []


def test_query_complete_sentinel_is_recognised():
    assert canbus.QUERY_COMPLETE_RE.search("CANBus UUID Query Complete")
    assert not canbus.QUERY_COMPLETE_RE.search("Detected UUID: aaa, Application: Klipper")


# --------------------------------------------------------------------------
# sysfs interface enumeration - faked directories, never touches /sys
# --------------------------------------------------------------------------


def _make_net_device(root, name: str, type_value: str) -> None:
    dev_dir = root / name
    dev_dir.mkdir(parents=True)
    (dev_dir / "type").write_text(f"{type_value}\n", encoding="utf-8")


def test_finds_a_can_interface_by_its_sysfs_type(paths, fake_root):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can0", canbus.ARPHRD_CAN)
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(net_root))

    assert canbus.list_can_interfaces(fake_paths) == ["can0"]


def test_a_non_can_interface_is_not_reported(paths, fake_root):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "eth0", "1")  # ARPHRD_ETHER, not CAN
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(net_root))

    assert canbus.list_can_interfaces(fake_paths) == []


def test_more_than_one_bridge_gives_more_than_one_interface(paths, fake_root):
    """A host with more than one USB-CAN bridge has more than one independent
    physical bus - each its own interface, never collapsed to a single
    hardcoded guess like `can0`."""
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can0", canbus.ARPHRD_CAN)
    _make_net_device(net_root, "can1", canbus.ARPHRD_CAN)
    _make_net_device(net_root, "eth0", "1")
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(net_root))

    assert canbus.list_can_interfaces(fake_paths) == ["can0", "can1"]


def test_can_interface_metadata_attaches_its_usb_adapter(paths, fake_root, monkeypatch):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can7", canbus.ARPHRD_CAN)
    usb_root = fake_root / "usb"
    adapter = usb_root / "1-2"
    adapter.mkdir(parents=True)
    (adapter / "idVendor").write_text("1d50\n", encoding="utf-8")
    (adapter / "idProduct").write_text("606f\n", encoding="utf-8")
    (adapter / "serial").write_text("ADAPTER-SERIAL\n", encoding="utf-8")
    fake_paths = dataclasses.replace(
        paths, can_sysfs_net=str(net_root), usb_sysfs=str(usb_root)
    )
    device_link = str(net_root / "can7" / "device")
    realpath = canbus.os.path.realpath
    monkeypatch.setattr(
        canbus.os.path,
        "realpath",
        lambda path: str(usb_root / "1-2" / "interface") if path == device_link else realpath(path),
    )

    found = canbus.list_can_interface_metadata(fake_paths)

    assert found[0].name == "can7"
    assert found[0].adapter is not None
    assert found[0].adapter.serial == "ADAPTER-SERIAL"


@pytest.mark.skipif(os.name == "nt", reason="symlink fixtures require POSIX")
def test_can_interface_metadata_follows_the_real_net_device_symlink(paths, fake_root):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can7", canbus.ARPHRD_CAN)
    usb_root = fake_root / "usb"
    adapter = usb_root / "1-2"
    adapter.mkdir(parents=True)
    (adapter / "idVendor").write_text("1d50\n", encoding="utf-8")
    (adapter / "idProduct").write_text("606f\n", encoding="utf-8")
    (adapter / "serial").write_text("ADAPTER-SERIAL\n", encoding="utf-8")
    interface = usb_root / "1-2:1.0"
    interface.mkdir()
    (net_root / "can7" / "device").symlink_to(interface, target_is_directory=True)
    fake_paths = dataclasses.replace(
        paths, can_sysfs_net=str(net_root), usb_sysfs=str(usb_root)
    )

    found = canbus.list_can_interface_metadata(fake_paths)

    assert found[0].adapter is not None
    assert found[0].adapter.serial == "ADAPTER-SERIAL"


def test_no_sysfs_net_directory_at_all_is_not_an_error(paths, fake_root):
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(fake_root / "does-not-exist"))
    assert canbus.list_can_interfaces(fake_paths) == []


# --------------------------------------------------------------------------
# query() / scan_all() - run_streamed itself is faked, never a real
# flashtool.py or real hardware
# --------------------------------------------------------------------------


def _fake_flashtool(paths) -> None:
    os.makedirs(os.path.dirname(paths.flashtool), exist_ok=True)
    with open(paths.flashtool, "w", encoding="utf-8") as fh:
        fh.write("# fake flashtool.py, never actually executed\n")


def test_query_parses_the_fake_subprocess_output(paths, settings, monkeypatch):
    _fake_flashtool(paths)

    def fake_run_streamed(
        cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None
    ):
        assert "-i" in cmd and "can0" in cmd
        assert "--query" in cmd
        if reporter is not None:
            reporter("stdout", "Detected UUID: bcb5346fc731, Application: Klipper")
            reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    sightings = canbus.query(paths, settings, "can0")
    assert len(sightings) == 1
    assert sightings[0].uuid == "bcb5346fc731"
    assert sightings[0].interface == "can0"


def test_read_only_query_still_runs_when_writes_are_dry_run(paths, settings, monkeypatch):
    _fake_flashtool(paths)
    dry_run_settings = dataclasses.replace(settings, dry_run=True)

    def fake_run_streamed(cmd, *, reporter=None, dry_run=False, **kwargs):
        assert dry_run is False
        assert reporter is not None
        reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    assert canbus.query(paths, dry_run_settings, "can0") == []


def test_query_rejects_a_failed_subprocess_even_when_it_printed_a_device(paths, settings, monkeypatch):
    _fake_flashtool(paths)

    def fake_run_streamed(*args, reporter=None, **kwargs):
        assert reporter is not None
        reporter("stdout", "Detected UUID: bcb5346fc731, Application: Klipper")
        reporter("stdout", "CANBus UUID Query Complete")
        return 1

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    with pytest.raises(canbus.CanQueryError) as exc:
        canbus.query(paths, settings, "can0")
    assert exc.value.failure.interface == "can0"
    assert exc.value.failure.returncode == 1


def test_query_rejects_a_transcript_without_the_completion_sentinel(paths, settings, monkeypatch):
    _fake_flashtool(paths)

    def fake_run_streamed(*args, reporter=None, **kwargs):
        assert reporter is not None
        reporter("stdout", "Detected UUID: bcb5346fc731, Application: Klipper")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    with pytest.raises(canbus.CanQueryError) as exc:
        canbus.query(paths, settings, "can0")
    assert exc.value.failure.reason == "completion sentinel missing"


def test_scan_all_keeps_other_interfaces_when_one_query_fails(paths, settings, fake_root, monkeypatch):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can0", canbus.ARPHRD_CAN)
    _make_net_device(net_root, "can1", canbus.ARPHRD_CAN)
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(net_root))
    _fake_flashtool(fake_paths)

    def fake_run_streamed(cmd, *, reporter=None, **kwargs):
        assert reporter is not None
        if "can0" in cmd:
            return 2
        reporter("stdout", "Detected UUID: bcb5346fc731, Application: Klipper")
        reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    result = canbus.scan_all_result(fake_paths, settings)
    assert [sighting.uuid for sighting in result.sightings] == ["bcb5346fc731"]
    assert [(failure.interface, failure.returncode) for failure in result.failures] == [("can0", 2)]


def test_query_with_nothing_unclaimed_returns_an_empty_list(paths, settings, monkeypatch):
    """A clean query (nothing unclaimed on the bus) is not a failure - it's
    reported as "found nothing", same convention as `find_untracked`."""
    _fake_flashtool(paths)

    def fake_run_streamed(
        cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None
    ):
        if reporter is not None:
            reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    assert canbus.query(paths, settings, "can0") == []


def test_query_raises_if_flashtool_itself_is_missing(paths, settings):
    with pytest.raises(FileNotFoundError):
        canbus.query(paths, settings, "can0")


def test_scan_all_sweeps_every_interface_and_merges(paths, settings, fake_root, monkeypatch):
    net_root = fake_root / "sys_class_net"
    _make_net_device(net_root, "can0", canbus.ARPHRD_CAN)
    _make_net_device(net_root, "can1", canbus.ARPHRD_CAN)
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(net_root))
    _fake_flashtool(fake_paths)

    def fake_run_streamed(
        cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None
    ):
        interface = cmd[cmd.index("-i") + 1]
        uuid = f"bcb5346fc73{interface[-1]}"
        if reporter is not None:
            reporter("stdout", f"Detected UUID: {uuid}, Application: Klipper")
            reporter("stdout", "CANBus UUID Query Complete")
        return 0

    monkeypatch.setattr(canbus, "run_streamed", fake_run_streamed)

    interfaces, sightings = canbus.scan_all(fake_paths, settings)
    assert interfaces == ["can0", "can1"]
    assert {s.uuid for s in sightings} == {"bcb5346fc730", "bcb5346fc731"}


def test_scan_all_with_no_interfaces_never_shells_out(paths, settings, fake_root, monkeypatch):
    fake_paths = dataclasses.replace(paths, can_sysfs_net=str(fake_root / "no-such-dir"))

    def boom(*args, **kwargs):
        raise AssertionError("must not run a subprocess with no CAN interface present")

    monkeypatch.setattr(canbus, "run_streamed", boom)

    interfaces, sightings = canbus.scan_all(fake_paths, settings)
    assert interfaces == []
    assert sightings == []
