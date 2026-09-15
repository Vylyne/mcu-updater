"""Tracking boards and types, for every builder, from the agent and the CLI alike.

`fw.serial.add` already worked across builders, and the CLI's `add-serial` did
not - it looked the type up in the kconfig registry, so a Roadrunner type did
not exist for it. One implementation, called from both, is how the two stop
disagreeing.
"""

from __future__ import annotations

from .config import Registry
from .errors import SerialTrackedElsewhereError
from .paths import Paths


def add_serial(paths: Paths, name: str, serial: str) -> tuple[bool, str]:
    """Track `serial` under type `name`. Returns (added, the type's chipset)."""
    with Registry.mutate(paths, f"add serial {serial}") as reg:
        chipset = reg.get_declared_chipset(name)  # UnknownTypeError if absent
        # One board tracked under two types would get flashed twice with
        # different firmware, so this is refused rather than merged.
        elsewhere = [t for t in reg.find_declared_types_for_serial(serial) if t != name]
        if elsewhere:
            raise SerialTrackedElsewhereError(
                f"serial '{serial}' is already tracked under '{elsewhere[0]}'. "
                f"Remove it from there first if it really belongs to '{name}'.",
                serial=serial,
                requested=name,
                tracked_under=elsewhere,
            )
        added = reg.add_declared_serial(name, serial)
    return added, chipset


def remove_serial(paths: Paths, name: str, serial: str) -> bool:
    """Stop tracking `serial` under `name`. False if it was not tracked there."""
    with Registry.mutate(paths, f"remove serial {serial}") as reg:
        reg.get_declared_chipset(name)  # UnknownTypeError if the type doesn't exist
        return reg.remove_declared_serial(name, serial)


def remove_type(paths: Paths, name: str) -> list[str]:
    """Delete type `name`, whichever builder owns it. Returns the serials it tracked."""
    with Registry.mutate(paths, f"remove type {name}") as reg:
        return reg.remove_declared_type(name)
