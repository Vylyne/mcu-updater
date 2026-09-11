# CMake types join the provenance axis

## The principle this spec answers to

> the seams in the backend are supposed to be more like building blocks that
> can be assembled via the config into firmware and board/function (type)
> definitions that then get processed and displayed in the same way.

A type is a type. The config says which building blocks assemble it; nothing
downstream of that should care. `flash_all` means all.

The previous spec
(`2026-09-10-provider-selection-seam-design.md`) says the opposite in its
section "Type-level flash stays refused, honestly", and its plan carries a
Global Constraint "No type-level CMake flash". **That was a misreading of
intent, not a design decision being revisited here.** This spec supersedes
both. The refusal shipped on `feat/provider-selection-seam` and was correct
for exactly one reason - the backend could not serve the button honestly - and
this spec removes that reason and then the refusal.

## Why "all" could not include CMake

Not because bulk flash is hard. Because CMake types are second-class on a
different axis entirely: **provenance**. Three gaps, found by reading the code
rather than by reasoning about it:

1. **No version.** `status.py::_cmake_target` sets
   `DeviceStatus(UNKNOWN_VERSION if present else OFFLINE)`. It has no idea
   what is on the board.
2. **No record.** Every other flasher writes a `FlashLog` entry - `flash.py`
   twice, `esptool.py`, and the agent's own `_cmake_flash`. `flashers/
   helper_bootsel.py` is the only one that does not, so a CLI CMake flash
   records nothing.
3. **No read.** `flashlog.entry_for` is consulted at four sites in
   `status.py`. `_cmake_target` is at none of them.

`_boards_to_flash` gates on `info["needs_flash"]`. For a CMake type there is
nothing to compute that from, so accepting the name would have flashed zero
boards and reported success - strictly worse than an error. That is the whole
of the reason, and it is a gap, not a boundary.

## The version source already exists and is thrown away

`scripts/roadrunner_usb.py`'s INFO reply carries `fw_version`, a 32-byte ASCII
field, alongside `protocol`, `model`, `serial` and `provisioned`.
`discovery/roadrunner.py::_valid_info` checks the other four and discards it.
Nothing in `src/` reads `fw_version` at all.

The loop it closes was designed in already. `cmake.expand_args` substitutes
`${git_describe}` - `SourceState.version`, a repo-wide `git describe --tags
--always` with a subtree-scoped `-dirty` suffix - into the family's
`cmake_args:`, and the firmware stamps it. `cmake.record_build`'s docstring
states the intent outright: *"`sha` is subtree-scoped and decides rebuilds,
`version` is repo-wide and is what the board reports back."* The board does
report it back. Nobody listens.

## The precedent to follow is the screen, not the MCU

A Roadrunner is not a Klipper MCU. It enumerates as
`usb-Vylyne_Roadrunner_RR-...-if00` and speaks its own binary protocol, so
`mcu_info()` - which joins `mcu_version` against `[mcu ...] serial:` from
`configfile.settings` - can never see one. Designing around `mcu_info` is a
dead end.

Displays are already in exactly this position, and `status.py` already serves
them fully: `_screen_confidence` keys the ledger on a device-reported id and
`pio.running_sha(...)`, and `_screen_device_status` layers `PROTOCOL_MISMATCH`
on top of the version comparison rather than folding it in. A Roadrunner has
all three inputs: the canonical `RR-` serial as the key, `fw_version` as the
running version, and `protocol == 1` / `model == "roadrunner-v1"` - which
`_valid_info` already evaluates - as the mismatch signal.

## The one real design decision: where version-reading lives

`pio.running_sha` will not do. Its `_FW_SHA_RE` is `\+(?:\d+\.)?g([0-9a-f]{6,40})`
- PlatformIO's `+`-delimited form. `git describe --tags --always` produces
`v1.2.3-4-gabcdef` with a hyphen, or a bare sha when there is no tag, or the
literal `dev` (`cmake.UNKNOWN_VERSION_STRING`) when the describe fails and the
CMakeLists' own default wins.

The first draft of this spec put the replacement in `providers/cmake.py`,
beside the `SourceState` that produces the string. That is the wrong seam, and
`docs/decisions.md` now says why: **providers are per build system, not per
vendor.** Two firmwares built by the same provider can stamp their versions
differently, and one firmware can change how it stamps without changing how it
builds. Cartographer is the standing proof - it is built by `kconfig_make`
exactly as stock klipper is, and it reports a version string klipper's own
parser cannot read, because its fork patches `buildcommands.py`. Putting the
parser in the provider would say the build system decides the version format.
It does not. The firmware does.

So version-reading is a **helper** question, answered per firmware family.

### This widens the helper seam, deliberately

`helpers/spec.py` today is one protocol, `BootselRequester`, and its own
docstring scopes it: *"The narrowly scoped contract for firmware-specific
BOOTSEL requests."* Both its methods take a `Bench`, a serial and a chipset -
they are device-shaped, about acting on hardware. Version-reading is not that
shape. It is a pure `str | None -> str | None` function of a reported version
string, needing no bench, no port and no device at all.

Adding it is therefore a second capability alongside the first, not a method
bolted onto `BootselRequester`. A firmware can need one without the other: a
knomi screen is never put into BOOTSEL and still reports a version; a future
board could take a helper-driven BOOTSEL write and report nothing back.
Bundling them would force every helper to implement a method it has no answer
for, which is how a narrow seam turns into a god object.

The shape:

```python
class VersionReader(Protocol):
    name: str
    def running_sha(self, running: str | None) -> str | None: ...
```

`registry.HELPERS` keeps its rule unchanged - each implementation reviewed and
named explicitly, never resolved by scanning or by config-driven import - and
`for_name` grows a capability-aware form, or a second lookup, so that asking
for a reader a family does not have is a `None`, not a crash.

### The three helpers this needs, and what each returns

**roadrunner** - new behaviour. Recognises the `git describe --tags --always`
forms `cmake.expand_args` feeds into the firmware. Returns None for `dev` and
for a bare tag with no commit - None being "cannot tell", which
`FlashLog.entry_for` already treats as a reason to withhold a verdict rather
than to claim a match. `-dirty` gets the treatment `pio._FW_DIRTY_RE` gives it:
a build from a dirty tree is not reproducible, so it can never be *shown* to
match, and saying "up to date" about one is a lie.

Copying `pio`'s regex and loosening the delimiter would make one expression
answer for two independently-versioned build systems, which is exactly the
drift `pio.py`'s own comment at `_FW_SHA_RE` warns against.

**knomi_serial** - existing behaviour, relocated. `_screen_confidence` calls
`pio_mod.running_sha(...)` directly today. The helper wraps that call rather
than reimplementing it: PlatformIO's form is what knomi actually stamps, so the
regex stays where it is and the helper names *whose* format it is. This is the
smallest possible migration and it is the point of doing it - it proves the
seam carries an existing firmware without changing what that firmware reports.

**cartographer** - existing behaviour, made explicit. Returns None, always, and
that is the terminal answer rather than a gap. `docs/decisions.md`, "Do not
synthesize a sha into Cartographer's `CONFIG_VERSION`", settles this: the fork
discards the git describe, so there is no commit in the string, and
synthesizing one would permanently report `customised` profiles and
`CONFIG_CHANGED` artifacts. A cartographer is judged instead by comparing
`CONFIG_VERSION` against `profiles.stamped_version`, backed by the flash record
- `states.VERSION_ONLY`. The helper must carry that reasoning in its docstring
so nobody reads the `return None` as unfinished work.

Adding the two existing-behaviour helpers is not required to close the CMake
provenance gap. It is required to show the seam is a seam and not a Roadrunner
special case with a protocol wrapped round it - the same mistake in the same
place, one layer up.

## What changes, in dependency order

1. `fw_version` stops being discarded - `RoadrunnerDevice` carries it, and the
   CMake device path in `status.py` can reach it.
2. `flashers/helper_bootsel.py` writes `FlashLog`, on the same rule
   `agent/methods/flash.py::_cmake_flash` states: whenever a copy completed,
   before any failure is raised.
3. The `VersionReader` capability lands in `helpers/spec.py` and
   `helpers/registry.py`, with the roadrunner reader implementing it - the
   `-dirty` and unknown-version cases included.
4. `_cmake_target` returns a real `DeviceStatus` from the comparison, and
   reports a version on its device rows.
5. `_boards_to_flash` enumerates CMake boards; `_require_flashable_type` stops
   refusing; `_flash_actions` goes on the CMake type row; the CLI's
   `-t`-only refusal is replaced by the same path.

6. The knomi_serial and cartographer readers land, and `_screen_confidence`
   asks the seam rather than `pio_mod.running_sha` directly.

Steps 1-4 are what make step 5 honest. Step 5 is mechanical once they land.
Step 6 changes no behaviour and is what keeps step 3 from being a private
arrangement between one helper and one caller; it is sequenced last only
because it must not block the gap being closed.

## What this spec does not settle

**BOOTSEL sequencing.** A CMake write reboots the board into its ROM
bootloader and stops services to do it. A sweep over several CMake boards has
physical sequencing that an esptool or flashtool write does not, and a
partially-swept fleet is a state the batch has to be able to describe. This
needs specifying - it does not need CMake carved back out, and it must not be
allowed to become that argument a second time.

**The `type_not_bulk_flashable` code.** It is documented as stable in
`docs/agent-api.md` for exactly as long as step 5 takes. Its retirement is
part of step 5, not a separate deprecation.

**Whether `VersionReader` is reached through `for_name`.** `for_name` returns a
`BootselRequester` today and its callers type it as one. Whether the second
capability arrives as a widened return, a parallel `reader_for_name`, or a
`for_name(..., capability=...)` is an implementation choice for the plan. What
this spec fixes is that asking a family for a capability it does not declare
must return None rather than raise - a firmware with no reader is an ordinary
firmware, not a misconfiguration.

**Config strictness and the "undefined equals klipper" default.** Declaring a
helper on a family is how a family gets a reader, and `firmware.py` currently
holds that *"every key is optional and the section itself is optional"*, with
`resolve()` inventing a conventional family for any undeclared name. That
legacy is being reconsidered separately - see the README TODO - and nothing in
this spec depends on which way it goes: an undeclared family resolves to a
family with no `helper:`, which yields no reader, which yields None, which is
the same "cannot tell" every other unanswerable case produces.
