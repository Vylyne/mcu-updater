"""Declared identity joined with one sweep of the bus.

The one place a declared board meets what is plugged in. Every provider's rows
come from here: `type_status`, `_cmake_target` and the CLI's `status` used to
each scan and match privately, with different rules (a chipset filter in one,
none in another). Here there is one rule: exactly one sighting with the
declared serial is a match.

The sweep is injected - the same way `type_status` takes versions and canbus
results - so the join is testable without hardware and the agent can reuse a
sweep it already holds. Running state (FlashLog, device info) joins in a later
step; today a row is identity and presence.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from .discovery.byid import STATE_OFFLINE

if TYPE_CHECKING:
    from .discovery.byid import BusDevice
    from .typelist import TypeEntry

#: A row's `kind`: which declared identity it is.
SERIAL = "serial"
CANBUS_UUID = "canbus_uuid"

#: A CAN node's state: a missing version cannot tell an offline node from one
#: waiting in Katapult, so it is never guessed.
STATE_UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class Sweep:
    """What was seen: the by-id scan and the canbus results, keyed by lowercased uuid."""

    byid: tuple[BusDevice, ...] = ()
    canbus: Mapping[str, Mapping[str, Any]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Row:
    """One declared identity, and whether the sweep saw it."""

    type: str
    builder: str
    kind: str
    id: str
    present: bool
    state: str
    path: str | None
    device: BusDevice | None = None
    canbus: Mapping[str, Any] | None = None


def build(entries: Iterable[TypeEntry], sweep: Sweep) -> list[Row]:
    """A row per declared serial and canbus uuid, in type order."""
    rows: list[Row] = []
    for entry in entries:
        for serial in entry.serials:
            matches = [device for device in sweep.byid if device.serial == serial]
            match = matches[0] if len(matches) == 1 else None
            rows.append(
                Row(
                    type=entry.name,
                    builder=entry.builder,
                    kind=SERIAL,
                    id=serial,
                    present=match is not None,
                    state=match.state if match is not None else STATE_OFFLINE,
                    path=match.path if match is not None else None,
                    device=match,
                )
            )
        for uuid in entry.canbus_uuids:
            cross = sweep.canbus.get(uuid.lower())
            rows.append(
                Row(
                    type=entry.name,
                    builder=entry.builder,
                    kind=CANBUS_UUID,
                    id=uuid,
                    present=cross is not None,
                    state=STATE_UNKNOWN,
                    path=None,
                    canbus=cross,
                )
            )
    return rows


def index(rows: Iterable[Row]) -> dict[tuple[str, str, str], Row]:
    """Rows keyed by `(type, kind, id)`."""
    return {(row.type, row.kind, row.id): row for row in rows}
