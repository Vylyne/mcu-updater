# The cmake build provider — design

Status: **shipped**, merged to `develop` 2026-09-06. See
[cmake-provider.md](cmake-provider.md) for what it does, how it is configured,
and what is still open.

This is the reasoning, kept because the decisions below are the ones that are
expensive to rediscover — the per-file walkthrough, the test list and the
error-handling table that lived here as well have been dropped, because the
code now says all three better than prose can.

Written against mcu-updater `0c446f2` and roadrunner `rp2040/CMakeLists.txt` as
it stood on 2026-09-04. The line numbers below are from that commit and have
moved since; the arguments have not.

This was **project 1 of two**. It ends at a staged `.uf2` and a manual
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

**Add a third provider, `cmake`, and a `cmake_target:` key naming which of its build
outputs to stage.**

```ini
[firmware roadrunner]
source: ~/roadrunner/rp2040     # the cmake directory, not the repo root
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${git_describe}

[type roadrunner]
chipset: rp2040
firmware: roadrunner
cmake_target: roadrunner_v1_i2c_rgb  # -> build/roadrunner_v1_i2c_rgb.uf2
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
```

### `cmake_target:` is the cmake target name, spelled out

Not a short form like `i2c_rgb`. A short form would mean this provider knows
how to expand one into `roadrunner_v1_i2c_rgb` — that is a naming convention
belonging to one vendor's `CMakeLists.txt`, and the next cmake tree will not
share it. The target name is what CMake itself uses, so `build/<cmake_target>.uf2`
is a fact rather than an invented rule, and `make <cmake_target>` stays available
without a second mapping to keep in step.

It costs the user a longer value once, in a file they edit by hand with the
target list in front of them.

**The key is named for CMake's own vocabulary, not ours**, and the namespacing
follows from the same rule. This argument was general enough to outlive the
feature, so it now lives in [decisions.md](decisions.md) as "Config keys borrow
the upstream tool's own vocabulary".

### One `make` builds all six; the target selects which is staged

This was the shape decision. `cmake .. && make` produces every image, so
`cmake_target:` is *which output gets copied to `paths.uf2_file()`*, not a
configure-time narrowing. That is why build-time and flash-time selection come
out of the same key with no extra machinery: the flasher reads the same staged
path it always has, and the target has already been resolved before it looks.

`make <target>` could narrow the compile later if build time bites on a
printer. Deliberately not designed for now — the whole tree is small, and a
narrowed build would make `artifact_status` answer for one image while five
stale ones sit beside it.

### `cmake_target:` is per-`[type]`, not per-board

RGB vs GRB is a property of the physical neopixel, so two Roadrunners on one
printer can genuinely differ. They get two `[type]` sections, each with its own
`serials:` list — which is the `env:` precedent from `providers/pio.py`
exactly, and needs no new machinery. One staged `.uf2` per type keeps
`paths.uf2_file(type, fw)` as it is.

Per-board would need a per-serial value shape in the config, a staged `.uf2`
per target, and per-device selection at flash time. Revisit only when a real
mixed fleet exists.

### `source:` points at `rp2040/`, not the repo root

No new key, and the provider learns no subdirectory convention. A cmake tree
that keeps its `CMakeLists.txt` elsewhere just says so in `source:`.

### The provider owns its own artifact path

`firmware.built_artifact` hardcodes `out/<artifact>.<ext>`; cmake emits
`build/<target>.uf2`. Routing cmake through it would mean teaching a
Klipper-Makefile convention to a build system that does not share it. The
provider computes its own path, exactly as `pio.firmware_bin` does.

## How it works, where the reason is not obvious

### Subtree scoping — `source:` is not the repo root

**The Roadrunner is the first source tree that is a subdirectory of its
repository**, and git does not scope itself to a subdirectory. Run from
`~/roadrunner/rp2040`, `git rev-parse HEAD` and `git status --porcelain` walk
up and answer about the whole repo. Measured on the roadrunner repo,
2026-09-05:

```text
repo HEAD                        1d7660f
last commit touching rp2040/     35864cd
```

`pio.source_state()` uses the repo-wide form, and was always right to: knomi
serial's `source:` *is* its repo root. Copying it here would mean every commit
to `klippy/extras`, `docs/` or the README marks the built `.uf2`
`SOURCE_CHANGED` and triggers a rebuild that produces byte-identical output.

So this provider's `source_state()` splits two questions that used to be one:

- **"Did the firmware source change?"** — subtree-scoped.
  `git log -1 --format=%H -- .` for the sha, `git status --porcelain -- .` for
  dirty. This is the provenance recorded in the sidecar and the thing
  `artifact_status` compares, so it is what decides a rebuild.
- **"What release is this?"** — repo-wide `git describe`. A tag is a fact about
  the repository, not about one directory in it, and this is the string the
  board reports back.

The two legitimately disagree, and both are recorded. `git describe --dirty`
takes no pathspec, which is why the dirty suffix is appended from the
subtree-scoped status instead — otherwise a dirty `klippy/extras` would stamp
`-dirty` on a clean firmware build.

A `source:` that *is* a repo root gets identical answers from both forms, so
nothing else changes shape.

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

**Retrofitting subtree scoping onto `pio.source_state()`.** It has the same
latent issue — a PlatformIO `source:` pointing at a subdirectory of a larger
repo would over-report `SOURCE_CHANGED` the same way. No such install exists:
knomi serial's `source:` is its repo root, which is why the repo-wide form has
always been correct there. Changing it would alter the rebuild behaviour of
every existing display install to fix a case nobody has, so the two
implementations differ deliberately until a real one turns up. Recorded here
rather than in `decisions.md` because it is deferred, not refused.
