"""The broadcast listen pass - asking the displays themselves.

The only source that can be taken at *flash time*. Klipper's answer and the
watcher's map are both read before the ports are released, so both describe
where displays were; this describes where they are with esptool about to
write - which is what the display project's own docs require, because a
remembered path is the thing the whole identity scheme exists to avoid.

Split out of `test_pio.py` in Step 25, alongside `discover()`'s own move to
`discovery/listen.py`. The monkeypatch targets moved with it: `discover()`
now calls `shutil.which`/`run_streamed` from its own module's namespace, not
`providers.pio`'s, so patching the old path would silently stop reaching it -
the same shared-object lesson Step 24 hit with `bootsel_scan`.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater.discovery.listen import discover
from mcu_updater.errors import ToolMissingError
from mcu_updater.providers import pio


@pytest.fixture
def tree(tmp_path):
    """A source tree that looks enough like knomi-serial."""
    root = tmp_path / "knomi_serial"
    (root / ".pio" / "build" / "knomi_toolchanger").mkdir(parents=True)
    (root / "platformio.ini").write_text("[env:knomi_toolchanger]\n", encoding="utf-8")
    return root


@pytest.fixture
def display(tree):
    return pio.PioType(name="knomi_toolchanger", env="knomi_toolchanger", source=str(tree))


def _fake_python(tmp_path, stdout: str, rc: int = 0):
    """Stand in for the interpreter that runs the discovery helper."""
    calls: list[list[str]] = []

    def fake_which(name):
        return f"/usr/bin/{name}"

    def fake_run(cmd, *, cwd, reporter, **kwargs):
        calls.append(cmd)
        for line in stdout.splitlines():
            reporter("stdout", line)
        return rc

    return calls, fake_which, fake_run


MARKER = "__mcu_updater_discover__"
REAL = MARKER + (
    '{"19aa38": {"port": "/dev/ttyUSB0", "fw": "0.5.0+54.g5509d4f", "var": "knomi"},'
    ' "196c94": {"port": "/dev/ttyUSB1", "fw": "0.5.0+54.g5509d4f", "var": "knomi"}}'
)


def test_every_display_that_answered_is_returned(paths, settings, display, monkeypatch, tmp_path):
    calls, which, run = _fake_python(tmp_path, REAL)
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    found = discover(paths, settings, display)

    assert sorted(found) == ["196c94", "19aa38"]
    assert found["19aa38"].port == "/dev/ttyUSB0"
    assert found["19aa38"].firmware_version == "0.5.0+54.g5509d4f"
    assert found["19aa38"].build_variant == "knomi"
    assert found["19aa38"].present is True, "it spoke - that is not a guess from a stat"


def test_noise_on_stdout_is_not_mistaken_for_the_answer(
    paths, settings, display, monkeypatch, tmp_path
):
    """A deprecation warning or a udev grumble shares stdout with the result."""
    noisy = "DeprecationWarning: something\n" + REAL + "\nall done\n"
    calls, which, run = _fake_python(tmp_path, noisy)
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    assert sorted(discover(paths, settings, display)) == ["196c94", "19aa38"]


def test_nothing_answering_is_an_empty_map_not_an_error(
    paths, settings, display, monkeypatch, tmp_path
):
    """Klipper still holding the ports looks exactly like this, and the caller's
    answer - flash nothing we cannot identify - is the same either way."""
    calls, which, run = _fake_python(tmp_path, MARKER + "{}")
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    assert discover(paths, settings, display) == {}


def test_an_entry_with_no_port_is_not_offered(paths, settings, display, monkeypatch, tmp_path):
    calls, which, run = _fake_python(tmp_path, MARKER + '{"19aa38": {"fw": "0.5.0"}}')
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    assert discover(paths, settings, display) == {}


def test_ids_are_lowered_so_they_compare(paths, settings, display, monkeypatch, tmp_path):
    calls, which, run = _fake_python(tmp_path, MARKER + '{"19AA38": {"port": "/dev/ttyUSB0"}}')
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    assert list(discover(paths, settings, display)) == ["19aa38"]


def test_a_missing_pyserial_says_what_to_install(
    paths, settings, display, monkeypatch, tmp_path
):
    calls, which, run = _fake_python(
        tmp_path, "ModuleNotFoundError: No module named 'serial'", rc=1
    )
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    with pytest.raises(ToolMissingError) as exc:
        discover(paths, settings, display)
    assert "pyserial" in str(exc.value)
    assert "python3-serial" in str(exc.value)


def test_the_helper_runs_against_the_configured_source_tree(
    paths, settings, display, monkeypatch, tmp_path
):
    """knomi_serial is imported from the tree, so a relocated checkout has to
    be the one asked - otherwise discovery and the build disagree."""
    calls, which, run = _fake_python(tmp_path, MARKER + "{}")
    monkeypatch.setattr("mcu_updater.discovery.listen.shutil.which", which)
    monkeypatch.setattr("mcu_updater.discovery.listen.run_streamed", run)

    discover(paths, settings, display)

    assert calls[0][-2] == os.path.expanduser(display.source)
