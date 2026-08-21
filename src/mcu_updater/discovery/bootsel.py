"""BOOTSEL - an RP2040's ROM bootloader.

No ``dfu-util`` protocol - it mounts as mass storage. See :func:`bootsel_scan`.
It does publish a by-id serial, unmounted or not; see :func:`bootsel_devices`.
"""

from __future__ import annotations

import glob
import os

from ..paths import Paths

#: udisks2's two automount conventions. The username segment is glob-matched
#: rather than assumed - "pi" has not been the default login on Raspberry Pi OS
#: since Bookworm, and this process does not otherwise know who is logged in.
DEFAULT_BOOTSEL_ROOT_GLOBS = ("/media/*", "/run/media/*")

#: The RP2040 boot ROM's own volume label.
BOOTSEL_VOLUME_NAME = "RPI-RP2"

#: Every UF2 bootloader publishes this file at the volume root. Required so an
#: unrelated drive that happens to share the label is never mistaken for one.
_BOOTSEL_MARKER = "INFO_UF2.TXT"


def bootsel_scan(paths: Paths) -> list[str]:
    """Mount path of every RPI-RP2 volume currently attached.

    Unlike DFU, a BOOTSEL board is not a USB device this process can query -
    it is a mounted filesystem, discovered the same way a human would: look
    for the drive. `MCU_UPDATER_FAKE_BUS` cannot stand in for that, so
    `paths.bootsel_root` is the equivalent seam: empty in production (search
    the standard automount locations), or one exact directory to look in
    instead - which a test points at a tmp_path, and which a real deployment
    with a non-standard automount setup could point at the real one.
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
        candidate = os.path.join(root, BOOTSEL_VOLUME_NAME)
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
