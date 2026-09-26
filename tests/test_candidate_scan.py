"""Finding a bare board is the flasher's job: `CandidateScanner`.

The agent's `fw.dfu.scan`/`fw.bootsel.scan` keep their wire shapes
(tests/test_agent_dfu.py and tests/test_agent_bootsel.py pass unmodified);
this file covers what moved and what is new - `port` on every device, and
naming a tracked board of any builder.
"""

from __future__ import annotations

import dataclasses

from mcu_updater import flashers
from mcu_updater.flashers import CandidateScan, TrackedBoard

from .conftest import bootsel_device_node, mounted_bootsel_volume, on_port
from .test_agent_dfu import ONE_BOARD, TWO_BOARDS, patch_dfu


def _quiet(level, message):
    pass


def test_dfu_util_and_bootsel_are_scanners_and_nothing_else_is():
    scanners = {f.name for f in flashers.FLASHERS if flashers.candidate_scanner(f)}
    assert scanners == {"dfu_util", "bootsel"}


def test_port_is_the_one_ready_devices_port():
    ready = CandidateScan(True, None, None, [{"port": "1-1.2"}])
    two = CandidateScan(False, "ambiguous", "two", [{"port": "1-1.2"}, {"port": "1-1.3"}])
    assert ready.port == "1-1.2"
    assert two.port is None


def test_to_json_carries_count_and_extras():
    scan = CandidateScan(False, "none", "nothing", [], {"vid_pid": "0483:df11"})
    assert scan.to_json() == {
        "devices": [],
        "count": 0,
        "ready": False,
        "reason": "none",
        "message": "nothing",
        "vid_pid": "0483:df11",
    }


def test_a_dfu_device_carries_its_port(paths, monkeypatch):
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    scan = flashers.by_name("dfu_util").scan_candidates(paths, tracked=(), reporter=_quiet)

    assert scan.ready
    # dfu-util's `path` is sysfs's own port name.
    assert scan.port == "6-1.6.6.1.3"


def test_two_dfu_boards_are_ambiguous_with_a_port_each(paths, monkeypatch):
    patch_dfu(monkeypatch, stdout=TWO_BOARDS)
    scan = flashers.by_name("dfu_util").scan_candidates(paths, tracked=(), reporter=_quiet)

    assert scan.reason == "ambiguous"
    assert {d["port"] for d in scan.devices} == {"6-1.6.6.1.3", "6-1.6.6.1.4"}
    assert scan.port is None


def test_a_bootsel_device_carries_its_port(paths, fake_root):
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    here = dataclasses.replace(on_port(paths, fake_root, "1-1.2"), bootsel_root=str(root))

    scan = flashers.by_name("bootsel").scan_candidates(here, tracked=(), reporter=_quiet)

    assert scan.ready
    assert scan.port == "1-1.2"


def test_bootsel_names_a_tracked_board_of_any_builder(paths, fake_root):
    """A cmake Roadrunner is tracked in the type list, never in `Registry` -
    which is all the old agent-side identification read."""
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, "E0C9125B0D9B")
    here = dataclasses.replace(paths, bootsel_root=str(root))
    tracked = [TrackedBoard("roadrunner", "E0C9125B0D9B", "rp2040")]

    scan = flashers.by_name("bootsel").scan_candidates(here, tracked=tracked, reporter=_quiet)

    assert scan.devices[0]["tracked_by"] == "roadrunner"
    assert scan.devices[0]["known_serial"] == "E0C9125B0D9B"


def test_one_id_owned_twice_names_neither():
    devices = [{"serial": "X"}]
    flashers.name_tracked(devices, {"X": [("a", "1"), ("b", "2")]}, "serial")
    assert devices[0]["tracked_by"] is None and devices[0]["known_serial"] is None
