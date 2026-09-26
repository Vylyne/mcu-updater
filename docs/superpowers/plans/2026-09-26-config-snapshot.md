# Config Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse `mcu-updater.cfg` once and reuse that parse until the file's stat changes, so an unchanged file costs one `stat` per loader call instead of an open, read and parse.

**Architecture:** A new `cfgsnapshot.py` module keeps one frozen `CfgDocument` per file, keyed on `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`. A parse is kept only when its read began more than 2 s after the file's mtime (git's racy-clean rule). The four loaders read through it and keep their own error rules. The three writers read fresh under their lock and invalidate the cache after `os.replace`. `CfgDocument.freeze()` makes a shared document refuse edits.

**Tech Stack:** Python 3.11 stdlib only, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-config-snapshot-design.md`

## Global Constraints

- stdlib only. Never add a dependency.
- Python 3.11 is the floor. Run the suite with `.venv/Scripts/python.exe` from a `uv venv --python 3.11` in this worktree.
- Keep `from __future__ import annotations` in every module.
- LF line endings everywhere. When a test writes a file, it uses `open(..., "w", encoding="utf-8", newline="\n")`, never `Path.write_text` (its `newline=` is 3.10+, and without it Windows writes CRLF).
- **Call sites of the loaders do not change.** Only these change: `typelist.read_doc`, `typelist.read_config`, `firmware.load`, `settings.load_settings`/`mutate`/`_write_settings`, `Registry.load`/`mutate`/`_save` and `seed.seed_firmware_sections`.
- Every loader's error rule and message stays byte-for-byte what it is today:

  | Loader | Missing file | Unreadable | Duplicate sections |
  | --- | --- | --- | --- |
  | `typelist.read_doc` | `None` | `ConfigCorruptError("could not read {path}: {exc}")` | any is refused |
  | `typelist.read_config` | `[], {}` | `[], {}` | tolerated |
  | `firmware.load` | `{}` | `{}` | tolerated |
  | `settings.load_settings` | `Settings()` | `ConfigError("could not read {path}: {exc}")` | only `[updater]` is refused |

- Writers (`Registry.mutate`, `settings.mutate`, `settings._write_settings`, `seed`) never read through the cache.
- `RACY_WINDOW_NS = 2_000_000_000`.
- Before editing any line, grep `scripts/mutations/` for it. Re-anchor any spec it breaks in the same commit.
- Run mutation specs one at a time via `scripts/mutation_test.py`. After each run, run `tests/test_repo_hygiene.py`.
- Gate before every commit:
  - `.venv/Scripts/python.exe -m pytest -q`
  - `.venv/Scripts/python.exe -m ruff check src tests scripts`
  - `.venv/Scripts/python.exe -m mypy src`
  - `.venv/Scripts/python.exe scripts/check_line_endings.py`
- Commit voice: a conventional-commit prefix, a lowercase message with no trailing period, and the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An editor that saves by rename** (vim, VS Code): the inode changes, so the next read re-parses. Pinned by `test_a_replaced_file_is_parsed_again` (Task 1).
2. **A hand edit that lands within 2 s of a read, in place, at the same size:** the next read sees it. Pinned by `test_a_rewrite_the_stat_key_cannot_see_is_caught_by_the_window` (Task 1).
3. **The config is deleted while the agent runs:** the next read returns `None` and drops the cached parse, and nothing raises. Pinned by `test_a_deleted_file_is_none_and_drops_its_parse` (Task 1).
4. **A caller edits a `Registry` it got from `Registry.load`:** this raises `FrozenDocumentError` instead of corrupting every reader's view. Pinned by `test_a_read_only_registry_cannot_edit_the_shared_document` (Task 2).
5. **The clock goes backwards** (a Pi with no RTC, before NTP): nothing is trusted, so every read re-parses. Pinned by `test_a_clock_behind_the_mtime_trusts_nothing` (Task 1).

---

### Task 1: `cfgsnapshot` and `CfgDocument.freeze`

**Files:**
- Modify: `src/mcu_updater/cfgdoc.py` (add `FrozenDocumentError`, `_frozen`, `freeze()`, `frozen`, and a check in `_splice`)
- Create: `src/mcu_updater/cfgsnapshot.py`
- Test: `tests/test_cfgsnapshot.py`

**Interfaces:**
- Produces:
  - `cfgdoc.FrozenDocumentError(RuntimeError)`
  - `CfgDocument.freeze() -> None`
  - `CfgDocument.frozen -> bool` (property)
  - `cfgsnapshot.read(path: str) -> CfgDocument | None`
  - `cfgsnapshot.read_fresh(path: str) -> CfgDocument | None`
  - `cfgsnapshot.invalidate(path: str) -> None`
  - `cfgsnapshot.RACY_WINDOW_NS`
  - Test seams: `cfgsnapshot._parse(path) -> tuple[CfgDocument, os.stat_result]`, `cfgsnapshot._stat_key(st) -> tuple`, `cfgsnapshot._now_ns() -> int`, `cfgsnapshot._cache: dict[str, _Entry]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cfgsnapshot.py`:

```python
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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run `.venv/Scripts/python.exe -m pytest tests/test_cfgsnapshot.py -q`.
Expected: a collection error, `ImportError: cannot import name 'cfgsnapshot'`.

- [ ] **Step 3: Add `freeze` to `CfgDocument`**

In `src/mcu_updater/cfgdoc.py`, add this after `_TRUE`/`_FALSE`:

```python
class FrozenDocumentError(RuntimeError):
    """An edit to a document that is shared read-only.

    `cfgsnapshot.read` hands the same parse to every reader, so an edit through
    one would silently change what every other reader sees - and could never be
    saved, since the write paths read their own copy under the lock.
    """
```

In `CfgDocument.__init__`, add `self._frozen = False` before `self._parse()`:

```python
    def __init__(self, text: str = "") -> None:
        self.lines: list[str] = text.splitlines() if text else []
        self.sections: dict[str, Section] = {}
        #: Names appearing more than once. First wins, so the later copy is dead
        #: text - which is silent and confusing enough that callers refuse on it.
        self.duplicate_sections: list[str] = []
        self._frozen = False
        self._parse()
```

Add `freeze` and `frozen` just above `# -- writing --`:

```python
    def freeze(self) -> None:
        """Refuse every edit from here on. See `FrozenDocumentError`."""
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen
```

Guard `_splice`. Every edit goes through it:

```python
    def _splice(self, start: int, end: int, replacement: list[str]) -> None:
        if self._frozen:
            raise FrozenDocumentError(
                "this document is the shared config snapshot and is read-only; "
                "write through Registry.mutate or settings.mutate"
            )
        self.lines[start:end] = replacement
```

`add_section` and `set` reach `_splice` before they change anything, so a refused edit leaves the document intact. The `render()` assertion in the test proves this.

- [ ] **Step 4: Create `src/mcu_updater/cfgsnapshot.py`**

```python
"""One parse of a config file, shared until the file changes.

`mcu-updater.cfg` is read by four loaders - `typelist.read_doc`,
`typelist.read_config`, `firmware.load`, `settings.load_settings` - and each
opened and parsed it itself: one `fw.status` poll against a small fixture
opened it 46 times. This keeps one frozen parse per file and hands it to every
reader until the file's stat says it changed.

Correctness rests on the stat key and the racy-clean window alone
(docs/decisions.md, "The config is one snapshot per file"). `invalidate`,
called by every writer, is belt and braces: a writer that forgets it still
cannot cause a stale read.

Writers never read through here. They take `read_fresh` under their lock,
because the lock exists so another process's edit cannot be lost, and that
must not depend on the window judging right.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time

from .cfgdoc import CfgDocument

#: A parse is kept only when its read began more than this long after the
#: file's mtime. A rewrite in place, at the same size, inside the same mtime
#: tick keeps every field of the stat key - and any read that could have raced
#: such a rewrite began inside that tick. Two seconds covers ext4's coarse
#: clock, NTFS, and FAT's 2 s resolution; a poll is seconds apart, so a file
#: written moments ago costs a parse or two, no more.
RACY_WINDOW_NS = 2_000_000_000

#: The clock, a seam tests pin. Behind the file's mtime - a Pi with no RTC
#: before NTP - the difference is negative, and nothing is kept.
_now_ns = time.time_ns

_Key = tuple[int, int, int, int, int]


@dataclasses.dataclass(frozen=True)
class _Entry:
    doc: CfgDocument
    key: _Key


_cache: dict[str, _Entry] = {}
_lock = threading.Lock()


def _stat_key(st: os.stat_result) -> _Key:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _parse(path: str) -> tuple[CfgDocument, os.stat_result]:
    """The text and the stat of the one handle it was read from, so a replace
    between a `stat` and this `open` cannot pair one file's key with another's
    text."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
        st = os.fstat(fh.fileno())
    return CfgDocument(text), st


def read(path: str) -> CfgDocument | None:
    """The file's document, frozen and shared. None when there is no file.

    Raises OSError when the file exists and cannot be read; nothing is cached
    for it, so the next call tries again.
    """
    where = os.path.abspath(path)
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        invalidate(path)
        return None
    with _lock:
        entry = _cache.get(where)
    if entry is not None and entry.key == _stat_key(st):
        return entry.doc
    started = _now_ns()
    try:
        doc, st = _parse(path)
    except (FileNotFoundError, NotADirectoryError):
        invalidate(path)
        return None
    doc.freeze()
    trusted = started - st.st_mtime_ns > RACY_WINDOW_NS
    with _lock:
        if trusted:
            _cache[where] = _Entry(doc, _stat_key(st))
        else:
            _cache.pop(where, None)
    return doc


def read_fresh(path: str) -> CfgDocument | None:
    """An uncached, writable parse, for a writer under its lock."""
    try:
        doc, _st = _parse(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return doc


def invalidate(path: str) -> None:
    """Drop the file's cached parse. Every writer calls this after os.replace."""
    with _lock:
        _cache.pop(os.path.abspath(path), None)
```

Ruling, not a spec change: the spec gives `_Entry` a `trusted` flag. This keeps only trusted parses, which is equivalent and simpler. An untrusted parse is never reused, so there is no reason to store it.

- [ ] **Step 5: Run the tests to confirm they pass**

Run `.venv/Scripts/python.exe -m pytest tests/test_cfgsnapshot.py -q`.
Expected: every test passes.

- [ ] **Step 6: Gate and commit**

Run the full gate from Global Constraints. Nothing reads through the snapshot yet, so the rest of the suite is unaffected. `cfg-comments.json` anchors in `cfgdoc.py`. Grep it for `_splice` and `__init__` lines and, if either is anchored, run that spec alone.

```bash
git add src/mcu_updater/cfgdoc.py src/mcu_updater/cfgsnapshot.py tests/test_cfgsnapshot.py
git commit -m "feat(config): a frozen, stat-keyed snapshot of a config file, kept only outside the racy window" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Loaders read the snapshot, writers read fresh

**Files:**
- Modify: `src/mcu_updater/typelist.py` (`read_doc`, `read_config`)
- Modify: `src/mcu_updater/firmware.py` (`load`)
- Modify: `src/mcu_updater/settings.py` (`load_settings`, `mutate`, `_write_settings`)
- Modify: `src/mcu_updater/config.py` (`Registry.load`, a new `_from_doc`, `mutate`, `_save`)
- Modify: `src/mcu_updater/seed.py` (`seed_firmware_sections`)
- Modify: `tests/conftest.py` (`save_registry`)
- Modify: `scripts/mutations/single-write-path.json` (re-anchor)
- Test: `tests/test_config_snapshot_views.py`

**Interfaces:**
- Consumes from Task 1: `cfgsnapshot.read`, `read_fresh`, `invalidate`, `CfgDocument.frozen`, `FrozenDocumentError`.
- Produces:
  - `typelist.read_doc(paths, *, fresh: bool = False)`
  - `Registry._from_doc(paths, doc: CfgDocument | None) -> Registry`
  - `settings._settings_from_doc(doc, path) -> Settings`
  - `settings._load_fresh(path) -> Settings`

- [ ] **Step 1: Grep the anchors**

Run:

```bash
git grep -n -A1 -E '"file": "src/mcu_updater/(typelist|firmware|settings|config|seed)\.py"' -- scripts/mutations
```

Also read the top-level `"file"` and `"find"` values of `seed-lock.json`, `firmware-source.json`, `application-firmware.json`, `pio-provider-selection.json`, `family-keys.json` and `type-keys.json`. Record in the ledger each anchored line that the steps below rewrite.

Known today: `single-write-path.json` anchors this line in `settings.mutate`:

```
current = load_settings(paths.settings_file)
```

This task rewrites it (Step 5). The body of `Registry.load` moves into `_from_doc` at the same indentation, so anchors inside it keep matching verbatim.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_config_snapshot_views.py`:

```python
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
```

- [ ] **Step 3: Run the tests to confirm they fail**

Run `.venv/Scripts/python.exe -m pytest tests/test_config_snapshot_views.py -q`.
Expected:
- the writer tests and the frozen-registry test fail, because nothing reads through the snapshot yet;
- the error-rule tests pass already. They are the regression net for Steps 4–7.

- [ ] **Step 4: Route `typelist` and `firmware` through the snapshot**

In `src/mcu_updater/typelist.py`, add `cfgsnapshot` to the imports:

```python
from . import cfgsnapshot, firmware, sections
```

Then drop `import os` if nothing else uses it. Ruff will say.

Replace `read_doc`:

```python
def read_doc(paths: Paths, *, fresh: bool = False) -> CfgDocument | None:
    """The config document, or None when there is no file.

    The shared, frozen snapshot (`cfgsnapshot`) unless `fresh`: a writer under
    its lock asks for its own writable parse, read from disk.

    Refuses an unreadable file and duplicate sections: only the first copy of a
    section is read, so everything in a later one would be silently ignored.
    """
    path = paths.registry_file
    try:
        doc = cfgsnapshot.read_fresh(path) if fresh else cfgsnapshot.read(path)
    except OSError as exc:
        raise ConfigCorruptError(f"could not read {path}: {exc}", path=path) from exc
    if doc is None:
        return None
    if doc.duplicate_sections:
        dupes = ", ".join(f"[{name}]" for name in doc.duplicate_sections)
        raise ConfigCorruptError(
            f"{path}: duplicate section(s) {dupes}. Only the first copy is read, so "
            f"everything in the later one is silently ignored - merge them into one.",
            path=path,
            value=doc.duplicate_sections,
        )
    return doc
```

Replace the body of `read_config`:

```python
    """The type list and the families, leniently: no file is neither."""
    try:
        doc = cfgsnapshot.read(paths.main_config)
    except OSError:
        return [], {}
    if doc is None:
        return [], {}
    families = firmware.load_from_doc(doc)
    return read(doc, families), families
```

In `src/mcu_updater/firmware.py`, add `from . import cfgsnapshot` and replace the body of `load`, keeping its docstring:

```python
    try:
        doc = cfgsnapshot.read(paths.main_config)
    except OSError:
        return {}
    if doc is None:
        return {}
    return load_from_doc(doc)
```

Check the import graph: `cfgsnapshot` imports only `cfgdoc`, so neither module gains a cycle.

- [ ] **Step 5: Settings read the snapshot, `mutate` and `_write_settings` read fresh**

In `src/mcu_updater/settings.py`, add `from . import cfgsnapshot`. Keep `_read` exactly as it is: it is the uncached, writable read. Rename the body of `load_settings`, from `s = Settings()` to the end with the `doc = _read(path)` line dropped, to:

```python
def _settings_from_doc(doc: CfgDocument, path: str) -> Settings:
    """The [updater] section of `doc`. `path` is for messages."""
    s = Settings()
    # ... the old body of load_settings, unchanged from the duplicate-[updater]
    # check through `return s` ...
```

Then:

```python
def load_settings(path: str) -> Settings:
    """Read the [updater] section. A missing file or section yields defaults.

    A *malformed value* still raises. Silently ignoring `dry_run = maybe` means
    the user's dry run quietly does not apply, which is the kind of surprise that
    ends up flashing a board.

    Read through the shared config snapshot; `mutate` reads its own copy.
    """
    try:
        doc = cfgsnapshot.read(path)
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}", path=path) from exc
    return _settings_from_doc(CfgDocument() if doc is None else doc, path)


def _load_fresh(path: str) -> Settings:
    """`load_settings` from disk, never the snapshot - for `mutate`, under the lock."""
    return _settings_from_doc(_read(path), path)
```

In `mutate`, make this one-line change:

```python
    with ExclusiveLock(paths, path=paths.registry_lock_file).acquire(label):
        current = _load_fresh(paths.settings_file)
```

At the end of `_write_settings`, after `os.replace(tmp, path)`:

```python
    cfgsnapshot.invalidate(path)
```

Re-anchor `scripts/mutations/single-write-path.json` "settings are loaded under the lock, not before it":

```json
      "find": "    with ExclusiveLock(paths, path=paths.registry_lock_file).acquire(label):\n        current = _load_fresh(paths.settings_file)\n",
      "replace": "    current = _load_fresh(paths.settings_file)\n    with ExclusiveLock(paths, path=paths.registry_lock_file).acquire(label):\n"
```

- [ ] **Step 6: `Registry` reads the snapshot, `mutate` reads fresh, `_save` invalidates**

In `src/mcu_updater/config.py`, add `cfgsnapshot` to `from . import firmware, sections, typelist`. Split `load`:

```python
    @classmethod
    def load(cls, paths: Paths) -> Registry:
        """The registry, over the shared config snapshot.

        Its document is frozen: a Registry from here is for reading. Every
        edit goes through `mutate`, which reads its own copy under the lock.
        """
        return cls._from_doc(paths, typelist.read_doc(paths))

    @classmethod
    def _from_doc(cls, paths: Paths, doc: CfgDocument | None) -> Registry:
        path = paths.registry_file
        if doc is None:
            return cls({}, CfgDocument())

        # ... the old body of load from `# Which families exist is itself
        # config` through `return cls(types, doc)`, unchanged ...
```

In `mutate`:

```python
        with ExclusiveLock(paths, path=paths.registry_lock_file).acquire(label):
            # Fresh from disk, never the snapshot: the lock exists so another
            # process's edit cannot be lost, and that must not rest on the
            # snapshot's racy window judging right.
            reg = cls._from_doc(paths, typelist.read_doc(paths, fresh=True))
            yield reg
            reg._save(paths)
```

At the end of `_save`, after `os.replace(tmp, paths.registry_file)`:

```python
        cfgsnapshot.invalidate(paths.registry_file)
```

- [ ] **Step 7: `seed` invalidates**

In `src/mcu_updater/seed.py`, add `from . import cfgsnapshot`, keeping the module's existing import style. Then, after `os.replace(tmp, paths.main_config)`:

```python
        cfgsnapshot.invalidate(paths.main_config)
```

`_read_missing` stays an uncached read. It is a writer's read, under the lock the second time.

- [ ] **Step 8: Let fixtures save a registry they loaded**

`tests/conftest.py`'s `save_registry` calls `reg._save` on registries that tests built with `Registry.load`. Those now hold the frozen document. Give the fixture a writable copy of the same text:

```python
def save_registry(reg, paths: Paths) -> None:
    """Write a fixture registry to the fake install, as it stands.

    Production writes only through `Registry.mutate`, which is why `_save` is
    private. A fixture building its starting state has no lock to contend for
    and nothing to re-read, so it is the one place outside config.py's own
    tests that writes directly - and only through here. A registry from
    `Registry.load` holds the shared, frozen snapshot, so it is saved from a
    writable copy of the same text.
    """
    from mcu_updater.cfgdoc import CfgDocument

    if reg._doc.frozen:
        reg._doc = CfgDocument(reg._doc.render())
    reg._save(paths)
```

- [ ] **Step 9: Run the new tests, then the whole suite**

Run `.venv/Scripts/python.exe -m pytest tests/test_config_snapshot_views.py tests/test_cfgsnapshot.py -q`.
Expected: all pass.

Run `.venv/Scripts/python.exe -m pytest -q`. Every `FrozenDocumentError` is a caller that edits a `Registry` obtained from `Registry.load`. Rule on each one:
- **In `src/`:** that edit could never have been saved, so it is a latent bug. Move it into `Registry.mutate`, or into a registry that `mutate` yields, and record the ruling in the ledger.
- **In a test:**
  - if it builds a registry to save, route it through `save_registry`;
  - if it edits in memory only, build the registry with `Registry._from_doc(paths, typelist.read_doc(paths, fresh=True))`.

Any other new failure is a regression against the table in Global Constraints. Fix the loader, not the test.

- [ ] **Step 10: Mutation hygiene, then gate and commit**

Run every spec from Step 1's ledger list alone, starting with `single-write-path.json`:

```bash
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/single-write-path.json
.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py
```

Then run the full gate.

```bash
git add -A
git commit -m "feat(config): loaders read one shared parse until the file changes, writers read fresh under their lock" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The poll-level measure, the mutation spec, and the decision record

**Files:**
- Modify: `tests/test_agent_targets.py` (one test)
- Create: `scripts/mutations/config-snapshot.json`
- Modify: `docs/decisions.md` (one entry at the end of "Conclusions that close an avenue")

**Interfaces:**
- Consumes: everything from Tasks 1–2.

- [ ] **Step 1: Write the poll test**

In `tests/test_agent_targets.py`, add `import time` beside `import os`. After `test_first_install_does_not_read_the_config_file_again`, add:

```python
def test_an_unchanged_config_is_parsed_once_across_polls(api, paths, monkeypatch):
    """The config snapshot's reason to exist: one `fw.status` poll opened the
    config 46 times. Aged past the racy window, the first poll parses it once
    and every later poll not at all."""
    from mcu_updater import cfgsnapshot

    then = time.time_ns() - 10 * cfgsnapshot.RACY_WINDOW_NS
    os.utime(paths.main_config, ns=(then, then))
    parsed: list[str] = []
    real = cfgsnapshot._parse

    def spy(path):
        if os.path.abspath(path) == os.path.abspath(paths.main_config):
            parsed.append(path)
        return real(path)

    monkeypatch.setattr(cfgsnapshot, "_parse", spy)

    api.dispatch("fw.status")
    assert len(parsed) == 1, parsed
    api.dispatch("fw.status")
    assert len(parsed) == 1, parsed
```

Run `.venv/Scripts/python.exe -m pytest tests/test_agent_targets.py -q -k parsed_once`.
Expected: pass. The code is already in place from Task 2. If the first poll parses more than once, some loader still bypasses the snapshot: find it with the `_config_opens` spy above, then route it through the snapshot.

- [ ] **Step 2: Create `scripts/mutations/config-snapshot.json`**

```json
{
  "_comment": "The config snapshot reuses a parse only while the file's stat key is unchanged and only if the parse was read outside the racy window; the shared parse refuses edits; both write paths read the file fresh under their lock, never the snapshot.",
  "file": "src/mcu_updater/cfgsnapshot.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_cfgsnapshot.py",
    "tests/test_config_snapshot_views.py",
    "tests/test_agent_targets.py::test_an_unchanged_config_is_parsed_once_across_polls",
    "-q"
  ],
  "mutations": [
    {
      "name": "a parse is reused only while the stat key matches",
      "find": "    if entry is not None and entry.key == _stat_key(st):",
      "replace": "    if entry is not None:"
    },
    {
      "name": "a parse read inside the racy window is not kept",
      "find": "    trusted = started - st.st_mtime_ns > RACY_WINDOW_NS",
      "replace": "    trusted = True"
    },
    {
      "name": "a frozen document refuses every edit",
      "file": "src/mcu_updater/cfgdoc.py",
      "find": "        if self._frozen:",
      "replace": "        if False:"
    },
    {
      "name": "Registry.mutate reads the file fresh, not the snapshot",
      "file": "src/mcu_updater/config.py",
      "find": "            reg = cls._from_doc(paths, typelist.read_doc(paths, fresh=True))",
      "replace": "            reg = cls.load(paths)"
    },
    {
      "name": "settings.mutate reads the file fresh, not the snapshot",
      "file": "src/mcu_updater/settings.py",
      "find": "        current = _load_fresh(paths.settings_file)",
      "replace": "        current = load_settings(paths.settings_file)"
    }
  ]
}
```

Run it alone, then the hygiene test:

```bash
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/config-snapshot.json
.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py
```

Expected: `all 5 guard(s) are load-bearing`, and the hygiene test passes. If a guard survives, the test that should catch it is not asserting what it claims. Fix the test, not the spec.

- [ ] **Step 3: Record the decision**

Append to `docs/decisions.md`, after the last entry under "Conclusions that close an avenue":

```markdown
### The config is one snapshot per file, keyed on stat

`mcu-updater.cfg` is parsed once per process and reused by every loader until
its `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` changes
(`cfgsnapshot.py`). One `fw.status` poll used to open it 46 times.

A parse is kept only if its read began more than 2 s after the file's mtime -
git's racy-clean rule. A rewrite in place, at the same size, inside one mtime
tick keeps every field of the key, and nano rewrites in place and ext4 reuses
the inode `os.replace` frees; any read that could have raced such a rewrite
began inside that tick, so it is never kept. Writers (`Registry.mutate`,
`settings.mutate`, `seed`) never read the snapshot - they parse under their
lock, so the lost-update guarantee does not rest on the window - and drop it
after `os.replace`, as belt and braces. The shared parse is frozen: an edit
through a `Registry.load` raises rather than changing every reader's view.

The accepted gap: a rewrite that forges the whole key - same size, same inode,
mtime restored with `utime` to more than 2 s ago - is not seen until the next
real change. Only a tool that deliberately restores timestamps does that, and
writers are unaffected.

Do not replace this with inotify. It is not stdlib, it needs a thread, and an
event still leaves a gap between the write and the read that the stat check
has to cover anyway.
```

- [ ] **Step 4: Gate and commit**

Run the full gate.

```bash
git add tests/test_agent_targets.py scripts/mutations/config-snapshot.json docs/decisions.md
git commit -m "test(config): one parse across polls of an unchanged config, and the guards that keep it honest" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
