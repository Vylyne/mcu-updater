"""Katapult's flashtool.py: the normal case for a board on the Klipper bus.

A thin adapter. :func:`mcu_updater.flashers.flash.flash_katapult` keeps its whole body -
the bootloader request, the re-enumeration wait, the flash-log record - because
this is not a rewrite. What moves here is the *shape*: what has to be true
before the write, and what has to be waited for after it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from ..devices import STATE_KATAPULT, STATE_KLIPPER
from ..paths import REENUMERATE_TIMEOUT
from .spec import Bench, FlashTarget


class Flashtool:
    """Writes one tracked board through its Katapult bootloader."""

    name = "flashtool"
    label = "Katapult flashtool"
    #: Both states a board on the Klipper bus can be in - the write itself
    #: reboots it from one into the other, so this flasher owns both rather
    #: than needing to be told which it is starting from.
    chipsets: tuple[str, ...] = ("stm32", "rp2040")
    states: tuple[str, ...] = (STATE_KLIPPER, STATE_KATAPULT)
    #: Not because the write needs it - by then the board is in Katapult and
    #: Klipper has long since let go. Because *getting* it there does: the
    #: reboot-into-bootloader request is sent over the serial port Klipper is
    #: holding open, and it goes nowhere while Klipper has it.
    needs_services_stopped = True

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up. Katapult answers on the board's own node."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from .flash import flash_katapult

        flash_katapult(
            bench.paths,
            bench.settings,
            target.type,
            target.detail["chipset"],
            target.id,
            fw=target.detail["fw"],
            reporter=ctx.reporter,
            # Absent for any caller that never sets it - a batch across more
            # than one board never should. See cli.py's _board_targets.
            force=bool(target.detail.get("force", False)),
        )
        # `serial` as well as the uniform `id`, because that is what a board's
        # id has always been called on this wire and in the CLI. Same reason
        # `targets[].devices[]` carries both an `id` and a `name`.
        return {"serial": target.id}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Wait for the board to come back as a Klipper device.

        Per board and not once at the end of a batch. It re-enumerates over USB
        after rebooting into the new firmware, and starting Klipper before its
        device node exists brings it up in an error state - so the last board
        written would otherwise have nothing at all between its write and the
        service restart.

        A timeout here is reported and not raised: the write succeeded, and the
        readiness check after the batch is the real verdict.
        """
        from ..devices import KLIPPER_FW_NAME, wait_for_device
        from ..errors import BootloaderTimeoutError

        if bench.settings.dry_run:
            return
        try:
            wait_for_device(
                bench.paths,
                target.detail["chipset"],
                target.id,
                KLIPPER_FW_NAME,
                timeout=REENUMERATE_TIMEOUT,
                settle=1.0,
            )
        except BootloaderTimeoutError as exc:
            ctx.reporter("warn", str(exc))


def target_for(
    board: dict[str, Any], *, stop_services: tuple[str, ...] = ()
) -> FlashTarget:
    """One entry from the agent's board selection, as a target.

    The selection's dict shape is on the wire (`fw.flash_all` returns it), so it
    is carried whole rather than unpacked and rebuilt - a second copy of those
    keys is a second thing to keep in step.

    `stop_services` is resolved by the caller against the board's type, its
    firmware family and `[updater]` - see `stop_services.py`.
    """
    return FlashTarget(
        flasher=Flashtool.name,
        type=board["type"],
        id=board["serial"],
        stop_services=stop_services,
        detail=board,
    )
