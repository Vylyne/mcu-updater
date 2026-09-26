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
