"""Which discovery sources exist.

**Static, and not discovered** - for the same reason `flashers.registry` is.
This process holds the exclusive lock and writes firmware to boards; importing
whatever `.py` landed in a directory is privilege escalation, not a plugin
system. The tuple is the seam.

The two knomi sources (`knomi_serial/listen.py`, `knomi_serial/watcher.py`)
implement `discovery.spec.Source` as of Step 26. `byid.py` does too, as of
Step 27, alongside `esptool.port_for`'s board-side counterpart in
`flash_katapult`. `dfu.py`/`bootsel.py` still do not - nothing needs them yet;
they back `flash_initial_bootloader`'s first-time-flash path, which computes
its own state rather than consulting `confirm()`.
"""

from __future__ import annotations

from .byid import Byid
from .knomi_serial.listen import Listen
from .knomi_serial.watcher import Watcher
from .roadrunner import Roadrunner
from .spec import Source

#: Every discovery source. Order matters for `confirm()`'s tie-breaking: the
#: live answer is listed before the remembered one. `Byid` is last - its
#: `UNIQUE_BUS_ID` sightings rank equal to `Listen`'s `ANSWERED` ones, and a
#: tie keeps whichever the loop saw first, so a knomi sighting is never
#: displaced by a board one (their `Sighting.id`s - by-id serial vs. knomi
#: device id - would not collide in practice, but the ordering costs nothing).
SOURCES: tuple[Source, ...] = (Listen(), Watcher(), Roadrunner(), Byid())

_BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}


def by_name(name: str) -> Source:
    source = _BY_NAME.get(name)
    if source is None:
        raise KeyError(f"no discovery source {name!r}; known: {sorted(_BY_NAME)}")
    return source


__all__ = ["SOURCES", "by_name"]
