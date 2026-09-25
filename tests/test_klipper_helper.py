"""The klipper helper: a running RP2040 into BOOTSEL, and back as Klipper.

Every hardware edge is monkeypatched, so none of this is posix_only. The
request's real behaviour - flashtool's `-r` exit code on a running RP2040,
and where a board with Katapult lands - is a bench item, not a test.
"""

from __future__ import annotations

import dataclasses
import os
import struct
import sys

import pytest

from mcu_updater import device_info, flashers
from mcu_updater.artifacts import KIND_UF2, Artifact
from mcu_updater.discovery.byid import BusDevice
from mcu_updater.errors import DeviceNotFoundError, FlashError
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
    bench, ctx, requested, tmp_path, monkeypatch
):
    """A board with Katapult answers Klipper's bootloader request with
    Katapult, not BOOTSEL. Waiting for a volume that is not coming would time
    out with the wrong reason; copying anywhere would be worse."""
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


def test_a_config_that_does_not_mention_the_chip_id_uses_it(paths):
    """`USB_SERIAL_NUMBER_CHIPID` defaults to y on the rp2040, so a minimal
    config that leaves it out still answers with the chip ID."""
    _config(paths, 'CONFIG_USB_SERIAL_NUMBER="12345"\n')

    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


def test_no_config_keeps_the_current_serial(paths):
    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


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
