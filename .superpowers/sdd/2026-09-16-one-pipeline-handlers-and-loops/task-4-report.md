# Task 4 report: every write selects through its family

## Implemented

- Added `flashers.select_device`, `flashers.refusal`, and `flashers.select_each`.
  Selection resolves each device's declared firmware family and helper, preserves
  request order, and turns only `NoFlasherError` into a batch refusal. An
  undeclared family or bad helper name remains a runtime `ConfigCorruptError`.
- Removed `flashers.select_for` and its global chipset/state selection path.
- Extended `flashers.write_all` to report refusals first, optionally collect the
  original write exceptions, and skip the readiness callback when no target was
  written.
- Routed `fw.flash_all`, `fw.update_all`, serial/CAN/display `fw.flash`, CLI
  `flash`/`update-all`, and first install through each device's firmware family.
- Preserved the serial RPC's existing wire behavior by collecting and re-raising
  the write's original `UpdaterError`; CAN and CMake retain their existing
  `FlashError` wrapping.
- First install now asks `[firmware katapult]` for its writer and keeps the
  existing `unsupported_chipset` refusal when that list cannot write the bare
  board.
- Closed the carried Task 3 defect by restricting Bootsel's helper handoff branch
  to `KIND_SERIAL`. Screen and CAN devices now fall through to the next flasher
  in the family list instead of reaching `Bootsel.target` without `uf2_file`.
- Updated the mutation corpus: added `batch-selection.json`, removed the obsolete
  `flasher-selection.json`, and re-anchored BOOTSEL erase, force, and Bootsel
  support guards.
- Updated the bulk/display/API decision documentation. Per the coordinator's
  explicit Task 3 carry-forward instruction, the ordered `fw.flash` preconditions
  table was not given the brief's `no_flasher` row; that row remains Task 11.

## TDD evidence

### RED: batch selection and Bootsel handoff

Command:

```text
C:\git\github\mcu-updater\.venv\Scripts\python.exe -m pytest tests/test_flasher_select.py -q -k "batch or undeclared or refusal or non_serial_handoff"
```

Result: `5 failed, 36 deselected in 0.47s`.

- Both screen and CAN handoff cases failed because `Bootsel.supports(...)` returned
  `True`.
- Batch selection failed with missing `flashers.select_each` and
  `flashers.select_device`.
- Refusal reporting failed because `write_all` did not accept `refused=`.

These were the expected failures before the selection seam, refusal plumbing,
and Bootsel kind narrowing existed.

### GREEN: batch selection and Bootsel handoff

Command:

```text
C:\git\github\mcu-updater\.venv\Scripts\python.exe -m pytest tests/test_flasher_select.py -q
```

Result: `41 passed in 0.44s`.

### RED: callers

Command:

```text
C:\git\github\mcu-updater\.venv\Scripts\python.exe -m pytest tests/test_flash.py tests/test_agent_bulk.py tests/test_agent_flash.py tests/test_agent_flash_can.py tests/test_cli.py -q -k "katapult_lists or cannot_write or keeps_its_own or both_serial_and_canbus"
```

Result: `5 failed, 2 passed, 243 deselected in 5.75s`.

- First install still called the removed global selector.
- Bulk and CLI still wrote with Flashtool despite the family's incompatible list.
- Serial `fw.flash` did not refuse before creating a job.
- The CAN/bulk test could not import `_board_request`.
- The pre-existing serial error-code guard passed, as expected; it guards the
  migration rather than introducing new behavior.

### GREEN: callers

The same command after implementation produced:

```text
7 passed, 243 deselected in 0.47s
```

The complete affected set from brief Step 12 also completed with exit code 0.

## Verification

Final gate, using the repository's Python 3.11 virtual environment from the
task worktree:

```text
python -m pytest -q
1985 passed, 16 skipped in 108.09s

python -m ruff check src tests scripts
All checks passed!

python -m mypy src
Success: no issues found in 78 source files

python scripts/check_line_endings.py
every working-tree file is LF.
```

The configured default `python` on this host is Python 3.14 without pytest, and
the worktree has no local `.venv`; commands therefore used
`C:\git\github\mcu-updater\.venv\Scripts\python.exe` while retaining the task
worktree as the working directory.

## Mutation sweep

Every required spec was run separately and allowed to finish:

- `batch-selection.json`: 9/9 caught.
- `bootsel-erase.json`: 17/17 caught.
- `flash-offset-diagnostic.json`: 17/17 caught.
- `flasher-supports.json`: 15/15 caught.
- `bulk-operations.json`: 13/13 caught.
- `display-flash.json`: 14/14 caught.
- `add-mcu.json`: 7/7 caught.
- `dfu-pairings.json`: 5/5 caught.
- `cli-every-type.json`: 9/9 caught.
- `single-write-path.json`: 8/8 caught.

Total: 114/114 mutations caught. The final full suite also ran the repository's
mutation-anchor hygiene test; no live mutation or dead anchor remained.

## `_cmake_flash` provenance check

`_cmake_flash` records provenance with the RPC's resolved `serial`, not
`target.id`:

```python
FlashLog(self.paths).record(serial, ...)
```

The attachment gate and target construction currently make those values equal.
Task 4 does not open a differing identity path there, so no code change was
needed.

## Files changed

- `src/mcu_updater/flashers/{registry.py,__init__.py,batch.py,bootsel.py,flash.py}`
- `src/mcu_updater/agent/methods/{_api.py,bulk.py,flash.py}`
- `src/mcu_updater/cli.py`
- `tests/test_flasher_select.py`
- `tests/test_flash.py`
- `tests/test_agent_bulk.py`
- `tests/test_agent_flash.py`
- `tests/test_agent_flash_can.py`
- `tests/test_cli.py`
- `scripts/mutations/{batch-selection.json,bootsel-erase.json,flash-offset-diagnostic.json,flasher-supports.json}`
- Deleted `scripts/mutations/flasher-selection.json`
- `docs/agent-api.md`
- `docs/decisions.md`

## Self-review

- Confirmed `rg -n "select_for|_board_target\\b" src tests scripts docs README.md
  --glob '!docs/superpowers/**'` returns no matches.
- Confirmed the mutation pre-sweep now finds only the live re-anchored
  `bootsel-erase.json` reference.
- Confirmed the only reader of `FlashTarget.needs_services_stopped` remains
  `flashers.registry.needs_services_stopped`; no caller reads it directly.
- Confirmed the Task 3 statements are now true: there is no global
  chipset/state table, and CMake boards use the same family `flashers:` list as
  every other board.
- Confirmed `git diff --check` is clean.
- Reviewed refusal ordering and empty-batch behavior: refusals precede write
  failures, an all-refused batch stops no services and performs no readiness
  wait, refused boards remain in `boards`, and refused screens are omitted from
  `displays` but included in job failures.

## Concerns

None. Hardware flashing was not performed; all write-path tests used fakes or
dry-run-safe paths as required.

## Fix round

### Fix 1: document the serial flash flasher precondition

Changed `docs/agent-api.md` to add the ordered `no_flasher` precondition row
between `device_not_found` and `print_in_progress`.

Covering test/check: `python -m pytest -q tests/test_agent_bulk.py -k
'board_its_family_cannot_write or batch_stops_klipper_once'` -> `2 passed, 39
deselected in 1.44s`.

### Fix 2: count refused boards in flash_all job parameters

Changed `flash_all` job submission to set `count` to `len(targets) + len(refused)`
and extended the refusal-bearing batch test to assert the count equals the
reported board count.

Covering test/check: `python -m pytest -q tests/test_agent_bulk.py -k
'board_its_family_cannot_write or batch_stops_klipper_once'` -> `2 passed, 39
deselected in 1.44s`.

### Fix 3: clarify the batch readiness comment

Reworded the comment in `src/mcu_updater/flashers/batch.py` to describe that
readiness is skipped only when no target was attempted, while attempted free
targets may have stopped nothing.

Covering test/check: `python -m pytest -q tests/test_agent_bulk.py -k
'board_its_family_cannot_write or batch_stops_klipper_once'` -> `2 passed, 39
deselected in 1.44s`.

### Full gate

`python -m pytest -q` -> `1985 passed, 16 skipped in 107.75s (0:01:47)`.

`python -m ruff check src tests scripts` -> `All checks passed!`.

`python -m mypy src` -> `Success: no issues found in 78 source files`.

`python scripts/check_line_endings.py` -> `every working-tree file is LF.`

No mutation spec was run because no edited source line had an anchor in
`scripts/mutations/`.
