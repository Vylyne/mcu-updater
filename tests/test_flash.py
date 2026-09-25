from __future__ import annotations

import contextlib
import dataclasses
import os

import pytest

from mcu_updater import devices as devices_mod
from mcu_updater import flashers
from mcu_updater.discovery.roadrunner import RoadrunnerError
from mcu_updater.errors import (
    AmbiguousDfuError,
    BootloaderTimeoutError,
    BootselNotMountedError,
    DeviceNotFoundError,
    FlashError,
    OffsetMismatchError,
    OperationCancelled,
    ToolMissingError,
    UnsupportedChipsetError,
)
from mcu_updater.flashers import flash as flash_mod
from mcu_updater.flashers import registry as flasher_registry
from mcu_updater.flashers.flash import (
    flash_dfu_stm32,
    flash_initial_bootloader,
    flash_katapult,
)
from mcu_updater.helpers import BootselHandoff
from mcu_updater.service import NullService

from .conftest import (
    bootsel_device_node,
    cmd_tokens,
    make_device,
    mounted_bootsel_volume,
    seed_base_firmwares,
)


def _cmds(events: list) -> list[str]:
    return [line for stream, line in events if stream == "cmd"]


@pytest.fixture
def ready(paths, settings, fake_root):
    """A staged firmware binary and an installed flashtool.py."""
    seed_base_firmwares(paths)
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
    seed_base_firmwares(paths)
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
    seed_base_firmwares(paths)
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


def _reboots_into_katapult(monkeypatch, bus_dir, chipset, serial, probe_lines, write_lines):
    """The real hardware sequence the probe triggers, in a fake bus.

    ``-s`` shares ``-f``'s handshake, which includes the app-to-bootloader
    transition, and has no finish step to jump back - so the Klipper by-id
    entry the probe was called with *disappears* and a katapult one takes its
    place, under a different name. Records every argv so a test can assert
    which path the write was actually addressed to.
    """
    klipper = make_device(bus_dir, "Klipper", chipset, serial)
    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        probing = "-s" in cmd
        if probing:
            klipper.unlink()
            make_device(bus_dir, "katapult", chipset, serial)
        rc, lines = (0, probe_lines) if probing else (0, write_lines)
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return rc

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    return calls


_REQUEST = "Requesting USB bootloader for /dev/serial/by-id/usb-Klipper_chipA_S1..."


def test_the_write_addresses_the_path_the_probe_left_the_board_on(
    paths, ready, fake_root, monkeypatch
):
    """The 2026-09-05 hardware failure. The probe rebooted an OctopusMAXEZ
    running Klipper into katapult, and the write was still addressed to the
    Klipper by-id path the probe had been handed - which no longer existed:
    "FlashError: No Serial Device found at ...usb-Klipper_...". Nothing was
    wrong with the board; the flash just never happened.
    """
    ready.dry_run = False
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _reboots_into_katapult(
        monkeypatch,
        fake_root / "bus",
        "chipA",
        "S1",
        probe_lines=[_REQUEST, "Application Start: 0x8004000"],
        write_lines=["Application Start: 0x8004000"],
    )

    flash_katapult(paths, ready, "board", "chipA", "S1")

    assert len(calls) == 2, "probe, then the write"
    probe_path = calls[0][calls[0].index("-d") + 1]
    write_path = calls[1][calls[1].index("-d") + 1]
    assert "Klipper" in probe_path, "the probe is addressed to the board as found"
    assert "katapult" in write_path, (
        f"the write must follow the board into its bootloader, got {write_path}"
    )


def test_a_board_that_never_reappears_after_the_probe_refuses_to_write(
    paths, ready, fake_root, monkeypatch
):
    """No fallback to the stale path - that fallback *is* the bug. Raised as
    `bootloader_timeout` rather than `device_not_found`: the board was seen,
    and was rebooted by us; "it never came back in its bootloader" is what
    happened, and it is not the same event as "nothing is plugged in". And the
    refusal has to say the board is in its bootloader, or an operator reads it
    as a brick."""
    ready.dry_run = False
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    klipper = make_device(fake_root / "bus", "Klipper", "chipA", "S1")
    calls: list[list[str]] = []

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        klipper.unlink(missing_ok=True)  # rebooted, and nothing comes back
        if reporter is not None:
            for line in (_REQUEST, "Application Start: 0x8004000"):
                reporter("stdout", line)
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    monkeypatch.setattr(flash_mod.time, "sleep", lambda _s: None)

    with pytest.raises(BootloaderTimeoutError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1", timeout=0)

    assert len(calls) == 1, "the write must not be attempted on a stale path"
    assert "katapult" in str(exc.value) and "re-run" in str(exc.value)


def test_a_probe_that_fails_after_rebooting_the_board_says_where_it_is(
    paths, ready, fake_root, monkeypatch
):
    """The reconnect is the flakiest step in the sequence, and it happens
    *after* the board has already been rebooted. A non-zero exit there is the
    failure most likely to be read as "my board is gone" - so the exit-code
    refusal has to account for the reboot too, not just the offset ones."""
    ready.dry_run = False
    make_device(fake_root / "bus", "Klipper", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(monkeypatch, probe=(1, [_REQUEST]))

    with pytest.raises(FlashError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1")

    assert len(calls) == 1, "the write must not run after a failed probe"
    assert "katapult now" in str(exc.value)


def test_a_probe_that_did_not_move_the_board_keeps_its_path(
    paths, ready, fake_root, monkeypatch
):
    """A board already in its bootloader is not rebooted by the probe, so
    there is nothing to re-resolve - and no re-enumeration wait to sit
    through."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    calls = _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),  # no bootloader request
        write=(0, ["Application Start: 0x8004000"]),
    )

    flash_katapult(paths, ready, "board", "chipA", "S1")

    assert calls[0][calls[0].index("-d") + 1] == calls[1][calls[1].index("-d") + 1]


def test_a_lingering_klipper_symlink_is_not_mistaken_for_the_rebooted_board(
    paths, ready, fake_root, monkeypatch
):
    """flashtool's "Waiting for USB Reconnect..done" covers USB enumeration,
    not udev: the board's now-dead Klipper by-id entry can still be sitting
    there when the probe returns, before udev catches up and swaps in the
    katapult one. Taking that first sighting writes to the stale path - the
    original bug, with an extra step. The re-resolve waits for katapult state.
    """
    ready.dry_run = False
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    bus = fake_root / "bus"
    klipper = make_device(bus, "Klipper", "chipA", "S1")
    calls: list[list[str]] = []

    def udev_catches_up(_seconds):
        klipper.unlink(missing_ok=True)
        make_device(bus, "katapult", "chipA", "S1")

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        calls.append(list(cmd))
        lines = [_REQUEST, "Application Start: 0x8004000"] if "-s" in cmd else []
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    # The probe returns with the stale entry still present; only the poll's
    # own sleep lets udev get there.
    monkeypatch.setattr(flash_mod.time, "sleep", udev_catches_up)

    flash_katapult(paths, ready, "board", "chipA", "S1")

    write_path = calls[1][calls[1].index("-d") + 1]
    assert "katapult" in write_path, (
        f"the stale Klipper entry was taken as the rebooted board: {write_path}"
    )


def test_the_re_resolve_poll_does_not_re_run_the_listen_pass(
    paths, ready, fake_root, monkeypatch
):
    """`device_for` defaults to the full `SOURCES` sweep, and `Listen.sight`
    in that sweep opens serial ports - a real pass per display family. One of
    those up front is the price of confirming identity at write time; running
    it again on every poll tick means up to thirty of them, at a bus that is
    mid-re-enumeration. The re-resolve asks `Byid` and nothing else."""
    from mcu_updater.discovery.knomi_serial.listen import Listen

    ready.dry_run = False
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    bus = fake_root / "bus"
    klipper = make_device(bus, "Klipper", "chipA", "S1")
    listens: list[int] = []
    monkeypatch.setattr(Listen, "sight", lambda self, bench: listens.append(1) or [])

    def udev_catches_up(_seconds):
        klipper.unlink(missing_ok=True)
        make_device(bus, "katapult", "chipA", "S1")

    def fake(cmd, *, cwd=None, reporter=None, dry_run=False, fake_delay=0.0, cancel=None):
        lines = [_REQUEST, "Application Start: 0x8004000"] if "-s" in cmd else []
        if reporter is not None:
            for line in lines:
                reporter("stdout", line)
        return 0

    monkeypatch.setattr(flash_mod, "run_streamed", fake)
    monkeypatch.setattr(flash_mod.time, "sleep", udev_catches_up)

    flash_katapult(paths, ready, "board", "chipA", "S1")

    assert len(listens) == 1, (
        f"the listen pass ran {len(listens)} times - the re-resolve poll is "
        f"sweeping every source, not just by-id"
    )


def test_a_refusal_after_a_reboot_says_where_the_board_is(
    paths, ready, fake_root, monkeypatch
):
    """Refusing is right, but the probe already moved the board to ask. The
    message has to account for that."""
    ready.dry_run = False
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _reboots_into_katapult(
        monkeypatch,
        fake_root / "bus",
        "chipA",
        "S1",
        probe_lines=[_REQUEST, "Application Start: 0x8000"],
        write_lines=[],
    )

    with pytest.raises(OffsetMismatchError) as exc:
        flash_katapult(paths, ready, "board", "chipA", "S1")

    assert "0x8004000" in str(exc.value) and "0x8000" in str(exc.value)
    assert "katapult now" in str(exc.value), (
        "the board was rebooted to ask and left there - say so"
    )


def test_a_real_flash_reports_unique_bus_id_confidence(paths, ready, fake_root, monkeypatch):
    """A by-id sighting is die-derived, not remembered, so a real write
    *reports* `unique_bus_id` for the loop to file - the board-side counterpart
    to a display's `answered` after a listen pass."""
    ready.dry_run = False
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    _write_sidecar(paths, "board", "klipper", app_address=0x08004000)
    _fake_run_streamed_by_call(
        monkeypatch,
        probe=(0, ["Application Start: 0x8004000"]),
        write=(0, ["Application Start: 0x8004000"]),
    )

    assert flash_katapult(paths, ready, "board", "chipA", "S1") == "unique_bus_id"


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
    seed_base_firmwares(paths)
    called = {}
    monkeypatch.setattr(
        flash_mod,
        "flash_dfu_stm32",
        lambda *a, **kw: called.setdefault("yes", True),
    )
    flash_initial_bootloader(
        paths, ready, "stm32f072xb", "x.bin", fw="katapult", mcu_type="board"
    )
    assert called == {"yes": True}


def test_rp2040_dispatches_to_bootsel_when_a_uf2_was_built(paths, settings, tmp_path):
    """Also the regression test for the target-shape bug this step fixed:
    `flash_initial_bootloader` used to build a DfuUtil-shaped target for every
    chipset, so handing one to Bootsel would `KeyError` on
    `target.detail["uf2_file"]` rather than copy anything."""
    seed_base_firmwares(paths)
    root = tmp_path / "bootsel_root"
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))

    uf2, cfg = _katapult_uf2(tmp_path)

    flash_initial_bootloader(
        rp_paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
        uf2_bin=uf2, katapult_config=cfg
    )

    assert (vol / "katapult.uf2").exists()


def _fake_uf2(payload: bytes = b"\x01" * 32, *, address: int = 0x10000000) -> bytes:
    """`payload` wrapped as a minimal, valid UF2 - real enough for
    `image_extent` to accept, split across as many 256-byte blocks as
    `payload` needs. `Bootsel.write` now reads every image once before
    copying it (M-3), so a fixture that used to be arbitrary bytes has to be
    a container `image_extent` can parse, even when the test has nothing to
    do with the image's own content."""
    import struct

    chunk = 256
    chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)] or [b""]
    out = bytearray()
    for index, data in enumerate(chunks):
        out += struct.pack(
            "<IIIIIIII",
            0x0A324655,
            0x9E5D5157,
            0x2000,
            address + index * chunk,
            chunk,
            index,
            len(chunks),
            0xE48BFF56,
        )
        out += data.ljust(476, b"\x00")
        out += struct.pack("<I", 0x0AB16F30)
    return bytes(out)


def _katapult_uf2(tmp_path, *, address="0x10004000"):
    """A one-page Katapult image at the start of flash, and its `.config`."""
    import struct

    block = struct.pack(
        "<8I", 0x0A324655, 0x9E5D5157, 0x2000, 0x10000000, 256, 0, 1, 0xE48BFF56
    )
    block += b"\xaa" * 256 + b"\0" * 220 + struct.pack("<I", 0x0AB16F30)
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(block)
    cfg = tmp_path / "katapult.config"
    lines = "CONFIG_MACH_RP2040=y\n"
    if address is not None:
        lines += f"CONFIG_LAUNCH_APP_ADDRESS={address}\n"
    cfg.write_text(lines, encoding="utf-8")
    return str(uf2), str(cfg)


def test_bootsel_copies_katapult_with_the_application_sector_erased(
    paths, settings, tmp_path
):
    """Parity with DFU's mass-erase. Without it a board that last ran other
    firmware keeps that image at the application address, Katapult chain-loads
    it, and the board never comes back as Katapult."""
    seed_base_firmwares(paths)
    from mcu_updater.uf2_erase import with_erased_sector

    root = tmp_path / "bootsel_root"
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2, cfg = _katapult_uf2(tmp_path)
    with open(uf2, "rb") as fh:
        original = fh.read()

    flash_initial_bootloader(
        rp_paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
        uf2_bin=uf2, katapult_config=cfg
    )

    assert (vol / "katapult.uf2").read_bytes() == with_erased_sector(original, 0x10004000)
    with open(uf2, "rb") as fh:
        assert fh.read() == original, "the built artifact itself must not change"


@pytest.mark.parametrize("address", [None, "nonsense"], ids=["absent", "unreadable"])
def test_bootsel_refuses_without_an_application_address(
    paths, settings, tmp_path, address
):
    """No address, no erase - and no silent fallback to copying Katapult alone,
    which is exactly the write that leaves a board chain-loading old firmware."""
    seed_base_firmwares(paths)
    root = tmp_path / "bootsel_root"
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2, cfg = _katapult_uf2(tmp_path, address=address)

    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            rp_paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
            uf2_bin=uf2, katapult_config=cfg
        )
    assert "LAUNCH_APP_ADDRESS" in str(exc.value)
    assert not (vol / "katapult.uf2").exists()


def test_bootsel_refuses_with_no_katapult_config(paths, settings, tmp_path):
    seed_base_firmwares(paths)
    uf2, _cfg = _katapult_uf2(tmp_path)
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
            uf2_bin=uf2,
        )
    assert "LAUNCH_APP_ADDRESS" in str(exc.value)


def test_bootsel_refuses_a_uf2_it_cannot_extend(paths, settings, tmp_path):
    """A corrupt artifact is a flash failure the caller can read, not a
    traceback out of the UF2 parser."""
    seed_base_firmwares(paths)
    _uf2, cfg = _katapult_uf2(tmp_path)
    bad = tmp_path / "bad.uf2"
    bad.write_bytes(b"\0" * 8)
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
            uf2_bin=str(bad), katapult_config=cfg
        )
    assert "UF2" in str(exc.value)


def test_bootsel_reports_a_missing_uf2_as_a_flash_error(paths, settings, tmp_path):
    seed_base_firmwares(paths)
    _uf2, cfg = _katapult_uf2(tmp_path)
    missing = str(tmp_path / "nope.uf2")
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
            uf2_bin=missing, katapult_config=cfg
        )
    assert missing in str(exc.value)


def test_rp2040_refuses_with_no_uf2_built(paths, settings):
    """A .bin copied to BOOTSEL mass storage is silently ignored - refusing
    outright is better than a write that appears to succeed and does nothing."""
    seed_base_firmwares(paths)
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "x.bin", fw="katapult", mcu_type="board"
        )
    assert ".uf2" in str(exc.value)


def test_dfu_refuses_with_no_bin_built(paths, settings):
    """A Katapult build can stage only a `.uf2`. DFU writes a `.bin`, so the
    first install names the missing build rather than the chipset."""
    seed_base_firmwares(paths)
    with pytest.raises(FlashError) as exc:
        flash_initial_bootloader(
            paths, settings, "stm32f072xb", None, fw="katapult", mcu_type="board",
            uf2_bin="x.uf2",
        )
    assert ".bin" in str(exc.value)


def test_an_unknown_chipset_is_reported_clearly(paths, ready):
    seed_base_firmwares(paths)
    with pytest.raises(UnsupportedChipsetError) as exc:
        flash_initial_bootloader(
            paths, ready, "esp32", "x.bin", fw="katapult", mcu_type="board"
        )
    assert exc.value.data["chipset"] == "esp32"


def test_first_install_writes_only_with_what_katapult_lists(paths, settings, tmp_path):
    """A bare board's flasher comes from [firmware katapult]'s `flashers:`.
    A katapult listing only dfu_util has nothing that writes an RP2040 in
    BOOTSEL, and refuses it the way an unknown chipset is refused."""
    seed_base_firmwares(paths)
    with open(paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert "flashers: dfu_util, bootsel" in text
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("flashers: dfu_util, bootsel", "flashers: dfu_util"))
    uf2, cfg = _katapult_uf2(tmp_path)

    with pytest.raises(UnsupportedChipsetError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "unused.bin", fw="katapult", mcu_type="board",
            uf2_bin=uf2, katapult_config=cfg
        )
    assert exc.value.data["chipset"] == "rp2040"


def _klipper_writes_bare_boards(paths) -> None:
    """`[firmware klipper]` with bootsel and dfu_util listed, as a type with no
    Katapult needs for its first install."""
    seed_base_firmwares(paths)
    with open(paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    section = "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n"
    assert section in text
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            text.replace(
                section,
                "[firmware klipper]\nsource: ~/klipper\n"
                "flashers: flashtool, bootsel, dfu_util\n",
            )
        )


def test_a_first_application_image_is_copied_as_built(paths, settings, tmp_path):
    """No erased sector: an application starts at the start of flash and
    overwrites what boots. No katapult_config is needed for it either."""
    _klipper_writes_bare_boards(paths)
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "klipper.uf2"
    image = _fake_uf2(b"\x01" * 32, address=0x10000000)
    uf2.write_bytes(image)

    flash_initial_bootloader(
        rp_paths, settings, "rp2040", None, fw="klipper", mcu_type="board",
        uf2_bin=str(uf2),
    )

    assert (vol / "klipper.uf2").read_bytes() == image


def test_a_first_application_uf2_built_for_an_offset_is_refused(paths, settings, tmp_path):
    """The backstop for the CLI, which has no pre-check of its own: nothing is
    copied, and the refusal names the rebuild."""
    from mcu_updater.errors import BareImageOffsetError

    _klipper_writes_bare_boards(paths)
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "klipper.uf2"
    uf2.write_bytes(_fake_uf2(b"\x01" * 32, address=0x10004000))

    with pytest.raises(BareImageOffsetError) as exc:
        flash_initial_bootloader(
            rp_paths, settings, "rp2040", None, fw="klipper", mcu_type="board",
            uf2_bin=str(uf2),
        )

    assert exc.value.code == "offset_mismatch"
    assert "0x10004000" in str(exc.value)
    assert "Rebuild board with no bootloader offset" in str(exc.value)
    assert not (vol / "klipper.uf2").exists()


def test_a_first_application_bin_is_checked_against_its_build_record(
    paths, settings, monkeypatch
):
    """A .bin carries no address of its own, so the sidecar's app_address has to
    say 0x08000000 - the address DFU writes at."""
    import json

    from mcu_updater.errors import BareImageOffsetError

    _klipper_writes_bare_boards(paths)
    written: list[str] = []
    monkeypatch.setattr(
        flash_mod, "flash_dfu_stm32", lambda p, s, fw_bin, **kw: written.append(fw_bin)
    )
    os.makedirs(paths.artifact_dir("board"), exist_ok=True)
    fw_bin = paths.bin_file("board", "klipper")
    with open(fw_bin, "wb") as fh:
        fh.write(b"\0" * 16)
    sidecar = paths.sidecar_file("board", "klipper")

    for address in (0x08002000, None):
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump({"app_address": address}, fh)
        with pytest.raises(BareImageOffsetError):
            flash_initial_bootloader(
                paths, settings, "stm32g0b1xx", fw_bin, fw="klipper", mcu_type="board"
            )
    assert written == []

    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump({"app_address": 0x08000000}, fh)
    flash_initial_bootloader(
        paths, settings, "stm32g0b1xx", fw_bin, fw="klipper", mcu_type="board"
    )
    assert written == [fw_bin]


def test_a_bootloader_first_image_is_not_offset_checked(paths, settings, monkeypatch):
    """Katapult is linked at the start of flash and records no app_address -
    the check is for an application with nothing below it, not for this."""
    seed_base_firmwares(paths)
    written: list[str] = []
    monkeypatch.setattr(
        flash_mod, "flash_dfu_stm32", lambda p, s, fw_bin, **kw: written.append(fw_bin)
    )

    flash_initial_bootloader(
        paths, settings, "stm32g0b1xx", "katapult.bin", fw="katapult", mcu_type="board"
    )
    assert written == ["katapult.bin"]


def test_no_bare_board_writer_on_the_family_names_the_line_to_add(paths, settings, tmp_path):
    """Not "flash katapult manually": the fix is the install family's list."""
    seed_base_firmwares(paths)
    uf2 = tmp_path / "klipper.uf2"
    uf2.write_bytes(_fake_uf2(address=0x10000000))

    with pytest.raises(UnsupportedChipsetError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", None, fw="klipper", mcu_type="board",
            uf2_bin=str(uf2),
        )

    assert "flashers: flashtool, bootsel" in str(exc.value)
    assert "[firmware klipper]" in str(exc.value)
    assert "katapult" not in str(exc.value).lower()
    assert exc.value.data["fw"] == "klipper"


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
    image = _fake_uf2(b"\x01" * 32)
    uf2.write_bytes(image)

    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")
    result = flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))

    assert (vol / "katapult.uf2").read_bytes() == image
    assert result["mount"] == str(vol)


def _volume_that_dies(monkeypatch, *, error, after_bytes):
    """Make the BOOTSEL volume go away underneath a copy.

    `after_bytes` is an int for a genuine mid-write death - that many bytes
    land and the next write fails - or None for the RP2040's normal ending,
    where every byte lands and the failure arrives afterwards, at close, as
    the boot ROM resets the board out from under the mount.
    """
    real_open = flashers.bootsel._open_dest

    class Handle:
        def __init__(self, fh, path):
            self._fh = fh
            self._path = path
            self._landed = 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

        def _check_unbuffered(self):
            # Pins the guarantee the classification rests on. If `_open_dest`
            # ever buffers again, bytes a `write` reported as taken are still
            # in Python's buffer when the loop ends, and a flush failure - an
            # incomplete image - becomes indistinguishable from the board
            # resetting after the last block. Every test using this fake fails
            # the moment that stops being true, which is the point.
            assert os.path.getsize(self._path) == self._landed, (
                "the destination handle must stay unbuffered"
            )

        def write(self, data):
            if after_bytes is None:
                written = self._fh.write(data)
                self._landed += written
                self._check_unbuffered()
                return written
            payload = bytes(data)[:after_bytes]
            if payload:
                self._landed += self._fh.write(payload)
                self._check_unbuffered()
            raise error

        def fileno(self):
            return self._fh.fileno()

        def close(self):
            with contextlib.suppress(OSError):
                self._fh.close()
            if after_bytes is None:
                raise error

    monkeypatch.setattr(
        flashers.bootsel, "_open_dest", lambda dest: Handle(real_open(dest), dest)
    )


def test_a_volume_that_vanishes_after_the_last_byte_is_a_successful_write(
    paths, settings, tmp_path, monkeypatch
):
    """On an RP2040 this is the *normal* ending, not an edge case.

    The boot ROM resets the board the instant the final UF2 block lands, so
    the volume is gone before anything after the data can run. Treating that
    as a failed copy would report every good flash as broken, skip the
    `FlashLog` record the operator needs, and invite a re-flash of a board
    that is already correct - the I2 hazard through a third door.
    """
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    image = _fake_uf2(b"every-byte-of-this-image")
    uf2.write_bytes(image)
    _volume_that_dies(
        monkeypatch, error=OSError(5, "Input/output error"), after_bytes=None
    )
    events: list[tuple[str, str]] = []
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert result == {"mount": str(vol)}
    assert (vol / "katapult.uf2").read_bytes() == image
    assert any(
        level == "info" and "Input/output error" in text for level, text in events
    )


def test_a_vanished_volume_reaches_flashed_so_provenance_can_record(
    paths, settings, tmp_path, monkeypatch
):
    """`write_all` records the `FlashLog` off a successful write, so the batch
    has to count this one as one - a failure row would silence the ledger for
    the most common successful ending there is."""
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(_fake_uf2(b"image"))
    _volume_that_dies(
        monkeypatch, error=OSError(5, "Input/output error"), after_bytes=None
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    result = flashers.write_all(
        bench, [target], flashers.PlainContext(lambda *a: None)
    )

    assert len(result["flashed"]) == 1
    assert result["failures"] == []
    assert result["flashed"][0]["mount"] == str(vol)


def test_a_copy_that_dies_partway_through_the_data_is_still_a_failure(
    paths, settings, tmp_path, monkeypatch
):
    """The other side of the same boundary: bytes that never landed are a
    genuine failure however few are missing, and must not be excused by the
    reset-at-the-end rule."""
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    image = _fake_uf2(b"half-written-image")
    uf2.write_bytes(image)
    _volume_that_dies(
        monkeypatch, error=OSError(28, "No space left on device"), after_bytes=4
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(FlashError) as exc:
        flashers.Bootsel().write(
            bench, None, target, flashers.PlainContext(lambda *a: None)
        )

    assert exc.value.code == "flash_failed"
    assert "No space left" in str(exc.value)
    assert (vol / "katapult.uf2").read_bytes() == image[:4]


def test_the_destination_handle_never_holds_bytes_back(tmp_path):
    """The unbuffered guarantee, on its own.

    `_copy_bytes` concludes "every byte was handed over" from the write loop
    ending. That is only true while nothing downstream is holding the tail: a
    buffered handle would flush it at close, and a failure there - a truncated
    image - would read as the benign board-reset ending.
    """
    dest = tmp_path / "probe.bin"

    with flashers.bootsel._open_dest(str(dest)) as out:
        out.write(b"ten-bytes!")
        assert dest.stat().st_size == 10


def test_a_multi_chunk_image_is_copied_whole(paths, settings, tmp_path, monkeypatch):
    """Real UF2 images run past `_COPY_CHUNK`, so the loop's second pass is a
    live path rather than a defensive one."""
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    image = _fake_uf2((b"UF2\n" * 4096) + os.urandom(flashers.bootsel._COPY_CHUNK))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(image)
    real_open = flashers.bootsel._open_dest
    sizes: list[int] = []

    class Counting:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self._fh.close()
            return False

        def fileno(self):
            return self._fh.fileno()

        def write(self, data):
            sizes.append(len(data))
            return self._fh.write(data)

    monkeypatch.setattr(
        flashers.bootsel, "_open_dest", lambda dest: Counting(real_open(dest))
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: None)
    )

    assert (vol / "katapult.uf2").read_bytes() == image
    assert len(sizes) > 1


def test_a_short_write_resumes_where_it_stopped(paths, settings, tmp_path, monkeypatch):
    """A `write` that takes only part of what it was offered is a normal
    outcome, not a failure - the loop has to resume from the byte it stopped
    at rather than dropping or repeating the remainder."""
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    image = _fake_uf2(bytes(range(256)) * 8)
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(image)
    real_open = flashers.bootsel._open_dest

    class ShortWriter:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self._fh.close()
            return False

        def fileno(self):
            return self._fh.fileno()

        def write(self, data):
            return self._fh.write(bytes(data)[:7])

    monkeypatch.setattr(
        flashers.bootsel, "_open_dest", lambda dest: ShortWriter(real_open(dest))
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: None)
    )

    assert (vol / "katapult.uf2").read_bytes() == image


def test_a_copy_onto_the_image_itself_is_refused_before_it_is_destroyed(
    paths, settings, tmp_path
):
    """`shutil.copy2` refused this; the explicit copy has to as well.

    Opening one inode for reading and for writing truncates it, the read then
    returns nothing, and the loop would finish "successfully" having reported
    a zero-byte file as a flashed image. Only reachable when a firmware
    directory overlaps `bootsel_root` - which nothing forbids - and the file
    it would destroy is the image the operator would re-flash with.
    """
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = vol / "katapult.uf2"
    image = _fake_uf2(b"the-only-copy-of-this-image")
    uf2.write_bytes(image)
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(FlashError) as exc:
        flashers.Bootsel().write(
            bench, None, target, flashers.PlainContext(lambda *a: None)
        )

    assert exc.value.code == "flash_failed"
    assert "onto itself" in str(exc.value)
    assert uf2.read_bytes() == image


def test_a_copy_that_dies_mid_write_is_a_structured_flash_failure(
    paths, settings, tmp_path, monkeypatch
):
    """The volume can go away underneath the copy.

    Unplugged mid-write, a full FAT volume, an I/O error on a board that reset
    early - all `OSError`. Raw, it escapes `write_all` entirely, past the
    Klipper readiness gate `on_ready` runs; the operator gets a traceback-shaped
    failure instead of a `flash_failed` one, and for a helper-requested BOOTSEL that
    happens with Klipper's services still down.
    """
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(_fake_uf2(b"image"))

    _volume_that_dies(
        monkeypatch, error=OSError(5, "Input/output error"), after_bytes=0
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(FlashError) as exc:
        flashers.Bootsel().write(
            bench, None, target, flashers.PlainContext(lambda *a: None)
        )

    assert exc.value.code == "flash_failed"
    assert "Input/output error" in str(exc.value)
    assert exc.value.data["mount"] == str(vol)


def test_a_copy_failure_still_reaches_the_klipper_readiness_gate(
    paths, settings, tmp_path, monkeypatch
):
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(_fake_uf2(b"image"))

    _volume_that_dies(
        monkeypatch, error=OSError(28, "No space left on device"), after_bytes=0
    )
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")
    ready: list[object] = []

    result = flashers.write_all(
        bench,
        [target],
        flashers.PlainContext(lambda *a: None),
        on_ready=lambda _reporter: ready.append(True),
    )

    assert result["flashed"] == []
    assert len(result["failures"]) == 1
    assert "No space left" in result["failures"][0]["error"]
    assert ready == [True]


def test_bootsel_dry_run_copies_nothing(paths, settings, tmp_path):
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    settings.dry_run = True

    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(_fake_uf2())

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
    uf2.write_bytes(_fake_uf2())

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
    uf2.write_bytes(_fake_uf2())
    bench = flashers.Bench(paths=rp_paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(BootselNotMountedError) as exc:
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))
    assert exc.value.data["devices"] == [node]
    message = exc.value.message.lower()
    assert "install.sh" in message or "udev" in message
    # The old fixed mountpoint is not where the current rule mounts, so it must
    # not be handed out as manual-mount instructions. Mirrors the same guard on
    # the agent-side message in test_agent_bootsel.py, so the two front ends'
    # advice cannot drift apart silently.
    assert "/media/<user>/rpi-rp2" not in message
    assert "bootsel/by-path" in message


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
    uf2.write_bytes(_fake_uf2())
    bench = flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    with pytest.raises(FlashError) as exc:
        flashers.Bootsel().write(bench, None, target, flashers.PlainContext(lambda *a: None))
    assert len(exc.value.data["mounts"]) == 2


def test_bootsel_handoff_requests_handoff_then_copies_only_to_matching_mount(
    paths, settings, tmp_path
):
    root = tmp_path / "bootsel_root"
    by_path = root / "BOOTSEL" / "by-path"
    matching = by_path / "platform-x_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    bystander = by_path / "platform-y_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    for mount in (matching, bystander):
        mount.mkdir(parents=True)
        (mount / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "roadrunner.uf2"
    image = _fake_uf2(b"road-runner")
    uf2.write_bytes(image)
    calls: list[tuple[str, str]] = []

    class Helper:
        name = "test"

        def request_bootsel(self, bench, *, serial, chipset, ctx):
            calls.append((serial, chipset))
            return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    target = flashers.bootsel.target_for(
        str(uf2),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
        stop_services=("klipper",),
    )

    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: None)
    )

    assert calls == [("RR-0123456789ABCDEFGHJKMNPQRS", "rp2040")]
    assert (matching / "roadrunner.uf2").read_bytes() == image
    assert not (bystander / "roadrunner.uf2").exists()
    assert result == {"mount": str(matching)}


#: Captured at import, before the autouse fixture swaps in boards that apply
#: instantly - the apply-wait tests below need the real presence check.
_REAL_VOLUME_STILL_MOUNTED = flashers.bootsel._volume_still_mounted


class _ApplyClock:
    """Stands in for `bootsel.time`. Sleeping advances the clock, and each
    marker in `leaves` is deleted once the clock reaches its time - the board
    resetting out from under its volume."""

    def __init__(self, leaves):
        self.now = 0.0
        self._leaves = dict(leaves)

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for marker, at in list(self._leaves.items()):
            if self.now >= at:
                marker.unlink()
                del self._leaves[marker]


def _real_apply_wait(monkeypatch, leaves=None):
    monkeypatch.setattr(
        flashers.bootsel, "_volume_still_mounted", _REAL_VOLUME_STILL_MOUNTED
    )
    clock = _ApplyClock(leaves or {})
    monkeypatch.setattr(flashers.bootsel, "time", clock)
    return clock


def _bootsel_bench_and_target(paths, settings, tmp_path, image=None):
    root, vol = mounted_bootsel_volume(tmp_path)
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(image if image is not None else _fake_uf2(b"image"))
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda name=None: None
    )
    return bench, flashers.bootsel.target_for(str(uf2), chipset="rp2040"), vol


def test_a_clean_copy_waits_for_the_board_to_take_the_image(
    paths, settings, tmp_path, monkeypatch
):
    """The copy returning only means the host has the bytes. Whatever waits
    for the board to re-enumerate runs after `write`, so `write` must not
    return until the volume has gone - on the bench that took most of a
    minute, and a re-enumerate timeout started at the copy ran out first."""
    bench, target, vol = _bootsel_bench_and_target(paths, settings, tmp_path)
    clock = _real_apply_wait(monkeypatch, {vol / "INFO_UF2.TXT": 45.0})
    events: list[tuple[str, str]] = []

    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert result == {"mount": str(vol)}
    assert clock.now >= 45.0
    assert not any(level == "warn" for level, _text in events)
    assert any("volume has gone" in text for _level, text in events)


def test_a_volume_that_never_goes_warns_after_the_full_wait_but_is_still_flashed(
    paths, settings, tmp_path, monkeypatch
):
    """The copy finished, so this is not a failed write - raising would invite
    a re-flash of a board that may yet come back. It is a warning, and the
    readiness check that follows is the verdict."""
    bench, target, vol = _bootsel_bench_and_target(paths, settings, tmp_path)
    clock = _real_apply_wait(monkeypatch)
    events: list[tuple[str, str]] = []

    result = flashers.write_all(
        bench, [target], flashers.PlainContext(lambda *a: events.append(a))
    )

    assert len(result["flashed"]) == 1
    assert result["failures"] == []
    assert 60.0 <= clock.now < 61.0
    assert any(
        level == "warn" and "still mounted 60s" in text and str(vol) in text
        for level, text in events
    )


def test_the_image_is_synced_to_the_volume_before_the_wait(
    paths, settings, tmp_path, monkeypatch
):
    """Without the `fsync` the image can sit in the page cache for the
    kernel's writeback delay, and the apply wait would be timing that instead
    of the board."""
    image = _fake_uf2(b"every-byte-of-this-image")
    bench, target, vol = _bootsel_bench_and_target(
        paths, settings, tmp_path, image=image
    )
    clock = _real_apply_wait(monkeypatch, {vol / "INFO_UF2.TXT": 1.0})
    synced: list[tuple[int, float]] = []
    monkeypatch.setattr(
        flashers.bootsel.os,
        "fsync",
        lambda fd: synced.append((os.fstat(fd).st_size, clock.now)),
    )

    flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: None)
    )

    assert synced == [(len(image), 0.0)]


def test_an_fsync_error_after_every_byte_is_judged_by_the_volume_going(
    paths, settings, tmp_path, monkeypatch
):
    """Forcing writeback on a volume the board has already reset away fails -
    that is why the sync was once left out. It is the late case, not a failed
    write, and the volume going away is what shows the image landed."""
    bench, target, vol = _bootsel_bench_and_target(paths, settings, tmp_path)
    clock = _real_apply_wait(monkeypatch, {vol / "INFO_UF2.TXT": 3.0})

    def fsync(_fd):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(flashers.bootsel.os, "fsync", fsync)
    events: list[tuple[str, str]] = []

    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert result == {"mount": str(vol)}
    assert clock.now >= 3.0
    assert not any(level == "warn" for level, _text in events)
    assert any(
        level == "info" and "Input/output error" in text for level, text in events
    )


def test_a_late_error_with_the_volume_still_there_warns_after_the_short_wait(
    paths, settings, tmp_path, monkeypatch
):
    """A board that had reset would already be gone, so a late error with the
    volume still there gets the short wait - and the warning carries the error,
    which is the one clue to a writeback that truncated the image."""
    bench, target, vol = _bootsel_bench_and_target(paths, settings, tmp_path)
    clock = _real_apply_wait(monkeypatch)

    def fsync(_fd):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(flashers.bootsel.os, "fsync", fsync)
    events: list[tuple[str, str]] = []

    flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert 10.0 <= clock.now < 11.0
    assert any(
        level == "warn"
        and "still mounted 10s" in text
        and "Input/output error" in text
        for level, text in events
    )


def test_bootsel_handoff_waits_for_its_own_volume_not_a_bystander(
    paths, settings, tmp_path, monkeypatch
):
    root = tmp_path / "bootsel_root"
    by_path = root / "BOOTSEL" / "by-path"
    matching = by_path / "platform-x_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    bystander = by_path / "platform-y_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    for mount in (matching, bystander):
        mount.mkdir(parents=True)
        (mount / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    uf2 = tmp_path / "roadrunner.uf2"
    uf2.write_bytes(_fake_uf2(b"road-runner"))
    clock = _real_apply_wait(monkeypatch, {matching / "INFO_UF2.TXT": 5.0})

    class Helper:
        name = "test"

        def request_bootsel(self, bench, *, serial, chipset, ctx):
            return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

    bench = flashers.Bench(
        paths=dataclasses.replace(paths, bootsel_root=str(root)),
        settings=settings,
        controller=lambda name=None: None,
    )
    target = flashers.bootsel.target_for(
        str(uf2),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    events: list[tuple[str, str]] = []

    flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *a: events.append(a))
    )

    assert 5.0 <= clock.now < 6.0
    assert not any(level == "warn" for level, _text in events)


def test_bootsel_handoff_targets_require_services_stopped(tmp_path):
    class Helper:
        name = "test"

    handoff = flashers.bootsel.target_for(
        str(tmp_path / "rr.uf2"),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
        stop_services=("klipper",),
    )
    bare = flashers.bootsel.target_for(str(tmp_path / "k.uf2"), chipset="rp2040")

    assert flashers.Bootsel.needs_services_stopped is False
    assert flashers.needs_services_stopped(handoff) is True
    assert flashers.needs_services_stopped(bare) is False


def test_bootsel_handoff_waits_for_helper_before_service_restart(
    paths, settings, tmp_path
):
    root = tmp_path / "bootsel_root"
    by_path = root / "BOOTSEL" / "by-path"
    matching = by_path / "platform-x_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    matching.mkdir(parents=True)
    (matching / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "roadrunner.uf2"
    uf2.write_bytes(_fake_uf2(b"road-runner"))
    order: list[str] = []

    class Service(NullService):
        def stop(self, reporter):
            order.append("stop")
            super().stop(reporter)

        def start(self, reporter):
            order.append("start")
            super().start(reporter)

    class Helper:
        name = "test"

        def request_bootsel(self, bench, *, serial, chipset, ctx):
            order.append("request")
            return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

        def wait_ready(self, bench, *, serial, chipset, ctx, type_name="", fw="", topology=""):
            order.append("ready")

    service = Service()
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda _name=None: service
    )
    target = flashers.bootsel.target_for(
        str(uf2),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
        stop_services=("klipper",),
    )

    result = flashers.write_all(
        bench, [target], flashers.PlainContext(lambda *a: None)
    )

    assert order == ["stop", "request", "ready", "start"]
    assert len(result["flashed"]) == 1
    assert result["failures"] == []


def test_a_whole_batch_still_succeeds_when_post_copy_readiness_fails(
    paths, settings, tmp_path
):
    """The end-to-end shape of the ruling: real Bootsel handoff, real write_all,
    a helper whose readiness wait raises. A completed copy is reported as
    flashed, nothing lands in failures, and the operator gets a warning."""
    root = tmp_path / "bootsel_root"
    by_path = root / "BOOTSEL" / "by-path"
    matching = by_path / "platform-x_usb-usb-0_1_3_1_0-scsi-0_0_0_0"
    matching.mkdir(parents=True)
    (matching / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    rp_paths = dataclasses.replace(paths, bootsel_root=str(root))
    uf2 = tmp_path / "roadrunner.uf2"
    image = _fake_uf2(b"road-runner")
    uf2.write_bytes(image)

    class Helper:
        name = "test"

        def request_bootsel(self, bench, *, serial, chipset, ctx):
            return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

        def wait_ready(self, bench, *, serial, chipset, ctx, type_name="", fw="", topology=""):
            raise RoadrunnerError(
                "More than one Roadrunner matched that serial",
                serial=serial,
            )

    events: list[tuple[str, str]] = []
    bench = flashers.Bench(
        paths=rp_paths, settings=settings, controller=lambda _name=None: NullService()
    )
    target = flashers.bootsel.target_for(
        str(uf2),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
        stop_services=("klipper",),
    )

    result = flashers.write_all(
        bench, [target], flashers.PlainContext(lambda kind, text: events.append((kind, text)))
    )

    assert len(result["flashed"]) == 1
    assert result["failures"] == []
    assert (matching / "roadrunner.uf2").read_bytes() == image
    # The bare message, not batch.py's "<id>: <message>" fallback: this pins
    # that `settled` itself absorbed it, not just that the job survived.
    assert ("warn", "More than one Roadrunner matched that serial") in events


def test_bootsel_handoff_settled_warns_when_helper_readiness_times_out(
    paths, settings
):
    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            raise AssertionError("settled must not request BOOTSEL again")

        def wait_ready(self, *_args, **_kwargs):
            raise BootloaderTimeoutError("Roadrunner did not become ready")

    target = flashers.bootsel.target_for(
        "roadrunner.uf2",
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    events: list[tuple[str, str]] = []
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda _name=None: None
    )

    flashers.Bootsel().settled(
        bench,
        target,
        flashers.PlainContext(lambda *event: events.append(event)),
    )

    assert events == [("warn", "Roadrunner did not become ready")]


@pytest.mark.parametrize(
    "error",
    [
        RoadrunnerError("Roadrunner INFO response was invalid"),
        RoadrunnerError("More than one Roadrunner matched that serial"),
        FlashError("could not read serial by-path topology"),
    ],
)
def test_bootsel_handoff_settled_warns_on_non_timeout_roadrunner_errors(
    paths, settings, error
):
    """The UF2 is already on the board by the time `settled` runs.

    The spec's error list is entirely pre-copy or at-copy; there is no
    post-write boundary, and the post-copy wait is non-fatal *in every
    outcome*. A readiness probe that fails - for whatever reason - is reported
    as a warning, never as a write that did not happen.
    """

    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            raise AssertionError("settled must not request BOOTSEL again")

        def wait_ready(self, *_args, **_kwargs):
            raise error

    target = flashers.bootsel.target_for(
        "roadrunner.uf2",
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    events: list[tuple[str, str]] = []
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda _name=None: None
    )

    flashers.Bootsel().settled(
        bench, target, flashers.PlainContext(lambda *event: events.append(event))
    )

    assert events == [("warn", str(error))]


def test_bootsel_handoff_settled_still_honours_cancellation(paths, settings):
    """Non-fatal covers readiness, not a cancelled job."""

    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            raise AssertionError("settled must not request BOOTSEL again")

        def wait_ready(self, *_args, **_kwargs):
            raise OperationCancelled("job cancelled")

    target = flashers.bootsel.target_for(
        "roadrunner.uf2",
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda _name=None: None
    )

    with pytest.raises(OperationCancelled):
        flashers.Bootsel().settled(
            bench, target, flashers.PlainContext(lambda *a: None)
        )


def test_a_failed_settle_is_never_counted_as_both_flashed_and_failed(
    paths, settings, tmp_path, monkeypatch
):
    """M1: a target appended to `flashed` must not also appear in `failures`.

    A consumer that sums both - as the panel's counts do - would report two
    outcomes for one board.
    """
    uf2 = tmp_path / "board.uf2"
    uf2.write_bytes(b"image")

    class Late:
        name = "late"
        label = "late"
        chipsets: tuple[str, ...] = ("rp2040",)
        states: tuple[str, ...] = ()
        needs_services_stopped = False

        @contextlib.contextmanager
        def prepared(self, bench, targets, ctx):
            yield None

        def write(self, bench, session, target, ctx):
            return {"mount": "/media/x"}

        def record(self, bench, target):
            return None

        def settled(self, bench, target, ctx):
            raise FlashError("the board came back slowly")

    target = flashers.FlashTarget(
        flasher="late", type="rp2040", id="board-1", detail={"uf2_file": str(uf2)}
    )
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda _name=None: None
    )
    events: list[tuple[str, str]] = []

    monkeypatch.setitem(flasher_registry._BY_NAME, "late", Late())
    result = flashers.write_all(
        bench, [target], flashers.PlainContext(lambda *event: events.append(event))
    )

    assert len(result["flashed"]) == 1
    assert result["failures"] == []
    assert ("warn", "board-1: the board came back slowly") in events


def test_bootsel_handoff_settled_skips_helper_readiness_in_dry_run(paths, settings):
    settings.dry_run = True
    waits: list[object] = []

    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            raise AssertionError("settled must not request BOOTSEL again")

        def wait_ready(self, *_args, **_kwargs):
            waits.append(True)

    target = flashers.bootsel.target_for(
        "roadrunner.uf2",
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda _name=None: None
    )

    flashers.Bootsel().settled(
        bench, target, flashers.PlainContext(lambda *a: None)
    )

    assert waits == []


def test_bootsel_handoff_dry_run_does_not_request_or_copy(
    paths, settings, tmp_path
):
    settings.dry_run = True
    uf2 = tmp_path / "roadrunner.uf2"
    uf2.write_bytes(_fake_uf2(b"road-runner"))
    requested: list[object] = []

    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            requested.append(True)
            raise AssertionError("dry run must not reboot hardware")

    target = flashers.bootsel.target_for(
        str(uf2),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    events: list[tuple[str, str]] = []
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda name=None: None
    )

    result = flashers.Bootsel().write(
        bench, None, target, flashers.PlainContext(lambda *event: events.append(event))
    )

    assert requested == []
    assert result == {"mount": None}
    assert any("dry-run" in line for _level, line in events)


def test_bootsel_handoff_refuses_a_missing_uf2_before_requesting_bootsel(
    paths, settings, tmp_path
):
    requested: list[object] = []

    class Helper:
        name = "test"

        def request_bootsel(self, *_args, **_kwargs):
            requested.append(True)
            raise AssertionError("a missing artifact must fail before BOOTSEL")

    target = flashers.bootsel.target_for(
        str(tmp_path / "missing.uf2"),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
    )
    bench = flashers.Bench(
        paths=paths, settings=settings, controller=lambda name=None: None
    )

    with pytest.raises(FlashError, match="firmware image not found"):
        flashers.Bootsel().write(
            bench, None, target, flashers.PlainContext(lambda *a: None)
        )

    assert requested == []


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
