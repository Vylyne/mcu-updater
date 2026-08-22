from __future__ import annotations

import pytest

from mcu_updater.config import Registry
from mcu_updater.errors import ConfigError
from mcu_updater.settings import Settings, load_settings, save_settings


def test_missing_file_yields_defaults(paths):
    s = load_settings(paths.settings_file)
    assert s == Settings()
    assert s.clean_before_build is True
    assert s.enable_flashing is False  # agent-side gate, off until deliberately enabled


def test_values_are_parsed_with_the_right_types(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write(
            "[updater]\n"
            "make_jobs = 4\n"
            "clean_before_build = false\n"
            "dry_run = yes\n"
            "stop_services = klipper, knomi_serial\n"
            "service_backend = systemd\n"
        )
    s = load_settings(paths.settings_file)
    assert s.make_jobs == 4
    assert s.clean_before_build is False
    assert s.dry_run is True
    assert s.stop_services == ["klipper", "knomi_serial"]
    assert s.service_backend == "systemd"


def test_stop_services_accepts_space_separated_values_at_the_updater_level(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nstop_services = klipper knomi_serial\n")
    assert load_settings(paths.settings_file).stop_services == ["klipper", "knomi_serial"]


def test_a_bare_legacy_service_key_becomes_a_one_element_stop_services(paths):
    """Retired in favour of `stop_services`, but a KIAUH multi-instance cfg
    that still says `service: klipper-1` must not silently stop the wrong
    unit the moment this version is installed."""
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nservice = klipper-1\n")
    assert load_settings(paths.settings_file).stop_services == ["klipper-1"]


def test_an_explicit_stop_services_wins_over_a_legacy_service_key(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nservice = klipper-1\nstop_services = klipper\n")
    assert load_settings(paths.settings_file).stop_services == ["klipper"]


def test_stop_services_blank_means_the_global_off_switch(paths):
    """The one setting that makes a flash unsafe rather than merely
    inconvenient - see the README's "Which services stop before a write"."""
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nstop_services:\n")
    assert load_settings(paths.settings_file).stop_services == []


def test_stop_services_absent_is_none_not_the_default(paths):
    """Resolution, not storage, is where the built-in default lives - see
    `stop_services.resolve_stop_services`."""
    s = load_settings(paths.settings_file)
    assert s.stop_services is None


def test_dashes_are_accepted_as_underscores(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nclean-before-build = false\n")
    assert load_settings(paths.settings_file).clean_before_build is False


def test_unknown_keys_are_ignored_not_fatal(paths):
    """A newer version may have written a setting this one doesn't know."""
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nsome_future_option = 7\nmake_jobs = 2\n")
    assert load_settings(paths.settings_file).make_jobs == 2


def test_a_bad_value_raises_rather_than_being_ignored(paths):
    """Silently discarding `dry_run = maybe` is how you flash a board by accident."""
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\ndry_run = maybe\n")
    with pytest.raises(ConfigError):
        load_settings(paths.settings_file)


def test_an_invalid_backend_raises(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nservice_backend = telepathy\n")
    with pytest.raises(ConfigError) as exc:
        load_settings(paths.settings_file)
    assert exc.value.data["key"] == "service_backend"


def test_no_section_yields_defaults(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[something-else]\nfoo = bar\n")
    assert load_settings(paths.settings_file) == Settings()


def test_save_then_load_round_trips(paths):
    original = Settings(
        make_jobs=3, dry_run=True, stop_services=["klipper-2"], enable_flashing=True
    )
    save_settings(paths.settings_file, original)
    assert load_settings(paths.settings_file) == original


def test_save_then_load_round_trips_an_absent_stop_services(paths):
    original = Settings(make_jobs=3)
    assert original.stop_services is None
    save_settings(paths.settings_file, original)
    assert load_settings(paths.settings_file) == original


def test_save_then_load_round_trips_a_blank_stop_services(paths):
    original = Settings(stop_services=[])
    save_settings(paths.settings_file, original)
    assert load_settings(paths.settings_file).stop_services == []


def test_saving_never_leaves_the_legacy_service_key_behind(paths):
    """The two keys must not be able to disagree once one of them has been
    written back out."""
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nservice: klipper-1\n")
    save_settings(paths.settings_file, Settings(stop_services=["klipper"]))
    with open(paths.settings_file, encoding="utf-8") as fh:
        out = fh.read()
    assert "service:" not in out
    assert load_settings(paths.settings_file).stop_services == ["klipper"]


# --------------------------------------------------------------------------
# sharing a file with the registry
# --------------------------------------------------------------------------


def test_settings_and_the_registry_are_the_same_file(paths):
    """One file to find and one file to edit - and the reason every test below
    exists, because now a careless write to either destroys the other."""
    assert paths.settings_file == paths.registry_file == paths.main_config


def test_saving_settings_keeps_the_mcu_sections_and_the_comments(paths, live_registry_text):
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(live_registry_text)

    save_settings(paths.settings_file, Settings(enable_flashing=True, make_jobs=3))

    with open(paths.main_config, encoding="utf-8") as fh:
        out = fh.read()
    assert "# mcu-updater configuration." in out
    assert "src/Makefile -> src-y += buffer.c" in out

    reg = Registry.load(paths)
    assert reg.names() == [
        "OctopusMAXEZ",
        "bttebb36",
        "cartographer",
        "flylllplusbuffer",
        "hexadistrofusion",
    ]
    assert len(reg.all_serials()) == 12
    assert load_settings(paths.settings_file).enable_flashing is True


def test_saving_the_registry_keeps_the_settings(paths, live_registry_text):
    """The inverse. The panel writes both, and neither write knows about the other."""
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(live_registry_text)
    save_settings(paths.settings_file, Settings(enable_flashing=True))

    reg = Registry.load(paths)
    reg.add_serial("bttebb36", "NEWBOARD-if00")
    reg.save(paths)

    assert load_settings(paths.settings_file).enable_flashing is True
    assert "NEWBOARD-if00" in Registry.load(paths).get("bttebb36").serials


def test_a_registry_only_file_yields_default_settings(paths, live_registry_text):
    """No [updater] section is the normal case: everything has a default."""
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(live_registry_text)
    assert load_settings(paths.settings_file) == Settings()


def test_repeated_saves_do_not_grow_the_file(paths, live_registry_text):
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(live_registry_text)
    for jobs in range(4):
        save_settings(paths.settings_file, Settings(make_jobs=jobs))
    with open(paths.main_config, encoding="utf-8") as fh:
        out = fh.read()
    assert out.count("[updater]") == 1
    # Only the real key - the sample also carries a commented-out `#make_jobs: 0`
    # documenting the default, which must survive and must not be counted.
    assert out.count("\nmake_jobs:") == 1
    assert "#make_jobs: 0" in out
    assert "\n\n\n" not in out


def test_a_second_updater_section_is_refused(paths):
    """Appending a block rather than editing the existing one is the natural
    mistake, and first-wins would make `enable_flashing: true` do nothing at all."""
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[updater]\nmake_jobs: 2\n\n[updater]\nenable_flashing: true\n")
    with pytest.raises(ConfigError) as exc:
        load_settings(paths.settings_file)
    assert "more than one [updater]" in str(exc.value)


def test_a_duplicate_mcu_section_is_refused(paths):
    """Same trap on the registry side: the second board's serials would vanish."""
    from mcu_updater.errors import ConfigCorruptError

    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[mcu a]\nchipset: x\nserials:\n    S1\n\n[mcu a]\nserials:\n    S2\n")
    with pytest.raises(ConfigCorruptError) as exc:
        Registry.load(paths)
    assert "[mcu a]" in str(exc.value)


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [(0, []), (1, ["-j1"]), (8, ["-j8"])],
)
def test_make_flags(jobs, expected):
    assert Settings(make_jobs=jobs).make_flags() == expected


def test_negative_jobs_means_auto():
    flags = Settings(make_jobs=-1).make_flags()
    assert len(flags) == 1 and flags[0].startswith("-j")


def test_platformio_bin_is_read_from_the_updater_section(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nplatformio_bin: /opt/pio/bin/pio\n")
    assert load_settings(paths.settings_file).platformio_bin == "/opt/pio/bin/pio"


def test_flashtool_path_is_read_from_the_updater_section(paths):
    with open(paths.settings_file, "w", encoding="utf-8") as fh:
        fh.write("[updater]\nflashtool_path: ~/forked-katapult/scripts/flashtool.py\n")
    assert (
        load_settings(paths.settings_file).flashtool_path
        == "~/forked-katapult/scripts/flashtool.py"
    )


def test_pio_settings_default_to_empty(paths):
    s = load_settings(paths.settings_file)
    assert s.platformio_bin == ""
    assert s.flashtool_path == ""


def test_every_settings_field_the_code_reads_actually_exists():
    """A getattr-with-a-default cannot tell a missing field from an unset one.

    Nothing outside this module should reach a setting defensively.
    """
    import pathlib
    import re

    # Three-argument getattr only: the trailing comma is the default. The
    # two-argument form over dataclasses.fields() in save_settings is how that
    # function is meant to work.
    defensive = re.compile(r"""getattr\(\s*(?:self\.)?settings(?:\(\))?\s*,\s*["'][^"']+["']\s*,""")

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "mcu_updater"
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in src.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if defensive.search(line)
    ]
    assert not offenders, f"settings reached defensively at: {offenders}"
