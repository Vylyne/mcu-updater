# Closed-loop BOOTSEL helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flash a configured Roadrunner CMake UF2 through `fw.flash` by requesting BOOTSEL from its confirmed application USB device and writing only the topology-matched BOOTSEL volume.

**Architecture:** A static `helpers` package binds a configured firmware family's `helper:` name to an optional `request_bootsel()` capability. The Roadrunner helper reuses the existing direct-USB confirmation and protocol bridge; a helper-backed BOOTSEL flasher owns the shared mount validation/copy flow and is selected only when a type's application declares that helper.

**Tech Stack:** Python 3.11+, stdlib only, pytest, Ruff, mypy, Moonraker JSON-RPC, Linux udev-mounted RP2040 BOOTSEL volumes.

**Spec:** `docs/superpowers/specs/2026-09-08-bootsel-helper-design.md`

## Global Constraints

- Keep LF line endings and run `python scripts/check_line_endings.py` before each commit.
- Keep Python 3.11 compatibility and `from __future__ import annotations`.
- Add no dependencies and never dynamically import a helper named in config.
- Register every helper manually in one static tuple; do not use entry points or package scanning.
- A provisioned Roadrunner UUID is durable identity; USB topology is only a transient same-operation handoff.
- Require `INFO_UF2.TXT`; never select a first or only arbitrary BOOTSEL mount.
- Cancellation remains between writes, never during a UF2 copy.
- Before editing guarded lines, search `scripts/mutations/` and re-anchor affected mutation specs in the same commit.
- Run the full gate with `C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe` from this worktree.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/mcu_updater/firmware.py` | Parse and expose the optional firmware-family `helper:` name. |
| `src/mcu_updater/helpers/spec.py` | Define the narrow BOOTSEL requester protocol and topology handoff value. |
| `src/mcu_updater/helpers/registry.py` | Static helper lookup; reject unknown configured names. |
| `src/mcu_updater/helpers/roadrunner.py` | Confirm a tracked Roadrunner, request BOOTSEL, and return its transient topology. |
| `scripts/roadrunner_usb.py` | Encode/decode Roadrunner `REBOOT_BOOTSEL` as the bridge's `bootsel` operation. |
| `src/mcu_updater/discovery/roadrunner.py` | Reuse confirmed-device functions; add old-port disappearance wait and public BOOTSEL request primitive. |
| `src/mcu_updater/discovery/bootsel.py` | Normalize serial/disk topology aliases and select one marker-bearing mount for a handoff. |
| `src/mcu_updater/flashers/bootsel.py` | Extract shared validated UF2 copy and retain bare-board ambiguity behavior. |
| `src/mcu_updater/flashers/helper_bootsel.py` | Helper-backed flasher that requests BOOTSEL then delegates to shared BOOTSEL copy. |
| `src/mcu_updater/flashers/registry.py` | Register the helper-backed flasher without changing Katapult selection. |
| `src/mcu_updater/agent/methods/flash.py` | Route eligible configured CMake types through the batch flasher path. |
| `tests/test_roadrunner.py`, `tests/test_devices.py`, `tests/test_flash.py`, `tests/test_agent_jobs.py`, `tests/test_agent_targets.py` | Regression tests for protocol, topology, write safety, and agent routing. |
| `README.md`, `docs/layout.md`, `docs/agent-api.md`, `docs/cmake-provider.md`, `docs/bootsel-mountpoint-design.md` | User-facing configuration, API, and safety documentation. |

### Task 1: Declare and resolve static firmware helpers

**Files:**
- Create: `src/mcu_updater/helpers/__init__.py`
- Create: `src/mcu_updater/helpers/spec.py`
- Create: `src/mcu_updater/helpers/registry.py`
- Modify: `src/mcu_updater/firmware.py`
- Test: `tests/test_firmware.py`

**Interfaces:**
- Produces: `FirmwareFamily.helper: str`, `helpers.for_name(name: str, *, family: str) -> BootselRequester | None`.
- Contract: an empty helper name returns `None`; any nonempty unknown name raises `ConfigCorruptError` naming the firmware family and configured value.

- [ ] **Step 1: Write failing configuration and registry tests**

```python
def test_firmware_family_reads_a_registered_helper():
    doc = CfgDocument("[firmware roadrunner]\\nhelper: roadrunner\\n")
    assert firmware.load_from_doc(doc)["roadrunner"].helper == "roadrunner"

def test_unknown_helper_is_refused_without_importing_it():
    doc = CfgDocument("[firmware custom]\\nhelper: ../../evil\\n")
    with pytest.raises(ConfigCorruptError, match="unknown helper"):
        helpers.for_name("../../evil", family="custom")
```

- [ ] **Step 2: Run the two tests and verify they fail because the field/lookup is absent**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_firmware.py -k helper -v`

- [ ] **Step 3: Implement the minimal static seam**

Add `helper: str = ""` to `FirmwareFamily`, parse `doc.get(section, "helper")`, and create a protocol with only:

```python
class BootselRequester(Protocol):
    name: str
    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff: ...
```

Keep `BootselHandoff` frozen and topology-only. `registry.py` imports only explicit helper modules and maps `""` to `None`.

- [ ] **Step 4: Run the focused tests and static checks**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_firmware.py -k helper -v`
Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m ruff check src/mcu_updater/helpers src/mcu_updater/firmware.py tests/test_firmware.py`

- [ ] **Step 5: Commit**

```powershell
git add src/mcu_updater/helpers src/mcu_updater/firmware.py tests/test_firmware.py
git commit -m "feat(helpers): register firmware-specific helpers"
```

### Task 2: Add Roadrunner BOOTSEL protocol and disappearance evidence

**Files:**
- Modify: `scripts/roadrunner_usb.py`
- Modify: `src/mcu_updater/discovery/roadrunner.py`
- Create: `src/mcu_updater/helpers/roadrunner.py`
- Test: `tests/test_roadrunner.py`
- Test: `tests/test_roadrunner_usb.py` (create if the bridge tests do not have a dedicated file)

**Interfaces:**
- Consumes: `BootselRequester.request_bootsel()`.
- Produces: `Roadrunner.request_bootsel(paths, device) -> UsbDevice`; helper returns `BootselHandoff(topology=...)`.
- Contract: a Roadrunner must be confirmed by its tracked provisioned serial and INFO response before BOOTSEL is requested; an unchanged old CDC topology is a timeout, not a mount-selection fallback.

- [ ] **Step 1: Write failing wire and helper tests**

```python
def test_bridge_sends_reboot_bootsel_and_requires_an_empty_success_payload():
    assert run(Namespace(operation="bootsel", port="/dev/ttyACM0")) == {"bootsel": True}

def test_helper_refuses_before_request_when_serial_is_not_a_confirmed_roadrunner(monkeypatch):
    with pytest.raises(RoadrunnerError, match="No confirmed provisioned"):
        RoadrunnerHelper().request_bootsel(bench, serial="RR-BAD", chipset="rp2040", ctx=ctx)

def test_request_waits_for_the_old_topology_to_disappear(monkeypatch):
    assert request_bootsel(paths, device) == device.topology
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing `bootsel` support**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_roadrunner.py tests/test_roadrunner_usb.py -k bootsel -v`

- [ ] **Step 3: Implement only the Roadrunner transition**

Add opcode `REBOOT_BOOTSEL = 0x02`; accept `bootsel` in argparse; require status zero and no response payload. Add `request_bootsel()` to discovery that invokes the bridge and polls the old USB topology until no matching Roadrunner CDC candidate remains. Do not wait for the board to reappear as a Roadrunner; it should become storage. Have the helper call `find_provisioned()` and this primitive.

- [ ] **Step 4: Run focused tests**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_roadrunner.py tests/test_roadrunner_usb.py -k bootsel -v`

- [ ] **Step 5: Commit**

```powershell
git add scripts/roadrunner_usb.py src/mcu_updater/discovery/roadrunner.py src/mcu_updater/helpers/roadrunner.py tests/test_roadrunner.py tests/test_roadrunner_usb.py
git commit -m "feat(roadrunner): request bootsel through helper"
```

### Task 3: Correlate BOOTSEL mounts and share the safe UF2 write

**Files:**
- Modify: `src/mcu_updater/discovery/bootsel.py`
- Modify: `src/mcu_updater/flashers/bootsel.py`
- Create: `src/mcu_updater/flashers/helper_bootsel.py`
- Modify: `src/mcu_updater/flashers/registry.py`
- Modify: `src/mcu_updater/flashers/__init__.py`
- Test: `tests/test_devices.py`
- Test: `tests/test_flash.py`

**Interfaces:**
- Consumes: `BootselHandoff.topology`, one staged `.uf2`.
- Produces: `mount_for_topology(paths, topology) -> str` and a helper-backed `FlashTarget`.
- Contract: normalize only for transient matching; return one marker-bearing matching mount; refuse zero or multiple matches; generic `Bootsel._find_mount()` remains unchanged when no topology was supplied.

- [ ] **Step 1: Write failing topology-selection tests**

```python
def test_mount_for_topology_selects_only_the_matching_by_path_volume(paths, fake_root):
    assert mount_for_topology(paths, "platform-usb-0:1.3:1.0") == str(matching_mount)

def test_mount_for_topology_ignores_a_bystander_bootsel_volume(paths, fake_root):
    assert mount_for_topology(paths, topology) != str(bystander_mount)

def test_mount_for_topology_refuses_two_normalized_matches(paths):
    with pytest.raises(FlashError, match="ambiguous"):
        mount_for_topology(paths, topology)
```

- [ ] **Step 2: Run them and verify they fail because topology selection is absent**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_devices.py tests/test_flash.py -k "topology or bootsel" -v`

- [ ] **Step 3: Implement normalized discovery and shared copy**

Implement serial/disk `by-path` normalization as documented in the design: collapse `usbv2-` to `usb-`, preserve hub-port components, remove only the last USB interface component, and compare the sanitized tag prefix against the mount name. Require `INFO_UF2.TXT` after matching. Extract a copy helper that checks artifact existence, honors dry run, reports the destination, and calls `shutil.copy2`; call it from both the original `Bootsel` and new `HelperBootsel`. The new flasher has `needs_services_stopped = True`, requests the handoff in `write()`, then copies only to `mount_for_topology()`.

- [ ] **Step 4: Verify focused behavior and no bare-BOOTSEL regression**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_devices.py tests/test_flash.py -k bootsel -v`

- [ ] **Step 5: Commit**

```powershell
git add src/mcu_updater/discovery/bootsel.py src/mcu_updater/flashers tests/test_devices.py tests/test_flash.py
git commit -m "feat(bootsel): correlate helper handoffs by topology"
```

### Task 4: Route configured CMake Roadrunners through `fw.flash`

**Files:**
- Modify: `src/mcu_updater/agent/methods/flash.py`
- Modify: `src/mcu_updater/agent/methods/status.py`
- Test: `tests/test_agent_jobs.py`
- Test: `tests/test_agent_targets.py`

**Interfaces:**
- Consumes: a CMake type's application `FirmwareFamily.helper`, staged `paths.uf2_file(type, application)`, tracked serial, and `stop_services.for_mcu()`.
- Produces: normal `fw.flash` job response and target action with `extra.flashable: true` only when a helper-backed flash route is configured.
- Contract: an ordinary CMake type without a helper remains non-flashable; Katapult serial/CAN behavior remains unchanged.

- [ ] **Step 1: Write failing RPC/action tests**

```python
def test_flash_routes_a_configured_roadrunner_cmake_uf2_through_helper(api, monkeypatch):
    response = api.dispatch("fw.flash", {"name": "roadrunner", "serial": SERIAL})
    assert response["job"]["kind"] == "flash"

def test_cmake_target_is_flashable_only_when_its_family_declares_a_helper():
    assert actions["flash"]["params"] == {"name": "roadrunner", "serial": SERIAL}
```

- [ ] **Step 2: Run them and verify current CMake refusal/non-flashable payload**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_jobs.py tests/test_agent_targets.py -k "cmake and flash" -v`

- [ ] **Step 3: Implement the one-target batch route**

Before the legacy serial flashtool branch, resolve the type/application/family. If its helper is present, require the staged UF2, retain the existing attachment and print-idle gates, build a helper-BOOTSEL target with `stop_services.for_mcu()`, and invoke `flashers.write_all(self._bench(...), [target], ctx, on_ready=...)`. Project a batch failure back into this single job as `FlashError`. Update CMake target status so it advertises flash only for a declared helper route; do not list synthetic devices.

- [ ] **Step 4: Run focused agent tests**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_jobs.py tests/test_agent_targets.py -k "cmake or roadrunner" -v`

- [ ] **Step 5: Commit**

```powershell
git add src/mcu_updater/agent/methods/flash.py src/mcu_updater/agent/methods/status.py tests/test_agent_jobs.py tests/test_agent_targets.py
git commit -m "feat(flash): route helper-backed cmake uf2 images"
```

### Task 5: Publish the configuration and safety contract

**Files:**
- Modify: `README.md`
- Modify: `docs/layout.md`
- Modify: `docs/agent-api.md`
- Modify: `docs/cmake-provider.md`
- Modify: `docs/bootsel-mountpoint-design.md`
- Modify: relevant exact-line specs in `scripts/mutations/` if guarded production lines moved
- Test: `tests/test_ui_contract.py` only if the target/action wire shape changes
- Test: `tests/test_repo_hygiene.py`

- [ ] **Step 1: Write documentation/wire assertions before changing prose where an existing contract test applies**

Add or update the target payload assertion so CMake remains non-flashable without `helper:` and becomes flashable with it. If no API field changes, document the existing `fw.flash` request/response shape without inventing one.

- [ ] **Step 2: Run the changed contract test and verify the intended initial failure**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_targets.py tests/test_repo_hygiene.py -v`

- [ ] **Step 3: Update all user-facing material**

Document `helper: roadrunner` beside the CMake Roadrunner example; explain that helpers are statically registered capabilities, not module paths. State the closed-loop sequence, topology's transient-only role, `INFO_UF2.TXT` requirement, bystander-safe mount selection, and retained manual BOOTSEL workflow for bare boards. Mark the BOOTSEL design's closed-loop item implemented only after code/tests land.

- [ ] **Step 4: Re-anchor mutation specifications**

Run: `rg -n -F "<each changed guarded source line>" scripts/mutations`. Update only the matching JSON anchor text; run one mutation spec at a time and then its hygiene test.

- [ ] **Step 5: Run documentation-adjacent tests and commit**

Run: `& C:\\git\\github\\mcu-updater\\.venv\\Scripts\\python.exe -m pytest tests/test_agent_targets.py tests/test_repo_hygiene.py -v`

```powershell
git add README.md docs scripts/mutations tests
git commit -m "docs: explain helper-backed bootsel flashing"
```

### Task 6: Independent review and full gate

**Files:**
- Review: every changed file from Tasks 1-5

- [ ] **Step 1: Review the diff against the spec**

Verify no dynamic import exists; Roadrunner keeps provisioned serial as durable identity; generic bare BOOTSEL still rejects ambiguous mounts; no write uses an unvalidated marker-less mount; and the helper route stops services before opening CDC.

- [ ] **Step 2: Run the full required gate**

Run:

```powershell
& C:\git\github\mcu-updater\.venv\Scripts\python.exe -m pytest -q
& C:\git\github\mcu-updater\.venv\Scripts\python.exe -m ruff check src tests scripts
& C:\git\github\mcu-updater\.venv\Scripts\python.exe -m mypy src
& C:\git\github\mcu-updater\.venv\Scripts\python.exe scripts\check_line_endings.py
git diff --check
```

- [ ] **Step 3: Record verification boundaries**

Report host-test coverage separately from bench validation. Bench proof must use a bench Roadrunner and demonstrate confirmed serial -> CDC disappearance -> matching BOOTSEL mount -> UF2 copy -> Roadrunner re-enumeration, with a second BOOTSEL board attached for the bystander case.
