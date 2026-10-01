# Uniform Targets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every builder's `targets[]` row says the same kinds of things in one shape (`source`, `extras`, `devices_note`), PlatformIO device listing sits behind a helper capability, identity provisioning is routed generically, and no display/screen vocabulary or firmware-named machine identifier reaches the core, a generic seam or the wire - shipped as `API_VERSION` 5 with the UI.

**Architecture:** A new `DeviceLister` helper capability (knomi_serial implements it) turns Klipper printer objects into `ListedDevice`s; `status.py` keeps only the generic query loop. Seams contribute `Extra` entries (`mcu_updater/extras.py`) that `targets()` passes through as a list. `Provisioner` gains `identity_state`/`clear` so `fw.identity.*` can ask every registered helper which one owns a serial. Wire removals, the job-kind fix and the version bump land together with contract tests that pin agent and UI to each other.

**Tech Stack:** Python 3.11+ stdlib only (agent), pytest, ruff, mypy; Vue 3 + TypeScript + vitest (UI in `ui/`).

**Spec:** `docs/superpowers/specs/2026-09-29-uniform-targets-design.md` - read it first; this plan argues from it. Its "Amendments" section records the rulings this plan made where the spec was wrong or silent; they bind like the rest of the spec.

## Global Constraints

- Work in `C:\git\github\mcu-updater\.worktrees\uniform-targets` on branch `refactor/uniform-targets`. Every command below runs from that directory.
- `PY=../../.venv/Scripts/python.exe` is the Python 3.11 floor interpreter. Shell state does not survive between tool calls: put `PY=...` in the same command as its use, or spell the path out.
- **Gate, before every commit** (AGENTS.md):
  ```bash
  PY=../../.venv/Scripts/python.exe
  $PY -m pytest -q && $PY -m ruff check src tests scripts && $PY -m mypy src && $PY scripts/check_line_endings.py
  ```
  Tasks that touch `ui/` also run the UI gate:
  ```bash
  cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm run typecheck && npm test && npm run build && npm run lint
  ```
  The worktree has no `ui/node_modules`: the first task that touches `ui/` runs `npm ci` in `ui/` once.
- stdlib only; never add a dependency. Keep `from __future__ import annotations` in every module. LF line endings.
- Commit voice: conventional-commit prefix, lowercase sentence, no trailing period. Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit after a green gate without asking.
- **Before rewriting any line, grep `scripts/mutations/` for it.** Every mutation spec anchors on a verbatim source line; a broken anchor fails `tests/test_repo_hygiene.py::test_no_mutation_is_left_live_in_the_source` inside the gate. The anchors this plan knows it breaks are listed in each task's re-anchor step; grep anyway. A spec's `command` naming a renamed test file also fails that test.
- Run `scripts/mutation_test.py` one spec at a time: `$PY scripts/mutation_test.py scripts/mutations/<name>.json`. Never in parallel, never under a shell timeout shorter than it needs (use `timeout: 600000`). After any interrupted run, run the hygiene test and read its output.
- Never a real flash in this plan. Bench-board checks happen after the PR, with Vi.
- Wire rule (spec, "The rule this applies"): machine-readable names on the wire - methods, error codes, keys - are generic. Human text a firmware-specific seam produces may name its firmware or say "screen".
- Do not read `docs/backlog.md`.

## Review Focus

Inputs the spec implies that tests could easily miss. Each line has a test in the task named.

1. **Klipper unreachable while a PlatformIO type is configured.** The row lists no devices and carries the lister's unreachable `devices_note`; `fw.status` does not raise. Pinned in Task 3 (`test_an_unreachable_klipper_lists_nothing_and_says_so`).
2. **A `device_id:` section that discovery has not found yet.** It is listed with `present: false`, `id` equal to the configured id and `resolved_path: null`, and flashing it is blocked rather than aimed at a stale path. Pinned in Task 2 (`test_a_device_id_section_is_listed_before_discovery_finds_it`) and Task 3 (`test_an_undiscovered_device_id_section_is_a_blocked_row_not_a_missing_one`).
3. **Two PlatformIO types sharing one Klipper prefix.** Both list every section under that prefix, and the prefix is queried once (spec amendment A2). Pinned in Task 3 (`test_two_types_sharing_a_prefix_both_list_it_and_it_is_queried_once`).
4. **A build record left at `data_dir/displays/`.** The type reports `no_provenance`, not an error. Pinned in Task 7 (`test_a_record_left_in_the_old_folder_is_no_provenance_not_an_error`).
5. **`fw.serial.add` when provisioning is withheld** (read-only agent, or flashing off). It refuses with `serial_unprovisioned`, uses the helper's reason as its message, and writes nothing. Pinned in Task 6 (`test_serial_add_refuses_an_unprovisioned_serial_with_the_generic_code`).

---

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `src/mcu_updater/extras.py` (new) | `Extra`, `SEAMS`, `ordered()` - the seam-contributed row facts | 1 |
| `src/mcu_updater/helpers/spec.py` | `ListedDevice`, `DeviceLister`; `Provisioner.identity_state`/`clear` | 1, 6 |
| `src/mcu_updater/helpers/__init__.py` | `device_lister()`, `all_helpers()` accessors | 1, 6 |
| `src/mcu_updater/helpers/registry.py` | `all_helpers()` | 6 |
| `src/mcu_updater/helpers/knomi_serial.py` | knomi's `DeviceLister`: parsing, extras, notes | 2 |
| `src/mcu_updater/helpers/roadrunner.py` | `identity_state`, `clear` | 6 |
| `src/mcu_updater/agent/methods/status.py` | generic listing loop, `platformio_status`, rows, `fw.identity.*` | 3, 4, 5, 6, 9 |
| `src/mcu_updater/agent/methods/flash.py` | PlatformIO flash route on `ListedDevice` | 3, 5, 9 |
| `src/mcu_updater/agent/methods/bulk.py` | PlatformIO fleet selection; `flash_all` response | 3, 5, 9 |
| `src/mcu_updater/agent/methods/build.py` | PlatformIO build job kind | 5, 9 |
| `src/mcu_updater/agent/methods/_api.py` | mixin stubs | 3 |
| `src/mcu_updater/discovery/roadrunner.py`, `errors.py` | generic error codes | 6 |
| `src/mcu_updater/paths.py` | `platformio_sidecar` folder | 7 |
| `src/mcu_updater/__init__.py` | `API_VERSION = 5` and its history entry | 5, 6 |
| `ui/src/api/targets.ts`, `TargetRow.vue`, `BulkDialog.vue` | new row shape | 8 |
| `ui/src/api/jobs.ts`, `ui/src/api/agent.ts` | `profile_apply` kind, version 5 | 5 |
| `ui/src/store/agent.ts`, `BusPanel.vue` | `fw.identity.*` | 6 |
| `tests/test_vocabulary.py` (new) | the display/screen guard | 9 |
| `tests/test_ui_contract.py` | job-kind contract | 5 |
| `src/mcu_updater/tracking.py`, `agent/methods/registry.py` | comments naming the renamed code | 6 |
| `tests/test_identity_provisioning.py` (new), `scripts/mutations/identity-routing.json` (new) | identity routing and its guard | 6 |
| `ui/src/components/BusPanel.vue`, `ui/src/components/SummaryChips.vue`, `ui/src/api/bulk.ts` | vocabulary | 6, 9 |
| `docs/agent-api.md`, `README.md`, `docs/decisions.md`, `docs/layout.md` | docs | 10 |

---

### Task 1: `Extra` and the `DeviceLister` capability

**Files:**
- Create: `src/mcu_updater/extras.py`
- Modify: `src/mcu_updater/helpers/spec.py` (add after the `Identifier` Protocol)
- Modify: `src/mcu_updater/helpers/__init__.py`
- Test: `tests/test_extras.py` (new), `tests/test_helpers.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `mcu_updater.extras.Extra(seam: Seam, name: str, key: str, label: str, value: Scalar)`, frozen, with `.to_json() -> dict[str, Any]`
  - `mcu_updater.extras.SEAMS: tuple[str, ...] = ("builder", "flasher", "helper")`
  - `mcu_updater.extras.ordered(extras: Iterable[Extra]) -> list[dict[str, Any]]`, ordered by seam, then as each seam returned them
  - `mcu_updater.helpers.ListedDevice`, frozen: `id, section, label, configured_id, reported_id, configured_path, resolved_path, version, compatible, answering, raw`
    - `.present -> bool` (property)
    - `.to_json() -> dict[str, Any]`, with no `raw`
  - `mcu_updater.helpers.DeviceLister` Protocol, with members `name`, `klipper_prefix`, `device_from_klipper(section, values) -> ListedDevice`, `extras(devices) -> list[Extra]` and `devices_note(*, reachable: bool) -> str`
  - `mcu_updater.helpers.device_lister(helper) -> DeviceLister | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extras.py`:

```python
"""`targets[].extras`: facts a seam contributes to a row, as one flat list.

The UI renders `label value` and never branches on `key`, `seam` or `name`, so
a new builder, flasher or helper adds entries rather than a new wire type.
"""

from __future__ import annotations

import pytest

from mcu_updater.extras import SEAMS, Extra, ordered


def test_an_extra_serialises_to_the_documented_keys():
    extra = Extra("helper", "knomi_serial", "module_version", "Module", "0.5.0")
    assert extra.to_json() == {
        "seam": "helper",
        "name": "knomi_serial",
        "key": "module_version",
        "label": "Module",
        "value": "0.5.0",
    }


def test_an_unknown_seam_is_refused():
    with pytest.raises(ValueError, match="seam"):
        Extra("provider", "x", "k", "K", 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [{"a": 1}, [1], (1,), object()])
def test_a_value_that_is_not_a_json_scalar_is_refused(value):
    with pytest.raises(TypeError, match="scalar"):
        Extra("builder", "cmake", "k", "K", value)


@pytest.mark.parametrize("value", ["s", 1, 1.5, True, None])
def test_every_json_scalar_is_accepted(value):
    assert Extra("builder", "cmake", "k", "K", value).value == value


def test_extras_are_ordered_by_seam_then_as_each_seam_returned_them():
    helper_b = Extra("helper", "h", "b", "B", 1)
    builder = Extra("builder", "cmake", "a", "A", 1)
    helper_a = Extra("helper", "h", "a", "A", 1)
    flasher = Extra("flasher", "bootsel", "a", "A", 1)

    keys = [(e["seam"], e["key"]) for e in ordered([helper_b, builder, helper_a, flasher])]

    assert keys == [("builder", "a"), ("flasher", "a"), ("helper", "b"), ("helper", "a")]


def test_the_seams_are_the_three_the_spec_names():
    assert SEAMS == ("builder", "flasher", "helper")
```

Append to `tests/test_helpers.py`:

```python
# --------------------------------------------------------------------------
# DeviceLister: a firmware lists its own devices from Klipper's objects
# --------------------------------------------------------------------------


def _listed(**overrides):
    from mcu_updater.helpers import ListedDevice

    fields = {
        "id": "/dev/ttyUSB0",
        "section": "fake_dev t0",
        "label": "t0",
        "configured_id": None,
        "reported_id": "19aa44",
        "configured_path": "/dev/ttyUSB0",
        "resolved_path": "/dev/ttyUSB0",
        "version": "1.0.0",
        "compatible": True,
        "answering": True,
        "raw": {"secret_firmware_field": 7},
    }
    fields.update(overrides)
    return ListedDevice(**fields)


def test_a_listed_device_is_present_exactly_when_its_path_resolved():
    assert _listed().present is True
    assert _listed(resolved_path=None).present is False


def test_a_listed_device_keeps_its_raw_values_off_the_wire():
    """`raw` is for the helper's own `extras()`; a firmware's field reaching the
    wire through it would be the leak `DeviceLister` exists to stop."""
    wire = _listed().to_json()

    assert "raw" not in wire
    assert "secret_firmware_field" not in wire
    assert wire == {
        "id": "/dev/ttyUSB0",
        "section": "fake_dev t0",
        "label": "t0",
        "configured_id": None,
        "reported_id": "19aa44",
        "configured_path": "/dev/ttyUSB0",
        "resolved_path": "/dev/ttyUSB0",
        "present": True,
        "version": "1.0.0",
        "compatible": True,
        "answering": True,
    }


def test_raw_does_not_make_two_listings_of_one_device_unequal():
    assert _listed(raw={"a": 1}) == _listed(raw={"b": 2})


class _FullLister:
    name = "fake"
    klipper_prefix = "fake_dev"

    def device_from_klipper(self, section, values):
        return _listed(section=section)

    def extras(self, devices):
        return []

    def devices_note(self, *, reachable):
        return "none"


def test_a_helper_with_every_member_is_a_device_lister():
    from mcu_updater.helpers import device_lister

    fake = _FullLister()
    assert device_lister(fake) is fake


def test_an_image_reporter_is_not_mistaken_for_a_device_lister():
    """Roadrunner has a `klipper_prefix` and an `ImageReporter.from_klipper`.
    That is why the lister's method is `device_from_klipper`: one name for two
    signatures would make a helper unable to implement both."""
    from mcu_updater.helpers import device_lister, for_name

    assert device_lister(for_name("roadrunner", family="roadrunner")) is None
    assert device_lister(None) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_extras.py tests/test_helpers.py -q`
Expected: FAIL. `test_extras.py` errors with `ModuleNotFoundError: No module named 'mcu_updater.extras'`, and the new `test_helpers.py` tests fail with `ImportError: cannot import name 'ListedDevice'`.

- [ ] **Step 3: Write `src/mcu_updater/extras.py`**

```python
"""What a seam contributes to a `targets[]` row beyond the shared keys.

A builder, flasher or helper that knows a fact worth showing on a row returns
an `Extra`, and `targets()` collects them into one list. The list is the point:
a new seam adds entries rather than a new wire type, and a reader renders
`label value` without branching on who said it. The per-builder `extra` object
this replaced grew one TypeScript type per builder, which is exactly the
branching a uniform row exists to remove.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any, Literal

#: Which kind of seam contributed an entry. A row orders its extras by this.
Seam = Literal["builder", "flasher", "helper"]
SEAMS: tuple[str, ...] = ("builder", "flasher", "helper")

#: A JSON scalar. Anything richer would be a new wire shape, which is what this
#: list exists to avoid.
Scalar = str | int | float | bool | None


@dataclasses.dataclass(frozen=True)
class Extra:
    """One fact for a row: who said it, a stable key, and words for a person."""

    seam: Seam
    #: The seam's registered name: `cmake`, `bootsel`, `knomi_serial`.
    name: str
    #: Stable and machine-readable. The UI never branches on it.
    key: str
    label: str
    value: Scalar

    def __post_init__(self) -> None:
        if self.seam not in SEAMS:
            raise ValueError(f"unknown seam {self.seam!r}; expected one of {SEAMS}")
        if self.value is not None and not isinstance(self.value, (str, int, float, bool)):
            raise TypeError(
                f"an extra's value must be a JSON scalar, not {type(self.value).__name__}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "seam": self.seam,
            "name": self.name,
            "key": self.key,
            "label": self.label,
            "value": self.value,
        }


def ordered(extras: Iterable[Extra]) -> list[dict[str, Any]]:
    """Builders, then flashers, then helpers, each as its seam returned them.

    `sorted` is stable, so sorting on the seam alone keeps each seam's order.
    """
    return [extra.to_json() for extra in sorted(extras, key=lambda e: SEAMS.index(e.seam))]


__all__ = ["SEAMS", "Extra", "Scalar", "Seam", "ordered"]
```

- [ ] **Step 4: Add `ListedDevice` and `DeviceLister` to `src/mcu_updater/helpers/spec.py`**

Change the `collections.abc` import to `from collections.abc import Callable, Mapping, Sequence`. Under the existing `if TYPE_CHECKING:` block, add `from ..extras import Extra`. Then add after the `Identifier` Protocol:

```python
@dataclasses.dataclass(frozen=True)
class ListedDevice:
    """One configured device, as the core uses it and nothing else.

    A firmware's own fields stay in `raw`, which only its helper reads (for its
    `extras()`), and which `to_json` never emits. The core may grow a field
    here when it already uses the fact. A firmware's field may not be added.
    """

    #: What the device is addressed by: its configured id, else its configured path.
    id: str | None
    #: Its printer object, in the capitalisation printer.cfg used.
    section: str
    #: Its short name: the section without the prefix.
    label: str
    #: The id printer.cfg names, or None.
    configured_id: str | None
    #: The hardware id the device reports: the flash log's `hwid:` key and the
    #: source of `confidence`.
    reported_id: str | None
    #: The port printer.cfg or discovery gave, or None.
    configured_path: str | None
    #: `configured_path` resolved, or None when it does not exist.
    resolved_path: str | None
    #: The firmware version the device reports, or None.
    version: str | None
    #: False when the device declares it cannot work with the host; drives the
    #: `protocol_mismatch` state. None is unknown.
    compatible: bool | None
    #: Whether the far end answers, as opposed to the port merely existing.
    #: None is unknown.
    answering: bool | None
    raw: Mapping[str, Any] = dataclasses.field(default_factory=dict, compare=False, repr=False)

    @property
    def present(self) -> bool:
        return self.resolved_path is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "label": self.label,
            "configured_id": self.configured_id,
            "reported_id": self.reported_id,
            "configured_path": self.configured_path,
            "resolved_path": self.resolved_path,
            "present": self.present,
            "version": self.version,
            "compatible": self.compatible,
            "answering": self.answering,
        }


@runtime_checkable
class DeviceLister(Protocol):
    """Lists a family's configured devices from Klipper's printer objects.

    Modelled on `ImageReporter`: the helper declares a prefix and interprets
    the object values, and the core owns the Moonraker query. The method is
    `device_from_klipper`, not `from_klipper`, because `ImageReporter` already
    owns that name with another signature, and one helper may be both.
    """

    name: str
    #: The Klipper section prefix whose objects are this firmware's devices.
    klipper_prefix: str

    def device_from_klipper(self, section: str, values: Mapping[str, Any]) -> ListedDevice:
        """One device from its printer object. `section` is the object's own
        capitalisation, which is what printer.cfg says."""
        ...

    def extras(self, devices: Sequence[ListedDevice]) -> list[Extra]:
        """Facts about one type's devices worth showing on its row."""
        ...

    def devices_note(self, *, reachable: bool) -> str:
        """Why a type of this family lists no devices. Called only when it lists none."""
        ...
```

- [ ] **Step 5: Export them from `src/mcu_updater/helpers/__init__.py`**

Add `DeviceLister` and `ListedDevice` to the `from .spec import (...)` list and to `__all__`, with `device_lister` in `__all__`. Add the accessor after `device_info_reader`:

```python
def device_lister(helper: Helper | None) -> DeviceLister | None:
    """The helper's device-listing capability, or None when it has none."""
    return helper if isinstance(helper, DeviceLister) else None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_extras.py tests/test_helpers.py -q`
Expected: PASS, with no failures.

- [ ] **Step 7: Gate and commit**

Run the full gate (Global Constraints). Expected: all green.

```bash
git add src/mcu_updater/extras.py src/mcu_updater/helpers/spec.py src/mcu_updater/helpers/__init__.py tests/test_extras.py tests/test_helpers.py
git commit -m "feat(helpers): add the DeviceLister capability and seam-contributed extras

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: knomi_serial lists its own devices

**Files:**
- Modify: `src/mcu_updater/helpers/knomi_serial.py`
- Test: `tests/test_knomi_serial_helper.py` (new)
- Create: `scripts/mutations/device-listing.json` (new spec; Task 3 adds its core mutations)

**Interfaces:**
- Consumes: `ListedDevice`, `DeviceLister` (Task 1); `Extra` (Task 1).
- Produces: `KnomiSerialHelper` is a `DeviceLister`:
  - `device_from_klipper(section: str, values: Mapping[str, Any]) -> ListedDevice`
  - `extras(devices: Sequence[ListedDevice]) -> list[Extra]`, which returns `[Extra("helper", "knomi_serial", "module_version", "Module", <str>)]` or `[]`
  - `devices_note(*, reachable: bool) -> str`, returning either `"Could not reach Klipper to check for screens."` or `"No screens found under [knomi_serial ...]."`

This task only adds methods. `status.py` still has its own `device_list` until Task 3 deletes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knomi_serial_helper.py`:

```python
"""knomi_serial's own reading of its klippy module's printer objects.

This is firmware-specific code, so the knomi vocabulary lives here: a
`[knomi_serial T0_knomi]` section names its port one of two ways, and
`serial:` writes it in printer.cfg directly. `device_id:` names the screen by
the id burned into its chip and leaves discovery to find the path, which the
module then reports back as `port`. The core never sees these fields. It gets
a `ListedDevice`.
"""

from __future__ import annotations

import os

from mcu_updater.helpers import DeviceLister, ListedDevice, device_lister
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper

KNOMI = KnomiSerialHelper()


def test_knomi_serial_is_a_device_lister_on_its_own_prefix():
    assert device_lister(KNOMI) is KNOMI
    assert isinstance(KNOMI, DeviceLister)
    assert KNOMI.klipper_prefix == "knomi_serial"


def test_the_label_keeps_the_sections_own_capitalisation():
    device = KNOMI.device_from_klipper("knomi_serial T0_knomi", {})
    assert device.section == "knomi_serial T0_knomi"
    assert device.label == "T0_knomi"


def test_a_serial_section_is_addressed_by_its_path(fake_root):
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")

    device = KNOMI.device_from_klipper("knomi_serial t0", {"port": str(port)})

    assert device.configured_id is None
    assert device.configured_path == str(port)
    assert device.id == str(port)
    assert device.present is True


def test_a_device_id_section_is_listed_before_discovery_finds_it():
    """The screen that needs flashing is exactly the one this must not be blind
    to, so a `device_id:` section with no port yet is listed rather than dropped."""
    device = KNOMI.device_from_klipper("knomi_serial t0", {"device_id": "19AA44"})

    assert device.id == "19AA44"
    assert device.configured_id == "19AA44"
    assert device.configured_path is None
    assert device.resolved_path is None
    assert device.present is False


def test_a_device_id_section_is_addressed_by_its_id_once_discovery_finds_it(fake_root):
    """The discovered path changes when the screen moves socket, so it is never
    the identity. The id is."""
    port = fake_root / "ttyUSB3"
    port.write_text("", encoding="utf-8")

    device = KNOMI.device_from_klipper(
        "knomi_serial t0", {"device_id": "19aa44", "port": str(port)}
    )

    assert device.id == "19aa44"
    assert device.configured_path == str(port)
    assert device.present is True


def test_a_missing_symlink_is_listed_as_not_present(fake_root):
    """The case the klippy module swallows: Klipper starts happily with a blank
    screen and no error anywhere."""
    device = KNOMI.device_from_klipper(
        "knomi_serial t0", {"port": str(fake_root / "not-there")}
    )
    assert device.present is False
    assert device.resolved_path is None


def test_a_symlink_is_resolved_to_the_real_device(fake_root):
    real = fake_root / "ttyUSB0"
    real.write_text("", encoding="utf-8")
    link = fake_root / "knomi_t0"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks unavailable")

    device = KNOMI.device_from_klipper("knomi_serial t0", {"port": str(link)})

    assert device.resolved_path == os.path.realpath(real)
    assert device.configured_path == str(link)


def test_the_reported_id_is_lowered_because_the_docs_say_not_to_trust_its_case():
    device = KNOMI.device_from_klipper("knomi_serial t0", {"reported_id": "19AA44"})
    assert device.reported_id == "19aa44"


def test_a_screen_that_never_answered_reports_no_identity():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"reported_id": ""}).reported_id is None


def test_a_protocol_mismatch_is_an_incompatible_device():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": False}).compatible is False
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": True}).compatible is True


def test_an_unknown_protocol_is_not_an_incompatible_device():
    """None until the device reports in. Reading it as a mismatch would send
    people to reflash a healthy screen."""
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": None}).compatible is None
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": "yes"}).compatible is None


def test_device_online_is_whether_the_screen_answers():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": True}).answering is True
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": False}).answering is False
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": None}).answering is None


def test_a_module_too_old_to_report_leaves_every_live_field_unknown():
    device = KNOMI.device_from_klipper("knomi_serial t0", {})
    assert (device.version, device.reported_id, device.compatible, device.answering) == (
        None,
        None,
        None,
        None,
    )


def test_the_running_version_is_the_firmware_version():
    device = KNOMI.device_from_klipper("knomi_serial t0", {"firmware_version": "0.5.0+54.g5509d4f"})
    assert device.version == "0.5.0+54.g5509d4f"


def test_every_value_stays_in_raw_for_the_helper_alone():
    values = {"port": "/dev/x", "tool": 0, "page_count": 3}
    device = KNOMI.device_from_klipper("knomi_serial t0", values)
    assert dict(device.raw) == values
    assert "tool" not in device.to_json()


def _with_module(version):
    return KNOMI.device_from_klipper("knomi_serial t0", {"module_version": version})


def test_the_module_version_is_one_helper_extra():
    """One klippy module serves every screen of a type, so the first screen
    that reports a version speaks for the type."""
    extras = KNOMI.extras([_with_module(None), _with_module("0.5.0"), _with_module("0.4.0")])
    assert [e.to_json() for e in extras] == [
        {
            "seam": "helper",
            "name": "knomi_serial",
            "key": "module_version",
            "label": "Module",
            "value": "0.5.0",
        }
    ]


def test_no_module_version_means_no_extra():
    assert KNOMI.extras([_with_module(None)]) == []
    assert KNOMI.extras([]) == []


def test_the_notes_tell_unreachable_apart_from_none_configured():
    """"No screens configured" and "we could not ask Klipper" must not look alike."""
    assert KNOMI.devices_note(reachable=False) == "Could not reach Klipper to check for screens."
    assert KNOMI.devices_note(reachable=True) == "No screens found under [knomi_serial ...]."


def test_a_listed_device_is_the_generic_type():
    assert isinstance(KNOMI.device_from_klipper("knomi_serial t0", {}), ListedDevice)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_knomi_serial_helper.py -q`
Expected: FAIL with `AttributeError: 'KnomiSerialHelper' object has no attribute 'device_from_klipper'`, or on the `device_lister(KNOMI) is KNOMI` assertion.

- [ ] **Step 3: Implement the three methods on `KnomiSerialHelper`**

In `src/mcu_updater/helpers/knomi_serial.py`:
- Add `import os`.
- Add `from collections.abc import Mapping, Sequence` and `from typing import Any`, merged with the existing `typing` import.
- Add `from ..extras import Extra` and `from .spec import ListedDevice`.

Then add these methods to the class:

```python
    def device_from_klipper(self, section: str, values: Mapping[str, Any]) -> ListedDevice:
        """One `[knomi_serial ...]` printer object, as the core's `ListedDevice`.

        The module refuses both `serial:` and `device_id:` and requires one, so
        which of them loaded says how the section is addressed. `port` is the
        module's merged value: the configured `serial:` where there is one, the
        path discovery found otherwise. A `device_id:` section therefore has no
        port until discovery finds it, and it still belongs in the list rather
        than vanishing, because a screen that cannot be found is the one to
        say so about. See knomi_serial's docs/protocol.md, "The device map".

        Every live field is None against a module too old for `get_status`, so
        None means unknown here, never False.
        """
        configured_id = values.get("device_id") or None
        configured_path = values.get("port") or None
        # A symlink is the point: the whole scheme is "a stable name udev keeps
        # pointed at the right tty". A discovered path is already a real tty, so
        # for it this is only an existence check.
        resolved = None
        if configured_path:
            try:
                if os.path.exists(configured_path):
                    resolved = os.path.realpath(configured_path)
            except OSError:
                resolved = None
        # False means the screen speaks a different wire protocol than the
        # module expects: the one authoritative "this needs reflashing" a
        # screen can produce, because the device itself declares it.
        compatible = values.get("protocol_match")
        answering = values.get("device_online")
        return ListedDevice(
            # A `device_id:` section is addressed by the id burned into its chip:
            # the discovered path changes when the screen moves socket.
            id=configured_id or configured_path,
            section=section,
            label=section.split(" ", 1)[1] if " " in section else section,
            configured_id=configured_id,
            # Six hex characters from the low three bytes of the screen's eFuse
            # MAC. Burned in, so it survives a reflash, an erase_flash and a move
            # to another socket: the only stable name a KNOMI has, because the
            # CH340K in front of it reports no USB serial. Lowered because the
            # vendor's own docs say not to depend on its case.
            reported_id=(values.get("reported_id") or "").lower() or None,
            configured_path=configured_path,
            resolved_path=resolved,
            version=values.get("firmware_version"),
            compatible=compatible if isinstance(compatible, bool) else None,
            answering=answering if isinstance(answering, bool) else None,
            raw=dict(values),
        )

    def extras(self, devices: Sequence[ListedDevice]) -> list[Extra]:
        """The klippy module's version: one module serves every screen of a
        type, so the first screen that reports one speaks for them all."""
        version = next(
            (d.raw.get("module_version") for d in devices if d.raw.get("module_version")),
            None,
        )
        if version is None:
            return []
        return [Extra("helper", self.name, "module_version", "Module", str(version))]

    def devices_note(self, *, reachable: bool) -> str:
        if not reachable:
            return "Could not reach Klipper to check for screens."
        return f"No screens found under [{self.klipper_prefix} ...]."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_knomi_serial_helper.py tests/test_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Add the knomi half of the `device-listing` mutation spec**

Create `scripts/mutations/device-listing.json`:

```json
{
  "_comment": "Spec 2026-09-29 uniform targets, section 1: device listing is a helper capability. The helper decides what a device is (its addressing rule, its compatibility), the core owns the query and the reachable answer. Each guard here is one of those decisions.",
  "file": "src/mcu_updater/helpers/knomi_serial.py",
  "command": ["python", "-m", "pytest", "tests/test_knomi_serial_helper.py", "-q"],
  "mutations": [
    {
      "name": "a protocol mismatch reaches the core as an incompatible device",
      "find": "            compatible=compatible if isinstance(compatible, bool) else None,",
      "replace": "            compatible=None,"
    },
    {
      "name": "an unreachable Klipper is not reported as an empty list",
      "find": "        if not reachable:\n            return \"Could not reach Klipper to check for screens.\"",
      "replace": "        if False:\n            return \"Could not reach Klipper to check for screens.\""
    }
  ]
}
```

(The `id=configured_id or configured_path` addressing rule is not duplicated here: Task 3 moves `identity.json`'s existing "addressed by its id" mutation onto that line.)

Run: `PY=../../.venv/Scripts/python.exe; $PY scripts/mutation_test.py scripts/mutations/device-listing.json` (Bash `timeout: 600000`).
Expected: every mutation reported killed. Then run `$PY -m pytest tests/test_repo_hygiene.py -q`. Expected: PASS.

- [ ] **Step 6: Gate and commit**

```bash
git add src/mcu_updater/helpers/knomi_serial.py tests/test_knomi_serial_helper.py scripts/mutations/device-listing.json
git commit -m "feat(knomi_serial): list its own devices through the DeviceLister capability

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: PlatformIO devices are listed through the helper

The core keeps the Moonraker query and loses every knomi field name. This is the task that removes `fw.device.list`, and it is the largest. The wire removals that are not about listing (`flash_all`'s `displays`, the job kind, the version) wait for Task 5.

**Files:**
- Modify: `src/mcu_updater/agent/methods/status.py`
  - `status()` (~line 481): `displays = self.pio_status()` → `platformio = self.platformio_status()`
  - `pio_status` (509-636) → `platformio_status`
  - `targets()` (723-755): parameter `displays` → `platformio`
  - `_screen_id` (1278-1295): delete
  - `_pio_target` (1297-1400) → `_platformio_target`
  - `_platformio_confidence`, `_platformio_device_status`, `_platformio_state` (1402-1466)
  - `target_get` (1633): `pio_status()` → `platformio_status()`
  - METHODS: drop `"fw.device.list"` (2002)
  - `device_list` (2123-2314) → `platformio_devices`; `_watcher_map` (2316-2386): delete
  - `pio_types` (2388) → `platformio_types`
  - imports
- Modify: `src/mcu_updater/agent/methods/flash.py` (`port_detail` 24-38, `_pio_flash` 423-544)
- Modify: `src/mcu_updater/agent/methods/bulk.py` (`_screens_to_flash` 346-401 → `_platformio_to_flash`, and its two callers at ~590 and ~711)
- Modify: `src/mcu_updater/agent/methods/build.py` (`pio_types` at 67 and 161)
- Modify: `src/mcu_updater/agent/methods/_api.py` (stubs 55-57, 81)
- Modify: `src/mcu_updater/helpers/spec.py` (the `Identifier` docstring, 161-164)
- Rename: `tests/test_agent_displays.py` → `tests/test_agent_platformio_devices.py` (new full content, below)
- Rename: `tests/test_agent_display_jobs.py` → `tests/test_agent_platformio_flash.py` (edits, below)
- Modify: `tests/test_agent_targets.py`, `tests/test_agent_methods.py`, `tests/test_helpers.py`, `tests/test_knomi_watcher.py`
- Modify: `scripts/mutations/display-flash.json`, `identity.json`, `targets.json`, `pio.json`, `verdict.json`, `flashlog-loop.json`, `device-listing.json`

**Interfaces:**
- Consumes: `ListedDevice`, `DeviceLister`, `helpers.device_lister` (Task 1); `Extra`, `ordered` (Task 1); `KnomiSerialHelper.device_from_klipper/extras/devices_note` (Task 2).
- Produces:
  - `Api.platformio_types() -> dict[str, PioType]` (renamed from `pio_types`)
  - `Api.platformio_devices() -> tuple[dict[str, list[ListedDevice]], bool]`: a dict keyed by PlatformIO type name, and `reachable`
  - `Api._device_lister_for(entry: PioType, families) -> DeviceLister | None` (staticmethod; never raises)
  - `Api.platformio_status() -> list[dict]` (renamed from `pio_status`). Each payload is `PioType.to_json()` plus these keys:
    - `devices`: each is `ListedDevice.to_json()` plus `reason` and `confidence`
    - `extras: list[dict]`
    - `devices_note: str | None`
    - `source: dict | None`
    - `has_firmware`, `artifact_reason`, `build_blocked`, `needs_flash`
  - The platformio `targets[]` row gains `firmware` (the family), `source`, `extras` and `devices_note`, and loses `extra`.
  - module function `status._source_json(path: str | None, version: str | None, dirty: bool | None) -> dict | None`, which Task 4 reuses
  - `Api._platformio_to_flash(scope, only=None)` (renamed from `_screens_to_flash`)
  - `flash.port_detail(entry: PioType, device: Mapping[str, Any]) -> dict`, which reads `ListedDevice.to_json()` keys. Its output keys (`env`, `port`, `device_id`, `name`, `section`) are unchanged, because `flashers/platformio.py` reads them.
  - Not yet changed: the flash responses still carry `displays` (now `[d.to_json() ...]`), and the job kind is still `display_flash`. Task 5 removes both.

- [ ] **Step 1: Move the two test files**

```bash
git mv tests/test_agent_displays.py tests/test_agent_platformio_devices.py
git mv tests/test_agent_display_jobs.py tests/test_agent_platformio_flash.py
```

In every `scripts/mutations/*.json` `command` list, replace `tests/test_agent_displays.py` with `tests/test_agent_platformio_devices.py` and `tests/test_agent_display_jobs.py` with `tests/test_agent_platformio_flash.py`. The specs are `display-flash`, `identity`, `pio`, `verdict` and `flashlog-loop`. Confirm none is left:

Run: `git grep -n "test_agent_display" -- scripts tests src`
Expected: the only hit is `tests/test_knomi_watcher.py`'s docstring, which Step 8 rewords.

- [ ] **Step 2: Replace `tests/test_agent_platformio_devices.py` with tests against a fake lister**

The old file tested knomi's fields through the agent. Task 2 moved those tests to `tests/test_knomi_serial_helper.py`, and the watcher-map tests go with `_watcher_map`. Replace the whole file with:

```python
"""PlatformIO devices: the core's half of listing them.

The core owns one Moonraker query and one `reachable` answer. What a device
*is* - its addressing rule, its fields, whether it is compatible - belongs to
the family's helper, through `DeviceLister`. So nothing here imports
knomi_serial: every test runs against `FakeLister`, and a core line that knew a
knomi field name would fail them.

`firmware.HELPERS` is a static tuple that config loading checks, so the fake
stands in under a registered name. The name is the only knomi thing about it.
"""

from __future__ import annotations

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import ERR_METHOD_NOT_FOUND, RpcError
from mcu_updater.extras import Extra
from mcu_updater.helpers import ListedDevice
from mcu_updater.helpers import registry as helpers_registry

from .conftest import serve_klipper, write_main_config

PREFIX = "fake_dev"


class FakeLister:
    name = "knomi_serial"
    klipper_prefix = PREFIX

    def device_from_klipper(self, section, values):
        path = values.get("path")
        hwid = values.get("hwid")
        return ListedDevice(
            id=hwid or path,
            section=section,
            label=section.split(" ", 1)[1],
            configured_id=hwid,
            reported_id=values.get("reports"),
            configured_path=path,
            resolved_path=path if values.get("present") else None,
            version=values.get("version"),
            compatible=values.get("ok"),
            answering=values.get("up"),
            raw=dict(values),
        )

    def extras(self, devices):
        if not devices:
            return []
        return [Extra("helper", self.name, "count", "Devices", len(devices))]

    def devices_note(self, *, reachable):
        return "fake: none configured" if reachable else "fake: unreachable"


def _config(source, *types, family="fakefw", helper="knomi_serial"):
    text = (
        f"[firmware {family}]\nsource: {source}\nbuilder: platformio\n"
        + (f"helper: {helper}\n" if helper else "")
        + "flashers: platformio\n"
    )
    for name in types:
        text += f"\n[type {name}]\nchipset: esp32\nfirmware: {family}\nplatformio_env: {name}\n"
    return text


@pytest.fixture
def fake(monkeypatch):
    lister = FakeLister()
    monkeypatch.setitem(helpers_registry._BY_NAME, "knomi_serial", lister)
    return lister


@pytest.fixture
def api(paths, fake_root, fake):
    (fake_root / "fakefw").mkdir()
    write_main_config(paths, _config(fake_root / "fakefw", "fake_a"))
    return Api(paths)


def _row(api, name="fake_a"):
    return {t["name"]: t for t in api.dispatch("fw.status")["targets"]}[name]


# --------------------------------------------------------------------------
# the listing
# --------------------------------------------------------------------------


def test_devices_are_the_listers_prefix_and_nothing_else(api):
    api._call = serve_klipper(
        {
            "fake_dev t0": {"path": "/dev/a", "present": True},
            "fake_dev_other x": {"path": "/dev/b"},
            "mcu ebbt0": {},
        }
    )

    listed, reachable = api.platformio_devices()

    assert reachable is True
    assert [d.section for d in listed["fake_a"]] == ["fake_dev t0"]


def test_devices_come_back_in_a_stable_order_whatever_their_case(api):
    """A list that reorders between polls makes the panel jump around."""
    api._call = serve_klipper({f"fake_dev {n}": {} for n in ("t2", "T0", "t1")})

    listed, _ = api.platformio_devices()

    assert [d.label for d in listed["fake_a"]] == ["T0", "t1", "t2"]


def test_each_object_reaches_the_lister_under_its_own_capitalisation(api):
    """The printer object keeps printer.cfg's case; querying by a lowered name
    returns nothing at all, silently."""
    call = serve_klipper({"fake_dev T0": {"version": "1.0"}})
    api._call = call

    listed, _ = api.platformio_devices()

    assert listed["fake_a"][0].section == "fake_dev T0"
    assert listed["fake_a"][0].version == "1.0"
    assert list(call.queries[-1]["objects"]) == ["fake_dev T0"]


def test_an_unreachable_klipper_lists_nothing_and_says_so(api):
    """Review Focus 1. "No devices configured" and "we could not ask Klipper"
    must not look alike, and neither may raise out of fw.status."""
    api._call = serve_klipper({"fake_dev t0": {}}, reachable=False)

    row = _row(api)

    assert row["devices"] == []
    assert row["devices_note"] == "fake: unreachable"


def test_a_query_klipper_cannot_answer_is_unreachable_not_empty(api):
    """The object list can come from the cache while the query itself fails."""
    inner = serve_klipper({"fake_dev t0": {}})

    def call(method, params, timeout):
        if method == "printer.objects.query":
            return {}
        return inner(method, params, timeout)

    api._call = call

    assert api.platformio_devices() == ({}, False)


def test_a_reachable_klipper_with_no_sections_uses_the_reachable_note(api):
    api._call = serve_klipper({})

    row = _row(api)

    assert row["devices"] == []
    assert row["devices_note"] == "fake: none configured"


def test_two_types_sharing_a_prefix_both_list_it_and_it_is_queried_once(paths, fake_root, fake):
    """Review Focus 3. A prefix belongs to a family's helper, not to a type, so
    two types of one family list the same sections - and one query serves both."""
    (fake_root / "fakefw").mkdir()
    write_main_config(paths, _config(fake_root / "fakefw", "fake_a", "fake_b"))
    api = Api(paths)
    call = serve_klipper({"fake_dev t0": {}, "fake_dev t1": {}})
    api._call = call

    listed, _ = api.platformio_devices()

    assert [d.label for d in listed["fake_a"]] == ["t0", "t1"]
    assert [d.label for d in listed["fake_b"]] == ["t0", "t1"]
    assert len(call.queries) == 1


def test_a_family_with_no_lister_lists_nothing_and_names_itself(paths, fake_root):
    (fake_root / "plainfw").mkdir()
    write_main_config(paths, _config(fake_root / "plainfw", "plain", family="plainfw", helper=None))
    api = Api(paths)
    call = serve_klipper({"fake_dev t0": {}})
    api._call = call

    row = _row(api, "plain")

    assert row["devices"] == []
    assert row["devices_note"] == (
        "No devices are listed for this type: [firmware plainfw] names no "
        "helper that can list them."
    )
    assert api.platformio_devices() == ({}, True)


def test_no_platformio_types_costs_no_query_of_their_own(paths):
    """An absent feature should cost an absent key, not a round trip."""
    write_main_config(paths, "")
    calls = []
    api = Api(paths)
    api._call = lambda method, params, timeout: calls.append(method) or {}

    assert api.platformio_status() == []
    assert calls == []


def test_the_object_list_is_fetched_once_for_mcus_and_platformio(api):
    """A whole extra round trip, and fw.status has a sub-second budget."""
    calls = []
    inner = serve_klipper({"fake_dev t0": {}})

    def counting(method, params, timeout):
        calls.append(method)
        return inner(method, params, timeout)

    api._call = counting
    api.platformio_devices()
    api._mcu_object_names()
    api.platformio_devices()

    assert calls.count("printer.objects.list") == 1


def test_fw_device_list_is_gone(api):
    """Its data rides `targets[]` and `fw.target.get`; a second, unversioned
    copy of it on the wire is what this refactor removes."""
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.device.list")
    assert exc.value.code == ERR_METHOD_NOT_FOUND
    assert "fw.device.list" not in api.dispatch("fw.ping")["capabilities"]


# --------------------------------------------------------------------------
# the status payload and the row
# --------------------------------------------------------------------------


def test_a_device_reaches_status_as_its_generic_fields_only(api):
    api._call = serve_klipper(
        {"fake_dev t0": {"path": "/dev/a", "present": True, "private": 7}}
    )

    device = api.platformio_status()[0]["devices"][0]

    assert set(device) == set(ListedDevice.__dataclass_fields__) - {"raw"} | {
        "present",
        "reason",
        "confidence",
    }
    assert "private" not in device


def test_an_incompatible_device_makes_the_type_need_flashing(api):
    """The device declaring it cannot work with the host is authoritative."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "ok": False}})

    assert api.platformio_status()[0]["needs_flash"] is True
    assert _row(api)["devices"][0]["reason"] == "protocol_mismatch"


def test_unknown_compatibility_is_not_a_mismatch(api):
    """None until the device reports in; reading it as a mismatch would send
    people to reflash a healthy device."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "ok": None}})

    assert api.platformio_status()[0]["needs_flash"] is False


def test_the_listers_extras_reach_the_row(api):
    api._call = serve_klipper({"fake_dev t0": {}, "fake_dev t1": {}})

    assert _row(api)["extras"] == [
        {"seam": "helper", "name": "knomi_serial", "key": "count", "label": "Devices", "value": 2}
    ]


def test_no_devices_means_no_extras_and_a_note(api):
    api._call = serve_klipper({})

    row = _row(api)

    assert row["extras"] == []
    assert row["devices_note"] is not None


def test_a_row_with_devices_carries_no_note(api):
    api._call = serve_klipper({"fake_dev t0": {}})

    assert _row(api)["devices_note"] is None


def test_a_platformio_row_names_its_firmware_family(api):
    api._call = serve_klipper({})

    assert _row(api)["firmware"] == "fakefw"


def test_a_platformio_row_carries_no_extra(api):
    api._call = serve_klipper({})

    assert "extra" not in _row(api)


def test_an_undiscovered_device_id_section_is_a_blocked_row_not_a_missing_one(paths, api):
    """Review Focus 2. A section addressed by id, which discovery has not
    found yet: listed, addressed by its id, and its flash refused rather than
    aimed at a stale path."""
    from .conftest import write_settings
    from mcu_updater.jobs import JobRunner
    from mcu_updater.settings import load_settings

    write_settings(paths, enable_flashing="true", service_backend="null")
    runner = JobRunner(paths, lambda: load_settings(paths.settings_file))
    api = Api(paths, runner=runner, call=serve_klipper({"fake_dev t0": {"hwid": "aa11"}}))

    device = _row(api)["devices"][0]
    flash = next(a for a in device["actions"] if a["id"] == "flash")

    assert (device["id"], device["present"], device["path"], device["state"]) == (
        "aa11",
        False,
        None,
        "missing",
    )
    assert flash["params"] == {"name": "fake_a", "port": "aa11"}
    assert flash["blocked"]["code"] in (Api.BLOCKED_NO_DEVICE, Api.BLOCKED_NO_ARTIFACT)


@pytest.mark.parametrize(
    ("up", "state"), [(True, "online"), (False, "silent"), (None, "reachable")]
)
def test_a_present_devices_state_is_whether_it_answers(api, up, state):
    """A port that opens is not a device that answers, and an unknown answer
    must not read as a fault."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "up": up}})

    assert _row(api)["devices"][0]["state"] == state
```

- [ ] **Step 3: Update the other tests to the new names and shapes**

`tests/test_agent_targets.py`:
- `test_a_display_projects_onto_the_same_shape` (159-168): replace the `set(display) == mcu_keys | {"extra"}` assertion and its comment with:
  ```python
      # Everything an MCU row carries, plus the three uniform keys Task 4 gives
      # every row. Not `extra`: that bag is what this refactor removes.
      assert set(display) == mcu_keys | {"source", "extras", "devices_note"}
  ```
- The projection test (741-792): `legacy_displays = api.pio_status()` → `legacy_platformio = api.platformio_status()`. Pass it to `api.targets(...)` and use it in the name-set assertion. Then replace the display loop (from `for legacy in legacy_displays:` through the `device["name"] == screen["section"]` line) with:
  ```python
      for legacy in legacy_platformio:
          target = targets[legacy["name"]]
          assert target["descriptor"] == legacy["env"]
          assert target["firmware"] == legacy["firmware"]
          assert [d["id"] for d in target["devices"]] == [d["id"] for d in legacy["devices"]]
          assert target["source"] == legacy["source"]
          assert target["extras"] == legacy["extras"]
          assert target["devices_note"] == legacy["devices_note"]
          for device, listed in zip(target["devices"], legacy["devices"], strict=True):
              assert device["present"] == listed["present"]
              assert device["version"] == listed["version"]
              assert device["name"] == listed["section"]
              assert device["path"] == listed["resolved_path"]
              assert device["confidence"] == listed["confidence"]
  ```
- Line 1508: `displays = api.pio_status()` → `platformio = api.platformio_status()`, and pass `platformio` on the next call.
- Replace `pio_status` with `platformio_status` in the module docstring (7, 13) and at 1321.
- `test_a_display_has_no_firmware_family_and_says_so` (214-221) asserts the opposite of `test_a_platformio_row_names_its_firmware_family` above. Replace it with:
  ```python
  def test_a_platformio_row_names_the_family_it_was_declared_under(api, paths, fake_root):
      """The row's `firmware` is the `[firmware ...]` family its `[type]` names -
      the same axis every other builder's row reports, and what selects its
      helper."""
      _add_display(paths, fake_root, api)
      assert _targets(api, "platformio")[ENV]["firmware"] == "knomi_serial"
  ```

`tests/test_agent_methods.py:292`: rename the test to `test_target_get_returns_the_same_detail_as_status_for_a_platformio_type`. Add `assert "devices" in res["target"] and "screens" not in res["target"]`.

`tests/test_agent_platformio_flash.py` (renamed in Step 1):
- 168-174: `original = api.device_list` / `def watched_list(args):` / `return original(args)` / `monkeypatch.setattr(api, "device_list", watched_list)` become `original = api.platformio_devices` / `def watched_list():` / `return original()` / `monkeypatch.setattr(api, "platformio_devices", watched_list)`.
- 987: `api.pio_types()` → `api.platformio_types()`.
- Keep the `res["displays"]` assertions for now. Task 5 removes the key; in this task it holds `ListedDevice.to_json()` dicts, which still have `configured_path`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_agent_platformio_devices.py tests/test_agent_targets.py tests/test_agent_platformio_flash.py tests/test_agent_methods.py -q -x`
Expected: FAIL. The first failure is `AttributeError: 'Api' object has no attribute 'platformio_devices'`.

- [ ] **Step 5: Replace the listing in `status.py`**

Imports:
- Delete `null_reporter` from `from ...build import null_reporter, read_sidecar`; only `_watcher_map` used it.
- Change `from ...helpers import DeviceInfoReader, ImageReporter` to `from ...helpers import DeviceInfoReader, DeviceLister, ImageReporter, ListedDevice`.
- Add `from ...extras import ordered`.
- Add `from ...providers.pio import PioType` under a new `if TYPE_CHECKING:` block, and add `TYPE_CHECKING` to the `typing` import.
- Change the module docstring's first line to `"""fw.ping / fw.status / fw.type.list / fw.job.* -- the read surface."""`.

Add a module-level function after `_mtime`:

```python
def _source_json(
    path: str | None, version: str | None, dirty: bool | None
) -> dict[str, Any] | None:
    """A row's `source`: the tree its builder would build from right now.

    `version` is whatever string that builder's staleness check compares, so
    a reader can set it beside what the devices report. None with no checkout,
    because a path with nothing in it is not a source.
    """
    if not path or not version:
        return None
    return {"path": os.path.expanduser(path), "version": version, "dirty": dirty}
```

Replace `device_list` and `_watcher_map` (2121-2386, from the `# -- ESP32 displays --` comment through the end of `_watcher_map`) with the following. Rename `pio_types` to `platformio_types` in the same edit.

```python
    # -- PlatformIO devices -------------------------------------------------

    def platformio_devices(self) -> tuple[dict[str, list[ListedDevice]], bool]:
        """Each PlatformIO type's configured devices, and whether Klipper answered.

        **The list comes from Klipper, not from our registry.** A family's
        klippy module names its devices in printer.cfg, and a second copy here
        would only be something to disagree with. The family's helper says which
        printer objects are its devices and what their values mean
        (`DeviceLister`); this owns the query, so every prefix costs one shared
        round trip rather than one per type.

        A type whose family has no lister lists nothing. Its row says why, from
        `platformio_status`.
        """
        types = self.platformio_types()
        families = firmware.load(self.paths)
        listers = {name: self._device_lister_for(entry, families) for name, entry in types.items()}
        prefixes = sorted({lister.klipper_prefix for lister in listers.values() if lister})
        if not prefixes:
            return {}, True

        # The printer objects, in their real capitalisation. These *are* the
        # section list: a klippy extra whose section is in printer.cfg has an
        # object, because Klipper refuses to start when loading one raises.
        objects = {prefix: self._object_names_for(prefix) for prefix in prefixes}
        wanted = sorted({name for names in objects.values() for name in names})
        if not wanted:
            # No sections. Still have to say whether we could *ask*: "none
            # configured" and "could not reach Klipper" must not look alike,
            # and with nothing to query there is no answer to infer it from.
            return {}, bool(self._probe("printer.info"))

        # None means every field: the module decides what it can report, and
        # one too old for get_status answers nothing.
        res = self._probe("printer.objects.query", {"objects": dict.fromkeys(wanted)})
        status = (res or {}).get("status")
        if not isinstance(status, dict):
            return {}, False

        out: dict[str, list[ListedDevice]] = {}
        for name, lister in listers.items():
            if lister is None:
                continue
            out[name] = [
                lister.device_from_klipper(obj, status.get(obj) or {})
                for obj in sorted(objects[lister.klipper_prefix], key=str.lower)
            ]
        return out, True

    @staticmethod
    def _device_lister_for(
        entry: PioType, families: dict[str, firmware.FirmwareFamily]
    ) -> DeviceLister | None:
        """The type's family's lister, or None - never an exception.

        The type list is read leniently and never validated, so an undeclared
        family or a misspelt `helper:` is still live here. A status poll has to
        report "cannot list" on that one row rather than raise for all of them.
        """
        try:
            family = firmware.resolve(None, entry.firmware, families)
            return helpers.device_lister(helpers.for_name(family.helper, family=family.name))
        except ConfigCorruptError:
            return None

    def platformio_types(self) -> dict[str, PioType]:
        """Configured PlatformIO `[type <env>]` sections."""
        from ...providers import pio as pio_mod

        return pio_mod.load(self.paths)
```

Check `firmware.resolve`'s signature before writing that call (`grep -n "def resolve" src/mcu_updater/firmware.py`). The deleted `_watcher_map` called it as `firmware.resolve(self.paths, display.firmware, families)`; the staticmethod has no `self`, so pass what that signature needs. If `paths` is required, make `_device_lister_for` a plain method and pass `self.paths`.

Delete `"fw.device.list": "device_list",` from `METHODS`.

- [ ] **Step 6: Replace `pio_status` with `platformio_status`**

Replace the whole of `pio_status` (509-636) with:

```python
    def platformio_status(self) -> list[dict[str, Any]]:
        """Configured PlatformIO types, each with the devices its helper lists.

        Rolled into fw.status so the panel paints in one call. Cheap when
        unconfigured: no PlatformIO `[type]` means no query at all.
        """
        from ...build import FlashLog
        from ...providers import pio as pio_mod

        types = self.platformio_types()
        if not types:
            return []

        listed, reachable = self.platformio_devices()
        # Read once for every device of every type, not once per device.
        flashlog = FlashLog(self.paths)
        families = firmware.load(self.paths)

        out = []
        for name, entry in sorted(types.items()):
            reader = device_info.reader_for(families.get(entry.firmware))
            lister = self._device_lister_for(entry, families)
            # Once per type, not once per device: they share a source tree, and
            # it costs three git calls.
            tree = pio_mod.source_state(entry.source)
            expected = verdict.Expected(
                head=tree.head,
                stamp=tree.version,
                stamp_kind=verdict.STAMP_TAG,
                # A release build reports a bare version with no commit in it,
                # so it is current only while the tree is still sitting on that
                # tag with nothing uncommitted.
                tag_clean=tree.on_tag and not tree.dirty,
                # No checkout means no verdict, even though the VERSION file
                # would still give a stamp.
                require_head=True,
            )
            art = pio_mod.artifact_status(self.paths, entry, tree)
            devices = []
            for device in listed.get(name, []):
                judged = verdict.decide(
                    # No `state`: presence is layered on top by
                    # `_platformio_device_status`, which owns the `offline` and
                    # `protocol_mismatch` answers for a device row.
                    verdict.Evidence(
                        version=device.version,
                        running_sha=pio_mod.running_sha(device.version),
                        dirty=pio_mod.is_dirty(device.version),
                    ),
                    expected,
                )
                devices.append(
                    {
                        **device.to_json(),
                        # None (current), source_changed, device_dirty, or
                        # unknown_version: the sha baked into what the device
                        # reports running, against the tree's HEAD.
                        "reason": judged.reason,
                        # How this device's identity was confirmed the last
                        # time this tool wrote to it, from our own record.
                        "confidence": self._platformio_confidence(device, flashlog, reader),
                    }
                )
            if devices:
                note = None
            elif lister is None:
                note = (
                    "No devices are listed for this type: "
                    f"[firmware {entry.firmware}] names no helper that can list them."
                )
            else:
                note = lister.devices_note(reachable=reachable)
            out.append(
                {
                    **entry.to_json(),
                    "devices": devices,
                    "extras": ordered(lister.extras(listed.get(name, []))) if lister else [],
                    "devices_note": note,
                    "source": _source_json(entry.source, tree.head, tree.dirty),
                    # Built by PlatformIO, not by us - so this is "is there an
                    # image on disk", not staleness.
                    "has_firmware": os.path.exists(pio_mod.firmware_bin(entry)),
                    # None (current), never_built, config_changed,
                    # source_changed, built_dirty, foreign_build, or
                    # no_provenance. Real staleness, unlike has_firmware.
                    "artifact_reason": art.reason,
                    # Why a build would be skipped, or None. The same answer the
                    # provider gives a fleet build, from the same function, so
                    # the preview cannot name work the batch will pass over.
                    "build_blocked": providers.platformio.source_problem(entry),
                    # Two independent reasons to reflash, and either is enough:
                    # the device declaring it cannot work with the host, or it
                    # running an older commit than the tree.
                    "needs_flash": any(
                        d["compatible"] is False or d["reason"] == pio_mod.SOURCE_CHANGED
                        for d in devices
                    ),
                }
            )
        return out
```

In `status()`: `displays = self.pio_status()` → `platformio = self.platformio_status()`, and `self.targets(reg, types, displays, rows)` → `self.targets(reg, types, platformio, rows)`. Replace the comment above it ("types[] and displays[] said in one shape...") with `# One row shape for every builder, so one component renders them all.`

In `targets()`: rename the parameter `displays` to `platformio`, make the docstring `"""Every configured type, in one row shape."""`, and change the projection line to

```python
            self._platformio_target(payload, allowed) for payload in platformio
```

In `target_get`: `for payload in self.pio_status():` → `for payload in self.platformio_status():`.

- [ ] **Step 7: Replace the row and its three helpers**

Delete `_screen_id`. Replace `_pio_target` with `_platformio_target`:

```python
    def _platformio_target(
        self, payload: dict[str, Any], allowed: set[str]
    ) -> dict[str, Any]:
        name = payload["name"]
        status = ArtifactStatus(payload["artifact_reason"])

        devices = []
        for device in payload["devices"]:
            judged = self._platformio_device_status(device)
            devices.append(
                {
                    # What the device is addressed by, which the helper decided:
                    # a configured id where there is one, because a discovered
                    # path changes when the device moves socket.
                    "id": device["id"],
                    # Its printer object - the same slot the MCU rows use for
                    # their [mcu] section, and the same kind of fact.
                    "name": device["section"],
                    "present": device["present"],
                    "state": self._platformio_state(device),
                    "path": device["resolved_path"],
                    "version": device["version"],
                    # Our record of how this device was identified at write
                    # time, in the same slot and vocabulary an MCU device uses.
                    "confidence": device["confidence"],
                    **self._device_json(judged),
                    "actions": self._device_actions(
                        allowed,
                        flash=(
                            "fw.flash",
                            {"name": name, "port": device["id"]},
                        ),
                        present=device["present"],
                        has_artifact=bool(payload["has_firmware"]),
                        what=f"{payload['firmware']} firmware",
                        label=device["label"],
                    ),
                }
            )
```

The `actions` block that follows (build action plus `_flash_actions`) stays unchanged. Keep `if not problem` verbatim, because `targets.json` #14 anchors it. In its comment, "A fleet build skips a display without one" becomes "A fleet build skips a type without one". Replace the `return {...}` with:

```python
        return {
            "provider": providers.PlatformIO.name,
            "name": name,
            "descriptor": payload["env"],
            "firmware": payload["firmware"],
            "artifact": self._artifact_json(status),
            # Always null: PlatformIO carries its configuration in
            # platformio.ini, so there are no answers for a profile to be.
            "profile": None,
            "needs_flash": self._aggregate(devices),
            "devices": devices,
            "actions": actions,
            "source": payload["source"],
            "extras": payload["extras"],
            "devices_note": payload["devices_note"],
        }
```

Replace the three helpers after it:

```python
    @staticmethod
    def _platformio_confidence(
        device: ListedDevice, flashlog: Any, reader: DeviceInfoReader
    ) -> str | None:
        """How this device's identity was confirmed when we last wrote to it.

        The counterpart of the lookup in `flash_state`, with the same care: our
        own record, discarded by `entry_for` when what the device reports
        running disagrees with the tree commit we recorded writing. Never a
        live discovery answer - a status poll must not pay for one.

        Null in three cases a caller cannot tell apart from this field alone: no
        record yet, a record discarded as stale, or no hardware id to have filed
        a record under.
        """
        from ...build import hardware_id_key

        ident = (device.configured_id or device.reported_id or "").lower()
        if not ident:
            return None
        record = flashlog.entry_for(
            hardware_id_key(ident), reader.running_sha(device.version)
        )
        return (record or {}).get("confidence")

    @staticmethod
    def _platformio_device_status(device: dict[str, Any]) -> DeviceStatus:
        """Does this device want firmware, and why?

        Two independent ways to want it: the device declaring it cannot work
        with the host, and `source_changed`. The version comparison has no word
        for the first, so it is applied on top rather than folded in.

        One function because there are two callers and they must not disagree:
        the panel row and the fleet-flash selection.
        """
        if device.get("compatible") is False:
            return DeviceStatus(PROTOCOL_MISMATCH)
        if not device["present"]:
            return DeviceStatus(OFFLINE)
        return DeviceStatus(device.get("reason"))

    @staticmethod
    def _platformio_state(device: dict[str, Any]) -> str:
        """The slot an MCU row fills with klipper/katapult/offline.

        The middle state is the point: a port that opens is not a device that
        answers, and `present` stays true with the far end unplugged.
        """
        if not device["present"]:
            return "missing"
        answering = device.get("answering")
        if answering is True:
            return "online"
        if answering is False:
            return "silent"
        # Unknown must not read as "silent": that would invent a fault on a
        # device working perfectly.
        return "reachable"
```

In `_api.py`: replace the three stubs at 55-57 with

```python
    def platformio_status(self) -> list[dict[str, Any]]: ...
    def platformio_types(self) -> dict[str, PioType]: ...
    def platformio_devices(self) -> tuple[dict[str, list[ListedDevice]], bool]: ...
```

Change line 81 to `def _platformio_device_status(device: dict[str, Any]) -> DeviceStatus: ...`. Add `ListedDevice` to the `from ...helpers import` line, and `from ...providers.pio import PioType` under a `TYPE_CHECKING` guard (this file is already TYPE_CHECKING-only in effect; follow how it imports its other types).

In `build.py`, rename `self.pio_types()` to `self.platformio_types()` at 161, and the docstring's "Mirrors `pio_types()`" at 67.

- [ ] **Step 8: Move the flash route and the fleet selection onto `ListedDevice`**

`flash.py`: replace `port_detail` with

```python
def port_detail(entry: PioType, device: Mapping[str, Any]) -> dict[str, Any]:
    """A listed PlatformIO device, as the `platformio` flasher reads it.

    `device` is `ListedDevice.to_json()`, with or without the status fields.
    The id is resolved the way the flasher always has - the configured one,
    else the one the firmware reported. Shared with `bulk`.
    """
    return {
        "env": entry,
        "port": device["configured_path"],
        "device_id": (device.get("configured_id") or device.get("reported_id") or "").lower(),
        "name": device["label"],
        "section": device["section"],
    }
```

In `_pio_flash`:
- `types = self.pio_types()` → `types = self.platformio_types()`, and the local `display = types[name]` → `entry = types[name]` (with every later use of `display` in the method).
- Replace everything from `# Read the devices NOW` through the end of the `if not targets:` block with:

```python
        # Read the devices NOW, while Klipper can still answer.
        listed, reachable = self.platformio_devices()
        # Either spelling of the one identity. `port` is what this call has
        # always taken; `id` is the uniform slot, and either can name the
        # configured path, the configured id, or the identity the firmware
        # itself reported. The path stays exact because it is a POSIX
        # filesystem path; only identities are compared case-insensitively.
        wanted = args.get("port") or args.get("id")
        want = None if wanted is None else str(wanted)
        targets = [
            d
            for d in listed.get(name, [])
            if d.present
            and (
                want is None
                or want == d.configured_path
                or want.lower() in {i.lower() for i in (d.configured_id, d.reported_id) if i}
            )
        ]
        if not targets:
            raise RpcError(
                "no device is reachable to flash. Check that the configured ports "
                "exist - fw.target.get shows which are missing.",
                data={
                    "code": "nothing_to_do",
                    "message": "no reachable devices",
                    "data": {
                        "devices": [d.to_json() for d in listed.get(name, [])],
                        "reachable": reachable,
                    },
                },
            )
```

- Keep the `assert_printer_idle(...)` block and the `# Built from the list read *before* the stop` comment verbatim; `display-flash.json` #1 anchors both.
- In the `flashers.Device(...)` build, use `id=d.configured_path or ""` and `detail=port_detail(entry, d.to_json())`, iterating `for d in targets`. Rename the `screens` result of `select_each` to `selected`.
- In `run`: `named = {d.configured_path: d.label for d in targets}`, and rename "a display caller" in its comment to "a caller of this route".
- The return becomes `{"job_id": job.id, "job": job.to_dict(), "displays": [d.to_json() for d in targets]}`. Task 5 removes `displays`.

`bulk.py`: rename `_screens_to_flash` → `_platformio_to_flash`, and update both callers (~590 and ~711). The call sites' local names `screens`/`screens_refused` become `platformio`/`platformio_refused`. The body becomes:

```python
        known = self.platformio_types()
        settings = self.settings()
        families = firmware.load(self.paths)
        requests: list[tuple[flashers.Device, tuple[str, ...]]] = []
        for payload in self.platformio_status():
            if only is not None and payload["name"] != only:
                continue
            if not payload["has_firmware"]:
                continue
            # The live object, not one rebuilt from the payload: `to_json` is a
            # wire projection and reversing it is the thing this codebase keeps
            # deciding not to do.
            entry = known[payload["name"]]
            units = stop_services.for_platformio(self.paths, entry, settings, families)
            for device in payload["devices"]:
                if not device["present"]:
                    continue
                status = self._platformio_device_status(device)
                if scope != "all" and status.needs_flash is not True:
                    continue
                requests.append(
                    (
                        flashers.Device(
                            type=entry.name,
                            id=device["configured_path"],
                            chipset="",
                            state=inventory.STATE_UNKNOWN,
                            fw=entry.firmware,
                            kind=flashers.KIND_PORT,
                            detail={
                                **port_detail(entry, device),
                                "reason": "forced" if scope == "all" else status.reason,
                            },
                        ),
                        units,
                    )
                )
        return flashers.select_each(self.paths, families, requests)
```

Rewrite its docstring with "device"/"type" for "screen"/"display". `loops.json` #0 anchors the identical line `if not device["present"]:\n                    continue\n` in the cmake loop. `mutation_test.py` replaces the *first* occurrence, and `_cmake_boards_to_flash` comes earlier in the file, so the anchor still lands on the cmake loop. Confirm the order with `grep -n 'if not device\["present"\]' src/mcu_updater/agent/methods/bulk.py`: the cmake line must be listed first.

`helpers/spec.py` `Identifier` docstring (161-164): replace "because `fw.device.list` already puts it on the wire and a five-field copy here would be a second description of one thing;" with "because a five-field copy here would be a second description of one thing;".

`tests/test_helpers.py:227`: change the docstring to `"""`answered` is a fact about one write-time listen, not a field of the device map, so `to_json` never carries it."""`.

`tests/test_knomi_watcher.py:11-12`: replace "The two tests that exercise `api.device_list` stayed behind - they are agent-level, not `providers.pio`-level." with "Agent-level listing is tested in `test_agent_platformio_devices.py`, against a fake lister."

- [ ] **Step 9: Run the tests**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_agent_platformio_devices.py tests/test_knomi_serial_helper.py tests/test_agent_targets.py tests/test_agent_platformio_flash.py tests/test_agent_methods.py tests/test_agent_bulk.py -q`
Expected: PASS. If `tests/test_agent_platformio_flash.py` fails on a `"displays"` item key, the key is one `ListedDevice.to_json()` renamed:

| Old key | New key |
|---|---|
| `name` | `label` |
| `device_id` | `configured_id` |
| `firmware_version` | `version` |

Update that assertion to the new key; do not add the old key back.

- [ ] **Step 10: Re-anchor the mutation specs**

`scripts/mutations/display-flash.json`:

| # | New `find` | New `replace` |
|---|---|---|
| 2 (status.py) | `        objects = {prefix: self._object_names_for(prefix) for prefix in prefixes}` | `        objects = {prefix: self._all_object_names() for prefix in prefixes}` |
| 3 | `            self._platformio_target(payload, allowed) for payload in platformio` | `""` (as before) |
| 17 | `        record = flashlog.entry_for(\n            hardware_id_key(ident), reader.running_sha(device.version)\n        )` | `        record = flashlog.entry_for(hardware_id_key(ident), None)` |
| 18 | `                    "confidence": device["confidence"],` | `                    "confidence": None,` |

Mutation 2's name becomes "devices are matched by their lister's own section prefix".

`scripts/mutations/identity.json`:
- #6 moves to knomi: delete its `"file"` key (the spec's default file is `helpers/knomi_serial.py`). Set `find` to `            id=configured_id or configured_path,` and `replace` to `            id=configured_path,`.
- Delete #7 and #8: `_watcher_map` is gone, and a status read no longer reaches `Identifier` at all.
- #9 and #10 (`flash.py`) both get the `find` `                or want.lower() in {i.lower() for i in (d.configured_id, d.reported_id) if i}`. #9's `replace` swaps the tuple for `(d.reported_id,)`, and #10's for `(d.configured_id,)`.

`scripts/mutations/targets.json`:

| # | New `find` | New `replace` |
|---|---|---|
| 7 | `        if device.get("compatible") is False:\n            return DeviceStatus(PROTOCOL_MISMATCH)` | `        if False:\n            return DeviceStatus(PROTOCOL_MISMATCH)` |
| 8 | `        if not device["present"]:\n            return DeviceStatus(OFFLINE)` | `        if False:\n            return DeviceStatus(OFFLINE)` |
| 11 | `                            {"name": name, "port": device["id"]},` | `                            {"name": name, "port": device["configured_path"]},` |

Add a core mutation to `scripts/mutations/device-listing.json`, and add `tests/test_agent_platformio_devices.py` and `tests/test_agent_targets.py` to its `command`:

```json
    {
      "name": "a query Klipper cannot answer is unreachable, not an empty list",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        if not isinstance(status, dict):\n            return {}, False",
      "replace": "        if not isinstance(status, dict):\n            return {}, True"
    }
```

Run the hygiene test: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_repo_hygiene.py -q`
Expected: PASS. If it names another anchor, grep for it and re-anchor that spec as well.

Then run each touched spec, one at a time, with Bash `timeout: 600000`:

```bash
PY=../../.venv/Scripts/python.exe
$PY scripts/mutation_test.py scripts/mutations/device-listing.json
$PY scripts/mutation_test.py scripts/mutations/targets.json
$PY scripts/mutation_test.py scripts/mutations/identity.json
$PY scripts/mutation_test.py scripts/mutations/display-flash.json
$PY -m pytest tests/test_repo_hygiene.py -q
```

Expected: every mutation killed; hygiene PASS. A surviving mutation means a test is missing. Add it to the test file named in that spec's `command`; do not weaken the mutation.

- [ ] **Step 11: Gate and commit**

Run the full gate. Expected: all green. `ruff` flags any import left unused (for example `null_reporter`, or `secrets` if it is no longer used; `secrets` stays until Task 6).

```bash
git add -A src tests scripts
git commit -m "refactor(agent): list platformio devices through the helper's DeviceLister, and remove fw.device.list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Every row carries `source`, `extras` and `devices_note`; `extra` is gone

After Task 3 only the platformio row has the three uniform keys. This task gives them to kconfig_make and cmake rows and removes cmake's `extra`, so no row carries a per-builder bag.

**Files:**
- Modify: `src/mcu_updater/agent/methods/status.py`
  - `type_status` payload (~line 398)
  - `_mcu_target` return (~line 934)
  - `_cmake_target` return (~line 1260)
  - a module constant
- Test: `tests/test_agent_targets.py`
- Modify: `scripts/mutations/targets.json`

**Interfaces:**
- Consumes: `status._source_json` (Task 3).
- Produces:
  - Every `targets[]` row has `source: {path, version, dirty} | None`, `extras: list[dict]` and `devices_note: str | None`, and none has `extra`.
  - `type_status()` gains `"source"`, which `fw.type.list` carries (an additive change).
  - `status.NO_TRACKED_DEVICES`, the MCU and cmake note.

- [ ] **Step 1: Write the failing tests**

In `tests/test_agent_targets.py`:

- Add `"source"`, `"extras"` and `"devices_note"` to the key set in `test_an_mcu_type_projects_onto_the_shared_shape`.
- In `test_a_display_projects_onto_the_same_shape`, change the assertion Task 3 wrote to `assert set(display) == mcu_keys`. Every row now has the same keys, so the comment above it becomes `# One shape: every key an MCU row has, and nothing it lacks.`
- In `test_every_fact_in_the_old_keys_survives_the_projection`'s `for legacy in legacy_types:` loop, add `assert target["source"] == legacy["source"]`.
- Replace the four `row["extra"]["flashable"]` assertions:

| Test | Replacement |
|---|---|
| `test_a_cmake_type_gets_a_row_in_targets` | `assert row["devices_note"] == "No serial devices are tracked for this type yet."` |
| `test_a_helper_backed_cmake_type_projects_real_serial_devices` | `assert "flash" in _ids(row["devices"][0])` |
| `test_a_cmake_type_without_a_helper_does_not_advertise_device_actions` | delete the line; the `actions == []` assertion after it already says it |
| `test_a_misspelled_helper_blocks_its_own_row_not_the_whole_panel` | delete the line and its comment; the blocked-flash assertions after it already say it |

Then append these tests:

```python
# --------------------------------------------------------------------------
# the uniform keys
# --------------------------------------------------------------------------

_SCALAR = (str, int, float, bool, type(None))


def _assert_uniform(row):
    from mcu_updater.extras import SEAMS

    assert {"source", "extras", "devices_note"} <= set(row), row["name"]
    assert "extra" not in row, row["name"]
    # Set exactly when there is nothing to list, so a reader never has to
    # decide which of the two to believe.
    assert (row["devices_note"] is None) == bool(row["devices"]), row["name"]
    for entry in row["extras"]:
        assert set(entry) == {"seam", "name", "key", "label", "value"}
        assert entry["seam"] in SEAMS
        assert isinstance(entry["value"], _SCALAR)
    if row["source"] is not None:
        assert set(row["source"]) == {"path", "version", "dirty"}
        assert row["source"]["version"]


def test_every_kconfig_and_platformio_row_has_the_uniform_keys(api, paths, fake_root):
    _add_display(paths, fake_root, api)

    rows = _targets(api)

    assert {r["provider"] for r in rows.values()} == {"kconfig_make", "platformio"}
    for row in rows.values():
        _assert_uniform(row)


@pytest.mark.parametrize("serial", [None, "RR-5K3DNTFCR1B3C9D0RZMYA3Y720"])
def test_every_cmake_row_has_the_uniform_keys(paths, tmp_path, serial):
    _cmake_config(paths, tmp_path, helper=True, serial=serial)

    _assert_uniform(_targets(Api(paths), "cmake")["roadrunner"])


def test_an_mcu_rows_source_is_its_application_tree_not_its_bootloaders(
    api, paths, monkeypatch
):
    """The tree whose commit a board is judged against. A katapult checkout
    beside it would be a second, wrong answer."""
    monkeypatch.setattr("mcu_updater.build.git_head", lambda d, **_: f"head:{d}")
    families = firmware.load(paths)
    klipper = firmware.resolve(paths, "klipper", families).source_dir(paths)

    row = _targets(api)["bttebb36"]

    assert row["source"] == {
        "path": os.path.expanduser(klipper),
        "version": f"head:{klipper}",
        "dirty": None,
    }


def test_an_mcu_row_with_no_checkout_has_no_source(api, monkeypatch):
    monkeypatch.setattr("mcu_updater.build.git_head", lambda d, **_: None)

    assert _targets(api)["bttebb36"]["source"] is None


def test_a_cmake_rows_source_is_what_its_staleness_check_compares(
    paths, tmp_path, monkeypatch
):
    source = _cmake_config(paths, tmp_path)
    monkeypatch.setattr(
        cmake,
        "source_state",
        lambda _src: cmake.SourceState(sha="deadbee", dirty=True, version="v1.0-2-gdeadbee-dirty"),
    )

    row = _targets(Api(paths), "cmake")["roadrunner"]

    assert row["source"] == {
        "path": str(source),
        "version": "v1.0-2-gdeadbee-dirty",
        "dirty": True,
    }


def test_a_tracked_type_has_no_devices_note(api):
    assert _targets(api)["bttebb36"]["devices_note"] is None
```

`bttebb36` has a tracked serial in `live_registry_text`, which is why the last test reads it. If the MCU source test finds `firmware.resolve` wants different arguments, use `grep -n "def resolve" src/mcu_updater/firmware.py` and match `type_status`'s own call, `firmware.resolve(self.paths, application, families)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_agent_targets.py -q -x -k "uniform or source or devices_note or shared_shape or same_shape or cmake"`
Expected: FAIL on the first new assertion, with `source` missing from the MCU row's keys.

- [ ] **Step 3: Implement**

Add this to `status.py`, next to `_source_json`:

```python
#: What a kconfig_make or cmake row with no devices says. A PlatformIO row's
#: comes from its family's `DeviceLister`, which knows where its devices live.
NO_TRACKED_DEVICES = "No serial devices are tracked for this type yet."
```

In `type_status`'s `out` dict, right after `"firmware": application,` (leave that line exactly as it is; `targets.json` anchors it):

```python
            # The tree the application builds from, at the HEAD its staleness
            # check compares. `dirty` is None: this builder never asks.
            "source": _source_json(family.source_dir(self.paths), fw_head, None),
```

In `_mcu_target`'s return dict, after `"actions": actions,`:

```python
            "source": payload["source"],
            # No kconfig_make seam contributes any yet.
            "extras": [],
            "devices_note": None if devices else NO_TRACKED_DEVICES,
```

In `_cmake_target`'s return dict, replace the whole `"extra": {...}` block with:

```python
            "source": _source_json(
                payload["source"], payload["source_version"], payload["source_dirty"]
            ),
            # No cmake seam contributes any yet. `flashable` went with `extra`:
            # the device's flash action already says it, blocked or not.
            "extras": [],
            "devices_note": None if devices else NO_TRACKED_DEVICES,
```

`helper_configured` is still used above the return, so keep it.

- [ ] **Step 4: Run the tests**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_agent_targets.py tests/test_agent_methods.py tests/test_agent_bootsel.py -q`
Expected: PASS.

- [ ] **Step 5: Guard the note with a mutation**

Append to `scripts/mutations/targets.json`'s `mutations`. The first occurrence of this line is `_mcu_target`'s, which is the one it mutates:

```json
    {
      "name": "a row with devices carries no devices_note",
      "find": "            \"devices_note\": None if devices else NO_TRACKED_DEVICES,",
      "replace": "            \"devices_note\": NO_TRACKED_DEVICES,"
    }
```

Run: `PY=../../.venv/Scripts/python.exe; $PY scripts/mutation_test.py scripts/mutations/targets.json && $PY -m pytest tests/test_repo_hygiene.py -q` (Bash `timeout: 600000`)
Expected: every mutation killed, then hygiene PASS.

- [ ] **Step 6: Gate and commit**

Run the full gate. Expected: green.

```bash
git add src tests scripts
git commit -m "refactor(agent): give every targets row source, extras and devices_note, and drop extra

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The wire drops its intermediates; API_VERSION 5

`fw.flash_all` and the PlatformIO `fw.flash` answer with the job alone. PlatformIO jobs use the kinds the rest of the agent uses. Two new contract tests fail on any job kind the UI does not know. `API_VERSION` and `SUPPORTED_API_VERSION` both go to 5 in this commit.

**Files:**
- Modify: `src/mcu_updater/agent/methods/bulk.py` (`_platformio_json` 50-61, `flash_all` return ~627-637)
- Modify: `src/mcu_updater/agent/methods/flash.py` (`_pio_flash` submit and return)
- Modify: `src/mcu_updater/agent/methods/build.py` (the `display_build` submit, ~183)
- Modify: `src/mcu_updater/agent/methods/status.py` (`target_get`'s platformio error message; `platformio_status`'s `**entry.to_json()` spread)
- Modify: `src/mcu_updater/providers/pio.py` (delete `PioType.to_json` and `_compat_service`, 103-128)
- Modify: `src/mcu_updater/__init__.py` (`API_VERSION` and its history comment)
- Modify: `ui/src/api/jobs.ts`, `ui/src/api/agent.ts`
- Test: `tests/test_ui_contract.py`, `tests/test_agent_bulk.py`, `tests/test_agent_platformio_flash.py`, `tests/test_agent_methods.py`

**Interfaces:**
- Consumes: `Api._platformio_to_flash` (Task 3).
- Produces:
  - `fw.flash_all` returns `{job_id, job}`, and so does the PlatformIO `fw.flash`.
  - A PlatformIO flash job has kind `"flash"` and params `{"name": name}`, plus `"port": <the address the call named>` when it named one.
  - A PlatformIO build job has kind `"build"` and params `{"name": name, "fw": entry.firmware}`.
  - `API_VERSION == 5`, and the UI's `SUPPORTED_API_VERSION` is 5.
  - The UI's `JobKind` gains `"profile_apply"`, and `DEFERRED_CANCEL_KINDS` includes it.

- [ ] **Step 1: Install the UI toolchain**

Run: `cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm ci`
Expected: completes without errors.

- [ ] **Step 2: Write the failing contract tests**

Append to `tests/test_ui_contract.py`:

```python
SRC = REPO_ROOT / "src"

_SUBMIT_RE = re.compile(r"""runner\.submit\(\s*["']([a-z_]+)["']""")
_JOB_KIND_RE = re.compile(r"export type JobKind\s*=([^;]+);")
_DEFERRED_RE = re.compile(r"DEFERRED_CANCEL_KINDS[^=]*=\s*new Set\(\[([^\]]*)\]")
_QUOTED_RE = re.compile(r"""["']([a-z_]+)["']""")


def _agent_job_kinds() -> set[str]:
    kinds: set[str] = set()
    for path in SRC.rglob("*.py"):
        kinds |= set(_SUBMIT_RE.findall(path.read_text(encoding="utf-8")))
    return kinds


def _jobs_ts() -> str:
    return (UI_SRC / "api" / "jobs.ts").read_text(encoding="utf-8")


def _ui_job_kinds() -> set[str]:
    match = _JOB_KIND_RE.search(_jobs_ts())
    assert match is not None, "ui/src/api/jobs.ts must define JobKind"
    return set(_QUOTED_RE.findall(match.group(1)))


def _ui_deferred_kinds() -> set[str]:
    match = _DEFERRED_RE.search(_jobs_ts())
    assert match is not None, "ui/src/api/jobs.ts must define DEFERRED_CANCEL_KINDS"
    return set(_QUOTED_RE.findall(match.group(1)))


def test_every_job_kind_the_agent_submits_is_one_the_ui_names():
    """A kind the UI has never heard of falls through `cancelIsImmediate` as
    immediately cancellable. `display_flash` did exactly that, telling the user
    a flash would stop mid-write when the agent was correctly deferring it."""
    agent = _agent_job_kinds()
    assert agent, "the runner.submit scan found nothing; the pattern has rotted"
    assert agent <= _ui_job_kinds(), sorted(agent - _ui_job_kinds())


def test_the_ui_defers_exactly_the_kinds_the_agent_defers():
    from mcu_updater.jobs import IMMEDIATELY_CANCELLABLE

    assert _ui_deferred_kinds() == _ui_job_kinds() - set(IMMEDIATELY_CANCELLABLE)
```

- [ ] **Step 3: Write the failing wire tests**

In `tests/test_agent_bulk.py`:
- `test_a_batch_stops_klipper_once_not_once_per_board`: replace `assert len(res["boards"]) == 2` with `assert set(res) == {"job_id", "job"}`. The `len(job.result["flashed"]) == 2` below it still counts the boards.
- `test_a_board_its_family_cannot_write_is_a_failure_not_an_abort`: delete `assert [b["serial"] for b in res["boards"]] == [EBB_A]`. Replace `assert job.params["count"] == len(res["boards"])` with:
  ```python
      assert job.params["count"] == 1
      assert [f["id"] for f in job.result["failures"]] == [EBB_A]
  ```
- `test_flash_all_selects_cmake_boards_beside_the_others`: replace the two `res["boards"]` assertions with
  ```python
      selected = bulk._cmake_boards_to_flash("all", None)
      assert (RR, RR_SERIAL) in [(b["type"], b["serial"]) for b in selected]
  ```
- `test_a_cmake_type_is_no_longer_refused_by_name`: replace its assertion with `assert [b["serial"] for b in bulk._cmake_boards_to_flash("all", RR)] == [RR_SERIAL]`.

In `tests/test_agent_platformio_flash.py`:
- `test_one_screen_can_be_singled_out`: delete `assert len(res["displays"]) == 1`.
- The three tests that assert `[d["configured_path"] for d in res["displays"]]`: add the `no_pio` fixture to any that lacks it. Then assert on what the job wrote, after it finishes:
  ```python
      assert api.runner.wait(timeout=30)
      job = api.runner.get(res["job_id"])
      assert [f["id"] for f in job.result["flashed"]] == [str(port)]
  ```
  In the loop test (`for slot in ("id", "port")`), the expected id is `screens_port(screens, "t0")`, and it already waits.
- `test_flash_all_selects_screens_beside_boards`: replace the three `res["displays"]` assertions with
  ```python
      selected, _refused = api._platformio_to_flash("all")
      assert [t.to_json()["flasher"] for t in selected] == ["platformio", "platformio"]
      assert {t.to_json()["id"] for t in selected} == {
          screens_port(screens, "t0"),
          screens_port(screens, "t1"),
      }
      assert all(t.detail["reason"] == "forced" for t in selected)
  ```
- Append:
  ```python
  def test_a_platformio_flash_answers_with_the_job_alone(api, no_pio, screens):
      port = screens["knomi_serial t0_knomi"]["serial"]

      res = api.dispatch("fw.flash", {"name": ENV, "port": port})

      assert set(res) == {"job_id", "job"}
      assert res["job"]["kind"] == "flash"
      assert res["job"]["params"] == {"name": ENV, "port": port}
      assert api.runner.wait(timeout=30)


  def test_a_whole_type_flash_names_no_port(api, no_pio, screens):
      res = api.dispatch("fw.flash", {"name": ENV})

      assert res["job"]["params"] == {"name": ENV}
      assert api.runner.wait(timeout=30)


  def test_a_platformio_flash_cancels_only_between_devices(api, no_pio, screens):
      """The bug this kind rename fixes: the panel told the user this job
      would stop at once, mid-write."""
      from mcu_updater.jobs import IMMEDIATELY_CANCELLABLE

      res = api.dispatch("fw.flash", {"name": ENV})

      assert res["job"]["kind"] not in IMMEDIATELY_CANCELLABLE
      api.runner.wait(timeout=30)


  def test_a_platformio_build_is_a_build_job(api, paths, fake_root, no_pio):
      _built(api, paths, fake_root)

      res = api.dispatch("fw.build", {"name": ENV})

      assert res["job"]["kind"] == "build"
      assert res["job"]["params"] == {"name": ENV, "fw": "knomi_serial"}
      api.runner.wait(timeout=30)
  ```
  The cancel test reads the kind rather than cancelling. With `no_pio` the job may finish before a cancel lands, and a finished job's answer has no `immediate` in it.

In `tests/test_agent_methods.py`, after `test_target_get_returns_the_same_detail_as_status_for_a_platformio_type` (renamed in Task 3):

```python
def test_target_get_echoes_no_firmware_specific_config_for_a_platformio_type(api):
    """`klipper_section` and `device_map` are the helper's own business since
    `DeviceLister` (a knomi prefix, a knomi watcher's map), and `service` is a
    compatibility echo of the retired key that `stop_services` replaced.
    Nothing reads any of them; version 5 removes them rather than leaving a
    second bump for later."""
    from_status = next(t for t in api.dispatch("fw.status")["targets"] if t["provider"] == "platformio")
    res = api.dispatch("fw.target.get", {"name": from_status["name"], "provider": "platformio"})

    assert not {"klipper_section", "device_map", "service"} & set(res["target"])
    assert {"name", "env", "firmware", "stop_services", "source", "devices"} <= set(res["target"])
```

- [ ] **Step 4: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_ui_contract.py tests/test_agent_bulk.py tests/test_agent_platformio_flash.py tests/test_agent_methods.py -q`
Expected: FAIL. The contract test names `display_build`, `display_flash` and `profile_apply`, the new wire tests see `displays` and `boards` still in the responses, and `fw.target.get` still carries `klipper_section`, `device_map` and `service`.

- [ ] **Step 5: Implement the agent half**

`bulk.py`: delete `_platformio_json`. In `flash_all`, return only the job:

```python
        return {"job_id": job.id, "job": job.to_dict()}
```

What the batch will write is the job's business now: its `flashed[]` and `failures[]` already use the uniform `{type, id, flasher}` shape.

`flash.py` `_pio_flash`: replace the submit and the return with

```python
        # The same kind the board routes use, so a client already knows it
        # cancels between devices and never inside one.
        params: dict[str, Any] = {"name": name}
        if want is not None:
            params["port"] = want
        job = runner.submit("flash", params, run)
        return {"job_id": job.id, "job": job.to_dict()}
```

`build.py` (~183): replace `job = runner.submit("display_build", {"name": name}, run)` with

```python
        # Kind `build`, as the cmake route: a compile like any other, and so
        # immediately cancellable (`pio_mod.build` takes `cancel=ctx.cancel`).
        job = runner.submit("build", {"name": name, "fw": display.firmware}, run)
```

(`display` becomes `entry` in Task 9. Leave the name alone here.)

`status.py` `platformio_status`: replace the `**entry.to_json(),` line (Task 3) with the keys the wire keeps. `"source"` is already set further down, from `_source_json`:

```python
                    "name": entry.name,
                    "env": entry.env,
                    "firmware": entry.firmware,
                    "stop_services": entry.stop_services,
```

`providers/pio.py`: delete `PioType.to_json` and `_compat_service`: `platformio_status` was their only caller. `PioType.klipper_section` and `PioType.device_map` stay: the helper and `Identifier` still read them.

`status.py` `target_get`: change the platformio branch's `f"no such display: {name}"` to `f"no such platformio type: {name}"`. Keep the error code `unknown_target`. This task adds no test for it: an unknown name is refused earlier, by `_provider_of`, so the branch is reachable only when a type disappears between two reads. Task 9's vocabulary guard is what keeps the old word out.

`src/mcu_updater/__init__.py`: set `API_VERSION = 5` and add this entry under `# 4: ...`:

```python
# 5: `targets[].extra` is gone - `source`, `extras` and `devices_note` replace
#    it on every row, and a platformio row names its `firmware`. `fw.device.list`
#    is gone. `fw.flash_all` and the PlatformIO `fw.flash` answer `{job_id, job}`
#    only; PlatformIO jobs are kinds `flash` and `build`. `fw.target.get` names
#    a PlatformIO type's `devices`, and no longer echoes `klipper_section`,
#    `device_map` or the retired `service`.
```

Task 6 adds the identity renames to this entry.

- [ ] **Step 6: Implement the UI half**

`ui/src/api/agent.ts`: `export const SUPPORTED_API_VERSION = 5;`

`ui/src/api/jobs.ts`:

```ts
export type JobKind =
  | "build"
  | "build_all"
  | "flash"
  | "flash_all"
  | "update_all"
  | "add_mcu"
  | "profile_apply";
```

Add `"profile_apply"` to `DEFERRED_CANCEL_KINDS`, and to its doc comment add: "`profile_apply` is here because the agent does not list it as immediately cancellable, and `tests/test_ui_contract.py` holds this set to exactly that."

- [ ] **Step 7: Run the tests**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_ui_contract.py tests/test_agent_bulk.py tests/test_agent_platformio_flash.py tests/test_agent_methods.py -q`
Expected: PASS.

Run: `cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm run typecheck && npm test && npm run build && npm run lint`
Expected: all pass. A spec that asserts `apiVersion` 4 is refused, or that pins `JobKind`'s members, needs its expected value moved to the new one.

- [ ] **Step 8: Check the mutation specs**

Run: `git grep -n "display_build\|display_flash\|_platformio_json\|\"boards\"\|_compat_service\|def to_json" -- scripts/mutations`
Expected: no output. If a spec anchors one of the lines this task removed, re-anchor it per the ground rule, then run that spec alone and the hygiene test.

- [ ] **Step 9: Gate and commit**

Run the full gate and the UI gate. Expected: green.

```bash
git add src tests scripts ui/src
git commit -m "feat(api): drop the intermediate wire outputs and platformio config echoes, name platformio jobs flash and build, and bump api_version to 5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `fw.identity.*`, routed through the helper that claims the serial

`fw.roadrunner.provision`/`.clear` become `fw.identity.provision`/`.clear`. The core no longer knows which firmware has identities: it asks every registered `Provisioner` whether a serial is its identity, and in which state. Every `roadrunner_*` wire code gets a generic name. This goes in the same API version as Task 5 and before any release, so it costs no second bump.

**Files:**
- Modify: `src/mcu_updater/helpers/spec.py` (`Provisioner`, ~101-119)
- Modify: `src/mcu_updater/helpers/registry.py`, `src/mcu_updater/helpers/__init__.py` (`all_helpers`)
- Modify: `src/mcu_updater/helpers/roadrunner.py` (`identity_state`, `clear`, and the `is_trackable` reason text)
- Modify: `src/mcu_updater/agent/methods/status.py`
  - roadrunner methods (1682-1727)
  - METHODS (2000-2001)
  - HARDWARE_METHODS (2082-2085)
  - `import secrets`
- Modify: `src/mcu_updater/discovery/roadrunner.py` (every `roadrunner_*` code, and `_TRANSIENT_READINESS_CODES`)
- Modify: `src/mcu_updater/errors.py:84-90` (`UnprovisionedSerialError.code`)
- Modify: `src/mcu_updater/agent/methods/registry.py:245,276`, `src/mcu_updater/tracking.py:113` (the code named in comments)
- Modify: `src/mcu_updater/__init__.py` (the version 5 history entry)
- Modify: `ui/src/store/agent.ts` (657-690), `ui/src/components/BusPanel.vue`, `ui/src/api/targets.ts:172`, `ui/src/store/agent.spec.ts`, `ui/src/components/BusPanel.spec.ts`
- Create: `tests/test_identity_provisioning.py`
- Modify: `tests/test_roadrunner.py`, `tests/test_agent_methods.py`, `tests/test_auto_provision.py`, `tests/test_cli.py`, `tests/test_provision_on_track.py`
- Create: `scripts/mutations/identity-routing.json`

**Interfaces:**
- Consumes: nothing from Tasks 1-5 beyond the version entry Task 5 wrote.
- Produces:
  - `Provisioner.identity_state(serial: str) -> Literal["unprovisioned", "provisioned"] | None`
  - `Provisioner.clear(paths: Paths, serial: str) -> str`
  - `helpers.all_helpers() -> tuple[Helper, ...]`, which reads `registry._BY_NAME` so a test's stand-in counts
  - `Api.identity_provision(args)` and `Api.identity_clear(args)`, dispatched as `fw.identity.provision` and `fw.identity.clear`
  - `Api._identity_claimant(serial: str, want: str) -> Provisioner`
  - `Api._identity_untracked(serial: str) -> None`
  - the codes:

| Old | New |
|---|---|
| `roadrunner_tracked` | `device_tracked` |
| `roadrunner_no_candidate` | `device_not_found` |
| `roadrunner_ambiguous` | `device_ambiguous` |
| `roadrunner_invalid_probe` | `identity_unconfirmed` |
| `roadrunner_helper` | `helper_failed` |
| `roadrunner_timeout` | `reenumerate_timeout` |
| `roadrunner_mismatch` | `identity_mismatch` |
| `roadrunner_unprovisioned` | `serial_unprovisioned` |
| (new) | `not_provisionable` |

  - UI: `provisionIdentity(serial)` and `clearIdentity(serial)` in `ui/src/store/agent.ts`

- [ ] **Step 1: Write the failing routing tests**

Create `tests/test_identity_provisioning.py`:

```python
"""fw.identity.* - an identity write routed to the one helper that claims it.

The call names no family: the board is untracked, usually before any type for
it exists. So the core asks every registered `Provisioner` whether the serial
is its identity, and in which state, and needs exactly one yes. These tests
run against stand-ins registered beside the real helpers; nothing here opens a
port.
"""

from __future__ import annotations

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.helpers import registry as helpers_registry
from mcu_updater.jobs import JobRunner
from mcu_updater.settings import load_settings

from .conftest import write_settings

UNPROVISIONED = "FAKE-UNPROVISIONED-0001"
PROVISIONED = "FAKE-0001"


class FakeProvisioner:
    def __init__(self, name: str = "fake_identity") -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []

    def identity_state(self, serial):
        if serial.startswith("FAKE-UNPROVISIONED-"):
            return "unprovisioned"
        if serial.startswith("FAKE-"):
            return "provisioned"
        return None

    def provision(self, paths, serial):
        self.calls.append(("provision", serial))
        return PROVISIONED

    def clear(self, paths, serial):
        self.calls.append(("clear", serial))
        return UNPROVISIONED


@pytest.fixture
def api(paths):
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    return Api(paths, runner=JobRunner(paths, lambda: load_settings(paths.settings_file)))


@pytest.fixture
def fake(monkeypatch):
    prov = FakeProvisioner()
    monkeypatch.setitem(helpers_registry._BY_NAME, prov.name, prov)
    return prov


def _refusal(api, method, serial):
    with pytest.raises(RpcError) as exc:
        api.dispatch(method, {"serial": serial})
    return exc.value.data


def test_provision_goes_to_the_helper_that_claims_the_serial(api, fake):
    res = api.dispatch("fw.identity.provision", {"serial": UNPROVISIONED})

    assert res == {"serial": PROVISIONED, "prior_serial": UNPROVISIONED, "state": "provisioned"}
    assert fake.calls == [("provision", UNPROVISIONED)]


def test_clear_goes_to_the_helper_that_claims_the_serial(api, fake):
    res = api.dispatch("fw.identity.clear", {"serial": PROVISIONED})

    assert res == {"serial": UNPROVISIONED, "prior_serial": PROVISIONED, "state": "unprovisioned"}
    assert fake.calls == [("clear", PROVISIONED)]


def test_a_serial_no_helper_claims_is_not_provisionable(api, fake):
    data = _refusal(api, "fw.identity.provision", "E6614103E7452D2F")

    assert data["code"] == "not_provisionable"
    assert data["data"] == {"serial": "E6614103E7452D2F", "helpers": []}
    assert fake.calls == []


def test_two_helpers_claiming_one_serial_write_nothing(api, fake, monkeypatch):
    """Two firmwares both answering "mine" is a naming collision. Picking one
    would be a coin toss over an irreversible write."""
    other = FakeProvisioner("fake_identity_2")
    monkeypatch.setitem(helpers_registry._BY_NAME, other.name, other)

    data = _refusal(api, "fw.identity.provision", UNPROVISIONED)

    assert data["code"] == "not_provisionable"
    assert data["data"]["helpers"] == ["fake_identity", "fake_identity_2"]
    assert fake.calls == other.calls == []


def test_provisioning_an_already_provisioned_serial_is_refused(api, fake):
    data = _refusal(api, "fw.identity.provision", PROVISIONED)

    assert data["code"] == "not_provisionable"
    assert data["data"]["helpers"] == ["fake_identity"]
    assert fake.calls == []


def test_clearing_an_unprovisioned_serial_is_refused(api, fake):
    data = _refusal(api, "fw.identity.clear", UNPROVISIONED)

    assert data["code"] == "not_provisionable"
    assert fake.calls == []


def _track(paths, serial):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(
            "[firmware fakefw]\nsource: ~/fakefw\nbuilder: cmake\nflashers: bootsel\n"
            f"\n[type faketype]\nchipset: rp2040\nfirmware: fakefw\nserials:\n    {serial}\n"
        )


@pytest.mark.parametrize(
    ("method", "serial"),
    [("fw.identity.provision", UNPROVISIONED), ("fw.identity.clear", PROVISIONED)],
)
def test_a_tracked_serial_is_refused_before_anything_is_written(api, fake, paths, method, serial):
    _track(paths, serial)

    data = _refusal(api, method, serial)

    assert data["code"] == "device_tracked"
    assert data["data"]["tracked_under"] == ["faketype"]
    assert fake.calls == []


def test_the_old_method_names_are_gone(api):
    capabilities = api.dispatch("fw.ping")["capabilities"]

    assert "fw.identity.provision" in capabilities
    assert "fw.identity.clear" in capabilities
    assert not [m for m in capabilities if m.startswith("fw.roadrunner.")]


def test_no_wire_code_is_named_for_a_firmware():
    """Machine-readable names are generic; only human text may name a firmware."""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    named = [
        f"{path.name}: {match}"
        for path in src.rglob("*.py")
        for match in re.findall(r"""["'](roadrunner_[a-z_]+)["']""", path.read_text(encoding="utf-8"))
    ]
    assert named == []
```

`_track` writes the registry exactly the way `test_roadrunner.py:821`'s `test_agent_refuses_maintenance_for_a_cmake_tracked_roadrunner` does, with only the names changed. The untracked check reads `Registry.find_declared_types_for_serial` (`config.py:544`), which lists sections and resolves no helper.

- [ ] **Step 2: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_identity_provisioning.py -q`
Expected: FAIL. The dispatch answers `-32601` for `fw.identity.provision`, and the code scan lists every `roadrunner_*` literal. At planning time every such literal was a wire code in the rename table, or a `METHODS` value (`roadrunner_provision`, `roadrunner_clear`) that Step 4 replaces.

The real `RoadrunnerHelper` stays registered beside the fake. That is safe because its patterns (`discovery/roadrunner.py:24-25`, `^RR-UNPROVISIONED-[0-9A-F]{16}$` and `^RR-[0-9A-HJKMNP-TV-Z]{26}$`) match neither `FAKE-*` serial nor `E6614103E7452D2F`, so it never becomes a third claimant.

- [ ] **Step 3: Extend the capability and the registry**

`helpers/spec.py`: add `Literal` to the `typing` import, and add both methods to `Provisioner`. Its docstring gains a paragraph:

```python
    """...(existing text)...

    Giving an identity and taking it back are one firmware feature, so they are
    one capability. `identity_state` is how a caller that names no family finds
    the helper to ask: a string test, like `is_trackable`, that never opens a
    port.
    """

    name: str

    def identity_state(self, serial: str) -> Literal["unprovisioned", "provisioned"] | None:
        """Whether `serial` is this firmware's identity, and in which state."""
        ...

    def provision(self, paths: Paths, serial: str) -> str:
        """Provision the board answering to `serial`; return its new serial."""
        ...

    def clear(self, paths: Paths, serial: str) -> str:
        """Return the board answering to `serial` to its unprovisioned
        identity; return the serial it came back under."""
        ...
```

`helpers/registry.py`, after `_BY_NAME`:

```python
def all_helpers() -> tuple[Helper, ...]:
    """Every registered helper.

    Read from `_BY_NAME` rather than `HELPERS` so that a stand-in a test
    registers is asked too - the same table `for_name` resolves from.
    """
    return tuple(_BY_NAME.values())
```

`helpers/__init__.py`, beside `for_name`, and add it to `__all__`:

```python
def all_helpers() -> tuple[Helper, ...]:
    """Every registered helper, for a caller that names no family."""
    from .registry import all_helpers as every

    return every()
```

`helpers/roadrunner.py`: add `Literal` to the typing import, and add to `RoadrunnerHelper`:

```python
    def identity_state(self, serial: str) -> Literal["unprovisioned", "provisioned"] | None:
        from ..discovery.roadrunner import PROVISIONED_RE, UNPROVISIONED_RE

        if UNPROVISIONED_RE.fullmatch(serial):
            return "unprovisioned"
        if PROVISIONED_RE.fullmatch(serial):
            return "provisioned"
        return None

    def clear(self, paths: Paths, serial: str) -> str:
        """Return this board to its diagnostic identity. The caller holds the op lock."""
        from ..discovery import roadrunner

        device = roadrunner.find_provisioned(paths, serial)
        return roadrunner.clear_roadrunner(paths, device).serial
```

In `is_trackable`'s reason, change `fw.roadrunner.provision` to `fw.identity.provision`.

- [ ] **Step 4: Route the agent methods through it**

In `status.py`, replace `_roadrunner_refusal`, `_roadrunner_untracked`, `roadrunner_provision` and `roadrunner_clear` (1682-1727) with:

```python
    def _identity_claimant(self, serial: str, want: str) -> Provisioner:
        """The one helper that calls `serial` its identity, in state `want`.

        Every registered provisioner is asked, and asking is a string test.
        None, or more than one, refuses: an irreversible write goes to a
        helper that is unambiguously the owner, or nowhere.
        """
        provisioners = [
            prov
            for prov in (helpers.provisioner(h) for h in helpers.all_helpers())
            if prov is not None
        ]
        states = {prov.name: prov.identity_state(serial) for prov in provisioners}
        claimants = [prov for prov in provisioners if states[prov.name] == want]
        if len(claimants) != 1:
            verb = "provision" if want == "unprovisioned" else "clear"
            raise RpcError(
                f"No single firmware can {verb} '{serial}'.",
                data={
                    "code": "not_provisionable",
                    "message": f"exactly one helper must be able to {verb} this serial",
                    "data": {
                        "serial": serial,
                        "helpers": sorted(n for n, s in states.items() if s is not None),
                    },
                },
            )
        return claimants[0]

    def _identity_untracked(self, serial: str) -> None:
        owners = self.registry().find_declared_types_for_serial(serial)
        if owners:
            raise RpcError(
                f"'{serial}' is already tracked under '{owners[0]}'.",
                data={
                    "code": "device_tracked",
                    "message": "untrack the device before changing its identity",
                    "data": {"serial": serial, "tracked_under": owners},
                },
            )

    def identity_provision(self, args: dict) -> dict[str, Any]:
        """Give one confirmed, untracked board its durable identity."""
        serial = self._require_str(args, "serial")
        prov = self._identity_claimant(serial, "unprovisioned")
        self._identity_untracked(serial)
        try:
            # The lock covers selection, the irreversible write, and its
            # re-enumeration handoff. A timeout never retries the write.
            with exclusive(self.paths, f"provision {serial}"):
                provisioned = prov.provision(self.paths, serial)
        except UpdaterError as exc:
            raise RpcError(exc.message, data=exc.to_dict()) from exc
        self._changed()
        return {"serial": provisioned, "prior_serial": serial, "state": "provisioned"}

    def identity_clear(self, args: dict) -> dict[str, Any]:
        """Return one confirmed, untracked board to its unprovisioned identity."""
        serial = self._require_str(args, "serial")
        prov = self._identity_claimant(serial, "provisioned")
        self._identity_untracked(serial)
        try:
            with exclusive(self.paths, f"clear {serial}"):
                cleared = prov.clear(self.paths, serial)
        except UpdaterError as exc:
            raise RpcError(exc.message, data=exc.to_dict()) from exc
        self._changed()
        return {"serial": cleared, "prior_serial": serial, "state": "unprovisioned"}
```

- Add `Provisioner` to the `from ...helpers import ...` line.
- In METHODS: `"fw.identity.provision": "identity_provision",` and `"fw.identity.clear": "identity_clear",`.
- In HARDWARE_METHODS: the same two names.
- Delete `import secrets` if ruff reports it unused. The helper's `provision` makes its own token.

Rename the codes in `discovery/roadrunner.py` using the table: every `_error("roadrunner_...")`, the `code = "roadrunner_helper"` class attribute, `_TRANSIENT_READINESS_CODES`, and the docstrings that name them (221, 273-274). Keep the human messages: they are a firmware-specific seam's text, and may say "Roadrunner". In `errors.py`, `UnprovisionedSerialError.code = "serial_unprovisioned"`, and its docstring becomes `"""A helper refused a serial because it is not a durable identity."""`. In `agent/methods/registry.py:245,276` and `tracking.py:113`, name `serial_unprovisioned` and `fw.identity.provision` where they named the old ones.

Extend the version 5 entry in `src/mcu_updater/__init__.py`:

```python
#    `fw.roadrunner.provision`/`.clear` are `fw.identity.provision`/`.clear`,
#    routed to whichever helper claims the serial, and no error code names a
#    firmware any more (`roadrunner_unprovisioned` is `serial_unprovisioned`;
#    docs/agent-api.md has the full table).
```

- [ ] **Step 5: Move the existing tests to the new names**

Across `tests/test_roadrunner.py`, `tests/test_agent_methods.py` and `tests/test_provision_on_track.py`, apply the code table and `fw.roadrunner.*` → `fw.identity.*`, including in docstrings and test names (`test_roadrunner_methods_are_*` → `test_identity_methods_are_*`).

In `test_roadrunner.py`'s `test_agent_provisions_once_without_tracking`, the token now comes from the helper's own `import secrets`, so patch the module itself: `monkeypatch.setattr("secrets.token_bytes", lambda size: generated.append(size) or bytes(range(size)))`.

`tests/test_agent_methods.py:602`'s `test_serial_add_withholds_provisioning_from_a_runner_less_agent` is Review Focus 5. Rename it `test_serial_add_refuses_an_unprovisioned_serial_with_the_generic_code`. In its docstring, `fw.roadrunner.provision` becomes `fw.identity.provision`. Its code assertion becomes `== "serial_unprovisioned"`. After `assert helper.calls == []`, add:

```python
    with open(api.paths.main_config, encoding="utf-8") as fh:
        assert "RR-UNPROVISIONED-50543165187A4D1C" not in fh.read(), "nothing tracked"
```

Give every `Provisioner` stand-in the two new methods, or it stops satisfying the `runtime_checkable` protocol and silently stops being a provisioner. That means `_FakeRoadrunner` in `test_agent_methods.py` (~628) and `test_cli.py` (~1350), `_FakeRoadrunner` and `_ProvisionerOnly` in `test_auto_provision.py` (~48, ~61), and the stand-in in `test_provision_on_track.py` (~47):

```python
    def identity_state(self, serial):
        return "unprovisioned" if serial.startswith("RR-UNPROVISIONED-") else None

    def clear(self, paths, serial):
        raise AssertionError("nothing in this test clears an identity")
```

Run: `git grep -n "roadrunner_\(tracked\|no_candidate\|ambiguous\|invalid_probe\|helper\|timeout\|mismatch\|unprovisioned\)\|fw\.roadrunner" -- src tests`
Expected: no output.

- [ ] **Step 6: The UI half**

`ui/src/store/agent.ts`: rename `provisionRoadrunner` → `provisionIdentity` and `clearRoadrunner` → `clearIdentity`. They call `"fw.identity.provision"` and `"fw.identity.clear"`, and their doc comments name those methods. `ui/src/components/BusPanel.vue` imports and calls them under the new names, and checks `hasCapability("fw.identity.provision")` and `hasCapability("fw.identity.clear")`. It still picks rows with `isRoadrunnerDevice`: per-device actions from the helper are the README TODO this spec adds. `ui/src/api/targets.ts:172`: `fw.roadrunner.provision`/`.clear` → `fw.identity.provision`/`.clear`.

In `ui/src/store/agent.spec.ts` and `ui/src/components/BusPanel.spec.ts`, update the method strings, the function names and the codes (`roadrunner_no_candidate` → `device_not_found`, `roadrunner_tracked` → `device_tracked`).

Run: `git grep -n "fw\.roadrunner\|provisionRoadrunner\|clearRoadrunner\|roadrunner_no_candidate\|roadrunner_tracked" -- ui/src`
Expected: no output.

- [ ] **Step 7: Run the tests**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_identity_provisioning.py tests/test_roadrunner.py tests/test_agent_methods.py tests/test_auto_provision.py tests/test_cli.py tests/test_provision_on_track.py tests/test_ui_contract.py -q`
Expected: PASS.

Run: `cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm run typecheck && npm test && npm run lint`
Expected: PASS.

- [ ] **Step 8: Guard the routing with a mutation spec**

Create `scripts/mutations/identity-routing.json`:

```json
{
  "_comment": "fw.identity.* writes an identity only through the one helper that claims the serial, in the state the call needs, and never to a tracked board.",
  "file": "src/mcu_updater/agent/methods/status.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_identity_provisioning.py",
    "tests/test_roadrunner.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "two claimants refuse rather than picking one",
      "find": "        if len(claimants) != 1:",
      "replace": "        if not claimants:"
    },
    {
      "name": "a claimant must be in the state the call needs",
      "find": "        claimants = [prov for prov in provisioners if states[prov.name] == want]",
      "replace": "        claimants = [prov for prov in provisioners if states[prov.name] is not None]"
    },
    {
      "name": "a tracked board is refused before its identity is written",
      "find": "        self._identity_untracked(serial)",
      "replace": "        pass"
    }
  ]
}
```

No existing spec anchors a line this task rewrites. At planning time, `git grep -nE "roadrunner_|fw\.roadrunner|provision Roadrunner|token_bytes|_roadrunner_(refusal|untracked)" -- scripts/mutations/` found only `roadrunner-info-digest.json`, which anchors `scripts/roadrunner_usb.py`, a file this task does not touch. Run the grep again before editing. If it finds more, re-anchor those specs in this commit.

A mutation replaces the first occurrence, and `identity_provision` is defined before `identity_clear`, so the third mutation removes provision's check. The `fw.identity.provision` case of `test_a_tracked_serial_is_refused_before_anything_is_written` kills it.

Run: `PY=../../.venv/Scripts/python.exe; $PY scripts/mutation_test.py scripts/mutations/identity-routing.json && $PY -m pytest tests/test_repo_hygiene.py -q` (Bash `timeout: 600000`)
Expected: all three killed; hygiene PASS.

- [ ] **Step 9: Gate and commit**

Run the full gate and the UI gate. Expected: green.

```bash
git add src tests scripts ui/src
git commit -m "feat(api): route identity provisioning through the claiming helper as fw.identity.*, with generic error codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The PlatformIO build record moves to `data_dir/platformio/`

**Files:**
- Modify: `src/mcu_updater/paths.py:156-164`
- Test: `tests/test_paths.py:108-113`

**Interfaces:**
- Produces: `Paths.platformio_sidecar(env)` returns `<data_dir>/platformio/<env>.build.json`. There is no migration, so a record left in `displays/` reads as no record.

- [ ] **Step 1: Write the failing tests**

Replace `test_the_platformio_sidecar_keeps_its_on_disk_path` in `tests/test_paths.py` with:

```python
def test_the_platformio_sidecar_lives_under_platformio(paths):
    assert paths.platformio_sidecar("knomi") == os.path.join(
        paths.data_dir, "platformio", "knomi.build.json"
    )


def test_a_record_left_in_the_old_folder_is_no_provenance_not_an_error(paths, tmp_path):
    """No migration: one rebuild restores provenance, and until then the type
    says it cannot vouch for its image - never that it is current, and never
    an exception on the status poll."""
    from mcu_updater.providers import pio
    from mcu_updater.states import NO_PROVENANCE

    entry = pio.PioType(name="knomi", env="knomi", source=str(tmp_path / "src"), firmware="knomi_serial")
    image = pio.firmware_bin(entry)
    os.makedirs(os.path.dirname(image))
    with open(image, "wb") as fh:
        fh.write(b"\xe9" * 64)
    state = pio.SourceState(head="deadbee", version="v1.0-1-gdeadbee", dirty=False)
    pio.record_build(paths, entry, state)
    assert pio.artifact_status(paths, entry, state).reason is None

    old = os.path.join(paths.data_dir, "displays", "knomi.build.json")
    os.makedirs(os.path.dirname(old), exist_ok=True)
    os.replace(paths.platformio_sidecar("knomi"), old)

    assert pio.artifact_status(paths, entry, state).reason == NO_PROVENANCE
```

`tests/test_paths.py` uses the `paths` fixture from `tests/conftest.py:65`. `providers/pio.py` imports `NO_PROVENANCE` from `..states`, so the test imports it from there too.

- [ ] **Step 2: Run them to verify they fail**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_paths.py -q`
Expected: the path test FAILS on `displays`, and the old-folder test FAILS because the moved record is still found. Both are paths into the same directory until Step 3.

- [ ] **Step 3: Implement**

```python
    def platformio_sidecar(self, env: str) -> str:
        """Build provenance for one PlatformIO env: which commit the image is from.

        In our data tree even though the image itself lives in the source repo's
        `.pio/build/<env>/`. That directory is PlatformIO's, and writing our
        bookkeeping into it would put it in the path of `pio run -t clean` and
        into the user's git status.

        Not migrated from the folder it used to live in: one rebuild restores
        provenance, and until then the type reports `no_provenance`.
        """
        return os.path.join(self.data_dir, "platformio", f"{env}.build.json")
```

- [ ] **Step 4: Run the tests, gate, and commit**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_paths.py tests/test_agent_platformio_flash.py tests/test_agent_targets.py -q`
Expected: PASS.

Run the full gate. Expected: green.

```bash
git add src/mcu_updater/paths.py tests/test_paths.py
git commit -m "refactor(paths): keep platformio build records under data_dir/platformio

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The UI renders `source`, `extras` and `devices_note`

**Files:**
- Modify: `ui/src/api/targets.ts` (76-120)
- Modify: `ui/src/components/TargetRow.vue` (55-80, 276-278, 394)
- Modify: `ui/src/components/BulkDialog.vue:53`
- Test: `ui/src/components/TargetRow.spec.ts`, plus every spec fixture typed `Target` (`ui/src/api/bulk.spec.ts`, `ui/src/api/targets.spec.ts`, `ui/src/components/AddMcuWizard.spec.ts`, `ui/src/components/SummaryChips.spec.ts`, `ui/src/store/agent.spec.ts`)

**Interfaces:**
- Consumes: the wire from Tasks 3-4.
- Produces:
  - TS `Extra`: `{seam: "builder" | "flasher" | "helper"; name; key; label; value: string | number | boolean | null}`
  - TS `TargetSource`: `{path: string; version: string; dirty: boolean | null}`
  - `Target` gains required `source: TargetSource | null`, `extras: Extra[]` and `devices_note: string | null`, and loses `extra`

- [ ] **Step 1: Write the failing specs**

In `ui/src/components/TargetRow.spec.ts`:
- Add `source: null, extras: [], devices_note: null` to `mcuTarget`.
- Replace `displayTarget` with:

```ts
const platformioTarget: Target = {
  provider: "platformio",
  name: "knomi",
  descriptor: "esp32dev",
  firmware: "knomi_serial",
  artifact: {
    state: "stale",
    tone: "attention",
    label: "Needs a build",
    reason: "source_changed",
  },
  profile: null,
  needs_flash: null,
  actions: [],
  devices: [],
  source: { path: "/home/pi/knomi_serial", version: "d34db33", dirty: false },
  extras: [
    {
      seam: "helper",
      name: "knomi_serial",
      key: "module_version",
      label: "Module",
      value: "0.5.0",
    },
  ],
  devices_note: "Nothing is declared under [fake_dev ...].",
};
```

Replace the three empty-row tests (from "shows the display-specific hint" to "does not describe a CMake target's source metadata as a screen") with:

```ts
  it("says what the agent says when a type lists no devices", () => {
    const wrapper = mount(TargetRow, { props: { target: platformioTarget } });
    expect(wrapper.text()).toContain("Nothing is declared under [fake_dev ...].");
  });

  it("does not invent its own empty-row wording", () => {
    // The sentence is the agent's, so a new builder or helper never needs a
    // UI release to explain an empty row.
    const target: Target = {
      ...mcuTarget,
      devices: [],
      devices_note: "Something only the agent knows.",
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("Something only the agent knows.");
    expect(wrapper.text()).not.toContain("No serial devices are tracked");
  });

  it("renders every extra as label and value, without knowing any of them", () => {
    const target: Target = {
      ...mcuTarget,
      extras: [
        ...platformioTarget.extras,
        { seam: "builder", name: "cmake", key: "anything", label: "Board rev", value: 3 },
      ],
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("Module 0.5.0");
    expect(wrapper.text()).toContain("Board rev 3");
  });

  it("renders a row with no extras without an empty caption", () => {
    const wrapper = mount(TargetRow, { props: { target: mcuTarget } });
    expect(wrapper.findAll("[data-extra]")).toHaveLength(0);
  });
```

In every other spec fixture typed `Target`, add `source: null, extras: [], devices_note: null`. For a fixture with an empty `devices`, set `devices_note` to a string instead. Delete any `extra:` key. `npm run typecheck` lists each fixture it rejects.

- [ ] **Step 2: Run to verify they fail**

Run: `cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm run typecheck`
Expected: FAIL. `Target` has no `source`/`extras`/`devices_note` yet.

- [ ] **Step 3: Implement**

`ui/src/api/targets.ts`: delete `DisplayExtra`, `CmakeExtra` and the comment above them. Add:

```ts
/** One fact a builder, flasher or helper contributes to a row. The UI renders
 * `label value` and never branches on `key`, `seam` or `name` - that is what
 * lets a new seam add one without a UI release (docs/agent-api.md's "Target"). */
export interface Extra {
  seam: "builder" | "flasher" | "helper";
  name: string;
  key: string;
  label: string;
  value: string | number | boolean | null;
}

/** The tree a row's builder would build from right now. `version` is the
 * string its staleness check compares; `dirty` is null where it never asks. */
export interface TargetSource {
  path: string;
  version: string;
  dirty: boolean | null;
}
```

In `Target`, replace `extra?: DisplayExtra | CmakeExtra;` with

```ts
  source: TargetSource | null;
  extras: Extra[];
  /** Why `devices` is empty, in the agent's words; null when it is not. */
  devices_note: string | null;
```

Make the `Target` doc comment `/** One targets[] row - every builder's types in one shape. */`.

`ui/src/components/TargetRow.vue`: delete the comment block and both computeds (`noDevicesHint`, `moduleVersion`). Change the first line comment to `// One targets[] row, rendered the same way whatever builds it - see`. Replace the caption:

```vue
      <span
        v-for="extra in target.extras"
        :key="`${extra.seam}:${extra.name}:${extra.key}`"
        data-extra
        class="text-caption text--disabled"
      >
        {{ extra.label }} {{ extra.value }}
      </span>
```

Replace `{{ noDevicesHint }}` with `{{ target.devices_note }}`.

`ui/src/components/BulkDialog.vue:53`: `"Flash every device that needs it. This stops Klipper once for the whole batch."`

- [ ] **Step 4: Run the UI gate**

Run: `cd ui && export PATH="$HOME/scoop/apps/nodejs/current:$PATH" && npm run typecheck && npm test && npm run build && npm run lint`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

Run the Python gate too (`test_ui_contract.py` reads these files). Expected: green.

```bash
git add ui/src
git commit -m "feat(ui): render targets rows from source, extras and devices_note

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: The core and the UI stop saying "display" and "screen", and a test keeps it that way

Tasks 1-8 removed every wire name that carried the words. What is left is local names, error text, comments and docstrings. None of it is on the wire, which is why it goes last: it is a large diff with no behaviour change, and reviewing it after the behaviour has settled keeps the two apart.

The rule, from the spec: "display" and "screen" name a device only in code that belongs to a firmware whose device is one. That means `helpers/knomi_serial.py` and `discovery/knomi_serial/`, and nowhere else. The words stay where they mean something other than a device: a menuconfig screen, a screen reader, text kept on screen, CSS `display:`, and the product name KlipperScreen. Those are listed, line by line, in the guard's allowlist.

**Files:**
- Create: `tests/test_vocabulary.py`
- Modify, every line the guard names. At the start of this task the largest are:

| File | Lines |
|---|---|
| `src/mcu_updater/agent/methods/status.py` | ~100 after Task 3 |
| `src/mcu_updater/providers/pio.py` | 60 |
| `src/mcu_updater/agent/methods/bulk.py` | ~30 |
| `src/mcu_updater/agent/methods/flash.py` | ~20 |
| `src/mcu_updater/cli.py` | 20 |

  and a few each in `providers/platformio.py`, `flashers/spec.py`, `agent/methods/build.py`, `verdict.py`, `providers/selection.py`, `flashers/batch.py`, `discovery/byid.py`, `states.py`, `paths.py`, `flashers/registry.py`, `flashers/flash.py`, `build.py`, `stop_services.py`, `providers/spec.py`, `helpers/spec.py`, `discovery/confirm.py`, `discovery/canbus.py`, `discovery/__init__.py`, `cfgdoc.py`, `agent/methods/_api.py`, and in `ui/src`: `TargetRow.vue`, `BusPanel.vue`, `SummaryChips.vue`, `api/targets.ts`, `api/bulk.ts`, and the specs `bulk.spec.ts`, `jobs.spec.ts`, `targets.spec.ts`, `SummaryChips.spec.ts`, `TargetsView.spec.ts`
- Modify: `scripts/mutations/identity.json`, `scripts/mutations/provider-family-axis.json`, and any other spec the hygiene test names

**Interfaces:**
- Consumes: the tree as Tasks 1-8 left it.
- Produces:
  - `ui/src/api/targets.ts`: `roadrunnerDisplaySerial` becomes `roadrunnerSerialLabel`, with the same signature
  - `tests/test_vocabulary.py`
  - No other public name changes. Every Python rename here is a local variable, a parameter or prose. Nothing in `src` or `tests` passes `display=`, `screen=`, `displays=` or `screens=` as a keyword (checked with `git grep -nE "\b(display|screen|displays|screens)="`).

- [ ] **Step 1: Write the guard**

Create `tests/test_vocabulary.py`:

```python
""""display" and "screen" name a device only in a display firmware's own code.

The core, the generic seams, the wire and the UI say "device" and "PlatformIO
type". A KNOMI is a screen; that fact lives in `helpers/knomi_serial.py` and
`discovery/knomi_serial/`, which are the one place allowed to know it. The last
time it leaked, the wire grew `displays`, `screens` and `display_flash`
alongside `targets[]`, and every consumer had two vocabularies to keep in step.

Words, not substrings: identifiers are split on `_` and on camelCase, so
`screen_id` and `roadrunnerDisplaySerial` are caught, and `displayed` and
`flipMenuIfOffscreen` are not.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNED = (ROOT / "src", ROOT / "ui" / "src")
SUFFIXES = {".py", ".ts", ".vue"}
EXEMPT = (
    "src/mcu_updater/helpers/knomi_serial.py",
    "src/mcu_updater/discovery/knomi_serial/",
)
BANNED = {"display", "displays", "screen", "screens"}
_CSS = re.compile(
    r"display:\s*(none|block|flex|grid|inline[-a-z]*|contents|table[-a-z]*)|@media\s+screen",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")

#: (path, the line stripped, why the word is not a device there).
ALLOWED = {
    ("src/mcu_updater/providers/kconfig.py", "the choice, not the option), so the screen was three padlocked toggles and", "menuconfig screen"),
    ("src/mcu_updater/providers/kconfig.py", "is enabled reads as indented under it, not as a separate screen.", "menuconfig screen"),
    ("src/mcu_updater/providers/kconfig.py", "# menuconfig is its own screen, reached by `enterable`.", "menuconfig screen"),
    ("src/mcu_updater/providers/kconfig.py", "# are their own screens; a choice is represented by its", "menuconfig screen"),
    ("src/mcu_updater/providers/kconfig.py", '"""The current screen: where we are, and what is on it."""', "menuconfig screen"),
    ("src/mcu_updater/providers/kconfig.py", "beats rendering an empty screen with no way out of it.", "menuconfig screen"),
    ("src/mcu_updater/states.py", "#: understood. A chip, an icon and a screen reader all need the `label`; the", "screen reader"),
    ("src/mcu_updater/__init__.py", "# 2: fields were *removed*. `screens[].mac`/`flashed_at`/`moved_from`/`moved_at`", "API history"),
    ("src/mcu_updater/__init__.py", "# 3: `fw.display.list` and `fw.display.build` are gone (use `fw.device.list`", "API history"),
    ("src/mcu_updater/sections.py", "``[mcu carto_v4]`` and ``[display knomi_toolchanger]`` were two spellings of one", "config history"),
    ("src/mcu_updater/agent/events.py", "tab, a phone, KlipperScreen - which is enough to make the UI stutter on a Pi.", "product name"),
    ("ui/src/api/kconfig.ts", "* current screen. */", "menuconfig screen"),
    ("ui/src/store/agent.ts", "// but Vi wants the finished job (and its log) to stay on screen rather", "on screen"),
    ("ui/src/store/agent.ts", "// A menu-changing reply (open/enter/up/set/reset) replaces the screen", "menuconfig screen"),
    ("ui/src/store/agent.ts", "* assignment can rewrite the screen - picking a different architecture", "menuconfig screen"),
}


def _scanned_lines():
    for base in SCANNED:
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                yield rel, number, line


def _names_a_device(line: str) -> bool:
    words = _WORD.findall(_CSS.sub("", line))
    return any(word.lower() in BANNED for word in words)


def test_the_core_and_the_ui_say_device():
    allowed = {(path, text) for path, text, _why in ALLOWED}
    found = [
        f"{rel}:{number}: {line.strip()}"
        for rel, number, line in _scanned_lines()
        if _names_a_device(line) and (rel, line.strip()) not in allowed
    ]
    assert found == [], (
        "say 'device' or 'PlatformIO type' - only a display firmware's own code "
        "may call its device a screen:\n" + "\n".join(found)
    )


def test_every_allowance_is_still_needed():
    """An allowance for a line that has gone would quietly permit its return."""
    present = {(rel, line.strip()) for rel, _n, line in _scanned_lines()}
    stale = [f"{path}: {text}" for path, text, _why in ALLOWED if (path, text) not in present]
    assert stale == []


@pytest.mark.parametrize(
    ("line", "names_one"),
    [
        ("for screen in payload:", True),
        ("screen_id = x", True),
        ("roadrunnerDisplaySerial(serial)", True),
        ("PlatformIO build failed for display 'knomi'", True),
        ("displayed = True", False),
        ("flipMenuIfOffscreen()", False),
        ("  display: none;", False),
        ("@media screen and (max-width: 600px)", False),
    ],
)
def test_the_guard_reads_words_not_substrings(line, names_one):
    assert _names_a_device(line) is names_one
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_vocabulary.py -q`
Expected: `test_the_guard_reads_words_not_substrings` and `test_every_allowance_is_still_needed` PASS. `test_the_core_and_the_ui_say_device` FAILS, listing roughly 300 lines. That list is this task's worklist.

If `test_every_allowance_is_still_needed` fails, an earlier task already reworded one of those lines. Read the line, and if the word still means something other than a device, update the allowance's text to match it. Otherwise delete the allowance.

- [ ] **Step 3: Rename the code names**

Rename by what the thing is:

| Old name | New name | Where |
|---|---|---|
| `display` (a `PioType`) | `entry` | `providers/pio.py` parameters and locals; `cli.py`; `providers/platformio.py`; `agent/methods/build.py`, `status.py`, `flash.py`, `bulk.py` locals |
| `displays` (a `dict[str, PioType]`) | `pio_types` | `cli.py:807-810`, and any remaining local |
| `screen` / `screens` (device dicts or `ListedDevice`s) | `device` / `devices` | `status.py`, `bulk.py`, `flash.py`, `_api.py` (`_platformio_device_status(screen: ...)` → `device`) |
| `roadrunnerDisplaySerial` | `roadrunnerSerialLabel` | `ui/src/api/targets.ts:199`, `ui/src/components/BusPanel.vue` (34, 350, 550) and any spec that imports it |

If `device` is already bound in a scope, use `listed` for the renamed one. If `entry` is, use `pio_type`. Read the enclosing function before renaming. A shadowed loop variable is exactly the kind of bug a rename introduces and a green suite can miss.

- [ ] **Step 4: Reword the text**

Error messages, reporter lines, comments and docstrings say what the thing is in terms of the seam they sit in. For example:

| Old text | New text |
|---|---|
| `f"PlatformIO build failed for display '{display.name}': pio exited {rc}."` | `f"PlatformIO build failed for '{entry.name}': pio exited {rc}."` |
| `f"upload failed for display '{display.name}' on {port}: ..."` (both, ~607 and ~622) | `f"upload failed for '{entry.name}' on {port}: ..."` |
| `"device on its own, and every display here is an identical CH340."` | `"device on its own, and devices of one type can be identical USB-serial bridges."` |
| `"""ESP32 displays: PlatformIO builds, esptool uploads.` (pio.py:1) | `"""PlatformIO types: PlatformIO builds and uploads.` |
| `"""What the display source tree would build right now."""` | `"""What the PlatformIO source tree would build right now."""` |
| `# -- ESP32 displays ---...` (status.py section comment, if Task 3 kept it) | `# -- PlatformIO devices ---...` |
| `#: Display-only. Never use these to build a path...` (byid.py:60) | `#: For showing only. Never use these to build a path...` |
| `for *this scan's own display only*` (canbus.py:89) | `for *this scan's own report only*` |
| `A second display firmware would get a sibling subpackage` (discovery/__init__.py:11) | `A second firmware with its own discovery would get a sibling subpackage` |
| `A KNOMI screen has none of them` (helpers/spec.py:148) | `A KNOMI has none of them` |
| `"Flash every board and screen..."` (already changed by Task 8) | - |

Where a sentence explains a CH340 or KNOMI fact inside a generic module, keep the fact but name the firmware ("a KNOMI's CH340 ...") rather than "a screen". If the sentence only matters to knomi_serial, move it to `helpers/knomi_serial.py`. Do not delete an explanation to make the guard pass. The explanation is why the code is the shape it is.

UI spec titles and comments follow the same rule: `"(a display with no build)"` → `"(a type with no build)"`, `"anything that writes to a board or screen"` → `"anything that writes to a device"`, `"a display counts too"` → `"a PlatformIO row counts too"`, `"MCU and display alike"` → `"whatever builds them"`, and `targets.spec.ts:6`'s "An MCU type and a display live in different config files" → "A kconfig type and a PlatformIO type ...".

- [ ] **Step 5: Re-anchor the mutation specs**

Run the hygiene test first. It names every anchor this task's edits broke:

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_repo_hygiene.py -q`

These are known to break. Apply the same rename to `find` and to `replace`:

| Spec | Old `find` | New `find` |
|---|---|---|
| `identity.json` (`cli.py`) | `            c.paths, c.settings, display, ask=True, reporter=stdout_reporter` | `            c.paths, c.settings, entry, ask=True, reporter=stdout_reporter` |
| `identity.json` (`cli.py`) | `        c.paths, c.settings, display, ask=False, reporter=stdout_reporter` | `        c.paths, c.settings, entry, ask=False, reporter=stdout_reporter` |
| `identity.json` (`cli.py`) | `        where = identify.remembered_at(c.paths, display) or "(nothing remembered)"` | `        where = identify.remembered_at(c.paths, entry) or "(nothing remembered)"` |
| `provider-family-axis.json` (`platformio.py`) | `            BuildTarget(self.name, name, display.firmware)` | `            BuildTarget(self.name, name, entry.firmware)` |

Re-run the hygiene test and re-anchor whatever else it names, the same way. Then run each touched spec, one at a time, with Bash `timeout: 600000`:

```bash
PY=../../.venv/Scripts/python.exe
$PY scripts/mutation_test.py scripts/mutations/identity.json
$PY scripts/mutation_test.py scripts/mutations/provider-family-axis.json
$PY -m pytest tests/test_repo_hygiene.py -q
```

Add a line for every other spec the hygiene test named.
Expected: every mutation killed; hygiene PASS.

- [ ] **Step 6: Run the guard and the gates**

Run: `PY=../../.venv/Scripts/python.exe; $PY -m pytest tests/test_vocabulary.py -q`
Expected: PASS.

Run the full Python gate and the UI gate. Expected: green. A rename that shadowed or missed a variable shows up here as a `NameError` or a ruff `F821`/`F841`.

- [ ] **Step 7: Commit**

```bash
git add -A src ui/src tests scripts
git commit -m "refactor: say device and platformio type outside the knomi_serial helper, and guard the vocabulary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The docs describe API version 5

**Files:**
- Modify: `docs/agent-api.md`
- Modify: `README.md`
- Modify: `docs/decisions.md`
- Modify: `docs/layout.md`

**Interfaces:**
- Consumes: everything Tasks 1-9 shipped. The docs describe the code as it now is; where they disagree, the code is right and the doc is fixed.

- [ ] **Step 1: `docs/agent-api.md`**

1. Header (line 9): `api_version: **4**` → `**5**`. In the version paragraph (11-23), add one sentence per the `# 5:` entry in `src/mcu_updater/__init__.py`. Copy its facts, do not paraphrase them into new claims.
2. Method table (133-134, 148): delete the `fw.device.list` row. Replace the two `fw.roadrunner.*` rows with `fw.identity.provision` and `fw.identity.clear`. Both are hardware methods, gated like `fw.flash`.
3. The `fw.roadrunner.*` section (152-220) becomes `fw.identity.provision` / `fw.identity.clear`. It gives:
   - the params `{serial}`, and the result `{serial, prior_serial, state}`
   - the routing rule: every registered helper with the provisioning capability is asked whether the serial is its identity and in which state, and exactly one must answer in the state the call needs. None, or several, is `not_provisionable`, with `data: {serial, helpers}` naming every helper that recognised the serial
   - the tracked refusal, `device_tracked` with `data: {serial, tracked_under}`
   - the code table from Task 6 (old code → new code, one row each), under the heading "Codes renamed in version 5"
4. `Target` section (~489): delete `extra`. Document `source` (`{path, version, dirty}` or null; `dirty` is null where the builder never asks), `extras` (the `Extra` shape `{seam, name, key, label, value}`, rendered as `label value`; a client never branches on `key`), and `devices_note` (the agent's sentence for an empty `devices`, null otherwise). Add `firmware` to the PlatformIO row's description.
5. The "ESP32 displays" section (~1563) becomes "PlatformIO devices". Describe the listing from the helper's `DeviceLister`, addressing by `configured_id` or `configured_path`, and the sidecar at `data_dir/platformio/<env>.build.json`.
6. Delete the `fw.device.list` section (~1619) and "The watcher's map" section (~1668).
7. `fw.flash` for a PlatformIO type and `fw.flash_all` answer `{job_id, job}`. Delete the `displays` and `boards` keys from their examples (~1086 and wherever else `git grep -n '"displays"\|"boards"' docs/agent-api.md` finds them). The job-kind list: PlatformIO jobs are `flash` and `build` with params `{name, port?}` and `{name, fw}`; `display_flash` and `display_build` are gone. `nothing_to_do`'s data names `devices`.
8. `fw.target.get` for a PlatformIO type carries `devices`, not `screens`, and no longer carries `klipper_section`, `device_map` or `service`. Its keys are `name`, `env`, `firmware`, `stop_services`, `source`, `devices`, `extras`, `devices_note` and the verdict fields.

Run: `git grep -n "fw\.device\.list\|fw\.roadrunner\|display_flash\|display_build\|\"screens\"\|\"displays\"\|\"extra\"\|targets\[\]\.extra\b" docs/agent-api.md`
Expected: only lines inside the version-history paragraph and the "Codes renamed in version 5" table.

- [ ] **Step 2: `README.md`**

1. `## TODO` (line 88): strike through the **NEXT** entry this plan closes, per the file's convention. Add a new entry: "Per-device helper actions: a helper contributes a device's action rows (identity provision/clear today is chosen in `BusPanel.vue` by `isRoadrunnerDevice`) so the UI stops naming a firmware."
2. `## Features`: add a checked item, "One `targets[]` row shape for every builder: `source`, `extras` and `devices_note`, with no builder-specific bag."
3. "ESP32 displays" (lines 27, 184, 263, 568) → "PlatformIO devices". Where a sentence is really about KNOMI, name KNOMI.
4. Line 195: `fw.roadrunner.provision` / `.clear` → `fw.identity.provision` / `.clear`.
5. Line 387: the precedence line still names a `[display ...]` section, which `typelist.REMOVED_KEYS` already rejects. Rewrite it for `[type ...]` + `[firmware ...]`.
6. Upgrade notes, in whatever section the README keeps them (search for "Upgrading"; if there is none, add `### Upgrading to API version 5` at the end of `## Requirements`). Three sentences: build records now live under `platformio/`, so each PlatformIO type reports "no provenance" until it is rebuilt once. The UI and the agent must both be on this release. `fw.roadrunner.*` callers move to `fw.identity.*`.

- [ ] **Step 3: `docs/decisions.md`**

Near the existing PlatformIO entries (~494 and ~520), add three entries, in the file's existing voice (the decision, then why, then what it would cost to reverse):
- **Screen and display are firmware words.** Only `helpers/knomi_serial.py` and `discovery/knomi_serial/` may use them for a device. `tests/test_vocabulary.py` enforces it, with a line-by-line allowlist for the other meanings. Why: the wire grew a second vocabulary once, and every consumer paid for it.
- **`extras` is a list the UI renders blind.** A seam adds a fact by returning an `Extra`. The UI never branches on its `key`. Why: a new builder or helper should not need a UI release to show one field.
- **Identity writes route by claim, not by name.** `fw.identity.*` names no family, and the helper that recognises the serial takes the call. Two claimants refuse rather than pick. Why: the board is untracked at that point, so there is no family to name, and a guess over an irreversible write is not acceptable.

- [ ] **Step 4: `docs/layout.md`**

- Replace `test_agent_displays.py` / `test_agent_display_jobs.py` with `test_agent_platformio_devices.py` / `test_agent_platformio_flash.py`.
- Add `tests/test_identity_provisioning.py` and `tests/test_vocabulary.py`.
- Add `src/mcu_updater/extras.py` (Task 1).
- Change the data-tree entry for build records from `displays/<env>.build.json` to `platformio/<env>.build.json`.

Run: `git grep -n "test_agent_displays\|test_agent_display_jobs\|displays/" docs/layout.md`
Expected: no output.

- [ ] **Step 5: Gate and commit**

Run the full gate. `tests/test_ui_contract.py` and any doc-anchored test read these files. Expected: green.

```bash
git add README.md docs/agent-api.md docs/decisions.md docs/layout.md
git commit -m "docs: describe api_version 5 - uniform targets rows, fw.identity, and platformio devices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Release note for whoever cuts it (not a step here):** this bumps `API_VERSION`. Promote the UI release to stable first, and merge `develop` → `main` second (AGENTS.md, "The one ordering rule").
