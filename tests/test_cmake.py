"""The cmake provider: config parsing, subtree-scoped provenance, staging.

Mirrors `test_pio.py`, which is the closest existing suite. The parts that are
deliberately *not* copied from it - subtree-scoped git, an artifact path the
provider owns - have their own tests saying why.
"""

from __future__ import annotations

import subprocess

import pytest

from mcu_updater.errors import ConfigError
from mcu_updater.providers import cmake


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
