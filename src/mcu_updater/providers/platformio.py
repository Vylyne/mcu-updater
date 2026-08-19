"""PlatformIO: the second implementation that was here all along.

The reason the Provider seam is not being written on spec. When this was
deferred, the argument was that there would be two implementations and no third
committed - but PlatformIO *was* the second, written as a parallel module rather
than recognised as one. :mod:`~mcu_updater.providers.pio` has its own `build()`,
its own `artifact_status()`, its own source-tree check; it agreed with the
kconfig side about what those words meant only after the states vocabulary was
unified, and stayed duplicated regardless.

Like the kconfig adapter, this decides nothing. `pio.py` keeps its body -
including the parts with no kconfig counterpart at all, such as never letting
PlatformIO choose its own upload port.

A type declaring `firmware:` (see the target schema) names a real family, and
that family is what `fw` carries here - "rebuild knomi_serial everywhere" then
correctly reaches it. A type predating that key has no family to name, so its
own name stands in: a PlatformIO env already names the board, the partition
table and the build flags, so the env *is* the type, and a `fw` filter for
anything else still correctly leaves it alone.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from ..build import Reporter
from ..states import ArtifactStatus
from . import pio as pio_mod
from .spec import BuildTarget, Install


def source_problem(target: pio_mod.PioType) -> Optional[str]:
    """Why this type cannot be built, or None if it can be attempted.

    A module function so the status payload can ask the same question without
    assembling an `Install` it does not need. `fw.status` has a sub-second budget
    and already holds the parsed sections; loading them again to answer one
    string would be the sort of duplication this package exists to remove.

    Absent and non-existent are separated because the fixes differ: one is a
    missing `source:` or `pio_source:`, the other is a path that is there and
    wrong. A single "not configured" would send somebody to edit a key that is
    already set.
    """
    source = os.path.expanduser(target.source or "")
    if not source:
        return (
            f"'{target.name}' has no source tree configured - set 'source:' in its "
            f"section, or 'pio_source' in [updater]."
        )
    if not os.path.isdir(source):
        return f"source directory {source} not found for '{target.name}'."
    return None


class PlatformIO:
    """Builds one PlatformIO env: one type whose `provider:` names this."""

    name = "platformio"
    label = "PlatformIO"

    def targets(self, install: Install) -> list[BuildTarget]:
        return [
            BuildTarget(self.name, name, display.firmware or name)
            for name, display in install.displays.items()
        ]

    def blocked(self, install: Install, target: BuildTarget) -> Optional[str]:
        """Is there a tree to build in?

        The counterpart of "has this been through menuconfig": the one thing
        somebody has to do outside this tool before a build is possible at all.
        A display with no source tree is skipped rather than failed for the same
        reason an unconfigured MCU type is - there is nothing the batch could do
        about it, and it should not take the fleet down with it.
        """
        display = install.displays.get(target.name)
        if display is None:
            return f"no display type '{target.name}' is configured."
        return source_problem(display)

    def artifact_status(self, install: Install, target: BuildTarget) -> ArtifactStatus:
        display = install.displays[target.name]
        return pio_mod.artifact_status(
            install.paths, display, pio_mod.source_state(display.source)
        )

    def build(
        self,
        install: Install,
        target: BuildTarget,
        *,
        reporter: Reporter,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        pio_mod.build(
            install.paths,
            install.settings,
            install.displays[target.name],
            reporter=reporter,
            cancel=cancel,
        )

    def describe(self, target: BuildTarget) -> str:
        return target.name
