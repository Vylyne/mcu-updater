"""The kernel's USB-device inventory, shared by discovery and diagnostics."""

from __future__ import annotations

import dataclasses
import os

from ..paths import Paths

_DEFAULT_SYSFS = "/sys/bus/usb/devices"
_DEFAULT_TTY_SYSFS = "/sys/class/tty"


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _read_int(path: str) -> int:
    try:
        return int(_read(path) or 0)
    except ValueError:
        return 0


@dataclasses.dataclass(frozen=True)
class UsbDevice:
    """One physical USB device, named by its stable sysfs topology path."""

    name: str
    path: str
    vendor_id: str | None
    product_id: str | None
    product: str | None
    manufacturer: str | None
    serial: str | None
    speed: str | None
    ports: int


def collect(paths: Paths) -> list[UsbDevice]:
    """Return physical USB devices, excluding their ``:1.0`` interfaces."""
    root = paths.usb_sysfs or _DEFAULT_SYSFS
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    devices = []
    for name in names:
        if ":" in name:
            continue
        path = os.path.join(root, name)
        devices.append(
            UsbDevice(
                name=name,
                path=path,
                vendor_id=_read(os.path.join(path, "idVendor")),
                product_id=_read(os.path.join(path, "idProduct")),
                product=_read(os.path.join(path, "product")),
                manufacturer=_read(os.path.join(path, "manufacturer")),
                serial=_read(os.path.join(path, "serial")),
                speed=_read(os.path.join(path, "speed")),
                ports=_read_int(os.path.join(path, "maxchild")),
            )
        )
    return devices


def device_for_sysfs_path(devices: list[UsbDevice], path: str) -> UsbDevice | None:
    """Return the physical USB ancestor of a sysfs path or symlink target."""
    by_name = {device.name: device for device in devices}
    path = os.path.realpath(path)
    while path:
        name = os.path.basename(path).split(":", 1)[0]
        if name in by_name:
            return by_name[name]
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def device_for_tty(devices: list[UsbDevice], paths: Paths, tty: str) -> UsbDevice | None:
    """Return the physical USB device owning a tty class entry."""
    root = paths.tty_sysfs or _DEFAULT_TTY_SYSFS
    return device_for_sysfs_path(devices, os.path.join(root, tty, "device"))


__all__ = ["UsbDevice", "collect", "device_for_sysfs_path", "device_for_tty"]
