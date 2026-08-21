"""Which discovery sources exist.

**Static, and not discovered** - for the same reason `flashers.registry` is.
This process holds the exclusive lock and writes firmware to boards; importing
whatever `.py` landed in a directory is privilege escalation, not a plugin
system. The tuple is the seam.

Empty for now. The three bus sources (`byid.py`, `dfu.py`, `bootsel.py`) and
the two knomi sources (`listen.py`, `watcher.py`) all now live in this
package, but none of them implement `discovery.spec.Source` yet - that wiring,
and `SOURCES` actually gaining entries, is a later step.
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
