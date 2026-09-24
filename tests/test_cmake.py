"""The cmake provider: config parsing, subtree-scoped provenance, staging.

Mirrors `test_pio.py`, which is the closest existing suite. The parts that are
deliberately *not* copied from it - subtree-scoped git, an artifact path the
provider owns - have their own tests saying why.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import types

import pytest

from mcu_updater import providers
from mcu_updater.artifacts import KIND_BIN
from mcu_updater.errors import BuildError, ConfigError
from mcu_updater.providers import Cmake, Install, cmake
from mcu_updater.states import (
    BUILT_DIRTY,
    CONFIG_CHANGED,
    FOREIGN_BUILD,
    NEVER_BUILT,
    NO_PROVENANCE,
    SOURCE_CHANGED,
)

from .conftest import cmd_tokens

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def write_config(paths, text: str) -> None:
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text)


ROADRUNNER_CFG = """
[firmware roadrunner]
source: {source}
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${{git_describe}}
flashers: bootsel

[type roadrunner]
chipset: rp2040
firmware: roadrunner
cmake_target: roadrunner_v1_i2c_rgb
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
"""


def test_a_cmake_type_loads_with_its_target_and_args(paths, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    types = cmake.load(paths)
    assert set(types) == {"roadrunner"}
    rr = types["roadrunner"]
    assert rr.cmake_target == "roadrunner_v1_i2c_rgb"
    assert rr.firmware == "roadrunner"
    assert rr.source == str(tmp_path)
    assert rr.cmake_args == "-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"
    assert rr.submodules is False
    assert rr.chipset == "rp2040"
    assert rr.serials == ["RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    assert rr.stop_services is None


def test_a_cmake_type_loads_its_own_stop_services_override(paths, tmp_path):
    write_config(
        paths,
        ROADRUNNER_CFG.format(source=tmp_path).replace(
            "cmake_target: roadrunner_v1_i2c_rgb",
            "cmake_target: roadrunner_v1_i2c_rgb\nstop_services: klipper, roadrunner-watch",
        ),
    )

    assert cmake.load(paths)["roadrunner"].stop_services == [
        "klipper",
        "roadrunner-watch",
    ]


def test_the_families_submodules_key_reaches_the_type(paths, tmp_path):
    """`submodules:` is a fact about the tree, so it is written on the family
    and projected onto every type that family builds - the same route
    `cmake_args:` takes, and for the same reason."""
    write_config(
        paths,
        ROADRUNNER_CFG.format(source=tmp_path).replace(
            "builder: cmake", "builder: cmake\nsubmodules: yes"
        ),
    )
    assert cmake.load(paths)["roadrunner"].submodules is True


def test_a_kconfig_type_is_not_ours(paths):
    """Provider is derived from the declared family's builder, exactly as
    pio.load() derives its own."""
    write_config(
        paths,
        "[type bttebb36]\nchipset: stm32g0b1xx\nfirmware: klipper, katapult\nserials:\n",
    )
    assert cmake.load(paths) == {}


def test_a_cmake_type_with_no_target_is_refused(paths, tmp_path):
    """The same refusal pio.load() gives a PlatformIO type with no env:. A
    default would mean guessing which of six images belongs on the board."""
    write_config(
        paths,
        f"[firmware roadrunner]\nsource: {tmp_path}\nbuilder: cmake\nflashers: bootsel\n\n"
        "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\nserials:\n",
    )
    with pytest.raises(ConfigError) as exc:
        cmake.load(paths)
    assert "roadrunner" in str(exc.value)
    assert "cmake_target" in str(exc.value)


def test_a_missing_config_file_is_not_an_error(paths):
    """No config means no cmake types, which is what every install has today."""
    assert cmake.load(paths) == {}


def _git(directory, *args) -> str:
    return subprocess.run(
        ("git",) + args, cwd=directory, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo whose firmware source is a *subdirectory*, with a sibling.

    This is the Roadrunner's shape: rp2040/ beside klippy/, both edited
    independently. It is what makes repo-wide git wrong here.
    """
    root = tmp_path / "roadrunner"
    (root / "rp2040").mkdir(parents=True)
    (root / "klippy").mkdir()
    (root / "rp2040" / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (root / "klippy" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


def test_provenance_ignores_a_commit_outside_the_source_subtree(repo):
    """The reason this provider does not reuse pio.source_state().

    A commit to klippy/ moves repo HEAD but changes nothing the firmware is
    built from. Reporting it as a source change rebuilds the .uf2 to produce
    byte-identical output, on every unrelated commit.
    """
    source = str(repo / "rp2040")
    before = cmake.source_state(source)

    (repo / "klippy" / "extra.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch klippy only")

    after = cmake.source_state(source)
    assert after.sha == before.sha
    assert _git(repo, "rev-parse", "HEAD") != before.sha


def test_provenance_follows_a_commit_inside_the_source_subtree(repo):
    source = str(repo / "rp2040")
    before = cmake.source_state(source)

    (repo / "rp2040" / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch rp2040")

    assert cmake.source_state(source).sha != before.sha


def test_dirtiness_is_scoped_to_the_subtree_too(repo):
    """A dirty sibling must not stamp -dirty on a clean firmware build."""
    source = str(repo / "rp2040")
    (repo / "klippy" / "extra.py").write_text("uncommitted\n", encoding="utf-8")
    state = cmake.source_state(source)
    assert state.dirty is False
    assert state.version is not None and not state.version.endswith("-dirty")

    (repo / "rp2040" / "CMakeLists.txt").write_text("project(rr2)\n", encoding="utf-8")
    dirty = cmake.source_state(source)
    assert dirty.dirty is True
    assert dirty.version.endswith("-dirty")


def test_the_version_string_is_repo_wide_and_carries_the_hash(repo):
    """A tag is a fact about a repository, not one directory in it. With no
    tags, --always still yields the commit hash, which is what makes this
    usable on develop."""
    state = cmake.source_state(str(repo / "rp2040"))
    assert state.version
    assert state.version != "dev"

    _git(repo, "tag", "v1.0")
    assert cmake.source_state(str(repo / "rp2040")).version == "v1.0"


def test_a_non_checkout_yields_no_provenance(tmp_path):
    assert cmake.source_state(str(tmp_path)) == cmake.SourceState()
    assert cmake.source_state("") == cmake.SourceState()


def test_git_describe_is_substituted_into_the_configure_args(repo):
    state = cmake.source_state(str(repo / "rp2040"))
    args = cmake.expand_args("-DROADRUNNER_FIRMWARE_VERSION=${git_describe}", state)
    assert args == [f"-DROADRUNNER_FIRMWARE_VERSION={state.version}"]


def test_git_describe_falls_back_to_dev_outside_a_checkout(tmp_path):
    """`dev` is the CMakeLists' own default, so the fallback agrees with the
    firmware rather than inventing a third answer."""
    state = cmake.source_state(str(tmp_path))
    assert cmake.expand_args("-DV=${git_describe}", state) == ["-DV=dev"]


def test_empty_args_produce_no_arguments():
    assert cmake.expand_args("", cmake.SourceState()) == []


def test_args_are_split_as_a_shell_would(repo):
    """One config string, several arguments - and a quoted value stays one."""
    state = cmake.source_state(str(repo / "rp2040"))
    assert cmake.expand_args('-DA=1 -DB="two words"', state) == ["-DA=1", "-DB=two words"]


def _cmake_type(source, target="roadrunner_v1_i2c_rgb"):
    return cmake.CmakeType(
        name="roadrunner", cmake_target=target, source=str(source), firmware="roadrunner"
    )


def test_no_source_names_the_missing_key(tmp_path):
    problem = cmake.source_problem(cmake.CmakeType(name="roadrunner", cmake_target="t"))
    assert problem is not None
    assert "source:" in problem


def test_a_source_that_does_not_exist_says_so_differently(tmp_path):
    """Absent and non-existent are separated because the fixes differ - one is
    a missing key, the other a path that is there and wrong. Same split as
    pio.source_problem."""
    missing = tmp_path / "nope"
    problem = cmake.source_problem(_cmake_type(missing))
    assert problem is not None
    assert str(missing) in problem
    assert "source:" not in problem


def test_a_tree_with_no_cmakelists_is_blocked(tmp_path):
    problem = cmake.source_problem(_cmake_type(tmp_path))
    assert problem is not None
    assert "CMakeLists.txt" in problem


def test_an_uninitialised_submodule_is_named_with_its_fix(tmp_path):
    """The CMake error for this is unreadable, so it is worth catching first.
    Read from .gitmodules rather than hardcoding pico-sdk/, which is the Pico
    SDK's path and not a fact about cmake trees."""
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "pico-sdk"]\n\tpath = pico-sdk\n\turl = https://example/x\n',
        encoding="utf-8",
    )
    (tmp_path / "pico-sdk").mkdir()
    problem = cmake.source_problem(_cmake_type(tmp_path))
    assert problem is not None
    assert "pico-sdk" in problem
    assert "git submodule update --init --recursive" in problem


def test_a_tree_that_syncs_its_own_submodules_is_not_blocked_by_an_empty_one(tmp_path):
    """The refusal above tells the user to run the command that `submodules:
    yes` runs for them. Keeping it would make the key unreachable in the one
    case it exists for: the first build after a fresh clone."""
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "pico-sdk"]\n\tpath = pico-sdk\n\turl = https://example/x\n',
        encoding="utf-8",
    )
    (tmp_path / "pico-sdk").mkdir()
    target = _cmake_type(tmp_path)
    target.submodules = True
    problem = cmake.source_problem(target)
    assert problem is None or "submodule" not in problem


def test_a_populated_submodule_is_not_a_problem(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "pico-sdk"]\n\tpath = pico-sdk\n', encoding="utf-8"
    )
    (tmp_path / "pico-sdk").mkdir()
    (tmp_path / "pico-sdk" / "pico_sdk_init.cmake").write_text("", encoding="utf-8")
    assert cmake.source_problem(_cmake_type(tmp_path)) is None


def test_target_list_is_unknown_before_a_configure(tmp_path):
    """No build directory means nothing to ask. Returning None rather than an
    empty set matters: empty would read as 'this tree declares no targets' and
    block every type."""
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    assert cmake.declared_targets(str(tmp_path)) is None


def test_a_mistyped_target_is_blocked_once_the_list_is_known(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    monkeypatch.setattr(
        cmake, "declared_targets", lambda source: {"roadrunner_v1_i2c_rgb", "clean"}
    )
    problem = cmake.source_problem(_cmake_type(tmp_path, target="roadrunner_v1_i2c_rbg"))
    assert problem is not None
    assert "roadrunner_v1_i2c_rbg" in problem
    assert "roadrunner_v1_i2c_rgb" in problem


def test_a_known_target_is_not_blocked(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"roadrunner_v1_i2c_rgb"})
    assert cmake.source_problem(_cmake_type(tmp_path)) is None


def test_the_target_help_output_is_parsed(tmp_path, monkeypatch):
    """`cmake --build <dir> --target help` prints one '... <name>' per line,
    with the default target annotated."""
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(
        cmake,
        "_run",
        lambda *a, **k: (
            "The following are some of the valid targets for this Makefile:\n"
            "... all (the default if no target is provided)\n"
            "... clean\n"
            "... roadrunner_v1_i2c_rgb\n"
            "... roadrunner_v1_uart_grb\n"
        ),
    )
    assert cmake.declared_targets(str(tmp_path)) == {
        "all",
        "clean",
        "roadrunner_v1_i2c_rgb",
        "roadrunner_v1_uart_grb",
    }


@pytest.fixture
def capture_reporter():
    class Capture:
        def __init__(self):
            self.lines: list[tuple[str, str]] = []

        def reporter(self, stream: str, line: str) -> None:
            self.lines.append((stream, line))

    return Capture()


def test_a_configured_tree_is_not_reconfigured(tmp_path):
    """Re-running configure on every build is slow and changes nothing."""
    source = tmp_path / "rp2040"
    (source / "build").mkdir(parents=True)
    assert cmake.needs_configure(str(source)) is True

    (source / "build" / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
    )
    assert cmake.needs_configure(str(source)) is False


def test_a_cache_from_another_tree_forces_a_reconfigure(tmp_path):
    """A moved or copied build directory points at the wrong sources."""
    source = tmp_path / "rp2040"
    (source / "build").mkdir(parents=True)
    (source / "build" / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=/somewhere/else\n", encoding="utf-8"
    )
    assert cmake.needs_configure(str(source)) is True


def test_a_dry_run_stages_nothing_and_runs_both_steps(paths, settings, tmp_path, capture_reporter):
    """A rehearsal must never leave an artifact behind, or the next status
    poll vouches for a build that did not happen."""
    source = tmp_path / "rp2040"
    source.mkdir()
    (source / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    target = _cmake_type(source)

    dry = dataclasses.replace(settings, dry_run=True)
    cmake.build(paths, dry, target, reporter=capture_reporter.reporter)

    cmds = [line for stream, line in capture_reporter.lines if stream == "cmd"]
    assert any("cmake" in c and "-S" in c for c in cmds)
    # Not `any("make" in c for c in cmds)`: that is also true of the *cmake*
    # line ("make" is a substring of "cmake"), so it stayed green with the
    # entire make invocation deleted from build(). Check the command's own
    # first token instead.
    assert any(cmd_tokens(c)[0] == "make" for c in cmds)
    assert not os.path.exists(paths.uf2_file("roadrunner", "roadrunner"))


def test_a_successful_build_stages_the_named_target(paths, settings, repo, monkeypatch):
    """The whole point of cmake_target: one make produces six images and
    exactly one is staged."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    target = _cmake_type(source)

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        reporter("cmd", " ".join(cmd))
        for name in ("roadrunner_v1_i2c_rgb", "roadrunner_v1_uart_grb"):
            (source / "build" / f"{name}.uf2").write_bytes(name.encode())
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    staged = cmake.build(paths, settings, target)

    assert staged == paths.uf2_file("roadrunner", "roadrunner")
    assert open(staged, "rb").read() == b"roadrunner_v1_i2c_rgb"


def test_a_nonzero_exit_raises_build_error(paths, settings, tmp_path, monkeypatch):
    """A bare, unconfigured source would let `needs_configure` consume this rc
    on the configure branch and never reach the make check at all - the tree
    already carries a matching cache so this exercises `make`'s own rc."""
    source = tmp_path / "rp2040"
    (source / "build").mkdir(parents=True)
    (source / "build" / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
    )
    monkeypatch.setattr(cmake.build_mod, "run_streamed", lambda *a, **k: 2)
    with pytest.raises(BuildError) as exc:
        cmake.build(paths, settings, _cmake_type(source))
    assert exc.value.data["returncode"] == 2


def test_a_missing_output_is_a_build_error_naming_the_target(paths, settings, repo, monkeypatch):
    """make succeeded but the named target produced nothing - a typo that
    survived because the tree was never configured when blocked() ran."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(cmake.build_mod, "run_streamed", lambda *a, **k: 0)
    with pytest.raises(BuildError) as exc:
        cmake.build(paths, settings, _cmake_type(source))
    assert "roadrunner_v1_i2c_rgb" in str(exc.value)


def _leaves(source, *names):
    """A run_streamed stand-in that leaves the named images in build/."""

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        for name in names:
            (source / "build" / f"{name}.uf2").write_bytes(name.encode())
        return 0

    return fake_run


def test_a_stale_image_from_a_target_this_tree_no_longer_declares_is_refused(
    paths, settings, repo, monkeypatch
):
    """`make` builds `all`, so it still succeeds after an upstream rename drops
    the configured target - and cmake does not delete a removed target's
    output. Without a post-make check on the declared list, the previous
    build's `.uf2` is staged and `record_build()` stamps those older bytes with
    today's subtree commit, which `artifact_status()` then calls current."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"DAY-ONE-IMAGE")
    monkeypatch.setattr(
        cmake.build_mod, "run_streamed", _leaves(source, "roadrunner_v2_i2c_rgb")
    )
    monkeypatch.setattr(
        cmake, "declared_targets", lambda source: {"all", "roadrunner_v2_i2c_rgb"}
    )

    target = _cmake_type(source)
    with pytest.raises(BuildError) as exc:
        cmake.build(paths, settings, target)

    assert "roadrunner_v1_i2c_rgb" in str(exc.value)
    assert "roadrunner_v2_i2c_rgb" in str(exc.value)
    # Nothing staged and nothing vouched for: a sidecar written here is the
    # whole failure, not a side effect of it.
    assert not os.path.exists(paths.uf2_file("roadrunner", "roadrunner"))
    assert cmake.read_sidecar(paths, target) is None


def test_a_declared_target_is_still_staged(paths, settings, repo, monkeypatch):
    """The other direction, so the refusal above is not passing merely because
    the check refuses everything."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(
        cmake.build_mod, "run_streamed", _leaves(source, "roadrunner_v1_i2c_rgb")
    )
    monkeypatch.setattr(
        cmake, "declared_targets", lambda source: {"all", "roadrunner_v1_i2c_rgb"}
    )

    staged = cmake.build(paths, settings, _cmake_type(source))
    assert open(staged, "rb").read() == b"roadrunner_v1_i2c_rgb"


def test_the_target_list_is_read_after_make_not_after_configure(
    paths, settings, repo, monkeypatch
):
    """Where the check sits, pinned.

    The configure step in `build()` is conditional: a tree with no
    `cmake_args:` whose cache already names it is not reconfigured, so a check
    asked before `make` would be answered by the build system an *earlier*
    configure generated - here, the one that still lists the removed v1 target.
    `make` re-runs cmake itself when CMakeLists.txt has moved, so only a
    post-make answer describes this tree. The stale answer below is what a
    post-configure check would have been given, and it would have staged day
    one's bytes."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"DAY-ONE-IMAGE")
    (source / "build" / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
    )
    assert cmake.needs_configure(str(source)) is False

    regenerated: list[bool] = []
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        if cmd[0] == "make":
            # What cmake_check_build_system does during `make`.
            regenerated.append(True)
            (source / "build" / "roadrunner_v2_i2c_rgb.uf2").write_bytes(b"DAY-TWO")
        return 0

    def fake_targets(source_dir):
        if regenerated:
            return {"all", "roadrunner_v2_i2c_rgb"}
        return {"all", "roadrunner_v1_i2c_rgb"}

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    monkeypatch.setattr(cmake, "declared_targets", fake_targets)

    target = _cmake_type(source)
    with pytest.raises(BuildError):
        cmake.build(paths, settings, target)

    # The premise: no configure ran this build, so the pre-make list really was
    # the stale one. Without this the test could pass for the wrong reason.
    assert [c for c in seen if c[0] == "cmake"] == []
    assert not os.path.exists(paths.uf2_file("roadrunner", "roadrunner"))
    assert cmake.read_sidecar(paths, target) is None


def test_an_unaskable_target_list_does_not_block_the_build(
    paths, settings, repo, monkeypatch
):
    """None means "could not be asked", not "declares nothing" - a tree cmake
    cannot answer about must still build, exactly as it did before the check."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(
        cmake.build_mod, "run_streamed", _leaves(source, "roadrunner_v1_i2c_rgb")
    )
    monkeypatch.setattr(cmake, "declared_targets", lambda source: None)

    staged = cmake.build(paths, settings, _cmake_type(source))
    assert open(staged, "rb").read() == b"roadrunner_v1_i2c_rgb"


def test_the_sidecar_records_the_subtree_commit_and_the_bytes(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    (source / "build").mkdir()

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    cmake.build(paths, settings, target)

    record = cmake.read_sidecar(paths, target)
    assert record is not None
    assert record["sha"] == cmake.source_state(str(source)).sha
    assert record["version"] == cmake.source_state(str(source)).version
    assert record["bin_sha256"] is not None
    # Named on the write side too: the sidecar path is shared with
    # kconfig_make and this key is what keeps the two schemas apart.
    assert record["provider"] == "cmake"
    # `record["dirty"] is False` dropped here: it would pass just as well if
    # `dirty` were hardcoded False. Dirtiness is exercised where it can
    # actually be forced true or false - see
    # test_a_build_from_a_dirty_subtree_is_built_dirty below.


def test_an_edit_landing_during_the_build_is_recorded_dirty(
    paths, settings, repo, monkeypatch
):
    """Provenance is read after `make`, not before it. An uncommitted edit that
    lands mid-compile is in the image, and a record sampled at t0 would call
    those bytes a clean build of the pre-edit commit - permanently current,
    never rebuilt."""
    source = repo / "rp2040"
    (source / "build").mkdir()

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "edited_mid_build.c").write_text("oops\n", encoding="utf-8")
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    cmake.build(paths, settings, target)

    record = cmake.read_sidecar(paths, target)
    assert record["dirty"] is True
    state = cmake.source_state(str(source))
    assert cmake.artifact_status(paths, target, state).reason == BUILT_DIRTY


def test_a_commit_landing_during_the_build_is_recorded_dirty_too(
    paths, settings, repo, monkeypatch
):
    """The half of B2 that re-sampling alone does not close. A `git pull` or a
    commit mid-compile leaves the subtree clean at a *new* sha, so a plain
    post-build sample would record a clean build of a commit that did not
    produce these bytes - and `artifact_status()` would call it current. The
    image straddles two revisions; dirty is the only honest answer."""
    source = repo / "rp2040"
    (source / "build").mkdir()

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "landed_mid_build.c").write_text("late\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "landed mid build")
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    before = cmake.source_state(str(source))
    cmake.build(paths, settings, target)

    after = cmake.source_state(str(source))
    assert after.sha != before.sha and after.dirty is False
    record = cmake.read_sidecar(paths, target)
    assert record["dirty"] is True
    assert cmake.artifact_status(paths, target, after).reason == BUILT_DIRTY


def test_an_edit_discarded_during_the_build_is_still_recorded_dirty(
    paths, settings, repo, monkeypatch
):
    """The third leg. An edit present when `make` started is compiled into the
    image; discarding it mid-compile (a stash, a checkout, an editor undo)
    leaves the subtree clean at the *same* sha it began on, so neither the
    post-build `dirty` nor the moved-sha check fires. Without the pre-build
    leg the sidecar calls those bytes a clean build of a commit that does not
    contain them - and says so while carrying a `-dirty` version string."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    edit = source / "edited_before_build.c"
    edit.write_text("compiled in\n", encoding="utf-8")

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        # missing_ok: run_streamed is called for configure and again for make.
        edit.unlink(missing_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    before = cmake.source_state(str(source))
    assert before.dirty is True
    cmake.build(paths, settings, target)

    after = cmake.source_state(str(source))
    assert after.sha == before.sha and after.dirty is False
    record = cmake.read_sidecar(paths, target)
    assert record["dirty"] is True
    assert cmake.artifact_status(paths, target, after).reason == BUILT_DIRTY


def test_a_tree_that_asks_for_submodules_syncs_before_it_configures(
    paths, settings, repo, monkeypatch
):
    """Order is the whole point. A submodule at a commit other than the
    recorded one is a modified gitlink in the parent, so a sync landing after
    the provenance sample would record a tree that no longer exists."""
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    target.submodules = True
    cmake.build(paths, settings, target)

    assert seen[0][:3] == ["git", "submodule", "update"]
    assert "--recursive" in seen[0]
    assert [c[0] for c in seen[1:]] == ["cmake", "make"]


def test_a_tree_that_does_not_ask_for_submodules_never_runs_git(
    paths, settings, repo, monkeypatch
):
    """Opt-in. Syncing unasked would reset a submodule somebody deliberately
    checked out elsewhere, and silently make that dirty tree clean."""
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    cmake.build(paths, settings, _cmake_type(source))

    assert [c[0] for c in seen] == ["cmake", "make"]


def test_a_failed_submodule_sync_refuses_before_anything_is_built(
    paths, settings, repo, monkeypatch
):
    """Fail closed. A sync that could not complete leaves a tree that is
    part one revision and part another - building it would stage an image no
    commit describes."""
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        return 128 if cmd[0] == "git" else 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    target.submodules = True
    with pytest.raises(BuildError) as excinfo:
        cmake.build(paths, settings, target)

    assert "submodule" in str(excinfo.value)
    assert [c[0] for c in seen] == ["git"]
    assert cmake.read_sidecar(paths, target) is None


def test_the_recorded_version_is_the_one_compiled_in(
    paths, settings, repo, monkeypatch
):
    """The other half of the split: `version` is *not* re-sampled. It is the
    string substituted into `cmake_args:` and compiled into the binary through
    `-D`, so it must stay the pre-build value or the sidecar disagrees with
    what the board reports."""
    source = repo / "rp2040"
    (source / "build").mkdir()

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "landed_mid_build.c").write_text("late\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "landed mid build")
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    compiled_in = cmake.source_state(str(source)).version
    cmake.build(paths, settings, target)

    record = cmake.read_sidecar(paths, target)
    assert record["version"] == compiled_in
    assert record["version"] != cmake.source_state(str(source)).version


def test_configure_args_carry_the_expanded_version(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = dataclasses.replace(
        _cmake_type(source), cmake_args="-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"
    )
    cmake.build(paths, settings, target)

    version = cmake.source_state(str(source)).version
    assert [f"-DROADRUNNER_FIRMWARE_VERSION={version}"] == [
        a for a in seen[0] if a.startswith("-DROADRUNNER")
    ]


def _fake_configure_and_build(source):
    """A run_streamed stand-in that leaves a matching cache after `cmake`,
    so a second build() call sees `needs_configure() is False` - exactly
    the state a real second build finds a real tree in."""

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        if cmd[0] == "cmake":
            (source / "build" / "CMakeCache.txt").write_text(
                f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
            )
        return 0

    return fake_run


def test_a_second_build_with_cmake_args_still_reconfigures(paths, settings, repo, monkeypatch):
    """cmake `-D` cache entries persist across invocations once set, and are
    only ever refreshed by passing them again - so `cmake_args:` (almost
    always carrying `${git_describe}`) must reach cmake on *every* build, or
    every board flashed after the first reports the first build's version
    forever, which is the sole board-side correlation channel this design
    relies on."""
    source = repo / "rp2040"
    monkeypatch.setattr(cmake.build_mod, "run_streamed", _fake_configure_and_build(source))
    target = dataclasses.replace(
        _cmake_type(source), cmake_args="-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"
    )

    cmake.build(paths, settings, target)
    assert cmake.needs_configure(str(source)) is False  # cache now matches the tree

    seen: list[list[str]] = []
    monkeypatch.setattr(
        cmake.build_mod,
        "run_streamed",
        lambda cmd, **kw: (seen.append(list(cmd)), _fake_configure_and_build(source)(cmd, **kw))[
            1
        ],
    )
    cmake.build(paths, settings, target)

    configure_cmds = [c for c in seen if c[0] == "cmake"]
    assert configure_cmds, "cmake_args: must reconfigure every build so ${git_describe} refreshes"


def test_a_second_build_without_cmake_args_still_skips_reconfigure(paths, settings, repo, monkeypatch):
    """A tree declaring no cmake_args: has no cache variable to refresh, so
    the brief's 're-running configure is slow and changes nothing' skip
    still applies on a second build."""
    source = repo / "rp2040"
    monkeypatch.setattr(cmake.build_mod, "run_streamed", _fake_configure_and_build(source))
    target = _cmake_type(source)  # no cmake_args

    cmake.build(paths, settings, target)
    assert cmake.needs_configure(str(source)) is False  # cache now matches the tree

    seen: list[list[str]] = []
    monkeypatch.setattr(
        cmake.build_mod,
        "run_streamed",
        lambda cmd, **kw: (seen.append(list(cmd)), _fake_configure_and_build(source)(cmd, **kw))[
            1
        ],
    )
    cmake.build(paths, settings, target)

    configure_cmds = [c for c in seen if c[0] == "cmake"]
    assert not configure_cmds


def _staged(paths, data=b"IMAGE"):
    path = paths.uf2_file("roadrunner", "roadrunner")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_no_image_is_never_built(paths, tmp_path):
    status = cmake.artifact_status(paths, _cmake_type(tmp_path), cmake.SourceState())
    assert status.reason == NEVER_BUILT
    assert not status.is_current


def test_an_image_with_no_sidecar_has_no_provenance(paths, tmp_path):
    """Claiming current about a binary we know nothing about is the failure -
    it flashes a board with firmware from before the fix you just made."""
    _staged(paths)
    status = cmake.artifact_status(paths, _cmake_type(tmp_path), cmake.SourceState())
    assert status.reason == NO_PROVENANCE


def _write_sidecar(paths, record):
    path = paths.sidecar_file("roadrunner", "roadrunner")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


def test_a_sidecar_another_provider_wrote_is_not_read_as_ours(paths, repo):
    """This path is shared with `kconfig_make`, whose record is a different
    schema in the same file, and `bin_sha256` is already common to both. The
    record below is the worst case: every field this provider looks at is
    present and agrees, so only the `provider` key stands between a foreign
    record and a confident answer about what is on a board."""
    source = repo / "rp2040"
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    path = _staged(paths)
    stat = os.stat(path)
    record = {
        "sha": state.sha,
        "dirty": False,
        "cmake_target": target.cmake_target,
        "bin_sha256": cmake.build_mod.sha256_file(path),
        "bin_size": stat.st_size,
        "bin_mtime": stat.st_mtime,
    }

    _write_sidecar(paths, dict(record, provider="kconfig_make"))
    assert cmake.read_sidecar(paths, target) is None
    assert cmake.artifact_status(paths, target, state).reason == NO_PROVENANCE

    # No key at all - every sidecar written before this one existed.
    _write_sidecar(paths, record)
    assert cmake.read_sidecar(paths, target) is None
    assert cmake.artifact_status(paths, target, state).reason == NO_PROVENANCE

    # The same record with our own key is read and answered on: the refusals
    # above are caused by that one key and by nothing else.
    _write_sidecar(paths, dict(record, provider="cmake"))
    assert cmake.artifact_status(paths, target, state).is_current


def test_an_image_somebody_else_rebuilt_is_a_foreign_build(paths, repo):
    source = repo / "rp2040"
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, state)

    _staged(paths, b"SOMETHING ELSE")
    assert cmake.artifact_status(paths, target, state).reason == FOREIGN_BUILD


def test_a_sidecar_too_old_to_carry_a_hash_accuses_nobody(paths, repo):
    """`FOREIGN_BUILD` is an accusation, and a record written before
    `bin_sha256` existed has no evidence for one. Drifted size and mtime with
    no recorded hash is still absence of evidence."""
    source = repo / "rp2040"
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    path = _staged(paths)
    cmake.record_build(paths, target, state)

    sidecar = paths.sidecar_file(target.name, target.firmware)
    with open(sidecar, encoding="utf-8") as fh:
        record = json.load(fh)
    del record["bin_sha256"]
    record["bin_mtime"] = 0
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    assert os.stat(path).st_mtime != 0
    assert cmake.artifact_status(paths, target, state).reason == NO_PROVENANCE


def test_changing_the_configured_target_makes_the_staged_image_stale(paths, repo):
    source = repo / "rp2040"
    built_target = _cmake_type(source)
    state = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, built_target, state)

    configured_target = dataclasses.replace(
        built_target, cmake_target="roadrunner_v1_i2c_grb"
    )
    assert (
        cmake.artifact_status(paths, configured_target, state).reason == CONFIG_CHANGED
    )


def test_a_build_from_a_dirty_subtree_is_built_dirty(paths, repo):
    source = repo / "rp2040"
    (source / "CMakeLists.txt").write_text("project(rr2)\n", encoding="utf-8")
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    assert state.dirty is True

    _staged(paths)
    cmake.record_build(paths, target, state)
    assert cmake.artifact_status(paths, target, state).reason == BUILT_DIRTY


def test_a_commit_to_the_subtree_makes_the_image_stale(paths, repo):
    source = repo / "rp2040"
    target = _cmake_type(source)
    built_from = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, built_from)

    (source / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change firmware")

    now = cmake.source_state(str(source))
    assert cmake.artifact_status(paths, target, now).reason == SOURCE_CHANGED


def test_a_commit_outside_the_subtree_leaves_the_image_current(paths, repo):
    """The regression pio's repo-wide source_state would fail."""
    source = repo / "rp2040"
    target = _cmake_type(source)
    built_from = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, built_from)

    (repo / "klippy" / "extra.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated")

    now = cmake.source_state(str(source))
    status = cmake.artifact_status(paths, target, now)
    assert status.is_current, status.reason


# --------------------------------------------------------------------------
# the Cmake provider adapter
# --------------------------------------------------------------------------


def test_the_provider_enumerates_its_types(paths, settings, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    targets = Cmake().targets(install)
    assert [(t.provider, t.name, t.fw) for t in targets] == [
        ("cmake", "roadrunner", "roadrunner")
    ]


def test_the_provider_is_never_swept_on_demand(paths, settings, tmp_path):
    """A cmake target is an application, not a bootloader - a sweep builds it.
    Asserted because `on_demand` defaulting the other way would silently drop
    it from every fleet build."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    assert all(not t.on_demand for t in Cmake().targets(install))


def test_a_blocked_type_is_skipped_by_selection_not_failed(paths, settings, tmp_path):
    """tmp_path has no CMakeLists.txt, so the type is blocked. It must appear
    in `skipped` with a reason, never silently vanish - the silent version of
    exactly this is the bug the provider seam was written to fix."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    selection = providers.select(install, stale_only=False)
    skipped = {s.target.name: s.reason for s in selection.skipped}
    assert "roadrunner" in skipped
    assert "CMakeLists.txt" in skipped["roadrunner"]


def test_describe_names_the_type(paths, settings, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    assert Cmake().describe(Cmake().targets(install)[0]) == "roadrunner"


def test_the_provider_build_delegates_to_the_module_function(
    paths, settings, tmp_path, capture_reporter
):
    """`Cmake.build` shares a name with the module-level `build` it calls -
    close the hole directly rather than trusting that mypy would have caught a
    bad bind. Asserting on the reported commands (rather than only "nothing
    staged", which a no-op body would also satisfy) is what makes this
    load-bearing."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    install = Install.load(paths, settings)
    dry = dataclasses.replace(settings, dry_run=True)
    install = dataclasses.replace(install, settings=dry)
    target = Cmake().targets(install)[0]

    Cmake().build(install, target, reporter=capture_reporter.reporter)

    cmds = [line for stream, line in capture_reporter.lines if stream == "cmd"]
    assert any("cmake" in c and "-S" in c for c in cmds)
    assert any(cmd_tokens(c)[0] == "make" for c in cmds)
    assert not os.path.exists(paths.uf2_file("roadrunner", "roadrunner"))


def test_the_provider_artifact_status_delegates_to_the_module_function(
    paths, settings, tmp_path
):
    """Same shadowing hole as `build`, for `artifact_status`."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    target = Cmake().targets(install)[0]

    assert Cmake().artifact_status(install, target).reason == NEVER_BUILT


def test_the_shipped_example_config_declares_a_loadable_cmake_type(paths):
    """The example in mcu-updater.cfg is documentation people copy. A typo in
    it is a support ticket."""
    shutil.copyfile(REPO_ROOT / "mcu-updater.cfg", paths.main_config)
    types = cmake.load(paths)
    assert "roadrunner" in types
    assert types["roadrunner"].cmake_target == "roadrunner_v1_i2c_rgb"


def test_cleaning_removes_the_build_dir_and_says_what_it_removed(paths, repo):
    """The recovery for a cache that outlived its toolchain. Returns the path
    because the caller reports it - "cleaned" is not a checkable sentence."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    (source / "build" / "CMakeCache.txt").write_text("stale\n", encoding="utf-8")

    removed = cmake.clean_build_dir(str(source))

    assert removed == str(source / "build")
    assert not (source / "build").exists()


def test_cleaning_a_tree_with_no_build_dir_is_not_an_error(paths, repo):
    """Nothing to remove is a successful clean, not a failure. A user reaching
    for this is already fixing something; a second error helps nobody."""
    assert cmake.clean_build_dir(str(repo / "rp2040")) is None


def test_cleaning_refuses_a_build_path_that_is_not_a_directory(paths, repo):
    """The only destructive operation a provider offers, so it refuses to
    unlink whatever happens to sit at that path."""
    source = repo / "rp2040"
    (source / "build").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(BuildError):
        cmake.clean_build_dir(str(source))

    assert (source / "build").exists()


def test_cleaning_leaves_the_staged_image_and_its_provenance(paths, settings, repo, monkeypatch):
    """A clean costs a recompile, never an artifact. The staged .uf2 and the
    sidecar live under printer_data, not in the tree being removed."""
    source = repo / "rp2040"

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    staged = cmake.build(paths, settings, target)
    before = cmake.read_sidecar(paths, target)

    cmake.clean_build_dir(str(source))

    assert os.path.exists(staged)
    assert cmake.read_sidecar(paths, target) == before


def test_a_cleaned_tree_reconfigures_on_the_next_build(paths, settings, repo, monkeypatch):
    """The point of the whole operation: `needs_configure` sees a cache naming
    the right source tree and skips configure, which is exactly what keeps a
    stale toolchain path alive. Removing the directory is what breaks that."""
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "CMakeCache.txt").write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
        )
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    cmake.build(paths, settings, target)
    assert cmake.needs_configure(str(source)) is False

    cmake.clean_build_dir(str(source))
    assert cmake.needs_configure(str(source)) is True

    seen.clear()
    cmake.build(paths, settings, target)
    assert [c[0] for c in seen] == ["cmake", "make"]


def test_the_record_carries_the_digest_a_board_should_report(paths, repo):
    """What `entry_for` compares against, stored at the one moment we know it.

    Named with the INFO payload's own field names, so the stored record and
    the board's report compare field to field with nothing in between.
    """
    from .test_uf2 import ROUNDED_UP_CRC, VECTOR_START, _vector_uf2

    source = repo / "rp2040"
    target = _cmake_type(source)
    _staged(paths, _vector_uf2())
    cmake.record_build(paths, target, cmake.source_state(str(source)))

    record = cmake.read_sidecar(paths, target)
    assert record is not None
    assert record["digest_algorithm"] == 1
    assert record["digest"] == ROUNDED_UP_CRC
    assert record["image_start"] == VECTOR_START
    assert record["image_length"] == 768


def test_an_artifact_that_is_not_a_uf2_still_records_a_build(paths, repo):
    """Absence is never mismatch, and never a failed build either.

    A digest we cannot compute costs the comparison one signal. Raising here
    would cost the operator the whole build over a provenance nicety.
    """
    source = repo / "rp2040"
    target = _cmake_type(source)
    _staged(paths, b"NOT A UF2")
    cmake.record_build(paths, target, cmake.source_state(str(source)))

    record = cmake.read_sidecar(paths, target)
    assert record is not None
    assert "digest" not in record
    assert cmake.artifact_status(paths, target, cmake.source_state(str(source))).is_current


def test_a_build_records_its_uf2_under_artifacts(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(cmake.build_mod, "run_streamed", _leaves(source, "roadrunner_v1_i2c_rgb"))
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"all", "roadrunner_v1_i2c_rgb"})

    cmake.build(paths, settings, _cmake_type(source))

    record = cmake.read_sidecar(paths, _cmake_type(source))
    assert record["artifacts"] == {"uf2": {"sha256": record["bin_sha256"]}}
    assert cmake.read_record(paths, "roadrunner", "roadrunner") == record


def _links(source, name, *, raw=True):
    """A run_streamed stand-in that links `name` the way pico-sdk does: the
    .elf, then the .bin and .uf2 made from it."""

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        build = source / "build"
        (build / f"{name}.elf").write_bytes(b"elf")
        if raw:
            (build / f"{name}.bin").write_bytes(f"{name} raw".encode())
        (build / f"{name}.uf2").write_bytes(name.encode())
        return 0

    return fake_run


def _build_with(paths, settings, repo, monkeypatch, fake_run):
    source = repo / "rp2040"
    (source / "build").mkdir(exist_ok=True)
    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run(source))
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"all", "roadrunner_v1_i2c_rgb"})
    cmake.build(paths, settings, _cmake_type(source))
    return source


def test_a_build_stages_the_bin_beside_the_uf2(paths, settings, repo, monkeypatch):
    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb")
    )

    with open(paths.bin_file("roadrunner", "roadrunner"), "rb") as fh:
        assert fh.read() == b"roadrunner_v1_i2c_rgb raw"
    record = cmake.read_sidecar(paths, _cmake_type(source))
    assert record["artifacts"] == {
        "bin": {"sha256": hashlib.sha256(b"roadrunner_v1_i2c_rgb raw").hexdigest()},
        "uf2": {"sha256": record["bin_sha256"]},
    }


def test_a_built_bin_is_offered_to_a_family_that_lists_flashtool(paths, settings, repo, monkeypatch):
    _build_with(paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb"))

    family = types.SimpleNamespace(name="roadrunner")
    artifact = cmake.staged(paths, "roadrunner", family).first_of((KIND_BIN,))

    assert artifact is not None
    assert artifact.path == paths.bin_file("roadrunner", "roadrunner")


def test_a_build_without_a_bin_removes_a_stale_one(paths, settings, repo, monkeypatch):
    """Review Focus 4: the record stops listing it, so leaving it would offer
    flashtool an older image beside today's uf2 the moment the family lists it."""
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"an older build's bin")

    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    assert not os.path.exists(paths.bin_file("roadrunner", "roadrunner"))
    assert set(cmake.read_sidecar(paths, _cmake_type(source))["artifacts"]) == {"uf2"}


def test_a_bin_older_than_its_elf_is_not_staged(paths, settings, repo, monkeypatch):
    """cmake does not delete an output the tree stopped declaring, so a .bin
    from before the tree dropped it survives in build/ - and predates the link."""
    build = repo / "rp2040" / "build"
    build.mkdir()
    stale = build / "roadrunner_v1_i2c_rgb.bin"
    stale.write_bytes(b"last month's bin")
    os.utime(stale, (1_000_000, 1_000_000))

    _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    assert not os.path.exists(paths.bin_file("roadrunner", "roadrunner"))


def test_a_dry_run_leaves_a_staged_bin_alone(paths, settings, repo, monkeypatch):
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"yesterday's bin")
    rehearsal = dataclasses.replace(settings, dry_run=True)

    _build_with(
        paths, rehearsal, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    with open(paths.bin_file("roadrunner", "roadrunner"), "rb") as fh:
        assert fh.read() == b"yesterday's bin"


def test_a_cmake_bin_changed_behind_the_record_is_a_foreign_build(paths, settings, repo, monkeypatch):
    """flashtool writes the bin without looking at the uf2, so a bin replaced
    behind the record is foreign even while the uf2 still matches."""
    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb")
    )
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"somebody else's bin")

    status = cmake.artifact_status(paths, _cmake_type(source), cmake.source_state(str(source)))

    assert status.reason == FOREIGN_BUILD
