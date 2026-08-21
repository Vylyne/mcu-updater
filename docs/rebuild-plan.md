# mcu-updater: schema-first rebuild — retired runbook

**This plan is finished and has been retired.** It ran from 2026-08-19 to
2026-08-21 across 28 steps, and what survives it lives in four places now:

| What | Where it went |
| --- | --- |
| The config vocabulary it introduced | [../README.md](../README.md) — Configuration |
| The wire contract it corrected | [agent-api.md](agent-api.md) |
| Standing "do not do this" decisions | [decisions.md](decisions.md) |
| Open items and the release checklist | [../README.md](../README.md) — TODO, Development |

Nothing is queued against this file. A future body of work gets its own
runbook rather than a Step 29 here.

## Why this file still exists

Roughly a dozen comments in `src/` and `tests/` cite this plan **by step
number** — `# See docs/rebuild-plan.md Step 18`,
`(docs/rebuild-plan.md Step 11)`, and so on. The specs and progress log those
citations point at were trimmed in two passes (Steps 1–15 on 2026-08-20, the
rest on 2026-08-21) to keep the file to live work, which left every one of them
dangling.

The index below is the fix: it maps each step to the commit that implemented
it, so a citation resolves with `git show <sha>`. The full spec and progress-log
entry for any step are in this file's own history:

```shell
git show <sha>                       # the step's implementation
git log -p -- docs/rebuild-plan.md   # its spec and progress-log entry
```

## What it set out to do, and did

Two live bugs motivated it, both fixed: **`fw.flash` could not flash
Cartographer at all** (one line assumed klipper), and **a Cartographer flashed
from the CLI did not boot** — recovery was DFU → katapult → the vendor's
prebuilt bin, and a guard for exactly that case existed but never ran on the
flash path. Beyond those, it moved config off `provider:`/singular `firmware:`
onto a firmware-family model, made flasher selection a capability match, and
built the discovery seam that gives a write a confirmed identity rather than a
remembered one.

## Step index

Steps 1–22 are the schema-first rebuild proper; 23–28 are the discovery surface,
scheduled on 2026-08-21 once the deferral criterion in `providers/spec.py` and
`flashers/spec.py` ("two implementations, and the third is not committed") had
been overtaken by there being six.

| Step | Title | Commit |
| --- | --- | --- |
| 1 | green baseline | `2eaaae8` |
| 2 | repo standards | `b418338` |
| 3 | Cartographer: the flash-path hardcodes | `273651e` |
| 4 | Cartographer: the board that did not boot | `83c77ba` |
| 4b | make the offset check preventive | `a9f2cb1` |
| 5 | `[firmware]` gains `builder` and `bootloader` | `89bc094` |
| 6 | `[type]` takes a firmware list | `df9b13b` |
| 7 | `[type]` for PlatformIO | `09d44ab` |
| 8 | providers keyed by builder | `726d3ce` |
| 9 | `sections.py` reduced | `fb6f047` |
| 10 | device states | `1949ef4` |
| 11 | migration script | `d0387f9`, `8118080` |
| 12 | flasher capability seam | `6232a36` |
| 13 | RP2040 BOOTSEL flasher | `8a9ce09` |
| 14 | legacy purge | `a597e2f` |
| 15 | sample config and fixtures | `3b24f0b` |
| 16 | docs and the Mainsail fork — survey only | `8d6585e`, `d564094` |
| 16a | wire fold, `api_version` 3, docs | `ebec15f` |
| 16b | Mainsail fork migration off `fw.display.*` | `b16dadb8` (fork) |
| 17 | split `agent/methods.py` into `agent/methods/` | `3605a62` |
| 18 | narrow the phantom `FwConfig` slots | `726f31c` |
| 19 | make `docs/agent-api.md` true | `5c46df7` |
| 20 | fix the fork against the corrected contract | `13cb4d81` (fork) |
| 21 | close the `.vue` type-checking gap | **blocked** — see [decisions.md](decisions.md) |
| 22 | on-printer verification | partly run; see README Development |
| 23 | `discovery/spec.py` + `discovery/registry.py` | `d1a4e9d` |
| 24 | move the three bus sources behind the seam | `0c2bb9e` |
| 25 | move the two knomi sources out of `providers/pio.py` | `7fd9da6` |
| 25b | name the knomi sources for their firmware | `9a3388a` |
| 26 | `discovery.confirm()`; `port_for` becomes a caller | `40758e7` |
| 27 | extend confirm to `flashtool` | `8f000bc` |
| 28 | `confidence` on the wire | `eeb9d49` |

Fork commits are on `Vylyne/mainsail`, branch `mu/stable`, not in this repo.

## The two steps that did not close

**Step 21 — the `.vue` type-checking gap.** Blocked, not deferred: no `vue-tsc`
version can check this tree, for a structural reason. The full finding is in
[decisions.md](decisions.md); read it before proposing a fix, because the
obvious ones were tested and do not work.

**Step 22 — on-printer verification.** Partly run. Vi flashed the Cartographer
on hestia on 2026-08-21 and the flash-path half is confirmed end to end (see
Step 27's log entry via `git log -p`, which records the transcript and the
`.flashed.json` evidence). The remaining checks are in the README's Development
section. Step 13's RP2040 BOOTSEL flasher has still never met a board.

## What the log recorded, and why it was kept honest

Worth carrying forward into whatever runbook comes next, because it caught real
things:

- **Three of the bugs this plan fixed were invisible to 985 passing tests.** The
  gate is necessary and not sufficient.
- **Three times, a step was declined or redesigned on a stated fact that turned
  out to be wrong** — a stale file-count in `mainsail-fork.md`, a wire shape read
  from the wrong function, an `api_version` bump the code's own rule said was
  unnecessary. Each was caught by checking the claim against the thing it
  describes. When a step is declined or redesigned on a stated fact, verify the
  fact first.
- **Deviations were logged even when they were right.** A silent improvement is
  indistinguishable from a mistake at review time.
