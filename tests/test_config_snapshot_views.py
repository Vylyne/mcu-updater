"""Each loader keeps its own rules when it reads through the snapshot, and
every writer reads the file fresh under its lock.

Files are aged past the racy window, so these run the cached path the rest of
the suite never reaches. `_stat_key` is pinned where a test needs a rewrite
the cache cannot see - the only way to make "the writer read fresh" observable.
"""

from __future__ import annotations

import os
import time

import pytest

from mcu_updater import cfgsnapshot, firmware, settings, typelist
from mcu_updater.cfgdoc import FrozenDocumentError
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigCorruptError, ConfigError

from .conftest import with_base_firmwares

AGED_NS = 10 * cfgsnapshot.RACY_WINDOW_NS

TYPE = "[type a]\nchipset: stm32f072xb\nfirmware: klipper\nserials:\n    S1\n"


def _write(paths, text: str) -> None:
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    then = time.time_ns() - AGED_NS
    os.utime(paths.main_config, ns=(then, then))


def _unreadable(paths) -> None:
    os.makedirs(paths.main_config)


@pytest.fixture
def blind_cache(monkeypatch):
    """Every rewrite is invisible to the stat key from here on."""
    monkeypatch.setattr(cfgsnapshot, "_stat_key", lambda st: (0, 0, 0, 0, 0))


# --- typelist.read_doc: None / ConfigCorruptError / every duplicate refused ---


def test_read_doc_of_a_missing_file_is_none(paths):
    assert typelist.read_doc(paths) is None


def test_read_doc_of_an_unreadable_file_is_corrupt(paths):
    _unreadable(paths)
    with pytest.raises(ConfigCorruptError, match="could not read"):
        typelist.read_doc(paths)


def test_read_doc_refuses_a_duplicate_section(paths):
    _write(paths, "[type a]\nchipset: x\n\n[type a]\nchipset: y\n")
    with pytest.raises(ConfigCorruptError, match="duplicate section"):
        typelist.read_doc(paths)


def test_a_duplicate_section_error_does_not_hand_out_the_shared_list(paths):
    """The error travels to handlers and the wire; whatever they do to its
    value must not reach the parse every other reader shares."""
    _write(paths, "[type a]\nchipset: x\n\n[type a]\nchipset: y\n")
    with pytest.raises(ConfigCorruptError) as caught:
        typelist.read_doc(paths)

    caught.value.data["value"].append("type b")

    shared = cfgsnapshot.read(paths.main_config)
    assert shared is not None and shared.duplicate_sections == ["type a"]


# --- typelist.read_config and firmware.load: lenient ---


def test_read_config_of_a_missing_or_unreadable_file_is_empty(paths):
    assert typelist.read_config(paths) == ([], {})
    _unreadable(paths)
    assert typelist.read_config(paths) == ([], {})


def test_read_config_tolerates_a_duplicate_section(paths):
    _write(paths, with_base_firmwares(TYPE + "\n" + TYPE))
    entries, families = typelist.read_config(paths)
    assert [e.name for e in entries] == ["a"]
    assert "klipper" in families


def test_firmware_load_of_a_missing_or_unreadable_file_is_empty(paths):
    assert firmware.load(paths) == {}
    _unreadable(paths)
    assert firmware.load(paths) == {}


def test_firmware_load_tolerates_a_duplicate_section(paths):
    _write(paths, with_base_firmwares(TYPE + "\n" + TYPE))
    assert "klipper" in firmware.load(paths)


# --- settings.load_settings: defaults / ConfigError / only [updater] refused ---


def test_load_settings_of_a_missing_file_is_the_defaults(paths):
    assert settings.load_settings(paths.settings_file) == settings.Settings()


def test_load_settings_of_an_unreadable_file_is_a_config_error(paths):
    _unreadable(paths)
    with pytest.raises(ConfigError, match="could not read"):
        settings.load_settings(paths.settings_file)


def test_load_settings_refuses_only_a_duplicate_updater(paths):
    _write(paths, "[updater]\nmake_jobs: 1\n\n[type a]\n\n[type a]\n")
    assert settings.load_settings(paths.settings_file).make_jobs == 1

    _write(paths, "[updater]\nmake_jobs: 1\n\n[updater]\nmake_jobs: 2\n")
    with pytest.raises(ConfigError, match="more than one"):
        settings.load_settings(paths.settings_file)


# --- writers read fresh ---


def test_registry_mutate_reads_the_file_not_the_snapshot(paths, blind_cache):
    _write(paths, with_base_firmwares(TYPE))
    assert Registry.load(paths).get("a").serials == ["S1"]

    _write(paths, with_base_firmwares(TYPE.replace("S1", "S2")))
    # The pinned key hides the rewrite from every reader...
    assert Registry.load(paths).get("a").serials == ["S1"]
    # ...but not from the writer, which reads under its lock.
    with Registry.mutate(paths, "test") as reg:
        assert reg.get("a").serials == ["S2"]


def test_settings_mutate_reads_the_file_not_the_snapshot(paths, blind_cache):
    _write(paths, "[updater]\nmake_jobs: 1\n")
    assert settings.load_settings(paths.settings_file).make_jobs == 1

    _write(paths, "[updater]\nmake_jobs: 2\n")
    assert settings.load_settings(paths.settings_file).make_jobs == 1
    with settings.mutate(paths, "test") as current:
        assert current.make_jobs == 2


def test_every_writer_drops_the_snapshot(paths, blind_cache):
    """Belt and braces: with the key blind, only `invalidate` can show a
    reader the write."""
    _write(paths, with_base_firmwares(TYPE))
    Registry.load(paths)
    with Registry.mutate(paths, "test") as reg:
        reg.add_serial("a", "S9")
    assert "S9" in Registry.load(paths).get("a").serials

    settings.load_settings(paths.settings_file)
    with settings.mutate(paths, "test") as current:
        current.make_jobs = 7
    assert settings.load_settings(paths.settings_file).make_jobs == 7


def test_seeding_drops_the_snapshot(paths, blind_cache):
    from mcu_updater import seed

    _write(paths, "[updater]\nmake_jobs: 1\n")
    assert firmware.load(paths) == {}

    seed.seed_firmware_sections(paths, {})

    assert set(firmware.load(paths)) == {"klipper", "katapult"}


def test_a_read_only_registry_cannot_edit_the_shared_document(paths):
    _write(paths, with_base_firmwares(TYPE))
    reg = Registry.load(paths)

    with pytest.raises(FrozenDocumentError):
        reg.remove_declared_type("a")
