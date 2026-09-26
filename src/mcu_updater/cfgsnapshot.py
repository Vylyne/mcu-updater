"""One parse of a config file, shared until the file changes.

`mcu-updater.cfg` is read by four loaders - `typelist.read_doc`,
`typelist.read_config`, `firmware.load`, `settings.load_settings` - and each
opened and parsed it itself: one `fw.status` poll against a small fixture
parsed it 46 times. This keeps one frozen parse per file and hands it to every
reader until the file's stat says it changed. Each reader still opens the file
to stat the handle; only a changed file is read and parsed again.

Correctness rests on the stat key - from the handle, before and after each
read - and the racy-clean window alone (docs/decisions.md, "The config is one
snapshot per file"). `invalidate`, called by every writer, is belt and braces:
a writer that forgets it still cannot cause a stale read.

Writers never read through here. They take `read_fresh` under their lock,
because the lock exists so another process's edit cannot be lost, and that
must not depend on the window judging right.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from typing import TextIO

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


def _parse(fh: TextIO) -> tuple[CfgDocument, os.stat_result]:
    """The rest of `fh`'s text, parsed, and the handle's stat after the read."""
    text = fh.read()
    return CfgDocument(text), os.fstat(fh.fileno())


def read(path: str) -> CfgDocument | None:
    """The file's document, frozen and shared. None when there is no file.

    Raises OSError when the file exists and cannot be read; nothing is cached
    for it, so the next call tries again.

    Every key comes from `fstat` on the one handle the text is read from, never
    from `os.stat(path)`: a replace between a path stat and the open cannot pair
    one file's key with another's text, and on Windows, where Python 3.12+
    reports the change time as st_ctime, the path's lags the handle's. The key
    is taken before the read and after it, so a rewrite in place *during* the
    read - `cp -p` or `rsync --inplace -t`, which put the old mtime back -
    cannot pair the new file's key with the old text; its ctime, at least,
    moved.
    """
    where = os.path.abspath(path)
    try:
        fh = open(path, encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        invalidate(path)
        return None
    with fh:
        before = _stat_key(os.fstat(fh.fileno()))
        with _lock:
            entry = _cache.get(where)
        if entry is not None and entry.key == before:
            return entry.doc
        started = _now_ns()
        doc, st = _parse(fh)
    doc.freeze()
    key = _stat_key(st)
    trusted = key == before and started - st.st_mtime_ns > RACY_WINDOW_NS
    with _lock:
        if trusted:
            _cache[where] = _Entry(doc, key)
        else:
            _cache.pop(where, None)
    return doc


def read_fresh(path: str) -> CfgDocument | None:
    """An uncached, writable parse, for a writer under its lock."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc, _st = _parse(fh)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return doc


def invalidate(path: str) -> None:
    """Drop the file's cached parse. Every writer calls this after os.replace."""
    with _lock:
        _cache.pop(os.path.abspath(path), None)
