"""Seeding the two firmware sections every install now declares.

install.sh runs this with the source paths it found. The properties that
matter: an existing section is never touched (it may point at a fork), a re-run
changes nothing, and the sections land where a person reading the file expects
them - after [updater], before the first family.
"""

from __future__ import annotations

import os

from mcu_updater import firmware, seed

from .conftest import BASE_FIRMWARES, read_main_config, write_main_config


def test_both_sections_are_written_to_a_missing_file(paths):
    assert seed.seed_firmware_sections(paths, {}) == ["klipper", "katapult"]
    assert read_main_config(paths) == BASE_FIRMWARES
    assert set(firmware.load(paths)) == {"klipper", "katapult"}


def test_an_existing_section_is_never_touched(paths):
    write_main_config(paths, "[firmware klipper]\nsource: ~/klipper-fork\n")
    assert seed.seed_firmware_sections(paths, {"klipper": "/elsewhere"}) == ["katapult"]
    text = read_main_config(paths)
    assert "source: ~/klipper-fork" in text
    assert "/elsewhere" not in text


def test_sections_go_after_updater_and_above_the_first_familys_comment(paths):
    write_main_config(
        paths,
        "[updater]\n#make_jobs: 0\n\n# a fork\n[firmware cartographer]\nsource: ~/carto\n",
    )
    seed.seed_firmware_sections(paths, {})
    text = read_main_config(paths)
    assert (
        text.index("[updater]")
        < text.index("[firmware klipper]")
        < text.index("[firmware katapult]")
        < text.index("# a fork")
        < text.index("[firmware cartographer]")
    )
    assert list(firmware.load(paths)) == ["klipper", "katapult", "cartographer"]


def test_a_rerun_changes_nothing(paths):
    seed.seed_firmware_sections(paths, {})
    before = read_main_config(paths)
    assert seed.seed_firmware_sections(paths, {}) == []
    assert read_main_config(paths) == before


def test_a_source_under_home_is_written_with_a_tilde(paths):
    seed.seed_firmware_sections(paths, {"katapult": os.path.join(paths.home, "forks", "katapult")})
    assert "source: ~/forks/katapult\n" in read_main_config(paths)


def test_a_source_outside_home_stays_absolute(paths):
    outside = os.path.abspath(os.path.join(paths.home, os.pardir, "elsewhere", "katapult"))
    seed.seed_firmware_sections(paths, {"katapult": outside})
    assert f"source: {outside}\n" in read_main_config(paths)


def test_main_reports_each_family(paths, fake_root, monkeypatch, capsys):
    monkeypatch.setenv("MCU_UPDATER_HOME", str(fake_root))
    assert seed.main(["--katapult", str(fake_root / "katapult")]) == 0
    out = capsys.readouterr().out
    assert "[firmware klipper] added" in out
    assert "[firmware katapult] added" in out
    assert seed.main([]) == 0
    assert "[firmware klipper] already declared" in capsys.readouterr().out


def test_the_documented_example_and_the_fixture_already_declare_both(
    paths, example_registry_text, live_registry_text
):
    for text in (example_registry_text, live_registry_text):
        write_main_config(paths, text)
        assert seed.seed_firmware_sections(paths, {}) == []
