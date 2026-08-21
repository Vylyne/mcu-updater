"""Where a device is, and how sure we are - the Inventory axis, named at last.

Six things already answer "what's out there": a by-id scan, ``dfu-util -l``, an
``RPI-RP2`` mount, the knomi broadcast-listen pass, the watcher's
``devices.json`` map, and Klipper's own ``printer.objects.query``. Each returns
its own shape, and every caller adapts. `providers/spec.py` and
`flashers/spec.py` both deferred this axis in near-identical words - "two
implementations, and the third is not committed" - which was true when
discovery meant a by-id scan plus DFU. It is not true any more.

**Identity and state are different axes.** Identity is chipset + serial: what
the silicon is, fixed for the device's life, and what a lookup matches on.
State is what the device is currently running, which changes on every flash.
Conflating them is what let `BusDevice.state` (`devices.py:88`) return a
firmware name - `"cartographer"` - as if it were a state, which meant a fork
was a case dispatch had never seen and had to be told about by hand.

**The rule, decided.** Once chipset+serial have matched, the firmware name
answers one question only: is this the bootloader, or not?

    fw in KATAPULT_NAMES  ->  STATE_KATAPULT   # in the bootloader
    anything else         ->  STATE_KLIPPER    # running an application
    not on the bus        ->  STATE_OFFLINE

That inverts `BusDevice.state`'s own default, and the inversion is the point:
defaulting to "running an application" means a new fork is never a case to add,
because nothing about state dispatch names it. `STATE_KLIPPER` means "running
an application", not "running Klipper" - it was already the name every
flasher's `states` tuple matches on, so it is not renamed here; this module
documents what it means rather than relitigating it.

**This inversion is only sound once identity has matched.** An *untracked*
candidate has no identity to match against, so there the firmware name is the
only evidence a parsed by-id entry is a board at all - `devices.is_mcu` keeps
its allowlist, and nothing here overrides it. Two different questions, answered
by two different rules.

Nothing here does anything yet. This is vocabulary and a Protocol - no
implementation, no importer - so it is green by construction. The bus sources
move behind it in a later step; a source list stays ``()`` until they do.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from .. import devices
from ..flashers.spec import Bench

# --------------------------------------------------------------------------
# identity vs. state - see the module docstring for the rule
# --------------------------------------------------------------------------

#: Reuse `devices.STATE_*` verbatim rather than a parallel vocabulary for the
#: same facts - a second enum meaning the same thing is a second thing to keep
#: in step, which is the failure `states.py` exists to have already fixed once.
STATE_KLIPPER = devices.STATE_KLIPPER
STATE_KATAPULT = devices.STATE_KATAPULT
STATE_OFFLINE = devices.STATE_OFFLINE
STATE_DFU = devices.STATE_DFU
STATE_BOOTSEL = devices.STATE_BOOTSEL
STATE_ESP_ROM = devices.STATE_ESP_ROM


def state_for_firmware(fw: str) -> str:
    """The bootloader-predicate rule. `fw` is the name a device announced
    itself under, once chipset+serial have already matched a known device -
    never call this to decide whether an unidentified by-id entry is a board
    at all; that is `devices.is_mcu`'s question, not this one."""
    if fw.lower() in devices.KATAPULT_NAMES:
        return STATE_KATAPULT
    return STATE_KLIPPER


@dataclasses.dataclass(frozen=True)
class Sighting:
    """One device, as one source currently sees it.

    Modelled on `flashers.spec.FlashTarget`: a key plus an envelope. `id` is
    the durable identity - a by-id serial, a DFU serial, a knomi device id -
    and `""` when the source cannot give one at all (a positional source, or
    one that only knows an address). `address` is what you would hand a tool
    to reach the device right now, which is allowed to change between two
    sightings of the same `id`.
    """

    #: Durable identity: by-id serial, DFU serial, knomi device id. `""` when
    #: this source cannot give one - never `None`, so a caller cannot forget
    #: to check and silently key a dict on a missing identity.
    id: str
    #: What you hand a tool right now: a `/dev/serial/by-id` path, a DFU bus
    #: address, a knomi host:port.
    address: str
    #: One of the `STATE_*` constants above.
    state: str
    #: Key into a future `discovery.registry.SOURCES` - which source produced
    #: this sighting.
    source: str
    #: The source's own private payload. Nothing else reads it, the same rule
    #: `FlashTarget.detail` follows and for the same reason: a union of every
    #: source's facts would be a fourth vocabulary to keep in step.
    detail: dict[str, object] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        """The uniform slice. `detail` never goes on the wire."""
        return {
            "id": self.id,
            "address": self.address,
            "state": self.state,
            "source": self.source,
        }


# --------------------------------------------------------------------------
# how sure we are - the reason is the fact, exactly as `states.py` does it
# --------------------------------------------------------------------------

#: It spoke, just now, with the ports free to ask it directly.
ANSWERED = "answered"
#: The kernel names it with a die-derived serial - a by-id entry, a DFU serial.
#: As good as answering, without needing the ports free to get it.
UNIQUE_BUS_ID = "unique_bus_id"
#: A map or config written earlier by something else - `devices.json`, a saved
#: registry entry. True until proven otherwise, which is weaker than it sounds.
REMEMBERED = "remembered"
#: Only topology identifies it - which USB port, which position in a list.
#: Survives nothing: a replug, a reboot, another device sharing the hub.
POSITIONAL = "positional"
#: Something is at this address. Nothing vouches for what.
UNCONFIRMED = "unconfirmed"

_REASONS = (ANSWERED, UNIQUE_BUS_ID, REMEMBERED, POSITIONAL, UNCONFIRMED)

#: Three tones, exactly as `states.TONE_*` - not a fourth "probably" bucket.
#: One more degree of certainty is one more thing for two call sites to
#: disagree about.
_TONE: dict[str, str] = {
    ANSWERED: "ok",
    UNIQUE_BUS_ID: "ok",
    REMEMBERED: "unknown",
    POSITIONAL: "unknown",
    UNCONFIRMED: "unknown",
}

_LABEL: dict[str, str] = {
    ANSWERED: "Confirmed",
    UNIQUE_BUS_ID: "Confirmed",
    REMEMBERED: "Remembered",
    POSITIONAL: "Unconfirmed position",
    UNCONFIRMED: "Unconfirmed",
}

#: Only these two reasons are strong enough to write firmware against. The
#: other three are all "something answers to this address", which is not the
#: same claim as "this identity is really there".
_SAFE_TO_WRITE: dict[str, bool | None] = {
    ANSWERED: True,
    UNIQUE_BUS_ID: True,
    REMEMBERED: None,
    POSITIONAL: None,
    UNCONFIRMED: None,
}


@dataclasses.dataclass(frozen=True)
class Confidence:
    """How sure a `Sighting` is, and why - never the other way around.

    The reason is the fact; `tone`, `label` and `safe_to_write` are derived
    from it rather than stored alongside it, so an inconsistent pair - "we
    answered it, but don't trust it" - cannot be constructed at all. The same
    discipline `states.ArtifactStatus`/`DeviceStatus` already enforce, for the
    same reason: two independent fields are only in step for as long as every
    call site remembers to keep them there.
    """

    reason: str

    def __post_init__(self) -> None:
        if self.reason not in _REASONS:
            raise ValueError(
                f"unknown confidence reason {self.reason!r}; "
                f"expected one of {', '.join(_REASONS)}"
            )

    @property
    def tone(self) -> str:
        return _TONE[self.reason]

    @property
    def label(self) -> str:
        return _LABEL[self.reason]

    @property
    def safe_to_write(self) -> bool | None:
        """True, None - never False. Absence of evidence that an identity is
        really there is not evidence that it is not; a caller that wants a
        hard refusal makes that call itself, on `is None`, rather than this
        type manufacturing a certainty it does not have."""
        return _SAFE_TO_WRITE[self.reason]


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------


class Source(Protocol):
    """One way of asking "what's out there" - a by-id scan, a DFU query, a
    knomi listen pass. Mirrors `flashers.spec.Flasher` deliberately: both are
    static, capability-declaring members of a registry tuple, matched rather
    than dispatched on by kind."""

    #: Key into a future `discovery.registry.SOURCES`.
    name: str
    #: What a human calls this source.
    label: str
    #: `STATE_*` values this source can report. Declared rather than assumed,
    #: the same reason `Flasher.states` is - a by-id scan never reports
    #: `STATE_DFU`, and a source claiming a state it cannot produce would be
    #: silently trusted by anything that filters on the declared set.
    states: tuple[str, ...]
    #: Does asking this source need every other user of the port gone first?
    #: True for the knomi listen pass, which opens real serial ports and
    #: fights Klipper and the watcher for them if either is still running.
    #: False for a by-id scan or a remembered map, which read state nobody
    #: else is holding.
    needs_ports_free: bool

    def sight(self, bench: Bench) -> list[Sighting]:
        """Everything this source currently sees. Raises rather than
        returning a partial or stale answer when `needs_ports_free` is True
        and the ports are not free - a hint here would eventually let this
        run on a poll path and fight Klipper for the port every few
        seconds."""
        ...


__all__ = [
    "STATE_KLIPPER",
    "STATE_KATAPULT",
    "STATE_OFFLINE",
    "STATE_DFU",
    "STATE_BOOTSEL",
    "STATE_ESP_ROM",
    "state_for_firmware",
    "Sighting",
    "ANSWERED",
    "UNIQUE_BUS_ID",
    "REMEMBERED",
    "POSITIONAL",
    "UNCONFIRMED",
    "Confidence",
    "Source",
]
