# First Install by Flasher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The "Add a new board" flow offers every type in the type list. Whether a type can be set up from a bare board is decided by its install family's `flashers:` list, and a successful install finds the board on its USB port rather than by chipset.

**Architecture:**
- Flashers that can find a bare board implement a new optional capability, `CandidateScanner`. DFU and BOOTSEL scans move out of the agent and into `flashers/dfu_util.py` and `flashers/bootsel.py`.
- One pure function, `flashers.first_install(entry, families)`, picks the flasher for a type. The agent's `fw.add_mcu.start`, the new `fw.add_mcu.scan`, every `targets[]` row, and the CLI's `add-mcu` all call it.
- After the write, the wait is keyed on the USB port the scan saw (`usb.UsbDevice.name`), never on the chipset segment in the by-id name.

**Tech Stack:** Python 3.11+ stdlib only (agent/CLI), pytest, ruff, mypy; Vue 3 + TypeScript + vitest (UI).

**Spec:** [docs/superpowers/specs/2026-09-25-first-install-by-flasher-design.md](../specs/2026-09-25-first-install-by-flasher-design.md) — read it before any task. The departures from it are listed right below and are also recorded in the spec's "Amendments" section.

## Deviations from the approved spec

Each one was forced by the code once the plan got down to line level:

1. **Identification moves into the scanners.** The spec kept `_identify_dfu`/`_identify_bootsel` in the agent. However, `add_mcu_start` and `fw.add_mcu.scan` are generic, so an agent-side identifier would have to be picked by flasher name — the caller branch the spec forbids. Instead, `scan_candidates` takes `tracked: Sequence[TrackedBoard]`, which the agent builds from the type list. The ROM-id derivation (`dfu_serial_for`, BOOTSEL's id == serial) is knowledge about the flasher, and it now lives there.
2. **`port` is per device, and `CandidateScan.port` is a property.**
   - A single scan-level port can't give the port of a *named* `dfu_serial` among several boards.
   - Every device dict therefore gains an additive `"port"` key. `fw.dfu.scan` and `fw.bootsel.scan` gain it too, and it is documented in agent-api.md.
   - `CandidateScan.port` is the port of the single device when `ready`, else None.
3. **`CandidateScanner.candidate_prefix`** (`"dfu"`, `"bootsel"`). It builds the `<prefix>_<reason>` refusal code, so the agent never names a flasher.
4. **New `Paths.block_sysfs` seam** (env `MCU_UPDATER_FAKE_BLOCK_SYSFS`) plus `usb.device_for_block`. BOOTSEL needs these to map a boot-ROM volume to its port.
5. **`targets[].first_install` is tested in `tests/test_agent_targets.py`**, not `tests/test_ui_contract.py`. The latter only checks `fw.*` literals and `API_VERSION`.
6. **Behaviour changes visible in existing tests.** Each is a synchronous refusal replacing a later failure:
   - A type whose install family lists no bare-board writer (e.g. `[firmware klipper] flashers: flashtool` on a no-Katapult STM32) is now refused as `unsupported_chipset` at `fw.add_mcu.start`, before a job exists. Previously the job was accepted and then failed.
   - The CLI's `add-mcu` now refuses such a type **before the build** instead of after menuconfig.
   - A `dfu_serial` passed for a BOOTSEL type is now `device_not_found`, because BOOTSEL devices carry no `serial`. It used to be ignored silently.
7. **Reason constants leave the agent.** The `DFU_*`/`BOOTSEL_*` class attributes in `agent/methods/status.py` and the `_api.py` Protocol are removed; nothing outside those two files references them. They become module constants in the flasher modules.
8. **`fw.add_mcu.start`'s job result** gains additive `flasher` and `port` keys.

## Global Constraints

- **Line endings:** LF everywhere. Run `python scripts/check_line_endings.py` before every commit.
- **Dependencies:** stdlib only. `pyproject.toml` `dependencies = []` stays empty.
- **Python floor:** 3.11. Keep `from __future__ import annotations` in every module that has it.
- **Mutation specs:**
  - Before rewriting any source line, `git grep -n` it under `scripts/mutations/`. Re-anchor the spec in the **same commit**, and rename the mutation if the rule it proves has widened.
  - Run `python scripts/mutation_test.py scripts/mutations/<spec>.json` **one spec at a time**, never in parallel. After every run, read `tests/test_repo_hygiene.py::test_no_mutation_is_left_live_in_the_source`'s output.
- **Firmware writes:** no agent performs a real flash. The bench step (Task 8) is Vi's, on the bench board only, never the toolhead.
- **No caller branches on a builder or chipset prefix** in the agent, CLI or wizard. The one sanctioned exception is the wizard's DFU pick-a-serial (spec §5).
- **Commit voice:** conventional prefix, lowercase sentence, no trailing period. End with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commits are pre-authorized once the gate passes.
- **Gate:** run it on a 3.11 interpreter. From the worktree root, create the venv once with `uv venv --python 3.11 && uv pip install -e . pytest ruff mypy` if `.venv` is absent. Then call it directly, never through an activation made in an earlier shell call:
  ```bash
  .venv/Scripts/python.exe -m pytest -q
  .venv/Scripts/python.exe -m ruff check src tests scripts
  .venv/Scripts/python.exe -m mypy src
  .venv/Scripts/python.exe scripts/check_line_endings.py
  ```
- **UI gate** (Task 7), from `ui/`: `npm test && npm run typecheck && npm run lint && npm run format:check`.
- **Worktree:** all work is in `C:\git\github\mcu-updater-first-install-by-flasher`, branch `feat/first-install-by-flasher`. Use `git -C <worktree>` or absolute paths, because a shell's cwd can reset to the main checkout between calls.

## Review Focus

1. **A type whose first install can't be answered** still yields a row with a reason, and `fw.status` still answers. This covers a missing `[firmware X]` section, an unknown flasher name, an empty `firmwares:` or an empty `chipset:`. Tests are in Task 3 (`test_first_install_never_raises_*`) and Task 6 (`test_a_row_first_install_cannot_answer_still_renders`).
2. **A board appearing on a different USB port during the wait** is not reported as the one just written. Tests are in Task 1 (`test_a_new_device_on_another_port_is_not_found`) and Task 5 (`test_a_board_on_another_port_is_not_the_new_board`).
3. **The scan can't resolve a port**, for example because sysfs is unreadable. The wait still finds the new board and the job log carries the no-port warning. Test is in Task 5 (`test_no_port_falls_back_to_any_new_board_with_a_warning`).
4. **A tracked cmake RP2040 sitting in BOOTSEL** is named (`tracked_by`) rather than shown as a stranger. `Registry` never listed cmake types. Test is in Task 2 (`test_bootsel_names_a_tracked_board_of_any_builder`).
5. **A newer UI against an older agent** falls back to exactly the old kconfig-only flow. This is the window the release order guarantees. Test is in Task 7 (`"falls back to the kconfig-only flow against an older agent"`).

---

### Task 1: Port seams and the port-keyed wait

**Files:**
- Modify: `src/mcu_updater/paths.py` (the `bootsel_root`/`usb_sysfs`/`tty_sysfs` fields at ~75-86, the docstring env list at ~20, `from_env` at ~270-285)
- Modify: `src/mcu_updater/discovery/usb.py` (after `device_for_tty`, and `__all__` at line 95)
- Modify: `src/mcu_updater/discovery/byid.py` (`find_untracked` at 278, `wait_for_new_device` at ~375)
- Modify: `tests/conftest.py` (new helper `on_port`, placed after `bootsel_device_node`)
- Create: `tests/test_port_wait.py`

**Interfaces:**
- Produces:
  - `Paths.block_sysfs: str = ""`
  - `usb.device_for_block(devices: list[UsbDevice], paths: Paths, node: str) -> UsbDevice | None`
  - `byid.port_of(paths: Paths, dev: BusDevice, inventory: list[usb.UsbDevice] | None = None) -> str | None`
  - `find_untracked(..., port: str | None = None)`
  - `wait_for_new_device(..., port: str | None = None)`
  - conftest `on_port(paths: Paths, root: pathlib.Path, port: str) -> Paths`

- [ ] **Step 1: Add the `on_port` test helper to `tests/conftest.py`**

```python
def on_port(paths: Paths, root: pathlib.Path, port: str) -> Paths:
    """`paths` with every tty and block device hanging off USB port `port`.

    Real sysfs is symlinks. These are plain nested directories whose *names*
    carry the port, which is all `usb.device_for_sysfs_path` reads: it walks
    up the path and splits each component on ':'. No interface directory
    (`<port>:1.0`) is used, because NTFS reads ':' as an alternate-data-stream
    separator.

    No `serial` file is written under the fake USB device, deliberately:
    `byid.scan` swaps in `hardware.serial` whenever one exists, which would
    collapse every fake board on this port onto that one serial.
    """
    usb_root = root / "sys-usb"
    (usb_root / port).mkdir(parents=True, exist_ok=True)
    tty = root / "sys-dev" / port / "tty"
    block = root / "sys-dev" / port / "block"
    tty.mkdir(parents=True, exist_ok=True)
    block.mkdir(parents=True, exist_ok=True)
    return dataclasses.replace(
        paths, usb_sysfs=str(usb_root), tty_sysfs=str(tty), block_sysfs=str(block)
    )
```

Add `import dataclasses` to conftest's imports if it is not already there.

- [ ] **Step 2: Write the failing tests in `tests/test_port_wait.py`**

```python
"""The add-mcu wait is keyed on the USB port, not the by-id chipset segment.

A Roadrunner enumerates as `usb-Vylyne_Roadrunner_<serial>`; its segment is
`Roadrunner`, not `rp2040`, so a chipset filter reported "No board appeared"
after every successful install. The port is what survives the reboot.
"""

from __future__ import annotations

from mcu_updater.discovery import usb
from mcu_updater.discovery.byid import find_untracked, port_of, scan, wait_for_new_device

from .conftest import bootsel_device_node, make_device, on_port

PORT = "1-1.2"


def test_a_new_device_on_the_named_port_is_found(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert [d.serial for d in find_untracked(here, set(), port=PORT)] == ["AAAA"]


def test_a_new_device_on_another_port_is_not_found(paths, fake_root):
    here = on_port(paths, fake_root, "1-1.3")
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert find_untracked(here, set(), port=PORT) == []


def test_the_chipset_segment_is_not_a_filter(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-UNPROVISIONED-1")

    appeared = wait_for_new_device(here, set(), port=PORT, timeout=0.1, settle=0)
    assert [d.serial for d in appeared] == ["RR-UNPROVISIONED-1"]


def test_no_port_is_every_new_board(paths, fake_root):
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert [d.serial for d in find_untracked(paths, set())] == ["AAAA"]


def test_port_of_names_the_usb_device(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    (dev,) = scan(here)
    assert port_of(here, dev) == PORT
    assert port_of(paths, dev) is None


def test_a_bootsel_volume_resolves_to_its_port(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    node = bootsel_device_node(fake_root / "bootsel_root")

    found = usb.device_for_block(usb.collect(here), here, node)
    assert found is not None and found.name == PORT
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_port_wait.py -v`
Expected: collection errors — `ImportError: cannot import name 'port_of'`, and `on_port` fails on `block_sysfs`.

- [ ] **Step 4: Add the `Paths` field**

In `src/mcu_updater/paths.py`:
- Add `  MCU_UPDATER_FAKE_BLOCK_SYSFS replace /sys/class/block when finding a volume's port` to the module docstring's env list, beside `MCU_UPDATER_FAKE_TTY_SYSFS`.
- Add the field right after `tty_sysfs`:

```python
    #: Replaces `/sys/class/block`, where a boot ROM's mass-storage volume is
    #: traced back to its USB port. Mirrors `tty_sysfs` above.
    block_sysfs: str = ""
```

In `from_env`:
- Beside `tty_sysfs = ...`, add `block_sysfs = e.get("MCU_UPDATER_FAKE_BLOCK_SYSFS") or ""`.
- Pass `block_sysfs=block_sysfs,` to the constructor next to `tty_sysfs=tty_sysfs,`.

In `docs/layout.md`'s env-var table, add a row after `MCU_UPDATER_FAKE_TTY_SYSFS`:

```markdown
| `MCU_UPDATER_FAKE_BLOCK_SYSFS` | `/sys/class/block` |
```

- [ ] **Step 5: Add `device_for_block` to `src/mcu_updater/discovery/usb.py`**

Next to the existing `_DEFAULT_TTY_SYSFS` constant:

```python
_DEFAULT_BLOCK_SYSFS = "/sys/class/block"
```

After `device_for_tty`:

```python
def device_for_block(devices: list[UsbDevice], paths: Paths, node: str) -> UsbDevice | None:
    """Return the physical USB device owning a block device node.

    `node` is anything that resolves to the node - a `/dev/disk/by-id/...`
    link included - so a boot ROM's mass-storage volume maps to the same
    port the board's tty will hang off once it reboots.
    """
    root = paths.block_sysfs or _DEFAULT_BLOCK_SYSFS
    dev = os.path.basename(os.path.realpath(node))
    return device_for_sysfs_path(devices, os.path.join(root, dev))
```

Change `__all__` to `["UsbDevice", "collect", "device_for_block", "device_for_sysfs_path", "device_for_tty"]`.

- [ ] **Step 6: Add `port_of` and the `port=` filter to `src/mcu_updater/discovery/byid.py`**

Add after `scan()`:

```python
def port_of(
    paths: Paths, dev: BusDevice, inventory: list[usb.UsbDevice] | None = None
) -> str | None:
    """The USB port (`usb.UsbDevice.name`, e.g. "1-1.2") a by-id device hangs
    off, or None when sysfs cannot say.

    The port, not the serial, is what survives a reboot into new firmware -
    the rule `helpers/klipper.py`'s topology wait already follows.
    """
    if inventory is None:
        inventory = usb.collect(paths)
    tty = os.path.basename(os.path.realpath(dev.path))
    hardware = usb.device_for_tty(inventory, paths, tty)
    return hardware.name if hardware is not None else None
```

In `find_untracked`:
- Add `port: str | None = None,` after `chipset`.
- Append this paragraph to the docstring: "`port` keeps only boards on that USB port - the add-mcu wait, where the chipset segment of a by-id name is not a filter (docs/decisions.md)."
- Change the loop head to:

```python
    inventory = usb.collect(paths) if port else None
    out = []
    for dev in scan(paths):
        if not dev.is_mcu:
            continue
        if dev.serial in known:
            continue
        if wanted_group is not None and dev.fw.lower() not in wanted_group:
            continue
        if chipset and dev.chipset != chipset:
            continue
        if port and port_of(paths, dev, inventory) != port:
            continue
        out.append(dev)
```

In `wait_for_new_device`, add `port: str | None = None,` after `chipset`, and pass `port=port` in **both** `find_untracked(...)` calls.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_port_wait.py tests/test_devices.py tests/test_usb.py -q`. Drop any of those files that don't exist; `git ls-files tests | grep -i "usb\|devices"` lists the candidates.
Expected: PASS.

- [ ] **Step 8: Gate and commit**

Run the full gate. Then:

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add src/mcu_updater/paths.py src/mcu_updater/discovery/usb.py src/mcu_updater/discovery/byid.py tests/conftest.py tests/test_port_wait.py docs/layout.md
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(discovery): key the new-device wait on a usb port, and trace a boot-rom volume to its port"
```

---

### Task 2: `CandidateScan` / `CandidateScanner`, and the scans move into their flashers

**Files:**
- Modify: `src/mcu_updater/flashers/spec.py` (new dataclasses and Protocol, after `Flasher`)
- Modify: `src/mcu_updater/flashers/registry.py` (new `candidate_scanner`)
- Modify: `src/mcu_updater/flashers/__init__.py` (re-exports and `__all__`)
- Modify: `src/mcu_updater/flashers/dfu_util.py` (`DfuUtil.scan_candidates`, reason constants)
- Modify: `src/mcu_updater/flashers/bootsel.py` (`Bootsel.scan_candidates`, reason constants)
- Modify: `src/mcu_updater/agent/methods/flash.py:626-839` (`_identify_*` deleted, `dfu_scan`/`bootsel_scan` become delegates)
- Modify: `src/mcu_updater/agent/methods/status.py:~2101-2112` and `src/mcu_updater/agent/methods/_api.py:~47-56` (remove the `DFU_*`/`BOOTSEL_*` constants)
- Modify: `docs/agent-api.md` (`fw.dfu.scan`, `fw.bootsel.scan`: per-device `port`)
- Create: `tests/test_candidate_scan.py`

**Interfaces:**
- Consumes (Task 1): `usb.device_for_block`, `usb.collect`, conftest `on_port`.
- Produces:
  - `flashers.TrackedBoard(type: str, serial: str, chipset: str)` (frozen dataclass)
  - `flashers.CandidateScan(ready: bool, reason: str | None, message: str | None, devices: list[dict[str, Any]], extra: dict[str, Any] = {})`, with property `.port -> str | None` and method `.to_json() -> dict[str, Any]`
  - `flashers.CandidateScanner` (runtime_checkable Protocol): `name: str`, `candidate_prefix: str`, `scan_candidates(self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter) -> CandidateScan`
  - `flashers.name_tracked(devices: list[dict[str, Any]], owners: dict[str, list[tuple[str, str]]], field: str) -> None`
  - `flashers.candidate_scanner(flasher: Flasher) -> CandidateScanner | None`
  - Agent: `Api._tracked_boards() -> list[TrackedBoard]` and `Api._candidate_report(scanner: CandidateScanner) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests in `tests/test_candidate_scan.py`**

```python
"""Finding a bare board is the flasher's job: `CandidateScanner`.

The agent's `fw.dfu.scan`/`fw.bootsel.scan` keep their wire shapes
(tests/test_agent_dfu.py and tests/test_agent_bootsel.py pass unmodified);
this file covers what moved and what is new - `port` on every device, and
naming a tracked board of any builder.
"""

from __future__ import annotations

import dataclasses

from mcu_updater import flashers
from mcu_updater.flashers import CandidateScan, TrackedBoard

from .conftest import bootsel_device_node, mounted_bootsel_volume, on_port
from .test_agent_dfu import ONE_BOARD, TWO_BOARDS, patch_dfu


def _quiet(level, message):
    pass


def test_dfu_util_and_bootsel_are_scanners_and_nothing_else_is():
    scanners = {f.name for f in flashers.FLASHERS if flashers.candidate_scanner(f)}
    assert scanners == {"dfu_util", "bootsel"}


def test_port_is_the_one_ready_devices_port():
    ready = CandidateScan(True, None, None, [{"port": "1-1.2"}])
    two = CandidateScan(False, "ambiguous", "two", [{"port": "1-1.2"}, {"port": "1-1.3"}])
    assert ready.port == "1-1.2"
    assert two.port is None


def test_to_json_carries_count_and_extras():
    scan = CandidateScan(False, "none", "nothing", [], {"vid_pid": "0483:df11"})
    assert scan.to_json() == {
        "devices": [],
        "count": 0,
        "ready": False,
        "reason": "none",
        "message": "nothing",
        "vid_pid": "0483:df11",
    }


def test_a_dfu_device_carries_its_port(paths, monkeypatch):
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    scan = flashers.by_name("dfu_util").scan_candidates(paths, tracked=(), reporter=_quiet)

    assert scan.ready
    # dfu-util's `path` is sysfs's own port name.
    assert scan.port == "6-1.6.6.1.3"


def test_two_dfu_boards_are_ambiguous_with_a_port_each(paths, monkeypatch):
    patch_dfu(monkeypatch, stdout=TWO_BOARDS)
    scan = flashers.by_name("dfu_util").scan_candidates(paths, tracked=(), reporter=_quiet)

    assert scan.reason == "ambiguous"
    assert {d["port"] for d in scan.devices} == {"6-1.6.6.1.3", "6-1.6.6.1.4"}
    assert scan.port is None


def test_a_bootsel_device_carries_its_port(paths, fake_root):
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    here = dataclasses.replace(on_port(paths, fake_root, "1-1.2"), bootsel_root=str(root))

    scan = flashers.by_name("bootsel").scan_candidates(here, tracked=(), reporter=_quiet)

    assert scan.ready
    assert scan.port == "1-1.2"


def test_bootsel_names_a_tracked_board_of_any_builder(paths, fake_root):
    """A cmake Roadrunner is tracked in the type list, never in `Registry` -
    which is all the old agent-side identification read."""
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, "E0C9125B0D9B")
    here = dataclasses.replace(paths, bootsel_root=str(root))
    tracked = [TrackedBoard("roadrunner", "E0C9125B0D9B", "rp2040")]

    scan = flashers.by_name("bootsel").scan_candidates(here, tracked=tracked, reporter=_quiet)

    assert scan.devices[0]["tracked_by"] == "roadrunner"
    assert scan.devices[0]["known_serial"] == "E0C9125B0D9B"


def test_one_id_owned_twice_names_neither():
    devices = [{"serial": "X"}]
    flashers.name_tracked(devices, {"X": [("a", "1"), ("b", "2")]}, "serial")
    assert devices[0]["tracked_by"] is None and devices[0]["known_serial"] is None
```

`flashers.by_name(...)` is typed `Flasher`, so `.scan_candidates` is an attribute mypy won't see. That's fine: mypy runs on `src` only.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_candidate_scan.py -v`
Expected: `ImportError: cannot import name 'CandidateScan'`.

- [ ] **Step 3: Add the types to `src/mcu_updater/flashers/spec.py`**

- Add `Sequence` to the `collections.abc` import.
- Add `runtime_checkable` to the `typing` import.
- Under the existing `if TYPE_CHECKING:` block, add `from ..build import Reporter` if `Reporter` isn't already imported there.

Then, after the `Flasher` Protocol:

```python
@dataclasses.dataclass(frozen=True)
class TrackedBoard:
    """A board the type list already tracks - what a scan names its finds by."""

    type: str
    serial: str
    chipset: str


@dataclasses.dataclass(frozen=True)
class CandidateScan:
    """What a flasher can see that it could write as a new board.

    `reason` is the flasher's own vocabulary (`none`, `ambiguous`, ...) and
    `extra` carries the flasher-specific keys its wire result always had
    (`vid_pid`, `mounts`, `output`), merged in unchanged by `to_json`.
    Every device dict carries `port` - `usb.UsbDevice.name`, or None when the
    flasher cannot say - which is what the post-write wait is keyed on.
    """

    ready: bool
    reason: str | None
    message: str | None
    devices: list[dict[str, Any]]
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def port(self) -> str | None:
        """The port a write would go to: the one device's, when `ready`."""
        if not self.ready or len(self.devices) != 1:
            return None
        port = self.devices[0].get("port")
        return str(port) if port else None

    def to_json(self) -> dict[str, Any]:
        return {
            "devices": self.devices,
            "count": len(self.devices),
            "ready": self.ready,
            "reason": self.reason,
            "message": self.message,
            **self.extra,
        }


@runtime_checkable
class CandidateScanner(Protocol):
    """A flasher that can find a bare board it could write.

    Optional, and reached through `flashers.candidate_scanner`. Implementing
    it is what makes a flasher able to set up a new board - `first_install`
    asks nothing else. `candidate_prefix` spells the refusal code a not-ready
    scan becomes (`<prefix>_<reason>`), so no caller names a flasher.
    """

    name: str
    candidate_prefix: str

    def scan_candidates(
        self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter
    ) -> CandidateScan: ...


def name_tracked(
    devices: list[dict[str, Any]], owners: dict[str, list[tuple[str, str]]], field: str
) -> None:
    """Set each device's `tracked_by`/`known_serial` from `owners`, keyed on
    `device[field]`.

    A device matching nothing is what a genuinely new board looks like, not an
    error. Two owners for one key names neither: an unlabelled board is a
    small annoyance, and a board labelled as the wrong one is how you flash
    the toolhead you meant to leave alone.
    """
    for device in devices:
        device["known_serial"] = None
        device["tracked_by"] = None
        matches = owners.get(str(device.get(field) or ""), [])
        if len(matches) == 1:
            device["tracked_by"], device["known_serial"] = matches[0]
```

- [ ] **Step 4: Add the accessor to `src/mcu_updater/flashers/registry.py`**

Extend the `.spec` import to `from .spec import KIND_SERIAL, CandidateScanner, Device, Flasher, FlashTarget`, and add after `by_name`:

```python
def candidate_scanner(flasher: Flasher) -> CandidateScanner | None:
    """`flasher` as a `CandidateScanner`, or None when it cannot find a new
    board. The one place that asks - the way helper capabilities are reached."""
    return flasher if isinstance(flasher, CandidateScanner) else None
```

In `src/mcu_updater/flashers/__init__.py`:
- Add `candidate_scanner` to the `.registry` import.
- Add `CandidateScan`, `CandidateScanner`, `TrackedBoard` and `name_tracked` to the `.spec` import.
- Add all five names to `__all__`.

- [ ] **Step 5: Move the DFU scan into `src/mcu_updater/flashers/dfu_util.py`**

Module constants, after the imports:

```python
#: `scan_candidates` reasons - see its docstring for where each sends the user.
SCAN_NO_TOOL = "no_tool"
SCAN_PERMISSION_DENIED = "permission_denied"
SCAN_NONE = "none"
SCAN_AMBIGUOUS = "ambiguous"
```

Then:
- Add `from typing import Any` and `from collections.abc import Sequence` if absent.
- Extend the `.spec` import with `CandidateScan`, `TrackedBoard` and `name_tracked`.
- Under `TYPE_CHECKING`, import `Reporter` from `..build` and `Paths` from `..paths`, whichever are not already imported.

In `class DfuUtil`, add `candidate_prefix = "dfu"` next to `name`, and this method. Its docstring is the agent's `dfu_scan` docstring (`agent/methods/flash.py:660-684`), moved verbatim, plus the identification paragraph from `_identify_dfu`:

```python
    def scan_candidates(
        self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter
    ) -> CandidateScan:
        """<the moved dfu_scan docstring, then _identify_dfu's paragraph on the
        derived DFU serial and the collision rule>"""
        from ..devices import dfu_devices, dfu_serial_for
        from ..errors import DfuPermissionError, ToolMissingError, UpdaterError
        from .flash import DFU_VID_PID

        extra: dict[str, Any] = {"vid_pid": DFU_VID_PID}
        try:
            devices = dfu_devices(reporter=reporter)
        except ToolMissingError as exc:
            return CandidateScan(False, SCAN_NO_TOOL, str(exc), [], extra)
        except DfuPermissionError as exc:
            # The raw dfu-util output, because a permissions diagnosis is exactly
            # the case where the operator wants to see what the tool actually said.
            # UpdaterError keeps its extras in .data, not as attributes.
            return CandidateScan(
                False,
                SCAN_PERMISSION_DENIED,
                str(exc),
                [],
                {**extra, "output": exc.data.get("output")},
            )
        except UpdaterError as exc:
            return CandidateScan(False, exc.code, str(exc), [], extra)

        owners: dict[str, list[tuple[str, str]]] = {}
        for board in tracked:
            computed = dfu_serial_for(board.serial)
            if computed:
                owners.setdefault(computed, []).append((board.type, board.serial))
        name_tracked(devices, owners, "serial")
        for device in devices:
            # dfu-util's `path` is the same string as sysfs's USB device name.
            device["port"] = device.get("path") or None

        if not devices:
            return CandidateScan(
                False,
                SCAN_NONE,
                "No board is in DFU mode. Fit the boot jumper (or hold BOOT0) and "
                "replug the board.",
                devices,
                extra,
            )
        if len(devices) > 1:
            return CandidateScan(
                False,
                SCAN_AMBIGUOUS,
                f"{len(devices)} boards are in DFU mode. Pick the one to flash by its "
                f"serial, or unplug the others.",
                devices,
                extra,
            )
        return CandidateScan(True, None, None, devices, extra)
```

- [ ] **Step 6: Move the BOOTSEL scan into `src/mcu_updater/flashers/bootsel.py`**

Module constants:

```python
SCAN_NONE = "none"
SCAN_NOT_MOUNTED = "not_mounted"
SCAN_AMBIGUOUS = "ambiguous"
```

In `class Bootsel`, add `candidate_prefix = "bootsel"`, and this method. Its docstring is the agent's `bootsel_scan` docstring (`agent/methods/flash.py:773-795`) plus `_identify_bootsel`'s (738-757), moved verbatim. The module already imports `bootsel_devices`, `bootsel_id_for` and `bootsel_scan` from `..devices`.

```python
    def scan_candidates(
        self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter
    ) -> CandidateScan:
        """<the moved bootsel_scan docstring, then _identify_bootsel's>"""
        from ..discovery import usb

        present = bootsel_devices(paths)
        inventory = usb.collect(paths)
        devices: list[dict[str, Any]] = []
        for node in present:
            hardware = usb.device_for_block(inventory, paths, node)
            devices.append(
                {
                    "id": bootsel_id_for(node),
                    "node": node,
                    "port": hardware.name if hardware is not None else None,
                }
            )
        owners: dict[str, list[tuple[str, str]]] = {}
        for board in tracked:
            if board.chipset.startswith("rp2040"):
                owners.setdefault(board.serial, []).append((board.type, board.serial))
        name_tracked(devices, owners, "id")

        mounts = bootsel_scan(paths)
        extra: dict[str, Any] = {"mounts": mounts, "mount_count": len(mounts)}
        if not present:
            return CandidateScan(
                False,
                SCAN_NONE,
                "No RP2040 in BOOTSEL is attached. Hold BOOTSEL and replug the board.",
                devices,
                extra,
            )
        if not mounts:
            return CandidateScan(
                False,
                SCAN_NOT_MOUNTED,
                f"An RP2040 in BOOTSEL is attached ({', '.join(present)}) but "
                f"nothing mounted its volume - this host has no automounter. "
                f"Re-run install.sh to install the udev rule, which mounts each "
                f"board under /media/<user>/BOOTSEL/by-path/<port>.",
                devices,
                extra,
            )
        if len(mounts) > 1:
            return CandidateScan(
                False,
                SCAN_AMBIGUOUS,
                f"{len(mounts)} RPI-RP2 volumes are mounted at once "
                f"({', '.join(mounts)}) - which one is this board? Unplug the "
                f"others and try again.",
                devices,
                extra,
            )
        return CandidateScan(True, None, None, devices, extra)
```

Add the same `Any`/`Sequence`/`.spec`/`TYPE_CHECKING` imports as in Step 5, whichever are missing.

- [ ] **Step 7: Make the agent's scans thin delegates**

In `src/mcu_updater/agent/methods/flash.py`:
- Delete `_identify_dfu` and `_identify_bootsel`.
- Replace the bodies of `dfu_scan` and `bootsel_scan`. Each keeps a one-paragraph docstring that points at the flasher: "Report-don't-raise; the reasons and what each means live on `flashers.dfu_util.DfuUtil.scan_candidates`".
- Add the two helpers.

```python
    def _tracked_boards(self) -> list[Any]:
        """Every tracked serial in the type list, of every builder - what a
        scan names its finds by. Lenient: a scan is a diagnosis, and a config
        problem is reported elsewhere."""
        from ... import typelist
        from ...flashers import TrackedBoard

        entries, _families = typelist.read_config(self.paths)
        return [TrackedBoard(e.name, s, e.chipset) for e in entries for s in e.serials]

    def _candidate_report(self, scanner: Any) -> dict[str, Any]:
        """One `CandidateScanner`'s scan, as its wire result."""
        scan = scanner.scan_candidates(
            self.paths, tracked=self._tracked_boards(), reporter=self._log_reporter
        )
        return scan.to_json()

    def dfu_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in DFU mode, and can this agent actually open it?
        Reports rather than raises; the reasons and where each sends the user
        live on `flashers.dfu_util.DfuUtil.scan_candidates`."""
        from ... import flashers

        return self._candidate_report(flashers.by_name("dfu_util"))

    def bootsel_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in BOOTSEL, and can this agent actually write it?
        Reports rather than raises; see `flashers.bootsel.Bootsel.scan_candidates`."""
        from ... import flashers

        return self._candidate_report(flashers.by_name("bootsel"))
```

Leave `add_mcu_start` alone in this task, apart from the renames its scan branches need. Its `self.bootsel_scan({})` and `self.dfu_scan({})` calls still return the same dict shape, so it keeps working untouched until Task 5 replaces it.

If `ToolMissingError` or `DfuPermissionError` are now unused imports in the agent module, delete them (ruff will flag them).

- [ ] **Step 8: Remove the agent's reason constants**

Run: `git -C C:/git/github/mcu-updater-first-install-by-flasher grep -n "DFU_NO_TOOL\|DFU_PERMISSION_DENIED\|DFU_NONE\|DFU_AMBIGUOUS\|BOOTSEL_NONE\|BOOTSEL_NOT_MOUNTED\|BOOTSEL_AMBIGUOUS"`

Delete every hit in `agent/methods/status.py` and `agent/methods/_api.py`. The hits in `agent/methods/flash.py` were removed with the old bodies in Step 7. Expect no other hits. If one exists, stop and report it rather than deleting it.

- [ ] **Step 9: Document the per-device `port`**

In `docs/agent-api.md`, in the `fw.dfu.scan` and `fw.bootsel.scan` result tables or examples:
- Add `port` to each device: "`port` - the USB port the device is on (sysfs's device name, e.g. `1-1.2`), or null when it can't be traced. Additive."
- In each example device object, add `"port": "6-1.6.6.1.3"` for DFU and `"port": "1-1.2"` for BOOTSEL.

- [ ] **Step 10: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_candidate_scan.py tests/test_agent_dfu.py tests/test_agent_bootsel.py tests/test_agent_add_mcu.py -q`
Expected: PASS. The last three files pass **unmodified** — that is the proof the move didn't change the wire.

- [ ] **Step 11: Mutation specs, gate, commit**

A sweep made while writing this plan (every `find` in `scripts/mutations/*.json` whose file this plan rewrites) found **no** spec anchored inside `dfu_scan`, `bootsel_scan` or the `_identify_*` bodies. Re-check that it still holds before editing, because specs may have been added since:

```bash
python - <<'EOF'
import json, glob
for p in sorted(glob.glob("scripts/mutations/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    for m in d.get("mutations", []):
        if m.get("file", d.get("file")) == "src/mcu_updater/agent/methods/flash.py":
            print(p, "|", m["name"], "|", m["find"].splitlines()[0])
EOF
```

Any hit whose line sits in lines 626-839 moves with its body: re-anchor it onto the moved line, setting `"file"` on that mutation to the flasher module. Then run each touched spec singly.

The same sweep lists the anchors this plan must keep **verbatim** in the files it rewrites, beyond those Tasks 4-6 name:
- `bootsel-erase.json`'s `boot_config = ...` line in `add_mcu_start`;
- `batch-selection.json`'s `    if choice is None:` in `flashers/flash.py`;
- `single-write-path.json`'s two lines in the CLI's add-mcu adoption tail;
- `dfu-pairings.json`'s late-adoption lines.

Run the full gate, then:

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add -A src tests docs/agent-api.md scripts/mutations
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "refactor(flashers): move the dfu and bootsel scans into their flashers as CandidateScanner, each device carrying its usb port"
```

---

### Task 3: `first_install` — which flasher sets this type up

**Files:**
- Modify: `src/mcu_updater/flashers/flash.py:901-910` (`install_family` takes a firmwares list)
- Modify: `src/mcu_updater/flashers/registry.py` (`FirstInstall` and `first_install`)
- Modify: `src/mcu_updater/flashers/__init__.py` (re-exports and `__all__`)
- Modify: `src/mcu_updater/agent/methods/flash.py:912`, `src/mcu_updater/cli.py:1152` (call-site argument only)
- Create: `tests/test_first_install.py`

**Interfaces:**
- Consumes (Task 2): `candidate_scanner`, `CandidateScanner`.
- Produces:
  - `install_family(firmwares: Sequence[str], families: dict[str, Any] | None = None) -> str`
  - `flashers.FirstInstall(fw: str, flasher: str | None, state: str, reason: str | None)`, with `.to_json() -> {"fw": str | None, "flasher": str | None, "reason": str | None}`. `fw` is `""` when nothing is declared, and `state` is `""` when there is no flasher.
  - `flashers.first_install(entry, families: dict[str, FirmwareFamily]) -> FirstInstall`, where `entry` is anything with read-only `name`, `chipset` and `firmwares` (a `TypeEntry` or an `McuType`).

- [ ] **Step 1: Write the failing tests in `tests/test_first_install.py`**

```python
"""`flashers.first_install`: a type's install family's `flashers:` list decides
whether a bare board can be set up, and with what. Never the builder, never a
chipset prefix in a caller. Pure and never raising - `fw.status` asks it for
every row on every poll."""

from __future__ import annotations

import dataclasses

import pytest

from mcu_updater import flashers
from mcu_updater.config import McuType
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import registry
from mcu_updater.flashers.spec import CandidateScan


@dataclasses.dataclass(frozen=True)
class Entry:
    name: str
    chipset: str
    firmwares: tuple[str, ...]


def fam(name, flashers_, *, bootloader=False, builder="kconfig_make"):
    return FirmwareFamily(
        name=name, builder=builder, flashers=tuple(flashers_), bootloader=bootloader
    )


BASE = {
    "klipper": fam("klipper", ["flashtool"]),
    "katapult": fam("katapult", ["dfu_util", "bootsel"], bootloader=True),
    "roadrunner": fam("roadrunner", ["bootsel"], builder="cmake"),
    "knomi_serial": fam("knomi_serial", ["esptool"], builder="platformio"),
}


@pytest.mark.parametrize(
    ("chipset", "flasher", "state"),
    [("stm32g0b1xx", "dfu_util", "dfu"), ("rp2040", "bootsel", "bootsel")],
)
def test_a_kconfig_type_with_katapult_installs_it(chipset, flasher, state):
    got = flashers.first_install(Entry("t", chipset, ("klipper", "katapult")), BASE)
    assert (got.fw, got.flasher, got.state, got.reason) == ("katapult", flasher, state, None)


def test_a_cmake_rp2040_installs_its_own_image_over_bootsel():
    got = flashers.first_install(Entry("roadrunner", "rp2040", ("roadrunner",)), BASE)
    assert (got.fw, got.flasher, got.state) == ("roadrunner", "bootsel", "bootsel")


def test_a_cmake_type_with_no_chipset_is_told_to_declare_one():
    got = flashers.first_install(Entry("roadrunner", "", ("roadrunner",)), BASE)
    assert got.flasher is None
    assert "chipset:" in got.reason


def test_a_pio_type_is_told_nothing_can_scan_for_it():
    got = flashers.first_install(Entry("knomi", "esp32", ("knomi_serial",)), BASE)
    assert got.flasher is None
    assert "can scan for a new board" in got.reason
    assert "[firmware knomi_serial]" in got.reason


def test_a_list_without_a_bare_writer_names_the_line_to_add():
    got = flashers.first_install(Entry("ebb", "stm32g0b1xx", ("klipper",)), BASE)
    assert got.flasher is None
    assert "flashers: flashtool, dfu_util" in got.reason


def test_an_mcutype_is_answered_too():
    mcu = McuType(name="ebb", chipset="stm32g0b1xx", firmwares=["klipper", "katapult"])
    assert flashers.first_install(mcu, BASE).flasher == "dfu_util"


def test_first_install_never_raises_on_a_missing_family():
    got = flashers.first_install(Entry("t", "rp2040", ("ghost",)), BASE)
    assert (got.fw, got.flasher) == ("ghost", None)
    assert "ghost" in got.reason


def test_first_install_never_raises_on_an_unknown_flasher_name():
    families = {**BASE, "katapult": fam("katapult", ["nosuch", "bootsel"], bootloader=True)}
    got = flashers.first_install(Entry("t", "rp2040", ("klipper", "katapult")), families)
    assert got.flasher == "bootsel"


def test_first_install_never_raises_on_no_firmwares():
    got = flashers.first_install(Entry("t", "rp2040", ()), BASE)
    assert got.flasher is None and got.fw == ""
    assert got.to_json() == {"fw": None, "flasher": None, "reason": got.reason}


class _Stub:
    """A flasher that would write anything bare."""

    def __init__(self, name, *, scans):
        self.name = name
        self.states = ("dfu",)
        if scans:
            self.candidate_prefix = name
            self.scan_candidates = lambda paths, *, tracked, reporter: CandidateScan(
                False, None, None, []
            )

    def supports(self, device, helper):
        return True


def test_the_list_order_decides_between_two_scanners(monkeypatch):
    monkeypatch.setattr(
        registry, "_BY_NAME", {"b": _Stub("b", scans=True), "a": _Stub("a", scans=True)}
    )
    got = flashers.first_install(Entry("t", "x", ("f",)), {"f": fam("f", ["b", "a"])})
    assert got.flasher == "b"


def test_a_flasher_that_cannot_scan_is_never_chosen(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_BY_NAME",
        {"writer": _Stub("writer", scans=False), "scanner": _Stub("scanner", scans=True)},
    )
    got = flashers.first_install(
        Entry("t", "x", ("f",)), {"f": fam("f", ["writer", "scanner"])}
    )
    assert got.flasher == "scanner"
```

If `FirmwareFamily` has required fields beyond `name`, give `fam()` the defaults its dataclass declares (`src/mcu_updater/firmware.py:106`). As of writing, only `name` is required.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_first_install.py -v`
Expected: `AttributeError: module 'mcu_updater.flashers' has no attribute 'first_install'`.

- [ ] **Step 3: Widen `install_family` in `src/mcu_updater/flashers/flash.py`**

```python
def install_family(firmwares: Sequence[str], families: dict[str, Any] | None = None) -> str:
    """The family a bare board of this type gets first: its bootloader if it
    has one, and otherwise the application it runs.

    One rule for every first install - `flashers.first_install`, the agent's
    `fw.add_mcu.start` and the CLI's `add-mcu` all ask it - so a type with no
    bootloader gets its own firmware rather than a Katapult it never declared.
    Takes the firmwares list, not a type, so a `typelist.TypeEntry` of any
    builder feeds it as well as an `McuType`; the rule itself is still
    `McuType`'s own.
    """
    mcu = McuType(name="", firmwares=list(firmwares))
    boot = mcu.bootloader(families)
    return boot if boot is not None else mcu.application(families)
```

Add `from collections.abc import Sequence` if absent. `McuType` is already imported (it's the old parameter type).

Update the two callers:
- `agent/methods/flash.py:912` → `install = install_family(mcu.firmwares, families)`
- `cli.py:1152` → `install = install_family(mcu.firmwares, families)`

- [ ] **Step 4: Add `FirstInstall` and `first_install` to `src/mcu_updater/flashers/registry.py`**

Imports:
- `import dataclasses`
- `from collections.abc import Iterable, Sequence` (extend the existing line)
- `Protocol` in the `typing` import
- `from ..firmware import missing_section_message`
- `KIND_BARE` in the `.spec` import (`FirmwareFamily` is already imported, or is under `TYPE_CHECKING`; keep whichever it is).

```python
@dataclasses.dataclass(frozen=True)
class FirstInstall:
    """Which flasher sets a bare board of one type up, or why none can."""

    #: The install family - bootloader, else application. "" when the type
    #: declares no firmwares at all.
    fw: str
    #: None exactly when nothing on the family's list can do it.
    flasher: str | None
    #: The ROM state that flasher writes ("dfu", "bootsel"); "" with no flasher.
    state: str
    #: Set exactly when `flasher` is None, naming the line to change.
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {"fw": self.fw or None, "flasher": self.flasher, "reason": self.reason}


class _Declared(Protocol):
    """A `typelist.TypeEntry` or an `McuType` - read-only, so both fit."""

    @property
    def name(self) -> str: ...
    @property
    def chipset(self) -> str: ...
    @property
    def firmwares(self) -> Sequence[str]: ...


def _bare(entry: _Declared, fw: str, state: str) -> Device:
    return Device(
        type=entry.name, id="", chipset=entry.chipset, state=state, fw=fw, kind=KIND_BARE
    )


def first_install(entry: _Declared, families: dict[str, FirmwareFamily]) -> FirstInstall:
    """Which flasher on this type's install family can find *and* write a
    bare board of it - the first, in list order, that is a `CandidateScanner`
    and whose `supports()` takes a bare device in one of its own states.

    No builder and no flasher name is compared: `flashtool` and `esptool`
    refuse `KIND_BARE` and cannot scan, so they are never chosen.

    Pure - no bus, no subprocess, no file read - because `fw.status` asks it
    for every row on every poll. Never raises: a row it cannot answer for
    still renders, with the reason.
    """
    from .flash import _no_first_install_writer, install_family

    if not entry.firmwares:
        return FirstInstall(
            "", None, "", f"[type {entry.name}] declares no firmware, so there is nothing to install."
        )
    fw = install_family(entry.firmwares, families)
    family = families.get(fw)
    if family is None:
        return FirstInstall(fw, None, "", missing_section_message(fw))

    listed = [_BY_NAME[n] for n in family.flashers if n in _BY_NAME]
    scanners = [f for f in listed if candidate_scanner(f) is not None]
    for flasher in scanners:
        for state in flasher.states:
            if flasher.supports(_bare(entry, fw, state), None):
                return FirstInstall(fw, flasher.name, state, None)

    if scanners and not entry.chipset:
        return FirstInstall(
            fw,
            None,
            "",
            f"[type {entry.name}] declares no chipset:, and a bare board has "
            f"nothing else to say which boot ROM it speaks. Add chipset: to it.",
        )
    for flasher in FLASHERS:
        if flasher in listed or candidate_scanner(flasher) is None:
            continue
        for state in flasher.states:
            if flasher.supports(_bare(entry, fw, state), None):
                return FirstInstall(
                    fw, None, "", _no_first_install_writer(family, entry.chipset, state)
                )
    if scanners:
        return FirstInstall(
            fw,
            None,
            "",
            f"none of the flashers on [firmware {fw}] that can find a new board "
            f"({', '.join(f.name for f in scanners)}) writes a bare "
            f"{entry.chipset}. Flash it by hand, then track it once it enumerates.",
        )
    return FirstInstall(
        fw,
        None,
        "",
        f"nothing on [firmware {fw}]'s flashers: ({', '.join(family.flashers)}) can "
        f"scan for a new board, so this type cannot be set up from bare yet. "
        f"Flash it by hand, then track it once it enumerates.",
    )
```

Re-export `FirstInstall` and `first_install` from `flashers/__init__.py`, and add both to `__all__`.

Check `_no_first_install_writer`'s wording for the stm32 case. It must contain `flashers: flashtool, dfu_util` when the family lists `flashtool`, which is what `test_a_list_without_a_bare_writer_names_the_line_to_add` asserts. If its format differs, align the **test's** expected substring with what the function says. Do not change the function; its wording is already on the wire.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_first_install.py tests/test_agent_add_mcu.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: New mutation spec `scripts/mutations/first-install.json`**

```json
{
  "_comment": "First install is gated by flashers: only a CandidateScanner that supports a bare board of the type is chosen, in list order; the new-device wait keeps only boards on the scanned port.",
  "file": "src/mcu_updater/flashers/registry.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_first_install.py",
    "tests/test_candidate_scan.py",
    "tests/test_port_wait.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "only a flasher that can scan is chosen",
      "find": "    scanners = [f for f in listed if candidate_scanner(f) is not None]",
      "replace": "    scanners = listed"
    },
    {
      "name": "the chosen flasher must support a bare board of the type",
      "find": "            if flasher.supports(_bare(entry, fw, state), None):\n                return FirstInstall(fw, flasher.name, state, None)",
      "replace": "            if True:\n                return FirstInstall(fw, flasher.name, state, None)"
    },
    {
      "name": "the family's list order decides",
      "find": "    for flasher in scanners:",
      "replace": "    for flasher in reversed(scanners):"
    },
    {
      "name": "a scanner is an isinstance check, not every flasher",
      "find": "    return flasher if isinstance(flasher, CandidateScanner) else None",
      "replace": "    return flasher"
    },
    {
      "name": "the wait keeps only boards on the scanned port",
      "file": "src/mcu_updater/discovery/byid.py",
      "find": "        if port and port_of(paths, dev, inventory) != port:\n            continue",
      "replace": "        if False:\n            continue"
    }
  ]
}
```

Run: `python scripts/mutation_test.py scripts/mutations/first-install.json`
Expected: every mutation reported caught. Then run `.venv/Scripts/python.exe -m pytest tests/test_repo_hygiene.py -q` and read its output; it must pass.

- [ ] **Step 7: Gate and commit**

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add src/mcu_updater/flashers src/mcu_updater/agent/methods/flash.py src/mcu_updater/cli.py tests/test_first_install.py scripts/mutations/first-install.json
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(flashers): first_install picks the flasher that sets a bare board up from the install family's list"
```

---

### Task 4: `flash_initial_bootloader(state=)`, `adoptable_devices(port=)`, and the CLI's `add-mcu`

**Files:**
- Modify: `src/mcu_updater/flashers/flash.py:968-1012` (`state` parameter), `:1158-1180` (`adoptable_devices`)
- Modify: `src/mcu_updater/agent/methods/flash.py` (interim `state=` at the `flash_initial_bootloader` call)
- Modify: `src/mcu_updater/cli.py:1143-1209` (`add_mcu`), plus a new `_scan_bare_board`
- Modify: `tests/test_flash.py` (every `flash_initial_bootloader(` call gains `state=`), `tests/test_cli.py:~1360-1410`

**Interfaces:**
- Consumes (Task 3): `flashers.first_install`, `FirstInstall`. (Task 2): `flashers.candidate_scanner`, `TrackedBoard`, `CandidateScan.port`.
- Produces:
  - `flash_initial_bootloader(paths, settings, chipset, fw_bin, *, fw, mcu_type, state: str, uf2_bin=None, katapult_config=None, reporter=null_reporter, target_serial=None)` — `state` is required.
  - `adoptable_devices(paths, known_serials, *, port: str | None, timeout=REENUMERATE_TIMEOUT)`
  - `cli._scan_bare_board(c: Context, choice: FirstInstall) -> CandidateScan`

- [ ] **Step 1: Write the failing CLI tests** (append to `tests/test_cli.py`, near the existing add-mcu tests at ~1360)

```python
def test_add_mcu_scans_after_the_build_and_waits_on_that_port(c, monkeypatch):
    from mcu_updater.flashers import CandidateScan

    order: list[str] = []
    seen: dict = {}
    monkeypatch.setattr(
        cli, "_build_interactive", lambda *a, **k: order.append("build") or _built()
    )
    monkeypatch.setattr(
        cli,
        "_scan_bare_board",
        lambda c_, choice: order.append("scan")
        or CandidateScan(True, None, None, [{"serial": "S", "port": "1-1.2"}]),
    )
    monkeypatch.setattr(
        cli, "flash_initial_bootloader", lambda *a, **k: order.append("write") or seen.update(k)
    )
    monkeypatch.setattr(
        cli, "adoptable_devices", lambda paths, before, **k: seen.update(wait=k) or []
    )

    cli.add_mcu(argparse.Namespace(type="board"))

    assert order == ["build", "scan", "write"]
    assert seen["state"] == "dfu"
    assert seen["wait"]["port"] == "1-1.2"


def test_add_mcu_refuses_a_type_no_flasher_can_set_up_before_the_build(c, monkeypatch):
    from mcu_updater.errors import UnsupportedChipsetError

    _klipper_only(c)  # see Step 1b
    monkeypatch.setattr(
        cli, "_build_interactive", lambda *a, **k: pytest.fail("built before refusing")
    )
    with pytest.raises(UnsupportedChipsetError, match="dfu_util"):
        cli.add_mcu(argparse.Namespace(type="board"))


def test_add_mcu_refuses_a_non_kconfig_type_by_name(c, monkeypatch, tmp_path):
    _add_cmake_type(c, tmp_path)  # see Step 1b
    with pytest.raises(UpdaterError, match="web panel"):
        cli.add_mcu(argparse.Namespace(type="roadrunner"))
```

- [ ] **Step 1b: Helpers for those tests**

The `c` fixture's type `board` is `stm32f072xb` with base firmwares. Look at the existing `_adopting` helper and the parametrized first-image test for how they build `_built()` (the `_build_interactive` stand-in with `.bin_path`/`.uf2_path`), and reuse that. If the file has no named helper for it, add:

```python
def _built(bin_path="/tmp/k.bin", uf2_path=None):
    return types.SimpleNamespace(bin_path=bin_path, uf2_path=uf2_path)


def _klipper_only(c):
    """`board` with no Katapult, and klipper's list without a bare writer."""
    from mcu_updater import firmware

    with Registry.mutate(c.paths, "test") as reg:
        reg.get("board").firmwares = ["klipper"]
    # BASE_FIRMWARES' klipper is `flashers: flashtool` - nothing writes bare.
    assert firmware.load(c.paths)["klipper"].flashers == ("flashtool",)


def _add_cmake_type(c, tmp_path):
    with open(c.paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware roadrunner]\nsource: " + str(tmp_path) + "\nbuilder: cmake\n"
            "flashers: bootsel\n\n[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
        )
```

Check how `test_cli.py` already mutates the registry or main config (grep `Registry.mutate\|main_config` in that file) and use the same idiom. Add `import types` if needed.

- [ ] **Step 2: Update the existing CLI tests that break by design**

- Every `adoptable_devices` stand-in, `lambda paths, before, chipset: ...` (around lines 1368 and 1409), becomes `lambda paths, before, **k: ...`.
- Every test that reaches the write through `cli.add_mcu` must also stub `_scan_bare_board`. The real scan would shell out to `dfu-util` or read a BOOTSEL mount. Add this to `_adopting` and to the parametrized first-image test:
  `monkeypatch.setattr(cli, "_scan_bare_board", lambda c_, choice: CandidateScan(True, None, None, [{"port": None}]))`
- In the parametrized `test_add_mcu_builds_and_writes_the_types_first_image`, the bare RP2040 `katapult_installed=False` case (`[False-klipper]`) now refuses before the build. Base klipper is `flashers: flashtool`, which cannot write a bare board. That case must give klipper `flashers: flashtool, bootsel`, the same way the agent tests' `_klipper_flashers` does. **This is behaviour change 6 in the plan's deviations**, not a weakened assertion. Say so in a comment on the change.

- [ ] **Step 3: Run them to verify the new ones fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -k add_mcu -v`
Expected: the three new tests FAIL, with `AttributeError: ... has no attribute '_scan_bare_board'`, or a missing refusal.

- [ ] **Step 4: `flash_initial_bootloader` takes `state`**

In `src/mcu_updater/flashers/flash.py`:
- Add `state: str,` after `mcu_type: str,` in the signature.
- Delete the line `    state = STATE_BOOTSEL if chipset.startswith("rp2040") else STATE_DFU`.
- Add to the docstring: "`state` is the ROM state `flashers.first_install` chose (`STATE_DFU`/`STATE_BOOTSEL`) - the chipset is not asked, so a type whose chipset names no vendor prefix is still set up by what its family lists."

If `STATE_DFU`/`STATE_BOOTSEL` are still used by the guards below, keep their imports.

In `tests/test_flash.py`, every `flash_initial_bootloader(` call gains `state=`:
- `state="bootsel"` when the chipset argument of that call starts with `rp2040`;
- `state="dfu"` otherwise.

That's about 18 calls, and it is mechanical: no assertion changes.

In `src/mcu_updater/agent/methods/flash.py`'s `add_mcu_start`, add this interim argument to the `flash_initial_bootloader(` call. Task 5 replaces it.

```python
                state="bootsel" if is_bootsel else "dfu",
```

- [ ] **Step 5: `adoptable_devices` waits on the port**

```python
def adoptable_devices(
    paths: Paths,
    known_serials: set,
    *,
    port: str | None,
    timeout: float = REENUMERATE_TIMEOUT,
) -> list[BusDevice]:
    """Devices that appeared on `port` and aren't tracked yet.

    Keyed on the USB port the scan saw, not the chipset: a board's by-id name
    carries whatever its firmware says (a Roadrunner's is `Roadrunner`), and
    docs/decisions.md already says that segment is not a filter. Not filtered
    to Katapult either: an image that survives an install chain-loads past it
    and reappears running that firmware instead. `port=None` - the scan could
    not trace one - is any new board; the caller warns.

    Replaces the original's fixed `time.sleep(3)` with a real poll.
    """
    return wait_for_new_device(paths, known_serials, port=port, timeout=timeout)
```

- [ ] **Step 6: The CLI's `add-mcu`**

In `src/mcu_updater/cli.py`, add `from . import flashers, typelist` (merge with existing imports) and `from .flashers import CandidateScan, FirstInstall` for the annotations. Then:

```python
def _scan_bare_board(c: Context, choice: FirstInstall) -> CandidateScan:
    """The chosen flasher's own scan, run after the build and right before the
    write - after, because the user may still be fitting the jumper while
    menuconfig runs. A board that isn't ready is refused here, naming why."""
    scanner = flashers.candidate_scanner(flashers.by_name(choice.flasher or ""))
    if scanner is None:  # first_install only ever names a scanner
        raise FlashError(f"{choice.flasher} cannot scan for a new board.")
    tracked = [
        flashers.TrackedBoard(e.name, s, e.chipset)
        for e in typelist.load(c.paths)
        for s in e.serials
    ]
    scan = scanner.scan_candidates(c.paths, tracked=tracked, reporter=stdout_reporter)
    if not scan.ready:
        raise FlashError(
            scan.message or f"no board is ready for {choice.flasher}.", reason=scan.reason
        )
    return scan
```

In `add_mcu`, replace everything from `c = ctx()` through the `adoptable_devices` call with the code below. The unchanged tail, from `if not candidates:`, stays as it is.

```python
    c = ctx()
    reg = c.registry()
    if args.type not in reg.names():
        entry = next((e for e in typelist.load(c.paths) if e.name == args.type), None)
        if entry is not None:
            # add-mcu builds through menuconfig, so it sets up kconfig types.
            raise UpdaterError(
                f"'{args.type}' builds with {entry.builder or 'no builder'}, and "
                f"add-mcu builds through menuconfig, so it sets up kconfig types "
                f"only. Build it, then add the board from the web panel's "
                f"'Add new board…'.",
                type=args.type,
            )
    mcu = reg.get(args.type)
    chipset = mcu.chipset

    # The board's first image and who writes it - the same question the
    # agent's fw.add_mcu.start asks, answered before menuconfig runs.
    families = firmware.load(c.paths)
    choice = flashers.first_install(mcu, families)
    if choice.flasher is None:
        raise UnsupportedChipsetError(choice.reason or "", chipset=chipset, fw=choice.fw)
    install = choice.fw
    family = firmware.resolve(c.paths, install, families)

    with exclusive(c.paths, f"add-mcu {args.type}"):
        # A brand new type has no saved .config, so this launches menuconfig.
        result = _build_interactive(c, args.type, install)
        scan = _scan_bare_board(c, choice)

        before = set(reg.all_serials()) | {
            d.serial for d in find_untracked(c.paths, reg.all_serials())
        }
        flash_initial_bootloader(
            c.paths,
            c.settings,
            chipset,
            result.bin_path,
            fw=install,
            mcu_type=args.type,
            state=choice.state,
            uf2_bin=result.uf2_path,
            # Where BOOTSEL erases the old application under a bootloader. An
            # application image replaces what boots, so it has none.
            katapult_config=(
                c.paths.config_file(args.type, install) if family.bootloader else None
            ),
            reporter=stdout_reporter,
        )

        if scan.port is None:
            print(
                f"WARNING: {choice.flasher} could not say which USB port the board "
                f"is on, so any new board that appears is offered as this one."
            )
        print(f"Waiting for the device to enumerate as {install}...")
        candidates = adoptable_devices(c.paths, before, port=scan.port)
```

In the "not candidates" message, `for chipset '{chipset}'` becomes `on port {scan.port}` when there is a port, and stays as it is otherwise:

```python
    if not candidates:
        where = f"on port {scan.port}" if scan.port else f"for chipset '{chipset}'"
        print(
            f"No new, unassigned {install} device found {where}. "
            f"Check `ls /dev/serial/by-id/` and use 'add-serial' manually."
        )
        return
```

Import `UnsupportedChipsetError` and `FlashError` from `.errors` if they aren't already imported. The `install_family` import in cli.py becomes unused; remove it.

Before editing, `git grep -n "before = set(reg.all_serials())\|adoptable_devices(c.paths" -- scripts/mutations`. Re-anchor any hit in the same commit.

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_flash.py tests/test_agent_add_mcu.py tests/test_tui.py -q`
Expected: PASS.

- [ ] **Step 8: Gate and commit**

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add src tests scripts/mutations
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(cli): add-mcu asks first_install, scans after the build, and waits for the board on its usb port"
```

---

### Task 5: `fw.add_mcu.start` over the type list, and `fw.add_mcu.scan`

**Files:**
- Modify: `src/mcu_updater/agent/methods/flash.py:846-1113` (`add_mcu_start` rewritten; new `add_mcu_scan`)
- Modify: `src/mcu_updater/agent/methods/status.py` (`METHODS`: `"fw.add_mcu.scan": "add_mcu_scan"`) and `_api.py` (Protocol stub for `add_mcu_scan` if the Protocol lists methods)
- Modify: `tests/test_agent_add_mcu.py`
- Modify: `scripts/mutations/add-mcu.json`, `scripts/mutations/dfu-pairings.json`
- Modify: `docs/agent-api.md` ("Setting up a brand-new board", `fw.add_mcu.start`, new `fw.add_mcu.scan`)

**Interfaces:**
- Consumes:
  - Task 1: `wait_for_new_device(..., port=)`, conftest `on_port`.
  - Task 2: `Api._candidate_report`, `Api._tracked_boards`, `CandidateScanner.candidate_prefix`.
  - Task 3: `flashers.first_install`.
  - Task 4: `flash_initial_bootloader(state=)`.
- Produces:
  - RPC `fw.add_mcu.scan {name}` returns the `CandidateScan.to_json()` keys plus `flasher: str | None`. With no scanner it returns `ready: false`, `reason: "no_scanner"`, `message: <first_install reason>`, `devices: []`, `count: 0`, `flasher: null`.
  - `fw.add_mcu.start` job result gains `flasher` and `port`.

- [ ] **Step 1: Fixture and helper changes in `tests/test_agent_add_mcu.py`** (existing tests that break by design; each change carries its reason)

- `adder` fixture: `api = Api(on_port(paths, fake_root, DFU_PORT), runner=runner)`, with a module constant `DFU_PORT = "6-1.6.6.1.3"  # ONE_BOARD's dfu-util path`. Import `on_port` from `.conftest`.
  - Reason: the wait is now keyed on the scanned port, so every fake board must hang off the port the DFU scan reports.
  - BOOTSEL tests get the same port through `device_for_block`, because `on_port` points `block_sysfs` at that port too.
- `_stage_katapult_uf2` → `return stage_uf2_only(paths, mcu_type, "katapult", content=b"\0" * 512)`.
- `_stage_klipper_uf2` → `return stage_uf2_only(paths, name, "klipper", content=_uf2_image(address))`.
  - Reason for both: the image is now read through `providers.staged`, and `build.staged` offers a `.uf2` only when its sidecar lists the kind. Spec §3 explains why.
  - Import `stage_uf2_only` from `.conftest`.
- `test_bootsel_flash_receives_the_uf2_path`: its spy takes fixed keyword arguments. Add `state=None` to its signature, or turn its tail into `**kw`, and add `assert calls[0]["state"] == "bootsel"`.
- `test_no_dfu_util_on_klipper_names_the_line_to_add` currently expects a job that fails. Rewrite it to expect a synchronous refusal:

```python
    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": ...})  # same name as today
    assert exc.value.data["code"] == "unsupported_chipset"
    assert "flashers: flashtool, dfu_util" in str(exc.value)
    assert adder.runner.current() is None
```

  Reason: first_install answers this before a job exists (deviation 6).
- `test_a_type_with_no_katapult_and_nothing_built_names_klipper`: add `_klipper_flashers(paths, "flashtool, dfu_util")` before dispatching. Reason: with base klipper (`flashtool`) it is now refused as `unsupported_chipset` before `no_artifact` is reached.
- `test_an_unrelated_chipset_is_refused_precisely`: unchanged. It still asserts `unsupported_chipset` and `data.chipset == "esp32"`.
- Any test passing `dfu_serial` for a BOOTSEL type now gets `device_not_found` (deviation 6). If one exists, update it and say so in the commit body.

- [ ] **Step 2: Write the new failing tests** (append to `tests/test_agent_add_mcu.py`)

```python
RR = "roadrunner"


def _roadrunner(adder, paths, fake_root, tmp_path, *, chipset="rp2040"):
    """A cmake RP2040 type, its .uf2 staged, one board in BOOTSEL."""
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            f"\n[firmware {RR}]\nsource: {tmp_path}\nbuilder: cmake\nflashers: bootsel\n"
            f"\n[type {RR}]\nchipset: {chipset}\nfirmware: {RR}\n"
            f"cmake_target: roadrunner_v1_i2c_rgb\n"
        )
    stage_uf2_only(paths, RR, RR, content=_uf2_image(0x10000000))
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root)
    adder.paths = dataclasses.replace(adder.paths, bootsel_root=str(root))


def test_a_cmake_rp2040_is_installed_and_found_on_its_port(
    adder, paths, fake_root, tmp_path, monkeypatch
):
    """The bug this feature exists for: a Roadrunner was unknown_type, and had
    it not been, the chipset-filtered wait would never have seen it come back
    as `usb-Vylyne_Roadrunner_...`."""
    _roadrunner(adder, paths, fake_root, tmp_path)
    calls: list = []

    def appear(*args, **kwargs):
        calls.append(kwargs)
        make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-UNPROVISIONED-1")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", appear)

    res = adder.dispatch("fw.add_mcu.start", {"name": RR})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert calls[0]["state"] == "bootsel"
    assert calls[0]["fw"] == RR
    assert calls[0]["uf2_bin"] == paths.uf2_file(RR, RR)
    assert [c["serial"] for c in job.result["candidates"]] == ["RR-UNPROVISIONED-1"]
    assert job.result["flasher"] == "bootsel"
    assert job.result["port"] == DFU_PORT


def test_a_board_on_another_port_is_not_the_new_board(
    adder, paths, fake_root, tmp_path, monkeypatch
):
    _roadrunner(adder, paths, fake_root, tmp_path)

    def elsewhere(*args, **kwargs):
        # Every tty now resolves to another port; the volume stays on DFU_PORT.
        adder.paths = dataclasses.replace(
            adder.paths, tty_sysfs=str(fake_root / "sys-dev" / "9-9" / "tty")
        )
        make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-OTHER")

    monkeypatch.setattr("mcu_updater.flashers.flash.flash_initial_bootloader", elsewhere)

    res = adder.dispatch("fw.add_mcu.start", {"name": RR})
    assert adder.runner.wait(timeout=30)
    assert adder.runner.get(res["job_id"]).result["candidates"] == []


def test_no_port_falls_back_to_any_new_board_with_a_warning(
    adder, paths, fake_root, tmp_path, monkeypatch
):
    _roadrunner(adder, paths, fake_root, tmp_path)
    # No block sysfs: the BOOTSEL volume cannot be traced to a port.
    adder.paths = dataclasses.replace(adder.paths, block_sysfs=str(fake_root / "nowhere"))
    monkeypatch.setattr(
        "mcu_updater.flashers.flash.flash_initial_bootloader",
        lambda *a, **k: make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-1"),
    )

    res = adder.dispatch("fw.add_mcu.start", {"name": RR})
    assert adder.runner.wait(timeout=30)
    job = adder.runner.get(res["job_id"])

    assert [c["serial"] for c in job.result["candidates"]] == ["RR-1"]
    assert any("which USB port" in line["message"] for line in job.log)


def test_a_cmake_type_with_no_chipset_is_refused_naming_the_key(
    adder, paths, fake_root, tmp_path
):
    _roadrunner(adder, paths, fake_root, tmp_path, chipset="")
    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": RR})
    assert exc.value.data["code"] == "unsupported_chipset"
    assert "chipset:" in str(exc.value)
    assert exc.value.data["data"]["flashers"] == ["bootsel"]


def test_a_katapult_uf2_its_sidecar_does_not_list_is_rebuilt_once(adder, paths):
    """A .uf2 staged before sidecars recorded kinds may be older than the .bin
    beside it - the rule fw.flash already applies."""
    _pico_type(adder, paths)
    os.makedirs(paths.artifact_dir(PICO), exist_ok=True)
    with open(paths.uf2_file(PICO, "katapult"), "wb") as fh:
        fh.write(b"\0" * 512)

    with pytest.raises(RpcError) as exc:
        adder.dispatch("fw.add_mcu.start", {"name": PICO})
    assert exc.value.data["code"] == "no_artifact"
    assert "rebuild" in str(exc.value).lower()


def test_add_mcu_scan_runs_the_scan_first_install_chose(adder, paths, fake_root, tmp_path):
    _roadrunner(adder, paths, fake_root, tmp_path)
    out = adder.dispatch("fw.add_mcu.scan", {"name": RR})
    assert out["flasher"] == "bootsel"
    assert out["ready"] is True
    assert out["devices"][0]["port"] == DFU_PORT


def test_add_mcu_scan_reports_a_type_with_no_scanner(adder):
    out = adder.dispatch("fw.add_mcu.scan", {"name": "knomi"})
    assert out["ready"] is False
    assert out["reason"] == "no_scanner"
    assert out["flasher"] is None
    assert "can scan for a new board" in out["message"]


def test_add_mcu_scan_is_advertised_read_only(read_only):
    assert "fw.add_mcu.scan" in read_only.dispatch("fw.ping")["capabilities"]
```

Check the job-log shape before relying on `job.log`: grep this file and `tests/test_jobs.py` for how a test reads a job's log lines (e.g. `job.log`, `job.to_dict()["log"]`), and use the same accessor. Apply the same check to the `fw.add_mcu.scan` dispatch for `knomi`: `tests/fixtures/registry.cfg` declares `knomi` as a platformio type with `firmware: knomi_serial`, and `live_registry_text` must carry that config. If it doesn't, write a minimal `[firmware knomi_serial] builder: platformio flashers: esptool` plus `[type knomi] chipset: esp32 firmware: knomi_serial` into `main_config`, following `_roadrunner`'s pattern.

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agent_add_mcu.py -q`
Expected:
- The new tests FAIL with `unknown_type` for roadrunner, unknown method `fw.add_mcu.scan`, and missing `flasher`/`port`.
- The existing tests changed in Step 1 fail the same way or already pass.

- [ ] **Step 4: Rewrite `add_mcu_start`**

Before editing, run `git grep -n` for each of these lines under `scripts/mutations/`:
- `        before = {d.serial for d in scan_bus(self.paths)}`
- `        if not family.bootloader:\n            # Nothing below an application boots it`

Both must survive **verbatim**. Replace the method from `name = self._require_str(args, "name")` down to its `return`:

```python
        name = self._require_str(args, "name")

        from ... import flashers, providers, typelist
        from ...artifacts import KIND_BIN, KIND_UF2
        from ...config import McuType
        from ...errors import UnknownTypeError
        from ...flashers.flash import refuse_unbootable_first_image

        # The type list, not the kconfig Registry: a cmake or PIO type is a
        # type like any other, and its flashers - never its builder - say
        # whether a bare board of it can be set up.
        entries = {e.name: e for e in typelist.load(self.paths)}
        entry = entries.get(name)
        if entry is None:
            raise UnknownTypeError(
                f"MCU type '{name}' does not exist.", type=name, known=sorted(entries)
            )
        families = firmware.load(self.paths)
        choice = flashers.first_install(entry, families)
        if choice.flasher is None:
            raise RpcError(
                choice.reason or f"{name} cannot be set up from a bare board.",
                data={
                    "code": "unsupported_chipset",
                    "message": "nothing on this type's install family can set up a bare board",
                    "data": {
                        "type": name,
                        "chipset": entry.chipset,
                        "fw": choice.fw or None,
                        "flashers": (
                            list(families[choice.fw].flashers) if choice.fw in families else []
                        ),
                    },
                },
            )
        scanner = flashers.candidate_scanner(flashers.by_name(choice.flasher))
        if scanner is None:  # first_install only ever names a scanner
            raise RuntimeError(f"{choice.flasher} is not a CandidateScanner")

        # What goes on the board first: the bootloader, or with none the
        # application itself. Every path and message below follows from it.
        install = choice.fw
        family = families[install]
        flasher = flashers.by_name(choice.flasher)
        bare = flashers.Device(
            type=name,
            id="",
            chipset=entry.chipset,
            state=choice.state,
            fw=install,
            kind=flashers.KIND_BARE,
        )
        # A build made earlier, which is exactly what `providers.staged`
        # describes. The CLI's add-mcu hands over what it just built instead;
        # docs/decisions.md records why both are right for their caller.
        staged = providers.staged(self.paths, name, family)
        picked = flashers.resolve(family, bare, None, staged)
        if picked is None:
            kind = flasher.accepts[0]
            kconfig = family.builder == firmware.DEFAULT_BUILDER
            path = (
                (self.paths.uf2_file if kind == KIND_UF2 else self.paths.bin_file)(name, install)
                if kconfig
                else None
            )
            if path is not None and os.path.exists(path):
                advice = (
                    f"The .{kind} on disk predates build records that list what a "
                    f"build made, so it may be older than the rest - rebuild "
                    f"{install} for {name} once."
                )
            elif family.bootloader:
                advice = (
                    "Build it first - this flow installs the bootloader, so the "
                    "bootloader has to exist."
                )
            elif kconfig and kind == KIND_UF2 and any(a.kind == KIND_BIN for a in staged.artifacts):
                # An RP2040 Klipper build makes a .bin only for an offset, so
                # building again as configured makes the same .bin again.
                advice = (
                    f"Its build made a .bin, which an RP2040 build does only for a "
                    f"bootloader offset. Rebuild {name} with no bootloader offset "
                    f"(Bootloader offset: No bootloader)."
                )
            else:
                advice = "Build it first."
            raise RpcError(
                f"no built {install} .{kind} for {name}. {advice}",
                data={
                    "code": "no_artifact",
                    "message": f"{install} has not been built for this type",
                    "data": {"type": name, "fw": install, "path": path},
                },
            )
        _chosen_flasher, artifact = picked
        if not family.bootloader:
            # Nothing below an application boots it, so one built for an offset
            # is refused now - synchronously, like no_artifact, not in a job.
            refuse_unbootable_first_image(self.paths, name, install, artifact)
        # Katapult's saved .config says where BOOTSEL erases the old
        # application. An application image replaces what boots, so has none.
        boot_config = self.paths.config_file(name, install) if family.bootloader else None

        # Which board, decided here rather than in the job, so an ambiguous bus is
        # a synchronous refusal the caller can act on instead of a job that dies.
        scan = self._candidate_report(scanner)
        target = args.get("dfu_serial")
        if target is not None:
            # Only a DFU device carries a `serial` to name it by; naming one on
            # any other flasher's scan finds nothing, and says so.
            target = str(target)
            chosen = next((d for d in scan["devices"] if d.get("serial") == target), None)
            if chosen is None:
                raise RpcError(
                    f"no board with serial {target} is in DFU mode.",
                    data={
                        "code": "device_not_found",
                        "message": "the named DFU device is not attached",
                        "data": {"dfu_serial": target, "devices": scan["devices"]},
                    },
                )
        elif not scan["ready"]:
            raise RpcError(
                scan["message"] or f"no board is ready for {scanner.name}.",
                data={
                    "code": f"{scanner.candidate_prefix}_{scan['reason']}",
                    "message": scan["message"],
                    "data": {"devices": scan["devices"], "reason": scan["reason"]},
                },
            )
        else:
            chosen = scan["devices"][0]
        # Wire names kept from when there were two branches: a DFU device has
        # a `serial`, a BOOTSEL one an `id`, and each is null for the other.
        dfu_serial: str | None = chosen.get("serial") or None
        bootsel_id: str | None = chosen.get("id") or None
        port: str | None = chosen.get("port") or None

        # Every serial actually on the bus right now - NOT "everything untracked".
        #
        # The distinction matters: a board being re-bootloadered is often already
        # tracked, sitting offline because it had no firmware. Baselining
        # on untracked-only meant it came back, was correctly excluded as tracked,
        # and the job reported "no new device appeared" - sending the user to hunt
        # for a failure when the flash had worked perfectly.
        from ...devices import scan as scan_bus

        before = {d.serial for d in scan_bus(self.paths)}

        def run(ctx) -> dict[str, Any]:
            from ...devices import wait_for_new_device
            from ...flashers.flash import flash_initial_bootloader

            ctx.step(
                f"Flashing {install} onto the {choice.state.upper()} board for {name}", 0, 2
            )
            flash_initial_bootloader(
                self.paths,
                self.settings(),
                entry.chipset,
                artifact.path if artifact.kind == KIND_BIN else None,
                fw=install,
                mcu_type=name,
                state=choice.state,
                uf2_bin=artifact.path if artifact.kind == KIND_UF2 else None,
                # Where BOOTSEL erases the old application; DFU mass-erases.
                katapult_config=boot_config,
                reporter=ctx.reporter,
                target_serial=dfu_serial,
            )

            # Recorded here - after the write, BEFORE the wait - because the wait
            # timing out is precisely the case this covers. A board on a marginal
            # port, or unplugged and brought back tomorrow, then still arrives
            # with its intent attached rather than as an anonymous stranger.
            pairing_key = bootsel_id or dfu_serial
            if pairing_key:
                from ...flashers.pairings import Pairings

                Pairings(self.paths).record(pairing_key, name)

            # Keyed on the USB port the scan saw: across a reboot the port is
            # the durable key, not the serial or the by-id chipset segment (a
            # Roadrunner comes back as `usb-Vylyne_Roadrunner_...`). Not
            # filtered to what was written either - an image that survives an
            # install chain-loads past it and reappears running that instead.
            ctx.step("Waiting for the board to re-enumerate", 1, 2)
            if port is None:
                ctx.reporter(
                    "warn",
                    f"{scanner.name} could not say which USB port the board is on, "
                    f"so any new board that appears is reported as this one.",
                )
            appeared = wait_for_new_device(
                self.paths,
                before,
                port=port,
                timeout=self.ADD_MCU_REENUMERATE_TIMEOUT,
            )

            # Split by whether the type list already tracks it. Both mean the
            # flash worked; only one leaves anything for the user to do.
            tracked = {s for e in typelist.read_config(self.paths)[0] for s in e.serials}
            candidates = [d for d in appeared if d.serial not in tracked]
            already = [d for d in appeared if d.serial in tracked]

            ctx.step(f"Found {len(appeared)} board(s)", 2, 2)
            where = f"in {install}" if family.bootloader else f"running {install}"
            then = (
                f" Flash {McuType(name=name, firmwares=list(entry.firmwares)).application(families)}"
                f" onto it when ready."
                if family.bootloader
                else ""
            )
            for device in already:
                ctx.reporter(
                    "info",
                    f"{device.serial} is back {where} and already tracked - "
                    f"nothing to adopt.{then}",
                )
            if not appeared:
                # Not raised: the write may well have succeeded and the board may
                # simply be slow or on a marginal port. Saying what to look at
                # beats failing a job that probably worked.
                ctx.reporter(
                    "warn",
                    f"No board appeared {where}. Check `ls /dev/serial/by-id/` - "
                    "if it is there, adopt it directly with fw.serial.add.",
                )
            return {
                "type": name,
                "chipset": entry.chipset,
                # What was written: the bootloader, or the application on a
                # type that has none. Additive; no API_VERSION change.
                "fw": install,
                "flasher": scanner.name,
                "port": port,
                "dfu_serial": dfu_serial,
                "bootsel_id": bootsel_id,
                "candidates": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in candidates
                ],
                # Appeared, but the type list already tracks it - the
                # re-bootloader case. Distinct from an empty result, which
                # means nothing came back at all.
                "already_tracked": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in already
                ],
            }

        job = runner.submit(
            "add_mcu", {"name": name, "dfu_serial": dfu_serial, "bootsel_id": bootsel_id}, run
        )
        return {
            "job_id": job.id,
            "job": job.to_dict(),
            "type": name,
            "dfu_serial": dfu_serial,
            "bootsel_id": bootsel_id,
        }
```

Then rewrite the method docstring. Replace its "STM32 goes over DFU, RP2040 over BOOTSEL" paragraph with:

> **The install family's `flashers:` list picks the mechanism** (`flashers.first_install`), never the builder or a chipset prefix. That flasher's own `CandidateScanner` finds the board, and the post-write wait is keyed on the USB port the scan saw. A bare board has no identity to adopt yet, so the bus is snapshotted first and diffed afterwards, rather than taking a serial as an argument.

Keep the "Klipper is not stopped" paragraph. Also check that `UnknownTypeError` maps to `unknown_type` over RPC the same way `reg.get`'s did. `test_an_unknown_type_fails_fast` proves it.

- [ ] **Step 5: `fw.add_mcu.scan`**

Add to the agent, after `add_mcu_start`:

```python
    def add_mcu_scan(self, args: dict) -> dict[str, Any]:
        """The scan `fw.add_mcu.start` would run for this type, as a report -
        what the wizard calls instead of choosing between `fw.dfu.scan` and
        `fw.bootsel.scan` itself. Reports rather than raises, like those two;
        a type nothing can set up is `reason: "no_scanner"` with
        `first_install`'s reason as its message."""
        from ... import flashers, typelist
        from ...errors import UnknownTypeError

        name = self._require_str(args, "name")
        entries, families = typelist.read_config(self.paths)
        entry = next((e for e in entries if e.name == name), None)
        if entry is None:
            raise UnknownTypeError(
                f"MCU type '{name}' does not exist.",
                type=name,
                known=sorted(e.name for e in entries),
            )
        choice = flashers.first_install(entry, families)
        scanner = (
            flashers.candidate_scanner(flashers.by_name(choice.flasher))
            if choice.flasher
            else None
        )
        if scanner is None:
            return {
                "devices": [],
                "count": 0,
                "ready": False,
                "reason": "no_scanner",
                "message": choice.reason,
                "flasher": None,
            }
        return {**self._candidate_report(scanner), "flasher": scanner.name}
```

In `agent/methods/status.py`'s `METHODS` dict, next to `"fw.bootsel.scan": "bootsel_scan"`, add `"fw.add_mcu.scan": "add_mcu_scan",`. It is read-only, so it is **not** added to `JOB_METHODS` or `FLASH_METHODS`. If `_api.py`'s Protocol declares the scan methods, add `def add_mcu_scan(self, args: dict) -> dict[str, Any]: ...` beside them.

- [ ] **Step 6: Re-anchor the mutation specs**

`scripts/mutations/add-mcu.json` — replace these entries, keeping the others byte-for-byte:

```json
    {
      "name": "the bus is snapshotted BEFORE the write",
      "find": "        before = {d.serial for d in scan_bus(self.paths)}",
      "replace": "        before = set()"
    },
    {
      "name": "the install family's artifact must have been staged",
      "find": "        if picked is None:",
      "replace": "        if False:"
    },
    {
      "name": "a type no listed flasher can set up is refused",
      "find": "        if choice.flasher is None:\n            raise RpcError(",
      "replace": "        if False:\n            raise RpcError("
    },
    {
      "name": "a not-ready scan is refused when no serial is named",
      "find": "        elif not scan[\"ready\"]:",
      "replace": "        elif False:"
    },
    {
      "name": "a named serial must actually be in DFU",
      "find": "            chosen = next((d for d in scan[\"devices\"] if d.get(\"serial\") == target), None)",
      "replace": "            chosen = next(iter(scan[\"devices\"]), None)"
    },
    {
      "name": "baseline is the whole bus, not untracked-only",
      "find": "        before = {d.serial for d in scan_bus(self.paths)}",
      "replace": "        _k = {s for e in entries.values() for s in e.serials}\n        before = _k | {d.serial for d in scan_bus(self.paths) if d.serial not in _k}"
    },
```

Delete the `"a not-ready BOOTSEL scan is refused"` entry. The merged `elif not scan["ready"]` guard covers BOOTSEL, and this spec's command already runs `tests/test_agent_add_mcu.py` and `tests/test_agent_bootsel.py`. Rename `_comment` to mention "the flasher first_install chose". The three offset-refusal entries stay unchanged.

In `scripts/mutations/dfu-pairings.json`, change the last entry's `find` to:

```json
      "find": "            pairing_key = bootsel_id or dfu_serial\n            if pairing_key:\n                from ...flashers.pairings import Pairings\n\n                Pairings(self.paths).record(pairing_key, name)\n",
```

The `"only untracked boards are adopted"` anchor in late adoption (~line 575) is untouched by this task. Confirm it still matches.

Then run each spec singly, in turn, reading the hygiene test after each:

```bash
python scripts/mutation_test.py scripts/mutations/add-mcu.json
python scripts/mutation_test.py scripts/mutations/dfu-pairings.json
python scripts/mutation_test.py scripts/mutations/single-write-path.json
.venv/Scripts/python.exe -m pytest tests/test_repo_hygiene.py -q
```

Expected: every mutation caught and hygiene green. If a mutation survives, the test that should catch it is missing. Add that test; do not delete the mutation.

- [ ] **Step 7: Docs — `docs/agent-api.md`**

- **"Setting up a brand-new board":**
  - The flow is now `fw.add_mcu.scan {name}` → `fw.add_mcu.start {name[, dfu_serial]}` → adopt (`fw.serial.add`).
  - Every type in the type list is eligible.
  - The mechanism is the install family's first `flashers:` entry that can scan for and write a bare board of the type's chipset.
  - The wait is keyed on the USB port the scan saw, falling back to any new board with a warning when the scan cannot trace a port.
  - `fw.dfu.scan`/`fw.bootsel.scan` remain, for diagnosis.
- **`fw.add_mcu.start`:**
  - `unknown_type` now means "not in the type list".
  - `unsupported_chipset` now carries `first_install`'s reason as its message, plus `data.fw` and `data.flashers`, and is raised for any type whose family lists no bare-board writer. **Previously that case was an accepted job that then failed.**
  - The `<prefix>_<reason>` codes stay as they are (`dfu_none`, `dfu_ambiguous`, `bootsel_none`, `bootsel_not_mounted`, `bootsel_ambiguous`, ...).
  - `dfu_serial` on a non-DFU type is `device_not_found`.
  - `no_artifact` gains the "rebuild once" case for a `.uf2` its build record does not list.
  - The job result gains `flasher` and `port`.
- **New `fw.add_mcu.scan`:**
  - Params `{name}`.
  - The result is the chosen scanner's report (same keys as `fw.dfu.scan`/`fw.bootsel.scan`) plus `flasher`.
  - `reason: "no_scanner"` when nothing can set the type up.
  - Read-only; always advertised.

- [ ] **Step 8: Gate and commit**

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add src tests scripts/mutations docs/agent-api.md
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(agent): add_mcu.start sets up any type its flashers can, and waits for the board on its usb port"
```

---

### Task 6: `targets[].first_install`

**Files:**
- Modify: `src/mcu_updater/agent/methods/status.py:723-747` (`targets()`), plus a new `_first_install_json`
- Modify: `tests/test_agent_targets.py`
- Modify: `docs/agent-api.md` (`targets[]` row fields)

**Interfaces:**
- Consumes (Task 3): `flashers.first_install`, `FirstInstall.to_json`.
- Produces: every `targets[]` row carries `first_install: {"fw": str | None, "flasher": str | None, "reason": str | None}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agent_targets.py`)

```python
def test_every_row_says_how_a_bare_board_of_it_is_set_up(api):
    targets = _targets(api)

    assert targets["bttebb36"]["first_install"] == {
        "fw": "katapult",
        "flasher": "dfu_util",
        "reason": None,
    }
    knomi = targets["knomi"]["first_install"]
    assert knomi["flasher"] is None
    assert "can scan for a new board" in knomi["reason"]
    assert all("first_install" in t for t in targets.values())


def test_a_cmake_rp2040_row_is_set_up_over_bootsel(paths, tmp_path):
    _cmake_config(paths, tmp_path)
    row = {t["name"]: t for t in Api(paths).dispatch("fw.status")["targets"]}["roadrunner"]
    assert row["first_install"] == {"fw": "roadrunner", "flasher": "bootsel", "reason": None}


def test_a_row_first_install_cannot_answer_still_renders(paths, tmp_path):
    """No chipset: first_install has no answer, and fw.status must still
    answer with the row and the reason - it is asked on every poll."""
    _cmake_config(paths, tmp_path)
    text = read_main_config(paths).replace("chipset = rp2040", "chipset =")
    write_main_config(paths, text)

    row = {t["name"]: t for t in Api(paths).dispatch("fw.status")["targets"]}["roadrunner"]
    assert row["first_install"]["flasher"] is None
    assert "chipset:" in row["first_install"]["reason"]
```

`_cmake_config` renders through `CfgDocument`, so check the exact `chipset` line spelling it writes (`chipset: rp2040` or `chipset = rp2040`) with `read_main_config` and match it in the `.replace`. Make sure the replace actually changed something: `assert text != read_main_config(paths)` before writing.

There is also a test asserting that the display's keys equal the MCU keys plus `"extra"`. It keeps passing, because `first_install` is added to every row.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agent_targets.py -k first_install -v` and the two named tests.
Expected: `KeyError: 'first_install'`.

- [ ] **Step 3: Implement**

`git grep -n "self._pio_target(payload, allowed) for payload in displays" -- scripts/mutations` → `targets.json` anchors that line. It stays **verbatim**, so only the `return [` in front of it changes:

```python
        out = [
            self._mcu_target(reg, payload, allowed, configurable, families)
            for payload in types
        ] + [
            self._pio_target(payload, allowed) for payload in displays
        ] + [
            self._cmake_target(payload, allowed, families, rows)
            for payload in self.cmake_status()
        ]
        # One answer for every provider's rows, from the type list: whether a
        # bare board of this type can be set up, and by which flasher.
        from ... import typelist

        entries = {e.name: e for e in typelist.read_config(self.paths)[0]}
        for row in out:
            row["first_install"] = self._first_install_json(entries.get(row["name"]), families)
        return out

    @staticmethod
    def _first_install_json(
        entry: Any, families: dict[str, firmware.FirmwareFamily]
    ) -> dict[str, Any]:
        from ... import flashers

        if entry is None:
            return {
                "fw": None,
                "flasher": None,
                "reason": "not in the type list, so there is nothing to set up from bare.",
            }
        return flashers.first_install(entry, families).to_json()
```

- [ ] **Step 4: New mutation for the row, in `scripts/mutations/targets.json`**

Append:

```json
    {
      "name": "every row carries first_install",
      "find": "            row[\"first_install\"] = self._first_install_json(entries.get(row[\"name\"]), families)",
      "replace": "            pass"
    }
```

Run: `python scripts/mutation_test.py scripts/mutations/targets.json`, then `.venv/Scripts/python.exe -m pytest tests/test_repo_hygiene.py -q`.

- [ ] **Step 5: Docs**

In `docs/agent-api.md`'s `targets[]` row table, add:

> `first_install` — `{fw, flasher, reason}`. `fw` is the install family (the bootloader, else the application). `flasher` is what finds and writes a bare board of the type, or null. `reason` is set exactly when `flasher` is null and names the line to change. Additive; no `API_VERSION` bump.

Include both of the spec's §5 examples.

- [ ] **Step 6: Gate and commit**

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add src tests scripts/mutations/targets.json docs/agent-api.md
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(api): targets rows say which flasher sets a bare board of the type up, or why none can"
```

---

### Task 7: UI — the wizard lists every type, and falls back against an older agent

**Files:**
- Modify: `ui/src/api/targets.ts` (`Target.first_install?`)
- Modify: `ui/src/store/agent.ts` (after `scanBareBoard` at ~826: `scanNewBoard`, `firstInstallAware`)
- Modify: `ui/src/components/AddMcuWizard.vue` (script and template)
- Modify: `ui/src/components/TargetsView.vue:61-64` (`canAddMcu`)
- Modify: `ui/src/components/AddMcuWizard.spec.ts`, `ui/src/components/TargetsView.spec.ts`

**Interfaces:**
- Consumes: RPC `fw.add_mcu.scan` (Task 5) and `targets[].first_install` (Task 6).
- Produces:
  - `scanNewBoard(name: string): Promise<Record<string, unknown> | null>`
  - `firstInstallAware(targets: Target[]): boolean`
  - `Target.first_install?: FirstInstall`

- [ ] **Step 1: Types — `ui/src/api/targets.ts`**

```ts
/** `targets[].first_install` - whether a bare board of this type can be set
 * up, and by which flasher. Absent from an agent older than the field. */
export interface FirstInstall {
  fw: string | null;
  flasher: string | null;
  reason: string | null;
}
```

In `interface Target`, add `first_install?: FirstInstall;`.

- [ ] **Step 2: Write the failing wizard specs** (append inside `describe("AddMcuWizard", ...)` in `AddMcuWizard.spec.ts`)

At the top of the file, add a factory and a reset. In the existing `afterEach`, add `state.ping = null;`.

```ts
function row(
  provider: Target["provider"],
  name: string,
  firstInstall: Target["first_install"],
): Target {
  return { ...mcuTarget(name, "rp2040"), provider, first_install: firstInstall };
}

function newAgent(): void {
  state.ping = {
    capabilities: ["fw.add_mcu.scan", "fw.add_mcu.start"],
  } as never;
}
```

```ts
  it("lists a cmake type and scans it through fw.add_mcu.scan", async () => {
    newAgent();
    state.status = {
      targets: [
        row("cmake", "roadrunner", {
          fw: "roadrunner",
          flasher: "bootsel",
          reason: null,
        }),
      ],
    } as never;
    const scanNew = vi
      .spyOn(store, "scanNewBoard")
      .mockResolvedValue({ ready: true, flasher: "bootsel" });
    const legacy = vi.spyOn(store, "scanBareBoard");
    const start = vi.spyOn(store, "startAddMcu").mockResolvedValue(true);
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("roadrunner");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Install roadrunner")!
      .trigger("click");
    await flushPromises();

    expect(scanNew).toHaveBeenCalledWith("roadrunner");
    expect(legacy).not.toHaveBeenCalled();
    expect(start).toHaveBeenCalledWith("roadrunner", undefined);
  });

  it("shows why a type with no flasher cannot be set up", async () => {
    newAgent();
    const reason =
      "nothing on [firmware knomi_serial]'s flashers: (esptool) can scan for a new board";
    state.status = {
      targets: [
        row("platformio", "knomi", { fw: "knomi_serial", flasher: null, reason }),
      ],
    } as never;
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("knomi");

    expect(wrapper.text()).toContain(reason);
    expect(wrapper.findAll("button").map((b) => b.text())).not.toContain("Scan");
  });

  it("offers the serial pick only for an ambiguous dfu_util scan", async () => {
    newAgent();
    state.status = {
      targets: [
        row("kconfig_make", "ebb", {
          fw: "katapult",
          flasher: "dfu_util",
          reason: null,
        }),
      ],
    } as never;
    vi.spyOn(store, "scanNewBoard").mockResolvedValue({
      ready: false,
      reason: "ambiguous",
      flasher: "dfu_util",
      devices: [
        { serial: "A", path: "1-1" },
        { serial: "B", path: "1-2" },
      ],
    });
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("ebb");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();

    expect(wrapper.findAll("select")).toHaveLength(2);
  });

  it("falls back to the kconfig-only flow against an older agent", async () => {
    // No fw.add_mcu.scan capability, no first_install on the rows: the
    // release order guarantees this pairing for a window.
    state.status = {
      targets: [
        mcuTarget("barepico", "rp2040"),
        { ...mcuTarget("roadrunner", "roadrunner_v1_i2c_rgb"), provider: "cmake" },
      ],
    } as never;
    const legacy = vi
      .spyOn(store, "scanBareBoard")
      .mockResolvedValue({ ready: true });
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    const options = wrapper.findAll("option").map((o) => o.text());
    expect(options.some((o) => o.startsWith("roadrunner"))).toBe(false);

    await wrapper.get("select").setValue("barepico");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();

    expect(legacy).toHaveBeenCalledWith("bootsel");
    expect(wrapper.findAll("button").map((b) => b.text())).toContain(
      "Install firmware",
    );
  });
```

The two existing tests stay unchanged: they run with `state.ping` null, which is the legacy path.

Check `Provider`'s literal spellings in `ui/src/api/targets.ts` (`"cmake"`, `"platformio"` or `"pio"`) and use them exactly.

- [ ] **Step 3: Run them to verify they fail**

Run: `cd ui && npx vitest run src/components/AddMcuWizard.spec.ts`
Expected: the new tests FAIL (`scanNewBoard` is not a function, and roadrunner is not listed).

- [ ] **Step 4: Store — `ui/src/store/agent.ts`**

After `scanBareBoard`:

```ts
/** `fw.add_mcu.scan`: the scan `first_install` chose for this type, plus
 * `flasher` - so the wizard never chooses a mechanism itself. Null (routed
 * into state.error) on failure, like every other store call. */
export async function scanNewBoard(
  name: string,
): Promise<Record<string, unknown> | null> {
  if (client === null) return null;
  try {
    const result = await callAgent<Record<string, unknown>>(
      client,
      "fw.add_mcu.scan",
      { name },
    );
    state.error = null;
    return result;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return null;
  }
}

/** Whether the agent answers the flasher-gated first install: the method
 * and `first_install` on its rows. Both, because the release order pairs a
 * newer UI with an older agent for a window - see AddMcuWizard.vue. */
export function firstInstallAware(targets: Target[]): boolean {
  return (
    hasCapability("fw.add_mcu.scan") &&
    targets.some((target) => target.first_install !== undefined)
  );
}
```

Add `import type { Target } from "../api/targets";` if `agent.ts` doesn't already import it.

- [ ] **Step 5: The wizard — `ui/src/components/AddMcuWizard.vue`**

Replace the `<script setup>` block:

```vue
<script setup lang="ts">
// docs/agent-api.md's "Setting up a brand-new board". Against an agent with
// `fw.add_mcu.scan` and `first_install` on its targets[] rows, every type is
// listed; the row's `first_install` says which flasher sets a bare board of it
// up, or why nothing can, and the scan goes through fw.add_mcu.scan, which
// runs that flasher's own. The wizard never picks a mechanism itself.
//
// Against an older agent - the release order guarantees one, since the UI is
// promoted to stable first - it falls back to exactly the old flow:
// kconfig_make rows, the mechanism read off `descriptor`, fw.dfu.scan /
// fw.bootsel.scan. Remove the fallback once no supported agent lacks
// fw.add_mcu.scan.
//
// Adopting the result (fw.serial.add) and putting the application on a board
// that got a bootloader (fw.flash) are existing, separate flows - JobPanel's
// add_mcu result panel is where the adopt step happens.
import { computed, ref, watch } from "vue";
import {
  firstInstallAware,
  scanBareBoard,
  scanNewBoard,
  startAddMcu,
  state,
} from "../store/agent";
import type { Target } from "../api/targets";
import UiDialog from "./UiDialog.vue";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();
const scanning = ref(false);
const starting = ref(false);
const scan = ref<Record<string, unknown> | null>(null);
const chosenName = ref("");
const chosenDfuSerial = ref("");

const allTargets = computed(
  () => (state.status?.targets as Target[] | undefined) ?? [],
);
const aware = computed(() => firstInstallAware(allTargets.value));
const listed = computed(() =>
  aware.value
    ? allTargets.value
    : allTargets.value.filter((t) => t.provider === "kconfig_make"),
);
const chosen = computed(() =>
  listed.value.find((t) => t.name === chosenName.value),
);

// The flasher that finds and writes a bare board of the chosen type. On the
// legacy path, the two the old flow knew, read off the chipset descriptor.
const flasher = computed<string | null>(() => {
  const target = chosen.value;
  if (!target) return null;
  if (aware.value) return target.first_install?.flasher ?? null;
  if (target.descriptor.startsWith("rp2040")) return "bootsel";
  if (target.descriptor.startsWith("stm32")) return "dfu_util";
  return null;
});

const whyNot = computed<string | null>(() => {
  if (!chosen.value || flasher.value) return null;
  if (aware.value)
    return (
      chosen.value.first_install?.reason ??
      "This type cannot be set up from a bare board."
    );
  return (
    `${chosenName.value}'s chipset has no DFU/BOOTSEL setup path - only ` +
    "STM32 (DFU) and RP2040 (BOOTSEL) boards can be added this way."
  );
});

// What goes on the board, when the agent says; the old agent does not.
const installLabel = computed(() => {
  const fw = aware.value ? chosen.value?.first_install?.fw : null;
  return fw ? `Install ${fw}` : "Install firmware";
});

const ready = computed(() => scan.value?.ready === true);
const reason = computed(() => scan.value?.reason as string | null | undefined);
const message = computed(
  () => scan.value?.message as string | null | undefined,
);
const scanDevices = computed(
  () => (scan.value?.devices as Record<string, unknown>[] | undefined) ?? [],
);
// DFU is the one mechanism that can target one of several boards
// (`dfu_serial`), so only it gets a pick - a property of the mechanism,
// documented in the spec, not a caller branch.
const canPick = computed(
  () => flasher.value === "dfu_util" && reason.value === "ambiguous",
);

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    scan.value = null;
    chosenName.value = "";
    chosenDfuSerial.value = "";
  },
);

function close(): void {
  emit("close");
}

async function runScan(): Promise<void> {
  if (!flasher.value) return;
  scanning.value = true;
  scan.value = aware.value
    ? await scanNewBoard(chosenName.value)
    : await scanBareBoard(flasher.value === "dfu_util" ? "dfu" : "bootsel");
  scanning.value = false;
}

async function start(): Promise<void> {
  starting.value = true;
  const ok = await startAddMcu(
    chosenName.value,
    canPick.value ? chosenDfuSerial.value : undefined,
  );
  starting.value = false;
  if (ok) close();
}
</script>
```

Template changes:
- `v-for="target in mcuTargets"` becomes `v-for="target in listed"`.
- `<p v-if="chosenName && !mechanism" class="muted">…</p>` becomes `<p v-if="whyNot" class="muted">{{ whyNot }}</p>`.
- `<template v-if="mechanism">` becomes `<template v-if="flasher">`.
- The instruction paragraph becomes:

```vue
      <p class="muted">
        Put the board in its boot ROM - fit the boot jumper, or hold BOOT /
        BOOTSEL - and plug it in, then scan.
        <template v-if="chosen?.first_install?.fw">
          This writes {{ chosen.first_install.fw }}.
        </template>
      </p>
```

- `v-if="mechanism === 'dfu' && reason === 'ambiguous'"` becomes `v-if="canPick"`.
- The button's `:disabled` expression becomes `starting || !(ready || (canPick && chosenDfuSerial))`.
- Its label becomes `{{ starting ? "Starting…" : installLabel }}`.

- [ ] **Step 6: `TargetsView.vue`'s menu entry**

```ts
const canAddMcu = computed(
  () =>
    hasCapability("fw.add_mcu.start") &&
    (firstInstallAware(targets.value)
      ? targets.value.some((target) => target.first_install?.flasher)
      : targets.value.some((target) => target.provider === "kconfig_make")),
);
```

Import `firstInstallAware` from `../store/agent` alongside `hasCapability`.

In `TargetsView.spec.ts`, find the existing test that grants `fw.add_mcu.start` (grep `add_mcu`). Copy it into two new tests:
- **(a)** A single `cmake` row carrying `first_install: {fw: "roadrunner", flasher: "bootsel", reason: null}`, with capabilities `["fw.add_mcu.start", "fw.add_mcu.scan"]`. Assert that the add-new-board entry is present.
- **(b)** The same row with `flasher: null` and a reason. Assert that it is absent.

The existing test stays as the legacy case.

- [ ] **Step 7: UI gate**

Run: `cd ui && npm test && npm run typecheck && npm run lint && npm run format:check`
Expected: all pass. If `format:check` fails, run `npx prettier --write` on the touched files and re-run.

Then run `.venv/Scripts/python.exe -m pytest tests/test_ui_contract.py -q`. `fw.add_mcu.scan` is now a UI literal, and Task 5 put it in `METHODS`.

- [ ] **Step 8: Commit**

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add ui/src
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "feat(ui): the add-board wizard lists every type and asks its flashers, falling back against an older agent"
```

---

### Task 8: Decisions, README, and the bench check

**Files:**
- Modify: `docs/decisions.md`
- Modify: `README.md` (`## TODO` item, `## Features` line)
- Modify: `docs/superpowers/plans/2026-09-26-first-install-by-flasher.md` (tick the bench step once Vi reports it)

- [ ] **Step 1: `docs/decisions.md` — new entry "First install is gated by flashers"**

Follow the file's existing entry format. It records four decisions:

- **Who decides.** Whether a type can be set up from a bare board is its install family's `flashers:` list, via `flashers.first_install`: the first `CandidateScanner` whose `supports()` takes a bare device of the type's chipset. The builder is not the question, and neither is a chipset prefix in a caller. A new mechanism is one capability on its flasher module (PlatformIO: `esptool`), with no change to the agent, the wire or the wizard.
- **Identification lives in the scanner.** It is handed the type list's tracked boards, because deriving a ROM id from a running serial is flasher knowledge.
- **The wait is keyed on the USB port**, never on the by-id chipset segment. It falls back to any new board, with a warning, only when the scan cannot trace a port.
- **Staged vs just-built.** `fw.add_mcu.start` writes a build made earlier, so it reads `providers.staged`. The CLI's `add-mcu` builds first and writes the files it just made, which is why `flash_initial_bootloader` rejects `providers.staged`. Both are right for their caller. Neither is to be "fixed" to match the other.
- **The wizard's DFU pick-a-serial** is the one sanctioned flasher-specific branch in a caller. It exists because only DFU can target one of several boards.

- [ ] **Step 2: `README.md`**

- Under `## TODO`, tick or remove the item "**FEATURE** Support Flashing new devices for other supported flashers…", following how the file marks done items.
- Under `## Features`, find the guided-setup line (grep `-i "new board\|guided\|add-mcu"`). Say that it sets up any type whose firmware's flashers can find and write a bare board: STM32 over DFU and RP2040 over BOOTSEL, for kconfig and cmake builds alike.

- [ ] **Step 3: Full gate, mutation hygiene, commit**

Run the full gate and the UI gate. Then run `.venv/Scripts/python.exe -m pytest tests/test_repo_hygiene.py -q` one last time.

```bash
git -C C:/git/github/mcu-updater-first-install-by-flasher add docs/decisions.md README.md
git -C C:/git/github/mcu-updater-first-install-by-flasher commit -m "docs: first install is gated by flashers, keyed on the usb port"
```

- [ ] **Step 4: Bench verification — Vi, bench board only, never the toolhead**

No agent runs this; it writes firmware. Hand these steps to Vi:

1. On the bench host, check out `feat/first-install-by-flasher`. Then run `sudo systemctl restart mcu-updater.service`, and confirm the restart took with `systemctl show -p ActiveEnterTimestamp mcu-updater.service`. A checkout does nothing for the UI until the service restarts.
2. Load the UI build from this branch.
3. Hold BOOTSEL on the **bench** Roadrunner and plug it in. The `roadrunner` type must declare `chipset: rp2040`.
4. In "Add new board…", `roadrunner` is listed. Scan → Ready → "Install roadrunner".
5. The job succeeds, and `candidates` lists the board as `RR-UNPROVISIONED-…`. Adopting it provisions it.
6. **The job log must not contain "could not say which USB port".** If it does, the BOOTSEL block-to-port trace failed on real sysfs and the pass is hollow, because the fallback found the board by luck. Report that, together with `readlink -f /sys/class/block/$(basename $(readlink -f /dev/disk/by-id/usb-RPI_RP2_*-part1))`.
7. Record the result under this step, then tick it.
