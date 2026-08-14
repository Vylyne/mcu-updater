"""Ways of getting an image onto a device, behind one protocol.

    from mcu_updater import flashers

    stopped, free = flashers.group_by_stop(targets)
    with klipper_stopped(...):
        for flasher, mine in flashers.by_flasher(stopped):
            with flasher.prepared(bench, mine, ctx) as session:
                for target in mine:
                    flasher.write(bench, session, target, ctx)
                    flasher.settled(bench, target, ctx)

See :mod:`.spec` for what a flasher has to answer, and why selection is not part
of it.
"""

from __future__ import annotations

from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .registry import (
    BOOTSTRAP,
    FLASHERS,
    BootstrapRoute,
    bootstrap_for,
    by_name,
    group_by_stop,
)
from .spec import Bench, Flasher, FlashTarget


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


__all__ = [
    "BOOTSTRAP",
    "FLASHERS",
    "Bench",
    "BootstrapRoute",
    "DfuUtil",
    "Esptool",
    "FlashTarget",
    "Flasher",
    "Flashtool",
    "bootstrap_for",
    "by_flasher",
    "by_name",
    "group_by_stop",
]
