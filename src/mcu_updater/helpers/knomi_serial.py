"""KNOMI screens running knomi-serial.

Reads device info through `providers.pio`, the same regex the display's own
staleness check uses - see `test_the_knomi_reader_is_the_platformio_one` for
why the two must not disagree. Identity arrives in Task 10. It is registered
first so a `platformio` family can declare it and have that declaration
checked when the config loads.
"""

from __future__ import annotations

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
        """The watcher's map, and failing that the devices themselves.

        In order of what it costs. `devices.json` is written by the klippy
        module's own watcher process for exactly this moment and answers
        instantly. The listen pass is six seconds of held ports, so it runs
        only when the map has nothing to say and only when the caller has
        said the ports are free.

        The map is a remembered path and the broadcast is the authority -
        knomi_serial's own docs put identity at flash time for that reason,
        and a map is by definition not flash time. But a remembered path
        that is still right is worth more than six seconds, and the write
        itself verifies the port again before it touches anything.

        Asking is best effort: it needs pyserial out of the module's source
        tree, and a host missing it must reach the caller's own "neither
        source could tell" refusal - which names both sources - rather than
        a tool error from the fallback.
        """
        found = read_device_map(paths, entry)
        if found or not ask:
            return found
        reporter("info", f"No device map for '{entry.name}' - asking the devices which they are...")
        try:
            return discover(paths, settings, entry, reporter=reporter)
        except UpdaterError as exc:
            reporter("warn", f"could not ask the devices ({exc})")
            return {}

    def remembered_at(self, paths: Paths, entry: pio.PioType) -> str:
        return device_map_path(paths, entry)


__all__ = ["KnomiSerialHelper"]
