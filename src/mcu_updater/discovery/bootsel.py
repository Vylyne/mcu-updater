"""BOOTSEL - an RP2040's ROM bootloader.

No ``dfu-util`` protocol - it mounts as mass storage. See :func:`bootsel_scan`.
It does publish a by-id serial, unmounted or not; see :func:`bootsel_devices`.
"""

from __future__ import annotations

import glob
import os
import re
import time

from ..errors import FlashError
from ..paths import Paths

#: udisks2's two automount conventions. The username segment is glob-matched
#: rather than assumed - "pi" has not been the default login on Raspberry Pi OS
#: since Bookworm, and this process does not otherwise know who is logged in.
DEFAULT_BOOTSEL_ROOT_GLOBS = ("/media/*", "/run/media/*")

#: The RP2040 boot ROM's own volume label.
BOOTSEL_VOLUME_NAME = "RPI-RP2"

#: Where the current udev rule mounts, relative to an automount root: one
#: directory per USB topology path, so two boards in BOOTSEL at once cannot
#: collide. See docs/bootsel-mountpoint-design.md.
BOOTSEL_BY_PATH_SUBDIR: tuple[str, str] = ("BOOTSEL", "by-path")

#: Every UF2 bootloader publishes this file at the volume root. Required so an
#: unrelated drive that happens to share the label is never mistaken for one.
_BOOTSEL_MARKER = "INFO_UF2.TXT"

#: What the current udev rule mounts under when `ID_PATH_TAG` is unset (see
#: `scripts/udev.d-mcu-updater-bootsel.rules`). A leaf no topology can ever
#: match, so a board that lands here is found only to be named in the refusal.
BOOTSEL_UNKNOWN_PATH_LEAF = "unknown"

#: The application has already disappeared by the time mount correlation
#: begins, but udev/systemd still need a bounded window to expose its volume.
BOOTSEL_MOUNT_TIMEOUT = 15.0
BOOTSEL_MOUNT_POLL = 0.25

_USB_ALIAS = re.compile(r"-usbv2-")
_SERIAL_INTERFACE = re.compile(r":[0-9]+\.[0-9]+(?:-port[0-9]+)?$")
_ID_PATH_TAG_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _serial_by_path_root(paths: Paths) -> str:
    """The by-path directory beside this installation's by-id directory."""
    return os.path.join(os.path.dirname(paths.serial_by_id), "by-path")


def _normalize_serial_topology(name: str) -> str:
    """Canonical controller-qualified USB parent from a serial by-path name."""
    topology = _USB_ALIAS.sub("-usb-", os.path.basename(name))
    interface = _SERIAL_INTERFACE.search(topology)
    if interface is None:
        raise ValueError(f"unrecognized USB serial by-path topology {name!r}")
    return topology[: interface.start()]


def serial_topology_for(paths: Paths, port: str) -> str:
    """Resolve one confirmed tty to one controller-qualified USB topology.

    A physical tty commonly has both ``usb`` and ``usbv2`` aliases. They are
    normalized and deduplicated before the uniqueness check. Distinct
    controller-qualified paths are refused: compact sysfs names such as
    ``1-3`` omit the controller and are not strong enough to authorize a write.
    """
    directory = _serial_by_path_root(paths)
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise FlashError(
            f"could not read serial by-path topology for {port}: {exc}",
            port=port,
            by_path=directory,
        ) from exc

    matched: list[str] = []
    for name in names:
        entry = os.path.join(directory, name)
        try:
            same = os.path.samefile(entry, port)
        except OSError:
            continue
        if not same:
            continue
        try:
            matched.append(_normalize_serial_topology(name))
        except ValueError as exc:
            raise FlashError(
                f"could not normalize serial by-path topology for {port}: {name}",
                port=port,
                by_path=entry,
            ) from exc

    topologies = sorted(set(matched))
    if not topologies:
        raise FlashError(
            f"no serial by-path topology matched the confirmed device {port}",
            port=port,
            by_path=directory,
        )
    if len(topologies) > 1:
        raise FlashError(
            f"serial by-path topology for {port} is ambiguous: "
            f"{', '.join(topologies)}",
            port=port,
            topologies=topologies,
        )
    return topologies[0]


def _topology_tag_prefix(topology: str) -> str:
    """Normalize serial evidence into the lossy ``ID_PATH_TAG`` namespace."""
    parent = _USB_ALIAS.sub("-usb-", os.path.basename(topology))
    # A requester returns the interface-free USB parent, whose final ``:1.3``
    # is a physical port path, not an interface. Tests and callers that pass a
    # complete serial by-path still get normalized here, but do not strip a
    # port from the canonical handoff evidence.
    if parent.endswith(":1.0") or re.search(r":1\.0-port[0-9]+$", parent):
        parent = _normalize_serial_topology(parent)
    return _ID_PATH_TAG_UNSAFE.sub("_", parent)


def _mount_matches_topology(mount: str, topology: str) -> bool:
    tag = _USB_ALIAS.sub("-usb-", os.path.basename(mount))
    prefix = _topology_tag_prefix(topology)
    # The serial parent is followed by exactly one USB interface component
    # before the mass-storage SCSI suffix. Requiring that shape avoids treating
    # a deeper hub path that merely shares the same textual prefix as a match.
    return (
        re.fullmatch(
            re.escape(prefix) + r"_[0-9]+_[0-9]+-scsi(?:[-_].*)?", tag
        )
        is not None
    )


def _unmatchable(mounts: list[str]) -> tuple[list[str], list[str]]:
    """The scanned mounts that no topology could ever have matched.

    Two of them exist. ``<root>/RPI-RP2`` is the pre-version-5 udev rule's fixed
    path, still accepted by `bootsel_scan` so an un-upgraded install keeps its
    manual bare-board flow. ``.../by-path/unknown`` is the current rule's
    fallback for a device `ID_PATH_TAG` could not name. Either one means the
    board rebooted, mounted, and cannot be correlated - a very different fact
    from "nothing appeared", and the operator needs to be told which.
    """
    legacy = [
        mount for mount in mounts if os.path.basename(mount) == BOOTSEL_VOLUME_NAME
    ]
    unknown = [
        mount
        for mount in mounts
        if os.path.basename(mount) == BOOTSEL_UNKNOWN_PATH_LEAF
    ]
    return legacy, unknown


def _no_match_error(topology: str, mounts: list[str]) -> FlashError:
    """The timeout, refined by whatever unmatchable volume was scanned.

    A refinement of the deadline and never a shortcut to it: an unmatchable
    volume is just as likely to be a bystander board as the target, so the full
    wait still runs, and neither kind is ever selected. Selecting one would be
    the arbitrary-mount write this whole path exists to prevent.
    """
    legacy, unknown = _unmatchable(mounts)
    message = f"no mounted BOOTSEL volume appeared for topology {topology}"
    if legacy:
        # First: it has a concrete remedy, and an install old enough to mount
        # here explains the `unknown` leaf away as well.
        message += (
            f" - but a BOOTSEL volume is mounted at the legacy path "
            f"{', '.join(legacy)}. Re-run install.sh to update the udev rule; "
            f"until it mounts by USB topology, no board can be matched to a "
            f"port and this write cannot be aimed."
        )
    elif unknown:
        message += (
            f" - but a BOOTSEL volume is mounted at {', '.join(unknown)}, where "
            f"udev could not name the USB path (ID_PATH_TAG was unset). It "
            f"cannot be matched to the board this write is for, and is not "
            f"written to on the chance that it is."
        )
    return FlashError(
        message,
        topology=topology,
        legacy_mounts=legacy,
        unknown_mounts=unknown,
    )


def mount_for_topology(
    paths: Paths,
    topology: str,
    *,
    timeout: float = BOOTSEL_MOUNT_TIMEOUT,
    poll: float = BOOTSEL_MOUNT_POLL,
) -> str:
    """Wait for exactly one marker-bearing BOOTSEL mount on ``topology``."""
    deadline = time.monotonic() + timeout
    while True:
        mounts = bootsel_scan(paths)
        matches = [
            mount for mount in mounts if _mount_matches_topology(mount, topology)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise FlashError(
                f"BOOTSEL topology {topology} is ambiguous: "
                f"{len(matches)} matching volumes are mounted",
                topology=topology,
                mounts=matches,
            )
        if time.monotonic() >= deadline:
            raise _no_match_error(topology, mounts)
        time.sleep(poll)


def bootsel_scan(paths: Paths) -> list[str]:
    """Mount path of every RPI-RP2 volume currently attached.

    Unlike DFU, a BOOTSEL board is not a USB device this process can query -
    it is a mounted filesystem, discovered the same way a human would: look
    for the drive. `MCU_UPDATER_FAKE_BUS` cannot stand in for that, so
    `paths.bootsel_root` is the equivalent seam: empty in production (search
    the standard automount locations), or one exact directory to look in
    instead - which a test points at a tmp_path, and which a real deployment
    with a non-standard automount setup could point at the real one.

    Two layouts are searched. Older installs still carry the first udev rule,
    which mounts every board at ``<root>/RPI-RP2``; the current rule mounts at
    ``<root>/BOOTSEL/by-path/<topology tag>``. Both are accepted until every
    install has been upgraded by ``install.sh``.
    """
    if paths.bootsel_root:
        roots = [paths.bootsel_root]
    else:
        # Read through the `devices` shim, not the module-level constant above,
        # so `monkeypatch.setattr(devices, "DEFAULT_BOOTSEL_ROOT_GLOBS", ...)` -
        # the pre-move patch target, still used by tests predating this file -
        # keeps working without editing them. Deferred import: `devices` itself
        # imports this module for the re-export, so this can't be a top-level one.
        from .. import devices

        globs = devices.DEFAULT_BOOTSEL_ROOT_GLOBS
        roots = [p for pattern in globs for p in glob.glob(pattern)]

    found = []
    for root in sorted(roots):
        # Two layouts, deliberately: the current rule's topology-named
        # directories, and the fixed RPI-RP2 path older installs still have
        # until install.sh replaces their rule.
        candidates = [os.path.join(root, BOOTSEL_VOLUME_NAME)]
        candidates += sorted(glob.glob(os.path.join(root, *BOOTSEL_BY_PATH_SUBDIR, "*")))
        for candidate in candidates:
            # Load-bearing, not belt-and-braces: a topology-named directory's
            # name says nothing about what is mounted there, so this marker is
            # the only thing separating a bootloader volume from any other
            # directory - including an empty mountpoint left behind by a replug.
            if os.path.isfile(os.path.join(candidate, _BOOTSEL_MARKER)):
                found.append(candidate)
    return found


#: Where the boot ROM's mass-storage device node shows up, unmounted or not.
#: The serial after ``usb-RPI_RP2_`` is the flash chip's unique ID - see
#: `bootsel_devices`.
_BOOTSEL_DISK_BY_ID_GLOB = "/dev/disk/by-id/usb-RPI_RP2_*-part1"


def bootsel_devices(paths: Paths) -> list[str]:
    """Every RP2040 boot-ROM block device attached, mounted or not.

    `bootsel_scan` answers "where can I copy a .uf2" and sees nothing without a
    mount. This answers "is a board even here" without needing one: the boot ROM
    publishes the flash chip's unique ID as a USB mass-storage serial, so the
    device node exists in `/dev/disk/by-id` the instant the board enumerates,
    before anything mounts it. Read-only, no privilege needed - `_find_mount`
    uses it to tell "no board" apart from "board present, nothing mounts it".

    `paths.bootsel_root` doubles as the seam here too: empty means search the
    real `/dev`, and a test pointing it at a tmp_path searches there instead
    (`<bootsel_root>/by-id/usb-RPI_RP2_*-part1`) so no test touches `/dev`.
    """
    if paths.bootsel_root:
        pattern = os.path.join(paths.bootsel_root, "by-id", "usb-RPI_RP2_*-part1")
    else:
        pattern = _BOOTSEL_DISK_BY_ID_GLOB
    return sorted(glob.glob(pattern))


#: The boot ROM's flash-chip unique ID, out of a `bootsel_devices` entry like
#: ``/dev/disk/by-id/usb-RPI_RP2_E0C9125B0D9B-0:0-part1``.
_SERIAL_RE = re.compile(r"usb-RPI_RP2_([0-9A-Fa-f]+)-")


def bootsel_id_for(node: str) -> str | None:
    """The boot ROM's flash-chip unique ID out of a `bootsel_devices()` entry.

    Central so both `flashers.bootsel.target_for` and the agent's
    `bootsel_scan`/`_identify_bootsel` parse the same string the same way,
    mirroring how `dfu_serial_for` lives here rather than in each of its
    callers.
    """
    match = _SERIAL_RE.search(node)
    return match.group(1) if match else None
