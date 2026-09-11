"""Which provider owns a type name, answered once.

    from mcu_updater import providers

    owner = providers.provider_of(paths, "roadrunner")

:mod:`.spec` says flashing is not a provider's business; :mod:`..flashers`
says selection is not a flasher's business. Both exclusions are right, and
together they left this question homeless - so every caller that needed it
answered it privately, and the two that guessed encoded a two-provider world
that stopped being true when CMake arrived.

This is the answer they should all have been asking for. It is a pure function
of :class:`~mcu_updater.paths.Paths`: every input is re-read per call, because
a type added over ``fw.type.add`` has to be answerable without restarting the
agent.
"""

from __future__ import annotations

from ..config import Registry
from ..errors import UnknownTypeError
from ..paths import Paths
from . import cmake as cmake_mod
from . import pio as pio_mod
from .cmake import Cmake
from .kconfig_make import KconfigMake
from .platformio import PlatformIO


def provider_of(paths: Paths, name: str) -> str:
    """Which build system owns this type, by name.

    The whole reason `fw.display.build` existed as a separate method: the
    caller had to know which kind of thing it was addressing, so the panel
    carried a `kind` and picked a method from it. It does not have to. A type
    name resolves to exactly one provider, and this is where that happens -
    once, rather than at every call site that would otherwise branch.

    Raises rather than guessing. A name belonging to none of them is a typo or
    a section somebody deleted, and defaulting it to kconfig would produce
    "no saved klipper config" for a screen.
    """
    if name in pio_mod.load(paths):
        return PlatformIO.name
    # Before the registry, not after. A cmake type is kept out of it by
    # `config.py`'s foreign-builder rule, so the order does not matter
    # today - but if that ever slipped, asking the registry first would
    # answer `kconfig_make` for a Roadrunner and send it down a build path
    # that has no .config to run, silently. Asking here first cannot.
    if name in cmake_mod.load(paths):
        return Cmake.name
    if name in Registry.load(paths).names():
        return KconfigMake.name
    raise UnknownTypeError(
        f"no type '{name}' is configured.",
        type=name,
        known=known_type_names(paths),
    )


def known_type_names(paths: Paths) -> list[str]:
    """Every configured type name, whichever provider owns it.

    Sorted and de-duplicated. Callers put this in an error payload so an
    operator who mistyped a name can see what they meant to type.
    """
    return sorted(
        set(Registry.load(paths).names())
        | set(pio_mod.load(paths))
        | set(cmake_mod.load(paths))
    )
