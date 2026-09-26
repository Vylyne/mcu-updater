"""KNOMI screens running knomi-serial.

Reads device info through `providers.pio`, the same regex the display's own
staleness check uses - see `test_the_knomi_reader_is_the_platformio_one` for
why the two must not disagree. Identity arrives in Task 10. It is registered
first so a `platformio` family can declare it and have that declaration
checked when the config loads.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..discovery.knomi_serial import device_map_path, discover, read_device_map
from ..errors import UpdaterError
from ..providers import pio

if TYPE_CHECKING:
    from ..build import Reporter
    from ..discovery.knomi_serial import WatcherDevice
    from ..paths import Paths
    from ..settings import Settings


class KnomiSerialHelper:
    """Identity and firmware access for a BTT KNOMI v2 screen.

    Named by a `[firmware ...]` section's `helper:`. The KNOMI needs one
    because the CH340K in front of it reports no USB serial, so every unit
    enumerates identically and the only stable name it has is one its own
    firmware will state if asked. That is a property of this hardware, not
    of `builder: platformio` - a PlatformIO board with a real serial is an
    ordinary by-id device and names no helper at all.
    """

    name: str = "knomi_serial"
    klipper_prefix: str = "knomi_serial"

    def running_sha(self, version: str | None) -> str | None:
        return pio.running_sha(version)

    def is_dirty(self, version: str | None) -> bool:
        return pio.is_dirty(version)

    def identify(
        self,
        paths: Paths,
        settings: Settings,
        entry: pio.PioType,
        *,
        ask: bool,
        reporter: Reporter,
    ) -> dict[str, WatcherDevice]:
        """What is remembered, or what the devices say now.

        `ask=False` is the watcher's map and nothing else. `devices.json` is
        written by knomi_serial's own watcher for the ports Klipper does not
        hold, answers instantly, and costs no port - which is what a status
        poll, and a CLI choosing what to flash, want.

        `ask=True` is a caller saying the ports are free and it wants to be
        sure: the listen pass, six seconds of held ports, and the only answer
        taken at flash time. It is not merged with the map. A device that stays
        silent while others answer is not there, and one the listen never heard
        must not come back with a remembered port dressed as a confirmed one -
        so what was heard is marked `answered`, and nothing else is.

        The map is the fallback only when the listen cannot run at all: it
        needs pyserial out of the module's source tree, and a host missing it
        must reach the caller's own "neither source could tell" refusal - which
        names both sources - rather than a tool error.
        """
        if not ask:
            return read_device_map(paths, entry)
        reporter("info", f"Asking the '{entry.name}' devices which they are...")
        try:
            heard = discover(paths, settings, entry, reporter=reporter)
        except UpdaterError as exc:
            reporter("warn", f"could not ask the devices ({exc}) - using the watcher's map instead")
            return read_device_map(paths, entry)
        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}

    def remembered_at(self, paths: Paths, entry: pio.PioType) -> str:
        return device_map_path(paths, entry)


__all__ = ["KnomiSerialHelper"]
