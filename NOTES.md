# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at
session start, struck through when acted on, not deleted.

---

## 2026-08-20 — Step 16 fork survey: all three flagged wire breaks are real, live code

Confirmed by reading `Vylyne/mainsail`'s `mu/stable` checkout at
`C:\git\github\mainsail` (currently at `80f09150`) directly — none of this was
assumed. This is a **survey only, no fork code touched**, per Vi's answer when
asked how to scope Step 16's fork migration (full migration vs. survey vs.
RPC-only fix → **survey and report only**).

**All three items the Step-14 note (below) asked Step 16 to confirm are real,
not no-ops.** The fork still reads the pre-Step-14 wire shape in several
places. Scope is wider than the fork's documented 4-edited-file budget
(`docs/mainsail-fork.md`) — this is real migration work for a dedicated
session, not a quick patch.

**1. `fw.display.flash` — still called by name, will get `-32601` from the new agent.**
`src/store/server/fwUpdater/actions.ts:297`, action `flashDisplay`, calls
`method: 'fw.display.flash'` directly. This is the one item that hard-fails
(not silently degrades) against the current agent — Step 14 deleted the
method and its three registry entries outright. Fix is mechanical: change the
method string to `'fw.flash'` (same `{name, port}` params, confirmed
unchanged). Two more references to the string exist and are lower-priority:
`getters.ts:166`'s `canFlashDisplay` falls back to checking capability
`'fw.display.flash'` (harmless — it's an `||` with `'fw.flash'` first, but the
capability will never again be advertised so the fallback is dead); `types.ts:328`
has an explanatory comment naming it (cosmetic only).

**2. `types[]`/`displays[]` — still populated from `fw.status`, will silently go empty.**
`mutations.ts:48` (`state.types = payload.types ?? []`) and `:61`
(`state.displays = payload.displays ?? []`) read two keys Step 14 removed from
`fw.status` entirely. Not a hard failure — `?? []` means both silently
degrade to empty arrays, which is worse than an error because it reads as "no
boards, no screens" rather than "wrong". Everything downstream that reads
`state.types` / `getters['server/fwUpdater/types']` / `state.displays` /
`getters.displays` goes dark with it:

- `getters.ts:26` `types` getter, `:154` `dfuCapableTypes` (filters for DFU-capable
  STM32 boards — **this feeds the add-mcu flow**, so a real regression, not
  cosmetic), `:156` `displays` getter, `:171` `missingScreens`
- `FirmwareUpdaterPanel.vue:298` (`get types()`)
- `FirmwareUpdaterPanel/FirmwareUpdaterPanelUntracked.vue:119,146` (`get types()`,
  `typesFor()` — used to label untracked bus devices with candidate types)
- `FirmwareUpdaterPanel/FirmwareUpdaterPanelAddMcuDialog.vue:184,189` (`get types()`,
  used to build the type-picker dropdown — **breaks first-time MCU setup's type
  selector**)
- `FirmwareUpdaterPanel/FirmwareUpdaterPanelTypeDialog.vue:209` (type-name list for
  the add/edit-type dialog), `:256` (`this.mcuType?.katapult_installed` — see #3)
- `FirmwareUpdaterPanel/FirmwareUpdaterPanelTarget.vue:617,640` (`state.displays.find(...)`,
  `state.types.find(...)` — looks up the matching legacy record for a given
  target row)

Fix is a real migration, not a rename: everything above needs to be re-derived
from `targets[]` (already the projection `docs/rebuild-plan.md`'s "Target
schema" describes — see the `targets-wire-shape` design memory) instead of the
two legacy arrays. `FwType`/`FwDisplayType` (`types.ts:57-71`, `:319-344`) may
end up unneeded entirely once every consumer moves to `FwTarget`.

**3. Legacy per-field names (`stale`/`stale_reason`, `firmware_state`,
`artifact_state`, `katapult_installed`) — still declared and read.**
`types.ts` still declares `FwArtifact.stale`/`stale_reason` (:24-25),
`FwDisplayScreen.firmware_state` (:381), `FwDisplayType.artifact_state` (:332),
and `FwType.katapult_installed` (:60) — all gone from the wire per Step 14.
Live reads, not just declarations:

- `FirmwareUpdaterPanelTarget.vue:597` — `screen.firmware_state` in a tooltip/label
- `FirmwareUpdaterPanelTypeDialog.vue:256,274,283` — `mcuType?.katapult_installed`
  seeds a form checkbox and is sent back in `fw.type.add`/`fw.type.update`
  params. **Check this one against the agent side specifically**: Step 6's log
  says `add_type()`/`fw.type.update` deliberately *kept* `katapult_installed`
  as an input-side compatibility parameter (translated internally to the new
  list), so this particular read may still work on the way in — but the value
  it reads it *from* (`mcuType.katapult_installed`, sourced from the now-empty
  `types[]`) will always be `undefined ?? true`, i.e. the checkbox always
  defaults true regardless of the real type. Needs re-sourcing from `targets[]`
  or a family-list check either way.
- `getters.staleCount`/`unprovableCount` (`getters.ts:100,102`) already read
  `t.artifact.state` (the new `FwArtifactState` on `FwTarget`, not the legacy
  `FwArtifact.stale`) — these two are already correct and need no change,
  noted so they aren't mistaken for part of the same bug.

**Net scope for the real fix**: `types.ts`, `getters.ts`, `mutations.ts`, and
four `.vue` files (`FirmwareUpdaterPanel.vue`, `.../FirmwareUpdaterPanelTarget.vue`,
`.../FirmwareUpdaterPanelTypeDialog.vue`, `.../FirmwareUpdaterPanelUntracked.vue`,
`.../FirmwareUpdaterPanelAddMcuDialog.vue` — five, not four) plus `actions.ts`'s
one-line method-name fix. This is bigger than the fork's own "4-file edit
budget" note in `docs/mainsail-fork.md` describes for the *original* delta —
expected, since that budget was about the rebase surface for new features, not
a wire-contract migration. Worth a dedicated session with its own gates
(`npm run test:unit`, then `npx vite build` last, per this project's own
Windows gotchas) rather than folding into whatever session picks this up next.

## 2026-08-20 — Step 15 found a real config.py bug, deferred not fixed

While rewriting `mcu-updater.cfg` in the target schema (Step 15,
`docs/rebuild-plan.md`), `Registry.load()`'s per-type loop
(`src/mcu_updater/config.py:380`, `for fw in fw_names:`) turned out to seed
an empty `FwConfig` slot for **every** globally-declared `[firmware ...]`
family, not just the two builtins (`klipper`, `katapult`). The real config
now declares `[firmware cartographer]` and `[firmware knomi_serial]`, so
every type - including plain STM32 boards with nothing to do with either -
picks up phantom "never built" artifact entries for both. Confirmed real
(not a fixture issue) by loading the sample directly and inspecting
`mcu.fws`; leaks into `fw.artifacts`, `fw.type.list`, and `type_status()`
for every board.

Escalated rather than fixed inline, since it's outside Step 15's scope
(fixture-only). **Vi's answer: note it, don't fix yet.** Two tests
(`test_artifacts_returns_both_firmwares`, `test_status_type_shape` in
`tests/test_agent_methods.py`) now pin the current, buggy behaviour
explicitly and are commented as a known bug - so a future fix has a clear
"this should go back to the narrower set" marker rather than having to
rediscover the leak from scratch. The likely fix is narrowing
`config.py:380`'s loop to `firmware.BUILTIN` instead of `fw_names` - see
Step 15's Progress log entry for the full reasoning.

## 2026-08-20 — Step 16 must confirm the Mainsail fork against two Step 14 wire changes

Step 14's legacy purge made two breaking changes to the agent's JSON-RPC
surface, both by Vi's explicit direction (not a unilateral call) - see
docs/rebuild-plan.md's Step 14 log for the full reasoning. Both need checking
against the actual deployed fork (`Vylyne/mainsail`, branch `mu/stable`)
before or during Step 16's migration work, since a fork still calling the old
shape will break silently against the new agent, not loudly:

1. **`stale`/`stale_reason` (artifact) and `firmware_state`/`artifact_state`
   (display) are gone from the wire.** `fw.status`'s per-artifact payload now
   carries only `reason` (the granular, un-collapsed value - `never_built`,
   `config_changed`, `source_changed`, `no_provenance`, etc., or `null` for
   current). If the fork reads `stale`/`stale_reason`/`firmware_state`/
   `artifact_state` anywhere - directly, or via `types[]`/`displays[]` - those
   reads now get `undefined`, not an error.
2. **`fw.display.flash` no longer exists as an RPC method.** Flashing a
   display now goes through `fw.flash` with `{name, port}` instead of
   `{name, port}` on the old method name - same params shape, different
   method string. `targets[]`'s per-device flash action already reflects this
   (`method: "fw.flash"`), so anything reading actions off `targets[]` is
   already correct; anything calling `fw.display.flash` by name directly is not.
3. **`types[]` and `displays[]` are gone from `fw.status` entirely** (this one
   was already scheduled - `API_VERSION` hit 2 specifically to allow it, see
   the `targets-wire-shape` design memory). Only `targets[]` remains. Confirm
   the fork reads `targets[]` exclusively and not either legacy key.

If the fork already only reads `targets[]` and never called `fw.display.flash`
by name, all three are no-ops on the panel side and this note can be struck
through once confirmed - but confirm it, don't assume it.

## 2026-08-19 — Step 13 (RP2040 BOOTSEL flasher) shipped untested on hardware

`src/mcu_updater/flashers/bootsel.py` is new: copies a `.uf2` onto a mounted
`RPI-RP2` volume, the same first-time-bootstrap role `dfu_util.py` plays for
STM32. Fully exercised off-hardware (`paths.bootsel_root` / a fake mount), but
**never against a real RP2040** - there is none to hand. Two things this
cannot verify from a dev box:

- Whether the board actually automounts as `RPI-RP2` at all on the printer's
  host, and if so, under which of the two globs `bootsel_scan()` searches
  (`/media/*`, `/run/media/*`) - see Step 10's own `untested` note, unchanged
  by this step.
- Whether a plain `shutil.copy2` onto that mount is sufficient, or whether the
  real bootloader wants the file flushed/synced before it reads it back.

Try `mcu-updater add-mcu <rp2040-type>` on a bench board (never the toolhead)
once one is available, and update this entry once it's confirmed either way.

## ~~2026-08-19 — Printer config to migrate~~

~~The real printer config (cartographer, knomi, OctopusMAXEZ,
hexadistrofusion), captured before Step 1 of `docs/rebuild-plan.md` reverted
`mcu-updater.cfg` to its pre-`ffcc210` state. This is the input to Step 11
(migration script) and Step 15 (sample config rewrite) — the post-migration
schema this file should end up expressing.~~

Struck through 2026-08-20: Step 15 rewrote `mcu-updater.cfg` from this
content in the target schema. The content is kept below for the record.

```ini
# mcu-updater configuration.
#
# Lives in printer_data/config so it is backed up with the rest of your config
# and editable in Mainsail. Built firmware is NOT here - it goes in
# ~/printer_data/mcu-updater, because binaries have no business in a config
# directory that gets git-committed.
#
# Comments in this file survive edits made from the panel.

# Tool settings. Every value has a default, so this whole section is optional.
[updater]
enable_flashing: true
make_jobs: -1
clean_before_build: true
service: klipper
service_backend: moonraker
dry_run: false
allow_flash_while_printing: false
log_ring_size: 2000
reseed_on_build: true              # take a vendor's updated profile answers before building
pio_source: ~/knomi_serial         # one repo, shared by every env

# The MCU registry: one [type <name>] section per board model, listing every
# physical board of that model by its /dev/serial/by-id serial. `[type ...]`
# is the current spelling; `[mcu ...]` and `[display ...]` still work too -
# see the README.

[type bttebb36]
chipset: stm32g0b1xx
serials:
    230048001750304158373620-if00  # mcu EBBT0
    290055001850304158373620-if00  # mcu EBBT1
    320019000451343438333339-if00
    27000E000551343438333339-if00

[type flylllplusbuffer]
chipset: stm32f072xb
klipper_makefile_patches:
    src/Makefile -> src-y += buffer.c
serials:
    4C0033000957465331323720-if00  # mcu T0_buffer
    3F0037000957465331323720-if00  # mcu T1_buffer
    2B0038000957465331323720-if00  # mcu T2_buffer
    440037000957465331323720-if00  # mcu T3_buffer
    400030000957465331323720-if00  # mcu T4_buffer
    190031000957465331323720-if00  # mcu T5_buffer

[type hexadistrofusion]
chipset: stm32f072xb
serials:
    4B0036000A53594731383520-if00

[type OctopusMAXEZ]
chipset: stm32h723xx
serials:
    210008000551333231343036-if00

# ESP32 displays, built by PlatformIO instead of make+Kconfig - see the README.
# `[display knomi]` is the older spelling of this and still works.
[type knomi]               # the section name IS the PlatformIO env
provider: platformio

# A vendor fork that doesn't follow the ~/<name> / out/<name>.bin convention
# gets its own [firmware <name>] section - see the README.
[firmware cartographer]
source: ~/cartographer-klipper
artifact: klipper          # whatever `ls out/*.bin` showed

[type cartographer]
chipset: stm32g431xx
firmware: cartographer
serials:
profile: config.CartoV4USB
```
