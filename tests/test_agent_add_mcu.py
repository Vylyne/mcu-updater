"""Setting up a brand-new board: `fw.add_mcu.start`.

The one method Phase 6 adds. Adopting the result is `fw.serial.add` and putting
Klipper on it is `fw.flash` - both already exist, so wrapping them here would be
a second implementation to keep in step with the first.

The property that shapes everything: **a board in DFU has no identity to adopt.**
It exposes no `/dev/serial/by-id` name at all, so there is nothing to put in the
registry until Katapult is on it and it re-enumerates. Hence the snapshot before
and the diff after, rather than taking a serial as an argument.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.config import Registry
from mcu_updater.jobs import JobRunner

from .conftest import bootsel_device_node, make_device, mounted_bootsel_volume, write_settings
from .test_agent_dfu import ONE_BOARD, TWO_BOARDS, patch_dfu

EBB = "bttebb36"
EBB_CHIPSET = "stm32g0b1xx"
TRACKED = "123456789012345678901"
PICO = "testrp2040"
PICO_CHIPSET = "rp2040"


def _runner(paths) -> JobRunner:
    return JobRunner(
        paths,
        lambda: __import__("mcu_updater.settings", fromlist=["load_settings"]).load_settings(
            paths.settings_file
        ),
    )


def _stage_katapult(paths, mcu_type=EBB) -> str:
    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    path = paths.bin_file(mcu_type, "katapult")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 512)
    return path


def _stage_katapult_uf2(paths, mcu_type=PICO) -> str:
    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    path = paths.uf2_file(mcu_type, "katapult")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 512)
    return path


@pytest.fixture
def adder(paths, live_registry_text, fake_root):
    """An agent with a runner and flashing enabled."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")

    runner = _runner(paths)
    api = Api(paths, runner=runner)
    # The "nothing appeared" cases otherwise wait out the full re-enumeration
    # timeout, which dominated the run at 15s apiece.
    api.ADD_MCU_REENUMERATE_TIMEOUT = 1.0
    yield api
    runner._cancel.set()
    runner.wait(timeout=20)


@pytest.fixture
def read_only(paths, live_registry_text, fake_root):
    """Flashing off: the capability gate, and nothing else changed."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null")
    return Api(paths, runner=_runner(paths))


# --------------------------------------------------------------------------
# the gates, every one before a job exists
# --------------------------------------------------------------------------


def test_it_is_not_advertised_without_flashing_enabled(read_only):
    caps = read_only.dispatch("fw.ping")["capabilities"]
    assert "fw.add_mcu.start" not in caps
    # The read-only probe stays: diagnosing a board you cannot see is exactly
    # what someone without flashing enabled still needs.
    assert "fw.dfu.scan" in caps


def test_the_method_refuses_too_not_just_the_advertisement(read_only):
    """Two independent layers. Turning the setting off mid-flight is honoured,
    not merely un-suggested."""
    with pytest.raises(RpcError) as exc:
        read_only.add_mcu_start({"name": EBB})
    assert exc.value.data["code"] == "flashing_disabled"


def test_an_unknown_type_fails_fast(adder):
    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": "nosuchtype"})
    assert exc.value.data["code"] == "unknown_type"
    assert adder.runner.current() is None


def test_a_type_with_no_katapult_build_is_refused_with_the_reason(adder, monkeypatch):
    """This flow installs the bootloader, so the bootloader has to exist."""
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": EBB})

    assert exc.value.data["code"] == "no_artifact"
    assert exc.value.data["data"]["fw"] == "katapult"
    assert adder.runner.current() is None


def test_an_unrelated_chipset_is_refused_precisely(adder, paths, monkeypatch):
    """Neither DFU nor BOOTSEL applies to an ESP32 - say so precisely rather
    than failing inside the job with something about dfu-util or a mount.

    Not named `knomi`: the fixture config already declares that as a
    platformio type, and `add_type` refuses another builder's name."""
    adder.dispatch("fw.type.add", {"name": "espboard", "chipset": "esp32"})
    assert "espboard" in Registry.load(paths).names()
    _stage_katapult(paths, "espboard")
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": "espboard"})
    assert exc.value.data["code"] == "unsupported_chipset"
    assert exc.value.data["data"]["chipset"] == "esp32"


def test_no_board_in_dfu_is_refused_before_a_job(adder, paths, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout="dfu-util 0.11\n")

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert exc.value.data["code"] == "dfu_none"
    assert adder.runner.current() is None


def test_a_permissions_problem_keeps_its_own_code(adder, paths, monkeypatch):
    """It must not collapse into "no board" here either - that is the whole point
    of keeping the reasons apart."""
    _stage_katapult(paths)
    patch_dfu(
        monkeypatch,
        stderr="Cannot open DFU device 0483:df11 found on devnum 51 (LIBUSB_ERROR_ACCESS)",
    )

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert exc.value.data["code"] == "dfu_permission_denied"


# --------------------------------------------------------------------------
# choosing between boards
# --------------------------------------------------------------------------


def test_two_boards_is_refused_until_one_is_named(adder, paths, monkeypatch):
    """The gate a second board on the bench tests. Not a dead end - dfu-util can
    target one exactly - but the panel has to make the choice explicit, because a
    USB serial says nothing about which board is which physically."""
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=TWO_BOARDS)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert exc.value.data["code"] == "dfu_ambiguous"
    assert len(exc.value.data["data"]["devices"]) == 2
    assert adder.runner.current() is None


def test_naming_one_of_two_gets_past_the_gate(adder, paths, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=TWO_BOARDS)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB, "dfu_serial": "205B33753539"})
    assert res["dfu_serial"] == "205B33753539"
    assert adder.runner.wait(timeout=30)
    assert adder.runner.get(res["job_id"]).state == "succeeded"


def test_naming_a_serial_that_is_not_in_dfu_is_refused(adder, paths, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": EBB, "dfu_serial": "NOPE"})
    assert exc.value.data["code"] == "device_not_found"
    assert adder.runner.current() is None


def test_a_lone_board_needs_no_choice(adder, paths, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    # Resolved for the caller, and reported so the log names what was written to.
    assert res["dfu_serial"] == "3941335F3434"
    assert adder.runner.wait(timeout=30)


# --------------------------------------------------------------------------
# finding what appeared
# --------------------------------------------------------------------------


def test_the_new_board_is_found_by_diffing_the_bus(adder, paths, fake_root, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "katapult", EBB_CHIPSET, "NEWBOARD")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [c["serial"] for c in job.result["candidates"]] == ["NEWBOARD"]
    assert job.result["type"] == EBB


def test_a_katapult_board_already_on_the_bus_is_not_reported_as_new(adder, paths, fake_root, monkeypatch):
    """The snapshot is taken before the write, so a board already sitting in
    Katapult - a previous adopt that was never finished, say - cannot be mistaken
    for the one this just created. The wait is no longer filtered to Katapult
    devices (see the chain-load test below), so this exclusion works purely off
    the before/after baseline diff now - not off firmware name.
    """
    make_device(fake_root / "bus", "katapult", EBB_CHIPSET, "WASHERE")
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert job.result["candidates"] == [], "the pre-existing board is not this one"


def test_the_new_board_is_told_apart_from_one_already_in_katapult(adder, paths, fake_root, monkeypatch):
    """Both on the bus at the end; only the one that appeared is offered."""
    make_device(fake_root / "bus", "katapult", EBB_CHIPSET, "WASHERE")
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "katapult", EBB_CHIPSET, "NEWBOARD")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [c["serial"] for c in job.result["candidates"]] == ["NEWBOARD"]


def test_a_board_that_chain_loads_past_katapult_is_still_a_candidate(adder, paths, fake_root, monkeypatch):
    """Found on hardware: a board that already carries a valid application does
    not sit in Katapult waiting to be found - Katapult's own first boot chain-
    loads straight into that application. Re-installing a bootloader on such a
    board (the normal case, not an edge case) makes it reappear running its own
    firmware, not "katapult". The wait must still recognize it.
    """
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "klipper", EBB_CHIPSET, "NEWBOARD")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [c["serial"] for c in job.result["candidates"]] == ["NEWBOARD"]


def test_a_re_bootloadered_tracked_board_is_reported_as_such_not_as_nothing(
    adder, paths, fake_root, monkeypatch
):
    """Found on the printer, and the reason this distinction exists.

    Re-installing the bootloader on a board that is ALREADY tracked is the normal
    case, not the exception - it sits offline in the registry precisely because it
    had no firmware. Baselining on untracked-only meant it came back, was
    correctly excluded as tracked, and the job said "no new device appeared",
    sending the user to hunt for a failure when the flash had worked perfectly.
    """
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "katapult", EBB_CHIPSET, TRACKED)

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    # Nothing to adopt - it is already ours...
    assert job.result["candidates"] == []
    # ...but it definitely came back, and the result says so.
    assert [d["serial"] for d in job.result["already_tracked"]] == [TRACKED]

    lines, _, _ = job.log_since(0)
    text = "\n".join(line.text for line in lines)
    assert "already tracked" in text
    assert "No board appeared" not in text, "it did appear; do not send them hunting"


def test_no_new_board_warns_rather_than_failing_the_job(adder, paths, monkeypatch):
    """The write may well have succeeded and the board simply be slow, or on a
    marginal port. Saying what to look at beats failing a job that worked."""
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert job.result["candidates"] == []
    lines, _, _ = job.log_since(0)
    assert any("serial/by-id" in line.text for line in lines)


def test_klipper_is_never_stopped(adder, paths, monkeypatch):
    """A board that is not in printer.cfg is not held by Klipper, so there is no
    port contention and no reason for an outage. The CLI's add-mcu never stopped
    it either."""
    from mcu_updater.service import NullService

    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)

    assert adder.runner.get(res["job_id"]).state == "succeeded"
    assert svc.actions == []


def test_adopting_the_result_is_the_existing_method(adder, paths, fake_root, monkeypatch):
    """No fw.add_mcu.confirm: fw.serial.add already adopts a serial into a type,
    validation included. A second implementation would only be one more thing to
    keep in step."""
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "katapult", EBB_CHIPSET, "NEWBOARD")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    candidate = adder.runner.get(res["job_id"]).result["candidates"][0]["serial"]

    adder.dispatch("fw.serial.add", {"name": EBB, "serial": candidate})
    assert candidate in Registry.load(paths).get(EBB).serials


# --------------------------------------------------------------------------
# the rp2040/BOOTSEL branch
# --------------------------------------------------------------------------


def _pico_type(adder, paths) -> None:
    adder.dispatch("fw.type.add", {"name": PICO, "chipset": PICO_CHIPSET})
    assert PICO in Registry.load(paths).names()


def test_a_type_with_no_katapult_uf2_is_refused_with_the_reason(adder, paths):
    """BOOTSEL needs a .uf2, not a .bin - the artifact check has to look for
    the right file or this fails deep inside the write with a confusing error."""
    _pico_type(adder, paths)
    _stage_katapult(paths, PICO)  # only the .bin, deliberately

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert exc.value.data["code"] == "no_artifact"
    assert exc.value.data["data"]["path"] == paths.uf2_file(PICO, "katapult")


def test_bootsel_no_board_is_refused_before_a_job(adder, paths, fake_root):
    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(fake_root / "nothing-here"))

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert exc.value.data["code"] == "bootsel_none"
    assert adder.runner.current() is None


def test_bootsel_unmounted_device_is_refused_before_a_job(adder, paths, fake_root):
    """The board is attached but nothing mounts it - the fix is the udev rule,
    not replugging a board that never had a problem."""
    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)
    root = fake_root / "bootsel_root"
    node = bootsel_device_node(root)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert exc.value.data["code"] == "bootsel_not_mounted"
    assert exc.value.data["data"]["devices"][0]["node"] == node
    assert adder.runner.current() is None


def test_bootsel_ambiguous_mounts_is_refused_before_a_job(adder, paths, fake_root, monkeypatch):
    """Two boards in BOOTSEL at once - unlike DFU there is no serial to pick
    one by, so this is a plain refusal, not a choice the caller can make."""
    import mcu_updater.discovery.bootsel as bootsel_discovery
    from mcu_updater import devices as devices_mod

    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)

    media = fake_root / "media"
    for user in ("alice", "bob"):
        vol = media / user / "RPI-RP2"
        vol.mkdir(parents=True)
        (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    monkeypatch.setattr(devices_mod, "DEFAULT_BOOTSEL_ROOT_GLOBS", (str(media / "*"),))

    by_id = fake_root / "by-id"
    by_id.mkdir()
    for serial in ("AAAAAAAAAAAA", "BBBBBBBBBBBB"):
        (by_id / f"usb-RPI_RP2_{serial}-0-0-part1").write_text("", encoding="utf-8")
    monkeypatch.setattr(bootsel_discovery, "_BOOTSEL_DISK_BY_ID_GLOB", str(by_id / "usb-RPI_RP2_*-part1"))
    adder.paths = dataclasses.replace(adder.paths, bootsel_root="")

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert exc.value.data["code"] == "bootsel_ambiguous"
    assert len(exc.value.data["data"]["devices"]) == 2
    assert adder.runner.current() is None


def test_a_lone_bootsel_board_needs_no_choice(adder, paths, fake_root, monkeypatch):
    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="E0C9125B0D9B")
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert res["bootsel_id"] == "E0C9125B0D9B"
    assert res["dfu_serial"] is None
    assert adder.runner.wait(timeout=30)
    assert adder.runner.get(res["job_id"]).state == "succeeded"


def test_bootsel_flash_receives_the_uf2_path(adder, paths, fake_root, monkeypatch):
    """The one detail that is easy to get wrong porting the DFU branch: BOOTSEL
    needs uf2_bin threaded through, or the write fails deep inside the job with
    a message about a missing .uf2 that a caller cannot act on synchronously."""
    _pico_type(adder, paths)
    uf2_path = _stage_katapult_uf2(paths, PICO)
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))

    calls: list[dict] = []

    def spy(
        paths,
        settings,
        chipset,
        fw_bin,
        *,
        fw,
        mcu_type,
        uf2_bin=None,
        katapult_config=None,
        reporter=None,
        target_serial=None,
    ):
        calls.append(
            {
                "chipset": chipset,
                "fw_bin": fw_bin,
                "fw": fw,
                "uf2_bin": uf2_bin,
                "katapult_config": katapult_config,
            }
        )

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", spy)

    res = adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert adder.runner.wait(timeout=30)
    assert adder.runner.get(res["job_id"]).state == "succeeded"
    assert calls[0]["uf2_bin"] == uf2_path
    assert calls[0]["chipset"] == PICO_CHIPSET
    # A type with Katapult still installs Katapult first.
    assert calls[0]["fw"] == "katapult"
    # Without it BOOTSEL cannot erase the old application and refuses the write.
    assert calls[0]["katapult_config"] == paths.config_file(PICO, "katapult")


def test_the_new_bootsel_board_is_found_by_diffing_the_bus(adder, paths, fake_root, monkeypatch):
    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))

    def appear(*args, **kwargs):
        make_device(fake_root / "bus", "katapult", PICO_CHIPSET, "NEWPICO")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [c["serial"] for c in job.result["candidates"]] == ["NEWPICO"]
    assert job.result["bootsel_id"] is not None


def test_bootsel_pairing_is_recorded_before_the_wait(adder, paths, fake_root, monkeypatch):
    """Keyed on the boot-ROM id, exactly what `Pairings` needs to catch a board
    that misses the live re-enumeration wait - see docs/agent-api.md."""
    from mcu_updater.flashers.pairings import Pairings

    _pico_type(adder, paths)
    _stage_katapult_uf2(paths, PICO)
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="E0C9125B0D9B")
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert adder.runner.wait(timeout=30)
    assert res["bootsel_id"] == "E0C9125B0D9B"
    assert Pairings(adder.paths).type_for("E0C9125B0D9B") == PICO


# --------------------------------------------------------------------------
# a type with no Katapult: its own application is the first image
# --------------------------------------------------------------------------

BARE_PICO = "barepico"
BARE_EBB = "bareebb"
KLIPPER_SECTION = "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n"


def _klipper_flashers(paths, flashers: str) -> None:
    """Rewrite only `[firmware klipper]`'s list. A plain replace of
    `flashers: flashtool` would hit `[firmware cartographer]` too."""
    with open(paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert KLIPPER_SECTION in text
    text = text.replace(
        KLIPPER_SECTION, f"[firmware klipper]\nsource: ~/klipper\nflashers: {flashers}\n"
    )
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _no_katapult_type(adder, name: str, chipset: str) -> None:
    adder.dispatch(
        "fw.type.add", {"name": name, "chipset": chipset, "katapult_installed": False}
    )
    assert "katapult" not in Registry.load(adder.paths).get(name).firmwares


def _uf2_image(address: int, payload: bytes = b"\x5a" * 256) -> bytes:
    """One valid UF2 block of `payload` at `address`."""
    import struct

    block = struct.pack(
        "<8I", 0x0A324655, 0x9E5D5157, 0x2000, address, 256, 0, 1, 0xE48BFF56
    )
    return block + payload.ljust(476, b"\0") + struct.pack("<I", 0x0AB16F30)


def _stage_klipper_uf2(paths, name: str, address: int) -> str:
    os.makedirs(paths.artifact_dir(name), exist_ok=True)
    path = paths.uf2_file(name, "klipper")
    with open(path, "wb") as fh:
        fh.write(_uf2_image(address))
    return path


def _stage_klipper_bin(paths, name: str, app_address: int | None) -> str:
    import json

    os.makedirs(paths.artifact_dir(name), exist_ok=True)
    path = paths.bin_file(name, "klipper")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 512)
    with open(paths.sidecar_file(name, "klipper"), "w", encoding="utf-8") as fh:
        json.dump({"app_address": app_address}, fh)
    return path


def _bare_pico(adder, paths, fake_root, *, address: int, flashers: str = "flashtool, bootsel"):
    """A no-Katapult RP2040 type, its klipper .uf2 staged, one board mounted."""
    _no_katapult_type(adder, BARE_PICO, PICO_CHIPSET)
    _klipper_flashers(paths, flashers)
    uf2 = _stage_klipper_uf2(paths, BARE_PICO, address)
    root, vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))
    return uf2, vol


def test_a_type_with_no_katapult_writes_its_klipper_uf2_over_bootsel(
    adder, paths, fake_root, monkeypatch
):
    """The bug: the flow always wrote Katapult. A type with none has its own
    application as its first image, and that image is what goes on the board -
    unmodified, since it starts at flash base and overwrites what boots.

    Not a dry run: the copy lands on the fake mounted volume, so the bytes on
    it are what this checks."""
    write_settings(paths, dry_run="false")
    uf2, vol = _bare_pico(adder, paths, fake_root, address=0x10000000)
    erased: list = []
    monkeypatch.setattr(
        "mcu_updater.flashers.flash._stage_erasing_uf2",
        lambda *a, **k: erased.append(a) or a[0],
    )

    res = adder.dispatch("fw.add_mcu.start", {"name": BARE_PICO})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    with open(uf2, "rb") as fh:
        assert (vol / "klipper.uf2").read_bytes() == fh.read()
    assert not (vol / "katapult.uf2").exists()
    assert erased == [], "an application image is not staged with an erased sector"
    assert job.result["fw"] == "klipper"
    lines, _, _ = job.log_since(0)
    text = "\n".join(line.text for line in lines)
    assert "Flashing klipper onto the BOOTSEL board" in text


def test_a_type_with_katapult_reports_katapult_as_its_fw(adder, paths, monkeypatch):
    _stage_katapult(paths)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", lambda *a, **k: None)

    res = adder.dispatch("fw.add_mcu.start", {"name": EBB})
    assert adder.runner.wait(timeout=30)
    assert adder.runner.get(res["job_id"]).result["fw"] == "katapult"


def test_an_offset_klipper_uf2_is_refused_before_a_job(adder, paths, fake_root):
    """Nothing below it would boot it. Refused synchronously, like no_artifact,
    so the panel can say why before anything is written."""
    write_settings(paths, dry_run="false")
    _uf2, vol = _bare_pico(adder, paths, fake_root, address=0x10004000)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": BARE_PICO})

    assert exc.value.data["code"] == "offset_mismatch"
    assert "0x10004000" in str(exc.value)
    assert "No bootloader" in str(exc.value)
    assert adder.runner.current() is None
    assert sorted(p.name for p in vol.iterdir()) == ["INFO_UF2.TXT"]


def test_a_corrupt_klipper_uf2_is_refused_before_a_job(adder, paths, fake_root):
    uf2, _vol = _bare_pico(adder, paths, fake_root, address=0x10000000)
    with open(uf2, "wb") as fh:
        fh.write(b"\0" * 8)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": BARE_PICO})
    assert exc.value.data["code"] == "offset_mismatch"
    assert "not a valid UF2 image" in str(exc.value)
    assert exc.value.data["data"]["start"] is None
    assert adder.runner.current() is None


def _bare_ebb(adder, paths, monkeypatch, *, app_address, flashers="flashtool, dfu_util"):
    """A no-Katapult STM32 type, its klipper .bin and sidecar staged, one board
    in DFU. The dfu-util write itself is recorded, never run."""
    _no_katapult_type(adder, BARE_EBB, EBB_CHIPSET)
    _klipper_flashers(paths, flashers)
    fw_bin = _stage_klipper_bin(paths, BARE_EBB, app_address)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    written: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.flashers.flash.flash_dfu_stm32",
        lambda paths, settings, fw_bin, **kw: written.append(fw_bin),
    )
    return fw_bin, written


def test_a_type_with_no_katapult_writes_its_klipper_bin_over_dfu(adder, paths, monkeypatch):
    fw_bin, written = _bare_ebb(adder, paths, monkeypatch, app_address=0x08000000)

    res = adder.dispatch("fw.add_mcu.start", {"name": BARE_EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert written == [fw_bin]
    assert job.result["fw"] == "klipper"


@pytest.mark.parametrize("app_address", [0x08002000, None], ids=["offset", "unknown"])
def test_a_klipper_bin_not_at_flash_base_is_refused_before_a_job(
    adder, paths, monkeypatch, app_address
):
    """An address the sidecar cannot vouch for is refused too: it cannot be
    proven bootable, and DFU writes at 0x08000000 regardless."""
    _fw_bin, written = _bare_ebb(adder, paths, monkeypatch, app_address=app_address)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": BARE_EBB})

    assert exc.value.data["code"] == "offset_mismatch"
    assert "No bootloader" in str(exc.value)
    assert adder.runner.current() is None
    assert written == []


def test_no_dfu_util_on_klipper_names_the_line_to_add(adder, paths, monkeypatch):
    """Not "flash Katapult by hand": this type has no Katapult. The fix is the
    install family's own list."""
    _fw_bin, written = _bare_ebb(
        adder, paths, monkeypatch, app_address=0x08000000, flashers="flashtool"
    )

    res = adder.dispatch("fw.add_mcu.start", {"name": BARE_EBB})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "failed"
    assert job.error["code"] == "unsupported_chipset"
    assert "[firmware klipper]" in job.error["message"]
    assert "flashers: flashtool, dfu_util" in job.error["message"]
    assert "katapult" not in job.error["message"].lower()
    assert written == []


def test_a_type_with_no_katapult_and_nothing_built_names_klipper(adder, paths, monkeypatch):
    _no_katapult_type(adder, BARE_EBB, EBB_CHIPSET)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": BARE_EBB})

    assert exc.value.data["code"] == "no_artifact"
    assert exc.value.data["data"]["fw"] == "klipper"
    assert exc.value.data["data"]["path"] == paths.bin_file(BARE_EBB, "klipper")
    assert "no built klipper .bin" in str(exc.value)
    assert "bootloader has to exist" not in str(exc.value)
    assert adder.runner.current() is None


def test_a_bin_only_rp2040_klipper_build_is_told_to_drop_the_offset(
    adder, paths, fake_root
):
    """An RP2040 Klipper build makes a .bin only for a bootloader offset, so
    "build it first" would make the same .bin again. The advice names the
    setting instead."""
    _bare_pico(adder, paths, fake_root, address=0x10000000)
    os.remove(paths.uf2_file(BARE_PICO, "klipper"))
    _stage_klipper_bin(paths, BARE_PICO, app_address=0x10004000)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": BARE_PICO})

    assert exc.value.data["code"] == "no_artifact"
    assert "no built klipper .uf2" in str(exc.value)
    assert "No bootloader" in str(exc.value)
    assert "Build it first" not in str(exc.value)
    assert adder.runner.current() is None
