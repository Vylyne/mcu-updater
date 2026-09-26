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
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from ..artifacts import KIND_BIN, Artifact
from ..devices import STATE_DFU
from .spec import (
    KIND_BARE,
    Bench,
    CandidateScan,
    Device,
    FlashRecord,
    FlashTarget,
    TrackedBoard,
    artifact_path,
    chipset_matches,
    name_tracked,
)

if TYPE_CHECKING:
    from ..build import Reporter
    from ..helpers.spec import Helper
    from ..paths import Paths

#: `scan_candidates` reasons - see its docstring for where each sends the user.
SCAN_NO_TOOL = "no_tool"
SCAN_PERMISSION_DENIED = "permission_denied"
SCAN_NONE = "none"
SCAN_AMBIGUOUS = "ambiguous"


class DfuUtil:
    """Writes a bare STM32 sitting in DFU mode."""

    name = "dfu_util"
    label = "dfu-util"
    candidate_prefix = "dfu"
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

    def scan_candidates(
        self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter
    ) -> CandidateScan:
        """What is sitting in DFU mode, and can this agent actually open it?

        Deliberately **reports** failures instead of raising them. Every other
        method treats a refusal as an error because the caller asked for work to
        happen; here, describing the situation *is* the work. "dfu-util is not
        installed" is this method's answer, not its failure.

        The distinctions are not cosmetic - each sends the user somewhere else:

        ``no_tool``
            `apt install dfu-util`. Nothing to do with the board.
        ``permission_denied``
            libusb saw a board and could not claim it. **The boot jumper worked.**
            Reporting this as "no device found" is what once sent a user back to
            redo the one step that had succeeded. The udev rule tags `uaccess`,
            which grants the *seated* user - and this agent is a daemon, not a
            login session - so in practice it rides on `GROUP="plugdev"` and the
            service user being in that group.
        ``none``
            Genuinely nothing in DFU. Fit the boot jumper and replug.
        ``ambiguous``
            More than one board in DFU, and no serial was named to pick between
            them. Not a dead end: dfu-util takes `-S/-p/-n`, so naming one is
            enough - `ready` is false only because the *caller* has not chosen.

        A DFU device has no `/dev/serial/by-id` name, so `3941335F3434` connects
        to nothing on its own - which is what makes several boards in DFU at once
        so awkward to tell apart. But the DFU serial is *derived* from the same
        unique id the running serial is built from, so every tracked board's DFU
        name can be computed and matched.

        A board that matches nothing is not an error - that is what a genuinely
        new board looks like, and saying so is useful in itself.

        The derivation sums two of the three id words, so a collision is possible
        in principle. Two known boards mapping to one DFU serial therefore names
        neither: an unlabelled board is a small annoyance, and a board labelled as
        the wrong one is how you flash the toolhead you meant to leave alone.
        """
        from ..devices import dfu_devices, dfu_serial_for
        from ..errors import DfuPermissionError, ToolMissingError, UpdaterError
        from .flash import DFU_VID_PID

        extra: dict[str, Any] = {"vid_pid": DFU_VID_PID}
        try:
            devices = dfu_devices(reporter=reporter)
        except ToolMissingError as exc:
            return CandidateScan(False, SCAN_NO_TOOL, str(exc), [], extra)
        except DfuPermissionError as exc:
            # The raw dfu-util output, because a permissions diagnosis is exactly
            # the case where the operator wants to see what the tool actually said.
            # UpdaterError keeps its extras in .data, not as attributes.
            return CandidateScan(
                False,
                SCAN_PERMISSION_DENIED,
                str(exc),
                [],
                {**extra, "output": exc.data.get("output")},
            )
        except UpdaterError as exc:
            return CandidateScan(False, exc.code, str(exc), [], extra)

        owners: dict[str, list[tuple[str, str]]] = {}
        for board in tracked:
            computed = dfu_serial_for(board.serial)
            if computed:
                owners.setdefault(computed, []).append((board.type, board.serial))
        name_tracked(devices, owners, "serial")
        for device in devices:
            # dfu-util's `path` is the same string as sysfs's USB device name.
            device["port"] = device.get("path") or None

        if not devices:
            return CandidateScan(
                False,
                SCAN_NONE,
                "No board is in DFU mode. Fit the boot jumper (or hold BOOT0) and "
                "replug the board.",
                devices,
                extra,
            )
        if len(devices) > 1:
            return CandidateScan(
                False,
                SCAN_AMBIGUOUS,
                f"{len(devices)} boards are in DFU mode. Pick the one to flash by its "
                f"serial, or unplug the others.",
                devices,
                extra,
            )
        return CandidateScan(True, None, None, devices, extra)


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
