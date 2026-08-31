"""fw.flash {uuid} and the CAN half of flash_all/update_all's selection.

Mirrors test_agent_flash.py's refusal-ordering tests and test_agent_bulk.py's
selection tests, for the CAN identity form. Every subprocess is faked - no
real katapult checkout and no CAN hardware required to run in CI.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.config import Registry
from mcu_updater.discovery.canbus import ARPHRD_CAN
from mcu_updater.flashers import flash as flash_mod
from mcu_updater.jobs import JobRunner

from .conftest import write_settings

TYPE = "hexadistrofusion"
CHIPSET = "stm32f072xb"
UUID = "bcb5346fc731"


def _write_settings(paths, **extra) -> None:
    write_settings(paths, dry_run="true", service_backend="null", **extra)


def _stage_artifact(paths, mcu_type=TYPE) -> str:
    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    path = paths.bin_file(mcu_type, "klipper")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 1024)
    return path


def _make_flashtool(paths) -> None:
    os.makedirs(os.path.dirname(paths.flashtool), exist_ok=True)
    with open(paths.flashtool, "w", encoding="utf-8") as fh:
        fh.write("# fake flashtool.py, never actually executed\n")


def _with_can_interface(paths, fake_root, name="can0"):
    net_root = fake_root / "sys_class_net"
    d = net_root / name
    d.mkdir(parents=True)
    (d / "type").write_text(f"{ARPHRD_CAN}\n", encoding="utf-8")
    return dataclasses.replace(paths, can_sysfs_net=str(net_root))


def _track_uuid(paths, mcu_type=TYPE, uuid=UUID) -> None:
    with Registry.mutate(paths, f"track {uuid}") as reg:
        reg.add_canbus_uuid(mcu_type, uuid)


def _moonraker(print_state="standby", idle_state="Ready", klippy="ready"):
    def call(method, params, timeout):
        if method == "printer.objects.query":
            return {
                "status": {
                    "print_stats": {"state": print_state},
                    "idle_timeout": {"state": idle_state},
                }
            }
        if method == "printer.info":
            return {"state": klippy, "state_message": f"klippy is {klippy}"}
        if method == "printer.firmware_restart":
            return "ok"
        if method == "machine.system_info":
            return {"system_info": {"service_state": {"klipper": {"active_state": "active"}}}}
        return {}

    return call


@pytest.fixture
def can_flashable(paths, live_registry_text, fake_root):
    """Everything in place for a successful CAN flash, with flashing enabled."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _write_settings(paths, enable_flashing="true")
    _stage_artifact(paths)
    _make_flashtool(paths)
    _track_uuid(paths)

    can_paths = _with_can_interface(paths, fake_root)

    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))
    api = Api(can_paths, runner=runner, call=_moonraker())
    api.KLIPPY_READY_TIMEOUT = 2.0
    api.KLIPPY_RESTART_TIMEOUT = 2.0
    api.KLIPPY_POLL_INTERVAL = 0.05
    yield api
    runner._cancel.set()
    runner.wait(timeout=20)


# --------------------------------------------------------------------------
# fw.flash {uuid}: refusal ordering
# --------------------------------------------------------------------------


def test_an_untracked_uuid_is_rejected(can_flashable):
    with pytest.raises(RpcError) as exc:
        can_flashable.dispatch("fw.flash", {"uuid": "deadbeef0000"})
    assert exc.value.data["code"] == "unknown_uuid"


def test_a_uuid_belonging_to_another_type_is_refused_outright(can_flashable):
    with pytest.raises(RpcError) as exc:
        can_flashable.dispatch("fw.flash", {"uuid": UUID, "name": "OctopusMAXEZ"})
    assert exc.value.data["code"] == "uuid_tracked_elsewhere"


def test_flashing_without_a_built_artifact_is_refused(can_flashable, paths):
    os.unlink(paths.bin_file(TYPE, "klipper"))
    with pytest.raises(RpcError) as exc:
        can_flashable.dispatch("fw.flash", {"uuid": UUID})
    assert exc.value.data["code"] == "no_artifact"
    assert can_flashable.runner.current() is None


def test_flashing_with_no_can_interface_is_refused_before_klipper_is_stopped(
    paths, live_registry_text, fake_root
):
    """No CAN hardware on this host at all is the one thing this can check
    synchronously - unlike a by-id board, "is this uuid actually there" is
    the flash attempt itself, not a check that can run up front."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _write_settings(paths, enable_flashing="true")
    _stage_artifact(paths)
    _make_flashtool(paths)
    _track_uuid(paths)  # no CAN interface faked at all

    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))
    api = Api(paths, runner=runner, call=_moonraker())
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash", {"uuid": UUID})
    assert exc.value.data["code"] == "device_not_found"
    assert runner.current() is None
    runner._cancel.set()
    runner.wait(timeout=20)


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_flashing_during_a_print_is_refused(can_flashable, state):
    can_flashable._call = _moonraker(print_state=state)
    with pytest.raises(RpcError) as exc:
        can_flashable.dispatch("fw.flash", {"uuid": UUID})
    assert exc.value.data["code"] == "print_in_progress"
    assert can_flashable.runner.current() is None


def test_a_successful_can_flash_completes_the_job(can_flashable, monkeypatch):
    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)

    res = can_flashable.dispatch("fw.flash", {"uuid": UUID})
    assert res["job_id"]
    assert can_flashable.runner.wait(timeout=30)
    job = can_flashable.runner.get(res["job_id"])
    assert job.result["uuid"] == UUID
    assert job.result["type"] == TYPE


def test_mapped_uuid_defaults_to_can0_without_falling_back(can_flashable, monkeypatch):
    """An adopted UUID with no explicit canbus_interface is Klipper's can0,
    even when another current CAN interface exists."""
    can1 = os.path.join(can_flashable.paths.can_sysfs_net, "can1")
    os.makedirs(can1)
    with open(os.path.join(can1, "type"), "w", encoding="utf-8") as fh:
        fh.write(f"{ARPHRD_CAN}\n")

    activity_call = _moonraker()
    mapping_call = _moonraker_canbus()

    def call(method, params, timeout):
        requested = ((params or {}).get("objects") or {}) if isinstance(params, dict) else {}
        if method == "printer.objects.list" or (
            method == "printer.objects.query" and "configfile" in requested
        ):
            return mapping_call(method, params, timeout)
        return activity_call(method, params, timeout)

    can_flashable._call = call
    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)

    res = can_flashable.dispatch("fw.flash", {"uuid": UUID})
    assert can_flashable.runner.wait(timeout=30)
    assert can_flashable.runner.get(res["job_id"]).result["uuid"] == UUID
    can_commands = [cmd for cmd in calls if "-i" in cmd]
    assert can_commands
    assert {cmd[cmd.index("-i") + 1] for cmd in can_commands} == {"can0"}


def test_a_known_bridge_from_canbus_info_skips_the_probe(can_flashable, monkeypatch):
    """`fw.flash {uuid}` is the one path where `mcu_constants.CANBUS_BRIDGE`
    is available at call time (already talking to Moonraker for the idle
    gate) - it must reach `Flashtool`/`flash_katapult_can`, not just the
    bulk `flash_all` path. A sidecar `app_address` is staged so the probe
    would fire (and disagree, since nothing here matches it) if `bridge`
    were not actually threaded through target.detail."""
    import json

    # dry_run gates the probe out entirely regardless of `bridge` - turn it
    # off so this test actually exercises the branch it means to check.
    write_settings(can_flashable.paths, dry_run="false")
    with open(can_flashable.paths.sidecar_file(TYPE, "klipper"), "w", encoding="utf-8") as fh:
        json.dump({"app_address": 0x08004000}, fh)
    monkeypatch.setattr(
        can_flashable, "canbus_info", lambda: {UUID: {"mcu": "mcu hexa", "version": "v1", "bridge": True}}
    )

    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)

    res = can_flashable.dispatch("fw.flash", {"uuid": UUID})
    assert can_flashable.runner.wait(timeout=30)
    job = can_flashable.runner.get(res["job_id"])
    assert job.result["uuid"] == UUID
    # No -s anywhere: a known bridge skips the pre-flight probe entirely.
    assert all("-s" not in c for c in calls)


# --------------------------------------------------------------------------
# flash_all / update_all: the two-tier CAN liveness check
# --------------------------------------------------------------------------


def _moonraker_canbus(
    mcu_object="mcu hexa", *, declared=True, version=None, bridge=False, interface=None
):
    """`configfile.settings` cross-reference, faked. `declared=False` means the
    uuid never appears under any `[mcu ...]` section - the fallback tier's
    trigger. `version=None` with `declared=True` means it is declared but not
    connected - the offline-exclusion case."""

    def call(method, params, timeout):
        if method == "printer.objects.list":
            return {"objects": ["configfile", mcu_object]}
        if method == "printer.objects.query":
            requested = (params or {}).get("objects") or {}
            status: dict = {}
            if "configfile" in requested:
                settings = {}
                if declared:
                    settings[mcu_object.lower()] = {
                        "canbus_uuid": UUID,
                        **({"canbus_interface": interface} if interface else {}),
                    }
                status["configfile"] = {"settings": settings}
            if mcu_object in requested:
                entry: dict = {}
                if version is not None:
                    entry["mcu_version"] = version
                entry["mcu_constants"] = {"CANBUS_BRIDGE": 1} if bridge else {}
                status[mcu_object] = entry
            return {"status": status}
        return {}

    return call


def test_canbus_info_reports_the_configfile_cross_reference(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, call=_moonraker_canbus(version="v0.13.0-711-gd7cea5bb", bridge=True))

    info = api.canbus_info()
    assert info[UUID]["mcu"] == "mcu hexa"
    assert info[UUID]["version"] == "v0.13.0-711-gd7cea5bb"
    assert info[UUID]["bridge"] is True
    assert info[UUID]["interface"] == "can0"


def test_canbus_info_uses_klippers_configured_interface(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, call=_moonraker_canbus(interface="can1"))

    assert api.canbus_info()[UUID]["interface"] == "can1"


def test_canbus_info_is_empty_with_no_configfile_declaration(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, call=_moonraker_canbus(declared=False))

    assert api.canbus_info() == {}


def test_cross_reference_hit_online_is_included_under_scope_all(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths)
    _track_uuid(paths)
    api = Api(paths, call=_moonraker_canbus(version="v0.13.0-711-gd7cea5bb"))

    boards = api._canbus_boards_to_flash(Registry.load(paths), "all")
    assert [b["uuid"] for b in boards] == [UUID]
    assert boards[0]["reason"] == "forced"
    assert boards[0]["state"] == "klipper"
    assert boards[0]["interface"] == "can0"


def test_cross_reference_hit_with_no_live_version_falls_back_not_excludes(
    paths, live_registry_text
):
    """Declared in printer.cfg, but the mcu object reports no live version -
    unlike a tracked serial's STATE_OFFLINE, this does NOT exclude the board.
    Absence of `mcu_version` here covers both "genuinely offline" and
    "sitting in Katapult, unreachable to klippy" indistinguishably, and the
    latter is exactly the board most in need of a flash - so this falls to
    the same unconditional-inclusion tier a cross-reference miss gets,
    rather than guessing "offline" and silently dropping it."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths)
    _track_uuid(paths)
    api = Api(paths, call=_moonraker_canbus(version=None, bridge=True))

    for scope in ("stale", "all"):
        boards = api._canbus_boards_to_flash(Registry.load(paths), scope)
        assert [b["uuid"] for b in boards] == [UUID]
        assert boards[0]["state"] == "unknown"
        # Even though liveness could not be judged, config at least named
        # the mcu object - so `bridge` still carries through.
        assert boards[0]["bridge"] is True


def test_cross_reference_miss_falls_back_to_unconditional_inclusion(paths, live_registry_text):
    """No `canbus_uuid:` declaration anywhere in configfile at all - included
    regardless of scope, since only the flash attempt itself can answer
    whether it is there. This is the accepted-cost fallback tier, not a
    reason to silently drop a tracked CAN board from a fleet sweep."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths)
    _track_uuid(paths)
    api = Api(paths, call=_moonraker_canbus(declared=False))

    boards = api._canbus_boards_to_flash(Registry.load(paths), "stale")
    assert [b["uuid"] for b in boards] == [UUID]
    assert boards[0]["reason"] == "unknown_liveness"
    assert boards[0]["state"] == "unknown"


def test_flash_all_selection_includes_both_serial_and_canbus_boards(paths, live_registry_text):
    """`_board_target` must route each dict shape to the right flasher -
    both identities to Flashtool - inside one combined batch."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths)
    _track_uuid(paths)
    api = Api(paths, call=_moonraker_canbus(declared=False))

    reg = Registry.load(paths)
    boards = api._boards_to_flash(reg, "all") + api._canbus_boards_to_flash(reg, "all")
    from mcu_updater.agent.methods.bulk import _board_target

    targets = [_board_target(b) for b in boards]
    by_flasher = {t.flasher for t in targets}
    assert by_flasher == {"flashtool"}
    can_targets = [t for t in targets if "uuid" in t.detail]
    assert [t.id for t in can_targets] == [UUID]
