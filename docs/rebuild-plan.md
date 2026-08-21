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
|---|---|
| **LF line endings everywhere**, repo and working tree | Ships to a Linux printer; a `\r` in a shebang becomes `bad interpreter: python3^M`. `.gitattributes` pins it. Run `python scripts/check_line_endings.py` before every commit. |
| **stdlib only** — never add a dependency | `pyproject.toml` `dependencies = []` is deliberate. The agent talks to Moonraker over a unix socket with nothing but stdlib. |
| **Python 3.9 is the floor** | Raspberry Pi OS. No `match`, no `X \| Y` at runtime, keep `from __future__ import annotations`. `UP007`/`UP045` are ruff-ignored on purpose — leave `Optional[X]` alone. |
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
  the tests too.

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

## Verification

**Per step:** the `GATE` block above. CI additionally runs Python 3.9 (the Pi
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

1. `python scripts/migrate_config.py` — inspect the diff before `--write`.
2. `updatefw status` — every type resolves; nothing reads as unmanaged.
3. `updatefw build cartographer`. Then confirm the offsets agree *before* any
   write: the app's `FLASH_APPLICATION_ADDRESS` against the `Application Start:`
   the handshake reports.
4. `updatefw flash <carto-serial>`, then `fw.flash` from the panel.
   ⚠️ **Bench board only** — this is the board that bricked, and recovery is
   DFU + katapult + a vendor bin.
5. `updatefw update-all --dry-run`, then for real.
6. **Klipper is running and ready after every one of these.** That is this
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

---

## Appendix B — open items, not in scope

- ~~The Mainsail fork sits three commits past `v2.18.4-vylyne.14`~~ — **resolved
  2026-08-19.** `v2.18.4-vylyne.19` is promoted to stable, and `build_all` /
  `flash_all` are tested on hardware. Step 16's fork work starts from `.19`.
- `needs_klipper_stopped` → per-type "services to stop" list. See "Do not do".
- An unreproduced flaky teardown `RuntimeError` in
  `test_an_unknown_inbound_method_gets_an_error_not_silence`.
- **Phantom `FwConfig` slots for every globally declared family.**
  `Registry.load()`'s per-type loop (`config.py:380`) iterates *every*
  `[firmware ...]` family, and `mcu.fw()` (`config.py:163`) is `setdefault` —
  so a read creates the slot. Every type ends up carrying `cartographer` and
  `knomi_serial` entries it never declared, visible in `to_json()`,
  `fw.artifacts`, `fw.type.list` and `type_status()`.
  **Reporting only** — build and flash use `families()`, not `fw_order()`, so
  nothing compiles or writes a phantom family. Found in Step 15, verified by
  review, deliberately deferred by Vi. Two tests pin the current behaviour and
  are commented as a known bug:
  `test_artifacts_returns_both_firmwares`, `test_status_type_shape`.
  Fix is to narrow the loop to `mcu.firmwares` — **not** `firmware.BUILTIN`,
  which would re-hardcode klipper/katapult. Full reasoning in `NOTES.md`,
  2026-08-20.
