"""Which flashers exist, which chipset/state pairs each can write, and how a
batch splits around the Klipper stop.

**Static, and not discovered** - for the same reason the provider registry is.
A `pkgutil` walk would mean this process imports whatever `.py` somebody dropped
in, and this process holds the exclusive lock, writes firmware to boards, and
has NOPASSWD `systemctl` for Klipper. The tuple is the seam.
"""

from __future__ import annotations

from ..errors import UnsupportedChipsetError
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import Flasher, FlashTarget

#: Every flasher. Order is not a batch order - a batch keeps the order its
#: selection produced - so this is just the set.
FLASHERS: tuple[Flasher, ...] = (Flashtool(), Esptool(), DfuUtil(), Bootsel())

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
    is what lets one `services_stopped` cover boards and screens without either
    loop knowing the other exists - and what would let a write that needs no
    stop stay outside it rather than inheriting one it does not need.
    """
    stopped: list[FlashTarget] = []
    free: list[FlashTarget] = []
    for target in targets:
        (stopped if by_name(target.flasher).needs_services_stopped else free).append(
            target
        )
    return stopped, free


def stop_services_union(targets: list[FlashTarget]) -> list[str]:
    """The first-seen-order union of `stop_services` across a group.

    One outage covers the whole group rather than one per distinct set - ten
    stop/start cycles is the thing `write_all`'s docstring already argues
    against, and stopping a unit a given target did not ask for is harmless
    since it comes back. Order matters (`services_stopped` stops in list
    order and restarts in reverse), so this is a dict used as an ordered set,
    not a `set()`.
    """
    seen: dict[str, None] = {}
    for target in targets:
        for unit in target.stop_services:
            seen.setdefault(unit, None)
    return list(seen)


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
# selection: which flasher writes a chipset while it's in a given state
# --------------------------------------------------------------------------


def select_for(chipset: str, state: str) -> Flasher:
    """Which flasher writes a device of this chipset while it is in this state.

    A capability match against `FLASHERS` itself - each flasher's own
    `chipsets`/`states` are the whole answer, so there is no separate table to
    keep in step. First-time install is not special: it is a selection where
    `state` happens to be `dfu` or `bootsel`, same as any other.

    Raises `UnsupportedChipsetError` when nothing registered answers to this
    chipset/state pair - the user's only recourse is to flash katapult
    manually, then use 'add-serial' once it enumerates.
    """
    for f in FLASHERS:
        if state in f.states and any(chipset.startswith(p) for p in f.chipsets):
            return f
    raise UnsupportedChipsetError(
        f"don't know how to perform a first-time flash for chipset '{chipset}'. "
        f"Flash katapult manually, then use 'add-serial' once it enumerates.",
        chipset=chipset,
    )
