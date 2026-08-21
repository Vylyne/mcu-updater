# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at session
start.

Acted-on entries are **struck through** while they still carry context worth
having to hand, and **removed** once they do not — this file loads into every
session, so it is kept to live work. Removed entries stay in git history
(`git log -p NOTES.md`).

---

## 2026-08-21 — Fork's FW_SUPPORTED_API_VERSION was still 2, found while cutting v2.18.4-vylyne.20

`store/server/fwUpdater/actions.ts`'s `FW_SUPPORTED_API_VERSION` was never
raised past 2, even after Step 16b migrated the panel onto the api_version 3
wire shape. `isTooNew` (`state.apiVersion > FW_SUPPORTED_API_VERSION`) read
true against the real agent, so the panel showed "panel outdated" and never
fetched status at all - a real regression that would have shipped in
v2.18.4-vylyne.20's beta had it not been caught while preparing the release.
No test exercised this path. Fixed (`fix(fwUpdater): bump
FW_SUPPORTED_API_VERSION to 3`, fork commit `9ccdcbe2`) and folded into the
same tag before pushing.

`v2.18.4-vylyne.20` is on `Vylyne/mainsail` `mu/stable`, published as a
**prerelease** (beta channel) - bundles Step 16b's wire-fold migration, Step
20's type-edit dialog fix, and this constant fix. Not yet promoted to stable;
that's `gh release edit v2.18.4-vylyne.20 --prerelease=false --latest` per
`mu-release.yml`'s own comment, once it's been soaked - do not re-run the
release workflow with `stable: true` to promote, it rebuilds the tree.

## 2026-08-20 — Step 16b found two Mainsail-fork bugs beyond its own scope

While migrating the fork off `fw.display.*`/`kind` (`docs/rebuild-plan.md`
Step 16b, `Vylyne/mainsail` `mu/stable` b16dadb8), found and fixed one bug
already: `fw.status` stopped carrying `types`/`displays` at API_VERSION 2, but
`mutations.ts` kept reading them from that payload anyway, so the screen-detail
popover and the type-edit dialog had been silently empty since. Fixed by
fetching `fw.type.list`/`fw.device.list` directly (`refreshDetail` in
`actions.ts`). Full writeup in `docs/rebuild-plan.md`'s Step 16b log entry.

One thing found but **not fixed, on Vi's direction** (asked, scope was
getting wide): `vite.config.ts`'s `checker({ typescript: {...} })` has no
`vueTsc: true`, so `npx vite build`'s TypeScript pass only checks bare `.ts`
files — every `.vue` `<script>` block is unchecked. Caught because
`FirmwareUpdaterPanelTypeDialog.vue` reads `mcuType.firmware`, a field
`FwType` (`types.ts`) never declares, and the build reported nothing. Add
`vueTsc: true` before trusting `npx vite build` to catch a `.vue` script
error — right now it can't, and that's a wider fork-quality gap than one
missing field.

## 2026-08-20 — Step 15 found a real config.py bug, deferred not fixed

While rewriting `mcu-updater.cfg` in the target schema (Step 15,
`docs/rebuild-plan.md`), `Registry.load()`'s per-type loop
(`src/mcu_updater/config.py:380`, `for fw in fw_names:`) turned out to seed
an empty `FwConfig` slot for **every** globally-declared `[firmware ...]`
family, not just the two builtins (`klipper`, `katapult`). The real config
now declares `[firmware cartographer]` and `[firmware knomi_serial]`, so
every type - including plain STM32 boards with nothing to do with either -
picks up phantom "never built" artifact entries for both. Confirmed real
(not a fixture issue) by loading the sample directly and inspecting
`mcu.fws`; leaks into `fw.artifacts`, `fw.type.list`, and `type_status()`
for every board.

Escalated rather than fixed inline, since it's outside Step 15's scope
(fixture-only). **Vi's answer: note it, don't fix yet.** Two tests
(`test_artifacts_returns_both_firmwares`, `test_status_type_shape` in
`tests/test_agent_methods.py`) now pin the current, buggy behaviour
explicitly and are commented as a known bug - so a future fix has a clear
"this should go back to the narrower set" marker rather than having to
rediscover the leak from scratch. The likely fix is narrowing
`config.py:380`'s loop to `firmware.BUILTIN` instead of `fw_names` - see
Step 15's Progress log entry for the full reasoning.

**Reviewed and confirmed 2026-08-20** (planning session). Reproduced by
loading the real `mcu-updater.cfg` through `Registry.load()` and inspecting
`mcu.fws`: every type carries `cartographer` and `knomi_serial` slots it never
declared. `to_json()` and `fw_order()` both carry them, so it is on the wire,
not just in memory. Three things the entry above does not say:

- **Root cause is a read with a write side effect.** `mcu.fw()`
  (`config.py:163`) is `self.fws.setdefault(fw, FwConfig())`, so merely
  *asking* for a family's config **creates** it. Narrowing the loop fixes this
  call site; it does not stop the next global-list iteration doing the same.
  A non-mutating accessor beside `fw()` would close the class.

- **Blast radius is bounded to reporting - nothing builds or flashes a
  phantom family.** Every build/flash path uses `families()` (the declared
  list, verbatim) rather than `fw_order()`: `providers/kconfig_make.py:54`,
  `methods.py:733`, `:3714`, `:3845`. Verified: `bttebb36.families()` is
  `['klipper', 'katapult']`, correctly excluding both phantoms. So this is a
  display defect, not a safety one - which is what makes "note it, don't fix
  yet" the right call. It is not cosmetic though: it scales with declared
  families, so every new `[firmware ...]` section adds a phantom row to
  *every* type.

- **The suggested fix is wrong in one respect.** Narrowing to
  `firmware.BUILTIN` re-hardcodes klipper/katapult - the assumption Steps 5-6
  spent two commits removing - and would drop a genuine
  `cartographer_extra_args` on the cartographer type. Narrow to
  **`mcu.firmwares`** instead: the families the type actually declares.
  `fw_order()`'s built-ins-first ordering (`config.py:213`) still works, since
  it filters `self.fws` rather than assuming its contents.

## 2026-08-19 — Step 13 (RP2040 BOOTSEL flasher) shipped untested on hardware

`src/mcu_updater/flashers/bootsel.py` is new: copies a `.uf2` onto a mounted
`RPI-RP2` volume, the same first-time-bootstrap role `dfu_util.py` plays for
STM32. Fully exercised off-hardware (`paths.bootsel_root` / a fake mount), but
**never against a real RP2040** - there is none to hand. Two things this
cannot verify from a dev box:

- Whether the board actually automounts as `RPI-RP2` at all on the printer's
  host, and if so, under which of the two globs `bootsel_scan()` searches
  (`/media/*`, `/run/media/*`) - see Step 10's own `untested` note, unchanged
  by this step.
- Whether a plain `shutil.copy2` onto that mount is sufficient, or whether the
  real bootloader wants the file flushed/synced before it reads it back.

Try `mcu-updater add-mcu <rp2040-type>` on a bench board (never the toolhead)
once one is available, and update this entry once it's confirmed either way.
