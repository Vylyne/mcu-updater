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
    return FirmwareFamily(
        name=name, builder=builder, flashers=tuple(flashers_), bootloader=bootloader
    )


BASE = {
    "klipper": fam("klipper", ["flashtool"]),
    "katapult": fam("katapult", ["dfu_util", "bootsel"], bootloader=True),
    "roadrunner": fam("roadrunner", ["bootsel"], builder="cmake"),
    "knomi_serial": fam("knomi_serial", ["esptool"], builder="platformio"),
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


def test_a_pio_type_is_told_nothing_can_scan_for_it():
    got = flashers.first_install(Entry("knomi", "esp32", ("knomi_serial",)), BASE)
    assert got.flasher is None
    assert "can scan for a new board" in got.reason
    assert "[firmware knomi_serial]" in got.reason


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
    assert got.to_json() == {"fw": None, "flasher": None, "reason": got.reason}


class _Stub:
    """A flasher that would write anything bare."""

    def __init__(self, name, *, scans):
        self.name = name
        self.states = ("dfu",)
        if scans:
            self.candidate_prefix = name
            self.scan_candidates = lambda paths, *, tracked, reporter: CandidateScan(
                False, None, None, []
            )

    def supports(self, device, helper):
        return True


def test_the_list_order_decides_between_two_scanners(monkeypatch):
    monkeypatch.setattr(
        registry, "_BY_NAME", {"b": _Stub("b", scans=True), "a": _Stub("a", scans=True)}
    )
    got = flashers.first_install(Entry("t", "x", ("f",)), {"f": fam("f", ["b", "a"])})
    assert got.flasher == "b"


def test_a_flasher_that_cannot_scan_is_never_chosen(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_BY_NAME",
        {"writer": _Stub("writer", scans=False), "scanner": _Stub("scanner", scans=True)},
    )
    got = flashers.first_install(
        Entry("t", "x", ("f",)), {"f": fam("f", ["writer", "scanner"])}
    )
    assert got.flasher == "scanner"
