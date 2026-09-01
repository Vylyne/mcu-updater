from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater import devices as devices_mod
from mcu_updater import flashers
from mcu_updater.errors import (
    AmbiguousDfuError,
    BootselNotMountedError,
    DeviceNotFoundError,
    FlashError,
    OffsetMismatchError,
    ToolMissingError,
    UnsupportedChipsetError,
)
from mcu_updater.flashers import flash as flash_mod
from mcu_updater.flashers.flash import (
    flash_dfu_stm32,
    flash_initial_bootloader,
    flash_katapult,
)

from .conftest import bootsel_device_node, cmd_tokens, make_device, mounted_bootsel_volume


def _cmds(events: list) -> list[str]:
    return [line for stream, line in events if stream == "cmd"]


@pytest.fixture
def ready(paths, settings, fake_root):
    """A staged firmware binary and an installed flashtool.py."""
    settings.dry_run = True
    (fake_root / "katapult" / "scripts").mkdir(parents=True, exist_ok=True)
    (fake_root / "katapult" / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    _stage_bin(paths)
    return settings


def _stage_bin(paths, mcu_type: str = "board") -> None:
    """Built firmware lives in the data tree, not beside the saved config."""
    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    with open(paths.bin_file(mcu_type, "klipper"), "wb") as fh:
        fh.write(b"\0" * 16)


def test_missing_flashtool_raises(paths, settings, fake_root):
    _stage_bin(paths)
    with pytest.raises(ToolMissingError) as exc:
        flash_katapult(paths, settings, "board", "chipA", "S1")
    assert exc.value.data["tool"] == "flashtool.py"


def test_flashtool_path_overrides_the_katapult_convention(paths, settings, fake_root):
    """A fork checked out elsewhere, say - `flashtool_path` names it directly
    rather than assuming ~/katapult/scripts/flashtool.py."""
    (fake_root / "elsewhere" / "scripts").mkdir(parents=True)
    (fake_root / "elsewhere" / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    settings.flashtool_path = "~/elsewhere/scripts/flashtool.py"
    _stage_bin(paths)

    with pytest.raises(DeviceNotFoundError):
        # Past the ToolMissingError means the configured path was found.
        flash_katapult(paths, settings, "board", "chipA", "S1")


def test_missing_firmware_binary_raises(paths, settings, fake_root):
    (fake_root / "katapult" / "scripts").mkdir(parents=True)
    (fake_root / "katapult" / "scripts" / "flashtool.py").write_text("", encoding="utf-8")
    with pytest.raises(FlashError) as exc:
        flash_katapult(paths, settings, "board", "chipA", "S1")
    assert "Build it first" in str(exc.value)


def test_offline_device_raises_device_not_found(paths, ready):
    with pytest.raises(DeviceNotFoundError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1")
    assert exc.value.data["serial"] == "S1"
    assert exc.value.code == "device_not_found"


def test_device_already_in_bootloader_is_flashed_directly(paths, ready, fake_root):
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )
    flags = [t for c in (cmd_tokens(x) for x in _cmds(events)) for t in c]
    assert "-f" in flags
    # No bootloader request needed - it is already there.
    assert "-r" not in flags
    assert any("Flashed S1 successfully" in line for _, line in events)


def test_device_running_klipper_is_flashed_without_a_separate_reboot(paths, ready, fake_root):
    make_device(fake_root / "bus", "klipper", "chipA", "S1")  # lowercase on purpose
    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )
    per_cmd = [cmd_tokens(c) for c in _cmds(events)]
    assert all("-r" not in toks for toks in per_cmd)
    assert any("-f" in toks for toks in per_cmd)
    assert any("Flashed S1 successfully" in line for _, line in events)


def test_a_fork_that_renames_its_usb_descriptor_is_still_found(paths, ready, fake_root):
    """A board running a non-klipper family must still be flashable.

    The cartographer probe enumerates as
    `usb-Cartographer_stm32g431xx_<serial>-if00` - its own family name, not
    klipper's. The bootloader-request lookup was hardcoded to katapult and
    klipper, so a board sitting on the bus was reported as "no device found
    ... Is it plugged in?" - while `updatefw status` showed it present, because
    every other lookup site passes no family at all and matches anything.

    Found on hardware 2026-08-21, on the one board in the fleet that is a
    renamed klipper fork.
    """
    make_device(fake_root / "bus", "Cartographer", "chipA", "S1")
    os.makedirs(paths.artifact_dir("board"), exist_ok=True)
    with open(paths.bin_file("board", "cartographer"), "wb") as fh:
        fh.write(bytes(16))

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths,
        ready,
        "board",
        "chipA",
        "S1",
        fw="cartographer",
        reporter=lambda s, line: events.append((s, line)),
    )
    per_cmd = [cmd_tokens(c) for c in _cmds(events)]
    assert all("-r" not in toks for toks in per_cmd)
    assert any("-f" in toks for toks in per_cmd)
    # The board names itself; the log should not claim it is running Klipper.
    assert any("Cartographer" in line for _, line in events)


# --------------------------------------------------------------------------
# the offset checks
#
# flashtool's -s/--status runs the same connect_btl() handshake as -f -
# including the "Application Start:" line these check - but skips send/
# verify/finish, so nothing is written. flash_katapult uses it to refuse a
# mismatched write before -f is ever called, then checks again from what -f
# itself reported, as a second line of defence against the board changing
# between the two. See the module docstring.
# --------------------------------------------------------------------------


def _write_sidecar(paths, mcu_type: str, fw: str, **fields) -> None:
    import json

    with open(paths.sidecar_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


def _fake_run_streamed_once(monkeypatch, rc: int, lines: list[str]) -> None:
    """One canned response, fed to every run_streamed call regardless of argv.

    Fine when the probe and the write are expected to agree (or when only one
    of them should ever run).
    """

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return rc

    monkeypatch.setattr(flash_mod, "run_streamed", fake)


def _fake_run_streamed_by_call(monkeypatch, probe=(0, []), write=(0, [])) -> list[list[str]]:
    """A different canned response for the -s probe than for the -f write, so
    the two can be made to disagree - and the argv of every call is recorded,
    so a test can assert whether the write was ever attempted at all."""
    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        rc, lines = probe if "-s" in cmd else write
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return rc

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    return calls


def test_a_mismatched_bootloader_refuses_before_writing(paths, ready, fake_root, monkeypatch):
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(monkeypatch, probe=(0, ["Application Start: 0x8000"]))

    with pytest.raises(OffsetMismatchError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1")

    assert "0x8004000" in str(exc.value) and "0x8000" in str(exc.value)
    # The probe ran; the write never did.
    assert len(calls) == 1
    assert "-s" in calls[0]


def test_agreeing_addresses_proceed_to_write(paths, ready, fake_root, monkeypatch):
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Application Start: 0x8004000"]),
    )

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )

    assert not [line for stream, line in events if stream in ("error", "warn")]
    assert len(calls) == 2  # probe, then the write


def test_a_real_flash_records_unique_bus_id_confidence(paths, ready, fake_root, monkeypatch):
    """The confirmed-at-write-time ledger Step 27 gives a board: a by-id
    sighting is die-derived, not remembered, so the FlashLog record for a real
    write carries `unique_bus_id` - the board-side counterpart to a display's
    `answered` after a listen pass."""
    from mcu_updater.build import FlashLog

    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Application Start: 0x8004000"]),
    )

    flash_katapult(paths, ready, "board", "chipA", "S1")

    record = FlashLog(paths).all()["S1"]
    assert record["confidence"] == "unique_bus_id"


def test_a_real_flash_records_the_sidecars_stamped_version(paths, ready, fake_root, monkeypatch):
    """Cartographer's CONFIG_VERSION carries no commit, so this is what
    `FlashLog.entry_for` has to fall back on for a board like this - see the
    discard test in test_build.py's counterpart."""
    from mcu_updater.build import FlashLog

    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(
        paths, "board", "klipper", app_address=0x08004000, version="CARTOGRAPHER 6.2.0"
    )
    _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Application Start: 0x8004000"]),
    )

    flash_katapult(paths, ready, "board", "chipA", "S1")

    record = FlashLog(paths).all()["S1"]
    assert record["version"] == "CARTOGRAPHER 6.2.0"


def test_a_version_only_record_is_discarded_when_the_stamp_disagrees(paths):
    """The safety half of the sha-less path. With no running commit to check, a
    disagreeing recorded version invalidates the record exactly as a
    disagreeing sha would on a normal board - it must not be left standing
    just because there was nothing to contradict it on the sha side."""
    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    log.record(
        "S1",
        mcu_type="cartographer",
        fw="klipper",
        bin_sha256="aa" * 32,
        fw_sha=None,
        version="CARTOGRAPHER 6.2.0",
    )

    assert log.entry_for("S1", None, version="CARTOGRAPHER 6.2.0") is not None
    assert log.entry_for("S1", None, version="CARTOGRAPHER 6.1.0") is None, (
        "a differing stamp invalidates it even though fw_sha would have passed - "
        "there is no running sha here to check"
    )
    # No reported version to check against: the record stands, since we have
    # nothing contradicting it.
    assert log.entry_for("S1", None, version=None) is not None


def test_an_unreadable_probe_refuses_before_writing(paths, ready, fake_root, monkeypatch):
    """We have our own half (app_address) but flashtool's own words didn't
    parse - the check went blind, which refuses same as a real mismatch: "a
    check that quietly stops checking is worse than no check"."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(monkeypatch, probe=(0, ["Erasing...", "Writing..."]))

    with pytest.raises(OffsetMismatchError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1")

    assert "could not read" in str(exc.value)
    assert len(calls) == 1
    assert "-s" in calls[0]


def test_force_downgrades_the_refusal_to_a_warning_and_still_writes(
    paths, ready, fake_root, monkeypatch
):
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8000"]),
        write=(0, ["Application Start: 0x8000"]),
    )

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths,
        ready,
        "board",
        "chipA",
        "S1",
        reporter=lambda s, line: events.append((s, line)),
        force=True,
    )

    warnings = [line for stream, line in events if stream == "warn"]
    assert any("0x8004000" in line and "0x8000" in line for line in warnings)
    assert len(calls) == 2  # forced past the refusal, so the write still ran
    assert any("Flashed S1 successfully" in line for _, line in events)


def test_a_board_that_changes_between_probe_and_write_is_still_caught(
    paths, ready, fake_root, monkeypatch
):
    """The probe agreed, but -f's own handshake - moments later - does not:
    the second line of defence, since the write already happened by then and
    cannot be un-done."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Application Start: 0x8000"]),
    )

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )

    errors = [line for stream, line in events if stream == "error"]
    assert any("0x8004000" in line and "0x8000" in line for line in errors)
    # Diagnostic only at this point - the write cannot be refused after it ran.
    assert any("Flashed S1 successfully" in line for _, line in events)


def test_an_unparseable_write_handshake_is_still_warned_about(
    paths, ready, fake_root, monkeypatch
):
    """The probe agreed, but -f's own handshake - moments later - didn't parse:
    same second-line-of-defence reasoning as the mismatch case above, just for
    the 'the check itself went blind' half of it."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Erasing...", "Writing..."]),
    )

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )

    warnings = [line for stream, line in events if stream == "warn"]
    assert any("could not read" in line for line in warnings)
    assert any("Flashed S1 successfully" in line for _, line in events)


def test_the_minimum_width_hex_quirk_is_tolerated(paths, ready, fake_root, monkeypatch):
    """Upstream's format string is `0x{app_start_addr:4X}` - a *minimum* width,
    not zero-padded, so a short address prints with a space after 0x rather
    than 0x08000000-style padding. A real STM32 address never needs this, but
    the parser must not assume it can't happen."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x800)
    _fake_run_streamed_once(monkeypatch, 0, ["Application Start: 0x 800"])

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )

    assert not [line for stream, line in events if stream in ("error", "warn")]


def test_nothing_is_reported_without_a_recorded_app_address(
    paths, ready, fake_root, monkeypatch
):
    """An older build, or a family that never defines the symbol - nothing of
    ours to compare against, so not a finding, and no probe is even attempted."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper")
    calls = _fake_run_streamed_by_call(monkeypatch, write=(0, ["Application Start: 0x8000"]))

    events: list[tuple[str, str]] = []
    flash_katapult(
        paths, ready, "board", "chipA", "S1", reporter=lambda s, line: events.append((s, line))
    )

    assert not [line for stream, line in events if stream in ("error", "warn")]
    assert len(calls) == 1  # the write only - no sidecar address, so no probe
    assert "-s" not in calls[0]


# --------------------------------------------------------------------------
# DFU
# --------------------------------------------------------------------------


def _dfu(serial=None, path="1-1.2", devnum="51"):
    """A parsed DFU device, as `dfu_devices` now returns them."""
    return {
        "vidpid": "0483:df11",
        "serial": serial,
        "path": path,
        "devnum": devnum,
        "raw": f"Found DFU: [0483:df11] ... path={path}",
    }


def test_no_dfu_device_raises(paths, ready, monkeypatch):
    monkeypatch.setattr(flash_mod, "dfu_devices", lambda **kw: [])
    with pytest.raises(DeviceNotFoundError):
        flash_dfu_stm32(paths, ready, str(paths.bin_file("board", "klipper")))


def test_multiple_dfu_devices_are_refused(paths, ready, monkeypatch):
    """The original targeted 0483:df11 unconditionally, so with two boards in DFU
    it would flash whichever answered first - i.e. possibly the wrong one.

    Still a refusal by default even though dfu-util can target one exactly: a USB
    serial says nothing about which board on the bench it is, so the choice has to
    be the caller's.
    """
    monkeypatch.setattr(
        flash_mod,
        "dfu_devices",
        lambda **kw: [_dfu(path="1-1.2"), _dfu(path="1-1.3", devnum="52")],
    )
    with pytest.raises(AmbiguousDfuError) as exc:
        flash_dfu_stm32(paths, ready, str(paths.bin_file("board", "klipper")))
    assert len(exc.value.data["devices"]) == 2
    assert "unplug all but the target" in str(exc.value).lower()
    # ...and it says the alternative, rather than only offering the blunt one.
    assert "serial" in str(exc.value)


def test_exactly_one_dfu_device_is_flashed(paths, ready, monkeypatch):
    monkeypatch.setattr(flash_mod, "dfu_devices", lambda **kw: [_dfu()])
    events: list[tuple[str, str]] = []
    flash_dfu_stm32(
        paths,
        ready,
        str(paths.bin_file("board", "klipper")),
        reporter=lambda s, line: events.append((s, line)),
    )
    per_cmd = [cmd_tokens(c) for c in _cmds(events)]
    assert any(
        toks
        and os.path.basename(toks[0]) == "dfu-util"
        and any("mass-erase" in t for t in toks)
        for toks in per_cmd
    )


def test_missing_binary_for_dfu_raises(paths, ready):
    import os

    with pytest.raises(FlashError):
        flash_dfu_stm32(paths, ready, os.path.join(paths.home, "nope.bin"))


# --------------------------------------------------------------------------
# first-time bootloader dispatch
# --------------------------------------------------------------------------


def test_stm32_dispatches_to_dfu(paths, ready, monkeypatch):
    called = {}
    monkeypatch.setattr(
        flash_mod,
        "flash_dfu_stm32",
        lambda *a, **kw: called.setdefault("yes", True),
    )
    flash_initial_bootloader(paths, ready, "stm32f072xb", "x.bin")
    assert called == {"yes": True}


def test_rp2040_dispatches_to_bootsel_when_a_uf2_was_built(paths, settings, tmp_path):
    """Also the regression test for the target-shape bug this step fixed:
    `flash_initial_bootloader` used to build a DfuUtil-shaped target for every
    chipset, so handing one to Bootsel would `KeyError` on
    `target.detail["uf2_file"]` rather than copy anything."""
    root = tmp_path / "bootsel_root"
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))

    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"\0" * 8)

    flash_initial_bootloader(rp_paths, settings, "rp2040", "unused.bin", uf2_bin=str(uf2))

    assert (vol / "katapult.uf2").read_bytes() == b"\0" * 8


def test_rp2040_refuses_with_no_uf2_built(paths, settings):
    """A .bin copied to BOOTSEL mass storage is silently ignored - refusing
    outright is better than a write that appears to succeed and does nothing."""
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(paths, settings, "rp2040", "x.bin")
    assert ".uf2" in str(exc.value)


def test_an_unknown_chipset_is_reported_clearly(paths, ready):
    with pytest.raises(UnsupportedChipsetError) as exc:
        flash_initial_bootloader(paths, ready, "esp32", "x.bin")
    assert exc.value.data["chipset"] == "esp32"


# --------------------------------------------------------------------------
# select_for: the capability-match seam flash_initial_bootloader now goes
# through, and the same one a batch's own flasher-per-target lookup uses.
# --------------------------------------------------------------------------


def test_select_for_matches_a_bare_stm32_to_dfu_util():
    assert flashers.select_for("stm32f072xb", devices_mod.STATE_DFU).name == "dfu_util"


def test_select_for_matches_a_tracked_board_to_flashtool_in_either_state():
    """Flashtool owns both states on the Klipper bus - the write is what moves
    a board from one to the other, not a precondition on which it starts in."""
    assert flashers.select_for("stm32g431xx", devices_mod.STATE_KLIPPER).name == (
        "flashtool"
    )
    assert flashers.select_for("rp2040", devices_mod.STATE_KATAPULT).name == "flashtool"


def test_select_for_matches_a_display_to_esptool():
    assert flashers.select_for("esp32", devices_mod.STATE_ESP_ROM).name == "esptool"


def test_select_for_matches_a_bare_rp2040_to_bootsel():
    assert flashers.select_for("rp2040", devices_mod.STATE_BOOTSEL).name == "bootsel"


def test_select_for_refuses_an_impossible_chipset_state_pair():
    """esp32 is a real, registered chipset (Esptool writes it) - just never in
    a `klipper` bus state, which nothing claims to answer to."""
    with pytest.raises(UnsupportedChipsetError):
        flashers.select_for("esp32", devices_mod.STATE_KLIPPER)


# --------------------------------------------------------------------------
# Bootsel: RP2040 BOOTSEL mass storage - a plain file copy, not a protocol.
# NOTE: exercised only against a fake mount (paths.bootsel_root). Not verified
# against a real RP2040 - see NOTES.md.
# --------------------------------------------------------------------------


def test_bootsel_copies_the_uf2_to_the_mounted_volume(paths, settings, tmp_path):
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))

    uf2 = tmp_path / "build" / "katapult.uf2"
    uf2.parent.mkdir()
    uf2.write_bytes(b"\x01" * 32)

    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")
    result = flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))

    assert (vol / "katapult.uf2").read_bytes() == b"\x01" * 32
    assert result["mount"] == str(vol)


def test_bootsel_dry_run_copies_nothing(paths, settings, tmp_path):
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    settings.dry_run = True

    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"\0")

    events: list = []
    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")
    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert result == {"mount": None}
    assert not (vol / "katapult.uf2").exists()
    assert any("dry-run" in line for _level, line in events)


def test_bootsel_refuses_a_missing_uf2(paths, settings, tmp_path):
    bench = flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(tmp_path / "nope.uf2"), chipset="rp2040")

    with pytest.raises(FlashError):
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))


def test_bootsel_refuses_when_no_volume_is_mounted(paths, settings, tmp_path):
    rp_paths = dataclasses.replace(paths, bootsel_root=str(tmp_path / "nothing-here"))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"\0")

    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(DeviceNotFoundError):
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))


def test_bootsel_reports_unmounted_device_distinctly_from_no_device(paths, settings, tmp_path):
    """A board attached but unmounted (no automounter on this host) is a
    different failure than no board at all - the fix is a udev rule, not
    holding BOOTSEL and replugging."""
    root = tmp_path / "bootsel_root"
    node = bootsel_device_node(root)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))

    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"\0")
    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(BootselNotMountedError) as exc:
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))
    assert exc.value.data["devices"] == [node]


def test_bootsel_target_for_populates_id_from_the_one_attached_device(paths, tmp_path):
    root = tmp_path / "bootsel_root"
    bootsel_device_node(root, serial="E0C9125B0D9B")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))

    target = flashers.bootsel.target_for("fw.uf2", chipset="rp2040", paths=rp_paths)

    assert target.id == "E0C9125B0D9B"


def test_bootsel_target_for_leaves_id_empty_without_paths():
    target = flashers.bootsel.target_for("fw.uf2", chipset="rp2040")
    assert target.id == ""


def test_bootsel_target_for_leaves_id_empty_with_no_or_ambiguous_devices(paths, tmp_path):
    empty_root = tmp_path / "empty"
    rp_paths = dataclasses.replace(paths, bootsel_root=str(empty_root))
    assert flashers.bootsel.target_for("fw.uf2", chipset="rp2040", paths=rp_paths).id == ""

    both_root = tmp_path / "both"
    bootsel_device_node(both_root, serial="AAAAAAAAAAAA")
    bootsel_device_node(both_root, serial="BBBBBBBBBBBB")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(both_root))
    assert flashers.bootsel.target_for("fw.uf2", chipset="rp2040", paths=rp_paths).id == ""


def test_bootsel_refuses_more_than_one_mounted_volume(paths, settings, tmp_path, monkeypatch):
    """No id to disambiguate with at all, unlike DFU's optional serial - so
    more than one RPI-RP2 volume at once is refused outright, same reasoning
    as `AmbiguousDfuError`."""
    media = tmp_path / "media"
    for user in ("alice", "bob"):
        vol = media / user / "RPI-RP2"
        vol.mkdir(parents=True)
        (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    monkeypatch.setattr(devices_mod, "DEFAULT_BOOTSEL_ROOT_GLOBS", (str(media / "*"),))

    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"\0")
    bench = flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(FlashError) as exc:
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))
    assert len(exc.value.data["mounts"]) == 2


# --------------------------------------------------------------------------
# the parser itself
#
# Every test above monkeypatches the listing out, so the parsing had no coverage
# at all. These use output captured verbatim from a real BTT EBB on a Pi running
# dfu-util 0.11.
# --------------------------------------------------------------------------

#: One physical board. dfu-util prints a line per DFU altsetting, so it is three
#: lines sharing devnum=51, path and serial.
_REAL_ONE_BOARD = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.
Copyright 2010-2021 Tormod Volden and Stefan Schmidt
This program is Free Software and has ABSOLUTELY NO WARRANTY
Please report bugs to http://sourceforge.net/p/dfu-util/tickets/

Found DFU: [0483:df11] ver=0200, devnum=51, cfg=1, intf=0, path="6-1.6.6.1.3", alt=2, name="@Internal Flash   /0x08000000/64*02Kg", serial="3941335F3434"
Found DFU: [0483:df11] ver=0200, devnum=51, cfg=1, intf=0, path="6-1.6.6.1.3", alt=1, name="@Internal Flash   /0x08000000/64*02Kg", serial="3941335F3434"
Found DFU: [0483:df11] ver=0200, devnum=51, cfg=1, intf=0, path="6-1.6.6.1.3", alt=0, name="@Internal Flash   /0x08000000/64*02Kg", serial="3941335F3434"
"""

#: Same board, same jumper, no udev rule.
_REAL_DENIED = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.
Copyright 2010-2021 Tormod Volden and Stefan Schmidt
This program is Free Software and has ABSOLUTELY NO WARRANTY
Please report bugs to http://sourceforge.net/p/dfu-util/tickets/

dfu-util: Cannot open DFU device 0483:df11 found on devnum 51 (LIBUSB_ERROR_ACCESS)
"""


def _fake_dfu_util(monkeypatch, stdout: str, stderr: str = "") -> None:
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        assert os.path.basename(cmd[0]) == "dfu-util"
        return sp.CompletedProcess(cmd, 0, stdout, stderr)

    # dfu_devices() itself lives in devices.py now; flash_mod re-exports the
    # name but no longer imports subprocess at all.
    monkeypatch.setattr(devices_mod.subprocess, "run", fake_run)


def test_one_board_with_three_altsettings_is_one_device(monkeypatch):
    """The bug that made every DFU flash impossible: counting lines instead of
    devices meant a single board looked like three and was refused as ambiguous."""
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD)
    assert len(flash_mod.list_dfu_devices()) == 1


def test_a_single_board_is_therefore_flashable(paths, ready, monkeypatch):
    """The end-to-end consequence: no AmbiguousDfuError for one board."""
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD)
    flash_dfu_stm32(paths, ready, str(paths.bin_file("board", "klipper")))


def test_two_real_boards_are_still_refused(monkeypatch):
    """Dedup must not go so far as to merge genuinely distinct boards."""
    second = _REAL_ONE_BOARD.replace('devnum=51', 'devnum=52').replace(
        'path="6-1.6.6.1.3"', 'path="6-1.6.6.1.4"'
    ).replace('serial="3941335F3434"', 'serial="OTHERSERIAL1"')
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD + second)
    assert len(flash_mod.list_dfu_devices()) == 2


def test_permission_denied_is_not_reported_as_no_device(monkeypatch):
    """"Hold BOOT0 and replug" is the worst possible advice here - the jumper was
    already right and nothing the user does at the board will help."""
    from mcu_updater.errors import DfuPermissionError

    _fake_dfu_util(monkeypatch, "", _REAL_DENIED)
    with pytest.raises(DfuPermissionError) as exc:
        flash_mod.list_dfu_devices()
    assert "permissions" in str(exc.value).lower()
    assert "udev" in str(exc.value).lower()


def test_a_genuinely_empty_listing_is_still_empty(monkeypatch):
    _fake_dfu_util(monkeypatch, "dfu-util 0.11\n\nNo DFU capable USB device available\n")
    assert flash_mod.list_dfu_devices() == []


def test_the_parser_does_not_depend_on_the_words_found_dfu(monkeypatch):
    """Matched on VID:PID, so a wording change cannot silently blind us."""
    _fake_dfu_util(
        monkeypatch,
        'Detected DFU: [0483:df11] devnum=7, path="1-2", alt=0, serial="ABC123"\n',
    )
    assert len(flash_mod.list_dfu_devices()) == 1


# --------------------------------------------------------------------------
# the exit code dfu-util returns after a *successful* :leave
# --------------------------------------------------------------------------

#: Captured verbatim from a real EBB flash. The write succeeded; dfu-util then
#: exited 74 because the board had already detached to run the new firmware.
_REAL_LEAVE_TRANSCRIPT = [
    "dfu-util: A valid DFU suffix will be required in a future dfu-util release",
    "Opening DFU capable USB device...",
    "Device ID 0483:df11",
    "Claiming USB DFU Interface...",
    "Performing mass erase, this can take a moment",
    "Downloading element to address = 0x08000000, size = 4720",
    "Download        [=========================] 100%         4720 bytes",
    "Download done.",
    "File downloaded successfully",
    "Submitting leave request...",
    "dfu-util: Error during download get_status",
]


def _fake_run_streamed(monkeypatch, rc: int, lines: list[str]) -> None:
    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return rc

    monkeypatch.setattr(flash_mod, "run_streamed", fake)


def test_the_exit_code_after_a_successful_leave_is_not_a_failure(paths, ready, monkeypatch):
    """`:leave` makes the board detach to boot the new firmware, so dfu-util's
    final status read cannot succeed and it exits 74. The flash worked."""
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD)
    _fake_run_streamed(monkeypatch, 74, _REAL_LEAVE_TRANSCRIPT)

    events: list[tuple[str, str]] = []
    flash_dfu_stm32(
        paths,
        ready,
        str(paths.bin_file("board", "klipper")),
        reporter=lambda s, line: events.append((s, line)),
    )
    assert any("expected" in line for _, line in events)


def test_a_real_dfu_failure_still_raises(paths, ready, monkeypatch):
    """No success marker, so nothing reached the board - must not be excused."""
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD)
    _fake_run_streamed(
        monkeypatch,
        74,
        ["Opening DFU capable USB device...", "dfu-util: Cannot open DFU device"],
    )
    with pytest.raises(FlashError):
        flash_dfu_stm32(paths, ready, str(paths.bin_file("board", "klipper")))


def test_a_download_that_succeeds_then_fails_unrecognisably_still_raises(
    paths, ready, monkeypatch
):
    """Being permissive only for the known leave artifact: an unfamiliar error
    after a good download is not something to wave through."""
    _fake_dfu_util(monkeypatch, _REAL_ONE_BOARD)
    _fake_run_streamed(
        monkeypatch,
        74,
        ["File downloaded successfully", "dfu-util: something nobody has seen before"],
    )
    with pytest.raises(FlashError):
        flash_dfu_stm32(paths, ready, str(paths.bin_file("board", "klipper")))


def test_the_board_and_screen_lookups_answer_in_the_same_shape():
    """`device_for`'s docstring says it was written "in the shape
    `esptool.port_for` already uses for displays". That claim is only worth
    anything while it stays true, and nothing else checks it - the two are
    called from different flashers and could drift apart silently, which is
    exactly how a display ended up reporting a literal null confidence while a
    board reported a real one.

    Both answer `(where, confidence, refusal reason)`, and both report a refusal
    rather than raising.
    """
    import inspect

    from mcu_updater.flashers.esptool import port_for
    from mcu_updater.flashers.flash import device_for

    def shape(fn):
        ret = inspect.signature(fn).return_annotation
        return [part.strip() for part in ret[len("tuple[") : -1].split(",")]

    board, screen = shape(device_for), shape(port_for)
    assert len(board) == len(screen) == 3
    # Slot 1 differs by kind - a BusDevice against a port string - but the two
    # that carry the verdict must not.
    assert board[1:] == screen[1:] == ["Confidence | None", "str | None"]
