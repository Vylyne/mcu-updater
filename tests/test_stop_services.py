"""The three-level `stop_services` resolver: override-never-merges, most
granular wins, per-provider default when nothing at all was set.
"""

from __future__ import annotations

from mcu_updater.config import McuType
from mcu_updater.providers.pio import PioType
from mcu_updater.settings import Settings
from mcu_updater.stop_services import (
    DEFAULT_DISPLAY,
    DEFAULT_MCU,
    for_display,
    for_mcu,
    resolve_stop_services,
)

# --------------------------------------------------------------------------
# the pure resolver
# --------------------------------------------------------------------------


def test_all_absent_falls_back_to_the_default():
    assert resolve_stop_services(None, None, None, default=("klipper",)) == ["klipper"]


def test_the_most_granular_level_wins_outright():
    assert resolve_stop_services(["a"], ["b"], ["c"], default=("d",)) == ["a"]


def test_a_middle_level_wins_when_the_first_is_absent():
    assert resolve_stop_services(None, ["b"], ["c"], default=("d",)) == ["b"]


def test_the_outermost_level_wins_when_only_it_is_set():
    assert resolve_stop_services(None, None, ["c"], default=("d",)) == ["c"]


def test_an_empty_list_wins_outright_and_is_not_treated_as_absent():
    """Blank is a real answer - "stop nothing" - not the same as not saying
    anything. It must not fall through to a less granular level."""
    assert resolve_stop_services([], ["b"], ["c"], default=("d",)) == []


def test_nothing_is_merged_from_a_less_granular_level():
    """The whole point of override-never-merges: a granular list wins
    outright, even though it omits something a less granular level named."""
    assert resolve_stop_services(["a"], ["a", "b"], None, default=()) == ["a"]


# --------------------------------------------------------------------------
# for_mcu / for_display: the convenience wrappers
# --------------------------------------------------------------------------


def test_for_mcu_falls_back_to_the_mcu_default(paths):
    mcu = McuType(name="bttebb36", firmwares=["klipper"])
    settings = Settings()
    assert for_mcu(paths, mcu, settings) == DEFAULT_MCU


def test_for_mcu_honours_a_type_level_override(paths):
    mcu = McuType(name="bttebb36", firmwares=["klipper"], stop_services=["klipper-1"])
    settings = Settings()
    assert for_mcu(paths, mcu, settings) == ("klipper-1",)


def test_for_mcu_honours_an_updater_level_override(paths):
    mcu = McuType(name="bttebb36", firmwares=["klipper"])
    settings = Settings(stop_services=["klipper-1"])
    assert for_mcu(paths, mcu, settings) == ("klipper-1",)


def test_for_mcu_type_level_beats_updater_level(paths):
    mcu = McuType(name="bttebb36", firmwares=["klipper"], stop_services=["klipper"])
    settings = Settings(stop_services=["klipper-1"])
    assert for_mcu(paths, mcu, settings) == ("klipper",)


def test_for_mcu_type_level_blank_stops_nothing(paths):
    mcu = McuType(name="bttebb36", firmwares=["klipper"], stop_services=[])
    settings = Settings()
    assert for_mcu(paths, mcu, settings) == ()


def test_for_display_falls_back_to_the_display_default(paths):
    display = PioType(name="knomi", env="knomi", firmware="knomi_serial")
    settings = Settings()
    assert for_display(paths, display, settings) == DEFAULT_DISPLAY


def test_for_display_honours_a_type_level_override(paths):
    display = PioType(
        name="knomi", env="knomi", firmware="knomi_serial", stop_services=["klipper"]
    )
    settings = Settings()
    assert for_display(paths, display, settings) == ("klipper",)
