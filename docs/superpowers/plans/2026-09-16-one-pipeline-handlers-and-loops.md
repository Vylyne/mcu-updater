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
3. ★ **A `platformio` family must name `helper: knomi_serial`.** Screens are identified and read through that helper from Task 2 on; the old implicit coupling (every platformio family was a knomi screen) becomes a declared one.
4. **`firmware.FLASHERS` and `firmware.HELPERS` are static tuples**, held equal to the flasher and helper registries by tests, for the reason `firmware.BUILDERS` is: `typelist` must not import the implementations (import cycle, and the registries import hardware code).
5. **Capabilities are separate `runtime_checkable` Protocols with accessor functions** (`helpers.bootsel_requester`, `device_info_reader`, `image_reporter`, `provisioner`, `identifier`). A helper that lacks one returns `None`; a misspelt helper name raises (Ruling 2). No caller compares a helper's `name`.
6. **The Klipper device-info reader is built in**, not a helper. A family whose helper has no device-info capability (klipper, katapult, forks) reads through `device_info.KLIPPER`.
7. ★ **Retire the UART limitation.** `sensor_provenance`/`device_provenance` become one `reported_images()`; the docstrings that said a UART sensor cannot report a digest go, and so does `_image_int`'s "None is the rangeless-UART case".
8. ★ **`helper_bootsel` disappears as a flasher name.** A Roadrunner write reports `"flasher": "bootsel"` in `fw.flash_all`'s `flashed[]`/`failures[]` and in `fw.flash`'s result. `docs/agent-api.md` and `docs/cmake-provider.md` change in Task 3.
9. **`FlashTarget.needs_services_stopped: bool | None`** overrides the flasher's class attribute when set. `Bootsel` sets it per target: `True` when it will ask a helper for BOOTSEL (the request goes over a port Klipper may hold), `False` for a board already in BOOTSEL.
10. ★ **A device no flasher supports is a reported failure, not an abort.** Batches list it in `failures[]` with `"error"` naming the family and its `flashers:`; single-device RPCs raise the new `NoFlasherError` (`code: "no_flasher"`, additive).
11. **First install selects through the katapult family's `flashers:`.** `select_for` is deleted. The user-facing `UnsupportedChipsetError` message stays, re-raised from `NoFlasherError`.
12. ★ **`FlashLog` is written only by `write_all`**, right after a successful write and before `settled` or any later failure is raised. `flash_katapult`, `flash_katapult_can`, `esptool._record` and the agent's `_cmake_flash` stop writing records. Every single-device path goes through `write_all`, so no path loses its record. The agent `fw.flash` serial path moves onto `write_all` (Task 5).
13. ★ **Provision-on-track.** `tracking.add_serial` given `RR-UNPROVISIONED-…` for a type whose family helper can provision, provisions it under `lock.exclusive` and tracks the returned serial. A held lock raises `BusyError` and is not retried. `fw.serial.add` returns `serial` (the tracked one) and adds `prior_serial` when it differs. The `roadrunner_unprovisioned` refusal stays only for types with no provisioner, where it is still correct.
14. ★ **`auto_provision:`** is a family boolean, default off, refused on a family whose helper cannot provision. It runs inline on the `BusWatcher` thread. `BusyError` means skip, and the watcher retries on its next poll even when the bus fingerprint has not changed. It never runs from `fw.status`.
15. ★ **One verdict** (`verdict.decide`). Three unifications change edge cases: an empty-string running version is `UNKNOWN` for every row (kconfig only treated `None` so); the running-sha comparison is a case-insensitive prefix match over the shorter of the two (kconfig used `head.startswith(running)`); a Roadrunner reporting `dev` against a built stamp is `SOURCE_CHANGED`.
16. ★ **New reason `unexpected_image`** (`needs_flash: true`, tone attention), for a digest that disagrees with the artifact. Additive in `docs/agent-api.md`.
17. ★ **CMake rows get real verdicts** in `fw.status` (they were a fixed `UNKNOWN_VERSION`-style stub), and **CMake boards join `fw.flash_all`, `fw.update_all`, CLI `flash -t` and CLI `update-all`**. `type_not_bulk_flashable`, the CLI's `-t`-only CMake refusal and `update-all`'s "No types configured." early exit go. `update-all` is build-all then flash-all.
18. ★ **§3 identity.** A PlatformIO row's `targets[].devices[].id` becomes the `device_id` for a `device_id:` section (the by-id path, as today, for a `serial:` section). `fw.flash` for a screen accepts either the configured path or the device_id. `knomi_serial`'s `identify` is the only identity handler.
19. **`_platformio_device_status` keeps layering** `PROTOCOL_MISMATCH`/`OFFLINE` over the screen's `reason` field; that field is on the wire and keeps its values.
20. **Registry mutation uses `paths.registry_lock_file`, not the op lock**, so provisioning under `exclusive()` and then calling `Registry.mutate` cannot deadlock.

**Out of scope:** removing the three builder views (`Registry.load`, `pio.load`, `cmake.load`); the mixed-builder refusal; a record-backed `ARTIFACT_CHANGED` for screens; config migrations; udev-driven presence.

**Worktree setup:** already created.

```bash
cd C:/git/github/mcu-updater/.worktrees/one-pipeline-loops
git log --oneline -1   # 38a5f1f (or the plan commit on top of it)
python -m pytest -q    # expect: all pass, the baseline
```

## File map

| File | Status | Responsibility |
| --- | --- | --- |
| `src/mcu_updater/firmware.py` | modify (T1,7) | `flashers`, `auto_provision` fields; `FLASHERS`, `HELPERS`, `PROVISIONING_HELPERS` |
| `src/mcu_updater/typelist.py` | modify (T1,7) | Refuse missing/unknown flashers, unknown helper, platformio without helper, auto_provision without provisioner |
| `src/mcu_updater/helpers/spec.py` | modify (T1,2,6,10) | Capability Protocols |
| `src/mcu_updater/helpers/__init__.py`, `helpers/registry.py` | modify (T1,2,6,10) | Accessors; `HELPERS` of every helper |
| `src/mcu_updater/helpers/knomi_serial.py` | create (T1) | Knomi screen helper (device info T2, identify T10) |
| `src/mcu_updater/helpers/cartographer.py` | create (T2) | Cartographer device info (no sha) |
| `src/mcu_updater/helpers/roadrunner.py` | modify (T2,6) | Device info, image reporting, provisioning |
| `src/mcu_updater/device_info.py` | create (T2) | `DeviceInfo`, the Klipper reader, key/int helpers |
| `src/mcu_updater/flashers/spec.py` | modify (T3,5) | `Device`, `supports`, `target`, `record`, `FlashRecord`, per-target stop flag |
| `src/mcu_updater/flashers/registry.py` | modify (T3,4) | `resolve`, `select`; `select_for` deleted |
| `src/mcu_updater/flashers/bootsel.py` | modify (T3) | Absorbs the helper BOOTSEL handoff |
| `src/mcu_updater/flashers/helper_bootsel.py` | delete (T3) | — |
| `src/mcu_updater/flashers/flashtool.py`, `dfu_util.py`, `esptool.py` | modify (T3,5) | `supports`, `target`, `record` |
| `src/mcu_updater/flashers/batch.py` | modify (T4,5) | `refused=`; FlashLog record after a write |
| `src/mcu_updater/flashers/flash.py` | modify (T4,5) | First install selects; record writes removed |
| `src/mcu_updater/build.py` | modify (T5) | `FlashLog.record` digest kwargs |
| `src/mcu_updater/errors.py` | modify (T3) | `NoFlasherError` |
| `src/mcu_updater/tracking.py` | modify (T6) | `Tracked`; provision-on-track |
| `src/mcu_updater/agent/events.py`, `agent/service.py` | modify (T7) | Watcher retry; auto-provision adapter |
| `src/mcu_updater/verdict.py` | create (T8) | `Evidence`, `Expected`, `decide` |
| `src/mcu_updater/states.py` | modify (T8) | `UNEXPECTED_IMAGE` |
| `src/mcu_updater/agent/methods/status.py` | modify (T1,2,3,6,8,10) | Readers, verdict wiring, identity |
| `src/mcu_updater/agent/methods/bulk.py` | modify (T4,9) | Select per family; CMake in the loops; refusal removed |
| `src/mcu_updater/agent/methods/flash.py` | modify (T3,4,5,10) | Single-device paths select and go through `write_all` |
| `src/mcu_updater/agent/methods/registry.py` | modify (T6) | `serial_add` provisions through tracking |
| `src/mcu_updater/providers/pio.py` | modify (T2,8,10) | `is_dirty`; `device_status` retired; identity |
| `src/mcu_updater/cli.py` | modify (T1,3,4,6,9) | Targets select; CMake in flash/update-all |
| `mcu-updater.cfg`, `tests/fixtures/registry.cfg`, `tests/conftest.py` | modify (T1) | `flashers:`/`helper:` on every family |
| `README.md`, `docs/decisions.md`, `docs/agent-api.md`, `docs/cmake-provider.md` | modify (T1-11) | Same-task docs |
| New tests | create | `test_helpers.py` (T1), `test_device_info.py` (T2), `test_flasher_select.py` (T3), `test_flashlog_loop.py` (T5), `test_provision_on_track.py` (T6), `test_auto_provision.py` (T7), `test_verdict.py` (T8) |
| New mutation specs | create | `family-keys.json` (T1), `device-info.json` (T2), `flasher-supports.json` (T3), `flashlog-loop.json` (T5), `provision-on-track.json` (T6), `auto-provision.json` (T7), `verdict.json` (T8), `loops.json` (T9) |

---

### Task 1: Strict family keys — `flashers:` and `helper:`

**Interfaces:**
- Produces:
  - `firmware.FirmwareFamily.flashers: tuple[str, ...] = ()` (from `flashers:`, comma list)
  - `firmware.FLASHERS: tuple[str, ...] = ("bootsel", "dfu_util", "esptool", "flashtool")`
  - `firmware.HELPERS: tuple[str, ...] = ("knomi_serial", "roadrunner")` (T2 adds `"cartographer"`)
  - `firmware.suggested_flashers(family: FirmwareFamily) -> str`
  - `helpers.Helper` Protocol (`name: str`); `helpers.registry.HELPERS: tuple[Helper, ...]`; `helpers.for_name(name, *, family) -> Helper | None`
  - `helpers.bootsel_requester(helper: Helper | None) -> BootselRequester | None`
  - `helpers.knomi_serial.KnomiSerialHelper` (`name = "knomi_serial"`)

### Task 2: Device-info handlers

**Interfaces:**
- Consumes: `helpers.Helper`, `helpers.for_name`, `FirmwareFamily.helper`
- Produces:
  - `device_info.DeviceInfo(source: str, version: str | None, digest_algorithm: int | None = None, digest: int | None = None, image_start: int | None = None, image_length: int | None = None)`, `.has_digest() -> bool`
  - `device_info.SOURCE_KLIPPER = "klipper"`, `device_info.SOURCE_INFO = "info"`
  - `device_info.serial_key(value: str) -> str`, `digest_int(value) -> int | None`, `image_int(value) -> int | None`
  - `device_info.KLIPPER: DeviceInfoReader`; `device_info.reader_for(paths, family: FirmwareFamily | None) -> DeviceInfoReader`
  - `helpers.DeviceInfoReader` Protocol: `name`, `klipper_prefix: str`, `running_sha(version: str | None) -> str | None`, `is_dirty(version: str | None) -> bool`
  - `helpers.ImageReporter` Protocol: `klipper_prefix`, `klipper_fields: tuple[str, ...]`, `from_klipper(name: str, values: Mapping) -> tuple[str, DeviceInfo] | None`, `wire_source(paths) -> Callable[[str], DeviceInfo | None]`
  - `helpers.device_info_reader(helper) -> DeviceInfoReader | None`, `helpers.image_reporter(helper) -> ImageReporter | None`
  - `status.reported_images(reporter: ImageReporter, klipper: Mapping[str, Mapping] | None, serials: Iterable[str], *, wire: Callable[[str], DeviceInfo | None] | None = None) -> dict[str, DeviceInfo]`
  - `pio.is_dirty(version: str | None) -> bool`

### Task 3: The flasher seam — `supports`, `target`, `select`; BOOTSEL absorbs the helper

**Interfaces:**
- Consumes: `helpers.bootsel_requester`, `FirmwareFamily.flashers`
- Produces:
  - `flashers.Device(type: str, id: str, chipset: str, state: str, fw: str, kind: str = "serial", detail: Mapping[str, Any] = {})`; kinds `KIND_SERIAL="serial"`, `KIND_CANBUS="canbus_uuid"`, `KIND_SCREEN="screen"`, `KIND_BARE="bare"`
  - `Flasher.supports(device: Device, helper: Helper | None) -> bool`
  - `Flasher.target(paths: Paths, device: Device, helper: Helper | None, *, stop_services: tuple[str, ...]) -> FlashTarget`
  - `FlashTarget.needs_services_stopped: bool | None = None`
  - `flashers.resolve(family: FirmwareFamily, device: Device, helper: Helper | None) -> Flasher | None`
  - `flashers.select(paths, family, device, helper, *, stop_services: tuple[str, ...] = ()) -> FlashTarget` (raises `NoFlasherError`)
  - `errors.NoFlasherError` (`code = "no_flasher"`)

### Task 4: Every write selects through its family

**Interfaces:**
- Consumes: `flashers.Device`, `flashers.select`, `NoFlasherError`
- Produces:
  - `write_all(bench, targets, ctx, *, on_ready=None, refused: Sequence[Mapping[str, Any]] = ()) -> dict` — each refused entry lands in `failures[]` unchanged
  - `bulk.select_targets(...)` helpers keep their names; `flashers.select_for` is gone

### Task 5: `FlashLog` is written by the loop

**Interfaces:**
- Consumes: `flashers.select`, `write_all`
- Produces:
  - `flashers.FlashRecord(key: str, mcu_type: str, fw: str, bin_sha256: str | None, fw_sha: str | None, version: str | None = None, digest_algorithm: int | None = None, digest: int | None = None, image_start: int | None = None, image_length: int | None = None)`
  - `Flasher.record(bench: Bench, target: FlashTarget) -> FlashRecord | None`
  - `Flasher.write` may return a `"confidence"` key, which `write_all` removes from `flashed[]` and passes to `FlashLog.record`
  - `FlashLog.record(key, *, mcu_type, fw, bin_sha256, fw_sha, confidence=None, version=None, digest_algorithm=None, digest=None, image_start=None, image_length=None)`

### Task 6: Tracking an unprovisioned board provisions it

**Interfaces:**
- Consumes: `helpers.for_name`, `typelist.read_config`, `lock.exclusive`
- Produces:
  - `helpers.Provisioner` Protocol: `name`, `is_unprovisioned(serial: str) -> bool`, `provision(paths: Paths, serial: str) -> str` (caller holds the op lock)
  - `helpers.provisioner(helper) -> Provisioner | None`
  - `tracking.Tracked(added: bool, chipset: str, serial: str, provisioned_from: str | None)`
  - `tracking.add_serial(paths, name, serial) -> Tracked`

### Task 7: Auto-provisioning on appearance

**Interfaces:**
- Consumes: `helpers.provisioner`, `lock.exclusive`, `errors.BusyError`
- Produces:
  - `FirmwareFamily.auto_provision: bool = False`; `firmware.PROVISIONING_HELPERS: tuple[str, ...] = ("roadrunner",)`
  - `BusWatcher(..., on_change: Callable[[dict], Any] | None)` — called with the sweep; a truthy return asks for a retry on the next poll
  - `provisioning.auto_provision(paths, devices: Mapping[str, Any], *, reporter) -> bool` (True = retry wanted)

### Task 8: One verdict

**Interfaces:**
- Consumes: `device_info.DeviceInfo`, `device_info.reader_for`, `FlashLog`
- Produces:
  - `states.UNEXPECTED_IMAGE = "unexpected_image"`
  - `verdict.Evidence(state: str, version: str | None, running_sha: str | None, dirty: bool, protocol_match: bool | None = None, info: DeviceInfo | None = None)`
  - `verdict.Expected(head: str | None, stamp: str | None = None, stamp_kind: str = STAMP_BUILT, tag_clean: bool = False, require_head: bool = False, artifact_sha: str | None = None, record: Mapping[str, Any] | None = None, digest: DeviceInfo | None = None)`; `STAMP_BUILT`, `STAMP_TAG`
  - `verdict.decide(evidence: Evidence, expected: Expected) -> DeviceStatus`

### Task 9: The loops cover every builder

**Interfaces:**
- Consumes: Tasks 3-5, 8
- Produces: CMake boards in `bulk._boards_to_flash` output (same dict keys as kconfig boards); `_require_flashable_type` deleted

### Task 10: Identity (spec §3)

**Interfaces:**
- Consumes: `helpers.Helper`
- Produces:
  - `helpers.Identifier` Protocol: `name`, `identify(paths, ports: Sequence[str]) -> dict[str, str]` (port -> device id); `helpers.identifier(helper)`
  - PlatformIO row `id` = `device_id` or by-id path

### Task 11: Docs, final gates, bench checklist
