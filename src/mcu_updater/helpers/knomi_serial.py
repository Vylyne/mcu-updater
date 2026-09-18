"""KNOMI screens running knomi-serial.

Only a name for now. Its capabilities arrive with the handlers that use them:
device info in Task 2 of the one-pipeline plan, identity in Task 10. It is
registered first so a `platformio` family can declare it and have that
declaration checked when the config loads.
"""

from __future__ import annotations


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


__all__ = ["KnomiSerialHelper"]
