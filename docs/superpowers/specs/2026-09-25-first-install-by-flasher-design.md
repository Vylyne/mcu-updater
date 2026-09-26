# First install, gated by flashers - design

README TODO: *Support flashing new devices for other supported flashers
(currently only shows klipper firmware types).*

## Intent

The guided "Add a new board" flow offers every type in the one type list, and
whether a type can be set up from a bare board is answered by its install
family's `flashers:` list - never by its builder, and never by a chipset
prefix hard-coded in a caller.

Success looks like:

- A Roadrunner (cmake type, RP2040) can be installed from BOOTSEL through the
  wizard and the job reports the board that came back, rather than "No board
  appeared". Its `[type]` must declare `chipset: rp2040`: a bare board in
  BOOTSEL has nothing but its chipset to vouch for it (`Bootsel.supports`), and
  cmake types may leave the key empty. One that does is listed with the reason
  naming the key to add - the config line is the fix, not a code path.
- Every type appears in the wizard. One that cannot be set up says why, naming
  the line to change.
- Adding PlatformIO later means implementing one capability in the esptool
  flasher module, with no change to the agent method, the wire shape or the
  wizard.

Out of scope:

- **PlatformIO bare-board detection.** A PIO type is listed with its reason
  ("nothing on `[firmware <name>]`'s flashers: can scan for a new board") until
  `esptool` implements `CandidateScanner`.
- **CLI and TUI `add-mcu`** stay kconfig-only, because they build through
  menuconfig. They take the port-based wait (below) and refuse a non-kconfig
  type by name instead of with `unknown_type`.
- A cmake **STM32** `.bin` first image. `refuse_unbootable_first_image` reads
  `app_address` from the kconfig sidecar, which a cmake build does not write, so
  such an image is refused as `offset_mismatch`. No such tree exists today; the
  refusal is conservative, not wrong.

## Why it is kconfig-only today

Three filters, each on the wrong axis:

1. `agent/methods/flash.py` `add_mcu_start` resolves the name through
   `self.registry()`, the kconfig-only `Registry` view, so a cmake or PIO type
   is `unknown_type`. It then gates on `chipset.startswith("stm32"/"rp2040")`
   and builds artifact paths by kconfig convention.
2. `flashers/flash.py` `flash_initial_bootloader` picks DFU or BOOTSEL by
   `chipset.startswith("rp2040")`.
3. `ui/src/components/AddMcuWizard.vue` and `TargetsView.vue` filter
   `targets[]` to `provider === "kconfig_make"`, and the wizard reads the
   mechanism out of `descriptor`, which is a chipset only for kconfig types.

And one latent failure that removing them would expose: the post-write wait
(`wait_for_new_device(chipset=...)`, and the CLI's `adoptable_devices`) filters
on the by-id chipset segment. A Roadrunner enumerates as
`usb-Vylyne_Roadrunner_<serial>`, whose segment is `Roadrunner`, so a
successful install would always end in "No board appeared". docs/decisions.md
already says that segment "is not a filter".

## Design

### 1. `CandidateScanner` - a flasher capability

A new optional Protocol in `flashers/spec.py`, alongside `Flasher`, reached
through an accessor the way helper capabilities are
(`flashers.candidate_scanner(flasher) -> CandidateScanner | None`):

```python
@dataclasses.dataclass(frozen=True)
class CandidateScan:
    """What a flasher can see that it could write as a new board."""

    ready: bool
    reason: str | None          # flasher's own vocabulary: none, ambiguous, ...
    message: str | None
    devices: list[dict[str, Any]]
    #: The USB port (`usb.UsbDevice.name`, e.g. "1-1.2") of the one device a
    #: write would go to, when `ready`. None when the flasher cannot say.
    port: str | None
    #: Flasher-specific extras the wire already carries (`vid_pid`, `mounts`,
    #: `output`), merged into the RPC result unchanged.
    extra: dict[str, Any]


@runtime_checkable
class CandidateScanner(Protocol):
    name: str

    def scan_candidates(self, paths: Paths, *, reporter: Reporter) -> CandidateScan: ...
```

- `DfuUtil` and `Bootsel` implement it. The bodies of the agent's `dfu_scan`
  and `bootsel_scan` move into `flashers/dfu_util.py` and `flashers/bootsel.py`
  unchanged in behaviour; the reason constants move with them.
- The agent's `_identify_dfu` / `_identify_bootsel` stay in the agent and
  annotate `devices` after the scan: naming a tracked board is registry
  knowledge, not flasher knowledge. They read tracked serials from the type
  list rather than `self.registry()`, so a tracked cmake RP2040 is named too.
- `fw.dfu.scan` and `fw.bootsel.scan` stay on the wire as thin delegates, with
  their existing result shapes, so nothing that calls them changes.
- `Flashtool` and `Esptool` do not implement it. `Esptool` is where the PIO
  follow-up lands.

### 2. One question: which flasher sets this type up?

`flashers.first_install(entry, families) -> FirstInstall` in
`flashers/registry.py`:

```python
@dataclasses.dataclass(frozen=True)
class FirstInstall:
    fw: str                     # install family: bootloader, else application
    flasher: str | None         # None when nothing on the list can do it
    state: str | None           # the ROM state that flasher writes (DFU, BOOTSEL)
    reason: str | None          # set exactly when flasher is None
```

The rule: take the install family (below), walk its `flashers:` list in order,
and choose the first flasher that is a `CandidateScanner` **and** whose
`supports()` accepts `Device(kind=KIND_BARE, state=s, chipset=entry.chipset)`
for one of its own `states`. `flashtool` and `esptool` already refuse
`KIND_BARE`, so no builder or flasher name is compared anywhere.

Reasons, when nothing qualifies, each naming the fix:

- the type declares no `chipset:` (a cmake type may leave it empty);
- a listed flasher could write it but none of them can scan for a new board
  (PIO today);
- nothing on the list writes a bare board of this chipset - the existing
  `_no_first_install_writer` wording, which names the flasher to add.

`install_family(mcu, families)` changes to take the firmwares list
(`install_family(firmwares, families)`) so a `TypeEntry` and a `McuType` both
feed it; the bootloader-else-application rule is unchanged.

`flash_initial_bootloader` takes the chosen state from `first_install` instead
of deriving it from the chipset. Its selection through `flashers.resolve`
against a bare `Device` is unchanged.

### 3. The type list is the source

`add_mcu_start` resolves `name` through `typelist` - the `TypeEntry` carries
`chipset` and `firmwares` for every builder - and:

1. asks `first_install`; a `None` flasher is refused synchronously as
   `unsupported_chipset` with the reason as its message (the existing code,
   widened);
2. reads the image from `providers.staged(paths, name, family)` and picks it
   with `flashers.resolve` against the bare device. `no_artifact` is raised
   when nothing the chosen flasher accepts was staged; the kconfig-specific
   "rebuild with no bootloader offset" advice stays, guarded on the family's
   builder the way `_refusal_error` already guards it.
   **Why staged, here and not in the CLI:** the agent writes an image that was
   built earlier, which is exactly what `providers.staged` describes. The CLI's
   `add-mcu` builds first and hands over the files it just made, which is why
   `flash_initial_bootloader`'s comment rejects `providers.staged` - both are
   right for their caller, and the difference is recorded here so neither is
   "fixed" to match the other.
   **One consequence for kconfig RP2040 Katapult:** `build.staged` offers a
   `.uf2` only when its sidecar lists the kind, because a `.uf2` staged before
   sidecars recorded kinds may be older than the `.bin` beside it. Today's
   agent path copies whatever `.uf2` is on disk. After this change a Katapult
   built before typed artifacts is refused `no_artifact` until rebuilt once -
   the same rule `fw.flash` already applies, and the message says to rebuild;
3. runs the chosen flasher's `scan_candidates` in place of the `is_bootsel`
   branch. The DFU `dfu_serial` argument keeps its meaning and its refusals
   (`device_not_found`, `dfu_ambiguous`); BOOTSEL keeps its dead-end
   `bootsel_ambiguous`. Error codes stay `<prefix>_<reason>` as today;
4. splits `already_tracked` from `candidates` using every serial in the type
   list, not `Registry.all_serials()`.

A new read-only RPC, `fw.add_mcu.scan {name}`, runs the scan `first_install`
chose for that type and returns its report plus `flasher`. The wizard calls
this instead of choosing between `fw.dfu.scan` and `fw.bootsel.scan` itself.

### 4. The wait is keyed on the USB port

The same rule `helpers/klipper.py`'s `_wait_for_topology` and Roadrunner's
provisioning wait already follow: across a reboot, the durable key is the
physical port, not the serial the board presents.

- `CandidateScan.port` is the port of the device the write is going to, in the
  one namespace both sides can produce: `usb.UsbDevice.name` (sysfs, e.g.
  `1-1.2`). DFU reports it directly as dfu-util's `path`. BOOTSEL derives it
  from the boot ROM's block device (`/dev/disk/by-id/usb-RPI_RP2_*` ->
  `/sys/class/block/<dev>` -> `usb.device_for_sysfs_path`).
- After the write, a new device is one that was not on the bus before **and**
  whose tty resolves (`usb.device_for_tty`) to that port. No chipset, no
  firmware-name filter, `is_mcu` still applies.
- With no port (the scan could not resolve one), the wait falls back to any new
  `is_mcu` device and logs a warning saying so.
- The CLI's `add-mcu` gets the same wait through `adoptable_devices`. It does
  not scan today - `flash_initial_bootloader` finds the device itself - so it
  now asks `first_install` and, after the build and immediately before the
  write, runs the chosen flasher's `scan_candidates` exactly as the agent does,
  handing the scan's `port` to the wait. After the build, not before, because
  the user may still be fitting the jumper while menuconfig runs.

A Roadrunner therefore comes back as `RR-UNPROVISIONED-...` on its port, is
reported in `candidates`, and the wizard's existing adopt step
(`fw.serial.add`) provisions it through the helper's `Trackable` /
`Provisioner` remedy, exactly as tracking one found any other way does.

Late adoption (`.dfu-pairings.json`) is unchanged. For a Roadrunner its
BOOTSEL key will not match the provisioned serial, so it never fires - the
documented "never a wrong adoption" outcome, not a new failure.

### 5. Wire and UI

Every `targets[]` row gains an additive `first_install` object, filled from
the row's type-list entry by one function shared by the kconfig, cmake and PIO
projections in `agent/methods/status.py`:

```json
"first_install": {"fw": "katapult", "flasher": "dfu_util", "reason": null}
"first_install": {"fw": "knomi", "flasher": null,
                  "reason": "nothing on [firmware knomi]'s flashers: can scan for a new board"}
```

Additive: no `API_VERSION` bump. An older UI ignores the field and keeps its
kconfig filter.

**A newer UI will meet an older agent.** The release order promotes the UI to
stable first, so there is a guaranteed window where this wizard talks to an
agent with neither `first_install` nor `fw.add_mcu.scan`. The wizard therefore
uses the new path only when `hasCapability("fw.add_mcu.scan")` and the rows
carry `first_install`; otherwise it falls back to today's behaviour exactly -
the `kconfig_make` filter, `descriptor` parsing, `fw.dfu.scan` /
`fw.bootsel.scan`. The fallback is kept until a later release removes it, and
has its own spec test.

`AddMcuWizard.vue`:

- lists every target, not only `kconfig_make`;
- takes the mechanism from `first_install.flasher` rather than parsing
  `descriptor`, and scans through `fw.add_mcu.scan`;
- shows `first_install.reason` for a type with no flasher instead of the
  hard-coded "only STM32 and RP2040" sentence;
- names what it will write (`first_install.fw`) on the button - the comment
  that declined a wire field just for this label is superseded;
- keeps one deliberate flasher-specific branch: when `first_install.flasher`
  is `dfu_util` and the scan is `ambiguous`, it offers the pick-a-serial
  choice, because only DFU can target one of several boards (`dfu_serial`).
  That is a property of the mechanism, documented as such, not drift from
  the "no caller branches" rule.

`TargetsView.vue` shows "Add new board…" when any target has a
`first_install.flasher`.

## Error handling

Unchanged codes, widened sources: `unknown_type` now means "not in the type
list"; `unsupported_chipset` keeps its name for wire compatibility, now
carries `first_install`'s reason as its message, and adds `data.fw` and
`data.flashers` (the install family and its list). `fw.add_mcu.scan` reports rather than raises, like the two
scans it routes to, with `reason: "no_scanner"` and the same message when the
type has no flasher.

## Testing

- `first_install`: kconfig STM32 -> `dfu_util`; kconfig RP2040 -> `bootsel`;
  cmake RP2040 -> `bootsel`; cmake with empty chipset -> reason names
  `chipset:`; PIO -> reason names the scan; family whose list lacks a bare
  writer -> reason names the line to add. List order respected when both could
  apply.
- `add_mcu_start` for a cmake type, through the fake bus: resolves, reads the
  staged `.uf2`, writes, and a device appearing on the scanned port with a
  non-rp2040 by-id segment lands in `candidates`.
- Port-keyed wait: a new device on another port is not a candidate; no port
  falls back with a warning.
- `fw.dfu.scan` / `fw.bootsel.scan` results byte-identical before and after
  the move (existing tests keep passing unmodified).
- `tests/test_ui_contract.py` covers `first_install` on every provider's rows.
- `AddMcuWizard.spec.ts`: a cmake target is listed and scans via
  `fw.add_mcu.scan`; a PIO target shows its reason; no `descriptor` parsing.
  Against an agent without `fw.add_mcu.scan` or `first_install`, the old
  kconfig-only behaviour, unchanged.
- CLI `add-mcu`: the scan runs between the build and the write, and its port
  reaches the wait.
- kconfig RP2040 Katapult whose sidecar does not list `uf2`: `no_artifact`
  telling the user to rebuild.
- Mutation specs anchored in `add_mcu_start`, `flash_initial_bootloader` and
  the moved scan bodies (`add-mcu.json`, `single-write-path.json`,
  `bootsel-*.json`, `dfu-pairings.json`, others found by grep) are re-anchored
  in the commit that moves their line, and renamed if the rule widened.
- Bench, BOOTSEL: one Roadrunner from bare to tracked through the wizard, on
  the bench board only.

## Docs

- `docs/agent-api.md`: "Setting up a brand-new board" and `fw.add_mcu.start`
  (type list, flasher-chosen mechanism, port-keyed wait, refusal table);
  new `fw.add_mcu.scan`; `targets[].first_install`.
- `docs/decisions.md`: "First install is gated by flashers" - the builder and
  a chipset prefix are not the question, `CandidateScanner` is; and the
  staged-vs-just-built distinction from section 3.
- `README.md`: tick the TODO; the Features line for guided setup names cmake.
