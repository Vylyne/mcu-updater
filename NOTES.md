# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at
session start, struck through when acted on, not deleted.

---

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

## 2026-08-19 — Printer config to migrate

The real printer config (cartographer, knomi, OctopusMAXEZ,
hexadistrofusion), captured before Step 1 of `docs/rebuild-plan.md` reverted
`mcu-updater.cfg` to its pre-`ffcc210` state. This is the input to Step 11
(migration script) and Step 15 (sample config rewrite) — the post-migration
schema this file should end up expressing.

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
