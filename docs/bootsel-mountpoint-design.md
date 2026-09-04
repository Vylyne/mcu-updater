# BOOTSEL mountpoint — design

Status: proposed, 2026-09-04. Not implemented.

Verified against mcu-updater `0c446f2`. Every file and line reference below was
read at that commit; re-check them before implementing if the tree has moved.

## Problem

`scripts/udev.d-mcu-updater-bootsel.rules` mounts every RP2040 BOOTSEL volume at
one hardcoded path:

```
ACTION=="add", SUBSYSTEM=="block", ENV{DEVTYPE}=="partition", ENV{ID_FS_LABEL}=="RPI-RP2", \
  RUN{program}+="/usr/bin/systemd-mount --no-block --collect --owner=%USER% $devnode /media/%USER%/RPI-RP2"
```

A single fixed mountpoint produces three distinct failures:

1. **Two boards in BOOTSEL collide.** The second mount lands on an occupied
   path. Whatever happens next — a failed mount, a shadowed one — the second
   board is not independently addressable.

2. **One bystander board blocks all flashing.** `_find_mount()`
   (`src/mcu_updater/flashers/bootsel.py:111`) refuses when more than one
   RPI-RP2 volume is mounted, because there is no way to tell them apart. That
   guard is correct given the information available, but it also fires when the
   board you want to flash is joined by an unrelated spare Pico on the same
   host.

3. **The flash UID cannot break the tie.** `bootsel_id_for()` extracts the flash
   chip UID from `/dev/disk/by-id/usb-RPI_RP2_<UID>-part1`, and
   `flashers/bootsel.py:134`'s `target_for()` records it as the flash target's
   `id`. **That UID is not unique** — two Roadrunners from one batch report the
   same `pico_get_unique_board_id()`, confirmed on hardware. So the one piece of
   per-device data the BOOTSEL interface exposes cannot serve as an identity,
   and `target_for()`'s docstring claim that a flash can be "recorded against a
   real identity" is false.

The underlying issue is that the mount path carries no information about *which*
board it belongs to.

## Decision

**Mount each BOOTSEL volume under its own USB topology path.**

```
ACTION=="add", SUBSYSTEM=="block", ENV{DEVTYPE}=="partition", ENV{ID_FS_LABEL}=="RPI-RP2", \
  RUN{program}+="/usr/bin/systemd-mount --no-block --collect --owner=%USER% \
  $devnode /media/%USER%/BOOTSEL/by-path/$env{ID_PATH_TAG}"
```

Topology is the only per-device fact available at this layer that is actually
unique, and it is stable for a fixed installation — a board plugged into the
same port always mounts at the same path.

### Use `ID_PATH_TAG`, not `ID_PATH`

The `path_id` builtin sets both. `ID_PATH` is the raw form and contains `:` and
`/`; `ID_PATH_TAG` is the sanitized variant systemd itself uses for unit names,
and is the one safe to embed in a directory path.

### Rule ordering is already correct

`ID_PATH`/`ID_PATH_TAG` exist only after `60-persistent-storage.rules` has run
the `path_id` builtin. The rule installs as
`/etc/udev/rules.d/99-mcu-updater-bootsel.rules` (`install.sh:44`), so it runs
after 60- and both variables are populated. No ordering change is needed.

## Consequences in the codebase

### `bootsel_scan()` changes shape, and its marker check becomes load-bearing

Today it looks for a directory *named* `RPI-RP2` and then confirms
`INFO_UF2.TXT` inside it. With topology-named directories the name proves
nothing, so the scan becomes a glob of `BOOTSEL/by-path/*` and the
`INFO_UF2.TXT` check goes from belt-and-braces to **the only thing
distinguishing a real bootloader volume from any other directory**. That check
is already written; it just needs a comment saying it is now doing the whole
job.

`paths.bootsel_root` remains the test seam and needs no change.

### The multi-volume refusal can go

`_find_mount()`'s `len(mounts) > 1` refusal exists solely because the mounts
were indistinguishable. Once each mount names its port, a caller that knows
which port it wants can address it directly, and a bystander board stops
blocking unrelated work.

**This is a behaviour change, not just a cleanup.** Anything that today relies
on "exactly one volume or refuse" must be given a port to target instead. Do not
delete the guard without giving callers that parameter.

### `target_for()` stops claiming the UID is an identity

`flashers/bootsel.py:134` should record the topology path, not
`bootsel_id_for()`'s output, and the docstring's "recorded against a real
identity" language goes with it. The UID keeps one legitimate use: distinguishing
"no board attached" from "board attached, nothing mounted it", which is what
`bootsel_devices()` does for `BootselNotMountedError`. That is a *presence*
check, not an identity check, and stays valid.

## Correlating a board across the BOOTSEL reboot

This is what the change unlocks, and the reason it matters beyond tidiness. A
closed-loop flash needs to know that the board that came back is the board that
went away.

**The flash UID cannot do this** — see Problem 3. Topology is the only key.

The two paths are *not* string-equal, and that is the detail most likely to be
got wrong:

```
serial (CDC)      …-usb-0:1.2:1.0
mass storage      …-usb-0:1.2:1.0-scsi-0:0:0:0
```

The USB port prefix is stable across the reboot; the trailing interface and SCSI
segments are not. **The match must be prefix-normalized, never equality.**

`/dev/serial/by-path` ↔ `/dev/disk/by-path` is the pairing. Note that nothing in
mcu-updater reads `by-path` today — this is new code. `discovery/usb.py`'s
`UsbDevice` already carries a sysfs topology name (`1-1.2` style), which is a
*third* namespace and does not directly equal either `by-path` form; if the
implementation wants to use it, the mapping needs writing and testing rather
than assuming.

### Two flags flip once correlation exists

- **`Bootsel.needs_services_stopped`** becomes `True`. Its own comment
  (`flashers/bootsel.py:37-42`) predicts this: *"The moment this tool routes a
  board into BOOTSEL itself, over a port Klipper may be holding, this flips."*
- **`Bootsel.settled()`** becomes a real wait instead of a documented no-op. It
  is currently empty because "it cannot name the device it is waiting for"
  (`flashers/bootsel.py:75`). With a port to watch, it can.

## Migration — the part most likely to be missed

`install.sh:216` skips installation entirely when the rule file already exists:

```sh
if [ -f "${BOOTSEL_UDEV_RULE}" ]; then
    printf "[BOOTSEL]  udev rule already present.\n\n"
    return 0
fi
```

**So every existing install keeps the old single-mountpoint rule and never
receives the new one.** Exactly the hosts that have been using this feature are
the ones that would not get the fix.

The installer needs a version check rather than a presence check — compare
against the shipped template, or embed a version marker comment in the rule and
compare that. Offer to replace when it differs, the same way the install already
prompts before writing.

Until an upgraded rule is in place, `bootsel_scan()` must tolerate **both**
layouts: the old `/media/<user>/RPI-RP2` and the new
`/media/<user>/BOOTSEL/by-path/<tag>`. Since the scan is driven by the
`INFO_UF2.TXT` marker rather than the directory name, supporting both is a
matter of globbing two roots.

## To verify on hardware before committing to this

None of the following can be settled by reading code:

- **`systemd-mount` creating a nested mountpoint.** systemd creates the mount
  directory, but `BOOTSEL/by-path/<tag>` is three levels deep and the parents
  may not exist. If it does not create them, the rule needs an `mkdir -p` or a
  tmpfiles.d entry.
- **`ID_PATH_TAG` actually being set for the partition.** The `path_id` builtin
  runs for block devices including partitions — that is how `/dev/disk/by-path`
  gets its `-partN` entries — but confirm the variable is populated in the rule's
  environment with `udevadm test`.
- **Two boards in BOOTSEL simultaneously**, mounting at distinct paths, both
  readable and writable.
- **Stale directory accumulation.** `--collect` garbage-collects the transient
  mount *unit*; it does not remove the directory. Check whether empty
  `by-path/<tag>` directories pile up across replugs, and whether that matters.
- **The prefix normalization**, against a real board: capture its
  `/dev/serial/by-path` entry, reboot it to BOOTSEL, capture its
  `/dev/disk/by-path` entry, and confirm the intended prefix rule actually
  matches the pair.

## Not in scope

The BOOTSEL closed loop itself — `REBOOT_BOOTSEL`, the wait for
re-enumeration, and `settled()`'s new body. This document only removes the
obstacle that made correlation impossible. The firmware side already exists:
`REBOOT_BOOTSEL` is opcode `02` and, as of the roadrunner identity-gate work, is
deliberately ungated so a board with no valid identity can still be recovered.
