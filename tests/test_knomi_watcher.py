"""The watcher's device map - `devices.json`, written by something else.

The one source that answers while Klipper is down - which is exactly when
flashing needs it, because esptool wants the port to itself, so Klipper has
to be stopped, and stopping Klipper removes the only other source.

Split out of `test_agent_displays.py` in Step 25, alongside
`read_device_map()`/`device_map_path()`/`WatcherDevice`'s own move to
`discovery/watcher.py`; moved again in Step 25b into `discovery/knomi_serial/`,
the subpackage named for the firmware this module integrates with. The two
tests that exercise `api.device_list` stayed behind - they are agent-level,
not `providers.pio`-level - and are re-tested via `providers.pio`'s re-export
shim rather than `discovery.knomi_serial` directly, matching how `devices.py`'s
shim is tested for the three bus sources.
"""

from __future__ import annotations

import json as _json
import os

import pytest

from mcu_updater.discovery import knomi_serial as watcher
from mcu_updater.providers import pio as pio_mod


def _write_map(paths, payload):
    path = os.path.join(paths.printer_data, "knomi", "devices.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(payload, fh)
    return path


GOOD_MAP = {
    "version": 1,
    "devices": {"19aa44": {"port": "/dev/ttyUSB0", "fw": "0.5.0+54.g5509d4f", "var": "knomi"}},
}


def _display(env="knomi"):
    return pio_mod.PioType(name=env, env=env, source="/nowhere")


def test_an_entry_carries_what_the_screen_reported(paths):
    _write_map(paths, GOOD_MAP)
    devices = watcher.read_device_map(paths, _display())

    assert list(devices) == ["19aa44"]
    entry = devices["19aa44"]
    assert entry.port == "/dev/ttyUSB0"
    assert entry.firmware_version == "0.5.0+54.g5509d4f"
    assert entry.build_variant == "knomi"


def test_ids_are_lowered_so_they_compare(paths):
    _write_map(paths, {"version": 1, "devices": {"19AA44": {"port": "/dev/ttyUSB0"}}})
    assert list(watcher.read_device_map(paths, _display())) == ["19aa44"]


def test_a_version_we_do_not_know_is_ignored_rather_than_guessed_at(paths):
    """The format is somebody else's to change, and a half-understood port is a
    write to the wrong display."""
    _write_map(paths, {"version": 2, "devices": {"19aa44": {"port": "/dev/ttyUSB0"}}})
    assert watcher.read_device_map(paths, _display()) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"devices": {"19aa44": {"port": "/dev/ttyUSB0"}}},  # no version at all
        {"version": 1},  # no devices
        {"version": 1, "devices": []},  # wrong shape
        {"version": 1, "devices": {"19aa44": "not a dict"}},
        {"version": 1, "devices": {"19aa44": {"fw": "0.5.0"}}},  # no port
    ],
)
def test_anything_unusable_is_an_empty_map_not_an_error(paths, payload):
    """Every one of these means "we cannot tell you where these displays are",
    and the caller's answer to that is the same in each case."""
    _write_map(paths, payload)
    assert watcher.read_device_map(paths, _display()) == {}


def test_a_missing_or_corrupt_file_is_not_an_error(paths):
    assert watcher.read_device_map(paths, _display()) == {}

    path = os.path.join(paths.printer_data, "knomi", "devices.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert watcher.read_device_map(paths, _display()) == {}


def test_a_family_can_declare_it_has_no_map(paths):
    _write_map(paths, GOOD_MAP)
    display = pio_mod.PioType(name="x", env="x", source="/nowhere", device_map="")
    assert watcher.device_map_path(paths, display) == ""
    assert watcher.read_device_map(paths, display) == {}


def test_an_absolute_map_path_is_used_as_given(paths, tmp_path):
    elsewhere = tmp_path / "elsewhere.json"
    with open(elsewhere, "w", encoding="utf-8") as fh:
        _json.dump(GOOD_MAP, fh)
    display = pio_mod.PioType(
        name="x", env="x", source="/nowhere", device_map=str(elsewhere)
    )
    assert watcher.read_device_map(paths, display)["19aa44"].port == "/dev/ttyUSB0"


def test_a_port_that_is_gone_proves_the_entry_is_stale(paths, fake_root):
    """No systemd needed: if the node the map names has disappeared, the entry
    is definitively out of date. The converse does not hold - a port that still
    exists may since have become a different display."""
    live = str(fake_root / "ttyUSB0")
    open(live, "w").close()
    _write_map(
        paths,
        {
            "version": 1,
            "devices": {
                "19aa44": {"port": live},
                "19aa45": {"port": str(fake_root / "gone")},
            },
        },
    )
    devices = watcher.read_device_map(paths, _display())

    assert devices["19aa44"].present is True
    assert devices["19aa45"].present is False, "a vanished port is not present"
    assert "19aa45" in devices, "still listed - a display we cannot find is the news"
