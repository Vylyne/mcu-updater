# The cmake build provider

What shipped, what was decided along the way, and what is still open. This
replaces the implementation plan and the review reports that produced it — the
plan is in git history (`git log -- docs/cmake-provider-plan.md`), and the
design doc it argued from is [cmake-provider-design.md](cmake-provider-design.md).

## What it does

A third build system beside `kconfig_make` and `platformio`, for trees that
build with CMake. Written for the Roadrunner rp2040, which builds six images
from one tree.

| Key | Where | What |
| --- | --- | --- |
| `builder: cmake` | `[firmware ...]` | Routes the family to this provider. Provider ownership is derived from the family's builder; there is no `provider:` key. |
| `cmake_args:` | `[firmware ...]` | Configure-step arguments, `shlex.split` — quoting groups words and is consumed. `${git_describe}` is the one substitution. |
| `submodules:` | `[firmware ...]` | Sync submodules before each build. Off by default. |
| `helper:` | `[firmware ...]` | Optional reviewed helper capability from the static registry. `roadrunner` provides the Roadrunner BOOTSEL request; this is a name, not a module path. |
| `cmake_target:` | `[type ...]` | Which of the tree's executables this board runs. Required — one tree builds several, and guessing is not this tool's business. |

Reachable as `mcu-updater build -t NAME -f FAMILY`, `mcu-updater clean -t NAME`,
the agent's `fw.build` / `fw.clean`, a `targets[]` row in the panel, and a fleet
build. A helper-backed CMake type also exposes its declared serial devices and
routes `fw.flash` through the helper-backed BOOTSEL flasher.

## Decisions worth keeping

**Provenance is sampled across the build, not at one end of it.** `version` is
read *before* the compile, because that is the string `-D` puts into the binary
and the board reports back. `sha` and `dirty` are read *after*, because they
answer what was compiled rather than what we set out to compile. `dirty` is
forced true if the subtree was unclean at *either* end or the commit moved
between them — all three legs are needed, and each was a bug found in review:

- a commit landing mid-build recorded a clean build of a commit that did not
  produce the bytes;
- an edit compiled in and then discarded left the tree clean at the sha it
  started on, with a `-dirty` version string beside a `dirty: false` record.

Both made `artifact_status()` answer `current` forever.

**The staged image is checked against the tree that produced it.** `make`
builds `all` and succeeds when an upstream rename drops the configured target;
the previous build's `.uf2` survives on disk. `build()` therefore re-asks
`declared_targets()` *after* `make` and refuses to stage an image this run
could not have produced. After `make`, not after configure: configure is
conditional, so a tree with no `cmake_args:` that is already configured never
re-runs it, and a post-configure answer could come off a stale build system.

**The target probe never runs on the status poll path.** It shells out to
`cmake` with a 30s timeout. `source_problem(probe_targets=False)` is what the
`fw.status` row asks; the build path asks for everything.

**`build/` is excluded from the dirty pathspec.** It lives inside the source
subtree by design, so without the exclusion every build after the first would
report the tree dirty on account of its own output.

**The sidecar names its provider.** The path is shared with `kconfig_make` and
the schemas differ. It now degrades to `NO_PROVENANCE` by design rather than by
a lucky difference in key names.

**No general pre-build hook.** A `pre_build_cmd:` in a hand-edited `.cfg` is
arbitrary execution in a process that holds the exclusive lock and has NOPASSWD
`systemctl` — the case [decisions.md](decisions.md) already refuses for plugin
auto-discovery. `submodules:` is the narrow capability instead.

**The key is `cmake_target:`, spelled out.** Not a short form like `i2c_rgb` —
expanding one into `roadrunner_v1_i2c_rgb` means knowing a naming convention
that belongs to one vendor's `CMakeLists.txt`. The name and the namespacing
follow the standing rule in [decisions.md](decisions.md), "Config keys borrow
the upstream tool's own vocabulary", which this key is the worked example
for.

**Correlation has a ceiling, and `cmake_args:` is what buys the middle tier.**
Three levels: the bytes we sent (`bin_sha256` in the sidecar, ours and exact);
the source the board reports (`${git_describe}` compiled in via `-D`, returned
by the Roadrunner's `INFO` as `identity.firmware_version`, which
`FlashLog.entry_for()` already compares and discards our record on mismatch —
"something else flashed this board since"); and the ceiling, which is that a
board cannot report its own binary hash. The maintenance protocol has no opcode
for it. Byte-level identity stays our own record. That is why `cmake_args:` is
not a convenience — the version string is the only channel through which a
Roadrunner can tell us anything about what it is running.

**`clean()` is on the Provider protocol, not a cmake special case.** kconfig and
platformio answer `None`, so a caller can offer the action on any type without
knowing which build systems keep a directory.

## Open follow-ups

None is load-bearing; all were reviewed and deliberately deferred.

- **`build.read_sidecar` accepts any dict.** The cmake side now requires its own
  `provider` key; the kconfig side does not, so a record written by the other
  provider is read as its own. Reaching a *wrong current* needs a `builder:`
  flip in config and a stale `.bin` from a previous life — very narrow. Fix is
  symmetrical: reject a `provider` that is neither absent nor its own.
- **The staged-image guard can vanish silently.** `declared_targets()` returns
  `None` for "could not be asked", and the code falls back to `os.path.exists`.
  On a host where cmake is missing and the tree is already configured, `make`
  still succeeds and the guard evaporates with no warning. One `reporter("warn",
  ...)` on that path.
- **`clean_before_build` is kconfig-only.** A user bitten by a stale build
  directory reaches for it and finds it does nothing for cmake. Either honour it
  or say so in the README.
- **The auto-retry.** Wipe `build/` and re-run configure once when configure
  fails, reporting that it did. Chosen and deferred; the explicit `clean`
  shipped first.
- **`_uninitialised_submodule` only detects an *empty* directory.** A submodule
  path that does not exist at all is skipped, and the user gets the unreadable
  CMake error the check exists to prevent.
- **The agent's cmake build takes no exclusive lock**, where the CLI's does.
  Parity with `_pio_build`, and the job runner serialises within the agent — but
  a CLI build and an agent build can run concurrently against the same tree.
  Matters more here than for kconfig, because cmake writes inside the source
  tree.
- **`_cmake_types` is missing from `_api.py`'s mixin protocol**, where
  `pio_types` is declared. Pattern inconsistency, not a type hole.
- **No test names the `BUILD_SUBDIR` exclusion.** It is guarded incidentally, so
  a refactor of those fixtures loses the guard silently.
- **`pio.build()` has both provenance legs open** — it takes the naive wholesale
  post-build sample. Pre-existing, and out of scope for that branch, but whoever
  next reads `cmake.py`'s record block will be looking straight at it.

## Helper-backed flashing

CMake boards are written through their family's `flashers:` list like every
other board. For `[firmware roadrunner]` that list is `bootsel`, which writes a
running board by asking the family's helper to put it into BOOTSEL, copying the
staged UF2 to the volume matching the board's USB topology, and waiting for the
helper to confirm the board came back. A board already sitting in BOOTSEL is
written the same way without a helper. The RP2040 flashtool route is untouched
for families that list `flashtool`.

That list is authoritative and is not checked against what the builder stages -
see [decisions.md](decisions.md), "Do not infer builder/flasher compatibility
from the staged filename". Today the pairing that does not fit crashes rather
than refusing: a CMake family listing `flashtool` reaches `flashtool.target_for`
with a request detail carrying only `uf2_file`, and the bare `KeyError` on
`board["type"]` is not an `UpdaterError`, so `flashers.select_each` does not
catch it. It is in the README ledger as its own bug.

The Roadrunner helper confirms the provisioned serial over the admin protocol,
captures the full serial `by-path` topology before `REBOOT_BOOTSEL`, and accepts
only one `INFO_UF2.TXT`-bearing mount under the matching normalized topology.
Unrelated BOOTSEL mounts are ignored; zero or multiple matching mounts fail
closed. After copying, it waits for the expected Roadrunner serial and INFO
response before service restart. Everything that wait can find is a warning
after a completed write, matching the other flashers' settle behavior - a
timeout, an ambiguous identity, a probe that failed. A probe that fails or
answers unconvincingly is retried to the deadline rather than believed: while
the board re-enumerates, its `by-id` symlink appears before its tty can
reliably be opened.

The target status includes every configured serial. Presence is determined by
exact serial discovery; status reads device information through the family's
helper and applies the common verdict to each board, so `version` and
`needs_flash` are populated whenever the board supplies enough evidence. Device
flash actions and `extra.flashable: true` are offered only when a helper is
configured.

This path is covered by host tests but has not yet been exercised as an
end-to-end hardware flash. Manual first-install BOOTSEL behavior remains the
ordinary ambiguous one-board workflow because a bare board has no running
firmware helper or provisioned serial.
