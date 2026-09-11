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

## The one real design decision

`pio.running_sha` will not do. Its `_FW_SHA_RE` is `\+(?:\d+\.)?g([0-9a-f]{6,40})`
- PlatformIO's `+`-delimited form. `git describe --tags --always` produces
`v1.2.3-4-gabcdef` with a hyphen, or a bare sha when there is no tag, or the
literal `dev` (`cmake.UNKNOWN_VERSION_STRING`) when the describe fails and the
CMakeLists' own default wins.

CMake gets its own `running_sha`, in `providers/cmake.py`, beside the
`SourceState` that produces the string it parses. It recognises the
git-describe forms and returns None for `dev` and for a bare tag - None being
"cannot tell", which `entry_for` already treats as a reason to withhold a
verdict rather than to claim a match. Copying `pio`'s regex and loosening the
delimiter would make one expression answer for two independently-versioned
build systems, which is the drift `pio.py`'s own comment at `_FW_SHA_RE` warns
against.

`-dirty` needs the same treatment `pio._FW_DIRTY_RE` gives it: a build from a
dirty tree is never reproducible, so it can never be *shown* to match, and
saying "up to date" about one is a lie.

## What changes, in dependency order

1. `fw_version` stops being discarded - `RoadrunnerDevice` carries it, and the
   CMake device path in `status.py` can reach it.
2. `flashers/helper_bootsel.py` writes `FlashLog`, on the same rule
   `agent/methods/flash.py::_cmake_flash` states: whenever a copy completed,
   before any failure is raised.
3. `cmake.running_sha` lands, with the `-dirty` and unknown-version cases.
4. `_cmake_target` returns a real `DeviceStatus` from the comparison, and
   reports a version on its device rows.
5. `_boards_to_flash` enumerates CMake boards; `_require_flashable_type` stops
   refusing; `_flash_actions` goes on the CMake type row; the CLI's
   `-t`-only refusal is replaced by the same path.

Steps 1-4 are what make step 5 honest. Step 5 is mechanical once they land.

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
