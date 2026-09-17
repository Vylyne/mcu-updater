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

from .conftest import read_main_config, save_registry, write_main_config

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
        ("orphan", ("nowhere",), ""),
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


OLD_SPELLINGS = [
    pytest.param("[type knomi]\nfirmware: knomi_serial\nenv: knomi\n", "env", "platformio_env", id="env"),
    pytest.param(
        "[type board]\nchipset: stm32f072xb\nfirmware: klipper\nprofile: config.X\n",
        "profile",
        "kconfig_make_profile",
        id="profile",
    ),
    pytest.param(
        "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\ndevice_map: a.json\n",
        "device_map",
        "knomi_serial_device_map",
        id="device_map",
    ),
]


@pytest.mark.parametrize("section,old,new", OLD_SPELLINGS)
def test_an_old_key_spelling_is_refused_naming_the_new_one(paths, section, old, new):
    write_main_config(paths, FAMILIES + section)
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    assert f"{old}:" in str(exc.value)
    assert f"{new}:" in str(exc.value)


KLIPPER_SECTION = (
    FAMILIES
    + "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\nklipper_section: knomi_serial\n"
)


@pytest.mark.parametrize("load", [typelist.load, pio_mod.load], ids=["typelist", "pio"])
def test_klipper_section_is_refused_as_no_longer_read(paths, load):
    write_main_config(paths, KLIPPER_SECTION)
    with pytest.raises(ConfigCorruptError, match="klipper_section:, which is no longer read"):
        load(paths)


def test_the_new_spellings_are_read(paths):
    write_main_config(
        paths,
        FAMILIES
        + "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\n"
        + "knomi_serial_device_map: elsewhere/devices.json\n\n"
        + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n"
        + "kconfig_make_profile: config.X\n",
    )
    knomi = pio_mod.load(paths)["knomi"]
    assert knomi.env == "knomi"
    assert knomi.device_map == "elsewhere/devices.json"
    assert knomi.klipper_section == "knomi_serial"
    assert Registry.load(paths).get("board").profile == "config.X"


def test_a_saved_profile_uses_the_new_spelling(paths):
    write_main_config(paths, FAMILIES + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n")
    reg = Registry.load(paths)
    reg.get("board").profile = "config.Y"
    save_registry(reg, paths)
    text = read_main_config(paths)
    assert "kconfig_make_profile: config.Y" in text
    assert "\nprofile:" not in text


def test_every_undeclared_family_is_reported_at_once(paths):
    """Raising on the first miss meant a config with two typos took two
    round-trips to fix: correct one, reload, discover the next."""
    write_main_config(
        paths,
        FAMILIES
        + "[type one]\nchipset: x\nfirmware: klipperr\n\n"
        + "[type two]\nchipset: x\nfirmware: katapultt, klipperr\n",
    )
    from mcu_updater import firmware

    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)

    message = str(exc.value)
    assert "not a known family" in message
    assert "one -> klipperr" in message
    assert "two -> katapultt" in message
    assert "two -> klipperr" in message
    # One snippet per distinct family, in first-seen order, a blank line
    # between them, and the shared trailer once at the end.
    klipperr = firmware.missing_section_snippet("klipperr")
    katapultt = firmware.missing_section_snippet("katapultt")
    assert message.count(klipperr) == 1
    assert message.count(katapultt) == 1
    assert f"{klipperr}\n\n{katapultt}" in message
    assert message.count(firmware.MISSING_SECTION_TRAILER) == 1
    assert message.endswith(firmware.MISSING_SECTION_TRAILER)
    assert message.count("install.sh") == 1
    assert exc.value.data["missing"] == {"one": ["klipperr"], "two": ["katapultt", "klipperr"]}
    # The first miss still fills the original fields.
    assert exc.value.data["type"] == "one"
    assert exc.value.data["value"] == "klipperr"
    assert exc.value.data["path"] == paths.registry_file


def test_a_family_repeated_on_one_type_is_one_miss(paths):
    write_main_config(paths, FAMILIES + "[type a]\nchipset: x\nfirmware: klipperr, klipperr\n")
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    assert str(exc.value).count("a -> klipperr") == 1
    assert exc.value.data["missing"] == {"a": ["klipperr"]}


def test_a_single_undeclared_family_keeps_the_type_and_value_fields(paths):
    write_main_config(paths, FAMILIES + "[type board]\nchipset: x\nfirmware: klipperr\n")
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    assert exc.value.data["type"] == "board"
    assert exc.value.data["value"] == "klipperr"
    assert exc.value.data["missing"] == {"board": ["klipperr"]}


def test_an_undeclared_family_beside_a_known_one_is_not_called_mixed_builders(paths):
    """An unknown family has no builder (`read` records ""), which must not
    count as a second tool - the real problem is the typo."""
    write_main_config(paths, FAMILIES + "[type board]\nchipset: x\nfirmware: klipper, bogus\n")
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    assert "not a known family" in str(exc.value)
    assert "different tools" not in str(exc.value)
    assert exc.value.data["missing"] == {"board": ["bogus"]}


def test_every_unknown_builder_is_refused_at_once(paths):
    """A typo'd `builder:` used to load, and its types silently dropped out of
    every build and flash loop. A family no type uses is refused too."""
    write_main_config(
        paths,
        FAMILIES
        + "[firmware odd]\nsource: ~/odd\nbuilder: cmkae\n\n"
        + "[firmware unused]\nsource: ~/unused\nbuilder: bogus\n\n"
        + "[type board]\nchipset: x\nfirmware: odd\n",
    )
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)

    message = str(exc.value)
    assert "[firmware odd] builder: cmkae" in message
    assert "[firmware unused] builder: bogus" in message
    assert "known: cmake, kconfig_make, platformio" in message
    assert exc.value.data["builders"] == {"odd": "cmkae", "unused": "bogus"}
    assert exc.value.data["family"] == "odd"
    assert exc.value.data["value"] == "cmkae"


def test_the_builder_names_are_exactly_the_providers():
    from mcu_updater import firmware
    from mcu_updater.providers.registry import PROVIDERS

    assert set(firmware.BUILDERS) == {p.name for p in PROVIDERS}
