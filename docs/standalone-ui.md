# The standalone UI

A second client for the agent, built to sit beside Mainsail/Fluidd rather than
replace either: static files under `ui/`, served by their own nginx site, on
their own port, talking to the same Moonraker instance the agent already
registers with. No Mainsail fork required — see
[docs/mainsail-fork.md](mainsail-fork.md) for why that fork exists at all and
why this does not need one.

This is a multi-phase build. What is documented below is what has actually
shipped; later phases (the UI itself, actions, Kconfig in the browser, the
iframe embed) will extend this file as they land.

## Layout

| Path | What |
| --- | --- |
| `ui/` | The Vite + Vue 3 + TypeScript source tree, in this repo |
| `~/mcu-updater-ui` | The **installed build** on a printer — override with `UI_PATH` |
| `/etc/nginx/sites-available/mcu-updater` → `sites-enabled/mcu-updater` | The nginx site, installed by `install.sh` |
| `/etc/nginx/conf.d/mcu-updater.conf` | This site's own `upstream`/`map`, self-contained — see below |

`~/mcu-updater-ui` is deliberately a sibling of `~/mcu-updater` (the agent's
own checkout), never a subdirectory of it and never under
`~/printer_data/mcu-updater/`. Both are load-bearing constraints, not style —
see [docs/decisions.md](decisions.md).

## Installing

`install.sh` handles all of this:

1. Appends `[update_manager mcu-updater-ui]` (`scripts/moonraker-update-manager-ui.conf`)
   to `moonraker.conf`, alongside the agent's own `[update_manager mcu-updater]`.
   The two are independent sections — the agent tracks `main` (`type: git_repo`),
   the UI tracks GitHub releases (`type: web`) — so they can and normally will
   be at different versions. That is expected, not an error; a version-gate in
   the UI itself (Phase 3) catches an actually-incompatible mismatch.
2. Fetches the latest published release into `UI_PATH` if nothing is installed
   there yet. Moonraker's own update manager will not do this itself — an empty
   `type: web` directory has no `release_info.json`, which it reads as an
   invalid install and never touches. This bootstrap fetch tries the stable
   channel first, then falls back to beta — a fresh repo can go a long time
   with nothing promoted to stable (see "Releasing" below), and the point here
   is only to unblock Moonraker's own check, which then follows whatever
   `channel:` is actually configured in `moonraker.conf`. If no release has
   been published yet at all (or the fetch fails), a placeholder page is left
   in place and `install.sh` says so; re-run it once a release exists.
3. Optionally installs the nginx site — prompted, skipped cleanly if `nginx`
   is not present. Env vars: `MCU_UPDATER_UI_PORT` (default `8090`, chosen
   clear of Mainsail/Fluidd's 80/81 and the four `mjpgstreamer` ports
   8080–8083) and `MCU_UPDATER_UI_SERVER_NAME` (default `_`, i.e. any host).
   TLS is a commented-out block in the generated site file — point it at
   your own cert (e.g. acme.sh) and uncomment.

The nginx site proxies `/websocket` and `^/(printer|api|access|machine|server)/`
to Moonraker on the same origin as the UI itself, so there is no CORS and no
`cors_domains` edit needed. It ships its own uniquely-named `upstream` and
`map` (`mcu_updater_apiserver`, `$mcu_updater_connection_upgrade`) rather than
depending on KIAUH's `upstreams.conf`/`common_vars.conf`, so it works whether
or not those exist.

**Mainsail's own nginx site is never edited.** There is no nginx equivalent of
an `import` directive that would let this site inject a `location` into
Mainsail's server block from the outside — the only way to run the UI as a
Mainsail subpath is hand-editing the file KIAUH/mainsail-config owns and
rewrites on update, which is fragile and unsupported. Running the UI on its
own port (or its own FQDN, with acme.sh) is the supported path; embedding it
*inside* Mainsail as a webcam-style iframe is the planned alternative (a later
phase).

## Releasing

`.github/workflows/ui-release.yml`, triggered by a `vX.Y.Z` tag (or manually,
with a `stable` flag). Publishes a GitHub Release carrying `mcu-updater-ui.zip`,
which is what `[update_manager mcu-updater-ui]` (`type: web`) consumes.

A few things about that mechanism are easy to get wrong and fail silently, so
the workflow enforces them rather than relying on discipline:

- The release **title** must equal the tag — Moonraker compares that, not
  `tag_name`, against `release_info.json`'s `version`.
- The zip's asset name must be explicit (`mcu-updater-ui.zip`), never left to
  default to `assets[0]`, which GitHub picks by sorting names.
- The tag must be valid semver and must sort above whatever is already
  published on the same channel — Mainsail's own Update Manager panel hides
  anything that fails `semver.gt(remote, local)`, even though Moonraker's own
  comparison is a plain string equality and would report the update anyway.
- `channel: stable` and `channel: beta` read different GitHub endpoints
  (`/releases/latest` vs. `/releases?per_page=1`), and neither is
  version-ordered — see the comment block at the top of `ui-release.yml` for
  what that means for publish order and for promoting an existing beta.

Unlike the Mainsail fork's release workflow, there is no upstream version to
sort under here — this repo owns its tags outright, so a plain ascending
`v0.1.0`, `v0.2.0`, … is enough.

## Talking to Moonraker (Phase 3)

`ui/src/api/` is the whole communication layer: `moonraker.ts` (the websocket
transport - JSON-RPC id correlation, a 15s client-side timeout per call since
Moonraker's own `call_method_with_response` has none, and reconnect with
backoff), `agent.ts` (the `server.extensions.request` funnel every `fw.*` call
goes through, plus error normalisation to `{code, message, data}`), and
`events.ts` (the `notify_agent_event` router for `state`/`bus`/`job`/`log` and
Moonraker's own `connected`/`disconnected`). `ui/src/store/agent.ts` is a plain
`reactive()` singleton wired to all three - no store library, matching this
repo's dependency-frugal default.

The UI refuses to render above `SUPPORTED_API_VERSION` (currently 3, must
equal `mcu_updater.API_VERSION` - `tests/test_ui_contract.py` asserts this on
every commit, no npm involved) and derives every control from `fw.ping`'s
`capabilities`, never from `phase`.

**Auth is intentionally partial in this phase.** The UI reads `/access/info`
and supports an API key from `localStorage`
(`mcu-updater-ui:apiKey`, sent as `X-Api-Key` over HTTP and as a `?token=`
query param on the websocket). It does **not** implement Moonraker's login
flow, so an install with `force_logins` enabled is not yet supported - a
trusted-client LAN install needs neither. The login form is deferred to a
later phase, not silently dropped.

`ui/src/App.vue` is currently a debug harness (connection state, the live
`fw.ping` JSON, a rolling event log, the raw `fw.status` JSON) rather than the
real UI - most of the debug surface is still there, but `targets[]` itself
now renders through `ui/src/components/TargetsView.vue`/`TargetRow.vue`
instead of only appearing in the raw JSON dump.

## Rendering `targets[]` (Phase 4)

One row component (`TargetRow.vue`) renders an MCU type and a display alike -
name, provider, the artifact's tone/label, and each device's tone/label/
presence - reading tone and label from the payload rather than re-deriving
them from `reason`, per `docs/agent-api.md`'s "one vocabulary, not four colour
maps" rationale. Rows are keyed on `provider:name` (`targetKey()` in
`ui/src/api/targets.ts`), not `name` alone, for the same reason the Mainsail
panel does: nothing stops an MCU type and a display sharing a name across
their separate config files.

The one place the row still reads `provider`-shaped state is deliberately
narrow: the empty-devices hint checks for `target.extra` (present only on a
display) rather than branching on `provider` directly, so the row stays
generic to whatever a third provider might add later.

`targets[].actions` and each device's own `actions[]` now render too, through
`ActionButton.vue` - see "Actions and jobs (Phase 5)" below. The decision
about where MCU-only type management (edit/remove a type) lives without a
`provider` branch is still unscheduled.

**Row detail is on demand**, via the new `fw.target.get {name, provider}`
(`fetchTargetDetail()` in `ui/src/store/agent.ts`) - "Show detail" on a row
fetches its full `fw.type.list`/`fw.device.list`-equivalent payload only when
opened, not for every row on every `fw.status` poll (the display branch of
that call is as expensive as a full `fw.status`, see `docs/agent-api.md`).

`scripts/fake_moonraker.py` fakes the agent's *unix socket* connection to
Moonraker, not a browser websocket, so it cannot drive this layer. Phase 3
and 4's own tests (`ui/src/**/*.spec.ts`) mock the `WebSocket` transport
instead; a real Moonraker/agent pair is still the only way to verify this,
and the visual result, end to end.

## Actions and jobs (Phase 5)

`ActionButton.vue` renders one entry from a `targets[]`/`devices[]` `actions[]`
array: a button whose `method`/`params` it never has to be taught, `blocked`
(the same `{code, message, data}` shape a failed call carries) shown as the
button's disabled title, and an optional `choices` (`{method, params, param}`)
that fetches its option list only when opened and puts the pick into
`params[param]` before invoking - it never learns what a profile, or anything
else offering `choices`, actually is.

**`blocked` is not the only gate.** `docs/agent-api.md` is explicit that a job
already running is deliberately *not* encoded in `blocked` - that is what
`job`/`locked_by` are for. `TargetRow.vue` derives a separate `busyReason` from
`state.job` and passes it down as `disabled`/`disabled-reason`, so a greyed
button from "something else is running" and a greyed button from "the payload
refused this" read differently, and neither one fakes the other's shape.

**A flashing method always confirms first, and the confirmation names real
devices.** `fw.flash`/`fw.flash_all` return their actual selection
(`boards`/`displays`) only *after* the job has already started - there is no
preview endpoint - so the confirmation dialog is built client-side instead,
from `targets[].devices[]`'s own `needs_flash`/`present` fields under the
action's own `scope` (`TargetRow.previewFor()`). If no target/device data is
available to name, the dialog says so and refuses to let the click through
rather than guessing. `ActionButton`'s `DESTRUCTIVE_METHODS` set is the one
place naming which methods this applies to.

**`JobPanel.vue`** renders the single job the agent ever runs at once: state,
0-based `progress.index` shown as `index + 1` of `total`, the streaming log
(via the store's existing gap-heal - see below), and a cancel button worded
from the job's own `kind` through `api/jobs.ts`'s `cancelIsImmediate()` -
`build`/`build_all` read "stops immediately", `flash`/`flash_all`/`update_all`
read "cancels after the current board or build finishes", per
`docs/agent-api.md`'s "Cancellation is not uniform" table. Deriving the
wording from `kind` rather than from `fw.job.cancel`'s own `immediate` return
means it still reads correctly after a page reload, when that return value is
long gone but the job (and its `kind`) is still live.

**The log gap-heal had a real bug, fixed in this phase.** `resyncLog()` in
`store/agent.ts` used to replace the entire rendered log with only the resync
tail, silently dropping every line rendered before the gap; it now keeps
lines up to the cursor and appends the resync's lines after them. It also now
tracks `state.logOmitted`, set when `fw.job.get`'s `log_from` comes back higher
than asked (the ring buffer already evicted the beginning) or `log_dropped >
0`, and `JobPanel.vue` shows an explicit "some earlier lines were dropped"
marker rather than letting the log renumber without comment.

**Not yet verified against a real printer.** Building, flashing, and
cancelling mid-run are all still to confirm on the bench board, per CLAUDE.md's
"Never interrupt a firmware write" and "bench board only" rules - this phase
ships `vitest`/`vue-tsc`/`vite build` clean, same caveat as Phase 4 carried
before its own printer verification.

## Kconfig in the browser (Phase 6)

A "Configure {family}" action (`method: "fw.kconfig.open"`, one per
configurable family a type declares - see `status.py`'s `kconfig_open` wiring)
rides on `targets[].actions[]` like any other action, but it does not fit
`ActionButton`'s normal invoke-and-done flow: opening one leaves a **session**
behind on the agent (a parsed Kconfig tree, held in memory because a full
parse is a few hundred milliseconds on a Pi), and everything after that talks
to the same session by its opaque id rather than re-sending `name`/`fw`.
`ActionButton.vue` special-cases `fw.kconfig.open` - on click it calls
`openKconfig(name, fw)` directly, no confirmation and no `choices` dance,
because opening a session is not destructive and has nothing to pick from
until the tree is in hand.

**One state slice, `state.kconfig`, is the whole session.** `store/agent.ts`
adds `KconfigState = KconfigMenu & {search, help}` (`api/kconfig.ts`), where
`KconfigMenu` (`session`, `revision`, `type`, `fw`, `dirty`, `breadcrumb`,
`nodes`) is exactly what `fw.kconfig.open`/`.enter`/`.up`/`.set`/`.reset` all
return - every one of those calls fully replaces the screen, so
`applyKconfigMenu()` is the one place that writes it, and it always resets
`search`/`help` to `null`: both are stale the instant the tree under them can
have moved. `search` and `help` are never part of the agent's own payload -
`api/kconfig.ts` says so explicitly - they are populated by their own calls
(`fw.kconfig.search`, `fw.kconfig.help`) layered onto the last menu, the same
approach `mainsail/src/store/server/fwUpdater/types.ts`'s `FwKconfigState`
uses.

**`KconfigDialog.vue`** is the session UI: breadcrumb (click any ancestor to
climb there - there is no direct-jump call, so `climbTo()` just calls
`fw.kconfig.up` the right number of times), a search box that replaces the
node list while active, the node list itself via `KconfigNode.vue`, and a
footer with Up / Discard (`fw.kconfig.reset`) / Save / Save & Build
(`fw.kconfig.save {build}`). Closing with unsaved edits (`dirty: true`) asks
for confirmation first - a save is the one thing here that cannot be
regenerated, so a stray click must not be able to lose it silently. A save
that also builds hands back nothing the dialog has to adopt itself: the
started job's `job`/`log` events arrive through the normal event path exactly
like a build kicked off any other way.

**`KconfigNode.vue`** renders one row by `kind`: `menu` (or any node with
`enterable: true`) is a destination, not a value, so it is a button that
emits `enter`; `bool` is a checkbox sending `y`/`n`; `choice` is a `<select>`
whose options carry the tree's own prompts as labels but send the option's
*symbol name*, not its label, as the value; `tristate` is a `<select>` over
raw `assignable` (`y`/`n`/`m`) since a tristate has no prompts to show;
everything else (`string`/`int`/`hex`) is a text input, committed on change
rather than per keystroke because each `set` is a round trip that can rewrite
the whole menu (`fw.kconfig.set`'s reply is the full current menu, for exactly
that reason - flipping the architecture symbol replaces nearly every row).
`editable: false` disables the control and shows a lock, rather than a control
that silently refuses to move: it means kconfiglib will not accept a change
right now, either because another symbol's `select` holds this one, or its
dependencies are unmet - and it is deliberately not the same signal as an
empty `assignable`.

**`kconfig_session_conflict` gets its own retry path in `ActionButton.vue`,
not in the dialog.** Opening a session with `force` unset fails if another
session already has unsaved changes to the same `(type, fw)` - two tabs
editing the same tree risks one save silently discarding the other's work.
The refusal's `data.code` is `kconfig_session_conflict`; `ActionButton`
recognises it after a failed `openKconfig()` and offers "Take over anyway",
which retries with `force: true`. This lives on the button that tried to
open, not inside `KconfigDialog`, because the dialog does not exist yet at
the point the conflict is discovered - there is no session to hold state in.

**Not yet verified against a real printer.** No menuconfig session has been
opened, edited, or saved through this UI on hardware - CLAUDE.md's "bench
board only" rule applies here too, since Save & Build can kick off a real
build. This phase ships `vitest`/`vue-tsc`/`vite build` clean, same caveat as
Phases 4 and 5 carried before their own printer verification.

## Embed mode (Phase 7)

`?embed=1` is for Mainsail's own **HTML Iframe** webcam service
(`service: iframe`, upstream since #2384) - it lets the standalone UI sit
inside Mainsail's webcam grid as a stream URL:
`http://<printer>:8090/?embed=1`, aspect ratio `4:3`.

`App.vue` reads the flag once, from `window.location.search`, and uses it to
skip everything that only earns its keep at a full page: the `<h1>`, the
"Connection" debug section, the raw `fw.ping`/`fw.status` JSON dumps, and the
rolling event log - all still there for a direct page load, all gone under
`embed`. What stays is the functional surface: the "Update required" and
"Error" gates, `TargetsView`, `JobPanel`, and `KconfigDialog`.

**The box is the iframe's, never the viewport's.** `HtmlIframe.vue` gives the
embedded page a fixed-aspect box it does not control the size of; assuming
`100vh` inside that box would size against Mainsail's own window instead.
`main.embed` gets `height: 100%; overflow-y: auto` so it fills and scrolls
*inside* whatever box it is handed - which only resolves to something
non-zero because `App.vue`'s `onMounted` also adds a `mcu-updater-embed`
class to `<html>` and `<body>` (and `style.css` gives both, plus the `#app`
div Vue 3 leaves behind after mounting, `height: 100%` in turn). All three
rules are scoped to that class, so a normal full-page load is untouched.

**Two known traps, not yet exercised against a real Mainsail:**
`HtmlIframe.vue` applies the webcam's rotate/flip transform to whatever it
embeds - leave both at their defaults (0) when adding the stream, since
nothing here expects to be shown upside down. And an `http://` iframe inside
an `https://` Mainsail is blocked as mixed content, so if Mainsail has TLS
the standalone UI needs it too (the commented `listen 443 ssl` block in
`scripts/nginx.sites-available-mcu-updater` is exactly for this).

**Not yet verified against a real Mainsail.** No iframe has actually been
added to a webcam grid and loaded against a live printer - this phase ships
`vitest`/`vue-tsc`/`vite build` clean, same caveat Phases 4-6 carried before
their own printer verification.

## Settings, adoption and the DFU/BOOTSEL wizard (Phase 8)

Three pieces, sharing one theme: every one of them was already possible
through the CLI or a config-file edit, and this phase just gives a browser
the same doors.

**`SettingsPanel.vue`** edits `fw.settings.set`'s `SETTABLE` subset -
`make_jobs`, `log_ring_size`, `clean_before_build`, `reseed_on_build`,
`dry_run`, `enable_flashing`, `allow_flash_while_printing` - and nothing
else; `stop_services`/`service_backend` are shown nowhere, matching
registry.py's own reasoning (docs/agent-api.md's "Settings" section) that
those describe how the host is wired, not a preference. `state.status.settings`
already carries the current values (`fw.status` embeds them), so there is no
separate load call - only `fw.settings.set` on save, replacing
`state.status.settings` wholesale from the reply rather than trusting the
draft.

**A saved `enable_flashing`/`allow_flash_while_printing` toggle does not
immediately unlock (or lock) the flash buttons elsewhere in this UI.**
docs/agent-api.md's "Settings" section explains why: Moonraker only registers
`fw.flash` et al. at handshake, so `fw.ping`'s live `capabilities` and what
Moonraker will actually dispatch can disagree until the agent reconnects.
`SettingsPanel` says this in a note rather than silently handing over a
button that 404s.

**`BusPanel.vue`** renders `state.bus` - every serial device on the host,
`tracked_by: null` ones offered a "Adopt as…" picker of existing MCU types,
calling `fw.serial.add`. The one bug worth flagging for anyone touching this
again: `state.bus` used to only ever get written by the `bus`
notify_agent_event, so the panel rendered empty on every fresh page load
until something was physically unplugged and replugged. `store/agent.ts`'s
`refreshStatus` now also seeds it from `fw.status`'s own `bus` key, which is
present on every load.

**`AddMcuWizard.vue`** is docs/agent-api.md's "Setting up a brand-new board"
flow, minus the two steps that already existed: pick an MCU type from
`targets[]`, scan (`fw.dfu.scan`/`fw.bootsel.scan`, chosen from whether the
type's chipset starts `stm32`/`rp2040`), then `fw.add_mcu.start`. It does not
poll or await the job itself - job submission emits its own `job` event
(agent/jobs.py's `on_job_change`), so `JobPanel` picks it up the same way it
picks up a build or a flash, with no extra wiring. What `JobPanel` adds for
`add_mcu` specifically is rendering the succeeded job's `candidates`/
`already_tracked` result with its own "Adopt" button per candidate - the
`fw.serial.add` step that closes the loop the wizard itself deliberately does
not take, mirroring why there is no `fw.add_mcu.confirm` on the wire either.

`add_mcu` was also missing from `api/jobs.ts`'s `JobKind` union, and its
cancel wording defaulted to "immediate" by falling outside
`DEFERRED_CANCEL_KINDS`. It is added to that set: `flash_initial_bootloader`
has no cancellation checkpoint inside a single DFU/BOOTSEL write, so
promising an immediate stop about a bootloader write would contradict
CLAUDE.md's "never interrupt a firmware write" the same way `flash` already
respects.

**Not yet verified against a real printer.** No settings change, adoption,
or DFU/BOOTSEL write has been exercised through this UI on hardware -
CLAUDE.md's "bench board only" rule applies hardest here, since
`fw.add_mcu.start` writes a bootloader. This phase ships
`vitest`/`vue-tsc`/`vite build` clean, same caveat every prior phase carried
before its own printer verification.

## Looking like the panel (Phase 9)

Phases 4-8 got the functional surface working; nothing had restyled it away
from browser defaults. This phase makes the standalone UI read as the same
product as the Mainsail fork's `FirmwareUpdaterPanel` - same palette, same
panel chrome, same row layout - **without adding a UI framework**:
`ui/package.json` still has exactly one runtime dependency (`vue`).

**The palette is copied out of the fork's own checkout, not invented.**
`ui/src/style.css` now carries CSS custom properties (`--color-surface`,
`--color-toolbar`, `--tone-attention`, etc.) read directly from Vuetify 2's
default dark/light theme presets and `mainsail/src/store/variables.ts` -
dark values live on bare `:root` (Mainsail defaults to dark), with a
`prefers-color-scheme: light` block overriding only the tokens, so light
mode (already supported via `color-scheme`) keeps working rather than
regressing to dark-only.

**Icons are copied, not depended on.** `ui/src/icons.ts` holds the SVG path
`d` strings for the ~20 glyphs `FirmwareUpdaterPanel*.vue` actually uses,
lifted from Material Design Icons (Apache License 2.0, via the fork's own
`@mdi/js` checkout) with an attribution header - so both clients render the
identical glyph without this one taking on `@mdi/js`'s several-thousand-icon
dependency for the handful it needs. `UiIcon.vue` renders one path at one of
three sizes.

**Two small layout primitives carry the rest:** `UiPanel.vue` (a titled card
with a 48px toolbar, an optional icon, a `#buttons` slot, and an optional
collapse toggle persisted to `localStorage` - the fork's equivalent persists
to Vuex) and `UiDialog.vue` (a backdrop + card + footer-actions pair,
generalising what used to be duplicated `.kconfig-backdrop`/`.kconfig-dialog`
markup in `KconfigDialog.vue` and `AddMcuWizard.vue`). Every panel in the UI
(`TargetsView`, `JobPanel`, `BusPanel`, `SettingsPanel`, the "Connection" /
`fw.ping` / raw-status / event-log sections in `App.vue`) is now a `UiPanel`;
every overlay (`ActionButton`'s choice picker, kconfig-conflict retry and
destructive confirm; `AddMcuWizard`; `KconfigDialog` and its nested
help/discard-confirm overlays) is now a `UiDialog`.

**`TargetRow.vue` adopts `FirmwareUpdaterPanelTarget.vue`'s layout exactly:**
a header line (name, descriptor, module version, device count, spacer,
artifact chip, profile chip, header actions, `⋮` overflow menu for
everything else), then one sub-row per device (a tone-coloured state icon,
truncating identity, spacer, version, verdict, device actions, detail
expander), then a trailing divider - replacing the old bordered `.target`
box. Actions themselves gained a `variant` prop on `ActionButton.vue`: `icon`
(the default, a row's own small icon buttons, coloured `primary` and
swapping glyph when an action *wants* doing - the same
build/flash/profile-primary logic `FirmwareUpdaterPanelTarget.vue` uses) or
`text` (dialog footers, the wizard). A blocked or busy icon action carries
its reason as a native `title` tooltip rather than inline visible text,
matching the fork's own `v-tooltip` behaviour.

**The profile chip is included** - `ProfileVerdict`/`ProfileChange` in
`ui/src/api/targets.ts` mirror the fork's `FwProfileVerdict`/
`FwProfileChange` (`mainsail/src/store/server/fwUpdater/types.ts`), replacing
the previous untyped `profile: Record<string, unknown> | null`. It renders
only for a managed profile, same as the fork - a type predating profiles
gets no chip at all, not a chip saying "unmanaged" on every row.

**Deliberately not in this phase:** the fork's summary chip row (stale /
unprovable / all-flashed / needs-flash / board / offline counts across the
panel's top) is new derived logic, not appearance, and the fork's own
comments record a real bug that kind of counting caused before - it is
scoped for its own approval rather than folded in here. Bulk actions
(`build_all`/`flash_all`) are likewise not wired into this UI at all yet, so
nothing was added to fake matching that part of the fork's toolbar - only
the Refresh-equivalent (Reconnect, in the Connection panel) and the existing
"Add new board…" launcher moved into the Firmware panel's own toolbar.

**Embed mode is unaffected by design, not by luck.** Page chrome (max-width,
padding) lives on `main` itself; `main.embed` clears the max-width and keeps
`height: 100%; overflow-y: auto`, so the `html.mcu-updater-embed` →
`body.mcu-updater-embed` → `#app` → `main.embed` chain Phase 7 built still
resolves - panel margins come from `.panel` itself, not from a page wrapper
that would break that chain.

**Not yet verified against a real printer or a real Mainsail iframe.** No
side-by-side comparison against a live `FirmwareUpdaterPanel` has happened
on hardware - this phase ships `vitest`/`vue-tsc`/`vite build`/`eslint`/
`prettier` clean, same caveat every prior phase carried before its own
verification.

## Building locally

```bash
cd ui
npm ci
npm run lint          # eslint
npm run typecheck     # vue-tsc --noEmit
npm run format:check   # prettier --check
npm run build           # vite build -> ui/dist
npm run test            # vitest run
```

`.github/workflows/ui-ci.yml` runs the same five, non-mutating, on every push
and PR touching `ui/`.
