"""flash_katapult_can / Flashtool: CAN-addressed flashing.

Mirrors test_flash.py's approach for the by-id path - every subprocess call
is faked via `run_streamed`, and CAN interfaces are faked via
`paths.can_sysfs_net` exactly as test_discovery_canbus.py already does. No
real katapult checkout and no CAN hardware is required to run in CI, and none
is available on this bench either way.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from mcu_updater import flashers
from mcu_updater.discovery.canbus import ARPHRD_CAN
from mcu_updater.errors import (
    DeviceNotFoundError,
    FlashError,
    OffsetMismatchError,
    ToolMissingError,
)
from mcu_updater.flashers import flash as flash_mod
from mcu_updater.flashers.flash import flash_katapult_can

UUID = "bcb5346fc731"


def _stage_bin(paths, mcu_type: str = "board") -> None:
    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    with open(paths.bin_file(mcu_type, "klipper"), "wb") as fh:
        fh.write(b"\0" * 16)


def _write_sidecar(paths, mcu_type: str, fw: str, **fields) -> None:
    with open(paths.sidecar_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


def _with_interfaces(paths, fake_root, names):
    net_root = fake_root / "sys_class_net"
    for name in names:
        d = net_root / name
        d.mkdir(parents=True)
        (d / "type").write_text(f"{ARPHRD_CAN}\n", encoding="utf-8")
    return dataclasses.replace(paths, can_sysfs_net=str(net_root))


@pytest.fixture
def ready(paths, settings, fake_root):
    """A staged firmware binary, an installed flashtool.py, and a real write
    (not dry-run) - the interface trial loop only matters once dry_run is off."""
    settings.dry_run = False
    (fake_root / "katapult" / "scripts").mkdir(parents=True, exist_ok=True)
    (fake_root / "katapult" / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    _stage_bin(paths)
    return settings


def _script_run_streamed(monkeypatch, script: dict):
    """`script` maps `(interface, "probe"|"write") -> (rc, lines)`. Anything
    not named defaults to `(1, [])` - a timeout/non-answer on that interface
    for that stage, which is exactly the "wrong bus, try the next one" case
    the interface-trial loop exists to handle."""
    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        interface = cmd[cmd.index("-i") + 1]
        stage = "probe" if "-s" in cmd else "write"
        rc, lines = script.get((interface, stage), (1, []))
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return rc

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    return calls


# --------------------------------------------------------------------------
# up-front refusals
# --------------------------------------------------------------------------


def test_missing_flashtool_raises(paths, settings, fake_root):
    _stage_bin(paths)
    with pytest.raises(ToolMissingError):
        flash_katapult_can(paths, settings, "board", UUID)


def test_missing_firmware_binary_raises(paths, settings, fake_root):
    (fake_root / "katapult" / "scripts").mkdir(parents=True)
    (fake_root / "katapult" / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    with pytest.raises(FlashError):
        flash_katapult_can(paths, settings, "board", UUID)


def test_no_can_interfaces_raises_device_not_found(paths, ready):
    with pytest.raises(DeviceNotFoundError) as exc:
        flash_katapult_can(paths, ready, "board", UUID)
    assert exc.value.data["uuid"] == UUID


# --------------------------------------------------------------------------
# the interface-trial loop - no sidecar app_address, so the probe is skipped
# entirely and the real -f write itself is the whole of interface discovery.
# --------------------------------------------------------------------------


def test_succeeds_on_the_second_interface_after_the_first_times_out(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    calls = _script_run_streamed(monkeypatch, {("can1", "write"): (0, [])})

    events: list[tuple[str, str]] = []
    flash_katapult_can(
        ready_paths, ready, "board", UUID, reporter=lambda s, line: events.append((s, line))
    )

    interfaces_tried = [c[c.index("-i") + 1] for c in calls]
    assert interfaces_tried == ["can0", "can1"]
    assert any("Flashed" in line and "successfully" in line for _, line in events)


def test_fails_when_every_interface_fails(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    _script_run_streamed(monkeypatch, {})  # nothing answers anywhere

    with pytest.raises(FlashError) as exc:
        flash_katapult_can(ready_paths, ready, "board", UUID)
    assert exc.value.data["uuid"] == UUID


def test_uses_dash_i_dash_u_instead_of_dash_d(paths, ready, fake_root, monkeypatch):
    """The whole point of the module: `-d <path>` becomes `-i <iface> -u <uuid>`."""
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    calls = _script_run_streamed(monkeypatch, {("can0", "write"): (0, [])})

    flash_katapult_can(ready_paths, ready, "board", UUID)

    assert "-d" not in calls[0]
    assert calls[0][calls[0].index("-i") + 1] == "can0"
    assert calls[0][calls[0].index("-u") + 1] == UUID


def test_a_configured_interface_is_used_without_trying_other_buses(
    paths, ready, fake_root, monkeypatch
):
    """A printer.cfg mapping is authoritative for this write, so a failed
    configured bus must not fall through and flash the same UUID elsewhere."""
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    calls = _script_run_streamed(monkeypatch, {("can1", "write"): (0, [])})

    flash_katapult_can(ready_paths, ready, "board", UUID, interface="can1")

    assert [call[call.index("-i") + 1] for call in calls] == ["can1"]


# --------------------------------------------------------------------------
# offset guard: native node - the -s probe doubles as interface discovery and
# the pre-write guard, in one step, exactly as flash.py's own docstring says.
# --------------------------------------------------------------------------


def test_native_node_probe_mismatch_refuses_before_writing(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(
        monkeypatch, {("can0", "probe"): (0, ["Application Start: 0x8000"])}
    )

    with pytest.raises(OffsetMismatchError) as exc:
        flash_katapult_can(ready_paths, ready, "board", UUID)

    assert "0x8004000" in str(exc.value) and "0x8000" in str(exc.value)
    # The probe ran; the write never did.
    assert len(calls) == 1
    assert "-s" in calls[0]


def test_native_node_agreeing_addresses_proceed_to_write(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(
        monkeypatch,
        {
            ("can0", "probe"): (0, ["Application Start: 0x8004000"]),
            ("can0", "write"): (0, ["Application Start: 0x8004000"]),
        },
    )

    events: list[tuple[str, str]] = []
    flash_katapult_can(
        ready_paths, ready, "board", UUID, reporter=lambda s, line: events.append((s, line))
    )

    assert not [line for stream, line in events if stream in ("error", "warn")]
    assert len(calls) == 2  # probe, then the write - same interface both times


def test_native_node_force_downgrades_refusal_to_warning_and_still_writes(
    paths, ready, fake_root, monkeypatch
):
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _script_run_streamed(
        monkeypatch,
        {
            ("can0", "probe"): (0, ["Application Start: 0x8000"]),
            ("can0", "write"): (0, ["Application Start: 0x8000"]),
        },
    )

    events: list[tuple[str, str]] = []
    flash_katapult_can(
        ready_paths,
        ready,
        "board",
        UUID,
        reporter=lambda s, line: events.append((s, line)),
        force=True,
    )

    warnings = [line for stream, line in events if stream == "warn"]
    assert any("0x8004000" in line and "0x8000" in line for line in warnings)
    assert any("Flashed" in line and "successfully" in line for _, line in events)


def test_probe_tries_each_interface_before_falling_through(paths, ready, fake_root, monkeypatch):
    """A native node whose probe times out on the first interface, but
    answers on the second - the probe loop is itself the interface trial."""
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(
        monkeypatch,
        {
            ("can1", "probe"): (0, ["Application Start: 0x8004000"]),
            ("can1", "write"): (0, ["Application Start: 0x8004000"]),
        },
    )

    flash_katapult_can(ready_paths, ready, "board", UUID)

    interfaces_tried = [c[c.index("-i") + 1] for c in calls]
    # can0 probe (fails), can1 probe (answers), then can1 write directly -
    # the confirmed interface, never retried against can0.
    assert interfaces_tried == ["can0", "can1", "can1"]


# --------------------------------------------------------------------------
# offset guard: bridge target - a bare -s probe cannot reach a bridge's own
# bootloader handshake before the jump, so this is skipped entirely and the
# real write is the whole of both interface discovery and the safety check.
# --------------------------------------------------------------------------


def test_a_known_bridge_skips_the_probe_entirely(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(monkeypatch, {("can0", "write"): (0, ["Application Start: 0x9000"])})

    events: list[tuple[str, str]] = []
    flash_katapult_can(
        ready_paths,
        ready,
        "board",
        UUID,
        reporter=lambda s, line: events.append((s, line)),
        bridge=True,
    )

    # No -s anywhere: the probe never ran for a known bridge.
    assert all("-s" not in c for c in calls)
    assert len(calls) == 1
    # The post-write mismatch check still caught the real disagreement -
    # the one safety net a bridge target keeps.
    errors = [line for stream, line in events if stream == "error"]
    assert any("0x8004000" in line and "0x9000" in line for line in errors)


def test_an_unanswered_probe_falls_through_to_the_write_loop(paths, ready, fake_root, monkeypatch):
    """`bridge` unknown (None), and the probe answers nowhere - exactly what a
    bridge's own uuid looks like to a bare -s probe. The real write across
    every interface is what actually finds it."""
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(monkeypatch, {("can1", "write"): (0, [])})

    flash_katapult_can(ready_paths, ready, "board", UUID)

    interfaces_tried = [c[c.index("-i") + 1] for c in calls]
    # Probed can0 and can1 (both silent), then wrote can0 (silent) and can1
    # (answered) - the full interface list tried again for the write.
    assert interfaces_tried == ["can0", "can1", "can0", "can1"]
    assert all("-s" in calls[i] for i in (0, 1))
    assert all("-s" not in calls[i] for i in (2, 3))


def test_a_known_native_node_never_writes_after_every_probe_fails(
    paths, ready, fake_root, monkeypatch
):
    ready_paths = _with_interfaces(paths, fake_root, ["can0", "can1"])
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _script_run_streamed(monkeypatch, {("can1", "write"): (0, [])})

    with pytest.raises(FlashError, match="Not attempting to write"):
        flash_katapult_can(ready_paths, ready, "board", UUID, bridge=False)

    assert [call[call.index("-i") + 1] for call in calls] == ["can0", "can1"]
    assert all("-s" in call for call in calls)


# --------------------------------------------------------------------------
# the flash log
# --------------------------------------------------------------------------


def test_a_real_flash_records_canbus_uuid_confidence(paths, ready, fake_root, monkeypatch):
    from mcu_updater.build import FlashLog

    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _script_run_streamed(monkeypatch, {("can0", "write"): (0, [])})

    flash_katapult_can(ready_paths, ready, "board", UUID)

    record = FlashLog(paths).all()[UUID]
    assert record["confidence"] == "canbus_uuid"


# --------------------------------------------------------------------------
# Flashtool / CAN target construction
# --------------------------------------------------------------------------


def test_target_for_builds_a_target_keyed_on_the_uuid():
    target = flashers.flashtool.target_for(
        {"type": "board", "uuid": UUID, "chipset": "stm32g431xx", "fw": "klipper"},
        stop_services=("klipper",),
    )
    assert target.flasher == "flashtool"
    assert target.type == "board"
    assert target.id == UUID
    assert target.stop_services == ("klipper",)


def test_flashtool_writes_a_can_target_and_returns_its_uuid(paths, ready, fake_root, monkeypatch):
    ready_paths = _with_interfaces(paths, fake_root, ["can0"])
    _script_run_streamed(monkeypatch, {("can0", "write"): (0, [])})

    bench = flashers.Bench(paths=ready_paths, settings=ready, controller=lambda name=None: None)
    target = flashers.flashtool.target_for(
        {"type": "board", "uuid": UUID, "chipset": "stm32g431xx", "fw": "klipper"}
    )
    result = flashers.Flashtool().write(
        bench, None, target, flashers.PlainContext(lambda *a: None)
    )
    assert result == {"uuid": UUID}


def test_flashtool_settles_a_can_target_as_a_harmless_no_op(paths, settings):
    bench = flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)
    target = flashers.flashtool.target_for(
        {"type": "board", "uuid": UUID, "chipset": "stm32g431xx", "fw": "klipper"}
    )
    # Must not raise - Flasher.settled's own contract.
    flashers.Flashtool().settled(bench, target, flashers.PlainContext(lambda *a: None))
