"""The payoff of the Inventory axis: asking every source, once, and merging.

Runs **inside** the Klipper stop, after watchers are paused - the knomi_serial
docs' own ordering, and the only moment identity can be *resolved* rather than
*remembered*. Klipper holds the port; the watcher merely contends for it.

**Confidence is a property of the source, not of any one sighting.** A source
that answered live (`Listen`) always means `ANSWERED`; a source reading
something written earlier (`Watcher`) always means `REMEMBERED`. That mapping
lives here, not on the sources themselves, because it is a policy decision -
how much a caller should trust each kind of evidence - and `Source.sight()`
only reports what it saw, not how much to believe it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..flashers.spec import Bench
from .byid import Byid
from .knomi_serial.listen import Listen
from .knomi_serial.watcher import Watcher
from .spec import ANSWERED, REMEMBERED, UNCONFIRMED, UNIQUE_BUS_ID, Confidence, Sighting, Source

#: Which `Confidence` reason a source's sightings carry. Sources not listed
#: here are `UNCONFIRMED` - "something answered a query", nothing more.
_CONFIDENCE_FOR_SOURCE: dict[str, str] = {
    Listen.name: ANSWERED,
    Watcher.name: REMEMBERED,
    Byid.name: UNIQUE_BUS_ID,
}


def confirm(
    bench: Bench, *, sources: Sequence[Source]
) -> dict[str, tuple[Sighting, Confidence]]:
    """Ask every source, and keep the most-confident sighting per identity.

    Returns every identity any source reported, keyed by `Sighting.id`. A
    caller matching a device it cares about does so by `id`, the same way
    `port_for` matches a screen's `device_id` against what came back.

    When two sources see the same identity - a screen the listen pass heard
    and the watcher also remembers - the more confident sighting wins, so a
    stale remembered port never shadows a live answer.
    """
    best: dict[str, tuple[Sighting, Confidence]] = {}
    for source in sources:
        reason = _CONFIDENCE_FOR_SOURCE.get(source.name, UNCONFIRMED)
        confidence = Confidence(reason)
        for sighting in source.sight(bench):
            if not sighting.id:
                continue
            current = best.get(sighting.id)
            if current is not None and _rank(current[1]) >= _rank(confidence):
                continue
            best[sighting.id] = (sighting, confidence)
    return best


def _rank(confidence: Confidence) -> int:
    """Higher is more trustworthy. Ties keep whichever sighting arrived first,
    since `confirm` iterates `sources` in the order the caller cares about."""
    return {True: 2, None: 1}.get(confidence.safe_to_write, 0)


__all__ = ["confirm"]
