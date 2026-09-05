# The cmake build provider — design

Status: design, approved 2026-09-05. Not yet implemented.

Verified against mcu-updater `0c446f2` and roadrunner `rp2040/CMakeLists.txt` as
it stood on 2026-09-04. Every file and line reference below was read at those
commits; re-check them before implementing if either tree has moved.

This is **project 1 of two**. It ends at a staged `.uf2` and a manual
BOOT/RESET flash — exactly the procedure `roadrunner/rp2040/README.md`
documents. The closed-loop BOOTSEL flash (`REBOOT_BOOTSEL`, topology
correlation, `needs_services_stopped`, a real `settled()`) is project 2 and has
its own spec; see "Deliberately not in scope" below.

## Problem

Roadrunner's RP2040 firmware is built by CMake driving the Pico SDK. Neither
provider here can build it: `kconfig_make` wants a Klipper-shaped tree with a
`Kconfig` and a saved `make menuconfig` answer file, and `platformio` wants a
`platformio.ini`. So a Roadrunner cannot be declared in `mcu-updater.cfg` at
all, and its firmware is built by hand and copied by hand.

The tree also builds **six images from one invocation**:

```text
roadrunner_v1_{uart,i2c,usbserial}_{rgb,grb}.uf2
```

`{uart,i2c,usbserial}` is the transport between MCU and sensor; `{rgb,grb}` is
the physical neopixel's colour ordering. Exactly one of the six belongs on any
given board, and nothing in this tool can currently say which.

## Decision

**Add a third provider, `cmake`, and a `variant:` key naming which of its build
outputs to stage.**

```ini
[firmware roadrunner]
source: ~/roadrunner/rp2040     # the cmake directory, not the repo root
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${git_describe}

[type roadrunner]
chipset: rp2040
firmware: roadrunner
variant: roadrunner_v1_i2c_rgb  # -> build/roadrunner_v1_i2c_rgb.uf2
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
```

### `variant:` is the cmake target name, spelled out

Not a short form like `i2c_rgb`. A short form would mean this provider knows
how to expand one into `roadrunner_v1_i2c_rgb` — that is a naming convention
belonging to one vendor's `CMakeLists.txt`, and the next cmake tree will not
share it. The target name is what CMake itself uses, so `build/<variant>.uf2`
is a fact rather than an invented rule, and `make <variant>` stays available
without a second mapping to keep in step.

It costs the user a longer value once, in a file they edit by hand with the
target list in front of them.

### One `make` builds all six; the variant selects which is staged

This was the shape decision. `cmake .. && make` produces every image, so
`variant:` is *which output gets copied to `paths.uf2_file()`*, not a
configure-time narrowing. That is why build-time and flash-time selection come
out of the same key with no extra machinery: the flasher reads the same staged
path it always has, and the variant has already been resolved before it looks.

`make <target>` could narrow the compile later if build time bites on a
printer. Deliberately not designed for now — the whole tree is small, and a
narrowed build would make `artifact_status` answer for one image while five
stale ones sit beside it.

### `variant:` is per-`[type]`, not per-board

RGB vs GRB is a property of the physical neopixel, so two Roadrunners on one
printer can genuinely differ. They get two `[type]` sections, each with its own
`serials:` list — which is the `env:` precedent from `providers/pio.py`
exactly, and needs no new machinery. One staged `.uf2` per type keeps
`paths.uf2_file(type, fw)` as it is.

Per-board would need a per-serial value shape in the config, a staged `.uf2`
per variant, and per-device selection at flash time. Revisit only when a real
mixed fleet exists.

### `source:` points at `rp2040/`, not the repo root

No new key, and the provider learns no subdirectory convention. A cmake tree
that keeps its `CMakeLists.txt` elsewhere just says so in `source:`.

### The provider owns its own artifact path

`firmware.built_artifact` hardcodes `out/<artifact>.<ext>`; cmake emits
`build/<target>.uf2`. Routing cmake through it would mean teaching a
Klipper-Makefile convention to a build system that does not share it. The
provider computes its own path, exactly as `pio.firmware_bin` does.

## Components

### `src/mcu_updater/providers/cmake.py` (new)

One module, not the adapter/body split `platformio.py`/`pio.py` uses. That
split exists because `flashers/esptool.py` and `agent/methods/status.py` also
import `pio` directly; nothing outside the provider seam needs cmake's body
yet. Split it when a second caller appears, not before.

Implements the `Provider` protocol from `providers/spec.py`:

**`load(paths) -> dict[str, CmakeType]`** — mirrors `pio.load()`. A type is
ours when the family it declares has `builder: cmake`. `variant:` absent is a
`ConfigError` naming the type, the same refusal `pio.load()` gives a PlatformIO
type with no `env:`.

**`targets(install)`** — one `BuildTarget` per declared cmake type, `fw` = its
declared family.

**`blocked(install, target)`** — the once-per-host setup that has to happen
outside this tool, and therefore a skip rather than a failure:

- `source:` unset, or naming a directory that does not exist (the
  `pio.source_problem` split — the fixes differ, so the messages must);
- no `CMakeLists.txt` in it;
- a `.gitmodules` listing a submodule whose directory is empty — the
  uninitialized-submodule case, whose fix is
  `git submodule update --init --recursive`, and worth naming because the
  CMake error for it is unreadable. Read from `.gitmodules` rather than
  hardcoding `pico-sdk/`, which is the Pico SDK's path and not a fact about
  cmake trees in general;
- `variant:` naming no target the tree declares (see below).

**How `blocked()` knows the target list.** After a successful configure,
`cmake --build <build> --target help` enumerates them; before one, there is no
build directory to ask. So this check is *conditional*: with a configured
build directory it is a real check, and without one it is skipped and the
mistyped variant surfaces as a `make` failure instead. Deliberate — the
alternative is parsing `CMakeLists.txt`, which means reimplementing CMake, and
a first build on a fresh clone would have nothing to check against anyway.

Not a prediction that the build will succeed. A missing ARM toolchain, a syntax
error, a full disk are all things to find out by trying — the protocol
docstring is explicit that reporting them as failures beats pretending we knew.

**`build(install, target, *, reporter, cancel)`** —

1. `cmake -S <source> -B <source>/build`, skipped when `build/CMakeCache.txt`
   already exists and its `CMAKE_HOME_DIRECTORY` matches (re-running configure
   on every build is slow and pointless);
2. `make -C <source>/build`, with `-j` from `settings.make_jobs` under the same
   rule `kconfig_make` uses (`0` means no flag at all, negative means one per
   CPU);
3. any `cmake_args:` from the `[firmware ...]` section appended to the
   configure step (see below);
4. stage `build/<variant>.uf2` → `paths.uf2_file(type, fw)`, which is what
   `build.py:755-762` already does for a katapult `.uf2`;
5. record the sidecar (below).

Both subprocesses go through `build.run_streamed`, so cancellation, dry-run and
log streaming behave as they do everywhere else. `build()` returns `None` per
the protocol.

**`cmake_args:` on the firmware family**, because a cache variable is a fact
about one tree and not about cmake:

```ini
[firmware roadrunner]
source: ~/roadrunner/rp2040
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${git_describe}
```

`${git_describe}` is the one substitution, expanded from the source tree's
`git describe --tags --always --dirty`. It exists because the alternative is a
version string that goes stale the moment it is written, and because the
Roadrunner's `INFO` response reports this value — a board answering `dev`
forever is a diagnostic we would have given up for nothing. An unresolvable
`git describe` (no tags, not a checkout) expands to `dev`, which is the
CMakeLists' own default.

The key lives on `[firmware ...]` rather than `[type ...]` because it
describes the tree, and every type sharing that tree wants the same answer.
`variant:` is the only per-type key this provider adds.

**`artifact_status(install, target)`** — modelled on `pio.artifact_status`, and
using `paths.sidecar_file(type, fw)` and `build.sha256_file`:

- no staged `.uf2` → `NEVER_BUILT`;
- no sidecar, or bytes on disk that are not the bytes we recorded →
  `NO_PROVENANCE`;
- recorded from a dirty tree → `BUILT_DIRTY`;
- recorded sha ≠ `git rev-parse HEAD` → `SOURCE_CHANGED`;
- otherwise current.

Not optional. Without it every answer is `NO_PROVENANCE` and every sweep
rebuilds — and `providers.select()` treats anything not provably current as
wanting a build, deliberately.

The two-tier check from `pio._is_our_image` carries over: size+mtime as the
fast path (this runs on the `fw.status` poll), content hash only when something
looks changed.

**`describe(target)`** — the type name, as `PlatformIO.describe` does.

### `src/mcu_updater/firmware.py`

One field on `FirmwareFamily`: `cmake_args: str = ""`, read from the section
and carried in `to_json()` beside `builder`. Optional and empty by default, so
every existing family is unaffected — the same shape `source:` and `artifact:`
already have.

Only the cmake provider reads it. That is consistent with how the module
already works: `[firmware ...]` is *the family*, not one provider's slice of
it, and `Install` already carries both section maps for the same reason.

### `src/mcu_updater/providers/registry.py`

One line: `PROVIDERS = (KconfigMake(), PlatformIO(), Cmake())`. Appended rather
than inserted — the tuple is a batch order, and reordering it inside a feature
is a behaviour change nobody asked for.

### `src/mcu_updater/config.py` — the closed-set inversion

Two sites branch on a **closed set of two builders**, and a third builder makes
both wrong:

- `config.py:388-394` skips a type from this registry when
  `builders == {"platformio"}`;
- `config.py:472` — `Registry.save()` — calls `doc.remove_section()` for any
  declared type not in `self.types` **unless** `_is_platformio_only`.

Both invert to: **this registry owns a type only when its builders are
`{"kconfig_make"}`.** `_is_platformio_only` is renamed to say what it now
asks — suggested `_is_foreign_builder`, returning True when a type belongs to
some other provider.

The `save()` half is the one that bites. Adding a cmake skip to `load()`
without it means a `[type roadrunner]` section is excluded from `self.types`,
`_is_platformio_only` returns False for it, and **the next save silently
deletes the user's section.** Config data loss, in a file that lives in
`printer_data/config` and is expected to be hand-edited.

## Error handling

Two failure classes, already distinguished by the protocol and kept apart here:

**Blocked** — setup missing outside this tool. Reported as a `Skipped` with a
reason a human can act on, never as a failure, and never takes a fleet build
down. Every case is listed under `blocked()` above.

**Failed** — `cmake` or `make` returned non-zero. Raises `BuildError` carrying
`type`, `fw` and `returncode`, matching `pio.build`'s shape. The transcript is
already streamed by `run_streamed`.

A `variant:` that names no target is deliberately *blocked*, not failed: it is
a config mistake with a clear fix, discoverable before spending a compile, and
failing a whole sweep over one mistyped variant is worse than skipping it and
saying so.

## Testing

- `load()`: a cmake type parses; a cmake type with no `variant:` raises
  `ConfigError`; a type whose family has no `builder:` is untouched (defaults
  to `kconfig_make`).
- **The save regression**: build a `Registry` from a config containing both a
  kconfig type and a cmake type, `save()` it, and assert `[type roadrunner]` is
  still present with its keys intact. This is the data-loss guard, and it fails
  against today's code.
- `blocked()`: one test per case, each asserting the message names the fix.
- `build()` under `dry_run`, asserting the argv for both subprocesses and that
  nothing is staged.
- `cmake_args:` reaches the configure argv verbatim; `${git_describe}` expands
  against a fixture checkout, and falls back to `dev` outside one.
- `build()` against a fixture tree with a stub
  `build/roadrunner_v1_i2c_rgb.uf2`, asserting the staged path is
  `paths.uf2_file(type, fw)` and that the sidecar was written.
- `artifact_status()`: one test per verdict, mirroring the `pio` suite.
- `providers.select()` picks up a cmake target on a sweep, and `only=`/`fw=`
  narrow to it.
- Line endings: `python scripts/check_line_endings.py` per AGENTS.md.

Mutation coverage via `scripts/mutation_test.py`, one spec at a time, never a
full sweep under a short timeout — AGENTS.md.

## Documentation

Per AGENTS.md "Finishing a plan", in this plan and not as a follow-up:

- `mcu-updater.cfg` — the worked `[firmware roadrunner]` / `[type roadrunner]`
  example above, commented in the file's existing voice.
- `README.md` — `builder: cmake` and `cmake_args:` in the firmware-family key
  list, `variant:` in the type key list, and the one-line note that a mixed
  RGB/GRB fleet takes two `[type]` sections.
- `docs/agent-api.md` — `cmake_args` joins `builder` in the family payload
  `agent/methods/status.py:203` emits.
- `docs/layout.md` — the staged `.uf2` path for a cmake type.
- `docs/decisions.md` — no entry. Nothing here closes an avenue; the per-board
  variant question is recorded in this spec instead, since it is deferred
  rather than refused.

## Deliberately not in scope

**The closed-loop BOOTSEL flash.** `REBOOT_BOOTSEL` (opcode `0x02`), the
topology correlation across the reboot, the port parameter that lifts
`_find_mount`'s multi-volume refusal, `Bootsel.needs_services_stopped` flipping
to `True`, and a real `settled()`. That is project 2, and it builds on
`docs/bootsel-mountpoint-design.md`, which measured the correlation facts on
hestia 2026-09-04 and lists this loop as its own out-of-scope item.

Until it lands, a Roadrunner is flashed the way its README documents: hold
`BOOT`, press and release `RESET`, release `BOOT`, copy the staged `.uf2` to
the mounted volume.

**A `select_for` axis for BOOTSEL routing.** `Flashtool.chipsets` is
`("stm32", "rp2040")` and its states include `STATE_KLIPPER`, and `Flashtool()`
is first in a first-match `FLASHERS` tuple — so `select_for("rp2040",
STATE_KLIPPER)` resolves to Katapult flashtool today, and a Roadrunner flasher
registered after it would never be reached. The agreed fix is a third axis on
`select_for`: whether the type carries a bootloader family (a Roadrunner
declares none; an SKR Pico declares katapult). It belongs to project 2, where
there is a flasher for it to select.

**Narrowing the compile to one `make` target.** See "One `make` builds all six"
above.
