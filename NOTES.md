# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at
session start, struck through when acted on, not deleted.

---

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
