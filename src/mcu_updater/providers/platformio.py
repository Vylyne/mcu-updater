"""PlatformIO: the second implementation that was here all along.

The reason the Provider seam is not being written on spec. When this was
deferred, the argument was that there would be two implementations and no third
committed - but PlatformIO *was* the second, written as a parallel module rather
than recognised as one. :mod:`~mcu_updater.displays` has its own `build()`, its
own `artifact_status()`, its own source-tree check; the display side agreed with
the MCU side about what those words meant only after the states vocabulary was
unified, and stayed duplicated regardless.

Like the kconfig adapter, this decides nothing. `displays.py` keeps its body -
including the parts with no MCU counterpart at all, such as never letting
PlatformIO choose its own upload port.

There is no family axis: a PlatformIO env already names the board, the partition
table and the build flags, so the env *is* the type. That is why these targets
carry `fw: None`, and why a `fw` filter - "rebuild katapult everywhere" -
correctly leaves every screen alone instead of matching one by accident.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from .. import displays as displays_mod
from ..build import Reporter
from ..states import ArtifactStatus
from .spec import BuildTarget, Install


def source_problem(display: displays_mod.DisplayType) -> Optional[str]:
    """Why this display cannot be built, or None if it can be attempted.

    A module function so the status payload can ask the same question without
    assembling an `Install` it does not need. `fw.status` has a sub-second budget
    and already holds the parsed sections; loading them again to answer one
    string would be the sort of duplication this package exists to remove.

    Absent and non-existent are separated because the fixes differ: one is a
    missing `source:` or `display_source:`, the other is a path that is there and
    wrong. A single "not configured" would send somebody to edit a key that is
    already set.
    """
    source = os.path.expanduser(display.source or "")
    if not source:
        return (
            f"display '{display.name}' has no source tree configured - set "
            f"'source:' in its [display] section, or 'display_source' in [updater]."
        )
    if not os.path.isdir(source):
        return f"source directory {source} not found for display '{display.name}'."
    return None


class PlatformIO:
    """Builds one PlatformIO env for one `[display ...]` section."""

    name = "platformio"
    label = "PlatformIO"

    def targets(self, install: Install) -> list[BuildTarget]:
        return [BuildTarget(self.name, name) for name in install.displays]

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
        return displays_mod.artifact_status(
            install.paths, display, displays_mod.source_state(display.source)
        )

    def build(
        self,
        install: Install,
        target: BuildTarget,
        *,
        reporter: Reporter,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        displays_mod.build(
            install.paths,
            install.settings,
            install.displays[target.name],
            reporter=reporter,
            cancel=cancel,
        )

    def describe(self, target: BuildTarget) -> str:
        return target.name
