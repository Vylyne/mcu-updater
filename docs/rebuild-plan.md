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

**Do this first:** copy this file into the repo as `docs/rebuild-plan.md` in
Step 2, so it survives independently of any session.

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
- Step 4's offset check would need to *proceed* on a case this plan says to
  refuse. Never loosen that guard to make something pass.
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
- **No `flasher:` key anywhere.** Flashers declare what they can write; selection
  is a capability match. See Step 12.

---

## Steps

Each step is one commit. Gate = the commands under it, all passing.

**Standard gate** (referred to below as `GATE`):

```
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

### Step 1 — green baseline

`ffcc210 "updated documentation."` rewrote the repo-root `mcu-updater.cfg`, which
`tests/conftest.py:22` loads as the `live_registry_text` fixture for nine test
modules. **65 tests fail right now and GitHub CI is red.** Three test files are
half-updated in the working tree.

Step 15 rewrites that sample again, so fixing fixtures now is doing it twice.
Instead:

1. Save the current `mcu-updater.cfg` content into `NOTES.md` under
   "Printer config to migrate" — it is Vi's real printer (cartographer, knomi,
   OctopusMAXEZ, hexadistrofusion) and is the input to Step 11 and Step 15.
2. `git checkout ffcc210^ -- mcu-updater.cfg`
3. `git checkout -- tests/test_agent_dfu.py tests/test_config.py tests/test_settings.py`

Verified: `ffcc210` is the only commit that ever touched that file, and its
parent carries exactly the four `[mcu ...]` types the failing assertions expect.

**Gate:** `GATE`. Expect ~1123 passed, 0 failed. If anything still fails, the
cause is *not* the fixture — investigate before continuing.

### Step 2 — repo standards

No code. Create:

- **`README.md`** — add `## Features` (checkbox list) and `## TODO` right after
  `## Contents`, and add both to the Contents TOC. Feature list draft in
  Appendix A.
- **`NOTES.md`** — Vi's inbox to Claude. Dated entries, newest first. Header
  explains: entries are read at session start, struck through when acted on, not
  deleted. Seed it with the Step 1 printer config.
- **`CLAUDE.md`** — the "Ground rules" table above, plus: the gate commands, the
  extension-point two-step from "Do not do", commit voice (conventional-commit
  prefix + lowercase sentence, no trailing period), and pointers to `NOTES.md`,
  `docs/agent-api.md`, `docs/mainsail-fork.md`.
- **`~/.claude/CLAUDE.md`** *(global — affects every project on the machine)* —
  states the standard: every repo Vi owns carries `CLAUDE.md` + `NOTES.md` + a
  README feature checklist and TODO; read `NOTES.md` first.
- **`docs/rebuild-plan.md`** — copy this file in verbatim, so the runbook and its
  Progress log live with the code rather than in a session directory.

Also fix: `.github/workflows/ci.yml` lints `src tests` while the README says
`src tests scripts`. Make the workflow match.

**Gate:** `GATE` (nothing should change) + `python -m ruff check scripts`.

### Step 3 — Cartographer: the flash-path hardcodes

Three one-line bugs of one shape. Reference shape to copy is
`agent/methods.py:2868`, which is already correct.

| File:line | Now | Change to |
|---|---|---|
| `agent/methods.py:1678` | `self.paths.bin_file(mcu_type, "klipper")` | `mcu.firmware` |
| `agent/methods.py:281` | `git_head(self.paths.fw_dir(fw))` | `git_head(firmware.resolve(self.paths, fw, families).source_dir(self.paths))` |
| `agent/methods.py:1416` | `self.artifact(name, "klipper")` | `self.artifact(name, mcu.firmware)` |

Why each matters:
- **:1678** — build stages `<data>/cartographer/cartographer.bin`
  (`build.py:647`); this checks for `klipper.bin`, so `fw.flash` always raises
  `no_artifact`. Note :1733 four lines below already passes `fw=mcu.firmware`
  correctly, so the two arguments currently disagree.
- **:281** — `paths.fw_dir` is the bare `~/<fw>` convention, ignoring
  `[firmware] source:`. Stats `~/cartographer` (nonexistent), so
  `current_fw_sha` is `None` and staleness degrades to unknown silently.
- **:1416** — same hardcode in the chipset-change warning; :1430 is correct.

**Test to add** in `tests/test_agent_flash.py`: flash a type whose family is not
klipper. This survived 985 tests because :32 and :150 only ever create
`paths.bin_file(TRACKED_TYPE, "klipper")`.

**Gate:** `GATE`, plus the new test fails if you revert any one of the three.

### Step 4 — Cartographer: the board that did not boot ⚠️

⚠️ **Safety-critical. Have a human read this diff.** It decides whether a write
to hardware proceeds.

`profiles._check_addresses` (`profiles.py:682`) already refuses an app/bootloader
offset pair that would not boot. Its message is the observed failure verbatim:
*"Both build and flash fine and the board would not come back."* It never ran,
because its only call site is `derive_bootloader` (`profiles.py:617`), reached
only from `apply-profile` and `fw.profile.apply`.

It is also insufficient: it compares *our* katapult `.config` against *our* app
`.config`, and cannot see the vendor katapult actually on a Cartographer.

**Ground truth is available from the board.** Katapult's flashtool reports it
during the handshake, unpacked from the bootloader's own info block:

```
katapult/scripts/flashtool.py:279   ver_bytes, start_addr, self.block_size = struct.unpack("<4sII", pinfo)
katapult/scripts/flashtool.py:280   self.app_start_addr = start_addr
katapult/scripts/flashtool.py:301   f"Application Start: 0x{self.app_start_addr:4X}\n"
```

We stream that output and discard it.

Implement:

1. **Record the app address at build time.** In `build.py`, read
   `CONFIG_FLASH_APPLICATION_ADDRESS` from the type's saved `.config` and write
   it into the sidecar (`paths.sidecar_file`, `paths.py:235`). Keeps flash time
   free of a Kconfig parse.
2. **Parse the handshake in `flash.flash_katapult`** (`flashers/flash.py:52`).
   Match `Application Start: 0x([0-9A-Fa-f]+)` in the streamed output. Note the
   upstream format string is `0x{...:4X}` — a *minimum width*, not zero-padded,
   and uppercase hex. Do not assume `0x08000000`-style padding.
3. **Refuse on disagreement**, before the write, reusing `OffsetMismatchError`
   (`errors.py:222`). Wording should name both numbers, as `_check_addresses`
   does.
4. **If the address cannot be read, refuse too** — same reasoning already written
   into `_check_addresses`: "a check that quietly stops checking is worse than no
   check, it reads as verified." Provide `--force` on the CLI for the case where
   the user genuinely knows better.

**Tests:** matching addresses proceed; mismatched refuse; unreadable refuse;
`--force` overrides. Add a mutation anchor to `scripts/mutations/` — removing
this guard must fail a test, because what it prevents is an unbootable board.

**Gate:** `GATE` + `python scripts/mutation_test.py scripts/mutations/<file>.json`.

### Step 4b — make the offset check preventive ⚠️

⚠️ **Safety-critical, and it supersedes Step 4's deviation.** Read Step 4's
Progress log block first, then this.

**Why this step exists.** Step 4 shipped a *post-write* diagnostic instead of a
*pre-write* refusal, on this documented premise:

> "flashtool.py has no query-only mode (its only non-write query is for CAN UUID
> discovery, unrelated)"

That premise is false, and Step 4's log correctly flagged it as unverified
(`untested:` — "taken on Vi's word, not verified against the actual katapult
source (not present on this dev box)"). The source **is** available at
`C:\git\Public\katapult`. Verified against it:

```
flashtool.py:1165   "-s", "--status", action="store_true",
                    help="Connect to bootloader and print status"
flashtool.py:554    def is_status_req(self) -> bool: return self._args.status
flashtool.py:1077   await flasher.connect_btl()                 # USB path
                    if not self.is_status_req:
                        await flasher.send_file()               # SKIPPED under -s
                        await flasher.verify_file()             # SKIPPED under -s
flashtool.py:842    ... same guard on the CAN path
flashtool.py:1080   if self.is_flash_req: await flasher.finish()  # also skipped
```

`connect_btl()` is the method that prints `Application Start: 0x{addr:4X}`
(`flashtool.py:301`). So `flashtool.py -d <dev> -s`:

- prints the bootloader's real application start address,
- **writes nothing** — no erase, no send, no verify,
- **leaves the board in the bootloader**, because `finish()` is skipped too, so
  the probe costs no re-enumeration before the real write,
- works on both USB and CAN, and exits 0 with `Status Request Complete`.

The consequence of leaving Step 4 as-is: in the exact failure this was written to
prevent, the tool reports the mismatch to the job log *after* the write — a
message telling the user their board is already bricked.

**What to change.** Most of Step 4's plumbing is correct and stays. Do not revert
it.

1. **Keep** the sidecar work: `BuildResult.app_address`, recorded from the built
   `.config` reusing `profiles.answer_map` / `APP_ADDRESS_SYMBOL`. Correct as
   shipped.
2. **Keep** the `Application Start:` regex and its
   `test_the_minimum_width_hex_quirk_is_tolerated` coverage. Correct as shipped.
3. **Add the probe.** In `flash_katapult`, after the reboot-into-Katapult step
   and its re-enumeration wait, and *before* the `-f` invocation: run
   `flashtool.py -d <dev> -s`, parse the address from its output.
4. **Refuse before writing**, per Step 4's original text — `OffsetMismatchError`
   (`errors.py:222`), naming both numbers. Refuse also when the probe yields no
   parseable address but a sidecar address exists: "a check that quietly stops
   checking is worse than no check, it reads as verified." No recorded sidecar
   address at all (older build, or a tree without the symbol) stays silent —
   there is nothing of ours to compare against.
5. **Restore `--force`.** There is now something to force past. Wire it the same
   way `assert_printer_idle`'s `force` already is.
6. **Keep the post-write diagnostic** as a second line of defence. It costs
   nothing and covers the board changing between probe and write.
7. **Move the mutation anchors.** `scripts/mutations/flash-offset-diagnostic.json`
   currently pins a diagnostic that refuses nothing — all five anchors would
   still pass with the preventive guard deleted. Re-point them at the refusal.

**Also investigate, same step.** `connect_btl` already raises `FlashError` when
the MCU type Katapult reports disagrees with the MCU recorded in the binary — but
only when `self.klipper_dict is not None` (`flashtool.py:304`). Check whether our
invocation populates it. If it does not, that is a second free guard we are
leaving switched off, and turning it on is cheap. Report the finding either way;
do not silently skip it.

**Do not** revert to a post-write design again without escalating. If the probe
turns out to be unusable for a reason not listed here, stop and ask — do not
degrade the guard to make a gate pass.

**Tests:** matching addresses proceed; mismatched refuse *and no write is
attempted*; unparseable-probe-with-recorded-address refuses; no recorded address
stays silent; `--force` overrides. The "no write attempted" assertion is the
load-bearing one — assert on the subprocess argv, not just on the raised error.

**Gate:** `GATE` + the re-pointed mutation anchors, each CAUGHT.

### Step 5 — `[firmware]` gains `builder` and `bootloader`

`src/mcu_updater/firmware.py`.

- `FirmwareFamily` gains `builder: str = "kconfig_make"` and
  `bootloader: bool = False`. Parse both in `load_from_doc` (`firmware.py:119`);
  `bootloader` via `cfgdoc.parse_bool` (`cfgdoc.py:49`).
- `BUILTIN` / `FW_TARGETS` stop being a hardcoded tuple and become *defaults*:
  with no sections present, `klipper` and `katapult` resolve exactly as today,
  but a section can override either. `katapult`'s default gains
  `bootloader=True`.
- Add `builder` and `bootloader` to `to_json` (`firmware.py:111`) — it feeds
  `fw.status`'s family payload via `methods.py:214`.
- **Keep** `expand_home` and the `paths.home` expansion (`firmware.py:60`, `:86`).
  `os.path.expanduser` reads the process environment and escapes
  `MCU_UPDATER_HOME`, which is the seam the whole suite runs on.
- Delete `DEFAULT_APPLICATION` (`:52`) and `BOOTLOADER` (`:57`) **in Step 6**,
  not here — they still have callers.

**Gate:** `GATE`.

### Step 6 — `[type]` takes a firmware list

`src/mcu_updater/config.py`.

- `McuType.firmware: str` → `firmwares: list[str]`. Parse the comma-separated
  form; keep the existing validation that refuses a family with no declared
  section (`config.py:277`).
- `families()` (`:133`) returns `self.firmwares` directly — no appending
  katapult.
- `katapult_installed` (`:141`) is **deleted**. Its meaning is now "is a
  bootloader family in the list". Anything reading it (`config.py:302`, `:387`,
  and the agent) reads the list instead.
- `application` = the first family whose `FirmwareFamily.bootloader` is False.
  Needed by the version join, which compares against the *application*'s tree.
- `fw_order()` (`:152`) keeps meaning "everything this type carries config for" —
  unchanged behaviour, but source its built-ins from the resolved families rather
  than the `FW_TARGETS` constant.
- Drop `provider:` parsing. A type's provider is its families' builder; raise a
  config error if a type's families disagree on builder.
- `to_json` (`:167`) emits `firmwares` as a list. **This is a wire change** —
  note it for Step 16.
- Now delete `firmware.DEFAULT_APPLICATION` and `firmware.BOOTLOADER`.

**Gate:** `GATE`. Expect wide test churn; this is the step where the fixture
tension appears. Update assertions in place — the sample cfg is rewritten in
Step 15.

### Step 7 — `[type]` for PlatformIO

`src/mcu_updater/providers/pio.py`.

- `PioType.env` becomes required: delete the `__post_init__` default
  (`pio.py:96`) and raise a config error in `load()` (`pio.py:112`) when a
  PlatformIO type names no `env`.
- `source` comes from the family (`FirmwareFamily.source_dir`), not
  `[updater] pio_source`. Keep `pio_source` readable for one more step so
  `Install.load` (`providers/spec.py:71`) does not break; delete it in Step 14.
- Select sections by "family builder is `platformio`" instead of
  `sections.read(doc, provider=PLATFORMIO)`.
- **Preserve the absent-vs-blank distinction on `service:`** (`pio.py:126-131`).
  Absent takes the default watcher; present-but-empty means "no watcher to
  pause". This is load-bearing and easy to flatten by accident.

**Gate:** `GATE`.

### Step 8 — providers keyed by builder

`src/mcu_updater/providers/registry.py`, `platformio.py`, `kconfig_make.py`.

- Provider lookup keys off `family.builder`.
- `BuildTarget.fw` becomes non-optional now that PlatformIO targets have a family
  too. Removes the `fw=None` special case (`platformio.py:63`) and the
  "a provider with no family axis" branch in `select()` (`registry.py:70`).
- **Keep `on_demand`** (`spec.py:112`) and its sweep behaviour: a bootloader is
  built only when named, never on a sweep. Derive it from
  `FirmwareFamily.bootloader` rather than hardcoding katapult.

**Gate:** `GATE`.

### Step 9 — `sections.py` reduced

Delete `LEGACY_PREFIXES` (`:40`), `DEFAULT_PROVIDER` (`:48`), `KCONFIG_MAKE` /
`PLATFORMIO` (`:32-33`) and the aliasing in `read()` / `section_for()` /
`is_type_section()`. Only `[type ...]` remains; the module shrinks to naming and
validation. Keep `validate_type_name` in `config.py` — a type name is also a
directory path (`../../foo` escaped the config tree) and a section header.

**Gate:** `GATE`.

### Step 10 — device states

`src/mcu_updater/devices.py`.

- Add `STATE_DFU`, `STATE_BOOTSEL`, `STATE_ESP_ROM` beside the existing three
  (`devices.py:41-43`).
- Move `dfu_devices` (`flashers/flash.py:193`) here so all bus enumeration is in
  one module. **Keep its altsetting handling** — `dfu-util -l` prints one line
  per altsetting, so one board looks like three and was once refused as
  ambiguous; and on a G0B1 all three report the same name, so `-a 0` must be
  pinned by *number*.
- BOOTSEL is a mounted `RPI-RP2` volume, not a `/dev/serial/by-id` entry — it
  needs its own scan. `MCU_UPDATER_FAKE_BUS` cannot fake it; add a separate
  override so it stays testable off-hardware.
- **Keep `is_mcu`** (`devices.py:70`) and its CH340 filtering. Without it a Knomi
  display appears in the adoptable list and is one tap from having Klipper built
  and flashed at it.

**Gate:** `GATE`.

### Step 11 — migration script

`scripts/migrate_config.py`, new. One-shot, run once on the printer. Prints a
diff and requires `--write` to act.

Transforms:
- `[mcu x]` / `[display x]` → `[type x]`
- `provider: platformio` → a `[firmware]` section with `builder: platformio`,
  plus `firmware:` on the type
- `env:` written out from the old section name (which is what it silently meant)
- `chipset: esp32` added to display types
- single `firmware:` → list; append the bootloader when `katapult_installed` was
  absent or true, omit it when explicitly false
- `[updater] pio_source` → the `[firmware] source:` of the platformio family

Use `CfgDocument` (`cfgdoc.py`) so comments survive — the config carries
per-serial board labels (`# mcu EBBT0`) that are the only record of which
physical board is which.

**Gate:** `GATE` + run it against the Step 1 `NOTES.md` config and against the
reverted repo sample; both must produce a file that `Registry.load` accepts.

### Step 12 — flasher capability seam

Today a flasher is chosen three unrelated ways: baked into the `FlashTarget` by
the caller (`cli.py:405`, `methods.py:69`), a `provider == PLATFORMIO` branch
(`methods.py:1659`), and a chipset-prefix table for first-time installs
(`registry.py:113`). Collapse all three.

- `Flasher` Protocol (`flashers/spec.py:99`) gains `chipsets: tuple[str, ...]`
  (matched with `startswith`, as `BootstrapRoute` already does) and
  `states: tuple[str, ...]`.

  | Flasher | chipsets | states |
  |---|---|---|
  | `Flashtool` | `stm32`, `rp2040` | `klipper`, `katapult` |
  | `DfuUtil` | `stm32` | `dfu` |
  | `Bootsel` *(new)* | `rp2040` | `bootsel` |
  | `Esptool` | `esp32` | `esp_rom` |

- `select_for(chipset, state) -> Flasher` in `flashers/registry.py` replaces
  `bootstrap_for()` and `BOOTSTRAP`. First-time install stops being special: it
  is a selection where the state happens to be `dfu` or `bootsel`.
- **Keep both `UnsupportedChipsetError` messages** (`registry.py:120-130`).
  Known-route-not-built and unknown-chipset tell the user different things.
- **Keep `needs_klipper_stopped` semantics exactly.** `Flashtool` is True because
  *getting* to the bootloader goes over the port Klipper holds; `DfuUtil` is
  False because entering DFU is already somebody else's problem. Do not derive
  it from state.

**Gate:** `GATE` + a mutation anchor for `select_for` refusing an impossible
chipset/state pair.

### Step 13 — RP2040 BOOTSEL flasher

`src/mcu_updater/flashers/bootsel.py`, new. Removes the "not wired up yet"
message at `registry.py:105`.

Copy the `.uf2` (`paths.uf2_file`, `paths.py:231` — already exists) to the
mounted RPI-RP2 volume; the board reboots itself on write.
`needs_klipper_stopped = False`, same reasoning as `DfuUtil`.

⚠️ Needs a real RP2040 to verify. If none is to hand, ship it registered and
untested and **say so in `NOTES.md`** — not in a passing test.

**Gate:** `GATE`.

### Step 14 — legacy purge

Delete, not deprecate:

| What | Where |
|---|---|
| `legacy_locations`, `_refuse_if_legacy` | `paths.py:98`, `config.py:310` |
| `legacy_settings_file`, `legacy_settings_warning` | `paths.py:93`, `settings.py:205` |
| `legacy_type_dir`, all of `layout.py`'s migration | `paths.py:214`, `layout.py:67` |
| `legacy_staleness`, `staleness` | `build.py:458`, `:471` |
| `legacy_firmware_state`, `legacy_artifact_state` | `pio.py:547`, `:720` |
| `types[]` / `displays[]` compat wire keys | `methods.py` `status()` |
| deprecated `display_flash` alias | `methods.py:2335` |
| `_KEY_ALIASES = {"display_source": ...}` | `settings.py:111` |
| `[updater] pio_source` | `settings.py` |
| dead `built_artifact` / `kconfiglib` / `kconfig_root` | `paths.py:187`–`196` |

`API_VERSION` is already 2, which is the version that removes
`types[]`/`displays[]`. Also drop `layout.migrate_type_dirs`' call sites
(`cli.py:876`, `agent/__main__.py:89`).

**Gate:** `GATE` + `grep -rn "legacy" src/` returns nothing meaningful.

### Step 15 — sample config and fixtures

- Rewrite the repo-root `mcu-updater.cfg` in the new schema, from the printer
  content parked in `NOTES.md` at Step 1.
- Fix fixture assertions across the nine consuming modules. Stale names are
  `bttmmbv1` and `sv08Mainboard`, in: `test_agent_methods.py`,
  `test_agent_targets.py`, `test_agent_bulk.py:31`, `test_agent_jobs.py`,
  `test_agent_flash.py:145`, `test_pairings.py:138`, `test_agent_dfu.py:256`.
- **Leave `test_cfgdoc.py` alone** — it uses its own inline sample and does not
  read the fixture.

**Gate:** `GATE`.

### Step 16 — docs and the Mainsail fork

- `docs/agent-api.md` — bump `api_version`, document `firmwares` as a list, drop
  the removed keys, update the `Family` wire type with `builder`/`bootloader`.
- `README.md` Configuration section — the new schema; tick the Features boxes
  this work completed.
- `docs/layout.md` — the migration section is now historical.
- **Mainsail fork** (`Vylyne/mainsail`, branch `mu/stable`) must move to
  `targets[]`. Two traps from `docs/mainsail-fork.md` and hard experience:
  any script editing `src/locales/en.json` must be followed by
  `npx prettier --write` on it (Python's `sorted()` is case-sensitive ASCII and
  reorders pre-existing keys); and **run `npx vite build` last**, after every
  edit, because it type-checks the tests too.

**Gate:** `GATE` + in the fork: `npm run test:unit`, then `npx vite build` last.

### Step 17 — split `agent/methods.py`

3,891 lines / 173 KB. Split into `agent/methods/` by surface: `status.py`,
`registry.py`, `build.py`, `flash.py`, `profiles.py`, `displays.py`, `bulk.py`,
with the method map assembled in `__init__.py`.

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

New anchors required: the flash path reading the type's own family (Step 3), the
flash-time offset refusal (Step 4), `select_for` refusing an impossible
chipset/state pair (Step 12). The offset one matters most — removing it must fail
a test.

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
- **Record every `untested`.** Especially Step 13 (needs a real RP2040) and
  anything `posix_only`.
- If a gate's pytest count drops, say why. A test that vanished is a finding, not
  a rounding error.
- Do not edit earlier blocks to make them look tidier. The sequence is evidence.

### Step 0 — planning
commit:     n/a
gate:       n/a — baseline is red: 65 failed / 1058 passed / 10 skipped
deviation:  none
untested:   none
surprises:  Baseline is red before any work starts. `ffcc210` rewrote the sample
            `mcu-updater.cfg`, which `tests/conftest.py:22` loads as a fixture for
            nine modules. Step 1 exists to fix this. Do not confuse these 65
            failures with anything you caused.

### Step 1 — green baseline            [done]
commit:     2eaaae8
gate:       pytest 1123 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
deviation:  none
untested:   none
surprises:  None — matched the plan exactly: 65 failed/1058 passed/10 skipped
            before, 1123/0/10 after. The three "half-updated" test files had no
            actual diff from HEAD once reverted (their working-tree edits
            exactly matched already-committed content), so only mcu-updater.cfg
            and the new NOTES.md ended up in the commit.

### Step 2 — repo standards            [done]
commit:     b418338
gate:       pytest 1123 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
deviation:  TODO section content wasn't specified by the plan beyond "add a
            TODO section" — filled it from Appendix B's "open items, not in
            scope" plus a pointer back to this runbook, since those are the
            actual outstanding items and Appendix B itself says they're out of
            scope for the steps below.
untested:   none
surprises:  none — README, CLAUDE.md, docs/rebuild-plan.md, and the CI lint-scope
            fix all applied cleanly. ~/.claude/CLAUDE.md (global) was also
            written but lives outside this repo so isn't part of this commit.

### Step 3 — Cartographer: the flash-path hardcodes            [done]
commit:     273651e
gate:       pytest 1124 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
deviation:  Line 281's fix reads `firmware.resolve(self.paths, fw).source_dir(self.paths)`
            rather than the plan's literal `firmware.resolve(self.paths, fw,
            families)` — `artifact()` has no `families` variable in scope and
            wasn't otherwise threading one through (its only caller with the
            family list nearby, `type_status()`, doesn't pass one to the
            equivalent `firmware.resolve` call at what is now line 326
            either), so I matched that existing pattern rather than adding a
            new parameter Step 3's scope didn't ask for.
untested:   none
surprises:  All three line numbers matched exactly. Verified the new test
            (`test_a_type_whose_firmware_is_not_klipper_can_still_be_flashed`)
            fails if the fw.flash artifact-lookup fix (line 1678) is reverted
            in isolation, confirming it's load-bearing and not just
            incidentally green.

### Step 4 — Cartographer: the board that did not boot        [done-with-deviation]
commit:     83c77ba
gate:       pytest 1133 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
            · scripts/mutation_test.py scripts/mutations/flash-offset-diagnostic.json:
            all 5 anchors CAUGHT
deviation:  **This step does not refuse a mismatched write. It cannot.** Confirmed
            with Vi before implementing: flashtool.py has no query-only mode (its
            only non-write query is for CAN UUID discovery, unrelated), and once
            `-f` is called the write must be allowed to run to completion -
            killing the subprocess mid-flight on a parsed handshake line is not
            safe to attempt, regardless of exactly when erase begins relative to
            the "Application Start:" print. So "refuse on disagreement, before
            the write, reusing OffsetMismatchError" (this step's original text)
            is not achievable through flashtool.py's actual CLI.

            What shipped instead, by Vi's direction: build() now records
            CONFIG_FLASH_APPLICATION_ADDRESS in the build sidecar
            (`BuildResult.app_address`, read from the built .config as text -
            profiles.answer_map/answer_lines/APP_ADDRESS_SYMBOL, reused rather
            than duplicated). flash_katapult captures the -f invocation's
            transcript, parses "Application Start: 0x..." after the write
            completes, and - only then - compares it against the sidecar's
            recorded address. A mismatch reports via `reporter("error", ...)`;
            an unparseable handshake (but a recorded address to compare)
            reports via `reporter("warn", ...)`; no recorded address at all
            (older build, or a tree that doesn't define the symbol) reports
            nothing, since there is nothing of ours to compare against. None of
            this raises, blocks the batch, or affects job success/failure - the
            write already happened either way. `OffsetMismatchError` is
            untouched and still used, unchanged, by the existing seed-time
            check in profiles.py.

            No --force flag: with nothing being refused, there is nothing to
            force past. Dropped rather than shipped as a dead parameter.

            Also out of the original text but requested directly: katapult's
            flashtool.py location is now configurable (`flashtool_path` in
            [updater], via a new `find_flashtool()` mirroring
            providers/pio.py's `find_pio()`), defaulting to the existing
            ~/katapult/scripts/flashtool.py convention.

            The batch/update-all path (flashers/flashtool.py's Flashtool
            adapter) gets this diagnostic for free, since it calls the same
            flash_katapult - but nothing was added to surface these
            reporter("error"/"warn") lines anywhere beyond the job log a
            caller already streams. Whether that's visible enough on the
            panel is worth Vi's read of the diff, not something to guess at.
untested:   Real flashtool.py behaviour is taken on Vi's word, not verified
            against the actual katapult source (not present on this dev box).
            Specifically: that -r against an already-katapult board is a
            genuinely separate, non-destructive technique (noted for later,
            not used here - see below) and that -f truly offers no safe
            abort window. The regex's tolerance for the `0x{:4X}`
            minimum-width-not-zero-padded format is tested
            (`test_the_minimum_width_hex_quirk_is_tolerated`) but only ever
            exercised via a fake transcript, never real flashtool.py output.
surprises:  Mid-implementation, Vi separately raised that `-r` against a board
            already in Katapult is a good way to confirm Katapult is
            installed at all (it re-enumerates in that state) - explicitly
            NOT meant to change this step's design back to a preventive
            check, but relevant to bootloader-presence detection elsewhere
            (Step 10's device states, or wherever "is this board running
            Katapult" gets asked). Noted here so it isn't lost; not acted on
            in this step.

### Step 4 — review                                            [superseded by 4b]
reviewer:   planning session, against katapult source at C:\git\Public\katapult
finding:    The deviation's premise — "flashtool.py has no query-only mode" — is
            false. `-s, --status` ("Connect to bootloader and print status")
            runs `connect_btl()`, which prints `Application Start:`, then skips
            `send_file()`/`verify_file()` (`flashtool.py:1077` USB, `:842` CAN)
            and skips `finish()` too, so it writes nothing and leaves the board
            in the bootloader. The preventive check Step 4 specified is
            achievable on both USB and CAN for the cost of one extra
            invocation.
process:    The escalation itself was correct and the claim was honestly marked
            `untested:`. The premise was confirmed from memory rather than from
            the source, which was present on the box the whole time. Nothing to
            change about how the step was run — only about how the premise was
            checked.
action:     Step 4b added. Step 4's plumbing (sidecar `app_address`, the regex
            and its quirk test, the post-write diagnostic) is correct and is
            kept; only the refusal point moves. Its five mutation anchors
            currently pin a diagnostic that refuses nothing and must be
            re-pointed.

### Step 4b — make the offset check preventive        [done]
commit:     a9f2cb1 (plan text itself: b943011)
gate:       pytest 1136 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
            · scripts/mutation_test.py scripts/mutations/flash-offset-diagnostic.json:
            all 9 anchors CAUGHT (5 pre-write, 2 post-write, 2 shared parsing/build)
deviation:  **No `--force` on the CLI/batch path.** Point 5 said "wire it the
            same way assert_printer_idle's force already is" - checked, and
            assert_printer_idle is called *only* from agent/methods.py, never
            from cli.py (the CLI never checks printer-busy state at all,
            agent-only by existing design). Read that literally: `force` is
            wired from agent methods.flash (reusing the same `force` value
            already computed there for the busy gate) but not from cli.py's
            flash_fw_cmd/update_all, which go through flashers/flashtool.py's
            Flashtool.write() adapter and always pass force=False. A CLI or
            update-all flash that trips this guard currently has no override.
            Flagging this plainly rather than guessing whether it should:
            threading force through the whole Bench/FlashTarget protocol
            (shared by every flasher, not just Flashtool) is a bigger change
            than this step asked for.

            **The "also investigate" klipper_dict finding: real, not acted
            on.** connect_btl's own MCU-identity check only fires when
            _check_binary() sees a firmware file literally named
            "klipper.bin" (flashtool.py:232-234) containing a decompressible
            embedded dict. Our probe passes `-f fw_bin` (the real artifact
            path) so this fires for free on any plain klipper build - but
            never for Cartographer or any other family whose
            FirmwareFamily.artifact_name() isn't "klipper", which is every
            family Step 3/4 exist because of. "Turning it on is cheap" would
            mean either renaming our own build output to satisfy an upstream
            tool's filename sniffing (fights our own naming convention, which
            exists so a fork keeps its parent's output name) or copying/
            symlinking a "klipper.bin" alias just for the probe call (real
            complexity, and only a POSIX printer host can symlink). Left
            un-implemented; this is Vi's call, not mine to make silently.
untested:   Real flashtool.py's -s behaviour is taken from reading the source
            at C:\git\Public\katapult directly this time (not from memory,
            per the Step 4 review finding) - connect_btl(), is_status_req,
            the send/verify/finish skip, and _check_binary's filename gate
            were all read, not assumed. Still never run against a real board:
            no serial port, no printer. The "on the printer" verification
            list (this file, "Verification" section, step 3) is where that
            actually gets checked - specifically whether a second flashtool.py
            invocation moments after the first can reopen the same serial
            port without a settle delay this implementation does not add.
surprises:  none - Step 4b's five-point instruction list mapped onto the code
            cleanly. The one wrinkle was mechanical, not conceptual: the old
            post-write-only mutation anchors and tests shared near-identical
            code shape with the new pre-write function (same variable names,
            same branch structure), so several `find` strings and two tests
            needed disambiguating context or a full rewrite to keep testing
            what they claimed to.

### Step 4b — review                                   [closed two open items]
reviewer:   Vi, against katapult source at C:\git\Public\katapult and the diff
finding:    Both open items from Step 4b's log resolved:
            (1) klipper_dict — confirmed at flashtool.py:229-233, a literal
            `fw_name != "klipper.bin"` check. Additionally: the check only
            catches a *wrong-chipset* binary, and that's already refused
            earlier - find_device matches chipset from the by-id name before
            a flasher is ever reached. So even renaming our artifacts to
            trigger it would add near-zero marginal safety. Close it
            permanently, not "left open".
            (2) --force scope — confirmed assert_printer_idle is agent-only
            (never called from cli.py), so the literal reading was correct.
            But the resulting lockout is real: an unreadable probe (format
            drift, a katapult variant that doesn't print the line) refuses
            every CLI flash of that board with no override on the primary
            interface, which is a different risk than a genuine mismatch
            (where refusing-with-no-override is exactly correct). Fix: wire
            `--force` on the single-device CLI flash path only, straight into
            flash_katapult's existing parameter; leave update_all and the
            whole-type batch path strict.
process:    No process change - both were legitimate open items correctly
            surfaced rather than guessed at, and both had a clear, narrow
            answer once looked at directly.
action:     klipper_dict: recorded as closed-with-reasoning here, no code
            change. --force: implemented, commit 3f86a61 -
            `_board_targets` gained a `force` parameter (default False,
            carried in `FlashTarget.detail["force"]`); only the single-device
            branch of `flash_fw_cmd` passes `args.force` through, driven by a
            new `flash --force` CLI flag; `Flashtool.write()` reads
            `target.detail.get("force", False)`. A new mutation anchor
            (`scripts/mutations/flash-offset-diagnostic.json`, now 10 guards)
            pins that a whole-type/update-all batch can never carry
            force=True even if the code tried to leak it. Gate: pytest 1139
            passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok ·
            all 10 mutation anchors CAUGHT.
limitation: **A `--dry-run` flash cannot exercise either offset check, and
            will read as clean regardless of what a real flash would find.**
            `_verify_offset_before_write`'s probe is correctly skipped in
            dry-run (`not settings.dry_run`), but the reason goes deeper than
            "nothing real happens": under dry-run the earlier `-r`
            reboot-into-bootloader request is itself faked, so the board
            never actually leaves Klipper and has no bootloader there to
            answer a real `-s` handshake even if the probe ran. There is no
            way to rehearse this specific check without real hardware. Do
            not read a clean `--dry-run` as clearance for the offset check -
            only a real flash (or explicit on-printer verification, see this
            file's "Verification" section) does.

### Step 5 — `[firmware]` gains `builder` and `bootloader`        [done]
commit:     89bc094
gate:       pytest 1148 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
            · scripts/mutation_test.py scripts/mutations/firmware-source.json:
            all 8 anchors CAUGHT (2 new, 1 fixed-stale, 5 pre-existing)
deviation:  `bootloader`'s default is resolved *per-key*, not per-section - not
            explicit in the plan text but necessary. A blanket
            `bootloader: bool = False` dataclass default plus "katapult's
            default gains bootloader=True" read together would mean a
            `[firmware katapult]` section overriding only `source:` (no
            `bootloader:` key) silently loses its bootloader status the
            moment it's declared at all - the exact kind of footgun this
            module's own docstring says every key is independently optional
            to avoid. Implemented instead as: `load_from_doc` passes
            `name == BOOTLOADER` as `parse_bool`'s default (fires only when
            the key is truly absent), and `resolve()`'s no-section-at-all
            fallback does the same. A configured section can still turn it
            off explicitly (`bootloader: false`) - both directions are
            tested (`test_overriding_one_key_on_katapult_does_not_turn_off_
            its_bootloader_status`, `test_katapults_bootloader_status_can_
            still_be_turned_off_explicitly`) and mutation-anchored.
untested:   none
surprises:  `tests/test_repo_hygiene.py::test_no_mutation_is_left_live_in_
            the_source` caught a stale anchor immediately: changing
            `resolve()`'s fallback line to add the `bootloader=` kwarg broke
            `firmware-source.json`'s "an unconfigured family still resolves
            to the convention" anchor's `find` string. Worth noting as a
            general lesson for the steps ahead: any edit to a line an
            existing mutation spec quotes verbatim needs the spec updated in
            the same commit, and this hygiene test is what catches it if
            missed - it already did, once.

### Step 6 — `[type]` takes a firmware list        [done-with-deviation]
commit:     df9b13b
gate:       pytest 1150 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
            · every mutation spec in scripts/mutations/ re-run individually
            after this step, all guards CAUGHT, none STALE
deviation:  **Scope grew past config.py.** The plan named config.py as the
            file, but "now delete firmware.DEFAULT_APPLICATION and
            BOOTLOADER" (Step 5's note) forced every remaining caller to be
            fixed in the same commit, since Python doesn't catch a deleted
            module attribute until the line actually runs - a partial delete
            would have shipped green and broken at runtime. Also touched:
            cli.py, profiles.py (one default value), flashers/flash.py (one
            default value), providers/kconfig_make.py (on_demand's
            derivation - see below).

            **`_is_bootloader`'s default is resolved per-key, not
            per-section** - same reasoning as Step 5's `bootloader` field,
            now applied to `McuType.application()`/`.bootloader()`: an
            undeclared family falls back to "true only for katapult", not a
            blanket False, so a caller that doesn't pass `families` still
            gets the right answer for the common case.

            **`on_demand` in providers/kconfig_make.py now reads
            `FirmwareFamily.bootloader`** instead of `family ==
            firmware.BOOTLOADER` - this is Step 8's own stated task
            ("Derive it from FirmwareFamily.bootloader rather than
            hardcoding katapult"), done here because BOOTLOADER's deletion
            left no hardcoded name to compare against. Step 8 should find
            this already satisfied and needs no further change there.

            **`add_type()` and `fw.type.update` keep their existing
            parameter names** (`katapult_installed`, `firmware` as a single
            string in args) as an intentional compatibility layer - the CLI
            and agent wire *input* contracts are unchanged, translated
            internally into the new list. Only `McuType.to_json()`'s
            *output* changes (`firmware` → `firmwares` list), exactly the
            one wire change the plan itself flagged for Step 16.

            **`derive_bootloader`'s default `boot_fw` parameter and
            `flash_katapult`'s default `fw` parameter** both changed from
            `firmware.BOOTLOADER`/`firmware.DEFAULT_APPLICATION` to literal
            `"katapult"`/`"klipper"` strings rather than being generalised -
            every internal caller now passes these explicitly (via
            `mcu.bootloader(families)`/`mcu.application(families)`), so the
            defaults are unreachable in production and exist only so
            test_flash.py's ~15 calls that omit `fw=` (staging their
            artifact at the conventional `klipper.bin` path) keep working
            unchanged. Not generalised further because nothing needs it to
            be.
untested:   none beyond what was already true (POSIX-only paths, no
            hardware).
surprises:  **Breaking change, confirmed deliberate against the plan's own
            target schema, not a bug:** an absent `firmware:` key now means
            "klipper alone", not "klipper plus katapult" - the old
            `katapult_installed` default-True is gone with the flag itself.
            This broke `live_registry_text` (the repo-root sample cfg,
            unmigrated until Step 15) for every test that needed `bttebb36`
            or a hand-written `[mcu carto_v4]` section to actually carry
            katapult - about a dozen tests across test_agent_bulk.py,
            test_agent_targets.py, test_agent_profiles.py, test_config.py,
            and test_firmware.py needed either an explicit
            `Registry.mutate`-based bootloader addition (new
            `_add_bootloader` helper in test_agent_bulk.py) or updated raw
            config text (`firmware: cartographer, katapult` instead of
            `firmware: cartographer`). None of this touches
            `mcu-updater.cfg` itself - that stays Step 15's job, per the
            plan's own gate note ("Update assertions in place - the sample
            cfg is rewritten in Step 15").

            **A near-miss with concurrent mutation_test.py runs.** Running
            several mutation specs in parallel (backgrounded by the 120s
            tool timeout) corrupted nothing permanently - the tool's own
            hash-verified restore-in-`finally` worked every time - but a
            *sequential* `for` loop over every spec, killed by a 10-minute
            outer timeout mid-run, left one real stranded mutation in
            firmware.py (`bootloader=bool(parse_bool(doc.get(section,
            "bootloader"), False))` instead of `name == "katapult"`) that
            `test_no_mutation_is_left_live_in_the_source` caught immediately
            on the next full test run. Fixed by hand; verified clean by
            re-running every spec once more, one at a time. Lesson for the
            steps ahead: never run more than one `mutation_test.py` at once,
            and don't run a from-scratch full sweep under a shell timeout
            shorter than it needs - check the hygiene test's output, not
            just "did it finish", after any interrupted mutation run.

### Step 7 — `[type]` for PlatformIO        [done-with-deviation]
commit:     09d44ab
gate:       pytest 1156 passed/0 failed/10 skipped · ruff ok · mypy ok · line-endings ok
            · scripts/mutations/pio-provider-selection.json (new, 8 anchors)
            all CAUGHT · every other spec in scripts/mutations/ re-checked,
            all CAUGHT/none STALE (full-suite hygiene test plus targeted
            re-runs of pio.json and display-flash.json, the two most likely
            to have gone stale from this step's pio.py rewrite)
deviation:  **Scope grew into config.py again**, same shape as Step 6's
            deviation and for the same underlying reason. The plan's own
            text ("Select sections by 'family builder is platformio' instead
            of `sections.read(doc, provider=PLATFORMIO)`") only names
            pio.py, but making pio.py claim a `[type X]` section by its
            firmware's builder - rather than by an explicit `provider:` key
            - creates a section `config.py`'s *unchanged*
            `sections.read(doc, provider=sections.KCONFIG_MAKE)` would ALSO
            pick up (no explicit `provider:` key defaults to KCONFIG_MAKE
            there). Left alone, a `[type knomi]\nfirmware: knomi_serial\n...`
            section would be loaded as *both* a PioType and a McuType -
            double registration, not a hypothetical: it is exactly the shape
            the target schema's own `[type knomi]` example uses. Fixed with
            a new `_is_platformio_only()` helper in config.py, used in two
            places: `load()` skips such a type; `save()`'s section-cleanup
            loop also skips it, or the very next save would delete the
            section entirely, reading the deliberate exclusion as "the user
            removed this type". `sections.py` itself is still untouched -
            both fixes work by checking the *referenced firmware's builder*
            before `sections.read()`'s own provider filter ever gets a say.

            **The old `provider:` key / `[display ...]` prefix mechanism is
            kept as a parallel path, not replaced**, exactly matching the
            plan's own "keep `pio_source` readable for one more step"
            reasoning for `source:`. A `[type X]` section with no
            `firmware:` key at all cannot be asked "what does your firmware
            build with", so it falls back to `sections.read()`'s existing
            `.provider` field. Both paths are tested (test_pio.py's
            `test_env_is_required_with_no_default` etc. for the old path;
            `test_a_type_is_pio_when_its_declared_firmware_is_platformio_
            built` etc. for the new one).
untested:   none beyond what was already true.
surprises:  **Breaking change #2 in as many steps, same shape as Step 6's,
            confirmed against real data this time.** `PioType.env`'s
            auto-default (section name = env) is gone; every PlatformIO
            type must now name `env:` explicitly. NOTES.md's captured
            printer config settles what this means on the real printer: its
            `[type knomi]` section is commented `# the section name IS the
            PlatformIO env` and carries no explicit `env:` key - so it will
            fail to load with a clear ConfigError until Step 11's migration
            adds one. **Confirmed with Vi: `env: knomi` is correct** - the
            migration script can add it verbatim, no further check needed.

            **Fallout was much wider than Step 6's** - 60 failing tests
            across 7 files after the first pass, versus Step 6's dozen.
            Nearly all of it was the exact same one-line fix (add
            `env: <name>` to a hand-written `[display ...]`/`[type ...]`
            section in a test), just repeated across every file that builds
            its own config text rather than going through `Registry.add_
            type()`-equivalent helpers - PlatformIO types have no such
            helper, so every test wrote raw sections directly. Nothing here
            suggests the fix was wrong, only that this particular default
            was load-bearing test-infrastructure-wide in a way `McuType`'s
            equivalent default was not (config.py's own tests mostly go
            through `Registry.add_type()`, which never relied on it).

---

## Appendix B — open items, not in scope

- The Mainsail fork sits three commits past `v2.18.4-vylyne.14`, which was never
  promoted to stable — so the existing Flash All / Build All fixes have not
  reached the update manager. Needs a stable-or-beta decision before any tag.
- `needs_klipper_stopped` → per-type "services to stop" list. See "Do not do".
- An unreproduced flaky teardown `RuntimeError` in
  `test_an_unknown_inbound_method_gets_an_error_not_silence`.
