"""Build systems, behind one protocol.

    from mcu_updater import providers

    install = providers.Install.load(paths, settings)
    for target in providers.select(install, stale_only=True).build:
        providers.by_name(target.provider).build(install, target, reporter=log)

See :mod:`.spec` for what a provider has to answer and why the seam stops where
it does.
"""

from __future__ import annotations

from .kconfig_make import KconfigMake
from .platformio import PlatformIO
from .registry import PROVIDERS, Selection, by_name, select
from .spec import BuildTarget, Install, Provider, Skipped

__all__ = [
    "PROVIDERS",
    "BuildTarget",
    "Install",
    "KconfigMake",
    "PlatformIO",
    "Provider",
    "Selection",
    "Skipped",
    "by_name",
    "select",
]
