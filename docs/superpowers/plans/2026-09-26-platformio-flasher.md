# PlatformIO Flasher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `esptool` flasher to `platformio`, describe the device it writes without screen vocabulary (`KIND_PORT`, a neutral `detail`), and move write-time rediscovery from `discovery.confirm()` into the family helper's `Identifier`.

**Architecture:** knomi_serial's `identify(ask=True)` becomes "listen now, and fall back to the map only when listening cannot run"; the CLI keeps its old cost by asking `ask=False` first. The flasher module is renamed, then rewritten to read a five-key `detail` (`env`, `port`, `device_id`, `name`, `section`), resolve the identifier in `target()`, and call it once per type in `prepared()`. `build.display_key` becomes `hardware_id_key` with the persisted `display:` prefix kept. Docs follow.

**Tech Stack:** Python 3.11 stdlib only, pytest, ruff, mypy, `scripts/mutation_test.py`.

**Spec:** `docs/superpowers/specs/2026-09-26-platformio-flasher-design.md`

## Global Constraints

- Work in the worktree `C:\git\github\mcu-updater\.worktrees\platformio-flasher`, branch `refactor/platformio-flasher`. Never `cd` to the main checkout.
- **stdlib only.** `pyproject.toml` `dependencies = []` stays empty.
- **Python 3.11 floor.** Run pytest on the floor venv: `../../.venv/Scripts/python.exe -m pytest -q`. pytest's `pythonpath = ["src"]` makes the worktree's `src` win over the venv's editable install.
- **Keep `from __future__ import annotations`** in every module touched.
- **LF line endings.** Run `python scripts/check_line_endings.py` before every commit. Never write a file with a tool that emits CRLF.
- **Gate before every commit:**
  ```bash
  ../../.venv/Scripts/python.exe -m pytest -q
  python -m ruff check src tests scripts
  python -m mypy src
  python scripts/check_line_endings.py
  ```
- **Mutation specs:** before rewriting any line, `git grep -n -F '<the line>' -- scripts/mutations/`. Re-anchor in the same commit. Run `python scripts/mutation_test.py scripts/mutations/<spec>.json` **one spec at a time, with `run_in_background`**, never under a shell timeout. After each run, run `../../.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py` and read its output.
- **No alias, no config migration** for the rename. `flashers: esptool` stops being valid.
- **Wire shapes unchanged** except the `flasher` value (`"esptool"` → `"platformio"`). `WatcherDevice.to_json` does not gain `answered`.
- **The flash log's `display:` prefix stays.** Only the function is renamed.
- **Out of scope** (next spec, README `## TODO`): the `displays`/`screens` wire keys, `display_flash` job kind, `pio_status`, the `display:` prefix migration, and first-time flashing of a silent device. Test-helper names such as `_screen_device` also stay for that change.
- **Commit voice:** conventional prefix, lowercase, no trailing period, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commits are authorized once the gate is green. Push, PR and merge are not.

## Review Focus

1. **A batch spanning two PlatformIO types.** Each type is asked once, and each device is matched only against its own type's answers. Pinned by `test_prepared_asks_each_type_once_with_the_ports_free` (Task 3).
2. **The listen hears nothing at all while the map names the device.** The device is written at its configured port with no confidence. It is neither refused nor reported `remembered`. Pinned by `test_a_listen_that_hears_nothing_writes_the_configured_port_unconfirmed` (Task 3).
3. **The listen cannot run and the map has the device elsewhere.** It is written where the map says, recorded `remembered`, never `answered`. Pinned by `test_a_listen_that_cannot_run_writes_where_the_map_says` (Task 3).
4. **`answered` leaking onto the wire through `fw.device.list`.** `to_json` must not carry it. Pinned by `test_how_a_device_was_found_stays_off_the_wire` (Task 1).
5. **A caller's extra `detail` (bulk's `reason`) surviving `target()`,** and `fw.flash_all`'s selection still reading `name` and `section`. Pinned by `test_a_callers_own_detail_rides_along` (Task 3). The existing `test_flash_all_selects_screens_beside_boards` covers the wire.

---

### Task 1: knomi_serial listens when asked; the CLI reads the map first

**Files:**
- Modify: `src/mcu_updater/discovery/knomi_serial/watcher.py` (`WatcherDevice`)
- Modify: `src/mcu_updater/helpers/knomi_serial.py` (`identify`)
- Modify: `src/mcu_updater/helpers/spec.py` (`Identifier` docstring)
- Modify: `src/mcu_updater/cli.py` (the PlatformIO selection function whose docstring begins "Devices of one PlatformIO type, from the firmware that knows them", ~line 690–740)
- Modify: `scripts/mutations/identity.json`
- Test: `tests/test_helpers.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `WatcherDevice.answered: bool = False`, the last field, off the wire.
- Produces: `KnomiSerialHelper.identify(..., ask=True)`:
  - returns what `discover` heard, each device stamped `answered=True`;
  - returns `read_device_map(...)` (with `answered=False`) only when `discover` raises `UpdaterError`;
  - returns `{}` when the listen heard nothing.

  `ask=False` returns `read_device_map(...)`, unchanged.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing helper tests**

In `tests/test_helpers.py`, delete `test_the_remembered_answer_wins_over_asking` (lines 126–141) and put these in its place:

```python
def test_asking_listens_even_when_the_map_has_an_answer(paths, settings, monkeypatch):
    """`ask=True` is a caller saying the ports are free and it wants to be
    sure - the write-time confirmation. The map is where a device was; only
    the listen says where it is, so a populated map must not stand in for it."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(
        handler,
        "discover",
        lambda p, s, e, **kw: {"aaa111": _device("aaa111", "/dev/ttyUSB3")},
    )

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=True, reporter=null_reporter
    )
    assert found["aaa111"].port == "/dev/ttyUSB3"
    assert found["aaa111"].answered is True


def test_a_listen_that_hears_nothing_is_not_the_map(paths, settings, monkeypatch):
    """The ports were free and nothing spoke. Handing back the map's port would
    call a remembered path a confirmed one."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", lambda p, s, e, **kw: {})

    assert (
        KnomiSerialHelper().identify(
            paths, settings, _pio_entry(), ask=True, reporter=null_reporter
        )
        == {}
    )


def test_a_listen_that_cannot_run_falls_back_to_the_map(paths, settings, monkeypatch):
    """No pyserial, no source tree: the map is the best answer left, and it is
    marked as remembered rather than heard."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.helpers import knomi_serial as handler

    def boom(*a, **kw):
        raise ToolMissingError("no python3 here", tool="python3")

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", boom)

    found = KnomiSerialHelper().identify(
        paths,
        settings,
        _pio_entry(),
        ask=True,
        reporter=lambda stream, line: said.append((stream, line)),
    )
    assert list(found) == ["aaa111"]
    assert found["aaa111"].answered is False
    assert [s for s, _ in said] == ["info", "warn"]


def test_how_a_device_was_found_stays_off_the_wire():
    """`fw.device.list` puts `to_json` on the wire. `answered` is a fact about
    one write-time listen, not a field of the device list."""
    device = WatcherDevice(
        device_id="aaa111", port="/dev/ttyUSB0", present=True, answered=True
    )
    assert "answered" not in device.to_json()
```

In `test_an_empty_map_asks_the_devices_themselves`, add as the last line:

```python
    assert found["bbb222"].answered is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_helpers.py`

Expected: FAIL.
- `test_how_a_device_was_found_stays_off_the_wire` fails with `TypeError ... unexpected keyword argument 'answered'`.
- `test_asking_listens_even_when_the_map_has_an_answer`, `test_a_listen_that_hears_nothing_is_not_the_map` and `test_a_listen_that_cannot_run_falls_back_to_the_map` fail because the map is returned first, or on `AttributeError: ... 'answered'`.
- `test_an_empty_map_asks_the_devices_themselves` fails on `AttributeError`.

- [ ] **Step 3: Add the field**

In `src/mcu_updater/discovery/knomi_serial/watcher.py`, add after `present: bool = False` in `WatcherDevice`. Leave `to_json` unchanged.

```python
    #: Did this device answer a listen just now, rather than come from the
    #: map? Set by the helper that asked. Never on the wire: it is a fact about
    #: one write-time pass, not about the device.
    answered: bool = False
```

- [ ] **Step 4: Rewrite `identify`**

In `src/mcu_updater/helpers/knomi_serial.py`, add `import dataclasses` below `from __future__ import annotations`, with a blank line between, then `from typing import TYPE_CHECKING`, keeping isort order. Replace the whole `identify` method body and docstring with:

```python
        """What is remembered, or what the devices say now.

        `ask=False` is the watcher's map and nothing else. `devices.json` is
        written by knomi_serial's own watcher for the ports Klipper does not
        hold, answers instantly, and costs no port - which is what a status
        poll, and a CLI choosing what to flash, want.

        `ask=True` is a caller saying the ports are free and it wants to be
        sure: the listen pass, six seconds of held ports, and the only answer
        taken at flash time. It is not merged with the map. A device that stays
        silent while others answer is not there, and one the listen never heard
        must not come back with a remembered port dressed as a confirmed one -
        so what was heard is marked `answered`, and nothing else is.

        The map is the fallback only when the listen cannot run at all: it
        needs pyserial out of the module's source tree, and a host missing it
        must reach the caller's own "neither source could tell" refusal - which
        names both sources - rather than a tool error.
        """
        if not ask:
            return read_device_map(paths, entry)
        reporter("info", f"Asking the '{entry.name}' devices which they are...")
        try:
            heard = discover(paths, settings, entry, reporter=reporter)
        except UpdaterError as exc:
            reporter("warn", f"could not ask the devices ({exc}) - using the watcher's map instead")
            return read_device_map(paths, entry)
        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}
```

In `src/mcu_updater/helpers/spec.py`, in the `Identifier` docstring, replace the sentences:

`True additionally opens the free ports and reads what broadcasts back - authoritative, and only possible once the caller has stopped the services holding them.`

with:

`True opens the free ports and reads what broadcasts back - authoritative, and only possible once the caller has stopped the services holding them. What True falls back to when it cannot ask is the helper's policy; a device it heard is returned with `answered` set.`

- [ ] **Step 5: Run the helper tests to verify they pass**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_helpers.py`
Expected: PASS (all).

- [ ] **Step 6: Write the failing CLI test**

In `tests/test_cli.py`, after `test_flashing_a_platformio_screen_matches_its_id_case_insensitively`, add:

```python
def test_a_populated_map_selects_without_opening_a_port(
    c, pio_type, captured, fake_root, monkeypatch
):
    """Choosing what to flash is not the write. The map answers instantly, so
    the six-second listen is kept for the one place that needs it - inside the
    write, once the ports are free - and is not paid twice for one flash."""
    from mcu_updater.helpers import knomi_serial as handler

    _device_map(c.paths, pio_type, aaa111=str(fake_root / "ttyUSB0"))
    asked: list[str] = []
    monkeypatch.setattr(
        handler, "discover", lambda p, s, d, **k: asked.append(d.name) or {}
    )
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert asked == []
    assert len(captured) == 1
```

- [ ] **Step 7: Run it to verify it fails**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_cli.py -k populated_map`

Expected: FAIL. The CLI still passes `ask=True`, so the helper now listens: either `asked == ["knomi_toolchanger"]`, or the empty listen makes the CLI raise `UpdaterError("nothing found ...")` instead of `SystemExit`.

- [ ] **Step 8: Ask the map first in the CLI**

In `src/mcu_updater/cli.py`, replace:

```python
    found = identify.identify(
        c.paths, c.settings, display, ask=True, reporter=stdout_reporter
    )
```

with:

```python
    # The map first: choosing what to flash is not the write, and the write
    # asks again inside its own stop. Listening here as well would hold the
    # ports twice for one flash. Only an empty map is worth the six seconds,
    # because without an answer there is nothing to select.
    found = identify.identify(
        c.paths, c.settings, display, ask=False, reporter=stdout_reporter
    )
    if not found:
        found = identify.identify(
            c.paths, c.settings, display, ask=True, reporter=stdout_reporter
        )
```

In the same function's docstring, replace the paragraph beginning `Asking needs the ports free.` and ending `correctly no-ops.` with:

```
    Asking needs the ports free, and both callers of this function are inside
    `_ports_free`. It still reads the map first (`ask=False`) and asks only
    when the map is empty: the write confirms identity again inside its own
    stop, so a populated map is enough to *select* from, and listening here
    too would hold the ports twice. `services_stopped` is idempotent per unit,
    so the batch's own stop inside that one correctly no-ops.
```

- [ ] **Step 9: Run the CLI and helper tests to verify they pass**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_cli.py tests/test_helpers.py`

Expected: PASS. `test_an_empty_device_map_falls_back_to_asking_the_devices` still sees `asked == [ENV]`, because the map is absent so the second call listens. `test_discovery_failing_still_names_both_sources` still gets the "nothing found" refusal: the listen raises, the fallback map is empty, and the CLI names both sources.

- [ ] **Step 10: Re-anchor `identity.json`**

In `scripts/mutations/identity.json`, replace the three helper mutations (the two named `a caller that did not free the ports is never asked to` / `the remembered answer is preferred to six seconds of held ports`, and `asking is best effort, never a tool error from the fallback`) with:

```json
    {
      "name": "a caller that did not free the ports is never asked to",
      "find": "        if not ask:\n            return read_device_map(paths, entry)",
      "replace": "        if False:\n            return read_device_map(paths, entry)"
    },
    {
      "name": "ask=True listens even when a map exists",
      "find": "        if not ask:\n            return read_device_map(paths, entry)",
      "replace": "        if not ask or read_device_map(paths, entry):\n            return read_device_map(paths, entry)"
    },
    {
      "name": "asking is best effort, never a tool error from the fallback",
      "find": "        except UpdaterError as exc:\n            reporter(\"warn\", f\"could not ask the devices ({exc}) - using the watcher's map instead\")\n            return read_device_map(paths, entry)",
      "replace": "        except UpdaterError:\n            raise"
    },
    {
      "name": "a listen that cannot run falls back to the map",
      "find": "            return read_device_map(paths, entry)\n        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}",
      "replace": "            return {}\n        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}"
    },
    {
      "name": "only what was heard is marked answered",
      "find": "        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}",
      "replace": "        return heard"
    },
```

Then replace the CLI mutation `the ports are free by here, so the devices are asked` with these two:

```json
    {
      "name": "the ports are free by here, so an empty map asks the devices",
      "file": "src/mcu_updater/cli.py",
      "find": "            c.paths, c.settings, display, ask=True, reporter=stdout_reporter",
      "replace": "            c.paths, c.settings, display, ask=False, reporter=stdout_reporter"
    },
    {
      "name": "the remembered answer is preferred to six seconds of held ports",
      "file": "src/mcu_updater/cli.py",
      "find": "        c.paths, c.settings, display, ask=False, reporter=stdout_reporter",
      "replace": "        c.paths, c.settings, display, ask=True, reporter=stdout_reporter"
    },
```

Update `_comment` to end: `... so \`ask\` is a required keyword and the guards that pass it False are as load-bearing as the ones that pass True. At ask=True the listen is the answer and the map only its fallback; the CLI's own map-first read is where the remembered answer is preferred.`

- [ ] **Step 11: Run the gate, then the mutation spec**

Run the gate (Global Constraints). Expected: all green.

Then, **in the background**: `python scripts/mutation_test.py scripts/mutations/identity.json`
Expected: every mutation `KILLED`, none `SURVIVED` or `STALE`.
Then: `../../.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py`. Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add src/mcu_updater/discovery/knomi_serial/watcher.py src/mcu_updater/helpers/knomi_serial.py src/mcu_updater/helpers/spec.py src/mcu_updater/cli.py scripts/mutations/identity.json tests/test_helpers.py tests/test_cli.py
git commit -m "feat(knomi): ask=True listens and falls back to the map only when it cannot, and the cli reads the map first

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: rename the `esptool` flasher to `platformio`, and `KIND_SCREEN` to `KIND_PORT`

A pure rename. Behaviour and the `detail` shape are unchanged until Task 3.

**Files:**
- Rename: `src/mcu_updater/flashers/esptool.py` → `src/mcu_updater/flashers/platformio.py`
- Modify: `src/mcu_updater/flashers/spec.py:83,452`, `src/mcu_updater/flashers/__init__.py:22,43,62,70`, `src/mcu_updater/flashers/registry.py:21,35`, `src/mcu_updater/firmware.py:61,79`
- Modify: `src/mcu_updater/agent/methods/flash.py:493`, `src/mcu_updater/agent/methods/bulk.py:391`, `src/mcu_updater/cli.py:759` (`flashers.KIND_SCREEN` → `flashers.KIND_PORT`)
- Modify: `mcu-updater.cfg:52`, `tests/fixtures/registry.cfg:28`, `ui/src/components/AddMcuWizard.spec.ts:173`
- Modify: `scripts/mutations/flasher-supports.json`, `scripts/mutations/display-flash.json`, `scripts/mutations/flashlog-loop.json`
- Test: every test file listed in Step 5

**Interfaces:**
- Produces:
  - `flashers.PlatformIO`, with `name = "platformio"` and `label = "PlatformIO upload"`;
  - `flashers.KIND_PORT = "port"`;
  - `firmware.FLASHERS == ("bootsel", "dfu_util", "flashtool", "platformio")`;
  - `firmware._SUGGESTED_FLASHERS["platformio"] == "platformio"`.
- Consumes: nothing from Task 1.

- [ ] **Step 1: Update the tests that name the flasher, kind or class**

```bash
sed -i 's/flashers: esptool/flashers: platformio/g' tests/*.py tests/fixtures/registry.cfg
sed -i 's/"esptool"/"platformio"/g' tests/test_flasher_select.py tests/test_agent_display_jobs.py tests/test_artifacts.py tests/test_first_install.py tests/test_cli.py tests/test_agent_flash.py
sed -i 's/\bKIND_SCREEN\b/KIND_PORT/g; s/flashers\.Esptool()/flashers.PlatformIO()/; s/def test_esptool_writes_screens/def test_platformio_writes_port_devices/' tests/test_flasher_select.py
```

Then edit by hand:
- `tests/test_typelist.py:309`: `flashers: flashtoool, esptool` → `flashers: flashtoool, platformio`.
- `tests/test_typelist.py:314`: `"known: bootsel, dfu_util, esptool, flashtool"` → `"known: bootsel, dfu_util, flashtool, platformio"`.
- `tests/test_flash.py:2398`: `` `esptool.port_for` `` → `` `platformio.port_for` ``.
- `tests/test_flash.py:2409`: `from mcu_updater.flashers.esptool import port_for` → `from mcu_updater.flashers.platformio import port_for`.

Check: `git grep -n -e '"esptool"' -e 'flashers: esptool' -e 'KIND_SCREEN' -e 'Esptool' -e 'flashers.esptool' -- tests` prints nothing.

The other `esptool` mentions are left alone: the ones in `tests/test_pio.py`, `tests/test_knomi_*.py`, and the comments in `test_agent_display_jobs.py:304` and `test_agent_displays.py:630`. They name the tool.

- [ ] **Step 2: Run the renamed tests to verify they fail**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_flasher_select.py tests/test_typelist.py tests/test_flash.py -k "platformio or flasher or known or shape"`

Expected: FAIL.
- Collection fails with `ImportError: cannot import name 'KIND_PORT'`.
- The typelist test fails on `unknown flasher 'platformio'`.

- [ ] **Step 3: Rename the module and its names**

```bash
git mv src/mcu_updater/flashers/esptool.py src/mcu_updater/flashers/platformio.py
```

In `src/mcu_updater/flashers/platformio.py`:
- `from .spec import KIND_SCREEN, Bench, Device, FlashRecord, FlashTarget` → `from .spec import KIND_PORT, Bench, Device, FlashRecord, FlashTarget`
- `class Esptool:` → `class PlatformIO:`
- `name = "esptool"` → `name = "platformio"`
- `label = "esptool (PlatformIO)"` → `label = "PlatformIO upload"`
- `return device.kind == KIND_SCREEN` → `return device.kind == KIND_PORT`
- `flasher=Esptool.name,` → `flasher=PlatformIO.name,`
- First docstring line `"""esptool, through PlatformIO: the screens.` → `"""PlatformIO: one env, uploaded to a port.`. The full docstring is rewritten in Task 3.

In `src/mcu_updater/flashers/spec.py`:

```python
#: A device reached at a configured port; its identity, if its family has a way
#: to know one, is confirmed at write time.
KIND_PORT = "port"
```

This replaces the two lines `#: A PlatformIO device reached through its configured port.` and `KIND_SCREEN = "screen"`. In `__all__`, `"KIND_SCREEN",` → `"KIND_PORT",`.

In `src/mcu_updater/flashers/__init__.py`:
- `from .esptool import Esptool` → `from .platformio import PlatformIO`.
- `KIND_SCREEN,` → `KIND_PORT,` in the import list, and `"KIND_SCREEN",` → `"KIND_PORT",` in `__all__`.
- `"Esptool",` → `"PlatformIO",` in `__all__`.
- Keep both lists in their existing (sorted) order. Move the entries if isort or ruff `I` asks.

In `src/mcu_updater/flashers/registry.py`:
- `from .esptool import Esptool` → `from .platformio import PlatformIO`, moved to its sorted place among the `from .` imports.
- In `FLASHERS`, `Esptool(),` → `PlatformIO(),`, same position.

In `src/mcu_updater/firmware.py`:
- `FLASHERS: tuple[str, ...] = ("bootsel", "dfu_util", "flashtool", "platformio")`
- `"platformio": "platformio",` in `_SUGGESTED_FLASHERS`.

In `agent/methods/flash.py:493`, `agent/methods/bulk.py:391` and `cli.py:759`: `kind=flashers.KIND_SCREEN,` → `kind=flashers.KIND_PORT,`.

`mcu-updater.cfg:52` and `tests/fixtures/registry.cfg:28` (if Step 1's sed missed it): `flashers: esptool` → `flashers: platformio`.

`ui/src/components/AddMcuWizard.spec.ts:173`: `flashers: (esptool)` → `flashers: (platformio)`. The string is a fixture of the agent's refusal text, so no UI code changes.

- [ ] **Step 4: Move the mutation specs with the file**

```bash
sed -i 's#src/mcu_updater/flashers/esptool.py#src/mcu_updater/flashers/platformio.py#g' scripts/mutations/display-flash.json scripts/mutations/flashlog-loop.json scripts/mutations/flasher-supports.json
```

In `scripts/mutations/flasher-supports.json`, the entry named `esptool writes screens only` becomes:

```json
    {
      "name": "platformio writes port devices only",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "        return device.kind == KIND_PORT",
      "replace": "        return True"
    },
```

In `scripts/mutations/display-flash.json`'s `_comment`, `the esptool flasher decides where each screen is written` → `the platformio flasher decides where each device is written`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `../../.venv/Scripts/python.exe -m pytest -q`

Expected: PASS, the whole suite. That includes:
- `test_the_flasher_names_are_exactly_the_registry`
- `test_platformio_writes_port_devices`
- the typelist unknown-flasher test
- `test_the_board_and_screen_lookups_answer_in_the_same_shape`
- `test_flash_all_selects_screens_beside_boards`, which now asserts `["platformio", "platformio"]`
- `test_no_mutation_is_left_live_in_the_source`

If a UI toolchain is on PATH, run `npx vitest run src/components/AddMcuWizard.spec.ts` from `ui/`. Expected: PASS. If node is unavailable, say so in the task report. The edited line is a fixture string, and CI runs the UI suite.

- [ ] **Step 6: Gate, then the mutation spec**

Run the gate. Expected: green.

Then, **in the background**: `python scripts/mutation_test.py scripts/mutations/flasher-supports.json`. Expected: all `KILLED`.

Then: `../../.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py`. Expected: PASS.

`display-flash.json` and `flashlog-loop.json` only moved file; their anchors are unchanged and are re-run in Task 3.

- [ ] **Step 7: Commit**

```bash
git add -A src tests scripts/mutations mcu-updater.cfg ui/src/components/AddMcuWizard.spec.ts
git commit -m "refactor(flashers): rename the esptool flasher to platformio and KIND_SCREEN to KIND_PORT

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: a neutral port payload, and rediscovery through the helper

**Files:**
- Modify (rewrite): `src/mcu_updater/flashers/platformio.py`
- Modify: `src/mcu_updater/flashers/spec.py` (the `Device` and `FlashTarget` docstrings, plus `Flasher.prepared`, `Flasher.write` and `needs_services_stopped` comments; lines ~6, 98, 123, 212, 262, 277)
- Modify: `src/mcu_updater/agent/methods/flash.py` (`_pio_flash` Device, ~line 474–500; add `port_detail`)
- Modify: `src/mcu_updater/agent/methods/bulk.py` (~383–399 Device; `_platformio_json` lines 49–60)
- Modify: `src/mcu_updater/cli.py` (the Device built in the PlatformIO selection function)
- Modify (comments only): `src/mcu_updater/discovery/knomi_serial/listen.py:204`, `src/mcu_updater/discovery/knomi_serial/watcher.py:130`, `src/mcu_updater/discovery/confirm.py:41`, `src/mcu_updater/discovery/registry.py:10`, `src/mcu_updater/flashers/flash.py:104`
- Modify: `scripts/mutations/display-flash.json`, `scripts/mutations/flashlog-loop.json`
- Create: `tests/test_platformio_flasher.py`
- Test: `tests/test_agent_display_jobs.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes (Task 1): `WatcherDevice.answered`; `KnomiSerialHelper.identify(ask=True)` semantics.
- Consumes (Task 2): `PlatformIO`, `KIND_PORT`.
- Produces: a `KIND_PORT` device's `detail` is exactly:
  - `{"env": PioType, "port": str, "device_id": str (lowercased, "" if none), "name": str, "section": str}`;
  - plus any caller extras.

  The target's `detail` adds `"identifier": helpers.Identifier | None`.
- Produces: `port_for(device: Mapping[str, Any], discovered: dict[str, Any], ctx: Any) -> tuple[str, Confidence | None, str | None]` (same return annotation text as before).
- Produces: `agent.methods.flash.port_detail(display: PioType, screen: Mapping[str, Any]) -> dict[str, Any]`, used by `bulk`.

- [ ] **Step 1: Write the failing flasher unit tests**

Create `tests/test_platformio_flasher.py`:

```python
"""The PlatformIO flasher asks the family's helper where each device is.

Rediscovery used to be a `discovery.confirm()` sweep inside this flasher, with
the knomi sources wired into it. It is the helper's question now, through the
`Identifier` capability - so a family with no way to tell its devices apart is
written at its configured ports, and this module names no vendor.
"""

from __future__ import annotations

import dataclasses
import types

import pytest

from mcu_updater.artifacts import KIND_PIO_ENV, Artifact
from mcu_updater.discovery.knomi_serial import WatcherDevice
from mcu_updater.errors import ToolMissingError
from mcu_updater.flashers import KIND_PORT, Bench, Device, PlatformIO
from mcu_updater.providers.pio import PioType
from mcu_updater.settings import Settings


def _env(name="knomi_toolchanger"):
    return PioType(name=name, env=name, source="/nowhere", firmware="knomi_serial")


def _device(env, port, device_id="aaa111", name="t0_knomi"):
    return Device(
        type=env.name,
        id=port,
        chipset="",
        state="unknown",
        fw=env.firmware,
        kind=KIND_PORT,
        detail={
            "env": env,
            "port": port,
            "device_id": device_id,
            "name": name,
            "section": f"knomi_serial {name}",
        },
    )


class _Identifier:
    """Answers from a fixed table, and records every question."""

    name = "fake"

    def __init__(self, **by_type):
        self.by_type = by_type
        self.asked: list[tuple[str, bool]] = []

    def identify(self, paths, settings, entry, *, ask, reporter):
        self.asked.append((entry.name, ask))
        return self.by_type.get(entry.name, {})

    def remembered_at(self, paths, entry):
        return ""


class _Broken(_Identifier):
    def identify(self, paths, settings, entry, *, ask, reporter):
        raise ToolMissingError("no pyserial", tool="discover")


def _bench(paths, **settings):
    return Bench(
        paths=paths,
        settings=Settings(service_backend="null", clean_before_build=False, **settings),
        controller=lambda unit: None,
    )


def _ctx():
    said: list[tuple[str, str]] = []
    return types.SimpleNamespace(
        reporter=lambda stream, line: said.append((stream, line)), said=said
    )


def _target(paths, device, helper):
    return PlatformIO().target(
        paths, device, helper, Artifact(KIND_PIO_ENV, "/nowhere"), stop_services=()
    )


def _write_one(paths, target):
    bench, ctx = _bench(paths), _ctx()
    with PlatformIO().prepared(bench, [target], ctx) as session:
        return PlatformIO().write(bench, session, target, ctx), ctx


@pytest.fixture
def uploads(monkeypatch):
    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, e, port, **k: ports.append(port) or {"port": port, "chip": None},
    )
    return ports


def test_prepared_asks_each_type_once_with_the_ports_free(paths):
    """One listen covers every port, so asking per device would multiply its
    cost - and a batch spanning two types asks each of them, once."""
    env_a, env_b = _env("knomi_a"), _env("knomi_b")
    ident = _Identifier()
    targets = [
        _target(paths, _device(env_a, "/dev/ttyUSB0"), ident),
        _target(paths, _device(env_a, "/dev/ttyUSB1", device_id="bbb222"), ident),
        _target(paths, _device(env_b, "/dev/ttyUSB2", device_id="ccc333"), ident),
    ]

    with PlatformIO().prepared(_bench(paths), targets, _ctx()) as session:
        assert set(session) == {"knomi_a", "knomi_b"}

    assert sorted(ident.asked) == [("knomi_a", True), ("knomi_b", True)]


def test_a_dry_run_asks_nobody(paths):
    """Asking can open real serial ports. A rehearsal that touches hardware is
    not a rehearsal."""
    ident = _Identifier()
    target = _target(paths, _device(_env(), "/dev/ttyUSB0"), ident)
    ctx = _ctx()

    with PlatformIO().prepared(_bench(paths, dry_run=True), [target], ctx) as session:
        assert session == {}

    assert ident.asked == []
    assert ("info", "[dry-run] would ask the devices which they are") in ctx.said


def test_a_family_with_no_identifier_is_written_at_its_configured_port(paths, uploads):
    """Nothing can say which device is which, so the configured port is the
    answer - what every write did before identity existed - and nothing
    claims it was confirmed."""
    result, _ = _write_one(paths, _target(paths, _device(_env(), "/dev/ttyUSB0"), None))

    assert uploads == ["/dev/ttyUSB0"]
    assert result["confidence"] is None


def test_an_identifier_that_fails_leaves_the_configured_port(paths, uploads):
    """Never fatal, whatever the identifier: a host that cannot ask was
    flashing by configured port before, and refusing would be a new way to
    fail."""
    result, ctx = _write_one(
        paths, _target(paths, _device(_env(), "/dev/ttyUSB0"), _Broken())
    )

    assert uploads == ["/dev/ttyUSB0"]
    assert result["confidence"] is None
    assert [s for s, _ in ctx.said] == ["warn"]


@pytest.mark.parametrize("answered, reason", [(True, "answered"), (False, "remembered")])
def test_the_confidence_says_how_the_device_was_found(paths, uploads, answered, reason):
    env = _env()
    found = WatcherDevice(
        device_id="aaa111", port="/dev/ttyUSB0", present=True, answered=answered
    )
    ident = _Identifier(**{env.name: {"aaa111": found}})

    result, _ = _write_one(paths, _target(paths, _device(env, "/dev/ttyUSB0"), ident))

    assert result["confidence"] == reason


def test_a_callers_own_detail_rides_along(paths):
    """bulk's `reason` is the caller's, and survives. The target's uniform
    slots are the device's own."""
    device = _device(_env(), "/dev/ttyUSB0")
    device = dataclasses.replace(device, detail={**device.detail, "reason": "forced"})

    target = _target(paths, device, None)

    assert target.detail["reason"] == "forced"
    assert target.detail["identifier"] is None
    assert (target.type, target.id, target.flasher) == (
        "knomi_toolchanger",
        "/dev/ttyUSB0",
        "platformio",
    )
```

- [ ] **Step 2: Point the agent tests at the helper, and add the three write-time cases**

In `tests/test_agent_display_jobs.py`:

```bash
sed -i 's/"mcu_updater.discovery.knomi_serial.listen.discover"/"mcu_updater.helpers.knomi_serial.discover"/g' tests/test_agent_display_jobs.py
```

Replace `_discover_only_for`'s docstring with:

```python
    """A `discover()` stand-in scoped to one PlatformIO type, the way the real
    thing is - the flasher asks once per type in the batch, and a type it was
    not told about answers nothing."""
```

After `test_a_dry_run_never_opens_a_serial_port`, add:

```python
def test_a_remembered_port_does_not_stand_in_for_a_silent_screen(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """The narrowing the helper seam made on purpose. The watcher's map still
    names this screen, but the ports were free, another screen answered and
    this one did not - so it is not there, and the map's port is a memory."""
    from mcu_updater.helpers import knomi_serial as handler

    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(
        handler, "read_device_map", lambda p, e: _found(aaa111=screens_port(screens, "t0"))
    )
    monkeypatch.setattr(
        handler, "discover", _discover_only_for(ENV, somebodyelse=str(fake_root / "ttyUSB9"))
    )
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))
    ports = _no_upload(monkeypatch)

    job = _flash_one(api, screens)

    assert job.state == "succeeded", job.error
    assert ports == [], "nothing was written"
    assert "did not answer" in job.result["failures"][0]["error"]


def test_a_listen_that_hears_nothing_writes_the_configured_port_unconfirmed(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """Nothing spoke at all, so there is nothing to match against: the
    configured port, as before identity existed, and no confidence. The map
    naming another port does not make it a confirmed one."""
    from mcu_updater.helpers import knomi_serial as handler

    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(
        handler, "read_device_map", lambda p, e: _found(aaa111=str(fake_root / "ttyUSB9"))
    )
    monkeypatch.setattr(handler, "discover", lambda *a, **k: {})
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))
    ports = _no_upload(monkeypatch)

    assert _flash_one(api, screens).state == "succeeded"

    assert ports == [screens_port(screens, "t0")]
    assert _flashlog(paths)["display:aaa111"]["confidence"] is None


def test_a_listen_that_cannot_run_writes_where_the_map_says(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """No pyserial: the watcher's map is the best answer left. Written where
    the map says, and recorded as remembered - never as answered."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.helpers import knomi_serial as handler

    def boom(*a, **k):
        raise ToolMissingError("pyserial is not installed", tool="discover")

    moved_to = str(fake_root / "ttyUSB9")
    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(handler, "read_device_map", lambda p, e: _found(aaa111=moved_to))
    monkeypatch.setattr(handler, "discover", boom)
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))
    ports = _no_upload(monkeypatch)

    assert _flash_one(api, screens).state == "succeeded"

    assert ports == [moved_to]
    assert _flashlog(paths)["display:aaa111"]["confidence"] == "remembered"
```

In `tests/test_cli.py`, in `test_flashing_a_platformio_screen_matches_its_id_case_insensitively`, change `target.detail["screen"]["device_id"]` to `target.detail["device_id"]`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_platformio_flasher.py tests/test_agent_display_jobs.py tests/test_cli.py`

Expected: FAIL.
- `test_platformio_flasher.py`: every test fails with `KeyError: 'display'` from `target()`.
- In `test_agent_display_jobs.py`, the discovery tests fail: the flasher still sweeps through `confirm()`, which the helper patch does not reach. Among them are `test_a_screen_is_written_where_it_answered_not_where_it_was` and the three new ones.
- The CLI test fails with `KeyError: 'device_id'`.

- [ ] **Step 4: Rewrite `flashers/platformio.py`**

Replace the whole file with:

```python
"""PlatformIO: one env, uploaded to a port.

`pio run -t upload` for any PlatformIO env, at the port its caller configured.
Every other write here targets a device by an identity it carries on the bus.
A device reached at a configured port may carry none the host can read, so if
its family has a way to tell its devices apart, they are asked again at the
moment of the write - which is only possible once the services holding the
ports are down.

That is one step, in one place, and it is the whole reason `prepared()` exists
on the protocol. Who can answer is the family's helper, through the
`helpers.Identifier` capability; this module only asks, and names no vendor. A
family with no identifier is written at its configured ports, which is what
every write did before identity existed.

:mod:`mcu_updater.providers.pio` keeps the upload itself, including the parts
with no MCU counterpart at all: never letting PlatformIO choose its own upload
port, and following a udev symlink to the device PlatformIO can actually see.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..artifacts import KIND_PIO_ENV, Artifact
from ..devices import STATE_ESP_ROM
from ..errors import FlashError, UpdaterError
from .spec import KIND_PORT, Bench, Device, FlashRecord, FlashTarget

if TYPE_CHECKING:
    # Annotation only. `discovery.spec` imports from this package, so a runtime
    # import here closes a cycle - and `from __future__ import annotations`
    # means nothing needs the symbol at run time. `port_for` imports the module
    # it builds one from lazily, for the same reason.
    from ..discovery.spec import Confidence
    from ..helpers.spec import Helper, Identifier
    from ..paths import Paths
    from ..providers.pio import PioType


class PlatformIO:
    """Writes one device of one PlatformIO type."""

    name = "platformio"
    label = "PlatformIO upload"
    chipsets: tuple[str, ...] = ("esp32",)
    states: tuple[str, ...] = (STATE_ESP_ROM,)
    #: The klippy module holds the port open for the write itself, and
    #: pyserial's exclusive open is an advisory flock that both it and the
    #: upload take. Unlike flashtool, this one is about the write and not about
    #: getting somewhere first.
    needs_services_stopped = True
    accepts: tuple[str, ...] = (KIND_PIO_ENV,)

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A PlatformIO device reached through its configured port. Its
        identity, if its family has a way to know one, is confirmed at write
        time, in `prepared`."""
        return device.kind == KIND_PORT

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        """The device as its caller described it, plus who can say which one
        it is.

        The identifier comes from the helper selection already resolved, so
        `prepared` needs no second config load. `None` for a family without
        the capability.
        """
        return FlashTarget(
            flasher=self.name,
            type=device.type,
            id=device.id,
            stop_services=stop_services,
            # Anything else the caller put in `detail` (bulk's `reason`) rides
            # along; the flasher's own key wins.
            detail={**device.detail, "identifier": helpers.identifier(helper)},
            artifact=artifact,
        )

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[dict[str, dict[str, Any]]]:
        """Ask each type's devices which they are, now that the ports are free.

        The watcher pause that used to happen here is part of the outer stop
        `write_all` opens over the batch's own `stop_services` union, verified
        and journaled - see `flashers.batch.write_all` and
        `service.services_stopped`.

        The device list was read before the stop, so its ports describe where
        these devices were; a remembered port is what the whole device-id
        scheme exists to avoid. This is the one moment identity can be
        resolved rather than remembered, so the family's identifier is asked
        with `ask=True`: the ports are free, and the caller wants to be sure.
        What that costs, and what it falls back to, is the helper's policy.

        Once per type rather than once per device: a single listen covers
        every port at once, and doing it per device would multiply its cost by
        the number of devices.

        Skipped entirely on a dry run: asking can open real serial ports, and
        a rehearsal that touches hardware is not a rehearsal.
        """
        if bench.settings.dry_run:
            ctx.reporter("info", "[dry-run] would ask the devices which they are")
            yield {}
            return
        by_type: dict[str, tuple[PioType, Identifier | None]] = {}
        for target in targets:
            env = target.detail["env"]
            by_type[env.name] = (env, target.detail["identifier"])
        yield {
            name: _identify(bench, env, identifier, ctx)
            for name, (env, identifier) in by_type.items()
        }

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from ..providers import pio as pio_mod

        env = target.detail["env"]
        port, confidence, problem = port_for(
            target.detail, (session or {}).get(env.name) or {}, ctx
        )
        if problem is not None:
            # Raised rather than collected, because a batch records a failure by
            # catching one. The check itself is unchanged: a device that stayed
            # silent while every other one answered is not there, and writing to
            # the port it used to be on would write to whatever is on that port
            # now.
            raise FlashError(problem, type=env.name, port=port)

        result = pio_mod.upload(
            bench.paths, bench.settings, env, port, reporter=ctx.reporter
        )

        return {
            "name": target.detail["name"],
            "port": port,
            # Taken back off by `write_all` - the ports are free exactly once,
            # inside this batch's stop, and this is the only moment the answer
            # exists.
            "confidence": confidence.reason if confidence is not None else None,
            **result,
        }

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The image this device now holds, filed under its hardware id.

        `None` for a device with no hardware id: the port it was written on is
        not a durable name for it - see `build.display_key`.
        """
        from ..build import display_key
        from ..providers import pio as pio_mod

        env = target.detail["env"]
        ident = target.detail["device_id"]
        if not ident:
            return None
        # The build already hashed the image and noted its commit; re-deriving
        # them here would be a second answer to a question with a recorded one.
        side = pio_mod.read_sidecar(bench.paths, env) or {}
        return FlashRecord(
            key=display_key(ident),
            mcu_type=env.name,
            fw=env.env,
            bin_sha256=side.get("bin_sha256"),
            # The PlatformIO sidecar calls the tree commit `sha`; the flash log
            # calls it `fw_sha`. One rename at the boundary.
            fw_sha=side.get("sha"),
        )

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait for. A device written here is not on the Klipper
        bus, so there is no device node whose absence would bring Klipper up
        in an error state - which is the only thing the MCU wait is protecting
        against."""


def _identify(
    bench: Bench, env: PioType, identifier: Identifier | None, ctx: Any
) -> dict[str, Any]:
    """One type's answer, or `{}` - never an exception.

    A host that cannot ask was writing to configured ports perfectly well
    before identity existed, and degrading to that is strictly what it used to
    do; refusing to flash would be a new way to fail. The knomi helper already
    softens its own listen this way. Holding it here too means the guarantee
    does not rest on every identifier getting it right.
    """
    if identifier is None:
        return {}
    try:
        return identifier.identify(
            bench.paths, bench.settings, env, ask=True, reporter=ctx.reporter
        )
    except UpdaterError as exc:
        ctx.reporter(
            "warn",
            f"could not ask the '{env.name}' devices which they are ({exc}) - "
            f"writing to their configured ports.",
        )
        return {}


def port_for(
    device: Mapping[str, Any], discovered: dict[str, Any], ctx: Any
) -> tuple[str, Confidence | None, str | None]:
    """Where to write this device, how sure we are, and why not if no answer.

    `device` is the target's `detail`. `(port, confidence, refusal reason)` -
    the same three-tuple `flash.device_for` returns for a board. `confidence`
    is None when nothing confirmed the identity and the port is a remembered
    one.

    Three cases, and the middle one is the point:

    * **Nothing was identified at all** - no identifier, one that could not
      run or heard nothing, or a dry run. Fall back to the configured port,
      which is what every write did before this. No worse than it was, and
      confirmed by nothing, so None.
    * **This device was found** - write to the port it was found on, not the
      one it used to be on. If those differ it moved, and saying so is the only
      warning anybody would ever get. The confidence says whether it answered
      just now or was remembered.
    * **Others were found and this one was not** - it is not there. The ports
      were free and every other device spoke, so a silent write to its old
      port would be a write to whatever is on that port now.

    A device with no id at all is the fourth case and falls back rather than
    failing. A `serial:` section names a socket, and its identity only arrives
    from the module's own report - so a module too old to send one, or a device
    that was silent when the list was read, has nothing to match on. Failing
    those would take flashing away from installs that have it today, to punish
    them for what their klippy module does not say.
    """
    from ..discovery import spec as discovery

    configured = device["port"]
    if not discovered:
        return configured, None, None

    ident = device["device_id"]
    if not ident:
        ctx.reporter(
            "warn",
            f"{device['name']} reports no hardware id, so the device on "
            f"{configured} cannot be confirmed as the one meant. Writing to the "
            f"configured port.",
        )
        return configured, None, None

    found = discovered.get(ident)
    if found is None:
        return configured, None, (
            "did not answer when asked which devices are present, so its "
            "port cannot be confirmed. Writing to the port it used to be on "
            "could write to a different device."
        )

    if found.port != configured:
        ctx.reporter(
            "warn",
            f"{device['name']} ({ident}) is on {found.port}, not "
            f"{configured} - it has moved. Writing to where it actually is.",
        )
    reason = discovery.ANSWERED if found.answered else discovery.REMEMBERED
    return found.port, discovery.Confidence(reason), None
```

- [ ] **Step 5: Build the new payload in the three callers**

In `src/mcu_updater/agent/methods/flash.py`, add at module level. Place it after the imports, before the first class or function; if a module-level helper section exists, put it there.

```python
def port_detail(display: PioType, screen: Mapping[str, Any]) -> dict[str, Any]:
    """A klippy-reported PlatformIO device, as the `platformio` flasher reads it.

    The id is resolved the way the flasher always has - the configured one,
    else the one the firmware reported - so nothing changes about which id a
    device is matched on. Shared with `bulk`, whose fleet selection reads the
    same payload.
    """
    return {
        "env": display,
        "port": screen["configured_path"],
        "device_id": (screen.get("device_id") or screen.get("reported_id") or "").lower(),
        "name": screen["name"],
        "section": screen["section"],
    }
```

Add `from collections.abc import Mapping` if missing. Import `PioType` under `TYPE_CHECKING` from `...providers.pio`, and add `from typing import TYPE_CHECKING` if the module lacks it.

In `_pio_flash`, replace `detail={"display": display, "screen": s},` with `detail=port_detail(display, s),`.

Replace the comment ending `...beyond the two steps the esptool flasher now owns.` with:

```python
        # Built from the list read *before* the stop, which is the only list a
        # running Klipper can produce. Everything after this - the stop, the
        # watcher pause, the identity check, the writes - is the same machinery
        # a fleet flash uses; the `platformio` flasher owns the two steps that
        # are specific to a device reached at a port.
```

In `src/mcu_updater/agent/methods/bulk.py`, add `from .flash import port_detail` beside `from ._api import _Base`. Replace:

```python
                            detail={
                                "display": display,
                                "screen": screen,
                                "reason": "forced" if scope == "all" else status.reason,
                            },
```

with:

```python
                            detail={
                                **port_detail(display, screen),
                                "reason": "forced" if scope == "all" else status.reason,
                            },
```

In `_platformio_json`, `target.detail["screen"]["name"]` → `target.detail["name"]` and `target.detail["screen"]["section"]` → `target.detail["section"]`.

In `src/mcu_updater/cli.py`, replace the `detail={"display": display, "screen": {...}}` block with:

```python
                    detail={
                        "env": display,
                        "port": device.port,
                        "device_id": device.device_id.lower(),
                        "name": device.device_id,
                        "section": f"{display.klipper_section} {device.device_id}",
                    },
```

- [ ] **Step 6: Reword the comments that named the old sweep**

- `discovery/knomi_serial/listen.py` (~204) and `discovery/knomi_serial/watcher.py` (~130): replace the three-line `# \`family\` is what a caller needing per-family grouping (esptool's ...` comment with:
  ```python
        # `family` names the `[type]` this sighting belongs to - Sighting itself
        # carries no family field by design. Nothing groups on it since the
        # `platformio` flasher moved to the helper's `Identifier`; it stays
        # because removing it is not that change.
  ```
- `discovery/confirm.py` (~40–41): `A caller matching a device it cares about does so by \`id\`, the same way \`port_for\` matches a screen's \`device_id\` against what came back.` → `A caller matching a device it cares about does so by \`id\`.`
- `discovery/registry.py:10`: `` `esptool.port_for`'s board-side counterpart in `flash_katapult` `` → `` `flash_katapult`'s board lookup (`flashers.flash.device_for`) ``.
- `flashers/flash.py:104`: `` in the shape `esptool.port_for` `` → `` in the shape `platformio.port_for` ``, and `already uses for displays` → `already uses for PlatformIO devices`.
- `flashers/spec.py`:
  - Line 6: `` `displays.upload` drives PlatformIO's esptool at a port it has to *rediscover* first, because every screen here is an indistinguishable CH340. `` → `` `providers.pio.upload` drives PlatformIO at a port whose device may have to be *rediscovered* first, because a KNOMI is an indistinguishable CH340. ``
  - `Device` docstring: `` `{"display", "screen"}` for esptool `` → `` `{"env", "port", "device_id", "name", "section"}` for platformio ``.
  - `FlashTarget` docstring: `a chipset means nothing to esptool` → `a chipset means nothing to a PlatformIO upload`, and `the display's \`[type]\` name and its configured port` → `a PlatformIO device's \`[type]\` name and its configured port`.
  - ~212: `esptool needs it because the klippy module holds the port for the write itself.` → `platformio needs it because the klippy module holds the port for the write itself.`
  - ~262: `what esptool needs to carry across a batch is a map of which screen answered on which port` → `what platformio needs to carry across a batch is each type's answer to "which device is on which port"`.
  - ~277: `the chip esptool reported` → `the chip a PlatformIO upload reported`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_platformio_flasher.py tests/test_agent_display_jobs.py tests/test_cli.py tests/test_agent_bulk.py tests/test_flash.py`

Expected: PASS. The three new agent tests patch `read_device_map` on the helper module only, so `fw.device.list`'s own read is untouched. If one fails because the device list changed anyway, fix the test's patch target, not production code, and ledger a ruling.

- [ ] **Step 8: Re-anchor the mutation specs**

In `scripts/mutations/display-flash.json`:
- Add `"tests/test_platformio_flasher.py",` to `command`, before `"-q"`.
- Replace every entry whose `file` is `src/mcu_updater/flashers/platformio.py` with the entries below. The `status.py`, `flash.py` and `batch.py` entries are untouched here.

```json
    {
      "name": "identity is verified once the ports are free",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "        return identifier.identify(\n            bench.paths, bench.settings, env, ask=True, reporter=ctx.reporter\n        )",
      "replace": "        return {}"
    },
    {
      "name": "the write asks with the ports free rather than reading the map",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "            bench.paths, bench.settings, env, ask=True, reporter=ctx.reporter",
      "replace": "            bench.paths, bench.settings, env, ask=False, reporter=ctx.reporter"
    },
    {
      "name": "an identifier that cannot answer is never fatal",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "    except UpdaterError as exc:\n        ctx.reporter(\n            \"warn\",\n            f\"could not ask the '{env.name}' devices",
      "replace": "    except ZeroDivisionError as exc:\n        ctx.reporter(\n            \"warn\",\n            f\"could not ask the '{env.name}' devices"
    },
    {
      "name": "a device is written where it was found, not where it was",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "    if found.port != configured:",
      "replace": "    return configured, None, None\n    if found.port != configured:"
    },
    {
      "name": "a device that did not answer is not flashed at its old port",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "        if problem is not None:",
      "replace": "        if False:"
    },
    {
      "name": "a dry run never opens a serial port",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "        if bench.settings.dry_run:\n            ctx.reporter(\"info\", \"[dry-run] would ask the devices which they are\")",
      "replace": "        if False:\n            ctx.reporter(\"info\", \"[dry-run] would ask the devices which they are\")"
    },
    {
      "name": "a confirmed write records how the device was identified",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "            \"confidence\": confidence.reason if confidence is not None else None,",
      "replace": "            \"confidence\": None,"
    },
    {
      "name": "an unconfirmed write is not recorded as confirmed",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "            \"confidence\": confidence.reason if confidence is not None else None,",
      "replace": "            \"confidence\": \"answered\","
    },
    {
      "name": "a remembered device is not recorded as answered",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "    reason = discovery.ANSWERED if found.answered else discovery.REMEMBERED",
      "replace": "    reason = discovery.ANSWERED"
    },
    {
      "name": "a device with no hardware id is recorded nowhere",
      "file": "src/mcu_updater/flashers/platformio.py",
      "find": "        ident = target.detail[\"device_id\"]\n        if not ident:\n            return None",
      "replace": "        ident = target.detail[\"device_id\"]\n        if False:\n            return None"
    },
```

The "never fatal" mutation swaps the caught type rather than re-raising. An `except ...: raise` replacement would leave `exc` unused, which ruff flags. With `ZeroDivisionError`, the `UpdaterError` escapes `prepared()` and `test_an_identifier_that_fails_leaves_the_configured_port` fails.

Keep the entries' order as they were (the batch.py and status.py ones stay in place). Verify each `find` is present exactly once:

```bash
../../.venv/Scripts/python.exe - <<'EOF'
import json
for name in ("display-flash", "flashlog-loop"):
    spec = json.load(open(f"scripts/mutations/{name}.json", encoding="utf-8"))
    for m in spec["mutations"]:
        path = m.get("file", spec.get("file"))
        n = open(path, encoding="utf-8").read().count(m["find"])
        print(name, n, m["name"])
EOF
```

Expected: every count is `1`, except the two `"confidence": confidence.reason ...` entries, which share one line and each show `1`.

`scripts/mutations/flashlog-loop.json`'s platformio entry (`        if not ident:\n            return None`) still matches once; leave it.

- [ ] **Step 9: Gate, then each mutation spec, one at a time**

Run the gate. Expected: green.

Then, **in the background, one at a time**, each followed by `../../.venv/Scripts/python.exe -m pytest -q tests/test_repo_hygiene.py`:
1. `python scripts/mutation_test.py scripts/mutations/display-flash.json`. Expected: all `KILLED`.
2. `python scripts/mutation_test.py scripts/mutations/flashlog-loop.json`. Expected: all `KILLED`.
3. `python scripts/mutation_test.py scripts/mutations/identity.json`. Expected: all `KILLED`. It re-runs `test_agent_display_jobs.py` against the new flasher.

- [ ] **Step 10: Commit**

```bash
git add -A src tests scripts/mutations
git commit -m "refactor(platformio): describe the device by env and port, and ask the family's identifier at write time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `build.display_key` becomes `build.hardware_id_key`

**Files:**
- Modify: `src/mcu_updater/build.py:915-932,954`
- Modify: `src/mcu_updater/flashers/platformio.py` (`record`)
- Modify: `src/mcu_updater/flashers/spec.py:177`
- Modify: `src/mcu_updater/agent/methods/status.py:1418,1424`
- Modify: `scripts/mutations/display-flash.json` (the `status.py` entry `a record the screen disagrees with is discarded`)
- Test: `tests/test_flashlog_loop.py`, `tests/test_agent_display_jobs.py:835,838`

**Interfaces:**
- Produces: `build.hardware_id_key(ident: str) -> str`, which returns `f"display:{ident.lower()}"`. `display_key` no longer exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_flashlog_loop.py`:

```python
def test_a_hardware_id_files_under_the_persisted_prefix():
    """`display:` is on disk in every flash log written so far. Renaming the
    function must not rename the key - moving it is a migration, and that is
    its own change."""
    from mcu_updater.build import hardware_id_key

    assert hardware_id_key("AAA111") == "display:aaa111"
```

In `tests/test_agent_display_jobs.py`'s `_record`, replace `from mcu_updater.build import FlashLog, display_key` with `from mcu_updater.build import FlashLog, hardware_id_key`, and `display_key(ident),` with `hardware_id_key(ident),`.

- [ ] **Step 2: Run to verify it fails**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_flashlog_loop.py tests/test_agent_display_jobs.py`
Expected: FAIL with `ImportError: cannot import name 'hardware_id_key'`.

- [ ] **Step 3: Rename**

In `src/mcu_updater/build.py`, replace the function with:

```python
def hardware_id_key(ident: str) -> str:
    """A flash-log key for a device known by a hardware id rather than a serial.

    Prefixed because the log is one flat dict and these are two identity
    namespaces: a board is keyed by its `/dev/serial/by-id` serial, a device
    reached at a port by the id its firmware states (a KNOMI's six hex
    characters of eFuse MAC). They cannot collide in practice, but sharing a
    keyspace unprefixed leaves nothing in the file saying which kind of name a
    key is.

    The prefix is still `display:`, because it is persisted in every flash log
    written so far. Renaming it is a migration, and is not this function's job.

    **Never a port.** `docs/decisions.md` rules out per-port tracking, and the
    hardware id is exactly what made dropping it safe - it follows the device
    into any socket. A device with no id gets no record at all rather than one
    keyed by where it happened to be.

    Lowercased on the way in: the id is emitted lowercase at both ends, but the
    vendor's own docs say not to depend on that.
    """
    return f"display:{ident.lower()}"
```

- `build.py:954`: `` Screens live here too, under :func:`display_key` `` → `` Devices reached at a port live here too, under :func:`hardware_id_key` ``.
- `flashers/platformio.py` `record`: `from ..build import display_key` → `from ..build import hardware_id_key`, `key=display_key(ident),` → `key=hardware_id_key(ident),`, and the docstring's `` `build.display_key` `` → `` `build.hardware_id_key` ``.
- `flashers/spec.py:177`: `` `build.display_key` `` → `` `build.hardware_id_key` ``.
- `agent/methods/status.py`: `from ...build import display_key` → `from ...build import hardware_id_key`, and `display_key(ident), reader.running_sha(...)` → `hardware_id_key(ident), reader.running_sha(...)`.

In `scripts/mutations/display-flash.json`, the `a record the screen disagrees with is discarded` entry becomes:

```json
      "find": "        record = flashlog.entry_for(\n            hardware_id_key(ident), reader.running_sha(entry.get(\"firmware_version\"))\n        )",
      "replace": "        record = flashlog.entry_for(hardware_id_key(ident), None)"
```

If the joined line exceeds 110 characters and ruff reflows it, re-anchor on the formatted text.

Check: `git grep -n display_key -- src tests scripts` prints nothing.

- [ ] **Step 4: Run to verify it passes**

Run: `../../.venv/Scripts/python.exe -m pytest -q tests/test_flashlog_loop.py tests/test_agent_display_jobs.py`
Expected: PASS.

- [ ] **Step 5: Gate, then the mutation spec**

Run the gate. Expected: green.

Then, in the background: `python scripts/mutation_test.py scripts/mutations/display-flash.json`, followed by the hygiene test. Expected: all `KILLED`; hygiene PASS.

- [ ] **Step 6: Commit**

```bash
git add -A src tests scripts/mutations
git commit -m "refactor(build): rename display_key to hardware_id_key, keeping the persisted display: prefix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: docs and the remaining comments

No behaviour change. The "test" is the grep in Step 2 and the gate.

**Files:**
- Modify: `docs/agent-api.md:624,1087,1142,1182,1372,1768-1773,1785`
- Modify: `docs/decisions.md:162,531,588-597`, plus one new section
- Modify: `README.md:47,325,327,381,581`
- Modify (comments): `src/mcu_updater/providers/pio.py:382`, `src/mcu_updater/stop_services.py:37`, `src/mcu_updater/flashers/registry.py:365`
- `docs/layout.md`: checked, and it names no flasher module, so no change.

- [ ] **Step 1: Edit**

`docs/agent-api.md`:
- 624 and 1372: `flashers: (esptool)` → `flashers: (platformio)`.
- 1087 and 1785: `"flasher": "esptool"` → `"flasher": "platformio"`.
- 1142: `This half of the wire says what esptool actually wrote to` → `This half of the wire says what the \`platformio\` flasher actually wrote to`.
- 1182: `A PlatformIO type has no candidate scanner yet — esptool detection is deferred — so` → `A PlatformIO type has no candidate scanner yet — detecting a bare device for the \`platformio\` flasher is deferred — so`.
- 1768–1773, the "Two deliberate softenings" paragraph. Replace `And if discovery cannot run at all (no pyserial, no source tree) every screen falls back, because that is exactly what every flash did before this existed.` with:

  > And if discovery cannot run at all (no pyserial, no source tree), each screen is written where the watcher's map says, or at its configured port when the map has nothing. That is what every flash did before this existed, and such a write records `remembered` or no confidence — never `answered`. A listen that runs and hears nothing at all is treated the same way minus the map: nothing was confirmed, so every screen is written to its configured port with no confidence.

- Leave 1586, 1684, 1776, 1779 and 1798 alone: they name esptool the tool, which PlatformIO still runs for an ESP32.

`docs/decisions.md`:
- 162: `flashtool over USB/CAN, esptool, DFU, BOOTSEL mass-storage` → `flashtool over USB/CAN, a PlatformIO upload to a port (\`platformio\`), DFU, BOOTSEL mass-storage`.
- 531: `(PlatformIO's \`esptool\`, say)` → `(a bare-device scanner for the \`platformio\` flasher, say)`.
- 588–589: replace `Both seams stay, and they compose: the handler's answer is a sighting like any other, and \`confirm()\` still decides what to trust at write time.` with:

  > Both seams stay, and each has its own caller. The identifier answers at write time too: the `platformio` flasher asks it with `ask=True` inside the stop, and never runs `confirm()`. `confirm()` ranks sightings for boards.

- 594–597: replace `` `fw.device.list` passes False and takes the remembered answer or nothing; the CLI passes True from inside `_ports_free` and takes the authoritative one. `` with:

  > `fw.device.list` passes False and takes the remembered answer or nothing. The CLI, choosing what to flash inside `_ports_free`, passes False first and True only when the map is empty, because the write asks again anyway. For knomi_serial, True means the listen is the answer: the map is used only when the listen cannot run, and a device the listen did not hear is not reported with a remembered port.

- Add a new section after "Identity is a helper capability, not a discovery source":

  ```markdown
  ### The PlatformIO flasher is `platformio`, not `esptool`

  It runs `pio run -t upload` for any PlatformIO env and does not implement
  esptool; PlatformIO runs esptool underneath for an ESP32, and would run
  something else for another target. The name `esptool` stays free for a real
  ESP32 image flasher, one that writes an image without PlatformIO - nothing
  needs one yet. The old name was dropped without an alias because the
  `flashers:` key had not reached `main`.

  Its kind is `KIND_PORT`, and its `detail` names an env and a port, not a
  display or a screen. That vocabulary still survives on the wire (`displays`,
  `screens`, `display_flash`) and in the flash log's `display:` prefix; removing
  it is a wire change with its own `API_VERSION` bump, tracked in the README's
  TODO.
  ```

`README.md`:
- 47: `` - [x] `esptool` - ESP32, via PlatformIO `` → `` - [x] `platformio` - any PlatformIO env, uploaded to its configured port ``.
- 325: `` `flashtool`, `esptool`, `dfu_util`, `bootsel` `` → `` `flashtool`, `platformio`, `dfu_util`, `bootsel` ``.
- 327: `` `esptool` a PlatformIO env `` → `` `platformio` a PlatformIO env ``.
- 381 and 581: `flashers: esptool` → `flashers: platformio`.

Comments:
- `providers/pio.py:382`: `the esptool flasher noting which image a screen was just given` → `the \`platformio\` flasher noting which image a device was just given`.
- `stop_services.py:37`: `what the esptool flasher hardcoded` → `what the PlatformIO flasher hardcoded`.
- `flashers/registry.py:365`: `` `flashtool` and `esptool` `` → `` `flashtool` and `platformio` ``.

- [ ] **Step 2: Verify nothing names the flasher `esptool` any more**

Run: `git grep -n -i 'esptool' -- src docs README.md mcu-updater.cfg tests ui/src scripts ':!docs/superpowers'`

Expected: every remaining line names esptool the *tool*. That covers:
- ROM handshake, port ownership and the banner: `byid.py:73`, `pio.py:1,549`, `service.py:315`, `status.py:2190`, `flash.py:411`, `listen.py:10,16,106,111`, `watcher.py:4`
- `agent-api.md:1586,1684,1776,1779,1798`
- the `test_pio.py` / `test_knomi_*.py` docstrings, and the comments at `test_agent_display_jobs.py:304` and `test_agent_displays.py:630`

None names the flasher, a `flashers:` value, or `Esptool`.

Run: `git grep -n -e KIND_SCREEN -e 'detail\["display"\]' -e 'detail\["screen"\]' -e display_key -- src tests scripts`
Expected: no output.

- [ ] **Step 3: Gate**

Run the gate. Expected: green.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/agent-api.md docs/decisions.md src/mcu_updater/providers/pio.py src/mcu_updater/stop_services.py src/mcu_updater/flashers/registry.py
git commit -m "docs: the flasher is platformio, and the identifier answers at write time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the last task

Bench check, done by Vi, on the bench board and never the toolhead:

- One `fw.flash` of a KNOMI with the watcher running and a populated map. The log shows the listen, and the result carries `"flasher": "platformio"` and `"confidence": "answered"`.
- One CLI flash of that type, which listens once, not twice.

This needs `mcu-updater.service` restarted after checkout. See the bench-restart note in memory.
