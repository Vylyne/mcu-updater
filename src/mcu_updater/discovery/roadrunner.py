"""Conservative discovery and direct-USB maintenance for Roadrunner."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from ..errors import UpdaterError
from . import usb
from .spec import STATE_KLIPPER, Sighting

if TYPE_CHECKING:
    from ..flashers.spec import Bench
    from ..paths import Paths


UNPROVISIONED_RE = re.compile(r"^RR-UNPROVISIONED-[0-9A-F]{16}$")
PROVISIONED_RE = re.compile(r"^RR-[0-9A-HJKMNP-TV-Z]{26}$")
_ENTRY_RE = re.compile(r"^usb-Vylyne_Roadrunner_(RR-[A-Z0-9-]+)-if00$")
_HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "scripts", "roadrunner_usb.py")
REENUMERATE_TIMEOUT = 15.0


class RoadrunnerError(UpdaterError):
    code = "roadrunner_helper"


@dataclasses.dataclass(frozen=True)
class RoadrunnerDevice:
    """A confirmed device; topology is a one-operation handoff, never identity."""

    serial: str
    port: str
    topology: usb.UsbDevice


def _error(code: str, message: str, **data: object) -> RoadrunnerError:
    error = RoadrunnerError(message, **data)
    error.code = code
    return error


def _helper(paths: Paths, operation: str, port: str, uuid_hex: str | None = None) -> dict[str, object]:
    argv = [sys.executable, _HELPER, operation, port]
    if uuid_hex is not None:
        argv.append(uuid_hex)
    try:
        result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("roadrunner_helper", f"Roadrunner helper could not run: {exc}") from exc
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise _error("roadrunner_helper", "Roadrunner helper returned invalid JSON") from exc
    if not isinstance(data, dict) or result.returncode:
        detail = data.get("error") if isinstance(data, dict) else None
        raise _error("roadrunner_helper", "Roadrunner helper failed", error=detail or result.stderr.strip())
    return data


def _entry_candidates(paths: Paths) -> list[tuple[str, str, usb.UsbDevice]]:
    try:
        names = sorted(os.listdir(paths.serial_by_id))
    except OSError:
        return []
    inventory = usb.collect(paths)
    candidates: list[tuple[str, str, usb.UsbDevice]] = []
    for name in names:
        match = _ENTRY_RE.fullmatch(name)
        if match is None:
            continue
        path = os.path.join(paths.serial_by_id, name)
        port = os.path.realpath(path)
        topology = usb.device_for_tty(inventory, paths, os.path.basename(port))
        if topology is None or topology.manufacturer != "Vylyne" or topology.product != "Roadrunner":
            continue
        candidates.append((match.group(1), port, topology))
    return candidates


def _valid_info(data: dict[str, object], serial: str, *, provisioned: bool) -> bool:
    return (
        data.get("protocol") == 1
        and data.get("model") == "roadrunner-v1"
        and data.get("serial") == serial
        and data.get("provisioned") is provisioned
    )


def discover(paths: Paths) -> list[RoadrunnerDevice]:
    """Read-only projection of confirmed, unprovisioned Roadrunners."""
    out: list[RoadrunnerDevice] = []
    for serial, port, topology in _entry_candidates(paths):
        if not UNPROVISIONED_RE.fullmatch(serial):
            continue
        try:
            info = _helper(paths, "info", port)
        except RoadrunnerError:
            continue
        if _valid_info(info, serial, provisioned=False):
            out.append(RoadrunnerDevice(serial, port, topology))
    return out


def find_untracked(paths: Paths, serial: str) -> RoadrunnerDevice:
    if not UNPROVISIONED_RE.fullmatch(serial):
        raise _error("roadrunner_invalid_probe", "Roadrunner serial is not an unprovisioned canonical serial")
    candidates = [item for item in _entry_candidates(paths) if item[0] == serial]
    if not candidates:
        raise _error("roadrunner_no_candidate", "No confirmed unprovisioned Roadrunner matched that serial", serial=serial)
    if len(candidates) != 1:
        raise _error("roadrunner_ambiguous", "More than one Roadrunner matched that serial", serial=serial)
    candidate_serial, port, topology = candidates[0]
    info = _helper(paths, "info", port)
    if not _valid_info(info, serial, provisioned=False):
        raise _error("roadrunner_invalid_probe", "Roadrunner INFO did not confirm the unprovisioned descriptor", serial=serial)
    return RoadrunnerDevice(candidate_serial, port, topology)


def find_provisioned(paths: Paths, serial: str) -> RoadrunnerDevice:
    """Confirm one already-provisioned Roadrunner without writing to it."""
    if not PROVISIONED_RE.fullmatch(serial):
        raise _error("roadrunner_invalid_probe", "Roadrunner serial is not a provisioned canonical serial")
    candidates = [item for item in _entry_candidates(paths) if item[0] == serial]
    if not candidates:
        raise _error("roadrunner_no_candidate", "No confirmed provisioned Roadrunner matched that serial", serial=serial)
    if len(candidates) != 1:
        raise _error("roadrunner_ambiguous", "More than one Roadrunner matched that serial", serial=serial)
    candidate_serial, port, topology = candidates[0]
    info = _helper(paths, "info", port)
    if not _valid_info(info, serial, provisioned=True):
        raise _error("roadrunner_invalid_probe", "Roadrunner INFO did not confirm the provisioned descriptor", serial=serial)
    return RoadrunnerDevice(candidate_serial, port, topology)


def _await_same_topology(
    paths: Paths, topology: usb.UsbDevice, serial: str, *, provisioned: bool
) -> RoadrunnerDevice:
    deadline = time.monotonic() + REENUMERATE_TIMEOUT
    while True:
        for candidate_serial, port, candidate_topology in _entry_candidates(paths):
            if candidate_topology.name != topology.name or candidate_serial != serial:
                continue
            try:
                info = _helper(paths, "info", port)
            except RoadrunnerError:
                continue
            if _valid_info(info, serial, provisioned=provisioned):
                return RoadrunnerDevice(serial, port, candidate_topology)
        if time.monotonic() >= deadline:
            raise _error("roadrunner_timeout", "Roadrunner did not re-enumerate with the expected identity", serial=serial)
        time.sleep(0.25)


def provision_roadrunner(paths: Paths, device: RoadrunnerDevice, uuid: bytes) -> RoadrunnerDevice:
    if len(uuid) != 16:
        raise ValueError("Roadrunner UUID must be 16 bytes")
    try:
        response = _helper(paths, "provision", device.port, uuid.hex())
    except RoadrunnerError:
        raise
    serial = response.get("serial")
    if not isinstance(serial, str) or not PROVISIONED_RE.fullmatch(serial):
        raise _error("roadrunner_invalid_probe", "Roadrunner returned an invalid provisioned serial")
    return _await_same_topology(paths, device.topology, serial, provisioned=True)


def clear_roadrunner(paths: Paths, device: RoadrunnerDevice) -> RoadrunnerDevice:
    _helper(paths, "clear", device.port)
    serial = f"RR-UNPROVISIONED-{device.topology.serial or ''}"
    # The USB diagnostic serial may not be the flash UID, so match only the
    # confirmed same hardware and accept its newly reported unprovisioned name.
    deadline = time.monotonic() + REENUMERATE_TIMEOUT
    while True:
        for candidate_serial, port, candidate_topology in _entry_candidates(paths):
            if candidate_topology.name != device.topology.name or not UNPROVISIONED_RE.fullmatch(candidate_serial):
                continue
            try:
                info = _helper(paths, "info", port)
            except RoadrunnerError:
                continue
            if _valid_info(info, candidate_serial, provisioned=False):
                return RoadrunnerDevice(candidate_serial, port, candidate_topology)
        if time.monotonic() >= deadline:
            raise _error("roadrunner_timeout", "Roadrunner did not return unprovisioned after clear", serial=serial)
        time.sleep(0.25)


class Roadrunner:
    name: str = "roadrunner"
    label: str = "Roadrunner direct USB"
    states: tuple[str, ...] = (STATE_KLIPPER,)
    needs_ports_free: bool = False

    def sight(self, bench: Bench) -> list[Sighting]:
        return [
            Sighting(
                id=device.serial,
                address=device.port,
                state=STATE_KLIPPER,
                source=self.name,
                detail={"model": "roadrunner-v1"},
            )
            for device in discover(bench.paths)
        ]


__all__ = [
    "Roadrunner",
    "RoadrunnerDevice",
    "RoadrunnerError",
    "clear_roadrunner",
    "discover",
    "find_provisioned",
    "find_untracked",
    "provision_roadrunner",
]
