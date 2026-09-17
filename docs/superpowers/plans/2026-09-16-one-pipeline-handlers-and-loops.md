# One Pipeline, Plan 2: Handlers and Loops — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every firmware names its flashers and helpers, device info comes from one handler per firmware, every write is selected through its family and recorded by one loop, one verdict judges every row, a Roadrunner provisions itself when tracked (or on appearance, opt-in), and flash-all/update-all cover CMake boards with the refusals that kept them out retired.

**Architecture:** `[firmware]` sections gain a required `flashers:` list and a strictly validated `helper:`. Helpers become capability objects (identify, device info, BOOTSEL request, provision) found through narrow accessors, so a caller asks "does this family's helper provide X" and never "is this a Roadrunner". Flashers gain `supports(device, helper)` and `target(...)`. `flashers.select()` walks the family's list and returns the first match, and `write_all` is the only place a write is recorded in `FlashLog`. A new pure `verdict.decide()` replaces `_device_status`, `pio.device_status` and the CMake stub. `tracking.add_serial` provisions an unprovisioned serial under the op lock, and `BusWatcher` offers `auto_provision` families the same step when a board appears.

**Tech Stack:** Python 3.11+ stdlib only, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-14-one-pipeline-design.md` (sections 3 and 5-11, plus "Order of work" steps 5-9 and "Testing"). Executors read both. Plan 1 (`docs/superpowers/plans/2026-09-14-one-pipeline-config-and-inventory.md`) is merged at `38a5f1f`.

## Global Constraints

- LF line endings in every file; `python scripts/check_line_endings.py` passes.
- Standard library only at runtime; Python 3.11 floor; every module starts with `from __future__ import annotations`.
- Mutation testing uses `scripts/mutation_test.py`, one spec per invocation, never interrupted, never a throwaway script.
- Before rewriting any line, grep `scripts/mutations/` for it; re-anchor every spec that finds it **in the same commit**.
- Work on a topic branch in its own worktree. Never `git worktree remove --force`. Never bare `git stash` / `git stash pop`.
- Conventional lowercase commit subjects. Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Docs change in the same task as the behaviour they describe. Do not read `docs/backlog.md`.
- Do not push, merge, or touch the bench printer's checkout or services. Task 11's bench checklist is handed to the user, not run.
- Wire shapes do not change except where a ruling below marked ★ says so, and every such change is additive or documented in `docs/agent-api.md` in the same task: `targets[]`, RPC names and params, `fw.status` keys, on-disk paths (including `data_dir/displays/<env>.build.json` and the FlashLog file), and CLI flags.
- Each task leaves `targets[]` and the verdicts unchanged unless the task exists to change them (spec, "Order of work"). Tasks 1-7 change no verdict; Task 8 is the verdict; Task 10 is identity.
- Never interrupt a firmware write, including in a test that drives real flasher code: tests use fakes or `dry_run`.

Gates, run from the worktree root:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
# per spec touched, one at a time:
python scripts/mutation_test.py scripts/mutations/<spec>.json
```

---

## Rulings

These are decided. The ones marked ★ change behaviour, wire or scope and the user should know about them.

1. ★ **Every `[firmware]` section needs `flashers:`** (Task 1). A missing or empty list is refused with the line to add. Printers need a hand edit for every family install.sh did not seed (cartographer, knomi_serial, roadrunner on the bench). No migration.
2. ★ **`helper:` is validated when the config loads**, like an unknown builder, and the message lists the known helpers. `_cmake_target`'s per-row catch stays as defence in depth.
3. ★ **A `builder: platformio` family must name a `helper:`** (Task 1). Screens are read and identified through that helper from Task 2 on, so a platformio family with no helper is a family nothing can read; the old implicit coupling (every platformio family *was* a knomi screen) becomes a declared one. `knomi_serial` is the only helper that fits today, but the refusal is "name a helper", not "name this helper" — `typelist.validate` checks the key is present and known, not which one it is.
4. **`firmware.FLASHERS` and `firmware.HELPERS` are static tuples**, held equal to the flasher and helper registries by tests, for the reason `firmware.BUILDERS` is: `typelist` must not import the implementations (import cycle, and the registries import hardware code).
5. **Capabilities are separate `runtime_checkable` Protocols with accessor functions** (`helpers.bootsel_requester`, `device_info_reader`, `image_reporter`, `provisioner`, `identifier`). A helper that lacks one returns `None`; a misspelt helper name raises (Ruling 2). No caller compares a helper's `name`.
6. **The Klipper device-info reader is built in**, not a helper. A family whose helper has no device-info capability (klipper, katapult, forks) reads through `device_info.KLIPPER`.
7. ★ **Retire the UART limitation.** `sensor_provenance`/`device_provenance` become one `reported_images()`; the docstrings that said a UART sensor cannot report a digest go, and so does `_image_int`'s "None is the rangeless-UART case".
8. ★ **`helper_bootsel` disappears as a flasher name.** A Roadrunner write reports `"flasher": "bootsel"` in `fw.flash_all`'s `flashed[]`/`failures[]` and in `fw.flash`'s result. `docs/agent-api.md` and `docs/cmake-provider.md` change in Task 3.
9. **`FlashTarget.needs_services_stopped: bool | None`** overrides the flasher's class attribute when set. `Bootsel` sets it per target: `True` when it will ask a helper for BOOTSEL (the request goes over a port Klipper may hold), `False` for a board already in BOOTSEL.
10. ★ **A device no flasher supports is a reported failure, not an abort.** Batches list it in `failures[]` with `"error"` naming the family and its `flashers:`; single-device RPCs raise the new `NoFlasherError` (`code: "no_flasher"`, additive).
11. **First install selects through the katapult family's `flashers:`.** `select_for` is deleted; `flash_initial_bootloader` calls `flashers.resolve` over that family. The user-facing `UnsupportedChipsetError` message stays, and is now raised when `resolve` over the katapult family finds nothing that supports the chipset — re-raised from `NoFlasherError` so the operator's message does not change.
12. ★ **`FlashLog` is written only by `write_all`**, right after a successful write and before `settled` or any later failure is raised. `flash_katapult`, `flash_katapult_can`, `esptool._record` and the agent's `_cmake_flash` stop writing records. Every single-device path goes through `write_all`, so no path loses its record — the agent `fw.flash` serial path moves onto `write_all` in **Task 4**, before Task 5 takes recording away from the flashers.
13. ★ **Provision-on-track.** `tracking.add_serial` given `RR-UNPROVISIONED-…` for a type whose family helper can provision, provisions it under `lock.exclusive` and tracks the returned serial. A held lock raises `BusyError` and is not retried. `fw.serial.add` returns `serial` (the tracked one) and adds `prior_serial` when it differs. The `roadrunner_unprovisioned` refusal stays only for types with no provisioner, where it is still correct.
14. ★ **`auto_provision:`** is a family boolean, default off, refused on a family whose helper cannot provision. It runs inline on the `BusWatcher` thread. `BusyError` means skip, and the watcher retries on its next poll even when the bus fingerprint has not changed. It never runs from `fw.status`.
15. ★ **One verdict** (`verdict.decide`). Four unifications change edge cases:
    - an empty-string running version is `UNKNOWN_VERSION` for every row (kconfig only treated `None` so);
    - the running-sha comparison is a case-insensitive prefix match over the shorter of the two (kconfig used `head.startswith(running)`);
    - a Roadrunner reporting `dev` against a built stamp is `SOURCE_CHANGED`;
    - a board reporting a running commit for a family whose tree head cannot be read falls back on the built stamp instead of answering `UNKNOWN_VERSION`.
16. ★ **New reason `unexpected_image`** (`needs_flash: true`, tone attention), for a digest that disagrees with the artifact. Additive in `docs/agent-api.md`.
17. ★ **CMake rows get real verdicts** in `fw.status` (they were a fixed `UNKNOWN_VERSION`-style stub, Task 8), and **CMake boards join `fw.flash_all`, `fw.update_all`, CLI `flash -t` and CLI `update-all`** (Task 9). `type_not_bulk_flashable`, the CLI's `-t`-only CMake refusal and `update-all`'s "No types configured." early exit go. `update-all` is build-all then flash-all.
18. ★ **§3 identity.** A PlatformIO row's `targets[].devices[].id` becomes the `device_id` for a `device_id:` section (the configured path, as today, for a `serial:` section). `fw.flash` for a screen accepts either the configured path or the `device_id`, in either the `port` or the `id` param. `knomi_serial`'s `identify` is the only identity handler.
19. **`_platformio_device_status` keeps layering** `PROTOCOL_MISMATCH`/`OFFLINE` over the screen's `reason` field; that field is on the wire and keeps its values.
20. **Registry mutation uses `paths.registry_lock_file`, not the op lock**, so provisioning under `exclusive()` and then calling `Registry.mutate` cannot deadlock.
21. **`flash_state` takes `built_version=`** (Task 8), and `bulk.py`'s two callers pass it. The verdict needs the stamp the builder recorded, and the only place that knows it is the caller holding the build install; a `flash_state` that re-read it would be the second reader of the same fact this plan exists to remove.

**Out of scope:** removing the three builder views (`Registry.load`, `pio.load`, `cmake.load`); the mixed-builder refusal; a record-backed `ARTIFACT_CHANGED` for screens; config migrations; udev-driven presence. Also out of scope, and deliberately so: spec §3's *"presence is checked against the by-id sweep"* for PlatformIO screens. Plan 1's §4 inventory join does not claim it, and `inventory.index` is keyed off declared identities the type list never sees for a screen declared in `printer.cfg` — a `[knomi_serial ...]` section is not a `[type ...]` section. A screen's `present` keeps coming from the watcher's map (Task 10) and from `_pio_target`'s existing checks. Closing that gap needs the type list to learn about printer.cfg-declared devices, which is its own design.

And out of scope: spec **§8 item 5**, *"Auto-provision (section 10) runs as a post-step where the helper has one."* Task 7 puts auto-provisioning on the `BusWatcher` thread and nowhere else (Ruling 14). In the agent that covers the flash case anyway — a board that comes back from a write reporting `RR-UNPROVISIONED-…` changes the bus fingerprint, so the very next poll picks it up, which is the mechanism §10 describes. In the CLI it is not covered, and the spec accepts that: §11 says *"The CLI has no watcher, so this is its only provisioning path."* A second trigger inside `write_all` would be the same decision made in two places, which is the shape this plan exists to remove.

**Worktree setup:** already created.

```bash
cd C:/git/github/mcu-updater/.worktrees/one-pipeline-loops
git log --oneline -1   # 38a5f1f (or the plan commit on top of it)
python -m pytest -q    # expect: all pass, the baseline
```

## File map

Derived from the eleven Files blocks below; a task number here appears in that task's Files block and nowhere else.

| File | Status | Responsibility |
| --- | --- | --- |
| `src/mcu_updater/firmware.py` | modify (T1,2,7) | `flashers`, `auto_provision` fields; `FLASHERS`, `HELPERS`, `PROVISIONING_HELPERS` |
| `src/mcu_updater/typelist.py` | modify (T1,7) | Refuse missing/unknown flashers, unknown helper, platformio without helper, auto_provision without provisioner |
| `src/mcu_updater/helpers/spec.py` | modify (T1,2,6,10) | Capability Protocols: `Helper`, `BootselRequester`, `DeviceInfoReader`, `ImageReporter`, `Provisioner`, `Identifier` |
| `src/mcu_updater/helpers/__init__.py` | modify (T1,2,6,10) | `for_name` and one accessor per capability |
| `src/mcu_updater/helpers/registry.py` | modify (T1,2) | `HELPERS`, the hand-written tuple of every helper |
| `src/mcu_updater/helpers/knomi_serial.py` | create (T1), modify (T2,10) | Knomi screen helper: BOOTSEL request (T1), device info (T2), `identify`/`remembered_at` (T10) |
| `src/mcu_updater/helpers/cartographer.py` | create (T2) | Cartographer device info (no sha) |
| `src/mcu_updater/helpers/roadrunner.py` | modify (T2,6) | Device info, image reporting, provisioning |
| `src/mcu_updater/device_info.py` | create (T2) | `DeviceInfo`, `reader_for`, the built-in Klipper reader, key/int helpers |
| `src/mcu_updater/provisioning.py` | create (T7) | The one provisioning step the watcher and `tracking` share |
| `src/mcu_updater/flashers/spec.py` | modify (T3,5) | `Device`, `KIND_*`, `chipset_matches`, `supports`, `target`, `record`, `FlashRecord`, per-target stop flag |
| `src/mcu_updater/flashers/registry.py` | modify (T3,4) | `resolve`, `select`, `select_device`, `select_each`, `refusal`, `group_by_stop`; `select_for` deleted |
| `src/mcu_updater/flashers/__init__.py` | modify (T3,4) | Re-exports for the two above |
| `src/mcu_updater/flashers/bootsel.py` | modify (T3,5) | Absorbs the helper BOOTSEL handoff; `record` |
| `src/mcu_updater/flashers/helper_bootsel.py` | delete (T3) | — |
| `src/mcu_updater/flashers/flashtool.py`, `dfu_util.py` | modify (T3,5) | `supports`, `target`, `record` |
| `src/mcu_updater/flashers/esptool.py` | modify (T3,5,10) | `supports`, `target`, `record`; `_record` deleted (T5); docstring pointer (T10) |
| `src/mcu_updater/flashers/batch.py` | modify (T4,5) | `write_all`: `refused=`, `errors`, the `on_ready` guard; the one `FlashLog` write |
| `src/mcu_updater/flashers/flash.py` | modify (T4,5) | First install selects; the two katapult paths stop recording |
| `src/mcu_updater/build.py` | modify (T5) | Docstring only: `FlashLog.record` names its one writer |
| `src/mcu_updater/errors.py` | modify (T3) | `NoFlasherError` |
| `src/mcu_updater/tracking.py` | modify (T6) | `Tracked`; provision-on-track |
| `src/mcu_updater/agent/events.py`, `agent/service.py` | modify (T7) | Watcher retry; auto-provision adapter |
| `src/mcu_updater/verdict.py` | create (T8) | `Evidence`, `Expected`, `decide` |
| `src/mcu_updater/states.py` | modify (T8) | `UNEXPECTED_IMAGE`, `_NEEDS_FLASH`, `_DEVICE_LABEL` |
| `src/mcu_updater/agent/methods/status.py` | modify (T1,2,8,9,10) | `reported_images`, verdict wiring, `_cmake_devices`, identity |
| `src/mcu_updater/agent/methods/bulk.py` | modify (T2,4,8,9) | Select per family; `built_version=`; CMake in the loops; refusal removed |
| `src/mcu_updater/agent/methods/flash.py` | modify (T1,3,4,5,10) | Single-device paths select and go through `write_all`; either identity spelling |
| `src/mcu_updater/agent/methods/_api.py`, `agent/methods/__init__.py` | modify (T2,4,9) | `_Api` Protocol stubs for the new cross-mixin calls; `_running_sha` re-export |
| `src/mcu_updater/agent/methods/registry.py` | modify (T6) | `serial_add` provisions through tracking |
| `src/mcu_updater/providers/pio.py` | modify (T2,8,10) | `is_dirty`; `device_status` retired; the identity re-export shim deleted |
| `src/mcu_updater/providers/spec.py` | modify (T9) | `Install.empty` |
| `src/mcu_updater/cli.py` | modify (T1,3,4,6,9,10) | Targets select; CMake in flash/update-all; `_pio_targets` through the identity seam |
| `mcu-updater.cfg`, `tests/fixtures/registry.cfg` | modify (T1,2) | `flashers:`/`helper:` on every family |
| `README.md`, `docs/decisions.md`, `docs/agent-api.md`, `docs/cmake-provider.md` | modify (T1-11) | Same-task docs |
| New tests | create | `test_helpers.py` (T1), `test_device_info.py` (T2), `test_flasher_select.py` (T3), `test_flashlog_loop.py` (T5), `test_provision_on_track.py` (T6), `test_auto_provision.py` (T7), `test_verdict.py` (T8) |
| New mutation specs | create | `family-keys.json` (T1), `device-info.json` (T2), `flasher-supports.json` (T3), `batch-selection.json` (T4), `flashlog-loop.json` (T5), `provision-on-track.json` (T6), `auto-provision.json` (T7), `verdict.json` (T8), `loops.json` (T9), `identity.json` (T10) |
| Mutation specs re-anchored | modify | `provenance-read.json`, `display-flash.json` (T2, again T5), `bulk-operations.json` (T3), `bootsel-erase.json`, `flash-offset-diagnostic.json` (T4), `states.json` command (T8), `inventory.json` (Plan 1 spec, run in T8 and T9), `targets.json` (T10) |
| `scripts/mutations/flasher-selection.json` | delete (T4) | `select_for` is gone; `flasher-supports.json` covers chipset and state matching |

---

### Task 1: Strict family keys — `flashers:` and `helper:`

Spec §6-7: "A missing `flashers:` key is a config error naming the key", and a misspelt helper raises. This task makes both keys load-time facts. Nothing reads `flashers:` at flash time until Task 3.

**Files:**
- Modify: `src/mcu_updater/firmware.py:1-28` (docstring), `:39-47` (`SEEDED_KEYS` comment), `:66-112` (`FirmwareFamily`), `:138-163` (`load_from_doc`)
- Modify: `src/mcu_updater/typelist.py:183-267` (`validate`)
- Modify: `src/mcu_updater/helpers/spec.py`, `src/mcu_updater/helpers/registry.py`, `src/mcu_updater/helpers/__init__.py`
- Create: `src/mcu_updater/helpers/knomi_serial.py`
- Modify: `src/mcu_updater/agent/methods/status.py:1084` (`_cmake_target`), `src/mcu_updater/agent/methods/flash.py:206` (`_cmake_flash`), `src/mcu_updater/cli.py:623` (`_cmake_targets`)
- Modify (sweep): `mcu-updater.cfg:24-60`, `tests/fixtures/registry.cfg:18-24`, every test file holding a `[firmware ...]` section literal (27 files; `rg -l '\[firmware ' tests`), `README.md` example blocks
- Create: `tests/test_helpers.py`, `scripts/mutations/family-keys.json`
- Test: `tests/test_typelist.py`
- Docs: `README.md`, `docs/decisions.md`
- Mutation specs at risk: `application-firmware.json` (anchors `typelist.validate`'s `if fw not in known:` and `missing.setdefault`), `firmware-source.json` (anchors `firmware.py`'s `bootloader=bool(parse_bool(...))` line). Grep both before editing those regions.

**Interfaces:**
- Consumes: `typelist.validate(entries, families, *, path)`, `firmware.SEEDED_KEYS`, `firmware.BUILDERS` (plan 1)
- Produces:
  - `firmware.FirmwareFamily.flashers: tuple[str, ...] = ()`
  - `firmware.FLASHERS: tuple[str, ...] = ("bootsel", "dfu_util", "esptool", "flashtool")`
  - `firmware.HELPERS: tuple[str, ...] = ("knomi_serial", "roadrunner")` (Task 2 adds `"cartographer"`)
  - `firmware.suggested_flashers(family: FirmwareFamily) -> str`
  - `helpers.Helper` (runtime-checkable Protocol with `name: str`)
  - `helpers.registry.HELPERS: tuple[Helper, ...]`
  - `helpers.for_name(name: str, *, family: str) -> Helper | None` (raises `ConfigCorruptError` naming the known helpers)
  - `helpers.bootsel_requester(helper: Helper | None) -> BootselRequester | None`
  - `helpers.knomi_serial.KnomiSerialHelper` (`name = "knomi_serial"`)
  - `typelist.validate` refuses: a family with no `flashers:`; an unknown flasher name; an unknown `helper:`; a `platformio` family with no `helper:`

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_helpers.py`:

```python
"""Helpers are capabilities, asked for by what they can do.

A caller that compared a helper's name ("is this a Roadrunner?") is the caller
branch the one-pipeline design removes. Each capability is a Protocol with an
accessor that answers None for a helper without it, so a family with no
BOOTSEL request simply has no BOOTSEL request - and a misspelt helper name is a
config error, never a silent None.
"""

from __future__ import annotations

import pytest

from mcu_updater import firmware, helpers
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper
from mcu_updater.helpers.registry import HELPERS
from mcu_updater.helpers.roadrunner import RoadrunnerHelper


def test_the_helper_names_are_exactly_the_registry():
    """`typelist` checks `helper:` against `firmware.HELPERS` without importing
    the implementations, so the two lists must not drift."""
    assert set(firmware.HELPERS) == {helper.name for helper in HELPERS}


def test_the_flasher_names_are_exactly_the_registry():
    from mcu_updater.flashers.registry import FLASHERS

    # helper_bootsel is folded into bootsel by Task 3, which drops this exclusion.
    assert set(firmware.FLASHERS) == {f.name for f in FLASHERS} - {"helper_bootsel"}


def test_no_helper_configured_is_none():
    assert helpers.for_name("", family="klipper") is None


def test_a_misspelt_helper_names_the_known_ones():
    with pytest.raises(ConfigCorruptError) as exc:
        helpers.for_name("roadruner", family="roadrunner")
    message = str(exc.value)
    assert "unknown helper 'roadruner'" in message
    assert "known: knomi_serial, roadrunner" in message


def test_each_registered_helper_resolves_by_name():
    assert isinstance(helpers.for_name("roadrunner", family="rr"), RoadrunnerHelper)
    assert isinstance(helpers.for_name("knomi_serial", family="knomi"), KnomiSerialHelper)


def test_a_helper_that_can_request_bootsel_is_offered_as_one():
    helper = helpers.for_name("roadrunner", family="rr")
    assert helpers.bootsel_requester(helper) is helper


def test_a_helper_without_the_capability_is_not():
    assert helpers.bootsel_requester(KnomiSerialHelper()) is None


def test_no_helper_has_no_capability():
    assert helpers.bootsel_requester(None) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_helpers.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'mcu_updater.helpers.knomi_serial'`.

- [ ] **Step 3: Implement the helper seam and the new family field**

Create `src/mcu_updater/helpers/knomi_serial.py`:

```python
"""KNOMI screens running knomi-serial.

Only a name for now. Its capabilities arrive with the handlers that use them:
device info in Task 2 of the one-pipeline plan, identity in Task 10. It is
registered first so a `platformio` family can declare it and have that
declaration checked when the config loads.
"""

from __future__ import annotations


class KnomiSerialHelper:
    """The helper a `builder: platformio` family names for KNOMI screens."""

    name: str = "knomi_serial"


__all__ = ["KnomiSerialHelper"]
```

In `src/mcu_updater/helpers/spec.py`, change the typing import to
`from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable`, and add above `BootselHandoff`:

```python
@runtime_checkable
class Helper(Protocol):
    """A registered firmware helper.

    What a helper can do is the capability Protocols below, each asked for
    through its accessor in `helpers` (`helpers.bootsel_requester(helper)`).
    Nothing compares a helper's `name` to decide what to do with it.
    """

    name: str
```

and decorate `class BootselRequester(Protocol):` with `@runtime_checkable`.

Replace `src/mcu_updater/helpers/registry.py` below the module docstring with:

```python
from __future__ import annotations

from ..errors import ConfigCorruptError
from .knomi_serial import KnomiSerialHelper
from .roadrunner import RoadrunnerHelper
from .spec import Helper

#: Every firmware-specific helper. Add an implementation, one explicit entry
#: here, and its name in `firmware.HELPERS`; configuration never controls which
#: Python module gets imported.
HELPERS: tuple[Helper, ...] = (KnomiSerialHelper(), RoadrunnerHelper())

_BY_NAME: dict[str, Helper] = {helper.name: helper for helper in HELPERS}


def for_name(name: str, *, family: str) -> Helper | None:
    """Resolve one configured helper, refusing misspellings before a write."""
    if not name:
        return None
    helper = _BY_NAME.get(name)
    if helper is None:
        raise ConfigCorruptError(
            f"firmware family '{family}' configures unknown helper '{name}' "
            f"(known: {', '.join(sorted(_BY_NAME))})",
            family=family,
            value=name,
        )
    return helper
```

Replace `src/mcu_updater/helpers/__init__.py` below its docstring with:

```python
from __future__ import annotations

from .spec import BootselHandoff, BootselRequester, Helper


def for_name(name: str, *, family: str) -> Helper | None:
    """Resolve a helper without importing implementations during package init."""
    from .registry import for_name as resolve

    return resolve(name, family=family)


def bootsel_requester(helper: Helper | None) -> BootselRequester | None:
    """`helper`'s BOOTSEL request capability, or None when it has none."""
    return helper if isinstance(helper, BootselRequester) else None


__all__ = ["BootselHandoff", "BootselRequester", "Helper", "bootsel_requester", "for_name"]
```

In `src/mcu_updater/firmware.py`:

1. After `BUILDERS`, add:

```python
#: Every name a `flashers:` list may use - `flashers.registry`'s names, spelled
#: out here because that package imports hardware code this module must not. A
#: test holds the two equal.
FLASHERS: tuple[str, ...] = ("bootsel", "dfu_util", "esptool", "flashtool")

#: Every `helper:` value a registered helper answers to - `helpers.registry`'s
#: names, for the same reason. A test holds the two equal.
HELPERS: tuple[str, ...] = ("knomi_serial", "roadrunner")

#: The `flashers:` line a refusal suggests, by what builds the family. A
#: suggestion for a message only: selection reads the family's own list.
_SUGGESTED_FLASHERS: dict[str, str] = {
    "cmake": "bootsel",
    "kconfig_make": "flashtool",
    "platformio": "esptool",
}
```

2. In `FirmwareFamily`, after `helper`, add:

```python
    #: The flashers that may write this family, in the order they are tried.
    #: Required: `typelist.validate` refuses a section without one. The first
    #: whose `supports()` accepts a device writes it.
    flashers: tuple[str, ...] = ()
```

3. In `load_from_doc`, after the `helper=` argument, add this line, indented to match the arguments around it:

   ```python
   flashers=tuple(doc.get_csv(section, "flashers") or ()),
   ```

4. After `names_of`, add:

```python
def suggested_flashers(family: FirmwareFamily) -> str:
    """The `flashers:` value to suggest for a family that has none.

    What install.sh seeds for the two sections it writes, otherwise what a
    family of this kind is normally written with.
    """
    seeded = dict(SEEDED_KEYS.get(family.name, ())).get("flashers")
    if seeded:
        return seeded
    if family.bootloader:
        return "dfu_util, bootsel"
    return _SUGGESTED_FLASHERS.get(family.builder, "flashtool")
```

Wrap the three BOOTSEL call sites so each keeps the type it had:

- `src/mcu_updater/agent/methods/status.py:1084`:
  `helper = helpers.bootsel_requester(helpers.for_name(family.helper, family=family.name))`
- `src/mcu_updater/agent/methods/flash.py:206`: the same expression.
- `src/mcu_updater/cli.py:623`: the same expression.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_helpers.py tests/test_roadrunner.py tests/test_cmake.py tests/test_agent_flash.py -q`
Expected: PASS.

Run the gates (`pytest`, `ruff`, `mypy`, `check_line_endings`). Expected: all pass. A `_cmake_target` row for a family whose helper cannot request BOOTSEL now reports `flashable: false`, the same as no helper.

- [ ] **Step 5: Commit**

```bash
git add src/mcu_updater/helpers src/mcu_updater/firmware.py src/mcu_updater/agent/methods/status.py src/mcu_updater/agent/methods/flash.py src/mcu_updater/cli.py tests/test_helpers.py
git commit -m "feat(helpers): helpers are capabilities, and families parse flashers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Sweep — every declared family names its flashers**

Nothing refuses a missing `flashers:` yet, so this commit changes no behaviour. It exists so the refusal in Step 8 lands on a tree that already satisfies it, and so a reviewer can judge the sweep apart from the refusal.

Procedure, for `mcu-updater.cfg`, `tests/fixtures/registry.cfg`, `README.md` (every fenced `ini` block), and every file from `rg -l "\[firmware " tests`:

1. For each `[firmware <name>]` section (in a `.cfg`, an `ini` block, or a Python string literal such as `"[firmware knomi_serial]\nsource: ~/knomi_serial\nbuilder: platformio\n\n"`) that has no `flashers:` line, add one as the last key of the section:
   - name `klipper` → `flashers: flashtool`; name `katapult` → `flashers: dfu_util, bootsel`
   - otherwise `bootloader: yes` → `flashers: dfu_util, bootsel`
   - otherwise `builder: cmake` → `flashers: bootsel`; `builder: platformio` → `flashers: esptool`; no builder or `kconfig_make` → `flashers: flashtool`
2. For each `builder: platformio` section with no `helper:` line, add `helper: knomi_serial`.
3. In `mcu-updater.cfg`, also add `helper: roadrunner` to `[firmware roadrunner]` (README's own example already has it), and change the comment at lines 26-27 from "`flashers:` is the order flashers are tried in (not read yet)." to "`flashers:` is required: the flashers that may write the family, in the order they are tried."
4. Leave `tests/conftest.py`'s `BASE_FIRMWARES` alone (it already has both lines), and leave sections a test deliberately writes malformed (search the test for `ConfigCorruptError` or `refus` before editing a literal inside it).
5. Families built key-by-key through a `CfgDocument` have no `[firmware ...]` literal to find, so sweep them separately:

   ```bash
   rg -n 'doc\.set\("firmware' tests
   ```

   Three of these need a key, and they are the only three:
   - `tests/test_agent_displays.py:643-644` — a `builder: platformio` family: add both `flashers: esptool` and `helper: knomi_serial`
   - `tests/test_agent_targets.py:732-733` and `:905-906` — two `builder: cmake` families: add `flashers: bootsel`

   Add them as further `doc.set("firmware <name>", "<key>", "<value>")` calls beside the existing ones.

Check the sweep missed nothing Python can see. Write this checker to your scratch directory and run it; it prints each file and family whose section still has no `flashers:`:

```python
# scratch/check_flashers.py
import pathlib
import re

pat = re.compile(r"\[firmware ([^\]]+)\]((?:(?!\n\s*\n|\[).)*)", re.S)
roots = [
    *pathlib.Path("tests").rglob("*.py"),
    pathlib.Path("tests/fixtures/registry.cfg"),
    pathlib.Path("mcu-updater.cfg"),
    pathlib.Path("README.md"),
]
for path in roots:
    text = path.read_text(encoding="utf-8").replace("\\n", "\n")
    for name, body in pat.findall(text):
        if "flashers:" not in body:
            print(path, name)
```

Expected: no output, or only sections inside tests that assert a refusal. (The regex is a checker, not an editor: edit by hand.)

The checker is blind to the three `CfgDocument` families by construction — they never spell a `[firmware ...]` header anywhere — which is why bullet 5 is a separate `rg` and not a refinement of this regex. A clean checker run is not evidence bullet 5 was done.

If adding `helper: roadrunner` to the example config changes a test's expected `extra.flashable` for the example `roadrunner` type from `false` to `true`, update that assertion: it described the example's missing helper, not a rule.

Run the gates. Expected: all pass.

```bash
git add mcu-updater.cfg tests README.md
git commit -m "test: every firmware fixture names its flashers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Write the failing refusal tests**

Append to `tests/test_typelist.py`:

```python
def _family_refusal(paths, text: str) -> ConfigCorruptError:
    write_main_config(paths, text)
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    return exc.value


def test_every_family_without_flashers_is_refused_with_the_line_to_add(paths):
    """Spec §7: a missing `flashers:` key is a config error naming the key."""
    error = _family_refusal(
        paths,
        "[firmware klipper]\nsource: ~/klipper\n\n"
        "[firmware katapult]\nsource: ~/katapult\n\n"
        "[firmware fork]\nsource: ~/fork\n\n"
        "[firmware boot]\nsource: ~/boot\nbootloader: yes\n\n"
        "[firmware rr]\nsource: ~/rr\nbuilder: cmake\nflashers:\n\n"
        "[firmware screen]\nsource: ~/s\nbuilder: platformio\nhelper: knomi_serial\n\n"
        "[type board]\nchipset: stm32f4\nfirmware: klipper, katapult\n",
    )
    message = str(error)
    assert "no flashers: list" in message
    assert "[firmware klipper] add: flashers: flashtool" in message
    assert "[firmware katapult] add: flashers: dfu_util, bootsel" in message
    assert "[firmware fork] add: flashers: flashtool" in message
    assert "[firmware boot] add: flashers: dfu_util, bootsel" in message
    # A bare `flashers:` is explicitly nothing, which cannot write anything.
    assert "[firmware rr] add: flashers: bootsel" in message
    assert "[firmware screen] add: flashers: esptool" in message
    assert error.data["key"] == "flashers"
    assert error.data["families"] == ["klipper", "katapult", "fork", "boot", "rr", "screen"]


def test_an_unknown_flasher_is_refused(paths):
    error = _family_refusal(
        paths,
        "[firmware klipper]\nsource: ~/klipper\nflashers: flashtoool, esptool\n\n"
        "[type board]\nchipset: stm32f4\nfirmware: klipper\n",
    )
    message = str(error)
    assert "[firmware klipper] flashers: flashtoool" in message
    assert "known: bootsel, dfu_util, esptool, flashtool" in message
    assert error.data["flashers"] == {"klipper": ["flashtoool"]}


def test_an_unknown_helper_is_refused_when_the_config_loads(paths):
    """It used to surface only on a CMake row's actions, and never for a
    family whose types were not CMake."""
    error = _family_refusal(
        paths,
        "[firmware rr]\nsource: ~/rr\nbuilder: cmake\nflashers: bootsel\nhelper: roadruner\n\n"
        "[type board]\nchipset: rp2040\nfirmware: rr\ncmake_target: x\n",
    )
    message = str(error)
    assert "[firmware rr] helper: roadruner" in message
    assert "known: knomi_serial, roadrunner" in message
    assert error.data["helpers"] == {"rr": "roadruner"}


def test_a_platformio_family_must_name_its_helper(paths):
    error = _family_refusal(
        paths,
        "[firmware screen]\nsource: ~/s\nbuilder: platformio\nflashers: esptool\n\n"
        "[type knomi]\nchipset: esp32\nfirmware: screen\nplatformio_env: e\n",
    )
    assert "[firmware screen] add: helper: knomi_serial" in str(error)
    assert error.data["families"] == ["screen"]


def test_a_family_with_both_keys_loads(paths):
    write_main_config(
        paths,
        "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n\n"
        "[firmware screen]\nsource: ~/s\nbuilder: platformio\nflashers: esptool\nhelper: knomi_serial\n\n"
        "[type board]\nchipset: stm32f4\nfirmware: klipper\n",
    )
    assert [entry.name for entry in typelist.load(paths)] == ["board"]


def test_flashers_keep_their_declared_order(paths):
    from mcu_updater import firmware

    write_main_config(paths, "[firmware katapult]\nsource: ~/k\nflashers: bootsel dfu_util\n")
    assert firmware.load(paths)["katapult"].flashers == ("bootsel", "dfu_util")
```

- [ ] **Step 8: Run them to verify they fail**

Run: `python -m pytest tests/test_typelist.py -q -k "flashers or helper"`
Expected: the four refusal tests FAIL with `DID NOT RAISE`. `test_a_family_with_both_keys_loads` and `test_flashers_keep_their_declared_order` pass.

- [ ] **Step 9: Implement the refusals**

In `src/mcu_updater/typelist.py`, call the new check at the end of the unknown-builder block in `validate`, before `known = firmware.names_of(families)`:

```python
    _refuse_family_keys(families, path=path)
```

and add above `validate`:

```python
def _refuse_family_keys(families: dict[str, firmware.FirmwareFamily], *, path: str) -> None:
    """Refuse a family whose flashers or helper nothing implements.

    Every family, used by a type or not, and every offender of a kind at once -
    the rule the builder check follows. A family with no flashers cannot be
    written by anything, and a misspelt helper used to surface only on a CMake
    row's actions.
    """
    known_flashers = ", ".join(firmware.FLASHERS)
    no_flashers = [family for family in families.values() if not family.flashers]
    if no_flashers:
        listed = "\n".join(
            f"  [firmware {family.name}] add: flashers: {firmware.suggested_flashers(family)}"
            for family in no_flashers
        )
        raise ConfigCorruptError(
            f"{path}: a firmware family with no flashers: list. It names the "
            f"flashers that may write the family, in the order they are tried "
            f"(known: {known_flashers}):\n{listed}",
            path=path,
            family=no_flashers[0].name,
            key="flashers",
            families=[family.name for family in no_flashers],
        )
    unknown_flashers = {
        family.name: [name for name in family.flashers if name not in firmware.FLASHERS]
        for family in families.values()
    }
    unknown_flashers = {name: bad for name, bad in unknown_flashers.items() if bad}
    if unknown_flashers:
        first = next(iter(unknown_flashers))
        listed = "\n".join(
            f"  [firmware {name}] flashers: {', '.join(bad)}"
            for name, bad in unknown_flashers.items()
        )
        raise ConfigCorruptError(
            f"{path}: a flasher that does not exist (known: {known_flashers}):\n"
            f"{listed}\nFix the spelling.",
            path=path,
            family=first,
            value=unknown_flashers[first][0],
            flashers=unknown_flashers,
        )
    unknown_helpers = {
        family.name: family.helper
        for family in families.values()
        if family.helper and family.helper not in firmware.HELPERS
    }
    if unknown_helpers:
        first = next(iter(unknown_helpers))
        listed = "\n".join(
            f"  [firmware {name}] helper: {helper}" for name, helper in unknown_helpers.items()
        )
        raise ConfigCorruptError(
            f"{path}: a helper no registered helper answers to (known: "
            f"{', '.join(firmware.HELPERS)}):\n{listed}\nFix the spelling, or "
            f"remove the helper: line.",
            path=path,
            family=first,
            value=unknown_helpers[first],
            helpers=unknown_helpers,
        )
    no_helper = [
        family.name
        for family in families.values()
        if family.builder == "platformio" and not family.helper
    ]
    if no_helper:
        listed = "\n".join(f"  [firmware {name}] add: helper: knomi_serial" for name in no_helper)
        raise ConfigCorruptError(
            f"{path}: a platformio family names no helper:. Its screens are "
            f"identified and read through one:\n{listed}",
            path=path,
            family=no_helper[0],
            key="helper",
            families=no_helper,
        )
```

- [ ] **Step 10: Run the tests**

Run: `python -m pytest tests/test_typelist.py tests/test_helpers.py tests/test_firmware.py -q`
Expected: PASS.

Run: `python -m pytest -q`
Expected: all pass. A failure whose message contains `no flashers: list` or `names no helper:` is a fixture Step 6 missed: add the line there, in this commit.

- [ ] **Step 11: Mutation spec**

Create `scripts/mutations/family-keys.json`:

```json
{
  "_comment": "A family names the flashers that may write it and the helper that reads it, and both are checked when the config loads. A family with no flashers is a board nothing can write, discovered at flash time; a misspelt helper silently took away a Roadrunner's BOOTSEL request; a screen family with no helper has nothing to identify its screens.",
  "file": "src/mcu_updater/typelist.py",
  "command": ["python", "-m", "pytest", "tests/test_typelist.py", "tests/test_helpers.py", "tests/test_firmware.py", "-q"],
  "mutations": [
    {
      "name": "a family with no flashers is refused",
      "find": "    no_flashers = [family for family in families.values() if not family.flashers]",
      "replace": "    no_flashers: list[firmware.FirmwareFamily] = []"
    },
    {
      "name": "an unknown flasher name is refused",
      "find": "        family.name: [name for name in family.flashers if name not in firmware.FLASHERS]",
      "replace": "        family.name: [name for name in family.flashers if False]"
    },
    {
      "name": "an unknown helper is refused at load",
      "find": "        if family.helper and family.helper not in firmware.HELPERS",
      "replace": "        if False"
    },
    {
      "name": "a platformio family must name a helper",
      "find": "        if family.builder == \"platformio\" and not family.helper",
      "replace": "        if False"
    },
    {
      "name": "the flashers list is read from the section",
      "file": "src/mcu_updater/firmware.py",
      "find": "            flashers=tuple(doc.get_csv(section, \"flashers\") or ()),",
      "replace": "            flashers=(\"flashtool\",),"
    },
    {
      "name": "a bootloader family is suggested its ROM flashers",
      "file": "src/mcu_updater/firmware.py",
      "find": "    if family.bootloader:\n        return \"dfu_util, bootsel\"",
      "replace": "    if False:\n        return \"dfu_util, bootsel\""
    },
    {
      "name": "a helper without a capability does not get offered as one",
      "file": "src/mcu_updater/helpers/__init__.py",
      "find": "    return helper if isinstance(helper, BootselRequester) else None",
      "replace": "    return helper  # type: ignore[return-value]"
    }
  ]
}
```

Run: `python scripts/mutation_test.py scripts/mutations/family-keys.json`
Expected: every mutant killed.

Run: `python scripts/mutation_test.py scripts/mutations/application-firmware.json`, then `python scripts/mutation_test.py scripts/mutations/firmware-source.json`
Expected: every mutant killed (anchors unchanged; re-anchor here if a find string moved).

- [ ] **Step 12: Docs**

- `src/mcu_updater/firmware.py` module docstring: replace the paragraph starting "`flashers:` is written for klipper and katapult by install.sh but not read yet" with: "`flashers:` is required on every section, and a `builder: platformio` section also names its `helper:`. `typelist.validate` refuses either missing, and a misspelt flasher or helper, with the line to fix."
- `SEEDED_KEYS` comment: replace "Nothing reads `flashers:` yet - the flash loop does, in the next plan. Kept here so" with "`flashers:` is required on every section. Kept here so".
- `README.md`, in the per-key list near the `helper` bullet (line ~301): add this bullet

  ````markdown
  - **`flashers`** - required on every `[firmware ...]`. The flashers that may write
    this family, tried in order: `flashtool`, `esptool`, `dfu_util`, `bootsel`. A
    section without one refuses the config with the line to add.
  ````

  and extend the `helper` bullet: "A misspelt helper refuses the config when it loads. A `builder: platformio` family must name `helper: knomi_serial`."
- `docs/decisions.md`: add after "### Presence comes from the inventory":

```markdown
### A family declares its flashers and its helper

`flashers:` is required on every `[firmware ...]` section, and `helper:` is
checked against the registered helpers when the config loads, like `builder:`.
Neither is inferred. An inferred flasher list is the chipset-and-state guess
the one-pipeline design removes, and a helper name that only fails at flash
time is a Roadrunner that silently loses its BOOTSEL request. The known names
are static tuples in `firmware.py` held equal to the registries by tests, so
`typelist` never imports hardware code. A printer upgrading past this needs a
hand edit, and the refusal says which line.
```

- [ ] **Step 13: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add src/mcu_updater/typelist.py src/mcu_updater/firmware.py tests scripts/mutations/family-keys.json README.md docs/decisions.md
git commit -m "feat(config): refuse a family with no flashers or an unknown helper

Every [firmware] section names the flashers that may write it, and helper: is
checked when the config loads. Printers need a hand edit for any family
install.sh did not seed; the refusal names the line.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

---

### Task 2: Device-info handlers

Spec §5-6: every firmware answers "what is this board running?" through a handler its family's helper supplies, and the Klipper reader is the built-in default. This task moves the reading. It changes no verdict: the Klipper reader extracts exactly what `status._running_sha` did, and Cartographer already had no sha to extract. `reported_images` has no production caller until Task 8, the same as `device_provenance` today.

**Files:**
- Create: `src/mcu_updater/device_info.py`, `src/mcu_updater/helpers/cartographer.py`
- Modify: `src/mcu_updater/helpers/spec.py`, `src/mcu_updater/helpers/__init__.py`, `src/mcu_updater/helpers/registry.py`, `src/mcu_updater/helpers/knomi_serial.py`, `src/mcu_updater/helpers/roadrunner.py`
- Modify: `src/mcu_updater/firmware.py` (`HELPERS`)
- Modify: `src/mcu_updater/providers/pio.py:90-100` (`klipper_section` comment), `:139-203` (`load`), `:284-320` (`running_sha`, new `is_dirty`, `device_status`)
- Modify: `src/mcu_updater/agent/methods/status.py:53` (`SENSOR_SECTION`), `:61-104` (`_provenance_key`, `_digest_int`, `_image_int`), `:114-134` (`_FW_SHA_RE`, `_running_sha`; keep `MCU_NAMES_TTL`), `:365-420` (`type_status`), `:560-600` (`pio_status`), `:1299-1322` (`_platformio_confidence`), `:2301-2408` (`sensor_provenance`, `device_provenance`), `:2558-2610` (`flash_state`)
- Modify: `src/mcu_updater/agent/methods/_api.py:66-75` (`flash_state` stub), `src/mcu_updater/agent/methods/__init__.py:27` (`_running_sha` re-export), `src/mcu_updater/agent/methods/bulk.py:140-160`, `:225-245`
- Modify: `mcu-updater.cfg` and `tests/fixtures/registry.cfg` (`helper: cartographer` on the cartographer family)
- Create: `tests/test_device_info.py`, `scripts/mutations/device-info.json`
- Modify: `tests/test_provenance.py` (rewritten onto `reported_images`), `tests/test_agent_methods.py:1485-1500` (the `_running_sha` test moves), `tests/test_helpers.py`, `tests/test_typelist.py` (the known-helper list gains `cartographer`)
- Modify: `scripts/mutations/provenance-read.json`, `scripts/mutations/display-flash.json` (re-anchored in this task's commit)
- Docs: `docs/decisions.md`
- Mutation specs at risk: `provenance-read.json` (every mutation anchors `status.py` code this task moves), `display-flash.json` (the `record = flashlog.entry_for(` anchor in `_platformio_confidence`), `pio.json`/`pio-provider-selection.json`/`type-keys.json` (anchor `pio.load`; grep before editing it), `states.json` (anchors `pio.device_status`), `cartographer-version.json` (grep `status.py` anchors before touching `flash_state`). Run `rg -n "_running_sha|_FW_DIRTY_RE|running_sha\(|_provenance_key|_digest_int|_image_int|SENSOR_SECTION|klipper_section" scripts/mutations` first and re-anchor every hit in this task's commit.

**Interfaces:**
- Consumes: `helpers.Helper`, `helpers.for_name(name, *, family)`, `helpers.bootsel_requester`, `FirmwareFamily.helper`, `firmware.HELPERS` (Task 1)
- Produces:
  - `device_info.DeviceInfo(source: str, version: str | None, digest_algorithm: int | None = None, digest: int | None = None, image_start: int | None = None, image_length: int | None = None)`, frozen, with `.has_digest() -> bool`
  - `device_info.SOURCE_KLIPPER = "klipper"`, `device_info.SOURCE_INFO = "info"`
  - `device_info.serial_key(serial: str) -> str`, `device_info.digest_int(value: object) -> int | None`, `device_info.image_int(value: object) -> int | None`
  - `device_info.KlipperReader`, `device_info.KLIPPER: KlipperReader` (`name = "klipper"`, `klipper_prefix = "mcu"`)
  - `device_info.reader_for(family: FirmwareFamily | None) -> DeviceInfoReader`
  - `helpers.DeviceInfoReader` (runtime-checkable Protocol): `name: str`, `klipper_prefix: str`, `running_sha(version: str | None) -> str | None`, `is_dirty(version: str | None) -> bool`
  - `helpers.ImageReporter` (runtime-checkable Protocol): `name: str`, `klipper_prefix: str`, `klipper_fields: tuple[str, ...]`, `from_klipper(values: Mapping[str, Any]) -> tuple[str, DeviceInfo] | None`, `wire_source(paths: Paths) -> Callable[[str], DeviceInfo | None]`
  - `helpers.device_info_reader(helper: Helper | None) -> DeviceInfoReader | None`, `helpers.image_reporter(helper: Helper | None) -> ImageReporter | None`
  - `helpers.cartographer.CartographerHelper` (`name = "cartographer"`); `firmware.HELPERS = ("cartographer", "knomi_serial", "roadrunner")`
  - `KnomiSerialHelper` and `RoadrunnerHelper` implement `DeviceInfoReader`; `RoadrunnerHelper` also implements `ImageReporter`
  - `StatusMixin.reported_images(reporter: ImageReporter, serials: Iterable[str], *, wire: Callable[[str], DeviceInfo | None] | None = None) -> dict[str, DeviceInfo]`
  - `StatusMixin.flash_state(..., built_version=None, reader: DeviceInfoReader = device_info.KLIPPER)`
  - `pio.is_dirty(running: str | None) -> bool`
  - Removed: `status.sensor_provenance`, `status.device_provenance`, `status._running_sha`, `status._provenance_key`, `status._digest_int`, `status._image_int`, `status.SENSOR_SECTION`

- [ ] **Step 1: Write the failing device-info tests**

Create `tests/test_device_info.py`:

```python
"""What a board is running, read through the handler its firmware names.

Before this, `status.py` owned one sha regex for boards, `pio.py` another for
screens, and a Roadrunner's `git describe` went through whichever the caller
happened to use. Each firmware now answers through its family's helper, and a
family with no reader goes through the built-in Klipper one - so the question
"which regex applies here?" has one place to be answered.
"""

from __future__ import annotations

import pytest

from mcu_updater import device_info, firmware, helpers
from mcu_updater.agent.methods import Api
from mcu_updater.config import Registry
from mcu_updater.device_info import KLIPPER, DeviceInfo
from mcu_updater.helpers.cartographer import CartographerHelper
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper
from mcu_updater.helpers.roadrunner import RoadrunnerHelper
from mcu_updater.providers import pio

from .conftest import make_device

HEAD = "d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65"
OLD_VERSION = "v0.13.0-623-gaea1bcf5"


class _StubReader:
    """A reader that claims every board runs HEAD.

    Wiring tests use it because its answer differs from the Klipper reader's
    on OLD_VERSION: a caller that dropped the family's reader and fell back to
    KLIPPER turns "current" into "source_changed", which a test can see.
    """

    name = "stub"
    klipper_prefix = "stub_screen"

    def running_sha(self, version):
        return HEAD[:8]

    def is_dirty(self, version):
        return False


# -- DeviceInfo ------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"digest_algorithm": 1, "digest": 5, "image_start": 0, "image_length": 600}, True),
        # DIGEST_NONE is 0: a board that says "no algorithm" reported no digest.
        ({"digest_algorithm": 0, "digest": 5, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": None, "digest": 5, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": None, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": 5, "image_start": None, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": 5, "image_start": 0, "image_length": None}, False),
    ],
)
def test_a_digest_is_only_comparable_with_its_algorithm_and_range(fields, expected):
    """A digest with no range cannot be checked against a file, so it is not one.

    `image_start` of 0 is a real start, which is why the check is `is not None`.
    """
    assert DeviceInfo(source="klipper", version="v1", **fields).has_digest() is expected


# -- the built-in Klipper reader -------------------------------------------


@pytest.mark.parametrize(
    ("version", "sha"),
    [
        ("v0.13.0-711-gd7cea5bb", "d7cea5bb"),
        # A makefile-patched build is always -dirty, so that must not defeat the
        # match or those types would report needing a flash forever.
        ("v0.13.0-712-g6d43f8b3-dirty", "6d43f8b3"),
        ("v0.12.0", None),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_the_klipper_reader_extracts_the_commit_from_a_git_describe(version, sha):
    assert KLIPPER.running_sha(version) == sha


def test_the_klipper_reader_never_calls_a_board_dirty():
    """`-dirty` is normal for a makefile-patched Klipper; it is not evidence."""
    assert KLIPPER.is_dirty("v0.13.0-712-g6d43f8b3-dirty") is False


# -- helper readers --------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "sha"),
    [
        ("v1.2.0-3-gdeadbee", "deadbee"),
        ("v1.2.0-3-gdeadbee-dirty", "deadbee"),
        # `git describe --always` in a repo with no tags is the bare sha.
        ("deadbee", "deadbee"),
        ("deadbee-dirty", "deadbee"),
        # Sitting exactly on a tag: no commit in the string.
        ("v1.2.0", None),
        # A development build that stamped no describe at all.
        ("dev", None),
        ("", None),
        (None, None),
    ],
)
def test_the_roadrunner_reader_reads_every_describe_form(version, sha):
    assert RoadrunnerHelper().running_sha(version) == sha


@pytest.mark.parametrize(
    ("version", "dirty"),
    [
        ("v1.2.0-3-gdeadbee-dirty", True),
        ("deadbee-dirty", True),
        ("v1.2.0-3-gdeadbee", False),
        ("dev", False),
        (None, False),
    ],
)
def test_the_roadrunner_reader_knows_a_dirty_build(version, dirty):
    assert RoadrunnerHelper().is_dirty(version) is dirty


def test_the_knomi_reader_is_the_platformio_one():
    """Two callers read a screen's version; they must not disagree."""
    reader = KnomiSerialHelper()
    assert reader.running_sha("0.4.0+3.gd34db33") == pio.running_sha("0.4.0+3.gd34db33")
    assert reader.running_sha("0.4.0+3.gd34db33") == "d34db33"
    assert reader.is_dirty("0.4.0+3.gd34db33.dirty") is True
    assert reader.is_dirty("0.4.0+3.gd34db33") is False


def test_the_cartographer_reader_never_invents_a_sha():
    """docs/decisions.md: "Do not synthesize a sha into Cartographer's
    `CONFIG_VERSION`". Even a string that looks like a describe is not one."""
    reader = CartographerHelper()
    assert reader.running_sha("v0.13.0-711-gd7cea5bb") is None
    assert reader.running_sha("CARTOGRAPHER 6.2.0") is None
    assert reader.klipper_prefix == "mcu"


# -- choosing the reader ---------------------------------------------------


def test_no_family_reads_through_klipper():
    assert device_info.reader_for(None) is KLIPPER


def test_a_family_with_no_helper_reads_through_klipper():
    assert device_info.reader_for(firmware.FirmwareFamily(name="klipper")) is KLIPPER


@pytest.mark.parametrize(
    ("helper", "kind"),
    [
        ("knomi_serial", KnomiSerialHelper),
        ("roadrunner", RoadrunnerHelper),
        ("cartographer", CartographerHelper),
    ],
)
def test_a_family_reads_through_its_helper(helper, kind):
    family = firmware.FirmwareFamily(name="x", helper=helper)
    assert isinstance(device_info.reader_for(family), kind)


def test_a_helper_without_a_reader_reads_through_klipper(monkeypatch):
    """A firmware without a capability is an ordinary firmware (spec §6)."""

    class _NameOnly:
        name = "name_only"

    monkeypatch.setattr(helpers, "for_name", lambda name, *, family: _NameOnly())
    family = firmware.FirmwareFamily(name="x", helper="name_only")

    assert device_info.reader_for(family) is KLIPPER


def test_the_capability_accessors_answer_none_for_what_a_helper_lacks():
    assert helpers.device_info_reader(None) is None
    assert helpers.image_reporter(None) is None
    assert helpers.image_reporter(KnomiSerialHelper()) is None
    assert helpers.image_reporter(CartographerHelper()) is None
    roadrunner = RoadrunnerHelper()
    assert helpers.device_info_reader(roadrunner) is roadrunner
    assert helpers.image_reporter(roadrunner) is roadrunner


# -- callers use the family's reader ---------------------------------------


def test_a_screen_types_klipper_section_comes_from_its_reader(paths, monkeypatch):
    """The prefix was a hardcoded default on `PioType`; it is the helper's now."""
    from .conftest import with_base_firmwares, write_main_config

    write_main_config(
        paths,
        with_base_firmwares(
            "[firmware knomi]\nsource: ~/knomi\nbuilder: platformio\nflashers: esptool\n"
            "helper: knomi_serial\n\n"
            "[type screen]\nfirmware: knomi\nplatformio_env: knomi\n"
        ),
    )
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())

    assert pio.load(paths)["screen"].klipper_section == "stub_screen"


def test_flash_state_extracts_the_sha_with_the_reader_it_is_given(paths):
    api = Api(paths)
    info = {"A": {"version": OLD_VERSION, "mcu": "mcu"}}

    default = api.flash_state("A", info, HEAD, state="klipper")
    stubbed = api.flash_state("A", info, HEAD, state="klipper", reader=_StubReader())

    assert default["reason"] == "source_changed"
    assert stubbed["running_sha"] == HEAD[:8]
    assert stubbed["needs_flash"] is False


def test_type_status_reads_each_board_through_its_familys_reader(
    paths, live_registry_text, fake_root, monkeypatch
):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "123456789012345678901")
    from mcu_updater import build as build_mod

    monkeypatch.setattr(build_mod, "git_head", lambda _d, **_kw: HEAD)
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())
    api = Api(paths)
    versions = {"123456789012345678901": {"version": OLD_VERSION, "mcu": "mcu EBBT0"}}

    ebb = api.type_status(api.registry(), "bttebb36", versions)

    by_serial = {s["serial"]: s for s in ebb["serials"]}
    assert by_serial["123456789012345678901"]["needs_flash"] is False


def test_a_bulk_flash_selects_through_each_familys_reader(
    paths, live_registry_text, fake_root, monkeypatch
):
    from .test_agent_bulk import _moonraker, _stage_artifact, monkey_head

    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths, "bttebb36")
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "123456789012345678901")
    api = Api(paths, call=_moonraker({"123456789012345678901": OLD_VERSION}))
    monkey_head(api, paths)
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())

    assert api._boards_to_flash(Registry.load(paths), "stale") == []
```

Delete `test_the_commit_is_extracted_from_a_git_describe` and its `parametrize` from `tests/test_agent_methods.py:1485-1500`; the Klipper-reader test above replaces it.

In `tests/test_helpers.py` and in `test_an_unknown_helper_is_refused_when_the_config_loads` in `tests/test_typelist.py`, change `"known: knomi_serial, roadrunner"` to `"known: cartographer, knomi_serial, roadrunner"`.

- [ ] **Step 2: Rewrite the provenance tests onto `reported_images`**

Replace `tests/test_provenance.py` from `@pytest.fixture\ndef api(paths):` down to `# -- the wire half` with the block below. Keep the module docstring, the imports (add `from mcu_updater.device_info import SOURCE_INFO, SOURCE_KLIPPER, DeviceInfo` and `from mcu_updater.helpers.roadrunner import RoadrunnerHelper`), `SERIAL`, `OBJECT` and `_sensor`. Keep the three `roadrunner.provenance` tests and `test_the_wire_source_reports_absence_rather_than_raising`. Replace everything after `# -- shapes that are absence` with the second block. In the module docstring, delete nothing: it does not mention UART.

```python
@pytest.fixture
def api(paths):
    return Api(paths)


REPORTER = RoadrunnerHelper()

WIRE_INFO = DeviceInfo(
    source=SOURCE_INFO,
    version="v1.2.0-3-gdeadbee",
    digest_algorithm=uf2.DIGEST_CRC32_ISO_HDLC,
    digest=0xBBE38AA9,
    image_start=0x10000000,
    image_length=600,
)


class _Spy:
    """A `wire` source that records which serials it was asked about.

    The point of the injection is that the wire is never touched for a board
    Klipper answered for, and the only way to assert that is to watch the
    source: a test that merely checked no port was opened would pass for the
    wrong reason, since the caller under test opens none either way.
    """

    def __init__(self, answers: dict[str, DeviceInfo] | None = None) -> None:
        self.asked: list[str] = []
        self.answers = answers or {}

    def __call__(self, serial: str) -> DeviceInfo | None:
        self.asked.append(serial)
        return self.answers.get(serial)


def test_klipper_answers_the_version_and_the_digest(api):
    api._call = serve_klipper(_sensor())

    found = api.reported_images(REPORTER, [SERIAL])

    assert found[SERIAL] == DeviceInfo(
        source=SOURCE_KLIPPER,
        version="v1.2.0-3-gdeadbee",
        digest_algorithm=uf2.DIGEST_CRC32_ISO_HDLC,
        # The extra sends "0xbbe38aa9"; every other side of this comparison
        # holds an int, and the conversion happens at this boundary.
        digest=0xBBE38AA9,
        image_start=0x10000000,
        image_length=600,
    )


def test_no_port_is_opened_for_a_board_klipper_answered_for(api):
    api._call = serve_klipper(_sensor())
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == []
    assert found[SERIAL].source == SOURCE_KLIPPER


def test_the_wire_answers_for_a_board_klipper_has_no_object_for(api):
    api._call = serve_klipper({})
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == [SERIAL]
    assert found[SERIAL] is WIRE_INFO


def test_without_a_wire_source_an_unanswered_serial_is_simply_absent(api):
    """`fw.status` passes no source, so it can never reach for a port.

    Absent is the honest answer there: "we did not ask" and "the board said
    nothing" both fall through to the version comparison, and neither may be
    turned into a mismatch.
    """
    api._call = serve_klipper({})

    assert api.reported_images(REPORTER, [SERIAL]) == {}


def test_the_join_survives_udevs_suffix_and_case(api):
    """The two sources spell one burned-in string two ways.

    A missed join is indistinguishable from "Klipper did not answer", so it
    would silently send every board to the wire - the failure this
    normalisation exists to prevent, and one a test that built both sides from
    the same literal could never catch.
    """
    api._call = serve_klipper(_sensor(serial=SERIAL))
    spy = _Spy()

    tracked = SERIAL.lower() + "-if00"
    found = api.reported_images(REPORTER, [tracked], wire=spy)

    assert spy.asked == []
    # Keyed as the caller spelled it, so a row finds its own answer.
    assert found[tracked].source == SOURCE_KLIPPER


def test_a_digest_without_a_range_is_carried_not_probed_for(api):
    """Firmware that reports no range has reported no comparable digest.

    That is absence, and it falls through to the version comparison. Going to
    the wire to fill the range in would be the host substituting for the board.
    """
    api._call = serve_klipper(_sensor(start=None, length=None))
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == []
    assert found[SERIAL].digest == 0xBBE38AA9
    assert found[SERIAL].image_start is None
    assert found[SERIAL].has_digest() is False


def test_an_algorithm_this_host_never_heard_of_is_absence(api):
    """Not mismatch. A permanent mismatch no flash can clear is the bug."""
    api._call = serve_klipper(_sensor(algorithm="sha256-truncated"))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest_algorithm is None


def test_an_unreported_algorithm_is_absence(api):
    api._call = serve_klipper(_sensor(algorithm=None, digest=None))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.digest_algorithm is None
    assert found.digest is None


def test_an_unparseable_digest_is_absence(api):
    api._call = serve_klipper(_sensor(digest="not-a-number"))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest is None


def test_a_sensor_with_no_serial_yet_is_not_guessed_at(api):
    """A board mid-connect reports the object with nothing in it.

    Attaching its digest to whichever serial was being asked about would put
    one board's image on another board's row.
    """
    api._call = serve_klipper(_sensor(serial=""))
    spy = _Spy()

    assert api.reported_images(REPORTER, [SERIAL], wire=spy) == {}
    assert spy.asked == [SERIAL]


def test_an_unreachable_klipper_falls_through_to_the_wire(api):
    api._call = serve_klipper(_sensor(), reachable=False)
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert found[SERIAL] is WIRE_INFO


def test_only_the_fields_the_reporter_names_are_queried(api):
    """The sub-second budget is why the cached object list exists at all."""
    api._call = serve_klipper(_sensor())

    api.reported_images(REPORTER, [SERIAL])

    queries = [
        params for params in api._call.queries if OBJECT in (params.get("objects") or {})
    ]
    assert queries and queries[0]["objects"][OBJECT] == ["identity", "firmware_image"]


def test_the_roadrunner_wire_source_speaks_device_info(paths, monkeypatch):
    """INFO's dict becomes the same `DeviceInfo` Klipper's answer does, so the
    verdict never learns which source it came from."""
    monkeypatch.setattr(
        roadrunner,
        "wire_provenance",
        lambda _paths: lambda serial: {
            "fw_version": "v1.2.0-3-gdeadbee",
            "digest_algorithm": 1,
            "digest": 0xBBE38AA9,
            "image_start": 0x10000000,
            "image_length": 600,
        },
    )

    assert REPORTER.wire_source(paths)(SERIAL) == WIRE_INFO


def test_the_roadrunner_wire_source_passes_absence_through(paths, monkeypatch):
    monkeypatch.setattr(roadrunner, "wire_provenance", lambda _paths: lambda serial: None)

    assert REPORTER.wire_source(paths)(SERIAL) is None
```

```python
# -- shapes that are absence, not a plausible wrong number -----------------


def test_a_boolean_digest_is_absence(api):
    """`isinstance(True, int)` is True in Python.

    Unguarded, a JSON `true` in that field becomes the digest `1` - a number
    that looks like an answer and compares against every artifact. Absence is
    the only honest reading.
    """
    api._call = serve_klipper(_sensor(digest=True))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest is None


def test_a_boolean_image_bound_is_absence(api):
    api._call = serve_klipper(_sensor(start=True, length=True))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.image_start is None
    assert found.image_length is None


def test_the_algorithm_name_is_matched_however_it_is_cased(api):
    """The extra sends one constant, but the name is the wire contract and
    another build of it could spell the same algorithm differently."""
    api._call = serve_klipper(_sensor(algorithm="CRC32-ISO-HDLC"))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.digest_algorithm == uf2.DIGEST_CRC32_ISO_HDLC


def test_a_sensor_object_with_no_serial_produces_no_entry_at_all():
    """Asserted on the reader, not the join: a junk key that happens never to
    be asked for would let the join test pass while the reader was broken."""
    assert REPORTER.from_klipper(_sensor(serial="")[OBJECT]) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_device_info.py tests/test_provenance.py tests/test_helpers.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'mcu_updater.device_info'`.

- [ ] **Step 4: Write `device_info.py`**

Create `src/mcu_updater/device_info.py`:

```python
"""What a board reports it is running, read through its firmware's handler.

Spec §5 of the one-pipeline design. A family's helper supplies the reader when
it has one; everything else - klipper, katapult, a fork with no oddity - reads
through `KLIPPER`, built in here because it is not firmware-specific code.

**Absence is never mismatch.** Every field of `DeviceInfo` is optional, and a
field a firmware does not report stays None. A comparison that treated None as
a value would flag a board running exactly what we flashed, permanently, with
no flash able to clear it.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

from .discovery.byid import canonical_serial

if TYPE_CHECKING:
    from .firmware import FirmwareFamily
    from .helpers.spec import DeviceInfoReader

#: Klipper's object graph answered.
SOURCE_KLIPPER = "klipper"
#: The board answered over its own protocol, because Klipper did not hold it.
SOURCE_INFO = "info"


@dataclasses.dataclass(frozen=True)
class DeviceInfo:
    """One board's report of its firmware, from whichever source answered."""

    source: str
    version: str | None
    digest_algorithm: int | None = None
    digest: int | None = None
    image_start: int | None = None
    image_length: int | None = None

    def has_digest(self) -> bool:
        """Whether this report can be checked against a built image.

        A digest needs its algorithm and the range it covers. `DIGEST_NONE` is
        0, so a board that says "no algorithm" has no digest, and an
        `image_start` of 0 is a real start.
        """
        return (
            bool(self.digest_algorithm)
            and self.digest is not None
            and self.image_start is not None
            and self.image_length is not None
        )


def serial_key(serial: str) -> str:
    """One spelling for a serial, so the two sources can actually meet.

    The board burns one string and reports it twice: through the USB serial
    descriptor, which reaches us via `/dev/serial/by-id` with udev's interface
    marker on the end, and through the identity register, which reaches us via
    the klippy extra verbatim. A join that compared them raw would miss on the
    suffix or on case - and a missed join is indistinguishable from "Klipper
    did not answer", so it would silently send every board to the wire.
    """
    return canonical_serial(serial.strip()).upper()


def digest_int(value: object) -> int | None:
    """Klipper's `"%#010x"` digest as the int every other side of this uses.

    The extra sends a hex string on purpose - an identifier to compare, not a
    quantity - while INFO sends four little-endian bytes and `record_build`
    stores an int. Int is canonical and each reader converts on the way in,
    because a comparison that ever saw `"0xbbe38aa9" != 3185217705` would call
    a correctly flashed board stale forever: reflashing cannot clear a
    formatting difference. Anything unparseable is absence, not mismatch.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def image_int(value: object) -> int | None:
    """A reported image bound, or None when the firmware reported none."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


#: git describe embeds the commit as a g<hex> token. Anything after it - notably
#: `-dirty`, which a makefile-patched build always carries - is noise here.
_KLIPPER_SHA_RE = re.compile(r"(?:^|-)g([0-9a-f]{7,40})(?:-|$)")


class KlipperReader:
    """Klipper and every tree that stamps Klipper's `git describe`.

    A board that stamps a hand-maintained literal instead - Cartographer's
    `CONFIG_VERSION` - has no g<hex> token, and its family's helper says so
    explicitly (`helpers.cartographer`).
    """

    name: str = "klipper"
    klipper_prefix: str = "mcu"

    def running_sha(self, version: str | None) -> str | None:
        match = _KLIPPER_SHA_RE.search(version or "")
        return match.group(1) if match else None

    def is_dirty(self, version: str | None) -> bool:
        """Never. A makefile-patched build is always `-dirty`, and a board that
        read as dirty for that would want a flash no flash can satisfy."""
        return False


KLIPPER = KlipperReader()


def reader_for(family: FirmwareFamily | None) -> DeviceInfoReader:
    """The reader a family's boards are read through.

    A misspelt helper raises, from `helpers.for_name`; a helper without the
    capability is an ordinary firmware and reads through Klipper.
    """
    from . import helpers

    if family is None or not family.helper:
        return KLIPPER
    reader = helpers.device_info_reader(helpers.for_name(family.helper, family=family.name))
    return reader if reader is not None else KLIPPER
```

- [ ] **Step 5: Add the capabilities and the handlers**

Append to `src/mcu_updater/helpers/spec.py` (add `from collections.abc import Callable, Mapping` and `runtime_checkable` to the imports, and `from ..device_info import DeviceInfo` plus `from ..paths import Paths` under `TYPE_CHECKING`):

```python
@runtime_checkable
class DeviceInfoReader(Protocol):
    """Reads a commit and a dirty flag out of the version string a board stamps."""

    name: str
    #: The Klipper section prefix whose objects are this firmware's boards.
    klipper_prefix: str

    def running_sha(self, version: str | None) -> str | None: ...

    def is_dirty(self, version: str | None) -> bool: ...


@runtime_checkable
class ImageReporter(Protocol):
    """Reports a board's image digest, Klipper first and the wire second."""

    name: str
    klipper_prefix: str
    #: The object fields to ask Klipper for; nothing else, inside fw.status's
    #: sub-second budget.
    klipper_fields: tuple[str, ...]

    def from_klipper(self, values: Mapping[str, Any]) -> tuple[str, DeviceInfo] | None:
        """(the serial the board reported, its report), or None with no serial."""
        ...

    def wire_source(self, paths: Paths) -> Callable[[str], DeviceInfo | None]:
        """A per-serial reader that opens the port. Never from `fw.status`."""
        ...
```

In `src/mcu_updater/helpers/__init__.py`, add beside `bootsel_requester` and export `DeviceInfoReader`, `ImageReporter`, `device_info_reader`, `image_reporter`:

```python
def device_info_reader(helper: Helper | None) -> DeviceInfoReader | None:
    """The helper's device-info reader, or None when it has none."""
    return helper if isinstance(helper, DeviceInfoReader) else None


def image_reporter(helper: Helper | None) -> ImageReporter | None:
    """The helper's image reporter, or None when it has none."""
    return helper if isinstance(helper, ImageReporter) else None
```

Create `src/mcu_updater/helpers/cartographer.py`:

```python
"""Cartographer's Klipper fork.

Its one oddity: `CONFIG_VERSION` is a hand-maintained literal such as
"CARTOGRAPHER 6.2.0", with no commit in it. docs/decisions.md ("Do not
synthesize a sha into Cartographer's `CONFIG_VERSION`") records why this reader
reports no sha rather than inventing one, and why the verdict for these boards
is the built-stamp comparison instead.
"""

from __future__ import annotations


class CartographerHelper:
    name: str = "cartographer"
    klipper_prefix: str = "mcu"

    def running_sha(self, version: str | None) -> str | None:
        return None

    def is_dirty(self, version: str | None) -> bool:
        return False


__all__ = ["CartographerHelper"]
```

Replace the body of `KnomiSerialHelper` in `src/mcu_updater/helpers/knomi_serial.py` (update the module docstring's "device info in Task 2" sentence to say it reads device info through `providers.pio`, and add `from ..providers import pio`):

```python
class KnomiSerialHelper:
    name: str = "knomi_serial"
    klipper_prefix: str = "knomi_serial"

    def running_sha(self, version: str | None) -> str | None:
        return pio.running_sha(version)

    def is_dirty(self, version: str | None) -> bool:
        return pio.is_dirty(version)
```

Add to `RoadrunnerHelper` in `src/mcu_updater/helpers/roadrunner.py` (imports: `re`, `from collections.abc import Callable, Mapping`, `from .. import device_info, uf2`, `from ..device_info import SOURCE_INFO, SOURCE_KLIPPER, DeviceInfo`, `from ..paths import Paths`; update the module docstring to "Roadrunner's device info, and its confirmed direct-USB BOOTSEL requester."):

```python
#: `git describe --tags --always --dirty`: `v1.2.0-3-gdeadbee`, a bare
#: `deadbee` in a repo with no tags, either with `-dirty`. A bare tag or `dev`
#: carries no commit.
_SHA_RE = re.compile(r"(?:^|-g)([0-9a-f]{7,40})(?:-dirty)?$")
```

```python
    klipper_prefix: str = "high_resolution_filament_sensor"
    klipper_fields: tuple[str, ...] = ("identity", "firmware_image")

    def running_sha(self, version: str | None) -> str | None:
        match = _SHA_RE.search((version or "").strip())
        return match.group(1) if match else None

    def is_dirty(self, version: str | None) -> bool:
        return (version or "").strip().endswith("-dirty")

    def from_klipper(self, values: Mapping[str, Any]) -> tuple[str, DeviceInfo] | None:
        """One sensor object's `identity` and `firmware_image`, as a report.

        The extra's `get_status` answers both halves: `identity.firmware_version`
        is the same `${git_describe}` INFO reports, and `firmware_image` is the
        digest and the range it covers. So a board Klippy is holding needs no
        port opened - which matters because Klippy holding the port is exactly
        what stops the admin protocol from opening it. It reports the same on
        usbserial, i2c and uart.

        Both wire spellings are converted here rather than at the comparison:
        the digest arrives as a hex string and the algorithm as a name, and one
        unconverted value would read as a permanent mismatch on a board that is
        running precisely what we flashed.
        """
        identity = values.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        serial = identity.get("serial")
        if not isinstance(serial, str) or not serial.strip():
            # No serial is no join key. A board mid-connect reports the
            # object with nothing in it, and guessing which tracked serial
            # it is would attach one board's digest to another's row.
            return None
        image = values.get("firmware_image")
        image = image if isinstance(image, dict) else {}
        version = identity.get("firmware_version")
        return serial, DeviceInfo(
            source=SOURCE_KLIPPER,
            version=version if isinstance(version, str) and version else None,
            digest_algorithm=uf2.algorithm_id(image.get("algorithm")),
            digest=device_info.digest_int(image.get("digest")),
            image_start=device_info.image_int(image.get("start")),
            image_length=device_info.image_int(image.get("length")),
        )

    def wire_source(self, paths: Paths) -> Callable[[str], DeviceInfo | None]:
        read = roadrunner.wire_provenance(paths)

        def source(serial: str) -> DeviceInfo | None:
            info = read(serial)
            if not info:
                return None
            return DeviceInfo(
                source=SOURCE_INFO,
                version=info.get("fw_version"),
                digest_algorithm=info.get("digest_algorithm"),
                digest=info.get("digest"),
                image_start=info.get("image_start"),
                image_length=info.get("image_length"),
            )

        return source
```

`src/mcu_updater/helpers/registry.py`:

```python
HELPERS: tuple[Helper, ...] = (CartographerHelper(), KnomiSerialHelper(), RoadrunnerHelper())
```

`src/mcu_updater/firmware.py`:

```python
HELPERS: tuple[str, ...] = ("cartographer", "knomi_serial", "roadrunner")
```

In `mcu-updater.cfg` and `tests/fixtures/registry.cfg`, add `helper: cartographer` under `[firmware cartographer]` (after its `flashers:` line).

- [ ] **Step 6: Move `pio`'s dirty check and set `klipper_section` from the reader**

In `src/mcu_updater/providers/pio.py`, add after `running_sha`:

```python
def is_dirty(running: str | None) -> bool:
    """Whether what a screen reports running was built from uncommitted changes."""
    return bool(_FW_DIRTY_RE.search(running or ""))
```

and in `device_status` replace `if _FW_DIRTY_RE.search(running):` with `if is_dirty(running):`. Grep `scripts/mutations/states.json` and `pio.json` for `_FW_DIRTY_RE.search(running)` and re-anchor any hit to `if is_dirty(running):`.

Replace the `klipper_section` comment on `PioType` with:

```python
    #: The Klipper section prefix whose entries are displays of this type.
    #: `[knomi_serial T0_knomi]` -> `knomi_serial`. Set by `load()` from the
    #: family helper's reader; never read from a `[type]` - see
    #: `typelist.REMOVED_KEYS`.
    klipper_section: str = "knomi_serial"
```

Add `from .. import device_info` to `pio.py`'s imports, and in `load()` pass `klipper_section=device_info.reader_for(family).klipper_prefix,` to `PioType(...)`, after `firmware=first_fw,`.

- [ ] **Step 7: Replace the status readers**

In `src/mcu_updater/agent/methods/status.py`:

1. Delete `SENSOR_SECTION`, `_provenance_key`, `_digest_int`, `_image_int`, the `_FW_SHA_RE` comment and regex, and `_running_sha`. Keep `MCU_NAMES_TTL` and its comment.
2. Add `from ... import device_info` to the `from ... import (...)` line, and `from ...device_info import DeviceInfo` plus `from ...helpers import DeviceInfoReader, ImageReporter` below it. Drop `canonical_serial` and `uf2_mod` if ruff reports them unused.
3. Replace `sensor_provenance` and `device_provenance` with:

```python
    def reported_images(
        self,
        reporter: ImageReporter,
        serials: Iterable[str],
        *,
        wire: Callable[[str], DeviceInfo | None] | None = None,
    ) -> dict[str, DeviceInfo]:
        """What each serial reports running: Klipper first, the wire second.

        The order is forced by the lock, not chosen: Klipper holds the port
        when connected, so a read that went to the wire first would fail on
        exactly the machines where the answer was already in the object graph.

        **The wire is injected, never reached for.** A wire source opens a
        port, which no caller with `fw.status`'s sub-second budget can afford.
        Callers that have already freed the ports pass
        `reporter.wire_source(paths)`; everyone else passes nothing and gets
        the Klipper answer or none at all.

        The fallback trigger is "Klipper had no object for this serial", never
        "Klipper's answer was incomplete": a field the firmware did not report
        is absence, and filling it from the wire would be the host substituting
        for the board.

        Serials come back spelled as the caller spelled them, so a row can be
        looked up by the serial it tracks rather than by the canonical form.
        """
        klipper = self._klipper_images(reporter)
        out: dict[str, DeviceInfo] = {}
        for serial in serials:
            answer = klipper.get(device_info.serial_key(serial))
            if answer is not None:
                out[serial] = answer
                continue
            if wire is None:
                continue
            info = wire(serial)
            if info is not None:
                out[serial] = info
        return out

    def _klipper_images(self, reporter: ImageReporter) -> dict[str, DeviceInfo]:
        """Canonical serial -> what Klipper says that board is running."""
        names = self._object_names_for(reporter.klipper_prefix)
        if not names:
            return {}
        query: dict[str, Any] = {name: list(reporter.klipper_fields) for name in names}
        res = self._probe("printer.objects.query", {"objects": query})
        status = (res or {}).get("status")
        if not isinstance(status, dict):
            return {}
        out: dict[str, DeviceInfo] = {}
        for name in names:
            values = status.get(name)
            if not isinstance(values, dict):
                continue
            found = reporter.from_klipper(values)
            if found is not None:
                serial, info = found
                out[device_info.serial_key(serial)] = info
        return out
```

4. `flash_state`: add the keyword `reader: DeviceInfoReader = device_info.KLIPPER` after `built_version`, document it in the docstring ("`reader` is the family's device-info reader; `device_info.reader_for(family)`"), and replace `running = _running_sha(version or "")` with `running = reader.running_sha(version or "")`. Mirror the signature in `_api.py`'s stub, including the `built_version` keyword it lacks today.
5. `type_status`: replace

```python
        fw_head = git_head(firmware.resolve(self.paths, application, families).source_dir(self.paths))
```

with

```python
        family = firmware.resolve(self.paths, application, families)
        fw_head = git_head(family.source_dir(self.paths))
        reader = device_info.reader_for(family)
```

and pass `reader=reader,` to both `self.flash_state(` calls (serial and CAN).
6. `pio_status`: after `flashlog = FlashLog(self.paths)`, add `families = firmware.load(self.paths)`; inside the per-type loop add `reader = device_info.reader_for(families.get(display.firmware))`, and call `self._platformio_confidence(entry, flashlog, reader)`.
7. `_platformio_confidence(entry, flashlog, reader: DeviceInfoReader)`: drop the `pio_mod` import and read

```python
        record = flashlog.entry_for(
            display_key(ident), reader.running_sha(entry.get("firmware_version"))
        )
```

In `src/mcu_updater/agent/methods/bulk.py`, in both `_boards_to_flash` and `_canbus_boards_to_flash`, resolve the family once per type the same way (`family = firmware.resolve(...)`, `fw_head = git_head(family.source_dir(self.paths))`, `reader = device_info.reader_for(family)`) and pass `reader=reader,` to every `self.flash_state(` call. Add `from ... import device_info`.

In `src/mcu_updater/agent/methods/__init__.py`, change `from .status import StatusMixin, _running_sha  # noqa: F401 - re-exported for tests` to `from .status import StatusMixin`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_device_info.py tests/test_provenance.py tests/test_helpers.py tests/test_typelist.py tests/test_agent_methods.py tests/test_agent_bulk.py tests/test_pio.py -q`
Expected: PASS.

Run: `rg -n "sensor_provenance|device_provenance|_running_sha|_provenance_key|_digest_int|_image_int|SENSOR_SECTION|rangeless" src tests docs/agent-api.md docs/decisions.md README.md`
Expected: no output.

- [ ] **Step 9: Re-anchor `provenance-read.json` and `display-flash.json`**

Replace `scripts/mutations/provenance-read.json` with:

```json
{
  "_comment": "Reading what a board is running, Klipper first. Every guard here is a wrong-answer shape rather than a crash: a digest read as 1 instead of absent, a join key that never matches, a name left unconverted. Each one produces a board that is running exactly what we flashed and is reported as needing a flash forever, because no reflash can clear a formatting difference. So each has to be shown load-bearing.",
  "file": "src/mcu_updater/device_info.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_provenance.py",
    "tests/test_device_info.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "the hex string Klipper sends is parsed as hex",
      "find": "            return int(value, 16)",
      "replace": "            return int(value)"
    },
    {
      "name": "an unparseable digest is absence, not an exception out of a status read",
      "find": "        except ValueError:",
      "replace": "        except TypeError:"
    },
    {
      "name": "a boolean digest is absence, not the number 1",
      "find": "    if isinstance(value, bool):",
      "replace": "    if False:"
    },
    {
      "name": "a boolean image bound is absence, not the number 1",
      "find": "    if isinstance(value, bool) or not isinstance(value, int):",
      "replace": "    if not isinstance(value, int):"
    },
    {
      "name": "the two sources' spellings of one serial are normalised before joining",
      "find": "    return canonical_serial(serial.strip()).upper()",
      "replace": "    return serial"
    },
    {
      "name": "the algorithm name becomes an id at the reader's boundary",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "            digest_algorithm=uf2.algorithm_id(image.get(\"algorithm\")),",
      "replace": "            digest_algorithm=image.get(\"algorithm\"),"
    },
    {
      "name": "a sensor object with no serial yields no entry",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "        if not isinstance(serial, str) or not serial.strip():",
      "replace": "        if False:"
    },
    {
      "name": "the wire source converts INFO into the same report Klipper's answer is",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "                source=SOURCE_INFO,",
      "replace": "                source=SOURCE_KLIPPER,"
    },
    {
      "name": "only the fields the reporter names are asked for, inside a sub-second budget",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        query: dict[str, Any] = {name: list(reporter.klipper_fields) for name in names}",
      "replace": "        query: dict[str, Any] = {name: None for name in names}"
    },
    {
      "name": "Klipper's answer is preferred over the wire",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "            answer = klipper.get(device_info.serial_key(serial))",
      "replace": "            answer = None"
    },
    {
      "name": "the wire is consulted when Klipper had no object",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "            if wire is None:",
      "replace": "            if wire is not None:"
    }
  ]
}
```

In `scripts/mutations/display-flash.json`, replace the `"a record the screen disagrees with is discarded"` mutation's `find` with:

```json
      "find": "        record = flashlog.entry_for(\n            display_key(ident), reader.running_sha(entry.get(\"firmware_version\"))\n        )",
```

- [ ] **Step 10: Write the device-info mutation spec**

Create `scripts/mutations/device-info.json`:

```json
{
  "_comment": "Device info is read through the family's helper. Each mutation here makes some caller read a board with the wrong reader, and each wrong reader is a verdict that is silently wrong: a Roadrunner describe read as sha-less, a Cartographer literal given an invented sha, a screen matched against another type's objects.",
  "file": "src/mcu_updater/device_info.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_device_info.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a digest needs its range to be comparable",
      "find": "            and self.image_start is not None\n",
      "replace": ""
    },
    {
      "name": "DIGEST_NONE is no digest",
      "find": "            bool(self.digest_algorithm)\n            and self.digest is not None",
      "replace": "            self.digest_algorithm is not None\n            and self.digest is not None"
    },
    {
      "name": "a family's helper supplies its reader",
      "find": "    return reader if reader is not None else KLIPPER",
      "replace": "    return KLIPPER"
    },
    {
      "name": "a helper without a reader falls back to Klipper",
      "find": "    return reader if reader is not None else KLIPPER",
      "replace": "    return reader  # type: ignore[return-value]"
    },
    {
      "name": "a bare sha is a Roadrunner commit",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "_SHA_RE = re.compile(r\"(?:^|-g)([0-9a-f]{7,40})(?:-dirty)?$\")",
      "replace": "_SHA_RE = re.compile(r\"-g([0-9a-f]{7,40})(?:-dirty)?$\")"
    },
    {
      "name": "a dirty Roadrunner build still carries its commit",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "_SHA_RE = re.compile(r\"(?:^|-g)([0-9a-f]{7,40})(?:-dirty)?$\")",
      "replace": "_SHA_RE = re.compile(r\"(?:^|-g)([0-9a-f]{7,40})$\")"
    },
    {
      "name": "a Roadrunner knows a dirty build",
      "file": "src/mcu_updater/helpers/roadrunner.py",
      "find": "        return (version or \"\").strip().endswith(\"-dirty\")",
      "replace": "        return False"
    },
    {
      "name": "Cartographer never gets an invented sha",
      "file": "src/mcu_updater/helpers/cartographer.py",
      "find": "    def running_sha(self, version: str | None) -> str | None:\n        return None",
      "replace": "    def running_sha(self, version: str | None) -> str | None:\n        from ..device_info import KLIPPER\n\n        return KLIPPER.running_sha(version)"
    },
    {
      "name": "a screen type's Klipper prefix comes from its reader",
      "file": "src/mcu_updater/providers/pio.py",
      "find": "            klipper_section=device_info.reader_for(family).klipper_prefix,\n",
      "replace": ""
    },
    {
      "name": "flash_state extracts the sha with the reader it is given",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        running = reader.running_sha(version or \"\")",
      "replace": "        running = device_info.KLIPPER.running_sha(version or \"\")"
    },
    {
      "name": "type_status reads through the family's reader",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        reader = device_info.reader_for(family)",
      "replace": "        reader = device_info.KLIPPER"
    },
    {
      "name": "a bulk flash selects through the family's reader",
      "file": "src/mcu_updater/agent/methods/bulk.py",
      "find": "        reader = device_info.reader_for(family)",
      "replace": "        reader = device_info.KLIPPER"
    }
  ]
}
```

If `bulk.py` ends up with the `reader = device_info.reader_for(family)` line in both `_boards_to_flash` and `_canbus_boards_to_flash` at the same indentation, the harness refuses the last find as ambiguous: extend it with the following line of `_boards_to_flash` so it is unique, and do not add a mutation for the CAN copy unless a test covers it (the CAN path has no reader-sensitive fixture).

Run, one at a time:

```bash
python scripts/mutation_test.py scripts/mutations/device-info.json
python scripts/mutation_test.py scripts/mutations/provenance-read.json
python scripts/mutation_test.py scripts/mutations/display-flash.json
python scripts/mutation_test.py scripts/mutations/states.json
python scripts/mutation_test.py scripts/mutations/pio.json
python scripts/mutation_test.py scripts/mutations/pio-provider-selection.json
python scripts/mutation_test.py scripts/mutations/cartographer-version.json
```

Expected: every mutation killed. A survivor is a missing test; write it before continuing.

- [ ] **Step 11: Docs**

Add to `docs/decisions.md`, after "### A family declares its flashers and its helper":

```markdown
### Device info is read through the family's helper

What a board is running - its commit, whether it was dirty, its image digest -
is read by the handler its family's helper supplies (`helpers.DeviceInfoReader`,
`helpers.ImageReporter`), and by `device_info.KLIPPER` for a family with none.
There used to be a sha regex in `status.py` for boards and another in `pio.py`
for screens, and a Roadrunner's describe went through whichever the caller
reached for. One reader per firmware means one answer per board. Cartographer
has a helper for exactly one reason: to say its version has no sha, rather
than leave that to a regex that happens not to match. A Roadrunner reports the
same device info on usbserial, i2c and uart; a field firmware does not report
is absence, never mismatch, whatever the transport.
```

- [ ] **Step 12: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass.

```bash
git add src tests scripts/mutations mcu-updater.cfg docs/decisions.md
git commit -m "refactor(status): read device info through the family's helper

Each firmware's reader extracts its own sha and dirty flag, and Roadrunner
reports its image through one reported_images read, Klipper first and the wire
second. Cartographer gets a helper that says its version has no sha. No
verdict changes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

---

### Task 3: The flasher seam — `supports`, `target`, `select`; BOOTSEL absorbs the helper

Spec §7: a family's `flashers:` list is walked in order and the first flasher whose `supports(device, helper)` accepts the device writes it. `helper_bootsel` folds into `bootsel`, and `needs_services_stopped` moves onto the target, where `Bootsel` can answer it per device. This task builds the seam and moves the two CMake write paths onto it, because they are the only callers of the flasher being deleted. Every other caller moves in Task 4. It changes no verdict and no target set; the one wire change is Ruling 8's flasher name.

**Files:**
- Modify: `src/mcu_updater/flashers/spec.py` (module docstring's "Selection is not here" paragraph; `Device`, `KIND_*`, `chipset_matches`; `FlashTarget.needs_services_stopped`; `Flasher.supports`, `Flasher.target`)
- Modify: `src/mcu_updater/flashers/flashtool.py`, `src/mcu_updater/flashers/dfu_util.py`, `src/mcu_updater/flashers/esptool.py` (`supports`, `target`; flashtool's `chipsets` gains `"lpc176"`)
- Modify: `src/mcu_updater/flashers/bootsel.py` (absorbs the handoff: `supports`, `target`, `write`, `settled`, `target_for`, the `copy_uf2` docstring's `HelperBootsel` mention)
- Delete: `src/mcu_updater/flashers/helper_bootsel.py`
- Modify: `src/mcu_updater/flashers/registry.py` (`FLASHERS`, `needs_services_stopped`, `group_by_stop`, `resolve`, `select`), `src/mcu_updater/flashers/__init__.py`
- Modify: `src/mcu_updater/errors.py` (`NoFlasherError`)
- Modify: `src/mcu_updater/agent/methods/flash.py:184-301` (`_cmake_flash`), `src/mcu_updater/cli.py:590-647` (`_cmake_targets`)
- Create: `tests/test_flasher_select.py`, `scripts/mutations/flasher-supports.json`
- Modify: `tests/test_flash.py:1423-2040` (the `helper_bootsel` tests move onto `Bootsel`), `tests/test_agent_flash.py:447`, `tests/test_cli.py:567,587,626-639`, `tests/test_helpers.py` (drop the `helper_bootsel` exclusion), `tests/test_roadrunner.py:674` (docstring names the renamed test)
- Modify: `scripts/mutations/bulk-operations.json` (the `group_by_stop` anchor)
- Docs: `docs/agent-api.md`, `docs/cmake-provider.md`, `docs/decisions.md`
- Mutation specs at risk: `bulk-operations.json` ("a write that needs no stop stays outside the outage" anchors the `group_by_stop` line this task rewrites: re-anchor it here), `bootsel-apply-wait.json` (anchors `bootsel.py`; this task edits the same file but none of its anchored lines, so run it), `bootsel-erase.json` (anchors `flashers/flash.py:965`'s `bootsel.target_for` call, whose signature this task extends compatibly: run it), `flasher-selection.json` (anchors `select_for`, untouched until Task 4: run it). Run `rg -n "helper_bootsel|HelperBootsel|needs_services_stopped|target_for" scripts/mutations` first and re-anchor every hit in this task's commit.

**Interfaces:**
- Consumes: `helpers.Helper`, `helpers.BootselRequester`, `helpers.bootsel_requester(helper)`, `helpers.for_name(name, *, family)`, `FirmwareFamily.flashers` (Task 1)
- Produces:
  - `flashers.KIND_SERIAL = "serial"`, `KIND_CANBUS = "canbus_uuid"`, `KIND_SCREEN = "screen"`, `KIND_BARE = "bare"`
  - `flashers.Device(type: str, id: str, chipset: str, state: str, fw: str, kind: str = KIND_SERIAL, detail: Mapping[str, Any] = {})`, frozen. `detail` is the caller's selection payload: the board dict (flashtool), `{"display", "screen"}` plus any extra keys to carry into the target's detail, e.g. `reason` (esptool), `{"uf2_file"}` (bootsel), `{"fw_bin"}` (dfu_util)
  - `flashers.spec.chipset_matches(flasher: Flasher, chipset: str) -> bool`
  - `FlashTarget.needs_services_stopped: bool | None = None` (last field)
  - `Flasher.supports(device: Device, helper: Helper | None) -> bool`
  - `Flasher.target(paths: Paths, device: Device, helper: Helper | None, *, stop_services: tuple[str, ...]) -> FlashTarget`
  - `flashers.needs_services_stopped(target: FlashTarget) -> bool`
  - `flashers.resolve(family: FirmwareFamily, device: Device, helper: Helper | None) -> Flasher | None`
  - `flashers.select(paths: Paths, family: FirmwareFamily, device: Device, helper: Helper | None, *, stop_services: tuple[str, ...]) -> FlashTarget` — `stop_services` is a **required** keyword; raises `NoFlasherError`
  - `flashers.bootsel.target_for(uf2_file: str, *, chipset: str, paths: Paths | None = None, type_name: str = "", serial: str = "", helper: BootselRequester | None = None, stop_services: tuple[str, ...] = ()) -> FlashTarget`
  - `errors.NoFlasherError(FlashError)`, `code = "no_flasher"`, data `family`, `flashers`, `type`, `id`, `chipset`, `state`
  - Removed: `flashers.HelperBootsel`, `flashers.helper_bootsel`

Decisions this task makes, so the implementer does not re-derive them:

- **Flashtool does not refuse on liveness.** A serial or CAN board is flashtool's whatever its state. An absent board is the write's `device_not_found`, which names the fix, and turning it into `no_flasher` would change a documented error code (`docs/agent-api.md`, the `fw.flash` preconditions table). `Flashtool.states` stays as documentation of what the write goes through.
- **The BOOTSEL helper path does not match on chipset.** A CMake `[type]` may leave `chipset:` empty (`CmakeType.chipset` defaults to `""`), and today's `_cmake_flash` writes such a type. The helper vouches for its own board: `request_bootsel` confirms the protocol identity before anything is written. A board already sitting in BOOTSEL has no helper to vouch for it, so that path still requires an `rp2040` chipset.
- **`Bootsel.target` sets `needs_services_stopped`.** `True` on the helper path (the request goes over a port Klipper may hold), `False`-by-default (left `None`, so the class attribute answers) for a board already in BOOTSEL.

- [ ] **Step 1: Write the failing selection tests**

Create `tests/test_flasher_select.py`:

```python
"""Which flasher writes a device is its family's answer.

`select_for` matched a chipset and a state against every registered flasher in
registry order, so an RP2040 had one reachable flasher no matter what it was
running. Each `[firmware]` section now lists the flashers that may write it, in
order, and the first whose `supports()` accepts the device wins. A family that
lists nothing able to write a device refuses it by name instead of guessing.
"""

from __future__ import annotations

import pytest

from mcu_updater import flashers
from mcu_updater.devices import (
    STATE_BOOTSEL,
    STATE_DFU,
    STATE_KATAPULT,
    STATE_KLIPPER,
    STATE_OFFLINE,
)
from mcu_updater.errors import NoFlasherError
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import (
    KIND_BARE,
    KIND_CANBUS,
    KIND_SCREEN,
    KIND_SERIAL,
    Device,
)
from mcu_updater.helpers import BootselHandoff

RR_SERIAL = "RR-0123456789ABCDEFGHJKMNPQRS"


class _Requester:
    """A helper that can put its board into BOOTSEL."""

    name = "requester"

    def request_bootsel(self, bench, *, serial, chipset, ctx):
        return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

    def wait_ready(self, bench, *, serial, chipset, ctx):
        return None


class _Plain:
    """A helper with no BOOTSEL capability."""

    name = "plain"


def _family(*names: str, name: str = "fam") -> FirmwareFamily:
    return FirmwareFamily(name=name, flashers=tuple(names))


def _device(
    *,
    chipset: str = "stm32f446xx",
    state: str = STATE_KLIPPER,
    kind: str = KIND_SERIAL,
    type: str = "board",
    id: str = "usb-Klipper_stm32f446xx_29000-if00",
    detail: dict | None = None,
) -> Device:
    return Device(
        type=type,
        id=id,
        chipset=chipset,
        state=state,
        fw="fam",
        kind=kind,
        detail=detail or {},
    )


def _roadrunner(uf2: str = "/tmp/rr.uf2", *, chipset: str = "rp2040") -> Device:
    return _device(
        chipset=chipset,
        state="roadrunner",
        type="roadrunner",
        id=RR_SERIAL,
        detail={"uf2_file": uf2},
    )


def _bare_rp2040(uf2: str = "/tmp/katapult.uf2") -> Device:
    return _device(
        chipset="rp2040",
        state=STATE_BOOTSEL,
        kind=KIND_BARE,
        type="rp2040",
        id="",
        detail={"uf2_file": uf2},
    )


# --- supports ----------------------------------------------------------------


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(state=STATE_KLIPPER), True),
        (_device(state=STATE_KATAPULT), True),
        # An absent board is the write's device_not_found, not a selection
        # failure: that error names the fix.
        (_device(state=STATE_OFFLINE), True),
        (_device(chipset="rp2040"), True),
        (_device(chipset="lpc1769"), True),
        (_device(kind=KIND_CANBUS, state="unknown", id="bcb5346fc731"), True),
        (_device(chipset="esp32"), False),
        (_device(kind=KIND_SCREEN, chipset="esp32"), False),
        (_device(kind=KIND_BARE, state=STATE_DFU), False),
    ],
)
def test_flashtool_writes_serial_and_can_boards(device, expected):
    assert flashers.Flashtool().supports(device, None) is expected


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(kind=KIND_SCREEN, chipset="esp32", state="unknown"), True),
        (_device(kind=KIND_SCREEN, chipset="", state="unknown"), True),
        (_device(chipset="esp32"), False),
        (_device(), False),
    ],
)
def test_esptool_writes_screens(device, expected):
    assert flashers.Esptool().supports(device, None) is expected


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(kind=KIND_BARE, state=STATE_DFU, id=""), True),
        (_device(kind=KIND_BARE, state=STATE_BOOTSEL, id=""), False),
        (_device(kind=KIND_BARE, state=STATE_DFU, chipset="rp2040", id=""), False),
        (_device(state=STATE_DFU), False),
    ],
)
def test_dfu_util_writes_a_bare_stm32_in_dfu(device, expected):
    assert flashers.DfuUtil().supports(device, None) is expected


def test_bootsel_writes_a_board_already_in_bootsel():
    assert flashers.Bootsel().supports(_bare_rp2040(), None) is True


def test_bootsel_does_not_write_a_bare_board_that_is_not_in_bootsel():
    device = _device(chipset="rp2040", state=STATE_DFU, kind=KIND_BARE, id="")
    assert flashers.Bootsel().supports(device, _Requester()) is False


def test_bootsel_writes_a_running_board_whose_helper_can_request_bootsel():
    assert flashers.Bootsel().supports(_roadrunner(), _Requester()) is True


def test_the_helper_vouches_for_its_board_without_a_chipset():
    """A CMake type may leave `chipset:` empty, and today's write path takes it."""
    assert flashers.Bootsel().supports(_roadrunner(chipset=""), _Requester()) is True


@pytest.mark.parametrize("helper", [None, _Plain()])
def test_bootsel_does_not_write_a_running_board_with_no_requester(helper):
    assert flashers.Bootsel().supports(_roadrunner(), helper) is False


def test_a_board_in_bootsel_still_needs_an_rp2040_chipset():
    device = _device(chipset="stm32f446xx", state=STATE_BOOTSEL, kind=KIND_BARE, id="")
    assert flashers.Bootsel().supports(device, None) is False


# --- resolve -----------------------------------------------------------------


def test_the_first_flasher_in_the_familys_order_wins():
    # A running RP2040 both flashers accept: flashtool by chipset, bootsel
    # through the helper.
    device = _device(chipset="rp2040", type="roadrunner", id=RR_SERIAL, detail={"uf2_file": "x"})
    helper = _Requester()

    assert flashers.resolve(_family("bootsel", "flashtool"), device, helper).name == "bootsel"
    assert flashers.resolve(_family("flashtool", "bootsel"), device, helper).name == "flashtool"


def test_first_install_goes_through_the_katapult_list():
    katapult = _family("dfu_util", "bootsel", name="katapult")

    assert flashers.resolve(katapult, _bare_rp2040(), None).name == "bootsel"
    dfu = _device(kind=KIND_BARE, state=STATE_DFU, id="")
    assert flashers.resolve(katapult, dfu, None).name == "dfu_util"


def test_a_flasher_the_family_does_not_list_is_never_chosen():
    """flashtool could write this board; the family did not list it."""
    assert flashers.resolve(_family("esptool"), _device(), None) is None


def test_a_family_with_no_list_resolves_nothing():
    assert flashers.resolve(_family(), _device(), None) is None


# --- select ------------------------------------------------------------------


def test_nothing_supporting_the_device_is_refused_by_name(paths):
    family = _family("esptool", name="klipper")
    device = _device(type="ebb36", id="usb-Klipper_stm32g0b1xx_1-if00")

    with pytest.raises(NoFlasherError) as exc:
        flashers.select(paths, family, device, None, stop_services=("klipper",))

    message = str(exc.value)
    assert "[firmware klipper]" in message
    assert "flashers: esptool" in message
    assert "ebb36" in message
    assert exc.value.code == "no_flasher"
    assert exc.value.data["family"] == "klipper"
    assert exc.value.data["flashers"] == ["esptool"]
    assert exc.value.data["state"] == STATE_KLIPPER


def test_a_family_with_no_flashers_says_so(paths):
    with pytest.raises(NoFlasherError) as exc:
        flashers.select(paths, _family(), _device(), None, stop_services=())
    assert "flashers: (none)" in str(exc.value)


def test_the_helper_path_target_stops_services(paths):
    helper = _Requester()

    target = flashers.select(
        paths,
        _family("bootsel", name="roadrunner"),
        _roadrunner("/tmp/rr.uf2"),
        helper,
        stop_services=("klipper", "moonraker"),
    )

    assert target.flasher == "bootsel"
    assert target.type == "roadrunner"
    assert target.id == RR_SERIAL
    assert target.needs_services_stopped is True
    assert flashers.needs_services_stopped(target) is True
    assert target.stop_services == ("klipper", "moonraker")
    assert target.detail["helper"] is helper
    assert target.detail["uf2_file"] == "/tmp/rr.uf2"
    assert target.detail["chipset"] == "rp2040"


def test_a_board_already_in_bootsel_stops_nothing(paths):
    target = flashers.select(
        paths,
        _family("dfu_util", "bootsel", name="katapult"),
        _bare_rp2040("/tmp/katapult.uf2"),
        None,
        stop_services=("klipper",),
    )

    assert target.flasher == "bootsel"
    assert flashers.needs_services_stopped(target) is False
    assert "helper" not in target.detail
    assert target.stop_services == ()


def test_a_board_already_in_bootsel_is_copied_not_asked(paths):
    """Even when the family's helper could ask: there is nothing to ask."""
    target = flashers.select(
        paths,
        _family("bootsel", name="roadrunner"),
        _bare_rp2040(),
        _Requester(),
        stop_services=("klipper",),
    )

    assert "helper" not in target.detail
    assert flashers.needs_services_stopped(target) is False


def test_the_flashtool_target_carries_the_board_dict(paths):
    board = {"type": "ebb36", "serial": "usb-x", "chipset": "stm32g0b1xx"}
    device = _device(type="ebb36", id="usb-x", chipset="stm32g0b1xx", detail=board)

    target = flashers.select(
        paths, _family("flashtool"), device, None, stop_services=("klipper",)
    )

    assert target.flasher == "flashtool"
    assert dict(target.detail) == board
    assert target.stop_services == ("klipper",)
    assert flashers.needs_services_stopped(target) is True


def test_the_dfu_target_carries_the_image_and_serial(paths):
    device = _device(
        kind=KIND_BARE, state=STATE_DFU, id="DFU123", detail={"fw_bin": "/tmp/k.bin"}
    )

    target = flashers.select(
        paths, _family("dfu_util"), device, None, stop_services=("klipper",)
    )

    assert target.flasher == "dfu_util"
    assert target.detail["fw_bin"] == "/tmp/k.bin"
    assert target.detail["dfu_serial"] == "DFU123"
    assert flashers.needs_services_stopped(target) is False


# --- group_by_stop -----------------------------------------------------------


def test_the_target_flag_overrides_the_flasher_when_grouping():
    stays_up = flashers.FlashTarget(flasher="flashtool", type="a", id="1", needs_services_stopped=False)
    goes_down = flashers.FlashTarget(flasher="bootsel", type="b", id="2", needs_services_stopped=True)
    default_free = flashers.FlashTarget(flasher="bootsel", type="c", id="3")
    default_stopped = flashers.FlashTarget(flasher="flashtool", type="d", id="4")

    stopped, free = flashers.group_by_stop([stays_up, goes_down, default_free, default_stopped])

    assert stopped == [goes_down, default_stopped]
    assert free == [stays_up, default_free]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_flasher_select.py -q`
Expected: collection error, `ImportError: cannot import name 'NoFlasherError'`.

- [ ] **Step 3: The seam in `flashers/spec.py` and `errors.py`**

In `src/mcu_updater/errors.py`, after `class DeviceNotFoundError(FlashError):`, add:

```python
class NoFlasherError(FlashError):
    """No flasher in a family's `flashers:` list can write this device.

    A config fact, not a hardware one: the fix is the family's list, and the
    message names it.
    """

    code = "no_flasher"
```

In `src/mcu_updater/flashers/spec.py`:

1. Replace the module docstring's final paragraph (from "**Selection is not here.**" to "a flasher writes.") with:

```text
**Selection is a question each flasher answers.** `supports(device, helper)`
says whether this flasher can write a device given its family's helper, and
`target()` turns that device into the `FlashTarget` it will write.
`flashers.registry.select` walks the family's `flashers:` list in order and
takes the first yes. Which devices exist and which of them want firmware stays
the inventory's business: the caller brings a `Device`, the family decides who
writes it, and the flasher writes.
```

2. Change the imports to:

```python
import dataclasses
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol

from ..paths import Paths
from ..service import ServiceController
from ..settings import Settings

if TYPE_CHECKING:
    from ..helpers.spec import Helper
```

3. After `Bench`, add:

```python
#: How a `Device` is addressed, which is most of what decides who can write it.
#: A by-id serial.
KIND_SERIAL = "serial"
#: A CAN UUID. Its liveness is often unknown, and flashtool writes it anyway.
KIND_CANBUS = "canbus_uuid"
#: A PlatformIO device reached through its configured port.
KIND_SCREEN = "screen"
#: A board with no firmware of ours yet, in a ROM bootloader (DFU or BOOTSEL).
KIND_BARE = "bare"


@dataclasses.dataclass(frozen=True)
class Device:
    """One device a write is being chosen for.

    What `Flasher.supports` reads and `Flasher.target` turns into a
    `FlashTarget`. `type`, `id`, `chipset` and `state` are the facts selection
    needs; `fw` is the family the device's `[type]` resolved to.

    `detail` is the caller's selection payload, carried onto the target for
    the flasher that ends up owning it: the board dict for flashtool,
    `{"display", "screen"}` for esptool, `{"uf2_file"}` for bootsel,
    `{"fw_bin"}` for dfu_util. Each caller builds the one payload its family's
    flashers read, so no flasher has to be told which caller it is.
    """

    type: str
    id: str
    chipset: str
    state: str
    fw: str
    kind: str = KIND_SERIAL
    detail: Mapping[str, Any] = dataclasses.field(default_factory=dict)
```

4. In `FlashTarget`, after `detail`, add:

```python
    #: Overrides the flasher's `needs_services_stopped` for this one write when
    #: set. `Bootsel` is why: a board already in BOOTSEL holds no port Klipper
    #: could have open, while asking a running board to enter BOOTSEL goes over
    #: exactly that port. One flasher, two answers, decided when the target is
    #: built - the same way `stop_services` already is. Read it through
    #: `flashers.registry.needs_services_stopped`, never directly.
    needs_services_stopped: bool | None = None
```

5. In the `Flasher` Protocol, replace the `chipsets` comment's second paragraph ("Together with `states`, ... selection already knows about.") with:

```python
    #: An input to `supports`, through `chipset_matches`.
```

and add, after `needs_services_stopped: bool` and before `prepared`:

```python
    def supports(self, device: Device, helper: Helper | None) -> bool:
        """Can this flasher write `device`, given its family's helper?

        Pure: no bus access, no config reads. The family's list decides the
        order these are asked in, so this answers only "could I", never
        "should I".
        """
        ...

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        """`device` as the target this flasher writes. Only called after
        `supports` said yes."""
        ...
```

6. After the `Flasher` Protocol, add:

```python
def chipset_matches(flasher: Flasher, chipset: str) -> bool:
    """Does `chipset` start with one of the flasher's chipset prefixes?"""
    return any(chipset.startswith(prefix) for prefix in flasher.chipsets)
```

7. `__all__ = ["KIND_BARE", "KIND_CANBUS", "KIND_SCREEN", "KIND_SERIAL", "Bench", "Device", "FlashTarget", "Flasher", "chipset_matches"]`

- [ ] **Step 4: `supports` and `target` on flashtool, esptool and dfu_util**

`src/mcu_updater/flashers/flashtool.py`: change the spec import to `from .spec import KIND_CANBUS, KIND_SERIAL, Bench, Device, FlashTarget, chipset_matches`, add `from typing import TYPE_CHECKING, Any` with

```python
if TYPE_CHECKING:
    from ..helpers.spec import Helper
    from ..paths import Paths
```

change `chipsets` to `("stm32", "rp2040", "lpc176")` with the comment line `#: lpc176x boards run Katapult too (profiles.py), and flash-all has always written them.` above it, and add after `needs_services_stopped = True`:

```python
    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A serial or CAN board whose chipset Katapult runs on.

        Not narrowed by state. A board that is absent right now is still
        flashtool's to write, and the write says `device_not_found`, which
        names the fix; a CAN board's liveness is often unknown and has always
        been written anyway.
        """
        return device.kind in (KIND_SERIAL, KIND_CANBUS) and chipset_matches(
            self, device.chipset
        )

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        return target_for(dict(device.detail), stop_services=stop_services)
```

`src/mcu_updater/flashers/esptool.py`: change the spec import to `from .spec import KIND_SCREEN, Bench, Device, FlashTarget`, add `from ..helpers.spec import Helper` and `from ..paths import Paths` inside the existing `if TYPE_CHECKING:` block, and add after `needs_services_stopped = True`:

```python
    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A PlatformIO device reached through its configured port. Its
        identity is confirmed at write time, in `prepared`."""
        return device.kind == KIND_SCREEN

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        target = target_for(
            device.detail["display"],
            device.detail["screen"],
            stop_services=stop_services,
        )
        # Anything else the caller put in `detail` (bulk's `reason`) rides
        # along; the screen's own keys win.
        return dataclasses.replace(
            target, detail={**device.detail, **target.detail}
        )
```

`src/mcu_updater/flashers/dfu_util.py`: change the spec import to `from .spec import KIND_BARE, Bench, Device, FlashTarget, chipset_matches`, add `from typing import TYPE_CHECKING, Any` with an `if TYPE_CHECKING:` block importing `Helper` and `Paths` as above, and add after `needs_services_stopped = False`:

```python
    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A bare STM32 holding BOOT0."""
        return (
            device.kind == KIND_BARE
            and device.state in self.states
            and chipset_matches(self, device.chipset)
        )

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        return target_for(
            device.detail["fw_bin"],
            chipset=device.chipset,
            dfu_serial=device.id or None,
        )
```

- [ ] **Step 5: `Bootsel` absorbs the handoff; delete `helper_bootsel.py`**

In `src/mcu_updater/flashers/bootsel.py`:

1. Module docstring: replace the last paragraph ("`needs_services_stopped = False`, same reasoning as `DfuUtil`: ... already dealt with Klipper.") with:

```text
It writes a board two ways. A board already in BOOTSEL is copied to the one
mounted volume. A running board whose family's helper can request BOOTSEL
(`helpers.BootselRequester`) is asked to enter it first, the copy goes to the
volume matching the USB topology the helper captured, and `settled` waits for
the helper to confirm the board came back as itself. The second way goes over
a port Klipper may hold, so its targets carry `needs_services_stopped=True`;
the first stops nothing.
```

2. Imports become:

```python
import contextlib
import os
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..devices import STATE_BOOTSEL, bootsel_devices, bootsel_id_for, bootsel_scan
from ..discovery.bootsel import mount_for_topology
from ..errors import (
    BootselNotMountedError,
    DeviceNotFoundError,
    FlashError,
    OperationCancelled,
    UpdaterError,
)
from .spec import KIND_BARE, Bench, Device, FlashTarget, chipset_matches

if TYPE_CHECKING:
    from ..helpers.spec import BootselRequester, Helper
    from ..paths import Paths
```

3. In `copy_uf2`'s docstring, replace "which for `HelperBootsel` is" with "which for a helper-requested BOOTSEL is".

4. Replace `class Bootsel:` through the end of `settled` with:

```python
class Bootsel:
    """Writes an RP2040 through its BOOTSEL mass-storage bootloader."""

    name = "bootsel"
    label = "BOOTSEL (mass storage)"
    chipsets: tuple[str, ...] = ("rp2040",)
    states: tuple[str, ...] = (STATE_BOOTSEL,)
    #: False for a board already in BOOTSEL: nothing holds its port. A target
    #: that asks a helper for BOOTSEL overrides this with True (see
    #: `target_for`), because that request goes over a port Klipper may hold.
    needs_services_stopped = False

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A board in BOOTSEL, or a running board its helper can put there.

        The helper path does not match on chipset: a CMake `[type]` may leave
        `chipset:` empty, and the helper confirms its own board's protocol
        identity before anything is written. A board already in BOOTSEL has
        nothing to vouch for it but its chipset.
        """
        if device.state in self.states:
            return chipset_matches(self, device.chipset)
        return device.kind != KIND_BARE and helpers.bootsel_requester(helper) is not None

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        uf2 = device.detail["uf2_file"]
        requester = helpers.bootsel_requester(helper)
        if device.state in self.states or requester is None:
            return target_for(uf2, chipset=device.chipset, paths=paths)
        return target_for(
            uf2,
            chipset=device.chipset,
            type_name=device.type,
            serial=device.id,
            helper=requester,
            stop_services=stop_services,
        )

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up for the batch."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        uf2 = target.detail["uf2_file"]
        ensure_uf2(uf2)
        requester: BootselRequester | None = target.detail.get("helper")

        if bench.settings.dry_run:
            if requester is None:
                ctx.reporter(
                    "info", f"[dry-run] would copy {uf2} to the mounted RPI-RP2 volume"
                )
            else:
                ctx.reporter(
                    "info",
                    f"[dry-run] would request BOOTSEL then copy {uf2} to its matched volume",
                )
            return {"mount": None}

        if requester is None:
            mount = _find_mount(bench.paths)
        else:
            handoff = requester.request_bootsel(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
            )
            mount = mount_for_topology(bench.paths, handoff.topology)
        copy_uf2(uf2, mount, ctx)
        return {"mount": mount}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Wait for a helper-requested board to confirm its identity.

        A board that was already in BOOTSEL reboots as Katapult under a serial
        it has never had; waiting for that is adoption, which this cannot do
        because it cannot name the device. Waiting for the board to take the
        image already happened inside `write`.
        """
        requester: BootselRequester | None = target.detail.get("helper")
        if requester is None or bench.settings.dry_run:
            return
        try:
            requester.wait_ready(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
            )
        except OperationCancelled:
            raise
        except UpdaterError as exc:
            # The UF2 copy already completed. Every boundary the spec enumerates
            # is pre-copy or at-copy; there is no post-write one, and step 4 of
            # the flow waits for the device "non-fatally" without qualification.
            # So *any* readiness outcome - a slow return, a probe that would not
            # answer, an identity that came back wrong, two devices answering to
            # one serial - is a warning here. Rewriting a completed write as a
            # failure would invite a re-flash of a board that is already correct.
            ctx.reporter("warn", str(exc))
```

5. Replace `target_for` with:

```python
def target_for(
    uf2_file: str,
    *,
    chipset: str,
    paths: Paths | None = None,
    type_name: str = "",
    serial: str = "",
    helper: BootselRequester | None = None,
    stop_services: tuple[str, ...] = (),
) -> FlashTarget:
    """An RP2040 to write, as a target.

    **With a helper**, a running board of a configured type: `serial` is its
    durable identity, the helper asks it to enter BOOTSEL, and the target
    stops `stop_services` because that request goes over a port Klipper may
    hold.

    **Without one**, a board already in BOOTSEL. The boot ROM publishes the
    flash chip's id as a USB mass-storage serial, and it is recorded here, but
    **it is not an identity**: two boards from one batch have been observed on
    hardware reporting the same `pico_get_unique_board_id()`. It is a label on
    the flash, nothing more, and nothing downstream keys on it - `write` and
    `settled` never read `target.id` on this path, and the add-mcu pairing key
    is looked up separately in `agent.methods.flash`. Correlating a board
    across the BOOTSEL reboot needs the USB topology path instead; see
    docs/bootsel-mountpoint-design.md.

    `paths` is only used for that lookup (via `bootsel_devices`); callers that
    omit it, or that hit zero or more than one device, get `id=""` - the
    multi-volume case is still refused in `write`.
    """
    if helper is not None:
        return FlashTarget(
            flasher=Bootsel.name,
            type=type_name,
            id=serial,
            stop_services=stop_services,
            detail={"uf2_file": uf2_file, "chipset": chipset, "helper": helper},
            needs_services_stopped=True,
        )
    device_id = ""
    if paths is not None:
        present = bootsel_devices(paths)
        if len(present) == 1:
            device_id = bootsel_id_for(present[0]) or ""
    return FlashTarget(
        flasher=Bootsel.name,
        type=chipset,
        id=device_id,
        detail={"uf2_file": uf2_file, "chipset": chipset},
    )
```

Delete `src/mcu_updater/flashers/helper_bootsel.py` (`git rm`).

If `from .. import helpers` at module level closes an import cycle (`python -c "import mcu_updater.flashers"` fails), move it into `supports` and `target` as a local `from .. import helpers`, the way `esptool.write` imports `pio`.

- [ ] **Step 6: `resolve`, `select` and the per-target stop flag in the registry**

In `src/mcu_updater/flashers/registry.py`:

1. Module docstring first line becomes: `"""Which flashers exist, which one a family picks for a device, and how a batch splits around the Klipper stop.`
2. Imports:

```python
from typing import TYPE_CHECKING

from ..errors import NoFlasherError, UnsupportedChipsetError
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import Device, Flasher, FlashTarget

if TYPE_CHECKING:
    from ..firmware import FirmwareFamily
    from ..helpers.spec import Helper
    from ..paths import Paths
```

3. `FLASHERS` loses `HelperBootsel()`.
4. Before `group_by_stop`, add:

```python
def needs_services_stopped(target: FlashTarget) -> bool:
    """The target's own answer when it has one, else its flasher's."""
    if target.needs_services_stopped is not None:
        return target.needs_services_stopped
    return by_name(target.flasher).needs_services_stopped
```

5. In `group_by_stop`, replace the three-line append with:

```python
        (stopped if needs_services_stopped(target) else free).append(target)
```

6. Replace the selection section's banner comment with `# selection: the family's list, in order` and add above `select_for`:

```python
def resolve(
    family: FirmwareFamily, device: Device, helper: Helper | None
) -> Flasher | None:
    """The first flasher in `family.flashers` that supports `device`, or None.

    The family's order, not the registry's: the same RP2040 is flashtool's in
    `[firmware klipper]` and bootsel's in `[firmware roadrunner]`, and a global
    first match could only ever reach one of them.
    """
    for name in family.flashers:
        flasher = by_name(name)
        if flasher.supports(device, helper):
            return flasher
    return None


def select(
    paths: Paths,
    family: FirmwareFamily,
    device: Device,
    helper: Helper | None,
    *,
    stop_services: tuple[str, ...],
) -> FlashTarget:
    """The target that writes `device`, chosen by its family.

    `stop_services` is required so no caller can forget it: a flasher that
    needs Klipper down and gets an empty list stops nothing.

    Raises `NoFlasherError` naming the family and its list when nothing in it
    supports the device. A batch reports that as the device's failure; a
    single-device call raises it.
    """
    flasher = resolve(family, device, helper)
    if flasher is None:
        listed = ", ".join(family.flashers) or "(none)"
        raise NoFlasherError(
            f"nothing in [firmware {family.name}] (flashers: {listed}) can write "
            f"{device.type} {device.id or device.chipset} while it is "
            f"{device.state}.",
            family=family.name,
            flashers=list(family.flashers),
            type=device.type,
            id=device.id,
            chipset=device.chipset,
            state=device.state,
        )
    return flasher.target(paths, device, helper, stop_services=stop_services)
```

`select_for` stays until Task 4; change its `for f in FLASHERS:` body only if ruff complains (it should not).

In `src/mcu_updater/flashers/__init__.py`: drop the `helper_bootsel` import and `"HelperBootsel"`, import `needs_services_stopped`, `resolve`, `select` from `.registry` and `KIND_BARE`, `KIND_CANBUS`, `KIND_SCREEN`, `KIND_SERIAL`, `Device` from `.spec`, and add all eight to `__all__` in sorted order.

- [ ] **Step 7: Run the selection tests**

Run: `python -m pytest tests/test_flasher_select.py -q`
Expected: PASS.

- [ ] **Step 8: Move the CMake write paths onto `select`**

`src/mcu_updater/agent/methods/flash.py`, in `_cmake_flash`:

1. Replace the `helper = ...` statement and its `if helper is None: raise FlashError(...)` block (Task 1 wrapped it in `helpers.bootsel_requester`) with:

```python
        helper = helpers.for_name(family.helper, family=family.name)
```

2. Replace `if find_device(self.paths, "", serial) is None:` with

```python
        present = find_device(self.paths, "", serial)
        if present is None:
```

keeping the `RpcError` body.

3. Move `units = stop_services.for_cmake(...)` above `from ...service import assert_printer_idle`, and replace the `target = flashers.helper_bootsel.target_for(...)` statement with (also above the idle check, so a config problem refuses before the printer is asked about):

```python
        # The family decides who writes this board. A family whose list or
        # helper cannot is a NoFlasherError here, before a job exists.
        target = flashers.select(
            self.paths,
            family,
            flashers.Device(
                type=mcu_type,
                id=serial,
                chipset=target_type.chipset,
                state=present.state,
                fw=family.name,
                detail={"uf2_file": fw_bin},
            ),
            helper,
            stop_services=units,
        )
```

`src/mcu_updater/cli.py`, in `_cmake_targets`:

1. Replace the `helper = ...` statement and its `if helper is None: raise UpdaterError(...)` block with `helper = helpers.for_name(family.helper, family=family.name)`.
2. Replace the `return [flashers.helper_bootsel.target_for(...)]` with:

```python
    present = find_device(c.paths, "", serial)
    return [
        flashers.select(
            c.paths,
            family,
            flashers.Device(
                type=mcu_type,
                id=serial,
                chipset=target_type.chipset,
                state=present.state if present is not None else STATE_OFFLINE,
                fw=family.name,
                detail={"uf2_file": fw_bin},
            ),
            helper,
            stop_services=stop_services.for_cmake(
                c.paths, target_type, c.settings, families
            ),
        )
    ]
```

and add `STATE_OFFLINE` and `find_device` to the `from .devices import (...)` block if either is missing.
3. In its docstring, replace "`fw.flash` resolves the family, requires its configured static helper" wording (the paragraph starting "No `FlashLog` record and no attachment check") with: "No `FlashLog` record and no attachment check, matching the CLI's other flash paths: the family's `flashers:` list picks the writer (`flashers.select`), and a family that cannot write the board refuses with `NoFlasherError`. The helper performs its own protocol confirmation once the services have released the port." Remove the sentence saying both refusals are setup the operator must fix if it now describes only one refusal; keep the no-artifact refusal text.

- [ ] **Step 9: Move the tests off `helper_bootsel`**

`tests/test_flash.py`, mechanically, in the tests at lines 1423-2040:
- `flashers.helper_bootsel.target_for(` → `flashers.bootsel.target_for(` (every keyword argument stays: the new `target_for` accepts them all)
- `flashers.HelperBootsel()` → `flashers.Bootsel()`
- rename each `test_helper_bootsel_<rest>` to `test_bootsel_handoff_<rest>`
- line 1253's docstring: "for `HelperBootsel` that" → "for a helper-requested BOOTSEL that"; line 1739: "real HelperBootsel" → "real Bootsel handoff"
- replace `test_helper_bootsel_requires_services_stopped` with:

```python
def test_bootsel_handoff_targets_require_services_stopped(tmp_path):
    class Helper:
        name = "test"

    handoff = flashers.bootsel.target_for(
        str(tmp_path / "rr.uf2"),
        type_name="roadrunner",
        serial="RR-0123456789ABCDEFGHJKMNPQRS",
        chipset="rp2040",
        helper=Helper(),
        stop_services=("klipper",),
    )
    bare = flashers.bootsel.target_for(str(tmp_path / "k.uf2"), chipset="rp2040")

    assert flashers.Bootsel.needs_services_stopped is False
    assert flashers.needs_services_stopped(handoff) is True
    assert flashers.needs_services_stopped(bare) is False
```

`tests/test_roadrunner.py:674`: `test_helper_bootsel_settled_warns_on_non_timeout_roadrunner_errors` → `test_bootsel_handoff_settled_warns_on_non_timeout_roadrunner_errors`.

`tests/test_agent_flash.py:447` and `tests/test_cli.py:567,587`: `"helper_bootsel"` → `"bootsel"`.

`tests/test_cli.py:626-639`, `test_a_cmake_type_with_no_helper_says_so`, becomes:

```python
def test_a_cmake_type_with_no_helper_names_its_flashers(c, fake_root, captured, monkeypatch):
    """A family whose helper cannot request BOOTSEL leaves `bootsel` nothing to
    write a running board with, and the refusal names the list to fix."""
    _cmake_flashable(c, fake_root, helper=False)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(NoFlasherError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    assert "[firmware roadrunner]" in str(exc.value)
    assert "flashers: bootsel" in str(exc.value)
    assert captured == []
```

and add `NoFlasherError` to that file's `from mcu_updater.errors import` line.

Add to `tests/test_agent_flash.py`, after `test_a_successful_cmake_write_records_build_sidecar_provenance`:

```python
def test_a_cmake_family_that_cannot_write_the_board_refuses_before_a_job(
    cmake_flash_factory, monkeypatch
):
    api = cmake_flash_factory(dry_run="false")
    monkeypatch.setattr(
        "mcu_updater.flashers.registry.resolve", lambda family, device, helper: None
    )

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash", {"name": "roadrunner", "serial": ROADRUNNER_SERIAL})

    assert exc.value.data["code"] == "no_flasher"
    assert api.runner.list() == []
```

If `cmake_flash_factory` builds an api whose board is not attached, the `device_not_found` check fires first; in that case monkeypatch `mcu_updater.devices.find_device` the way the neighbouring tests do (search the file for `find_device`) so the board is present. If the runner has no `list()`, assert on whatever the neighbouring refusal tests use to prove no job was created.

`tests/test_helpers.py`, `test_the_flasher_names_are_exactly_the_registry`: drop the comment and the `- {"helper_bootsel"}`.

- [ ] **Step 10: Run the affected tests**

Run: `python -m pytest tests/test_flasher_select.py tests/test_flash.py tests/test_agent_flash.py tests/test_cli.py tests/test_helpers.py tests/test_roadrunner.py tests/test_agent_bulk.py -q`
Expected: PASS.

- [ ] **Step 11: Mutation specs**

Re-anchor `scripts/mutations/bulk-operations.json`'s "a write that needs no stop stays outside the outage":

```json
    {
      "name": "a write that needs no stop stays outside the outage",
      "file": "src/mcu_updater/flashers/registry.py",
      "find": "        (stopped if needs_services_stopped(target) else free).append(target)",
      "replace": "        stopped.append(target)"
    },
```

Create `scripts/mutations/flasher-supports.json`:

```json
{
  "_comment": "Selection through a family's flashers: list. Each supports() is the only thing keeping a flasher off a device it cannot write, resolve() must keep the family's order, and the per-target stop flag is what keeps Klipper up for a board already in BOOTSEL and down for a helper-requested one.",
  "file": "src/mcu_updater/flashers/registry.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_flasher_select.py",
    "tests/test_flash.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "resolve walks the family's list in order",
      "find": "    for name in family.flashers:",
      "replace": "    for name in reversed(family.flashers):"
    },
    {
      "name": "select refuses when nothing supports the device",
      "find": "    if flasher is None:\n        listed = ",
      "replace": "    if False:\n        listed = "
    },
    {
      "name": "the target's own stop flag wins",
      "find": "    if target.needs_services_stopped is not None:",
      "replace": "    if False:"
    },
    {
      "name": "chipsets match by prefix",
      "file": "src/mcu_updater/flashers/spec.py",
      "find": "    return any(chipset.startswith(prefix) for prefix in flasher.chipsets)",
      "replace": "    return chipset in flasher.chipsets"
    },
    {
      "name": "flashtool writes serial and CAN boards only",
      "file": "src/mcu_updater/flashers/flashtool.py",
      "find": "        return device.kind in (KIND_SERIAL, KIND_CANBUS) and chipset_matches(",
      "replace": "        return True and chipset_matches("
    },
    {
      "name": "flashtool writes lpc176x boards",
      "file": "src/mcu_updater/flashers/flashtool.py",
      "find": "(\"stm32\", \"rp2040\", \"lpc176\")",
      "replace": "(\"stm32\", \"rp2040\")"
    },
    {
      "name": "esptool writes screens only",
      "file": "src/mcu_updater/flashers/esptool.py",
      "find": "        return device.kind == KIND_SCREEN",
      "replace": "        return True"
    },
    {
      "name": "dfu_util writes bare boards only",
      "file": "src/mcu_updater/flashers/dfu_util.py",
      "find": "            device.kind == KIND_BARE\n",
      "replace": "            True\n"
    },
    {
      "name": "dfu_util writes a board in DFU only",
      "file": "src/mcu_updater/flashers/dfu_util.py",
      "find": "            and device.state in self.states\n",
      "replace": "\n"
    },
    {
      "name": "a board in BOOTSEL still needs an rp2040 chipset",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "            return chipset_matches(self, device.chipset)",
      "replace": "            return True"
    },
    {
      "name": "the handoff path needs a requester",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        return device.kind != KIND_BARE and helpers.bootsel_requester(helper) is not None",
      "replace": "        return device.kind != KIND_BARE"
    },
    {
      "name": "a bare board is never a handoff",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        return device.kind != KIND_BARE and helpers.bootsel_requester(helper) is not None",
      "replace": "        return helpers.bootsel_requester(helper) is not None"
    },
    {
      "name": "a handoff target stops services",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "            needs_services_stopped=True,",
      "replace": "            needs_services_stopped=None,"
    },
    {
      "name": "a board already in BOOTSEL gets the plain target",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        if device.state in self.states or requester is None:",
      "replace": "        if requester is None:"
    },
    {
      "name": "settled waits for a handoff board",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        if requester is None or bench.settings.dry_run:\n            return\n",
      "replace": "        return\n"
    }
  ]
}
```

The "dfu_util writes a board in DFU only" replacement leaves a blank line inside a parenthesised expression, which is valid Python. If the harness reports a mutant as a syntax error rather than killed, change its replace to `"            and True\n"`.

Run, one at a time:
- `python scripts/mutation_test.py scripts/mutations/flasher-supports.json`
- `python scripts/mutation_test.py scripts/mutations/bulk-operations.json`
- `python scripts/mutation_test.py scripts/mutations/bootsel-apply-wait.json`
- `python scripts/mutation_test.py scripts/mutations/bootsel-erase.json`
- `python scripts/mutation_test.py scripts/mutations/flasher-selection.json`

Expected: every mutant killed. A survivor in `flasher-supports.json` means a test above is missing its case: add the case, do not delete the mutation.

- [ ] **Step 12: Docs**

`docs/agent-api.md`:
- In the stable error-code list (line ~87), add `no_flasher` after `device_not_found`.
- Replace the paragraph starting "For a `builder: cmake` type, the same `fw.flash {name?, serial, force?}` method" (line ~673) through "`helper: roadrunner`." with:

```markdown
For a `builder: cmake` type, the same `fw.flash {name?, serial, force?}` method
uses the type's declared serial identity, and the firmware family's
`flashers:` list picks the writer. Resolution spans all configured providers:
an unknown serial, a duplicate declaration, or a `name` that points at a
different owner fails as `unknown_serial`/`ambiguous_serial`/
`serial_tracked_elsewhere` before a job is created. The named type must have a
staged UF2, and a family whose flashers cannot write the board (for Roadrunner,
`flashers: bootsel` with `helper: roadrunner`) fails with `no_flasher`, whose
`data` carries `family`, `flashers`, `type`, `id`, `chipset` and `state`. The
write reports `"flasher": "bootsel"`.
```

- Wherever a `flashed[]`/`failures[]` example or prose names `helper_bootsel` (search the file), change it to `bootsel`.

`docs/cmake-provider.md`, "## Helper-backed flashing": replace the first paragraph with:

```markdown
CMake boards are written through their family's `flashers:` list like every
other board. For `[firmware roadrunner]` that list is `bootsel`, which writes a
running board by asking the family's helper to put it into BOOTSEL, copying the
staged UF2 to the volume matching the board's USB topology, and waiting for the
helper to confirm the board came back. A board already sitting in BOOTSEL is
written the same way without a helper. The RP2040 flashtool route is untouched
for families that list `flashtool`.
```

`docs/decisions.md`, after "### A family declares its flashers and its helper":

```markdown
### A family's list picks the flasher

`flashers.select` walks the family's `flashers:` list and takes the first
flasher whose `supports(device, helper)` says yes. There is no global
chipset-and-state table: `select_for` made an RP2040 reach exactly one flasher
whatever it ran. `helper_bootsel` is folded into `bootsel` because a flasher
describes a mechanism, and "ask the firmware to enter BOOTSEL first" is a step
of that mechanism the helper supplies, not a second product-named flasher.
`needs_services_stopped` can therefore differ per target, and a target's own
value wins over its flasher's. Do not turn it back into a class-only
attribute: a board already in BOOTSEL would then stop Klipper for nothing, or a
helper-requested one would write under a running Klipper.
```

- [ ] **Step 13: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass. Then `rg -n "helper_bootsel|HelperBootsel" src tests docs scripts README.md` prints nothing outside `docs/superpowers/`, `docs/bootsel-mountpoint-design.md` and `docs/cmake-provider-design.md` (historical design records; leave them).

```bash
git add -A src/mcu_updater/flashers src/mcu_updater/errors.py src/mcu_updater/agent/methods/flash.py src/mcu_updater/cli.py tests scripts/mutations docs/agent-api.md docs/cmake-provider.md docs/decisions.md
git commit -m "feat(flashers): select through the family's flashers list

Every flasher answers supports(device, helper) and builds its own target, and
flashers.select walks the family's list in order. helper_bootsel folds into
bootsel, which now sets needs_services_stopped per target. A Roadrunner write
reports \"flasher\": \"bootsel\", and a family that cannot write a board refuses
with no_flasher.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

---

### Task 4: Every write selects through its family

Spec §7 ("First-time install has a family too") and §8 step 1: "A device with no supporting flasher becomes a reported failure. It does not abort the batch." Task 3 moved the CMake paths onto `select`. This task moves every other write: `fw.flash_all`, `fw.update_all`, the agent's serial, CAN and display `fw.flash` paths, the CLI's `flash` and `update-all`, and first install. `select_for` goes.

**Files:**
- Modify: `src/mcu_updater/flashers/registry.py` (`select_device`, `refusal`, `select_each`; delete `select_for` and its section banner; drop the `UnsupportedChipsetError` import), `src/mcu_updater/flashers/__init__.py`
- Modify: `src/mcu_updater/flashers/batch.py:59-142` (`write_all`: `refused`, `errors`, the `on_ready` guard)
- Modify: `src/mcu_updater/flashers/flash.py:913-981` (`flash_initial_bootloader`)
- Modify: `src/mcu_updater/agent/methods/bulk.py:25-46` (`_board_target` → `_board_request`), `:280-324` (`_screens_to_flash`), `:387-403` (`_do_flash_all`), `:504-583` (`flash_all`), `:635-665` (`update_all`'s `run`)
- Modify: `src/mcu_updater/agent/methods/_api.py:102-104` (`_do_flash_all` stub)
- Modify: `src/mcu_updater/agent/methods/flash.py:23-182` (`flash`, serial path), `:303-410` (`_flash_can`), `:412-506` (`_pio_flash`)
- Modify: `src/mcu_updater/cli.py:547-592` (`_board_targets`, `_canbus_targets`), `:648-717` (`_pio_targets`), `:754-776` (`_run_batch`), `:779-895` (`flash_fw_cmd`), `:951-975` (`update_all`)
- Test: `tests/test_flasher_select.py`, `tests/test_flash.py:759-936`, `tests/test_agent_bulk.py`, `tests/test_agent_flash.py`, `tests/test_agent_flash_can.py:375-395`, `tests/test_cli.py`
- Create: `scripts/mutations/batch-selection.json`
- Delete: `scripts/mutations/flasher-selection.json`
- Modify: `scripts/mutations/bootsel-erase.json`, `scripts/mutations/flash-offset-diagnostic.json`
- Docs: `docs/agent-api.md`, `docs/decisions.md`
- Mutation specs at risk: `bootsel-erase.json` ("BOOTSEL copies the erasing UF2" anchors the `bootsel.target_for(staged, ...)` line this task removes: re-anchor), `flash-offset-diagnostic.json` ("a whole-type or update-all batch never carries force" anchors `_board_targets`' dict indentation, which changes: re-anchor), `flasher-selection.json` (anchors `select_for`: delete; `flasher-supports.json` from Task 3 already covers chipset and state matching), `bulk-operations.json` (anchors `bulk.py`'s selection and `batch.py`'s `settled` call, neither rewritten: run it), `display-flash.json` (anchors `_pio_flash`'s `if not targets:` and idle gate, kept verbatim: run it), `add-mcu.json`, `dfu-pairings.json` (anchor other methods of `agent/methods/flash.py`: run them), `cli-every-type.json`, `single-write-path.json` (anchor other functions in `cli.py`: run them). Run `rg -n "target_for|_board_target|select_for|write_all|staged" scripts/mutations` before editing and re-anchor every hit on a line this task changes.

**Interfaces:**
- Consumes (Task 3): `flashers.Device`, `KIND_SERIAL`, `KIND_CANBUS`, `KIND_SCREEN`, `KIND_BARE`, `flashers.resolve(family, device, helper)`, `flashers.select(paths, family, device, helper, *, stop_services)`, `errors.NoFlasherError`, `Esptool.target` merging `device.detail` into the target's detail. (Task 1): `helpers.for_name(name, *, family)`, `FirmwareFamily.flashers`.
- Produces:
  - `flashers.select_device(paths: Paths, families: dict[str, FirmwareFamily], device: Device, *, stop_services: tuple[str, ...]) -> FlashTarget`: resolves `device.fw` and its helper, then `select`. Raises `ConfigCorruptError` (undeclared family) or `NoFlasherError`.
  - `flashers.refusal(device: Device, exc: NoFlasherError) -> dict[str, Any]`, which returns `{"type", "id", "flasher": None, "error"}`
  - `flashers.select_each(paths: Paths, families: dict[str, FirmwareFamily], requests: Iterable[tuple[Device, tuple[str, ...]]]) -> tuple[list[FlashTarget], list[dict[str, Any]]]`
  - `flashers.write_all(bench, targets, ctx, *, on_ready=None, refused: Sequence[Mapping[str, Any]] = (), errors: list[UpdaterError] | None = None)`. Refusals are warned and put first in `failures`. Each caught write error is appended to `errors` when given. `on_ready` runs only when `targets` is non-empty.
  - `bulk._board_request(board: dict) -> tuple[flashers.Device, tuple[str, ...]]` (replaces `_board_target`)
  - `BulkMixin._screens_to_flash(scope, only=None) -> tuple[list[FlashTarget], list[dict[str, Any]]]`
  - `BulkMixin._do_flash_all(ctx, targets, refused=())`
  - `cli._board_targets(c, mcu_type, serials, *, force=False)`, `cli._canbus_targets(c, mcu_type, uuids)`, `cli._pio_targets(c, name, only_id=None, *, allow_discovery=False)`: each returns `(targets, refused)`
  - `cli._run_batch(c, targets, label, refused=())`
  - Removed: `flashers.select_for`, `bulk._board_target`

Decisions this task makes:

- **The serial `fw.flash` job re-raises the write's own error.** Its old hand-written stop/write/wait let `flash_katapult`'s exception out unchanged, so a panel could switch on `offset_mismatch` or `device_not_found`. `write_all` turns failures into strings, so the serial path passes `errors=[]` and raises `errors[0]`. The CAN and CMake paths keep wrapping in `FlashError` as they do today. Changing their codes is not this task.
- **Units resolve at submission for the serial path**, as they already do for CAN and CMake. The old path read `stop_services.for_mcu` inside the job.
- **A refused screen is absent from `fw.flash_all`'s `displays`**, because that list is projected from targets. It appears in the job's `failures[]`. A refused board stays in `boards`, which is the selection, and also appears in `failures[]`.
- **`on_ready` is skipped when nothing was written.** A batch of only refusals stopped nothing, so there is no restart to confirm.
- **A device's selection state:** by-id boards use their scanned state (`device_state`, or the `find_device` result). CAN boards use `"klipper"` when the cross-reference reports a version, else `"unknown"` (the CLI always says `"unknown"`). Screens use `inventory.STATE_UNKNOWN`. Bare boards use `bootsel` or `dfu`. Flashtool and esptool do not refuse on state (Task 3), so this changes no outcome today. It is what a `NoFlasherError` message reports.

- [ ] **Step 1: Write the failing batch-selection tests**

Append to `tests/test_flasher_select.py` (add `from mcu_updater.errors import ConfigCorruptError` and `from mcu_updater.settings import Settings` to its imports):

```python
# --- a batch ------------------------------------------------------------------


def _board_device(fw: str = "klipper") -> Device:
    return Device(
        type="ebb36",
        id="usb-Klipper_stm32g0b1xx_1-if00",
        chipset="stm32g0b1xx",
        state=STATE_KLIPPER,
        fw=fw,
        detail={
            "type": "ebb36",
            "serial": "usb-Klipper_stm32g0b1xx_1-if00",
            "chipset": "stm32g0b1xx",
            "fw": fw,
        },
    )


def _screen_device(fw: str = "knomi") -> Device:
    return Device(
        type="knomi",
        id="/dev/ttyKNOMI",
        chipset="",
        state="unknown",
        fw=fw,
        kind=KIND_SCREEN,
        detail={},
    )


def test_a_batch_refuses_one_device_without_dropping_the_rest(paths):
    """Spec §8 step 1. The refused screen comes first, so a refusal that raised
    would lose the board after it."""
    families = {
        "klipper": _family("flashtool", name="klipper"),
        "knomi": _family("dfu_util", name="knomi"),
    }

    targets, refused = flashers.select_each(
        paths,
        families,
        [(_screen_device(), ("klipper",)), (_board_device(), ("klipper", "moonraker"))],
    )

    assert [t.id for t in targets] == ["usb-Klipper_stm32g0b1xx_1-if00"]
    assert targets[0].flasher == "flashtool"
    assert targets[0].stop_services == ("klipper", "moonraker")
    [entry] = refused
    assert entry["type"] == "knomi"
    assert entry["id"] == "/dev/ttyKNOMI"
    assert entry["flasher"] is None
    # Its own family, not whichever the batch happened to resolve first.
    assert "[firmware knomi] (flashers: dfu_util)" in entry["error"]


def test_a_device_whose_family_is_undeclared_is_a_config_error(paths):
    with pytest.raises(ConfigCorruptError):
        flashers.select_device(
            paths, {}, _board_device(fw="nowhere"), stop_services=()
        )


def _no_controller(name=None):
    raise AssertionError(f"nothing was written, so nothing should stop {name!r}")


def test_a_refusal_is_reported_with_the_failures_and_nothing_waits(paths):
    """A refusal is warned and listed in `failures[]` like a failed write. A batch of
    nothing but refusals stopped nothing, so it has no restart to wait on."""
    lines: list[tuple[str, str]] = []
    ready: list = []
    entry = {
        "type": "ebb36",
        "id": "usb-x",
        "flasher": None,
        "error": "nothing in [firmware klipper] (flashers: esptool) can write it",
    }
    bench = flashers.Bench(paths=paths, settings=Settings(), controller=_no_controller)

    result = flashers.write_all(
        bench,
        [],
        flashers.PlainContext(lambda stream, line: lines.append((stream, line))),
        on_ready=ready.append,
        refused=[entry],
    )

    assert result == {"flashed": [], "failures": [entry]}
    assert ("warn", f"usb-x: {entry['error']}") in lines
    assert ready == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_flasher_select.py -q -k "batch or undeclared or refusal"`
Expected: FAIL with `AttributeError: module 'mcu_updater.flashers' has no attribute 'select_each'` (and `select_device`), and `TypeError: write_all() got an unexpected keyword argument 'refused'`.

- [ ] **Step 3: `select_device`, `refusal`, `select_each`; delete `select_for`**

In `src/mcu_updater/flashers/registry.py`:

1. Imports become:

```python
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..errors import NoFlasherError
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import Device, Flasher, FlashTarget

if TYPE_CHECKING:
    from ..firmware import FirmwareFamily
    from ..helpers.spec import Helper
    from ..paths import Paths
```

2. Delete `select_for` whole.
3. Append after `select`:

```python
def select_device(
    paths: Paths,
    families: dict[str, FirmwareFamily],
    device: Device,
    *,
    stop_services: tuple[str, ...],
) -> FlashTarget:
    """`select`, for a caller holding a device rather than its family.

    `device.fw` names the family and the family names the helper, so every
    caller asks the same two questions in the same order. Raises
    `ConfigCorruptError` for an undeclared family and `NoFlasherError` as
    `select` does.
    """
    from .. import firmware, helpers

    family = firmware.resolve(paths, device.fw, families)
    helper = helpers.for_name(family.helper, family=family.name)
    return select(paths, family, device, helper, stop_services=stop_services)


def refusal(device: Device, exc: NoFlasherError) -> dict[str, Any]:
    """A device nothing could write, in a batch's `failures[]` shape.

    The uniform slots `FlashTarget.to_json` has, with no flasher because none
    was chosen, and the refusal's own sentence as the error.
    """
    return {"type": device.type, "id": device.id, "flasher": None, "error": str(exc)}


def select_each(
    paths: Paths,
    families: dict[str, FirmwareFamily],
    requests: Iterable[tuple[Device, tuple[str, ...]]],
) -> tuple[list[FlashTarget], list[dict[str, Any]]]:
    """Select a batch: (targets, refusals), each in request order.

    A device nothing can write is a refusal, not an exception. Spec §8: it is
    reported with the batch's failures and does not abort the rest. Hand the
    refusals to `write_all(refused=...)`.
    """
    targets: list[FlashTarget] = []
    refused: list[dict[str, Any]] = []
    for device, units in requests:
        try:
            targets.append(
                select_device(paths, families, device, stop_services=units)
            )
        except NoFlasherError as exc:
            refused.append(refusal(device, exc))
    return targets, refused
```

In `src/mcu_updater/flashers/__init__.py`: drop `select_for` from the import and `__all__`. Import `refusal`, `select_device` and `select_each` from `.registry` and add them to `__all__` in sorted order.

- [ ] **Step 4: `write_all` takes refusals and an error collector**

In `src/mcu_updater/flashers/batch.py`:

1. `from collections.abc import Callable` becomes `from collections.abc import Callable, Mapping, Sequence`.
2. The signature becomes:

```python
def write_all(
    bench: Bench,
    targets: list[FlashTarget],
    ctx: Any,
    *,
    on_ready: ReadyCheck | None = None,
    refused: Sequence[Mapping[str, Any]] = (),
    errors: list[UpdaterError] | None = None,
) -> dict[str, Any]:
```

3. Add to the docstring, after the cancellation paragraph:

```text
    `refused` is what selection could not give a flasher (`select_each`). Each
    entry is warned and listed first in `failures`, so a batch never drops a
    device without saying why. `errors`, when given, collects each write's
    exception as raised: a single-device job re-raises it with its own code,
    which a `failures` string has lost.
```

4. Replace `failures: list[dict[str, Any]] = []` with:

```python
    failures: list[dict[str, Any]] = []
    for entry in refused:
        ctx.reporter("warn", f"{entry['id'] or entry['type']}: {entry['error']}")
        failures.append(dict(entry))
```

5. In the write's `except UpdaterError as exc:` block, after `failures.append(...)` and before `continue`:

```python
                        if errors is not None:
                            errors.append(exc)
```

6. Replace the `if on_ready is not None:` guard with `if on_ready is not None and targets:` (same indentation), and extend its comment's first line to read `# Nothing written means nothing was stopped; otherwise services_stopped`.

- [ ] **Step 5: Run the batch-selection tests**

Run: `python -m pytest tests/test_flasher_select.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing caller tests**

`tests/test_flash.py`: add `seed_base_firmwares(paths)` as the first line of each first-install test that passes `paths` through: `test_stm32_dispatches_to_dfu`, `test_rp2040_dispatches_to_bootsel_when_a_uf2_was_built`, `test_bootsel_copies_katapult_with_the_application_sector_erased`, `test_bootsel_refuses_without_an_application_address`, `test_bootsel_refuses_with_no_katapult_config`, `test_bootsel_refuses_a_uf2_it_cannot_extend`, `test_bootsel_reports_a_missing_uf2_as_a_flash_error`, `test_rp2040_refuses_with_no_uf2_built`, `test_an_unknown_chipset_is_reported_clearly`. (`seed_base_firmwares` is already imported.) Delete the `select_for` banner comment and the five `test_select_for_*` tests. Add after `test_an_unknown_chipset_is_reported_clearly`:

```python
def test_first_install_writes_only_with_what_katapult_lists(paths, settings, tmp_path):
    """A bare board's flasher comes from [firmware katapult]'s `flashers:`.
    A katapult listing only dfu_util has nothing that writes an RP2040 in
    BOOTSEL, and refuses it the way an unknown chipset is refused."""
    seed_base_firmwares(paths)
    with open(paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert "flashers: dfu_util, bootsel" in text
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("flashers: dfu_util, bootsel", "flashers: dfu_util"))
    uf2, cfg = _katapult_uf2(tmp_path)

    with pytest.raises(UnsupportedChipsetError) as exc:
        flash_initial_bootloader(
            paths, settings, "rp2040", "unused.bin", uf2_bin=uf2, katapult_config=cfg
        )
    assert exc.value.data["chipset"] == "rp2040"
```

`tests/test_agent_bulk.py`, after `test_a_batch_stops_klipper_once_not_once_per_board`:

```python
def _set_klipper_flashers(paths, value: str) -> None:
    block = "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n"
    with open(paths.registry_file, encoding="utf-8") as fh:
        text = fh.read()
    assert block in text
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(text.replace(block, block.replace("flashtool", value)))


def test_a_board_its_family_cannot_write_is_a_failure_not_an_abort(
    bulk, paths, fake_root, monkeypatch
):
    """Spec §8 step 1. Selection goes through the family's `flashers:`. A board
    that nothing in the list can write shows up in the job's `failures[]` with
    no flasher. It is not dropped, and Klipper is not stopped for it."""
    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)
    _set_klipper_flashers(paths, "dfu_util")
    _stage_artifact(paths, EBB)
    make_device(fake_root / "bus", "Klipper", EBB_CHIPSET, EBB_A)
    bulk._call = _moonraker({EBB_A: OLD_VERSION})
    monkey_head(bulk, paths)
    waited: list = []
    bulk._await_klippy_ready = waited.append

    res = bulk.dispatch("fw.flash_all", {})
    assert [b["serial"] for b in res["boards"]] == [EBB_A]
    assert bulk.runner.wait(timeout=60)

    job = bulk.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error
    assert job.result["flashed"] == []
    [failure] = job.result["failures"]
    assert (failure["type"], failure["id"], failure["flasher"]) == (EBB, EBB_A, None)
    assert "[firmware klipper] (flashers: dfu_util)" in failure["error"]
    assert svc.actions == [], "nothing to write, so nothing to stop"
    assert waited == []
```

`tests/test_agent_flash.py`, after `test_a_flash_stops_klipper_flashes_then_starts_it_again`:

```python
def test_a_board_its_family_cannot_write_refuses_before_a_job(flashable, paths):
    block = "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n"
    with open(paths.registry_file, encoding="utf-8") as fh:
        text = fh.read()
    assert block in text
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(text.replace(block, block.replace("flashtool", "esptool")))

    with pytest.raises(RpcError) as exc:
        flashable.dispatch("fw.flash", {"serial": TRACKED_SERIAL})

    assert exc.value.data["code"] == "no_flasher"
    assert flashable.runner.current() is None


def test_a_failed_serial_write_keeps_its_own_error_code(flashable, monkeypatch):
    """The serial path writes through the batch loop, and a batch reports
    failures as strings. The job re-raises the write's own error, so a panel
    switching on `device_not_found` or `offset_mismatch` still can."""
    import mcu_updater.flashers.flash as flash_mod
    from mcu_updater.errors import DeviceNotFoundError

    def gone(*args, **kwargs):
        raise DeviceNotFoundError("the board vanished mid-write", serial=TRACKED_SERIAL)

    monkeypatch.setattr(flash_mod, "flash_katapult", gone)

    res = flashable.dispatch("fw.flash", {"serial": TRACKED_SERIAL})
    assert flashable.runner.wait(timeout=30)

    job = flashable.runner.get(res["job_id"])
    assert job.state == "failed"
    assert job.error["code"] == "device_not_found"
    assert job.error["data"] == {"serial": TRACKED_SERIAL}
```

(The second test passes before this task too. It guards the move.)

`tests/test_agent_flash_can.py`, `test_flash_all_selection_includes_both_serial_and_canbus_boards`: replace the docstring's first line with

```text
"""`_board_request` must route each dict shape to the right flasher -
```

and replace the import and the `targets = ...` line with:

```python
    from mcu_updater import firmware, flashers
    from mcu_updater.agent.methods.bulk import _board_request

    targets, refused = flashers.select_each(
        paths, firmware.load(paths), [_board_request(b) for b in boards]
    )
    assert refused == []
```

`tests/test_cli.py`, after `test_a_single_device_flash_defaults_to_not_forced`:

```python
def test_a_board_its_family_cannot_write_is_reported_not_written(c, monkeypatch):
    """The CLI selects through the family's `flashers:` too. A board that
    nothing in the list can write reaches the batch as a refusal, and the
    command exits non-zero."""
    with open(c.paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("flashers: flashtool") == 1
    with open(c.paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("flashers: flashtool", "flashers: esptool"))
    written: list = []
    refusals: list = []

    def fake(bench, targets, ctx, *, refused=(), **kwargs):
        written.extend(targets)
        refusals.extend(refused)
        return {"flashed": [], "failures": list(refused)}

    monkeypatch.setattr(flashers, "write_all", fake)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type="board", serial=None, yes=True))

    assert exc.value.code == 1
    assert written == []
    assert [r["id"] for r in refusals] == ["AAAA-if00"]
    assert "[firmware klipper]" in refusals[0]["error"]
```

- [ ] **Step 7: Run them to verify they fail**

Run: `python -m pytest tests/test_flash.py tests/test_agent_bulk.py tests/test_agent_flash.py tests/test_agent_flash_can.py tests/test_cli.py -q -k "katapult_lists or cannot_write or keeps_its_own or both_serial_and_canbus"`
Expected: FAIL. The first-install test raises something other than `UnsupportedChipsetError`, the three `cannot_write` tests flash with flashtool anyway, and the CAN test cannot import `_board_request`. `keeps_its_own_error_code` passes.

- [ ] **Step 8: First install through the katapult family**

In `src/mcu_updater/flashers/flash.py`:

1. Add `import dataclasses` to the stdlib imports and `UnsupportedChipsetError` to the `from ..errors import (...)` block.
2. In `flash_initial_bootloader`'s docstring, replace the sentence starting "`flashers.select_for` is the actual dispatch" through "when it lands." with: "What goes on the board is katapult, so `[firmware katapult]`'s `flashers:` list picks the writer, through the same `Flasher` protocol a batch uses."
3. Replace from the `from .. import flashers` line through the end of the `else:` branch that builds the dfu target with:

```python
    from .. import flashers

    state = STATE_BOOTSEL if chipset.startswith("rp2040") else STATE_DFU
    katapult = firmware.resolve(paths, "katapult")
    device = flashers.Device(
        type=chipset,
        id=target_serial or "",
        chipset=chipset,
        state=state,
        fw=katapult.name,
        kind=flashers.KIND_BARE,
        detail={"fw_bin": fw_bin},
    )
    flasher = flashers.resolve(katapult, device, None)
    if flasher is None:
        raise UnsupportedChipsetError(
            f"don't know how to perform a first-time flash for chipset '{chipset}'. "
            f"Flash katapult manually, then use 'add-serial' once it enumerates.",
            chipset=chipset,
        )

    with tempfile.TemporaryDirectory(prefix="mcu-updater-bootsel-") as staging:
        if state == STATE_BOOTSEL:
            if uf2_bin is None:
                raise FlashError(
                    f"no .uf2 was built for {chipset}. BOOTSEL mass storage ignores "
                    f"a .bin - build again once the tree produces one.",
                    chipset=chipset,
                )
            staged = _stage_erasing_uf2(uf2_bin, katapult_config, staging, chipset)
            reporter(
                "info",
                "Staged Katapult with the application sector erased, so the board "
                "cannot chain-load whatever it ran before.",
            )
            device = dataclasses.replace(
                device, detail={**device.detail, "uf2_file": staged}
            )
        target = flasher.target(paths, device, None, stop_services=())
```

The `bench = ...` statement and the `with flasher.prepared(...)` block after it stay as they are.

- [ ] **Step 9: The agent's bulk selection**

In `src/mcu_updater/agent/methods/bulk.py`:

1. `from ... import firmware, flashers, providers, stop_services` becomes `from ... import firmware, flashers, inventory, providers, stop_services`.
2. Replace `_board_target` whole with:

```python
def _board_request(board: dict) -> tuple[flashers.Device, tuple[str, ...]]:
    """One entry from `_boards_to_flash`/`_canbus_boards_to_flash`, as a
    device for `flashers.select_each`, with its resolved stop list.

    The dict rides whole as `detail`: its shape is on the wire (`fw.flash_all`
    returns it) and flashtool's target is built from it. Branches on which
    identity key the dict carries, `uuid` or `serial`, since the two selections
    never overlap for one board.
    """
    can = "uuid" in board
    return (
        flashers.Device(
            type=board["type"],
            id=board["uuid"] if can else board["serial"],
            chipset=board["chipset"],
            state=board["state"],
            fw=board["fw"],
            kind=flashers.KIND_CANBUS if can else flashers.KIND_SERIAL,
            detail=board,
        ),
        tuple(board.get("stop_services") or ()),
    )
```

3. `_screens_to_flash`: the return annotation becomes `tuple[list[flashers.FlashTarget], list[dict[str, Any]]]`. Add to its docstring: "Returns `(targets, refused)` from `flashers.select_each`: a screen its family cannot write is a refusal for the batch to report." Replace the body from `known = self.pio_types()` to the end with:

```python
        known = self.pio_types()
        settings = self.settings()
        families = firmware.load(self.paths)
        requests: list[tuple[flashers.Device, tuple[str, ...]]] = []
        for payload in self.pio_status():
            if only is not None and payload["name"] != only:
                continue
            if not payload["has_firmware"]:
                continue
            # The live object, not one rebuilt from the payload: `to_json` is a
            # wire projection and reversing it is the thing this codebase keeps
            # deciding not to do.
            display = known[payload["name"]]
            units = stop_services.for_platformio(self.paths, display, settings, families)
            for screen in payload["screens"]:
                if not screen["present"]:
                    continue
                status = self._platformio_device_status(screen)
                if scope != "all" and status.needs_flash is not True:
                    continue
                requests.append(
                    (
                        flashers.Device(
                            type=display.name,
                            id=screen["configured_path"],
                            chipset="",
                            state=inventory.STATE_UNKNOWN,
                            fw=display.firmware,
                            kind=flashers.KIND_SCREEN,
                            detail={
                                "display": display,
                                "screen": screen,
                                "reason": "forced" if scope == "all" else status.reason,
                            },
                        ),
                        units,
                    )
                )
        return flashers.select_each(self.paths, families, requests)
```

Remove `import dataclasses` from bulk.py if ruff reports it unused.

4. `_do_flash_all` becomes:

```python
    def _do_flash_all(
        self,
        ctx: Any,
        targets: list[flashers.FlashTarget],
        refused: list[dict[str, Any]] | tuple[()] = (),
    ) -> dict[str, Any]:
```

Its `write_all` call gains `refused=refused,` after `on_ready=self._await_klippy_ready,`. Make the same signature change to the stub in `src/mcu_updater/agent/methods/_api.py`.

5. In `flash_all`, replace `screens = self._screens_to_flash(scope, only)` and the `if not boards and not screens:` line with:

```python
        screens, screens_refused = self._screens_to_flash(scope, only)
        if not boards and not screens and not screens_refused:
```

Replace `targets = [_board_target(b) for b in boards] + screens` and the `run` definition with:

```python
        board_targets, refused = flashers.select_each(
            self.paths, firmware.load(self.paths), [_board_request(b) for b in boards]
        )
        targets = board_targets + screens
        refused += screens_refused

        def run(ctx) -> dict[str, Any]:
            return self._do_flash_all(ctx, targets, refused=refused)
```

Add to the `displays` comment: `# A screen its family cannot write is not here; it is in the job's failures[].`

6. In `update_all`'s `run`, replace the `devices = ...` statement and the `if not devices:` block with:

```python
            board_targets, refused = flashers.select_each(
                self.paths,
                firmware.load(self.paths),
                [_board_request(b) for b in boards],
            )
            screens, screens_refused = self._screens_to_flash(scope, only)
            devices = board_targets + screens
            refused += screens_refused
            if not devices and not refused:
                ctx.reporter("info", "No device needs flashing.")
                return {"build": build_result, "flash": {"flashed": [], "failures": []}}
```

and its final `return` becomes `return {"build": build_result, "flash": self._do_flash_all(ctx, devices, refused=refused)}`.

- [ ] **Step 10: The agent's single-device paths**

In `src/mcu_updater/agent/methods/flash.py`, `flash` (the serial path):

1. Docstring: "In order: capability gate, argument validation, type/serial pairing, artifact present, board actually attached, and finally the print gate." becomes "In order: capability gate, argument validation, type/serial pairing, artifact present, board actually attached, the family able to write it, and finally the print gate."
2. Replace `application = mcu.application(firmware.load(self.paths))` with:

```python
        families = firmware.load(self.paths)
        application = mcu.application(families)
```

3. Replace `if find_device(self.paths, mcu.chipset, serial) is None:` with

```python
        present = find_device(self.paths, mcu.chipset, serial)
        if present is None:
```

keeping the `RpcError` body.
4. After that block and before `# Last gate.`, add:

```python
        # The family decides who writes this board, before a job exists.
        target = flashers.select_device(
            self.paths,
            families,
            flashers.Device(
                type=mcu_type,
                id=serial,
                chipset=mcu.chipset,
                state=present.state,
                fw=application,
                detail={
                    "type": mcu_type,
                    "serial": serial,
                    "chipset": mcu.chipset,
                    "fw": application,
                    "force": force,
                },
            ),
            stop_services=stop_services.for_mcu(self.paths, mcu, settings, families),
        )
```

5. Replace the whole `def run(ctx)` body with:

```python
        def run(ctx) -> dict[str, Any]:
            # The batch loop, for one target: it stops the units, writes, waits
            # for the board to come back and restarts them. No cancel is
            # threaded into the write - interrupting flashtool leaves half an
            # image on the board.
            state_holder: dict[str, Any] = {}

            def on_ready(reporter: Any) -> None:
                state_holder["klippy_state"] = self._await_klippy_ready(reporter)

            errors: list[UpdaterError] = []
            flashers.write_all(
                self._bench(self.settings()),
                [target],
                ctx,
                on_ready=on_ready,
                errors=errors,
            )
            if errors:
                raise errors[0]
            return {
                "type": mcu_type,
                "serial": serial,
                "fw_bin": fw_bin,
                "klippy_state": state_holder.get("klippy_state"),
            }
```

Remove `REENUMERATE_TIMEOUT` from the imports if ruff reports it unused.

`_flash_can`:

1. `application = mcu.application(firmware.load(self.paths))` becomes `families = firmware.load(self.paths)` then `application = mcu.application(families)`.
2. Replace `units = ...` and `target = flashers.flashtool.target_for(...)` with:

```python
        board = {
            "type": mcu_type,
            "uuid": uuid,
            "chipset": mcu.chipset,
            "fw": application,
            "force": force,
            "bridge": bridge,
            "interface": interface,
        }
        target = flashers.select_device(
            self.paths,
            families,
            flashers.Device(
                type=mcu_type,
                id=uuid,
                chipset=mcu.chipset,
                state=(
                    "klipper"
                    if cross is not None and cross.get("version") is not None
                    else "unknown"
                ),
                fw=application,
                kind=flashers.KIND_CANBUS,
                detail=board,
            ),
            stop_services=stop_services.for_mcu(self.paths, mcu, settings, families),
        )
```

3. Move the `from ...service import assert_printer_idle` / `assert_printer_idle(...)` statement below this block, so a family that cannot write refuses before the print gate, as on the serial path.
4. In the docstring, "Routes through `flashtool.target_for`" becomes "Routes through `flashers.select_device`".

`_pio_flash`: replace the `units = ...` and `screens = [...]` statements with:

```python
        units = stop_services.for_platformio(self.paths, display, settings)
        families = firmware.load(self.paths)
        screens, refused = flashers.select_each(
            self.paths,
            families,
            [
                (
                    flashers.Device(
                        type=display.name,
                        id=s["configured_path"],
                        chipset="",
                        state=inventory.STATE_UNKNOWN,
                        fw=display.firmware,
                        kind=flashers.KIND_SCREEN,
                        detail={"display": display, "screen": s},
                    ),
                    units,
                )
                for s in targets
            ],
        )
```

In `run`, `self._do_flash_all(ctx, screens)` becomes `self._do_flash_all(ctx, screens, refused=refused)`, and `named = {t.id: t.detail["screen"]["name"] for t in screens}` becomes `named = {d["configured_path"]: d["name"] for d in targets}`, so a refused screen is named too. Add `inventory` to the `from ... import` line.

- [ ] **Step 11: The CLI**

In `src/mcu_updater/cli.py`, add `device_state` to the `from .devices import (...)` block. Replace `_board_targets` and `_canbus_targets` whole with:

```python
def _board_targets(
    c: Context, mcu_type: str, serials: list[str], *, force: bool = False
) -> tuple[list, list]:
    """Tracked boards of one kconfig type, selected through their family:
    `(targets, refused)` for `_run_batch`.

    `force` overrides a refused bootloader offset check (flash_katapult's own
    `force` parameter) and defaults off - a caller flashing more than one board
    at a time should never pass it, since a blanket override across a fleet is
    exactly what that check exists to prevent. Single-device `flash --force` is
    the only caller that sets it.
    """
    mcu = c.registry().get(mcu_type)
    families = firmware.load(c.paths)
    application = mcu.application(families)
    units = stop_services.for_mcu(c.paths, mcu, c.settings, families)
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=mcu_type,
                    id=serial,
                    chipset=mcu.chipset,
                    state=device_state(c.paths, mcu.chipset, serial)[0],
                    fw=application,
                    detail={
                        "type": mcu_type,
                        "serial": serial,
                        "chipset": mcu.chipset,
                        "fw": application,
                        "force": force,
                    },
                ),
                units,
            )
            for serial in serials
        ],
    )


def _canbus_targets(c: Context, mcu_type: str, uuids: list[str]) -> tuple[list, list]:
    """CAN-uuid counterpart to `_board_targets`, for a type's `canbus_uuids:`.

    Same resolved `stop_services` as this same type's by-id boards. The CLI
    has no Klipper mapping, so the flasher discovers the current interface at
    write time; `canbus_uuids:` stores no interface, and liveness is unknown.
    """
    mcu = c.registry().get(mcu_type)
    families = firmware.load(c.paths)
    application = mcu.application(families)
    units = stop_services.for_mcu(c.paths, mcu, c.settings, families)
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=mcu_type,
                    id=uuid,
                    chipset=mcu.chipset,
                    state=inventory.STATE_UNKNOWN,
                    fw=application,
                    kind=flashers.KIND_CANBUS,
                    detail={
                        "type": mcu_type,
                        "uuid": uuid,
                        "chipset": mcu.chipset,
                        "fw": application,
                    },
                ),
                units,
            )
            for uuid in uuids
        ],
    )
```

`_pio_targets`: the return annotation becomes `tuple[list, list]`. Add a docstring line: "Returns `(targets, refused)`: the screens go through their family's `flashers:` like any other device." Replace its final `return [...]` with:

```python
    return flashers.select_each(
        c.paths,
        firmware.load(c.paths),
        [
            (
                flashers.Device(
                    type=display.name,
                    id=device.port,
                    chipset="",
                    state=inventory.STATE_UNKNOWN,
                    fw=display.firmware,
                    kind=flashers.KIND_SCREEN,
                    detail={
                        "display": display,
                        "screen": {
                            "name": device.device_id,
                            "section": f"{display.klipper_section} {device.device_id}",
                            "configured_path": device.port,
                            "device_id": device.device_id,
                            "present": device.present,
                        },
                    },
                ),
                units,
            )
            for device in sorted(found.values(), key=lambda d: d.port)
            if device.present
            and (only_id is None or only_id in (device.port, device.device_id))
        ],
    )
```

`_run_batch` becomes:

```python
def _run_batch(c: Context, targets: list, label: str, refused: list | tuple = ()) -> int:
```

Add to its docstring: "`refused` is what selection could not give a flasher. The batch reports each as a failure." The `write_all` call becomes `flashers.write_all(_bench(c), targets, flashers.PlainContext(stdout_reporter), refused=refused)`, and the summary's `{len(targets)}` becomes `{len(targets) + len(refused)}`.

`flash_fw_cmd`:

1. The PlatformIO branch:

```python
                targets, refused = _pio_targets(
                    c, args.type, only_id=args.serial, allow_discovery=True
                )
                if not targets and not refused:
                    print(f"No device is reachable for '{args.type}'.", file=sys.stderr)
                    sys.exit(1)
                code = _run_batch(c, targets, f"flash {args.type}", refused)
```

2. The whole-type branch's `targets = ...` and `code = ...` become:

```python
            boards, refused = _board_targets(c, args.type, mcu.serials)
            can, can_refused = _canbus_targets(c, args.type, mcu.canbus_uuids)
            code = _run_batch(
                c, boards + can, f"flash {args.type}", refused + can_refused
            )
```

3. The final single-device `_run_batch` call becomes:

```python
    with exclusive(c.paths, f"flash {mcu_type}/{args.serial}"):
        targets, refused = _board_targets(c, mcu_type, [args.serial], force=args.force)
        code = _run_batch(c, targets, f"flash {args.serial}", refused)
```

`update_all`, inside `with _ports_free(...)`:

```python
            targets: list = []
            refused: list = []
            for name in sorted(install.registry.names()):
                reg_mcu = install.registry.get(name)
                boards, boards_refused = _board_targets(c, name, reg_mcu.serials)
                can, can_refused = _canbus_targets(c, name, reg_mcu.canbus_uuids)
                targets += boards + can
                refused += boards_refused + can_refused
            for name in sorted(install.platformio):
                try:
                    screens, screens_refused = _pio_targets(c, name, allow_discovery=True)
                except UpdaterError as exc:
                    # Not fatal: the boards are still worth writing, and a host with
                    # no watcher running is a configuration gap rather than a fault.
                    print(f"SKIP {name}: {exc}", file=sys.stderr)
                    failures.append((name, "no devices found"))
                    continue
                targets += screens
                refused += screens_refused

            if not targets and not refused:
                print("\nNothing to write.")
            else:
                result = flashers.write_all(
                    _bench(c),
                    targets,
                    flashers.PlainContext(stdout_reporter),
                    refused=refused,
                )
                for failure in result["failures"]:
                    print(f"ERROR: {failure['id']}: {failure['error']}", file=sys.stderr)
                    failures.append((failure["type"], failure["id"]))
                print(
                    f"\nWrote {len(result['flashed'])} of "
                    f"{len(targets) + len(refused)} device(s)."
                )
```

- [ ] **Step 12: Run the affected tests**

Run: `python -m pytest tests/test_flasher_select.py tests/test_flash.py tests/test_agent_bulk.py tests/test_agent_flash.py tests/test_agent_flash_can.py tests/test_cli.py tests/test_agent_display_jobs.py tests/test_agent_displays.py tests/test_agent_add_mcu.py -q`
Expected: PASS. If a serial-path test reads a job log line the old path wrote itself ("is back as a Klipper device", "Stopping", "Restarting"), assert on the batch loop's own lines ("Flashing <serial> (<type>)", the controller's "would stop"/"would start") instead. The write order the test is about does not change.

Then: `rg -n "select_for|_board_target\b" src tests scripts docs README.md --glob '!docs/superpowers/**'`
Expected: no output.

- [ ] **Step 13: Mutation specs**

Delete `scripts/mutations/flasher-selection.json`.

`scripts/mutations/bootsel-erase.json`, "BOOTSEL copies the erasing UF2, not Katapult alone":

```json
      "find": "                device, detail={**device.detail, \"uf2_file\": staged}",
      "replace": "                device, detail={**device.detail, \"uf2_file\": uf2_bin}"
```

`scripts/mutations/flash-offset-diagnostic.json`, "a whole-type or update-all batch never carries force, only a single device does":

```json
      "find": "                        \"fw\": application,\n                        \"force\": force,",
      "replace": "                        \"fw\": application,\n                        \"force\": True,"
```

Create `scripts/mutations/batch-selection.json`:

```json
{
  "_comment": "Every write selects through its family. A refusal must reach failures[] without aborting the batch, a device must be resolved against its own family, a batch of only refusals must not wait on a restart it never caused, the serial fw.flash job must keep its write's own error code, and first install must refuse what katapult's list cannot write.",
  "file": "src/mcu_updater/flashers/registry.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_flasher_select.py",
    "tests/test_flash.py",
    "tests/test_agent_bulk.py",
    "tests/test_agent_flash.py",
    "tests/test_cli.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a refusal does not abort the batch's selection",
      "find": "        except NoFlasherError as exc:\n            refused.append(refusal(device, exc))",
      "replace": "        except ZeroDivisionError as exc:\n            refused.append(refusal(device, exc))"
    },
    {
      "name": "each device resolves its own family",
      "find": "    family = firmware.resolve(paths, device.fw, families)",
      "replace": "    family = next(iter(families.values()))"
    },
    {
      "name": "refusals are reported with the failures",
      "file": "src/mcu_updater/flashers/batch.py",
      "find": "    for entry in refused:",
      "replace": "    for entry in ():"
    },
    {
      "name": "nothing written means nothing to wait for",
      "file": "src/mcu_updater/flashers/batch.py",
      "find": "    if on_ready is not None and targets:",
      "replace": "    if on_ready is not None:"
    },
    {
      "name": "a write's own error is collected for a single-device job",
      "file": "src/mcu_updater/flashers/batch.py",
      "find": "                        if errors is not None:\n                            errors.append(exc)",
      "replace": "                        if False:\n                            errors.append(exc)"
    },
    {
      "name": "the serial fw.flash job fails with its write's error",
      "file": "src/mcu_updater/agent/methods/flash.py",
      "find": "            if errors:\n                raise errors[0]",
      "replace": "            if False:\n                raise errors[0]"
    },
    {
      "name": "flash_all hands its refusals to the batch",
      "file": "src/mcu_updater/agent/methods/bulk.py",
      "find": "            return self._do_flash_all(ctx, targets, refused=refused)",
      "replace": "            return self._do_flash_all(ctx, targets)"
    },
    {
      "name": "the CLI hands its refusals to the batch",
      "file": "src/mcu_updater/cli.py",
      "find": "        _bench(c), targets, flashers.PlainContext(stdout_reporter), refused=refused",
      "replace": "        _bench(c), targets, flashers.PlainContext(stdout_reporter)"
    },
    {
      "name": "first install refuses what katapult's list cannot write",
      "file": "src/mcu_updater/flashers/flash.py",
      "find": "    if flasher is None:\n        raise UnsupportedChipsetError(",
      "replace": "    if False:\n        raise UnsupportedChipsetError("
    }
  ]
}
```

If ruff reformats `_run_batch`'s `write_all` call onto several lines, re-anchor "the CLI hands its refusals to the batch" to the `refused=refused` line as it ends up.

Run each, one at a time, and wait for each to finish:

```bash
python scripts/mutation_test.py scripts/mutations/batch-selection.json
python scripts/mutation_test.py scripts/mutations/bootsel-erase.json
python scripts/mutation_test.py scripts/mutations/flash-offset-diagnostic.json
python scripts/mutation_test.py scripts/mutations/flasher-supports.json
python scripts/mutation_test.py scripts/mutations/bulk-operations.json
python scripts/mutation_test.py scripts/mutations/display-flash.json
python scripts/mutation_test.py scripts/mutations/add-mcu.json
python scripts/mutation_test.py scripts/mutations/dfu-pairings.json
python scripts/mutation_test.py scripts/mutations/cli-every-type.json
python scripts/mutation_test.py scripts/mutations/single-write-path.json
```

Expected: every mutation killed.

- [ ] **Step 14: Docs**

`docs/agent-api.md`, `fw.flash`'s refusal table: add a row between "board is on the bus" and "printer idle":

```markdown
| the family's `flashers:` can write it | `no_flasher` |
```

After the table, add: "A failed write fails the job with the write's own error (`offset_mismatch`, `device_not_found`, `tool_missing`, …), as it always has."

In "Bulk operations", after the paragraph ending "the confirmation is not, because a human reading it wants the real names.", add:

````markdown
Selection goes through each device's `[firmware]` `flashers:` list. A device
that nothing in the list can write is not dropped, and it does not stop the
batch. It appears in the job's `failures[]` with `"flasher": null` and an
`error` naming the family and its list:

```json
{"type": "bttebb36", "id": "2900...", "flasher": null,
 "error": "nothing in [firmware klipper] (flashers: dfu_util) can write bttebb36 2900... while it is klipper."}
```

A refused board is still listed in `boards`. A refused screen is not listed in
`displays`. A batch made only of refusals stops no service.
````

In "Flashing a display", where the job result's `failures[]` is described, add: "A screen its family's `flashers:` cannot write is listed here too, with the refusal as its `error`."

`docs/decisions.md`, after Task 3's "### A family's list picks the flasher":

```markdown
### A device nothing can write is a failure, not an abort

Spec §8 step 1. `flashers.select_each` turns a `NoFlasherError` into a
`failures[]` entry with `"flasher": null`, and `write_all` reports it with the
writes that failed. A single-device RPC raises instead, before a job exists.
First install asks `[firmware katapult]` the same question and keeps its
`unsupported_chipset` refusal. The serial `fw.flash` job collects its write's
exception (`write_all(errors=...)`) and re-raises it, because the job's error
code was already on the wire.
```

- [ ] **Step 15: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass.

```bash
git add -A src/mcu_updater tests scripts/mutations docs/agent-api.md docs/decisions.md
git commit -m "feat(flashers): every write selects through its family

fw.flash_all, fw.update_all, the serial, CAN and display fw.flash paths, the
CLI's flash and update-all, and first install all pick their flasher from the
device's [firmware] flashers: list. A device nothing in the list can write is a
failures[] entry with flasher null, not an abort. select_for is gone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `FlashLog` is written by the loop

Ruling 12 and spec §7. Four places write the ledger today — `flash_katapult`, `flash_katapult_can`, `esptool._record` and the agent's `_cmake_flash` — with four copies of the dry-run guard and four ideas of where the provenance comes from. `_cmake_flash`'s copy sits after the batch returns, which is why `docs/agent-api.md:694` has to admit that a failed service restart loses the record of a completed copy. After this task a flasher only *describes* its record; `write_all` writes it, right after the write and before `settled` or anything later can raise.

**Files:**
- Modify: `src/mcu_updater/flashers/spec.py` (`FlashRecord`; `Flasher.record`; `Flasher.write`'s docstring)
- Modify: `src/mcu_updater/flashers/batch.py:95-115` (`write_all`'s per-target body)
- Modify: `src/mcu_updater/flashers/flashtool.py` (`record`), `src/mcu_updater/flashers/esptool.py` (`record`; `_record` deleted), `src/mcu_updater/flashers/bootsel.py` (`record`), `src/mcu_updater/flashers/dfu_util.py` (`record`)
- Modify: `src/mcu_updater/flashers/flash.py:186-260` (`flash_katapult` returns its confidence and stops recording), `:625-695` (`flash_katapult_can` the same)
- Modify: `src/mcu_updater/agent/methods/flash.py:255-290` (`_cmake_flash`'s record block deleted)
- Modify: `src/mcu_updater/build.py:884-907` (`FlashLog.record`'s docstring names its one writer)
- Test: `tests/test_flashlog_loop.py` (create), `tests/test_flash.py:485-530`, `:1057-1080`, `tests/test_flash_can.py:350-362`, `tests/test_agent_flash.py:390-505`
- Create: `scripts/mutations/flashlog-loop.json`
- Docs: `docs/agent-api.md:694`, `docs/decisions.md`
- Mutation specs at risk: `display-flash.json` (anchors `_record`'s `if bench.settings.dry_run:` guard and `status.py`'s `entry_for` call — the first line is deleted here, so re-anchor that mutation onto the loop's guard or onto `Esptool.record`'s ident check in this same commit; the `status.py` one is untouched), `bulk-operations.json`, `single-write-path.json`, `bootsel-erase.json`, `flash-offset-diagnostic.json` (anchor lines in `batch.py`, `cli.py` and `flash.py` that this task does not rewrite: run them). Run `rg -n "_record|FlashLog|confidence" scripts/mutations` before editing and re-anchor every hit on a line this task changes.

**Interfaces:**
- Consumes (Task 3): `Flasher.supports`/`target`, `Bench`. (Task 4): `write_all(bench, targets, ctx, *, on_ready=None, refused=(), errors=None)`, every single-device path already going through it.
- Produces:
  - `flashers.FlashRecord(key: str, mcu_type: str, fw: str, bin_sha256: str | None, fw_sha: str | None, version: str | None = None)`, frozen. `key` is what the record is filed under: a serial, a CAN uuid, or `build.display_key(<hardware id>)`.
  - `Flasher.record(bench: Bench, target: FlashTarget) -> FlashRecord | None`. `None` means "nothing durable to file this under" — a bare board with no tracked serial, a screen with no hardware id, a flasher whose write is not a tracked device's firmware. The dry-run guard is **not** here; `write_all` owns it.
  - `Flasher.write` may return a `"confidence"` key. `write_all` removes it from the `flashed[]` entry and passes it to `FlashLog.record`, so the wire shape of `flashed[]` is unchanged.
  - `flash.flash_katapult(...) -> str | None` and `flash.flash_katapult_can(...) -> str | None`: the `discovery.spec.Confidence.reason` the write confirmed the board by (`"unique_bus_id"`, `"canbus_uuid"`), or `None`.

Decisions this task makes:

- **A flasher reads its own builder's sidecar.** `Flashtool.record` reads `build.read_sidecar` (the kconfig schema: `fw_sha`), `Bootsel.record` reads `providers.cmake.read_sidecar` (the CMake schema: `sha`), `Esptool.record` reads `providers.pio.read_sidecar`. That coupling is not new — `esptool._record` has always done exactly this — and the alternative, a union schema passed through selection, is a third description of a build record to keep in step with the two that exist.
- **`Bootsel.record` returns `None` for a type that is not a CMake type.** First install writes katapult to a bare board whose `type` is a chipset string and whose id may be empty; there is nothing to file and nothing filed one today.
- **`DfuUtil.record` returns `None` always.** dfu-util only ever writes a bootloader to a bare board here. When it grows an application path it gets a record with the rest of that work.
- **No new fields on `FlashLog`.** The verdict (Task 8) compares a reported digest against the *artifact's* sidecar, which is the authority for what the image should look like; the record's `bin_sha256` is what distinguishes "we wrote this artifact" from "we wrote a different one". A digest copied into the record would be a third copy of a fact those two already carry.

- [ ] **Step 1: Write the failing loop tests**

Create `tests/test_flashlog_loop.py`:

```python
"""One writer for the ledger: the batch loop.

Spec section 7 and Ruling 12. A flasher describes what it just wrote; it does
not decide whether to file it, where the file is, or whether this run was a
rehearsal. Those three are the same three decisions every copy of this code
used to make separately.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from mcu_updater import flashers
from mcu_updater.build import FlashLog
from mcu_updater.errors import FlashError
from mcu_updater.settings import Settings


class _Fake:
    """A flasher with nothing behind it but a record and a plan."""

    name = "fake"
    label = "Fake"
    chipsets: tuple[str, ...] = ("",)
    states: tuple[str, ...] = ()
    needs_services_stopped = False

    def __init__(self, record=None, *, fails=(), settled_raises=False, extra=None):
        self.record_value = record
        self.fails = set(fails)
        self.settled_raises = settled_raises
        self.extra = extra or {}
        self.written: list[str] = []

    def supports(self, device, helper) -> bool:
        return True

    def target(self, paths, device, helper, *, stop_services):
        return flashers.FlashTarget(
            flasher=self.name, type=device.type, id=device.id,
            stop_services=stop_services,
        )

    def prepared(self, bench, targets, ctx):
        import contextlib

        return contextlib.nullcontext(None)

    def write(self, bench, session, target, ctx):
        if target.id in self.fails:
            raise FlashError("the write failed", type=target.type, id=target.id)
        self.written.append(target.id)
        return dict(self.extra)

    def record(self, bench, target):
        if self.record_value is None:
            return None
        return dataclasses.replace(self.record_value, key=target.id)

    def settled(self, bench, target, ctx):
        if self.settled_raises:
            raise FlashError("it never came back", type=target.type, id=target.id)


RECORD = flashers.FlashRecord(
    key="",
    mcu_type="ebb36",
    fw="klipper",
    bin_sha256="ab" * 32,
    fw_sha="cafe1234",
    version="v0.12.0-1-gcafe123",
)


@pytest.fixture
def bench(paths, settings):
    return flashers.Bench(
        paths=paths, settings=settings, controller=lambda name=None: None
    )


def _target(flasher, id: str) -> flashers.FlashTarget:
    return flashers.FlashTarget(flasher=flasher.name, type="ebb36", id=id)


def _run(bench, flasher, targets, monkeypatch) -> dict:
    monkeypatch.setattr(flashers.registry, "FLASHERS", {flasher.name: flasher})
    return flashers.write_all(
        bench, targets, flashers.PlainContext(lambda *a: None)
    )


def test_the_loop_writes_the_record_a_flasher_describes(bench, monkeypatch):
    flasher = _Fake(RECORD)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    entry = FlashLog(bench.paths).all()["S1"]
    assert entry["type"] == "ebb36"
    assert entry["fw"] == "klipper"
    assert entry["bin_sha256"] == "ab" * 32
    assert entry["fw_sha"] == "cafe1234"
    assert entry["version"] == "v0.12.0-1-gcafe123"


def test_a_dry_run_records_nothing(paths, monkeypatch):
    """The guard that used to be copied into all four writers. Nothing was
    written, so nothing about the board is true afterwards."""
    rehearsal = flashers.Bench(
        paths=paths,
        settings=dataclasses.replace(Settings(), dry_run=True),
        controller=lambda name=None: None,
    )
    flasher = _Fake(RECORD)

    _run(rehearsal, flasher, [_target(flasher, "S1")], monkeypatch)

    assert FlashLog(paths).all() == {}


def test_a_flasher_with_nothing_to_file_records_nothing(bench, monkeypatch):
    flasher = _Fake(None)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert FlashLog(bench.paths).all() == {}


def test_confidence_rides_the_write_result_and_leaves_the_wire_alone(
    bench, monkeypatch
):
    """How the board was identified is known inside the write and nowhere else,
    so it comes back with the result - and comes straight back off it again.
    `flashed[]` is on the wire; the ledger is not."""
    flasher = _Fake(RECORD, extra={"serial": "S1", "confidence": "unique_bus_id"})

    result = _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert result["flashed"] == [
        {"type": "ebb36", "id": "S1", "flasher": "fake", "serial": "S1"}
    ]
    assert FlashLog(bench.paths).all()["S1"]["confidence"] == "unique_bus_id"


def test_a_written_board_is_recorded_before_a_later_device_fails(bench, monkeypatch):
    """Spec, Testing: `FlashLog` written before the failure is raised. An
    operator told "failed" with no record has every reason to write the same
    image to the same board again."""
    flasher = _Fake(RECORD, fails={"S2"})

    result = _run(
        bench, flasher, [_target(flasher, "S1"), _target(flasher, "S2")], monkeypatch
    )

    assert [f["id"] for f in result["failures"]] == ["S2"]
    assert sorted(FlashLog(bench.paths).all()) == ["S1"]


def test_a_board_that_never_came_back_is_still_recorded(bench, monkeypatch):
    """`settled` runs after the record, not before it. The image is on the
    board the moment the write returns; a slow return does not unwrite it."""
    flasher = _Fake(RECORD, settled_raises=True)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert "S1" in FlashLog(bench.paths).all()


# --- what each flasher describes ----------------------------------------------


def test_flashtool_describes_the_kconfig_sidecar(bench, paths):
    os.makedirs(paths.artifact_dir("ebb36"), exist_ok=True)
    with open(paths.sidecar_file("ebb36", "klipper"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "fw_sha": "built-klipper-sha",
                "bin_sha256": "built-bin-sha",
                "version": "CARTOGRAPHER 6.2.0",
            },
            fh,
        )
    target = flashers.flashtool.target_for(
        {"type": "ebb36", "serial": "S1", "chipset": "stm32g0b1xx", "fw": "klipper"}
    )

    record = flashers.Flashtool().record(bench, target)

    assert record == flashers.FlashRecord(
        key="S1",
        mcu_type="ebb36",
        fw="klipper",
        bin_sha256="built-bin-sha",
        fw_sha="built-klipper-sha",
        version="CARTOGRAPHER 6.2.0",
    )


def test_bootsel_describes_the_cmake_sidecar(bench, paths, cmake_type, tmp_path):
    """The two sidecar schemas name the tree commit differently - `sha` here,
    `fw_sha` for kconfig - so each flasher reads the one its own builder wrote."""
    uf2 = tmp_path / "roadrunner.uf2"
    uf2.write_bytes(b"image")
    target = flashers.bootsel.target_for(
        str(uf2), chipset="rp2040", type_name="roadrunner", serial="RR-1"
    )

    record = flashers.Bootsel().record(bench, target)

    assert record is not None
    assert record.key == "RR-1"
    assert record.fw == "roadrunner"
    assert record.fw_sha == "built-subtree-sha"
    assert record.bin_sha256 == "built-uf2-sha256"


def test_bootsel_has_nothing_to_file_for_a_bare_board(bench, tmp_path):
    """First install: the `type` is a chipset string and there may be no serial
    at all, so there is no tracked device to file this under."""
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"image")
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    assert flashers.Bootsel().record(bench, target) is None
```

The `cmake_type` fixture writes a `roadrunner` CMake type with a sidecar. Add it to `tests/conftest.py` beside the other type fixtures:

```python
@pytest.fixture
def cmake_type(paths):
    """A configured CMake type with a built sidecar, for the record paths."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[type roadrunner]\n"
            "builder: cmake\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
            "serials: RR-1\n"
        )
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.sidecar_file("roadrunner", "roadrunner"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "provider": "cmake",
                "sha": "built-subtree-sha",
                "version": "v1.2.3-4-gabcdef0",
                "dirty": False,
                "cmake_target": "roadrunner_v1_i2c_rgb",
                "bin_sha256": "built-uf2-sha256",
                "bin_size": 15,
                "bin_mtime": 123.0,
            },
            fh,
        )
    return "roadrunner"
```

If `tests/fixtures/registry.cfg` already declares a `[type roadrunner]` (Task 1 added `[firmware roadrunner]`, not a type), drop the config write from the fixture and keep only the sidecar.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_flashlog_loop.py -q`
Expected: FAIL — `AttributeError: module 'mcu_updater.flashers' has no attribute 'FlashRecord'`, and once that is defined, `_Fake.record` is never called so no entry is written.

- [ ] **Step 3: `FlashRecord` and `Flasher.record`**

In `src/mcu_updater/flashers/spec.py`, after `FlashTarget`:

```python
@dataclasses.dataclass(frozen=True)
class FlashRecord:
    """What a completed write is worth remembering, in `FlashLog`'s vocabulary.

    A flasher describes; `write_all` files. The three decisions a record used
    to carry with it at four separate call sites - whether this run was a
    rehearsal, where the ledger lives, and whether a lost write is worth
    failing a good flash over - are the batch's, and there is one of each now.

    `key` is what the entry is filed under and is *not* always `target.id`: a
    screen's id is a port, which is not durable, so it files under
    `build.display_key` of its hardware id instead.
    """

    key: str
    mcu_type: str
    fw: str
    bin_sha256: str | None
    fw_sha: str | None
    version: str | None = None
```

In the `Flasher` Protocol, after `write`:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """What was just written to this target, for the ledger.

        Called only after a successful `write`, and never on a dry run - the
        batch owns both of those conditions. `None` means there is nothing
        durable to file this under: a bare board with no tracked serial, a
        screen that would not say which one it is.

        Reads the build record its own builder wrote. The two sidecar schemas
        in this tree name the tree commit differently (`fw_sha` for kconfig,
        `sha` for cmake), and the flasher that wrote the image is the one side
        that knows which it is looking at.
        """
        ...
```

Extend `write`'s docstring, after "a board's serial under the name it has always had.":

```text
        A `"confidence"` key is special: `write_all` takes it off the result
        and passes it to the ledger, so it never reaches the wire. How a board
        was identified is known inside the write and nowhere else - the batch
        cannot re-derive it afterwards, because the ports are no longer free.
```

Add `FlashRecord` to `__all__`, and export it from `src/mcu_updater/flashers/__init__.py` (import and `__all__`, sorted).

- [ ] **Step 4: The loop writes it**

In `src/mcu_updater/flashers/batch.py`, replace `from ..service import services_stopped` with:

```python
    from ..build import FlashLog
    from ..service import services_stopped
```

and replace the write's success path — from `flashed.append(...)` through the `settled` comment's first line — with:

```python
                    # Off the result and onto the ledger: see `Flasher.write`.
                    extra = dict(extra)
                    confidence = extra.pop("confidence", None)
                    if not bench.settings.dry_run:
                        record = flasher.record(bench, target)
                        if record is not None:
                            FlashLog(bench.paths).record(
                                record.key,
                                mcu_type=record.mcu_type,
                                fw=record.fw,
                                bin_sha256=record.bin_sha256,
                                fw_sha=record.fw_sha,
                                confidence=confidence,
                                version=record.version,
                            )
                    flashed.append({**target.to_json(), **extra})
                    # After the write and after it is recorded: a device that
```

Add to `write_all`'s docstring, after the cancellation paragraph:

```text
    **The ledger is written here and nowhere else** (Ruling 12). Right after
    the write returns and before `settled`, the service restart, or any later
    device's failure - all three can fail after the image is already on the
    board, and a completed write with no record is what makes an operator
    flash an already-correct board a second time.
```

- [ ] **Step 5: Each flasher describes its record**

`src/mcu_updater/flashers/flashtool.py` — change the spec import to include `FlashRecord`, and add after `write`:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        from ..build import git_head, read_sidecar
        from .. import firmware

        fw = target.detail.get("fw") or "klipper"
        side = read_sidecar(bench.paths, target.type, fw) or {}
        return FlashRecord(
            key=target.id,
            mcu_type=target.type,
            fw=fw,
            bin_sha256=side.get("bin_sha256"),
            # A sidecar from before the field existed, or a build this tool did
            # not perform: the tree's head is the same answer one step less
            # directly, and is what this path has always fallen back on.
            fw_sha=side.get("fw_sha")
            or git_head(firmware.resolve(bench.paths, fw).source_dir(bench.paths)),
            version=side.get("version"),
        )
```

`src/mcu_updater/flashers/esptool.py` — delete `_record` whole and its `Confidence` import if ruff reports it unused, change `_record(bench, display, screen, confidence)` in `write` to nothing, and make `write` return the confidence:

```python
        return {
            "name": screen["name"],
            "port": port,
            # Taken back off by `write_all` - the ports are free exactly once,
            # inside this batch's stop, and this is the only moment the answer
            # exists.
            "confidence": confidence.reason if confidence is not None else None,
            **result,
        }

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The image this screen now holds, filed under its hardware id.

        `None` for a screen with no hardware id: the port it answered on is not
        a durable name for it - see `build.display_key`.
        """
        from ..build import display_key
        from ..providers import pio as pio_mod

        display = target.detail["display"]
        screen = target.detail["screen"]
        ident = (screen.get("device_id") or screen.get("reported_id") or "").lower()
        if not ident:
            return None
        # The build already hashed the image and noted its commit; re-deriving
        # them here would be a second answer to a question with a recorded one.
        side = pio_mod.read_sidecar(bench.paths, display) or {}
        return FlashRecord(
            key=display_key(ident),
            mcu_type=display.name,
            fw=display.env,
            bin_sha256=side.get("bin_sha256"),
            # The display sidecar calls the tree commit `sha`; the flash log
            # calls it `fw_sha`. One rename at the boundary.
            fw_sha=side.get("sha"),
        )
```

`src/mcu_updater/flashers/bootsel.py` — add after `write`:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The CMake image this board now holds.

        `None` for anything that is not a configured CMake type: first install
        writes a bootloader to a bare board whose `type` is a chipset string
        and whose id may be empty, and there is no tracked device to file that
        under.
        """
        from ..providers import cmake as cmake_mod

        target_type = cmake_mod.load(bench.paths).get(target.type)
        if target_type is None:
            return None
        side = cmake_mod.read_sidecar(bench.paths, target_type) or {}
        return FlashRecord(
            key=target.id,
            mcu_type=target.type,
            fw=target_type.firmware,
            bin_sha256=side.get("bin_sha256"),
            # `sha`, not `fw_sha`: the CMake sidecar's own spelling.
            fw_sha=side.get("sha"),
            version=side.get("version"),
        )
```

`src/mcu_updater/flashers/dfu_util.py` — add after `write`:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """Nothing to file. dfu-util only ever writes a bootloader to a bare
        board here, before it has the durable identity a record is filed
        under."""
        return None
```

Each of these needs `FlashRecord` in its `from .spec import ...` line.

- [ ] **Step 6: The old writers stop writing**

In `src/mcu_updater/flashers/flash.py`, `flash_katapult`:

1. The return annotation becomes `-> str | None`, and the docstring's last line becomes:

```text
    Raises on any failure. Returns how the board was identified - a
    `discovery.spec.Confidence.reason`, or None when the sighting was a
    remembered one - for `write_all` to put in the ledger.
```

2. In the pre-write block, `from ..build import FlashLog, git_head, read_sidecar` becomes `from ..build import read_sidecar`.
3. Replace the post-write record — the comment starting "Note which binary this board now holds." through the `FlashLog(paths).record(...)` call — with nothing, leaving:

```python
    if not settings.dry_run:
        _report_offset_mismatch(reporter, serial, mcu_type, fw, side, transcript)

    reporter("info", f"Flashed {serial} successfully.")
    return confidence.reason if confidence is not None else None
```

In `flash_katapult_can`: the return annotation becomes `-> str | None`, its `from ..build import` loses `FlashLog` and `git_head` the same way, the `FlashLog(paths).record(...)` call goes, and it ends:

```python
    if not settings.dry_run:
        _report_offset_mismatch(reporter, uuid, mcu_type, fw, side, transcript)

    reporter("info", f"Flashed {uuid} successfully.")
    # Finding a uuid answer on the bus at all - probe or write - is itself the
    # confirmation; there is no separate by-id sighting to carry a
    # `Confidence.reason` the way a serial's does.
    return "canbus_uuid"
```

If `side` is now only read by `_report_offset_mismatch` and the offset probe, leave it exactly where it is — both still need it.

In `src/mcu_updater/flashers/flashtool.py`'s `write`, return the reason from each path:

```python
        if "uuid" in target.detail:
            confidence = flash_katapult_can(...)
            return {"uuid": target.id, "confidence": confidence}
        ...
        confidence = flash_katapult(...)
        return {"serial": target.id, "confidence": confidence}
```

In `src/mcu_updater/agent/methods/flash.py`, `_cmake_flash`: delete the `if result["flashed"] and not settings_now.dry_run:` block whole (its comment included) and the now-unused `cmake_mod` reference if ruff reports it — `cmake_mod.load` is still used above, so only the `read_sidecar` call goes. `settings_now` is still passed to `self._bench(...)`.

In `src/mcu_updater/build.py`, add to `FlashLog.record`'s docstring, after its first paragraph:

```text
        Written from exactly one place: `flashers.batch.write_all`, right after
        a write returns (Ruling 12). A flasher describes the record and the
        loop files it, so the dry-run guard and the "never raise" rule exist
        once each rather than once per write path.
```

- [ ] **Step 7: Move the tests that asserted the old writers**

`tests/test_flash.py`:

- `test_a_real_flash_records_unique_bus_id_confidence` — rename to `test_a_real_flash_reports_unique_bus_id_confidence`, drop the `FlashLog` import and the record read, and replace the call and assertion with:

```python
    assert flash_katapult(paths, ready, "board", "chipA", "S1") == "unique_bus_id"
```

Update its docstring's last clause to "…so a real write *reports* `unique_bus_id` for the loop to file - the board-side counterpart to a display's `answered` after a listen pass."

- `test_a_real_flash_records_the_sidecars_stamped_version` — delete it. `test_flashtool_describes_the_kconfig_sidecar` in the new file covers the same sidecar field through the code that now reads it.
- `test_a_vanished_volume_reaches_flashed_so_provenance_can_record` — replace the docstring's first sentence with: "`write_all` records the `FlashLog` off a successful write, so the batch has to count this one as one - a failure row would silence the ledger for the most common successful ending there is."

`tests/test_flash_can.py`, `test_a_real_flash_records_canbus_uuid_confidence`: rename to `test_a_real_can_flash_reports_canbus_uuid_confidence`, drop the `FlashLog` import, and replace the call and assertion with:

```python
    assert flash_katapult_can(ready_paths, ready, "board", UUID) == "canbus_uuid"
```

`tests/test_agent_flash.py`:

- The Roadrunner test whose fake `write_all` returns a flashed target: delete the six `record = FlashLog(...)` assertion lines at its end (the fake never writes, and the real loop is covered in `test_flashlog_loop.py`), keeping the job and target assertions. Add to its docstring: "The ledger is `write_all`'s now (Ruling 12), so a faked batch writes none - see `test_flashlog_loop.py`."
- `test_a_completed_cmake_copy_records_provenance_whatever_follows_it` — delete it. Its subject moved to `test_a_written_board_is_recorded_before_a_later_device_fails`, which tests it where the guarantee now lives instead of through a fake that no longer records.
- Drop the `FlashLog` import if line 293's `assert FlashLog(api.paths).all() == {}` is the only remaining use — it is, so keep the import.

- [ ] **Step 8: Run the affected tests**

Run: `python -m pytest tests/test_flashlog_loop.py tests/test_flash.py tests/test_flash_can.py tests/test_agent_flash.py tests/test_agent_display_jobs.py tests/test_agent_displays.py tests/test_build.py -q`
Expected: PASS.

Then: `rg -n "FlashLog\(" src`
Expected: exactly two hits in `src/mcu_updater/agent/methods/bulk.py`, two in `agent/methods/status.py`, the class in `build.py`, and the one write in `flashers/batch.py`. No other writer.

- [ ] **Step 9: Mutation spec**

Create `scripts/mutations/flashlog-loop.json`:

```json
{
  "_comment": "One writer for the ledger. The loop must file what a flasher describes, skip a rehearsal, take the confidence off the write result, and record before anything downstream of the write can fail. Each flasher must read its own builder's sidecar schema.",
  "file": "src/mcu_updater/flashers/batch.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_flashlog_loop.py",
    "tests/test_flash.py",
    "tests/test_flash_can.py",
    "tests/test_agent_display_jobs.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "the loop files what the flasher describes",
      "find": "                        if record is not None:",
      "replace": "                        if False:"
    },
    {
      "name": "a rehearsal records nothing",
      "find": "                    if not bench.settings.dry_run:\n                        record = flasher.record(bench, target)",
      "replace": "                    if True:\n                        record = flasher.record(bench, target)"
    },
    {
      "name": "confidence comes off the result and onto the ledger",
      "find": "                    confidence = extra.pop(\"confidence\", None)",
      "replace": "                    confidence = extra.get(\"confidence\")"
    },
    {
      "name": "a flashtool write reports how it identified the board",
      "file": "src/mcu_updater/flashers/flash.py",
      "find": "    return confidence.reason if confidence is not None else None",
      "replace": "    return None"
    },
    {
      "name": "flashtool reads the kconfig sidecar's commit",
      "file": "src/mcu_updater/flashers/flashtool.py",
      "find": "            fw_sha=side.get(\"fw_sha\")",
      "replace": "            fw_sha=side.get(\"sha\")"
    },
    {
      "name": "bootsel reads the cmake sidecar's commit",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "            fw_sha=side.get(\"sha\"),",
      "replace": "            fw_sha=side.get(\"fw_sha\"),"
    },
    {
      "name": "a bare board has nothing to file",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        if target_type is None:\n            return None",
      "replace": "        if target_type is None:\n            target_type = next(iter(cmake_mod.load(bench.paths).values()))"
    },
    {
      "name": "a screen with no hardware id has nothing to file",
      "file": "src/mcu_updater/flashers/esptool.py",
      "find": "        if not ident:\n            return None",
      "replace": "        if False:\n            return None"
    }
  ]
}
```

Run, one at a time, waiting for each:

```bash
python scripts/mutation_test.py scripts/mutations/flashlog-loop.json
python scripts/mutation_test.py scripts/mutations/display-flash.json
python scripts/mutation_test.py scripts/mutations/bulk-operations.json
python scripts/mutation_test.py scripts/mutations/single-write-path.json
python scripts/mutation_test.py scripts/mutations/batch-selection.json
python scripts/mutation_test.py scripts/mutations/bootsel-erase.json
python scripts/mutation_test.py scripts/mutations/flash-offset-diagnostic.json
```

Expected: every mutation killed. The "a bare board has nothing to file" mutant needs a CMake type configured for `next(iter(...))` to find one — `test_bootsel_has_nothing_to_file_for_a_bare_board` uses the bare `paths` fixture, so add `cmake_type` to its parameters if the mutant survives for want of a type to pick.

- [ ] **Step 10: Docs**

`docs/agent-api.md`, the Roadrunner paragraph at line 694: replace from "A copy that completed is recorded in `flash.json` before any failure is reported," through "so a completed copy can go unrecorded." with:

```markdown
A copy that completed is recorded in `flash.json` the moment the write returns
— before the readiness wait, before stopped services are restarted, and before
any later failure is reported. A completed copy is never unrecorded.
```

`docs/decisions.md`, after Task 4's entry:

```markdown
### The batch loop is the only writer of the flash ledger

Ruling 12. `Flasher.record` describes what was written; `flashers.batch.
write_all` files it, right after the write and before `settled`, the service
restart, or a later device's failure. Four writers each carried their own
dry-run guard and their own idea of which sidecar schema to read, and the
CMake one filed after the batch returned — so a failed service restart lost
the record of a copy that had already landed.

A flasher still reads its own builder's sidecar, because the two schemas in
this tree spell the tree commit differently (`fw_sha`, `sha`) and the flasher
that wrote the image is the one side that knows which it is reading. What a
flasher no longer decides is whether the run was a rehearsal, where the ledger
lives, or whether losing it is worth failing a good write over.
```

- [ ] **Step 11: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass.

```bash
git add -A src/mcu_updater tests scripts/mutations docs/agent-api.md docs/decisions.md
git commit -m "feat(flashers): the batch loop writes the flash ledger

Flasher.record describes what a write put on a device; write_all files it,
right after the write and before settled, the restart, or a later failure.
flash_katapult, flash_katapult_can, esptool._record and _cmake_flash stop
recording. flash_katapult returns the confidence it confirmed the board by.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Tracking an unprovisioned board provisions it

Spec §11 and Ruling 13. Tracking an `RR-UNPROVISIONED-…` serial is refused today, and the operator is told to go and run the provision action first. That refusal is right for a family that cannot provision and wrong for one that can: the two steps have the same precondition (the board is here, untracked, and nobody else holds the bus), and asking for them separately is the only reason the order can go wrong. `tracking.add_serial` provisions, then tracks what provisioning returned.

**Files:**
- Modify: `src/mcu_updater/helpers/spec.py` (`Provisioner` Protocol)
- Modify: `src/mcu_updater/helpers/__init__.py` (`provisioner` accessor, export)
- Modify: `src/mcu_updater/helpers/roadrunner.py` (`is_unprovisioned`, `provision`)
- Modify: `src/mcu_updater/tracking.py:16-53` (`Tracked`; provision-on-track)
- Modify: `src/mcu_updater/agent/methods/registry.py:227-272` (`serial_add`)
- Modify: `src/mcu_updater/cli.py:245-252`, `:865`, `:1032` (three `added, _ =` call sites)
- Test: `tests/test_provision_on_track.py` (create), `tests/test_cli.py:820-845`, `:880-900`, `tests/test_agent_registry.py`
- Create: `scripts/mutations/provision-on-track.json`
- Docs: `docs/agent-api.md` (`fw.serial.add`), `docs/decisions.md`
- Mutation specs at risk: `add-mcu.json` (anchors the agent's `not_an_mcu` refusal and the adoption path in `serial_add`, neither rewritten here: run it), `roadrunner-*.json` if any anchor `discovery/roadrunner.py` (untouched: run them), `cli-every-type.json` (anchors `cli.py` lines this task does not rewrite: run it). Run `rg -n "add_serial|UNPROVISIONED|provision" scripts/mutations` before editing and re-anchor every hit on a line this task changes.

**Interfaces:**
- Consumes (Task 1): `helpers.for_name(name, *, family)`, `helpers.registry.HELPERS`, `typelist.read_config(paths) -> (list[TypeEntry], dict[str, FirmwareFamily])`, `TypeEntry.firmwares`. Existing: `lock.exclusive(paths, label)`, `errors.BusyError`, `config.Registry.mutate` (its own lock file — Ruling 20, so provisioning under `exclusive()` and then mutating cannot deadlock).
- Produces:
  - `helpers.Provisioner` Protocol (`runtime_checkable`): `name: str`, `is_unprovisioned(serial: str) -> bool`, `provision(paths: Paths, serial: str) -> str` (returns the new durable serial; **the caller holds the op lock**)
  - `helpers.provisioner(helper: Helper | None) -> Provisioner | None`
  - `tracking.Tracked(added: bool, chipset: str, serial: str, provisioned_from: str | None = None)`, frozen
  - `tracking.add_serial(paths, name, serial) -> Tracked` (was `tuple[bool, str]`)
  - `fw.serial.add` result: `serial` is the tracked serial, and `prior_serial` appears when it differs from the requested one ★

Decisions this task makes:

- **The type is checked before the hardware is.** `add_serial` reads the declared type and its family first and raises `UnknownTypeError` for a type that does not exist, so a typo never provisions a board. Only then does it take the op lock.
- **A held op lock refuses; it never waits and never retries** (Ruling 13). `exclusive` raises `BusyError`, which propagates. Provisioning is an irreversible write to a board, and a caller queueing behind a flash for it would provision at a moment nobody chose.
- **The refusal stays for a family with no provisioner.** `UnprovisionedSerialError` is still raised inside the registry mutation, and is now reached only when no provisioner was found — which is exactly where its message ("provision it first") is still true.
- **Nothing here scans the bus.** `provision` is the helper's business and it finds its own device; `tracking` only asks whether the serial *looks* unprovisioned, which is a string question.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provision_on_track.py`:

```python
"""Tracking an unprovisioned board provisions it first.

Spec section 11. The two steps have the same precondition - the board is here,
untracked, and nobody else has the bus - so the only thing splitting them buys
is an order to get wrong.
"""

from __future__ import annotations

import pytest

from mcu_updater import helpers, tracking
from mcu_updater.config import Registry
from mcu_updater.errors import BusyError, UnprovisionedSerialError, UnknownTypeError
from mcu_updater.lock import exclusive

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-9F2C11A45E7B0033"


class _FakeRoadrunner:
    """Stands in for the roadrunner helper: the real one talks to a board."""

    name = "roadrunner"
    label = "Roadrunner"

    def __init__(self, result: str = PROVISIONED, error: Exception | None = None):
        self.calls: list[str] = []
        self.result = result
        self.error = error

    def is_unprovisioned(self, serial: str) -> bool:
        return serial.startswith("RR-UNPROVISIONED-")

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def rr(paths, monkeypatch):
    """A `[type roadrunner]` whose family helper can provision."""
    helper = _FakeRoadrunner()
    monkeypatch.setitem(helpers.registry.HELPERS, "roadrunner", helper)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware roadrunner]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "helper: roadrunner\n"
            "\n[type roadrunner]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
        )
    return helper


def test_tracking_an_unprovisioned_board_provisions_it_and_tracks_the_result(
    paths, rr
):
    tracked = tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == [UNPROVISIONED]
    assert tracked.serial == PROVISIONED
    assert tracked.provisioned_from == UNPROVISIONED
    assert tracked.added is True
    assert tracked.chipset == "rp2040"
    serials = Registry.load(paths).declared_serials("roadrunner")
    assert serials == [PROVISIONED], "the diagnostic identity must never be saved"


def test_tracking_an_already_provisioned_board_provisions_nothing(paths, rr):
    tracked = tracking.add_serial(paths, "roadrunner", PROVISIONED)

    assert rr.calls == []
    assert tracked.provisioned_from is None
    assert tracked.serial == PROVISIONED


def test_an_unknown_type_is_refused_before_any_board_is_written_to(paths, rr):
    """A typo in the type name must not provision hardware. The type is read
    first, and provisioning is the step after."""
    with pytest.raises(UnknownTypeError):
        tracking.add_serial(paths, "roadrunnr", UNPROVISIONED)

    assert rr.calls == []


def test_a_held_lock_refuses_the_track_and_never_retries(paths, rr):
    """Ruling 13. Provisioning is an irreversible write to a board; a caller
    that queued behind a running flash would perform it at a moment nobody
    chose."""
    with exclusive(paths, "something else entirely"):
        with pytest.raises(BusyError):
            tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == []
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_a_family_that_cannot_provision_still_refuses_the_diagnostic_serial(
    paths, rr, monkeypatch
):
    """The old refusal, where it is still correct: with nothing able to
    provision, "provision it first" is the only useful thing to say."""
    monkeypatch.delitem(helpers.registry.HELPERS, "roadrunner")

    with pytest.raises(UnprovisionedSerialError):
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_the_real_roadrunner_helper_offers_provisioning(paths):
    helper = helpers.for_name("roadrunner", family="roadrunner")
    prov = helpers.provisioner(helper)

    assert prov is not None
    assert prov.is_unprovisioned(UNPROVISIONED) is True
    assert prov.is_unprovisioned(PROVISIONED) is False


def test_a_helper_without_the_capability_offers_none(paths):
    assert helpers.provisioner(None) is None
    assert helpers.provisioner(helpers.for_name("knomi_serial", family="knomi")) is None
```

Add to `tests/test_agent_registry.py`:

```python
def test_serial_add_reports_the_serial_it_actually_tracked(api, monkeypatch):
    """The panel offers an unprovisioned board; what gets tracked is the serial
    provisioning returned. `prior_serial` is how a caller holding the old one
    knows its handle moved."""
    import mcu_updater.tracking as tracking_mod

    monkeypatch.setattr(
        tracking_mod,
        "add_serial",
        lambda paths, name, serial: tracking_mod.Tracked(
            added=True, chipset="rp2040", serial="RR-NEW", provisioned_from=serial
        ),
    )

    result = api.dispatch(
        "fw.serial.add", {"name": "roadrunner", "serial": "RR-UNPROVISIONED-0"}
    )

    assert result["serial"] == "RR-NEW"
    assert result["prior_serial"] == "RR-UNPROVISIONED-0"
    assert result["added"] is True


def test_serial_add_omits_prior_serial_when_nothing_moved(api, monkeypatch):
    import mcu_updater.tracking as tracking_mod

    monkeypatch.setattr(
        tracking_mod,
        "add_serial",
        lambda paths, name, serial: tracking_mod.Tracked(
            added=True, chipset="stm32g0b1xx", serial=serial
        ),
    )

    result = api.dispatch("fw.serial.add", {"name": "board", "serial": "AAAA-if00"})

    assert "prior_serial" not in result
```

Use whatever fixture and type names `tests/test_agent_registry.py` already uses for `fw.serial.add`; the two assertions are the point, not the fixture.

In `tests/test_cli.py`, replace `test_add_serial_refuses_an_unprovisioned_roadrunner_diagnostic_serial` with:

```python
def test_add_serial_provisions_an_unprovisioned_roadrunner_then_tracks_it(
    c, monkeypatch, capsys
):
    """What used to be a refusal. The CLI reaches cmake types, and the family's
    helper can provision, so the operator is not sent to the web UI and back."""
    _declare_roadrunner(c.paths, "RR-ONE")
    monkeypatch.setattr(
        tracking,
        "add_serial",
        lambda paths, name, serial: tracking.Tracked(
            added=True, chipset="rp2040", serial="RR-NEW", provisioned_from=serial
        ),
    )

    cli.add_serial(
        argparse.Namespace(type="roadrunner", serial="RR-UNPROVISIONED-50543165187A4D1C")
    )

    out = capsys.readouterr().out
    assert "Provisioned RR-UNPROVISIONED-50543165187A4D1C as RR-NEW" in out
    assert "Added serial RR-NEW to roadrunner" in out
```

and update `test_the_flash_prompt_tracks_a_new_serial_through_tracking`'s spy to return a `Tracked`:

```python
    def spy(paths, name, serial):
        calls.append((name, serial))
        return real(paths, name, serial)
```

(unchanged — it forwards the real return, which is now a `Tracked`. Only the `UnprovisionedSerialError` import may become unused; drop it if ruff says so.)

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_provision_on_track.py -q`
Expected: FAIL with `AttributeError: module 'mcu_updater.helpers' has no attribute 'provisioner'` and `module 'mcu_updater.tracking' has no attribute 'Tracked'`.

- [ ] **Step 3: The `Provisioner` capability**

In `src/mcu_updater/helpers/spec.py`, beside the other capability Protocols:

```python
@runtime_checkable
class Provisioner(Protocol):
    """Give an unprovisioned board its durable identity.

    Two questions, because the first is cheap and the second is irreversible:
    `is_unprovisioned` is a string test on a serial the caller already has, and
    `provision` writes to hardware.

    **The caller holds the op lock.** Provisioning finds its own device, writes
    to it and waits for it to re-enumerate under a new name - three steps that
    must not interleave with a flash or with a second provision, and the lock
    covering all three is the caller's to take (`lock.exclusive`), because the
    caller is the one that knows what to call the operation.
    """

    name: str

    def is_unprovisioned(self, serial: str) -> bool:
        """Does this serial look like an unprovisioned board's diagnostic one?"""
        ...

    def provision(self, paths: Paths, serial: str) -> str:
        """Provision the board answering to `serial`; return its new serial."""
        ...
```

In `src/mcu_updater/helpers/__init__.py`, beside the other accessors:

```python
def provisioner(helper: Helper | None) -> Provisioner | None:
    """This helper's provisioning capability, or None if it has none.

    None is an ordinary answer, not a misconfiguration: most firmware has no
    identity to hand out.
    """
    return helper if isinstance(helper, Provisioner) else None
```

Export `Provisioner` and `provisioner` (import and `__all__`, sorted).

In `src/mcu_updater/helpers/roadrunner.py`, on the helper class:

```python
    def is_unprovisioned(self, serial: str) -> bool:
        from ..discovery.roadrunner import UNPROVISIONED_RE

        return UNPROVISIONED_RE.fullmatch(serial) is not None

    def provision(self, paths: Paths, serial: str) -> str:
        """Give this board a durable serial. The caller holds the op lock.

        `find_untracked` confirms the board over its own protocol before
        anything is written, and `provision_roadrunner` does not return until
        the same hardware has come back under the new name - which is what
        makes the serial this returns safe to track.
        """
        import secrets

        from ..discovery import roadrunner

        device = roadrunner.find_untracked(paths, serial)
        return roadrunner.provision_roadrunner(
            paths, device, secrets.token_bytes(16)
        ).serial
```

- [ ] **Step 4: `tracking.add_serial` provisions**

Replace `src/mcu_updater/tracking.py`'s imports and `add_serial` with:

```python
import dataclasses

from .config import Registry
from .errors import SerialTrackedElsewhereError, UnprovisionedSerialError
from .paths import Paths


@dataclasses.dataclass(frozen=True)
class Tracked:
    """What tracking a serial did.

    `serial` is what ended up in `serials:`, which is not always what the
    caller asked for: an unprovisioned board is given its durable identity
    first, and that is the one tracked. `provisioned_from` is the serial the
    caller passed, and is None when nothing moved - so a caller that just
    forwards both fields reports the truth either way.
    """

    added: bool
    chipset: str
    serial: str
    provisioned_from: str | None = None


def _provisioner_for(paths: Paths, name: str):
    """The provisioning capability of type `name`'s firmware family, or None.

    Read before anything is written to a board, so a typo in the type name is
    an `UnknownTypeError` rather than a provisioned board nobody asked for.
    """
    from . import firmware, helpers, typelist

    entries, families = typelist.read_config(paths)
    entry = next((e for e in entries if e.name == name), None)
    if entry is None or not entry.firmwares:
        return None
    family = firmware.resolve(paths, entry.firmwares[0], families)
    return helpers.provisioner(helpers.for_name(family.helper, family=family.name))


def add_serial(paths: Paths, name: str, serial: str) -> Tracked:
    """Track `serial` under type `name`, provisioning it first if it needs it.

    Spec section 11. A board whose serial is its unprovisioned diagnostic
    identity is provisioned under the op lock and the *returned* serial is
    tracked - the two steps share a precondition (this board, here, untracked,
    nobody else on the bus) and splitting them only creates an order to get
    wrong.

    A held lock raises `BusyError` and is never retried: provisioning writes
    irreversibly to a board, and queueing behind a flash would perform that
    write at a moment nobody chose.
    """
    provisioned_from: str | None = None
    prov = _provisioner_for(paths, name)  # UnknownTypeError before any write
    if prov is not None and prov.is_unprovisioned(serial):
        from .lock import exclusive

        with exclusive(paths, f"provision {serial} for {name}"):
            provisioned = prov.provision(paths, serial)
        provisioned_from, serial = serial, provisioned

    with Registry.mutate(paths, f"add serial {serial}") as reg:
        chipset = reg.get_declared_chipset(name)  # UnknownTypeError if absent

        # An unprovisioned Roadrunner's serial is `RR-UNPROVISIONED-<flash-uid>`
        # - the trailing 16 hex characters ARE the RP2040 flash UID, which this
        # plan's constraints forbid ever persisting. Reached only when nothing
        # could provision it (above), which is where "provision it first" is
        # still the useful answer. Checked here, not only from the agent's live
        # bus scan, so every caller of this function refuses one. Not every
        # registry write: the agent's pairing-key adoption
        # (agent/methods/flash.py) calls Registry.add_serial directly, and only
        # ever for kconfig types.
        from .discovery.roadrunner import UNPROVISIONED_RE

        if UNPROVISIONED_RE.fullmatch(serial):
            raise UnprovisionedSerialError(
                f"'{serial}' is an unprovisioned Roadrunner's diagnostic identity, "
                f"not a stable serial - provision it first (the web UI's Provision "
                f"Roadrunner action, or fw.roadrunner.provision), then track the "
                f"resulting RR-... serial.",
                serial=serial,
            )

        # One board tracked under two types would get flashed twice with
        # different firmware, so this is refused rather than merged.
        elsewhere = [t for t in reg.find_declared_types_for_serial(serial) if t != name]
        if elsewhere:
            raise SerialTrackedElsewhereError(
                f"serial '{serial}' is already tracked under '{elsewhere[0]}'. "
                f"Remove it from there first if it really belongs to '{name}'.",
                serial=serial,
                requested=name,
                tracked_under=elsewhere,
            )
        added = reg.add_declared_serial(name, serial)
    return Tracked(
        added=added,
        chipset=chipset,
        serial=serial,
        provisioned_from=provisioned_from,
    )
```

`_provisioner_for` returns `None` for an unknown type rather than raising, and the `get_declared_chipset` call inside the mutation raises `UnknownTypeError` as it always has — so the type check keeps its one home. The test for it passes because a `None` provisioner writes to no board.

- [ ] **Step 5: The callers report what was tracked**

`src/mcu_updater/agent/methods/registry.py`, `serial_add`: replace `added, chipset = tracking.add_serial(...)` with `tracked = tracking.add_serial(self.paths, name, serial)`, and the return with:

```python
        self._changed()
        result = {
            "name": name,
            "serial": tracked.serial,
            "added": tracked.added,
            "chipset": tracked.chipset,
        }
        if tracked.provisioned_from is not None:
            # The caller's handle on this board just changed. Reported rather
            # than assumed: a panel holding the old serial has no other way to
            # learn that the row it was looking at has a new name.
            result["prior_serial"] = tracked.provisioned_from
        return result
```

Add to its docstring, after "Touches nothing but the registry: no build, no flash, no board.":

```text
        One exception, and it is the point of Ruling 13: a serial the type's
        firmware family can recognise as an unprovisioned board's diagnostic
        identity is provisioned first, and the resulting serial is what gets
        tracked. That does touch the board. `prior_serial` says so.
```

`src/mcu_updater/cli.py`, `add_serial`:

```python
def add_serial(args: argparse.Namespace) -> None:
    c = ctx()
    tracked = tracking.add_serial(c.paths, args.type, args.serial)
    if tracked.provisioned_from is not None:
        print(f"Provisioned {tracked.provisioned_from} as {tracked.serial}")
    if tracked.added:
        print(f"Added serial {tracked.serial} to {args.type}")
    else:
        print(f"Serial {tracked.serial} already exists under {args.type}")
```

At `cli.py:865` (the flash prompt) and `cli.py:1032` (the post-flash adoption loop), replace `added, _ = tracking.add_serial(...)` with `tracked = tracking.add_serial(...)` and use `tracked.added` / `tracked.serial` in the messages that follow, keeping each message's existing wording otherwise.

- [ ] **Step 6: Run the affected tests**

Run: `python -m pytest tests/test_provision_on_track.py tests/test_cli.py tests/test_agent_registry.py tests/test_tracking.py tests/test_agent_add_mcu.py -q`
Expected: PASS. (`tests/test_tracking.py` only if it exists; otherwise the tracking tests live in `test_cli.py` and `test_agent_registry.py`.)

Then: `rg -n "tracking.add_serial" src`
Expected: four call sites, none of them unpacking a tuple.

- [ ] **Step 7: Mutation spec**

Create `scripts/mutations/provision-on-track.json`:

```json
{
  "_comment": "Provision-on-track. An unprovisioned serial must be provisioned before it is tracked, never tracked as-is; the op lock must cover the write and never be retried; the type must be checked before any board is; and a family with nothing to provision with must still refuse.",
  "file": "src/mcu_updater/tracking.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_provision_on_track.py",
    "tests/test_cli.py",
    "tests/test_agent_registry.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "an unprovisioned serial is provisioned first",
      "find": "    if prov is not None and prov.is_unprovisioned(serial):",
      "replace": "    if False:"
    },
    {
      "name": "what gets tracked is what provisioning returned",
      "find": "        provisioned_from, serial = serial, provisioned",
      "replace": "        provisioned_from = serial"
    },
    {
      "name": "provisioning holds the op lock",
      "find": "        with exclusive(paths, f\"provision {serial} for {name}\"):\n            provisioned = prov.provision(paths, serial)",
      "replace": "        provisioned = prov.provision(paths, serial)"
    },
    {
      "name": "a family with no provisioner still refuses the diagnostic serial",
      "find": "        if UNPROVISIONED_RE.fullmatch(serial):",
      "replace": "        if False:"
    },
    {
      "name": "the serial that was tracked is the one reported",
      "file": "src/mcu_updater/agent/methods/registry.py",
      "find": "            \"serial\": tracked.serial,",
      "replace": "            \"serial\": serial,"
    },
    {
      "name": "a moved handle is reported",
      "file": "src/mcu_updater/agent/methods/registry.py",
      "find": "        if tracked.provisioned_from is not None:",
      "replace": "        if False:"
    },
    {
      "name": "only a helper that can provision offers one",
      "file": "src/mcu_updater/helpers/__init__.py",
      "find": "    return helper if isinstance(helper, Provisioner) else None",
      "replace": "    return helper"
    }
  ]
}
```

Run, one at a time, waiting for each:

```bash
python scripts/mutation_test.py scripts/mutations/provision-on-track.json
python scripts/mutation_test.py scripts/mutations/add-mcu.json
python scripts/mutation_test.py scripts/mutations/cli-every-type.json
python scripts/mutation_test.py scripts/mutations/family-keys.json
```

Expected: every mutation killed.

- [ ] **Step 8: Docs**

`docs/agent-api.md`, `fw.serial.add`: add to its result description

```markdown
`serial` is the serial that was tracked, which is not always the one that was
requested. When the type's firmware family can provision and the requested
serial is an unprovisioned board's diagnostic identity (`RR-UNPROVISIONED-…`),
the board is provisioned first and the durable serial is tracked; the request's
serial comes back as `prior_serial`. `prior_serial` is absent when nothing
moved.

Provisioning holds the operation lock. A lock held elsewhere refuses with
`busy` and does not retry — this is the one call under `fw.serial.add` that
writes to hardware. A family with no provisioning capability still refuses an
unprovisioned serial with `roadrunner_unprovisioned`.
```

`docs/decisions.md`:

```markdown
### Tracking an unprovisioned board provisions it

Spec section 11, Ruling 13. "Provision it first, then track the result" is two
operations with one precondition — this board, here, untracked, nobody else on
the bus — and the only thing the split ever produced was an order to get
wrong. `tracking.add_serial` reads the type's family, and if its helper offers
the `provision` capability and recognises the serial as a diagnostic identity,
provisions under the op lock and tracks what came back.

The old refusal is kept for a family with no provisioner, because there
"provision it first" is still the only useful thing to say. A held lock
refuses rather than waits: the write is irreversible, and a caller queued
behind a flash would perform it at a moment nobody chose.
```

- [ ] **Step 9: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass.

```bash
git add -A src/mcu_updater tests scripts/mutations docs/agent-api.md docs/decisions.md
git commit -m "feat(tracking): tracking an unprovisioned board provisions it

tracking.add_serial reads the type's firmware family, and when its helper can
provision and recognises an unprovisioned diagnostic serial, provisions under
the op lock and tracks the serial that came back. fw.serial.add reports it as
serial, with prior_serial when the handle moved. A held lock refuses; a family
with no provisioner still refuses the diagnostic serial.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Auto-provisioning on appearance

Spec §10 and Ruling 14. A board plugged in and not yet configured anywhere is invisible to the klippy extra's `klippy:connect` provisioning, so the host does it: `auto_provision: true` on a family whose helper can provision means a board that appears unprovisioned is given its durable identity on the watcher's own thread. Opt in, because a `[firmware]` section should not write to hardware nobody asked about.

**Files:**
- Modify: `src/mcu_updater/firmware.py:79-180` (`auto_provision` field and parse; `PROVISIONING_HELPERS`)
- Modify: `src/mcu_updater/typelist.py` (`_refuse_family_keys`)
- Create: `src/mcu_updater/provisioning.py`
- Modify: `src/mcu_updater/agent/events.py:237-335` (`on_change` takes the sweep; `_poll` extracted; the retry flag)
- Modify: `src/mcu_updater/agent/service.py:60-95` (`_on_bus_change`)
- Test: `tests/test_auto_provision.py` (create), `tests/test_typelist.py`
- Create: `scripts/mutations/auto-provision.json`
- Docs: `README.md` (`## Features`), `docs/agent-api.md` (the `bus` event / config keys), `docs/decisions.md`
- Mutation specs at risk: `family-keys.json` (Task 1's; anchors `_refuse_family_keys`'s first list comprehension, which this task appends after rather than rewrites: run it), `add-mcu.json` (anchors `adopt_paired`, still called on the same hook: run it). Run `rg -n "on_change|_fingerprint|adopt_paired" scripts/mutations` first and re-anchor every hit on a line this task changes.

**Interfaces:**
- Consumes (Task 6): `helpers.provisioner`, `helpers.Provisioner`. (Task 1): `firmware.HELPERS`, `typelist._refuse_family_keys`, `typelist.read_config`. Existing: `lock.exclusive`, `errors.BusyError`, `errors.UpdaterError`, `reporters.null_reporter`.
- Produces:
  - `firmware.FirmwareFamily.auto_provision: bool = False` (from `auto_provision:`, default off) ★
  - `firmware.PROVISIONING_HELPERS: tuple[str, ...] = ("roadrunner",)`
  - `provisioning.auto_provision(paths: Paths, devices: Mapping[str, Any], *, reporter: Reporter = null_reporter) -> bool` — True means "ask me again on your next poll"
  - `BusWatcher(..., on_change: Callable[[Mapping[str, Any]], Any] | None)` — called with `{serial: BusDevice}` from the sweep; a truthy return asks for a retry on the next poll even if the bus has not changed ★ (internal; no wire change)
  - `BusWatcher._poll()` — one poll, extracted from `_loop` so it can be driven from a test

Decisions this task makes:

- **It runs on the watcher thread, inline.** No job, no queue. Provisioning is a sub-second helper call, the watcher already survives any exception, and a job would put an entry in the panel's job list for something the operator did not ask for.
- **`BusyError` means skip, and the watcher asks again.** The fingerprint will not have changed — the board is still sitting there unprovisioned — so without the retry flag the next attempt would wait for an unrelated device to come or go. Every other `UpdaterError` is reported and dropped: a board that refuses its INFO probe will refuse it again next poll, and a warning per 15 seconds is noise.
- **Nothing here reaches `fw.status`.** A status poll must never write to a board (Ruling 14). The watcher is the only caller.
- **The key is refused on a family that cannot provision**, at config load, like `helper:` itself. `firmware.PROVISIONING_HELPERS` is the static tuple `typelist` checks against, held equal to the real capability by a test, for the reason `firmware.HELPERS` is (Ruling 4).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auto_provision.py`:

```python
"""Provisioning a board that appears, for families that asked for it.

Spec section 10. Opt in, on the bus watcher's thread, never from a status
poll, and never twice for one board - an unprovisioned serial stops matching
the moment provisioning succeeds.
"""

from __future__ import annotations

import pytest

from mcu_updater import firmware, helpers, provisioning
from mcu_updater.errors import BusyError, RoadrunnerError
from mcu_updater.lock import exclusive

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-9F2C11A45E7B0033"


class _FakeRoadrunner:
    name = "roadrunner"
    label = "Roadrunner"

    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self.error = error

    def is_unprovisioned(self, serial: str) -> bool:
        return serial.startswith("RR-UNPROVISIONED-")

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        if self.error is not None:
            raise self.error
        return PROVISIONED


def _family(paths, *, auto: str) -> None:
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware roadrunner]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "helper: roadrunner\n"
            f"{auto}"
        )


@pytest.fixture
def rr(monkeypatch):
    helper = _FakeRoadrunner()
    monkeypatch.setitem(helpers.registry.HELPERS, "roadrunner", helper)
    return helper


def _sweep(*serials: str) -> dict[str, object]:
    return {serial: object() for serial in serials}


def test_a_family_that_did_not_ask_provisions_nothing(paths, rr):
    """The default. A firmware section must not write to hardware nobody
    mentioned."""
    _family(paths, auto="")

    assert provisioning.auto_provision(paths, _sweep(UNPROVISIONED)) is False
    assert rr.calls == []


def test_an_opted_in_family_provisions_a_board_that_appeared(paths, rr):
    lines: list[tuple[str, str]] = []
    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED, "usb-Klipper_stm32-if00"),
        reporter=lambda stream, line: lines.append((stream, line)),
    )

    assert rr.calls == [UNPROVISIONED], "only the board that looks unprovisioned"
    assert retry is False
    assert ("info", f"Provisioned {UNPROVISIONED} as {PROVISIONED}.") in lines


def test_a_held_lock_is_skipped_and_asked_for_again(paths, rr):
    """Ruling 14. The bus has not changed - the board is still sitting there -
    so without the retry the next attempt would wait for an unrelated device to
    come or go."""
    _family(paths, auto="auto_provision: true\n")

    with exclusive(paths, "a flash, for instance"):
        retry = provisioning.auto_provision(paths, _sweep(UNPROVISIONED))

    assert retry is True
    assert rr.calls == []


def test_a_board_that_refuses_its_probe_is_reported_and_not_retried(paths, monkeypatch):
    """It will refuse again next poll. A warning every 15 seconds is noise, and
    the watcher must keep running either way."""
    helper = _FakeRoadrunner(error=RoadrunnerError("INFO did not confirm the device"))
    monkeypatch.setitem(helpers.registry.HELPERS, "roadrunner", helper)
    lines: list[tuple[str, str]] = []
    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED),
        reporter=lambda stream, line: lines.append((stream, line)),
    )

    assert retry is False
    assert any(stream == "warn" and UNPROVISIONED in line for stream, line in lines)


def test_one_board_is_provisioned_exactly_once(paths, rr):
    """After the write the serial no longer matches, so a second sweep of the
    same bus has nothing to do. This is what stops the handler looping."""
    _family(paths, auto="auto_provision: true\n")

    provisioning.auto_provision(paths, _sweep(UNPROVISIONED))
    provisioning.auto_provision(paths, _sweep(PROVISIONED))

    assert rr.calls == [UNPROVISIONED]


def test_the_key_is_refused_on_a_family_that_cannot_provision(paths):
    from mcu_updater import typelist
    from mcu_updater.errors import ConfigCorruptError

    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware knomi]\n"
            "builder: platformio\n"
            "flashers: esptool\n"
            "helper: knomi_serial\n"
            "auto_provision: true\n"
        )

    entries, families = typelist.read_config(paths)
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.validate(entries, families, path=paths.main_config)

    assert "auto_provision" in str(exc.value)
    assert "knomi" in str(exc.value)


def test_the_provisioning_helper_list_matches_the_capability():
    """`typelist` checks the key without importing the helpers (Ruling 4), so
    the static tuple has to be held equal to the real thing."""
    from mcu_updater.helpers.registry import HELPERS

    assert set(firmware.PROVISIONING_HELPERS) == {
        helper.name
        for helper in HELPERS
        if helpers.provisioner(helper) is not None
    }


# --- the watcher --------------------------------------------------------------


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, name: str, payload: object) -> None:
        self.events.append((name, payload))


class _Dev:
    """The three attributes `_fingerprint` and the handler read."""

    def __init__(self, serial: str, fw: str = "Klipper", chipset: str = "stm32"):
        self.serial, self.fw, self.chipset = serial, fw, chipset


def _watcher(paths, monkeypatch, found, handler=None):
    from mcu_updater.agent.events import BusWatcher

    monkeypatch.setattr("mcu_updater.devices.scan", lambda p: found)
    return BusWatcher(
        paths, _Emitter(), serialize=lambda devices: [], on_change=handler
    )


def test_the_watcher_hands_the_handler_the_sweep_it_found(paths, monkeypatch):
    """The handler used to take no arguments and go and look for itself."""
    found = [_Dev("usb-Klipper_stm32-if00"), _Dev(UNPROVISIONED, "Roadrunner", "rp2040")]
    seen: list[dict] = []
    watcher = _watcher(paths, monkeypatch, found, lambda devices: seen.append(devices))

    watcher._poll()

    assert [sorted(sweep) for sweep in seen] == [
        sorted(device.serial for device in found)
    ]


def test_a_handler_asking_for_a_retry_runs_again_on_an_unchanged_bus(
    paths, monkeypatch
):
    calls: list[dict] = []

    def handler(devices):
        calls.append(devices)
        return len(calls) == 1  # ask once, then stop asking

    watcher = _watcher(paths, monkeypatch, [_Dev("S1")], handler)

    watcher._poll()  # the bus changed: the handler runs and asks for a retry
    watcher._poll()  # unchanged, but retried
    watcher._poll()  # unchanged and nothing asked: nothing runs

    assert len(calls) == 2, "asked again once, then left alone"


def test_an_unchanged_bus_emits_nothing_even_when_the_handler_reran(
    paths, monkeypatch
):
    """The retry is for the handler, not for the panel: the payload would be
    identical and a client cannot tell a repeat from a change."""
    watcher = _watcher(paths, monkeypatch, [_Dev("S1")], lambda devices: True)

    watcher._poll()
    watcher._poll()

    assert [name for name, _ in watcher.emitter.events] == ["bus"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_auto_provision.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcu_updater.provisioning'`, and `BusWatcher._poll` missing.

- [ ] **Step 3: The family key**

In `src/mcu_updater/firmware.py`, after `bootloader`:

```python
    #: Provision a board of this family that appears on the bus unprovisioned,
    #: without being asked. Opt in, and off everywhere it is not written: an
    #: unprovisioned board exposes only its identity and admin registers, so
    #: this cannot interrupt a print - but a `[firmware]` section should not
    #: write to hardware nobody mentioned. Refused on a family whose helper
    #: cannot provision. See `provisioning.auto_provision`.
    auto_provision: bool = False
```

and in `load_from_doc`, after `bootloader=...`:

```python
            auto_provision=bool(parse_bool(doc.get(section, "auto_provision"), False)),
```

Beside `HELPERS`:

```python
#: Helpers that implement the provisioning capability, for `typelist` to check
#: `auto_provision:` against. Static for the reason `HELPERS` is: `typelist`
#: must not import the helper implementations, and a test holds this equal to
#: the registry.
PROVISIONING_HELPERS: tuple[str, ...] = ("roadrunner",)
```

In `src/mcu_updater/typelist.py`, at the end of `_refuse_family_keys`:

```python
    cannot_provision = [
        family.name
        for family in families.values()
        if family.auto_provision and family.helper not in firmware.PROVISIONING_HELPERS
    ]
    if cannot_provision:
        listed = "\n".join(
            f"  [firmware {name}] auto_provision: true" for name in cannot_provision
        )
        raise ConfigCorruptError(
            f"{path}: auto_provision: on a family whose helper cannot provision "
            f"(helpers that can: {', '.join(firmware.PROVISIONING_HELPERS)}):\n"
            f"{listed}\nRemove the line, or name a helper that provisions.",
            path=path,
            family=cannot_provision[0],
            key="auto_provision",
            families=cannot_provision,
        )
```

- [ ] **Step 4: `provisioning.auto_provision`**

Create `src/mcu_updater/provisioning.py`:

```python
"""Giving a board that turned up its durable identity, unasked.

Spec section 10. The klippy extra provisions on `klippy:connect`, which covers
a board already configured in Klipper and nothing else. A board plugged into
the host and not yet configured anywhere is exactly the board somebody is
about to want to track, and it is invisible to that hook.

Opt in per family, because the setting lives on a `[firmware]` section and a
firmware section should not write to hardware nobody mentioned. Safe because an
unprovisioned Roadrunner exposes only its identity and admin registers - Klippy
cannot reach a printing state with one, so provisioning it interrupts nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import BusyError, UpdaterError
from .paths import Paths
from .reporters import Reporter, null_reporter


def auto_provision(
    paths: Paths,
    devices: Mapping[str, Any],
    *,
    reporter: Reporter = null_reporter,
) -> bool:
    """Provision every unprovisioned board an opted-in family recognises.

    `devices` is one bus sweep, keyed by serial - the only thing read from it
    is the keys, because whether a serial is an unprovisioned identity is a
    string question and the helper does its own confirmation over the wire
    before writing anything.

    Returns True when something should be tried again on the next poll. Today
    that is one case: the op lock was held, so the board is still sitting there
    unprovisioned and the bus fingerprint will not change to prompt a retry.
    Every other failure is reported and dropped - a board that refuses its
    probe will refuse it again in fifteen seconds.

    Never raises. This runs on the bus watcher's thread, and a watcher that
    dies takes the panel's device list with it.
    """
    from . import firmware, helpers, typelist
    from .lock import exclusive

    retry = False
    try:
        _entries, families = typelist.read_config(paths)
    except UpdaterError as exc:
        reporter("warn", f"could not read the firmware families: {exc}")
        return False

    for family in families.values():
        if not family.auto_provision:
            continue
        try:
            helper = helpers.for_name(family.helper, family=family.name)
        except UpdaterError as exc:
            # Refused at config load (typelist), so this is defence in depth
            # for a file edited under a running agent.
            reporter("warn", f"[firmware {family.name}]: {exc}")
            continue
        prov = helpers.provisioner(helper)
        if prov is None:
            continue
        for serial in sorted(devices):
            if not prov.is_unprovisioned(serial):
                continue
            try:
                # The lock covers selection, the irreversible write and the
                # re-enumeration handoff, exactly as fw.roadrunner.provision
                # takes it. Never retried inside this call: a write nobody
                # asked for must not queue behind a flash.
                with exclusive(paths, f"auto-provision {serial}"):
                    provisioned = prov.provision(paths, serial)
            except BusyError:
                retry = True
                continue
            except UpdaterError as exc:
                reporter("warn", f"{serial}: {exc}")
                continue
            reporter("info", f"Provisioned {serial} as {provisioned}.")
    return retry


__all__ = ["auto_provision"]
```

If `reporters.Reporter`/`null_reporter` live elsewhere in this tree, import them from wherever `flashers/flash.py` imports them.

- [ ] **Step 5: The watcher hands over its sweep and honours a retry**

In `src/mcu_updater/agent/events.py`, `BusWatcher.__init__`: the parameter becomes

```python
        on_change: Callable[[Mapping[str, Any]], Any] | None = None,
```

with the comment extended:

```python
        #: Run when the set of devices changes, before the event goes out, with
        #: the sweep keyed by serial. Used to adopt a board that has finally
        #: turned up from a bootloader install, so the `bus` event already
        #: reflects it rather than showing it as untracked for one poll and
        #: tracked the next - and to provision a board that appeared
        #: unprovisioned, for a family that asked. A truthy return means "ask
        #: me again next poll", even if the bus has not changed: a board that
        #: could not be provisioned because the lock was held is still sitting
        #: there, and nothing about the bus will change to prompt a retry.
        self._on_change = on_change
```

Add `self._retry = False` beside `self._last = None`, and add `Mapping` to the `collections.abc` import.

Replace the body of `_loop`'s `try:` with `self._poll()` and add `_poll` above it:

```python
    def _poll(self) -> None:
        """One sweep: hand it to the handler if anything is interested, then
        emit if the bus itself changed."""
        from .. import devices as devices_mod

        found = devices_mod.scan(self.paths)
        # devices.scan() already uses the shared USB inventory to map by-id tty
        # nodes to physical hardware serials. Fingerprinting every unrelated USB
        # device would emit an unchanged bus payload.
        fp = _fingerprint(found)
        changed = fp != self._last
        if not changed and not self._retry:
            return
        self._last = fp
        self._retry = False
        if self._on_change is not None:
            try:
                self._retry = bool(self._on_change({d.serial: d for d in found}))
            except Exception as exc:  # noqa: BLE001 - never kill the watcher
                if self._log is not None:
                    self._log.warning(f"bus change handler failed: {exc}")
        if changed:
            # Only on a real change: a retry's payload is identical, and a
            # client cannot tell a repeat from something moving.
            self.emitter.emit("bus", {"devices": self._serialize(found)})
```

- [ ] **Step 6: The agent wires both handlers**

In `src/mcu_updater/agent/service.py`, `on_change=self.api.adopt_paired` becomes `on_change=self._on_bus_change`, and add the method beside `_on_job_change`:

```python
    def _on_bus_change(self, devices: Mapping[str, Any]) -> bool:
        """Watcher-thread work for a changed bus.

        Late adoption first, because it completes something the user already
        asked for; then any family that asked to provision boards on sight.
        Each is insulated from the other: a failure in one is not a reason to
        skip the other, and neither may kill the watcher.
        """
        try:
            self.api.adopt_paired()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"late adoption failed: {exc}")

        from ..provisioning import auto_provision

        def reporter(stream: str, line: str) -> None:
            if stream == "warn":
                self.log.warning(line)
            else:
                self.log.info(line)

        try:
            return auto_provision(self.paths, devices, reporter=reporter)
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"auto-provisioning failed: {exc}")
            return False
```

Use whatever attribute this class already holds its `Paths` under; if it keeps the constructor argument in a local only, store it as `self.paths` in `__init__` in this commit.

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_auto_provision.py tests/test_typelist.py tests/test_agent_add_mcu.py tests/test_agent_service.py -q`
Expected: PASS. (`test_agent_service.py` only if it exists.)

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Mutation spec**

Create `scripts/mutations/auto-provision.json`:

```json
{
  "_comment": "Auto-provisioning on appearance. Opt in, one provision per board, a held lock skips and asks again, a refused probe does not, and the watcher's retry must not re-emit an unchanged bus.",
  "file": "src/mcu_updater/provisioning.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_auto_provision.py",
    "tests/test_typelist.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a family that did not ask is left alone",
      "find": "        if not family.auto_provision:\n            continue",
      "replace": "        if False:\n            continue"
    },
    {
      "name": "only a serial that looks unprovisioned is written to",
      "find": "            if not prov.is_unprovisioned(serial):\n                continue",
      "replace": "            if False:\n                continue"
    },
    {
      "name": "a held lock asks again",
      "find": "            except BusyError:\n                retry = True",
      "replace": "            except BusyError:\n                retry = False"
    },
    {
      "name": "a refused probe does not ask again",
      "find": "            except UpdaterError as exc:\n                reporter(\"warn\", f\"{serial}: {exc}\")\n                continue",
      "replace": "            except UpdaterError as exc:\n                reporter(\"warn\", f\"{serial}: {exc}\")\n                retry = True\n                continue"
    },
    {
      "name": "provisioning holds the op lock",
      "find": "                with exclusive(paths, f\"auto-provision {serial}\"):\n                    provisioned = prov.provision(paths, serial)",
      "replace": "                provisioned = prov.provision(paths, serial)"
    },
    {
      "name": "a retry runs the handler on an unchanged bus",
      "file": "src/mcu_updater/agent/events.py",
      "find": "        if not changed and not self._retry:",
      "replace": "        if not changed:"
    },
    {
      "name": "a retry does not re-emit an unchanged bus",
      "file": "src/mcu_updater/agent/events.py",
      "find": "        if changed:\n            # Only on a real change",
      "replace": "        if True:\n            # Only on a real change"
    },
    {
      "name": "auto_provision is refused on a family that cannot provision",
      "file": "src/mcu_updater/typelist.py",
      "find": "        if family.auto_provision and family.helper not in firmware.PROVISIONING_HELPERS",
      "replace": "        if False"
    }
  ]
}
```

Run, one at a time, waiting for each:

```bash
python scripts/mutation_test.py scripts/mutations/auto-provision.json
python scripts/mutation_test.py scripts/mutations/family-keys.json
python scripts/mutation_test.py scripts/mutations/provision-on-track.json
python scripts/mutation_test.py scripts/mutations/add-mcu.json
```

Expected: every mutation killed.

- [ ] **Step 9: Docs**

`docs/agent-api.md`, in the configuration-keys section for `[firmware …]`:

```markdown
`auto_provision: true` (default `false`) asks the agent to provision a board of
this family that appears on the bus with an unprovisioned identity, without
being asked. It runs on the bus watcher's poll, never from `fw.status`, and is
refused at config load on a family whose `helper:` cannot provision. A board
skipped because the operation lock was held is tried again on the next poll.
The resulting board is *not* tracked — `fw.serial.add` does that, and does its
own provisioning (see `prior_serial`).
```

`README.md`, in `## Features`, add one checked item:

```markdown
- [x] Optional auto-provisioning of a Roadrunner that appears unprovisioned
      (`auto_provision:` on its `[firmware]` section)
```

`docs/decisions.md`:

```markdown
### Auto-provisioning is opt in, inline on the watcher, and retried only when busy

Spec section 10, Ruling 14. The klippy extra provisions on `klippy:connect`,
which reaches only boards already configured in Klipper; the host's watcher is
the only thing that sees a board plugged in and configured nowhere. It is opt
in because a `[firmware]` section should not write to hardware nobody
mentioned, not because it is dangerous — an unprovisioned Roadrunner cannot
bring Klippy to a printing state, so provisioning one interrupts nothing.

Inline on the watcher thread, with no job: it is a sub-second helper call, and
a job entry would appear in the panel for something nobody asked for. A held
lock is the one failure worth retrying, because the bus will not change to
prompt one — so the handler asks the watcher to call it again. The retry does
not re-emit the `bus` event: the payload would be identical.
```

- [ ] **Step 10: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: all pass.

```bash
git add -A src/mcu_updater tests scripts/mutations docs README.md
git commit -m "feat(provisioning): auto-provision a board that appears, per family

auto_provision: is a [firmware] boolean, default off, refused on a family whose
helper cannot provision. The bus watcher hands its sweep to the handler, which
provisions every unprovisioned board an opted-in family recognises, under the
op lock. A held lock skips and asks the watcher to try again next poll.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: One verdict

Spec §9. Three functions answer "does this device want firmware, and why?" and they disagree: `status._device_status` for kconfig boards, `providers.pio.device_status` for screens, and a fixed `DeviceStatus(UNKNOWN_VERSION if present else OFFLINE)` stub for CMake boards, which is why a Roadrunner's panel row has never said anything. This task makes them one pure function over injected inputs, and adds the reason a measured image needs.

**Files:**
- Create: `src/mcu_updater/verdict.py`
- Modify: `src/mcu_updater/states.py:170-230` (the Q2 reason block, `_NEEDS_FLASH`, `_DEVICE_LABEL`)
- Modify: `src/mcu_updater/agent/methods/status.py:546-600` (`pio_status`), `:1021-1060` (`cmake_status`), `:1090-1140` (`_cmake_target`'s device loop), `:2557-2704` (`flash_state`, `_device_status`)
- Modify: `src/mcu_updater/agent/methods/bulk.py:140-160`, `:225-245` (both `flash_state` calls gain `built_version=`)
- Modify: `src/mcu_updater/providers/pio.py:297-333` (`device_status` deleted)
- Create: `tests/test_verdict.py`, `scripts/mutations/verdict.json`
- Modify: `tests/test_pio.py:545-602` (the `device_status` tests move out), `tests/test_states.py:210-225` (the device-verdict tests move out)
- Modify: `scripts/mutations/states.json` (its `command` gains `tests/test_verdict.py`, in this task's commit)
- Docs: `docs/agent-api.md:310-321`, `docs/decisions.md`
- Mutation specs at risk: `inventory.json` (Plan 1's; its fourth mutation anchors `status.py`'s `present = device_row is not None and device_row.present` inside the loop this task rewrites — Step 8 keeps that line verbatim as the anchor line above the replacement, so the spec survives: run it to confirm rather than assume), `states.json` (its mutations anchor `states.py` and survive, but two of them are only covered by tests this task moves — the `command` must gain `tests/test_verdict.py` in the same commit), `cartographer-version.json` (anchors `build.py`, `command` runs `tests/test_agent_methods.py`), `display-flash.json` (anchors `_platformio_confidence`, which this task does not touch — confirm, do not assume), `provenance-read.json` (Task 2 already re-anchored it). Run this first and re-anchor every hit in this task's own commit:

```bash
rg -n "device_status|flash_state|_device_status|built_version|fw_head|_FW_DIRTY_RE" scripts/mutations
rg -n "present = device_row" scripts/mutations
```

**Interfaces:**
- Consumes: `device_info.DeviceInfo`, `device_info.reader_for(family)`, `helpers.image_reporter`, `StatusMixin.reported_images` (Task 2); `build.FlashLog.entry_for`
- Produces:
  - `states.UNEXPECTED_IMAGE = "unexpected_image"` (`needs_flash: True`, tone attention, label `"Unexpected firmware"`)
  - `verdict.STAMP_BUILT = "built"`, `verdict.STAMP_TAG = "tag"`
  - `verdict.Evidence(state: str | None = None, version: str | None = None, running_sha: str | None = None, dirty: bool = False, protocol_match: bool | None = None, info: DeviceInfo | None = None)`, frozen
  - `verdict.Expected(head: str | None = None, stamp: str | None = None, stamp_kind: str = STAMP_BUILT, tag_clean: bool = False, require_head: bool = False, artifact_sha: str | None = None, record: Mapping[str, Any] | None = None, digest: Mapping[str, Any] | None = None)`, frozen. `digest` is a build sidecar, read by the four keys `uf2.digest_fields` writes — so a caller passes the whole sidecar and constructs nothing.
  - `verdict.decide(evidence: Evidence, expected: Expected) -> DeviceStatus`
  - Removed: `providers.pio.device_status`, `StatusMixin._device_status`
  - `cmake_status()` payloads gain `"sidecar"` (the CMake build sidecar, or `{}`)
  - CMake `targets[].devices[]` rows carry a real `version`, `confidence`, `needs_flash`, `tone`, `label` and `reason` instead of nulls and a stub

**Decisions this task makes.** Each one is a place where the three originals disagreed or where the unification has a choice to make. They are settled; do not relitigate them mid-task.

1. **A digest match is decisive and consults no record.** `Expected.digest` comes from the build sidecar, which is rewritten every time the artifact is, so it describes the image on disk *now*. A reported digest equal to it is a byte-level measurement that the board is running that image. `ARTIFACT_CHANGED` exists because a *commit* match is a weak proxy ("same commit, different binary" — `states.py`); asking the weaker witness to overrule the stronger one about the same fact would send someone to reflash a board that provably holds the newest image. Match → up to date, full stop.
2. **A digest match also outranks `DEVICE_DIRTY`.** `DEVICE_DIRTY` is `needs_flash: None`, "cannot be shown current". The digest shows it current. The "which tree built this?" worry is real and already lives on the artifact axis as `ArtifactStatus.built_dirty`; carrying it again here double-counts it.
3. **Steps 8 and 11 keep their record lookup.** They are only reached when one side had no digest, and there the commit-match-is-weak reasoning applies in full. Do not "simplify" the record out of them — that would delete `ARTIFACT_CHANGED`'s reason for existing.
4. **The head requirement belongs to the sha comparison, not to the caller.** A running commit with no tree commit to compare it against falls through to the stamp instead of answering `unknown_version` immediately. kconfig's old code answered `unknown_version` there; the new answer only differs when a sidecar version exists to fall back on, and it is strictly better (our own build record says which release we produced). This is the fourth unification in Ruling 15.
5. **`require_head` is the screens' rule only.** `pio.device_status` bailed out on a missing tree head *before* looking at the version at all, even on its tag path. That is preserved for screens and not extended to anyone else.
6. **CMake rows use the stamp path, not the sha path** (`head=None`). A Roadrunner reports the repository-wide `git describe`; `cmake.SourceState.sha` is *subtree*-scoped and the two "routinely disagree" (`cmake.py:148-151`). Comparing them would read every Roadrunner as behind whenever an unrelated part of its repo moved. The sidecar's `version` is the same repo-wide describe recorded at build time, so the stamp comparison is the one that means anything — and the digest above is the real strength for these boards anyway.
7. **`bulk.py`'s two `flash_state` calls gain `built_version=`.** They omit it today, so a Cartographer board reads `source_changed` on the panel and `unknown_version` in the fleet-flash selection — the panel paints a board as wanting firmware and the batch passes over it. That is the same class of silent wrong answer `_platformio_device_status`'s docstring exists to prevent.

---

- [ ] **Step 1: Write the failing verdict tests**

Create `tests/test_verdict.py`:

```python
"""One verdict, for every row shape.

`verdict.decide` replaced three functions that disagreed with each other:
`status._device_status` for kconfig boards, `providers.pio.device_status` for
screens, and a fixed `unknown_version` stub for CMake boards. The device-verdict
tests that lived in `tests/test_pio.py` and `tests/test_states.py` moved here
with them, because the whole point of the unification is that there is now one
place to assert against.

The rule every case here defends: **a verdict is never favourable on absent
evidence.** An offline board, an unreadable record, a tree that is not a
checkout - none of them are evidence that a board is current, and saying
otherwise leaves a toolhead running old firmware while the panel says it is
fine. The opposite error is just as real and has its own cases below: a wrong
"behind" sends someone to reflash a healthy board during a print.
"""

from __future__ import annotations

import pytest

from mcu_updater import uf2
from mcu_updater.device_info import SOURCE_KLIPPER, DeviceInfo
from mcu_updater.states import (
    ARTIFACT_CHANGED,
    DEVICE_DIRTY,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    TONE_ATTENTION,
    UNEXPECTED_IMAGE,
    UNKNOWN_VERSION,
    VERSION_ONLY,
    DeviceStatus,
)
from mcu_updater.verdict import STAMP_BUILT, STAMP_TAG, Evidence, Expected, decide

HEAD = "d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65"

#: What `uf2.digest_fields` writes into a build sidecar, and what a board
#: reports back for the same image - the same four key names on both sides, on
#: purpose, so the comparison needs no translation. The numbers are the
#: provenance spec's golden vector.
IMAGE = {
    "digest_algorithm": uf2.DIGEST_CRC32_ISO_HDLC,
    "digest": 0xBBE38AA9,
    "image_start": 0x10000000,
    "image_length": 600,
}


def _reported(version: str | None = "v1.2.0-3-gdeadbee", **overrides) -> DeviceInfo:
    """A board's own report of the image it is running."""
    return DeviceInfo(source=SOURCE_KLIPPER, version=version, **{**IMAGE, **overrides})


# -- the strongest signals, in order ---------------------------------------


def test_a_protocol_mismatch_is_checked_before_anything_else():
    """The device saying it cannot talk to this host's module at all. No
    version comparison has a word for that, so it must not be folded in."""
    status = decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", protocol_match=False),
        Expected(head="deadbee"),
    )
    assert status.reason == PROTOCOL_MISMATCH
    assert status.needs_flash is True


def test_a_silent_protocol_field_is_not_a_mismatch():
    """None is "it never said". Absence is never mismatch."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0", running_sha=None, protocol_match=None),
        Expected(stamp="v1.2.0"),
    ).reason is not PROTOCOL_MISMATCH


def test_an_offline_board_is_not_an_answer():
    status = decide(Evidence(state="offline", version="v1.2.0-3-gdeadbee"), Expected(head="deadbee"))
    assert status.reason == OFFLINE
    assert status.needs_flash is None


def test_a_board_in_its_bootloader_is_a_strong_yes():
    """It reports no application version at all, which is not "unknown": a
    board waiting in Katapult is the clearest possible signal it wants
    firmware."""
    status = decide(Evidence(state="katapult"), Expected(head=HEAD))
    assert status.reason == IN_BOOTLOADER
    assert status.needs_flash is True


# -- the measurement -------------------------------------------------------


def test_a_digest_that_disagrees_is_an_unexpected_image():
    """The board measured itself and got a different number than the artifact
    we hold. We do not know what is on it, and flashing is what makes it
    known."""
    status = decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported(digest=0x12345678)),
        Expected(head="deadbee", digest=IMAGE),
    )
    assert status.reason == UNEXPECTED_IMAGE
    assert status.needs_flash is True


def test_a_reported_range_that_disagrees_is_a_mismatch_too():
    """The board reports the range and a host must not substitute its own, so
    a board digesting a different extent than the artifact covers is not
    running the artifact - whatever the number came out as."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported(image_length=512)),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == UNEXPECTED_IMAGE


def test_a_digest_match_outranks_an_older_commit():
    """The measurement beats the claim. The board's version string says it is
    behind; the bytes say it is running exactly what is on disk, and a flash
    would write the same image back."""
    status = decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported()),
        Expected(head="deadbee", digest=IMAGE),
    )
    assert status.reason is None
    assert status.needs_flash is False


def test_a_digest_match_with_an_empty_reported_version_is_up_to_date():
    """A board too old to stamp a version, or a module that reported none, is
    still a board we have measured. Falling through to `unknown_version` here
    would throw away the strongest evidence in the whole function."""
    status = decide(
        Evidence(state="klipper", version="", info=_reported(version="")),
        Expected(digest=IMAGE),
    )
    assert status.reason is None
    assert status.needs_flash is False


def test_a_digest_match_outranks_a_dirty_build():
    """`device_dirty` means "cannot be shown current". The digest shows it
    current. Which tree produced it is a fact about the artifact, and it is
    already carried there as `built_dirty`."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee-dirty", running_sha="deadbee", dirty=True, info=_reported()),
        Expected(head="deadbee", digest=IMAGE),
    ).reason is None


def test_a_digest_match_never_consults_the_flash_record():
    """The record is a weaker, older witness to the same fact. A record saying
    we last wrote a different binary cannot overrule a measurement of the one
    that is running now - and `artifact_changed` would send someone to reflash
    a board holding the newest image."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported()),
        Expected(
            head="deadbee",
            digest=IMAGE,
            artifact_sha="new" + "0" * 61,
            record={"bin_sha256": "old" + "0" * 61},
        ),
    ).reason is None


def test_an_artifact_we_could_not_describe_is_never_a_mismatch():
    """`uf2.digest_fields` returns {} for a file it cannot parse. A build whose
    artifact cannot be described still built, and the comparison falls through
    to the version exactly as it does for a board that reports no digest."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported()),
        Expected(head="deadbee", digest={}),
    ).reason is None


def test_a_half_described_artifact_is_absence_not_mismatch():
    """A sidecar written before the digest fields existed carries some of the
    keys and not others. Comparing None to a real number would be a permanent
    mismatch no flash could ever clear."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported()),
        Expected(head="deadbee", digest={**IMAGE, "image_length": None}),
    ).reason == SOURCE_CHANGED


def test_a_board_that_reports_no_digest_falls_through_to_the_version():
    """Only the Roadrunner reports one today. Every other row must reach the
    version comparison untouched."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=None),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == SOURCE_CHANGED


def test_a_digest_the_board_could_not_compute_is_absence():
    """Algorithm 0 is a current board saying "I cannot", which `has_digest`
    reports as no digest - distinct from a board too old for the fields."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported(digest_algorithm=0)),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == SOURCE_CHANGED


# -- absence ---------------------------------------------------------------


@pytest.mark.parametrize("version", [None, ""])
def test_a_device_that_said_nothing_is_unknown(version):
    """Both shapes: a module that omits the field and one that sends an empty
    string said the same thing. The kconfig original only tested `is None`, so
    an empty string took the sha path with no sha in it."""
    status = decide(Evidence(state="klipper", version=version), Expected(head=HEAD, stamp="v1.2.0"))
    assert status.reason == UNKNOWN_VERSION
    assert status.needs_flash is None


def test_a_screen_with_no_tree_to_compare_against_is_unknown():
    """The screens' rule, kept: no git checkout means no verdict at all, even
    though the tree's VERSION file would still give a stamp."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head=None, stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason == UNKNOWN_VERSION


def test_a_board_with_no_tree_falls_back_on_what_we_built():
    """A board's own row does not require a head: with no checkout to compare
    a commit against, our own build record still says which release we
    produced, and that is more than nothing."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee"),
        Expected(head=None, stamp="v1.2.0-3-gdeadbee", record={"bin_sha256": "aa" * 32}, artifact_sha="aa" * 32),
    ).reason is None


def test_a_commit_with_no_tree_and_nothing_built_is_unknown():
    """Today's answer for a kconfig board, unchanged: nothing on either side
    to compare."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee"),
        Expected(head=None, stamp=None),
    ).reason == UNKNOWN_VERSION


# -- a build from an uncommitted tree --------------------------------------


def test_a_dirty_build_is_unprovable_rather_than_behind():
    """The sha may well match head, but the working tree it was built from is
    not recoverable, so "current" is unprovable - and it is not evidence of
    being behind either, hence None rather than True."""
    status = decide(
        Evidence(state="klipper", version="0.4.0+3.gd34db33.dirty", running_sha="d34db33", dirty=True),
        Expected(head="d34db33", require_head=True),
    )
    assert status.reason == DEVICE_DIRTY
    assert status.needs_flash is None


def test_a_reader_that_never_calls_a_build_dirty_is_believed():
    """Klipper's reader always answers False, because a makefile-patched type
    is `-dirty` by construction - the patch is in place while klipper stamps
    its version. Those boards would want a flash no flash could satisfy."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-712-g6d43f8b3-dirty", running_sha="6d43f8b3", dirty=False),
        Expected(head="6d43f8b3ddbfab679d1a64cb6f9f7adbe851ee82"),
    ).reason is None


# -- the commit comparison -------------------------------------------------


def test_an_older_commit_is_source_changed():
    status = decide(
        Evidence(state="klipper", version="v0.13.0-623-gaea1bcf5", running_sha="aea1bcf5"),
        Expected(head=HEAD),
    )
    assert status.reason == SOURCE_CHANGED
    assert status.needs_flash is True


def test_a_matching_commit_with_no_record_is_taken_at_face_value():
    """The flash log only ever *adds* doubt. Degrading every board that
    predates the log to "unknown" would be noise, not caution."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="aa" * 32, record=None),
    ).reason is None


def test_the_same_commit_with_a_different_binary_is_artifact_changed():
    """The case a version comparison structurally cannot see: edit the buffer
    patch, rebuild, and the boards still report the same klipper commit while
    holding last week's firmware."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="new" + "0" * 61, record={"bin_sha256": "old" + "0" * 61}),
    ).reason == ARTIFACT_CHANGED


def test_a_record_with_no_binary_recorded_invents_no_mismatch():
    """A record written before `bin_sha256` existed. Absence is never
    mismatch, here as everywhere."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="new" + "0" * 61, record={"bin_sha256": None}),
    ).reason is None


@pytest.mark.parametrize(
    ("running", "head"),
    [
        ("d34db33", "d34db3399aa"),
        ("d34db3399aa", "d34db33"),
        # Case is not dependable across the tools that produce these strings,
        # and the kconfig original compared with a case-sensitive startswith.
        ("D34DB33", "d34db3399aa"),
    ],
)
def test_short_shas_are_compared_on_the_shorter_of_the_two(running, head):
    """Different builds abbreviate to different lengths. Requiring one to be a
    prefix of the other in the recorded case was a mismatch that no flash
    could clear."""
    assert decide(Evidence(state="klipper", version=f"v1-1-g{running}", running_sha=running), Expected(head=head)).reason is None


def test_a_different_commit_of_the_same_length_still_differs():
    """The guard on the comparison above: shortening must not make everything
    match."""
    assert decide(
        Evidence(state="klipper", version="v1-1-gbadc0de", running_sha="badc0de"),
        Expected(head="d34db3399aa"),
    ).reason == SOURCE_CHANGED


# -- the stamp comparison, built ------------------------------------------


CARTO = "CARTOGRAPHER 6.2.0"


def test_a_stamped_version_with_nothing_built_is_unknown():
    """No built artifact to compare the stamp against, so there is nothing to
    say."""
    assert decide(Evidence(state="klipper", version=CARTO), Expected(head=HEAD, stamp=None)).reason == UNKNOWN_VERSION


def test_a_differing_stamp_is_source_changed():
    """CARTOGRAPHER 6.2.0 on the board, CARTOGRAPHER v4 6.2.0 out of the build
    - genuinely not our binary."""
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp="CARTOGRAPHER v4 6.2.0"),
    ).reason == SOURCE_CHANGED


def test_a_development_build_against_a_built_stamp_is_source_changed():
    """A Roadrunner built outside a tagged checkout stamps `dev`, which carries
    no commit. It is not unknown - we know what we built, and `dev` is not
    it."""
    assert decide(
        Evidence(state="klipper", version="dev"),
        Expected(stamp="v1.2.0-3-gdeadbee"),
    ).reason == SOURCE_CHANGED


def test_a_matching_stamp_with_no_record_is_version_only():
    """The honest amber: the release is recognised and the binary is not -
    distinct from `unknown_version`, which means nothing was recognised at
    all. A hand-maintained literal is identical in anyone's build of that
    release."""
    status = decide(Evidence(state="klipper", version=CARTO), Expected(head=HEAD, stamp=CARTO))
    assert status.reason == VERSION_ONLY
    assert status.needs_flash is None


def test_a_matching_stamp_backed_by_a_record_is_up_to_date():
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp=CARTO, artifact_sha="aa" * 32, record={"bin_sha256": "aa" * 32}),
    ).reason is None


def test_a_matching_stamp_with_a_stale_binary_is_artifact_changed():
    """Same release, different build - only the record can see it, exactly as
    on the commit path."""
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp=CARTO, artifact_sha="new" + "0" * 61, record={"bin_sha256": "old" + "0" * 61}),
    ).reason == ARTIFACT_CHANGED


def test_surrounding_whitespace_in_a_report_is_not_a_difference():
    assert decide(Evidence(state="klipper", version=f"  {CARTO}\n"), Expected(stamp=CARTO, record={})).reason is None


# -- the stamp comparison, tag --------------------------------------------


def test_a_release_build_on_a_clean_tag_is_current():
    """No sha at all means a clean build sitting exactly on the version tag. It
    is current only if the tree is still there - same version, still on the
    tag, still clean."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason is None


def test_a_release_build_off_the_tag_is_source_changed():
    """The tree has moved past the tag the screen reports, so the same version
    string no longer means the same build."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=False, require_head=True),
    ).reason == SOURCE_CHANGED


def test_a_release_build_of_another_version_is_source_changed():
    assert decide(
        Evidence(state="klipper", version="0.3.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason == SOURCE_CHANGED


def test_a_tree_with_no_version_of_its_own_says_nothing():
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp=None, stamp_kind=STAMP_TAG, require_head=True),
    ).reason == UNKNOWN_VERSION


def test_a_tag_match_needs_no_record_behind_it():
    """`version_only` is a board's word. It exists because a hand-maintained
    literal is identical in anyone's build of that release; a tag match is
    against a tree we can still see, so a record has nothing to add and a
    screen - which never has one - must not go amber for its absence."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True, record=None),
    ).reason is None


def test_the_built_stamp_is_the_default_kind():
    """Boards outnumber screens, and a caller that forgets the kind gets the
    comparison that consults our own build record rather than one that assumes
    a tree is sitting on a tag."""
    assert Expected().stamp_kind == STAMP_BUILT


# -- the new reason --------------------------------------------------------


def test_an_unexpected_image_asks_for_attention():
    """It has to be all three: `DEVICE_REASONS` derives from `_NEEDS_FLASH`, so
    a reason registered there alone constructs fine and raises on `.label`."""
    status = DeviceStatus(UNEXPECTED_IMAGE)
    assert status.needs_flash is True
    assert status.tone == TONE_ATTENTION
    assert status.label == "Unexpected firmware"


def test_the_default_verdict_is_still_nothing_to_do():
    """`decide` returns `DeviceStatus()` on every favourable path, and every
    one of them must land on the same object a caller tests with `is None`."""
    assert DeviceStatus().reason is None
    assert DeviceStatus().needs_flash is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_verdict.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'mcu_updater.verdict'`.

- [ ] **Step 3: Add the new reason**

In `src/mcu_updater/states.py`, after the `VERSION_ONLY` block (the one ending "once our own record backs the match."), add:

```python
#: The board measured the image it is running and got a number that is not the
#: artifact's. Stronger than any version comparison and pointed the other way:
#: `source_changed` says "we know what is on it and it is old", this says "we
#: do not know what is on it". Flashing is what makes it known.
UNEXPECTED_IMAGE = "unexpected_image"
```

Then add to `_NEEDS_FLASH`, immediately after the `ARTIFACT_CHANGED` line:

```python
    UNEXPECTED_IMAGE: True,
```

and to `_DEVICE_LABEL`, in the same position:

```python
    UNEXPECTED_IMAGE: "Unexpected firmware",
```

Both, not one: `DEVICE_REASONS` and `__post_init__` derive from `_NEEDS_FLASH`, so a reason added there alone constructs cleanly and raises `KeyError` the first time a panel asks for its label.

- [ ] **Step 4: Write the verdict**

Create `src/mcu_updater/verdict.py`:

```python
"""One verdict for every device row: `decide(evidence, expected)`.

Three functions used to answer "does this device want firmware, and why?" and
they disagreed: `agent.methods.status._device_status` for kconfig boards,
`providers.pio.device_status` for screens, and a fixed `unknown_version` stub
for CMake boards - which is why a Roadrunner's panel row never said anything at
all. They are one function now, pure over its two inputs so that every row
shape is testable without hardware.

The rules are the provenance spec's
(`docs/superpowers/specs/2026-09-11-cmake-provenance-design.md`), unchanged:

- **Klipper first.** What the device says it is running outranks what we infer
  from a file on disk.
- **Absence is never mismatch.** No version, no head, no record, no digest are
  all "cannot tell", never "behind". A wrong "behind" sends someone to reflash
  a healthy board during a print; a wrong "current" leaves a toolhead on old
  firmware while the panel says it is fine. Neither is acceptable, and every
  step below picks the answer that claims the least.
- **A measurement outranks a claim.** A reported digest is the running bytes;
  a version string is a story about them. When both sides have measured the
  same image, nothing weaker gets to overturn the result - not the version,
  not our own flash record, not a dirty tree.
- **The board's reported range is authoritative.** Start and length are
  compared field to field with the artifact's, and this host never substitutes
  its own.

`Evidence` is everything the device said. `Expected` is everything we hold for
it. Nothing in here opens a port, reads a file or runs git.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from .device_info import DeviceInfo
# The definition site rather than `devices`, which re-exports the BOOTSEL, DFU
# and USB scanners too - none of which this module has any business importing.
from .discovery.byid import STATE_KATAPULT, STATE_OFFLINE
from .states import (
    ARTIFACT_CHANGED,
    DEVICE_DIRTY,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    UNEXPECTED_IMAGE,
    UNKNOWN_VERSION,
    VERSION_ONLY,
    DeviceStatus,
)

#: The stamp is a version string our own build wrote down, so a match means "we
#: built what it reports" and nothing weaker. Cartographer's `CONFIG_VERSION`
#: and the Roadrunner's repo-wide `git describe` both land here.
STAMP_BUILT = "built"
#: The stamp is the source tree's own version, so a match only means anything
#: while the tree is still sitting on that tag, clean. The screens' rule.
STAMP_TAG = "tag"

#: The four INFO fields that make a digest comparable. Named once: the board's
#: report, the build sidecar and `uf2.digest_fields` all spell them this way on
#: purpose, so the comparison needs no translation in between.
_DIGEST_FIELDS = ("digest_algorithm", "digest", "image_start", "image_length")


@dataclasses.dataclass(frozen=True)
class Evidence:
    """What the device itself said. Nothing here was read off a disk."""

    #: A `discovery.byid` state - "klipper", "katapult", "offline". None where
    #: the caller has no liveness answer to offer, which is not the same as
    #: offline and must not read as one.
    state: str | None = None
    #: Exactly what the device reported, unparsed.
    version: str | None = None
    #: The commit inside `version` per the family's own reader, or None when
    #: the version carries none - normal for a release build, and permanent
    #: for a tree that stamps a hand-maintained literal.
    running_sha: str | None = None
    #: The device reports a build from an uncommitted tree. Per-family: a
    #: makefile-patched klipper is `-dirty` by construction, and its reader
    #: says False so those boards do not ask forever for a flash that cannot
    #: satisfy them.
    dirty: bool = False
    #: False only when the device says it cannot talk to this host's module.
    #: None is "it never said", which is not a mismatch.
    protocol_match: bool | None = None
    #: The image the board measured of itself, where it can measure one.
    info: DeviceInfo | None = None


@dataclasses.dataclass(frozen=True)
class Expected:
    """What we hold for it: the tree, the artifact, and our own ledger."""

    #: The source tree commit a running commit is compared against. None
    #: disables the comparison rather than failing it - see `decide`.
    head: str | None = None
    #: The version string to compare when there is no commit to compare.
    stamp: str | None = None
    stamp_kind: str = STAMP_BUILT
    #: STAMP_TAG only: the tree is still on the tag it stamped, and clean.
    tag_clean: bool = False
    #: No tree at all is "cannot tell" for this caller, whatever else it has.
    #: The screens' rule, and theirs alone.
    require_head: bool = False
    #: `bin_sha256` of the artifact on disk now.
    artifact_sha: str | None = None
    #: Our own `FlashLog` entry for this device - already discarded by
    #: `entry_for` if it disagrees with what the device reports running, so
    #: anything that arrives here is a record we still believe.
    record: Mapping[str, Any] | None = None
    #: The artifact's own digest fields, exactly as `uf2.digest_fields` writes
    #: them into a build sidecar. A caller passes the whole sidecar.
    digest: Mapping[str, Any] | None = None


def decide(evidence: Evidence, expected: Expected) -> DeviceStatus:
    """Does this device want firmware, and why?

    Ordered strongest evidence first. Every step that can only say "cannot
    tell" says so, rather than falling through to a favourable answer.
    """
    if evidence.protocol_match is False:
        # The device saying it cannot talk to this host's module at all. No
        # version comparison has a word for that, so it is asked first rather
        # than folded in.
        return DeviceStatus(PROTOCOL_MISMATCH)
    if evidence.state == STATE_OFFLINE:
        return DeviceStatus(OFFLINE)
    if evidence.state == STATE_KATAPULT:
        # Sitting in its bootloader, so it reports no application version at
        # all. Not "unknown" - the clearest possible signal that it wants
        # firmware.
        return DeviceStatus(IN_BOOTLOADER)

    measured = _image_verdict(evidence.info, expected.digest)
    if measured is not None:
        return measured

    if not evidence.version:
        # Empty string as well as None: a device that answered with nothing
        # said nothing, whichever shape its module used to say it.
        return DeviceStatus(UNKNOWN_VERSION)
    if expected.require_head and not expected.head:
        return DeviceStatus(UNKNOWN_VERSION)
    if evidence.dirty:
        # Built from uncommitted changes. The commit may well match head, but
        # the working tree it was built from is not recoverable, so "current"
        # is unprovable rather than merely unknown - and it is not evidence of
        # being behind either, hence a None verdict rather than True.
        return DeviceStatus(DEVICE_DIRTY)

    if evidence.running_sha and expected.head:
        # Short shas differ in length between builds and in case between the
        # tools that print them; compare over the shorter of the two.
        running = evidence.running_sha.lower()
        head = expected.head.lower()
        size = min(len(running), len(head))
        if running[:size] != head[:size]:
            return DeviceStatus(SOURCE_CHANGED)
        # The commit matches, so only our own record can tell two builds of it
        # apart. Used to *add* doubt and never to remove it: with no record the
        # commit match stands, rather than degrading every board to unknown.
        return _record_verdict(expected, absent_is=None)

    # A commit with no tree to compare it against falls through rather than
    # bailing out: our own build record still says which release we produced,
    # and that is more than nothing. It reaches `unknown_version` below anyway
    # when there is no stamp either, which is the old answer.
    if not expected.stamp:
        return DeviceStatus(UNKNOWN_VERSION)

    if expected.stamp_kind == STAMP_TAG:
        # The tree's own version, not ours. It means "current" only while the
        # tree is still on that tag with nothing uncommitted - the same release
        # built from a moved tree is a different build, and there is no record
        # on this path to tell them apart.
        if evidence.version.strip() == expected.stamp and expected.tag_clean:
            return DeviceStatus()
        return DeviceStatus(SOURCE_CHANGED)

    if evidence.version.strip() != expected.stamp:
        return DeviceStatus(SOURCE_CHANGED)
    # The stamp matches. Unlike the commit path there is no commit match to
    # stand on when the record is absent: a hand-maintained literal is
    # identical in anyone's build of that release, so here the absence of a
    # record is the difference between green and amber rather than a no-op.
    return _record_verdict(expected, absent_is=VERSION_ONLY)


def _image_verdict(
    info: DeviceInfo | None, expected: Mapping[str, Any] | None
) -> DeviceStatus | None:
    """The measurement, when both sides have one. None means "no evidence".

    Decisive in both directions, which is the point of measuring. A match is
    the running bytes against the bytes on disk, so nothing weaker overturns
    it - not the version string, not our own ledger, not a dirty tree. Our
    ledger in particular is a *record of what we last wrote*, which is the same
    question one step less directly and one flash out of date; asking it to
    overrule a measurement would report "newer build available" for a board
    provably holding the newest build.
    """
    if info is None or not info.has_digest() or not expected:
        return None
    ours = tuple(expected.get(field) for field in _DIGEST_FIELDS)
    if None in ours:
        # A sidecar from before these fields existed, or an artifact that could
        # not be parsed. Absence is never mismatch - comparing None to a real
        # number would be a permanent mismatch no flash could clear.
        return None
    theirs = tuple(getattr(info, field) for field in _DIGEST_FIELDS)
    if theirs != ours:
        return DeviceStatus(UNEXPECTED_IMAGE)
    return DeviceStatus()


def _record_verdict(expected: Expected, *, absent_is: str | None) -> DeviceStatus:
    """What our own flash record adds once the version already agrees.

    `absent_is` is what a missing record means on this path, and the two paths
    differ: a commit match stands on its own, a bare release string does not.
    """
    if expected.record is None:
        return DeviceStatus(absent_is)
    flashed = expected.record.get("bin_sha256")
    if flashed and expected.artifact_sha and flashed != expected.artifact_sha:
        # Same commit, different binary: an edited makefile patch or a changed
        # .config builds differently from an identical tree, and only our own
        # record knows which build the board actually got.
        return DeviceStatus(ARTIFACT_CHANGED)
    return DeviceStatus()


__all__ = ["STAMP_BUILT", "STAMP_TAG", "Evidence", "Expected", "decide"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_verdict.py -q`
Expected: PASS, every case.

- [ ] **Step 6: The kconfig rows go through it**

In `src/mcu_updater/agent/methods/status.py`, replace the body of `flash_state` from `entry = info.get(serial) or {}` to the `return {...}` (keep the signature and the docstring's first two paragraphs; the `reason` bullet list in it is now `verdict.decide`'s job and goes):

```python
        entry = info.get(serial) or {}
        version = entry.get("version")
        mcu = entry.get("mcu")
        running = reader.running_sha(version)
        # One lookup, two consumers. `version` is passed so a sha-less board's
        # record is governed by the same discard rule as its verdict: a
        # confidence read off a discarded record would be exactly as misleading
        # as a stale `bin_sha256`.
        record = (
            flashlog.entry_for(serial, running, version=version)
            if flashlog is not None
            else None
        )
        status = verdict.decide(
            verdict.Evidence(
                state=state,
                version=version,
                running_sha=running,
                dirty=reader.is_dirty(version),
            ),
            verdict.Expected(
                head=fw_head,
                stamp=built_version,
                artifact_sha=artifact_sha,
                record=record,
            ),
        )
        return {
            "mcu": mcu,
            "running_version": version,
            "running_sha": running,
            "needs_flash": status.needs_flash,
            "reason": status.reason,
            # Our own record of how the board's identity was confirmed the last
            # time this tool wrote to it - a `discovery.spec.Confidence.reason`
            # string, or None when there is no believable record. Never the live
            # discovery answer: that exists only inside a flash's own Klipper
            # stop, and a status poll must not pay for one.
            "confidence": (record or {}).get("confidence"),
        }
```

Replace the docstring's `reason` paragraph (everything from ```` `reason` is the useful part ```` to the end of the docstring) with:

```text
        `reason` is the useful part, because the answers are not equivalent -
        "in its bootloader" is a strong yes and "offline" is not an answer at
        all. `verdict.decide` holds the vocabulary and the ordering; this
        method's job is to assemble the two halves it judges from.
```

Delete `_device_status` entirely.

Add `verdict` to the package-level import at the top of the file (`from ... import API_VERSION, __version__, firmware, helpers, profiles, providers, typelist, verdict` — keep it alphabetical), and drop `ARTIFACT_CHANGED`, `IN_BOOTLOADER`, `OFFLINE`, `SOURCE_CHANGED`, `UNKNOWN_VERSION` and `VERSION_ONLY` from the `...states` import if ruff reports them unused. `PROTOCOL_MISMATCH`, `OFFLINE` and `DeviceStatus` stay: `_platformio_device_status` still layers them (Ruling 19), and `_cmake_target` still needs `STATE_OFFLINE`.

In `src/mcu_updater/agent/methods/bulk.py`, both `flash_state` calls gain `built_version=`. At `:150`, above the serial loop, after the `artifact_sha` line:

```python
            sidecar = read_sidecar(self.paths, name, application) or {}
            artifact_sha = sidecar.get("bin_sha256")
            # Passed for the same reason the panel passes it: a type whose
            # boards stamp a literal instead of a git describe has no commit to
            # compare, and omitting this here made the fleet-flash selection
            # disagree with the row the panel painted.
            built_version = sidecar.get("version")
```

and each `flash_state(...)` call in this file gains `built_version=built_version,` after `flashlog=flashlog,`. Apply the same two edits to the canbus loop at `:233`.

- [ ] **Step 7: The screens go through it**

In `src/mcu_updater/agent/methods/status.py`, `pio_status`: after `tree = pio_mod.source_state(display.source)`, add

```python
            # Once per type: every screen of it is judged against the same tree.
            expected = verdict.Expected(
                head=tree.head,
                stamp=tree.version,
                stamp_kind=verdict.STAMP_TAG,
                # A release build reports a bare version with no commit in it,
                # so it is current only while the tree is still sitting on that
                # tag with nothing uncommitted.
                tag_clean=tree.on_tag and not tree.dirty,
                # The screens' own rule, kept: no checkout means no verdict,
                # even though the VERSION file would still give a stamp.
                require_head=True,
            )
```

and replace `device = pio_mod.device_status(entry.get("firmware_version"), tree)` with:

```python
                running = entry.get("firmware_version")
                device = verdict.decide(
                    # No `state`: presence is layered on top by
                    # `_platformio_device_status`, which owns the `offline` and
                    # `protocol_mismatch` answers for a screen row.
                    verdict.Evidence(
                        version=running,
                        running_sha=pio_mod.running_sha(running),
                        dirty=pio_mod.is_dirty(running),
                    ),
                    expected,
                )
```

Delete `device_status` from `src/mcu_updater/providers/pio.py` whole, including the comment banner above it if it belongs to it, and drop whatever its removal leaves unused from that module's imports (`DeviceStatus` and the reason constants it alone used; `SOURCE_CHANGED` and `UNKNOWN_VERSION` are also used by `artifact_status`, so check rather than assume).

Move the tests: delete `tests/test_pio.py`'s `device_status` cases (from `test_a_screen_on_the_tree_head_is_current` through `test_an_empty_version_is_unknown`, and the `DeviceStatus`/reason imports they alone used) and `tests/test_states.py`'s two `pio.device_status` assertions. Their coverage is in `tests/test_verdict.py` already — do not re-add it, and do not leave a module with no tests behind.

- [ ] **Step 8: The CMake rows get a real verdict**

In `src/mcu_updater/agent/methods/status.py`, `cmake_status`, add to the payload dict after `"artifact_reason": status.reason,`:

```python
                    # The build's own record of the image on disk: its commit,
                    # the version it stamped, its `bin_sha256` and its digest
                    # fields. Read once per type here rather than once per
                    # board in the projection below.
                    "sidecar": cmake_mod.read_sidecar(self.paths, entry) or {},
```

In `_cmake_target`, after `helper_configured = helper is not None`:

```python
        from ...build import FlashLog
        from ... import device_info, verdict

        flashlog = FlashLog(self.paths)
        sidecar = payload["sidecar"]
        reader = device_info.reader_for(family)
        reporter = helpers.image_reporter(helper)
        # Klipper's own answer for every serial at once. A board Klippy is
        # holding is a board whose port cannot be opened, which is exactly when
        # its own measurement of its image matters most - and no port is opened
        # from a status poll, so a family whose helper cannot report through
        # Klipper simply has no digest here.
        reported = (
            self.reported_images(reporter, payload["serials"])
            if reporter is not None
            else {}
        )
        expected = verdict.Expected(
            # No `head`: a Roadrunner reports the repository-wide `git
            # describe`, and `cmake.SourceState.sha` is subtree-scoped - the two
            # "routinely disagree", so comparing them would read every board as
            # behind whenever an unrelated part of its repo moved. The stamp we
            # recorded building is the comparison that means something.
            stamp=sidecar.get("version"),
            artifact_sha=sidecar.get("bin_sha256"),
            digest=sidecar,
        )
```

and replace the two stub lines in the serial loop —

```python
            present = device_row is not None and device_row.present
            device_status = DeviceStatus(UNKNOWN_VERSION if present else OFFLINE)
```

— with:

```python
            present = device_row is not None and device_row.present
            info = reported.get(serial)
            version = info.version if info is not None else None
            running = reader.running_sha(version)
            record = flashlog.entry_for(serial, running, version=version)
            device_status = verdict.decide(
                verdict.Evidence(
                    state=device_row.state if device_row is not None else STATE_OFFLINE,
                    version=version,
                    running_sha=running,
                    dirty=reader.is_dirty(version),
                    info=info,
                ),
                dataclasses.replace(expected, record=record),
            )
```

In the `devices.append({...})` below it, `"version": None` becomes `"version": version` and `"confidence": None` becomes `"confidence": (record or {}).get("confidence")`. Both keys already exist on the wire and were placeholders; they carry the same meaning here as on an MCU row.

Drop `UNKNOWN_VERSION` and `DeviceStatus` from the file's `...states` import if ruff now reports them unused.

- [ ] **Step 9: Run the gates, and check what moved**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

The existing verdict tests — `tests/test_agent_methods.py:1500-1830`, `tests/test_agent_displays.py`, `tests/test_agent_targets.py` — must pass **unchanged**. This is the check the self-review cannot do for you: `decide` was written to reproduce three functions exactly except where a decision above says otherwise, and the only edits any of those files should need are fixtures for the new CMake row fields. If you find yourself editing an assertion about a *verdict*, stop and find out which step of `decide` diverged; the answer is a bug in this task, not a test that needs updating.

CMake row fixtures will need updating, and should: `tests/test_agent_targets.py:1130` and anything asserting a CMake device's `version`, `confidence`, `reason` or `needs_flash` was asserting the stub.

- [ ] **Step 10: Pin the verdict with mutation tests**

Create `scripts/mutations/verdict.json`:

```json
{
  "_comment": "The one verdict. Every mutation here is a way of claiming more than the evidence supports, in one direction or the other: a favourable answer on absence leaves a toolhead running old firmware while the panel says it is fine, and an unfavourable one sends someone to reflash a healthy board during a print. The ordering is as load-bearing as the rules - a measurement that a version string can overrule is not a measurement.",
  "file": "src/mcu_updater/verdict.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_verdict.py",
    "tests/test_agent_methods.py",
    "tests/test_agent_displays.py",
    "tests/test_agent_targets.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a device that cannot talk to this host is not judged on its version",
      "find": "    if evidence.protocol_match is False:",
      "replace": "    if False:"
    },
    {
      "name": "an offline device is never a verdict",
      "find": "    if evidence.state == STATE_OFFLINE:",
      "replace": "    if False:"
    },
    {
      "name": "a board in its bootloader is a yes, not an unknown",
      "find": "    if evidence.state == STATE_KATAPULT:",
      "replace": "    if False:"
    },
    {
      "name": "the measurement is consulted before the version string",
      "find": "    measured = _image_verdict(evidence.info, expected.digest)\n    if measured is not None:\n        return measured",
      "replace": "    measured = None"
    },
    {
      "name": "a digest that disagrees is reported rather than shrugged off",
      "find": "    if theirs != ours:\n        return DeviceStatus(UNEXPECTED_IMAGE)",
      "replace": "    if False:\n        return DeviceStatus(UNEXPECTED_IMAGE)"
    },
    {
      "name": "an artifact we could not describe is absence, not mismatch",
      "find": "    if None in ours:\n        return None",
      "replace": "    if False:\n        return None"
    },
    {
      "name": "a device that reported no digest is not compared as if it had",
      "find": "    if info is None or not info.has_digest() or not expected:",
      "replace": "    if info is None or not expected:"
    },
    {
      "name": "an empty reported version says as little as a missing one",
      "find": "    if not evidence.version:",
      "replace": "    if evidence.version is None:"
    },
    {
      "name": "a screen with no checkout behind it gets no verdict",
      "find": "    if expected.require_head and not expected.head:",
      "replace": "    if False:"
    },
    {
      "name": "a build from an uncommitted tree is never proven current",
      "find": "    if evidence.dirty:",
      "replace": "    if False:"
    },
    {
      "name": "shas are compared on the shorter of the two, not the reported one",
      "find": "        size = min(len(running), len(head))",
      "replace": "        size = len(head)"
    },
    {
      "name": "a differing commit is reported rather than passed over",
      "find": "        if running[:size] != head[:size]:\n            return DeviceStatus(SOURCE_CHANGED)",
      "replace": "        if False:\n            return DeviceStatus(SOURCE_CHANGED)"
    },
    {
      "name": "a bare release string with no tree left on its tag is not current",
      "find": "        if evidence.version.strip() == expected.stamp and expected.tag_clean:",
      "replace": "        if evidence.version.strip() == expected.stamp:"
    },
    {
      "name": "a stamp we did not build is reported",
      "find": "    if evidence.version.strip() != expected.stamp:\n        return DeviceStatus(SOURCE_CHANGED)",
      "replace": "    if False:\n        return DeviceStatus(SOURCE_CHANGED)"
    },
    {
      "name": "a matching literal with no record behind it is amber, not green",
      "find": "    return _record_verdict(expected, absent_is=VERSION_ONLY)",
      "replace": "    return _record_verdict(expected, absent_is=None)"
    },
    {
      "name": "a record naming another binary is not ignored",
      "find": "    if flashed and expected.artifact_sha and flashed != expected.artifact_sha:",
      "replace": "    if False:"
    }
  ]
}
```

Run, uninterrupted: `python scripts/mutation_test.py scripts/mutations/verdict.json`
Expected: every mutation caught.

Then add `"tests/test_verdict.py"` to `scripts/mutations/states.json`'s `command` array, after `"tests/test_states.py"` — two of its mutations (`DEVICE_DIRTY: None` and `UNKNOWN_VERSION: None`) were covered by the `pio.device_status` assertions Step 7 deleted, and it must not be left passing on tests that no longer exercise them.

Run, uninterrupted: `python scripts/mutation_test.py scripts/mutations/states.json`
Expected: every mutation caught.

Run, uninterrupted: `python scripts/mutation_test.py scripts/mutations/inventory.json`
Expected: every mutation caught. This is Plan 1's spec, run here because its
fourth mutation anchors a `status.py` line inside the loop Step 8 rewrote. Step 8
kept that line verbatim, so it should still match; if it reports `find not
found`, re-anchor it in this task's commit rather than deferring it.

- [ ] **Step 11: Documentation**

In `docs/agent-api.md`, after the `running_sha` / `version_only` paragraph (ending "once a record does back it."), add:

```markdown
`reason` can also be `"unexpected_image"` (attention, `needs_flash: true`). A
board that can measure the image it is running - today the Roadrunner, through
its klippy extra's `firmware_image` - reports a digest and the byte range it
covers, and this host compares them field for field against the build's own
record of the artifact on disk. A disagreement is not "behind": it means we do
not know what is on the board, and flashing is what makes it known. The
comparison outranks every version check, including a matching commit and a
matching release string, because a digest is a measurement of the running image
and a version string is a claim about it. It is equally decisive the other way:
a board whose digest matches the artifact is up to date even when its version
reads as older, and even when this tool's own flash record names a different
binary. Absence on either side - a board too old to report one, a board that
answers algorithm `0`, an artifact this host could not parse - falls through to
the version comparison untouched and is never reported as a mismatch.
```

Immediately after that, add:

```markdown
CMake type rows carry real device verdicts. Until now every `devices[]` entry
under a CMake target reported `version: null`, `confidence: null` and a fixed
`unknown_version`/`offline`, whatever the board was doing; they now use the same
`DeviceStatus` vocabulary, the same `confidence` field and the same rules as
every other row.
```

In `docs/decisions.md`, add under the provenance/verdict heading:

```markdown
### A digest match is the end of the question

`verdict.decide` checks the board's reported image digest before it looks at
any version string, and the check is decisive in both directions. A mismatch is
`unexpected_image`; a match is up to date, and neither a version that reads as
older, nor a dirty tree, nor this tool's own flash record can overturn it.

The tempting extra caution - "the digest matches, but our record says we last
wrote a different binary, so call it `artifact_changed`" - is wrong, and
`artifact_changed`'s own definition says why. It exists because a *commit*
match is a weak proxy: same commit, edited makefile patch, different bytes. The
record is what makes that case visible. A digest match is not a proxy for
anything; it is the running bytes measured against the bytes on disk, and the
sidecar computes `bin_sha256` and the digest fields from the same file in the
same write, so they cannot disagree about which image they describe. Letting
the weaker witness overrule the stronger one would report "newer build
available" for a board provably holding the newest build - and send someone to
reflash it mid-print.

The same reasoning puts the digest ahead of `device_dirty`, which is
`needs_flash: null`, "cannot be shown current". The digest shows it current.
The unrecoverable-tree worry behind `device_dirty` is real, but it is a fact
about the artifact and is already reported there as `built_dirty`; carrying it
on the device axis as well double-counts one doubt as two.
```

- [ ] **Step 12: Commit**

```bash
git add src/mcu_updater/verdict.py src/mcu_updater/states.py \
  src/mcu_updater/agent/methods/status.py src/mcu_updater/agent/methods/bulk.py \
  src/mcu_updater/providers/pio.py tests/test_verdict.py tests/test_pio.py \
  tests/test_states.py tests/test_agent_targets.py \
  scripts/mutations/verdict.json scripts/mutations/states.json \
  docs/agent-api.md docs/decisions.md
git commit -m "$(cat <<'EOF'
feat(status): one verdict for every device row

Three functions answered "does this device want firmware, and why?" and
disagreed: _device_status for kconfig boards, pio.device_status for
screens, and a fixed unknown_version stub for CMake boards, which is why
a Roadrunner's panel row never said anything. verdict.decide is one pure
function over an Evidence and an Expected, and every row goes through it.

A new reason, unexpected_image: a board that measures the image it is
running and gets a different number than the artifact on disk is not
behind, it is unaccounted for. The check outranks every version
comparison and is decisive both ways - a digest match is up to date even
against an older version string, a dirty tree, or a flash record naming
another binary, because a measurement is not a proxy for anything.

Also unified, and visible: an empty reported version is unknown for every
row shape; commits compare case-insensitively over the shorter of the
two; a commit with no tree behind it falls back on what we recorded
building; and bulk's fleet-flash selection passes built_version, so it
stops disagreeing with the row the panel paints.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: The loops cover every builder

Spec "Order of work" step 8, Ruling 17. The verdict Task 8 gave a CMake row is
now reachable from the selections, so a Roadrunner joins `fw.flash_all`,
`fw.update_all`, CLI `flash -t` and CLI `update-all` instead of being refused by
name. Three retirements fall out of it, all named in the spec: the
`type_not_bulk_flashable` code, the CLI's `-t`-only CMake refusal, and
`update-all`'s "No types configured." early exit.

**Files:**
- Modify: `src/mcu_updater/agent/methods/status.py:1090-1140` (extract
  `_cmake_devices` out of `_cmake_target`'s device loop)
- Modify: `src/mcu_updater/agent/methods/_api.py:56-100` (two new stubs)
- Modify: `src/mcu_updater/agent/methods/bulk.py:470-502` (delete
  `_require_flashable_type`), `:504-583` (`flash_all`), `:585-670`
  (`update_all`), plus the new `_cmake_boards_to_flash`
- Modify: `src/mcu_updater/providers/spec.py:51-84` (`Install.empty`)
- Modify: `src/mcu_updater/cli.py:779-800` (delete the CMake `-t` refusal),
  `:810-830` (the new CMake type branch), `:898-915` (`update_all`'s early
  exit), `:950-965` (`update_all`'s flash sweep)
- Test: `tests/test_agent_bulk.py:120-141` (`_declare_cmake` gains `serials`,
  `helper`, `staged`), `:906-970` (three refusal tests rewritten),
  `tests/test_cli.py:523-546` (`_cmake_flashable` gains `serials`), `:609-624`
  (the refusal test becomes a selection test), `tests/test_providers.py`
- Create: `scripts/mutations/loops.json`
- Docs: `docs/agent-api.md`, `docs/decisions.md`
- Mutation specs at risk: `bulk-operations.json` (its file is `bulk.py`; it
  anchors `if scope == "all" or info["needs_flash"] is True:` and
  `if state == STATE_OFFLINE:\n    continue\n` inside `_boards_to_flash`, and
  `targets = self._build_targets(install, scope, only).build` plus the second
  `assert_printer_idle` block inside `update_all`), `cli-every-type.json` (its
  `cli.py` anchors are all in `status_cmd`). Before editing either region run:

```bash
rg -n "_require_flashable_type|type_not_bulk_flashable|No types configured|_boards_to_flash|install.platformio" scripts/mutations
rg -n "present = device_row" scripts/mutations
```

Nothing this task deletes is anchored today. Confirm that, then leave both
files alone.

The second grep hits `inventory.json` (Plan 1's), whose fourth mutation anchors
`status.py`'s `present = device_row is not None and device_row.present`, indented
twelve spaces.
This task *moves* that line, from `_cmake_target`'s loop into `_cmake_devices`'s,
and the new method's loop sits at the same depth — method body plus one `for` —
so the line keeps its twelve spaces and the anchor still matches. Add
`inventory.json` to the specs you run in Step 13 to prove that, rather than
trusting the indentation by eye.

**Interfaces:**
- Consumes: Task 3 (`Bootsel.supports`/`Bootsel.target`, which reads
  `device.detail["uf2_file"]`), Task 4 (`bulk._board_request`,
  `flashers.select_each`, `_do_flash_all(ctx, targets, refused=())`,
  `cli._cmake_targets` returning a one-target list), Task 8
  (`verdict.decide`, `cmake_status()`'s `"sidecar"` key, `_cmake_target`'s
  device loop)
- Produces:
  - `StatusMixin._cmake_devices(payload: dict[str, Any], family: firmware.FirmwareFamily, helper: helpers.Helper | None, rows: dict[tuple[str, str, str], inventory_mod.Row] | None = None) -> list[dict[str, Any]]`, each entry
    `{"serial": str, "present": bool, "state": str, "path": str | None, "version": str | None, "confidence": str | None, "status": DeviceStatus}`
  - `BulkMixin._cmake_boards_to_flash(scope: str, only: str | None = None) -> list[dict]`, board dicts with the kconfig keys
    (`type`, `serial`, `chipset`, `fw`, `stop_services`, `state`, `reason`)
    plus `uf2_file`
  - `providers.Install.empty -> bool`
  - `_require_flashable_type` and the `type_not_bulk_flashable` code are gone

**Decisions this task makes:**

1. **`_cmake_boards_to_flash` is a sibling of `_canbus_boards_to_flash`, not a
   branch inside `_boards_to_flash`.** The two call sites already build the
   union in one expression, because a by-id serial and a CAN uuid are different
   identities with different liveness tests. A declared CMake serial is a third
   such identity. Folding it into `_boards_to_flash` would put a `cmake.load`
   walk inside a method whose first argument is the kconfig `Registry`.
2. **`_cmake_boards_to_flash` does not resolve the helper for flasher
   selection.** `flashers.select_each` resolves the family's helper itself, so a
   CMake type with no `helper:` produces a board that becomes a **refusal** in
   the batch's `failures[]` (Task 4) rather than a board silently dropped from
   the selection. The helper is resolved here only for `image_reporter`, which
   is what gives the verdict a digest to compare; a `ConfigCorruptError` from a
   misspelled helper name yields `helper=None`, meaning "no digest evidence",
   and the same refusal downstream says what is actually wrong.

   That `ConfigCorruptError` is reachable, not defensive: `cmake.load` reads the
   type list through `typelist.read_config`, which is deliberately lenient and
   never calls `typelist.validate`, so Task 1's refusal — which fires on the
   `typelist.load` path — does not cover this call. Task 10's `_watcher_map`
   wraps the same call for the same reason.
3. **A board not present on the by-id bus is skipped**, exactly as
   `_boards_to_flash` skips `STATE_OFFLINE`. This does mean a Roadrunner already
   sitting in BOOTSEL is not selected: it has no by-id entry while its
   mass-storage volume is mounted. That is not a regression — its verdict is
   `offline`, whose `needs_flash` is `None`, so it was never selectable — and
   `fw.flash` with its serial still writes it.
4. **`_cmake_devices` returns facts, not a wire projection.** `status` is the
   `DeviceStatus` object, not `_device_json`'s three keys, because the selection
   reads `needs_flash` and `reason` off it. Rebuilding either from a wire dict
   is the reversal this codebase keeps declining to do.
5. **`_cmake_devices` takes `rows=None` from `bulk.py`**, so the fleet selection
   pays one inventory sweep per CMake type rather than threading the panel's
   index through. `_boards_to_flash` already pays one `device_state` per serial;
   a host has one or two CMake types, and the alternative is an `inventory()`
   stub in `_api.py` for a saving nobody can measure.
6. **The CLI's type-level CMake flash calls `_cmake_targets` once per serial.**
   That re-reads `cmake.load` and `firmware.load` per board. Deliberate: the
   refusals `_cmake_targets` raises (no helper, nothing staged) are per-type and
   so identical for every serial, the first one aborts the loop, and a second
   copy of its body kept in step with it is the cost this whole branch exists to
   avoid.
7. **`Install.empty` replaces `update-all`'s two-provider test.** The early exit
   read `not install.registry and not install.platformio`, which told a
   Roadrunner-only host it had no types configured. Asking the seam one question
   is what stops the fourth provider reintroducing it; enumerating three maps at
   the call site would not.
8. **A CMake type that cannot be enumerated in `cli.update_all` is a SKIP and a
   failure, matching `_pio_targets`.** `_cmake_targets` raises `UpdaterError`
   for a missing helper, a missing artifact and `NoFlasherError`; each is a
   configuration gap the operator has to fix, worth naming and worth a non-zero
   exit, and not worth abandoning the rest of the fleet over.

- [ ] **Step 1: Write the failing selection tests**

In `tests/test_agent_bulk.py`, replace `_declare_cmake` whole with:

```python
def _declare_cmake(
    paths,
    fake_root,
    name="roadrunner",
    *,
    serials=(),
    helper=False,
    staged=False,
) -> str:
    """A cmake type with a source tree, and optionally the three things that
    make it flashable.

    Opt-in rather than part of the `bulk` fixture: several tests here assert on
    the *exact* pair list a sweep produces, and a type declared for everybody
    would break them for a reason that has nothing to do with what they check.
    The three flags are opt-in for the same reason - the build tests want the
    bare declaration, and only the flash tests want a writable one.

    The tree needs a CMakeLists.txt and deliberately no `build/`: `blocked()`
    reads the first and would shell out to real `cmake` for the target list if
    the second existed, which is not a thing a unit test should depend on.
    """
    tree = os.path.join(fake_root, "roadrunner", "rp2040")
    os.makedirs(tree, exist_ok=True)
    with open(os.path.join(tree, "CMakeLists.txt"), "w", encoding="utf-8") as fh:
        fh.write("project(roadrunner)\n")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\n"
            f"flashers: bootsel\n"
            + ("helper: roadrunner\n" if helper else "")
            + f"\n[type {name}]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_i2c_rgb\n"
            + (f"serials: {', '.join(serials)}\n" if serials else "")
        )
    if staged:
        os.makedirs(paths.artifact_dir(name), exist_ok=True)
        with open(paths.uf2_file(name, "roadrunner"), "wb") as fh:
            fh.write(b"UF2\n")
    return tree
```

Add beside the other constants at the top of the file:

```python
RR = "roadrunner"
RR_CHIPSET = "rp2040"
RR_SERIAL = "5K3DNTFCR1B3C9D0RZMYA3Y720"
RR_SERIAL_B = "5K3DNTFCR1B3C9D0RZMYA3Z831"
```

Append these tests to the file:

```python
# --------------------------------------------------------------------------
# the third builder joins the loops
#
# `_boards_to_flash` walks the kconfig registry and `_canbus_boards_to_flash`
# the tracked CAN uuids. A Roadrunner belonged to neither, so a fleet flash
# wrote every board on the host except the one whose panel row said it was
# behind - and `fw.flash_all {name: "roadrunner"}` refused by name rather than
# doing it.
# --------------------------------------------------------------------------


def test_a_present_cmake_board_joins_the_flash_selection(bulk, paths, fake_root):
    """Spec §8: the loop covers every builder.

    `unknown_version` is the verdict because nothing here reports a running
    image for the board - Klipper has no `roadrunner ...` object in this
    fixture's object list - and Task 8's `decide` answers unknown before it
    reaches the stamp. That is a `needs_flash: True` reason, which is what puts
    the board in a `stale` selection.
    """
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    boards = bulk._cmake_boards_to_flash("stale")

    assert [b["serial"] for b in boards] == [RR_SERIAL]
    board = boards[0]
    assert board["type"] == RR
    assert board["fw"] == RR
    assert board["chipset"] == RR_CHIPSET
    assert board["state"] == "klipper"
    assert board["reason"] == "unknown_version"
    assert "klipper" in board["stop_services"]


def test_a_cmake_board_carries_the_staged_uf2(bulk, paths, fake_root):
    """`Bootsel.target` reads `detail["uf2_file"]` and the selection dict rides
    whole as `detail` (Task 4), so the path the build staged has to be in the
    dict. Without it the batch raises `KeyError: 'uf2_file'` mid-outage."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    [board] = bulk._cmake_boards_to_flash("stale")

    assert board["uf2_file"] == paths.uf2_file(RR, RR)


def test_an_absent_cmake_board_is_never_selected(bulk, paths, fake_root):
    """The same exclusion `_boards_to_flash` makes for an offline serial: a
    flash needs the device on the bus, so including it would produce a
    guaranteed failure partway through a batch that has already stopped
    Klipper."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    # No `make_device`: declared, tracked, not plugged in.

    assert bulk._cmake_boards_to_flash("stale") == []
    assert bulk._cmake_boards_to_flash("all") == []


def test_a_cmake_type_with_nothing_staged_is_never_selected(bulk, paths, fake_root):
    """A type with no built artifact has nothing to write. `scope: all`
    overrides the version judgement, never the physics."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    assert bulk._cmake_boards_to_flash("stale") == []
    assert bulk._cmake_boards_to_flash("all") == []


def test_scope_all_forces_a_cmake_board_and_says_so(bulk, paths, fake_root):
    """`forced` rather than the verdict's own reason, exactly as the other two
    selections report it - the reason field says why *this* board is in the
    list, and under `all` that is the scope, not the judgement."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    [board] = bulk._cmake_boards_to_flash("all")

    assert board["reason"] == "forced"


def test_a_cmake_selection_covers_every_declared_serial(bulk, paths, fake_root):
    """Per-device, not per-type: two boards of one model genuinely do run
    different firmware, which is why the panel gives each its own row."""
    _declare_cmake(
        paths, fake_root, serials=[RR_SERIAL, RR_SERIAL_B], helper=True, staged=True
    )
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL_B)

    boards = bulk._cmake_boards_to_flash("stale")

    assert sorted(b["serial"] for b in boards) == sorted([RR_SERIAL, RR_SERIAL_B])


def test_a_named_type_narrows_the_cmake_selection_too(bulk, paths, fake_root):
    """`name` is a filter over the one selection, not a second one. A name that
    is not this type selects nothing here rather than everything."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    assert bulk._cmake_boards_to_flash("stale", RR) != []
    assert bulk._cmake_boards_to_flash("stale", EBB) == []


def test_flash_all_selects_cmake_boards_beside_the_others(bulk, paths, fake_root):
    """The union at the call site, through the wire result. `fw.flash_all`
    returns its selection up front so the panel can name the boards in its
    confirmation, and a Roadrunner missing from that list was missing from the
    outage that followed."""
    make_device(fake_root / "bus", "Klipper", EBB_CHIPSET, EBB_A)
    _stage_artifact(paths, EBB)
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    res = bulk.dispatch("fw.flash_all", {"scope": "all"})

    assert RR_SERIAL in [b["serial"] for b in res["boards"]]
    assert RR in [b["type"] for b in res["boards"]]
    assert bulk.runner.wait(timeout=60)


def test_a_cmake_type_is_no_longer_refused_by_name(bulk, paths, fake_root):
    """Retires `type_not_bulk_flashable`. The refusal was honest while
    `_boards_to_flash` had no CMake branch; with one, refusing the name would
    be the only way left to leave the board behind.
    """
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    res = bulk.dispatch("fw.flash_all", {"name": RR})

    assert [b["serial"] for b in res["boards"]] == [RR_SERIAL]
    assert bulk.runner.wait(timeout=60)
```

`_stage_artifact` is already in this file; a built `klipper.bin` is what makes
the kconfig type `EBB` selectable beside the Roadrunner.

- [ ] **Step 2: Run them to watch them fail**

```bash
python -m pytest tests/test_agent_bulk.py -q -k "cmake"
```

Expected: FAIL. The seven `_cmake_boards_to_flash` tests raise
`AttributeError: 'Api' object has no attribute '_cmake_boards_to_flash'`, and
the two dispatch tests raise `RpcError` with `type_not_bulk_flashable` (the
named one) or come back with no Roadrunner in `boards` (the union one).

- [ ] **Step 3: Extract the verdict out of the panel row**

In `src/mcu_updater/agent/methods/status.py`, insert this method immediately
before `_cmake_target`:

```python
    def _cmake_devices(
        self,
        payload: dict[str, Any],
        family: firmware.FirmwareFamily,
        helper: helpers.Helper | None,
        rows: dict[tuple[str, str, str], inventory_mod.Row] | None = None,
    ) -> list[dict[str, Any]]:
        """What each of a cmake type's declared serials is running, and whether
        it is behind.

        One verdict with two readers: the panel row projects it onto the wire,
        and the fleet-flash selection turns it into a board to write. While this
        lived inside `_cmake_target`, `bulk.py` had no CMake branch at all -
        `fw.flash_all` refused a Roadrunner by name and `update-all` walked past
        one - which is spec §8's rule read backwards. The loop covers every
        builder, so the judgement it loops over has to be reachable from the
        loop.

        Returns facts rather than a projection: `status` is the `DeviceStatus`
        itself, not `_device_json`'s three keys, because the selection reads
        `needs_flash` and `reason` off it and rebuilding either from a wire dict
        is the reversal this codebase keeps declining to do.

        `rows` is the caller's inventory index when it has one. `fw.status`
        builds one index for every provider and passes it; the fleet selection
        passes nothing and pays one sweep per cmake type, which is the same
        order of cost `_boards_to_flash` already pays per serial.
        """
        from ... import device_info, verdict
        from ...build import FlashLog

        name = payload["name"]
        flashlog = FlashLog(self.paths)
        sidecar = payload["sidecar"]
        reader = device_info.reader_for(family)
        reporter = helpers.image_reporter(helper)
        # Klipper's own answer for every serial at once. A board Klippy is
        # holding is a board whose port cannot be opened, which is exactly when
        # its own measurement of its image matters most - and no port is opened
        # from a status poll, so a family whose helper cannot report through
        # Klipper simply has no digest here.
        reported = (
            self.reported_images(reporter, payload["serials"])
            if reporter is not None
            else {}
        )
        expected = verdict.Expected(
            # No `head`: a Roadrunner reports the repository-wide `git
            # describe`, and `cmake.SourceState.sha` is subtree-scoped - the two
            # "routinely disagree", so comparing them would read every board as
            # behind whenever an unrelated part of its repo moved. The stamp we
            # recorded building is the comparison that means something.
            stamp=sidecar.get("version"),
            artifact_sha=sidecar.get("bin_sha256"),
            digest=sidecar,
        )

        if rows is None:
            rows = inventory_mod.index(self.inventory())
        out: list[dict[str, Any]] = []
        for serial in payload["serials"]:
            device_row = rows.get((name, inventory_mod.SERIAL, serial))
            state = device_row.state if device_row is not None else STATE_OFFLINE
            info = reported.get(serial)
            version = info.version if info is not None else None
            running = reader.running_sha(version)
            record = flashlog.entry_for(serial, running, version=version)
            out.append(
                {
                    "serial": serial,
                    "present": device_row is not None and device_row.present,
                    "state": state,
                    "path": device_row.path if device_row is not None else None,
                    "version": version,
                    "confidence": (record or {}).get("confidence"),
                    "status": verdict.decide(
                        verdict.Evidence(
                            state=state,
                            version=version,
                            running_sha=running,
                            dirty=reader.is_dirty(version),
                            info=info,
                        ),
                        dataclasses.replace(expected, record=record),
                    ),
                }
            )
        return out
```

Then in `_cmake_target`, delete everything Task 8 added between
`helper_configured = helper is not None` and `if rows is None:` (the
`FlashLog`/`device_info`/`verdict` imports, `flashlog`, `sidecar`, `reader`,
`reporter`, `reported` and `expected`), delete the `if rows is None:` pair of
lines, and replace the loop header and the six lines Task 8 put at the top of
the body —

```python
        if rows is None:
            rows = inventory_mod.index(self.inventory())
        devices: list[dict[str, Any]] = []
        for serial in payload["serials"]:
            device_row = rows.get((name, inventory_mod.SERIAL, serial))
            present = device_row is not None and device_row.present
            info = reported.get(serial)
            version = info.version if info is not None else None
            running = reader.running_sha(version)
            record = flashlog.entry_for(serial, running, version=version)
            device_status = verdict.decide(
                verdict.Evidence(
                    state=device_row.state if device_row is not None else STATE_OFFLINE,
                    version=version,
                    running_sha=running,
                    dirty=reader.is_dirty(version),
                    info=info,
                ),
                dataclasses.replace(expected, record=record),
            )
```

— with:

```python
        devices: list[dict[str, Any]] = []
        for device in self._cmake_devices(payload, family, helper, rows):
            serial = device["serial"]
            present = device["present"]
```

and replace the `devices.append({...})` block's five affected lines so it reads:

```python
            devices.append(
                {
                    "id": serial,
                    "name": None,
                    "present": present,
                    "state": device["state"],
                    "path": device["path"],
                    "version": device["version"],
                    "confidence": device["confidence"],
                    **self._device_json(device["status"]),
                    "actions": device_actions,
                }
            )
```

The `device_actions` block between them is untouched: it reads `present`,
`payload` and `allowed`, all of which still mean what they did.

`name` is still used by `_cmake_target` (the action params and the returned
row), so leave its assignment alone. Drop `STATE_OFFLINE` from the file's
`...devices` import only if ruff reports it unused - other status paths use it.

- [ ] **Step 4: Teach `_api.py` the two new calls**

In `src/mcu_updater/agent/methods/_api.py`:

1. Add to the imports, after `from ... import flashers`:

```python
from ... import inventory
from ...firmware import FirmwareFamily
from ...helpers import Helper
```

2. Add to the `# -- status.py` block, after the `flash_state` stub:

```python
    def cmake_status(self) -> list[dict[str, Any]]: ...
    def _cmake_devices(
        self,
        payload: dict[str, Any],
        family: FirmwareFamily,
        helper: Helper | None,
        rows: dict[tuple[str, str, str], inventory.Row] | None = None,
    ) -> list[dict[str, Any]]: ...
```

- [ ] **Step 5: The third selection**

In `src/mcu_updater/agent/methods/bulk.py`, insert this method immediately after
`_canbus_boards_to_flash` and before `_screens_to_flash`:

```python
    def _cmake_boards_to_flash(
        self, scope: str, only: str | None = None
    ) -> list[dict]:
        """The cmake counterpart to `_boards_to_flash`: which declared serials
        of a cmake type a flash_all should write, with the reason for each.

        A sibling rather than a branch inside `_boards_to_flash`, for the same
        reason the CAN selection is one: a by-id serial, a CAN uuid and a
        declared cmake serial are three identities with three liveness tests,
        and the two call sites already build the union in one expression. It
        takes no `Registry`, because the kconfig registry is not where these
        types live.

        The verdict is `_cmake_devices`, the same one the panel row shows, so a
        board reading `source_changed` in the UI is a board this selects. They
        disagreed before: the panel had a fixed stub and this had no branch.

        Two exclusions, both the same as the other selections'. A type with
        nothing staged has nothing to write, and a board that is not on the bus
        cannot be written to - which does leave out a board already sitting in
        BOOTSEL, since its by-id entry is gone while its volume is mounted. Its
        verdict is `offline`, so it was never selectable anyway, and `fw.flash`
        with its serial still reaches it.

        No helper is resolved for the *write*: `flashers.select_each` asks the
        family, so a type with no `helper:` becomes a refusal in the batch's
        failures rather than a board quietly missing from the selection. The
        helper resolved here is the one that reports the running image, and a
        misspelled name means "no digest evidence" instead of blanking the
        sweep.
        """
        from ...providers import cmake as cmake_mod

        families = firmware.load(self.paths)
        settings = self.settings()
        entries = cmake_mod.load(self.paths)

        out: list[dict] = []
        for payload in self.cmake_status():
            name = payload["name"]
            if only is not None and name != only:
                continue
            if not payload["has_firmware"]:
                continue
            entry = entries[name]
            family = firmware.resolve(self.paths, payload["firmware"], families)
            try:
                helper = helpers.for_name(family.helper, family=family.name)
            except ConfigCorruptError:
                helper = None
            # Once per type, not per serial - every board of this type shares
            # the same resolved list and the same staged image.
            units = stop_services.for_cmake(self.paths, entry, settings, families)
            uf2 = self.paths.uf2_file(name, payload["firmware"])
            for device in self._cmake_devices(payload, family, helper):
                if not device["present"]:
                    continue
                if scope != "all" and device["status"].needs_flash is not True:
                    continue
                out.append(
                    {
                        "type": name,
                        "serial": device["serial"],
                        "chipset": payload["chipset"],
                        # The family, carried so the flash writes what this
                        # board runs rather than assuming klipper.
                        "fw": payload["firmware"],
                        # `Bootsel.target` reads this off `detail`. The dict
                        # rides whole (`_board_request`), so the path the build
                        # staged travels with the board that needs it.
                        "uf2_file": uf2,
                        "stop_services": list(units),
                        "state": device["state"],
                        "reason": (
                            "forced" if scope == "all" else device["status"].reason
                        ),
                    }
                )
        return out
```

Add `helpers` to the package import line, which becomes:

```python
from ... import firmware, flashers, helpers, inventory, providers, stop_services
```

and add `ConfigCorruptError` to the `from ...errors import (...)` block, in
sorted order.

- [ ] **Step 6: Both call sites take the union, and the refusal goes**

Still in `src/mcu_updater/agent/methods/bulk.py`:

1. Delete `_require_flashable_type` whole, including its docstring and any
   section banner comment that belongs to it alone.
2. In `flash_all`, replace

```python
        if only is not None:
            only = self._require_flashable_type(str(only))
```

with

```python
        if only is not None:
            only = str(only)
            # A typo, refused before a job exists. Nothing else to check: every
            # provider's devices are selectable now, which is what retired
            # `type_not_bulk_flashable`.
            self._provider_of(only)  # RpcError unknown_type
```

3. In `flash_all`, replace the `boards = ...` statement with:

```python
        # Every identity a type can declare, and none excludes the others - a
        # type may legitimately track `serials:` and `canbus_uuids:` both, and a
        # cmake type's serials are a third list in a different document.
        boards = (
            self._boards_to_flash(reg, scope, only)
            + self._canbus_boards_to_flash(reg, scope, only)
            + self._cmake_boards_to_flash(scope, only)
        )
```

4. In `update_all`, make the same replacement of the
   `_require_flashable_type` pair as in step 2, and replace its `run` body's
   `boards = ...` statement with:

```python
            reg_now = self.registry()
            boards = (
                self._boards_to_flash(reg_now, scope, only)
                + self._canbus_boards_to_flash(reg_now, scope, only)
                + self._cmake_boards_to_flash(scope, only)
            )
```

`providers` stays imported: `flash_all`'s `enable_flashing` refusal does not
use it, but `_install` and `_build_targets` do. Check with ruff rather than
assuming.

- [ ] **Step 7: Run the agent tests**

```bash
python -m pytest tests/test_agent_bulk.py tests/test_agent_targets.py -q
```

Expected: the nine new tests pass. The three old refusal tests
(`test_a_cmake_type_is_refused_by_name_not_as_a_typo`,
`test_a_cmake_type_never_reports_a_successful_flash_of_nothing`,
`test_update_all_refuses_a_cmake_type_the_same_way`) now fail - they assert the
behaviour this task retires, and step 8 deletes them.
`test_an_unknown_type_still_reports_unknown_type` must still pass: the typo path
is the one thing `_require_flashable_type` did that survives it.

- [ ] **Step 8: Retire the three refusal tests**

In `tests/test_agent_bulk.py`, delete `test_a_cmake_type_is_refused_by_name_not_as_a_typo`,
`test_a_cmake_type_never_reports_a_successful_flash_of_nothing` and
`test_update_all_refuses_a_cmake_type_the_same_way`, and replace them with one
test that pins what `update_all` now does:

```python
def test_update_all_covers_a_cmake_type_end_to_end(bulk, paths, fake_root):
    """`update_all` is build_all then flash_all, so a provider joining the
    flash half joins this without it being edited. It used to refuse the name
    outright, having refused it in `flash_all` first.

    Asserts the selection rather than the write: the build half runs for real
    under `dry_run`, and what this task changed is which boards the flash half
    then finds.
    """
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    res = bulk.dispatch("fw.update_all", {"scope": "all", "name": RR})

    assert res["name"] == RR
    assert bulk.runner.wait(timeout=60)
    job = bulk.runner.get(res["job_id"])
    assert [f["id"] for f in job.result["flash"]["flashed"]] == [RR_SERIAL]
```

- [ ] **Step 9: `Install.empty`, with its test**

In `tests/test_providers.py`, append:

```python
def test_a_host_with_no_types_at_all_is_empty(paths, settings):
    """What `update-all`'s early exit was actually asking."""
    assert Install.load(paths, settings).empty is True


def test_a_host_whose_only_type_is_cmake_is_not_empty(paths, settings, fake_root):
    """The bug in one assertion. `update-all` read `not registry and not
    platformio` and told a Roadrunner-only host it had no types configured -
    and would have said it again for the next provider. One question, asked of
    the seam, is what stops that."""
    tree = os.path.join(fake_root, "roadrunner", "rp2040")
    os.makedirs(tree, exist_ok=True)
    with open(os.path.join(tree, "CMakeLists.txt"), "w", encoding="utf-8") as fh:
        fh.write("project(roadrunner)\n")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\n"
            "flashers: bootsel\n\n"
            "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
        )

    assert Install.load(paths, settings).empty is False
```

Run: `python -m pytest tests/test_providers.py -q -k empty`
Expected: FAIL with `AttributeError: 'Install' object has no attribute 'empty'`.

In `src/mcu_updater/providers/spec.py`, add to `Install` after the `load`
classmethod:

```python
    @property
    def empty(self) -> bool:
        """No type of any provider is configured on this host.

        One question rather than a list of section maps. The caller that
        enumerated `registry` and `platformio` told a host whose only type was a
        Roadrunner that it had no types configured, and adding the third map at
        that call site would only move the same mistake one provider along.
        """
        return not self.registry and not self.platformio and not self.cmake
```

Run: `python -m pytest tests/test_providers.py -q -k empty`
Expected: PASS.

- [ ] **Step 10: Write the failing CLI tests**

In `tests/test_cli.py`, change `_cmake_flashable`'s signature to

```python
def _cmake_flashable(
    c, fake_root, *, helper: bool = True, staged: bool = True, serials=(RR_SERIAL,)
):
```

add `RR_SERIAL_B = "5K3DNTFCR1B3C9D0RZMYA3Z831"` beside `RR_SERIAL`, and replace
its `serials:` line so the config write ends:

```python
            f"cmake_target: roadrunner_v1_i2c_rgb\nserials: {', '.join(serials)}\n"
```

Replace `test_flashing_a_cmake_type_by_name_alone_is_refused` with:

```python
def test_flashing_a_cmake_type_by_name_alone_writes_its_boards(
    c, cmake_flashable, captured, monkeypatch
):
    """Spec §8: the loop covers every builder. `-t roadrunner` was refused, so
    the one provider whose boards are only reachable through a helper was also
    the only one you had to name a serial for - on a host where the panel's
    "Flash All" button covered everything else."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="roadrunner", serial=None, yes=True, force=False)
        )

    assert exc.value.code == 0
    assert [t.id for t in captured[0]] == [RR_SERIAL]
    assert [t.flasher for t in captured[0]] == ["bootsel"]


def test_flashing_a_cmake_type_covers_every_serial_it_declares(
    c, fake_root, captured, monkeypatch
):
    """Per-device, like every other type-level flash: the type names the image,
    each declared serial is its own write."""
    _cmake_flashable(c, fake_root, serials=(RR_SERIAL, RR_SERIAL_B))
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="roadrunner", serial=None, yes=True, force=False)
        )

    assert exc.value.code == 0
    assert [t.id for t in captured[0]] == [RR_SERIAL, RR_SERIAL_B]


def test_flashing_a_cmake_type_that_tracks_nothing_says_so(
    c, fake_root, captured, capsys, monkeypatch
):
    """A type with no `serials:` has nothing to write. Named, because the
    alternative is a batch of zero devices reporting a clean sweep."""
    _cmake_flashable(c, fake_root, serials=())
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="roadrunner", serial=None, yes=True, force=False)
        )

    assert exc.value.code == 1
    assert "No serials tracked" in capsys.readouterr().err
    assert captured == []


def test_update_all_flashes_cmake_boards_too(
    c, fake_root, captured, capsys, monkeypatch
):
    """`update-all` built a Roadrunner and then left it on the old image: its
    flash half enumerated the registry and the PlatformIO map by name, and a
    cmake type was in neither."""
    _cmake_flashable(c, fake_root)
    built: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.cmake.Cmake.build",
        lambda self, install, target, **kw: built.append(target.name),
    )
    monkeypatch.setattr(
        "mcu_updater.providers.kconfig_make.KconfigMake.build",
        lambda self, install, target, **kw: built.append(target.name),
    )

    cli.update_all(argparse.Namespace(yes=True, jobs=None))

    assert "roadrunner" in built, capsys.readouterr().out
    assert RR_SERIAL in [t.id for t in captured[0]]


def test_update_all_names_a_cmake_type_it_could_not_write(
    c, fake_root, captured, capsys, monkeypatch
):
    """A type built and then unwritable is a configuration gap, not a fault:
    named, counted as a failure, and the rest of the fleet still written. The
    same treatment `_pio_targets` gets when no watcher is running."""
    _cmake_flashable(c, fake_root, helper=False)
    monkeypatch.setattr(
        "mcu_updater.providers.cmake.Cmake.build",
        lambda self, install, target, **kw: None,
    )
    monkeypatch.setattr(
        "mcu_updater.providers.kconfig_make.KconfigMake.build",
        lambda self, install, target, **kw: None,
    )

    with pytest.raises(SystemExit) as exc:
        cli.update_all(argparse.Namespace(yes=True, jobs=None))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "SKIP roadrunner" in err
```

Run:

```bash
python -m pytest tests/test_cli.py -q -k "cmake or update_all"
```

Expected: FAIL. The two type-level flash tests exit 1 on the old refusal, the
tracks-nothing test exits 1 with the *wrong* message, and the two `update-all`
tests find no Roadrunner in `captured[0]`.

`test_no_cmake_argument_combination_raises_keyerror` keeps its
`("roadrunner", None)` case and keeps passing: it accepts `SystemExit`
whichever code it carries, which is exactly the point of it.

- [ ] **Step 11: The CLI's two loops**

In `src/mcu_updater/cli.py`:

1. In `flash_fw_cmd`, delete the CMake refusal whole - the comment above it too,
   since it exists only to explain the refusal:

```python
    # Before the confirmation, not after: this is deferred work with its own
    # spec, and there is nothing to warn about a flash that will not happen.
    # The same refusal `fw.bulk_flash` makes, worded for this caller.
    if owner == providers.Cmake.name and not args.serial:
        print(
            f"ERROR: type-level flash is not available for CMake-built type "
            f"'{args.type}'. Flash its boards individually with -s <serial>.",
            file=sys.stderr,
        )
        sys.exit(1)
```

2. Immediately after the PlatformIO branch's `sys.exit(code)` and before the
   "Whole type" comment, add:

```python
    # A CMake type: its boards are the `serials:` its own `[type]` section
    # declares, each written over BOOTSEL by the family's helper. The same shape
    # as the registry branch below, over the list a cmake type keeps instead of
    # the registry's - and `_cmake_targets` is the single-board path unchanged,
    # so a refusal it raises (no helper, nothing staged) is a fact about the
    # type and stops the whole loop on the first serial, which is correct.
    if owner == providers.Cmake.name and not args.serial:
        from .providers import cmake as cmake_mod

        # `.get`, not `[...]`: `provider_of` proved membership a moment ago, but
        # it re-read the config to do it.
        entry = cmake_mod.load(c.paths).get(args.type)
        if entry is None or not entry.serials:
            print(f"No serials tracked under '{args.type}'.", file=sys.stderr)
            sys.exit(1)

        with exclusive(c.paths, f"flash type {args.type}"):
            targets: list = []
            for serial in entry.serials:
                targets += _cmake_targets(c, args.type, serial)
            code = _run_batch(c, targets, f"flash {args.type}")
        sys.exit(code)
```

3. In `update_all`, replace

```python
    if not install.registry and not install.platformio:
        print("No types configured.", file=sys.stderr)
        sys.exit(1)
```

with

```python
    if install.empty:
        print("No types configured.", file=sys.stderr)
        sys.exit(1)
```

4. In `update_all`'s flash sweep, after the `install.platformio` loop and before
   `if not targets:`, add:

```python
            for name in sorted(install.cmake):
                for serial in install.cmake[name].serials:
                    try:
                        targets += _cmake_targets(c, name, serial)
                    except UpdaterError as exc:
                        # Not fatal, and not silent: a type with no helper or
                        # nothing staged is a configuration gap the operator
                        # fixes, and the rest of the fleet is still worth
                        # writing. Named per serial because that is what the
                        # failure list names.
                        print(f"SKIP {name}: {exc}", file=sys.stderr)
                        failures.append((name, serial))
```

- [ ] **Step 12: Run every gate**

From the worktree root:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

Two things to look at rather than only the exit codes:

- `tests/test_agent_targets.py`'s CMake rows go through `_cmake_devices` now
  instead of the loop Task 8 left inside `_cmake_target`. They must pass
  **unchanged**: this step is a pure extraction, and a changed row means the
  extraction dropped something. Compare the two code blocks in step 3 field by
  field before editing an assertion.
- `tests/test_agent_bulk.py`'s exact-set assertions
  (`test_a_fleet_build_covers_every_build_system` and friends) call
  `_declare_cmake` with its new defaults, which declare no serials and stage
  nothing. They must pass unchanged too.

- [ ] **Step 13: Pin the loops with mutation tests**

Create `scripts/mutations/loops.json`:

```json
{
  "_comment": "The loops cover every builder. Every mutation here is a way for a Roadrunner to be built and then silently left on its old image - the exact failure spec §8 exists to end. Run: ./scripts/mutation_test.py scripts/mutations/loops.json",
  "file": "src/mcu_updater/agent/methods/bulk.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_agent_bulk.py",
    "tests/test_cli.py",
    "tests/test_providers.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a cmake board that is not on the bus is never flashed",
      "find": "                if not device[\"present\"]:\n                    continue\n",
      "replace": ""
    },
    {
      "name": "scope 'all' overrides the cmake verdict",
      "find": "                if scope != \"all\" and device[\"status\"].needs_flash is not True:",
      "replace": "                if device[\"status\"].needs_flash is not True:"
    },
    {
      "name": "a cmake type with nothing staged is skipped",
      "find": "            if not payload[\"has_firmware\"]:\n                continue\n            entry = entries[name]",
      "replace": "            entry = entries[name]"
    },
    {
      "name": "a named type narrows the cmake selection too",
      "find": "            name = payload[\"name\"]\n            if only is not None and name != only:\n                continue",
      "replace": "            name = payload[\"name\"]"
    },
    {
      "name": "the cmake board dict carries the staged uf2",
      "find": "                        \"uf2_file\": uf2,",
      "replace": "                        \"uf2_file\": \"\","
    },
    {
      "name": "flash_all selects cmake boards beside the others",
      "find": "            + self._canbus_boards_to_flash(reg, scope, only)\n            + self._cmake_boards_to_flash(scope, only)",
      "replace": "            + self._canbus_boards_to_flash(reg, scope, only)"
    },
    {
      "name": "update_all selects cmake boards beside the others",
      "find": "                + self._canbus_boards_to_flash(reg_now, scope, only)\n                + self._cmake_boards_to_flash(scope, only)",
      "replace": "                + self._canbus_boards_to_flash(reg_now, scope, only)"
    },
    {
      "name": "the panel row and the fleet selection share one verdict",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        for device in self._cmake_devices(payload, family, helper, rows):",
      "replace": "        for device in self._cmake_devices(payload, family, None, rows):"
    },
    {
      "name": "a cmake type-level flash covers every serial it declares",
      "file": "src/mcu_updater/cli.py",
      "find": "            for serial in entry.serials:\n                targets += _cmake_targets(c, args.type, serial)",
      "replace": "            for serial in entry.serials[:1]:\n                targets += _cmake_targets(c, args.type, serial)"
    },
    {
      "name": "update-all sweeps cmake types",
      "file": "src/mcu_updater/cli.py",
      "find": "            for name in sorted(install.cmake):",
      "replace": "            for name in ():"
    },
    {
      "name": "update-all names a cmake type it could not write",
      "file": "src/mcu_updater/cli.py",
      "find": "                        print(f\"SKIP {name}: {exc}\", file=sys.stderr)\n                        failures.append((name, serial))",
      "replace": "                        pass"
    },
    {
      "name": "a host whose only type is cmake is configured",
      "file": "src/mcu_updater/providers/spec.py",
      "find": "        return not self.registry and not self.platformio and not self.cmake",
      "replace": "        return not self.registry and not self.platformio"
    }
  ]
}
```

Run it, once, uninterrupted:

```bash
python scripts/mutation_test.py scripts/mutations/loops.json
python scripts/mutation_test.py scripts/mutations/inventory.json
```

Expected: every mutation is caught, in both specs. `inventory.json` is Plan 1's
and is here because this task moved the line its fourth mutation anchors; if
that mutation now reports `find not found`, re-anchor it onto the same line in
`_cmake_devices` in this task's commit.

For `loops.json`: The eighth one is the guard that matters
most - it passes `None` where the helper goes, so the fleet selection and the
panel row would judge the board off different evidence, which is the class of
disagreement this task exists to end. If it survives, a test is asserting the
verdict's *reason* without any digest evidence in play; add one that puts a
reported image behind the board.

- [ ] **Step 14: Documentation**

In `docs/agent-api.md`, after the "A tracked `canbus_uuids:` entry is included
too, never excluded" block and its two numbered tiers, before the `fw.bus.scan`
paragraph, add:

```markdown
**A cmake type's declared `serials:` are included too**, judged by the same
verdict the panel row shows and selected by the same two tests as a kconfig
board: something staged to write, and the board on the by-id bus. Its board
dict carries `uf2_file` beside the usual keys, because a BOOTSEL write copies an
image rather than driving a bootloader protocol. A board already *in* BOOTSEL is
not selected - it has no by-id entry while its volume is mounted, so its verdict
is `offline` - and `fw.flash` with its serial still writes it.

A cmake type with no `helper:` cannot be put into BOOTSEL by anything here. It
is not dropped from the selection: it appears in `boards[]` and then in the
job's `failures[]` as a refusal naming the family, the same as any other device
its family's `flashers:` cannot write.
```

In `docs/decisions.md`, append:

```markdown
### One selection per identity, every builder

A fleet flash's selection is one list per kind of identity a type can declare -
a by-id `serials:` entry, a `canbus_uuids:` entry, a cmake type's `serials:` -
and the callers concatenate them. Adding a builder adds a selection beside the
others; it never adds a branch to a caller, and it never adds a refusal saying
this operation does not serve that builder. `type_not_bulk_flashable` and the
CLI's `-t`-only CMake refusal were both honest while the selection did not
exist, and both became the only remaining way to leave a board behind once it
did.

The verdict behind each selection is the verdict the panel shows. A CMake row
had a fixed `unknown_version` stub and the selection had no branch, so the two
could not disagree; `_cmake_devices` is the one judgement both read, which is
what makes "the panel says this board is behind" and "the fleet flash writes
this board" the same claim.

Whether a host has any types at all is `providers.Install.empty`, not a list of
section maps at the call site. The list was `registry` and `platformio`, and it
told a Roadrunner-only host it had nothing configured.
```

- [ ] **Step 15: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(bulk): cmake boards join every flash loop

`_boards_to_flash` walks the kconfig registry and
`_canbus_boards_to_flash` the tracked CAN uuids. A Roadrunner belonged to
neither, so a fleet flash wrote every board on the host except the one whose
panel row said it was behind - and `fw.flash_all {name}` refused it by name
rather than doing it.

`_cmake_devices` comes out of `_cmake_target`, so the panel row and the new
`_cmake_boards_to_flash` read one verdict. The board dict carries `uf2_file`
for `Bootsel.target`; no helper is resolved for the write, so a type without
one is a refusal in the batch's failures rather than a board missing from the
selection.

Retires `type_not_bulk_flashable` and `_require_flashable_type`, the CLI's
`-t`-only CMake refusal, and `update-all`'s two-provider "No types configured."
early exit - which told a Roadrunner-only host it had nothing configured.
`Install.empty` asks the seam instead.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Identity (spec §3)

**Files:**
- Modify: `src/mcu_updater/helpers/spec.py` (append the `Identifier` Protocol)
- Modify: `src/mcu_updater/helpers/__init__.py` (the `identifier` accessor and its export)
- Modify: `src/mcu_updater/helpers/knomi_serial.py` (`identify`, `remembered_at`)
- Modify: `src/mcu_updater/agent/methods/status.py:1199-1231` (`_pio_target`'s `id` and flash action), `:2210-2253` (`_watcher_map`)
- Modify: `src/mcu_updater/agent/methods/flash.py:447-456` (`_pio_flash` accepts either spelling)
- Modify: `src/mcu_updater/cli.py:22-32` (import `helpers`), `:648-717` (`_pio_targets` through the seam), `:816-818` (the dropped `allow_discovery` argument)
- Modify: `src/mcu_updater/providers/pio.py:20-27, 41-46` (delete the identity re-export shim)
- Modify: `src/mcu_updater/flashers/esptool.py:174-178` (docstring pointer)
- Test: `tests/test_helpers.py`, `tests/test_agent_targets.py:353-365`, `tests/test_agent_displays.py:665-696`, `tests/test_agent_display_jobs.py:466-472`, `tests/test_cli.py:266-295, 459-507`, `tests/test_knomi_watcher.py:1-15`
- Create: `scripts/mutations/identity.json`
- Docs: `docs/agent-api.md`, `docs/decisions.md`

**Mutation specs at risk:**

```bash
rg -n "configured_path|read_device_map|device_map_path|addressed_by|allow_discovery|only_id" scripts/mutations
```

One hit, and it anchors a line this task rewrites:

- `scripts/mutations/targets.json` mutation 11, *"a screen's flash is pinned to its own port"*, whose `find` is the flash-action params line in `_pio_target`. That line keeps its `port` key and changes only its value, so the `find` string still moves. Step 16 re-anchors it onto the new line in the same commit as the change; the mutation's intent is unchanged, so only its name is tightened.

`display-flash.json` and `pio.json` both survive untouched: `display-flash.json`'s anchors are in `_pio_flash`'s refusal and in the esptool flasher, neither of which this task's edit reaches, and `pio.json`'s four per-mutation files are `discovery/knomi_serial/listen.py` and `watcher.py`, which keep every line they have — the handler *calls* them, it does not move them.

**Interfaces:**
- Consumes:
  - `helpers.Helper` and `helpers.for_name(name, *, family) -> Helper | None` (Task 1)
  - `helpers.KnomiSerialHelper` with `name`/`klipper_prefix`/`running_sha`/`is_dirty` (Tasks 1, 2)
  - the accessor shape `helpers.bootsel_requester` / `helpers.device_info_reader` establish (Tasks 1, 2)
- Produces:
  - `helpers.Identifier`, a `@runtime_checkable` Protocol:
    - `name: str`
    - `identify(paths: Paths, settings: Settings, entry: PioType, *, ask: bool, reporter: Reporter) -> dict[str, WatcherDevice]` — keyed by device id
    - `remembered_at(paths: Paths, entry: PioType) -> str` — `""` when nothing is remembered
  - `helpers.identifier(helper: Helper | None) -> Identifier | None`
  - `KnomiSerialHelper.identify` / `KnomiSerialHelper.remembered_at`
  - `StatusMixin._screen_id(screen: dict[str, Any]) -> str | None`
  - a PlatformIO row's `devices[].id` is the `device_id` for a `device_id:` section and the configured path for a `serial:` section; that device's flash action **keeps its params `{"name", "port"}`** and carries the same declared identity in `port` that the row reports in `id`
  - `fw.flash` for a screen accepts `port` or `id`, spelled as either the configured path or the device id
  - `cli._pio_targets(c, name, only_id=None)` — `allow_discovery` is gone
  - `providers.pio` no longer re-exports `discover`, `read_device_map`, `device_map_path`, `WatcherDevice` or `DEVICE_MAP_VERSION`

**Decisions this task makes:**

1. **Identity is a helper capability, not a `discovery.Source`.** The two seams answer different questions and both stay. `discovery/registry.py`'s `SOURCES` and `confirm()` answer *"which sighting do I trust"* about identities the host can already see — a serial in `/dev/serial/by-id`, a DFU descriptor, an `RPI-RP2` volume. `Identifier` answers *"what is this thing"* for hardware the host cannot see at all: a KNOMI sits behind a CH340K that reports no USB serial, so the only stable name it has is one the firmware knows and will state if asked. `esptool.discover` and `discovery.knomi_serial.Watcher.sight` already sit behind the Source seam and are **not** touched here.

2. **The Protocol's return type is `WatcherDevice`, knomi's own record.** `fw.device.list`'s `watcher` block already puts `WatcherDevice.to_json()` on the wire (`device_id`, `port`, `firmware_version`, `build_variant`, `present`), so a generic five-field copy would be a second description of the same thing — the failure this whole plan exists to stop. knomi_serial is the only identifier there is; a second one is the moment to generalise the record, not before.

3. **`ask` is a required keyword, not a defaulted one.** It is the difference between reading a file and opening every free serial port, and the caller is the only party that knows whether it has stopped the services holding them. A default would let a status poll acquire that cost by omission.

4. **`cli._pio_targets` loses `allow_discovery`.** Both call sites passed `True`, and both are already inside `_ports_free`. The flag was a caller branch standing in for a contract; the contract now lives on `ask`, where one caller genuinely passes `False`.

5. **A status read never asks, and never raises.** `_watcher_map` calls `identify(..., ask=False)`, and every way of having no handler - no identity capability, a misspelt `helper:`, an undeclared family - yields `{}` and `updated: None` rather than an error. `fw.device.list` rides along in every `fw.status` poll, and a status read that raises is worse than one that says it cannot tell.

   That refusal is live, not theoretical, and it is why Task 9's `_cmake_boards_to_flash` wraps the same call: `pio_types()` and `cmake_status()` both reach the type list through `typelist.read_config`, which is deliberately lenient and never calls `typelist.validate`. Task 1's refusal fires on the `typelist.load` path only. `cli._pio_targets` leaves the call bare on purpose - a CLI invocation that hits a misspelt helper should stop with the config error, because that is the message naming the fix.

6. **`_watcher_map` keeps its name and its knomi shape.** It reports on *a watcher service and its file* — `service`, `active`, `updated` — which is a fact about knomi's watcher, not about identity in general. Only the two calls that ask *what was identified* and *where the answers live* move onto the seam.

7. **`FlashTarget.id` for a screen stays its port.** `flash_all`'s result `id` is what esptool wrote to, and `_pio_flash`'s `failures[].port` is projected from it. A `device_id:` screen therefore has one value in `targets[].devices[].id` (its burned-in id, how it is *addressed*) and another in the batch result (the tty, where it was *written*); the row's `path` carries the tty, so a caller can bridge them. Documented in Step 17 rather than papered over.

8. **A device's flash action carries the same identity its row reports — in the param it already had.** The value changes, the key does not: the params stay `{"name", "port"}` and `port` carries `_screen_id(screen)` instead of `screen["configured_path"]`. `targets[].devices[].id` being handed straight back is what makes that slot uniform, and renaming the param is not needed to get it.

   Spelling it `id` was the first draft, and the spec refuses it. §3's identity paragraph enumerates exactly what may change — *"The PlatformIO row's `id` becomes the declared identity… The resolved port stays in `path`"* — and then fences the rest: *"Flash parameter keys are unchanged."* A fencing sentence in the same breath as the permission is a constraint, not filler. Ruling 18 is the whole of what this task may do to the wire here: the row's `id`, and `fw.flash` accepting either spelling.

   What makes the value change safe rather than assumed safe: `_pio_flash` reads `args.get("port") or args.get("id")` and keeps doing so, and Step 7 *widens* what it matches rather than narrowing it — so a caller sending `port` with a configured path is unaffected, and a caller replaying this action's `port` with a `device_id` in it now resolves too. That guarantee has to live in the method rather than in a survey of callers, because the standalone UI ships from outside this repository (`UI_PATH`, `~/mcu-updater-ui`) and its consumers cannot be grepped from here.

9. **`entry: PioType` on a Protocol whose pitch is genericity.** The same argument as the return type. The only firmware whose hardware carries no name of its own is the PlatformIO-built one, so a `providers`-shaped parameter is the honest signature rather than an `Any` that documents nothing — and typing it is what lets `mypy` check the handler against `identify`'s two real call sites. A second identifier for some non-PlatformIO firmware is the moment to widen this to a union or a shared record, and it would be one signature change, because nothing compares a helper's name to decide what to pass it.

10. **`providers.pio`'s identity re-export shim goes.** It existed so callers of the old `pio.read_device_map` kept working through the move into `discovery/knomi_serial/`. After this task nothing in `src` calls those names except the handler, which imports them directly. `source_dir as _source_dir` stays — that one is about *building*, not identity.

---

- [ ] **Step 1: Write the failing helper tests**

Append to `tests/test_helpers.py`:

```python
# --------------------------------------------------------------------------
# identity
#
# A board is found by the serial udev puts in /dev/serial/by-id. A KNOMI is
# behind a CH340K that reports no serial at all, so the only stable name it
# has is one the firmware will state if asked. That is a firmware-specific
# capability, which is what makes it a helper.
# --------------------------------------------------------------------------


def _pio_entry(name="knomi_toolchanger"):
    from mcu_updater.providers.pio import PioType

    return PioType(name=name, env=name, source="/nowhere", firmware="knomi_serial")


def test_only_the_knomi_helper_can_identify_devices():
    assert helpers.identifier(KnomiSerialHelper()) is not None
    assert helpers.identifier(RoadrunnerHelper()) is None
    assert helpers.identifier(None) is None


def test_the_remembered_map_answers_without_opening_a_port(paths, settings, monkeypatch):
    """`ask=False` is what a status poll passes. It must never cost a port."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", _never_asked)

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=False, reporter=null_reporter
    )
    assert list(found) == ["aaa111"]


def test_nothing_remembered_and_no_permission_to_ask_is_empty(paths, settings, monkeypatch):
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(handler, "discover", _never_asked)

    assert (
        KnomiSerialHelper().identify(
            paths, settings, _pio_entry(), ask=False, reporter=null_reporter
        )
        == {}
    )


def test_the_remembered_answer_wins_over_asking(paths, settings, monkeypatch):
    """Cheapest source first. The listen pass costs six seconds of held ports,
    so it runs when the map has nothing - not alongside it."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", _never_asked)

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=True, reporter=null_reporter
    )
    assert list(found) == ["aaa111"]


def test_an_empty_map_asks_the_devices_themselves(paths, settings, monkeypatch):
    """The map is a remembered path; the broadcast is the authority. Each
    device announces its id every couple of seconds unprompted, so with the
    ports free this is a fact rather than a memory."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(
        handler,
        "discover",
        lambda p, s, e, **kw: {"bbb222": _device("bbb222", "/dev/ttyUSB1")},
    )

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=True, reporter=null_reporter
    )
    assert list(found) == ["bbb222"]


def test_asking_is_best_effort_and_never_raises(paths, settings, monkeypatch):
    """Discovery needs pyserial out of the module's own source tree. A host
    without it must get the caller's "neither source could tell" refusal,
    which names both sources, not a tool error from the fallback."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.helpers import knomi_serial as handler

    def boom(*a, **kw):
        raise ToolMissingError("no python3 here", tool="python3")

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(handler, "discover", boom)

    found = KnomiSerialHelper().identify(
        paths,
        settings,
        _pio_entry(),
        ask=True,
        reporter=lambda stream, line: said.append((stream, line)),
    )
    assert found == {}
    assert [s for s, _ in said] == ["warn"]
    assert "no python3 here" in said[0][1]


def test_where_the_answers_are_remembered_is_the_handlers_to_say(paths):
    """The CLI's refusal names the file it read. Asking the handler is what
    keeps `providers.pio` out of that sentence."""
    entry = _pio_entry()
    assert KnomiSerialHelper().remembered_at(paths, entry).endswith("devices.json")
```

and add to that file's imports and helpers, beside the existing ones:

```python
from mcu_updater.build import null_reporter
from mcu_updater.discovery.knomi_serial import WatcherDevice


def _device(device_id: str, port: str) -> WatcherDevice:
    return WatcherDevice(device_id=device_id, port=port, present=True)


def _never_asked(*a, **kw):
    raise AssertionError("opened the ports when the caller had not allowed it")
```

`tests/test_helpers.py` is Task 1's file and already imports `helpers`, `KnomiSerialHelper` and `RoadrunnerHelper`; it does not yet use the `paths`/`settings` fixtures, which come from `tests/conftest.py` and need no declaration.

- [ ] **Step 2: Run the helper tests to verify they fail**

```bash
python -m pytest tests/test_helpers.py -q
```

Expected: FAIL. `AttributeError: module 'mcu_updater.helpers' has no attribute 'identifier'` on the first, and `AttributeError: 'KnomiSerialHelper' object has no attribute 'identify'` on the rest.

- [ ] **Step 3: Add the `Identifier` capability**

Append to `src/mcu_updater/helpers/spec.py`, and add to its `TYPE_CHECKING` block:

```python
    from ..build import Reporter
    from ..discovery.knomi_serial import WatcherDevice
    from ..providers.pio import PioType
    from ..settings import Settings
```

```python
@runtime_checkable
class Identifier(Protocol):
    """Answers which device is which, for hardware the host cannot name.

    Every other device in this tool is found by something the host can see
    without asking: a serial in `/dev/serial/by-id`, a DFU descriptor, an
    `RPI-RP2` volume. `discovery`'s sources exist to decide which of those
    sightings to trust. A KNOMI screen has none of them - the CH340K in
    front of it reports no USB serial at all - so the only stable name it
    has is one its *firmware* knows and will state if asked. That is
    firmware-specific by construction, which is what makes it a helper
    capability rather than a source.

    `ask` is the cost. False is the remembered answer: a file, instant, and
    safe while Klipper holds every port. True additionally opens the free
    ports and reads what broadcasts back - authoritative, and only possible
    once the caller has stopped the services holding them. No default, so
    that cost is never acquired by omission.

    Keyed by device id. The value is knomi's own `WatcherDevice` because
    `fw.device.list` already puts it on the wire and a five-field copy here
    would be a second description of one thing; knomi_serial is the only
    identifier, and a second one is when to generalise it.
    """

    name: str

    def identify(
        self,
        paths: Paths,
        settings: Settings,
        entry: PioType,
        *,
        ask: bool,
        reporter: Reporter,
    ) -> dict[str, WatcherDevice]: ...

    def remembered_at(self, paths: Paths, entry: PioType) -> str:
        """The file the remembered answers live in, or "" when there is none."""
        ...
```

In `src/mcu_updater/helpers/__init__.py`, import `Identifier` from `.spec`, add it to `__all__`, and add beside the other accessors:

```python
def identifier(helper: Helper | None) -> Identifier | None:
    """The helper's identity capability, or None when it has none."""
    return helper if isinstance(helper, Identifier) else None
```

In `src/mcu_updater/helpers/knomi_serial.py`, add the imports

```python
from ..discovery.knomi_serial import device_map_path, discover, read_device_map
from ..errors import UpdaterError
```

and, since Task 2 left this module with no `typing` import at all, the block:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..build import Reporter
    from ..discovery.knomi_serial import WatcherDevice
    from ..paths import Paths
    from ..settings import Settings
```

then add to `KnomiSerialHelper`:

```python
    def identify(
        self,
        paths: Paths,
        settings: Settings,
        entry: pio.PioType,
        *,
        ask: bool,
        reporter: Reporter,
    ) -> dict[str, WatcherDevice]:
        """The watcher's map, and failing that the devices themselves.

        In order of what it costs. `devices.json` is written by the klippy
        module's own watcher process for exactly this moment and answers
        instantly. The listen pass is six seconds of held ports, so it runs
        only when the map has nothing to say and only when the caller has
        said the ports are free.

        The map is a remembered path and the broadcast is the authority -
        knomi_serial's own docs put identity at flash time for that reason,
        and a map is by definition not flash time. But a remembered path
        that is still right is worth more than six seconds, and the write
        itself verifies the port again before it touches anything.

        Asking is best effort: it needs pyserial out of the module's source
        tree, and a host missing it must reach the caller's own "neither
        source could tell" refusal - which names both sources - rather than
        a tool error from the fallback.
        """
        found = read_device_map(paths, entry)
        if found or not ask:
            return found
        reporter("info", f"No device map for '{entry.name}' - asking the devices which they are...")
        try:
            return discover(paths, settings, entry, reporter=reporter)
        except UpdaterError as exc:
            reporter("warn", f"could not ask the devices ({exc})")
            return {}

    def remembered_at(self, paths: Paths, entry: pio.PioType) -> str:
        return device_map_path(paths, entry)
```

The module already imports `from ..providers import pio` (Task 2) for `running_sha`/`is_dirty`, which is where `pio.PioType` comes from. `discover`, `read_device_map` and `device_map_path` are imported by name rather than through `pio`, because this handler is the one place that should know where knomi's discovery lives — and because Step 14 deletes the re-export that would otherwise make `pio.discover` work.

- [ ] **Step 4: Run the helper tests to verify they pass**

```bash
python -m pytest tests/test_helpers.py -q
```

Expected: PASS.

- [ ] **Step 5: Write the failing identity tests for `targets[]`**

In `tests/test_agent_targets.py`, add beside `_add_display`:

```python
def _add_display_by_id(paths, fake_root, api, device_id="aaa111"):
    """The other half of spec §3: a `[knomi_serial ...]` section that names the
    screen's burned-in id instead of a path. Klipper's own discovery resolves
    it and reports the path back, so the config has no path in it at all."""
    (fake_root / "knomi_serial").mkdir(exist_ok=True)
    port = fake_root / "knomi_discovered"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[type {ENV}]\nchipset: esp32\nfirmware: knomi_serial\nplatformio_env: {ENV}\n")
    api._call = serve_klipper(
        display_objects(
            {"knomi_serial t0_knomi": {"device_id": device_id}},
            {"knomi_serial t0_knomi": {"port": str(port)}},
        ),
        reachable=True,
    )
    return device_id, str(port)
```

and add these tests after `test_a_screen_carries_the_display_flash_call_pinned_to_its_port`:

```python
def test_a_screen_addressed_by_id_reports_that_id(api, paths, fake_root):
    """spec §3. A `device_id:` section names the screen's own burned-in id and
    no path at all - the path is whatever discovery found this boot, and
    reporting it as the identity hands a caller back a value that changes when
    the screen moves socket. It was null until discovery ran, too."""
    device_id, port = _add_display_by_id(paths, fake_root, api)

    device = _targets(api, "platformio")[ENV]["devices"][0]
    assert device["id"] == device_id
    assert device["path"] == os.path.realpath(port)


def test_a_screen_addressed_by_port_still_reports_its_port(api, paths, fake_root):
    """The other branch of the same rule, unchanged: a `serial:` section names
    a path and carries no id, so the path *is* the identity."""
    port = _add_display(paths, fake_root, api)

    assert _targets(api, "platformio")[ENV]["devices"][0]["id"] == port


def test_a_screens_flash_action_carries_the_identity_its_row_reports(
    api, paths, fake_root
):
    """`devices[].id` exists to be handed straight back. An action carrying a
    different value than the row reports would defeat that for exactly the
    sections whose path is the least trustworthy thing about them.

    The param keeps its name. Spec section 3: the row's `id` becomes the
    declared identity, and "Flash parameter keys are unchanged."""
    write_settings(paths, enable_flashing="true")
    device_id, _port = _add_display_by_id(paths, fake_root, api)
    api = Api(paths, runner=_runner(), call=api._call)

    device = _targets(api, "platformio")[ENV]["devices"][0]
    assert _action(device, "flash")["params"] == {"name": ENV, "port": device_id}
```

and rename `test_a_screen_carries_the_display_flash_call_pinned_to_its_port`, keeping its params assertion exactly as it is — a `serial:` screen's identity *is* its configured port, so this test's expectation does not move and is the guard that the key did not either:

```python
def test_a_screen_carries_the_display_flash_call_pinned_to_its_identity(api, paths, fake_root):
    """A port is never inferred: every screen of a type is an identical CH340,
    and PlatformIO's auto-detect was seen picking between two of them."""
    write_settings(paths, enable_flashing="true")
    port = _add_display(paths, fake_root, api)
    api = Api(paths, runner=_runner(), call=api._call)

    device = _targets(api, "platformio")[ENV]["devices"][0]
    flash = _action(device, "flash")

    assert flash["method"] == "fw.flash"
    assert flash["params"] == {"name": ENV, "port": port}
```

The file imports `os` already (it uses `os.path` in the artifact tests); add it to the import block if it does not.

In `tests/test_agent_display_jobs.py`, add at the end of the section its `screens_port` helper heads (the one whose banner comment ends "...which is the only moment identity is a fact rather than a memory."):

```python
def test_a_screen_can_be_flashed_by_its_device_id(api, paths, fake_root):
    """Ruling 18: either spelling of the one identity. A caller reading
    `targets[].devices[].id` off the wire hands it back without knowing
    whether the section it came from named a path or an id.

    Overrides the call channel rather than using the `screens` fixture: that
    fixture builds two `serial:` sections, and the case here is the other kind
    of section entirely - `device_id:`, whose path Klipper's own discovery
    resolved and reported back.
    """
    port = fake_root / "knomi_discovered"
    port.write_text("", encoding="utf-8")
    api._call = serve_klipper(
        display_objects(
            {"knomi_serial t0_knomi": {"device_id": "aaa111"}},
            {"knomi_serial t0_knomi": {"port": str(port)}},
        )
    )

    res = api.flash({"name": ENV, "id": "aaa111"})  # the spelling a caller may pick

    assert [d["configured_path"] for d in res["displays"]] == [str(port)]
```

The module's `api` fixture already sets `enable_flashing`, `dry_run` and a `JobRunner`, and already imports `display_objects` and `serve_klipper` from `.conftest`. The assertion reads `flash`'s synchronous return, so the submitted job never has to be waited on — the fixture's teardown cancels the runner.

- [ ] **Step 6: Run the identity tests to verify they fail**

```bash
python -m pytest tests/test_agent_targets.py tests/test_agent_display_jobs.py -q
```

Expected: FAIL, but read which. `test_a_screen_addressed_by_id_reports_that_id` asserts `device["id"] == "aaa111"` and gets the discovered path. `test_a_screens_flash_action_carries_the_identity_its_row_reports` gets `{"name": ..., "port": <the discovered path>}` and wanted the `device_id`. The `fw.flash` test refuses with `nothing_to_do`, because the filter only matches the configured path.

The renamed `..._pinned_to_its_identity` test **passes already** and must: a `serial:` screen's declared identity is its configured port, so nothing about it changes. It is the fence that says the param key stayed `port` — Step 16's `targets.json` mutation is what proves it still bites.

- [ ] **Step 7: Make a screen report how it is addressed**

In `src/mcu_updater/agent/methods/status.py`, add above `_pio_target`:

```python
    @staticmethod
    def _screen_id(screen: dict[str, Any]) -> str | None:
        """How this screen is addressed, which is what identifies it.

        printer.cfg says one of two things. A `serial:` section names a path
        and carries no id, so the path is the identity. A `device_id:`
        section names the screen's own burned-in id and no path at all: the
        path is whatever Klipper's discovery found this boot, so reporting it
        as the identity would hand a caller back a value that changes when
        the screen moves socket - and it is null until discovery runs, which
        left a `device_id:` screen with no `id` at all.

        `addressed_by` is computed once, in `device_list`, from the same
        config this would have to re-read. This only reads it.
        """
        if screen.get("addressed_by") == "device_id":
            return screen.get("device_id")
        return screen.get("configured_path")
```

and in `_pio_target`, replace the head of the per-screen loop:

```python
        devices = []
        for screen in payload["screens"]:
            device = self._platformio_device_status(screen)
            screen_id = self._screen_id(screen)
            devices.append(
                {
                    "id": screen_id,
```

and its flash action's value — the param keeps its name (Decision 8), and only what goes in it changes:

```python
                    "actions": self._device_actions(
                        allowed,
                        flash=(
                            "fw.flash",
                            {"name": name, "port": screen_id},
                        ),
```

`screen_id` is `str | None`, which is what went in here before: `screen["configured_path"]` is `live.get("port")` and has always been able to be `None`. The `device_id` branch cannot make it *newly* `None` — `device_list` sets `addressed_by = "device_id"` only when `configured_device_id` is truthy, and writes that same value into `screen["device_id"]` — so this needs no narrowing that `_device_actions` does not already do.

In `src/mcu_updater/agent/methods/flash.py`, in `_pio_flash`, replace the target filter:

```python
        # Read the devices NOW, while Klipper can still answer.
        listed = self.device_list({})
        # Either spelling of the one identity. `port` is what this call has
        # always taken; `id` is the uniform slot, and for a screen that is its
        # configured path or - where printer.cfg named one instead - its
        # burned-in device id. Matching both means a caller can hand back what
        # it read from `targets[].devices[].id` without knowing which kind of
        # section produced it.
        wanted = args.get("port") or args.get("id")
        targets = [
            d
            for d in listed["displays"]
            if d["present"]
            and (wanted is None or str(wanted) in {d["configured_path"], d["device_id"]})
        ]
```

- [ ] **Step 8: Run the identity tests to verify they pass**

```bash
python -m pytest tests/test_agent_targets.py tests/test_agent_display_jobs.py tests/test_agent_displays.py -q
```

Expected: PASS.

- [ ] **Step 9: Write the guard for `_watcher_map`**

This one is a fence, not a red test. The behaviour it protects — a status read never opening a port — is already true, and Step 11 has to keep it true while moving the call onto a seam whose other caller *does* open ports. So it passes before and after; what proves it bites is Step 16's `ask=False` → `ask=True` mutation, which makes `discover` run and trips the assertion below.

Append to `tests/test_agent_displays.py`, beside `test_the_map_file_mtime_is_reported`:

```python
def test_the_watcher_block_never_asks_the_devices(api, paths, monkeypatch):
    """`fw.device.list` rides along in every `fw.status` poll, and asking means
    six seconds with every free port open. The handler takes `ask` for exactly
    this: a status read passes False and gets the remembered answer or
    nothing."""
    from mcu_updater.helpers import knomi_serial as handler

    env = _declare_display(paths)

    def boom(*a, **kw):
        raise AssertionError("opened the ports from a status read")

    monkeypatch.setattr(handler, "discover", boom)
    api._call = _moonraker({}, reachable=False)

    assert api.device_list({})["watcher"][env]["devices"] == []
```

No map is written, so the remembered answer is empty — which is the state in which a cascade that ignored `ask` would go on to ask.

- [ ] **Step 10: Run it to verify it passes**

```bash
python -m pytest tests/test_agent_displays.py -q
```

Expected: PASS, including the three existing `watcher` tests. `monkeypatch.setattr(handler, "discover", boom)` resolves because Step 3 imported that name into the handler module; if it raises `AttributeError`, Step 3 did not land.

- [ ] **Step 11: Put `_watcher_map` on the seam**

In `src/mcu_updater/agent/methods/status.py`, add `null_reporter` to the existing build import:

```python
from ...build import null_reporter, read_sidecar
```

and replace `_watcher_map`'s body below its docstring:

```python
        from ... import stop_services
        from ...service import make_controller

        settings = self.settings()
        families = firmware.load(self.paths)
        out: dict[str, Any] = {}
        for name, display in self.pio_types().items():
            # The resolved list, minus klipper: klipper is reported through
            # `fw.status`'s own MCU join, not here, and this map exists to
            # answer one question - is this display's own watcher up? - which
            # only the non-klipper units bear on.
            resolved = stop_services.for_platformio(self.paths, display, settings)
            watcher = next((u for u in resolved if u != "klipper"), None)
            svc = (
                make_controller(settings, call=self._call_for_service, name=watcher)
                if watcher
                else None
            )
            # The family names the helper, and the helper knows where its
            # firmware remembers identities. `ask=False` is not a preference:
            # this method rides along in every `fw.status` poll, and asking
            # means six seconds with every free port open.
            #
            # Wrapped because `pio_types()` reaches the type list through
            # `typelist.read_config`, which is deliberately lenient and never
            # calls `validate` - so an undeclared family or a misspelt
            # `helper:` is still live here, and a status poll has to report
            # "cannot tell" rather than raise.
            try:
                family = firmware.resolve(self.paths, display.firmware, families)
                identify = helpers.identifier(
                    helpers.for_name(family.helper, family=family.name)
                )
            except ConfigCorruptError:
                identify = None
            devices = (
                identify.identify(
                    self.paths, settings, display, ask=False, reporter=null_reporter
                )
                if identify is not None
                else {}
            )
            out[name] = {
                "service": watcher,
                "active": svc.is_active() if svc is not None else None,
                # When the watcher last wrote. Weak evidence, and only in one
                # direction: an old file is suspicious, but a fresh one does not
                # mean the watcher is still running - it could have stopped a
                # second after writing - and an old one does not mean the map is
                # wrong, because nothing changing means nothing to write. Shown
                # so a human can judge; never branched on.
                "updated": (
                    _mtime(identify.remembered_at(self.paths, display))
                    if identify is not None
                    else None
                ),
                "devices": [d.to_json() for d in devices.values()],
            }
        return out
```

The local `from ...providers import pio as pio_mod` goes: those two calls were its only users in this method. `firmware`, `helpers` and `ConfigCorruptError` are all module-level imports in this file already.

- [ ] **Step 12: Write the failing CLI tests**

In `tests/test_cli.py`, rewrite the two tests that monkeypatch `pio.discover` to patch the handler instead, and add two more.

Replace `test_an_empty_device_map_falls_back_to_asking_the_devices`:

```python
def test_an_empty_device_map_falls_back_to_asking_the_devices(
    c, pio_type, captured, fake_root, monkeypatch
):
    """The map is a remembered path; discovery is the authority. knomi_serial's
    own docs put identity at flash time for exactly this reason, and the ports
    are free by the time this runs - which is the only moment it is possible.
    """
    from mcu_updater.discovery.knomi_serial import WatcherDevice
    from mcu_updater.helpers import knomi_serial as handler

    port = fake_root / "ttyUSB7"
    port.write_text("", encoding="utf-8")
    asked: list[str] = []

    def fake_discover(paths, settings, display, **kwargs):
        asked.append(display.name)
        return {
            "aaa111": WatcherDevice(device_id="aaa111", port=str(port), present=True)
        }

    monkeypatch.setattr(handler, "discover", fake_discover)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert asked == [ENV]
    assert [t.id for t in captured[0]] == [str(port)]
```

Replace `test_discovery_failing_still_names_both_sources`:

```python
def test_discovery_failing_still_names_both_sources(c, pio_type, monkeypatch):
    """A host with no pyserial must not surface a tool error from the fallback -
    the useful message is the one naming what it tried."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.helpers import knomi_serial as handler

    def boom(*a, **k):
        raise ToolMissingError("no python3 here", tool="python3")

    monkeypatch.setattr(handler, "discover", boom)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert "device map" in str(exc.value)
    assert "asking the devices directly" in str(exc.value)
```

Add beside them:

```python
def test_the_refusal_names_the_file_it_read(c, pio_type, monkeypatch):
    """Where the remembered answers live is the handler's to say. The CLI
    naming `devices.json` itself is what put `providers.pio` in this sentence."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert "devices.json" in str(exc.value)


def test_a_type_whose_firmware_cannot_identify_devices_is_refused(
    c, pio_type, monkeypatch
):
    """The handler-absent refusal, in the same shape a missing flasher gets:
    say so, rather than flash whatever happens to be on a remembered path."""
    monkeypatch.setattr(cli.helpers, "identifier", lambda helper: None)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert "identify" in str(exc.value)
```

- [ ] **Step 13: Run the CLI tests to verify they fail**

```bash
python -m pytest tests/test_cli.py -q
```

Expected: FAIL. The two rewritten tests fail at `monkeypatch.setattr(handler, "discover", ...)` if Step 3 has not landed, or — with Step 3 landed — because `_pio_targets` still calls `pio.discover` and never reaches the handler: `asked == []`, and the `ToolMissingError` one gets the real "nothing found" message with no discovery attempted. `test_the_refusal_names_the_file_it_read` already passes (the old code named the path too); it is here so Step 16's mutation has an anchor. `test_a_type_whose_firmware_cannot_identify_devices_is_refused` fails with `AttributeError: module 'mcu_updater.cli' has no attribute 'helpers'`.

- [ ] **Step 14: Put the CLI on the seam and delete the shim**

In `src/mcu_updater/cli.py`, add `helpers,` to the package import block (after `flashers,`):

```python
from . import (
    __version__,
    firmware,
    flashers,
    helpers,
    inventory,
    profiles,
    providers,
    stop_services,
    tracking,
    typelist,
)
```

and replace `_pio_targets` entirely:

```python
def _pio_targets(c: Context, name: str, only_id: str | None = None) -> list:
    """Devices of one PlatformIO type, from the firmware that knows them.

    **Not from Klipper.** The agent reads its list from the klippy module's own
    printer objects, and the CLI has no Moonraker to ask.

    So it asks the firmware's own identity handler, which knows the two
    sources this firmware has and what each costs: the watcher's `id -> port`
    map, written for exactly this moment, and failing that the broadcast
    listen pass, which is the *authoritative* one - each device announces its
    id every couple of seconds unprompted. Their own docs are explicit that
    identity belongs at flash time rather than to a remembered path, and the
    map is a remembered path.

    Asking needs the ports free. Both callers of this function are inside
    `_ports_free`, which is why `ask=True` is passed unconditionally rather
    than through a flag: a caller that had not stopped the services would be
    a caller in the wrong place, not a caller with the wrong argument.
    `services_stopped` is idempotent per unit, so the batch's own stop inside
    that one correctly no-ops.

    An empty answer from both is reported as "cannot tell", not as "no
    devices". Flashing nothing and calling it success is the failure this
    whole area exists to prevent.
    """
    from .providers import pio

    display = pio.load(c.paths)[name]
    families = firmware.load(c.paths)
    family = firmware.resolve(c.paths, display.firmware, families)
    identify = helpers.identifier(helpers.for_name(family.helper, family=family.name))
    if identify is None:
        raise UpdaterError(
            f"firmware family '{family.name}' names no helper that can identify "
            f"its devices, so there is no way to tell which screen of '{name}' is "
            f"which - and writing to a remembered path is what this refuses to do."
        )

    found = identify.identify(
        c.paths, c.settings, display, ask=True, reporter=stdout_reporter
    )
    units = stop_services.for_platformio(c.paths, display, c.settings)
    if not found:
        where = identify.remembered_at(c.paths, display) or "(nothing remembered)"
        own_watcher = [u for u in units if u != "klipper"]
        watcher = f"the '{own_watcher[0]}' watcher" if own_watcher else "a watcher"
        raise UpdaterError(
            f"nothing found for '{name}'. Neither the device map at {where} nor "
            f"asking the devices directly turned anything up - so either {watcher} "
            f"is not running and nothing answered on the free ports, or there is "
            f"nothing plugged in."
        )
    return [
        flashers.esptool.target_for(
            display,
            {
                "name": device.device_id,
                "section": f"{display.klipper_section} {device.device_id}",
                "configured_path": device.port,
                "device_id": device.device_id,
                "present": device.present,
            },
            stop_services=units,
        )
        for device in sorted(found.values(), key=lambda d: d.port)
        if device.present and (only_id is None or only_id in (device.port, device.device_id))
    ]
```

and drop the argument at its two call sites — `:816-818`:

```python
                targets = _pio_targets(c, args.type, only_id=args.serial)
```

and `:959`:

```python
                    targets += _pio_targets(c, name)
```

Then delete the shim. In `src/mcu_updater/providers/pio.py`, remove these five import lines:

```python
from ..discovery.knomi_serial import DEVICE_MAP_VERSION as DEVICE_MAP_VERSION
from ..discovery.knomi_serial import WatcherDevice as WatcherDevice
from ..discovery.knomi_serial import device_map_path as device_map_path
from ..discovery.knomi_serial import discover as discover
from ..discovery.knomi_serial import read_device_map as read_device_map
```

keeping `from ..discovery.knomi_serial import source_dir as _source_dir`, which is about building. Replace the module docstring's last paragraph:

```text
The two knomi discovery sources - the broadcast listen pass and the watcher's
`devices.json` map - live in `discovery.knomi_serial`, the subpackage named for
the firmware they integrate with (as opposed to `discovery.byid`/`dfu`/
`bootsel`, which answer questions true of any board). Nothing here re-exports
them: the one module that reaches for either is `helpers.knomi_serial`, the
firmware's own identity handler, and a provider is handed its configuration
rather than going looking for devices.
```

Fix the three places that read the shim:

- `src/mcu_updater/flashers/esptool.py:176` — `providers.pio.WatcherDevice` becomes `discovery.knomi_serial.WatcherDevice`.
- `tests/test_agent_display_jobs.py:468` — `from mcu_updater.providers.pio import WatcherDevice` becomes `from mcu_updater.discovery.knomi_serial import WatcherDevice`.
- `tests/test_knomi_watcher.py`, module docstring — replace its last sentence ("...and are re-tested via `providers.pio`'s re-export shim rather than `discovery.knomi_serial` directly, matching how `devices.py`'s shim is tested for the three bus sources.") with: "The two tests that exercise `api.device_list` stayed behind - they are agent-level, not `providers.pio`-level. The re-export shim that used to carry these names through `providers.pio` is gone: the only module that reaches for them now is `helpers.knomi_serial`, the firmware's own identity handler."

Confirm nothing else read them:

```bash
rg -n "pio\.(discover|read_device_map|device_map_path|WatcherDevice|DEVICE_MAP_VERSION)" src tests scripts docs
```

Expected: no output.

- [ ] **Step 15: Run the gates**

From the worktree root:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

Expected: all pass.

`mypy` is the one that earns its keep here. `identify` and `remembered_at` are reached only through the `Identifier | None` the accessor returns, so every call site has to narrow — and `_screen_id` returns `str | None`, which is what `devices[].id` and the flash action's `port` have always been for a screen whose path could not be resolved — so no call site needs a narrowing it did not already have.

- [ ] **Step 16: Re-anchor `targets.json` and add the identity spec**

In `scripts/mutations/targets.json`, mutation 11 becomes:

```json
    {
      "name": "a screen's flash is pinned to the identity its row reports",
      "find": "                            {\"name\": name, \"port\": screen_id},",
      "replace": "                            {\"name\": name, \"port\": screen[\"configured_path\"]},"
    },
```

Create `scripts/mutations/identity.json`:

```json
{
  "_comment": "Spec §3: a KNOMI is behind a CH340K that reports no USB serial, so the only stable name it has is one its firmware will state if asked. Two failures live here. Writing firmware to the wrong screen, which is what a remembered path preferred over a broadcast produces. And a status poll that opens every free serial port, which is a six-second stall on a call that rides along in every fw.status - so `ask` is a required keyword and the guards that pass it False are as load-bearing as the ones that pass True.",
  "file": "src/mcu_updater/helpers/knomi_serial.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_helpers.py",
    "tests/test_agent_targets.py",
    "tests/test_agent_displays.py",
    "tests/test_agent_display_jobs.py",
    "tests/test_cli.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a caller that did not free the ports is never asked to",
      "find": "        if found or not ask:\n            return found",
      "replace": "        if found:\n            return found"
    },
    {
      "name": "the remembered answer is preferred to six seconds of held ports",
      "find": "        if found or not ask:\n            return found",
      "replace": "        if not ask:\n            return found"
    },
    {
      "name": "asking is best effort, never a tool error from the fallback",
      "find": "        except UpdaterError as exc:\n            reporter(\"warn\", f\"could not ask the devices ({exc})\")\n            return {}",
      "replace": "        except UpdaterError:\n            raise"
    },
    {
      "name": "a screen is addressed by its id wherever its config names one",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        if screen.get(\"addressed_by\") == \"device_id\":\n            return screen.get(\"device_id\")",
      "replace": "        if False:\n            return screen.get(\"device_id\")"
    },
    {
      "name": "a status read never asks the devices",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "                    self.paths, settings, display, ask=False, reporter=null_reporter",
      "replace": "                    self.paths, settings, display, ask=True, reporter=null_reporter"
    },
    {
      "name": "the watcher block's mtime is the handler's own file",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "                    _mtime(identify.remembered_at(self.paths, display))",
      "replace": "                    None"
    },
    {
      "name": "either spelling of a screen's identity is accepted",
      "file": "src/mcu_updater/agent/methods/flash.py",
      "find": "            and (wanted is None or str(wanted) in {d[\"configured_path\"], d[\"device_id\"]})",
      "replace": "            and (wanted is None or str(wanted) == d[\"configured_path\"])"
    },
    {
      "name": "the ports are free by here, so the devices are asked",
      "file": "src/mcu_updater/cli.py",
      "find": "        c.paths, c.settings, display, ask=True, reporter=stdout_reporter",
      "replace": "        c.paths, c.settings, display, ask=False, reporter=stdout_reporter"
    },
    {
      "name": "a firmware that cannot identify its devices is refused",
      "file": "src/mcu_updater/cli.py",
      "find": "    if identify is None:",
      "replace": "    if False:"
    },
    {
      "name": "the refusal names the file the handler read",
      "file": "src/mcu_updater/cli.py",
      "find": "        where = identify.remembered_at(c.paths, display) or \"(nothing remembered)\"",
      "replace": "        where = \"(nothing remembered)\""
    }
  ]
}
```

Run both, one invocation each, never interrupted:

```bash
python scripts/mutation_test.py scripts/mutations/identity.json
python scripts/mutation_test.py scripts/mutations/targets.json
```

Expected: every mutation caught. Note the first two share a `find` — the harness replaces the first match per file per mutation and restores between mutations, so a shared anchor with two different replacements is fine and is the point: one mutation removes the permission check, the other removes the ordering.

- [ ] **Step 17: Document it**

In `docs/agent-api.md`:

**One.** In the `Target` section, leave the *"a board's action carries `serial`, a screen's carries `port`"* sentence exactly as it is — the params did not change (Decision 8), so the only correction that sentence would have needed is one this task deliberately does not make. Add this after the paragraph it closes:

```text
**A screen's `id` is how it is addressed, not where it was found.** A
`[knomi_serial ...]` section says one of two things. `serial:` names a path
and carries no id, so the path is the identity and `id` is that path.
`device_id:` names the screen's own burned-in id and no path at all — the path
is whatever Klipper's discovery found this boot — so `id` is that id, and the
tty is reported as `path` beside it. Before this, a `device_id:` screen had
`id: null` until discovery ran. The device's own flash action carries the same
value, in the `port` param it has always used, and `fw.flash` accepts either
spelling in either `port` or `id` — so a value read off this row can always be
handed straight back, whichever slot you read it from.
```

**Two.** In `#### Failures do not abandon the batch`, after *"`id` is the uniform slot: a board's serial, a screen's configured port."*, add:

```text
For a screen, that "configured port" is deliberately still the port, and from
here on it can differ from the `id` the screen's `targets[]` row reports. This
half of the wire says what esptool actually wrote to; a `device_id:` screen is
addressed by an id and written to a tty. The row's `path` carries that same
tty, which is how a caller correlates the two. A screen's flash *action* is the
row's side of that line, not this one: it carries the identity, in `port`.
```

**Three.** In `### Flashing a display`, change *"so the call is `{name, port?, force?}`"* to *"so the call is `{name, port?, id?, force?}` — either slot, spelled as the configured path or as the screen's own device id"*.

**Four.** In `### The watcher's map — the source that answers with Klipper down`, add after the JSON block:

```text
The block itself is about the watcher — is this display family's own watcher
service up, and when did it last write. What it *found* comes through the
firmware's identity handler with asking disabled: this method rides along in
every `fw.status` poll, and asking means six seconds with every free port open.
```

In `docs/decisions.md`, append:

```markdown
### Identity is a helper capability, not a discovery source

`discovery`'s sources — `byid`, `dfu`, `bootsel`, the knomi listen pass, the
knomi watcher map — all answer the same question: *which of these sightings do
I trust?* They exist because a board can be seen twice, differently, and
something has to rank the answers. Every one of them is about an identity the
host can already read off the bus.

A KNOMI screen has no such identity. The CH340K in front of it reports no USB
serial at all, so `/dev/serial/by-id` has nothing to say and neither does
anything else the host can do on its own. The only stable name the screen has
is one its *firmware* knows and will state if asked — which makes "what is this
thing" a question about the firmware, not about the host, and therefore a
`helpers.Identifier` rather than a `discovery.Source`.

Both seams stay, and they compose: the handler's answer is a sighting like any
other, and `confirm()` still decides what to trust at write time.

The capability carries `ask` as a required keyword because the two sources cost
three orders of magnitude apart — reading `devices.json` versus opening every
free serial port for six seconds — and only the caller knows whether it has
stopped the services holding those ports. `fw.device.list` passes False and
takes the remembered answer or nothing; the CLI passes True from inside
`_ports_free` and takes the authoritative one. There is no default, so that
cost cannot be acquired by omission.

One consequence worth stating: `providers.pio` no longer re-exports
`read_device_map`, `discover` or `device_map_path`. A provider is handed its
configuration and builds from it; going looking for devices was never its job,
and the shim that made it look like it was is gone.
```

- [ ] **Step 18: Run the gates again and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

Expected: all pass.

```bash
git add src/mcu_updater/helpers src/mcu_updater/agent/methods/status.py \
  src/mcu_updater/agent/methods/flash.py src/mcu_updater/cli.py \
  src/mcu_updater/providers/pio.py src/mcu_updater/flashers/esptool.py \
  tests/test_helpers.py tests/test_agent_targets.py tests/test_agent_displays.py \
  tests/test_agent_display_jobs.py tests/test_cli.py tests/test_knomi_watcher.py \
  scripts/mutations/identity.json scripts/mutations/targets.json \
  docs/agent-api.md docs/decisions.md
git commit -m "feat(helpers): a screen's identity comes from its firmware

Spec §3. A KNOMI is behind a CH340K that reports no USB serial, so the only
stable name it has is one its firmware will state if asked. That is a
firmware-specific capability, so it is a helper: helpers.Identifier, with
knomi_serial as its one implementation and ask= as the caller's statement
that it has freed the ports.

The CLI's two-source cascade and fw.device.list's watcher lookup both go
through it, which retires providers.pio's identity re-export shim - a
provider is handed its configuration, it does not go looking for devices.

A PlatformIO row's devices[].id is now how the screen is addressed: its
device_id where printer.cfg names one, its configured path otherwise. It
was the path either way before, which meant null for a device_id: section
until discovery ran. fw.flash takes either spelling.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Docs, final gates, bench checklist

Spec step 9. Every task above documented its own change — AGENTS.md requires it, and a reviewer judging Task 6 needs Task 6's docs in Task 6's diff. What is left is the part no single task could write: the reader-facing summary of the shape as a whole, the claims that are only stale once all ten have landed, and the one thing host tests cannot answer.

**Files:**
- Modify: `README.md:34-72` (`## Features`), `:76-84` (`## TODO`), `:558-560` (`### ESP32 displays`), `:616-629` (`### RP2040 cmake trees`)
- Modify: `docs/decisions.md` (one closing entry)
- Create: nothing
- Test: nothing — this task changes no behaviour, and the union run in `## Final verification` is its gate

**Mutation specs at risk:** none. This task adds no guarded line and touches no file a spec anchors. Confirm with

```bash
rg -n "README|decisions\.md" scripts/mutations
```

Expected: no output.

**Interfaces:**
- Consumes: every behaviour Tasks 1-10 shipped. Nothing in this task is importable.
- Produces: no code. The one thing later work depends on is the **bench checklist** in Step 7, which is handed to the human partner rather than run, and the prepared edit in Step 8 that flips the two hardware caveats once they report back.

**Decisions this task makes:**

1. **No mutation spec.** `scripts/mutations/` exists to prove a *guard* is load-bearing. Prose has no guard. Inventing a spec here would be a spec that cannot fail, which is worse than none.

2. **The bench is not touched.** 192.168.83.105 and its services belong to the human partner. Step 7 writes the checklist and stops; running it, and reporting what happened, is theirs. A plan step that ssh'd into a printer to reflash a board would be the single most destructive thing in this plan.

3. **The two "not verified end to end on hardware" caveats stay.** `README.md`'s `### RP2040 cmake trees` and `docs/agent-api.md`'s `fw.flash` section both say the closed BOOTSEL loop has host-test coverage only. Ten tasks of host tests do not change that, and this plan must not let a green suite read as a hardware claim. Step 8 writes the edit that flips them, unstaged, so it is one commit the moment the bench says yes.

4. **`docs/layout.md` needs nothing.** Plan 2 adds no `[type ...]` key, no config directory and no override variable — the three things that file is a table of. `[firmware ...]`'s new keys are documented where the rest of them are, in `README.md`'s `### Firmware families`, which Task 1 already extended.

5. **The one-pipeline TODO item does not simply disappear.** It becomes a `BENCH` item naming what is left, because deleting it would lose the design-doc link and would claim hardware coverage this plan does not have.

---

- [ ] **Step 1: Say what shipped, in `## Features`**

In `README.md`, under `Flashing:`, add one item after the CAN line:

```markdown
- [x] Per-firmware `flashers:` lists - a family declares which tools may write it, tried in order, and a device no tool supports is refused by name
```

Under `Firmware and boards:`, add three after the Roadrunner provision/clear line:

```markdown
- [x] Firmware-specific behaviour behind reviewed helper capabilities - device info, identity, BOOTSEL entry and provisioning, never a caller branch
- [x] One verdict per device, from one inventory join, whatever builds or flashes it
- [x] A screen's identity comes from its firmware, and is what `targets[]` reports it as
```

Under `Interfaces:`, amend the existing bulk line in place rather than adding a second:

```markdown
- [x] Bulk build / flash / update-all, covering every provider - kconfig, PlatformIO and cmake alike
```

Task 7 adds its own auto-provisioning item to this list; if it is already there, leave it and do not restate it here.

- [ ] **Step 2: Retire the `IN PROGRESS` item in `## TODO`**

Replace the one-pipeline bullet — the whole `- [ ] **IN PROGRESS** One pipeline: ...` line — with:

```markdown
- [ ] **BENCH** One pipeline is code-complete: one type list, one inventory, one flash loop, one verdict. Design: [docs/superpowers/specs/2026-09-14-one-pipeline-design.md](docs/superpowers/specs/2026-09-14-one-pipeline-design.md). What is left is hardware. A Roadrunner over usbserial, with a bystander RP2040 sitting in BOOTSEL during the write, to prove the topology match refuses the wrong volume - the one claim host tests cannot make. Until that runs, the closed BOOTSEL loop has host-test coverage only.
```

Leave the other two TODO items exactly as they are. The flaky-teardown one is unrelated, and config migrations are in the spec's own `## Deferred`.

- [ ] **Step 3: Fix the two claims that only go stale now**

In `README.md`, `### ESP32 displays`, replace:

```text
The screens themselves are not listed here - `[knomi_serial T0_knomi]` in
`printer.cfg` already names its port, and a second copy would only be something
to disagree with.
```

with:

```text
The screens themselves are not listed here - `[knomi_serial T0_knomi]` in
`printer.cfg` already names them, and a second copy would only be something to
disagree with. A section names *either* a port (`serial:`) or the screen's own
burned-in id (`device_id:`), and that choice is what identifies it: `status`,
`fw.status` and `fw.flash` all address a `device_id:` screen by its id and a
`serial:` screen by its path. The port a `device_id:` screen is actually on is
whatever discovery found this boot, and is reported beside its id rather than
standing in for it.
```

In `README.md`, `### RP2040 cmake trees`, add a paragraph after the mixed-fleet one (the paragraph that ends "...before stopped services restart."):

```text
Cmake types are ordinary members of every batch. `flash -t roadrunner` with no
`-s` writes every serial the type declares, `update-all` builds and then flashes
them alongside the kconfig and PlatformIO types, and `status` gives each board a
real verdict rather than the `unknown_version` stub it used to. A board whose
declared serial is not on the bus is reported offline and skipped, exactly as a
kconfig board is.
```

- [ ] **Step 4: Sweep the retired vocabulary**

Ten tasks deleted a lot of names. Prove none of them survive in prose that a user reads. Run each; every one must print nothing outside `docs/superpowers/` (plans and specs are historical records — leave them alone) and `docs/bootsel-mountpoint-design.md` / `docs/cmake-provider-design.md`, which are the same:

```bash
rg -n "type_not_bulk_flashable|_require_flashable_type|No types configured" README.md docs
rg -n "helper_bootsel|HelperBootsel|select_for|_board_target" README.md docs
rg -n "device_status\(|sensor_provenance|device_provenance" README.md docs
rg -n "flash_state" README.md docs/agent-api.md
```

The last one is the exception to expect a hit from: `flash_state` is still a live field name on the wire, so read each hit rather than deleting it. The first three should be empty.

Then check the docs still agree with each other on the two things this plan moved the most:

```bash
rg -n "flashers:" README.md docs/agent-api.md docs/decisions.md
git show baedbdc:docs/decisions.md | rg -c "^### "
rg -c "^### " docs/decisions.md
```

The first is informational: read the hits and confirm `flashers:` is described in the same words in all three places. The last two are a count, and the second must exceed the first by at least ten — every task from 1 to 10 listed `docs/decisions.md` in its Files block, and two of them add more than one heading, so a larger gap is fine and a smaller one is not. A task whose entry is missing is a task whose docs step was skipped: go back and add it in a commit naming that task, rather than writing it here.

- [ ] **Step 5: One closing entry in `docs/decisions.md`**

Every task above added the entry for its own decision. This one is the only thing none of them could say: what the ten add up to, and what it costs.

Append:

```markdown
### One loop per operation, and handlers for everything else

Ten changes, one rule: a caller never branches on which firmware, which
builder or which flasher it is holding. The branch becomes a capability
somebody registered by hand.

What that looks like in practice. `fw.status` joins one inventory against one
type list and produces one verdict per device, and a firmware with an odd
version string answers through a `DeviceInfoReader` rather than being special-
cased in the join. `fw.flash_all` walks every provider's boards through one
selection, and a family that needs a particular tool says so in `flashers:`
rather than being routed by a name comparison. A board that has to be talked
into its bootloader first has a `BootselRequester`; a screen whose hardware
carries no name has an `Identifier`; a board that needs an identity written to
it before it can be tracked has a `Provisioner`. `update-all` is build-all then
flash-all, over the same lists, for every provider there is.

The cost is real and worth stating. There are now four capability Protocols and
a registry of helpers, where before there were `if` statements - more
indirection to read through, and a new firmware means writing a module and
adding two lines to two registries rather than one branch in one function.
That trade was taken because the branches did not stay in one function: the
same "is this a display?" question was being asked in `status.py`, `flash.py`,
`bulk.py` and `cli.py`, and the four answers drifted. A Roadrunner tracked in
the UI and invisible to the CLI was that drift, reported as a bug.

Configuration never chooses which Python module gets imported. Every helper is
named in `helpers.registry.HELPERS` and every flasher in the flashers registry,
by hand, because a helper can stop services and write firmware. A misspelt
`helper:` or `flashers:` refuses the config when it loads, naming the known
values - the one place in this design where the answer to a wrong name is a
refusal rather than a fallback.
```

- [ ] **Step 6: Run every gate**

From the worktree root:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

Expected: all pass. This is the last behaviour-neutral commit in the plan, so a failure here is a failure one of Tasks 1-10 left behind — find which, and fix it in a commit that names it, rather than folding it into the docs commit.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/decisions.md
git commit -m "docs: one loop per operation, and what is left on the bench

The reader-facing half of the one-pipeline work: what shipped, in Features;
the two claims that only go stale once all ten changes have landed - a
screen addressed by its own id, and cmake types as ordinary members of every
batch; and one decisions.md entry for what the ten add up to, including what
the four capability Protocols cost to read.

The one-pipeline TODO item becomes a BENCH item rather than disappearing.
Host tests cannot prove the topology match refuses the wrong BOOTSEL volume,
and a green suite must not read as though they had.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Hand the bench checklist over — do not run it**

**Stop here and give the human partner the checklist below.** The bench is 192.168.83.105; its services and the boards attached to it are theirs, and nothing in this plan may ssh into it, restart `mcu-updater.service`, or write firmware to a board there. Present the checklist, say that it is the one claim host tests cannot make, and wait.

Two things to say when handing it over, because both will bite otherwise:

- A checkout does nothing for UI flashes until `mcu-updater.service` restarts. Check the service's start time before debugging anything that looks like old behaviour.
- Item 4 is the point of the whole exercise and it is the one that should *fail*. A refusal there is a pass.

```markdown
## Bench checklist — one pipeline, plan 2

Host: 192.168.83.105. Needed: one Roadrunner on usbserial with a provisioned
serial, and one other RP2040 you are willing to leave in BOOTSEL. Nothing here
writes to the second board; item 4 exists to prove that.

1. **The service is running this branch.** `systemctl status mcu-updater`, and
   check its start time is after the checkout. Restart it if not.
2. **`status` names every type.** `updatefw status`. Every type appears -
   kconfig, PlatformIO and the Roadrunner - and the Roadrunner's declared
   serial shows a real verdict, not `unknown_version`. This is the CLI-vs-UI
   tracking gap the plan set out to close; if the Roadrunner is missing here
   and present in the UI, stop and report that.
3. **A Roadrunner write over usbserial, alone.** With no other RP2040 in
   BOOTSEL: `updatefw flash -t roadrunner`. Expect the helper to confirm the
   board over its admin protocol, capture its USB topology, request BOOTSEL,
   copy the UF2 to the one matching mount, and then wait for the same serial
   and protocol identity to come back before services restart. Note whether
   the readiness wait succeeded or warned - a warning is not a failure, and
   the log says which.
4. **The same write with a bystander in BOOTSEL.** Put the second RP2040 into
   BOOTSEL (hold `BOOT`, press and release `RESET`, release `BOOT`) so two
   `RPI-RP2` volumes are mounted, then `updatefw flash -t roadrunner` again.
   **This must refuse.** The topology captured before the request matches one
   mount; two candidates is a refusal, and a refusal that names the count is
   the pass condition. If it writes to either board, stop immediately and
   report it - that is a fleet-brick path.
5. **`update-all` end to end.** `updatefw update-all --dry-run` first, and
   read what it says it would do: every provider's types, cmake included.
   Then for real if you are willing.
6. **The UI agrees.** Open the panel. Each board's verdict and each screen's
   identity should match what `status` said - a `device_id:` screen shown by
   its id, with its current port beside it.

Report back: which items passed, the exact refusal text from item 4, and
anything the readiness wait warned about in item 3.
```

- [ ] **Step 9: Prepare the caveat edit, and leave it unstaged**

Once — and only once — the human partner reports item 4 refusing correctly, two claims stop being true and both should go in one commit. Write the edit now so it is a single action later; do not commit it.

In `README.md`, `### RP2040 cmake trees`, replace:

```text
This closed loop has host-test coverage but has not yet been verified end to
end on hardware.
```

with:

```text
This closed loop is verified end to end on hardware, including the refusal:
with a second RP2040 sitting in BOOTSEL beside the Roadrunner, the write is
refused rather than sent to the wrong volume.
```

In `docs/agent-api.md`, `### fw.flash — the dangerous one`, replace:

```text
The closed loop is host-test-only so far, not an end-to-end hardware-verified
claim.
```

with:

```text
The closed loop is verified end to end on hardware, the bystander-volume
refusal included.
```

and in `README.md`'s `## TODO`, delete the `**BENCH**` item Step 2 wrote.

If the bench reports anything other than a clean refusal, none of this lands: the caveats are correct, and what the bench found is a bug to file rather than a line to edit.

---

## Final verification

Task 11's gates prove the tree is green. This section proves the guards still
bite — every mutation spec this plan **created** or **re-anchored**, run from the
worktree root, one invocation at a time, never interrupted. Enumerated from the
eleven Files blocks above, not from memory.

Ten the plan creates:

```bash
python scripts/mutation_test.py scripts/mutations/family-keys.json         # T1
python scripts/mutation_test.py scripts/mutations/device-info.json         # T2
python scripts/mutation_test.py scripts/mutations/flasher-supports.json   # T3
python scripts/mutation_test.py scripts/mutations/batch-selection.json    # T4
python scripts/mutation_test.py scripts/mutations/flashlog-loop.json      # T5
python scripts/mutation_test.py scripts/mutations/provision-on-track.json # T6
python scripts/mutation_test.py scripts/mutations/auto-provision.json     # T7
python scripts/mutation_test.py scripts/mutations/verdict.json            # T8
python scripts/mutation_test.py scripts/mutations/loops.json              # T9
python scripts/mutation_test.py scripts/mutations/identity.json           # T10
```

Eight the plan re-anchored or whose `command` it changed — these are the ones a
mid-plan mistake leaves quietly passing on a line that no longer means anything:

```bash
python scripts/mutation_test.py scripts/mutations/provenance-read.json        # T2
python scripts/mutation_test.py scripts/mutations/display-flash.json          # T2, T5
python scripts/mutation_test.py scripts/mutations/bulk-operations.json        # T3
python scripts/mutation_test.py scripts/mutations/bootsel-erase.json          # T4
python scripts/mutation_test.py scripts/mutations/flash-offset-diagnostic.json # T4
python scripts/mutation_test.py scripts/mutations/states.json                 # T8 (command)
python scripts/mutation_test.py scripts/mutations/inventory.json              # T8, T9 (Plan 1's)
python scripts/mutation_test.py scripts/mutations/targets.json                # T10
```

Expected: every mutation in all eighteen specs is **caught**. Two failure modes
to read differently:

- **`find not found`** means an anchor moved and a task's re-anchoring step was
  skipped or done wrong. Find the line's new home and fix the spec, in a commit
  naming the task that moved it.
- **A mutation survives** means the guard is gone, not that the spec is stale.
  Do not re-anchor your way out of it — write the test the spec's `_comment`
  says should have caught it.

Then confirm the one file `scripts/mutations/` should have lost:

```bash
rg --files scripts/mutations | rg flasher-selection
```

Expected: no output.

The specs this plan touched *no anchored line* in were run inside their own
tasks, which is where a break is cheap to localise; they are deliberately not
repeated here. If you want the whole set anyway, run every file in
`scripts/mutations/` one at a time — but treat that as a separate session's
work, not a step in this one.

---

## Done

When the eighteen specs above are green, the gates pass, and Task 11's bench
checklist is in the user's hands: use superpowers:finishing-a-development-branch.

The two hardware caveats in `README.md` and `docs/agent-api.md` stay as written
until the user reports back from the bench. Task 11 Step 8 has the edit that
flips them, prepared and unstaged.
