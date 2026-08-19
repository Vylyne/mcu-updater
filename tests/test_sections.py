"""One section kind for a type, and the two spellings that predate it.

`[mcu ...]` and `[display ...]` were two spellings of one idea: a class of
device this host builds firmware for. What differed was the build system and the
flasher - which the Provider and Flasher seams turned into data, leaving the
prefix encoding a decision nothing else made that way any more.

Two properties matter here, and they pull in opposite directions:

**A config written before this still works, unchanged and unwarned.** These
files are hand-edited, on printers nobody is watching.

**A file is never rewritten into the new spelling behind the user's back.** The
save path rewrites the whole document, so a section that quietly changed prefix
would churn a diff nobody asked for - and, if the old section were left behind,
produce a duplicate-section error on the next load.
"""

from __future__ import annotations

from mcu_updater import sections
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.config import Registry
from mcu_updater.providers import pio

# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def test_a_bare_type_section_is_a_kconfig_one():
    """kconfig, because that is what a type meant for every release before the
    provider became a key. A bare `[type x]` reading as anything else would
    silently change what an edited `[mcu x]` builds."""
    doc = CfgDocument("[type carto_v4]\nchipset: stm32g431xx\n")
    declared = sections.read(doc)

    assert [(d.name, d.provider) for d in declared] == [("carto_v4", sections.KCONFIG_MAKE)]


def test_a_type_names_its_own_provider():
    doc = CfgDocument("[type knomi]\nprovider: platformio\nsource: ~/knomi-serial\n")
    assert sections.read(doc)[0].provider == sections.PLATFORMIO


def test_the_old_prefixes_mean_the_provider_they_always_implied():
    doc = CfgDocument("[mcu board]\n[display knomi]\n")
    assert {d.name: d.provider for d in sections.read(doc)} == {
        "board": sections.KCONFIG_MAKE,
        "knomi": sections.PLATFORMIO,
    }


def test_a_provider_filter_leaves_the_other_kind_alone():
    doc = CfgDocument("[mcu board]\n[display knomi]\n[type probe]\n")
    assert {d.name for d in sections.read(doc, provider=sections.KCONFIG_MAKE)} == {
        "board",
        "probe",
    }
    assert {d.name for d in sections.read(doc, provider=sections.PLATFORMIO)} == {"knomi"}


def test_an_unknown_provider_is_kept_rather_than_defaulted():
    """Dropping it would make a typo look like a section that was never there.
    Nobody matches it, so it costs nothing to carry - and it stays visible to
    anything listing or validating what is declared."""
    doc = CfgDocument("[type odd]\nprovider: platformIO\n")
    assert sections.read(doc)[0].provider == "platformIO"
    assert sections.read(doc, provider=sections.PLATFORMIO) == []


def test_a_nameless_section_is_ignored_rather_than_crashing():
    doc = CfgDocument("[type]\n[mcu]\n[display]\n")
    assert sections.read(doc) == []


def test_firmware_sections_are_a_different_axis():
    """`[firmware klipper]` declares a family, not a type. They share a file, so
    each has to ignore the other's sections."""
    doc = CfgDocument("[firmware cartographer]\nsource: ~/carto\n[type board]\n")
    assert [d.name for d in sections.read(doc)] == ["board"]
    assert not sections.is_type_section("firmware cartographer")
    assert not sections.is_type_section("updater")


# --------------------------------------------------------------------------
# writing: which spelling a section keeps
# --------------------------------------------------------------------------


def test_an_existing_section_keeps_the_spelling_it_has():
    doc = CfgDocument("[mcu board]\nchipset: stm32f072xb\n")
    assert sections.section_for(doc, "board", sections.KCONFIG_MAKE) == "mcu board"


def test_a_type_this_document_has_never_seen_gets_the_new_spelling():
    doc = CfgDocument("[mcu board]\n")
    assert sections.section_for(doc, "fresh", sections.KCONFIG_MAKE) == "type fresh"


def test_the_same_name_under_a_different_provider_is_a_different_section():
    """Nothing stops a board and a screen sharing a name. Matching on name alone
    would hand back the other provider's section and overwrite it."""
    doc = CfgDocument("[display knomi]\nsource: ~/k\n")
    assert sections.section_for(doc, "knomi", sections.PLATFORMIO) == "display knomi"
    assert sections.section_for(doc, "knomi", sections.KCONFIG_MAKE) == "type knomi"


# --------------------------------------------------------------------------
# end to end, through the two readers that use it
# --------------------------------------------------------------------------


def test_a_registry_round_trips_an_old_file_without_changing_its_spelling(paths):
    """The no-churn property. `save()` rewrites the whole document, so an
    unrelated edit must not turn every `[mcu ...]` into `[type ...]`."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write("[mcu board]\nchipset: stm32f072xb\nserials:\n")

    reg = Registry.load(paths)
    assert "board" in reg.names()
    reg.save(paths)

    text = open(paths.registry_file, encoding="utf-8").read()
    assert "[mcu board]" in text
    assert "[type board]" not in text


def test_a_new_type_is_written_in_the_new_spelling(paths):
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.save(paths)

    assert "[type carto_v4]" in open(paths.registry_file, encoding="utf-8").read()


def test_the_pio_provider_reads_both_spellings(paths):
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(
            "[display knomi_toolchanger]\nenv: knomi_toolchanger\nsource: ~/knomi-serial\n"
            "[type second_screen]\nprovider: platformio\nenv: second_screen\nsource: ~/other\n"
        )

    found = pio.load(paths)
    assert set(found) == {"knomi_toolchanger", "second_screen"}
    assert found["second_screen"].source == "~/other"


def test_a_pio_type_is_not_picked_up_by_the_mcu_registry(paths):
    """They share a file and now share a prefix, so the provider key is the only
    thing keeping one reader out of the other's sections."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(
            "[type board]\nchipset: stm32f072xb\n"
            "[type knomi]\nprovider: platformio\nenv: knomi\n"
        )

    assert Registry.load(paths).names() == ["board"]
    assert set(pio.load(paths)) == {"knomi"}
