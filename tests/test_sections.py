"""Which sections declare a type: naming, and nothing else.

`[mcu ...]` and `[display ...]` were two spellings of one idea, aliased here so
each reader could ask for its own kind without learning there was more than one
way to spell one. Both are retired: only `[type ...]` is recognised now, and
which build system a type belongs to is decided elsewhere - by the builder its
declared `firmware:` family names, not by anything this module reads.
"""

from __future__ import annotations

import pytest

from mcu_updater import sections
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.providers import pio

# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def test_a_type_section_is_read():
    doc = CfgDocument("[type carto_v4]\nchipset: stm32g431xx\n")
    declared = sections.read(doc)

    assert [d.name for d in declared] == ["carto_v4"]


def test_the_old_prefixes_are_no_longer_recognised():
    """Retired, not aliased. A config still using them is not an error here -
    it simply declares no type, the same as any other section this module
    does not know about."""
    doc = CfgDocument("[mcu board]\n[display knomi]\n")
    assert sections.read(doc) == []


def test_a_nameless_section_is_ignored_rather_than_crashing():
    doc = CfgDocument("[type]\n")
    assert sections.read(doc) == []


def test_firmware_sections_are_a_different_axis():
    """`[firmware klipper]` declares a family, not a type. They share a file, so
    each has to ignore the other's sections."""
    doc = CfgDocument("[firmware cartographer]\nsource: ~/carto\n[type board]\n")
    assert [d.name for d in sections.read(doc)] == ["board"]
    assert not sections.is_type_section("firmware cartographer")
    assert not sections.is_type_section("updater")


# --------------------------------------------------------------------------
# writing: which section a name already has, or gets
# --------------------------------------------------------------------------


def test_an_existing_section_is_found_by_name():
    doc = CfgDocument("[type board]\nchipset: stm32f072xb\n")
    assert sections.section_for(doc, "board") == "type board"


def test_a_name_this_document_has_never_seen_gets_a_fresh_section():
    doc = CfgDocument("[type board]\n")
    assert sections.section_for(doc, "fresh") == "type fresh"


# --------------------------------------------------------------------------
# end to end, through the two readers that use it
# --------------------------------------------------------------------------


def test_a_registry_round_trips_without_changing_the_file(paths):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write("[type board]\nchipset: stm32f072xb\nfirmware: klipper\nserials:\n")

    reg = Registry.load(paths)
    assert "board" in reg.names()
    reg.save(paths)

    text = open(paths.registry_file, encoding="utf-8").read()
    assert "[type board]" in text


def test_a_new_type_is_written_as_type(paths):
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.save(paths)

    assert "[type carto_v4]" in open(paths.registry_file, encoding="utf-8").read()


def test_a_platformio_type_is_recognised_by_its_declared_firmware(paths):
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(
            "[firmware knomi_serial]\nsource: ~/knomi-serial\nbuilder: platformio\n\n"
            "[type knomi_toolchanger]\nfirmware: knomi_serial\nenv: knomi_toolchanger\n"
        )

    found = pio.load(paths)
    assert set(found) == {"knomi_toolchanger"}


def test_a_pio_type_is_not_picked_up_by_the_mcu_registry(paths):
    """They share a file and a prefix, so the declared firmware's builder is
    the only thing keeping one reader out of the other's sections."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(
            "[firmware knomi_serial]\nsource: ~/knomi-serial\nbuilder: platformio\n\n"
            "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n"
            "[type knomi]\nfirmware: knomi_serial\nenv: knomi\n"
        )

    assert Registry.load(paths).names() == ["board"]
    assert set(pio.load(paths)) == {"knomi"}


def test_a_type_predating_firmware_is_refused_not_defaulted(paths):
    """The old provider:/prefix fallback is fully retired as of step 9, and
    firmware: became required in step 11 - a type carrying the old
    'provider: platformio' key but no firmware: is not silently a
    kconfig_make type any more, it is refused. pio.load() still just skips
    it (its own firmware:-required check predates this one, from step 7),
    so only the MCU registry's refusal is new here."""
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write("[type knomi]\nprovider: platformio\nenv: knomi\n")

    assert pio.load(paths) == {}
    with pytest.raises(ConfigCorruptError) as exc:
        Registry.load(paths)
    assert "knomi" in str(exc.value)
    assert "firmware" in str(exc.value)
