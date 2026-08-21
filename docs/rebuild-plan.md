# mcu-updater: schema-first rebuild — implementation runbook

> **Written to be executed by a model with none of the planning context.**
> Read "Ground rules" and "Do not do" in full before touching anything. Every
> step ends with a gate command; if a gate fails, stop and fix before moving on.
> Do not batch steps together — each numbered step is one commit.

---

## Handoff

**This file is the source of truth.** It was produced by a planning session that
is still open and will review the finished work. You have the whole design; you
do not need to re-derive it. If this file and the code disagree about intent,
this file wins — but if this file and the code disagree about *fact* (a line
number, a function name), the code wins and you note it in the Progress log.

**Scope.** Steps 1-15 are done; this file has been trimmed to live work
(Step 16 onward). Their specs and log entries are in git history
(`git log -p docs/rebuild-plan.md`) if a decision ever needs re-reading.

**How to work:**

- One step, one commit. Do not batch. Do not skip a gate because a step "looks
  safe" — three of the bugs this plan fixes were invisible to 985 passing tests.
- Line numbers here were accurate at planning time and drift as you commit.
  Treat them as starting points; confirm by reading before you edit.
- Append to the **Progress log** at the bottom of this file after every step.
  That log is what gets reviewed.

**Stop and ask rather than deciding, if:**

- A gate fails for a reason this plan does not anticipate, and the fix would
  change behaviour rather than just fix a test.
- The flash-time bootloader offset check (`flash.flash_katapult`) would need to
  *proceed* on a case it currently refuses. Never loosen that guard to make
  something pass — what it prevents is an unbootable board.
- A step needs a new third-party dependency (the answer is no — see Ground
  rules — but if you think you have found the exception, ask).
- The schema needs a key this plan does not list. Adding one is a design
  decision, not an implementation detail.
- Anything requires flashing real hardware. Every on-printer step is Vi's to
  run, not yours.

**What you cannot verify and must not claim:** anything needing a printer, a
board, or a POSIX kernel. `posix_only` tests silently skip on Windows. If a step
ships untested for that reason, say so in the Progress log and in `NOTES.md` —
not in a passing test.

---

## Context

This started as a shell script and grew a CLI, a Moonraker agent, a Mainsail
panel, then PlatformIO display flashing. The `providers/` (build systems) and
`flashers/` (write paths) seams already fixed the structural half: each is one
Protocol with a static registry.

What was never fixed is the *vocabulary* those seams are configured with. Config
still says `[type x] provider: platformio` (builder on the board, not the tree),
`firmware: cartographer` (singular), and picks a flasher three different ways
depending on the call site. Two live bugs come out of that:

1. **`fw.flash` cannot flash Cartographer at all** — one line assumes klipper.
2. **A Cartographer flashed from the CLI did not boot.** Recovery was DFU →
   katapult → the vendor's prebuilt bin. A guard for exactly this exists in the
   codebase and never runs on the flash path.

Goal: fix both, move config to the model in "Target schema", delete every legacy
path, and give the repo a features list, a TODO, and a notes file.

**Not a rewrite.** `kconfig.py`, `pio.py`, `flash.py`, `devices.py`, `lock.py`,
`service.py` and both `spec.py` files encode behaviour verified on real hardware.
Keep them.

---

## Scope discipline

Only read files I explicitly name or point to. Do not read additional files to "get context," "understand the project," or "see how things connect" unless I ask you to.
If you think reading more files would help, ask first. One sentence: "Want me to also read X?" Wait for my answer.
This applies to every task in this project. No exceptions for "just checking" or "quick look."

## Ground rules

Violating any of these produces a bug that tests will not catch.

| Rule | Why |
| --- | --- |
| **LF line endings everywhere**, repo and working tree | Ships to a Linux printer; a `\r` in a shebang becomes `bad interpreter: python3^M`. `.gitattributes` pins it. Run `python scripts/check_line_endings.py` before every commit. |
| **stdlib only** — never add a dependency | `pyproject.toml` `dependencies = []` is deliberate. The agent talks to Moonraker over a unix socket with nothing but stdlib. |
| **Python 3.11 is the floor** | Bumped from 3.9 on 2026-08-21; both printers run Trixie's 3.13. `X \| Y` and `match` are fine now, and annotations are PEP 604 throughout — do not reintroduce `Optional[X]`, ruff will reject it. **Keep `from __future__ import annotations`**: it is still load-bearing, because `config.py` returns `MakefilePatch \| None` from a method inside that class and the name is unbound at def time without it. |
| **Never pip-install kconfiglib** | Klipper and Katapult each vendor their own *locally patched* copy. Their sentinels (`MENU`, `BOOL`) are different objects across trees; cross-comparing silently yields `False`. Always load from the tree being configured. |
| **`posix_only` tests silently skip on Windows** | Every flock/signal/`/proc` assertion. A green Windows run proves nothing about locking. CI runs Linux too — trust that. |
| **Use `scripts/mutation_test.py`, never a throwaway** | An inline mutation script once stranded a sabotaged guard on disk. `tests/test_mutation_helper.py` exists because of it. |
| **Run `mutation_test.py` one spec at a time** | Never in parallel, and never a full sweep under a shell timeout shorter than it needs. An interrupted sweep strands a *live* mutation in the source — this happened once, in `firmware.py`, and only `test_no_mutation_is_left_live_in_the_source` caught it. After any interrupted run, read the hygiene test's output; do not settle for "the command finished". |
| **Git Bash mangles `/FI`-style switches** into paths | Silently turns wait-for-process loops into no-ops. Use PowerShell for those. |
| **Never interrupt a firmware write** | Cancellation is checked *between* targets, never inside one. Half an image is a brick. |
| **Bench board only** for any flash test | Never the toolhead. Recovery from a bad flash there is a DFU hunt inside the hotend assembly. |

---

## Do not do

These look like gaps. They are decisions.

- **Do not add plugin auto-discovery** (`pkgutil`, entry points) for providers or
  flashers. This process holds the exclusive lock, writes firmware, and has
  NOPASSWD `systemctl` for Klipper — importing whatever `.py` landed in a
  directory is privilege escalation. Adding one is *one module + one line in the
  registry tuple*, and that is the documented extension point.
- **Do not implement CAN discovery or flashing.** Deliberately deferred; a CAN
  node has no `/dev/serial/by-id` entry at all, so it needs an identity source
  that does not exist yet.
- **Do not enable the katapult deployer.** It overwrites the bootloader region
  and is linked against the *currently installed* bootloader's offset — a wrong
  guess bricks the board with no software recovery.
- **Do not "fix" `-dirty` in reported versions.** Normal for makefile-patched
  types; it must not read as out of date.
- **Do not reintroduce per-port board tracking.** Removed deliberately; this is
  an updater, not an asset tracker.
- **Do not rename `needs_klipper_stopped`.** It wants generalising to a per-type
  service list, but the rename must land *with* the list or the new name is less
  true than the current one. Out of scope.
- **Do not delete `src/updatefw.py`.** It is the documented entry point.

---

## Target schema

One file, `~/printer_data/config/mcu-updater/mcu-updater.cfg`. `[updater]`
unchanged. Two other section kinds:

```ini
# A firmware family: a source tree, how it is built, what it emits.
[firmware klipper]
source: ~/klipper                 # default: ~/<name>
builder: kconfig_make             # default: kconfig_make
artifact: klipper                 # default: <name>

[firmware katapult]
source: ~/katapult
bootloader: true                  # a bootloader, not an application

[firmware cartographer]
source: ~/cartographer-klipper
artifact: klipper                 # a fork keeps its parent's output name

[firmware knomi_serial]
source: ~/knomi_serial
builder: platformio

# A device type: what a board of this model is, and what it runs.
[type bttebb36]
chipset: stm32g0b1xx
firmware: klipper, katapult       # a LIST
serials:
    230048001750304158373620-if00  # mcu EBBT0

[type flylllplusbuffer]
chipset: stm32f072xb
firmware: klipper, katapult
klipper_makefile_patches:
    src/Makefile -> src-y += buffer.c
serials:
    4C0033000957465331323720-if00  # mcu T0_buffer

[type cartographer]
chipset: stm32g431xx
firmware: cartographer, katapult
profile: config.CartoV4USB
serials:

[type knomi]
chipset: esp32
firmware: knomi_serial
env: knomi                        # REQUIRED for a platformio type; no default
klipper_section: knomi_serial
service: knomi_serial
```

Rationale for each change, so it is not re-litigated:

- **`builder:` on `[firmware]`, not `[type]`.** How a tree compiles is a property
  of the tree. `[type] provider:` is deleted; a type's provider is derived.
- **`firmware:` is a list.** Replaces `McuType.firmware` (single) *and* the
  `katapult_installed` flag *and* the implicit "and also katapult" in
  `McuType.families()`. A type that uses no bootloader simply omits it.
- **`chipset:` required on every type**, PlatformIO included (`esp32`). It is the
  sole input to flasher selection, so it cannot be optional on one kind.
- **`env:` required on a PlatformIO type, no default.** The type name is wrong
  (knomi-serial ships a `knomi_i2cscan` diagnostic env beside the firmware one),
  and `platformio.ini`'s `default_envs` is a *list of what builds by default*,
  not a canonical choice. Naming it is the only unambiguous answer.
- **No `flasher:` key anywhere.** Flashers declare which chipsets and device
  states they can write (`flashers/spec.py`), and selection is a capability
  match via `flashers.registry.select_for`.

---

## Steps

**Steps 1–15 are complete.** Their specs and progress-log entries were removed
on 2026-08-20 to keep this file to live work. Recover them from git history
(`git log -p docs/rebuild-plan.md`) if a decision needs re-reading. Everything
from those steps that still *binds* was promoted out of the log and lives in
**Ground rules**, **Do not do**, **Target schema** and **Appendix B**.

### Step 16 — docs and the Mainsail fork

- `docs/agent-api.md` — bump `api_version`, document `firmwares` as a list, drop
  the removed keys, update the `Family` wire type with `builder`/`bootloader`.
- `README.md` Configuration section — the new schema; tick the Features boxes
  this work completed.
- `docs/layout.md` — the migration section is now historical.
- **Fold `fw.display.*` into `fw.build` / `fw.status`.** This is a wire change
  and belongs in this release with the others. Most of it is already done and
  the rest is deletion, not redesign — `device_list` (`methods.py:2054`) is
  already the real method with `display_list` (`:2049`) a two-line alias to it,
  and `display_build` (`:2290`) is a pass-through to `_pio_build` whose own
  docstring makes the argument: *"Nothing here is display-shaped: `fw.build`
  routes by the type's provider."*
  - Delete the `display_list` / `display_build` aliases and their method-map
    entries (`:1957-1958`), leaving `fw.device.list` (`:1951`).
  - `methods.py:888,899` still **emits** `fw.display.build` as a pio target's
    action method — that is the string the panel calls, so it changes with the
    fork in this same release. Point it at `fw.build`; drop
    `fw.display.build` from the gated list (`:2001`).
  - Rename `display_status` (`:449`), `display_types` (`:2285`) and
    `_display_target` (`:853`) to provider-neutral names.
  - The two `# Deprecated alias for provider` fields (`:759`, `:924`) go here
    too — same release, same reason.

- **Mainsail fork** (`Vylyne/mainsail`, branch `mu/stable`, at
  `C:\git\github\mainsail`) must move to `targets[]`.

  **It is within budget — do not decline it on file count.** Every file below is
  fork-*added*; the migration touches none of the four *edited* files that are
  the rebase surface. `docs/mainsail-fork.md` previously miscounted the added
  files and read as if the budget covered them; it has been corrected.

  Call sites, from a direct grep of the fork (wider than the Step 16 survey,
  which missed `getters.ts` and named five files rather than seven):

  | File | What breaks |
  | --- | --- |
  | `store/server/fwUpdater/actions.ts:281,297` | calls `fw.display.build` / `fw.display.flash` by name — hard `-32601` |
  | `store/server/fwUpdater/getters.ts:163,166` | already falls back to `fw.build`/`fw.flash`; **drop the stale half** |
  | `store/server/fwUpdater/mutations.ts:48,61` | populates `state.types`/`state.displays` from keys gone from the wire |
  | `store/server/fwUpdater/types.ts:328` | declares the retired per-field names |
  | `FirmwareUpdaterPanelTarget.vue` | reads the retired per-field names |
  | `FirmwareUpdaterPanelBulkDialog.vue` | consumes `types`/`displays` |
  | `FirmwareUpdaterPanelTypeDialog.vue` | consumes `types`/`displays` |

  Two traps from `docs/mainsail-fork.md` and hard experience: any script editing
  `src/locales/en.json` must be followed by `npx prettier --write` on it
  (Python's `sorted()` is case-sensitive ASCII and reorders pre-existing keys);
  and **run `npx vite build` last**, after every edit, because it type-checks
  the tests too. **Caveat, found in Step 21:** that pass covers bare `.ts` only —
  `.vue` `<script>` blocks are unchecked unless `vue-tsc` runs separately.

**Gate:** `GATE` + in the fork: `npm run test:unit`, then `npx vite build` last.

### Step 17 — split `agent/methods.py`

3,891 lines / 173 KB. Split into `agent/methods/` by surface: `status.py`,
`registry.py`, `build.py`, `flash.py`, `profiles.py`, `bulk.py`, with the method
map assembled in `__init__.py`.

**No `displays.py`.** A display is not a kind any more — it is a `[type]` whose
firmware's builder is `platformio`, and Step 16 removes the last display-named
method. A `displays.py` here would re-enshrine in the file layout exactly the
distinction Steps 5–9 deleted from the schema. The pio-specific helpers land in
`build.py` and `status.py` by surface, like everything else.

Purely mechanical — no behaviour change in the same commit. Done last so it never
overlaps a real change in review.

**Gate:** `GATE`. The diff should be almost entirely moves.

---

### Step 18 — narrow the phantom `FwConfig` slots ⚠️ contract change

`src/mcu_updater/config.py`. Was Appendix B's deferred bug; pulled into scope
because `fw_order()` feeds `artifacts`, which Steps 19–20 have to document and
re-declare — fix it first so the contract describes the intended shape rather
than the defect.

- `:380` — `for fw in fw_names:` iterates *every globally declared* family, and
  `mcu.fw()` (`:163`) is `setdefault`, so a read **creates** the slot. Every type
  ends up carrying `cartographer` and `knomi_serial` entries it never declared.
  Iterate `mcu.firmwares` instead.
- **Not `firmware.BUILTIN`.** That re-hardcodes klipper/katapult — the assumption
  Steps 5–6 spent two commits removing — and would drop a genuine
  `cartographer_extra_args` on the cartographer type.
- Add a **non-mutating accessor** beside `fw()` and use it wherever a slot is only
  read. Narrowing this loop fixes one call site; the `setdefault`-on-read shape is
  what would recreate the bug at the next global-list iteration.
- `fw_order()` (`:213`) needs no change — it filters `self.fws` rather than
  assuming its contents.
- **Unpin the two tests that assert the bug.** `test_artifacts_returns_both_firmwares`
  and `test_status_type_shape` (`tests/test_agent_methods.py`) carry "known bug"
  comments; flip them to assert the narrow set.

**Gate:** `GATE`. `artifacts` should lose the keys a type does not declare.

### Step 19 — make `docs/agent-api.md` true

No code. The document is declared the single source of truth for the panel
contract, and it is stale — which is *why* Step 20's bug shipped: the fork
implements what this file says.

Known-stale, from one grep — **treat as a starting point, not a complete list.
Re-read the whole document against `McuType.to_json()` and the live method
payloads.**

| Location | Documents | Reality |
| --- | --- | --- |
| `:162` | `"firmware": "klipper"` | `firmwares: [...]` since Step 6 |
| `:163` | `"katapult_installed": true` | deleted in Step 6 |
| `:165` | `"installed": true` on the katapult block | removed from `FwConfig` |
| `:172` | `artifacts: {klipper, katapult}` | keyed per declared family |
| `:184`, `:192`, `:198` | `stale` / `stale_reason` | retired in Step 14 |
| `:250` | `"kind": "mcu"` on `Target` | retired in Step 16b |
| `:114` | `"api_version": 2` | contradicts `:9`'s **3** |

The `Family` section (`:352-357`) is already correct — Step 16a updated that much.
Document `artifacts` as it stands *after* Step 18.

**Gate:** `python scripts/check_line_endings.py`, plus spot-check two or three
payloads against a live `Api` call rather than trusting the source read.

### Step 20 — fix the fork against the corrected contract ⚠️

> **Corrected 2026-08-20.** This step's original text was written on a premise
> that does not hold, caught by the implementer before any fork code was
> touched. It claimed the agent emits `firmwares` with no `firmware` key and
> that `katapult_installed` was deleted, and concluded the type dialog silently
> rewrites a type's firmware to klipper. **That is wrong.** The error came from
> reading `McuType.to_json()` in `config.py` — the *registry serialization* —
> and assuming it was the `fw.type.list` wire shape. It is not: `type_status()`
> (`agent/methods/status.py:265`) builds its own payload with a **derived**
> singular `firmware` (the application) and a **derived** `katapult_installed`.
> Step 19 had already documented this correctly against live payloads; this step
> was never reconciled with it.

**The live payload**, pulled from the sample config for the cartographer type:

```text
top-level keys:      artifacts, cartographer, chipset, firmware, katapult,
                     katapult_installed, name, needs_flash, serials
firmware           -> 'cartographer'
firmwares          -> absent
katapult_installed -> True
artifacts keys     -> ['cartographer', 'katapult']
```

`mutations.ts` assigns `fw.type.list` straight into `state.types` with no
mapping, so the runtime object carries `firmware` regardless of `FwType` never
declaring it. **There is no firmware-corruption bug. Do not "fix" it.**

**The real bug, same payload.** Every per-family field in the dialog is
hardcoded to `klipper`, and for any type whose application is not klipper those
resolve to `undefined`:

| Line | Reads | For cartographer |
| --- | --- | --- |
| `:213` | `mcuType?.artifacts?.klipper?.has_bin` | always `false` — no `klipper` key |
| `:217` | `mcuType?.klipper?.makefile_patches` | always empty |
| `:254` | `mcuType?.klipper?.extra_args` | always blank |

`hasBinary` (`:213`) gates **both safety warnings** — chipset-changed (`:43`)
and firmware-changed (`:81`). So on a Cartographer, the board that has already
bricked once, both warnings are silently suppressed. That is the actual defect
and it is a safety one.

**The fix, in the shape the payload actually calls for:**

- `store/server/fwUpdater/types.ts` — `FwType` **gains `firmware: string`**. It
  is simply missing; the field has always been live. **Do not add `firmwares`
  and do not remove `katapult_installed`** — both would break fields the agent
  really sends.
- Replace the fixed `klipper` / `katapult` members and the fixed
  `artifacts: {klipper, katapult}` pair with a **map keyed by family name**
  (`Record<string, FwFirmwareConfig>` / `Record<string, FwArtifact>`), since
  `artifacts` is built from `mcu.fw_order()` and carries exactly the families a
  type declares.
- `FirmwareUpdaterPanelTypeDialog.vue` — `hasBinary`, `patchLines` and the
  extra-args fields resolve against **the type's own application family**
  (`mcuType.firmware`), not the literal `klipper`.
- Check `stale_reason` (`:25`), `artifact_state` (`:329`) and `firmware_state`
  (`:378`) against the corrected `docs/agent-api.md` before touching them —
  Step 19 rewrote that document against live payloads, so it is now the
  authority, not this step's prose.

Every file here is fork-**added**, so this spends **no rebase budget** — see
`docs/mainsail-fork.md`, the budget covers edited files only.

**Gate:** `npx eslint src` · `npx vitest run` · `npx prettier --check` on the
scoped paths · `npx vite build` **last**. Add a vitest case that a non-klipper
type surfaces `hasBinary === true` when its own artifact exists — the assertion
that would have caught this.

### Step 21 — close the `.vue` type-checking gap

`vite.config.ts:84-89` configures `checker({ typescript: {...} })` with no
`vueTsc: true`, so `npx vite build` type-checks bare `.ts` only. Step 20's bug
lives in a `.vue` `<script>` block and was invisible to every gate.

**Fix it in CI, not in the build.** `vite.config.ts` is an *upstream* file (last
touched by upstream `ec6e2a58`, `5ee21d42`) and `vue-tsc` is absent from
`package.json` (also upstream) — the direct fix costs two upstream files, taking
the rebase surface 4 → 6. Instead add a step to `.github/workflows/mu-ci.yml`,
which is fork-added and unbudgeted:

```yaml
- name: type-check .vue script blocks
  run: npx vue-tsc --noEmit
```

- **Do not add `vue-tsc` to `package.json`.** `npx` fetches it — but see the
  version trap below before assuming the command as written works.
- ⚠️ **This tree is Vue 2.7.10** (`vue-class-component`,
  `vue-property-decorator`, Vuetify 2). `vue-tsc`'s Vue 2 support is
  version-dependent and a bare `npx vue-tsc` fetches the newest release, which
  may not handle 2.7 at all. Expect to pin a version, and possibly to set
  `vueCompilerOptions.target: 2.7` in `tsconfig.json`. **Confirm the tool runs
  usefully here before treating this step as a one-liner.**
- Upstream has never type-checked `.vue`, so expect pre-existing errors. If it is
  noisy, **scope the run to fork-owned paths** — a permanently red job is worse
  than no job.
- If `vue-tsc` cannot run usefully against this Vue 2 / class-component tree,
  **say so and stop.** Do not spend the upstream-file budget as a fallback
  without asking.
- Correct the claim in **Step 16's spec above** that `npx vite build`
  "type-checks the tests too" — true for `.ts`, false for `.vue`. It is not in
  Ground rules; that is the only copy.

**Gate:** the job passes on `mu/stable`, **and fails** on a deliberately broken
`.vue` field read. A checker that cannot fail has not been verified.

The upstream half of this — raising it with `mainsail-crew/mainsail` as an issue
or a PR off `develop` — is recorded in `docs/backlog.md`. Not part of this step.

### Step 22 — on-printer verification (Vi only)

The **Verification** section below, which has never been run. **Do not start
before Step 20 ships** — it is done through the panel, and the panel currently
corrupts a type's firmware on save.

Also still open and needing hardware: **Step 13's RP2040 BOOTSEL flasher**, which
shipped untested. Whether the board automounts as `RPI-RP2` under either glob
`bootsel_scan()` searches, and whether `shutil.copy2` alone suffices or the mount
needs a sync. See `NOTES.md`, 2026-08-19.

### Steps 23–28 — the discovery surface (the Inventory axis)

**Not queued behind Step 22.** That is a hardware gate, not a code step. Steps
23–26 are dev-box work and can start immediately; 27 needs a bench board; 28 is
the only one that touches the fork.

**Why now.** `providers/spec.py:28-31` and `flashers/spec.py:33-38` each defer
this axis in near-identical words — "there are two implementations of it and the
third is not committed." That was true when discovery meant `/dev/serial/by-id`
plus `dfu-util -l`. It is not true now. There are **six**:

| Source | Lives in | Returns |
| --- | --- | --- |
| `/dev/serial/by-id` scan | `devices.py:134` | `BusDevice` |
| `dfu-util -l` | `devices.py:292` | `list[dict[str, str \| None]]` |
| `RPI-RP2` mount | `devices.py:381` | `list[str]` |
| knomi listen pass | `providers/pio.py:297` | `WatcherDevice` |
| watcher `devices.json` | `providers/pio.py:214` | `WatcherDevice` |
| Klipper `printer.objects.query` | `agent/methods/status.py:1376` | dict payload |

Three return types for one question, and every caller adapts. The seam's own
stated criterion for readiness is met on its own terms.

⚠️ **Read this before writing any of these steps.** The knomi pre-flash
re-discovery **already exists, already works, and is already mutation-pinned** —
`scripts/mutations/display-flash.json` carries "identity is verified once the
ports are free" and "a screen that did not answer is not flashed at its old
port". These steps are **not adding a missing guard**. They un-weld a working one
from `flashers/esptool.py` so `flashtool`, the CLI and the agent can reach it and
be held to it. A step read as *new* safety invites a redesign of a guard that is
correct today. Move it; do not improve it.

**The problem it solves, precisely.** A CH340K reports no USB serial at all, so
several identical displays collapse onto indistinguishable by-id names. The only
durable identity a screen has is the six hex characters it broadcasts itself, so
its port must be re-resolved **after Klipper and the watcher are stopped** —
the only moment the ports are free and identity can be *resolved* rather than
*remembered*. Every other source describes where a display *was*.

### Step 23 — `discovery/spec.py` + `discovery/registry.py`, nothing moved

New package `src/mcu_updater/discovery/`. Vocabulary and Protocol only; no
implementations, no importers. Green by construction.

- **`Sighting`** — frozen dataclass: `id` (durable identity: by-id serial, DFU
  serial, knomi device id; `""` when the source cannot give one), `address` (what
  you hand a tool), `state`, `source`, `detail`. Modelled on `FlashTarget`
  (`flashers/spec.py:70`): a key plus an envelope, `to_json()` dropping `detail`.
- **Reuse `devices.STATE_*` verbatim** for `state`. A parallel vocabulary for the
  same facts is a second thing to keep in step, which is the failure
  `states.py` exists to have already fixed once.
- ⚠️ **Identity and state are different axes, and this step is where that gets
  decided.** Identity is **chipset + serial** — it is what the silicon is, it
  does not change when you write to it, and it is what a lookup matches on.
  State is *what the board is currently running*, which changes every time you
  flash it. The firmware name belongs to the second and has no business in the
  first.
  They are conflated today: `BusDevice.state` (`devices.py:88`) returns
  `self.fw.lower()` for anything that is neither klipper nor katapult, so a
  Cartographer reports the string `"cartographer"` as a *state*. That is a
  firmware name wearing a state's clothes, and `7bbf152` removed the last place
  it was doing identity work — dropping the hardcoded klipper filter from
  `flash_katapult`'s lookup so a Cartographer could be found at all.
  **The rule, decided.** Once chipset+serial have matched, the firmware name is
  read as one thing only: **are we in the bootloader or not?** It is a predicate,
  not a family.

  ```
  fw in KATAPULT_NAMES  ->  STATE_KATAPULT   # in the bootloader
  anything else         ->  STATE_KLIPPER    # running an application
  not on the bus        ->  STATE_OFFLINE
  ```

  That **inverts today's default**, and the inversion is the point.
  `BusDevice.state` currently allowlists klipper, allowlists katapult, and falls
  through to `self.fw.lower()` — so every unrecognised name becomes its own
  unmatched state string. Defaulting to "running an application" instead means
  Cartographer stops being a case to handle, and so does the next fork nobody has
  written yet. `KLIPPER_NAMES` is then no longer load-bearing for state at all.
  The family name it enumerates under travels in `detail`, available without
  steering dispatch.

  ⚠️ **`STATE_KLIPPER` then means "running an application", not "running
  Klipper".** Do not rename it — the constant is what every flasher's `states`
  tuple already matches on, and per "Do not do"'s `needs_klipper_stopped`
  precedent a rename lands with the thing that makes it true or not at all.
  Document the meaning where it is defined.

- ⚠️ **Do not apply that inversion to `is_mcu` / `find_untracked`.** It is only
  sound *after* identity has matched — a tracked board is known to be ours, so
  the sole open question is bootloader-or-not. An **untracked** candidate has no
  identity to match against, and there the firmware name is the only evidence a
  parsed by-id entry is a board at all. `is_mcu` (`devices.py:96`) must keep its
  allowlist: a CH340 enumerates as `usb-1a86_USB_Serial-if00` and parses into a
  perfectly well-formed `BusDevice`, so an inverted default would put a Knomi
  display in the adoptable list, one tap from being tracked and having Klipper
  built and flashed at it. Two questions, two rules — the docstring there already
  explains why, and it stays true.
- **`Confidence`** — built the way `states.py` is, and for the same reason: **the
  reason is the fact**, with `tone`/`label`/`safe_to_write` derived rather than
  stored, so an inconsistent pair cannot be constructed at all. Reasons:
  `ANSWERED` (it spoke, just now, ports free), `UNIQUE_BUS_ID` (kernel names it
  with a die-derived serial), `REMEMBERED` (a map or config written earlier by
  something else), `POSITIONAL` (only topology identifies it), `UNCONFIRMED`
  (something is at this address; nothing vouches for what).
- `safe_to_write` is **tri-state and never `True` on absent evidence** — the rule
  `DeviceStatus.needs_flash` (`states.py:244`) already enforces, for the same
  reason: absence of evidence is not evidence.
- **`Source` Protocol**, mirroring `Flasher`: `name`, `label`, `states`,
  `needs_ports_free`, `sight(bench) -> list[Sighting]`.
- **Reuse `flashers.spec.Bench`** (`flashers/spec.py:53`) rather than a second
  host object. It already carries `paths`, `settings` and the `controller`
  factory, which is exactly what a source needs.
- `SOURCES = ()`. **Static, never `pkgutil`** — see "Do not do".
- Tests mirror `tests/test_states.py`: every reason has a tone and a label, an
  unknown reason raises, `safe_to_write` is never `True` on absent evidence.

**Gate:** `GATE`. Nothing imports the package yet, so the suite count should rise
and nothing else move.

### Step 24 — move the three bus sources behind the seam

`discovery/byid.py`, `discovery/dfu.py`, `discovery/bootsel.py`, out of
`devices.py`. Looks risky — five importers — and is mechanical.

- **`devices.py` keeps every public name as a thin re-export.** No call site
  changes in `flashers/flash.py`, `flashers/esptool.py`, `agent/methods/status.py`,
  the CLI or the TUI.
- **`tests/test_devices.py` must pass untouched.** That is this step's real gate.
  A test that needed editing means the move was not a move.
- `dfu.py` takes `flash.dfu_selector` with it — the preference order it encodes
  (serial ▸ path ▸ devnum) is a fact about how well each field survives a
  replug, which is a discovery fact, not a flashing one.
- `dfu_serial_for` (`devices.py:232`) goes to `dfu.py` too. It is the only thing
  connecting a DFU serial to a board you know about.

**Gate:** `GATE`. The diff should be almost entirely moves.

### Step 25 — move the two knomi sources out of `providers/pio.py`

> **Corrected 2026-08-21 by Step 25b.** This step's destination for the two
> knomi sources — plain `discovery/listen.py` / `discovery/watcher.py`, flat
> beside the three generic bus sources — was itself wrong, not just imprecise.
> `providers/pio.py`'s own docstring (`:19-24`) already said what the modules
> are: firmware integrations, not host scans. Naming that in the file tree is
> Step 25b's whole job. The text below is left as originally written; where it
> says `discovery/listen.py` / `discovery/watcher.py`, read
> `discovery/knomi_serial/listen.py` / `discovery/knomi_serial/watcher.py`.

`discovery/listen.py` (the broadcast listen) and `discovery/watcher.py` (the
`devices.json` map). `providers/pio.py` keeps its build-and-write half —
`build`, `upload`, `artifact_status`, `source_state`, `resolve_port`,
`firmware_bin` — and loses discovery entirely.

- `WatcherDevice` becomes a `Sighting` with `detail={"fw": ..., "var": ...}`.
  `present` maps to `Confidence`: `ANSWERED` from the listen pass, `REMEMBERED`
  from the map.
- ⚠️ **`DEVICE_MAP_VERSION = 1` and the `devices.json` shape are somebody else's
  contract** (`providers/pio.py:176`). Read that schema; never write a new one.
  A file announcing another version stays ignored rather than guessed at — a
  half-understood port is a write to the wrong display.
- `listen.py` sets `needs_ports_free = True` and **`sight()` raises** when called
  outside a stop. Not a warning. The listen pass costs six seconds and opens real
  serial ports; a hint would eventually land it on the `fw.status` poll path,
  where it would fight Klipper for the port every few seconds.
- Keep `_DISCOVER_MARKER` and the `DISCOVER_PYTHON_CANDIDATES` reasoning intact —
  the marker exists so a stray deprecation warning on stdout cannot be mistaken
  for the answer, and the interpreter choice is about which `python3` has apt's
  `python3-serial`.
- Discovery tests split out of `tests/test_pio.py` (785 lines) into
  `tests/test_discovery_listen.py` / `test_discovery_watcher.py`.
- `tests/test_repo_hygiene.py:215`
  (`test_pyserial_is_declared_because_discovery_shells_out_for_it`) will need its
  path updated. It is the only hygiene test that names this code.

**Gate:** `GATE`.

### Step 25b — name the knomi sources for the firmware they integrate with

Not in the original plan; raised by Vi mid-Step-26 and scoped as its own step
before Step 26 resumed. `byid.py`, `dfu.py` and `bootsel.py` are generic host
scans — each answers a question true of any board. `listen.py` and `watcher.py`
are not: `listen.py`'s discovery snippet does `import knomi_serial as k` and
calls `k.discover_reports()` inside that klippy module's own source tree;
`watcher.py` reads `devices.json`, a shape that module's watcher process owns
(`DEVICE_MAP_VERSION`). Both sat flat beside the generic three, so nothing in
the tree said which sources were firmware-scoped — which is what actually
stalled Step 26's implementation (see that step's log).

- Move `discovery/listen.py` and `discovery/watcher.py` into
  `discovery/knomi_serial/`, a new subpackage. `byid.py`/`dfu.py`/`bootsel.py`
  stay flat — they landed in Step 24 and moving them again is churn without
  clarity, and it matches how `providers/` and `flashers/` keep their own
  implementations flat.
- **Not a merge into one `knomi_serial.py`.** `read_device_map` (watcher) and
  `_parse_discovered` (listen) carry textually identical "no port" / "lowered
  id" guards, and `scripts/mutation_test.py` only mutates the first match per
  file. Step 25's own log records that splitting them into two files is what
  took `scripts/mutations/pio.json` from 9 guards to 11 — merging back would
  silently return 2 of those 11 to untested, with the spec still reporting
  all-CAUGHT. Two modules, one subpackage.
- `discovery/knomi_serial/__init__.py` re-exports the public names (`discover`,
  `source_dir`, `DEVICE_MAP_VERSION`, `WatcherDevice`, `device_map_path`,
  `read_device_map`) the same `as`-suffixed way `devices.py` and
  `providers/pio.py` already do.
- Relative imports in both moved files gain one level (`..` → `...`);
  `listen.py`'s `from .watcher import WatcherDevice` does not change — both
  files stay siblings.
- `providers/pio.py`'s re-export block and docstring repoint to
  `discovery.knomi_serial`.
- Tests rename with the source: `tests/test_discovery_listen.py` →
  `tests/test_knomi_listen.py`, `tests/test_discovery_watcher.py` →
  `tests/test_knomi_watcher.py`. The listen tests monkeypatch by dotted string
  (`"mcu_updater.discovery.listen.shutil.which"`, ×7) — every one needs the new
  path, the same trap Step 24 hit with `bootsel_scan`.
- `scripts/mutations/pio.json`'s 5 per-mutation `file` entries, its `command`
  array, and its `_comment` (which should say **why** the two files stay
  separate, not just where they live).
- `tests/test_repo_hygiene.py:218`'s docstring names the old path.

**Gate:** `GATE`, expect the suite count **unchanged** (pure move) —
plus `python scripts/mutation_test.py scripts/mutations/pio.json`, standalone,
expecting **11 guards, all CAUGHT** (a report of 9 means the split property was
lost) — plus `tests/test_devices.py` passing with **zero edits**, proving the
bus half is unaffected.

### Step 26 — `discovery.confirm()`; `port_for` becomes a caller

The payoff step, and the one that makes the pre-flash check reusable.

- `confirm(bench, targets, *, sources)` runs **inside** the Klipper stop, after
  watchers are paused, and returns a `Confidence` per target. That ordering is
  the knomi_serial docs' own — Klipper holds the port, the watcher contends for
  it, discovery needs both gone.
- `esptool.port_for` (`flashers/esptool.py:148`) **keeps its three cases and its
  exact warning wording** and reduces to a caller. The *policy* — a device that
  stayed silent while its siblings answered is not there — moves into the seam as
  a rule about `Confidence`.
- The fourth case stays too: a screen with no id at all falls back rather than
  failing. Failing it would take flashing away from installs that have it today,
  to punish them for what their klippy module does not say.
- **Behaviour-preserving.** `scripts/mutations/display-flash.json` must stay green
  **with no edits**, then gains anchors for the generalised guard. Run it **one
  spec at a time**; see Ground rules.
- Dry run still skips discovery entirely. A rehearsal that opens real serial ports
  is not a rehearsal.

**Gate:** `GATE`, then
`python scripts/mutation_test.py scripts/mutations/display-flash.json` —
unedited, and green.

### Step 27 — extend confirm to `flashtool` ⚠️ behaviour change

An MCU gets the same confirmed-at-write-time ledger a screen has.

- The two `find_device` calls inside the stop, at the top of
  `flash.flash_katapult`, become a `UNIQUE_BUS_ID` sighting. **Cited by name, not
  line: this function is churning.**
- ⚠️ **The `select_for` trap — read before writing a line of this step.**
  `BusDevice.state` (`devices.py:88`) falls through to `self.fw.lower()`, so a
  Cartographer running its application reports state `"cartographer"`. No
  flasher declares that: `Flashtool.states` is `(STATE_KLIPPER, STATE_KATAPULT)`
  (`flashers/flashtool.py:29`). It is **not a live bug today** — the only
  production caller of `select_for` is `flash_initial_bootloader`
  (`flashers/flash.py:580`), which computes its own state and never passes an
  observed one.
  This step is what would arm it. The moment a batch picks a flasher from a
  *sighted* state rather than a computed one, a Cartographer matches nothing and
  `select_for` raises `UnsupportedChipsetError` telling the operator to flash
  katapult by hand — the same class of failure `7bbf152` fixed in the lookup,
  one layer up in the dispatch.
  **Step 23's bootloader-predicate rule is what disarms it**, and it must be in
  place first: under that rule a Cartographer running its application sights as
  `STATE_KLIPPER`, which `Flashtool` already declares, and no fork is ever a
  case again. If Step 23 shipped without it, do not wire sightings into
  `select_for` — go back and finish Step 23.
- A board whose by-id entry vanished between selection and write is **refused
  with a reason** rather than raising `DeviceNotFoundError` from inside
  `flash_katapult`. Same outcome, a better-shaped one — and the batch already
  records a failure by catching one.
- The flash record (`FlashLog.record`) gains `confidence`.
- ⚠️ **Do not fold this into Step 26.** 26 is behaviour-preserving and 27 is not;
  batching them means a hardware regression has two candidate causes.
- ⚠️ **Needs the printer. Bench board only** — never the toolhead.

**Gate:** `GATE`, plus `scripts/mutations/targets.json` and
`flasher-selection.json`, one at a time. Then the on-printer checks below.

### Step 28 — `confidence` on the wire ⚠️ contract change — DEFERRED

Recorded so it is not rediscovered; **deliberately not scheduled.**

- `fw.status` / `fw.device.list` carry `confidence` per device, so the panel can
  distinguish "confirmed" from "remembered" instead of rendering `present` as
  though it answered the question. `device_list`'s own comment
  (`agent/methods/status.py:1510`) already says `present` is "necessary but
  nowhere near sufficient" — the panel does not know that.
- Bumps `api_version` → a fork edit → **and a `FW_SUPPORTED_API_VERSION` bump in
  `store/server/fwUpdater/actions.ts`**. That constant was missed once already
  and shipped a panel that never fetched status at all; see `NOTES.md`,
  2026-08-21.
- **Deferred because it is the only step here that spends fork budget**, and
  23–27 are useful without it. Take it only when something in the UI actually
  needs to show the distinction.

### Steps 23–28 — do not

- **Do not fold `scripts/usb_topology.py` into the package.** It is a human
  diagnostic with its own argparse CLI and no caller in `src/`. Moving it makes
  the package look complete while adding the one source nothing consumes.
- **Do not build `discovery/topology.py`.** Name the slot if it helps; leave it
  empty. That is where CAN identity would live, and "Do not do" says no.
- **Do not rename `needs_klipper_stopped`** to match `needs_ports_free`. "Do not
  do" is explicit: the rename lands with the per-type service list or not at all.
- **Do not give `Confidence` a fourth degree of certainty.** Three tones,
  tri-state `safe_to_write`, as `states.py` has. A "probably" bucket is one more
  thing for two call sites to disagree about.
- **Do not move the cancellation boundary.** It stays between targets in
  `flashers/batch.py:100`. Half an image is a brick.

---

## Verification

**Per step:** the `GATE` block above. CI additionally runs Python 3.11 (the
floor) and Windows.

**Guard-level**, per the project's own rule (README, "Checking that a guard is
load-bearing"):

```
python scripts/mutation_test.py scripts/mutations/targets.json
```

Anchors that already exist and must keep passing: the flash path reading the
type's own family, the flash-time offset refusal
(`scripts/mutations/flash-offset-diagnostic.json`, 10 guards), and `select_for`
refusing an impossible chipset/state pair. The offset one matters most —
removing it must fail a test, because what it prevents is an unbootable board.
Run specs **one at a time**; see Ground rules.

**On the printer**, in order. The dev box cannot test what matters here.

1. `updatefw status` — every type resolves; nothing reads as unmanaged.
2. `updatefw build cartographer`. Then confirm the offsets agree *before* any
   write: the app's `FLASH_APPLICATION_ADDRESS` against the `Application Start:`
   the handshake reports.
3. `updatefw flash <carto-serial>`, then `fw.flash` from the panel.
   ⚠️ **Bench board only** — this is the board that bricked, and recovery is
   DFU + katapult + a vendor bin.
4. `updatefw update-all --dry-run`, then for real.
5. **Klipper is running and ready after every one of these.** That is this
   project's existing release gate and it does not change.

---

## Appendix A — README feature list (draft)

```markdown
## Features

Build systems ("builders" — one module + one registry line to add another):
- [x] `kconfig_make` — Klipper, Katapult, and forks (menuconfig + make)
- [x] `platformio` — anything with a `platformio.ini`
- [ ] prebuilt images — download a release asset instead of building

Flashing:
- [x] `flashtool.py` — Katapult over USB, STM32 and RP2040
- [x] `dfu-util` — bare STM32, first bootloader install
- [x] `esptool` — ESP32, via PlatformIO
- [ ] RP2040 BOOTSEL — copy a `.uf2` to the mounted volume
- [ ] CAN — `flashtool.py -i <iface> -u <uuid>`
- [ ] Katapult deployer — replaces a bootloader; no software recovery if wrong

Firmware and boards:
- [x] Multiple firmware families, each with its own tree and builder
- [x] Multiple firmwares per board type
- [x] Per-type saved menuconfig answers, per firmware
- [x] Per-type Makefile patches
- [x] Vendor profile seeding, custom profiles, drift detection
- [x] Flash-time bootloader offset check
- [x] Board tracking by `/dev/serial/by-id` serial
- [x] Displays re-identified at flash time, once the ports are free
- [ ] Discovery surface — one vocabulary for where a device is and how sure we are
- [ ] CAN device discovery (needs `canbus_uuid` from printer.cfg)

Interfaces:
- [x] CLI and interactive TUI
- [x] Moonraker agent (JSON-RPC over the unix socket)
- [x] Mainsail panel, including menuconfig in the browser
- [x] Bulk build / flash / update-all
- [x] Guided first-time MCU setup over DFU
- [ ] Standalone embeddable UI (today it is a Mainsail fork)
- [ ] Event-driven bus watching via pyudev (today: adaptive polling)
```

## Progress log

Append one block per step, in order. Keep it short and factual. This is the
review surface — a reviewer reads this before reading the diff.

Template:

```
### Step N — <title>            [done | done-with-deviation | blocked]
commit:     <sha>
gate:       pytest <passed>/<failed> · ruff ok · mypy ok · line-endings ok
deviation:  <what this plan said, what you did instead, why> — or "none"
untested:   <anything shipped without verification, and why> — or "none"
surprises:  <facts that contradicted the plan: wrong line number, function
            already existed, test that failed for an unrelated reason> — or "none"
```

Rules for the log:

- **Record deviations even when you are confident they were right.** A silent
  improvement is indistinguishable from a mistake at review time.
- **Record every `untested`.** Anything needing hardware, and anything
  `posix_only` (those skip silently on Windows).
- If a gate's pytest count drops, say why. A test that vanished is a finding, not
  a rounding error.
- Do not edit earlier blocks to make them look tidier. The sequence is evidence.

### Step 16 — docs and the Mainsail fork            [in progress — fork survey done, fork migration not started]

commit:     8d6585e (docs fix only; the survey itself produced no code diff)
gate:       GATE unaffected (survey touched only NOTES.md) · pytest 1156
            passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
deviation:  **Scoped down to a survey, on Vi's direction, before any fork
            code was touched.** Reading the fork (`Vylyne/mainsail`,
            `mu/stable`, checked out at `C:\git\github\mainsail`, currently
            `80f09150`) confirmed all three items NOTES.md's Step-16 entry
            asked this step to check are real, live code, not no-ops: the
            fork still calls `fw.display.flash` by name
            (`actions.ts:297`), still populates `state.types`/`state.displays`
            from `fw.status` (`mutations.ts:48,61`, gone from the wire),
            and still declares/reads the retired per-field names
            (`stale`/`stale_reason`, `firmware_state`, `artifact_state`,
            `katapult_installed` — `types.ts`). Full call-site map was
            written to a NOTES.md survey entry; it now lives in **Step 16's
            own spec above**, corrected and extended by the Steps 10–16
            review (the survey missed `getters.ts` and named five files
            where a grep finds seven). The NOTES.md entry was removed in the
            2026-08-20 doc cleanup — see git history if the original
            wording is needed. Asked Vi to choose between full migration now / survey
            only / RPC-only fix, given the real scope (5 `.vue` files + 3
            store files, past the fork's documented 4-file edit budget,
            which is a different kind of change than what that budget was
            written for) - **Vi chose survey only**. `docs/agent-api.md`'s
            own doc updates (bump api_version, document `firmwares` as a
            list, drop removed keys, `Family` wire type) and the README
            Configuration section are not yet done either - this step is
            not closed, only its most dangerous unknown (does the fork
            silently break) is now answered instead of assumed.
untested:   The fork's actual runtime behaviour against the current agent is
            still unverified - this was a source read, not `npm run
            test:unit` or a live panel session. Nothing here needed
            hardware.
surprises:  none - matched NOTES.md's own framing exactly ("confirm it,
            don't assume it"); the survey confirmed the worst case
            (all three) rather than the best case (fork already clean).

### Steps 10–16 — review                                       [2 corrections]

reviewer:   planning session, against the diff, the fork at
            C:\git\github\mainsail, and docs/mainsail-fork.md
verdict:    Steps 10-15 sound. Step 16's survey was the right *shape* - confirm
            rather than assume - but it was scoped down on a premise that does
            not hold. Both problems below were raised by Vi, not found by the
            gate; neither is a code defect. Both are documentation defects that
            produced, or nearly produced, a wrong decision.
finding 1:  **The fork edit budget does not apply to this work.**
            `mainsail-fork.md:34` claimed "9 files added, 4 edited"; the fork
            on disk has ~17 added (10 components under FirmwareUpdaterPanel/
            where the doc listed 3, plus FirmwareUpdaterPanel.vue and the
            5-file store). The budget was only ever about the four *edited*
            files, because those are the rebase surface - `mainsail-fork.md:272`
            says so directly. Every file the survey names is fork-added, so the
            migration spends no budget at all. The implementing context did
            the right thing procedurally (surveyed, escalated, did not guess);
            it escalated a number read from a stale document.
action 1:   `mainsail-fork.md` corrected: real added count, and an explicit
            statement that the budget governs edited files only with a
            "do not decline work because it touches many added files" line.
            Step 16 now records the migration as in-budget and proceeds. Vi's
            call: migrate now.
finding 2:  **Step 17's split list named `displays.py`**, which would
            re-enshrine in the file layout the display/MCU distinction Steps
            5-9 removed from the schema - inside a step whose own rule is
            "purely mechanical, no behaviour change", i.e. the wrong place to
            notice it. The underlying item ("generalise esptool/platformio
            from display/screen vocabulary to device") was a known
            consider-item that was never scheduled.
action 2:   The `fw.display.*` fold moves into Step 16 as a wire change, where
            the other wire changes and the fork migration already are.
            `displays.py` dropped from Step 17. Vi's call: fold into
            `fw.build`/`fw.status`, not a `fw.pio.*` rename.
correction: **The survey's call-site map was incomplete.** A direct grep found
            seven files, not five: it missed
            `store/server/fwUpdater/getters.ts:163,166`. Those two getters
            already fall back to `fw.build`/`fw.flash`, so the fork is
            partly migrated already - the stale half of each `||` is what
            needs dropping. The `.vue` files carry no `fw.display.*` call at
            all; their breakage is only the retired per-field names and the
            `types`/`displays` arrays. Step 16 now carries the full table.
process:    Worth keeping: both findings came from checking a document's claim
            against the thing it describes. The Step 4 review found the same
            shape (a premise confirmed from memory rather than source). That
            is now three for three - when a step is declined or redesigned on
            a stated fact, verify the fact first.

### Step 16a — the mcu-updater side: wire fold, api_version, docs   [done]

commit:     (pending)
gate:       pytest 1155 passed/0 failed/10 skipped (1 fewer than the prior
            log entry's 1156: `test_the_old_method_names_still_answer` was
            deleted, see deviation) · ruff ok · mypy ok · line-endings ok ·
            mutation spec `scripts/mutations/display-flash.json` re-run
            standalone, all 9 guards still CAUGHT after the rename below
deviation:  **`fw.display.list`/`fw.display.build` deleted, not just
            `targets[].kind`.** The spec's own text called for both; done as
            written. `display_status`/`display_types`/`_display_target`
            renamed provider-neutral per the plan, but as **`pio_status`
            (was `display_status`) and `pio_types`** rather than
            underscore-private `_pio_*` - `pio_status` is called directly by
            `tests/test_agent_targets.py` the same way `type_status` is (its
            peer, also unprefixed), and `pio_types` turned out to have the
            same shape of caller in `test_agent_display_jobs.py::_built`. A
            private name for either would have meant tests reaching into a
            "private" method, which is not this codebase's pattern for the
            legacy-shape helpers `targets[]` is projected from. `_pio_target`
            stayed private - nothing outside `methods.py` calls it.
            **`test_the_old_method_names_still_answer`
            (`tests/test_agent_display_jobs.py`) was deleted rather than
            updated.** Its entire premise - that the legacy aliases still
            answer - is what this step removes; rewriting it to assert the
            new names would have duplicated
            `test_the_generic_build_reaches_a_platformio_type` and the
            `fw.device.list` coverage already in `test_agent_displays.py`.
untested:   none - this half touches no hardware path.
surprises:  `docs/agent-api.md`'s `Family` prose was missing `builder`/
            `bootloader` even though the wire already carried them
            (`firmware_families()`, `methods.py:222-223`, backed by
            `test_firmware_families_carries_builder_and_bootloader`) - the
            code had shipped ahead of the doc before this step, not because
            of it. `README.md`'s Configuration section and `docs/layout.md`
            were still teaching the **pre-Step-15** config vocabulary
            (`[mcu ...]`/`[display ...]`, `provider: platformio`, singular
            `firmware:`, `pio_source`/`display_source` in `[updater]`,
            `katapult_installed:` as a config key) despite the sample
            `mcu-updater.cfg` at the repo root already being in the target
            schema - rewritten to match what Steps 1-15 actually shipped,
            beyond what this step's own spec named. `firmwares` (the plan's
            phrase "document `firmwares` as a list") is not a wire key
            anywhere in `type_status()`/`fw.status` - `mcu.firmwares` is a
            config-model attribute, never serialised under that name - so
            this was read as "fix the doc's `firmware:` list framing",
            covered by the `Family` and prose edits above rather than by
            inventing a wire key nothing emits.
next:       Step 16b, the Mainsail fork migration (7 files, in-budget per
            the Steps 10-16 review above) against this now-finished wire
            shape. Then Step 17 (split `agent/methods.py`), purely
            mechanical, done last.

### Step 16b — the Mainsail fork migration            [done]

commit:     `Vylyne/mainsail` `mu/stable` b16dadb8 (fork repo, not this one)
gate:       `npx eslint src` clean · `npx vitest run` 108 passed/0 failed ·
            `npx prettier --check` clean on the scoped paths · `npx vite
            build` succeeded (last, per Ground rules)
deviation:  **Found and fixed a real bug beyond the plan's named 7-file
            table, on Vi's go-ahead when asked.** `methods.py:409-447`
            (`status()`) computes `types`/`displays` only to feed
            `targets()` - the dict it returns never includes them, and its
            own comment says so ("The two originals retired at
            API_VERSION 2"). The fork's `mutations.ts` `setStatus` was
            still doing `state.types = payload.types ?? []` /
            `state.displays = payload.displays ?? []` regardless, so both
            have been silently `[]` since API_VERSION 2 shipped - not
            something this session broke. Two real consumers were reading
            that dead state: `FirmwareUpdaterPanelTarget.vue`'s
            `screenFor()` (the screen-detail popover: tool, filament,
            protocol_match) and its `mcuType` getter (feeds
            `FirmwareUpdaterPanelTypeDialog`'s chipset/firmware/extra_args/
            makefile_patches/katapult_installed fields). Fixed by adding
            `refreshDetail` (`actions.ts`), which fetches `fw.type.list`
            and `fw.device.list` and commits new `setTypes`/`setDisplays`
            mutations, dispatched after every `setStatus` (both from
            `onStatus` and the `state` event handler). **Sequential, not
            `Promise.all`** - `request`'s retry timer and `loading` flag
            live in one module-level slot every other call site already
            treats as single-flight; two requests racing would have one's
            timeout handling stomp the other's.
            Also renamed `isKconfig`'s `kind` fallback away (getters.ts),
            dropped `FwTarget.kind` from `types.ts`, fixed the two
            remaining `${target.kind}:${target.name}` list keys
            (`FirmwareUpdaterPanel.vue`, `BulkDialog.vue`) to
            `${target.provider}:...`, and `canBuildDisplay`/
            `canFlashDisplay` (getters.ts) to plain `fw.build`/`fw.flash`
            checks - none of this was in the plan's table but all of it
            follows directly from `kind` and `fw.display.*` actually being
            gone from api_version 3, not just deprecated.
untested:   Not run against a live printer/agent - `npx vitest run` and
            `npx vite build` are what a dev box can verify. The panel
            itself (screen popover, type-edit dialog, bulk dialogs) needs
            a browser against a running agent - Vi's to do, per Ground
            rules ("anything requires flashing real hardware... every
            on-printer step is Vi's to run"; verifying the fixed
            popover/dialog isn't a flash but is still a live-agent check).
surprises:  `vite-plugin-checker` (the thing `npx vite build` runs as its
            TypeScript pass) is configured `typescript: { buildMode: false
            }` with no `vueTsc: true` - it only checks bare `.ts` files,
            not `.vue` `<script>` blocks. Found because
            `FirmwareUpdaterPanelTypeDialog.vue` reads `mcuType.firmware`
            (line 81, 86, 253) but `FwType` (`types.ts`) declares no
            `firmware` field at all, and `npx vite build` reported no
            error for it. This is a real latent bug - `TypeStatus.firmware`
            is a documented `fw.type.list` field (`docs/agent-api.md`
            "TypeStatus") that `FwType` never picked up - but it predates
            this step, is invisible to every gate this repo or the fork
            run today, and is unrelated to anything the plan named here.
            **Not fixed - flagged only**, per Vi's direction when asked
            (third finding-beyond-scope this step surfaced; the first two
            were addressed, this one was explicitly left for later since
            chasing every latent `.vue` typing gap is unbounded once the
            build cannot catch them). Add `vueTsc: true` to
            `vite.config.ts`'s `checker()` options before trusting `npx
            vite build` to catch `.vue` script-block errors again.

### Step 17 — split `agent/methods.py`            [done]

commit:     3605a62
gate:       pytest 1155 passed/0 failed/10 skipped (unchanged from Step 16a) ·
            ruff ok · mypy ok · line-endings ok · all 6 affected mutation
            specs re-run standalone, every guard still CAUGHT (52 guards
            total: targets.json 18, bulk-operations.json 13, display-flash.json
            9, add-mcu.json 6, dfu-pairings.json 6, declare-type.json 4)
deviation:  **Added a seventh file, `agent/methods/_api.py`, beyond the plan's
            named six.** Not a surface file - it holds `_Api`, a
            `TYPE_CHECKING`-only `Protocol` describing every attribute and
            method one mixin calls on `self` that a *different* mixin defines
            (`self.paths`, `self.registry()`, `self._require_str`, ...). Splitting
            one class into six mixins composed only in `__init__.py`'s `Api`
            means no single mixin file has anything to type-check `self.foo`
            against on its own - mypy reported 176 `has no attribute` errors
            across all six files before this existed. `_Base` (`_Api` under
            `TYPE_CHECKING`, else plain `object`) is what each mixin actually
            inherits, so runtime MRO is unchanged - still exactly the six
            mixins, composed once in `Api`. Confirmed with Vi before adding it
            (asked: extra file vs. folding job-lifecycle methods into flash.py
            vs. a suppression comment; **Vi chose the extra file**, named
            `_api.py` on Vi's suggestion since it holds the `Api` surface's
            shape). One more deviation inside that same fix:
            `BuildMixin._kconfig_sessions` needed an explicit
            `Optional[Any]` class-level annotation beside the Protocol's -
            assigning `self._kconfig_sessions` inside `_sessions()` made mypy
            treat `BuildMixin` as the attribute's owner and infer its type
            from that assignment alone, circular against the `if ... is None:`
            guard reading it first (`has-type` error). The real, lazy
            initialisation (`None` until first use) still happens once, in
            `StatusMixin.__init__`, as before.

            **job_get/job_cancel and the klippy-ready/idle-probe helpers
            stayed in `status.py`, not a new `jobs.py`.** Raised as a
            boundary question (they are generic `JobRunner` accessors used by
            both build and flash jobs, not obviously either surface) -
            **Vi's answer: they belong on `status.py`** alongside the other
            Moonraker-probe helpers (`_probe`, `_printer_activity`,
            `_klippy_state`) and the RPC-surface bookkeeping already there
            (`dispatch`, `available_methods`, `METHODS`/`JOB_METHODS`/
            `FLASH_METHODS`). Kept the plan's six named surface files exactly
            as written - no `jobs.py`.

            Every module-level helper the old file carried before `class Api:`
            (`PROBE_TIMEOUT`, `_mtime`, `_size`, `_FW_SHA_RE`, `MCU_NAMES_TTL`,
            `_running_sha`, `_serial_from_path`, `serialize_device`,
            `_board_target`, `_screen_json`) turned out to be used by exactly
            one target file each - verified by grep, not assumed - so each
            landed as a plain module-level function in its one consumer
            (`status.py` for all but `_board_target`/`_screen_json`, which are
            `bulk.py`'s). None needed to live in `_api.py` or `__init__.py`.

            Six mutation specs named `src/mcu_updater/agent/methods.py` as
            their target `"file"` and went stale the moment the old file was
            deleted (`test_no_mutation_is_left_live_in_the_source` catches
            this class of drift by design - see that test's own docstring).
            Repointed each at its methods now actually live in:
            `targets.json` → `status.py`, `declare-type.json` → `registry.py`,
            `bulk-operations.json` → `bulk.py`, `dfu-pairings.json` and
            `add-mcu.json` → `flash.py`. `display-flash.json` splits across
            two files (`_pio_flash` in `flash.py`, `_object_names_for`/
            `targets()` in `status.py`), so two of its nine mutations carry a
            per-mutation `"file"` override instead of matching the spec's
            default. `dfu-pairings.json` also had to bump one `find`/`replace`
            string's own embedded import line (`from ..flashers.pairings
            import Pairings` → `from ...flashers.pairings import Pairings`) -
            every relative import in the moved code gained one `.` levels
            deep, `agent/methods.py` → `agent/methods/<file>.py`, and a spec
            matching exact source text has to track that.
untested:   none - this step touches no hardware path, and the mutation specs
            (which do reach flash/DFU/add-mcu code) were re-run against the
            moved source directly, standalone, one at a time per Ground rules.
surprises:  `scripts/check_line_endings.py` and `git ls-files -s` (the tests in
            `test_repo_hygiene.py`) stayed green throughout, including through
            two rounds of `ruff --fix`, which the plan's docstring for
            `test_no_working_tree_file_has_crlf_endings` singles out by name as
            the exact failure mode a prior scripted rewrite hit on this file -
            worth the explicit check given the history, and it held.
next:       This closes the six-step run started at Step 16. Nothing else is
            queued in this file.

### Steps 16–17 — review                                    [1 live bug, 3 findings]

reviewer:   planning session, against the diff, the fork at
            C:\git\github\mainsail, and a full gate run
gate:       verified independently — pytest 1155 passed/0 failed/10 skipped ·
            ruff ok · mypy ok (49 files) · line-endings ok · tree clean
verdict:    All 17 steps complete and green. The schema work landed as
            designed. One live bug found, and its root cause is documentation,
            not code.
finding 1:  ~~**The type-edit dialog silently rewrites a type's firmware to~~
            **SUPERSEDED 2026-08-20 — this finding was wrong.** See the
            "Step 20 premise" correction block below. The dialog reads
            `firmware`, the agent really sends it, and nothing is
            corrupted. The real defect is `hasBinary` hardcoding klipper.
            Original text kept below as written; the log is evidence.
            ~~The type-edit dialog silently rewrites a type's firmware to
            klipper.** Agent emits `firmwares` and no `firmware`
            (`config.py:219`); `FirmwareUpdaterPanelTypeDialog.vue:253` reads
            `mcuType?.firmware ?? 'klipper'` -> always undefined -> always
            klipper; `submit()` posts it back; `registry.py:290-307` applies
            it. The guard at `:299` is a **warning appended to a list, not a
            refusal** - the write proceeds. Open the cartographer type to
            change its chipset, save, and it becomes klipper. Same shape at
            `:256` for the deleted `katapult_installed`. -> Step 20.
finding 2:  **`docs/agent-api.md` is stale, and it is the declared contract.**
            Step 16a updated the `Family` section and the headline
            `api_version: 3`, then stopped. `TypeStatus` still documents
            `firmware` singular, `katapult_installed`, `installed` on the
            katapult block and a fixed `artifacts` pair; `Artifact` still
            documents `stale`/`stale_reason`; `Target` still documents `kind`;
            and `:114` says `api_version: 2` against `:9`'s 3. This is *why*
            finding 1 shipped - the fork's `FwType` matches the stale doc, and
            Step 16b's own log cites it as evidence the field is real. The
            implementer followed the contract; the contract was wrong.
            -> Step 19, ordered before the fork fix.
finding 3:  **The `vueTsc` note understates the cost of fixing it.**
            `vite.config.ts` is an *upstream* file and `vue-tsc` is absent
            from `package.json` (also upstream), so the direct fix takes the
            rebase surface 4 -> 6. Vi's call: CI-only, in the fork-added
            `mu-ci.yml`, which spends nothing. Also: this file's Ground rules
            claim `npx vite build` "type-checks the tests too" - true for
            `.ts`, false for `.vue`. -> Step 21.
finding 4:  The deferred phantom-slot bug is pulled into scope as **Step 18**,
            on Vi's call, because it must be fixed *before* the contract is
            rewritten or the contract documents the defect.
process:    Fourth review running where a step was declined, redesigned or
            shipped on a stated fact that did not hold. Steps 18-22 are
            ordered so each makes the next correct, and Step 22 is gated
            behind Step 20 because the on-printer work is done through the
            panel that currently corrupts a type on save.

### Step 18 — narrow the phantom `FwConfig` slots            [done]

commit:     726f31c
gate:       pytest 1155 passed/0 failed/10 skipped (unchanged) · ruff ok ·
            mypy ok (49 files) · line-endings ok · no mutation spec targets
            the changed lines (checked `application-firmware.json` and
            `pio-provider-selection.json`, the only two naming
            `src/mcu_updater/config.py`; neither `find` string matches
            anything touched here, so none needed a re-run)
deviation:  **None in the implementation** - `config.py:380`'s per-type loop
            now iterates `mcu.firmwares` exactly as specced, not
            `firmware.BUILTIN` and not `fw_names`. Added the non-mutating
            `fw_get()` accessor beside `fw()` as specced and repointed the
            three pure-read call sites (`build.py:264`, `build.py:583`,
            `agent/methods/status.py:334`) onto it; `registry.py:312` and
            `config.py`'s own load-time populate line keep `fw()` because
            both are genuine writes. Unpinned the two named tests
            (`test_artifacts_returns_both_firmwares`,
            `test_status_type_shape`) to assert the narrow set.
untested:   none - this step touches no hardware path.
surprises:  **Narrowing to `mcu.firmwares` broke four tests the plan did not
            name**, because they encoded the *other* half of the old
            behaviour as if it were intentional: `test_a_declared_family_
            gets_its_own_per_type_keys` and `test_a_declared_family_appears_
            in_a_types_own_ordering` (`tests/test_firmware.py`) both round-
            tripped or ordered a family the type under test never declared;
            `test_a_type_lists_only_the_families_it_uses`
            (`tests/test_firmware.py`) asserted klipper stayed in
            `fw_order()` "harmless and unused" for a cartographer-only type;
            and `test_the_artifact_shown_is_the_one_this_type_would_flash`
            (`tests/test_agent_targets.py`) had a docstring explicitly
            describing the phantom klipper entry as the *cause* of a panel
            bug, then asserted the entry's presence as the expected
            behaviour rather than the defect. Weighed reverting to
            `set(firmware.BUILTIN) | set(mcu.firmwares)` instead, which
            would have kept all four green unmodified - the "Reviewed and
            confirmed 2026-08-20" entry this step is based on considered and
            rejected `firmware.BUILTIN` twice, by name, so treated that as
            settled intent rather than re-litigating it against tests
            written before the bug was understood. Updated all four to
            declare the family under test (first two) or assert its absence
            (last two) instead, matching the corrected contract. Flagging
            here rather than only in the diff because Step 18 is marked a
            contract change and this is the concrete shape of it: a type
            that does not declare klipper no longer gets a klipper artifact
            entry, full stop, including in cases these four tests happened
            to be the only coverage for.
next:       Step 19 (make `docs/agent-api.md` true), ordered before Step 20's
            fork fix per the Steps 16-17 review.

### Step 19 — make `docs/agent-api.md` true            [done]

commit:     5c46df7
gate:       line-endings ok · no code touched, so no pytest/ruff/mypy run ·
            spot-checked against a live `Api` (four calls: `fw.ping`,
            `fw.status`, `fw.type.list`, `fw.artifacts`) run through a real
            `Registry.load()` of the repo-root `mcu-updater.cfg`, plus a
            direct read of `errors.py` and `states.py` for the two catalogs
            (error codes, `Artifact.reason`) - more than the plan's own
            "two or three payloads", because the known-stale table's items
            turned out to need the source read to sort real staleness from
            not.
deviation:  **Most of the plan's own known-stale table turned out to be
            wrong, not the doc.** Checked each of its seven rows against the
            live dump and Step 16a's own log (which had already re-verified
            some of these): `firmware: "klipper"` (singular) is correct -
            there is no `firmwares` wire key, `mcu.firmwares` is never
            serialised under that name (Step 16a found this already);
            `katapult_installed` and `installed` on the katapult block are
            both still live keys, not deleted - `status.py:326,339-341`
            emits both today. Three rows were real: `:114`'s `api_version: 2`
            (fixed to 3), `stale`/`stale_reason` (genuinely retired in Step
            14, only `reason` exists on the wire - fixed the example and
            rewrote the paragraph that explained two fields to describe one),
            and `targets[].kind` (fixed to `provider`, matching Step 16b).
            Treated the table as a lead, per its own "starting point, not a
            complete list" framing, and verified every row rather than
            trusting it - this is the fifth review-log entry finding that
            shape (a stated fact not holding up against source), so verifying
            first rather than acting on the table directly was deliberate,
            not incidental.
untested:   The rewrite covers what a full read plus four live payloads plus
            two source-file cross-references could confirm: `fw.ping`,
            `fw.status`, `TypeStatus`, `Artifact`, `Target`, `Family`, and the
            error-code catalog. Jobs, bulk operations, the DFU flow, display
            flashing and profiles were read in full and not found
            self-contradictory, but were not independently re-derived against
            a live payload the way the four above were - none of those are
            reachable from a plain `Registry.load()` without a job runner,
            a DFU mock, or a Kconfig tree, and building those fixtures is
            past what this step's "no code" framing covers. If Step 20's fork
            work runs into one of those sections disagreeing with the fork,
            that section needs the same live-payload treatment this step gave
            the first four.
surprises:  **A self-contradiction inside the doc itself**, found on the full
            read rather than from the known-stale table: `targets[].extra`'s
            key list (then line 332) still named `moved` as a current field,
            while the changelog nine lines above (`:14`) already said Version
            2 retired `targets[].extra.moved`. Removed `moved` from the key
            list. Also found, not in the table: `fw.status`'s example was
            missing two real top-level keys (`kconfig_available`,
            `idle_state`) and carried two that do not exist there at all
            (`types`, `displays` - those are `fw.type.list`/`fw.device.list`'s
            own return shapes, never embedded in `fw.status`); `TypeStatus`
            was missing `needs_flash` entirely and its serial objects were
            missing 5 of 8 real fields (`mcu`, `running_version`,
            `running_sha`, `needs_flash`, `reason` - the exact set
            `test_status_type_shape` already pins); and the top-level error
            catalog ("Codes come from `errors.py`") was both incomplete
            (missing `duplicate_type`, `service_control`, `flashing_disabled`,
            `profile`, `profile_not_found`, `profile_customised`,
            `offset_mismatch`, `no_session`) and, in its own framing, wrong -
            several real codes (`no_artifact`, `nothing_to_do`, the
            `dfu_<reason>` family) are built inline at the call site and were
            never going to be found in `errors.py` no matter how carefully
            that file was read.
next:       Step 20, the fork fix against this now-corrected contract.

### Step 20 premise — correction                          [caught before any code]

raised by:  the implementing context, which paused rather than proceeding
reviewer:   planning session; confirmed by pulling a live `type_status()`
            payload, not by re-reading source
finding:    **Step 20's stated premise was false, and it was mine.** I claimed
            `fw.type.list` emits `firmwares` with no `firmware` key and that
            `katapult_installed` was deleted, concluding the type dialog
            silently rewrote a type's firmware to klipper. The live payload for
            cartographer shows `firmware: 'cartographer'`,
            `katapult_installed: True`, `firmwares` absent, and `artifacts`
            keyed `['cartographer', 'katapult']`.
cause:      I read `McuType.to_json()` in `config.py` — the registry
            serialization — and assumed it was the wire shape. It is not.
            `type_status()` (`agent/methods/status.py:265`) builds its own
            payload with a *derived* singular `firmware` and a *derived*
            `katapult_installed`. Step 19 had already documented this correctly
            against live payloads; Step 20's prose was never reconciled with
            Step 19's own finding.
impact:     None shipped. The implementer checked the premise before touching
            fork code, which is exactly what the Handoff section asks for. Had
            it been followed as written, it would have added `firmwares` and
            removed `katapult_installed` — breaking two fields that are live.
real bug:   `hasBinary` (`FirmwareUpdaterPanelTypeDialog.vue:213`) reads
            `artifacts.klipper.has_bin`, which is `undefined` for any type whose
            application is not klipper. It gates **both** safety warnings
            (chipset-changed `:43`, firmware-changed `:81`), so on a Cartographer
            — the board that already bricked once — both are silently
            suppressed. `patchLines` (`:217`) and the extra-args fields (`:254`)
            hardcode klipper the same way, more cosmetically.
action:     Step 20 rewritten against the payload: `FwType` **gains**
            `firmware: string` (it was simply missing), keeps
            `katapult_installed`, and the fixed klipper/katapult members become
            family-keyed maps. Plus a vitest case that a non-klipper type
            surfaces `hasBinary === true` — the assertion that would have caught
            this.
process:    Fifth instance of a step being redesigned on a stated fact, and the
            first where the wrong fact was the reviewer's. The rule earns its
            keep in both directions: **`docs/agent-api.md`, rewritten in Step 19
            against live payloads, is the authority — not this file's prose.**
            Where the two disagree, the doc wins and this file gets corrected.

### Step 20 — fix the fork against the corrected contract            [done]

commit:     `Vylyne/mainsail` `mu/stable` 13cb4d81 (fork repo, not this one)
gate:       `npx eslint src tests` clean · `npx vitest run` 111 passed/0
            failed (108 prior + 3 new) · `npx prettier --check` clean on the
            scoped paths · `npx vite build` succeeded (last, per Ground
            rules) - no code in this repo touched, so no pytest/ruff/mypy run
deviation:  **`patchCount` (`FirmwareUpdaterPanelTarget.vue:642`) carried the
            same `.klipper` bug**, one file beyond the corrected spec's named
            two - same live payload proves it (a cartographer type's
            "patched build" caption would stay hidden forever even with real
            makefile patches applied). Fixed alongside the other two.

            **Kept `katapult` a fixed `FwType` field rather than folding it
            into a generic map too**, diverging from the corrected spec's
            literal "replace the fixed klipper/katapult members ... with a
            map keyed by family name". `katapult` is one of the two builtin
            families and `type_status()` always writes its block under that
            exact literal name when declared - unlike the application, it was
            never dynamic and never actually broken; folding it in added risk
            (the `katapultInstalled` toggle's whole design assumes one fixed
            bootloader field) for no bug it fixes. Added
            `fwApplicationConfig(type)` in `types.ts` for the one field that
            *is* dynamic - a small cast (`(type as unknown as Record<string,
            unknown>)[type.firmware]`) rather than an index signature on
            `FwType` itself, which would have had to be a union across every
            other field's type and weakened typo-checking on all of them for
            one dynamic case.

            **Found the write side was broken too - the corrected spec's fix
            list named reading (`onOpen`), not `submit()`.** `submit()`'s
            editing branch always sent `klipper_extra_args`;
            `registry.py`'s `type_update` loops `f"{fw}_extra_args" for fw in
            mcu.fw_order()`, so for a cartographer type that key is never
            checked - the field looked editable and silently saved nothing.
            Fixed by keying the submitted arg off `mcuType.firmware` (the
            *saved* application, not `this.firmware`, which may be a family
            just picked in this same edit with no answers of its own yet).
            Renamed `klipperExtraArgs` -> `applicationExtraArgs` while
            touching every read/write site.

            **Left the create-flow's `klipper_extra_args` key untouched,
            flagged not fixed.** `fw.type.add` (`registry.py`'s `type_add`)
            has no generic per-family args parameter - only
            `klipper_args`/`katapult_args` on `reg.add_type()` - so a dynamic
            key here is a differently-named no-op for a non-klipper family,
            same as today. Needs a backend signature change; out of a
            fork-only step.

            **Also fixed `FwArtifact.stale`/`.stale_reason`**, found while
            touching this exact type for the `artifacts` shape change: both
            retired in Step 14 (`docs/agent-api.md`'s `Artifact` section,
            corrected in Step 19), replaced by `reason` plus a nested
            `profile`. Grepped first - nothing in the fork read either field
            (`Target.artifact`/`Target.profile` are a separate projection,
            not this type) - zero runtime risk, and leaving it would have
            been a trap for the next reader given the exact kind of dynamic
            access this step just introduced elsewhere. Removed the
            now-dead `FwStaleReason` export with it.

            **`fwHasBinary(type)` exists as its own exported function, not a
            component getter, specifically so the Gate's required regression
            test could exist at all.** This fork has zero component-mount
            tests today - `@vue/test-utils` is not a devDependency, every
            existing spec tests store logic as pure functions - so a getter
            buried in a `.vue` file cannot be unit-tested without new test
            infrastructure, a bigger decision than this step. Hoisted the
            DFU-setup describe block's `artifact`/`type` fixture factories
            (`tests/store/server/fwUpdater/getters.spec.ts`) to module scope
            so the new suite could reuse them instead of duplicating the full
            `FwArtifact` shape a second time.
untested:   Not run against a live printer/agent - same caveat as Step 16b.
            The panel itself (the two safety warnings and the extra-args
            round-trip, specifically on a cartographer type) needs a browser
            against a running agent - Vi's to do, per Ground rules.
surprises:  The label text ("Klipper extra args") on the now-generic
            extra-args field was **not** changed - it still reads "Klipper"
            while editing a cartographer type. Fixing it needs an i18n key
            change (`src/locales/en.json`, the case-sensitive sort trap Step
            16 notes) across every locale - cosmetic only, and beyond what
            the corrected spec's fix list named. Flagged, not fixed.
next:       Step 21 (close the `.vue` type-checking gap), then Step 22
            (on-printer verification, Vi only, gated behind this step).

### Step 21 — close the `.vue` type-checking gap            [blocked]

commit:     none - no code changed, nothing survived the investigation
gate:       n/a - blocked before any CI or fork change was made
deviation:  n/a - blocked
untested:   n/a - blocked
surprises:  **`vue-tsc` cannot check this tree at all, at any version, for a
            structural reason the plan's own warning undersold.** The plan
            named a version trap ("may not handle 2.7 at all... expect to pin
            a version, and possibly to set `vueCompilerOptions.target: 2.7`")
            as the risk to confirm before treating this as a one-liner. The
            actual finding is narrower and harder: no version works, because
            the incompatibility is with **decorator-based class components**
            (`vue-class-component`/`vue-property-decorator`), not with the
            Vue 2.7 vs 3 target setting.

            Installed `vue-tsc` locally (`npm install vue-tsc --no-save`,
            not persisted) rather than via bare `npx`, since a bare `npx
            vue-tsc --version` failed outright
            (`ERR_PACKAGE_PATH_NOT_EXPORTED` on `typescript/lib/tsc`) - it
            fetches an isolated `typescript` peer that doesn't match this
            project's `typescript@6.0.3`. Once installed as a real
            dependency it resolved the project's own TypeScript correctly.

            **Newest available version (3.3.10, the only major compatible
            with `typescript@6.0.3`)**: `npx vue-tsc --noEmit` produces 6307
            `error TS2339` across the tree, every one shaped
            `Property '<x>' does not exist on type 'Vue3Instance<...>'`.
            Tested `vueCompilerOptions.target` explicitly at both `2.7` and
            `3` via an override tsconfig (`extends` the real one, deleted
            after testing, never committed) - **identical error count both
            times**, and confirmed directly via
            `@vue/language-core/lib/compilerOptions.js`'s
            `CompilerOptionsResolver` that the override was actually being
            read. The `target` knob changes template-directive nuances, not
            whether class-component properties are visible on `this` at all
            - `@vue/language-core` infers a component's public type from a
            `defineComponent(...)`-shaped export, which a `@Component class
            X extends Vue` decorator export never produces, regardless of
            target. Checked the exact file Step 20 fixed
            (`FirmwareUpdaterPanelTypeDialog.vue`) specifically: every
            single template-bound property - `editing`, `chipset`,
            `mcuType`, `hasBinary`, all of it - is flagged. A real regression
            there would be error #6308 among 6307 identical-looking false
            positives - undetectable, and the checker could never pass
            cleanly on this tree's current, correct code either way.

            **Old, Vue-2.7-era version (1.8.27, contemporaneous with
            `vue-class-component`'s peak usage)**: crashes outright against
            `typescript@6.0.3` -
            `Search string not found: "/supportedTSExtensions = .*(?=;)/"`.
            That version patches TypeScript's internals via a regex replace
            against `tsc`'s compiled source, and the pattern it looks for no
            longer exists in this TypeScript version. So the two failure
            modes bracket the whole option space: new `vue-tsc` runs but is
            structurally blind to this tree's component pattern; old
            `vue-tsc` understood that pattern but cannot load against this
            TypeScript version at all.

            All test artifacts removed before stopping - `vue-tsc` and
            `vue-tsc@1.8.27` were installed with `--no-save` (never touched
            `package.json`/`package-lock.json`, confirmed via `git status`
            after each), and the scratch override tsconfig was deleted.
            Fork tree is exactly as Step 20 left it.

            Per the plan's own instruction ("if vue-tsc cannot run usefully
            against this Vue 2 / class-component tree, say so and stop; do
            not spend the upstream-file budget as a fallback without
            asking"), reported this finding rather than picking a fallback.
            **Vi's call: log as blocked, move to Step 22.** `vite.config.ts`,
            `package.json` and `tsconfig.json` remain untouched - rebase
            surface stays at 4 edited files.
next:       Step 22 (on-printer verification, Vi only). The `.vue`
            type-checking gap stays open; `docs/backlog.md` already records
            the upstream half of this (raising it with `mainsail-crew/mainsail`)
            per this step's own spec - not reopened here, since nothing about
            today's finding changes what that entry should say.

### Step 23 — `discovery/spec.py` + `discovery/registry.py`, nothing moved            [done]

commit:     d1a4e9d
gate:       pytest 1162 passed/0 failed/10 skipped (7 more than Step 21's
            1155 - the new discovery test file) · ruff ok · mypy ok
            (52 files, up from 49) · line-endings ok · nothing imports the
            new package yet, so no other suite count moved
deviation:  none - `Sighting`/`Confidence`/`Source` modelled on
            `flashers/spec.py`'s `FlashTarget`/`Flasher` exactly as specced,
            `Confidence` built the way `states.py`'s `ArtifactStatus`/
            `DeviceStatus` are (reason is the fact, tone/label/safe_to_write
            derived, `__post_init__` raises on an unknown reason).
            `state_for_firmware()` implements the bootloader-predicate rule
            verbatim from the spec's own pseudocode, reusing
            `devices.STATE_*` and `devices.KATAPULT_NAMES` rather than
            redeclaring them. `SOURCES = ()`, static tuple, no `pkgutil` -
            per "Do not do". Did not touch `is_mcu`/`find_untracked` or
            `devices.py` at all, as specced - that allowlist is untouched
            until a later step, and this step's `state_for_firmware` is a
            new function, not a replacement for `BusDevice.state`.
untested:   none - this step touches no hardware path and imports nothing
            that does; it is vocabulary only.
surprises:  none - `flashers/spec.py` and `states.py` were close enough in
            shape that no design question came up while writing this.
next:       Step 24 (move the three bus sources behind the seam -
            `discovery/byid.py`, `discovery/dfu.py`, `discovery/bootsel.py`,
            out of `devices.py`, with `devices.py` keeping every public name
            as a thin re-export).

### Step 24 — move the three bus sources behind the seam            [done]

commit:     0c2bb9e
gate:       pytest 1162 passed/0 failed/10 skipped (unchanged from Step 23) ·
            ruff ok · mypy ok (55 files, up from 52) · line-endings ok ·
            `tests/test_devices.py` passed with zero edits, as specced ·
            mutation specs re-run standalone, one at a time:
            `flash-offset-diagnostic.json` (10 guards, all CAUGHT),
            `dfu-pairings.json` (6, all CAUGHT), `add-mcu.json` (6, all
            CAUGHT), `display-flash.json` (9, all CAUGHT),
            `flasher-selection.json` (2, all CAUGHT), `targets.json` (17
            CAUGHT, 1 pre-existing SURVIVED - confirmed unrelated, see
            surprises)
deviation:  **`bootsel_scan`'s automount-glob lookup reads through the
            `devices` shim at call time, not a plain module-level constant in
            `discovery/bootsel.py`.** Two tests neither this step nor Step 23
            named (`tests/test_devices.py`'s
            `test_bootsel_scan_searches_the_automount_globs_with_no_override`
            and `tests/test_flash.py`'s
            `test_bootsel_refuses_more_than_one_mounted_volume`) both do
            `monkeypatch.setattr(devices_mod, "DEFAULT_BOOTSEL_ROOT_GLOBS",
            ...)` and expect the real scan to honour it. A plain `from
            .discovery.bootsel import DEFAULT_BOOTSEL_ROOT_GLOBS` re-export is
            a value copy at import time - patching the copy on `devices`
            would not reach the tuple `bootsel_scan` actually reads from its
            own module globals. Since Step 24's own text makes
            `tests/test_devices.py` passing **untouched** the step's real
            gate, `bootsel_scan` does a deferred `from .. import devices`
            inside the function body (mirrors the existing lazy-import
            pattern in `agent/events.py:311`, not a new idiom) and reads
            `devices.DEFAULT_BOOTSEL_ROOT_GLOBS` rather than its own
            module-level name. The module-level constant in
            `discovery/bootsel.py` still exists (production code with no
            override still uses it, since nothing has patched `devices` in
            that path) but is no longer what the patched tests observe.
            `subprocess` needed the same shared-object treatment for
            `dfu_devices`: `devices.py` now does a plain `import subprocess`
            so `monkeypatch.setattr(devices_mod.subprocess, "run", ...)`
            (`tests/test_flash.py:655`) mutates the one singleton module
            object both `devices.py` and `discovery/dfu.py` import, which
            needed no special handling beyond the import itself since
            `subprocess.run` patching is attribute mutation on a shared
            object, not a name rebind.

            **`dfu_selector` moved out of `flashers/flash.py` into
            `discovery/dfu.py`, as specced, but this required an explicit
            check that nothing tests it by its old module path** -
            `tests/test_agent_dfu.py` imports it as `from
            mcu_updater.flashers.flash import dfu_selector`, which keeps
            working because `flash.py` now imports the name from `..devices`
            (the same pattern `dfu_devices` already used) rather than
            defining it.
untested:   none - this step touches no hardware path, and the mutation specs
            that do reach flash/DFU/BOOTSEL code were re-run standalone
            against the moved source, one at a time per Ground rules.
surprises:  `targets.json`'s "configure is offered only for the families this
            type uses" guard SURVIVED both before and after this step's
            changes - confirmed by stashing this step's diff and re-running
            the spec against unmodified `main` (identical result: 177
            passed, 1 skipped, same SURVIVED line). Pre-existing, not
            introduced here; not this step's to fix.
next:       Step 25 (move the two knomi sources out of `providers/pio.py` -
            `discovery/listen.py`, `discovery/watcher.py`).

### Step 25 — move the two knomi sources out of `providers/pio.py`            [done]

commit:     7fd9da6
gate:       pytest 1162 passed/0 failed/10 skipped (unchanged from Step 24) ·
            ruff ok · mypy ok (57 files, up from 55) · line-endings ok ·
            `scripts/mutations/pio.json` and `pio-provider-selection.json`
            re-run standalone, one at a time: `pio.json` 11 guards all
            CAUGHT (9 before this step - see deviation), all 7 of
            `pio-provider-selection.json` unaffected and still CAUGHT ·
            `states.json` re-run too since its command names `test_pio.py`
            (mutates `states.py`, not this step's files) - 10 guards, all
            CAUGHT
deviation:  **`_source_dir` moved into `discovery/listen.py` (renamed
            `source_dir`, no longer private) rather than staying in
            `providers/pio.py`.** Not named in the plan's own text, which only
            called out `WatcherDevice`/`discover`/`read_device_map`/etc. by
            name. `discover()` needs it and `build()`/`upload()` (staying in
            `pio.py`) also need it, and `pio.py`'s re-export shim does `from
            ..discovery.listen import discover as discover` - so keeping
            `_source_dir` in `pio.py` and having `listen.py` import it back
            would be a hard cycle (`pio` -> `listen` -> `pio`). Resolved the
            same way Step 24 already resolved an identical shape for
            `dfu_selector`: the helper moved to the lower layer
            (`discovery/listen.py`), and `pio.py`'s `build()`/`upload()` import
            it back under its old private name (`from ..discovery.listen
            import source_dir as _source_dir`) so neither function's body
            changed at all.

            **The "no port" / "lowered id" pair in `pio.json` needed splitting
            into two mutations each, one per destination file, not just a
            `"file"` repoint.** Both guards were already duplicated verbatim in
            the original `pio.py` - once in `read_device_map` (watcher-bound)
            and once in `_parse_discovered` (listen-bound) - and
            `mutation_test.py`'s `str.replace(needle, replace, 1)` only ever
            mutates the *first* match in a file, so before this step the
            watcher copy was the only one ever actually exercised; the listen
            copy's identical guard was untested and the spec's 9-guard count
            never caught it. Splitting the two files apart made this
            unfixable by a single repoint - each file now has exactly one
            occurrence - so both mutations were duplicated per destination
            instead (11 guards total, up from 9). Net effect: a previously
            silent gap (the listen-pass copy of both guards) is now covered,
            found only because the split forced the ambiguity into the open
            rather than because it was gone looking for.

            **`WatcherDevice`/`discover`/`read_device_map`/`device_map_path`/
            `DEVICE_MAP_VERSION` are re-exported from `pio.py` via `as`-suffixed
            imports** (`from ..discovery.watcher import X as X`), the same shim
            shape `devices.py` already uses for the three bus sources - kept
            every call site in `cli.py`, `flashers/esptool.py`, and every test
            that patches `mcu_updater.providers.pio.discover`/`.upload` by name
            working with zero changes, since those patch the *function binding
            on the module*, not an internal the function reads from its own
            namespace.
untested:   none - this step touches no hardware path, and the mutation specs
            that do reach discovery/flash code were re-run standalone against
            the moved source, one at a time per Ground rules.
surprises:  **The `shutil.which`/`run_streamed` monkeypatches in
            `test_pio.py`'s discover tests had to move their patch target, not
            just their import path.** `discover()` calls `shutil.which(...)`
            and `run_streamed(...)` from its own module's global namespace now
            (`discovery.listen.shutil`, `discovery.listen.run_streamed`), so a
            test that still patched `mcu_updater.providers.pio.shutil.which`
            would silently stop reaching the real function - the same
            shared-object lesson Step 24's own log already recorded for
            `bootsel_scan` and `dfu_devices`, hit again here in the test suite
            rather than the source. All patch targets in the new
            `test_discovery_listen.py` point at `mcu_updater.discovery.listen.*`
            accordingly. By contrast, `test_agent_display_jobs.py`'s
            `monkeypatch.setattr("mcu_updater.providers.pio.discover", ...)`
            needed no change at all: that replaces the whole function binding
            on `pio`, which `flashers/esptool.py` still looks up by attribute
            access (`pio_mod.discover(...)`) at call time - a different shape
            of patch than the internal-dependency case above, and the shim
            preserves it for free.

            **A first attempt at splitting `test_pio.py`'s tail orphaned one
            assertion line.** The file's last test
            (`test_the_helper_runs_against_the_configured_source_tree`) had a
            trailing `assert calls[0][-2] == ...` one blank-line gap below its
            last `monkeypatch`/`discover()` call that an initial read of the
            file's tail (a windowed read that stopped one line short of EOF)
            missed copying into the new test file. Caught immediately by the
            gate - `NameError: name 'calls' is not defined` in what was left
            behind in `test_pio.py` - not by inspection; fixed by moving the
            line to its correct new home in `test_discovery_listen.py` before
            re-running the suite.

            `tests/test_repo_hygiene.py:215`'s docstring named `displays.discover`
            (a name from a pre-Step-16 era, already stale before this step -
            the module it named was renamed to `providers/pio.py` well before
            Step 25) - updated to `discovery.listen.discover`, the plan's own
            predicted "will need its path updated," though the fix was to a
            docstring only; the test's assertions never touched the module
            path at all.
next:       Step 25b (name the knomi sources for the firmware they integrate
            with) - raised mid-Step-26, see that step's own log entry.

### Step 25b — name the knomi sources for the firmware they integrate with            [done]
commit:     (pending)
gate:       pytest 1162 passed/0 failed/10 skipped (unchanged from Step 25) ·
            ruff ok · mypy ok (58 files, up from 57 - the new
            `knomi_serial/__init__.py`) · line-endings ok ·
            `tests/test_devices.py` passed with zero edits ·
            `scripts/mutations/pio.json` re-run standalone: 11 guards, all
            CAUGHT (unchanged count - the split property survived the move)
deviation:  **Not in the original plan.** Raised by Vi partway through
            implementing Step 26, when `discovery.spec.Source.sight(bench)`'s
            fixed, family-less signature ran into a real question with no
            answer in the tree: should a knomi source scan every configured
            display family internally (matching how `byid`/`dfu` scan the
            whole bus), or be scoped to the one family a caller cares about?
            The friction traced back one step further - nothing in the layout
            said `listen.py`/`watcher.py` were firmware-specific rather than
            generic host scans, so there was no established place to decide
            how they should behave. Scoped as its own step, ahead of Step 26,
            rather than folded into it: Step 26's own gate requires
            `scripts/mutations/display-flash.json` stay green **with no
            edits**, and this move touches paths that spec's `find` strings
            do not pin, so doing the rename first keeps that constraint
            simple to verify in isolation.

            Went with a subpackage (`discovery/knomi_serial/{listen,watcher}.py`)
            rather than a single merged `knomi_serial.py`, on a constraint
            Step 25's own log already recorded and this step re-derived
            independently before checking: `read_device_map` and
            `_parse_discovered` carry textually identical "no port"/"lowered
            id" guards, and `mutation_test.py`'s `str.replace(needle, replace,
            1)` only mutates the first match per file. Step 25 split them into
            two files for exactly this reason - 9 guards became 11. A merge
            would silently return 2 of those 11 to untested, with
            `pio.json` still reporting all-CAUGHT, since the spec verifies
            guards are load-bearing, not that the file layout keeps them
            independently mutable. Confirmed by running the gate after the
            move: 11 guards, all CAUGHT, unchanged from before.

            `byid.py`/`dfu.py`/`bootsel.py` stayed flat, not moved into a
            mirrored `discovery/sources/` layout - considered and declined.
            They landed in Step 24, one commit prior; moving them again so
            soon is churn the generic/firmware split does not need, and it
            matches how `providers/` and `flashers/` already keep their own
            implementations flat rather than subpackaged.
untested:   none - this step touches no hardware path, and the mutation spec
            that does reach this code was re-run standalone, per Ground
            rules.
surprises:  none in the move itself. Three findings surfaced while writing the
            Step 26 work this step interrupted, carried forward rather than
            lost: `Source.states` for a knomi source should be
            `(STATE_KLIPPER,)` written plainly, not computed via
            `state_for_firmware("")`; `Sighting.state` must not be derived
            from `WatcherDevice.firmware_version` (a version string, not a
            firmware name - `state_for_firmware` expects the latter, and
            feeding it the former only works by falling through to the
            default); and the per-family scoping question that started this
            step is still open, now with a natural answer once the
            subpackage names the firmware - a knomi source scoped to its own
            families, `confirm()` taking an optional scope - but that is
            Step 26's decision to make, not this step's.
next:       Step 26 (`discovery.confirm()`; `port_for` becomes a caller),
            resumed against the corrected paths.

---

## Appendix B — open items, not in scope

- ~~The Mainsail fork sits three commits past `v2.18.4-vylyne.14`~~ — **resolved
  2026-08-19.** `v2.18.4-vylyne.19` is promoted to stable, and `build_all` /
  `flash_all` are tested on hardware. Step 16's fork work starts from `.19`.
- `needs_klipper_stopped` → per-type "services to stop" list. See "Do not do".
- An unreproduced flaky teardown `RuntimeError` in
  `test_an_unknown_inbound_method_gets_an_error_not_silence`.
- ~~Phantom `FwConfig` slots for every globally declared family.~~ — **pulled into scope 2026-08-20 as Step 18**, because `fw_order()` feeds
  `artifacts`, which Steps 19–20 must document and re-declare.
- ~~The Inventory axis, deferred by both `spec.py` files.~~ — **pulled into scope
  2026-08-21 as Steps 23–28.** The deferral's own criterion ("two
  implementations, and the third is not committed") no longer holds: there are
  six. Step 28 is written down but stays deferred — it is the only one that
  spends fork budget.
- `discovery/topology.py` — the sysfs USB tree as a `Source`, which is where CAN
  identity would land. Blocked by "Do not do", not by the seam.
