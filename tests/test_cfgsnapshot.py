"""The config snapshot: one parse per file, reused until its stat changes.

`os.utime` ages a file past `RACY_WINDOW_NS` so its parse can be kept; a file
written moments ago never is, which is also why the rest of the suite - whose
fixtures write their configs just before reading them - never hits the cache.
"""

from __future__ import annotations

import os
import time

import pytest

from mcu_updater import cfgsnapshot
from mcu_updater.cfgdoc import CfgDocument, FrozenDocumentError

#: Well past the window.
AGED_NS = 10 * cfgsnapshot.RACY_WINDOW_NS


def _write(path, text: str, *, aged: bool = True) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if aged:
        then = time.time_ns() - AGED_NS
        os.utime(path, ns=(then, then))


@pytest.fixture
def parses(monkeypatch) -> list[str]:
    """Every parse `cfgsnapshot` makes from here on."""
    calls: list[str] = []
    real = cfgsnapshot._parse

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(cfgsnapshot, "_parse", spy)
    return calls


def test_an_unchanged_aged_file_is_parsed_once(tmp_path, parses):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\nk: v\n")

    first = cfgsnapshot.read(str(cfg))
    second = cfgsnapshot.read(str(cfg))

    assert first is second
    assert len(parses) == 1


def test_a_changed_file_is_parsed_again(tmp_path, parses):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    cfgsnapshot.read(str(cfg))

    _write(cfg, "[a]\nk: v\n")
    doc = cfgsnapshot.read(str(cfg))

    assert doc is not None and doc.get("a", "k") == "v"
    assert len(parses) == 2


def test_a_replaced_file_is_parsed_again(tmp_path, parses):
    """An editor that saves by rename: same size, new inode."""
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    cfgsnapshot.read(str(cfg))

    tmp = tmp_path / "a.cfg.tmp"
    _write(tmp, "[b]\n")
    os.replace(tmp, cfg)
    doc = cfgsnapshot.read(str(cfg))

    assert doc is not None and doc.has_section("b")
    assert len(parses) == 2


def test_a_freshly_written_file_is_parsed_on_every_read(tmp_path, parses):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n", aged=False)

    cfgsnapshot.read(str(cfg))
    cfgsnapshot.read(str(cfg))

    assert len(parses) == 2


def test_a_rewrite_the_stat_key_cannot_see_is_caught_by_the_window(tmp_path, monkeypatch):
    """Same size, same inode, same mtime tick: the key cannot tell. The read
    that could have raced it began inside the window, so it was never kept."""
    monkeypatch.setattr(cfgsnapshot, "_stat_key", lambda st: (0, 0, 0, 0, 0))
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n", aged=False)
    cfgsnapshot.read(str(cfg))

    _write(cfg, "[b]\n", aged=False)
    doc = cfgsnapshot.read(str(cfg))

    assert doc is not None and doc.has_section("b")


def test_outside_the_window_a_forged_key_is_trusted(tmp_path, monkeypatch):
    """The accepted limitation, pinned so the window test above is known to be
    what catches it: with the key forged and the file aged, the parse is kept."""
    monkeypatch.setattr(cfgsnapshot, "_stat_key", lambda st: (0, 0, 0, 0, 0))
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    cfgsnapshot.read(str(cfg))

    _write(cfg, "[b]\n")
    doc = cfgsnapshot.read(str(cfg))

    assert doc is not None and doc.has_section("a")


def test_a_clock_behind_the_mtime_trusts_nothing(tmp_path, monkeypatch, parses):
    monkeypatch.setattr(cfgsnapshot, "_now_ns", lambda: 0)
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")

    cfgsnapshot.read(str(cfg))
    cfgsnapshot.read(str(cfg))

    assert len(parses) == 2


def test_a_missing_file_is_none_and_not_cached(tmp_path, parses):
    cfg = tmp_path / "absent.cfg"

    assert cfgsnapshot.read(str(cfg)) is None
    assert parses == []
    assert os.path.abspath(cfg) not in cfgsnapshot._cache


def test_a_deleted_file_is_none_and_drops_its_parse(tmp_path):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    cfgsnapshot.read(str(cfg))

    os.remove(cfg)

    assert cfgsnapshot.read(str(cfg)) is None
    assert os.path.abspath(cfg) not in cfgsnapshot._cache


def test_an_unreadable_file_raises_and_is_not_cached(tmp_path):
    cfg = tmp_path / "a.cfg"
    cfg.mkdir()

    with pytest.raises(OSError):
        cfgsnapshot.read(str(cfg))
    assert os.path.abspath(cfg) not in cfgsnapshot._cache


def test_invalidate_drops_the_parse(tmp_path, parses):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    cfgsnapshot.read(str(cfg))

    cfgsnapshot.invalidate(str(cfg))
    cfgsnapshot.read(str(cfg))

    assert len(parses) == 2


def test_the_shared_parse_is_frozen(tmp_path):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    doc = cfgsnapshot.read(str(cfg))

    assert doc is not None and doc.frozen
    with pytest.raises(FrozenDocumentError):
        doc.set("a", "k", "v")


def test_read_fresh_is_writable_and_leaves_the_shared_parse_alone(tmp_path):
    cfg = tmp_path / "a.cfg"
    _write(cfg, "[a]\n")
    shared = cfgsnapshot.read(str(cfg))

    fresh = cfgsnapshot.read_fresh(str(cfg))
    assert fresh is not None and fresh is not shared and not fresh.frozen
    fresh.set("a", "k", "v")

    again = cfgsnapshot.read(str(cfg))
    assert again is shared and again.get("a", "k") is None


def test_read_fresh_of_a_missing_file_is_none(tmp_path):
    assert cfgsnapshot.read_fresh(str(tmp_path / "absent.cfg")) is None


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.set("a", "k", "w"),
        lambda d: d.set("a", "new", "w"),
        lambda d: d.remove_option("a", "k"),
        lambda d: d.rename_section("a", "b"),
        lambda d: d.add_section("c"),
        lambda d: d.remove_section("a"),
    ],
    ids=["set", "set-new", "remove_option", "rename_section", "add_section", "remove_section"],
)
def test_every_edit_refuses_a_frozen_document(edit):
    doc = CfgDocument("[a]\nk: v\n")
    doc.freeze()

    with pytest.raises(FrozenDocumentError, match="Registry.mutate"):
        edit(doc)
    assert doc.render() == "[a]\nk: v\n"
    assert doc.get("a", "k") == "v"
