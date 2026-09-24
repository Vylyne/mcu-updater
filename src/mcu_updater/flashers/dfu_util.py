"""dfu-util: the first firmware a bare board ever receives.

The odd one out, and the reason `needs_services_stopped` is a property of the
*flasher's work* rather than of the device. There is no bootloader here to speak
flashtool's protocol and no Klipper to ask for a reboot - the board is holding
BOOT0 because somebody fitted a jumper and replugged it.

:func:`mcu_updater.flashers.flash.flash_dfu_stm32` keeps its body, including the two
things that are easy to get wrong and were: refusing to guess between several
boards in DFU, and treating dfu-util's non-zero exit after ``:leave`` as the
expected detach it is rather than a failure.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from ..artifacts import KIND_BIN, Artifact
from ..devices import STATE_DFU
from .spec import (
    KIND_BARE,
    Bench,
    Device,
    FlashRecord,
    FlashTarget,
    artifact_path,
    chipset_matches,
)

if TYPE_CHECKING:
    from ..helpers.spec import Helper
    from ..paths import Paths


class DfuUtil:
    """Writes a bare STM32 sitting in DFU mode."""

    name = "dfu_util"
    label = "dfu-util"
    chipsets: tuple[str, ...] = ("stm32",)
    states: tuple[str, ...] = (STATE_DFU,)
    #: False, and this is the one worth watching.
    #:
    #: By the time this is called the board is already in DFU, which means
    #: either it was never on the Klipper bus or whatever put it there already
    #: dealt with Klipper. Today that is the user: fit the boot jumper, replug.
    #:
    #: The conflict is not the write, it is the *transition*. The moment this
    #: tool routes a board into DFU itself rather than asking - over the serial
    #: port Klipper may be holding - this becomes True.
    needs_services_stopped = False
    accepts: tuple[str, ...] = (KIND_BIN,)

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A bare STM32 holding BOOT0."""
        return (
            device.kind == KIND_BARE
            and device.state in self.states
            and chipset_matches(self, device.chipset)
        )

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        return target_for(artifact, chipset=device.chipset, dfu_serial=device.id or None)

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up. The board is already where it needs to be."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from .flash import flash_dfu_stm32

        flash_dfu_stm32(
            bench.paths,
            bench.settings,
            artifact_path(target),
            reporter=ctx.reporter,
            target_serial=target.detail.get("dfu_serial"),
        )
        return {"dfu_serial": target.detail.get("dfu_serial")}

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """Nothing to file. dfu-util only ever writes a bootloader to a bare
        board here, before it has the durable identity a record is filed
        under."""
        return None

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait *for* here, and deliberately so.

        The board does reboot and re-enumerate - as Katapult, under a serial it
        has never had before. Waiting for that is adoption rather than settling:
        the caller is looking for a device it cannot name yet, which is what
        `adoptable_devices` does and what this could not.
        """


def target_for(
    fw_bin: str | Artifact, *, chipset: str, dfu_serial: str | None = None
) -> FlashTarget:
    """A bare board, as a target.

    `id` is the DFU serial when one was named. Without it there is genuinely no
    id - a DFU device has no `/dev/serial/by-id` name - and the write refuses
    rather than guessing whenever more than one board answers.
    """
    artifact = fw_bin if isinstance(fw_bin, Artifact) else Artifact(KIND_BIN, fw_bin)
    return FlashTarget(
        flasher=DfuUtil.name,
        type=chipset,
        id=dfu_serial or "",
        detail={"dfu_serial": dfu_serial, "chipset": chipset},
        artifact=artifact,
    )
