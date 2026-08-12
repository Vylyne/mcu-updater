"""Firmware families: where a tree lives, and what it builds.

Two conventions were baked into `paths`: the source tree is ``~/<fw>``, and the
build drops ``out/<fw>.bin``. Both hold for klipper and katapult and both break
on the first vendor fork - cartographer's firmware is a klipper fork in a
differently named directory whose Makefile still emits ``klipper.bin``.

The property these tests exist to hold is that **making them overridable
changed nothing for anyone not overriding them.** Every default is the old
hardcoded behaviour, and a config file with no [firmware] section at all must
be indistinguishable from the code before this module existed.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater import firmware
from mcu_updater.build import build
from mcu_updater.config import Registry
from mcu_updater.errors import SourceTreeMissingError
from mcu_updater.firmware import FirmwareFamily


def _write_firmware(paths, name, **keys):
    """Add a `[firmware <name>]` section without disturbing the rest of the file.

    With no keys the section still gets written - declaring a family that takes
    every default is the ordinary case, and a helper that quietly wrote nothing
    would make those tests pass for the wrong reason.
    """
    from mcu_updater.cfgdoc import CfgDocument

    text = ""
    if os.path.exists(paths.main_config):
        with open(paths.main_config, encoding="utf-8") as fh:
            text = fh.read()
    doc = CfgDocument(text)
    for key, value in (keys or {"source": ""}).items():
        doc.set(f"firmware {name}", key, value)
    os.makedirs(paths.config_dir, exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())


# --------------------------------------------------------------------------
# the conventions still hold when nobody overrides them
# --------------------------------------------------------------------------


def test_no_config_at_all_is_the_old_behaviour(paths):
    """The whole compatibility claim, in one assertion."""
    family = firmware.resolve(paths, "klipper")
    assert family.source_dir(paths) == paths.fw_dir("klipper")
    assert family.built_artifact(paths) == paths.built_artifact("klipper", "bin")
    assert family.built_artifact(paths, "uf2") == paths.built_artifact("klipper", "uf2")


def test_a_family_nobody_configured_still_resolves(paths):
    """`resolve` never returns None, so no call site has to re-implement the
    fallback - which is how two of them would eventually disagree."""
    assert firmware.resolve(paths, "katapult").name == "katapult"
    assert firmware.resolve(paths, "invented").source_dir(paths).endswith("invented")


def test_a_missing_config_file_is_not_an_error(paths, fake_root):
    if os.path.exists(paths.main_config):
        os.remove(paths.main_config)
    assert firmware.load(paths) == {}
    assert firmware.resolve(paths, "klipper").source_dir(paths) == paths.fw_dir("klipper")


def test_the_artifact_defaults_to_the_family_name(paths):
    assert FirmwareFamily(name="klipper").artifact_name() == "klipper"


# --------------------------------------------------------------------------
# overriding them
# --------------------------------------------------------------------------


def test_a_family_can_name_its_own_source_tree(paths, fake_root):
    _write_firmware(paths, "klipper", source=str(fake_root / "elsewhere"))
    assert firmware.resolve(paths, "klipper").source_dir(paths) == str(fake_root / "elsewhere")


def test_a_source_path_is_expanded(paths):
    _write_firmware(paths, "klipper", source="~/somewhere")
    resolved = firmware.resolve(paths, "klipper").source_dir(paths)
    assert "~" not in resolved
    assert resolved.endswith("somewhere")


def test_a_fork_keeps_its_parents_output_name(paths, fake_root):
    """Cartographer's firmware *is* klipper, so its Makefile emits klipper.bin
    no matter what we call the family. A family whose name matches neither its
    directory nor its output is what every fork looks like."""
    _write_firmware(
        paths,
        "cartographer",
        source=str(fake_root / "MCU-Firmware---Based-on-Klipper"),
        artifact="klipper",
    )
    family = firmware.resolve(paths, "cartographer")

    assert family.built_artifact(paths) == os.path.join(
        str(fake_root / "MCU-Firmware---Based-on-Klipper"), "out", "klipper.bin"
    )
    assert "cartographer.bin" not in family.built_artifact(paths)


def test_source_and_artifact_are_independent(paths, fake_root):
    """A relocated tree that still builds its own name, and a fork in the
    conventional place that does not, are both legitimate."""
    _write_firmware(paths, "klipper", source=str(fake_root / "elsewhere"))
    assert firmware.resolve(paths, "klipper").artifact_name() == "klipper"

    _write_firmware(paths, "katapult", artifact="renamed")
    katapult = firmware.resolve(paths, "katapult")
    assert katapult.source_dir(paths) == paths.fw_dir("katapult")
    assert katapult.built_artifact(paths).endswith("renamed.bin")


def test_a_nameless_section_is_ignored_rather_than_crashing(paths):
    from mcu_updater.cfgdoc import CfgDocument

    doc = CfgDocument("[firmware]\nsource: /nowhere\n")
    os.makedirs(paths.config_dir, exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())
    assert firmware.load(paths) == {}


def test_the_parsed_sections_can_be_passed_in_to_avoid_rereading(paths, fake_root):
    """fw.status answers for every type on every poll; re-reading the config
    per firmware is two file reads per board."""
    _write_firmware(paths, "klipper", source=str(fake_root / "elsewhere"))
    families = firmware.load(paths)
    assert firmware.resolve(paths, "klipper", families).source_dir(paths) == str(
        fake_root / "elsewhere"
    )


# --------------------------------------------------------------------------
# it actually reaches the build
# --------------------------------------------------------------------------


def _registry(paths) -> Registry:
    reg = Registry.load(paths)
    reg.add_type("board", "stm32f072xb")
    reg.save(paths)
    return reg


def _write_saved_config(paths, mcu_type="board", fw="klipper"):
    os.makedirs(paths.type_dir(mcu_type), exist_ok=True)
    with open(paths.config_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")


def test_build_looks_in_the_configured_tree_not_the_conventional_one(
    paths, settings, fake_root
):
    """The wiring test. ~/klipper exists and the configured tree does not, so
    the error naming the *configured* path is proof the override was honoured
    rather than silently ignored."""
    reg = _registry(paths)
    _write_saved_config(paths)
    _write_firmware(paths, "klipper", source=str(fake_root / "not-there"))

    with pytest.raises(SourceTreeMissingError) as exc:
        build(paths, reg, settings, "board", "klipper")

    assert "not-there" in str(exc.value)
    assert str(fake_root / "klipper") not in str(exc.value)


def test_menuconfig_sessions_open_the_configured_tree(paths, fake_root):
    """menuconfig reads the tree's *vendored* kconfiglib, so opening the wrong
    tree would parse one klipper's Kconfig against another's library."""
    from mcu_updater.errors import KconfigError
    from mcu_updater.kconfig import KconfigSession

    _write_firmware(paths, "klipper", source=str(fake_root / "elsewhere"))

    # The session fails because the configured tree has no kconfiglib - and the
    # path it names is the assertion. Deliberately not a try/except that passes
    # when nothing is raised: that would hold whatever the code did.
    with pytest.raises(KconfigError) as exc:
        KconfigSession("id", paths, "board", "klipper")

    assert str(fake_root / "elsewhere") in str(exc.value)
    assert str(fake_root / "klipper") not in str(exc.value)


# --------------------------------------------------------------------------
# declaring a family that did not exist before
# --------------------------------------------------------------------------


def test_the_builtin_families_are_always_there(paths):
    """klipper is what a board runs and katapult is what puts it there. Enough
    of this tool is about that pair that neither may be removed by editing a
    config file."""
    assert firmware.names(paths) == ("klipper", "katapult")

    _write_firmware(paths, "cartographer", artifact="klipper")
    assert firmware.names(paths)[:2] == ("klipper", "katapult")


def test_a_declared_family_joins_the_known_set(paths):
    _write_firmware(paths, "cartographer", artifact="klipper")
    assert firmware.names(paths) == ("klipper", "katapult", "cartographer")


def test_declared_families_are_ordered_independently_of_the_file(paths):
    """Otherwise the artifacts payload and the CLI listing reorder themselves
    depending on where somebody happened to add a section."""
    _write_firmware(paths, "zzz")
    _write_firmware(paths, "aaa")
    assert firmware.names(paths) == ("klipper", "katapult", "aaa", "zzz")


def test_a_declared_family_gets_its_own_per_type_keys(paths):
    """`<fw>_extra_args` is derived from the family name, so a new family has
    to be known before the registry can read or write its keys at all."""
    _write_firmware(paths, "cartographer", artifact="klipper")

    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.get("carto_v4").fw("cartographer").extra_args = "-DSCANNER"
    reg.save(paths)

    reloaded = Registry.load(paths)
    assert reloaded.get("carto_v4").fw("cartographer").extra_args == "-DSCANNER"
    assert "cartographer_extra_args" in open(paths.main_config, encoding="utf-8").read()


def test_a_declared_family_appears_in_a_types_own_ordering(paths):
    _write_firmware(paths, "cartographer")
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.save(paths)

    order = Registry.load(paths).get("carto_v4").fw_order()
    assert order[:2] == ["klipper", "katapult"]
    assert "cartographer" in order


def test_a_declared_family_builds_from_its_own_tree(paths, settings, fake_root):
    """The end of the chain: declare it, and build reaches the fork rather than
    ~/cartographer, which does not exist and never will."""
    _write_firmware(
        paths,
        "cartographer",
        source=str(fake_root / "MCU-Firmware---Based-on-Klipper"),
        artifact="klipper",
    )
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.save(paths)
    _write_saved_config(paths, "carto_v4", "cartographer")

    with pytest.raises(SourceTreeMissingError) as exc:
        build(paths, reg, settings, "carto_v4", "cartographer")

    assert "MCU-Firmware---Based-on-Klipper" in str(exc.value)
    assert str(fake_root / "cartographer") not in str(exc.value)


def test_a_relocated_tree_is_what_staleness_compares_against(paths, fake_root, monkeypatch):
    """`artifact_status` asks git for the source HEAD. Asking the wrong tree
    would report every board current forever."""
    from mcu_updater import build as build_mod

    seen = []
    monkeypatch.setattr(build_mod, "git_head", lambda d: seen.append(d) or "abc1234")

    _write_firmware(paths, "klipper", source=str(fake_root / "elsewhere"))
    os.makedirs(paths.artifact_dir("board"), exist_ok=True)
    with open(paths.bin_file("board", "klipper"), "wb") as fh:
        fh.write(b"\0")
    with open(paths.sidecar_file("board", "klipper"), "w", encoding="utf-8") as fh:
        fh.write('{"fw_sha": "abc1234"}')

    build_mod.artifact_status(paths, "board", "klipper")
    assert seen == [str(fake_root / "elsewhere")]


# --------------------------------------------------------------------------
# which family a board actually runs
# --------------------------------------------------------------------------


def test_a_type_runs_klipper_unless_it_says_otherwise(paths):
    """Every [mcu ...] section predating the key means what it always meant."""
    reg = Registry.load(paths)
    reg.add_type("bttebb36", "stm32g0b1xx")
    reg.save(paths)
    assert Registry.load(paths).get("bttebb36").firmware == "klipper"


def test_the_default_is_not_written_back_into_the_file(paths):
    """The file stays readable rather than filling with restated defaults."""
    reg = Registry.load(paths)
    reg.add_type("bttebb36", "stm32g0b1xx")
    reg.save(paths)
    assert "firmware:" not in open(paths.main_config, encoding="utf-8").read()


def test_a_declared_application_round_trips(paths):
    _write_firmware(paths, "cartographer", artifact="klipper")
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.get("carto_v4").firmware = "cartographer"
    reg.save(paths)

    assert Registry.load(paths).get("carto_v4").firmware == "cartographer"


def test_a_misspelt_family_is_refused_rather_than_defaulted(paths):
    """Defaulting would build and flash klipper at a board running something
    else - the exact mistake this key exists to prevent."""
    from mcu_updater.errors import ConfigCorruptError

    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.save(paths)
    text = open(paths.main_config, encoding="utf-8").read()
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("[mcu carto_v4]", "[mcu carto_v4]\nfirmware: cartographr"))

    with pytest.raises(ConfigCorruptError) as exc:
        Registry.load(paths)
    assert "cartographr" in str(exc.value)
    assert "klipper" in str(exc.value)  # names what it does know


def test_a_type_lists_only_the_families_it_uses(paths):
    _write_firmware(paths, "cartographer", artifact="klipper")
    reg = Registry.load(paths)
    reg.add_type("carto_v4", "stm32g431xx")
    reg.get("carto_v4").firmware = "cartographer"
    reg.save(paths)

    mcu = Registry.load(paths).get("carto_v4")
    assert mcu.families() == ["cartographer", "katapult"]
    # ...while still *carrying* klipper's keys, which are harmless and unused.
    assert "klipper" in mcu.fw_order()


def test_a_board_with_no_bootloader_lists_only_its_application(paths):
    reg = Registry.load(paths)
    reg.add_type("bare", "stm32f072xb", katapult_installed=False)
    reg.save(paths)
    assert Registry.load(paths).get("bare").families() == ["klipper"]
