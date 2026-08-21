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

### Do not reintroduce per-port board tracking

Removed deliberately in `9ebbaef`. This is an updater, not an asset tracker —
which port a board sat on last time is not a fact worth persisting, and keeping
it invites writes addressed to a remembered port rather than a confirmed one.

### Do not rename `needs_klipper_stopped`

It wants generalising to a per-type service list, and the rename must land
*with* that list or the new name is less true than the current one. Tracked in
the README TODO.

The same rule applies to `needs_ports_free` on `discovery.spec.Source`: do not
rename either to match the other.

### Do not rename `STATE_KLIPPER`

It means "running an application", not "running Klipper" — the bootloader
predicate in `discovery/spec.py` reads any non-Katapult firmware name as this
state, so a Cartographer sights as `STATE_KLIPPER`. That inversion is the point:
it stops every vendor fork being a case to handle.

The constant is what every flasher's `states` tuple matches on, so per the
`needs_klipper_stopped` precedent the rename lands with the thing that makes it
true, or not at all. The meaning is documented where it is defined.

### Do not delete `src/updatefw.py`

It is the documented entry point.

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

### Do not move the cancellation boundary

It stays *between* targets, in `flashers/batch.py`. Cancellation is never
checked inside a single write, because half an image is a brick.

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
