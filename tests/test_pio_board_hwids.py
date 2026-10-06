"""Which USB ids a PlatformIO type's board enumerates under.

The chain is `platformio.ini` -> the env's `board` -> that board's JSON
manifest -> `build.hwids`. PlatformIO resolves the first link, because an env
can get its `board` from `[env]` or through `extends`; the rest is a file read.

Never an exception: a first-install scan has something to do without the
answer, so every way of not having one is a sentence.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from mcu_updater.errors import ConfigError, ToolMissingError
from mcu_updater.providers import pio as pio_mod
from mcu_updater.providers.pio import PioType
from mcu_updater.settings import Settings

CH340 = [["0x1A86", "0x7522"]]


def _manifest(path, hwids=CH340):
    path.parent.mkdir(parents=True, exist_ok=True)
    build = {"mcu": "esp32s3"}
    if hwids is not None:
        build["hwids"] = hwids
    path.write_text(json.dumps({"build": build, "name": "a board"}), encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / "knomi_serial"
    source.mkdir()
    return source


def _entry(tree, env="knomi"):
    return PioType(name="knomi", env=env, source=str(tree), firmware="knomi_serial")


def _config(monkeypatch, tree, core, envs):
    """What `pio project config` answers, already resolved."""
    config = {
        "platformio": {"boards_dir": str(tree / "boards"), "core_dir": str(core)},
        **{f"env:{name}": values for name, values in envs.items()},
    }
    monkeypatch.setattr(pio_mod, "project_config", lambda settings, entry: config)


def test_the_projects_own_manifest_says_which_ids(tree, tmp_path, monkeypatch):
    _manifest(tree / "boards" / "knomi.json")
    _config(monkeypatch, tree, tmp_path / "core", {"knomi": {"board": "knomi", "platform": "espressif32"}})

    assert pio_mod.board_hwids(Settings(), _entry(tree)) == (frozenset({("1a86", "7522")}), None)


def test_ids_are_spelt_the_way_sysfs_spells_them(tree, tmp_path, monkeypatch):
    """Manifests write `0x239A`, `0X811b`, or bare hex; sysfs writes four
    lowercase digits, and the two are compared as strings."""
    _manifest(tree / "boards" / "knomi.json", [["0x239A", "0X811b"], ["303a", "1"]])
    _config(monkeypatch, tree, tmp_path / "core", {"knomi": {"board": "knomi"}})

    ids, problem = pio_mod.board_hwids(Settings(), _entry(tree))
    assert problem is None
    assert ids == {("239a", "811b"), ("303a", "0001")}


def test_a_stock_board_is_read_from_its_installed_platform(tree, tmp_path, monkeypatch):
    core = tmp_path / "core"
    _manifest(
        core / "platforms" / "espressif32" / "boards" / "esp32-s3-devkitc-1.json", [["0x303A", "0x1001"]]
    )
    _config(
        monkeypatch,
        tree,
        core,
        {"knomi": {"board": "esp32-s3-devkitc-1", "platform": "espressif32"}},
    )

    assert pio_mod.board_hwids(Settings(), _entry(tree))[0] == {("303a", "1001")}


def test_the_envs_own_platform_is_tried_before_another_that_has_the_board(tree, tmp_path, monkeypatch):
    """Two platforms can ship a board of the same name. `aaa` sorts first and
    is the wrong one."""
    core = tmp_path / "core"
    _manifest(core / "platforms" / "aaa" / "boards" / "generic.json", [["0x0001", "0x0001"]])
    _manifest(core / "platforms" / "espressif32@6.4.0" / "boards" / "generic.json", [["0x303A", "0x1001"]])
    _config(
        monkeypatch,
        tree,
        core,
        {"knomi": {"board": "generic", "platform": "espressif32@6.4.0"}},
    )

    assert pio_mod.board_hwids(Settings(), _entry(tree))[0] == {("303a", "1001")}


def test_the_projects_manifest_wins_over_a_platforms(tree, tmp_path, monkeypatch):
    core = tmp_path / "core"
    _manifest(core / "platforms" / "espressif32" / "boards" / "knomi.json", [["0x0001", "0x0001"]])
    _manifest(tree / "boards" / "knomi.json")
    _config(monkeypatch, tree, core, {"knomi": {"board": "knomi", "platform": "espressif32"}})

    assert pio_mod.board_hwids(Settings(), _entry(tree))[0] == {("1a86", "7522")}


@pytest.mark.parametrize(
    ("envs", "manifest", "says"),
    [
        ({}, CH340, "no [env:knomi]"),
        ({"knomi": {"platform": "espressif32"}}, CH340, "names no board"),
        ({"knomi": {"board": "elsewhere"}}, CH340, "no manifest for board 'elsewhere'"),
        ({"knomi": {"board": "knomi"}}, None, "declares no USB ids"),
        ({"knomi": {"board": "knomi"}}, [], "declares no USB ids"),
        ({"knomi": {"board": "knomi"}}, [["0xZZZZ", "0x7522"]], "could not be read"),
        ({"knomi": {"board": "knomi"}}, ["0x1A86"], "could not be read"),
    ],
)
def test_every_way_of_not_knowing_is_a_sentence(tree, tmp_path, monkeypatch, envs, manifest, says):
    _manifest(tree / "boards" / "knomi.json", manifest)
    _config(monkeypatch, tree, tmp_path / "core", envs)

    ids, problem = pio_mod.board_hwids(Settings(), _entry(tree))
    assert ids is None
    assert says in problem


def test_a_host_without_platformio_is_a_sentence_too(tree, monkeypatch):
    def missing(settings, entry):
        raise ToolMissingError("PlatformIO not found.", tool="pio")

    monkeypatch.setattr(pio_mod, "project_config", missing)

    assert pio_mod.board_hwids(Settings(), _entry(tree)) == (None, "PlatformIO not found.")


# --------------------------------------------------------------------------
# asking PlatformIO
# --------------------------------------------------------------------------

#: `pio project config --json-output`, as PlatformIO 6 prints it for an env
#: that inherits its board through `extends`.
RESOLVED = json.dumps(
    [
        ["platformio", [["boards_dir", "/src/boards"], ["default_envs", ["knomi"]]]],
        ["env:knomi", [["platform", "espressif32"], ["board", "knomi"]]],
        ["env:knomi_i2cscan", [["extends", ["env:knomi"]], ["platform", "espressif32"], ["board", "knomi"]]],
    ]
)


def _pio(monkeypatch, *, returncode=0, stdout=RESOLVED, stderr=""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(pio_mod, "find_pio", lambda settings: "/usr/bin/pio")
    monkeypatch.setattr(pio_mod.subprocess, "run", run)
    return calls


def test_platformio_resolves_the_ini_and_the_sections_come_back_keyed(tree, monkeypatch):
    calls = _pio(monkeypatch)

    config = pio_mod.project_config(Settings(), _entry(tree))

    assert config["env:knomi_i2cscan"]["board"] == "knomi"
    assert config["platformio"]["boards_dir"] == "/src/boards"
    (cmd, kwargs) = calls[0]
    assert cmd == ["/usr/bin/pio", "project", "config", "--json-output"]
    assert kwargs["cwd"] == str(tree)
    assert kwargs["timeout"] == pio_mod.PROJECT_CONFIG_TIMEOUT


def test_an_env_that_only_extends_another_still_has_its_board(tree, tmp_path, monkeypatch):
    """The reason PlatformIO does the reading: `[env:knomi_i2cscan]` has no
    `board` line of its own."""
    _pio(monkeypatch, stdout=RESOLVED.replace("/src/boards", str(tree / "boards").replace("\\", "\\\\")))
    _manifest(tree / "boards" / "knomi.json")

    ids, problem = pio_mod.board_hwids(Settings(), _entry(tree, env="knomi_i2cscan"))
    assert (ids, problem) == ({("1a86", "7522")}, None)


def test_a_project_platformio_cannot_parse_says_what_platformio_said(tree, monkeypatch):
    _pio(monkeypatch, returncode=1, stdout="", stderr="Error: Invalid 'platformio.ini'\nline 3: bad")

    with pytest.raises(ConfigError, match="line 3: bad"):
        pio_mod.project_config(Settings(), _entry(tree))


def test_output_that_is_not_the_expected_json_is_refused(tree, monkeypatch):
    _pio(monkeypatch, stdout="not json")

    with pytest.raises(ConfigError, match="not the JSON expected"):
        pio_mod.project_config(Settings(), _entry(tree))


def test_a_hung_launcher_is_an_answer_not_a_hang(tree, monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(pio_mod, "find_pio", lambda settings: "/usr/bin/pio")
    monkeypatch.setattr(pio_mod.subprocess, "run", run)

    ids, problem = pio_mod.board_hwids(Settings(), _entry(tree))
    assert ids is None
    assert "could not ask PlatformIO" in problem
