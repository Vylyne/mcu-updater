"""The watcher's device map - `devices.json`, written by something else.

The one source that answers while Klipper is *down*, which is precisely when
flashing needs it: esptool wants the port to itself, so Klipper has to be
stopped, and stopping Klipper is what removes the only other source that could
answer instantly.

**`DEVICE_MAP_VERSION` and the file's shape are somebody else's contract.**
Read it; never write a new one. A file announcing a version this does not
understand is ignored rather than guessed at - a half-understood port is a
write to the wrong display.
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...paths import Paths
    from ...providers.pio import PioType

#: The only schema this understands. A file announcing anything else is ignored
#: rather than guessed at - the format is somebody else's to change, and a
#: half-understood port is a write to the wrong display.
DEVICE_MAP_VERSION = 1


@dataclasses.dataclass(frozen=True)
class WatcherDevice:
    """One display the watcher has identified during its current run."""

    device_id: str
    port: str
    firmware_version: str | None = None
    build_variant: str | None = None
    #: Does the port still exist? A gone node proves the entry is stale without
    #: asking systemd anything. The converse does not hold - a port that exists
    #: may since have become a *different* display, which is the whole reason
    #: these are keyed by an id burned into the chip rather than by path.
    present: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "port": self.port,
            "firmware_version": self.firmware_version,
            "build_variant": self.build_variant,
            "present": self.present,
        }


def device_map_path(paths: Paths, display: PioType) -> str:
    """Where this family's watcher writes its map. Empty if it has none."""
    configured = display.device_map.strip()
    if not configured:
        return ""
    expanded = os.path.expanduser(configured)
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(paths.printer_data, expanded)


def read_device_map(paths: Paths, display: PioType) -> dict[str, WatcherDevice]:
    """Parse the watcher's id -> port map.

    **This says nothing about whether the file is current.** There are
    deliberately no timestamps in it: an entry existing means the display was
    identified during the watcher's current run and its port has not
    disappeared since - which is only true while the watcher is *running*.
    Callers must check the service first; nothing here can.

    Unreadable, unparseable, wrong version, or wrong shape all mean an empty
    map rather than an error. Every one of them is "we cannot tell you where
    these displays are", and the caller's answer to that is the same in each
    case.
    """
    path = device_map_path(paths, display)
    if not path:
        return {}

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != DEVICE_MAP_VERSION:
        return {}

    devices = data.get("devices")
    if not isinstance(devices, dict):
        return {}

    out: dict[str, WatcherDevice] = {}
    for raw_id, entry in devices.items():
        if not isinstance(entry, dict):
            continue
        port = entry.get("port")
        if not raw_id or not port:
            # An id with no port names a display we cannot reach, which is the
            # same as not knowing about it - and a WatcherDevice whose whole
            # purpose is its port would be a lie.
            continue
        # Lowered because ids are compared case-insensitively; the vendor emits
        # lowercase but their docs say not to depend on it.
        device_id = str(raw_id).lower()
        out[device_id] = WatcherDevice(
            device_id=device_id,
            port=str(port),
            firmware_version=entry.get("fw"),
            build_variant=entry.get("var"),
            present=os.path.exists(str(port)),
        )
    return out
