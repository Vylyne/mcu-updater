"""KNOMI screens running knomi-serial.

Reads device info through `providers.pio`, the same regex the display's own
staleness check uses - see `test_the_knomi_reader_is_the_platformio_one` for
why the two must not disagree. Identity arrives in Task 10. It is registered
first so a `platformio` family can declare it and have that declaration
checked when the config loads.
"""

from __future__ import annotations

from ..providers import pio


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


__all__ = ["KnomiSerialHelper"]
