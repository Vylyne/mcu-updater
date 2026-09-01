# Agent API

The contract between `mcu-updater` (Python) and the Mainsail panel
(TypeScript). Both sides are hand-written, so **this file is the single source of
truth** — and `tests/test_agent_methods.py` is what stops them drifting.

- Agent name: `mcu_updater` — a protocol identifier, deliberately unchanged
  when the project was renamed to `mcu-updater`; the panel matches on it.
- `api_version`: **4**

  Bumped only when a field is *removed* or changes meaning; additions do not
  need one, since a panel that has never heard of a key simply does not read it.
  Version 2 removed `fw.status`'s top-level `types` and `displays` (use
  `fw.type.list` and `fw.device.list`, which `targets[]` is projected from), and
  `screens[].mac`/`flashed_at`/`moved_from`/`moved_at` and
  `targets[].extra.moved`, which went with the per-port identity tracking - see
  "Which screen is on which port is not tracked" below. Version 3 removed
  `fw.display.list` and `fw.display.build` (use `fw.device.list` and `fw.build`,
  which already did the same work) and `targets[].kind` (use
  `targets[].provider`, `"kconfig_make"` | `"platformio"`, which already said
  the same thing). Version 4 makes USB MCU serials canonical by removing the
  terminal `-if00` suffix from the hardware serial; the full `/dev/serial/by-id`
  path remains the transport address.
- Every planned capability has shipped: build, flash, bulk build/flash/update,
  registry and settings editing, Kconfig in the browser, and DFU setup of a new
  board. The flashing ones stay behind `enable_flashing`, off by default.

**Gate controls on `capabilities` from `fw.ping`, not on `phase`.** An agent
without a job runner does not register or advertise the job methods at all, so a
newer panel talking to an older agent must hide its build buttons rather than
offer something that returns `-32601`.

## Transport

The agent connects to `~/printer_data/comms/moonraker.sock` and identifies itself:

```json
{"jsonrpc": "2.0", "id": 1, "method": "server.connection.identify",
 "params": {"client_name": "mcu_updater", "version": "0.9.0",
            "type": "agent", "url": "https://github.com/Vylyne/mcu-updater"}}
```

All four params are required. No `api_key`/`access_token` — unix socket
connections are pre-authenticated. The reply is `{"connection_id": <int>}`.

Each message on the socket is UTF-8 JSON **terminated by an ETX byte (`0x03`)**,
not a newline. One read may contain several messages; one message may span
several reads.

After identifying, the agent registers every method name with
`connection.register_remote_method`. **Registrations are per-connection** and are
dropped when the socket closes, so they are re-sent on every reconnect.

### Calling a method

From a front end, over Mainsail's existing websocket:

```ts
this.$socket.emit('server.extensions.request', {
    agent: 'mcu_updater',
    method: 'fw.status',
    arguments: {},
})
```

Or over HTTP: `POST /server/extensions/request` with the same body.

> ⚠️ **Moonraker's `call_method_with_response` has no timeout.** If the agent
> fails to answer, the caller's HTTP request never completes. The agent
> guarantees exactly one response per request, and every method returns in well
> under a second. Clients should still arm their own timeout (the panel uses 15s)
> so a wedged agent can't leave a spinner running forever.

### Errors

```json
{"jsonrpc": "2.0", "id": 9, "error": {
    "code": -32000,
    "message": "MCU type 'nope' does not exist.",
    "data": {"code": "unknown_type", "message": "...", "data": {"known": ["bttebb36"]}}}}
```

Switch on `error.data.code`, never on the message text. Those codes are stable
API; the prose is not. Most come from `errors.py`: `config_corrupt`,
`unknown_type`, `invalid_type_name`, `duplicate_type`, `unknown_serial`,
`ambiguous_serial`, `serial_tracked_elsewhere`, `source_missing`,
`no_saved_config`, `build_failed`, `tty_required`, `flash_failed`,
`device_not_found`, `bootloader_timeout`, `ambiguous_dfu`,
`dfu_permission_denied`, `bootsel_not_mounted`, `tool_missing`,
`unsupported_chipset`, `service_control`, `flashing_disabled`, `busy`,
`print_in_progress`, `cancelled`, `profile`, `profile_not_found`,
`profile_customised`, `offset_mismatch`, `kconfig`, `no_session`. A few are
built inline at the call site rather than from a typed exception -
`no_artifact`, `nothing_to_do`, `unknown_job` (`fw.job.get`/`fw.job.cancel`),
`unknown_target` (`fw.target.get` - one code for either provider, deliberately;
an unknown MCU name through `fw.artifacts` still reports `unknown_type`
because that path raises `UnknownTypeError` directly, but `fw.target.get`
pre-checks the name against both registries itself so it never does), the
`dfu_<reason>` family (`dfu_none`, `dfu_no_tool`, `dfu_permission_denied`,
`dfu_ambiguous`) `fw.add_mcu.start` derives from `fw.dfu.scan`'s own `reason`,
and the `bootsel_<reason>` family (`bootsel_none`, `bootsel_not_mounted`,
`bootsel_ambiguous`) it derives from `fw.bootsel.scan`'s the same way -
documented where each is raised rather than repeated here.

JSON-RPC codes: `-32601` unknown method, `-32602` bad params, `-32000`
application error (see `data.code`), `-32603` internal.

## Methods

| Method | Arguments | Returns |
| --- | --- | --- |
| `fw.ping` | — | version/capability handshake |
| `fw.status` | — | everything the panel needs, in one call |
| `fw.type.list` | — | `{types: [TypeStatus]}` |
| `fw.type.add` | `name`, `chipset` (required), `firmware?`, `<fw>_extra_args?`, `<fw>_extra_repos?`, `<fw>_makefile_patches?`, `katapult_extra_args?`, `katapult_installed?` | `{name, chipset, firmware, warnings?}` — declares a board model, no hardware required; `<fw>` is `klipper` or `katapult` |
| `fw.type.update` | `name` (required), any of the `fw.type.add` fields | `{name, chipset, firmware, warnings}` — only the keys supplied are touched; `<fw>` ranges over the type's own `firmware:` list |
| `fw.type.remove` | `name` (required), `force?` | `{name, removed_serials, kept_config_dir}` — refuses while boards are still tracked unless forced |
| `fw.target.get` | `name`, `provider` (required) | `{provider, target}` — one `targets[]` entry's full detail |
| `fw.bus.scan` | `only_untracked?`, `chipset?` | `{devices: [BusDevice]}` |
| `fw.bus.ignore` | `serial` (required) | `{serial, ignored: true}` — hide a bus device from the "new board?" flow; idempotent, flag not filter |
| `fw.bus.unignore` | `serial` (required) | `{serial, ignored: false}` — reverse `fw.bus.ignore`; idempotent |
| `fw.dfu.scan` | — | `{devices, count, ready, reason, message}` — read-only |
| `fw.bootsel.scan` | — | `{devices, count, mounts, mount_count, ready, reason, message}` — read-only |
| `fw.canbus.scan` | — | `{interfaces, devices, failures, count, message}` — read-only, run only when called |
| `fw.canbus.ignore` | `uuid` (required) | `{uuid, ignored: true}` — hide every sighting of a CAN UUID from the "new board?" flow; idempotent, flag not filter |
| `fw.canbus.unignore` | `uuid` (required) | `{uuid, ignored: false}` — reverse `fw.canbus.ignore`; idempotent |
| `fw.add_mcu.start` | `name`, `dfu_serial?` (STM32 only) | `{job_id, job, dfu_serial, bootsel_id}` — **off by default** |
| `fw.artifacts` | `name` (required) | `{<fw>: Artifact, ...}`, one key per family the type declares |
| `fw.settings.get` | — | `{settings: Settings}` |
| `fw.settings.set` | `settings` (required, non-empty) | `{settings: Settings, changed: [key]}` — only the `SETTABLE` keys |
| `fw.serial.add` | `name`, `serial` (required) | `{name, serial, added, chipset}` — track a bus device under an existing type |
| `fw.serial.remove` | `name`, `serial` (required) | `{name, serial, removed}` — untrack a serial from a type; non-destructive, keeps its firmware and saved config |
| `fw.canbus.add` | `name`, `uuid` (required) | `{name, uuid, added, chipset}` — track a CAN-addressed board under an existing type; parallel to `fw.serial.add`, not an overload of it |
| `fw.canbus.remove` | `name`, `uuid` (required) | `{name, uuid, removed}` — untrack a CAN uuid from a type; non-destructive, same as `fw.serial.remove` |
| `fw.build` | `name`, `fw`, `jobs?`, `clean?`, `reseed?` | `{job_id, job}` — returns immediately |
| `fw.flash` | `serial\|port\|uuid`, `name?`, `force?` | `{job_id, job}` — **off by default**, see below |
| `fw.build_all` | `fw?`, `scope?` | `{job_id, job, types, builds, skipped}` — builds only, touches no board |
| `fw.flash_all` | `scope?`, `name?`, `force?` | `{job_id, job, boards, displays}` — **off by default** |
| `fw.update_all` | `scope?`, `name?`, `force?` | `{job_id, job, types}` — **off by default** |
| `fw.device.list` | — | `{displays, reachable, watcher}` — read-only |
| `fw.job.get` | `job_id?`, `log_from?` | `{job, log, log_from, log_next, log_dropped}` |
| `fw.job.cancel` | `job_id?` | `{cancelling, immediate}` |

### `fw.ping`

```json
{"api_version": 4, "version": "0.9.0", "dry_run": false, "enable_flashing": false,
 "phase": 1, "capabilities": ["fw.artifacts", "fw.bus.scan", "..."],
 "host": {"nproc": 4, "python": "3.13.5",
          "config_dir": "/home/biqu/printer_data/config/mcu-updater",
          "data_dir": "/home/biqu/printer_data/mcu-updater"},
 "now": 1785412345.6}
```

The panel should refuse to render if `api_version` exceeds what it knows, and use
`capabilities` to decide which controls to show — that is how a Phase-1 agent and
a Phase-3 panel coexist without either lying to the user.

This is not belt-and-braces. A panel built against version 1 reads
`targets[].extra.moved` as `extra?.moved.length`, where the optional chain guards
`extra` and not `moved` — so against a version-2 agent it does not degrade, it
throws. Refusing to render is what turns that into a message telling somebody to
update the panel.

### `fw.status`

```json
{"targets": [Target], "firmware_families": [Family],
 "kconfig_available": {"klipper": true, "katapult": true},
 "bus": [BusDevice],
 "job": null, "recent": [],
 "locked_by": null,
 "klipper_service": "active",
 "printing": false,
 "idle_state": "Ready",
 "settings": {...},
 "read_only": true}
```

`targets` is `TypeStatus` and `DisplayStatus` said in one shape - see below.
Those two originals are not embedded here; fetch one with
`fw.target.get {name, provider}` when a caller needs the full per-target
detail `targets` projects away (extra_args, makefile_patches, extra_repos,
serial-by-serial version info). `provider` is required alongside `name`,
not inferred - nothing stops an MCU type and a display sharing a name across
their separate config files, which is exactly why a client keys a target row
on `provider:name` rather than `name` alone. The response is
`{provider, target}`, where `target` is the same per-item shape as the
matching entry in `fw.type.list`'s `types[]` (`provider: "kconfig_make"`) or
`fw.device.list`'s `displays[]` (`provider: "platformio"`). `fw.type.list`
and `fw.device.list` still exist for a caller that wants every target of one
kind in a single round trip; `fw.target.get` is for the common case of a row
the user is already looking at.

`kconfig_available` is keyed by family name, `true` when that family's tree has
a parseable Kconfig - it is what a picker uses to decide whether "configure"
can be offered for a family at all, before spending a Kconfig parse to find out.

`job` and `recent` are always `null`/`[]` in Phase 1; the keys exist now so the
shape doesn't change when jobs arrive. `klipper_service`, `printing` and
`idle_state` are **best-effort** — they come from querying Moonraker, and are
`null` when it can't be reached. Never treat them as load-bearing.

`locked_by` is non-null when a CLI build or flash is running on the host:
`{"pid": 1234, "label": "build klipper/bttebb36", "since": 1785412000.0}`.

### `TypeStatus`

```json
{"name": "bttebb36",
 "chipset": "stm32g0b1xx",
 "firmware": "klipper",
 "katapult_installed": true,
 "needs_flash": false,
 "klipper":  {"extra_args": "", "makefile_patches": [], "extra_repos": []},
 "katapult": {"extra_args": "", "makefile_patches": [], "extra_repos": [],
              "installed": true},
 "serials": [
   {"serial": "290055001850304158373620", "state": "klipper",
    "path": "/dev/serial/by-id/usb-Klipper_stm32g0b1xx_290055001850304158373620-if00",
    "mcu": "EBBT0", "running_version": "v0.12.0-381-g...", "running_sha": "e4f5a6b",
    "confidence": "unique_bus_id", "needs_flash": false, "reason": null},
   {"serial": "230048001750304158373620", "state": "offline", "path": null,
    "mcu": null, "running_version": null, "running_sha": null,
    "confidence": null, "needs_flash": null, "reason": "offline"}],
 "artifacts": {"klipper": Artifact, "katapult": Artifact}}
```

`firmware` is the type's *application* - the first declared family that is not a
bootloader (`McuType.application()`), not the full `firmware:` list. `katapult`
is folded into it as `katapult_installed` plus the `installed` flag on the
`katapult` block, rather than a `firmwares` array - `mcu.firmwares` is a
config-model attribute that is never serialised under that name.
`artifacts` is keyed by exactly the families this type declares (see
docs/rebuild-plan.md Step 18) - a type with no bootloader carries no `katapult`
key at all, here or in `artifacts`. `needs_flash` at this level is `true` if any
serial's is, `false` only if every serial provably is not, `null` otherwise -
the same tri-state rule `Target.needs_flash` uses, described below.

`state` ∈ `"klipper"` | `"katapult"` | `"offline"`. Case in the firmware name is
not dependable on the bus, so matching is case-insensitive and `path` is the real
on-disk path, never a reconstructed one. `mcu`, `running_version` and
`running_sha` are `null` while offline - they come from Klipper's own MCU
identification, not from the bus scan. `needs_flash`/`reason` per serial use the
same `DeviceStatus` vocabulary as a `Target` device entry, below.

`running_sha` can also be `null` on a board that is online and reports a
version - not only offline. Some trees stamp a hand-maintained literal instead
of a git describe (Cartographer's `CONFIG_VERSION`, e.g. `"CARTOGRAPHER
6.2.0"`), which carries no commit at all, so there is nothing for the `g<hex>`
pattern to find. `reason` then falls to a comparison against what the build
stamped rather than the source tree: `"version_only"` (amber, `needs_flash:
null`) when the stamp matches but no believable flash record backs it,
`"source_changed"` when it does not match, or the ordinary green/`null` verdict
once a record does back it.

`confidence` is a `discovery.spec.Confidence.reason` string (`"unique_bus_id"`,
`"answered"`, ...), or `null`. It is this tool's own record of how the board's
identity was last confirmed *at flash time* - not a live discovery answer, which
only exists inside a flash's own Klipper stop and is never computed on a status
poll. `null` covers two different things a caller cannot tell apart from this
field alone: never flashed by this tool, or a stale record discarded because the
board's running commit no longer matches what was recorded (`FlashLog.entry_for`).
Distinct from `present`/`state`, which are a live bus read - a board can be
`present: true` and `confidence: null` when it answers the bus but this tool has
never confirmed it by writing to it.

**Screens carry one too, and it is usually the stronger of the two.** A display
flash asks each screen directly once the ports are free, so a confirmed write
records `"answered"` - where a board typically records `"unique_bus_id"`, ranked
equal but derived from the kernel's name for it rather than from the device
speaking. A screen's `null` has the same two meanings plus a third: a `serial:`
section whose klippy module reports no hardware id has nothing to file a record
under, and its record is skipped rather than being keyed by a port. Records are
keyed by the eFuse id precisely so they follow the screen into another socket -
see "Which screen is on which port is not tracked" below.

### `Artifact`

```json
{"has_config": true, "config_mtime": 1785400000.0,
 "has_bin": true, "bin_mtime": 1785410000.0, "bin_size": 43120,
 "has_uf2": false,
 "built_fw_sha": "a1b2c3d", "current_fw_sha": "e4f5a6b",
 "reason": "source_changed",
 "last_build_seconds": 74.2, "last_build_at": 1785410000.0,
 "config_rewritten": false,
 "profile": {"managed": true, "profile": "config.CartoV4USB", "custom": false,
             "parent": null, "reason": null, "tone": "ok",
             "label": "Matches profile"}}
```

`reason` ∈ `null` | `"never_built"` | `"config_changed"` | `"source_changed"` |
`"built_dirty"` | `"foreign_build"` | `"no_provenance"`. Retired in Step 14 of
docs/rebuild-plan.md: this used to be two fields, a three-value `stale`/
`stale_reason` collapse and a six-value `reason` carrying the full detail
beside it - now there is only `reason`, and it carries the full set directly.
The two extra values are why a single granular field was worth keeping instead
of collapsing back down: a binary sitting on disk with no build record
(`"no_provenance"`) is **not** the same as never having built one
(`"never_built"`), and a three-value field cannot say which you have. `reason`
is also what `Target.artifact` carries, unchanged.

**This is the field the whole panel exists for**: it answers "do I need to
reflash after that Klipper update?" at a glance. It compares recorded provenance
— the source-tree commit and a hash of the `.config` actually used — not
timestamps, so a `touch` doesn't lie and a `git pull` of Klipper correctly marks
every board stale.

`config_rewritten` is true when `make` ran `olddefconfig` over the saved config,
which silently changes menuconfig answers after Klipper's `src/Kconfig` changes.
Worth surfacing; users otherwise get "why did my CAN setting move?".

`profile` is a **third** verdict, deliberately beside the other two rather than
folded into `reason`. `reason` answers "is the binary current with its inputs?";
this answers "do the inputs still say what the profile said?". A customised
config is not a stale artifact and does not want a rebuild — it wants somebody
to know about it. `reason` ∈ `null` | `"unmanaged"` | `"customised"` |
`"seed_moved"`, and `unmanaged` carries an **ok** tone: it is the state of every
type predating profiles, and painting those amber would be noise about a thing
that is not wrong. See `fw.profile.*`.

`customised` carries an **ok** tone too, which is a **change**: it was
`"unknown"` while there was nowhere to put a user's own answers. Now that a save
captures them as a profile of their own, being on your own answers is a
destination rather than drift, and its label reads *"Your own answers"* rather
than *"Customised"*. `custom` is true when the profile being tracked is this
type's own (`config.custom`), and `parent` then names what it was forked from —
elsewhere `profile` already names that, because a customised config's record
still names the seed it drifted from.

### `BusDevice`

```json
{"fw": "Klipper", "chipset": "stm32g0b1xx", "serial": "1100...",
 "path": "/dev/serial/by-id/usb-Klipper_...", "state": "klipper",
 "tracked_by": "bttebb36", "is_mcu": true, "ignored": false}
```

`tracked_by` is `null` for a device on the bus that no MCU type claims — that's
the "new board, want to track it?" case. `is_mcu` is false for anything that
merely parses as a by-id device without looking like a Klipper or Katapult
board — a USB serial adapter feeding a display, say — and a "track this"
affordance should not be offered for it. `ignored` is set by `fw.bus.ignore`;
like `is_mcu`, it is a flag rather than a filter, so an ignored device still
appears here.

### `Target`

An MCU type and an ESP32 display are different kinds of thing, but they are the
same kind of *row*: something that gets built, and some devices it gets written
to. `targets` is `types` and `displays` projected onto that shape, so one
component renders both — and renders whatever comes next without being taught to.

```json
{"provider": "kconfig_make", "name": "carto_v4", "descriptor": "stm32g431xx",
 "firmware": "cartographer",
 "artifact": {"state": "stale", "tone": "attention",
              "label": "Source updated - rebuild", "reason": "source_changed"},
 "profile": {"managed": true, "profile": "config.CartoV4USB", "custom": false,
             "parent": null, "reason": "seed_moved", "tone": "attention",
             "label": "Profile updated - reseed available"},
 "needs_flash": true,
 "devices": [
   {"id": "290055001850304158373620", "name": "mcu scanner",
    "present": true, "state": "klipper", "path": "/dev/serial/by-id/usb-...",
    "version": "v0.12.0-381-g...", "confidence": "unique_bus_id",
    "needs_flash": true, "tone": "attention",
    "label": "Update available", "reason": "source_changed",
    "actions": [{"id": "flash", "label": "Flash", "method": "fw.flash",
                 "params": {"name": "carto_v4", "serial": "2900..."},
                 "blocked": null}]}],
 "actions": [
   {"id": "build", "label": "Build", "method": "fw.build",
    "params": {"name": "carto_v4", "fw": "cartographer"}, "blocked": null},
   {"id": "profile", "label": "Change profile", "method": "fw.profile.apply",
    "params": {"name": "carto_v4", "fw": "cartographer"}, "blocked": null,
    "choices": {"method": "fw.profile.list",
                "params": {"name": "carto_v4", "fw": "cartographer",
                           "detail": true},
                "param": "profile"}},
   {"id": "flash", "label": "Flash", "method": "fw.flash_all",
    "params": {"name": "carto_v4", "scope": "stale"},
    "blocked": {"code": "no_artifact", "message": "...", "data": {...}}}]}
```

**It is a projection, not a second source of truth.** Everything here is derived
from the same payloads `types` and `displays` are built from, in the same call.
A fact that appears here and cannot be found there is a bug in the projection.

Four things are deliberate:

- **`tone` and `label` ride along.** `tone` is `ok` | `unknown` | `attention` —
  a traffic light, named semantically because colour is one presentation of it
  and must not be the only way it is understood. `reason` is still there and is
  still what you switch on; `label` exists so the CLI, the panel and whatever
  renders a probe next word the same verdict identically instead of growing
  three sets of copy that drift.
- **A capability is the presence of an action.** If the agent cannot flash, the
  `flash` action is simply absent — not disabled. Gating is `fw.ping`'s
  `capabilities` one level down.
- **A requirement is only visible as `blocked`.** Same `{code, message, data}`
  shape a failed call carries, so a greyed button and a refusal are one object
  with one renderer. `null` means go. Transient global state — a job already
  running — is deliberately *not* here; that is what `job` and `locked_by` are.
- **`method` and `params` ride on each action** so the panel does not hold its
  own RPC map. Without this you ship a uniform shape and the reader still
  branches on `kind`, which is the whole thing this is for.
- **An action may carry `choices`**, meaning "this one takes an option, fetch
  them when you open it". `{method, params, param}`: call `method` with `params`,
  and put what the user picks into the `param` key of the action's own `params`.
  Deliberately generic — the renderer draws a radio group and never learns what a
  profile is — and deliberately *fetched*, because naming the options costs a
  Kconfig parse that `fw.status` cannot afford and a click can.

`profile` is the third verdict from `Artifact.profile`, which this projection
used to drop. When the type is on its own answers it also carries `changes`: the
answers that differ from the profile it was forked from, as
`{symbol, was, now, line}`. Computed only in that state, and free there — both
sides are answer lists, so no Kconfig tree is parsed to produce it.

Two blocked codes mean "no saved config", and which one you get says where to
send the user. `no_config` is the tree that ships no profiles — *"Run menuconfig
for it first"*, unchanged, which is upstream Klipper and therefore most types.
`no_profile` is a tree that ships them, where answering a menu of hundreds by
hand is the wrong first step and the picker is the right one.

Devices carry `actions` too, because the reasons differ per device: one board of
a type can be offline while its neighbour waits in Katapult. `fw.flash` writes
both kinds now — a board's action carries `serial`, a screen's carries `port` —
so the reader never has to branch on which it is holding.

For an MCU target, `devices` contains both tracked USB serials and tracked CAN
UUIDs. A CAN device's flash action carries `uuid` (rather than `serial`), and a
UUID whose liveness cannot be established is reported as `state: "unknown"`,
`version: null`, and `unknown_version`. It deliberately remains `present: true`:
CAN cannot passively distinguish an offline node from one in Katapult, so the
flash attempt is the only safe liveness check. Keeping it eligible ensures the
per-type preview matches `fw.flash_all`, which must attempt an
unknown-liveness UUID rather than silently omitting it.

`needs_flash` is tri-state at both levels, and the target's is `true` if any
device is, `false` only if every device provably is not, and `null` otherwise.
`any()` would read "cannot tell" as "nothing to do" and report a fleet nobody
can see as up to date.

`confidence` is populated for both kinds, from the same record and in the same
vocabulary - a screen that answered the listen pass at its last flash reads
`"answered"`, exactly as a board reads `"unique_bus_id"`. It was a hard-coded
`null` on displays until the write path stopped discarding the `Confidence` it
already computed.

A display carries one extra key, `extra`, holding the facts only a screen has
(`module_version`, `source_version`, `source_dirty`, `klipper_section`,
`reachable`). A reader that never opens it renders both kinds.
`firmware` is `null` for a display: PlatformIO builds from its own tree rather
than from a `[firmware ...]` family, and naming one would be a guess.

### Settings

`fw.status`'s `settings` key and `fw.settings.get` both return the full
`Settings` dataclass (settings.py) as-is. `fw.settings.set {settings: {...}}`
only accepts registry.py's `SETTABLE` subset - `make_jobs`,
`clean_before_build`, `reseed_on_build`, `dry_run`, `enable_flashing`,
`allow_flash_while_printing`, `log_ring_size`, `ui_accent_color` - and refuses
anything else with `setting_not_settable`, whose `data.settable` names the
keys it does accept.
`stop_services` and `service_backend` describe how this host is wired, not a
behaviour preference, and are deliberately absent - editing them from a
browser risks a real flash proceeding with Klipper never stopped. They stay a
cfg-file-only edit.

`ignored_serials` and `ignored_canbus_uuids` are also absent from `SETTABLE`,
for a different reason: they are device lists, not behaviour preferences, and going through
`fw.settings.set` would hit `_coerce_setting`'s int-fallthrough and refuse a
JSON array as "must be a whole number". They are read and written through
their dedicated `fw.bus.*` and `fw.canbus.*` ignore methods instead.

`ui_accent_color` is the one `SETTABLE` key that isn't a behaviour preference
at all - the agent never reads it, only stores and serves it back, so every
browser pointed at this printer agrees on the same accent colour rather than
each one's `localStorage` disagreeing. Empty string means "use the UI's own
default"; otherwise it must be a 6-digit hex colour (`#2196f3`), refused
otherwise with `ERR_INVALID_PARAMS`.

**Toggling `enable_flashing` or `allow_flash_while_printing` does not take
effect until the agent's next reconnect.** `fw.ping`'s `capabilities` is
computed live from `available_methods()`, but what Moonraker will actually
dispatch is fixed at `connection.register_remote_method` time, in
`_handshake` - once per connection, not once per setting. So a save can
leave `fw.ping` reporting `fw.flash` as available while calling it still
answers `-32601` until the agent reconnects. A client offering this toggle
should say so rather than hand over a button that fails.

### `Family`

```json
{"name": "cartographer", "source": "/home/biqu/MCU-Firmware---Based-on-Klipper",
 "artifact": "klipper", "builder": "kconfig_make", "bootloader": false,
 "present": true, "configurable": true, "builtin": false}
```

Every firmware family this install knows about, for a picker to offer. `present`
and `configurable` are separate answers: a declared family whose tree has not
been cloned yet is a real state — it is what every install looks like between
adding the section and running `git clone` — and it wants "check out the source",
not "unknown family". `builtin` marks `klipper` and `katapult`, which cannot be
removed by editing a config file.

`builder` is `[firmware ...]`'s own `builder:` key (default `kconfig_make`) —
how a tree compiles is a property of the tree, not of a type that happens to
use it, so it lives here rather than on `TypeStatus` or `Target`. `bootloader`
marks a family as `katapult`-shaped: not an application, so it is never the
thing a build failure or a staleness check is really about, and a type omits
it from `firmware:` entirely rather than carrying a `katapult_installed` flag.

## Jobs

Builds and flashes are long, so they never block an RPC call. `fw.build` returns
a `job_id` immediately and progress arrives as events.

**One job at a time, and it is not a queue.** Submitting while something runs
fails with `busy`, carrying the incumbent in `error.data.current`. A queue would
be worse than a refusal: the user asks for one thing, walks away, and returns to
find a second operation they'd forgotten about had started itself.

The same exclusive file lock guards the CLI, and it is taken *before* the job is
created — so if someone is mid-`updatefw build` over SSH, `fw.build` fails
immediately with `busy` and names them, rather than producing a job that dies a
moment later.

```jsonc
// Job (never includes the log - that travels separately and is large)
{
  "id": "job-7",
  "kind": "build",
  "params": {"name": "bttebb36", "fw": "klipper"},
  "state": "running",          // queued|running|succeeded|failed|cancelled
  "created": 1785412000.0, "started": 1785412000.1, "finished": null,
  "duration": 12.4,            // live while running
  "progress": {"step": "Building klipper for bttebb36", "index": 0, "total": 1},
  "result": null,              // set on success
  "error": null,               // {code, message, data} on failure
  "cancel_requested": false,
  "log_next": 431,             // next sequence number that will be assigned
  "log_dropped": 0             // lines evicted by the ring buffer
}
```

### Cancellation is not uniform

`fw.job.cancel` returns `immediate`, and the panel must say which it got:

| kind | behaviour |
| --- | --- |
| `build`, `build_all` | **Immediate.** The active `make` process group is killed, then an uncancellable `make clean` removes its source-tree outputs before the job finishes cancelling. |
| `flash`, `flash_all` | **Deferred** — honoured only *between* devices. Interrupting a `flashtool -f` write leaves a board with half an image. Show "cancelling after the current board finishes…". |
| `update_all` | **Deferred**, because it may reach the flashing half. Cancelling during its build phase still waits for the current type's `make` to be killed and the loop to come round. |

### `fw.flash` — the dangerous one

Writes a board. `name` resolving to a PlatformIO type routes to a display
instead — see "Flashing a display" below, which documents that path's own
checks; everything here is the board path.

Flashing stops Klipper and writes to a board. It is therefore **not advertised
unless it is explicitly switched on**:

```ini
# ~/printer_data/config/mcu-updater/mcu-updater.cfg
[updater]
enable_flashing: true
```

Off by default so that updating the agent never silently grants a browser the
ability to reflash the printer. While off, `fw.flash` is absent from
`capabilities` and returns `-32601`; called directly it returns
`flashing_disabled`. **The CLI ignores this setting entirely** — it has always
been able to flash, and Phase 0 didn't change that.

Every refusal happens synchronously, before a job exists, so the caller gets a
real explanation instead of a job that dies a second later. In order:

| Check | Error code |
| --- | --- |
| capability gate | `flashing_disabled` |
| `serial` present | `-32602` |
| serial resolves to a type | `unknown_serial` / `ambiguous_serial` / `serial_tracked_elsewhere` |
| firmware has been built | `no_artifact` |
| board is on the bus | `device_not_found` |
| printer idle | `print_in_progress` (bypass with `force: true`) |

**`uuid` is a third identity form**, alongside `serial`/`port` - `{uuid, name?,
force?}` flashes a CAN-addressed board instead of a by-id one. Same ordering,
with two differences a CAN uuid's lack of a chipset-segment identity forces:

| Check | Error code |
| --- | --- |
| capability gate | `flashing_disabled` |
| `uuid` present | `-32602` |
| uuid resolves to a type | `unknown_uuid` / `ambiguous_uuid` / `uuid_tracked_elsewhere` |
| firmware has been built | `no_artifact` |
| **a CAN interface exists on this host at all** | `device_not_found` |
| printer idle | `print_in_progress` (bypass with `force: true`) |

The last difference is the one that matters: there is no by-id equivalent of
"is this specific uuid on the bus right now" to check synchronously - finding
out *is* the flash attempt, via the unified flashtool flasher's own
per-interface trial (see
"What gets selected" below). So this only refuses up front when there is no
CAN hardware on the host at all; a uuid that simply does not answer on any
interface is discovered inside the job instead, the same timeout-means-
not-found fallback `flash_all`/`update_all`'s CAN liveness check uses. For an
adopted CAN uuid, the configured Klipper `canbus_interface` is used when
present, with the historical default `can0` when it is omitted. If no adopted
interface mapping is available, the updater retries the current CAN interfaces
discovered from sysfs and retains successful results when another interface
fails. Interface names are never persisted. The unified `flashtool` flasher
selects the CAN operation itself; callers do not add a separate recovery `-r`
option.

The idle check looks at **two** fields, and needs both:

- `print_stats.state` — refuses `printing` / `paused`. Only knows about
  virtual_sdcard print jobs.
- `idle_timeout.state` — refuses `"Printing"`. This is the one that means
  "Klipper is executing commands", and it is the only one that catches a manual
  home, a quad-gantry-level, a bed mesh, or a macro run from the console.

`print_stats` alone is not enough, and that gap was found on real hardware: a
flash went ahead during a QGL, Klipper was stopped mid-motion, and the MCU came
back shut down. `error.data.reason` is `"print"` or `"busy"` so a client can word
its message correctly.

The board check is up front on purpose: discovering a detached board *after*
stopping Klipper would mean an outage for nothing.

Once running, the job stops Klipper, writes, waits for the board to come back,
restarts Klipper, and then confirms Klipper is actually usable. Klipper is down
only for the write itself.

**The stop is verified.** If Klipper is still running after the stop request (no
passwordless sudo, Moonraker unreachable, a wedged unit) the job aborts with
`service_control` rather than flashing anyway, because Klipper holds the serial
port and writing into that contention is unsafe.

**The board is waited for.** After the write it reboots into the new firmware and
re-enumerates over USB, which takes a couple of seconds. Starting Klipper before
the device node exists means Klipper cannot find its MCU and comes up in an error
state.

**`systemctl is-active klipper` is not the same as Klipper being ready.** A board
that was mid-motion when the service stopped comes back with its MCU shut down, so
Klippy reaches `error` or `shutdown` and the printer will not move until a
`FIRMWARE_RESTART`. The job polls `printer.info`, and if Klippy is not `ready` it
issues `printer.firmware_restart` once and polls again — the same thing a human
does by hand. The final state is reported as `result.klippy_state`, and if it is
still broken the job log says exactly what to run. The flash itself is not marked
failed, because the write did succeed.

Cancellation is **deferred** for a flash (`immediate: false`) — see the table
above. Show "cancelling after the current board finishes…", not a spinner.

#### Getting Klipper back is the release gate

Four independent layers, because this is the one failure that leaves a printer
dead until a human notices:

1. the `finally` in `klipper_stopped()`;
2. `MoonrakerService.start()` falling through to `sudo systemctl start klipper`
   if Moonraker itself has gone away;
3. a journal at `~/printer_data/mcu-updater/.updater.state`, written before the stop and cleared
   after the start, which the agent reconciles on startup — this is what covers
   `kill -9`, where no `finally` ever runs;
4. `ExecStopPost` on the systemd unit.

The agent also **refuses to exit while a flash is in progress** (SIGTERM is
deferred up to `--shutdown-grace`, under the unit's `TimeoutStopSec`), because
`systemctl restart mcu-updater` mid-write would otherwise leave half an image
on a board.

### Bulk operations

Three methods, but only two implementations: `fw.update_all` is `build_all`
followed by `flash_all`, sharing their bodies rather than reimplementing the loop.

| Method | Stops Klipper | Writes to boards |
| --- | --- | --- |
| `fw.build_all` | no | no |
| `fw.flash_all` | yes, **once for the whole batch** | yes |
| `fw.update_all` | yes, once, after the builds | yes |

`fw.build_all` needs only a runner. The two that write are gated on
`enable_flashing` exactly like `fw.flash`, and are absent from `capabilities`
while it is off.

#### `scope`

Both scopes exist because provenance and intent are different things.

| `scope` | Meaning |
| --- | --- |
| `"stale"` (default) | Only what the recorded provenance says needs doing. |
| `"all"` | Everything in scope regardless. |

`stale` is more precise than a version comparison, not less: a rebuilt artifact
makes its boards stale even when the Klipper commit has not moved, which is what
`artifact_changed` reports and what a `mcu_version` comparison structurally
cannot see.

`all` is for when you know something the provenance cannot — an edited source
file that is not tracked, or a makefile patch whose effect you want on every
board of a type whatever the records say. It overrides the *judgement*, never the
physics: an offline board is still excluded, because a flash needs the board on
the bus and including it would only guarantee a failure partway through a batch
that has already stopped Klipper.

An unrecognised `scope` is refused with `-32602` rather than falling back to
`stale`, since a silent fallback would mean a user asking for `all` quietly
getting nothing.

#### What gets selected

`flash_all` walks the **registry**, so an adopted-but-untracked board can never
be swept into a bulk flash — it has no type, and therefore no firmware.

`build_all` walks the **providers** — the build systems this host has, one per
module in `mcu_updater/providers/`. That is what puts displays in a fleet build:
the registry used to be the only list it had, so "build everything" meant "build
every MCU" and every screen stayed on whatever it was running, silently.

Each provider enumerates its own targets:

| Provider | A target is | `fw` |
| --- | --- | --- |
| `kconfig_make` | one `[type ...]` × one firmware family it names | the family |
| `platformio` | one `[type ...]` whose firmware's builder is `platformio` | `null` — the env *is* the type |

Three rules follow, and each of them was a bug first:

- **A type builds the families it runs, not klipper.** Applying one family to
  every type meant a `firmware: cartographer` board had no klipper `.config`, was
  dropped, and the batch reported success having never built it.
- **`fw` filters; it never forces.** "Rebuild katapult everywhere" narrows the
  sweep to targets that already use that family. A display has no family, so it
  is correctly left alone rather than matched by a missing value.
- **A sweep leaves katapult alone.** The bootloader is built when a device is
  adopted or when `fw` names it — never incidentally. It is already on the
  hardware doing its one job, a fleet flash never writes it, and a CAN board is
  reachable only *through* the bootloader that would be replaced.

Anything that cannot be built at all — a type that has never been through
`menuconfig`, a display with no source tree — is **skipped, not failed**: there
is nothing the batch could do about it, and failing over one unconfigured target
would turn a one-target problem into a fleet-wide one. Every such target is
returned in `skipped` with a reason, on the job submission and inside the
`nothing_to_do` refusal. A batch that drops something and reports success is the
failure this whole area exists to make impossible.

```json
{"job_id": "job-7", "job": {...},
 "types": ["carto_v4", "knomi_toolchanger"],
 "builds": [{"type": "carto_v4", "fw": "cartographer", "provider": "kconfig_make"},
            {"type": "knomi_toolchanger", "fw": null, "provider": "platformio"}],
 "skipped": [{"type": "bttebb36", "fw": "klipper", "provider": "kconfig_make",
              "reason": "'bttebb36' has no saved klipper configuration yet - run menuconfig for it once first."}]}
```

`flash_all` takes every tracked serial that is online, belongs to a type with a
built artifact, and (under `stale`) has `needs_flash: true` — **and every screen
that meets the same three tests.** It is per-device, not per-type: two boards of
one model genuinely do run different firmware. Passing `name` narrows it to a
single type, board or screen — that is "flash this type", implemented as this
same operation with a filter.

**A tracked `canbus_uuids:` entry is included too, never excluded** — the
CAN counterpart of the same rule, with liveness answered by two tiers rather
than a single instant by-id check:

1. **Preferred: a `canbus_uuid` → `[mcu <name>]` cross-reference**, read from
   Klipper's own `configfile.settings` (the same `printer.objects.query` this
   already uses for a tracked serial's `mcu`/`running_version`). A hit whose
   mcu object also reports a live version answers "online, and what's it
   running" as cheaply as a tracked serial's presence does today, with no
   CAN bus traffic of its own — judged by the usual `needs_flash` reasons
   (`source_changed`, `artifact_changed`, ...), `state: "klipper"`.
2. **Fallback: attempt the flash and let a timeout mean "not found."**
   Included in the sweep unconditionally, `state: "unknown"`,
   `reason: "unknown_liveness"` (or `"forced"` under `scope: "all"`) — used
   whenever the preferred tier cannot give a real answer: the uuid has no
   `canbus_uuid:` declaration anywhere in `configfile.settings` at all,
   Klipper cannot be asked, **or** it *is* declared but the mcu object
   reports no live version. That last case is deliberately **not** treated
   as offline the way a missing by-id device is: absence of `mcu_version`
   here covers both "genuinely offline" and "sitting in Katapult, unreachable
   to klippy" indistinguishably — and the latter is exactly the board most in
   need of a flash, so guessing "offline" would silently drop it. Only the
   flash attempt's own per-interface trial can actually tell the two apart.
   Slower than the by-id scan's instant presence check, and an accepted cost
   rather than a reason to leave a tracked CAN board out of a fleet operation.

`fw.bus.scan` stays USB-by-id-specific, as it is today — CAN's own "on bus"
view is `fw.canbus.scan`, not a merge into this one.

Screens are selected **before anything stops**, because the screen list comes
from the klippy module's own printer objects and only a running Klipper answers.
That constraint is why selection is the agent's job rather than the flasher's.

`update_all` is `build_all` followed by `flash_all`, so it now covers screens on
both halves.

Both refuse with `nothing_to_do` when the selection comes out empty, rather than
starting a job that does nothing and reads as a bug.

`fw.flash_all` returns the selection up front, so the panel can name the boards
in its confirmation:

```json
{"job_id": "job-9", "job": {...},
 "boards": [{"type": "flylllplusbuffer", "serial": "4C00...",
             "chipset": "stm32f072xb", "state": "klipper",
             "reason": "artifact_changed"},
            {"type": "hexadistrofusion", "uuid": "bcb5346fc731",
             "chipset": "stm32f072xb", "state": "unknown", "bridge": true,
             "reason": "unknown_liveness"}],
 "displays": [{"type": "knomi_toolchanger", "id": "/dev/knomi_t0",
               "flasher": "esptool", "name": "t0_knomi",
               "section": "knomi_serial t0_knomi", "reason": "source_changed"}]}
```

A CAN board's entry carries `uuid` rather than `serial`, and `bridge` — `true`/
`false` from the `configfile` cross-reference's `mcu_constants.CANBUS_BRIDGE`
read, `null` when liveness could not be judged at all (the fallback tier's
"no cross-reference to read it from" case).

Two keys rather than one merged list: the selections answer with different facts
— a board has a chipset and a serial, a screen has a port and a klippy section —
and flattening them would invent nulls for half of each. The *batch* is uniform;
the confirmation is not, because a human reading it wants the real names.

#### Failures do not abandon the batch

One type failing to compile is usually about that type, so the loop continues and
reports what happened — matching what the CLI's `update-all` has always done:

```json
{"build": {"built": [{"type": "bttebb36", "fw": "klipper", "provider": "kconfig_make"}],
           "failures": [{"type": "bttmmbv1", "fw": "klipper",
                         "provider": "kconfig_make", "error": "make failed (exit 2)"}]},
 "flash": {"flashed": [{"type": "bttebb36", "id": "2900...",
                        "flasher": "flashtool", "serial": "2900..."}],
           "failures": []}}
```

A job with failures still ends `succeeded`; `result.*.failures` is the thing to
render, not the job state.

Both halves name what did the work — `provider` for a build, `flasher` for a
write — because "bttmmbv1 failed" stopped being enough once a type can build more
than one family and a host can write with more than one tool. `id` is the uniform
slot: a board's serial, a screen's configured port. `serial` rides along on a
flashtool result because that is what a board's id has always been called here.

#### Grouped by requirement, not by kind

A flash batch splits on whether each write needs Klipper down, and opens the stop
once for the group that does. A board needs it because *getting* to Katapult does
— the reboot request goes over the port Klipper is holding — and a screen needs it
because the klippy module holds the port for the write itself. dfu-util does not:
by the time it runs the board is in DFU, so it was never on the Klipper bus.

That is what lets one batch cover boards and screens without either path knowing
about the other, and what keeps a write that needs no outage from inheriting one.

#### Two things `update_all` does that a naive composition would not

**The boards are chosen after the build, not before.** A build is what makes
boards stale, so selecting up front would use provenance the build is about to
invalidate.

**The idle gate is checked twice.** Once before the job is created, and again
after the builds finish — a fleet build takes minutes, and the check that passed
at submission is stale by the time anything is about to be written. If the printer
has started moving, the job fails with `print_in_progress` and Klipper is never
stopped at all.

Each board is also waited for individually after its write, exactly as in a single
flash: the last board of a batch would otherwise have nothing between its write
and the service restart.

### Setting up a brand-new board

A board with no bootloader on it is reached over **DFU** (STM32) or **BOOTSEL**
mass storage (RP2040) — one `fw.add_mcu.start` routes to whichever mechanism the
type's `chipset` calls for, mirroring `flash_initial_bootloader`'s own dispatch.
Either way the whole flow is four calls of which only two are new:

| Step | Call | New? |
| --- | --- | --- |
| 1. What is in DFU / BOOTSEL? | `fw.dfu.scan` / `fw.bootsel.scan` | **new**, read-only |
| 2. Put Katapult on it | `fw.add_mcu.start {name}` | **new** |
| 3. Adopt what appeared | `fw.serial.add {name, serial}` | existing |
| 4. Put Klipper on it | `fw.flash {serial}` | existing |

There is no `fw.add_mcu.confirm`. Adopting the board is exactly what
`fw.serial.add` already does, validation included, so a confirm method would be a
second implementation to keep in step with the first — the same reason
`fw.flash_type` is just `fw.flash_all {name}`.

#### `fw.dfu.scan`

Reports rather than raises, because describing the situation *is* the work here.
`ready` is true only when exactly one board is present and openable.

| `reason` | What it means, and what to do |
| --- | --- |
| `null` | Ready. One board, openable. |
| `no_tool` | `apt install dfu-util`. Nothing to do with the board. |
| `permission_denied` | libusb saw a board and could not claim it. **The boot jumper worked** — this is the udev rule, not the hardware. |
| `none` | Nothing in DFU. Fit the jumper and replug. |
| `ambiguous` | More than one, and none named. Not a dead end — see below. |

`permission_denied` is kept apart from `none` deliberately. Collapsing them is
what once told a user "no DFU device detected, hold BOOT0 and replug" when the
board was sitting there perfectly, sending them to redo the one step that had
worked.

Each device carries `serial`, `path`, `devnum` and the raw line. A DFU board
exposes no `/dev/serial/by-id` name, so those are the only identity it has.
**`path` is the one to show prominently** — it is the only field corresponding to
a physical port, and therefore the only hint about which board is which.

#### `fw.bootsel.scan`

Mirrors `fw.dfu.scan`'s report-don't-raise shape. Diverges where BOOTSEL
genuinely differs: reading `/dev/disk/by-id` and a mount point is plain
filesystem access, not a subprocess or a libusb claim, so there is no
`no_tool`/`permission_denied` here at all. `ready` gates on the **mount**
count, not the device count — that is exactly what the write itself
(the flasher's `_find_mount`) gates on, since a board present but unmounted is
not writable regardless of how many are attached.

| `reason` | What it means, and what to do |
| --- | --- |
| `null` | Ready. One board, one mounted volume. |
| `none` | Nothing in BOOTSEL. Hold BOOTSEL and replug. |
| `not_mounted` | A board is attached but nothing mounted its volume — this host has no automounter. Re-run `install.sh` to install the udev rule, or mount it manually at `/media/<user>/RPI-RP2`. |
| `ambiguous` | More than one RPI-RP2 volume is mounted at once. |

`ambiguous` is a dead end here, unlike DFU's — there is no serial-based
targeting for a mount-based write (see `fw.add_mcu.start` below), and the udev
rule mounts every board to the same fixed path, so two boards in BOOTSEL at
once genuinely collide. Bench convention is one board at a time; unplug the
others and rescan.

Each device carries `id` (the boot ROM's flash-chip unique id, parsed from
`/dev/disk/by-id/usb-RPI_RP2_<id>-...`, or `null` if it couldn't be parsed) and
`node` (the raw by-id path). Like DFU's `_identify_dfu`, a device already
matching a tracked rp2040 board's serial carries `known_serial`/`tracked_by` —
but unlike DFU's derivation, this is an **assumed identity** (the boot-ROM id
equals Katapult's later running serial), not a computed one; see "A board that
turns up later is still adopted" below.

#### `fw.canbus.scan`

The CAN counterpart to `fw.bus.scan`'s untracked-USB-serial view — "on bus,
want to adopt it?" for a CAN-addressed board, not a liveness check for one
already adopted and connected. `flashtool.py --query` broadcasts a "who has no
CAN node id yet" admin request, and a board klippy has already connected to
(which assigns it a node id while establishing the link) goes silent to
further queries — confirmed both live on the bench and from Klipper's own
firmware source. That makes this reliable for discovering **unclaimed**
boards — freshly flashed, not yet in `printer.cfg`, or tracked here with no
live klippy connection — and unusable for polling an already-connected one;
that question is answered separately, via `printer.cfg`'s own
`canbus_uuid`/`configfile` cross-reference, not this method.

Runs **only** when called — never from `fw.status`, never swept into
`discovery.confirm`'s USB-flash sources, never on a timer. The standalone panel
starts it alongside `fw.status` on initial connection and manual refresh. The
results stay independent, so USB status is displayed as soon as it arrives even
when CAN queries are slow or fail. Older scan responses cannot replace a newer
refresh.

Mirrors `fw.dfu.scan`/`fw.bootsel.scan`'s report-don't-raise shape:
describing the situation *is* the work here, so this never throws for
"nothing found" — it reports it in `message` instead.

| `interfaces` | Every host network device whose sysfs `type` is `280` (`ARPHRD_CAN`) — read from the kernel, never assumed from a name like `can0`. Each entry is `{name, adapter}`; `adapter` is the shared USB inventory record when the interface belongs to a USB adapter, otherwise `null`. Empty means no CAN hardware on this host at all. |
| --- | --- |
| `devices` | One entry per unclaimed board that answered, across every interface: `{uuid, interface, application, state, tracked_by, ignored}`. `application` is exactly what flashtool printed (`"Klipper"`, `"Katapult"`, or `"Unknown"`); `interface` is informational only for *this* scan — Linux CAN interface names are enumeration order, not stable identity, so nothing here persists one. `tracked_by` is the type name if `uuid` is already in that type's `canbus_uuids:`, else `null`. `ignored` is set through `fw.canbus.ignore`; it is a flag rather than a filter, and applies to every sighting of the UUID on every interface. |
| `failures` | Per-interface query failures, `{interface, reason, returncode}`. A failed interface does not discard successful sightings from other interfaces. |
| `count` | `len(devices)`. |
| `message` | Set whenever there is nothing to show — no CAN interfaces present, `flashtool.py` itself is missing, every query failed, or no unclaimed board answered — otherwise `null`. |

No `is_mcu`-style filtering happens here: every CAN admin responder is
inherently a Klipper- or Katapult-speaking node, since the protocol itself
names the application in its reply. There is no non-board case to guard
against on this path, unlike a USB CH340 bridge chip that merely looks like a
board on `/dev/serial/by-id`.

#### `fw.add_mcu.start`

Writes Katapult to a board in DFU or BOOTSEL (by the type's `chipset`), waits
for it to re-enumerate, and reports what appeared. `dfu_serial` is populated
only on the DFU path; `bootsel_id` (the boot-ROM flash-chip id, when exactly one
board was attached) only on the BOOTSEL path — the other is always `null`:

```json
{"type": "bttebb36", "chipset": "stm32g0b1xx", "dfu_serial": "3941335F3434",
 "bootsel_id": null,
 "candidates":      [{"serial": "2D0043...", "path": "...", "state": "katapult"}],
 "already_tracked": []}
```

`candidates` are matched by chipset and by not having been on the bus before
the write, not by which firmware they come back running. A board that already
carried a valid application chain-loads straight past Katapult on its first
boot — this is the normal case for a board getting a bootloader *re*-installed
— so `state` here can legitimately be the board's own firmware name instead of
`"katapult"`.

`candidates` are boards that appeared and are **not** in the registry — the ones
to adopt. `already_tracked` are boards that appeared and already belong to a
type, which is the normal case when re-installing a bootloader: such a board sits
`offline` in the registry precisely because it had no firmware. Both mean the
flash worked; only `candidates` leaves anything to do.

Both empty means nothing came back at all, which is the only case worth
investigating.

**Neither path can take a serial for the new board, because there isn't one
yet.** A DFU device has no by-id name at all, and a BOOTSEL board's only
identity (the boot-ROM id) is not the serial it will run under — the identity
to adopt does not exist until Katapult is on it either way. That is why this
snapshots the bus first and diffs afterwards, for both mechanisms.

**Klipper is never stopped.** A board that is not in `printer.cfg` is not held by
Klipper, so there is no port contention and no reason for an outage. The
exclusive lock is still taken, so it cannot run beside a build or a flash.

Refusals, all synchronous and before a job exists:

| Check | Error code |
| --- | --- |
| capability gate | `flashing_disabled` |
| type exists | `unknown_type` |
| chipset uses DFU or BOOTSEL at all | `unsupported_chipset` |
| Katapult has been built for the type (`.bin` for DFU, `.uf2` for BOOTSEL) | `no_artifact` |
| **DFU:** something is in DFU | `dfu_none` / `dfu_permission_denied` / `dfu_no_tool` |
| **DFU:** exactly one, or one named | `dfu_ambiguous` |
| **DFU:** the named serial is present | `device_not_found` |
| **BOOTSEL:** something is in BOOTSEL and mounted | `bootsel_none` / `bootsel_not_mounted` |
| **BOOTSEL:** exactly one mounted | `bootsel_ambiguous` |

**Several boards in DFU is a choice, not a dead end.** dfu-util takes `-S`, `-p`
and `-n`, so passing `dfu_serial` targets one exactly. It is still refused by
default: a USB serial like `3941335F3434` says nothing about which board on the
bench it is, so choosing on the user's behalf risks writing a bootloader to the
wrong one.

The write is pinned to the chosen device even when only one is attached — between
the scan and the command, a second board can be jumpered and plugged in.

**BOOTSEL has no equivalent choice.** There is no `bootsel_id` argument to
`fw.add_mcu.start` — mounting *is* the addressing, the udev rule mounts every
board to the same fixed path, and the write (`_find_mount`) refuses outright on
more than one mounted volume. Bench convention is one board at a time; this is
a deliberate scope line, not a gap left to close later.

Finding no new board **warns rather than failing the job**: the write may have
succeeded and the board simply be slow or on a marginal port, so the log says to
check `/dev/serial/by-id` and adopt directly.

#### A board that turns up later is still adopted

The wait is 15 seconds, which a board on a chain of hubs can miss - and one
unplugged after flashing and brought back tomorrow will certainly miss. So a
pairing key is recorded against the chosen type in `.dfu-pairings.json`, written
**after the write and before the wait**, since the wait timing out is exactly the
case it covers.

The agent's bus poll then adopts such a board when it appears. That is the
completion of an operation already asked for - the type was chosen and the button
pressed - rather than a new decision. Five conditions keep it from ever being a
surprise:

- only **untracked** devices; anything already in the registry is left alone. Not
  filtered to Katapult — a board that already carried a valid application
  chain-loads straight past Katapult on its first boot, so it can turn up
  running its own firmware instead; the pairing-key match below is what
  actually identifies it, the same as the live wait in `fw.add_mcu.start`
- only an **unambiguous** match against the pairing key
- only within the **TTL** (24h), so a board found in a drawer next month is the stranger it has become
- only if the **type still exists**
- the pairing is **consumed**, so it cannot re-add a board you deliberately untracked

Each adoption is logged and emits a `state` event, because a registry edit nobody
can see happening is the thing to avoid.

**The pairing key means something different for each mechanism, and that
difference matters.** For DFU it is the *derived* DFU serial
(`devices.dfu_serial_for`), computed from the same 96-bit unique id the running
serial is built from - a real transformation, verified working. For BOOTSEL it
is the boot-ROM flash-chip id, compared directly against the board's full
canonical hardware serial - **no transformation of that identity** - an
*assumed* identity (RP2040 UF2
bootloaders and Katapult's own RP2040 port both commonly derive their USB
serial from the same Pico SDK unique-id call, but this repo has not yet
confirmed the two strings are literally identical on real hardware). If that
assumption turns out wrong, late adoption simply never fires for a BOOTSEL
board that missed its wait - the same "not adopted automatically" experience
as before this existed, never a *wrong* adoption, since nothing is ever
recorded under a bare running UID for a DFU-paired board either. `fw.add_mcu.start`'s
synchronous `candidates`/`already_tracked` result is unaffected either way -
this only covers the board that missed the live wait.

## ESP32 displays

Knomis and anything else built by PlatformIO. Different enough from an MCU to be
separate: no Kconfig, no Katapult — **a PlatformIO env already names the
board, its partitions and its build flags, so the env is the type.**
`chipset` is still required, as it is on every type; it names no build
behaviour here, only what flasher selection needs.

```ini
# mcu-updater.cfg
[firmware knomi_serial]
source: ~/knomi_serial     # one repo, shared by every env
builder: platformio

[type knomi_toolchanger]
chipset: esp32
firmware: knomi_serial
env: knomi_toolchanger            # REQUIRED - no default
# source: ~/knomi_serial          defaults to the firmware family's source
# klipper_section: knomi_serial   which [<prefix> X] sections are this type's
# service: knomi_serial           port watcher to pause while flashing
```

Adding the second screen is one more section.

`service` is a systemd unit that watches these displays' ports and has to let go
before esptool can have one. It is stopped **inside** the Klipper stop and
started before it — Klipper holds the port outright, the watcher only contends
for it. Absent takes the default; `service:` with nothing after it says this
family has no watcher.

Unlike the Klipper stop this one is never verified and never fatal: if the
watcher will not stop, the worst case is the flake it exists to remove — the
upload fails cleanly and a retry works — and refusing to flash at all would be
worse. A unit systemd has never heard of is simply never active, so an install
without one pays nothing.

**The device list is Klipper's, not ours.** `[knomi_serial T0_knomi]` names how
to find its port one of two ways: `serial:` writes it in printer.cfg directly, or
`device_id:` names the display by the id burned into its chip and leaves the path
to Klipper's own discovery, which reports the result back through the section's
`get_status()`. Either way a second copy here would only be something to disagree
with.

It comes from the **printer objects**, not from `configfile.settings`. A klippy
extra whose section is in printer.cfg always has an object — Klipper refuses to
start when loading one raises — so there is no state where `settings` knows about
a display the objects do not. Reading it as a fallback fetched the whole parsed
printer.cfg a second time on every poll, on top of the copy the MCU version join
already takes. The object reports both halves itself: `device_id` is what
printer.cfg named, and `port` is the configured `serial:` where there is one and
the discovered path otherwise.

A `device_id:` section still appears here before discovery finds it —
`"present": false`, `"configured_path": null` — because a display that needs
flashing is precisely the one this must not be blind to.

### `fw.device.list`

```json
{"displays": [{"name": "t0_knomi", "section": "knomi_serial t0_knomi",
               "device_id": null, "reported_id": "19aa44",
               "addressed_by": "serial",
               "configured_path": "/dev/knomi_t0",
               "resolved_path": "/dev/ttyUSB0", "present": true},
              {"name": "t1_knomi", "section": "knomi_serial t1_knomi",
               "device_id": "19AA44", "addressed_by": "device_id",
               "configured_path": "/dev/ttyUSB3",
               "resolved_path": "/dev/ttyUSB3", "present": true}],
 "reachable": true}
```

`present` is the field this exists for. The klippy module catches a failed open
and runs in no-op mode — deliberately, so one dead screen cannot take Klipper
down — which means a missing symlink produces **no error anywhere**. Klipper
starts happily with a blank display. Nothing else in the system notices.

`reachable` is distinct from an empty list: "no displays configured" and "we
could not ask Klipper" must not look the same.

### `fw.target.get`

```json
// request: {"name": "bttebb36", "provider": "kconfig_make"}
{"provider": "kconfig_make",
 "target": {"name": "bttebb36", "chipset": "stm32g0b1xx", "firmware": "klipper",
            "serials": [...], "artifacts": {...}}}
```

One `targets[]` row's full detail — the same per-item shape `fw.type.list`'s
`types[]` or `fw.device.list`'s `displays[]` would give the matching entry, in
one call instead of "fetch the right list and find the row in it". `name` and
`provider` are both required; `provider` is not inferred from `name` because
nothing stops an MCU type and a display sharing a name across their separate
config files (the same reason a client keys a target row on `provider:name`,
not `name` alone). An unknown `name` for the given `provider` raises
`unknown_target`; an unrecognised `provider` raises `-32602`.

**Not cheaper than `fw.status` for a display.** The `kconfig_make` branch is
genuinely single-target (`type_status` takes a name). The `platformio` branch
is not: displays are built and staled per-*type*, sharing one `printer.cfg`
query and one `git`/artifact read across every screen of that type, so
answering for one display type costs the same `pio_status()` pass `fw.status`
already pays and throws away every other type's result. Fine for "the user
opened this row's detail"; do not poll it per row.

### The watcher's map — the source that answers with Klipper down

`watcher` is `null` whenever `reachable` is true, and populated when it is not:

```json
{"displays": [], "reachable": false,
 "watcher": {"knomi_toolchanger": {
     "service": "knomi_serial", "active": true,
     "devices": [{"device_id": "19aa44", "port": "/dev/ttyUSB0",
                  "firmware_version": "0.5.0+54.g5509d4f",
                  "build_variant": "knomi"}]}}}
```

**This is the case flashing actually needs.** esptool wants the port to itself,
so Klipper has to be stopped — and stopping Klipper is precisely what removes
the `configfile.settings` source everything else here depends on.

**`active` is not decoration.** The map carries no timestamps by design: an
entry means "identified during the watcher's current run, and its port has not
disappeared since", which is only true while the watcher is *running*. A stopped
watcher leaves a file that still parses and may name ports that have since
moved, and nothing in the file says so. Treat `active: false` as "these are
last-known, not current".

It is keyed by display type because the watcher belongs to the family, not the
host — a second display family brings its own.

Not consulted while Klipper is answering, deliberately: deciding staleness means
asking systemd whether the unit is up, which is a fork per call on a method that
rides along in every `fw.status` poll. There is a test asserting it never asks
while the authoritative source is available.

A file with an unrecognised `version`, no `devices`, or an entry with no port
yields an empty map rather than an error — every one of those means "we cannot
tell you where these displays are", and the answer to that is the same in each
case. The format is the display project's to change, and a half-understood port
is a write to the wrong screen.

**`device_id` and `reported_id` are different questions.** `device_id` is what
printer.cfg names, so it is `null` for a `serial:` section — that addresses a
socket, not a display. `reported_id` is what the screen itself says: six hex
characters from the low three bytes of its eFuse MAC, burned in, surviving a
reflash, an `erase_flash` and a move to another socket. It is the only stable
name a display has, because the CH340K in front of it reports no USB serial
number at all. Emitted lowercase, but compare case-insensitively — the vendor's
own docs say not to depend on it.

`config_applied` separates "I pushed it" from "it took": a screen can be current
on firmware and still be showing the pages from before your last edit.
`config_crc` is what we sent, `device_config_crc` is what it holds, and
`page_count` is how many pages it actually built — the configured list minus any
that would have been empty, which otherwise can only be checked by picking the
display up.

`protocol_version` and `device_protocol_version` are the two halves behind
`protocol_match`, so a mismatch can say which way round it is.

Every one of these is `null` against a module too old to report it. **Absence
means unknown, never false** — a screen answering nothing must not read as one
with a mismatched config.

### Flashing a display

Reached through `fw.flash` — `name` resolving to a PlatformIO type is what
routes there instead of the board path above, so the call is `{name, port?,
force?}` rather than `{serial, name?, force?}`. (`fw.display.flash` was a
separate method for this until Step 14 of docs/rebuild-plan.md retired it;
nothing called it once `fw.flash` grew the same routing.)

Two properties carry the risk, and both are enforced rather than documented.

**A port is never inferred.** `pio run -t upload` auto-detects one when told
nothing — and was observed on this printer picking between two indistinguishable
CH340s, with no way for the user to know which it took. The upload refuses an
empty port and always passes `--upload-port`.

**The screen list is read before Klipper stops.** It comes from
`configfile.settings`, which only a *running* Klipper can answer, so reading it
after the stop would find nothing and flash nothing. Every other flow in this API
can query mid-job; this one cannot.

**And it is verified after.** That list says where the screens *were* — a
remembered path, which is the thing the whole hardware-id scheme exists to
avoid. Once Klipper and the watcher have let go, the ports are free for the
first time, and each display can be asked directly: they broadcast their id
every couple of seconds unprompted, so listening for a few seconds resolves
id → port as a fact rather than a memory. That is the order the display project
documents — ask Klipper, fall back to the watcher's file, then verify before
writing.

A screen that answers on a different port than Klipper reported has moved, and
is written where it actually is, with a warning. A screen that does **not**
answer is recorded in `failures` and skipped: the ports were free and everything
else spoke, so writing to its old path would be writing to whatever is on that
path now. The batch carries on, as it does for any other per-screen failure.

Two deliberate softenings, both to avoid taking away something that works today.
A screen with no hardware id at all — a `serial:` section whose klippy module is
too old to report one — falls back to its configured port rather than failing.
And if discovery cannot run at all (no pyserial, no source tree) every screen
falls back, because that is exactly what every flash did before this existed.
Discovery is skipped entirely on a dry run, since it opens real serial ports.

Klipper is stopped once for the batch, because the klippy module holds the port
open and esptool cannot have it while it does. The idle gate applies — a display
is not special enough to interrupt a QGL for.

**Verification is free.** esptool's ROM handshake refuses to write to anything
that is not an ESP32, so the target check is inherent rather than a step that
could be skipped.

```json
{"env": "knomi_toolchanger",
 "flashed": [{"type": "knomi_toolchanger", "id": "/dev/knomi_t0", "flasher": "esptool",
              "name": "t0_knomi", "port": "/dev/knomi_t0",
              "chip": "ESP32-S3 (QFN56) (revision v0.2)"}],
 "failures": []}
```

This runs the same batch machinery `fw.flash_all` does — one flasher, one stop,
the watcher paused and the screens rediscovered inside it — and projects the
result back onto the shape above. `flashed` gained the uniform `type`/`id`/
`flasher` slots; `failures` is unchanged.

**Which screen is on which port is not tracked**, deliberately. It used to be:
every upload recorded the eFuse MAC esptool prints against the port it wrote to,
and a different MAC answering on a known port raised a swap warning. That existed
because these boards had no durable identity — the CH340 in front of them reports
no USB serial — and a remembered path was the only handle there was.

They have one now. `device_id` is the low three bytes of the same eFuse MAC,
reported by the screen itself, and knomi_serial resolves it at every layer: the
klippy module's device map, the watcher's `devices.json`, and our own discovery
at flash time. A `device_id:` section follows its hardware into any socket, and a
`serial:` section addresses a socket because that is what its author chose to
address. Neither case is a fault, so neither gets a warning. An updater flashes
what is in front of it; where a given board lives is the operator's business.

### The log, and its sequence numbers

Every log line gets a monotonic index, starting at 0. A `log` event carries the
index of its **first** line:

```json
{"job_id": "job-7", "seq": 120,
 "lines": [{"i": 120, "s": "stdout", "t": "  Compiling out/src/stepper.o"},
           {"i": 121, "s": "stdout", "t": "  Compiling out/src/buffer.o"}]}
```

Keys are short (`i`/`s`/`t`) because a build emits hundreds of these to every
connected client. `s` is one of `stdout`, `info`, `warn`, `error`, `cmd`,
`stdout_warn`, `stdout_error`.

`stdout_warn` and `stdout_error` are emitted only for classified subprocess
output (build/flash tool stdout, with stderr merged in) - the agent's own
messages still use plain `warn`/`error`. Keeping them separate means the
CLI's stdout/stderr split (which only ever special-cases `warn`/`error`)
doesn't accidentally re-route a compiler diagnostic to stderr.

**Client contract.** Track the next index you expect, starting at 0. On a `log`
event:

- `seq == expected` → append, and set `expected = seq + lines.length`.
- anything else → **a gap.** Call `fw.job.get {job_id, log_from: expected}` and
  replace from there.

Without this a streaming log silently lies after a dropped frame, a page reload,
or a Moonraker restart mid-build.

`fw.job.get` returns `log_from` — the first index it could *actually* serve. That
may be higher than you asked for, because the log is a ring buffer (default 2000
lines, `log_ring_size`) and a very long build evicts its own beginning. When
`log_from > log_from_you_requested`, or `log_dropped > 0`, tell the user lines
were omitted rather than renumbering silently.

Batching is not optional: flushes happen at 250 ms, 40 lines, or 32 KiB,
whichever comes first. One event per line would be 400–800 broadcast frames per
build to every connected client.

## Events

The agent pushes with `connection.send_event`; clients receive
`notify_agent_event` whose params are a **list** with one object:

```json
{"jsonrpc": "2.0", "method": "notify_agent_event",
 "params": [{"agent": "mcu_updater", "event": "state", "data": {...}}]}
```

| Event | `data` | When |
| --- | --- | --- |
| `state` | the full `fw.status` payload | on connect, when Klipper's service state changes, and after any job finishes; coalesced over 150 ms |
| `bus` | `{devices: [BusDevice]}` | when the set of attached devices changes |
| `job` | `{job: Job}` | on every state transition and progress step |
| `log` | `{job_id, seq, lines}` | batched: 250 ms / 40 lines / 32 KiB |

Poll interval for `bus` is 15s idle, dropping to 2s while a job runs (a board
disappears and reappears within seconds during a flash).

`state` is built on its own worker thread and coalesced, so a burst of triggers
produces one event rather than one each — a single Klipper restart makes
Moonraker emit several service-state notifications. Every `state` is a full
snapshot, so a client sees no difference beyond receiving fewer of them.

Pending log lines are always flushed *before* the `job` event that follows them,
so the UI never shows "finished" above the final few lines of output.

A job outlives the connection deliberately. If Moonraker restarts mid-build the
build keeps going, and on reconnect the agent re-emits `state` plus a `job` event
for anything still running — so a client that joined late isn't left thinking the
printer is idle.

`connected` and `disconnected` are **reserved** — Moonraker emits those itself,
carrying the agent's identify payload, and rejects any attempt by an agent to
send them. That is deliberately what the panel uses for availability detection.

## Profiles — seeding a config instead of typing one

A Cartographer V4's `.config` is 138 lines, of which **seven** are answers and
131 are computed from them by Kconfig. The USB and CAN builds differ by exactly
one answer; the "lite" build by one more (`FOR_K1`, which means Creality K1, not
"feature-reduced"). `fw.profile.*` writes those seven from the vendor's own
`config.CartoV4USB`, which ships in their fork's root.

**The answers are read out of the firmware tree, never stored in this repo.**
Copying seven lines here would make us the owner of somebody else's hardware
definition, and it would go stale visibly: `CONFIG_VERSION` is maintained by
hand in those files, so the tree's Kconfig default still says `6.0.0` while
every shipped config says `6.2.0`. Reading them means a `git pull` of the fork
picks up the next bump.

The seed is **loaded and re-emitted**, not copied — the same thing `make
olddefconfig` does, minus needing a terminal. A seed written against last year's
Kconfig therefore picks up symbols added since, instead of leaving them to be
silently filled in by the next build.

### `fw.profile.list`

```json
{"name": "carto_v4", "fw": "cartographer", "detail": false}
```

```json
{"type": "carto_v4", "firmware": "cartographer", "fw": "cartographer",
 "profile": "config.CartoV4USB",
 "available": [
   {"name": "config.custom", "fw": "cartographer", "origin": "custom",
    "parent": "config.CartoV4USB",
    "path": "/home/pi/printer_data/mcu-updater/carto_v4/cartographer.custom.config",
    "distinguishing": [{"symbol": "CANBUS_FREQUENCY", "value": "500000",
                        "line": "CONFIG_CANBUS_FREQUENCY=500000",
                        "label": "CAN bus speed"}]},
   {"name": "config.CartoV4USB", "fw": "cartographer", "origin": "vendor",
    "parent": null,
    "path": "/home/pi/MCU-Firmware---Based-on-Klipper/config.CartoV4USB",
    "distinguishing": [{"symbol": "STM32_CANBUS_PA11_PA12", "value": "n",
                        "line": "# CONFIG_STM32_CANBUS_PA11_PA12 is not set",
                        "label": "CAN bus (on PA11/PA12)"}]}],
 "state": {"cartographer": {"managed": true, "reason": null, "...": "..."},
           "katapult":     {"managed": true, "reason": null, "...": "..."}}}
```

Keyed on the **type**, not on a firmware family: "which profiles apply to this
board" depends on the family the type declares it runs, not on which trees
happen to be installed. `fw` defaults to the family the type runs. Upstream
Klipper ships none, which is the right answer for a tree that builds for two
hundred boards.

`distinguishing` is the answers that tell each entry apart from the others
offered. Cartographer's USB and CAN variants differ by one answer out of seven,
so listing all seven under each of eight entries hides the line that decides
anything. **Disagreement counts; absence does not** — a symbol distinguishes
when two profiles that both answer it answer it differently, and each entry
lists only the answers it gives. Vendor seeds are hand-maintained and mention
computed lines inconsistently, and a custom profile is minimal by construction,
so treating "not mentioned" as a value would make every entry differ from every
other in a dozen places. This is text over small files — no Kconfig tree — so
every listing carries it.

`detail: true` adds `label`, the tree's own prompt text: *"Use PA11/PA12 for
CANbus"* rather than `STM32_CANBUS_PA11_PA12`. That needs the tree parsed, so it
is opt-in and costs one parse for the whole list — the same budget
`fw.kconfig.open` spends, affordable because opening a picker is a click.
`label` is `null` without it, and also for a symbol the tree gives no prompt.
It is deliberately **not** decomposed into "Version"/"Interface" dropdowns:
those axes exist only in the vendor's file names, whose own naming is already
inconsistent inside one directory (`config.CartoV3USBLite` against
`config.CartoV4USBlite`), and parsing them would teach a generic tool one
vendor's board family.

### Your own answers are a profile too

`origin` is `vendor` (shipped in the firmware tree) or `custom` — this type's
own answers, saved under the reserved name **`config.custom`**. It is shaped
exactly like a vendor seed, a short list of answer lines, so `fw.profile.apply`
consumes it through the same path and no caller needs a second concept.

The lifecycle it exists for:

1. Pick a vendor profile → you are **tracking** it. The vendor bumps their
   config and you get the bump.
2. Edit it → you are on **your own** profile, saved under that MCU, and the
   vendor's bump becomes informational.
3. Switching back and forth is lossless, because your answers have a home.

It is captured when `fw.kconfig.save` writes answers that differ from what the
profile put there — nearly free, since that call has the tree parsed, and that
save now returns the minimal `answers` it wrote plus `custom_profile`, the name
it kept them under or `null` when it kept none — and again
by `fw.profile.apply` before a `force` would overwrite a customised config,
which is what catches an edit made out of band by `make menuconfig`. The
`SeedResult` then reports `kept: "config.custom"`. A vendor shipping that exact
name is shadowed rather than listed twice under one name.

One slot per (type, fw), at
`printer_data/config/mcu-updater/types/<type>/<fw>.custom.config` — beside the
`.config` it was captured from, **not** beside the build artifacts. Once that
`.config` has been reseeded from a vendor profile this is the only copy of the
answers the user wrote, so it belongs with the things a backup takes; it is also
served by Moonraker, so it opens in Mainsail's editor next to the file it
describes. Never in the vendor's source directory, where a `git pull` would eat
it. `fw.type.remove` keeps the whole type directory, so removing a type and
re-adding it under the same name restores its custom profile with everything
else.

The file is a seed with a comment header (`# forked-from:`, `# base:`). Both
kconfiglib and the answer parser ignore comments, so it stays safe to hand-edit;
deleting the header costs the "what changed" display and nothing else.

`parent` is what a custom profile was forked from. It is what lets a UI say
"yours, forked from CartoV4USB", and what makes going back a named button rather
than a `force` flag.

### Which comparison runs where

| Question | Compares | Cost | Runs |
| --- | --- | --- | --- |
| Has this been edited? Has the vendor moved? | sha256 of the `.config` and of the vendor's file | two small reads | every `fw.status` |
| What did I change? | the capture's answers against its own `# base:` header | two small reads | every `fw.status`, only when customised |
| Which profiles exist? | — | one readdir of the tree root | every `fw.status` |
| What are these settings called? | — | one Kconfig parse | opening the picker (`detail: true`) |
| Seed / capture / reseed / build | — | reads plus one to three parses | a click or a build |

The vendor's file is only ever compared as **bytes**, never reduced to answers —
that is what keeps `fw.status` off the Kconfig parser. And `# base:` records the
fork point, so after a vendor bump the change list is measured against the
answers you forked from rather than the vendor's current ones. That is the honest
reading of "yours, forked from CartoV4USB"; re-measuring would cost a parse.

`fw.status` is recomputed on every state event — a mutation, a job finishing, a
Klipper service-state change, a reconnect — not on a timer. So a vendor bump is
noticed the next time one of those happens, or on Refresh, exactly as a `git
pull` of Klipper already behaves for the "Source updated — rebuild" chip.

### `fw.profile.apply`

```json
{"name": "carto_v4", "profile": "config.CartoV4USB",
 "derive": true, "force": false}
```

Returns `{"job_id", "job", "type", "fw"}`. The job's `result` is
`{"applied": SeedResult, "derived": SeedResult|null}`, where a `SeedResult`
carries the minimal `answers`, the `carried` / `dropped` split for a derivation,
and the sha256 of both the seed and the config written.

**A job for its runtime, not its danger** — it writes a `.config` and touches no
board. Seeding parses a Kconfig tree up to three times (the seed, a bare probe of
the bootloader tree, then the carried answers), and one parse is a few hundred
milliseconds on a Pi. Every method here answers in well under a second because
Moonraker awaits with no timeout, so this could not stay synchronous.

Everything answerable without a parse is still checked **before** the job exists,
and comes back as a refusal a caller can act on: an unknown type or family, a
profile the tree does not ship, a name that is not a plain basename, and a config
that has been customised (`force` is the answer to that one, and a caller wants
to be told so immediately rather than reading it out of a job that died). The
offset check is the exception — the two addresses only exist after the parse — so
a mismatch fails the job rather than refusing the call.

**Katapult is derived, not seeded, and by default rather than on request.**
There is no vendor config for it, and a second table describing the same board
is how two configs drift into disagreement. Every answer the bootloader tree
also *defines* is carried across; `SCANNER`, `CARTOGRAPHER_G431_ENABLE` and
`VERSION` are dropped by that same test rather than by a hand-maintained skip
list. Seeding only the application would leave a type whose two configs describe
different boards, so the safe combination is the one that takes no extra
argument. `derive: false` exists for a type whose `firmware:` list omits
`katapult`, which has nothing to derive.

**One invariant is checked rather than assumed.** Katapult's
`LAUNCH_APP_ADDRESS` is where it jumps; the application's
`FLASH_APPLICATION_ADDRESS` is where the application was linked to run. Those
agreeing is the whole of "the board boots", and they are separate answers in
separate trees that each build and flash perfectly happily when wrong. A
disagreement raises `offset_mismatch` carrying both addresses, and nothing is
written — including on the application side if it had already succeeded, whose
config stays because it is valid on its own. A missing symbol on either side
raises the same error rather than skipping the check: a check that quietly stops
checking still reads as verified.

Errors: `profile_not_found` (carries `available`), `profile` (a name that is not
a plain `config.*` file in the tree root — it arrives from a browser and is
about to be joined onto a source path), `profile_customised`, `offset_mismatch`.

### `fw.profile.forget`

```json
{"name": "carto_v4", "fw": "cartographer"}
```

Detaches, leaving every answer exactly as it is. Omit `fw` to detach every
family the type uses.

### Nothing here locks anything

A profile-managed config is an ordinary `.config` that `make menuconfig` and
`fw.kconfig.*` can both still change. What profiles add is that the change
becomes **visible** — `Artifact.profile.reason` reports `customised` — instead
of silent. A lock users cannot override gets worked around by editing the file
on disk, which is strictly worse, because then nobody knows.

For the same reason, `fw.profile.apply` refuses rather than overwrites. A config
that still matches its record is ours to rewrite, which is what makes reseeding
after a vendor bump safe and repeatable; anything else — a hand-built config
from before profiles existed, or one edited since — is the user's. `force: true`
replaces it, keeps the answers as `config.custom`, and keeps one generation of
the file as `.bak`. It is still a refusal by default even though the capture
makes it recoverable: the capture makes `force` undoable, not automatic.

### Taking a vendor bump, on the button you were already pressing

A build takes the vendor's updated answers before compiling, and reports which
profile it took as `reseeded` in the job result with a line in the log. Governed
by **one setting**, `reseed_on_build`, which defaults to true.

One setting rather than a flag per entry point, because there are four ways to
start a build — the panel, `updatefw build`, a fleet build, `update-all` — and
they must not disagree about what a build does. It lives in
`mcu_updater.build.build()`, which all four already call.

- `fw.build` accepts `reseed` as an **override for that run**: the confirm dialog
  sends `false` when the user picks "build as-is". Omit it and the setting
  decides.
- `updatefw build --no-reseed` is the same override on the CLI.
- Fleet builds and `update-all` inherit the setting; there is nothing to pass.

Two rules bound it:

- **Only on `seed_moved`.** That state means the saved config still matches our
  record and it is the vendor's file that moved, so nothing of the user's is
  discarded. A `customised` config is left alone *even when asked*: you are on
  your own profile and the bump is informational until you say otherwise.
- **Only in a build, never in `fw.status`.** Status only reports; a writer on the
  path that describes a config is the failure class `config_rewritten` exists to
  surface, not to join. Build time is where the config is about to matter and
  where there is a log to say what was done.

A bootloader config whose profile is `derived:<app>` is re-derived rather than
seeded, which is what re-runs the offset check that keeps the pair bootable.

Three answers are worth warning about specifically rather than blanketing the
whole menu, because they are the ones that cost you a board: the **clock
reference** (wrong and it never enumerates), the **communication interface**
(mismatched to how the board is wired and it vanishes from the bus — and a CAN
build has no software route to ROM DFU, because Klipper's
`STM32_DFU_ROM_ADDRESS` is `default 0 if !USB`), and the **bootloader offset**.
The rest of that menu is genuinely inert for a board like this.

## Availability detection

No polling needed:

1. On store init, `server.extensions.list` → is `mcu_updater` present?
2. If so, `fw.ping` (version gate), then `fw.status`.
3. Live updates come from Moonraker's own `connected` / `disconnected` agent
   events.

## Later phases

Nothing is reserved any more — `fw.add_mcu.start` shipped, and it was the last
one. See "Setting up a brand-new board" above.

**`fw.add_mcu.confirm` was never implemented and never will be**, for the same
reason as `fw.flash_type`: adopting the board is `fw.serial.add`, which already
does it with the validation.

**`fw.flash_type` was never implemented and never will be.** It is
`fw.flash_all {name}`: the same selection and the same loop with a filter, rather
than a second implementation to keep in step with the first.

`fw.build` refuses a type with no saved `.config`, returning `no_saved_config`.
`make menuconfig` is an ncurses UI and cannot run inside the agent, so the
`.config` has to come from either that command over SSH or the `fw.kconfig.*`
methods, which write the same file.
