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

from .. import typelist
from ..config import Registry
from ..errors import UnknownTypeError
from ..paths import Paths
from .cmake import BUILDER as CMAKE_BUILDER
from .cmake import Cmake
from .kconfig_make import KconfigMake
from .platformio import PlatformIO

#: The `builder:` value `pio.load()` claims a type on. Spelled here rather
#: than imported because `pio.py` matches the literal too.
PIO_BUILDER = "platformio"


def _declared_builders(paths: Paths) -> dict[str, str]:
    """Every declared type name -> the `builder:` of the family it names.

    This re-derives the ownership rule that `pio.load()` and `cmake.load()`
    each apply from their own side - "a type is ours if the family it declares
    is built by us" - rather than calling those loads, and the duplication is
    deliberate.

    Those loads *validate*: `cmake.load()` raises if a cmake type names no
    `cmake_target:`, `pio.load()` raises if a PlatformIO type names no `env:`.
    Resolving one name must not depend on every other section being
    well-formed. Asking them would mean a malformed screen section breaking
    `flash -t <kconfig type>`, which is a wider blast radius than the question
    deserves - selection answers "whose is this name", not "is this config
    good". The build path still runs the validating load and still raises.

    A section naming no family at all is absent from the result: both loads
    skip it, so neither provider claims it, and it falls through to the
    registry exactly as it does today.

    Read through `typelist.read_config`, which never raises for an undeclared
    family. Do not route this through `firmware.resolve()`: that refuses an
    undeclared name, and one bad section must not break resolving another.
    """
    entries, _ = typelist.read_config(paths)
    return {entry.name: entry.builder for entry in entries if entry.builder}


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
    builder = _declared_builders(paths).get(name)
    if builder == PIO_BUILDER:
        return PlatformIO.name
    # Before the registry, not after. A cmake type is kept out of it by
    # `config.py`'s foreign-builder rule, so the order does not matter
    # today - but if that ever slipped, asking the registry first would
    # answer `kconfig_make` for a Roadrunner and send it down a build path
    # that has no .config to run, silently. Asking here first cannot.
    if builder == CMAKE_BUILDER:
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
    return sorted(set(Registry.load(paths).names()) | set(_declared_builders(paths)))
