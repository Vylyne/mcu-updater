"""The one type list: every [type] section, whatever builds it.

Three loaders used to walk these sections and keep only the builder each knew.
The CLI used the kconfig one, so a Roadrunner type existed for the agent and
not for `status`. These tests pin the single walk and prove the three
remaining loaders are views over it.
"""

from __future__ import annotations

import pytest

from mcu_updater import providers, typelist
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.providers import cmake as cmake_mod
from mcu_updater.providers import pio as pio_mod

from .conftest import write_main_config

FAMILIES = (
    "[firmware klipper]\nsource: ~/klipper\n\n"
    "[firmware katapult]\nsource: ~/katapult\n\n"
    "[firmware knomi_serial]\nsource: ~/knomi_serial\nbuilder: platformio\n\n"
    "[firmware roadrunner]\nsource: ~/rr\nbuilder: cmake\n\n"
)

EXAMPLE_ORDER = [
    "mmb_can",
    "bttebb36",
    "flylllplusbuffer",
    "hexadistrofusion",
    "OctopusMAXEZ",
    "cartographer",
    "knomi",
    "roadrunner",
]


def test_every_type_is_listed_in_file_order(paths, example_registry_text):
    write_main_config(paths, example_registry_text)
    assert [entry.name for entry in typelist.load(paths)] == EXAMPLE_ORDER


def test_each_entry_carries_the_builder_of_its_family(paths, example_registry_text):
    write_main_config(paths, example_registry_text)
    builders = {entry.name: entry.builder for entry in typelist.load(paths)}
    assert builders["roadrunner"] == "cmake"
    assert builders["knomi"] == "platformio"
    assert builders["bttebb36"] == "kconfig_make"


def test_a_cmake_type_keeps_its_identity_and_its_own_block(paths, example_registry_text):
    write_main_config(paths, example_registry_text)
    rr = next(entry for entry in typelist.load(paths) if entry.name == "roadrunner")
    assert rr.firmwares == ("roadrunner",)
    assert rr.serials == ("RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ",)
    assert rr.block.has("cmake_target")
    assert not rr.block.has("platformio_env")


def test_read_never_refuses():
    doc = CfgDocument("[type orphan]\nfirmware: nowhere\n\n[type bare]\nchipset: x\n")
    entries = typelist.read(doc, {})
    assert [(e.name, e.firmwares, e.builder) for e in entries] == [
        ("orphan", ("nowhere",), "kconfig_make"),
        ("bare", (), ""),
    ]


REFUSALS = [
    pytest.param(FAMILIES + "[type board]\nchipset: stm32f072xb\n", "declares no firmware: key", id="no-firmware"),
    pytest.param(FAMILIES + "[type board]\nfirmware: klipperr\n", "not a known family", id="unknown"),
    pytest.param(
        FAMILIES + "[type board]\nfirmware: klipper, roadrunner\n", "different tools", id="mixed"
    ),
]


@pytest.mark.parametrize("text,needle", REFUSALS)
@pytest.mark.parametrize("load", [typelist.load, Registry.load], ids=["typelist", "registry"])
def test_the_strict_load_refuses(paths, load, text, needle):
    write_main_config(paths, text)
    with pytest.raises(ConfigCorruptError, match=needle):
        load(paths)


def test_no_config_file_is_an_empty_list(paths):
    assert typelist.load(paths) == []


def test_the_three_views_partition_the_one_list(paths, example_registry_text):
    write_main_config(paths, example_registry_text)
    every = {entry.name for entry in typelist.load(paths)}
    platformio = set(pio_mod.load(paths))
    cmake = set(cmake_mod.load(paths))
    kconfig = set(Registry.load(paths).names())
    assert platformio == {"knomi"}
    assert cmake == {"roadrunner"}
    assert kconfig == every - platformio - cmake


def test_selection_is_not_broken_by_another_types_bad_section(paths):
    """A PlatformIO type with no env is pio.load's refusal, not selection's."""
    write_main_config(
        paths,
        FAMILIES
        + "[type knomi]\nfirmware: knomi_serial\n\n"
        + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n",
    )
    assert providers.provider_of(paths, "board") == providers.KconfigMake.name
