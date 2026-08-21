"""The Inventory axis: where a device is, and how sure we are.

See `discovery.spec` for the vocabulary and `discovery.registry` for the
source list.

Two tiers of source live here. `byid.py`, `dfu.py` and `bootsel.py` are
generic host scans - each answers a question true of any board, and stays
flat beside this file. `knomi_serial/` is a firmware integration: it imports
`knomi_serial`'s own klippy module and reads a file shape that module's
watcher owns, so it gets its own subpackage named for the firmware it talks
to. A second display firmware would get a sibling subpackage, not a new file
here.
"""

from __future__ import annotations
