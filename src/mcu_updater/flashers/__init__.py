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

from .batch import PlainContext, write_all
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .registry import (
    FLASHERS,
    by_flasher,
    by_name,
    group_by_stop,
    select_for,
)
from .spec import Bench, Flasher, FlashTarget

__all__ = [
    "FLASHERS",
    "Bench",
    "DfuUtil",
    "Esptool",
    "FlashTarget",
    "Flasher",
    "Flashtool",
    "PlainContext",
    "by_flasher",
    "by_name",
    "group_by_stop",
    "select_for",
    "write_all",
]
