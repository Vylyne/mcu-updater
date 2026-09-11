# CMake types join the provenance axis — Implementation Plan

Spec: `docs/superpowers/specs/2026-09-11-cmake-provenance-design.md`

CMake types are second-class on one axis: provenance. There is no running
version, no flash record, and nothing that reads one. `_boards_to_flash` gates
on `info["needs_flash"]`, so a CMake type has nothing to compute that from —
which is why `flash_all` refuses it, and why that refusal is a gap rather than
a boundary.

This plan closes the gap and then removes the refusal. It also widens the
helper seam, because the version question is per firmware and not per build
system, and it lands the firmware image digest the Roadrunner now reports.

Nothing here depends on the config-strictness work (README TODO: declaring
every firmware in config, retiring "undefined equals klipper"). An undeclared
family has no `helper:`, which yields no reader, which yields None — the same
"cannot tell" every other unanswerable case produces.

## Global Constraints

- **Klipper first, admin protocol second.** Klipper holds the usbserial
  connection when connected, so the admin protocol cannot open a port Klippy
  owns. A read that goes to the wire first fails on exactly the machines where
  the answer was already sitting in the object graph.
- **Absence is never mismatch.** A missing digest — pre-revision board,
  algorithm `0`, or no stored record — falls through to the version
  comparison. Treating absence as mismatch flags every old board permanently,
  and reflashing does not clear it.
- **A digest mismatch outranks a version match.** The digest is the image; the
  version is a label the image carries, and it survives a truncated write.
- **Never `needs_flash: False` on absent evidence.** `states.py` says so, and
  it is the bug that module exists to fix.
- **The reason is the fact.** `state`, `tone` and `needs_flash` stay derived
  from `reason`, never stored beside it.
- **The board's reported `image start` / `image length` are authoritative.** A
  host must not substitute its own. The linked image does not end on a
  256-byte boundary.
- **No behaviour change for kconfig or PlatformIO.** Task 7 changes none
  either — it relocates a call.
- Every task ends with the full gate green: `pytest`, `ruff`, `mypy`, all-LF,
  `git diff --check`. Mutation-check new guards with `scripts/mutation_test.py`
  and a spec under `scripts/mutations/`; never a throwaway script.

## File structure

| File | Change |
|---|---|
| `src/mcu_updater/discovery/roadrunner.py` | carry `fw_version` and the digest fields off INFO |
| `src/mcu_updater/agent/methods/status.py` | read the Klipper object; real `DeviceStatus` for CMake; `_screen_confidence` via the seam |
| `src/mcu_updater/flashers/helper_bootsel.py` | write `FlashLog` |
| `src/mcu_updater/helpers/spec.py` | NEW `VersionReader` capability |
| `src/mcu_updater/helpers/registry.py` | capability-aware resolution |
| `src/mcu_updater/helpers/roadrunner.py` | git-describe reader |
| `src/mcu_updater/helpers/knomi_serial.py` | NEW — wraps `pio.running_sha` |
| `src/mcu_updater/helpers/cartographer.py` | NEW — permanent None, with the reasoning |
| `src/mcu_updater/uf2.py` | NEW — image digest over the reported range |
| `src/mcu_updater/providers/cmake.py` | `record_build` stores the staged digest |
| `src/mcu_updater/states.py` | one new device reason |
| `src/mcu_updater/agent/methods/bulk.py`, `src/mcu_updater/cli.py` | enumerate CMake; retire the refusal |
| `AGENTS.md`, `README.md`, `docs/agent-api.md` | the seam, the features, the codes |

## Task 1: The running version becomes reachable

Two sources, in the order the lock forces.

**Klipper first.** The Roadrunner's extra populates a
`high_resolution_filament_sensor` printer object carrying version information.
`status.py` already has the machinery: `_all_object_names()` is cached with a
TTL and `_object_names_for(prefix)` selects by prefix — its own comment says
one list serves every prefix at no extra round trip. Add the prefix; do not add
a probe.

**Admin protocol second.** `discovery/roadrunner.py::_valid_info` checks four
INFO fields and discards `fw_version`. Carry it, and the digest fields the
reader now parses, on `RoadrunnerDevice`.

**Confirm against the firmware before coding:** the object's exact name, the
field carrying the version, and whether it is the same `${git_describe}` string
INFO reports. If the two can differ in format the reader accepts both — the
`running_sha` contract is unchanged either way, being a string parser that does
not care which side of the machine produced the string.

**Acceptance:** gate green. Tests assert the Klipper object is preferred when
present, the INFO value is used when it is not, and that no path opens the port
when Klippy answered.

## Task 2: `helper_bootsel` writes a flash record

Every other flasher writes `FlashLog` — `flash.py` twice, `esptool.py`, and the
agent's own `_cmake_flash`. `flashers/helper_bootsel.py` is the only one that
does not, so a CLI CMake flash records nothing.

Same rule `_cmake_flash` states and explains: whenever a copy completed, before
any failure is raised.

**Acceptance:** gate green. A test asserts a record exists after a write that
subsequently failed.

## Task 3: The `VersionReader` capability

`helpers/spec.py` is a protocol seam — `request_bootsel` and `wait_ready` both
take a bench, a serial and a chipset, and talk to a device in its firmware's
own vocabulary. Version-reading is not that shape: it is
`str | None -> str | None`, with no device involved.

So it arrives alongside `BootselRequester`, not on it. A knomi screen never
enters BOOTSEL and still reports a version.

```python
class VersionReader(Protocol):
    name: str
    def running_sha(self, running: str | None) -> str | None: ...
```

`registry.HELPERS` keeps its rule: each implementation reviewed and named
explicitly, never scanned for and never resolved by config-driven import.
Asking a family for a capability it does not declare returns None, never
raises.

**Open for the implementer:** whether this is a widened `for_name` return, a
parallel `reader_for_name`, or `for_name(..., capability=...)`. Any is fine;
the None-not-raise rule is not.

The roadrunner reader lands here: recognises the `git describe --tags --always`
forms, returns None for `dev` and for a bare tag, and treats `-dirty` as
`pio._FW_DIRTY_RE` does. Do not loosen `pio`'s regex to cover both — that is
the drift its own comment warns against.

**Acceptance:** gate green. Mutation-checked. A family with no reader resolves
to None without raising.

## Task 4: The UF2 image digest — start this one first

Independent of Tasks 1-3: it touches only the build and the ledger, and until
Task 5 reads it, storing it changes nothing. It is the only task needing a new
algorithm rather than a wire-up, it has a golden vector to test against before
any board exists, and every board flashed before it lands carries no stored
digest until its next flash.

Port `roadrunner/scripts/uf2_image_digest.py`. Per block: verify the three
magic values; skip a block whose flag bit `0` (`NOT_MAIN_FLASH`) is set; **read
`payloadSize` from the header rather than assuming 256** — blocks carry up to
476 bytes and 256 is a convention of the tooling, not a rule of the format;
place payload byte `i` at `targetAddr + i`, discarding bytes outside
`[start, start + length)`. Then require every byte of the range to have been
supplied, and digest exactly `length` bytes. **A gap is an error, not a hole to
fill with `0xFF`** — filling yields a plausible wrong number instead of a
visible failure.

Algorithm `1` is CRC-32/ISO-HDLC, which is what `zlib.crc32` computes.

`cmake.record_build` stores the digest of the staged payload beside the hash it
already keeps.

**Test against the golden vector, not against their implementation:** 600 bytes
at `0x10000000` where byte `i` is `(i * 7 + 3) & 0xff`, CRC `0xBBE38AA9`,
packed as three 256-byte-payload blocks whose last carries 88 image bytes and
168 bytes of padding. A host that digests the container, or that rounds the
length up to 768, gets a different number. Testing two implementations against
each other proves only that they are wrong together.

**Acceptance:** gate green. The golden vector passes and both documented wrong
answers fail. Mutation-checked.

## Task 5: The comparison, and one new state

`states.py` gains **one** device reason for a digest mismatch: `needs_flash:
True`, tone `TONE_ATTENTION` by derivation, labelled as an unexpected image
rather than an available update.

**Not `ARTIFACT_CHANGED`** — that means "same commit, different binary" and
labels as "Newer build available", promising something better on disk. A
mismatch says the board is not running what we wrote. It has
`PROTOCOL_MISMATCH`'s shape: positive evidence about the device.

Absence needs no new code: it falls through to the version reasons already
there.

A match discharges two existing ambers. `VERSION_ONLY` already documents
resolving to None once our own record backs the match, and a digest match is
that corroboration arriving from the device rather than from an inference.
`DEVICE_DIRTY` says a dirty build cannot be shown current — true of a version
string, false of a digest, because we compare against the exact artifact we
recorded rather than reproducing a build. Neither reason changes meaning; both
gain a way to be discharged.

`_cmake_target` returns a real `DeviceStatus` and reports a version on its
device rows, following `_screen_confidence` and `_screen_device_status` as the
precedent — layering a signal on top, not folding it in.

**Acceptance:** gate green. Tests assert: a mismatch beats a matching version;
absence falls through; a match resolves `VERSION_ONLY` and `DEVICE_DIRTY`; and
`needs_flash` is never False on absent evidence. Mutation-checked.

## Task 6: `flash_all` means all

`_boards_to_flash` enumerates CMake boards; `_require_flashable_type` stops
refusing; `_flash_actions` goes on the CMake type row; the CLI's `-t`-only
refusal is replaced by the same path.

`type_not_bulk_flashable` retires here. It is emitted at `bulk.py:491` and
asserted in two tests and — contrary to an earlier claim in the spec, since
corrected — was never documented in `docs/agent-api.md`. Retire the code and
its tests rather than documenting it on the way out.

**Acceptance:** gate green. A CMake type flashes from `flash_all` and from
`-t`. No path reports a successful flash of zero boards.

## Task 7: The other two readers, and the docs

knomi_serial's reader wraps `pio.running_sha` rather than reimplementing it,
and `_screen_confidence` asks the seam instead of calling `pio_mod` directly.
Cartographer's returns None, permanently, carrying `decisions.md`'s reasoning
in its docstring so nobody reads it as unfinished work.

Behaviour-neutral by construction. It is what keeps Task 3 from being a private
arrangement between one helper and one caller.

Then the docs the provider-selection plan's Task 4 never delivered: `AGENTS.md`
records `providers/selection.py` as a seam and the helper seam as a protocol
seam with a version capability alongside; README `## Features` gains the
entries; `docs/agent-api.md` gains the error codes it is missing.

**Acceptance:** gate green. `_screen_confidence` makes no direct `pio_mod`
call. A full-branch review against the spec.

## Out of scope

- **BOOTSEL sequencing for a sweep.** Several CMake boards rebooting into ROM
  bootloaders has physical sequencing an esptool write does not, and a
  partially-swept fleet is a state the batch has to be able to describe. Needs
  its own spec. It does not need CMake carved back out, and must not be allowed
  to become that argument a second time.
- **Config strictness** — declaring every firmware, retiring "undefined equals
  klipper". Its own README TODO, with three blocking questions named there.
- **The helper-seam migration** — moving `discovery/roadrunner.py`,
  `discovery/knomi_serial/` and Cartographer's special-cases behind the seam.
  Its own README TODO.
