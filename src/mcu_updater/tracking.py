"""Tracking boards and types, for every builder, from the agent and the CLI alike.

`fw.serial.add` already worked across builders, and the CLI's `add-serial` did
not - it looked the type up in the kconfig registry, so a Roadrunner type did
not exist for it. One implementation, called from both, is how the two stop
disagreeing.
"""

from __future__ import annotations

import dataclasses

from .config import Registry
from .errors import SerialTrackedElsewhereError, UnprovisionedSerialError
from .paths import Paths


@dataclasses.dataclass(frozen=True)
class Tracked:
    """What tracking a serial did.

    `serial` is what ended up in `serials:`, which is not always what the
    caller asked for: an unprovisioned board is given its durable identity
    first, and that is the one tracked. `provisioned_from` is the serial the
    caller passed, and is None when nothing moved - so a caller that just
    forwards both fields reports the truth either way.
    """

    added: bool
    chipset: str
    serial: str
    provisioned_from: str | None = None


def _provisioner_for(paths: Paths, name: str):
    """The provisioning capability of type `name`'s firmware family, or None.

    Read before anything is written to a board, so a typo in the type name is
    an `UnknownTypeError` rather than a provisioned board nobody asked for.
    """
    from . import firmware, helpers, typelist

    entries, families = typelist.read_config(paths)
    entry = next((e for e in entries if e.name == name), None)
    if entry is None or not entry.firmwares:
        return None
    family = firmware.resolve(paths, entry.firmwares[0], families)
    return helpers.provisioner(helpers.for_name(family.helper, family=family.name))


def add_serial(paths: Paths, name: str, serial: str) -> Tracked:
    """Track `serial` under type `name`, provisioning it first if it needs it.

    Spec section 11. A board whose serial is its unprovisioned diagnostic
    identity is provisioned under the op lock and the *returned* serial is
    tracked - the two steps share a precondition (this board, here, untracked,
    nobody else on the bus) and splitting them only creates an order to get
    wrong.

    A held lock raises `BusyError` and is never retried: provisioning writes
    irreversibly to a board, and queueing behind a flash would perform that
    write at a moment nobody chose.
    """
    provisioned_from: str | None = None
    prov = _provisioner_for(paths, name)  # UnknownTypeError before any write
    if prov is not None and prov.is_unprovisioned(serial):
        from .lock import exclusive

        with exclusive(paths, f"provision {serial} for {name}"):
            provisioned = prov.provision(paths, serial)
        provisioned_from, serial = serial, provisioned

    with Registry.mutate(paths, f"add serial {serial}") as reg:
        chipset = reg.get_declared_chipset(name)  # UnknownTypeError if absent

        # An unprovisioned Roadrunner's serial is `RR-UNPROVISIONED-<flash-uid>`
        # - the trailing 16 hex characters ARE the RP2040 flash UID, which this
        # plan's constraints forbid ever persisting. Reached only when nothing
        # could provision it (above), which is where "provision it first" is
        # still the useful answer. Checked here, not only from the agent's live
        # bus scan, so every caller of this function refuses one. Not every
        # registry write: the agent's pairing-key adoption
        # (agent/methods/flash.py) calls Registry.add_serial directly, and only
        # ever for kconfig types.
        from .discovery.roadrunner import UNPROVISIONED_RE

        if UNPROVISIONED_RE.fullmatch(serial):
            raise UnprovisionedSerialError(
                f"'{serial}' is an unprovisioned Roadrunner's diagnostic identity, "
                f"not a stable serial - provision it first (the web UI's Provision "
                f"Roadrunner action, or fw.roadrunner.provision), then track the "
                f"resulting RR-... serial.",
                serial=serial,
            )

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
    return Tracked(
        added=added,
        chipset=chipset,
        serial=serial,
        provisioned_from=provisioned_from,
    )


def remove_serial(paths: Paths, name: str, serial: str) -> bool:
    """Stop tracking `serial` under `name`. False if it was not tracked there."""
    with Registry.mutate(paths, f"remove serial {serial}") as reg:
        reg.get_declared_chipset(name)  # UnknownTypeError if the type doesn't exist
        return reg.remove_declared_serial(name, serial)


def remove_type(paths: Paths, name: str) -> list[str]:
    """Delete type `name`, whichever builder owns it. Returns the serials it tracked."""
    with Registry.mutate(paths, f"remove type {name}") as reg:
        return reg.remove_declared_type(name)
