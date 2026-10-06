"""`flashers.first_install`: a type's install family's `flashers:` list decides
whether a bare board can be set up, and with what. Never the builder, never a
chipset prefix in a caller. Pure and never raising - `fw.status` asks it for
every row on every poll."""

from __future__ import annotations

import dataclasses

import pytest

from mcu_updater import flashers
from mcu_updater.config import McuType
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import registry
from mcu_updater.flashers.spec import CandidateScan


@dataclasses.dataclass(frozen=True)
class Entry:
    name: str
    chipset: str
    firmwares: tuple[str, ...]


def fam(name, flashers_, *, bootloader=False, builder="kconfig_make"):
    return FirmwareFamily(name=name, builder=builder, flashers=tuple(flashers_), bootloader=bootloader)


BASE = {
    "klipper": fam("klipper", ["flashtool"]),
    "katapult": fam("katapult", ["dfu_util", "bootsel"], bootloader=True),
    "roadrunner": fam("roadrunner", ["bootsel"], builder="cmake"),
    "knomi_serial": fam("knomi_serial", ["platformio"], builder="platformio"),
}


@pytest.mark.parametrize(
    ("chipset", "flasher", "state"),
    [("stm32g0b1xx", "dfu_util", "dfu"), ("rp2040", "bootsel", "bootsel")],
)
def test_a_kconfig_type_with_katapult_installs_it(chipset, flasher, state):
    got = flashers.first_install(Entry("t", chipset, ("klipper", "katapult")), BASE)
    assert (got.fw, got.flasher, got.state, got.reason) == ("katapult", flasher, state, None)


def test_a_cmake_rp2040_installs_its_own_image_over_bootsel():
    got = flashers.first_install(Entry("roadrunner", "rp2040", ("roadrunner",)), BASE)
    assert (got.fw, got.flasher, got.state) == ("roadrunner", "bootsel", "bootsel")


def test_a_cmake_type_with_no_chipset_is_told_to_declare_one():
    got = flashers.first_install(Entry("roadrunner", "", ("roadrunner",)), BASE)
    assert got.flasher is None
    assert "chipset:" in got.reason


def test_a_pio_type_is_set_up_by_its_own_uploader():
    """No ROM flasher is involved: the upload tool resets the chip into its
    ROM by itself, so the family's one flasher both finds and writes it."""
    got = flashers.first_install(Entry("knomi", "esp32s3", ("knomi_serial",)), BASE)
    assert (got.fw, got.flasher, got.state, got.reason) == ("knomi_serial", "platformio", "esp_rom", None)


def test_a_pio_type_with_no_chipset_is_told_to_declare_one():
    """The uploader writes ESP32s, and a type that does not say it is one is
    not assumed to be."""
    got = flashers.first_install(Entry("knomi", "", ("knomi_serial",)), BASE)
    assert got.flasher is None
    assert "chipset:" in got.reason


def test_a_pio_type_of_a_chip_the_uploader_does_not_reset_is_not_set_up_by_it():
    got = flashers.first_install(Entry("pico_w", "rp2040", ("knomi_serial",)), BASE)
    assert got.flasher is None
    assert "flashers: platformio, bootsel" in got.reason


def test_a_type_nothing_can_scan_for_is_told_so():
    got = flashers.first_install(Entry("mega", "atmega2560", ("klipper",)), BASE)
    assert got.flasher is None
    assert "can scan for a new board" in got.reason
    assert "[firmware klipper]" in got.reason


def test_a_list_without_a_bare_writer_names_the_line_to_add():
    got = flashers.first_install(Entry("ebb", "stm32g0b1xx", ("klipper",)), BASE)
    assert got.flasher is None
    assert "flashers: flashtool, dfu_util" in got.reason


def test_an_mcutype_is_answered_too():
    mcu = McuType(name="ebb", chipset="stm32g0b1xx", firmwares=["klipper", "katapult"])
    assert flashers.first_install(mcu, BASE).flasher == "dfu_util"


def test_first_install_never_raises_on_a_missing_family():
    got = flashers.first_install(Entry("t", "rp2040", ("ghost",)), BASE)
    assert (got.fw, got.flasher) == ("ghost", None)
    assert "ghost" in got.reason


def test_first_install_never_raises_on_an_unknown_flasher_name():
    families = {**BASE, "katapult": fam("katapult", ["nosuch", "bootsel"], bootloader=True)}
    got = flashers.first_install(Entry("t", "rp2040", ("klipper", "katapult")), families)
    assert got.flasher == "bootsel"


def test_first_install_never_raises_on_no_firmwares():
    got = flashers.first_install(Entry("t", "rp2040", ()), BASE)
    assert got.flasher is None and got.fw == ""
    assert got.to_json() == {"fw": None, "flasher": None, "reason": got.reason, "hint": None}


def test_the_chosen_flasher_says_how_to_get_a_board_ready_for_it():
    """Before the scan, so the scan cannot be what says it. Each flasher's
    own sentence: holding BOOT is right for one and wrong for the next."""
    dfu = flashers.first_install(Entry("t", "stm32g0b1xx", ("klipper", "katapult")), BASE)
    pio = flashers.first_install(Entry("knomi", "esp32", ("knomi_serial",)), BASE)

    assert "DFU" in dfu.to_json()["hint"]
    assert "Nothing has to be held" in pio.to_json()["hint"]
    assert dfu.hint != pio.hint


def test_every_scanner_has_a_hint():
    for flasher in flashers.FLASHERS:
        scanner = flashers.candidate_scanner(flasher)
        if scanner is not None:
            assert scanner.candidate_hint.endswith("."), flasher.name


class _Stub:
    """A flasher that would write anything bare."""

    def __init__(self, name, *, scans):
        self.name = name
        self.states = ("dfu",)
        if scans:
            self.candidate_prefix = name
            self.candidate_hint = f"get it ready for {name}"
            self.scan_candidates = lambda paths, *, tracked, reporter, type_name=None: CandidateScan(
                False, None, None, []
            )

    def supports(self, device, helper):
        return True


def test_the_list_order_decides_between_two_scanners(monkeypatch):
    monkeypatch.setattr(registry, "_BY_NAME", {"b": _Stub("b", scans=True), "a": _Stub("a", scans=True)})
    got = flashers.first_install(Entry("t", "x", ("f",)), {"f": fam("f", ["b", "a"])})
    assert got.flasher == "b"


def test_a_flasher_that_cannot_scan_is_never_chosen(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_BY_NAME",
        {"writer": _Stub("writer", scans=False), "scanner": _Stub("scanner", scans=True)},
    )
    got = flashers.first_install(Entry("t", "x", ("f",)), {"f": fam("f", ["writer", "scanner"])})
    assert got.flasher == "scanner"
