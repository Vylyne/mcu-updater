# Typed artifacts: flashers choose files by kind

Status: approved in conversation 2026-09-24, pending written-spec review.

## Problem

A flasher learns which file to write from `Device.detail`, and the caller
builds that payload: the kconfig board dict for flashtool, `{"uf2_file"}` for
bootsel, `{"fw_bin"}` for dfu_util. Each caller knows which flashers its family
lists and fills in exactly what those flashers read. Nothing states which files
a build produced, or which of them a flasher can take.

Three things follow from that:

- **A valid-looking config crashes.** A CMake family with `flashers:
  flashtool` raises `KeyError` inside `Flashtool.target`: the caller built a
  bootsel-shaped `detail`, and flashtool reads a board dict. This is the open
  README BUG, and its root cause is that `detail` is shaped for a flasher,
  not for an artifact.
- **Files the build already staged are never used.** kconfig-make stages
  `<fw>.uf2` beside `<fw>.bin` whenever `make` makes one
  (`build.py`, staging), so Klipper on RP2040 already has a `.uf2` on disk.
  No flash path can reach it.
- **One hash describes the whole build.** The kconfig sidecar's `bin_sha256`
  hashes the `.bin`. The CMake sidecar's `bin_sha256` hashes the `.uf2`. A
  staged kconfig `.uf2` has no recorded hash. If it were ever written,
  `FlashLog` and the foreign-build check would have nothing to compare
  against.

## Goal

1. A builder reports every file it staged, each with a kind and a hash.
2. A flasher declares the kinds it accepts. Selection gives it the matching
   file, and a missing kind becomes a named refusal.
3. Klipper on RP2040 can be written through BOOTSEL. This is for users who
   do not want Katapult, or who cannot make it work on a board.
4. CMake captures every flashable file it produces, not just the `.uf2`.

Success means:

- The CMake+flashtool `KeyError` is gone, and that config either flashes or
  is refused with a message.
- `[firmware klipper] flashers: bootsel` flashes a running USB RP2040 with
  the staged `.uf2`.
- Every `FlashLog` entry carries the hash of the file actually written.

## Decisions

These were agreed in conversation.

- **Kinds are named for what a flasher consumes, never for a builder.** The
  one exception is `pio_env`, and it is marked as one.
- **The chosen flasher brings its artifact.** A family does not pick a file.
  The family's `flashers:` list is the preference order: it picks the flasher,
  and the flasher's kind picks the file. This replaces a `(pattern, flashers)`
  pairing in config. That pairing would put build-tree internals into user
  config, and it would restate what each flasher already knows about its
  input. Revisit it only if one build ever stages two files of the same kind
  for different jobs.
- **Placement (link address) is not modelled.** See [Placement](#placement).
- **`.elf`, `.hex` and `.dis` are not captured.** No flasher consumes them. A
  kind is added when a flasher needs it.
- **`FlashRecord.bin_sha256` keeps its name.** It now means "the hash of the
  artifact this flasher wrote". The name is on disk in `FlashLog`, and
  renaming it would be a ledger migration for a word.
- **Klipper through BOOTSEL is opt-in, by list order.** There is no new
  config key.
- **Bootsel gains no CAN rule of its own.** Whether a CAN device can reach
  BOOTSEL is up to the family's helper. The `klipper` helper cannot, and says
  so.

## Design

### The artifact model

The new module `src/mcu_updater/artifacts.py` holds the model. It is separate
so that `flashers.spec` and `providers` can both import it without a cycle.

```python
KIND_BIN = "bin"          # a raw image
KIND_UF2 = "uf2"          # a UF2 container
KIND_PIO_ENV = "pio_env"  # a built PlatformIO env; see below

@dataclasses.dataclass(frozen=True)
class Artifact:
    kind: str
    path: Path
    sha256: str | None
```

What each builder reports, one `Artifact` per staged file:

| Builder | Artifacts |
| --- | --- |
| kconfig-make | `bin` always. Also `uf2` when `make` produced one: Klipper on RP2040, Katapult on RP2040. |
| cmake | `uf2` and `bin`. Today it captures only the `uf2`. |
| platformio | `pio_env`. Its `sha256` is the hash of `firmware.bin`, as the sidecar records today. |

**`pio_env` is the one builder-specific kind, on purpose.** The PlatformIO
flasher runs `pio run -t upload`, which uploads from PlatformIO's own build
directory rather than taking a file. No other builder can produce what it
consumes. The kind says so honestly, instead of pretending the flasher takes
a `bin`.

**Sidecar.** Build sidecars record a hash for each kind:

```json
"artifacts": {"bin": {"sha256": "..."}, "uf2": {"sha256": "..."}}
```

Sidecars already on printers carry only `bin_sha256`. Readers fall back to it
for the builder's *primary* artifact: `bin` for kconfig, `uf2` for cmake,
`pio_env` for platformio. Any other kind in such a sidecar reads as
`NO_PROVENANCE`, which accuses nobody. The foreign-build check from 5abce73
keeps its rule: `FOREIGN_BUILD` needs a recorded hash that disagrees, and a
missing hash is never a disagreement.

### Flashers and selection

The `Flasher` protocol gains `accepts: tuple[str, ...]`:

| Flasher | `accepts` |
| --- | --- |
| flashtool | `bin` |
| dfu_util | `bin` |
| bootsel | `uf2` |
| esptool (renamed `platformio` in the follow-on) | `pio_env` |

**Selection reads what is staged. Callers do not pass it in.**

- `registry.select_device` already resolves the family. It now also asks that
  family's builder what is staged for the device's type:
  `providers.staged(paths, type_name, family) -> tuple[Artifact, ...]`. It
  reads the staged files and the sidecar.
- This lives inside selection for the same reason `stop_services` is a
  required argument: no caller can forget it.

**`resolve` walks the family's `flashers:` list in order.** It takes the first
flasher that both `supports(device, helper)` and has a staged artifact of a
kind it `accepts`. For example, with `[firmware klipper] flashers: flashtool,
bootsel`:

- A board running Klipper goes to flashtool, with the `bin`.
- A board in BOOTSEL is not a state flashtool handles, so it goes to bootsel,
  with the `uf2`.

**`target()` takes the chosen artifact.** Its signature becomes
`target(paths, device, helper, artifact, *, stop_services)`.

- `FlashTarget` carries the artifact.
- `write` and `record` read the path and hash from the target, so `record` no
  longer re-reads its builder's sidecar to find a hash.
- `Device.detail` loses every file path (`uf2_file`, `fw_bin`) and keeps only
  addressing facts, such as the board dict flashtool needs for serial and CAN.
- Callers stop building payloads that describe files.

**A missing kind is a refusal that names the kind.** When some flasher in the
list supports the device but no artifact of its kind was staged, the result is
`NoFlasherError`:

- The message names the flasher and the kind. For example: "bootsel could
  write t0 while it is in BOOTSEL, but [firmware roadrunner] staged no uf2."
- The error data gains `missing: [kind, ...]`.

It is the same error type as today, so `select_each` reports it as the batch
refusal it already understands.

### Klipper through BOOTSEL

**Opting in.** The default for a kconfig family stays `flashers: flashtool`.
A user chooses BOOTSEL through the list:

- `flashers: bootsel` means BOOTSEL only, for a board without Katapult.
- `flashers: flashtool, bootsel` means Katapult first, with BOOTSEL as the
  fallback for a board already sitting in BOOTSEL.

**The `klipper` helper.** A board running Klipper needs a way into BOOTSEL.
Bootsel supports a device that is already in BOOTSEL, or one whose family
helper implements `BootselRequester`. Getting a running Klipper board into
BOOTSEL is firmware-specific behaviour, so it is a helper
(`src/mcu_updater/helpers/klipper.py`, set with `helper: klipper`). Bootsel
itself gains no branch.

The helper relies on this behaviour, verified against Klipper master
(`src/rp2040/main.c` last changed 2025-06-02):

- `bootloader_request()` calls `try_request_canboot()` and then
  `bootrom_reboot_usb_bootloader()`.
- `try_request_canboot` (`src/generic/armcm_reset.c`) returns without doing
  anything in two cases: the build has no bootloader offset, or there is no
  Katapult signature at the start of flash.
- On USB, the trigger is the Arduino-style 1200-baud touch
  (`src/generic/usb_cdc.c:456`), gated on `CONFIG_HAVE_BOOTLOADER_REQUEST`.

So on a board without Katapult, the request lands in BOOTSEL. On a board
*with* Katapult, the same request lands in Katapult.

- **`request_bootsel`** invokes flashtool's `-r` on the board's by-id port.
  That is the same request, and it is already a requirement, so we do not
  write our own 1200-baud touch. It then waits for a BOOTSEL volume.
  - If the board comes back in Katapult instead, the helper raises a named
    error ("it has Katapult; list flashtool first"). Nothing is written.
- **`wait_ready`** waits for the by-id node of the serial the *new* image
  will present. It does not assume the old serial comes back.
  - The staged build's `.config` gives that serial. With
    `CONFIG_USB_SERIAL_NUMBER_CHIPID=y` it is the chip ID, which stays the
    same across the flash. Otherwise it is the string in
    `CONFIG_USB_SERIAL_NUMBER`.
  - If the predicted serial differs from the one the board was addressed by,
    the helper waits for the new one and says the serial changed. Otherwise a
    changed serial would look like a board that never came back.
- **Klipper has to be stopped.** The request goes over the port Klipper holds.
  Bootsel's per-target `needs_services_stopped` already returns `True` for a
  running board.
- **Scope is RP2040.** RP2350 goes through the same `main.c`, but its boot
  ROM's UF2 handling (partition tables) is unverified. It is enabled after a
  bench test, not before.
- **picotool** is a possible later alternative for entering BOOTSEL. It is
  not a requirement, because Klipper already provides the route.

**CAN devices.** Bootsel's `supports()` is unchanged: it accepts a device
that is already in BOOTSEL, or one whose family helper implements
`BootselRequester`. It adds no CAN-specific refusal. The user's config and the
helper are trusted to know whether the route exists. A helper that could find
the USB side of a CAN device (from its device info, for example) is free to
offer the route.

The `klipper` helper cannot. A BOOTSEL board is a USB volume, and a CAN
address gives no port to find it on. flashtool's `-r` does work over CAN, but
a board that enters BOOTSEL that way cannot be reached afterwards. So the
helper's `request_bootsel` raises a named `FlashError` ("no BOOTSEL path from
a CAN device") before it sends anything. A USB-to-CAN bridge board is not
affected, because the bridge itself has a by-id serial.

### CMake

CMake stages its `bin` beside its `uf2`, and records both in the sidecar.
Pairing a CMake family with flashtool is now valid: flashtool gets the `bin`.
An image linked without the Katapult offset will not run, but it cannot brick
anything (see [Placement](#placement)), and the readiness check after the
batch reports it.

### Placement

Placement means where an image is linked to start. It is deliberately **not**
part of `Artifact`, because no combination of image and route here can brick
a board:

- **Every UF2 block carries its own target address.** The RP2040 boot ROM
  writes only the blocks the file contains, and erases only the 4 KB sectors
  they land in. A Klipper `uf2` built with the 16 KiB Katapult offset starts
  at `0x10004000`, so Katapult's four sectors are never touched.

| Case | Outcome |
| --- | --- |
| Offset `uf2` onto a board with Katapult | Works. Katapult is kept. |
| Offset `uf2` onto a bare board | No valid boot stage at the start of flash, so the ROM falls back to BOOTSEL and the board comes back as `RPI-RP2`. Recoverable. |
| No-offset `uf2` onto a board with Katapult | Overwrites Katapult and boots Klipper directly. This is the "no Katapult" route. |
| No-offset `bin` through Katapult | Katapult never overwrites itself. The image lands at the offset and does not run, and the board stays in Katapult, where it can be flashed again. |

The worst outcome is a board that does not come back running the new
firmware, and the readiness check already reports that. One case gets a
warning rather than a refusal: an *offset* `uf2` written through BOOTSEL. That
is legitimate on a board that has Katapult, and a BOOTSEL board cannot say
whether it does.

## Error handling

| Situation | Result |
| --- | --- |
| A flasher supports the device but its kind was not staged | `NoFlasherError`, naming the kind, with `missing` in its data. The batch reports it as a refusal. |
| Nothing in the list supports the device | `NoFlasherError`, as today. |
| The `klipper` helper is asked for BOOTSEL on a CAN device | Named `FlashError` ("no BOOTSEL path from a CAN device"), raised before anything is sent. |
| The Klipper request lands in Katapult | Named `FlashError` from `request_bootsel`. Nothing is written. |
| An offset `uf2` goes through BOOTSEL | Warning. The write proceeds. |
| The new image presents a different serial | `wait_ready` waits for the predicted serial and reports the change. |

## Documentation

- **README:** the flashers section; a Klipper-through-BOOTSEL example; remove
  the CMake+flashtool `KeyError` BUG from `## TODO`.
- **`docs/agent-api.md`:** the refusal's `missing` field.
- **`docs/decisions.md`:** the "do not infer builder/flasher compatibility
  from the staged filename" entry gets its resolution. The builder now
  supplies the machine-readable evidence that entry asked for.
- **`docs/cmake-provider.md`:** the staged `bin`, and the flashtool pairing.

## Implementation order

All of this goes on one branch, `feat/typed-artifacts`, in its own worktree.

1. **`artifacts.py`, and builders report artifacts.** Sidecar hashes are
   recorded per kind, and old sidecars fall back to `bin_sha256`.
2. **Selection by kind.** This covers:
   - `Flasher.accepts`
   - `providers.staged`
   - `target(..., artifact)`
   - file paths removed from `detail`
   - callers no longer building payloads that describe files
   - the `missing` refusal
3. **CMake stages its `bin`.**
4. **The `klipper` helper.** This includes its refusal for CAN devices.
5. **Docs and the README TODO.**

## Testing

- **Sidecars:**
  - An old sidecar for each builder reads correctly.
  - A non-primary kind in an old sidecar reads as `NO_PROVENANCE`.
  - A recorded hash that disagrees still reads as `FOREIGN_BUILD`.
- **Selection:**
  - A missing kind falls through to the next listed flasher.
  - A refusal names the missing kind.
  - A Klipper board in BOOTSEL resolves to bootsel with the `uf2`.
  - A CMake family with flashtool no longer raises `KeyError` (regression
    test).
- **Ledger:** `FlashRecord.bin_sha256` is the hash of the file actually
  written, including the `uf2` hash for Klipper through BOOTSEL.
- **The `klipper` helper:**
  - A request that lands in Katapult raises the named error and writes
    nothing.
  - Serial prediction works with both a chip-ID serial and a hardcoded one.
  - A CAN device raises the no-BOOTSEL-path error, and nothing is sent.
  - The offset-`uf2` warning.
  - Tty-level tests are `posix_only`. CI's Linux run is the one that counts.

### Mutation guards

A new `scripts/mutations/artifact-selection.json` guards:

- the kind check in `resolve`
- the missing-kind refusal
- the Katapult-landing refusal
- the `klipper` helper's CAN refusal

Before each commit, grep `scripts/mutations/` for every source line the commit
rewrites, and re-anchor any matching spec in the same commit. The bootsel and
flashtool `target()` lines are the likely hits. Run specs one at a time.

### Bench verification before merge

On the bench board only, never the toolhead:

- Klipper through BOOTSEL on a bare RP2040.
- Klipper through BOOTSEL on an RP2040 that has Katapult, to confirm Katapult
  survives.

## Out of scope: the PlatformIO flasher follow-on

This is a separate spec, plan and branch. It builds on `KIND_PIO_ENV`.
Decisions already agreed for it:

- **Rename the `esptool` flasher to `platformio`.** It runs `pio run -t
  upload` for any PlatformIO env and does not implement esptool. The name
  `esptool` stays free for a real ESP32 image flasher; nothing needs one yet.
  - No alias and no config migration are needed, because the `flashers:` key
    has not reached `main`.
  - The wire value `"flasher": "esptool"` in `docs/agent-api.md` changes with
    the rename. Nothing in `ui/src` reads it.
- **Drop the screen vocabulary.** `KIND_SCREEN` becomes a neutral kind for a
  device reached at a configured port, and `detail` carries the env and the
  port, not `display` and `screen`. That vocabulary is left over from
  knomi_serial being the first PlatformIO project.
- **Rediscovery goes through the helper seam.** `prepared()` asks the family's
  helper through the existing `Identifier` capability (`identify(...,
  ask=True)`), instead of running `discovery.confirm()` inside the flasher. A
  family with no identifier writes to its configured port.
