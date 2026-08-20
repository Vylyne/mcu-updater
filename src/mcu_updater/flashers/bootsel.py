"""BOOTSEL: the first firmware an RP2040 ever receives.

The RP2040 counterpart to :mod:`.dfu_util` - a factory-bare board has no
bootloader yet to speak flashtool's protocol, so this writes before one exists.
Unlike DFU, the ROM bootloader here has no USB bus presence a tool can query
directly: it mounts as mass storage (:func:`mcu_updater.devices.bootsel_scan`
finds it), and the "write" is a plain file copy - a `.uf2` dropped on the
volume is what makes the board flash itself and reboot, no protocol at all.

`needs_klipper_stopped = False`, same reasoning as `DfuUtil`: by the time this
runs the board is already in BOOTSEL, which means either it was never on the
Klipper bus or whatever put it there already dealt with Klipper.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterator
from typing import Any

from ..devices import STATE_BOOTSEL, bootsel_scan
from ..errors import DeviceNotFoundError, FlashError
from .spec import Bench, FlashTarget


class Bootsel:
    """Writes an RP2040 sitting in its BOOTSEL mass-storage bootloader."""

    name = "bootsel"
    label = "BOOTSEL (mass storage)"
    chipsets: tuple[str, ...] = ("rp2040",)
    states: tuple[str, ...] = (STATE_BOOTSEL,)
    #: False, for the same reason `DfuUtil.needs_klipper_stopped` is. The board
    #: is already in BOOTSEL by the time this is called - today that means the
    #: user held the button and replugged. The moment this tool routes a board
    #: into BOOTSEL itself, over a port Klipper may be holding, this flips.
    needs_klipper_stopped = False

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up. The board is already where it needs to be."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        uf2 = target.detail["uf2_file"]
        if not os.path.exists(uf2):
            raise FlashError(f"firmware image not found at {uf2}.", path=uf2)

        if bench.settings.dry_run:
            ctx.reporter(
                "info", f"[dry-run] would copy {uf2} to the mounted RPI-RP2 volume"
            )
            return {"mount": None}

        mount = _find_mount(bench.paths)
        dest = os.path.join(mount, os.path.basename(uf2))
        ctx.reporter("info", f"Copying {uf2} to {dest}...")
        shutil.copy2(uf2, dest)
        ctx.reporter(
            "info",
            "Copied. The board flashes itself from the .uf2 and reboots once the "
            "write lands - no further action needed here.",
        )
        return {"mount": mount}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait *for* here, and deliberately so - same reasoning as
        `DfuUtil.settled`. The board reboots as Katapult, under a serial it has
        never had before; waiting for that is adoption, which this could not do
        because it cannot name the device it is waiting for."""


def _find_mount(paths: Any) -> str:
    """The one RPI-RP2 volume to write, or a refusal.

    BOOTSEL exposes no per-device identity at all - not a serial, not even a
    bus address, just a mounted volume - so unlike DFU's `target_serial` there
    is nothing to disambiguate with. More than one mounted at once means
    guessing which board this write is for, which this refuses exactly the way
    `DfuUtil`'s ambiguity guard does.
    """
    mounts = bootsel_scan(paths)
    if not mounts:
        raise DeviceNotFoundError(
            "no RPI-RP2 volume is mounted. Hold BOOTSEL and replug the board, "
            "then give it a moment to automount before trying again."
        )
    if len(mounts) > 1:
        raise FlashError(
            f"{len(mounts)} RPI-RP2 volumes are mounted at once "
            f"({', '.join(mounts)}) - which one is this board? Unplug the "
            f"others and try again.",
            mounts=mounts,
        )
    return mounts[0]


def target_for(uf2_file: str, *, chipset: str) -> FlashTarget:
    """A bare RP2040, as a target.

    `id` is always empty - BOOTSEL has no identity to put there at all, unlike
    DFU's optional serial. Ambiguity is instead refused in `write`, once it is
    known whether more than one volume is actually mounted.
    """
    return FlashTarget(
        flasher=Bootsel.name,
        type=chipset,
        id="",
        detail={"uf2_file": uf2_file, "chipset": chipset},
    )
