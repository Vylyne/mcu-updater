"""Ways of getting an image onto a device, behind one protocol.

    from mcu_updater import flashers

    stopped, free = flashers.group_by_stop(targets)
    with services_stopped(paths, controllers_for(flashers.stop_services_union(stopped)), ...):
        for flasher, mine in flashers.by_flasher(stopped):
            with flasher.prepared(bench, mine, ctx) as session:
                for target in mine:
                    flasher.write(bench, session, target, ctx)
                    flasher.settled(bench, target, ctx)

See :mod:`.spec` for what a flasher has to answer - `supports`, `target`, and
the rest - and :func:`.registry.select` for how a family picks one.
"""

from __future__ import annotations

from .batch import PlainContext, write_all
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .registry import (
    FLASHERS,
    by_flasher,
    by_name,
    candidate_scanner,
    group_by_stop,
    needs_services_stopped,
    refusal,
    resolve,
    select,
    select_device,
    select_each,
    stop_services_union,
)
from .spec import (
    KIND_BARE,
    KIND_CANBUS,
    KIND_SCREEN,
    KIND_SERIAL,
    Bench,
    CandidateScan,
    CandidateScanner,
    Device,
    Flasher,
    FlashRecord,
    FlashTarget,
    TrackedBoard,
    artifact_path,
    name_tracked,
    staged_record,
)

__all__ = [
    "FLASHERS",
    "KIND_BARE",
    "KIND_CANBUS",
    "KIND_SCREEN",
    "KIND_SERIAL",
    "Bench",
    "Bootsel",
    "CandidateScan",
    "CandidateScanner",
    "Device",
    "DfuUtil",
    "Esptool",
    "FlashRecord",
    "FlashTarget",
    "Flasher",
    "Flashtool",
    "PlainContext",
    "TrackedBoard",
    "artifact_path",
    "by_flasher",
    "by_name",
    "candidate_scanner",
    "group_by_stop",
    "name_tracked",
    "needs_services_stopped",
    "refusal",
    "resolve",
    "select",
    "select_device",
    "select_each",
    "staged_record",
    "stop_services_union",
    "write_all",
]
