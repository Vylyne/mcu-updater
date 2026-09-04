# BOOTSEL mountpoint — design

Status: mount path, dual-layout scan and installer migration implemented on
`develop`, and **verified on hestia 2026-09-04** - `ID_PATH_TAG` populated, the
three-deep mountpoint created, two boards mounted at distinct paths, and the
serial/disk `by-path` pair captured (see "Correlating a board" below). One
finding: mountpoint directories are not cleaned up on unplug, addressed in rule
version 3. The multi-volume refusal, topology correlation,
`needs_services_stopped` and a real `settled()` remain deliberately deferred.

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
got wrong. Measured on hestia, 2026-09-04, one board across the reboot:

```
serial (CDC)    /dev/serial/by-path/platform-fd880000.usb-usb-0:1.3:1.0            -> ttyACM10
mass storage    /dev/disk/by-path/platform-fd880000.usb-usb-0:1.3:1.0-scsi-0:0:0:0-part1 -> sda1
mountpoint      /media/klipper/BOOTSEL/by-path/platform-fd880000_usb-usb-0_1_3_1_0-scsi-0_0_0_0
```

The USB port prefix is stable across the reboot; the trailing SCSI and partition
segments are not. **The match must be prefix-normalized, never equality.**

Two wrinkles the measurement exposed, neither of them guessable from the code:

1. **Every device appears twice in both `by-path` namespaces**, once as `usb-`
   and once as `usbv2-` (`platform-fd880000.usb-usb-0:1.3:1.0` and
   `platform-fd880000.usb-usbv2-0:1.3:1.0`, both symlinking to the same
   `ttyACM10`). A correlation pass must either normalize the alias away or
   accept that one physical board yields two candidate paths.
2. **The mountpoint name is not the `by-path` name.** `ID_PATH_TAG` is the
   sanitized form: `.` and `:` both become `_`. So
   `platform-fd880000.usb-usb-0:1.3:1.0-scsi-0:0:0:0` on disk is
   `platform-fd880000_usb-usb-0_1_3_1_0-scsi-0_0_0_0` as a directory. Going from
   a mountpoint back to a device path means comparing sanitized forms, not raw
   ones — and the sanitization is lossy, since `.` and `:` map to the same
   character.

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

## Verified on hardware (hestia, 2026-09-04)

None of the following could be settled by reading code. All five were run on a
bench board; results recorded here so nobody has to re-derive them.

- **`systemd-mount` creating a nested mountpoint** — ✅ created.
  `findmnt` showed `/dev/sda1` mounted at
  `/media/klipper/BOOTSEL/by-path/platform-fd880000_usb-usb-0_1_3_1_0-scsi-0_0_0_0`,
  three levels deep, `uid=1001,gid=1001`. The shipped tmpfiles.d entry is
  belt-and-braces rather than load-bearing: `systemd.mount(5)`'s `DirectoryMode=`
  creates mountpoint parents anyway. It buys known ownership and mode, nothing
  more.
- **`ID_PATH_TAG` set for the partition** — ✅ populated.
  `udevadm test /sys/class/block/sda1` expanded the RUN line to a non-empty leaf
  (`…/by-path/platform-fd880000_usb-usb-0_1_3_1_0-scsi-0_0_0_0`), never a bare
  `by-path/`. The empty-tag fallback rule has therefore not been exercised in
  practice — it remains as a guard, not as a tested path.
- **Two boards in BOOTSEL simultaneously** — ✅ distinct paths.
  Two directories (`platform-fd800000_usb-usb-0_1_6_3_1_1_1_0-scsi-0_0_0_0` and
  `platform-fd880000_usb-usb-0_1_3_1_0-scsi-0_0_0_0`), and `bootsel_scan()`
  returned both. Note what this now produces downstream: two mounts is an
  `ambiguous` refusal, so the spec's failure #2 (a bystander board blocking a
  flash) is *not* fixed by this change — it needs the deferred port parameter.
- **Stale directory accumulation** — ⚠️ confirmed, and fixed in rule version 3.
  `--collect` reaps the transient mount *unit* and leaves the directory, so an
  empty directory per port accumulated across replugs. It was never a
  correctness problem — `bootsel_scan` gates on `INFO_UF2.TXT`, so an empty
  leftover never reads as an attached board — but the clutter is real. Version 3
  adds `ACTION=="remove"` rules that `rmdir` the leaf; `rmdir` only removes an
  empty directory, so a not-yet-completed unmount fails the call harmlessly and
  leaves things exactly as version 2 did.
- **The prefix normalization** — ✅ captured; see "Correlating a board across the
  BOOTSEL reboot" above for the measured pair and the two wrinkles it exposed
  (the `usbv2` alias, and `ID_PATH_TAG`'s lossy `.`/`:` → `_` sanitization).

## Not in scope

The BOOTSEL closed loop itself — `REBOOT_BOOTSEL`, the wait for
re-enumeration, and `settled()`'s new body. This document only removes the
obstacle that made correlation impossible. The firmware side already exists:
`REBOOT_BOOTSEL` is opcode `02` and, as of the roadrunner identity-gate work, is
deliberately ungated so a board with no valid identity can still be recovered.
