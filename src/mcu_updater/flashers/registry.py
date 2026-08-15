"""Which flashers exist, which chipsets bootstrap through which, and how a
batch splits around the Klipper stop.

**Static, and not discovered** - for the same reason the provider registry is.
A `pkgutil` walk would mean this process imports whatever `.py` somebody dropped
in, and this process holds the exclusive lock, writes firmware to boards, and
has NOPASSWD `systemctl` for Klipper. The tuple is the seam.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from ..errors import UnsupportedChipsetError
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import Flasher, FlashTarget

#: Every flasher. Order is not a batch order - a batch keeps the order its
#: selection produced - so this is just the set.
FLASHERS: tuple[Flasher, ...] = (Flashtool(), Esptool(), DfuUtil())

_BY_NAME: dict[str, Flasher] = {f.name: f for f in FLASHERS}


def by_name(name: str) -> Flasher:
    flasher = _BY_NAME.get(name)
    if flasher is None:
        raise KeyError(f"no flasher {name!r}; known: {sorted(_BY_NAME)}")
    return flasher


def group_by_stop(
    targets: list[FlashTarget],
) -> tuple[list[FlashTarget], list[FlashTarget]]:
    """Split a batch into (needs Klipper down, does not), preserving order.

    The whole point of the flag. Grouping by *requirement* rather than by kind
    is what lets one `klipper_stopped` cover boards and screens without either
    loop knowing the other exists - and what would let a write that needs no
    stop stay outside it rather than inheriting one it does not need.
    """
    stopped: list[FlashTarget] = []
    free: list[FlashTarget] = []
    for target in targets:
        (stopped if by_name(target.flasher).needs_klipper_stopped else free).append(
            target
        )
    return stopped, free


def by_flasher(targets: list[FlashTarget]) -> list[tuple[Flasher, list[FlashTarget]]]:
    """Group targets by the flasher that owns them, in first-seen order.

    Order preserved rather than sorted, because a batch's order came from its
    selection - the registry's order for boards, the config file's for screens -
    and reordering it inside a refactor is a behaviour change nobody asked for.
    """
    order: list[str] = []
    groups: dict[str, list[FlashTarget]] = {}
    for target in targets:
        if target.flasher not in groups:
            order.append(target.flasher)
            groups[target.flasher] = []
        groups[target.flasher].append(target)
    return [(by_name(name), groups[name]) for name in order]


# --------------------------------------------------------------------------
# bootstrap: how a bare board takes its first firmware
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BootstrapRoute:
    """The route into a chipset family that has no bootloader yet.

    Keyed by chipset prefix rather than exact name because that is how the
    question is actually shaped: every `stm32*` part exposes the same ROM DFU
    interface, and enumerating the two hundred Klipper supports would be a list
    to maintain for no gain.
    """

    #: Matched with `startswith`. First match wins, so order these
    #: most-specific-first if a prefix ever nests inside another.
    prefix: str
    #: Key into `FLASHERS`, or None when the route is known and not built.
    flasher: Optional[str] = None
    #: What to tell somebody whose board takes a route we do not drive. Only
    #: read when `flasher` is None.
    unavailable: str = ""


#: A chipset with no entry here is not "unsupported hardware" - it is hardware
#: whose first-flash route nobody has taught this tool. The distinction is in
#: the messages: a known-but-unbuilt route says what to do by hand, and an
#: unknown one says to flash katapult however you like and come back.
BOOTSTRAP: tuple[BootstrapRoute, ...] = (
    BootstrapRoute("stm32", flasher=DfuUtil.name),
    BootstrapRoute(
        "rp2040",
        unavailable=(
            "RP2040 BOOTSEL flashing isn't wired up yet - hold BOOTSEL, mount the "
            "RPI-RP2 drive, copy the katapult .uf2 across, then use 'add-serial' "
            "once it enumerates as Katapult."
        ),
    ),
)


def bootstrap_for(chipset: str) -> Flasher:
    """Which flasher installs a first bootloader on this chipset.

    Raises `UnsupportedChipsetError` either way it can fail, because both
    answers leave the user doing the same thing - flashing katapult themselves -
    and only the instructions differ.
    """
    for route in BOOTSTRAP:
        if not chipset.startswith(route.prefix):
            continue
        if route.flasher is None:
            raise UnsupportedChipsetError(route.unavailable, chipset=chipset)
        return by_name(route.flasher)
    raise UnsupportedChipsetError(
        f"don't know how to perform a first-time flash for chipset '{chipset}'. "
        f"Flash katapult manually, then use 'add-serial' once it enumerates.",
        chipset=chipset,
    )
