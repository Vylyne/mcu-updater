"""The knomi_serial displays: firmware-specific, not host-generic.

`byid.py`, `dfu.py` and `bootsel.py` sit flat beside this package because each
answers a question true of any board: what is on `/dev/serial/by-id`, what
`dfu-util -l` reports, which volume mounted as `RPI-RP2`. Neither the question
nor the answer names a firmware.

These two do. `listen.py`'s discovery snippet does `import knomi_serial as k`
and calls `k.discover_reports()` - it runs *inside* that klippy module's own
source tree. `watcher.py` reads `devices.json`, a file shape that module's own
watcher process writes and owns (`DEVICE_MAP_VERSION`). Both would need a
sibling module, not a shared one, the day a second display firmware exists -
which is what a subpackage named for the firmware makes obvious on sight.
"""

from __future__ import annotations

from .listen import discover as discover
from .listen import source_dir as source_dir
from .watcher import DEVICE_MAP_VERSION as DEVICE_MAP_VERSION
from .watcher import WatcherDevice as WatcherDevice
from .watcher import device_map_path as device_map_path
from .watcher import read_device_map as read_device_map
