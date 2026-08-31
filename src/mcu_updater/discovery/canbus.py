"""CAN bus discovery: unclaimed boards only.

Deliberately **not** a `discovery.spec.Source`, and **not** added to
`discovery.registry.SOURCES` - see this module's own history in the plan this
implements. `discovery/confirm.py`'s `confirm()` calls `source.sight(bench)`
unconditionally for every source in whatever list a caller passes, and
`flashers/flash.py`'s `device_for()` passes `sources=SOURCES` on every single
USB board flash. Putting CAN discovery in that tuple would mean every
ordinary USB flash silently shells out to `flashtool.py --query` on the CAN
bus for no reason that flash needed. This mirrors existing precedent:
`discovery/dfu.py` and `discovery/bootsel.py` also stay outside
`Source`/`SOURCES` - nothing needs them swept automatically either.

**What `--query` actually finds.** `flashtool.py --query` broadcasts a "who
has no CAN node id yet" admin request. A board klippy has already connected
to (which assigns it a node id as part of establishing the link) goes silent
to further queries - confirmed both live on the bench and from Klipper's own
firmware source (`can_process_query_unassigned()` returns early once
`CanData.assigned_id` is set). That makes this a reliable way to discover
**unclaimed** CAN boards - freshly flashed, not yet in `printer.cfg`, or
tracked here with no live klippy connection - and *not* a way to poll an
already-adopted, actively-connected board. That is the same "on bus, want to
adopt it?" shape `discovery/byid.py`'s untracked-serial view gives USB boards,
just for CAN.

No `is_mcu`-style filtering is needed here: every CAN admin responder is
inherently a Katapult- or Klipper-speaking node - the protocol itself names
the application in its reply - unlike a USB bridge chip that merely *looks*
like a board on `/dev/serial/by-id`.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys

from ..build import Reporter, null_reporter, run_streamed
from ..flashers.flash import find_flashtool
from ..paths import Paths
from ..settings import Settings
from .spec import state_for_firmware

#: ARPHRD_CAN, from <linux/if_arp.h> - the kernel's own answer to "is this a
#: CAN interface", read out of a network device's own sysfs `type` file. Not
#: a naming convention (`can0`, `can1`, ...): a host with more than one
#: USB-CAN bridge has more than one independent physical bus, each its own
#: interface, and nothing says they enumerate in order or by that name at all.
ARPHRD_CAN = "280"

_DEFAULT_SYS_CLASS_NET = "/sys/class/net"

#: flashtool's own words. Matched loosely enough to survive incidental
#: reformatting, tightly enough to not fire on unrelated output -
#: `docs/backlog.md` already flags flashtool's output as human-readable only,
#: never machine-readable, so this is a defensive parse, not a trusted one.
_QUERY_LINE_RE = re.compile(
    r"Detected UUID:\s*([0-9a-fA-F]+),\s*Application:\s*(Klipper|Katapult|Unknown)"
)

#: The success sentinel: query-unassigned admin requests take a fixed timeout
#: to be sure nothing else is going to answer, so flashtool prints this once
#: it has genuinely finished listening, not just when it happens to exit.
QUERY_COMPLETE_RE = re.compile(r"CANBus UUID Query Complete")


@dataclasses.dataclass(frozen=True)
class CanSighting:
    """One unclaimed CAN board, as `flashtool.py --query` currently sees it.

    Mirrors `discovery.byid.BusDevice` in spirit - identity plus a computed
    state - but is not a `Sighting`: nothing here feeds `discovery.confirm`,
    per the module docstring above.
    """

    #: Lower-cased hex, e.g. "bcb5346fc731". The durable identity - what
    #: `canbus_uuids:` stores.
    uuid: str
    #: Exactly what flashtool printed: "Klipper", "Katapult", or "Unknown".
    application: str
    #: One of `discovery.spec.STATE_*`, via `state_for_firmware(application)`.
    state: str
    #: Which interface answered, for *this scan's own display only* - Linux
    #: CAN interface names are enumeration order, not stable identity, so this
    #: is never persisted and never trusted on a later scan. See
    #: `discovery.spec`'s identity-vs-state split for the general rule this
    #: follows: `uuid` is identity, `interface` is not part of it.
    interface: str


def list_can_interfaces(paths: Paths) -> list[str]:
    """Every real CAN network interface currently present on this host.

    A network device under ``<sysfs>/<name>/`` counts if its own `type` file
    reads `280` (ARPHRD_CAN) - read from the kernel, not assumed from a name
    like `can0`. `paths.can_sysfs_net` is the same kind of override
    `paths.bootsel_root` is for BOOTSEL: empty means search the real
    `/sys/class/net`, and a test pointing it at a tmp_path searches there
    instead.
    """
    root = paths.can_sysfs_net or _DEFAULT_SYS_CLASS_NET
    if not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        type_file = os.path.join(root, name, "type")
        try:
            with open(type_file, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value == ARPHRD_CAN:
            found.append(name)
    return found


def parse_query_output(transcript: list[str], interface: str) -> list[CanSighting]:
    """Every `Detected UUID: ..., Application: ...` line, parsed.

    Split out from `query()` so the parser itself needs no subprocess, no
    filesystem, and no hardware to test - feed it flashtool's own printed
    lines directly.
    """
    out = []
    for line in transcript:
        match = _QUERY_LINE_RE.search(line)
        if match is None:
            continue
        uuid, application = match.group(1).lower(), match.group(2)
        out.append(
            CanSighting(
                uuid=uuid,
                application=application,
                # Klipper -> STATE_KLIPPER, Katapult -> STATE_KATAPULT,
                # "Unknown" -> STATE_KLIPPER too, by the same "anything else
                # means an application is running" rule `discovery.spec`
                # documents - a CAN node flashtool cannot name is still an
                # application it heard answer, not an absence.
                state=state_for_firmware(application),
                interface=interface,
            )
        )
    return out


def query(
    paths: Paths,
    settings: Settings,
    interface: str,
    *,
    reporter: Reporter = null_reporter,
) -> list[CanSighting]:
    """Every unclaimed board answering on one CAN interface right now.

    Shells out to ``flashtool.py -i <interface> --query`` via the same
    `run_streamed` subprocess pattern `flashers/flash.py` uses. Does not
    raise when the query simply finds nothing, or when flashtool's own
    "CANBus UUID Query Complete" sentinel never shows up in its output -
    those read as "no unclaimed boards on this interface right now", not as a
    failure of this call. It *does* raise `FileNotFoundError` if flashtool.py
    itself is missing, the same hard dependency `flash_katapult` refuses to
    proceed without.
    """
    flashtool = find_flashtool(paths, settings)
    if not os.path.exists(flashtool):
        raise FileNotFoundError(
            f"flashtool.py not found at {flashtool}. Is katapult installed?"
        )

    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    run_streamed(
        [sys.executable, flashtool, "-i", interface, "--query"],
        cwd=paths.home,
        reporter=capture,
        dry_run=settings.dry_run,
        fake_delay=0.0,
    )
    return parse_query_output(transcript, interface)


def scan_all(
    paths: Paths,
    settings: Settings,
    *,
    reporter: Reporter = null_reporter,
) -> tuple[list[str], list[CanSighting]]:
    """Sweep every discovered interface and merge the results.

    What the `fw.canbus.scan` agent method calls. `interfaces` is returned
    alongside the sightings even when it is empty, so a caller can tell "no
    CAN hardware on this host" apart from "CAN hardware present, nothing
    unclaimed answered".
    """
    interfaces = list_can_interfaces(paths)
    sightings: list[CanSighting] = []
    for interface in interfaces:
        sightings.extend(query(paths, settings, interface, reporter=reporter))
    return interfaces, sightings


__all__ = [
    "ARPHRD_CAN",
    "QUERY_COMPLETE_RE",
    "CanSighting",
    "list_can_interfaces",
    "parse_query_output",
    "query",
    "scan_all",
]
