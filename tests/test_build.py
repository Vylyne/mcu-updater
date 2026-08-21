from __future__ import annotations

import json

import pytest

from mcu_updater.build import artifact_status, build, read_sidecar
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigNotFoundError, SourceTreeMissingError

from .conftest import cmd_tokens


def _registry(paths) -> Registry:
    reg = Registry.load(paths)
    reg.add_type("board", "stm32f072xb")
    reg.save(paths)
    return reg


def _write_config(paths, mcu_type="board", fw="klipper", body="CONFIG_MACH_STM32=y\n"):
    import os

    os.makedirs(paths.type_dir(mcu_type), exist_ok=True)
    with open(paths.config_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        fh.write(body)


def test_missing_saved_config_raises(paths, settings):
    reg = _registry(paths)
    with pytest.raises(ConfigNotFoundError) as exc:
        build(paths, reg, settings, "board", "klipper")
    assert exc.value.code == "no_saved_config"
    assert "menuconfig" in str(exc.value)


def test_missing_source_tree_raises(paths, settings, fake_root):
    reg = _registry(paths)
    _write_config(paths)
    import shutil

    shutil.rmtree(fake_root / "klipper")
    with pytest.raises(SourceTreeMissingError):
        build(paths, reg, settings, "board", "klipper")


def test_dry_run_produces_a_real_stub_artifact_and_sidecar(paths, settings):
    """Dry run writes an actual file so downstream artifact/staleness logic is
    exercised for real rather than being special-cased."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)

    result = build(paths, reg, settings, "board", "klipper")

    assert result.bin_path == paths.bin_file("board", "klipper")
    with open(result.bin_path, "rb") as fh:
        assert len(fh.read()) == 1024

    side = read_sidecar(paths, "board", "klipper")
    assert side is not None
    assert side["config_sha256"] == result.config_sha256
    assert "timestamp" in side


def test_a_build_records_its_own_app_address(paths, settings):
    """Read from the built .config, so flash time can compare it against what
    the board's own bootloader reports without a Kconfig parse."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths, body="CONFIG_MACH_STM32=y\nCONFIG_FLASH_APPLICATION_ADDRESS=0x08004000\n")

    result = build(paths, reg, settings, "board", "klipper")

    assert result.app_address == 0x08004000
    side = read_sidecar(paths, "board", "klipper")
    assert side["app_address"] == 0x08004000


def test_a_build_leaves_app_address_none_when_the_tree_defines_none(paths, settings):
    """A bootloader build, or any tree with no such symbol - not an error, just
    nothing to compare at flash time."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)  # default body has no FLASH_APPLICATION_ADDRESS

    result = build(paths, reg, settings, "board", "klipper")

    assert result.app_address is None
    side = read_sidecar(paths, "board", "klipper")
    assert side["app_address"] is None


def test_a_build_records_its_stamped_version(paths, settings):
    """Read from the built .config, mirroring app_address exactly - so flash
    time can compare a Cartographer's CONFIG_VERSION against what it stamps,
    without a Kconfig parse."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(
        paths, body='CONFIG_MACH_STM32=y\nCONFIG_VERSION="CARTOGRAPHER 6.2.0"\n'
    )

    result = build(paths, reg, settings, "board", "klipper")

    assert result.version == "CARTOGRAPHER 6.2.0"
    side = read_sidecar(paths, "board", "klipper")
    assert side["version"] == "CARTOGRAPHER 6.2.0"


def test_a_build_leaves_version_none_when_the_tree_defines_no_such_symbol(paths, settings):
    """The regression guard: upstream Klipper and Katapult define no VERSION
    symbol, so this must stay None - not an error, and the rest of the
    sidecar shape and verdict must be exactly what they were before this
    field existed."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)  # default body has no CONFIG_VERSION

    result = build(paths, reg, settings, "board", "klipper")

    assert result.version is None
    side = read_sidecar(paths, "board", "klipper")
    assert side["version"] is None
    assert artifact_status(paths, "board", "klipper").is_current


def test_artifact_status_reports_never_built_then_current(paths, settings):
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)

    assert artifact_status(paths, "board", "klipper").reason == "never_built"
    build(paths, reg, settings, "board", "klipper")
    assert artifact_status(paths, "board", "klipper").reason is None


def test_artifact_status_detects_a_changed_config(paths, settings):
    """Compares recorded hashes, not mtimes, so a touch doesn't lie."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)
    build(paths, reg, settings, "board", "klipper")

    _write_config(paths, body="CONFIG_MACH_STM32=y\nCONFIG_USBSERIAL=y\n")
    assert artifact_status(paths, "board", "klipper").reason == "config_changed"


def test_artifact_status_detects_a_changed_source_tree(paths, settings):
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)
    build(paths, reg, settings, "board", "klipper")

    # Forge the recorded firmware sha to simulate a `git pull` of klipper.
    side_path = paths.sidecar_file("board", "klipper")
    side = json.load(open(side_path, encoding="utf-8"))
    side["fw_sha"] = "deadbee"
    with open(side_path, "w", encoding="utf-8") as fh:
        json.dump(side, fh)

    status = artifact_status(paths, "board", "klipper")
    if side.get("fw_sha") and _has_git(paths):
        assert status.reason == "source_changed"
    else:
        # No git available / not a checkout: nothing to compare against, so the
        # build is reported current rather than falsely stale.
        assert status.is_current


def _has_git(paths) -> bool:
    from mcu_updater.build import git_head

    return git_head(paths.fw_dir("klipper")) is not None


def test_missing_sidecar_means_no_provenance(paths, settings):
    """Distinct from never having built at all: a binary is on disk, there is
    just nothing recorded about what produced it."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)
    build(paths, reg, settings, "board", "klipper")

    import os

    os.unlink(paths.sidecar_file("board", "klipper"))
    status = artifact_status(paths, "board", "klipper")
    assert status.reason == "no_provenance"
    assert not status.is_current


def test_extra_args_are_split_shell_style(paths, settings):
    """extra_args goes on the make command line, so it must tokenise properly."""
    settings.dry_run = True
    reg = _registry(paths)
    reg.get("board").fw("klipper").extra_args = 'FOO=bar BAZ="a b"'
    reg.save(paths)
    _write_config(paths)

    cmds: list[str] = []
    build(
        paths,
        reg,
        settings,
        "board",
        "klipper",
        reporter=lambda s, line: cmds.append(line) if s == "cmd" else None,
    )
    make_cmd = [c for c in cmds if "KCONFIG_CONFIG" in c][-1]
    assert "FOO=bar" in make_cmd
    assert "a b" in make_cmd


def test_jobs_argument_adds_a_make_flag(paths, settings):
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)

    cmds: list[str] = []
    build(
        paths,
        reg,
        settings,
        "board",
        "klipper",
        jobs=4,
        reporter=lambda s, line: cmds.append(line) if s == "cmd" else None,
    )
    assert any("-j4" in cmd_tokens(c) for c in cmds)


def test_no_jobs_by_default_matching_the_original(paths, settings):
    """The original never passed -j. Opt in explicitly rather than silently
    changing everyone's build."""
    settings.dry_run = True
    reg = _registry(paths)
    _write_config(paths)

    cmds: list[str] = []
    build(
        paths,
        reg,
        settings,
        "board",
        "klipper",
        reporter=lambda s, line: cmds.append(line) if s == "cmd" else None,
    )
    flags = [t for c in cmds for t in cmd_tokens(c)]
    assert not any(t.startswith("-j") for t in flags)


# --------------------------------------------------------------------------
# FlashLog - which binary each board actually holds
#
# The gap it closes: a board reports its klipper *commit* and nothing else, so two
# builds from the same commit (an edited makefile-patch source, a changed .config)
# are indistinguishable from the board's side. Without this record, "flash only the
# stale ones" silently skips exactly the boards a patch change affected.
# --------------------------------------------------------------------------


def test_a_build_records_the_binary_hash(paths, live_registry_text):
    """Dry run, which stages a stub .bin and a real sidecar precisely so the
    downstream provenance logic needs no special case."""
    import dataclasses

    from mcu_updater.build import build, read_sidecar
    from mcu_updater.config import Registry
    from mcu_updater.settings import Settings

    dry = dataclasses.replace(
        Settings(), dry_run=True, service_backend="null", clean_before_build=False
    )
    _stage_registry(paths, live_registry_text)
    _stage_config(paths, "bttebb36", "klipper")
    build(paths, Registry.load(paths), dry, "bttebb36", "klipper")

    side = read_sidecar(paths, "bttebb36", "klipper")
    assert side is not None
    assert side["bin_sha256"], "the artifact's own hash is the piece a board cannot report"
    assert len(side["bin_sha256"]) == 64


def test_a_record_round_trips(paths):
    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    assert log.all() == {}

    log.record("A-if00", mcu_type="bttebb36", fw="klipper", bin_sha256="aa" * 32, fw_sha="d7cea5bb")
    entry = log.entry_for("A-if00", "d7cea5bb")
    assert entry is not None
    assert entry["bin_sha256"] == "aa" * 32
    assert entry["type"] == "bttebb36"
    assert entry["at"] > 0


def test_a_record_is_discarded_when_the_board_disagrees(paths):
    """Someone flashed by hand outside the tool, so our note about which binary the
    board holds is no longer evidence of anything. Better to report unknown than a
    stale answer with a straight face."""
    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    log.record("A-if00", mcu_type="t", fw="klipper", bin_sha256="aa" * 32, fw_sha="d7cea5bb")

    assert log.entry_for("A-if00", "d7cea5bb") is not None
    assert log.entry_for("A-if00", "aaaaaaaa") is None, "a different running commit invalidates it"
    # No running commit to check against: the record stands, since we have nothing
    # contradicting it.
    assert log.entry_for("A-if00", None) is not None


def test_a_corrupt_log_reads_as_empty(paths):
    """Losing this degrades answers to "unknown", which is survivable - raising in
    the middle of painting a status panel is not."""
    import os

    from mcu_updater.build import FlashLog

    os.makedirs(paths.data_dir, exist_ok=True)
    with open(paths.flashlog_file, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert FlashLog(paths).all() == {}
    # ...and it recovers by being overwritten.
    FlashLog(paths).record("A", mcu_type="t", fw="klipper", bin_sha256="x", fw_sha="y")
    assert "A" in FlashLog(paths).all()


def test_records_for_other_boards_survive_a_write(paths):
    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    log.record("A", mcu_type="t", fw="klipper", bin_sha256="aa", fw_sha="1")
    log.record("B", mcu_type="t", fw="klipper", bin_sha256="bb", fw_sha="2")
    assert set(log.all()) == {"A", "B"}


def test_forget_drops_one_record(paths):
    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    log.record("A", mcu_type="t", fw="klipper", bin_sha256="aa", fw_sha="1")
    assert log.forget("A") is True
    assert log.forget("A") is False
    assert log.all() == {}


def test_no_temp_file_is_left_behind(paths):
    import os

    from mcu_updater.build import FlashLog

    log = FlashLog(paths)
    log.record("A", mcu_type="t", fw="klipper", bin_sha256="aa", fw_sha="1")
    assert not os.path.exists(paths.flashlog_file + ".tmp")

def _stage_registry(paths, text: str) -> None:
    import os

    os.makedirs(paths.config_dir, exist_ok=True)
    with open(paths.registry_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _stage_config(paths, mcu_type: str, fw: str) -> None:
    import os

    os.makedirs(paths.type_dir(mcu_type), exist_ok=True)
    with open(paths.config_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")
