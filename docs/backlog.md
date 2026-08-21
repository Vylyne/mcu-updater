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

### Generalise `needs_klipper_stopped` to a "services to stop" list

The rename must land *with* the list, or the new name is less true than the
current one. Design sketched with Vi 2026-08-20 — recorded so it is not
re-derived.

**Already built, and worth not rebuilding:** `PioType.service` is already
per-type (`providers/pio.py:92`), with a deliberate absent-vs-blank distinction
(absent takes the default watcher; blank means "no watcher here").
`make_controller(name=...)` (`service.py:168`) already targets an arbitrary unit,
and `esptool.py:81-83` already pauses a type's watcher inside `prepared()`. What
is missing is the *list* and the layering, not the plumbing.

**Where the services belong.** On the firmware section: `knomi_serial` names both
`klipper` and `knomi_serial`, everything else inherits `klipper`. The coupling is
genuinely firmware-level — the klippy module and the watcher ship with the
firmware repo and are versioned together.

**The key is `managed_services:`, at every level** (Vi, 2026-08-20). Borrowed
from Moonraker's `[update_manager]`, which this project already ships a line of
(`scripts/moonraker-update-manager.conf`), so the spelling is already in the
user's `moonraker.conf` next door.

```ini
[updater]
managed_services: klipper                     # the outermost level

[firmware knomi_serial]
managed_services: klipper, knomi_serial       # OVERRIDE - replaces, never merges

[type bttebb36]
managed_services: klipper                     # OVERRIDE - replaces both parients, only last tier applies
```

- `[updater] service:` becomes `managed_services:` and takes a list.
- Setting it at any level is an **override, not an addition.** A firmware whose
  `managed_services:` omits klipper does *not* get klipper stopped. Say so
  plainly in the docs and the sample cfg — the point of choosing override over
  additive is that what the line says is what happens.
- **Same name at every level**, rather than `default_services:` at the top.
  Under "most granular wins" the outermost level *is* the same thing at the
  widest scope, so a distinct name would imply a distinction that does not
  exist.
- **No variable/interpolation mechanism.** Considered and dropped: multi-Klipper
  is not a real scenario on one host (one install serves one `printer_data`
  serves one Klipper, and under `service_backend: moonraker` the agent is bound
  to one Moonraker anyway), so the repetition that would have justified it does
  not arise.

⚠️ **Same word as Moonraker's, deliberately — but not the same rules.** Ours
takes **arbitrary systemd unit names**. Moonraker's
`[update_manager] managed_services:` is a restricted vocabulary: it accepts only
the section's own name, `klipper`, or `moonraker`, and rejects anything else —
this repo's own config comment records that. The two files sit side by side in
`printer_data/config` and open in the same Mainsail editor, so the docs must
state the difference rather than assume the shared name carries it.

Distinguish both from **`moonraker.asvc`**, a third thing again: the allowlist of
units Moonraker's `machine.services.*` API may touch. Under
`service_backend: moonraker` our `managed_services:` are actioned *through* that
API, so a unit must be in `asvc` for our list to have any effect — see the
allowlist section below. Three related names, three different meanings; the
configuration docs need to say so once, clearly.

**Resolution rule: the most granular `managed_services:` wins.** One key, one
semantic, at every level — override, never merge:

```text
[updater]  ->  [firmware X]  ->  [type Y]        (all: managed_services:)
```

Vi's read, and it is the right one: a case where two types running the *same*
firmware need different services is hard to construct — they would almost
certainly want different firmwares. So the type level is a completeness rule, not
an expected use. Specifying it costs nothing, and it means `[type X] service:`
(`providers/pio.py:92`) does not need retiring — it becomes the bottom of the
same chain instead of a parallel mechanism.

⚠️ **The rename inverts meaning — a migration hazard, not a mechanical edit.**
Today `[type knomi] service: knomi_serial` means *"pause this watcher **in
addition to** stopping klipper"*, because klipper is global and unconditional.
Under the new rule `[type knomi] managed_services: knomi_serial` means *"stop
**only** knomi_serial"* — klipper keeps holding the tty and the write races it.
Same for blank: today `service:` blank means "no watcher to pause" while klipper
still stops; under the new rule `managed_services:` blank means **stop nothing at
all**, which is coherent and occasionally useful but is not what an existing
blank meant.

So the migration must **not** rewrite `service:` to `managed_services:` in place.
An existing `[type X] service: <unit>` becomes
`managed_services: klipper, <unit>` — or better, moves up to the firmware section
where it belongs. Whatever does the migration needs a test for exactly this: both
spellings parse cleanly and the failure is silent.

⚠️ **The override's one footgun, worth an explicit example rather than a guard.**
`knomi_serial` needs *both* klipper and its watcher: the klippy module holds the
display's tty, so a `managed_services: knomi_serial` that forgets klipper leaves
Klipper holding the port during the write. That is the interleaved-serial failure
— an intermittent handshake error or a partial write, not a clean "port busy".
The sample cfg should carry the knomi line spelled out in full as the canonical
illustration of an override that names everything it needs.

**Keep the flasher boolean.** It answers a different question — *does this write
mechanism need the holders released?* `DfuUtil` says no because entering DFU was
already somebody else's problem, and a board in DFU was never on the Klipper bus.
If a config list replaced the flag, the DFU path would start stopping Klipper for
a board Klipper never held. The two compose: **the flasher says whether, config
says which.**

**⚠️ The blocker to solve first: Moonraker's allowlist fails silently.**
`make_controller`'s own docstring records it — *"A unit Moonraker refuses to
touch because it is not in `moonraker.asvc` simply fails to stop, which is why
the only caller of this with a `name` uses `paused()` and does not treat that as
fatal."*

Best-effort is correct for pausing a watcher. It is **not** correct for a list of
services that must all be down before a firmware write: a unit missing from
`moonraker.asvc` would stay running, keep holding the port, and the write would
race it — which is not a clean "port busy" but an intermittent handshake failure
or a partial write. So a services list needs **verified-stopped semantics and a
hard failure** — if a named unit did not stop, refuse the write rather than
proceed.

**Whose job the allowlist is** (Vi, 2026-08-20): not ours. Anyone shipping a
service that Klipper interacts with, or that Moonraker must be able to stop, adds
its own `moonraker.asvc` entry — the same way `knomi_serial`'s own installer
would. `install.sh` does not go editing another project's allowlist. What this
project owes is **documentation**: say it in the sample cfg and in the README's
configuration section, next to `managed_services:`, so a third-party firmware author
knows the requirement exists before their first flash races a service that never
stopped.

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
