# One pipeline: devices, types, firmwares, builders and flashers

Supersedes `2026-09-08-bootsel-helper-design.md` and
`2026-09-10-provider-selection-seam-design.md`, and the sequencing and
"does not settle" sections of `2026-09-11-cmake-provenance-design.md`. That
spec's rules about the digest and the verdict still stand. This spec cites them
and does not restate them.

## The principle

> All firmwares are firmwares, all types are types, all flashers are flashers.

The app works from one list of devices. Each device maps to a type, each type
to a firmware, and each firmware to a builder and an ordered list of flashers.
"Build x and y" loops over the list and calls each firmware's builder. Flashing
is one loop for every firmware: flash what can be flashed, log each result,
and report at the end.

When a firmware needs different behaviour, a handler behind a seam provides it
and the loop carries on. The caller never branches. Code like
`if provider == ...` in a CLI command, or a separate path for a sweep over
CMake boards, is the thing this spec removes.

## Why the code is not shaped like that today

The codebase grew one provider at a time: a shell script, then PlatformIO
displays, then CMake. Each addition left its own copy of the pipeline behind:

- **Three type loaders.** `Registry.load`, `pio.load` and `cmake.load` each
  read the same `[type]` sections and keep only the builder they know. The CLI
  uses the first one, so it never sees a Roadrunner type. That is the open
  "tracked in the UI but not the CLI" bug. `Registry.load`'s
  `builders != {"kconfig_make"}` filter (`config.py:418`) is where the type
  disappears.
- **Three status paths.** `_device_status`, `pio.device_status`, and the
  `UNKNOWN_VERSION` stub in `_cmake_target`.
- **Flasher selection by accident.** `select_for` returns the first match in
  `FLASHERS`'s order. The CMake flash paths bypass it by calling
  `helper_bootsel.target_for` directly (`agent/methods/flash.py:250`,
  `cli.py:598`).
- **Bulk refusals that exist because of the split.** The CLI accepts a CMake
  flash only with `-t`. `bulk.py` refuses with `type_not_bulk_flashable`.
  `update-all` exits early.
- **Invented firmware families.** `firmware.resolve()` returns a conventional
  `FirmwareFamily` for any name, `BUILTIN = FW_TARGETS` makes klipper and
  katapult impossible to remove, and `paths.flashtool` hardcodes
  `~/katapult/scripts/flashtool.py`.

## 1. Every firmware is declared

`[firmware klipper]` and `[firmware katapult]` are required, the same as any
other family. `resolve()` stops inventing families. An undeclared name is a
config error that names the missing section.

The readers that bypass `resolve()` switch to the declared families:

| Reader | Today | After |
| --- | --- | --- |
| `firmware.BUILTIN` | `FW_TARGETS` | removed |
| `config.fw_order` (`config.py:243-251`) | `FW_TARGETS` first | declaration order |
| `profiles.py:43`, `:132` | `FW_TARGETS` | declared families |
| `status.py:260` `builtin` flag | `name in BUILTIN` | removed, with `docs/agent-api.md` updated in the same commit |
| `cli.py:1003` `--fw` choices | `FW_TARGETS` | declared family names |
| `Paths.fw_dir` | `~/<fw>` convention | only a family's `source:` |

**install.sh seeds both sections**, writing the source paths it actually finds
rather than leaving them to defaults:

- Klipper: `~/klipper` if it exists, otherwise the path the user enters.
- Katapult: `~/katapult` if it exists. If it doesn't, install.sh offers a
  single-branch clone (`git clone --single-branch`), and the user can instead
  enter a path to an existing checkout or fork.

install.sh writes no config today, so seeding and strictness can ship in one
commit. An existing install gets the sections by re-running install.sh. Until
config migrations exist (see [Deferred](#deferred)), the refusal message for a
missing section lists the exact lines to add.

**The flashtool path comes from the katapult family.** Resolution order:

1. `[updater] flashtool_path`, if set. This setting already exists
   (`settings.py:81`, `flashers/flash.py:80-82`, README Requirements).
2. `<katapult family source>/scripts/flashtool.py`.

No new key is added. The only change is that the fallback reads the declared
katapult source instead of the hardcoded `Paths.flashtool`.

## 2. One type list

One loader reads every `[type <name>]` section and returns one list.
`Registry.load`, `pio.load` and `cmake.load` become views over it, and are then
removed as their callers move over.

Each entry has these fields:

- `name`
- `firmware`: the family name
- identities: serials, `canbus_uuids`, by-id paths, `device_id`s
- `stop_services`
- a builder settings block

The builder parses its own block, so the loader never needs to know which
builder is which.

**A key read by one seam module is spelled
`<seam module name>_<param name>`.** The prefix is the name of the builder or
helper module that reads the key. `docs/decisions.md` already says config keys
borrow the upstream tool's vocabulary, and the param name keeps following that
wherever an upstream word exists. What changes is that the prefix is now
always there.

| Today | After | Read by |
| --- | --- | --- |
| `env:` | `platformio_env:` | the platformio builder |
| `cmake_target:` | `cmake_target:` (already fits) | the cmake builder |
| `[firmware] cmake_args:` | unchanged (already fits) | the cmake builder |
| `profile:` | `kconfig_make_profile:` | the kconfig_make builder |
| `device_map:` | `knomi_serial_device_map:` | the knomi_serial helper's `identify` (section 3) |
| `klipper_section:` | removed | the device-info handler knows its own Klipper object (section 5) |

**`knomi_serial_device_map:` stays a key.** It is the path of the JSON file the
knomi_serial watcher writes, relative to `printer_data`, with default
`knomi/devices.json`. knomi_serial hardcodes that path today, but it may not
stay hardcoded. Keeping the key means knomi_serial's own install can find
`mcu_updater.cfg` and update it if the path ever moves, instead of this
project having to ship a matching release.

**`klipper_section:` is removed.** It says which Klipper object the klippy
module registers, and nothing about that changes from one install to another.
The device-info handler already knows it. A firmware whose klippy module
registers a different object gets a different helper, not a different key
value.

Keys every type has stay unprefixed: `firmware`, `chipset`, `serials`,
`canbus_uuids`, `stop_services`. So do family keys every family can have:
`source`, `builder`, `helper`, `flashers`, `submodules`, `auto_provision`.

Per-family kconfig keys (`<family>_extra_args`, `<family>_makefile_patches`)
keep their family prefix. A kconfig type builds two families, so the family
is what tells the builder which one a key applies to.

An old spelling is refused with a message giving the new one. Until config
migrations exist, a user renames the key by hand.

This removes two hazards along with the bug:

- Mixed-builder refusals and "foreign builder" skipping (`_is_foreign_builder`,
  `config.py:120`) go away, because nothing filters by builder any more.
- `Registry.save()` currently deletes `[type]` sections it does not own. With
  one loader and one writer, every type section has the same owner.

**Remove the `[display]` vocabulary.** The section spelling is already gone
(`sections.py` knows only `[type]`). What remains is naming: `Install.displays`,
`Paths.display_sidecar` (`data_dir/displays/<env>.build.json`), "display
firmware" labels, `_screen_*` helpers, and comments. These become type, device
and firmware vocabulary. The sidecar's on-disk path is kept while its accessor is
renamed, so no build record already on a printer is orphaned.

## 3. Identity

**By-id is the default for every provider**, PlatformIO included. A type can
declare a `/dev/serial/by-id/...` path, and presence is checked against the
by-id sweep.

**Firmware-provided identity is a helper handler, and knomi_serial is the only
one.** The BTT Knomi v2 has a CH340 with nowhere to store a serial, so every
unit enumerates identically. The identity lives in its firmware instead. The
device map (`watcher.py`, read and never written), `listen.discover()` and
asking the device all become knomi_serial's `identify` handler. A knomi with a
unique by-id can still be tracked by by-id.

**Roadrunner needs no `identify` handler.** Its USB descriptor serial is its
identity, so it uses the by-id default. `byid.parse_entry` already reads
`usb-Vylyne_Roadrunner_RR-<serial>-if00` as a device with fw `Vylyne`, chipset
`Roadrunner` and serial `RR-...`, which lands in `BusWatcher`'s fingerprint.
A provisioned `RR-<26 base32>` serial goes in the type's `serials:` like any
other board's. The fw and chipset parsed from a by-id name are never how a
row gets its family; the family comes from the declared type.

The roadrunner helper handles what by-id can't confirm. That is all behind
capabilities, none of it in the identity path:

- **`device_info`**: the INFO probe (`_valid_info`, with a
  `Vylyne`/`Roadrunner` topology check) when Klipper does not hold the board.
- **`provision` / `on_appear`**: a sweep row whose serial matches
  `RR-UNPROVISIONED-<16 hex>`, confirmed with `find_untracked` before
  anything is written.
- **`request_bootsel`**: confirms the board with `find_provisioned` before
  requesting BOOTSEL.

The PlatformIO row's `id` becomes the declared identity: the by-id path for a
`serial:` section, the `device_id` for a `device_id:` section. The resolved
port stays in `path`. Flash parameter keys are unchanged.

## 4. The inventory

The inventory is a core module outside the agent. It joins three things:

1. **Declared identities**, from the one type list.
2. **Presence**, from a single sweep of by-id, CAN and bootloader states.
   The sweep is *injected*, the same way `type_status` takes versions and
   canbus results and `device_provenance` takes an `info_source`. That keeps
   the join testable without hardware, and lets the agent reuse the sweep it
   already holds.
3. **Running state**: `FlashLog`, the Klipper object, and the helper's device
   info (section 5).

`targets[]` becomes a projection of the inventory. Its wire shape does not
change. The CLI's `status`, `add-serial` and tracking commands read the same
inventory, which fixes the Roadrunner CLI gap as a consequence of the design
rather than as a patch.

`_cmake_target` stops calling `scan(self.paths)` itself and uses the sweep it
is given.

## 5. Device info: one handler per firmware

Every firmware answers "what is this board running?" through a device-info
handler. The handler knows which Klipper object to read. It falls back to
talking to the device directly only when Klipper does not hold the board.
The provenance spec's order still applies: Klipper first, then the wire.

| Firmware | Klipper object | Wire fallback |
| --- | --- | --- |
| klipper | `mcu` / `mcu <name>` sections | none |
| katapult | none: Klipper never holds a board in its bootloader | none; the state comes from the sweep |
| cartographer | `mcu <name>` (its `CONFIG_VERSION`) | none |
| roadrunner | `high_resolution_filament_sensor <name>` | INFO over the direct USB protocol |
| knomi_serial | `knomi_serial <name>` | the identify handler's report |

A handler returns identity, version, and (where the firmware reports one) the
labeled digest. Reading a sha out of a version string is part of the same
handler (the provenance spec's `VersionReader`, renamed): roadrunner reads the
`git describe` forms, knomi_serial wraps `pio.running_sha`, and cartographer
always returns `None`, for the reason `docs/decisions.md` records.

**Roadrunner reports the same device info on all three transports:** usbserial,
i2c and uart. The UART limitation in the provenance plan's transport table is
retired. So are Task 5's "over UART the digest arrives without `start` and
`length`" paragraph and the matching docstrings (`status.py:2291-2294`,
`:2352-2356`). **Absence is never mismatch** remains a general rule (provenance
spec, "Absence is not mismatch"), and it now applies to firmware that doesn't
report a field at all, not to a particular transport.

## 6. Helpers are capabilities

A helper is named on a family with `helper:`, and it implements any subset of
these capabilities:

| Capability | Contract | Implemented by |
| --- | --- | --- |
| `identify` | map an ambiguous device to its declared identity | knomi_serial |
| `device_info` | section 5 | roadrunner, knomi_serial, cartographer |
| `request_bootsel` / `wait_ready` | move a running board into its ROM bootloader, and confirm it came back | roadrunner |
| `provision` / `on_appear` | give an unprovisioned board its durable identity | roadrunner |

`helpers.registry.HELPERS` keeps its rule: every implementation is reviewed and
named explicitly, and nothing is imported because config said so. Asking a
family for a capability it does not have returns `None`. A firmware without a
capability is an ordinary firmware, not a misconfiguration. A misspelt
`helper:` name still raises.

This is also the migration `docs/decisions.md` ("New firmware-specific code
goes behind the helper seam") left for its own design:
`discovery/knomi_serial/`, `discovery/roadrunner.py`'s INFO parsing, and
Cartographer's version special cases move behind these capabilities.

## 7. Flashers are listed per firmware

Each family lists its flashers in order:

```ini
[firmware klipper]
source: ~/klipper
flashers: flashtool

[firmware katapult]
source: ~/katapult
flashers: dfu_util, bootsel

[firmware roadrunner]
source: ~/roadrunner
builder: cmake
helper: roadrunner
flashers: bootsel
```

`flashtool` is not in katapult's list. Replacing katapult over katapult needs a
deployer, and this tool doesn't track or manage that. The risk is probably
overstated, but it would be new scope. Katapult also doesn't report its version
and rarely needs an update. Katapult gets written only through a ROM
bootloader. `flashtool` writes klipper through katapult, so it belongs in
klipper's list.

Every flasher gains `supports(device, helper) -> bool`. The loop picks the
first flasher in the family's list that supports the device. `chipsets` and
`states` become inputs to `supports()` rather than a global first-match over
`FLASHERS`, so the unreachable-RP2040-flasher problem goes away: order is
per family, not global.

**`helper_bootsel` folds into `bootsel`.** The `bootsel` flasher supports a
device that is already in BOOTSEL, or a running device whose family's helper
has `request_bootsel`. The loop runs the helper steps around the write:
`request_bootsel`, then the topology-matched write, then `wait_ready`. The
direct `target_for` calls in `agent/methods/flash.py` and `cli.py` go away.
That keeps `docs/decisions.md`'s rule that flashers describe a mechanism,
never a product.

**`needs_services_stopped` moves onto the target.** Today it is a class
attribute that `group_by_stop` reads through `by_name(target.flasher)`, and it
differs between the two flashers being merged:

- `bootsel` has `False`. Nothing holds the port of a board that is already in
  BOOTSEL.
- `helper_bootsel` has `True`. The request goes over a port Klipper is holding.

One flasher can't carry both answers. The loop therefore sets it on the
`FlashTarget` when it picks the flasher, from the flasher and the device's
state together. This is the same way `stop_services` is already a per-target
tuple. Setting `True` for every BOOTSEL write would be simpler, but it would
also stop Klipper for a board Klipper has never held. `docs/decisions.md`
records the precedent: `needs_klipper_stopped` became `needs_services_stopped`
only when the per-type list landed alongside it. The flag moves together with
the thing that makes it true.

**First-time install has a family too.** `flashers.flash` calls
`select_for(chipset, state)` for a bare board with no type
(`flashers/flash.py:943`). What gets installed on that board is katapult, so
the flasher comes from the katapult family's `flashers:` list. `select_for` is
removed only once `add-mcu` resolves its flasher that way.

A family with no `flashers:` key is a config error that names the key. That
is the same strictness rule as section 1. install.sh seeds `flashers:` for
klipper and katapult.

## 8. Operations

**`build(types)`** loops over the given types and calls each firmware's
builder, collecting results. There is no per-provider command.

**`flash(devices)`** is the one loop:

1. Resolve each device's flasher (section 7). A device with no supporting
   flasher becomes a reported failure. It does not abort the batch.
2. Group by the services each write needs stopped
   (`flashers.registry.group_by_stop`, `stop_services_union`), so one outage
   covers each group.
3. For each device, in order: run the helper pre-step, write, then run the
   helper post-step.
4. The loop writes `FlashLog` whenever a write completed, before any failure is
   raised. That is the rule `_cmake_flash` already states, and it closes the
   provenance plan's Task 2, because no individual flasher is responsible for
   it any more.
5. Auto-provision (section 10) runs as a post-step where the helper has one.
6. Collect failures and report them after the batch.

Cancellation stays between devices (`docs/decisions.md`, "Do not move the
cancellation boundary"). BOOTSEL writes are sequential, and each is matched to
its own board by topology, so a sweep over several RP2040 boards has no extra
sequencing to specify. A partially completed sweep is described by the
per-device results the loop already collects.

This retires:

- the CLI's `-t`-only refusal for CMake types
- `type_not_bulk_flashable` and `_require_flashable_type`. The code was never
  documented in `docs/agent-api.md`, so it is removed rather than deprecated.
- `update-all`'s early exit. `update-all` becomes build-all followed by
  flash-all.

## 9. One verdict

One function replaces `_device_status`, `pio.device_status` and the
`UNKNOWN_VERSION` stub in `_cmake_target`. It takes the inventory row, meaning
the declared identity, presence, `FlashLog` and device info, and returns a
`DeviceStatus`.

The rules are the provenance spec's, unchanged:

- Klipper first
- absence is never mismatch
- a digest mismatch outranks a version match
- the board's reported range is authoritative
- CRC-32/ISO-HDLC, golden vector `0xBBE38AA9`, already shipped in `4ab49da`
- one new reason for an unexpected image, with tone `TONE_ATTENTION`, beside
  `PROTOCOL_MISMATCH`

## 10. Auto-provisioning on appearance

`auto_provision:` is a family key and is opt in. It is a boolean that defaults to
off:

```ini
[firmware roadrunner]
helper: roadrunner
auto_provision: true
```

A family whose helper has no `provision` capability refuses the key.

Provisioning without asking is safe because an unprovisioned Roadrunner
exposes only its identity and admin registers. Klippy cannot reach a printing
state with one, so provisioning it cannot interrupt anything the printer is
doing. It is opt in anyway, because the setting lives on the firmware and a
firmware section shouldn't write to hardware nobody asked about. The klippy
extra provisions on `klippy:connect` / `klippy:ready` as well, but that only
covers boards already configured in Klipper. Host provisioning covers a board
that is plugged in and not yet configured anywhere.

**Agent:** the agent already watches `/dev/serial/by-id`. `BusWatcher`
(`agent/events.py:237`) polls every 15 s when idle and every 2 s when busy, and
fingerprints on `(fw, chipset, serial)`. An `RR-UNPROVISIONED-<hex>` serial is
unique per board. It changes the fingerprint when the board appears and again
once the board is provisioned, after which `UNPROVISIONED_RE` stops matching,
so the handler cannot loop.

- `on_change` receives the devices the poll found (today it takes no
  arguments) and passes each one to the `on_appear` capability of every
  declared family that has `auto_provision: true` and a helper with that
  capability. `adopt_paired` stays on the same hook,
  called through a small adapter that throws away the argument, so its own
  signature doesn't change.
- `provision` takes the operation lock without waiting (`lock.exclusive`
  raises `BusyError` rather than blocking). While a build or flash holds the
  lock, the handler skips, and the next poll retries. It never blocks the
  watcher thread, which would stall the `bus` events the UI needs during a
  flash.
- It never runs from the status poll.

## 11. Tracking an unprovisioned board provisions it

The `roadrunner_unprovisioned` refusal (`agent/methods/registry.py:236-252`)
is removed. It exists because the serial changes at provisioning, but the new
serial isn't a mystery: `provision_roadrunner` generates it from the UUID the
host passes in, and the board confirms it by re-enumerating with that serial.

Tracking a serial goes through the type's family helper and never branches on
the provider:

1. If the helper has `provision` and recognises the serial as unprovisioned,
   provision under the operation lock (confirm, write, wait for
   re-enumeration). That is the same sequence `fw.roadrunner.provision` runs
   today.
2. Add the serial the board came back with to the type's `serials:`.

Two constraints:

- **The unprovisioned serial is never persisted.** Its 16 hex characters are
  the RP2040 flash UID. The only serial written to config is the provisioned
  one.
- **This does not depend on `auto_provision`.** Tracking is an explicit request
  for that one board; `auto_provision` only controls what the watcher does
  without being asked. The CLI has no watcher, so this is its only
  provisioning path, and the agent's `fw.serial.add` works the same way.

A lock held by another operation refuses the track with `BusyError`, the same
as any other write. It is not retried, because a person is waiting on the
answer.

## Order of work

Each step leaves `targets[]` and the verdicts unchanged until the step that is
meant to change them.

Two plans: steps 1-4 (config and inventory), then steps 5-9 (handlers and
loops).

1. **One type loader**, plus removing the display vocabulary. This fixes the
   Roadrunner CLI tracking gap. The key renames from section 2 land here too,
   since this is where builders start parsing their own blocks.
2. **Strict firmware sections**, install.sh seeding with the katapult clone
   offer, and the flashtool fallback.
3. **Inventory lift.** `targets[]` is unchanged.
4. **The CLI reads the inventory.**
5. **Device-info handlers**, with the UART limitation retired.
6. **`flashers:` lists, `supports()`, helper steps in the loop**,
   auto-provisioning, and provision-on-track (section 11).
7. **The verdict**, with the new reason.
8. **The build/flash/update-all loops**, retiring the refusals.
9. **Docs and bench.** On the bench (192.168.83.105): a Roadrunner over
   usbserial, plus a bystander RP2040 in BOOTSEL during a Roadrunner write, to
   prove the topology match refuses the wrong volume.

Step 1 goes ahead of strictness because it fixes the reported bug without
requiring an install.sh run on either printer. The key renames do need a
hand edit on any printer with a PlatformIO or kconfig type.

Already shipped from the provenance plan: Task 1 (`771ff49`, the running
version is reachable) and Task 4 (`4ab49da`, the UF2 image digest).

## Testing

- The inventory join and the verdict are pure functions over injected inputs.
  They are tested per row shape (kconfig, PlatformIO, CMake, CAN) with no
  hardware, and against `targets[]` fixtures to prove the projection is
  unchanged.
- The loop is tested with fake flashers and helpers: first-match selection per
  family, a device no flasher supports, a failure mid-batch that must not stop
  later devices, and `FlashLog` written before the failure is raised.
- Strictness: an undeclared family, a missing `flashers:` key, and a misspelt
  helper each refuse with a message naming the fix.
- Auto-provisioning: doesn't run with `auto_provision` absent (the default),
  skips while the lock is held, and finishes after exactly one provision per
  board.
- Provision-on-track: `serials:` gets the provisioned serial and never the
  `RR-UNPROVISIONED` one, whatever `auto_provision` says. A lock held
  elsewhere refuses the track.
- Key spellings: each old key is refused with the new spelling in the message.
- Every guarded line these steps rewrite has a mutation spec anchored to it.
  Re-anchor it in the same commit (AGENTS.md).

## Deferred

- **Config migrations at agent startup.** Running them as the agent's first
  step would let a service restart migrate an existing install. They are
  deferred until the service's run restrictions have been checked.
- **Event-driven presence** (udev) instead of `BusWatcher` polling. The
  injected sweep keeps this a drop-in change later.
