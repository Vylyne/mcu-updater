"""Shared USB sysfs inventory."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from mcu_updater.discovery import usb


def test_usb_topology_runs_from_a_checkout_without_an_installed_package(tmp_path):
    """The documented script command must find this checkout's ``src`` package."""
    root = tmp_path / "usb"
    _usb_device(root, "usb1")

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(Path(__file__).parents[1] / "scripts" / "usb_topology.py"),
            "--root",
            str(root),
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usb1" in result.stdout


def _usb_device(root, name: str, *, serial: str = "", product: str = "") -> None:
    device = root / name
    device.mkdir(parents=True)
    (device / "idVendor").write_text("1d50\n", encoding="utf-8")
    (device / "idProduct").write_text("606f\n", encoding="utf-8")
    (device / "serial").write_text(serial, encoding="utf-8")
    (device / "product").write_text(product, encoding="utf-8")


def test_collect_uses_raw_usb_serial_and_skips_interface_entries(paths, tmp_path, monkeypatch):
    root = tmp_path / "usb"
    _usb_device(root, "1-2", serial="RAW-USB-SERIAL", product="CAN adapter")
    real_listdir = usb.os.listdir
    monkeypatch.setattr(usb.os, "listdir", lambda path: [*real_listdir(path), "1-2:1.0"])
    found = usb.collect(dataclasses.replace(paths, usb_sysfs=str(root)))

    assert [(device.name, device.serial, device.vendor_id, device.product_id) for device in found] == [
        ("1-2", "RAW-USB-SERIAL", "1d50", "606f")
    ]


def test_collect_treats_a_malformed_port_count_as_unknown(paths, tmp_path):
    root = tmp_path / "usb"
    _usb_device(root, "1-2", serial="RAW-USB-SERIAL")
    (root / "1-2" / "maxchild").write_text("not-a-number\n", encoding="utf-8")

    found = usb.collect(dataclasses.replace(paths, usb_sysfs=str(root)))

    assert found[0].ports == 0


def test_collect_strictly_reports_an_unreadable_inventory(paths, monkeypatch):
    monkeypatch.setattr(usb.os, "listdir", lambda _path: (_ for _ in ()).throw(OSError("unreadable")))

    assert usb.collect(paths) == []
    with pytest.raises(OSError, match="unreadable"):
        usb.collect(paths, strict=True)


# --------------------------------------------------------------------------
# serial ports
# --------------------------------------------------------------------------


def _bridge(root, name: str, vid: str = "1a86", pid: str = "7522", *, serial: str = "") -> None:
    device = root / name
    device.mkdir(parents=True)
    (device / "idVendor").write_text(f"{vid}\n", encoding="utf-8")
    (device / "idProduct").write_text(f"{pid}\n", encoding="utf-8")
    (device / "product").write_text("USB Serial\n", encoding="utf-8")
    if serial:
        (device / "serial").write_text(f"{serial}\n", encoding="utf-8")


def _tty(tmp_path, name: str, under: str, interface: str = "if0") -> None:
    """A `/sys/class/tty/<name>/device` link into the USB device `under`, the
    way the kernel lays one out. Skips where the host cannot make a symlink."""
    target = tmp_path / "devices" / under / interface / name
    target.mkdir(parents=True)
    entry = tmp_path / "tty" / name
    entry.mkdir(parents=True)
    try:
        (entry / "device").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")


def _ports(paths, tmp_path):
    here = dataclasses.replace(paths, usb_sysfs=str(tmp_path / "usb"), tty_sysfs=str(tmp_path / "tty"))
    return usb.serial_ports(here)


def test_two_bridges_with_no_serial_are_two_ports(paths, tmp_path):
    """What `/dev/serial/by-id` cannot show: both get the same link there, so
    one of them is invisible."""
    _bridge(tmp_path / "usb", "3-1.6.5")
    _bridge(tmp_path / "usb", "3-1.6.6")
    _tty(tmp_path, "ttyUSB0", "3-1.6.5")
    _tty(tmp_path, "ttyUSB1", "3-1.6.6")

    found = _ports(paths, tmp_path)

    assert [(p.tty, p.path, p.device.name, p.vid_pid) for p in found] == [
        ("ttyUSB0", "/dev/ttyUSB0", "3-1.6.5", ("1a86", "7522")),
        ("ttyUSB1", "/dev/ttyUSB1", "3-1.6.6", ("1a86", "7522")),
    ]


def test_ports_are_in_plug_order_not_string_order(paths, tmp_path):
    for n in (10, 2, 0):
        _bridge(tmp_path / "usb", f"1-{n}")
        _tty(tmp_path, f"ttyUSB{n}", f"1-{n}")
    _bridge(tmp_path / "usb", "1-9", "1d50", "614e", serial="MCU")
    _tty(tmp_path, "ttyACM0", "1-9")

    assert [p.tty for p in _ports(paths, tmp_path)] == ["ttyACM0", "ttyUSB0", "ttyUSB2", "ttyUSB10"]


def test_a_tty_that_is_not_usb_is_not_a_port(paths, tmp_path):
    """A UART, a console and a pty are all under /sys/class/tty; so is a
    `ttyUSB` whose device is on no USB device this host lists."""
    _bridge(tmp_path / "usb", "1-1")
    _tty(tmp_path, "ttyUSB0", "1-1")
    _tty(tmp_path, "ttyUSB1", "not-a-usb-device")
    _tty(tmp_path, "ttyS0", "1-1")
    (tmp_path / "tty" / "ttyAMA0").mkdir()

    assert [p.tty for p in _ports(paths, tmp_path)] == ["ttyUSB0"]


def test_usb_ids_compare_as_sysfs_writes_them(paths, tmp_path):
    _bridge(tmp_path / "usb", "1-1", "1A86", "7522")
    _tty(tmp_path, "ttyUSB0", "1-1")

    assert _ports(paths, tmp_path)[0].vid_pid == ("1a86", "7522")


def test_no_tty_class_at_all_is_no_ports(paths, tmp_path):
    assert _ports(paths, tmp_path) == []


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS reads ':' as a stream separator")
def test_a_port_is_named_by_its_usb_interface(paths, tmp_path):
    """Real sysfs runs the tty through `<port>:<config>.<interface>`; that
    component is the port's stable name, and its prefix is the device."""
    _bridge(tmp_path / "usb", "3-1.6.5")
    _tty(tmp_path, "ttyUSB0", "3-1.6.5", "3-1.6.5:1.0")
    _tty(tmp_path, "ttyUSB1", "3-1.6.5", "3-1.6.5:1.1")

    assert [(p.device.name, p.interface) for p in _ports(paths, tmp_path)] == [
        ("3-1.6.5", "3-1.6.5:1.0"),
        ("3-1.6.5", "3-1.6.5:1.1"),
    ]
