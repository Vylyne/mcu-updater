"""Shared USB sysfs inventory."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

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
    monkeypatch.setattr(
        usb.os, "listdir", lambda path: [*real_listdir(path), "1-2:1.0"]
    )
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
