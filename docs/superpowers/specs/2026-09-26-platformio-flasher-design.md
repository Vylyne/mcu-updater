# The PlatformIO flasher

The follow-on named in `2026-09-24-typed-artifacts-design.md` ("Out of scope: the
PlatformIO flasher follow-on"). It builds on `KIND_PIO_ENV`. Three decisions were
agreed there; this spec turns them into a design and settles the one conflict
they left open.

## Goal

The flasher that runs `pio run -t upload` is named for what it does, describes
the device it writes without screen vocabulary, and leaves the question "which
port is this device on right now" to the family's helper.

Success is:

- No flasher, kind or `detail` key in `flashers/` names a screen, a display or
  esptool.
- A family whose helper can identify its devices is confirmed at write time,
  exactly as strongly as it is today.
- A family with no identifier writes to its configured port.
- Every wire shape is unchanged except the `flasher` value.

## Decisions already agreed

- **Rename the `esptool` flasher to `platformio`.** It runs `pio run -t upload`
  for any PlatformIO env and does not implement esptool. The name `esptool`
  stays free for a real ESP32 image flasher; nothing needs one yet. There is no
  alias and no config migration, because the `flashers:` key has not reached
  `main`. The wire value `"flasher": "esptool"` in `docs/agent-api.md` changes
  with the rename. Nothing in `ui/src` reads it.
- **Drop the screen vocabulary.** `KIND_SCREEN` becomes a neutral kind for a
  device reached at a configured port. `detail` carries the env and the port,
  not `display` and `screen`.
- **Rediscovery goes through the helper seam.** `prepared()` asks the family's
  helper through the existing `Identifier` capability (`identify(...,
  ask=True)`), instead of running `discovery.confirm()` inside the flasher. A
  family with no identifier writes to its configured port.

## The conflict, and how it is settled

Today, `KnomiSerialHelper.identify(ask=True)` returns the watcher's
`devices.json` whenever that file has entries, and listens on the ports only
when it is empty. Its docstring justifies this: "the write itself verifies the
port again". That verification is the `confirm()` pass this change removes from
the flasher. Calling `identify(ask=True)` unchanged from `prepared()` would stop
the write-time listen on every host running the watcher. The "And it is verified
after" paragraph in `agent-api.md` would then be false.

Settled by what the knomi_serial side is for. Klipper knows which screen is
where, through `knomi_cluster` and each `knomi_serial` section. The watcher's map
covers ports Klipper does not hold; per Vi, the watcher combines its own
findings with Klipper's through a Moonraker query. The design does not depend on
which. Connect-and-listen is for three cases:

- the map is missing;
- Klipper is stopped;
- a device has to be confirmed before a flash.

`prepared()` is the third case, and it runs with Klipper stopped. When to listen
is knomi_serial's policy, so it lives in the helper:

- `ask=False` means "what is remembered". It stays the map only, as today.
- `ask=True` means "the ports are free, confirm". The helper listens, and falls
  back to the map only when listening cannot run (no pyserial, no source tree).

The flasher just asks, and holds no knowledge of maps or listen passes.

## Design

### 1. `flashers/platformio.py`

`flashers/esptool.py` is renamed with `git mv`, so history follows.

- `class PlatformIO`: `name = "platformio"`, `label = "PlatformIO upload"`.
- `chipsets`, `states`, `needs_services_stopped = True` and
  `accepts = (KIND_PIO_ENV,)` are unchanged.
- `supports()` returns `device.kind == KIND_PORT`.
- The module docstring describes a PlatformIO env uploaded to a port. It keeps
  the two facts that are PlatformIO's rather than a screen's:
  - never letting PlatformIO choose its own upload port;
  - following a udev symlink to the device.
- `flashers/registry.py` and `flashers/__init__.py` import and export the new
  name.
- `firmware.FLASHERS` becomes `("bootsel", "dfu_util", "flashtool",
  "platformio")`, sorted, with the test that holds it equal to the registry
  unchanged. `_SUGGESTED_FLASHERS["platformio"]` becomes `"platformio"`.

### 2. `KIND_PORT` and the `detail` payload

In `flashers/spec.py`, `KIND_SCREEN = "screen"` becomes `KIND_PORT = "port"`,
commented "A device reached at a configured port; its identity, if its family
has a way to know one, is confirmed at write time."

A `KIND_PORT` device's `detail` is:

| key | value |
| --- | --- |
| `env` | the `PioType` being written: the `[type]` whose `platformio_env` is built. The object, not a string, because `pio.upload`, `read_sidecar` and `identify` all take it. |
| `port` | the configured port, as read before the stop. Equal to `Device.id`. |
| `device_id` | the hardware id, lowercased, or `""` when the device reports none. |
| `name` | the label `write()` reports as `name` on the wire. |

Anything else a caller adds rides along, as `bulk`'s `reason` does today. The
flasher's own keys win.

`device_id` and `name` are two keys the agreed decision did not list. They are
needed:

- `record()` files the flash under the hardware id (`build.display_key`).
- `port_for` matches on the hardware id.
- `write()`'s result carries `name`, and `fw.display.flash` projects failures
  onto it.

The three callers build this payload directly. Each resolves `device_id` the way
the flasher does today, so nothing changes about which id a device is matched
on.

- `agent/methods/flash.py` (`fw.display.flash`) and `agent/methods/bulk.py`
  (the fleet selection) read the klippy screen payload:
  - `port = s["configured_path"]`
  - `device_id = (s["device_id"] or s["reported_id"] or "").lower()`
  - `name = s["name"]`
- `cli.py` reads the `WatcherDevice`:
  - `port = device.port`
  - `device_id = device.device_id`
  - `name = device.device_id`, as its `screen["name"]` is today

`target_for(display, screen, ...)` is replaced by a `target_for(device, ...)`
that reads the `Device`, or folded into `PlatformIO.target` if nothing else calls
it. The plan decides which, after one grep.

The docstrings on `Device` and `FlashTarget` that say `{"display", "screen"}`
for esptool are updated to the new keys and name.

### 3. Rediscovery through the helper

`PlatformIO.target()` already receives the family's `helper`. It stores
`helpers.identifier(helper)` on the target as `detail["identifier"]`, which is
`None` for a family without the capability. `prepared()` therefore needs no
second config load, and does not re-resolve a helper that selection already
resolved.

`prepared()`:

- Groups its targets by `env.name`, the `[type]` name.
- On a dry run, reports `[dry-run] would ask the devices which they are` and
  yields `{}`, as today.
- For each type whose targets carry an identifier, calls
  `identifier.identify(bench.paths, bench.settings, env, ask=True,
  reporter=ctx.reporter)` once. A type with no identifier contributes `{}`.
- Yields `{type name: {device_id: WatcherDevice}}`.

It is never fatal. `identify` already turns an `UpdaterError` from the listen
into a warning and an empty answer, and an empty answer means the configured
port. That is today's "discovery could not run" softening, unchanged.

`write()` and `port_for` keep their four cases:

- nothing identified for this type: the configured port, confidence `None`;
- no hardware id: the configured port, with a warning;
- identified elsewhere and this device silent: refused with
  `FlashError(problem, type=..., port=...)`;
- answered: that port, with a "moved" warning if it differs.

The confidence is now derived from the `WatcherDevice` itself: `ANSWERED` when
`answered` is true, `REMEMBERED` otherwise (section 4). The `"confidence"` key in
`write()`'s result keeps its values.

Removed from the flasher: the `confirm()` import, `_Answered`,
`_sightings_by_family` and the `ctx._esptool_sightings_by_family` cache.
`discovery.confirm()` itself stays, because `flashers/flash.py`'s `device_for`
still uses it for boards. The `Listen` and `Watcher` sources stay in `SOURCES`
for the same caller. Their `_as_sighting` comments that name "esptool's
discover()" as the reason for `detail["family"]` are updated to say what reads
it now. If nothing reads it, it stays and the comment says so; removing it is not
this change.

### 4. knomi_serial's `identify` policy

`helpers/knomi_serial.py`:

- `ask=False`: `read_device_map`, unchanged.
- `ask=True`: `discover` (the listen pass). On `UpdaterError`, a warning and
  then `read_device_map` as the fallback. When the listen runs and hears
  nothing, the answer is `{}`, not the map. The ports were free and nothing
  spoke, which is the "refuse the silent one" case, not "use the remembered
  port".

  That is a deliberate narrowing. Today's write-time `confirm()` merges the map
  under the listen, so a silent screen still in the map is written to its
  remembered port. The new rule matches what `agent-api.md` already says: "A
  screen that does not answer is recorded in `failures` and skipped".
- The docstring is rewritten around the three cases above. The sentence "the
  write itself verifies the port again" goes, because this is that
  verification now.

`WatcherDevice` gains `answered: bool = False`. `listen._parse_discovered` sets
it `True`, and the map reader leaves it `False`. `to_json` is unchanged, so
`fw.device.list` does not change shape.

The CLI keeps today's cost. `cli.py`'s type selection runs inside `_ports_free`,
and it now asks `ask=False` first and `ask=True` only when the map is empty. The
map-first preference moves from the helper to the one caller that wants it, and
a CLI flash still listens once, in `prepared()`, not twice. The CLI's refusal for
a family with no identifier stays. That is the CLI having no other way to
enumerate a type's devices, not a write-time rule.

### 5. Docs

- `docs/agent-api.md`:
  - the two `"flasher": "esptool"` examples become `"platformio"`;
  - prose that names the flasher, such as "what esptool actually wrote to",
    becomes "the `platformio` flasher";
  - the `flashers: (esptool)` refusal examples become `(platformio)`.

  Sentences about esptool the tool stay, because PlatformIO still runs esptool
  underneath for an ESP32: the ROM handshake ("Verification is free") and "esptool
  wants the port to itself". The "And it is verified after" paragraph and its two
  softenings stay true as written. The plan checks that the "Two deliberate
  softenings" paragraph still matches section 4.
- `docs/decisions.md`:
  - In "Identity is a helper capability", the sentence "`confirm()` still decides
    what to trust at write time" becomes "the identifier answers at write time
    too, with `ask=True`, and `confirm()` ranks sightings for boards".
  - The `fw.device.list` / CLI paragraph gains that the CLI asks `ask=False`
    first.
  - The "First install is gated by flashers" example names `platformio`.
  - "Flashers are generic" lists `platformio`, not esptool.
- `README.md`:
  - the Features line `esptool - ESP32, via PlatformIO` becomes `platformio -
    any PlatformIO env, uploaded to its configured port`;
  - the flasher list and the two `flashers: esptool` config examples change.
- `docs/layout.md`: the module name, if it lists it.

### 6. Mutation specs

These are re-anchored in the commit that changes the lines they anchor on.

- `flasher-supports.json`: "esptool writes screens only" becomes "platformio
  writes port devices only", anchored on `return device.kind == KIND_PORT`.
- `display-flash.json`, `flashlog-loop.json`: `file` moves to
  `flashers/platformio.py`, and every `find` is re-anchored on the rewritten
  lines. The guards themselves stay:
  - verified once free;
  - written where it answered;
  - a silent device is refused;
  - a dry run opens nothing;
  - confidence is recorded;
  - no id means no record.

  "Identity is verified once the ports are free" now anchors on the
  `identify(..., ask=True, ...)` call in `prepared()`.
- `identity.json`:
  - Both `if found or not ask:` mutations are rewritten, because the line goes.
    "A caller that did not free the ports is never asked to" anchors on the new
    `if not ask:` branch.
  - "The remembered answer is preferred to six seconds of held ports" moves to
    `cli.py`'s `ask=False`-first call, since that is where the rule now lives.
  - A new guard: "ask=True listens even when a map exists", mutating the helper
    back to map-first.

## Testing

Renames are carried through every test that names `esptool`, `KIND_SCREEN`,
`display`/`screen` detail keys or `Esptool`. That is about 35 test files by
grep; most are one-line `flashers:` values in fixtures.

New or rewritten tests:

- `prepared()` calls the target's identifier with `ask=True` once per type,
  inside the stop, and never on a dry run.
- A family with no identifier writes every device to its configured port, with
  confidence `None` and no refusal.
- `identify(ask=True)` listens even when `devices.json` has entries, and returns
  the listen's answer.
- `identify(ask=True)` falls back to the map when the listen raises
  `UpdaterError`, with a warning.
- `identify(ask=True)` returns `{}` when the listen runs and hears nothing,
  even with a map. A test at the flasher level then refuses the silent device
  rather than writing its remembered port.
- `write()` reports `ANSWERED` for a listened device and `REMEMBERED` for a
  map-fallback one.
- CLI selection reads the map without listening when the map has entries, and
  listens when it is empty.
- The wire: `fw.display.flash` and `fw.flash_all` report `"flasher":
  "platformio"`, and nothing else in their shape moves.

The gate runs on the 3.11 floor venv. Each touched mutation spec is run once, one
at a time, in the background, followed by the hygiene test.

## Out of scope

- A real `esptool` flasher, one that writes an ESP32 image without PlatformIO.
- Removing the `Listen` or `Watcher` sources, or `confirm()`, from board
  discovery.
- Renaming `build.display_key`, the `fw.display.*` methods, the `displays`/
  `screens` wire keys, or `pio_status`. They are wire or status vocabulary, not
  the flasher's, and renaming them is a wire decision of its own.
- Confirming the knomi_serial watcher's Moonraker query against that repo. The
  design reads the map through `read_device_map` and does not depend on how it
  was produced.

## Bench check

On the bench, not the toolhead, per AGENTS.md:

- One `fw.display.flash` of a KNOMI with the watcher running and a populated
  map. The log shows the listen pass, and the result carries `"flasher":
  "platformio"` and `"confidence": "answered"`.
- One CLI flash of that KNOMI type, which listens once, not twice.
