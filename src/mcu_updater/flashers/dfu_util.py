"""dfu-util: the first firmware a bare board ever receives.

The odd one out, and the reason `needs_klipper_stopped` is a property of the
*flasher's work* rather than of the device. There is no bootloader here to speak
flashtool's protocol and no Klipper to ask for a reboot - the board is holding
BOOT0 because somebody fitted a jumper and replugged it.

:func:`mcu_updater.flash.flash_dfu_stm32` keeps its body, including the two
things that are easy to get wrong and were: refusing to guess between several
boards in DFU, and treating dfu-util's non-zero exit after ``:leave`` as the
expected detach it is rather than a failure.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any, Optional

from .spec import Bench, FlashTarget


class DfuUtil:
    """Writes a bare STM32 sitting in DFU mode."""

    name = "dfu_util"
    label = "dfu-util"
    #: False, and this is the one worth watching.
    #:
    #: By the time this is called the board is already in DFU, which means
    #: either it was never on the Klipper bus or whatever put it there already
    #: dealt with Klipper. Today that is the user: fit the boot jumper, replug.
    #:
    #: The conflict is not the write, it is the *transition*. The moment this
    #: tool routes a board into DFU itself rather than asking - over the serial
    #: port Klipper may be holding - this becomes True.
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
        from ..flash import flash_dfu_stm32

        flash_dfu_stm32(
            bench.paths,
            bench.settings,
            target.detail["fw_bin"],
            reporter=ctx.reporter,
            target_serial=target.detail.get("dfu_serial"),
        )
        return {"dfu_serial": target.detail.get("dfu_serial")}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait *for* here, and deliberately so.

        The board does reboot and re-enumerate - as Katapult, under a serial it
        has never had before. Waiting for that is adoption rather than settling:
        the caller is looking for a device it cannot name yet, which is what
        `adoptable_devices` does and what this could not.
        """


def target_for(
    fw_bin: str, *, chipset: str, dfu_serial: Optional[str] = None
) -> FlashTarget:
    """A bare board, as a target.

    `id` is the DFU serial when one was named. Without it there is genuinely no
    id - a DFU device has no `/dev/serial/by-id` name - and the write refuses
    rather than guessing whenever more than one board answers.
    """
    return FlashTarget(
        flasher=DfuUtil.name,
        type=chipset,
        id=dfu_serial or "",
        detail={"fw_bin": fw_bin, "dfu_serial": dfu_serial, "chipset": chipset},
    )
