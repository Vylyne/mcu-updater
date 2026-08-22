"""Which systemd units must be down before a write, resolved.

Three levels, most granular wins: ``[type ...]``/``[display ...]`` overrides
``[firmware ...]`` overrides ``[updater]`` overrides a per-provider built-in
default. Absent (``None``) at a level inherits the next one out; a value that
*is* set - even ``[]``, "stop nothing" - replaces every level beyond it and is
never merged with them. See ``docs/decisions.md`` and the sample
``mcu-updater.cfg`` for why override-never-merges is the point, not an
omission.

The pure resolver below has no imports from :mod:`settings`, :mod:`firmware`
or :mod:`config`, and none of them import this module for anything else -
keeping it a leaf is what lets all three call it without a cycle. The two
convenience wrappers underneath it are the exception: they import
:mod:`firmware` (lazily, to stay out of the import graph at load time) because
every caller needs to look up the same firmware family, and five copies of
that lookup is five chances for one of them to drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import McuType
    from .paths import Paths
    from .providers.pio import PioType
    from .settings import Settings

#: What a plain kconfig_make board (MCU) stops when nothing at any level says
#: otherwise. Klipper alone - exactly what every install did before this key
#: existed.
DEFAULT_MCU: tuple[str, ...] = ("klipper",)

#: What a PlatformIO display stops when nothing at any level says otherwise.
#: Klipper first, then the display's own port watcher - what the esptool
#: flasher hardcoded before this existed.
DEFAULT_DISPLAY: tuple[str, ...] = ("klipper", "knomi_serial")


def resolve_stop_services(
    *levels: list[str] | None,
    default: tuple[str, ...],
) -> list[str]:
    """The first explicitly-set level, most granular first.

    `levels` are passed most-specific-first - typically
    ``(type_level, firmware_level, updater_level)``. The first one that is not
    `None` wins outright, including an empty list; nothing beyond it is
    consulted and nothing is merged in from it. `default` applies only when
    every level was absent.
    """
    for level in levels:
        if level is not None:
            return level
    return list(default)


def for_mcu(
    paths: Paths,
    mcu: McuType,
    settings: Settings,
    families: dict | None = None,
) -> tuple[str, ...]:
    """The resolved list for one board's type: type, its application's
    firmware family, then `[updater]`, falling back to `DEFAULT_MCU`."""
    from . import firmware as firmware_mod

    application = mcu.application(families)
    family = firmware_mod.resolve(paths, application, families)
    return tuple(
        resolve_stop_services(
            mcu.stop_services, family.stop_services, settings.stop_services, default=DEFAULT_MCU
        )
    )


def for_display(
    paths: Paths,
    display: PioType,
    settings: Settings,
    families: dict | None = None,
) -> tuple[str, ...]:
    """The resolved list for one PlatformIO display type: type, its firmware
    family, then `[updater]`, falling back to `DEFAULT_DISPLAY`."""
    from . import firmware as firmware_mod

    family = firmware_mod.resolve(paths, display.firmware, families)
    return tuple(
        resolve_stop_services(
            display.stop_services,
            family.stop_services,
            settings.stop_services,
            default=DEFAULT_DISPLAY,
        )
    )
