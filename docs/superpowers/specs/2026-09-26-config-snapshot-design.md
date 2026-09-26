# Config snapshot — design

Date: 2026-09-26. Status: approved in conversation; spec awaiting review.

## Problem

`mcu-updater.cfg` is opened, read and parsed once per loader call. One
`fw.status` poll against a small fixture opened it 46 times. The four loaders:
`typelist.read_doc` (under `Registry.load` and `typelist.load`),
`typelist.read_config`, `firmware.load` and `settings.load_settings`. Each one
does its own `open` and `CfgDocument(fh.read())`. Their callers are spread
across `agent/methods/*`, `cli.py`, `providers/*`, `tracking.py`,
`provisioning.py` and `tui.py`, about 60 call sites in all.

## Goal

For each file, parse once and reuse that parse until the file changes. On an
unchanged file, a poll costs one `stat` per loader call and no parse. **Call
sites do not change.** Behaviour does not change: every loader keeps its
current error rules, writers keep their lost-update guarantee, and a hand edit
is still seen on the next read.

Non-goals: caching anything derived from the document (families, type entries,
`Settings`, `McuType`), inotify or any other watcher, and cross-process
sharing.

## Facts this design rests on

- `main_config`, `registry_file` and `settings_file` are one file
  (`paths.py`: the latter two return `self.main_config`).
- Every `CfgDocument` mutation goes through `_splice`: `set`, `remove_option`,
  `rename_section`, `add_section` and `remove_section`. A document can
  therefore be frozen by a single check. A copy, by contrast, costs a full
  re-parse, which is exactly the cost this design exists to avoid.
- The config has three writers. All three write through a `.tmp` file and
  `os.replace`: `Registry._save` (`config.py`), `settings._write_settings`, and
  `seed.py`.
- The loaders handle a missing or broken file in three different ways. These
  differences are load-bearing (see *Views*).

## Design

### `cfgsnapshot.py` (new module, next to `cfgdoc.py`)

```python
def read(path: str) -> CfgDocument | None:
    """The file's document, frozen and shared. None when there is no file.
    Raises OSError when the file exists and cannot be read."""

def read_fresh(path: str) -> CfgDocument | None:
    """An uncached, writable parse, for writers under their lock."""

def invalidate(path: str) -> None:
    """Drop the cached parse. Called by every writer after os.replace."""
```

The cache is a module-level `dict[str, _Entry]`, keyed on
`os.path.abspath(path)`. `abspath` makes no syscalls. Two symlinked spellings of
one file would get two entries, and each would still be correct. A
`threading.Lock` guards the dict. Parsing happens outside the lock: two threads
that both miss may both parse, the last write wins, and both results are
correct.

`_Entry` holds the frozen doc, the stat key and a `trusted` flag.

**Stat key:** `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`. The key
is taken from `os.fstat` on the handle the text was actually read from, so a
replace between `stat` and `open` cannot pair one file's key with another
file's text.

**Same-tick guard (git's racy-clean rule).** A rewrite of the same size, in
place, within one mtime tick, keeps an identical stat key. nano rewrites in
place, and ext4 reuses the inode `os.replace` frees. So an entry is `trusted`
only if its read began more than `RACY_WINDOW_NS = 2_000_000_000` (2 s) after
the file's `st_mtime_ns`. The start time is `time.time_ns()`, taken before the
open.
- An untrusted entry is never reused. The next `read` parses again and
  re-evaluates the flag.
- Why that is sufficient: a later write that the key would miss has to share
  the earlier mtime tick. The read happened between the two writes, so it
  would also fall inside that tick, and therefore inside the window.
- 2 s covers ext4's coarse clock, NTFS and FAT's 2-second resolution.
- The clock can go backwards (a Pi with no RTC, before NTP sets the time).
  That makes the difference negative, so the entry is untrusted, which is the
  safe direction.
- The clock is a module-level seam that tests can pin.

**Read path:**
1. `os.stat(path)`. `FileNotFoundError` returns `None`; any other `OSError`
   propagates.
2. If there is a cached entry, it is trusted, and its key equals the stat key,
   return its doc.
3. Otherwise open, read and `fstat`, parse, freeze, store, and return.

Errors are never cached: an unreadable file is retried on every call.

**Correctness rests on the stat key plus the guard alone.** `invalidate()` on
a write is belt-and-braces. A writer that forgets to call it still cannot
cause a stale read.

### `CfgDocument.freeze()`

`freeze()` sets `self._frozen = True`. After that, `_splice` raises
`FrozenDocumentError` (a `RuntimeError`). The message names the fix: "this
document is the shared config snapshot; write through Registry.mutate or
settings.mutate". Nothing else in `CfgDocument` changes. A caller that mutates
a read-only document now fails loudly in the test that reaches it, instead of
silently corrupting every other reader's view.

### Views: each loader keeps its own rules

| Loader | Missing file | Unreadable | Duplicate sections |
| --- | --- | --- | --- |
| `typelist.read_doc` | `None` | `ConfigCorruptError` | any is refused |
| `typelist.read_config` | `[], {}` | `[], {}` | tolerated (`providers.selection` depends on it) |
| `firmware.load` | `{}` | `{}` | tolerated |
| `settings.load_settings` | defaults | `ConfigError` | only `[updater]` is refused |

"Missing" is `FileNotFoundError`/`NotADirectoryError` only. The old loaders
asked `os.path.exists`, which also called a config missing when its existence
could not be checked at all (directory not searchable, symlink loop); that is
now "unreadable" for every loader and for `settings._read`, the writer's read,
so the loader and the writer never disagree about one file.

Each loader replaces its own `open`/`CfgDocument(...)` with
`cfgsnapshot.read(...)`, and maps `None` and `OSError` exactly as the table
says. The messages stay byte-for-byte the same. Everything derived from the
document (`firmware.load_from_doc`, `typelist.read`, `typelist.validate` and
the `Settings` fields) is recomputed on every call. These are dict walks over
an already-parsed document, so no mutable object is ever shared between
callers.

### Writers never read from the cache

- **`Registry`.** The body of `load` moves into a private
  `_from_doc(doc, path)`.
  - `Registry.load(paths)` builds from `typelist.read_doc` and gets the frozen,
    shared doc.
  - `Registry.mutate` builds from `cfgsnapshot.read_fresh`, under the lock, and
    gets a writable doc. The lock exists so that another process's edit cannot
    be lost. A cached read inside the lock would reopen exactly that hole
    whenever the guard misjudged, so the write path does not depend on the
    guard at all.
  - `_save` calls `cfgsnapshot.invalidate(paths.registry_file)` after
    `os.replace`.
  - A missing file keeps today's `cls({}, CfgDocument())`, which is writable.
- **`settings`.**
  - `load_settings`' body moves into `_settings_from_doc(doc, path)`.
  - `load_settings(path)` reads through the snapshot.
  - `mutate` builds from `_read(path)`, which stays uncached, under the lock.
  - `_write_settings` keeps its uncached `_read` and mutates that private
    document.
  - `invalidate` is called after its `os.replace`.
- **`seed.py`.** Its read-modify-write is unchanged. `invalidate` is called
  after its `os.replace`.

### Known limitation (accepted)

The cache is fooled by a rewrite that forges the stat key: same size, same
inode, and an mtime restored with `utime` to a value more than 2 s old. Only a
tool that deliberately restores timestamps does that. Writers are unaffected
because they read fresh. This limitation is recorded in `docs/decisions.md`
together with the reasons for not using inotify: it is not stdlib, it would
need a thread, and there would still be a gap between the event and the read.

## Testing

- **Unit tests for `cfgsnapshot`.** Deterministic, using `os.utime` and the
  pinned clock.
  - An aged file is parsed once across repeated reads (count calls to
    `CfgDocument._parse`).
  - A changed size, a changed mtime, and a replaced inode each cause a
    re-parse.
  - A freshly modified file (inside the window) is re-parsed on every read.
  - The same-tick case: in-place rewrite, same size, mtime set back to the
    identical ns value within the window. The next read sees the new text.
  - A missing file returns `None` and is not cached. An unreadable file raises
    and is not cached.
  - `invalidate` drops the entry.
- **`CfgDocument.freeze`.** Every mutator raises on a frozen document, and
  reads still work.
- **Views.** The existing error-rule tests pass unchanged. Add one test per row
  of the table, run through the snapshot path.
- **Writers read fresh.** Cache an aged file, then forge the stat key with an
  in-place same-size rewrite and a restored mtime. `Registry.mutate` and
  `settings.mutate` must see the new text, even though `Registry.load`, by
  design, does not.
- **Success measure.** Against an aged fixture, a second `fw.status` poll
  parses the config zero times, and the first poll parses it at most once.
  This sits beside `test_first_install_does_not_read_the_config_file_again`,
  which stays as it is. Its fixture is fresh, so it never hits the cache.
- **Suite-wide effect.** Test fixtures write their configs moments before
  reading them, so they fall inside the window and never hit the cache. The
  existing suite therefore runs the uncached path, as it does today. The new
  tests are what cover the hit path.
- **Mutation specs** (`scripts/mutations/config-snapshot.json`):
  - the key comparison forced true;
  - the trusted check removed;
  - `freeze`'s raise removed;
  - `Registry.mutate` switched to the cached read;
  - `settings.mutate` switched to the cached read.
- **Re-anchoring.** `single-write-path.json` anchors
  `current = load_settings(paths.settings_file)` inside `settings.mutate`,
  which this change rewrites. Re-anchor it in the same commit. Before the plan
  is written, grep `scripts/mutations/` for every other line the plan touches
  in `typelist.py`, `firmware.py`, `config.py`, `settings.py`, `cfgdoc.py` and
  `seed.py`.

## Docs

- `docs/decisions.md`: an entry covering the stat-keyed snapshot, the 2 s
  guard, writers reading fresh, the forged-key limitation, and why there is no
  inotify.
- `docs/layout.md`: one line for `cfgsnapshot.py`, if that file lists modules.

There is no user-facing change: no config key, no wire shape and no CLI
output changes, so neither README.md nor `docs/agent-api.md` changes.

## Risks

- **A read-only `Registry` that mutates its document.** Callers of
  `Registry.load` outside `mutate` (in `status.py`, `cli.py`,
  `providers/selection.py`, `providers/spec.py` and `tracking.py`) now hold a
  frozen doc. If any of them calls a `*_declared_*` mutator, it now raises.
  The first implementation task runs the full suite with freezing on. Any
  failure it finds is a latent bug: that edit could never have been saved. The
  fix is to move the edit into `Registry.mutate`.
- **Threads.** A snapshot doc is frozen, and derived objects are rebuilt on
  every call, so no shared object can be changed. The lock only guards the
  dict.
