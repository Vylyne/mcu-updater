# Uniform targets: one row shape per builder, and no display vocabulary on the wire

Date: 2026-09-29. Status: approved; identity provisioning (goal 5) added and approved the same day.
Follows the `platformio` flasher refactor (PRs #13, #14). Closes the README
TODO entry marked **NEXT**.

## Goal

Every builder's `targets[]` row is built the same way and says the same kinds of
things, and nothing a firmware alone knows reaches the core, a generic seam or
the wire. Concretely:

1. Remove the wire outputs that still expose the per-builder intermediate
   objects `targets[]` was projected from (`fw.device.list`, `fw.flash_all`'s
   `boards`/`displays`, `fw.flash`'s `displays`, `fw.target.get`'s `screens`,
   the `display_flash` job kind).
2. Replace `targets[].extra` - today a per-builder object (`DisplayExtra |
   CmakeExtra` in the UI) - with a shared `source`, a seam-contributed
   `extras` list, and a `devices_note`, so a new builder, flasher or helper
   adds entries, never a new wire type.
3. Move PlatformIO device listing, which is knomi_serial code living in
   `status.py`, behind a helper capability.
4. Remove "display"/"screen" as a description of a device from everywhere but
   firmware-specific code, and keep it removed with a guard test.
5. Route identity provisioning (`fw.roadrunner.provision`/`.clear`) through
   the helper's `Provisioner` capability under generic method names and
   generic error codes, so the one API bump carries every breaking rename.

`API_VERSION` goes from 4 to 5. The UI is released alongside it.

## The rule this applies

"Display" and "screen" may describe a device only in firmware-specific code
whose firmware is for a display: today `helpers/knomi_serial.py` and
`discovery/knomi_serial/`. Never in the core, never in a generic seam
(providers such as `pio`, flashers, generic discovery), never on the wire.
Text a firmware-specific seam produces for a person to read (a
`devices_note`, an `extras` label) is firmware-specific and may use them.

The same split governs firmware names on the wire generally: **machine-readable
names - methods, error codes, keys - are generic; human text a firmware-specific
seam produces may name its firmware.** So `roadrunner_timeout` becomes
`reenumerate_timeout`, while its message, "Roadrunner did not re-enumerate with
the expected identity", stays.

## Background: what is there today

- `targets[]` already carries every fact the legacy per-builder objects do;
  `test_every_fact_in_the_old_keys_survives_the_projection`
  (`tests/test_agent_targets.py`) pins it. The UI renders from `targets[]`
  alone: it reads none of `displays`, `screens`, `boards` or `display_flash`,
  and builds its fleet-flash confirmation from `targets[]`
  (`ui/src/api/bulk.ts`).
- Internally each builder still builds a rich per-type object -
  `type_status` (kconfig_make), `pio_status` (platformio), `cmake_status`
  (cmake) - and `targets()` projects them. That split stays: builders know
  different facts. What goes is the wire exposing the intermediates.
- **Bug:** the agent submits PlatformIO flashes as kind `display_flash`, which
  the UI's `JobKind` union (`ui/src/api/jobs.ts`) does not name, so
  `cancelIsImmediate("display_flash")` is `true` and the panel tells the user
  a PlatformIO flash cancels immediately. The agent correctly defers it
  (`jobs.IMMEDIATELY_CANCELLABLE`). No write is interrupted; the message is
  wrong.
- `targets[].extra` today:

  | Key | Sent by | UI reads it | What it really is |
  |---|---|---|---|
  | `source`, `source_version`, `source_dirty` | cmake, platformio | no | a source-tree fact every builder has |
  | `flashable` | cmake | no | redundant with `actions[].blocked` |
  | `module_version` | platformio | yes (caption) | knomi_serial's klippy module version - a helper fact |
  | `klipper_section`, `reachable` | platformio | yes (empty-row hint) | why `devices[]` is empty |

- `device_list` (`agent/methods/status.py`, ~250 lines) reads the
  knomi_serial klippy module's printer objects, knows its fields, and falls
  back to knomi_serial's watcher map. `pio_status` and the PlatformIO flash
  selection (`agent/methods/flash.py`) both take their device list from it.
- The PlatformIO row reports `"firmware": null`, on the stale grounds that
  PlatformIO types have no `[firmware ...]` family. They do.

## 1. Device listing is a helper capability

A new capability Protocol in `helpers/spec.py`, modelled on `ImageReporter`:
the helper declares a Klipper prefix and interprets the object values; the
core owns the Moonraker query.

```python
@runtime_checkable
class DeviceLister(Protocol):
    """Lists a family's configured devices from Klipper's printer objects."""

    name: str
    #: The Klipper section prefix whose objects are this firmware's devices.
    klipper_prefix: str

    def from_klipper(self, section: str, values: Mapping[str, Any]) -> ListedDevice:
        """One device from its printer object. `section` is the object's own
        capitalisation, which is what printer.cfg says."""
        ...

    def extras(self, devices: Sequence[ListedDevice]) -> list[Extra]:
        """Facts about one type's devices worth showing on its row."""
        ...

    def devices_note(self, *, reachable: bool) -> str:
        """Why a type of this family lists no devices. Called only when it lists none."""
        ...
```

Reached through an accessor, `helpers.device_lister(helper)`, like the other
capabilities. Nothing compares a helper's name.

`ListedDevice` is a frozen dataclass of what the core uses, and nothing else:

| Field | Meaning |
|---|---|
| `id` | what the device is addressed by (its configured id, else its configured path) |
| `section` | its printer object, `"knomi_serial t0_knomi"` |
| `label` | its short name, `"t0_knomi"` |
| `configured_id` | the id printer.cfg names, or `None` |
| `reported_id` | the hardware id the device reports; the flash log's `hwid:` key and the source of `confidence` |
| `configured_path` | the port printer.cfg or discovery gave, or `None` |
| `resolved_path` | `configured_path` resolved, or `None` when it does not exist |
| `present` | `resolved_path is not None` |
| `version` | the firmware version the device reports, or `None` |
| `compatible` | `False` when the device declares it cannot work with the host (knomi's `protocol_match`); drives the `protocol_mismatch` state. `None` is unknown. |
| `raw` | the object's values, for the helper's own `extras()`; the core never reads it |

The implementation may add a field the core already uses where the table
missed one. It must not add a firmware's field.

**What moves.**

- `status.py`'s `device_list` becomes a generic loop: for each PlatformIO
  type, find its family's helper, ask it for a `DeviceLister`, query that
  prefix's objects, and collect `ListedDevice`s plus one `reachable` flag.
  Every knomi-specific line (the klippy field names, the `device_id:`/
  `serial:` addressing rule, the eFuse-MAC comments) moves into
  `KnomiSerialHelper.from_klipper`.
- `platformio_status` (renamed from `pio_status`) and the PlatformIO flash
  selection consume `ListedDevice`, not raw dicts.
- `_watcher_map` is deleted: only `fw.device.list` used it. The flash path
  already reads knomi's map through `Identifier`.
- The `"knomi_serial"` fallback prefix for a host with no PlatformIO types is
  deleted: it only served `fw.device.list`. No PlatformIO types, no query.
- A PlatformIO type whose family names no helper that is a `DeviceLister`
  lists no devices, and its `devices_note` says so, naming the family:
  `No devices are listed for this type: [firmware <fw>] names no helper that
  can list them.` (No such config exists today; hestia and elpis both name
  `helper: knomi_serial`.)
- `PioType.klipper_section` keeps being derived from the helper's prefix. It
  leaves the wire with `extra`.
- The ~20 knomi fields only `fw.device.list` carried (`tool`, `page_count`,
  `config_crc`, `filament_color`, `sleep_state`, ...) leave the wire with no
  replacement. The UI never showed them. Per-device `extras` would be an
  additive change later.

**Left as is.** `Identifier` still returns knomi's own `WatcherDevice`; its
docstring says to generalise it when a second identifier exists. Its
justification ("`fw.device.list` already puts it on the wire") is reworded.

## 2. The wire, `API_VERSION` 5

### A `targets[]` row

| Key | Change |
|---|---|
| `provider`, `name`, `descriptor`, `artifact`, `profile`, `needs_flash`, `devices`, `actions`, `first_install` | unchanged |
| `firmware` | a platformio row names its family (`"knomi_serial"`), not `null` |
| `source` | **new**, every row: `{path, version, dirty}` - the application family's source tree; `version` is the string the builder's staleness check compares; `null` when there is no checkout |
| `extras` | **new**, every row, a list (often empty); replaces `extra` |
| `devices_note` | **new**, every row, `string \| null`; set exactly when `devices` is empty |
| `extra` | **removed** |

`source` costs a `fw.status` poll at most one read per firmware family,
reusing a read the artifact check already makes wherever one exists. A
kconfig_make type's source is its application family's tree (not its
bootloader's).

### An `extras` entry

```json
{"seam": "builder", "name": "cmake", "key": "…", "label": "…", "value": "…"}
```

- `seam`: `"builder"`, `"flasher"` or `"helper"`. `name`: that seam's
  registered name.
- `key`: stable, machine-readable. `label`: for a person. `value`: a JSON
  scalar (string, number, boolean or null).
- Ordered by seam (builders, then flashers, then helpers), then as the seam
  returned them.
- The UI renders `label: value` and never branches on `key`, `seam` or `name`.
- One dataclass, `Extra`, in a neutral module (`mcu_updater/extras.py`), with
  `to_json()`. Seams produce `Extra`s; `targets()` collects them.

At launch the only producer is knomi_serial's `DeviceLister.extras`:
`{"seam": "helper", "name": "knomi_serial", "key": "module_version",
"label": "Module", "value": <first non-null module_version its devices report>}`,
and no entry when none reports one. The cmake `source*` fields move to
`source`; `flashable` is dropped.

### `devices_note`

The three sentences the UI hardcodes today (`TargetRow.vue`'s
`noDevicesHint`) become the agent's:

- kconfig_make and cmake: `No serial devices are tracked for this type yet.`
- a PlatformIO family with a `DeviceLister`: whatever its `devices_note` says.
  knomi_serial keeps today's two: `Could not reach Klipper to check for
  screens.` and `No screens found under [knomi_serial ...].`
- a PlatformIO family without one: the sentence in section 1.

### Removed or changed

| Where | Today | After |
|---|---|---|
| `fw.device.list` | method | gone: not dispatched (answers `-32601`), not in `capabilities` |
| `fw.flash_all` response | `{job_id, job, boards, displays}` | `{job_id, job}` |
| `fw.flash`, PlatformIO route | `{job_id, job, displays}` | `{job_id, job}`, as the board route |
| PlatformIO flash job | kind `display_flash`, params `{name, count}` | kind `flash`, params `{name, port?}` - the address the call named, as the board route echoes `serial`/`uuid` |
| `fw.target.get`, `provider: "platformio"` | `target.screens` | `target.devices` (a list of `ListedDevice` plus the verdict fields `platformio_status` adds) |
| `fw.target.get` unknown platformio name | `no such display: <name>` | `no such platformio type: <name>`; code stays `unknown_target` |

The job results' `flashed[]` and `failures[]` are unchanged; they already use
the uniform `{type, id, flasher}`.

### Identity provisioning

`fw.roadrunner.provision` and `fw.roadrunner.clear` become
`fw.identity.provision` and `fw.identity.clear`. Params (`{serial}`), return
(`{serial, prior_serial, state}`), and gating (`HARDWARE_METHODS`: off by
default, withheld from a read-only agent) are unchanged. `fw.identity.*`, not
`fw.device.*`, so it does not read as a sibling of the removed `fw.device.list`.

**Routing.** The call names no family - the board is untracked, often before any
type for it exists - so the core asks every registered helper
(`helpers.registry.HELPERS`) that is a `Provisioner` for
`identity_state(serial)`:

- exactly one helper must answer `"unprovisioned"` (provision) or
  `"provisioned"` (clear);
- none, or more than one, refuses with `not_provisionable`, `data.helpers`
  naming any claimants;
- then the untracked check (`device_tracked`), then the op lock (`provision
  <serial>` / `clear <serial>`), then `helper.provision(paths, serial)` /
  `helper.clear(paths, serial)`.

`status.py`'s own copy of find-then-provision is deleted; the helper's
`provision` is the one path, as it already is for `fw.serial.add`.

**Capability.** `Provisioner` gains two methods:

```python
def identity_state(self, serial: str) -> Literal["unprovisioned", "provisioned"] | None:
    """Whether `serial` is this firmware's identity, and in which state.
    A string test, like `is_trackable`; never opens a port."""

def clear(self, paths: Paths, serial: str) -> str:
    """Return the board answering to `serial` to its unprovisioned identity;
    return the serial it came back under. The caller holds the op lock."""
```

One capability, not two: giving an identity and taking it back are one firmware
feature. Split it when a firmware has only one half.

**Codes.** Every wire code becomes generic. `discovery/roadrunner.py` raises the
new codes (its `_TRANSIENT_READINESS_CODES` follows); `RoadrunnerError` keeps its
name, being firmware-specific code.

| Old | New |
|---|---|
| `roadrunner_tracked` | `device_tracked` |
| `roadrunner_no_candidate` | `device_not_found` |
| `roadrunner_ambiguous` | `device_ambiguous` |
| `roadrunner_invalid_probe` | `identity_unconfirmed` |
| `roadrunner_helper` | `helper_failed` |
| `roadrunner_timeout` | `reenumerate_timeout` |
| `roadrunner_mismatch` | `identity_mismatch` |
| `roadrunner_unprovisioned` (`UnprovisionedSerialError`, also from `fw.serial.add`) | `serial_unprovisioned` |
| - | `not_provisionable` (new) |

`RoadrunnerHelper.is_trackable`'s reason text names `fw.identity.provision`.

**UI, this branch.** The store's `provisionRoadrunner`/`clearRoadrunner`
become `provisionIdentity`/`clearIdentity`, calling the new methods; BusPanel's
capability checks follow. BusPanel still decides which rows get the buttons from
`isRoadrunnerDevice` and the serial shape. Replacing that with per-device
actions from the helper is additive (no bump) and becomes a README TODO entry.

## 3. Renames, vocabulary, persisted state, docs

**Code.** Outside the two knomi_serial paths:

| Where | Examples | Becomes |
|---|---|---|
| agent methods | `pio_status`, `pio_types`, `_screen_id`, `_screens_to_flash`, `display`/`screen` locals, docstrings, comments | `platformio_status`, `platformio_types`, `_device_id`, `_platformio_to_flash`, `entry`/`device` |
| `providers/pio.py` | module docstring "ESP32 displays: PlatformIO builds, esptool uploads" | PlatformIO builds for any env |
| generic seams | a knomi screen as the worked example (`flashers/spec.py`, `discovery/byid.py`, ...) | generic ("a USB-serial bridge such as a CH340") |
| `cli.py` | `display` variables, any user-facing "screen" text | `device` |
| UI | `DisplayExtra`, "every board and screen", comments naming `fw.device.list` or screens | removed with `extra`, or "device" |
| tests | `test_agent_displays.py`, `test_agent_display_jobs.py`, test names and docstrings | `test_agent_platformio_devices.py`, `test_agent_platformio_flash.py`, "device" |

**Guard.** A hygiene test scans `src/` and `ui/src/` for `\bdisplays?\b` and
`\bscreens?\b`, outside `helpers/knomi_serial.py` and
`discovery/knomi_serial/`. It ignores the CSS `display:` property and checks an
explicit allowlist, each entry `(path, the matched line's text, reason)`, for
senses that are not a device (a kconfig menu "screen", "stay on screen").
Adding an entry is a reviewed decision, not a way round the rule.

**Persisted state.** The PlatformIO build sidecar moves from
`data_dir/displays/<env>.build.json` to `data_dir/platformio/<env>.build.json`
(`Paths.platformio_sidecar`). No migration: each PlatformIO type reports
`no_provenance` until its next build. The old directory is left on disk
(harmless); the release note says it can be removed by hand.

**Docs.**

- `docs/agent-api.md`: a version 5 entry in the changelog at the top;
  `api_version` 5; `fw.device.list` and "The watcher's map" sections deleted;
  the method table updated; `targets[]` documented with `source`, `extras`,
  `devices_note` and the platformio `firmware`; `fw.flash_all`/`fw.flash`
  responses and the job kind updated; "ESP32 displays" becomes "PlatformIO
  devices", with knomi_serial as a clearly labelled example of a
  `DeviceLister`.
  The `fw.roadrunner.*` section becomes `fw.identity.*`, with the routing
  rule, the code table, and the "retained for wire compatibility" wording
  about `roadrunner_unprovisioned` removed.
- `docs/decisions.md`: the "vocabulary still survives on the wire" paragraph
  in "The PlatformIO flasher is `platformio`" is replaced by the rule itself;
  a new entry, "`targets[]` extras are contributed by a seam, never typed per
  builder"; "Identity is a helper capability" gains the `DeviceLister`
  sibling. The trackability entry's "renaming it is a separate wire decision"
  (`roadrunner_unprovisioned`) records that this is that decision, and the
  generic-names rule; the provisioning-gate entry names `fw.identity.provision`
  and `serial_unprovisioned`.
- `README.md`: the **NEXT** TODO entry closed and a Features line checked;
  "ESP32 displays" reworded the same way; the stale `[type ...]`/`[display
  ...]` precedence line fixed; an upgrade note that PlatformIO types need one
  rebuild; the provisioning paragraph names `fw.identity.*`; a TODO entry for
  per-device helper actions replacing the UI's Roadrunner detection.
- `docs/layout.md`: checked for the renamed test files and the sidecar path;
  its `fw.roadrunner.*` mention renamed.
- `tracking.py`'s `add_serial` docstring: the method and code names.

## 4. UI and release

The UI does not support a version 4 agent. The release ships the UI and the
agent together (below), so users take both at once; the new fields are
required in the TS types, with no fallback reader for `extra`.

- `ui/src/api/targets.ts`: add `Extra`, `TargetSource`, and required
  `source`, `extras`, `devices_note` on `Target`; remove `DisplayExtra` and
  `CmakeExtra`.
- `TargetRow.vue`: `noDevicesHint` becomes `target.devices_note`; the
  `moduleVersion` caption becomes a loop over `extras` rendering
  `label value`; comments naming `fw.device.list` or screens go.
- `BulkDialog.vue`: "every board and screen" becomes "every device".
- `ui/src/api/agent.ts`: `SUPPORTED_API_VERSION = 5`.
- Specs: fixtures move to the new shape; `TargetRow` specs cover `extras`
  rendering and `devices_note`.

**Release sequence.** The plan ends at a PR into `develop`. Every step after
it needs Vi's go-ahead:

1. Merge the PR into `develop`. Bench check on elpis (bench only): the panel
   renders; the knomi row shows its `Module` extra; a knomi flash through the
   UI works; cancelling it says "between devices"; one knomi rebuild restores
   provenance.
2. Release commit on `develop` bumping `__version__`; tag `vX.Y.Z`;
   `ui-release.yml` publishes a prerelease.
3. Soak on the beta channel.
4. **Promote the UI release first:** `gh release edit vX.Y.Z --prerelease=false`.
5. **Then** PR `develop` → `main`, at nearly the same moment.

## 5. Testing

**The seam is generic.**

- Core tests (`test_agent_platformio_*`) run against a fake `DeviceLister`.
  No core test imports knomi_serial.
- knomi_serial's field parsing and addressing rule (`device_id:` before
  discovery, `serial:` sections, reported-id lowercasing, `protocol_match`
  → `compatible`, module too old to report anything) are tested against
  `KnomiSerialHelper` directly.
- `test_every_fact_in_the_old_keys_survives_the_projection` covers the
  `ListedDevice` and `platformio_status` facts, plus `source`, `extras` and
  `devices_note`.

**The wire.**

- Every row of all three providers carries `source`, `extras` and
  `devices_note`; none carries `extra`; `devices_note` is non-null exactly
  when `devices` is empty.
- Every `extras` entry has a valid `seam` and a scalar `value`.
- A platformio row's `firmware` is its family.
- `fw.device.list` is in neither the dispatch table nor `capabilities`.
- `fw.flash_all` and the PlatformIO `fw.flash` return exactly
  `{job_id, job}`; the PlatformIO job's kind is `flash`.
- `fw.target.get` for platformio returns `devices`, not `screens`.
- `fw.roadrunner.*` are in neither the dispatch table nor `capabilities`;
  `fw.identity.*` are, under the same gate.
- No wire error code starts with `roadrunner_`.

**Identity routing** runs against fake provisioners: no claimant; two
claimants; a tracked serial (`device_tracked`, nothing written); provision
asked of a serial its helper calls `"provisioned"` (`not_provisionable`), and
clear asked of an `"unprovisioned"` one. The existing Roadrunner and
provision-on-track tests move to the new names and codes.

**The contract** (`tests/test_ui_contract.py`), both halves in one commit:

- `SUPPORTED_API_VERSION == API_VERSION == 5`.
- **New:** every job kind the agent can submit (`runner.submit("<kind>"`
  literals in `src/`) is in the UI's `JobKind` union.
- **New:** the UI's `DEFERRED_CANCEL_KINDS` is exactly `JobKind` minus
  `jobs.IMMEDIATELY_CANCELLABLE`.

These two would have caught the `display_flash` bug.

**The vocabulary guard** (section 3), in the hygiene tests.

**Mutation specs.** Before rewriting any line, grep `scripts/mutations/` for
it. A spec anchored on a rewritten line is re-anchored in the same commit, and
renamed if the rule it proves widened. New guards get a spec: the `compatible`
→ `protocol_mismatch` mapping, the reachable/unreachable `devices_note`
choice, and identity routing's "exactly one claimant in the right state".
Sweeps run one spec at a time, with the hygiene test after each.

**Gate.** pytest on the 3.11 floor venv; ruff; mypy; the line-ending check;
the UI's vitest, type-check and build.

## Inputs the tests may not reach

For the plan's Review Focus:

- Klipper unreachable: the knomi row lists no devices, carries the
  unreachable `devices_note`, and nothing raises.
- A `device_id:` section before discovery finds it: listed with
  `present: false`, its `id` the configured id, `resolved_path` null.
- A family with a `DeviceLister` and no sections in printer.cfg: the
  reachable `devices_note`.
- Two PlatformIO types sharing one Klipper prefix: each lists only its own
  sections, and the prefix is queried once.
- A sidecar still at `data_dir/displays/`: the type reports `no_provenance`,
  not an error.
- `fw.serial.add` withholding provisioning (read-only or flashing off): refuses
  with `serial_unprovisioned`, the helper's reason as message, nothing written.

## Out of scope

- PlatformIO devices joining the inventory ("Presence comes from the
  inventory"). They still come from Klipper's printer objects, now through the
  helper.
- Generalising `Identifier`'s `WatcherDevice` return type.
- Per-device `extras`, and per-device helper actions (the UI's Roadrunner
  detection stays until then; README TODO).
