# Decisions

Things that look like gaps and are not. Each one was decided deliberately, and
each one has been proposed again at least once — so the reasoning lives here
rather than in a commit message nobody re-reads.

This file is for *standing* decisions. Rules that a change can violate silently
live in [CLAUDE.md](../CLAUDE.md)'s ground-rules table instead; the split is
that a ground rule is checked before every commit, and a decision here is
consulted before starting work that would undo it.

## Do not do

### Do not add plugin auto-discovery for providers, flashers or discovery sources

No `pkgutil`, no entry points. This process holds the exclusive lock, writes
firmware, and has NOPASSWD `systemctl` for Klipper — importing whatever `.py`
landed in a directory is privilege escalation, not a plugin system.

The extension point is deliberately manual and documented: **one module + one
line in the registry tuple**. `providers/registry.py`, `flashers/registry.py`
and `discovery/registry.py` each say so in their own docstring.

### Do not implement CAN discovery or flashing

Deliberately deferred. A CAN node has no `/dev/serial/by-id` entry at all, so it
needs an identity source that does not exist yet — it would come from
`canbus_uuid` in `printer.cfg`, which nothing here reads.

This is why `discovery/topology.py` is named but empty (below), and why the CAN
boxes in the README feature list are unticked rather than absent.

### Do not enable the katapult deployer

It overwrites the bootloader region and is linked against the *currently
installed* bootloader's offset. A wrong guess bricks the board with no software
recovery — the failure mode is a DFU hunt, and on a toolhead that means opening
the hotend assembly.

### Do not "fix" `-dirty` in reported versions

Normal for makefile-patched types: Klipper stamps the version from git while the
patch is applied, so the tree is briefly dirty and
`v0.13.0-712-g6d43f8b3-dirty-...` is the correct output. It must not be
suppressed, because it must not read as out of date.

### Do not synthesize a sha into Cartographer's `CONFIG_VERSION`

Cartographer's fork patches Klipper's `buildcommands.py` to stamp
`CONFIG_VERSION` (a literal from the `.config`) instead of `build_version()`'s
git describe. The describe is still computed, just discarded — so `mcu_version`
carries no commit at all, and `_running_sha` (`agent/methods/status.py`)
correctly returns `None`.

Since `read_config_version()` returns the string verbatim, appending the fork's
HEAD before `make` — `CARTOGRAPHER 6.2.0-gd34db33` — would work: `_FW_SHA_RE`
would match it and the whole existing sha-comparison path would run unchanged.
Rejected anyway, because the cost lands on things that matter more than the
convenience:

- A synthesized value in the saved `.config` differs from the vendor seed, so
  `profiles.status` reports `customised` permanently — destroying the one
  signal that means "the user edited this".
- It moves `config_sha256` on every commit of the fork, so `artifact_status`
  reports `CONFIG_CHANGED` forever.
- Avoiding both means injecting it outside the saved config, which makes our
  builds unreproducible by the vendor's own instructions.
- It does not even remove the need for the sha-less path: a board on the
  official prebuilt binary still reports a bare string, so `states.VERSION_ONLY`
  has to exist regardless. Synthesizing a sha would only move *our own* boards
  onto the good path, at that cost, for no boards we don't already control.

So the sha is gone, and putting one back costs more than it buys. A cartographer
is instead judged by comparing `CONFIG_VERSION` itself against what the built
`.config` carries (`profiles.stamped_version`), backed by our own flash record
the same way the sha path already is — see `states.VERSION_ONLY` and
`FlashLog.entry_for`'s version-based discard clause.

### Do not reintroduce per-port board tracking

Removed deliberately in `9ebbaef`. This is an updater, not an asset tracker —
which port a board sat on last time is not a fact worth persisting, and keeping
it invites writes addressed to a remembered port rather than a confirmed one.

### Do not rename `needs_ports_free`

`discovery.spec.Source.needs_ports_free` is a distinct question from
`flashers.spec.Flasher.needs_services_stopped` - one asks whether a discovery
pass needs the bus quiet, the other whether a write needs its device's
holders released - and the two must not be renamed to match each other. The
flasher side generalised to a per-type `stop_services` list in the
`stop_services` module; this one did not need the same treatment and is
unrelated to it.

### Do not rename `STATE_KLIPPER`

It means "running an application", not "running Klipper" — the bootloader
predicate in `discovery/spec.py` reads any non-Katapult firmware name as this
state, so a Cartographer sights as `STATE_KLIPPER`. That inversion is the point:
it stops every vendor fork being a case to handle.

The constant is what every flasher's `states` tuple matches on, so per the
`needs_services_stopped` precedent (it was `needs_klipper_stopped` until the
`stop_services` list landed with it) the rename lands with the thing that
makes it true, or not at all. The meaning is documented where it is defined.

### Do not delete `src/updatefw.py` or 'mcu-updater.py'

they are the documented entry points.

### Do not fold `scripts/usb_topology.py` into the package

It is a human diagnostic with its own argparse CLI and no caller in `src/`.
Moving it makes `discovery/` look complete while adding the one source nothing
consumes.

### Do not build `discovery/topology.py`

The slot is named because it helps to know where the thing would go — the sysfs
USB tree as a `Source`, which is where CAN identity would land. Leave it empty;
it is blocked by the CAN decision above, not by the seam.

### Do not give `Confidence` a fourth degree of certainty

Three tones and a tri-state `safe_to_write`, built the way `states.py` is. A
"probably" bucket is one more thing for two call sites to disagree about.

`safe_to_write` is never `True` on absent evidence, for the reason
`DeviceStatus.needs_flash` already enforces: absence of evidence is not
evidence.

### Do not spell the stop-list key `managed_services:`

`docs/backlog.md` sketched `managed_services:`, borrowed from Moonraker's
`[update_manager] managed_services:` next door in the same file. Rejected when
the list actually landed, for two reasons.

It drops the false cognate: Moonraker's key accepts only a restricted
vocabulary (the section's own name, `klipper`, or `moonraker`), while ours
takes arbitrary systemd unit names - two files, open in the same Mainsail
editor, using the same word for different rules is the kind of thing a user
copies from one into the other and gets a silent refusal or a silent no-op.

And `stop_services:` is imperative rather than declarative: *stop these*
reads as an instruction, and instructions replace - which is what makes
`stop_services.py`'s override-never-merges resolution rule self-evident
instead of a rule to memorise. A declarative name invites the merge the
design specifically rejects.

### Do not widen sudoers for an arbitrary `stop_services` unit

`scripts/sudoers.d-mcu-updater` grants NOPASSWD for exactly three commands on
the literal unit `klipper` - "three exact commands for one unit, no
wildcards," deliberately. A `stop_services` list can name any unit, and
`install.sh` does not go editing that file to widen it for one.

A unit missing from sudoers (systemd backend) or `moonraker.asvc` (moonraker
backend) hard-fails instead: `service.services_stopped` verifies every stop
and raises `ServiceControlError`, naming the exact sudoers lines to add,
before any write happens. This is deliberately not best-effort the way
`service.paused()` is - a firmware write racing a service that still holds
the port is not a clean failure, it is a corrupted flash. Widening the
allowlist for a third-party unit (a display's own watcher, say) is that
project's own installer's job, the same way `knomi_serial`'s would be -
not ours to do on their behalf, and not `install.sh`'s to guess at.

### Do not serve the standalone UI from inside the agent's git checkout

`~/mcu-updater-ui` (`UI_PATH`), never `~/mcu-updater/ui/dist`. Moonraker's
`type: web` update manager (`net_deploy.py`, `_validate_release_info`) refuses
a `path` inside a git repository — the install is marked invalid and never
updates. See [docs/standalone-ui.md](standalone-ui.md).

### Do not move the standalone UI under `~/printer_data/mcu-updater/`

It would technically work — Moonraker does not forbid it — but
`_extract_release()` `rmtree()`s a `type: web` path before every update, and
one directory up from there is `.updater.state`, the flash-recovery journal.
A Moonraker-managed directory that gets wiped on a schedule has no business
sharing a parent with state that must survive every update. `UI_PATH`
defaults to `~/mcu-updater-ui`, a sibling of `~/mcu-updater` itself, matching
what `~/mainsail` and `~/fluidd` already do.

### Do not edit Mainsail's own nginx site file to add the standalone UI as a subpath

nginx has no Caddy-style `import` that lets one server block inject a
`location` into another from the outside. The only way to run the standalone
UI as a Mainsail subpath is hand-editing the file KIAUH/mainsail-config owns
and rewrites on update — fragile, and not something `install.sh` does on a
user's behalf. The supported path is the UI's own nginx site on its own port
or FQDN (`scripts/nginx.sites-available-mcu-updater`); an iframe embed
(planned) is the supported way to fold it back into Mainsail visually.

### Do not move the cancellation boundary

It stays *between* targets, in `flashers/batch.py`. Cancellation is never
checked inside a single write, because half an image is a brick.

### Do not revert Kconfig bools to a bare checkbox

Tried, reversed on 2026-08-29. A bare `<input type="checkbox">` was meant to
read as menuconfig's `[*]` symbol rather than a preference toggle, but the
standalone UI is not menuconfig - visual consistency with the rest of the
panel's `.switch` toggles won. Tristate nodes are unaffected: they render
through a `<select>` (y/n/m), not a checkbox, so no third state is lost by
this.

## Conclusions that close an avenue

### `vue-tsc` cannot type-check the Mainsail fork, at any version

Investigated 2026-08-21. The `.vue` `<script>` blocks in `Vylyne/mainsail` are
unchecked by `npx vite build` — `vite.config.ts`'s `checker({ typescript })`
covers bare `.ts` only — and that gap hid a real bug through every gate. Adding
`vueTsc: true`, or a `vue-tsc` CI job, does not close it:

- **Newest `vue-tsc` (3.3.10**, the only major compatible with this tree's
  `typescript@6.0.3`) emits **6307** `error TS2339`, every one shaped
  `Property '<x>' does not exist on type 'Vue3Instance<...>'`.
  `@vue/language-core` infers a component's public type from a
  `defineComponent(...)`-shaped export, which a `@Component class X extends Vue`
  decorator export never produces. A real regression would be error #6308 among
  6307 identical false positives.
- **`vueCompilerOptions.target` is not the knob.** Tested explicitly at both
  `2.7` and `3`, identical error count both times, with the override confirmed
  read via `@vue/language-core`'s `CompilerOptionsResolver`. That setting
  changes template-directive nuances, not whether class-component properties are
  visible on `this`.
- **Old `vue-tsc` (1.8.27**, contemporaneous with `vue-class-component`'s peak
  usage) crashes against `typescript@6.0.3`:
  `Search string not found: "/supportedTSExtensions = .*(?=;)/"`. It patches
  TypeScript's internals by regex against compiled `tsc` source, and the pattern
  is gone.

The two failure modes bracket the whole option space: new `vue-tsc` runs but is
structurally blind to this tree's component pattern; old `vue-tsc` understood
that pattern but cannot load against this TypeScript version.

**So the gap stays open, and it is a real one** — treat `npx vite build` as
proving nothing about `.vue` script blocks, and review those by hand. The
upstream half (raising the class-component pattern with
`mainsail-crew/mainsail`) is in `docs/backlog.md`. Do not spend the fork's
edited-file rebase budget on a fallback without asking.
