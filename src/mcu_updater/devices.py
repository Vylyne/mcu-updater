"""Thin re-export shim.

The three bus sources - by-id scan, DFU, BOOTSEL - moved to
:mod:`mcu_updater.discovery` (`byid.py`, `dfu.py`, `bootsel.py`). Every name
below is re-exported here unchanged so no call site needed to change; new code
should import from `discovery` directly.

`subprocess` is imported (not just used internally by `discovery.dfu`) so
`monkeypatch.setattr(devices.subprocess, "run", ...)` keeps working: it is the
same module object either way, so patching it here patches it everywhere.
"""

from __future__ import annotations

import subprocess as subprocess

from .discovery.bootsel import BOOTSEL_VOLUME_NAME as BOOTSEL_VOLUME_NAME
from .discovery.bootsel import DEFAULT_BOOTSEL_ROOT_GLOBS as DEFAULT_BOOTSEL_ROOT_GLOBS
from .discovery.bootsel import bootsel_devices as bootsel_devices
from .discovery.bootsel import bootsel_id_for as bootsel_id_for
from .discovery.bootsel import bootsel_scan as bootsel_scan
from .discovery.byid import KATAPULT_FW_NAME as KATAPULT_FW_NAME
from .discovery.byid import KATAPULT_NAMES as KATAPULT_NAMES
from .discovery.byid import KLIPPER_FW_NAME as KLIPPER_FW_NAME
from .discovery.byid import KLIPPER_NAMES as KLIPPER_NAMES
from .discovery.byid import STATE_BOOTSEL as STATE_BOOTSEL
from .discovery.byid import STATE_DFU as STATE_DFU
from .discovery.byid import STATE_ESP_ROM as STATE_ESP_ROM
from .discovery.byid import STATE_KATAPULT as STATE_KATAPULT
from .discovery.byid import STATE_KLIPPER as STATE_KLIPPER
from .discovery.byid import STATE_OFFLINE as STATE_OFFLINE
from .discovery.byid import BusDevice as BusDevice
from .discovery.byid import device_state as device_state
from .discovery.byid import expected_path as expected_path
from .discovery.byid import find_device as find_device
from .discovery.byid import find_untracked as find_untracked
from .discovery.byid import parse_entry as parse_entry
from .discovery.byid import scan as scan
from .discovery.byid import wait_for_device as wait_for_device
from .discovery.byid import wait_for_new_device as wait_for_new_device
from .discovery.dfu import dfu_devices as dfu_devices
from .discovery.dfu import dfu_selector as dfu_selector
from .discovery.dfu import dfu_serial_for as dfu_serial_for
