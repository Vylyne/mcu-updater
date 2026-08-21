"""Which discovery sources exist.

**Static, and not discovered** - for the same reason `flashers.registry` is.
This process holds the exclusive lock and writes firmware to boards; importing
whatever `.py` landed in a directory is privilege escalation, not a plugin
system. The tuple is the seam.

Empty for now. The three bus sources (`devices.py`'s by-id scan, DFU query and
BOOTSEL mount) and the two knomi sources (`providers/pio.py`'s listen pass and
watcher map) move behind `discovery.spec.Source` in later steps; nothing is
registered until they do.
"""

from __future__ import annotations

from .spec import Source

#: Every discovery source. Order is not meaningful yet - it will become
#: "which source answers first" once more than one exists.
SOURCES: tuple[Source, ...] = ()

_BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}


def by_name(name: str) -> Source:
    source = _BY_NAME.get(name)
    if source is None:
        raise KeyError(f"no discovery source {name!r}; known: {sorted(_BY_NAME)}")
    return source


__all__ = ["SOURCES", "by_name"]
