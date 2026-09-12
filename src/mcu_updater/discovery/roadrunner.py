"""Conservative discovery and direct-USB maintenance for Roadrunner."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..errors import BootloaderTimeoutError, UpdaterError
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
    #: What the confirming INFO reply said this board is running. Every field
    #: defaults to None because every one of them is optional on the wire: a
    #: board built before the digest registers existed ends its INFO payload at
    #: the flash UID, and `DIGEST_NONE` is a current board that could not
    #: compute one. **None is absence, never mismatch** - a device handed back
    #: by `provision_roadrunner` or `clear_roadrunner` carries None here
    #: legitimately, because those confirm an identity rather than an image.
    fw_version: str | None = None
    digest_algorithm: int | None = None
    digest: int | None = None
    image_start: int | None = None
    image_length: int | None = None


def _error(code: str, message: str, **data: object) -> RoadrunnerError:
    error = RoadrunnerError(message, **data)
    error.code = code
    return error


def _helper(paths: Paths, operation: str, port: str, argument: str | None = None) -> dict[str, object]:
    argv = [sys.executable, _HELPER, operation, port]
    if argument is not None:
        argv.append(argument)
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


def _entry_candidates(
    paths: Paths, *, strict: bool = False
) -> list[tuple[str, str, usb.UsbDevice]]:
    try:
        names = sorted(os.listdir(paths.serial_by_id))
    except OSError:
        if strict:
            raise
        return []
    inventory = usb.collect(paths, strict=True) if strict else usb.collect(paths)
    candidates: list[tuple[str, str, usb.UsbDevice]] = []
    for name in names:
        match = _ENTRY_RE.fullmatch(name)
        if match is None:
            continue
        path = os.path.join(paths.serial_by_id, name)
        port = os.path.realpath(path)
        topology = usb.device_for_tty(inventory, paths, os.path.basename(port))
        if topology is None or topology.manufacturer != "Vylyne" or topology.product != "Roadrunner":
            if strict:
                raise OSError(
                    f"could not confirm USB topology for Roadrunner candidate {name}"
                )
            continue
        candidates.append((match.group(1), port, topology))
    return candidates


#: The INFO fields describing the image rather than the identity. `_valid_info`
#: does not check any of them: they are enrichment, and a board that answers
#: without them is a board this host is older or newer than, not a bad one.
_PROVENANCE_INTS = ("digest_algorithm", "digest", "image_start", "image_length")
_PROVENANCE_FIELDS = ("fw_version", *_PROVENANCE_INTS)


def provenance(info: dict[str, object]) -> dict[str, Any]:
    """The image fields of an INFO reply, keyword-ready, absent ones dropped.

    Type-checked field by field rather than passed through, because these come
    from a subprocess's JSON: a string where an int belongs would otherwise
    reach the digest comparison and read as a mismatch against every artifact.
    Dropping it means absence instead, which falls through to the version.
    """
    out: dict[str, Any] = {}
    version = info.get("fw_version")
    if isinstance(version, str) and version:
        out["fw_version"] = version
    for field in _PROVENANCE_INTS:
        value = info.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            out[field] = value
    return out


def wire_provenance(paths: Paths) -> Callable[[str], dict[str, Any] | None]:
    """An image-fields source for one provisioned serial that opens the port.

    **Never from `fw.status`.** This confirms the board over the admin protocol,
    which means a `usb.collect` sweep and a helper subprocess per serial, on a
    tty Klipper may well be holding - the second half of "Klipper first, admin
    protocol second". It belongs to callers that have already freed the ports
    deliberately, and it is injected rather than reached for so that the reader
    doing the joining cannot reach the wire by accident.

    Returns None for anything it cannot confirm; the caller reads that as
    absence, the same as a board that answered without a digest.
    """

    def read(serial: str) -> dict[str, Any] | None:
        try:
            device = find_provisioned(paths, serial)
        except (UpdaterError, OSError):
            return None
        return {
            field: getattr(device, field)
            for field in _PROVENANCE_FIELDS
            if getattr(device, field) is not None
        }

    return read


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
            out.append(RoadrunnerDevice(serial, port, topology, **provenance(info)))
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
    return RoadrunnerDevice(candidate_serial, port, topology, **provenance(info))


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
    return RoadrunnerDevice(candidate_serial, port, topology, **provenance(info))


#: Indistinguishable from "not back yet" while a board re-enumerates, so all
#: three are retried rather than believed. The `/dev/serial/by-id` symlink
#: appears before the tty can reliably be opened - udev is still settling and
#: ModemManager may still be probing - so an INFO probe in that window fails
#: (`roadrunner_helper`) or answers incompletely (`roadrunner_invalid_probe`)
#: for a board that is about to be perfectly fine. Ambiguity is deliberately
#: not here: two devices answering to one serial is a real condition that
#: waiting cannot resolve.
_TRANSIENT_READINESS_CODES = frozenset(
    {"roadrunner_no_candidate", "roadrunner_helper", "roadrunner_invalid_probe"}
)


def wait_for_provisioned(paths: Paths, serial: str) -> RoadrunnerDevice:
    """Wait for one exact provisioned identity and a confirming INFO reply."""
    if not PROVISIONED_RE.fullmatch(serial):
        # Keep malformed requested identities as immediate caller errors rather
        # than turning them into a misleading re-enumeration timeout.
        return find_provisioned(paths, serial)

    deadline = time.monotonic() + REENUMERATE_TIMEOUT
    last_error: RoadrunnerError | None = None
    while True:
        try:
            return find_provisioned(paths, serial)
        except RoadrunnerError as exc:
            if exc.code not in _TRANSIENT_READINESS_CODES:
                raise
            last_error = exc
        if time.monotonic() >= deadline:
            # Carry the last cause: having retried a failed or unconvincing
            # probe, a board that really did come back wrong would otherwise be
            # reported as a bare "never came back".
            raise BootloaderTimeoutError(
                "Roadrunner did not re-enumerate with its confirmed provisioned identity",
                serial=serial,
                last_error=str(last_error) if last_error is not None else None,
            )
        time.sleep(0.25)


def _await_reenumeration(
    paths: Paths,
    topology: usb.UsbDevice,
    matches_serial: Callable[[str], bool],
    *,
    provisioned: bool,
    error_serial: str,
) -> RoadrunnerDevice:
    """Poll the same physical USB topology until it re-enumerates as expected.

    Every candidate on the same topology is INFO-probed regardless of
    whether its descriptor serial is the one wanted, so a device that comes
    back with the *wrong* identity (a corrupted write, or a different device
    now sitting on that port) is distinguished from one that never comes
    back at all: the last such observed (serial, state) is remembered, and if
    the deadline expires having seen one, `roadrunner_mismatch` is raised
    instead of the generic `roadrunner_timeout`.
    """
    deadline = time.monotonic() + REENUMERATE_TIMEOUT
    last_mismatch: tuple[str, bool] | None = None
    while True:
        for candidate_serial, port, candidate_topology in _entry_candidates(paths):
            if candidate_topology.name != topology.name:
                continue
            try:
                info = _helper(paths, "info", port)
            except RoadrunnerError:
                continue
            if matches_serial(candidate_serial) and _valid_info(info, candidate_serial, provisioned=provisioned):
                return RoadrunnerDevice(candidate_serial, port, candidate_topology, **provenance(info))
            # Prefer what the wire protocol itself reported: the descriptor's
            # serial can already equal what was wanted (matches_serial passed)
            # while INFO disagrees - e.g. a write that updated the USB string
            # descriptor without the flash identity actually taking. Recording
            # the descriptor serial in that case would make the diagnostic
            # read "observed the identity you expected", which is useless.
            info_serial = info.get("serial")
            last_mismatch = (
                info_serial if isinstance(info_serial, str) else candidate_serial,
                bool(info.get("provisioned")),
            )
        if time.monotonic() >= deadline:
            if last_mismatch is not None:
                observed_serial, observed_provisioned = last_mismatch
                raise _error(
                    "roadrunner_mismatch",
                    "Roadrunner re-enumerated with an unexpected identity",
                    serial=error_serial,
                    observed_serial=observed_serial,
                    observed_state="provisioned" if observed_provisioned else "unprovisioned",
                )
            raise _error(
                "roadrunner_timeout",
                "Roadrunner did not re-enumerate with the expected identity",
                serial=error_serial,
            )
        time.sleep(0.25)


def _await_same_topology(
    paths: Paths, topology: usb.UsbDevice, serial: str, *, provisioned: bool
) -> RoadrunnerDevice:
    return _await_reenumeration(
        paths, topology, lambda candidate: candidate == serial, provisioned=provisioned, error_serial=serial
    )


def _await_disappearance(paths: Paths, device: RoadrunnerDevice) -> usb.UsbDevice:
    """Wait until no Roadrunner CDC candidate occupies the old USB topology."""
    deadline = time.monotonic() + REENUMERATE_TIMEOUT
    while True:
        unknown = False
        try:
            candidates = _entry_candidates(paths, strict=True)
        except OSError:
            unknown = True
            candidates = []
        if not unknown and not any(
            topology.name == device.topology.name
            for _serial, _port, topology in candidates
        ):
            return device.topology
        if time.monotonic() >= deadline:
            message = (
                "Could not confirm that the Roadrunner CDC device disappeared"
                if unknown
                else "Roadrunner CDC device did not disappear after the BOOTSEL request"
            )
            raise _error(
                "roadrunner_timeout",
                message,
                serial=device.serial,
            )
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
    # The USB diagnostic serial may not be the flash UID, so match only the
    # confirmed same hardware and accept its newly reported unprovisioned name.
    error_serial = f"RR-UNPROVISIONED-{device.topology.serial or ''}"
    return _await_reenumeration(
        paths,
        device.topology,
        lambda candidate: UNPROVISIONED_RE.fullmatch(candidate) is not None,
        provisioned=False,
        error_serial=error_serial,
    )


class Roadrunner:
    name: str = "roadrunner"
    label: str = "Roadrunner direct USB"
    states: tuple[str, ...] = (STATE_KLIPPER,)
    #: `sight()` -> `discover()` -> `_helper()` opens a real `serial.Serial`
    #: connection to probe the device (via the `roadrunner_usb.py` subprocess),
    #: the same as the knomi listen pass - so it belongs in that category, not
    #: `Byid`'s or `Watcher`'s, which only read state nobody else is holding.
    needs_ports_free: bool = True

    def request_bootsel(
        self, paths: Paths, device: RoadrunnerDevice
    ) -> usb.UsbDevice:
        """Request BOOTSEL and return topology only after the old CDC is gone."""
        _helper(paths, "bootsel", device.port, device.serial)
        return _await_disappearance(paths, device)

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
    "provenance",
    "provision_roadrunner",
    "wait_for_provisioned",
    "wire_provenance",
]
