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
   invalid install and never touches. If no release has been published yet
   (or the fetch fails), a placeholder page is left in place and `install.sh`
   says so; re-run it once a release exists.
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
