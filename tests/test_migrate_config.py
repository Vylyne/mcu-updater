"""scripts/migrate_config.py: bringing an old-schema mcu-updater.cfg forward.

Exercises the module directly (`migrate()`) for the transform logic, and
`main()` for the CLI's diff/--write/already-up-to-date behaviour. See
docs/rebuild-plan.md Step 11 for why firmware: becoming required and this
script landed in the same commit.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigError
from mcu_updater.paths import Paths
from mcu_updater.providers import pio

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "migrate_config.py"


def _load():
    """Import the script by path - scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("migrate_config", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_config = _load()


# --------------------------------------------------------------------------
# header renaming
# --------------------------------------------------------------------------


def test_mcu_and_display_headers_become_type():
    doc = CfgDocument("[mcu board]\nchipset: x\nserials:\n    S1\n\n[display screen]\nenv: e\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.section_names("type") == ["type board", "type screen"]
    assert not doc.has_section("mcu board")
    assert not doc.has_section("display screen")


def test_a_header_comment_survives_the_rename():
    doc = CfgDocument("[mcu board]  # the toolhead boards\nchipset: x\nserials:\n")
    migrate_config.migrate(doc, pio_source="")
    assert "# the toolhead boards" in doc.render()


def test_a_type_already_carrying_its_bootloader_is_left_alone():
    doc = CfgDocument("[type board]\nchipset: x\nfirmware: klipper, katapult\nserials:\n")
    before = doc.render()
    migrate_config.migrate(doc, pio_source="")
    assert doc.render() == before


# --------------------------------------------------------------------------
# provider: platformio -> a [firmware] section, env:, firmware:, chipset:
# --------------------------------------------------------------------------


def test_a_legacy_platformio_type_gains_a_firmware_section():
    doc = CfgDocument("[type knomi]\nprovider: platformio\n")
    migrate_config.migrate(doc, pio_source="~/knomi_serial")

    assert doc.get("firmware knomi_serial", "builder") == "platformio"
    assert doc.get("firmware knomi_serial", "source") == "~/knomi_serial"
    assert doc.get("type knomi", "firmware") == "knomi_serial"
    assert doc.get("type knomi", "env") == "knomi"
    assert doc.get("type knomi", "chipset") == "esp32"
    assert doc.get("type knomi", "provider") is None


def test_the_env_key_is_the_types_own_old_name():
    """The section name silently was the PlatformIO env under the old
    provider: spelling - that is what gets written out explicitly."""
    doc = CfgDocument("[type knomi_toolchanger]\nprovider: platformio\n")
    migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert doc.get("type knomi_toolchanger", "env") == "knomi_toolchanger"


def test_an_explicit_chipset_or_env_is_not_overwritten():
    doc = CfgDocument(
        "[type knomi]\nprovider: platformio\nenv: custom_env\nchipset: esp32s3\n"
    )
    migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert doc.get("type knomi", "env") == "custom_env"
    assert doc.get("type knomi", "chipset") == "esp32s3"


def test_two_legacy_types_share_one_family_from_pio_source():
    """One repo, shared by every env - the old model's own convention."""
    doc = CfgDocument(
        "[type knomi]\nprovider: platformio\n\n"
        "[type knomi_toolchanger]\nprovider: platformio\n"
    )
    migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert doc.section_names("firmware") == ["firmware knomi_serial"]
    assert doc.get("type knomi", "firmware") == "knomi_serial"
    assert doc.get("type knomi_toolchanger", "firmware") == "knomi_serial"


def test_no_pio_source_falls_back_to_the_lone_types_own_name():
    doc = CfgDocument("[type knomi]\nprovider: platformio\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.section_names("firmware") == ["firmware knomi"]
    assert doc.get("firmware knomi", "source") is None
    assert doc.get("type knomi", "firmware") == "knomi"


def test_two_legacy_types_with_no_pio_source_is_refused_not_guessed():
    """No shared-tree evidence and more than one type: there is no honest
    single answer, so this refuses rather than inventing one."""
    doc = CfgDocument(
        "[type knomi]\nprovider: platformio\n\n"
        "[type knomi_toolchanger]\nprovider: platformio\n"
    )
    with pytest.raises(ConfigError) as exc:
        migrate_config.migrate(doc, pio_source="")
    assert "knomi" in str(exc.value)
    assert "knomi_toolchanger" in str(exc.value)
    assert "pio_source" in str(exc.value)


def test_an_existing_firmware_section_is_not_duplicated():
    doc = CfgDocument(
        "[firmware knomi_serial]\nbuilder: platformio\nsource: ~/elsewhere\n\n"
        "[type knomi]\nprovider: platformio\n"
    )
    migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert doc.get("firmware knomi_serial", "source") == "~/elsewhere"


# --------------------------------------------------------------------------
# firmware: required, as a list - non-platformio types only
# --------------------------------------------------------------------------


def test_an_absent_firmware_key_becomes_klipper_and_katapult():
    doc = CfgDocument("[type board]\nchipset: x\nserials:\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type board", "firmware") == "klipper, katapult"


def test_katapult_installed_false_is_honoured_then_removed():
    doc = CfgDocument("[type board]\nchipset: x\nkatapult_installed: false\nserials:\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type board", "firmware") == "klipper"
    assert doc.get("type board", "katapult_installed") is None


def test_katapult_installed_true_is_equivalent_to_absent():
    doc = CfgDocument("[type board]\nchipset: x\nkatapult_installed: true\nserials:\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type board", "firmware") == "klipper, katapult"
    assert doc.get("type board", "katapult_installed") is None


def test_a_single_firmware_becomes_a_list_with_katapult_appended():
    """cartographer: ~/cartographer-klipper, firmware: cartographer (singular)
    under the old model, matching the real printer's config captured in
    NOTES.md - katapult was never listed because it did not need to be."""
    doc = CfgDocument(
        "[firmware cartographer]\nsource: ~/cartographer-klipper\nartifact: klipper\n\n"
        "[type cartographer]\nchipset: stm32g431xx\nfirmware: cartographer\nserials:\n"
    )
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type cartographer", "firmware") == "cartographer, katapult"


def test_a_platformio_type_never_gets_katapult_appended():
    """The firmware:-required pass must recognise an already-migrated
    PlatformIO type by its family's builder, not touch it as if it were a
    plain kconfig type missing a bootloader."""
    doc = CfgDocument(
        "[firmware knomi_serial]\nbuilder: platformio\n\n"
        "[type knomi]\nfirmware: knomi_serial\nenv: knomi\nchipset: esp32\n"
    )
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type knomi", "firmware") == "knomi_serial"


# --------------------------------------------------------------------------
# idempotency - running twice changes nothing further
# --------------------------------------------------------------------------


def test_running_it_twice_is_a_no_op_the_second_time():
    doc = CfgDocument(
        "[mcu board]\nchipset: x\nserials:\n\n"
        "[display knomi]\nprovider: platformio\n"
    )
    first_notes = migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert first_notes

    second_notes = migrate_config.migrate(doc, pio_source="~/knomi_serial")
    assert second_notes == []


def test_a_deliberately_bootloader_less_type_is_not_safe_to_migrate_twice():
    """The known, documented limitation (see the script's own module
    docstring): once katapult_installed: false is consumed and removed,
    there is nothing left on disk distinguishing "deliberately klipper-alone"
    from "predates firmware: entirely, apply the historical default" - both
    are a bare firmware: value with no katapult_installed key. This is why
    the script is one-shot, not idempotent in general - pinned here so a
    future change either fixes it deliberately or does not silently make it
    worse without this test explaining why."""
    doc = CfgDocument("[type board]\nchipset: x\nkatapult_installed: false\nserials:\n")
    migrate_config.migrate(doc, pio_source="")
    assert doc.get("type board", "firmware") == "klipper"

    second_notes = migrate_config.migrate(doc, pio_source="")
    assert doc.get("type board", "firmware") == "klipper, katapult"
    assert second_notes  # not a no-op, unlike the well-behaved case above


def test_the_real_printer_config_migrates_to_something_both_readers_accept(paths, fake_root):
    """The two required test inputs, per docs/rebuild-plan.md Step 11's own
    gate: the config captured in NOTES.md (this, in miniature - one plain
    type, one bootloader-less vendor fork, one legacy PlatformIO display) and
    the reverted repo sample (covered by the other tests here, which all
    start from a plain [type ...] with no firmware:, exactly that file's
    shape before this step)."""
    doc = CfgDocument(
        "[updater]\npio_source: ~/knomi_serial\n\n"
        "[type bttebb36]\nchipset: stm32g0b1xx\nserials:\n    S1\n\n"
        "[type knomi]\nprovider: platformio\n\n"
        "[firmware cartographer]\nsource: ~/cartographer-klipper\nartifact: klipper\n\n"
        "[type cartographer]\nchipset: stm32g431xx\nfirmware: cartographer\nserials:\n"
    )
    migrate_config.migrate(doc, pio_source="~/knomi_serial")

    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(doc.render())

    reg = Registry.load(paths)
    assert set(reg.names()) == {"bttebb36", "cartographer"}
    assert reg.get("bttebb36").firmwares == ["klipper", "katapult"]
    assert reg.get("cartographer").firmwares == ["cartographer", "katapult"]
    assert set(pio.load(paths)) == {"knomi"}


# --------------------------------------------------------------------------
# the CLI: diff preview vs --write, and the no-op message
# --------------------------------------------------------------------------


def test_dry_run_prints_a_diff_and_writes_nothing(tmp_path, capsys):
    cfg = tmp_path / "mcu-updater.cfg"
    cfg.write_text("[type board]\nchipset: x\nserials:\n", encoding="utf-8", newline="\n")

    rc = migrate_config.main([str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "firmware: klipper, katapult" in out
    assert "Re-run with --write" in out
    assert cfg.read_text(encoding="utf-8") == "[type board]\nchipset: x\nserials:\n"


def test_write_applies_the_change(tmp_path, capsys):
    cfg = tmp_path / "mcu-updater.cfg"
    cfg.write_text("[type board]\nchipset: x\nserials:\n", encoding="utf-8", newline="\n")

    rc = migrate_config.main([str(cfg), "--write"])
    assert rc == 0
    assert "firmware: klipper, katapult" in cfg.read_text(encoding="utf-8")
    assert "wrote" in capsys.readouterr().out


def test_an_already_migrated_file_reports_nothing_to_do(tmp_path, capsys):
    cfg = tmp_path / "mcu-updater.cfg"
    cfg.write_text(
        "[type board]\nchipset: x\nfirmware: klipper, katapult\nserials:\n",
        encoding="utf-8",
        newline="\n",
    )

    rc = migrate_config.main([str(cfg)])
    assert rc == 0
    assert "already up to date" in capsys.readouterr().out


def test_a_missing_file_is_a_clean_error_not_a_traceback(tmp_path, capsys):
    rc = migrate_config.main([str(tmp_path / "nope.cfg")])
    assert rc == 1
    assert "no such file" in capsys.readouterr().err


def test_the_ambiguous_pio_case_is_a_clean_error_not_a_traceback(tmp_path, capsys):
    cfg = tmp_path / "mcu-updater.cfg"
    cfg.write_text(
        "[type a]\nprovider: platformio\n\n[type b]\nprovider: platformio\n",
        encoding="utf-8",
        newline="\n",
    )

    rc = migrate_config.main([str(cfg)])
    assert rc == 1
    assert "pio_source" in capsys.readouterr().err
    assert cfg.read_text(encoding="utf-8").startswith("[type a]\nprovider: platformio")


def test_with_no_path_argument_it_uses_the_configured_registry_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    default_paths = Paths.from_env(env={"MCU_UPDATER_HOME": str(home)})
    with open(default_paths.registry_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[type board]\nchipset: x\nserials:\n")

    monkeypatch.setenv("MCU_UPDATER_HOME", str(home))
    rc = migrate_config.main(["--write"])
    assert rc == 0
    assert "firmware: klipper, katapult" in open(
        default_paths.registry_file, encoding="utf-8"
    ).read()
