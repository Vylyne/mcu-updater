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

## Two sources for the running version, and the order is forced

The INFO reply is not the only place a Roadrunner's version can be read, and it
is not the one to read first.

The Roadrunner's Klipper extra populates a `high_resolution_filament_sensor`
printer object with its version information. That is a third path, and this
spec had missed it: `grep -rn high_resolution src/ docs/` returns nothing, so
nothing in this repo knows the object exists.

**Klipper first, admin protocol second.** Not a preference - a lock. When
Klipper is connected to the Roadrunner over usbserial it holds that connection,
and the admin protocol cannot open a port Klippy owns. A provenance read that
goes to the wire first is therefore a read that fails, noisily, on exactly the
machines where the answer was already sitting in the object graph. The fallback
is for the cases where Klippy is not up, is not configured for that board, or
reached it over some other transport.

This costs no new transport and, done right, no new round trip.
`status.py::_probe("printer.objects.query", ...)` is already the mechanism at
four sites, and `discovery/spec.py` already names `printer.objects.query` as
one of its three inputs. Better: `_all_object_names()` is cached with a TTL and
`_object_names_for(prefix)` already exists to pick objects by prefix - the
comment on the cache says outright that *"one list serves every prefix, so
adding displays costs no extra round trip on top of the MCU lookup that was
already happening."* A `high_resolution_filament_sensor` prefix is the same
deal.

**What still has to be confirmed before this can be planned:** the object's
exact name and the exact field carrying the version, read off the extra rather
than assumed, and whether that string is the same `${git_describe}` value the
INFO reply carries. If the two sources can disagree in format, the helper's
reader has to accept both, and the spec's `running_sha` contract is unchanged
either way - it is a string parser, and it does not care which side of the
machine handed it the string.

## A firmware digest closes the two cases a version string cannot

Offered by the operator, with both options costed from the firmware side:

* **CRC32 via the RP2040's DMA sniffer** - hardware, essentially free, no
  blocking.
* **SHA-256 in software** - the RP2040 has no SHA block (the RP2350 does), so
  roughly 100 cycles/byte on the M0+, call it 150-200 ms for a typical image.
  Viable because it need not run in the sensor loop: compute once before the
  transport comes up and serve the cached digest from a register.

**Take CRC32.** Our own stated purpose picks it. This is anti-corruption
evidence - a truncated write, a bad sector, a half-erased block - and CRC32 is
built for exactly that class, catching every burst error up to 32 bits.
Collision resistance is the only thing SHA-256 adds here, and it is the one
property this check explicitly does not need.

The boot-time framing above does dispose of the cost objection - computing
before the transport comes up is clean, and the sensor loop is the place that
would have been a problem. So this is not "SHA is too expensive". It is that
free-and-sufficient beats cheap-and-more-than-required, and the sniffer makes
CRC32 free.

**Not a security check, and the spec must say so where the code can see it.** A
CRC32 is trivially forgeable and not collision-resistant. The docstring has to
say that plainly, or someone downstream will eventually treat a match as
attestation. It is evidence about accident, never about intent.

### Carry a labeled digest, not a bare number

The one thing worth designing now because it is cheap now and expensive later:
the INFO field should carry **algorithm and value**, not a bare `crc:`. The
RP2350 has the SHA block, and a future board - or a use that genuinely needs
content identity rather than integrity - then becomes a firmware change on one
side instead of a protocol break on both. A bare number forecloses that for no
saving at all.

### What it buys

It lands exactly where `running_sha` gives up. A version string answers *"is
this the firmware I built"* and returns None - "cannot tell" - in two cases
this spec already commits to:

- a build stamped `dev` or a bare tag, where there is no commit in the string;
- a `-dirty` build, which can never be *shown* to match, because a build from a
  dirty tree is not reproducible.

A digest answers a different question - *"are the bytes on the board the bytes
I wrote"* - and can answer it in both, turning two permanent "cannot tell"
verdicts into real ones. It joins the existing model cleanly: `FlashLog`
already records what was written, so recording the digest of the staged payload
beside it gives `entry_for` something to compare.

### The byte range: settled upstream, and not ours to choose

This was left as the open deliverable. It is now answered, in
`roadrunner/docs/roadrunner-usb-admin-protocol.md`, and the answer is better
than the one this spec was reaching for.

**The board reports the range.** INFO carries `image start` and `image length`
as little-endian 32-bit fields - on current firmware `__flash_binary_start`
through `__flash_binary_end` - and the document is explicit that *"a host must
use the reported start and length and must not substitute its own."* The linked
image does not end on a 256-byte boundary, so only the reported length says
where it stops. That removes the failure this spec was most worried about: the
two sides cannot silently disagree about the range, because only one side
decides it.

It also settles a question this spec never thought to ask. The reserved
identity sector at `0x1FF000` is outside the digested range, because the
application region is capped exactly where the sector begins. So provisioning,
clearing and re-provisioning never move the number, and two boards flashed from
the same UF2 report the same digest whatever their identity. A digest that
changed when a board was provisioned would have been useless to us.

**Algorithm `1` is CRC-32/ISO-HDLC** - reflected polynomial `0xEDB88320`, init
and final XOR `0xFFFFFFFF`, reflected in and out, which is what `zlib.crc32`
computes. Carried on the wire rather than assumed, which is the labeled-field
design this spec asked for, arrived at independently. Their document makes the
reason sharper than ours did: *"CRC32 on its own names at least half a dozen
mutually incompatible functions and is not a specification."* Algorithm `0`
means a current board that could not compute one - distinct from a board built
before the fields existed, which simply ends its payload at the flash UID.

Their document also states the non-attestation limit in its own terms, and more
precisely than this spec did: the firmware computing the digest is the firmware
in question, so anything able to replace the image can replace the hasher.
Evidence against accident, never against substitution.

### Reconstructing the range from a UF2 - and a correction

An earlier revision of this section said a `.uf2` carries "512-byte blocks each
carrying 256 bytes of payload plus headers". **That is wrong**, and it is the
exact error their document warns hosts against: a block carries a 32-byte
header and *up to 476* bytes of payload, and `payloadSize` must be read from
each block's header. 256 is a convention of the tooling, not a rule of the
format - and our own fixture would have passed while real firmware failed.

The host-side rules, which we implement rather than invent:

1. Verify the three magic values; reject the container otherwise.
2. Skip a block whose flag bit `0` (`NOT_MAIN_FLASH`) is set.
3. Read `payloadSize` from the header rather than assuming it.
4. Place payload byte `i` at `targetAddr + i`, discarding bytes outside
   `[start, start + length)`.

Then require every byte of the range to have been supplied, and digest exactly
`length` bytes. **A gap is an error, not a hole to fill with `0xFF`** - filling
produces a plausible wrong number instead of a visible failure, which is the
worse of the two outcomes by a wide margin.

`roadrunner/scripts/uf2_image_digest.py` is the reference implementation and is
small enough to reimplement. Both sides test against a golden vector rather
than against each other: a 600-byte image at `0x10000000` where byte `i` is
`(i * 7 + 3) & 0xff`, CRC-32/ISO-HDLC `0xBBE38AA9`, packed as three blocks
whose last carries 88 image bytes and 168 bytes of padding. A host that digests
the container, or that rounds the length up to 768, gets a different number.
Our port must use that vector, for the reason their document gives: testing two
implementations against each other proves only that they are wrong together.

### The INFO reader was already broken by this, and is fixed

Their document calls out a host obligation: parse `payload_length` rather than
sizing a fixed buffer, because the response limit went from 96 to 128 bytes
when the digest fields landed.

Our framing was already correct - `read_response` reads `header[4]` bytes and
hard-codes no size - so the truncation they warn about could not happen here.
`parse_info` failed differently and worse. It asserted the payload ended
exactly at the flash UID, so a revised board raised `ProtocolError` for the
*entire* reply; `_valid_info` never ran, and the board disappeared from
discovery rather than merely losing one field.

Fixed on this branch, with the digest fields parsed and unknown trailing fields
ignored. The frame's CRC-8 already covers corruption, so an exact-length
assertion catches nothing a bad wire produces while breaking every board built
against the next revision - which is exactly what it just did. Nothing consumes
the digest yet; that is this spec's plan to sequence.

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

### What the helper seam actually is today

`helpers/spec.py` names itself *"the narrowly scoped contract for
firmware-specific BOOTSEL requests"*, but that undersells what it is. Read the
two methods: `request_bootsel` and `wait_ready` both take a `Bench`, a serial
and a chipset, and what they do is **talk to a device in that firmware's own
protocol** - one sends a command, the other polls for a reply. BOOTSEL is the
message, not the subject. `scripts/roadrunner_usb.py` is the vocabulary.

That is the honest reading, and it explains what already lives on the wrong
side of the line: `discovery/roadrunner.py::_valid_info` parses an INFO reply
from the same protocol, in a different package, with no helper involved. The
migration TODO in the README lists it for that reason. This spec does not move
it - but the seam it widens is a communication seam, and naming it correctly
now is what keeps the next thing from being squeezed into the wrong shape.

### Version-reading is not that shape, so it is a separate capability

A `running_sha` takes a string and returns a string. No bench, no serial, no
port, no device - the board is long gone by the time it is called, and what it
reads came out of the ledger or out of Klippy, not off the wire. Hanging it on
`BootselRequester` would put a pure function inside a protocol contract and
force every helper to implement a method it has no answer for, which is how a
narrow seam becomes a god object.

It arrives alongside, not on top. A firmware can need either without the other,
and both cases are real: a knomi screen is never put into BOOTSEL and still
reports a version; a future board could take a helper-driven BOOTSEL write and
report nothing back.

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

## What the digest decides

Settled: the staged payload's digest is stored beside what `record_build`
already keeps, and a mismatch means the board needs flashing. The three things
a mismatch can mean - the wrong firmware, an incomplete write, a corrupted
image - are all repaired by writing the firmware again, so they do not need
telling apart to decide the action.

### A mismatch outranks a version match

This is the part that is easy to get backwards. A digest is not a second
opinion alongside the version comparison; where both have an answer, **the
digest wins**, including when it contradicts a version that matches.

A version string is a label the image carries. A digest is the image. A board
whose `fw_version` is exactly what we flashed, but whose digest differs, is
running something other than what we wrote - a truncated BOOTSEL copy that
boots and behaves is the case their document names, and the version string
survives that write perfectly well because it sits early in the image. Version
alone reports "up to date" there. That is the reading this ordering exists to
prevent.

So: digest decides when both sides have one; the version comparison decides
when they do not.

### Absence is not mismatch

The decisive rule above applies to a digest that disagrees, never to a digest
that is missing, and there are three ways for it to be missing:

* a board built before the fields existed, whose payload ends at the flash UID;
* a current board reporting algorithm `0`, which could not compute one;
* our own ledger having no stored digest, because the board was flashed before
  we recorded them.

None of these is evidence of anything, and all three must fall through to the
version comparison. Treating absence as mismatch would mark every pre-revision
board as needing flash permanently - and reflashing would not clear it, because
old firmware still reports no digest. That is a flash loop that never
converges, offered to the operator as a standing red flag. The distinction the
protocol draws between "no fields" and "algorithm 0" exists precisely so a host
can tell these apart, and it costs nothing to honour.

### It retires the dirty-build limitation, for boards that report one

This spec says elsewhere that a `-dirty` build can never be *shown* to match,
because a build from a dirty tree is not reproducible. That is true of
inference from a version string, and it is not true of a digest.

We are not reproducing the build. We are comparing the board against the exact
artifact we flashed and recorded. A digest match proves the board is running
those bytes whether or not the tree was clean when they were produced. So a
dirty build with a matching digest is up to date, and saying otherwise would be
as much a lie as the one the original rule was written to avoid.

The same applies to the `dev` and bare-tag cases. All three "cannot tell"
verdicts become real ones on a board that reports a digest, which was the whole
argument for taking the offer.

### It needs one new reason, and otherwise resolves existing ones

`states.py` already has the vocabulary, and the digest fits inside it rather
than beside it. `DeviceStatus` carries a reason, derives `needs_flash` and
`tone` from it, and its module docstring states the rule this has to respect:
*"the reason is the fact; everything else is a view of it"* - so an
inconsistent pair cannot be constructed at all.

**One reason is missing.** A digest mismatch is not `ARTIFACT_CHANGED`, which
is the closest existing code and the tempting one to reuse. `ARTIFACT_CHANGED`
means "same commit, different binary" and labels as *"Newer build available"* -
it says we have something better on disk. A digest mismatch says something
else: the board is not running what we wrote. Wrong firmware, truncated write,
corrupted image. Reusing `ARTIFACT_CHANGED` would tell an operator a newer
build is available when what happened is that their board came back from a
flash carrying bytes nobody recognises, and that is exactly the mis-wording
this module exists to prevent.

So: a new device reason, `needs_flash: True`, tone `TONE_ATTENTION` by
derivation, labelled as an unexpected image rather than an available update.
It sits next to `PROTOCOL_MISMATCH`, which has the same shape - positive
evidence about the device rather than about our build.

**Nothing new is needed for absence.** A missing digest changes no code path:
the comparison falls through to the version reasons that already exist, and
`UNKNOWN_VERSION`, `VERSION_ONLY` and `OFFLINE` keep meaning what they mean.
This is why absence-is-not-mismatch is cheap to honour - it is the behaviour
already there.

**And a match resolves two ambers to green**, which is where most of the value
lands. `VERSION_ONLY`'s own docstring describes the mechanism: it *"resolves to
None (up to date) on the first flash through this tool, once our own record
backs the match."* A digest match is that corroboration, arriving from the
device rather than from an inference. `DEVICE_DIRTY` is the same story - its
docstring says a dirty build *"cannot be shown current"*, which is true of a
version string and false of a digest, because we are comparing against the
exact artifact we recorded rather than reproducing a build.

Both stay amber when no digest is available. The reasons do not change meaning;
they acquire a way to be discharged.

## What changes, in dependency order

1. The running version becomes reachable: the
   `high_resolution_filament_sensor` object is read through the existing
   cached-prefix lookup, and `fw_version` stops being discarded -
   `RoadrunnerDevice` carries it as the fallback for when Klippy cannot
   answer.
2. `flashers/helper_bootsel.py` writes `FlashLog`, on the same rule
   `agent/methods/flash.py::_cmake_flash` states: whenever a copy completed,
   before any failure is raised.
3. The `VersionReader` capability lands in `helpers/spec.py` and
   `helpers/registry.py`, with the roadrunner reader implementing it - the
   `-dirty` and unknown-version cases included.
4. The UF2 image digest lands host-side - a port of
   `uf2_image_digest.py`, tested against the golden vector - and
   `cmake.record_build` stores the staged payload's digest beside the hash it
   already keeps. Independent of steps 1-3: it touches only the build and the
   ledger, and until step 5 reads it, storing it changes nothing.
5. The comparison applies the precedence: digest decides where both sides have
   one, absence falls through to version, a mismatch is `needs_flash` with its
   own reason. `_cmake_target` returns a real `DeviceStatus` and reports a
   version on its device rows.
6. `_boards_to_flash` enumerates CMake boards; `_require_flashable_type` stops
   refusing; `_flash_actions` goes on the CMake type row; the CLI's
   `-t`-only refusal is replaced by the same path.
7. The knomi_serial and cartographer readers land, and `_screen_confidence`
   asks the seam rather than `pio_mod.running_sha` directly.

Steps 1-5 are what make step 6 honest. Step 6 is mechanical once they land.

Step 4 is worth starting early despite its position: it is the only step
needing a new algorithm rather than a new wire-up, it has a golden vector to
test against before any board exists, and a board flashed before it lands has
no stored digest to compare - so every day it is not recording is a device that
falls through to the version comparison until its next flash.

Step 7 changes no behaviour and is what keeps step 3 from being a private
arrangement between one helper and one caller; it is sequenced last only
because it must not block the gap being closed.

## What this spec does not settle

**BOOTSEL sequencing.** A CMake write reboots the board into its ROM
bootloader and stops services to do it. A sweep over several CMake boards has
physical sequencing that an esptool or flashtool write does not, and a
partially-swept fleet is a state the batch has to be able to describe. This
needs specifying - it does not need CMake carved back out, and it must not be
allowed to become that argument a second time.

**The `type_not_bulk_flashable` code.** An earlier draft of this section said
it "is documented as stable in `docs/agent-api.md`". It is not documented
there at all - `bulk.py:491` emits it, two tests assert it, and the API
document has never mentioned it. That was the provider-selection plan's Task 4,
which has not run.

Documenting it now would be documenting something this spec retires in step 6.
Either is defensible; what is not defensible is a client receiving a code that
appears in no contract. If step 6 is close, skip it and retire the code; if it
is not, document it with its retirement stated in the same breath. Its
retirement is part of step 6 either way, not a separate deprecation.

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
