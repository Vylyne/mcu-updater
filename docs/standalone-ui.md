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
