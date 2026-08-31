# Where everything lives

Files are split by *what they are*, following the `printer_data` conventions.

```shell
~/printer_data/config/mcu-updater/     # hand-edited. backed up. editable in Mainsail.
    mcu-updater.cfg                        #   [updater] settings + one [firmware ...]/[type ...] per board
    types/bttebb36/klipper.config          #   saved menuconfig answers, per type
    types/flylllplusbuffer/klipper.config
    types/carto_v4/cartographer.custom.config  #   your own answers, kept before a reseed would overwrite them

~/printer_data/mcu-updater/            # generated. not backed up.
    bttebb36/klipper.bin                   #   built firmware
    bttebb36/klipper.build.json            #   build provenance, for staleness
    bttebb36/klipper.profile.json          #   what was seeded, for drift detection
    flylllplusbuffer/klipper.uf2
    .updater.lock                          #   runtime state
    .updater.state
```

The per-type folders sit under `types/` so that `mcu-updater.cfg` is the only
thing in the directory Mainsail's config editor opens. They held that spot
directly at first — six board types meant six folders listed above the one file
anyone edits. An older install is moved across automatically; see below. The
data tree keeps the flat layout: nobody browses it, and
its dotfiles already sort apart from the per-type folders.

## Why the split

**Config goes under `config/` because that directory is special.** Moonraker's
file manager serves it, so those files are editable in Mainsail's own editor —
which means you can adjust a saved `.config` from a browser today, without
waiting for a dedicated Kconfig UI. It's also what every backup scheme picks up,
and the saved menuconfig answers are the one thing here you genuinely cannot
regenerate: lose them and you're redoing menuconfig for every board.

**Firmware binaries deliberately do *not* go in `config/`.** Backup tools like
klipper-backup git-commit everything under that directory, so a `.bin` there
means a binary churn commit after every single build. They're also regenerable
from source plus the saved config, and Mainsail's editor would list files it
can't open. The same reasoning puts the lock and journal in the data tree —
they're runtime state, not configuration.

**`<fw>.profile.json` sits with the binaries rather than beside the `.config` it
describes.** It records which vendor answer file a config was seeded from and
the hash it had when written, which is how a later hand-edit becomes visible
instead of silent. Putting it in `config/` would offer it up for editing in
Mainsail's file browser, next to the very file whose integrity it exists to
vouch for. Losing it is survivable: the type reads as unmanaged, which is what
every install predating profiles reads as anyway.

`~/printer_data/mcu-updater/` follows the pattern other add-ons use, e.g.
`moonraker-timelapse` writing to `~/printer_data/timelapse/`.

## One config file: `mcu-updater.cfg`

Klipper-style, because it lives next to `printer.cfg` and gets hand-edited.
Settings and the registry share it — `.cfg` sections namespace cleanly, so there
is one file to find, one file to back up, and one file to open in Mainsail:

```ini
# Tool settings. Every value has a default, so this section is optional.
[updater]
enable_flashing: true

# Toolhead boards. The buffer patch is specific to this batch.
[type flylllplusbuffer]
chipset: stm32f072xb
firmware: klipper, katapult
serials:
    4C0033000957465331323720-if00
    3F0037000957465331323720-if00
klipper_makefile_patches:
    src/Makefile -> src-y += buffer.c
canbus_uuids:
    bcb5346fc731
```

| Key | Meaning |
| --- | --- |
| `chipset` | Required on every type, PlatformIO included. Matches the chipset segment of the `/dev/serial/by-id` name. |
| `serials` | One tracked board per line. |
| `canbus_uuids` | One tracked CAN-addressed board's uuid per line, parallel to `serials` but a separate key. No interface is stored — Linux CAN interface names (`can0`, `can1`, ...) are enumeration order, not stable identity, so the flasher re-discovers one at write time instead of trusting a remembered one. |
| `firmware` | A **list** of the families this board runs, e.g. `cartographer, katapult`. A type with no bootloader simply omits it. See `[firmware ...]` sections, below. |
| `profile` | The vendor answer file the config was seeded from, e.g. `config.CartoV4USB`. |
| `<fw>_extra_args` | Appended to the `make` command line. |
| `<fw>_makefile_patches` | `<file> -> <line>`, appended to that Makefile for one build then reverted. |
| `<fw>_extra_repos` | Secondary source trees whose git SHA is tracked alongside the main tree; a commit in any of them is reported the same as a change in the main source. |

`<fw>` is any family named in `firmware:`.

`builder:` lives on `[firmware ...]`, not on `[type ...]` — how a tree
compiles is a property of the tree, not of a board that happens to use it. A
type declaring only `[firmware ...]` sections whose builder is `platformio`
needs no Kconfig and no Katapult; the env named by `env:` is the type. There
is no `provider:` key on `[type ...]` — it is derived from the families the
type names.

A source tree that doesn't follow the `~/<name>` / `out/<name>.bin` convention
— any vendor fork, e.g. Cartographer's — gets a `[firmware <name>]` section of
its own, with `source:` and `artifact:` keys. See the main
[README](../README.md#firmware-families).

The `[updater]` section holds `make_jobs`, `clean_before_build`,
`reseed_on_build`, `service`, `service_backend`, `dry_run`, `enable_flashing`,
`allow_flash_while_printing`, `log_ring_size` and `platformio_bin`. All
optional. A PlatformIO firmware family's own source tree is named on its
`[firmware ...]` section, not in `[updater]`.

**Edit the existing `[updater]` section rather than appending a second one.** A
duplicate section is refused outright: first-wins would mean
`enable_flashing: true` in the later copy silently doing nothing, which is a
miserable thing to debug.

**Your comments survive.** The panel writes this file structurally when you add a
serial or edit a type, and `configparser` would throw every comment away doing
that — so writes go through a purpose-built round-tripper that preserves
comments, ordering, blank lines, and any keys it doesn't recognise. A note
explaining *why* a board needs a particular patch is exactly the kind of thing
that must not vanish because you tapped a button on your phone.

`makefile_patches` exists because Klipper's build system offers no way to add
`src-y +=` lines from the command line, and a permanent edit would leak into
every other type sharing that chipset and conflict on the next `git pull`.

## The systemd unit is called `mcu-updater`

Not `klipper-updater`, and that is load-bearing. KIAUH discovers instances with
`^<component>(-[0-9a-zA-Z]+)?\.service$`, so `klipper-updater.service` matches
the *Klipper* pattern: KIAUH treats it as a Klipper instance called "updater",
opens it to read `EnvironmentFile=`, and its whole menu crashes if the unit is
not world-readable.

`klipper_updater` and `klipper-klipper-updater` happen to slip past that exact
regex too, but only via quirks - an underscore is not a hyphen, and the character
class forbids a second hyphen. A name that starts with no component name at all
is safe by construction instead, which is what `mcu-updater` is.

The unit must also equal the `[update_manager <name>]` section, because Moonraker
only accepts a `managed_services` value matching that, `klipper`, or `moonraker`.
Both constraints point at the same answer.

The unit is installed mode 0644 with `install`, not `cp`. `mktemp` creates 0600
and `cp` carries that mode across, which is how the KIAUH crash was triggered in
the first place.

## Overrides

Every path derives from one `Paths` object, so nothing is hardcoded elsewhere:

| Variable | Replaces |
| --- | --- |
| `MCU_UPDATER_HOME` | `~` |
| `MCU_UPDATER_PRINTER_DATA` | `~/printer_data` |
| `MCU_UPDATER_CONFIG_DIR` | `…/config/mcu-updater` |
| `MCU_UPDATER_DATA_DIR` | `…/mcu-updater` |
| `MCU_UPDATER_FAKE_BUS` | `/dev/serial/by-id` |

## The standalone UI lives outside all of this

`~/mcu-updater-ui` (the installed build of `ui/`) and its nginx site are
deliberately outside the `Paths` object above and outside `~/printer_data/`
entirely — see [docs/standalone-ui.md](standalone-ui.md) for the full runbook
and [docs/decisions.md](decisions.md) for why. In short: Moonraker's
`type: web` update manager refuses a path inside a git repository, and
`rmtree()`s its path on every update — sharing a directory with
`.updater.state` (the flash-recovery journal, one level up from `DATA_PATH`)
would put a routinely-wiped directory next to state that must never be wiped.

Set with `install.sh` env vars, not `Paths` overrides — these are install-time
choices, not something the agent itself reads at runtime:

| Variable | Default | Replaces |
| --- | --- | --- |
| `UI_PATH` | `~/mcu-updater-ui` | Where the installed UI build lives, and nginx's `root` |
| `MCU_UPDATER_UI_PORT` | `8090` | The nginx site's `listen` port |
| `MCU_UPDATER_UI_SERVER_NAME` | `_` (any host) | The nginx site's `server_name` |

## Coming from the old layout

**Historical.** Both migrations below predate the schema-first rebuild
(`docs/rebuild-plan.md`) and describe moves off layouts nothing still ships.
The pre-rebuild `[mcu ...]`/`[display ...]`/single-`firmware`-key config had a
migration script as well; it was retired once the one install it existed for
had run it, on the same reasoning as "Registry moves" below — a one-time job is
not worth shipping code for.

### Per-type folders → `types/`

Handled for you. On startup the CLI and the agent each move any folder sitting
directly in `config/mcu-updater/` that holds a `klipper.config` or
`katapult.config`, and say which ones they moved. Anything else in there is not
ours and is left alone.

Two cases refuse rather than guess, because both mean two folders claim the same
type and building from the invisible one is exactly the failure this tool
exists to prevent: a name that already exists under `types/`, and an MCU type
literally named `types`. Resolve it by hand and re-run.

### Registry moves

Two earlier moves have no automatic migration — each is a one-time job and the
conversion isn't worth shipping code for. In both cases the tool **refuses to
start** rather than reporting an empty registry, because that would let the next
`add-type` write a fresh file while your real one sat untouched:

- `~/mcus/mcus.json` → `~/printer_data/config/mcu-updater/mcu-updater.cfg`
  (JSON to `.cfg`, and out of the home directory)
- `mcus.cfg` + `updater.conf` → the single `mcu-updater.cfg`. Rename the
  registry, then paste your old `[updater]` section into the top of it. A
  leftover `updater.conf` is warned about, not read.

To move across by hand:

```bash
NEW=~/printer_data/config/mcu-updater
mkdir -p "$NEW" ~/printer_data/mcu-updater

# saved menuconfig answers - the part worth keeping
mkdir -p "$NEW/types"
cp -r ~/mcus/*/                "$NEW"/types/
find "$NEW/types" -name '*.bin' -o -name '*.uf2' -o -name '*.build.json' -delete

# write $NEW/mcu-updater.cfg by hand (mcu-updater.cfg in this repo is a worked
# example), or recreate it with add-type / add-serial
rm -rf ~/mcus     # only once you have checked the new location works
```

Firmware binaries are not worth copying; rebuild them.
