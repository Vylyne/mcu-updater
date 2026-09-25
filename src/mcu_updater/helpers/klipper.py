"""Klipper's BOOTSEL requester: a running RP2040 into ROM BOOTSEL, and back.

For a board with no Katapult, or one whose owner would rather not use it. The
request is Katapult's own `flashtool.py -r`, which asks Klipper to reboot into
its bootloader. Klipper's rp2040 port tries Katapult first only when the
running image was built with a bootloader offset *and* Katapult's signature is
at the start of flash; otherwise it falls back to the ROM's BOOTSEL, and the
volume mounts on the same USB port the serial device held. When both hold,
the board lands in Katapult instead - and this refuses rather than wait for a
volume that is not coming.

Only USB: Klipper's CAN bootloader request goes to Katapult or nowhere, and
`Bootsel.supports` already keeps a CAN board off this path.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from .. import build as build_mod
from .. import profiles
from ..discovery import bootsel, byid
from ..errors import DeviceNotFoundError, FlashError, ToolMissingError
from ..flashers.spec import Bench
from ..paths import REENUMERATE_TIMEOUT, Paths
from .spec import BootselHandoff

#: How long a board is given to leave Klipper after the request, for BOOTSEL
#: or for Katapult. Timing out is not a failure here: `mount_for_topology`
#: waits again for the volume and names what it found instead.
REQUEST_TIMEOUT = 15.0
REQUEST_POLL = 0.25


class KlipperHelper:
    """Ask a running Klipper RP2040 for BOOTSEL, and wait for it back."""

    name: str = "klipper"

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff:
        paths = bench.paths
        if not chipset.startswith("rp2040"):
            raise FlashError(
                f"the klipper helper can only ask an rp2040 for BOOTSEL; {serial} is a {chipset}.",
                chipset=chipset,
                serial=serial,
            )
        device = byid.find_device(paths, chipset, serial)
        if device is None:
            raise DeviceNotFoundError(
                f"{chipset} {serial} is not on USB. It has to be running Klipper to "
                f"be asked for BOOTSEL.",
                chipset=chipset,
                serial=serial,
            )
        if device.is_katapult:
            raise FlashError(
                f"{serial} is in Katapult, not Klipper, and only Klipper can be asked "
                f"for BOOTSEL. List flashtool before bootsel in this family so a board "
                f"in Katapult is written through it.",
                serial=serial,
            )
        # Before the request: the serial device is gone once the board reboots,
        # and its USB port is the only thing that ties the volume to this board.
        topology = bootsel.serial_topology_for(paths, device.path)

        from ..flashers.flash import find_flashtool

        flashtool = find_flashtool(paths, bench.settings)
        if not os.path.exists(flashtool):
            raise ToolMissingError(
                f"flashtool.py not found at {flashtool}. Is katapult installed?",
                tool="flashtool.py",
                path=flashtool,
            )
        ctx.reporter("info", f"Asking {serial} to reboot into BOOTSEL...")
        rc = build_mod.run_streamed(
            [sys.executable, flashtool, "-d", device.path, "-r"],
            cwd=paths.home,
            reporter=ctx.reporter,
        )
        if rc != 0:
            raise FlashError(
                f"flashtool.py -r exited {rc} asking {serial} for BOOTSEL. Nothing "
                f"was written.",
                serial=serial,
                returncode=rc,
            )
        _await_bootsel(paths, topology, serial=serial)
        return BootselHandoff(topology=topology)

    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any, type_name: str, fw: str
    ) -> None:
        paths = bench.paths
        expected = predicted_serial(paths.config_file(type_name, fw), current=serial)
        if expected != serial:
            ctx.reporter(
                "warn",
                f"This image sets CONFIG_USB_SERIAL_NUMBER, so the board comes back as "
                f"{expected}, not {serial}. Update the serial: line in printer.cfg, and "
                f"the serial this board is tracked under, to match.",
            )
        byid.wait_for_device(
            paths, chipset, expected, byid.KLIPPER_FW_NAME, timeout=REENUMERATE_TIMEOUT, settle=1.0
        )


def predicted_serial(config_path: str, *, current: str) -> str:
    """The serial a board flashed from this config presents on USB.

    Klipper's rp2040 port answers with its flash chip's ID - the serial it
    has now - unless the config turns `USB_SERIAL_NUMBER_CHIPID` off, and
    then with the literal `USB_SERIAL_NUMBER`. The chip-ID answer defaults to
    y, so a config that does not mention it keeps the current serial. The
    current serial's interface suffix (`-if00`) is kept, because it comes
    from udev, not the firmware.
    """
    answers = profiles.answer_map(profiles.answer_lines(config_path))
    if answers.get("USB_SERIAL_NUMBER_CHIPID", "y") != "n":
        return current
    literal = answers.get("USB_SERIAL_NUMBER", "").strip().strip('"')
    if not literal:
        return current
    return literal + current[len(byid.canonical_serial(current)) :]


def _await_bootsel(paths: Paths, topology: str, *, serial: str) -> None:
    """Wait for the board to leave Klipper, and refuse if it went to Katapult."""
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while True:
        if bootsel.mounts_on(paths, topology):
            return
        if _katapult_on(paths, topology):
            raise FlashError(
                f"{serial} rebooted into Katapult, not BOOTSEL: the running Klipper was "
                f"built for Katapult, so its bootloader request goes to it. Nothing was written. "
                f"Power-cycle the board to boot Klipper again, and list flashtool "
                f"before bootsel in this family to write it through Katapult.",
                serial=serial,
                topology=topology,
            )
        if time.monotonic() >= deadline:
            return
        time.sleep(REQUEST_POLL)


def _katapult_on(paths: Paths, topology: str) -> bool:
    """Is a Katapult device on the USB port this board held?"""
    for dev in byid.scan(paths):
        if not dev.is_katapult:
            continue
        try:
            if bootsel.serial_topology_for(paths, dev.path) == topology:
                return True
        except FlashError:
            continue
    return False
