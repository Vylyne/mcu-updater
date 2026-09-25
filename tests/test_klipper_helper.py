"""The klipper helper: a running RP2040 into BOOTSEL, and back as Klipper.

Every hardware edge is monkeypatched, so none of this is posix_only. The
request's real behaviour - flashtool's `-r` exit code on a running RP2040,
and where a board with Katapult lands - is a bench item, not a test.
"""

from __future__ import annotations

import dataclasses
import os
import re
import struct
import sys

import pytest

from mcu_updater import device_info, flashers
from mcu_updater.artifacts import KIND_UF2, Artifact
from mcu_updater.discovery.byid import BusDevice
from mcu_updater.errors import BootloaderTimeoutError, DeviceNotFoundError, FlashError
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import bootsel as bootsel_flasher
from mcu_updater.helpers import BootselHandoff, klipper
from mcu_updater.helpers.klipper import KlipperHelper, predicted_serial

SERIAL = "E66138935F1234AB-if00"
TOPOLOGY = "platform-3f980000.usb-usb-0:1.2"
RUNNING = BusDevice("Klipper", "rp2040", SERIAL, f"/dev/serial/by-id/usb-Klipper_rp2040_{SERIAL}")
IN_KATAPULT = BusDevice("katapult", "rp2040", SERIAL, f"/dev/serial/by-id/usb-katapult_rp2040_{SERIAL}")


@pytest.fixture
def bench(paths, settings):
    return flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)


@pytest.fixture
def reported():
    return []


@pytest.fixture
def ctx(reported):
    return flashers.PlainContext(lambda stream, line: reported.append((stream, line)))


@pytest.fixture
def flashtool(tmp_path, monkeypatch):
    path = tmp_path / "flashtool.py"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr("mcu_updater.flashers.flash.find_flashtool", lambda paths, settings: str(path))
    return str(path)


@pytest.fixture
def requested(monkeypatch, flashtool):
    """A running Klipper RP2040 on TOPOLOGY. Returns every argv run."""
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        return 0

    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: RUNNING)
    monkeypatch.setattr(klipper.bootsel, "serial_topology_for", lambda paths, port: TOPOLOGY)
    monkeypatch.setattr(klipper.build_mod, "run_streamed", run)
    monkeypatch.setattr(klipper, "REQUEST_TIMEOUT", 0.0)
    return calls


def _config(paths, text: str) -> None:
    path = paths.config_file("pico", "klipper")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


HARDCODED = '# CONFIG_USB_SERIAL_NUMBER_CHIPID is not set\nCONFIG_USB_SERIAL_NUMBER="12345"\n'
CHIP_ID = 'CONFIG_USB_SERIAL_NUMBER_CHIPID=y\nCONFIG_USB_SERIAL_NUMBER="12345"\n'


# --- request_bootsel ---------------------------------------------------------


def test_a_running_board_is_asked_into_bootsel_through_flashtool(bench, ctx, requested, flashtool, monkeypatch):
    monkeypatch.setattr(klipper.bootsel, "mounts_on", lambda paths, topology: ["/media/pi/RPI-RP2"])

    handoff = KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)

    assert handoff == BootselHandoff(topology=TOPOLOGY)
    assert requested == [[sys.executable, flashtool, "-d", RUNNING.path, "-r"]]


def test_a_board_that_lands_in_katapult_is_refused_and_nothing_is_written(
    bench, ctx, requested, paths, tmp_path, monkeypatch
):
    """A board with Katapult answers Klipper's bootloader request with
    Katapult, not BOOTSEL. Waiting for a volume that is not coming would time
    out with the wrong reason; copying anywhere would be worse."""
    bench = dataclasses.replace(
        bench, paths=dataclasses.replace(paths, bootsel_root=str(tmp_path / "empty"))
    )
    monkeypatch.setattr(klipper.bootsel, "mounts_on", lambda paths, topology: [])
    monkeypatch.setattr(klipper.byid, "scan", lambda paths: [IN_KATAPULT])
    copied: list[str] = []
    monkeypatch.setattr(bootsel_flasher, "copy_uf2", lambda uf2, mount, ctx: copied.append(mount))
    target = bootsel_flasher.target_for(
        Artifact(KIND_UF2, _uf2(tmp_path, 0x10000000)),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )

    with pytest.raises(FlashError, match="Katapult"):
        bootsel_flasher.Bootsel().write(bench, None, target, ctx)

    assert copied == []


def test_a_board_already_in_katapult_is_refused_before_any_request(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: IN_KATAPULT)

    with pytest.raises(FlashError, match="flashtool before bootsel"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)

    assert requested == []


def test_a_chipset_that_is_not_an_rp2040_is_refused(bench, ctx, requested):
    with pytest.raises(FlashError, match="rp2040"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="stm32g0b1xx", ctx=ctx)

    assert requested == []


def test_a_board_that_is_not_on_usb_is_not_found(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: None)

    with pytest.raises(DeviceNotFoundError):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)


def test_a_failed_request_is_a_flash_error(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.build_mod, "run_streamed", lambda argv, **kwargs: 2)

    with pytest.raises(FlashError, match="exited 2"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)


# --- wait_ready --------------------------------------------------------------


@pytest.fixture
def waited(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def wait(paths, chipset, serial, fw, **kwargs):
        calls.append((chipset, serial, fw))
        return RUNNING

    monkeypatch.setattr(klipper.byid, "wait_for_device", wait)
    return calls


def test_wait_ready_follows_a_hardcoded_serial(bench, ctx, reported, paths, waited):
    """Review Focus 5: the new image answers to CONFIG_USB_SERIAL_NUMBER, so
    waiting for the old serial would time out on a board that came back fine."""
    _config(paths, HARDCODED)

    KlipperHelper().wait_ready(bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper")

    assert waited == [("rp2040", "12345-if00", "Klipper")]
    assert any(stream == "warn" and "12345-if00" in line for stream, line in reported)


def test_wait_ready_keeps_a_chip_id_serial(bench, ctx, reported, paths, waited):
    _config(paths, CHIP_ID)

    KlipperHelper().wait_ready(bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper")

    assert waited == [("rp2040", SERIAL, "Klipper")]
    assert not [line for stream, line in reported if stream == "warn"]


# --- wait_ready by topology (I-2) ---------------------------------------------


@pytest.fixture
def fast_topology(monkeypatch):
    """No real waiting in the topology poll/settle loop."""
    monkeypatch.setattr(klipper, "_TOPOLOGY_POLL", 0.0)
    monkeypatch.setattr(klipper, "_TOPOLOGY_SETTLE", 0.0)


def _by_topology(monkeypatch, devices, matches: dict[str, str]):
    """`byid.scan` answers `devices`; `serial_topology_for` answers `matches`
    keyed by device path, raising `FlashError` for a path missing from it."""
    monkeypatch.setattr(klipper.byid, "scan", lambda paths: devices)

    def topology_for(paths, path):
        if path not in matches:
            raise FlashError(f"no by-path entry for {path}")
        return matches[path]

    monkeypatch.setattr(klipper.bootsel, "serial_topology_for", topology_for)


def test_wait_ready_finds_the_board_by_the_port_it_was_asked_on(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    found = BusDevice("Klipper", "rp2040", SERIAL, RUNNING.path)
    _by_topology(monkeypatch, [found], {found.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    assert not [line for stream, line in reported if stream == "warn"]


def test_wait_ready_warns_when_the_board_on_that_port_answers_a_different_serial(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    """A chip-ID config, so no early notice fires - the board is tracked
    under a literal (fixed) serial, and the one actually found by topology
    is the news."""
    _config(paths, CHIP_ID)
    other = BusDevice(
        "Klipper", "rp2040", "OTHERSERIAL-if00", "/dev/serial/by-id/usb-Klipper_rp2040_OTHERSERIAL-if00"
    )
    _by_topology(monkeypatch, [other], {other.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 1
    assert SERIAL in warnings[0] and "OTHERSERIAL-if00" in warnings[0]
    assert "came back as" in warnings[0]


def test_wait_ready_does_not_accept_a_serial_collision_from_another_port(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    """A board presenting the *predicted literal* serial on some other port
    is a collision, not a match - only the board that answers on the port
    this one was asked on may settle the wait, whatever serial it turns out
    to have."""
    _config(paths, HARDCODED)
    decoy = BusDevice(
        "Klipper", "rp2040", "12345-if00", "/dev/serial/by-id/usb-Klipper_rp2040_12345-if00"
    )
    real = BusDevice(
        "Klipper", "rp2040", "reallive-if00", "/dev/serial/by-id/usb-Klipper_rp2040_reallive-if00"
    )
    _by_topology(monkeypatch, [decoy, real], {decoy.path: "some-other-topology", real.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 2
    assert "12345-if00" in warnings[0]
    assert SERIAL in warnings[1] and "reallive-if00" in warnings[1]


def test_wait_ready_does_not_repeat_the_early_notice_when_the_board_found_matches_it(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    """The literal-serial early notice already named `12345-if00`; the board
    found by topology answering to exactly that serial is not news twice."""
    _config(paths, HARDCODED)
    found = BusDevice(
        "Klipper", "rp2040", "12345-if00", "/dev/serial/by-id/usb-Klipper_rp2040_12345-if00"
    )
    _by_topology(monkeypatch, [found], {found.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 1
    assert "12345-if00" in warnings[0]


def test_wait_ready_warns_again_when_the_board_found_matches_neither_prediction(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    """The early notice named `12345-if00`; the board that actually turned up
    on the port is a third serial, neither the tracked one nor the predicted
    one - still worth a second, differently-worded warning."""
    _config(paths, HARDCODED)
    found = BusDevice(
        "Klipper", "rp2040", "surprise-if00", "/dev/serial/by-id/usb-Klipper_rp2040_surprise-if00"
    )
    _by_topology(monkeypatch, [found], {found.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 2
    assert "12345-if00" in warnings[0]
    assert SERIAL in warnings[1] and "surprise-if00" in warnings[1]


def test_wait_ready_tolerates_a_device_whose_topology_lookup_fails(
    bench, ctx, reported, paths, fast_topology, monkeypatch
):
    """The same tolerance `_katapult_on` has for a device unplugged mid-scan:
    one bad lookup does not abort the search over every other board on the
    bus - and it must not be mistaken for a match either."""
    bad = BusDevice("Klipper", "rp2040", "bad-if00", "/dev/serial/by-id/usb-Klipper_rp2040_bad-if00")
    good = BusDevice("Klipper", "rp2040", SERIAL, RUNNING.path)
    _by_topology(monkeypatch, [bad, good], {good.path: TOPOLOGY})

    KlipperHelper().wait_ready(
        bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
    )

    assert not [line for stream, line in reported if stream == "warn"]


def test_wait_ready_times_out_the_same_way_wait_for_device_does(
    bench, ctx, paths, fast_topology, monkeypatch
):
    _by_topology(monkeypatch, [], {})
    monkeypatch.setattr(klipper, "REENUMERATE_TIMEOUT", 0.0)

    with pytest.raises(BootloaderTimeoutError):
        KlipperHelper().wait_ready(
            bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper", topology=TOPOLOGY
        )


def test_a_config_that_does_not_mention_the_chip_id_uses_it(paths):
    """`USB_SERIAL_NUMBER_CHIPID` defaults to y on the rp2040, so a minimal
    config that leaves it out still answers with the chip ID."""
    _config(paths, 'CONFIG_USB_SERIAL_NUMBER="12345"\n')

    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


def test_no_config_keeps_the_current_serial(paths):
    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


def test_a_config_that_is_not_valid_utf8_keeps_the_current_serial(paths):
    """A `.config` a build tool half-wrote, or one with a stray non-UTF-8
    byte, reads the same as a missing one - not a crash on a board that would
    otherwise flash cleanly."""
    path = paths.config_file("pico", "klipper")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b'CONFIG_USB_SERIAL_NUMBER_CHIPID=y\n\xffCONFIG_USB_SERIAL_NUMBER="12345"\n')

    assert predicted_serial(path, current=SERIAL) == SERIAL


def test_a_klipper_family_with_the_helper_still_reads_boards_through_klipper():
    """The README puts `helper: klipper` on a whole family. It has no
    `DeviceInfoReader`, so every board in it must keep Klipper's reader, not
    lose its status verdict."""
    plain = FirmwareFamily(name="klipper", flashers=("flashtool",))
    helped = FirmwareFamily(name="klipper", flashers=("bootsel",), helper="klipper")

    assert type(device_info.reader_for(helped)) is type(device_info.reader_for(plain))


def test_settled_hands_the_helper_the_type_and_family(bench, ctx, paths, waited):
    _config(paths, HARDCODED)
    target = bootsel_flasher.target_for(
        Artifact(KIND_UF2, "/unused.uf2"),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )

    bootsel_flasher.Bootsel().settled(bench, target, ctx)

    assert waited == [("rp2040", "12345-if00", "Klipper")]


# --- the offset warning ------------------------------------------------------


def _uf2(tmp_path, address: int):
    block = (
        struct.pack("<IIIIIIII", 0x0A324655, 0x9E5D5157, 0x2000, address, 256, 0, 1, 0xE48BFF56)
        + b"\x00" * 476
        + struct.pack("<I", 0x0AB16F30)
    )
    path = tmp_path / f"image-{address:x}.uf2"
    path.write_bytes(block)
    return str(path)


def test_an_image_above_the_start_of_flash_is_warned_about(bench, ctx, reported, tmp_path):
    rehearsal = dataclasses.replace(bench, settings=dataclasses.replace(bench.settings, dry_run=True))
    target = bootsel_flasher.target_for(_uf2(tmp_path, 0x10004000), chipset="rp2040")

    bootsel_flasher.Bootsel().write(rehearsal, None, target, ctx)

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 1
    assert "0x10004000" in warnings[0]
    assert "bootloader" in warnings[0]


def test_an_image_at_the_start_of_flash_is_not(bench, ctx, reported, tmp_path):
    rehearsal = dataclasses.replace(bench, settings=dataclasses.replace(bench.settings, dry_run=True))
    target = bootsel_flasher.target_for(_uf2(tmp_path, 0x10000000), chipset="rp2040")

    bootsel_flasher.Bootsel().write(rehearsal, None, target, ctx)

    assert not [line for stream, line in reported if stream == "warn"]


# --- the offset refusal, on the helper path (I-1) -----------------------------


def _helper_target(tmp_path, address: int):
    return bootsel_flasher.target_for(
        Artifact(KIND_UF2, _uf2(tmp_path, address)),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )


def test_an_offset_image_is_refused_on_the_helper_path_before_any_request(
    bench, ctx, requested, paths, tmp_path
):
    """A board asked for BOOTSEL by its own firmware has no Katapult below it
    to boot an offset image - unlike a board already sitting in BOOTSEL,
    which this only warns about. The refusal has to land before the board is
    ever asked to reboot.

    A tmp `bootsel_root`, so a weakened refusal falls through to a mount scan
    of a fake root rather than the real host's `/media` (M-5's own fix,
    applied here defensively too)."""
    bench = dataclasses.replace(
        bench, paths=dataclasses.replace(paths, bootsel_root=str(tmp_path / "empty"))
    )
    target = _helper_target(tmp_path, 0x10004000)

    with pytest.raises(FlashError, match="bootloader offset"):
        bootsel_flasher.Bootsel().write(bench, None, target, ctx)

    assert requested == []


def test_an_offset_image_is_refused_on_the_helper_path_even_on_a_dry_run(bench, ctx, requested, tmp_path):
    rehearsal = dataclasses.replace(bench, settings=dataclasses.replace(bench.settings, dry_run=True))
    target = _helper_target(tmp_path, 0x10004000)

    with pytest.raises(FlashError, match="bootloader offset"):
        bootsel_flasher.Bootsel().write(rehearsal, None, target, ctx)

    assert requested == []


# --- a corrupt image, on both paths (M-3) -------------------------------------


def _garbage_uf2(tmp_path) -> str:
    path = tmp_path / "garbage.uf2"
    path.write_bytes(b"this is not a uf2 image, just garbage bytes")
    return str(path)


def test_a_corrupt_image_is_refused_by_name_on_the_helper_path(bench, ctx, requested, paths, tmp_path):
    bench = dataclasses.replace(
        bench, paths=dataclasses.replace(paths, bootsel_root=str(tmp_path / "empty"))
    )
    garbage = _garbage_uf2(tmp_path)
    target = bootsel_flasher.target_for(
        Artifact(KIND_UF2, garbage),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )

    with pytest.raises(FlashError, match=re.escape(garbage)):
        bootsel_flasher.Bootsel().write(bench, None, target, ctx)

    assert requested == []


def test_a_corrupt_image_is_refused_by_name_on_the_no_helper_path(bench, ctx, tmp_path):
    garbage = _garbage_uf2(tmp_path)
    target = bootsel_flasher.target_for(garbage, chipset="rp2040")

    with pytest.raises(FlashError, match=re.escape(garbage)):
        bootsel_flasher.Bootsel().write(bench, None, target, ctx)
