# Backlog

> **Do not read this file unless it is named explicitly.** It is not part of any
> session's default context and nothing here is scheduled. `NOTES.md` is the
> inbox for live work; `README.md`'s `## TODO` is the near-term list; this is
> the pile of things that are either somebody else's to fix or ours to do
> someday. Adding to it is cheap on purpose.

---

## Upstream — Mainsail (`mainsail-crew/mainsail`)

Fork lives at `Vylyne/mainsail`, branch `mu/stable`. Upstream's default branch
is `develop`; there is no `main` on upstream. Any PR branches off `develop`.

### `.vue` script blocks are not type-checked

`vite.config.ts` configures `vite-plugin-checker` with `typescript: { root,
buildMode: false }` and **no `vueTsc: true`**, so `npm run build`'s TypeScript
pass covers bare `.ts` files only. Every `.vue` `<script>` block is unchecked.

Found the hard way: a fork component read `mcuType.firmware`, a field its own
declared interface never had, and the build reported nothing. That shipped a
bug that silently rewrote a config value.

This is an upstream gap, not a fork one — it affects anyone writing Mainsail
components.

**Two ways to raise it:**

1. **Issue** — cheapest. Report that the checker's TS pass silently skips
   `.vue`, so a type error in a component cannot fail the build. Let upstream
   decide the fix.
2. **PR off `develop`** — add `vueTsc: true` plus a `vue-tsc` devDependency.
   Almost certainly **not** a one-line change: upstream has never type-checked
   `.vue`, so turning it on will surface a backlog of pre-existing errors across
   their own components. A credible PR either fixes those or lands the check in
   a non-blocking mode first.

**Verify before promising anything:** this tree is **Vue 2.7.10** with
`vue-class-component` / `vue-property-decorator` and Vuetify 2. `vue-tsc`'s Vue 2
support is version-dependent, and a bare `npx vue-tsc` fetches the latest, which
may not handle 2.7 at all. Expect to pin a version and possibly set
`vueCompilerOptions.target: 2.7` in `tsconfig.json`. Confirm the tool runs
usefully here *before* treating either option as viable.

### No panel plugin API

The reason this project maintains a Mainsail fork at all: there is no extension
point for a third-party panel, so a fork with a documented 4-edited-file budget
is the cheapest way in (`docs/mainsail-fork.md`).

A genuine upstream feature request — some registration hook for an
externally-supplied panel component — would let the fork be retired entirely.
Large ask, low odds, but it is the only thing that removes the rebase burden
permanently rather than managing it.

---

## Upstream — Katapult (`Arksine/katapult`)

### `flashtool.py` has no machine-readable output

Our flash-time bootloader offset guard scrapes
`Application Start: 0x{addr:4X}` out of `flashtool.py -s`'s human-readable
stdout. That guard is what stands between a mismatched image and an unbootable
board, and it is one print-statement reword away from silently not matching.

A `--json` flag on the status path — or any stable machine-readable form of the
`connect_btl()` handshake — would make it robust. Worth an issue; the ask is
small and the safety argument is concrete.

Note the format quirk if raising it: `0x{self.app_start_addr:4X}` is a *minimum
width*, not zero-padded, and uppercase.

---

## Ours — low priority

Nothing here is scheduled. Moved out of the runbook so it stops loading into
every session.

### Other low-priority items

- **Retire the agent's singular-`firmware` compat layer**
  (`agent/methods/registry.py:237`, `:290`, `:314`). It exists so the panel's
  type dialog can keep posting `firmware` + `katapult_installed`. Retiring it
  means changing the dialog's submit shape in the same release.
- **CAN device discovery and flashing.** A CAN node has no `/dev/serial/by-id`
  entry, so identity has to come from `canbus_uuid` in `printer.cfg` — an
  inventory source that does not exist yet. `flashtool.py -i <iface> -u <uuid>`
  is the write side.
- **Prebuilt-image provider** — fetch a release asset instead of building. The
  `Provider` protocol already fits it; the missing part is provenance, since
  staleness currently compares a source tree against a build sidecar.
- **Event-driven bus watching via pyudev**, replacing `BusWatcher`'s adaptive
  polling. knomi-serial is gaining pyudev, so the dependency may arrive on the
  host anyway — but this project is stdlib-only by policy, so it would need a
  graceful fallback rather than a hard requirement.
- **Standalone embeddable UI**, so the panel is not tied to a Mainsail fork.
  Large; see the plugin-API item above for the cheaper version of the same win.
- **Satellite host support** — a second host with no Moonraker and no agent,
  driven by a systemd timer.
- **Flaky teardown** `RuntimeError` in
  `test_an_unknown_inbound_method_gets_an_error_not_silence`. Unreproduced;
  order/timing-dependent agent-service teardown.

---

## Ours — not doing

Recorded so they stop being re-proposed. Reasons are in the runbook's
"Do not do".

- Plugin auto-discovery for providers/flashers (`pkgutil`, entry points). This
  process holds the exclusive lock, writes firmware, and has NOPASSWD
  `systemctl` — importing whatever landed in a directory is privilege
  escalation.
- Enabling the Katapult deployer by default. It overwrites the bootloader region
  and is linked against the *currently installed* bootloader's offset; a wrong
  guess bricks the board with no software recovery.
- Per-port board tracking. We are an updater, not an asset tracker.
