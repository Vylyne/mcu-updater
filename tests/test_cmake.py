"""The cmake provider: config parsing, subtree-scoped provenance, staging.

Mirrors `test_pio.py`, which is the closest existing suite. The parts that are
deliberately *not* copied from it - subtree-scoped git, an artifact path the
provider owns - have their own tests saying why.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess

import pytest

from mcu_updater.errors import BuildError, ConfigError
from mcu_updater.providers import cmake
from mcu_updater.states import BUILT_DIRTY, NEVER_BUILT, NO_PROVENANCE, SOURCE_CHANGED


def write_config(paths, text: str) -> None:
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text)


ROADRUNNER_CFG = """
[firmware roadrunner]
source: {source}
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${{git_describe}}

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
        f"[firmware roadrunner]\nsource: {tmp_path}\nbuilder: cmake\n\n"
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
    assert any("make" in c for c in cmds)
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
    source = tmp_path / "rp2040"
    source.mkdir()
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
    assert record["dirty"] is False


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


def test_an_image_somebody_else_rebuilt_has_no_provenance(paths, repo):
    source = repo / "rp2040"
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, state)

    _staged(paths, b"SOMETHING ELSE")
    assert cmake.artifact_status(paths, target, state).reason == NO_PROVENANCE


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
