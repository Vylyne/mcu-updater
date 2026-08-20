"""What it takes to put an image on a device, so a batch never has to know how.

The build half of this project had two parallel implementations; the flash half
has three, and they were written at three different times. `flash_katapult`
reboots a board into its bootloader and hands the binary to katapult's
`flashtool.py`. `displays.upload` drives PlatformIO's esptool at a port it has
to *rediscover* first, because every screen here is an indistinguishable CH340.
`flash_dfu_stm32` writes to a bare board holding BOOT0, before any bootloader
exists to speak a protocol.

Nothing named the thing all three are. So `_do_flash_all` grew a loop that knew
about katapult, `display_flash` grew a second loop that knew about screens, and
"Flash All" meant "flash all the boards" with no way to say otherwise.

Three members carry the whole difference between them, and each replaces
something that is currently hand-written inside one of those loops:

**`needs_klipper_stopped`** - so a batch groups by *requirement* rather than by
kind, and one stop covers boards and screens without either knowing the other
exists.

**`prepared()`** - the once-per-batch work that can only happen after Klipper is
down: pausing a port watcher, and asking the screens which they are now that the
ports are free. Hoisting that into the batch loop is exactly the branching this
removes.

**`settled()`** - the wait after a write, which has to stay *per device*. A
board re-enumerates over USB and starting Klipper before its node exists brings
it up in an error state; the last board of a batch would otherwise race the
service restart. A screen has nothing to wait for, so it is a no-op there rather
than an ``if kind ==``.

**Selection is not here.** Which devices exist, where they are and which of them
want firmware is the Inventory axis, and that stays deferred on its own
criterion - two implementations, and the third (CAN) is not committed.
Half-inventing it inside this seam is how a deferral quietly stops being one.
The agent selects and says which flasher owns each device; a flasher writes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, Callable, Optional, Protocol

from ..paths import Paths
from ..service import ServiceController
from ..settings import Settings


@dataclasses.dataclass(frozen=True)
class Bench:
    """The host a write happens on, as a flasher needs to see it.

    `controller` rather than a `ServiceController`, because the units are not
    known until the batch is: a display family names its own port watcher, and a
    batch spanning two families needs two. The factory keeps the *backend*
    choice in one place - a dry run must stay a dry run for every unit, or a
    rehearsal stops a real service.
    """

    paths: Paths
    settings: Settings
    #: unit name -> controller. `None` means Klipper's own service.
    controller: Callable[[Optional[str]], ServiceController]


@dataclasses.dataclass(frozen=True)
class FlashTarget:
    """One device to write, and who writes it.

    A key plus an envelope. `type` and `id` are the two facts every caller needs
    and are the same two slots `targets[].devices[]` already uses - the board's
    `[mcu]` name and its serial, the display's `[display]` name and its
    configured port.

    `detail` is the owning flasher's private payload and nothing else reads it.
    That is deliberate: a chipset means nothing to esptool and a klippy section
    means nothing to flashtool, and inventing a union of the two would be a
    third description of a device to keep in step with the two that exist.
    """

    #: Key into `flashers.FLASHERS`.
    flasher: str
    #: The `[mcu ...]` or `[display ...]` section name.
    type: str
    #: What identifies the device: a serial for a board, a configured port for a
    #: screen.
    id: str
    detail: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """The uniform slice. `detail` never goes on the wire - it holds live
        objects, and it is one flasher's business."""
        return {"type": self.type, "id": self.id, "flasher": self.flasher}


class Flasher(Protocol):
    """A way of getting an image onto a device."""

    #: Key into the registry, and what goes on the wire beside each result.
    name: str
    #: What a human calls this tool.
    label: str
    #: Chipset prefixes this flasher can write, matched with `startswith` - one
    #: `stm32*` part exposes the same protocol as any other, so this is prefixes
    #: rather than the two hundred exact names Klipper supports.
    #:
    #: Together with `states`, this is the whole answer `flashers.registry.
    #: select_for` matches against - there is no separate lookup table, so a
    #: route registering a new flasher is a route selection already knows about.
    chipsets: tuple[str, ...]
    #: Bus/device states (`devices.STATE_*`) this flasher answers to. A board
    #: already running Klipper and one sitting in Katapult are both states
    #: `Flashtool` answers to - the write goes through the bootloader either
    #: way. A bare board in DFU is a different flasher entirely, even though
    #: it may be the same chipset.
    states: tuple[str, ...]
    #: Does *this flasher's own work* need Klipper down?
    #:
    #: Scoped to the write and to any state transition the flasher performs
    #: itself - not to the device's lifetime. flashtool needs it not because the
    #: write does but because *getting there* does: the reboot-into-katapult
    #: request goes over the serial port Klipper is holding. esptool needs it
    #: because the klippy module holds the port for the write itself.
    #:
    #: dfu-util does not, and that is the one worth watching. It holds only
    #: while entering DFU is somebody else's problem - today the user fits the
    #: boot jumper and replugs, so by the time we are called the board is
    #: already there, which means either it was never on the Klipper bus or
    #: whatever put it there already dealt with Klipper. The moment this tool
    #: routes a board into DFU itself, that transition goes over a port Klipper
    #: may be holding and this flips to True.
    #:
    #: TODO: this wants generalising to "which services must be stopped", with
    #: a per-type list in config defaulting to klipper. Klipper is only the
    #: right answer because most boards here are Klipper MCUs; a display already
    #: needs its port watcher paused as well, and that is currently hardcoded in
    #: the esptool flasher rather than configured. Not renamed yet on purpose -
    #: `needs_service_stopped` without the list behind it would be *less* true
    #: than this name, since only Klipper is actually stopped. The two land
    #: together or not at all.
    needs_klipper_stopped: bool

    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> AbstractContextManager[Any]:
        """Set up once for the whole batch, and tear down after it.

        Entered *inside* the Klipper stop when this flasher needs one, so
        anything requiring free ports belongs here and nowhere else.

        Yields a session, which is opaque to the batch and handed back to this
        same flasher's `write`. Untyped on purpose: what esptool needs to carry
        across a batch is a map of which screen answered on which port, and
        what flashtool needs is nothing at all.
        """
        ...

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        """Put the image on the device. Raises `UpdaterError` on failure.

        Never cancellable. Interrupting a write leaves half an image on a
        board, so the batch checks between targets and never inside one.

        Returns whatever is worth recording beside the uniform result - the chip
        esptool reported, a board's serial under the name it has always had.
        """
        ...

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Wait for the device to come back, if it has to.

        Never fatal. The write already succeeded, and the readiness check after
        the batch is the real verdict; raising here would turn a good flash into
        a reported failure.
        """
        ...


__all__ = ["Bench", "FlashTarget", "Flasher"]
