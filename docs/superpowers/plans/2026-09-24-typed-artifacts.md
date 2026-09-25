# Typed Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each builder reports the files it staged, each with a kind and a
verified hash. Each flasher declares the kinds it accepts. Selection hands the
chosen flasher a matching file, or refuses and names the missing kind. As a
result, Klipper can be written to an RP2040 through BOOTSEL, and a CMake
family can list flashtool.

**Architecture:**
- A new leaf module, `artifacts.py`, holds `Artifact` and `Staged`.
- Each provider gains `staged(paths, type_name, family) -> Staged`. It reads
  the staged files and the sidecar, which now records a hash for each kind.
- `flashers.registry.resolve` walks the family's `flashers:` list and returns
  the first `(flasher, artifact)` pair where the flasher supports the device
  and a staged artifact is of a kind it accepts.
- `target()` takes that artifact and carries it on `FlashTarget`, so no caller
  puts file paths into `Device.detail` any more.
- A new `klipper` helper gives bootsel a way into BOOTSEL for a running
  Klipper RP2040. It uses Katapult flashtool's `-r` request.

**Tech Stack:** Python 3.11 stdlib only, pytest, ruff, mypy.

**Spec:** [docs/superpowers/specs/2026-09-24-typed-artifacts-design.md](../specs/2026-09-24-typed-artifacts-design.md).
Read the spec's "Corrections made while planning" section first. Where it
disagrees with the spec's earlier text, it wins.

## Global Constraints

- Work on branch `feat/typed-artifacts`, in its own worktree:
  `git worktree add ../mcu-updater-feat-typed-artifacts -b feat/typed-artifacts develop`.
- Use LF line endings everywhere. Run `python scripts/check_line_endings.py` before every commit.
- Use the stdlib only. Never add a dependency.
- Python 3.11 is the floor. Run the suite on the floor venv: `.venv/Scripts/python.exe`
  (create it with `uv venv --python 3.11` if it is missing).
- Keep `from __future__ import annotations` in every module you touch or create.
- Never pip-install kconfiglib.
- Before rewriting any source line, grep `scripts/mutations/` for it. Re-anchor
  any spec that matches, in the same commit. Each task lists the anchors it moves.
- Run `scripts/mutation_test.py` on one spec at a time, never in parallel, and
  never under a shell timeout shorter than the spec needs. After any
  interrupted run, read the output of `test_no_mutation_is_left_live_in_the_source`.
- Never interrupt a firmware write. Cancellation is checked between targets,
  never inside one.
- Flash tests on hardware use the bench board only, never the toolhead.
- No plugin auto-discovery. A new helper is one module plus one line in
  `helpers/registry.HELPERS` plus its name in `firmware.HELPERS`.
- Run the gate before every commit (all four must pass):
  ```bash
  .venv/Scripts/python.exe -m pytest -q
  .venv/Scripts/python.exe -m ruff check src tests scripts
  .venv/Scripts/python.exe -m mypy src
  .venv/Scripts/python.exe scripts/check_line_endings.py
  ```
- Commit voice: a conventional prefix, then a lowercase sentence with no
  trailing period. End the message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Kind names are exactly `"bin"`, `"uf2"` and `"pio_env"`. The sidecar field
  is exactly `"artifacts": {kind: {"sha256": ...}}`. The refusal data key is
  exactly `missing`.

## Review Focus

Each of these conditions is implied by the spec, but no spec test covers it.
Each one has a test in the task that owns the code.

1. **A rebuild lands between selection and filing the ledger.** The record
   must describe the bytes at the staged path when it is filed, not a
   snapshot taken at selection. Test: Task 2,
   `test_the_record_describes_the_bytes_staged_when_it_is_filed`.
2. **A staged file was replaced behind the sidecar**, and it is flashed
   anyway (a user copies in their own `.bin`). The ledger must record no hash,
   never the sidecar's hash for bytes it did not describe. Tests: Task 1,
   `test_kconfig_withholds_the_hash_of_a_bin_replaced_behind_it`, and
   `test_a_dirty_cmake_bin_is_staged_without_provenance`.
3. **A sidecar written before this change sits next to an existing `.uf2`.**
   The `.uf2` is not offered until a rebuild. `flashers: bootsel` must refuse
   with "staged no uf2 - build it first", not write an image nobody can vouch
   for. Tests: Task 1, `test_kconfig_stages_a_uf2_only_when_the_sidecar_lists_it`;
   Task 2, `test_an_unlisted_uf2_is_refused_as_missing`.
4. **A stale file is left by an earlier build.** For example, a kconfig type
   whose new build makes no `.uf2`, or a CMake tree that stopped producing a
   `.bin`. The build removes it rather than leaving an unlisted image beside
   the new one. Tests: Task 1, `test_a_build_without_a_uf2_removes_a_stale_one`;
   Task 3, `test_a_build_without_a_bin_removes_a_stale_one`.
5. **The flashed image presents a different USB serial.** This happens when
   the old image used a hardcoded `CONFIG_USB_SERIAL_NUMBER` and the new one
   uses the chip ID, or the reverse. `wait_ready` must wait for the new serial
   and say it changed, rather than time out on the old one. Tests: Task 4,
   `test_wait_ready_follows_a_hardcoded_serial` and
   `test_wait_ready_keeps_a_chip_id_serial`.

## File Map

| File | Change | Task |
| --- | --- | --- |
| `src/mcu_updater/artifacts.py` | **Create.** Kinds, `Artifact`, `Staged`, and the sidecar field helpers. | 1 |
| `src/mcu_updater/build.py` | Record a hash per kind, remove a stale `.uf2`, add a uf2 foreign-build check, add `staged()`. | 1 |
| `src/mcu_updater/providers/cmake.py` | Add `read_record`, record `artifacts`, add `staged()`. In Task 3, add `fresh_bin`, stage the `.bin`, and check it for a foreign build. | 1, 3 |
| `src/mcu_updater/providers/pio.py` | Record `artifacts`, add `staged()`. | 1 |
| `src/mcu_updater/providers/{spec,kconfig_make,platformio,registry,__init__}.py` | `Provider.staged`, and `providers.staged`. | 1 |
| `src/mcu_updater/flashers/spec.py` | `accepts`, the new `target()` signature, `FlashTarget.artifact`, `artifact_path`, `staged_record`. | 2 |
| `src/mcu_updater/flashers/registry.py` | Selection by kind, and the `missing` refusal. | 2 |
| `src/mcu_updater/flashers/{flashtool,bootsel,dfu_util,esptool}.py` | `accepts`, `target(..., artifact)`, and write and record from the artifact. | 2 |
| `src/mcu_updater/flashers/flash.py` | First install goes through `resolve(..., staged)`. | 2 |
| `src/mcu_updater/agent/methods/{flash,bulk}.py`, `src/mcu_updater/cli.py` | Stop building file payloads. | 2 |
| `src/mcu_updater/helpers/klipper.py` | **Create.** `KlipperHelper`. | 4 |
| `src/mcu_updater/helpers/{spec,roadrunner,registry}.py`, `src/mcu_updater/firmware.py` | `wait_ready` gains `type_name` and `fw`; register `klipper`. | 4 |
| `src/mcu_updater/discovery/bootsel.py` | Add `mounts_on()`. | 4 |
| `src/mcu_updater/flashers/bootsel.py` | Warn on an offset `uf2`; `settled` passes `type_name` and `fw`. | 4 |
| `scripts/mutations/artifact-selection.json` | **Create.** | 2, 4 |
| `scripts/mutations/{artifact-provenance,flashlog-loop,flasher-supports,batch-selection,bootsel-erase,loops}.json` | Re-anchor, add, and remove guards. | 1-4 |
| `tests/test_artifacts.py`, `tests/test_klipper_helper.py` | **Create.** | 1, 4 |
| `tests/test_{build,cmake,flasher_select,flashlog_loop,agent_bulk,flash}.py` | New and updated tests. | 1-4 |
| `README.md`, `docs/agent-api.md`, `docs/decisions.md`, `docs/cmake-provider.md` | Docs. | 5 |

---

### Task 1: The artifact model, and builders report what they staged

**Files:**
- Create: `src/mcu_updater/artifacts.py`
- Create: `tests/test_artifacts.py`
- Modify:
  - `src/mcu_updater/build.py`: `BuildResult`, `to_sidecar`, `artifact_status`, the build's uf2 staging, and a new `staged`
  - `src/mcu_updater/providers/cmake.py`: `record_build`, `read_sidecar`, and new `read_record` and `staged`
  - `src/mcu_updater/providers/pio.py`: `record_build`, and a new `staged`
  - `src/mcu_updater/providers/spec.py`, `kconfig_make.py`, `platformio.py`, `registry.py`, `__init__.py`
  - `scripts/mutations/artifact-provenance.json`
- Test: `tests/test_artifacts.py`, `tests/test_build.py`, `tests/test_cmake.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks rely on these exact names):
  - `mcu_updater.artifacts`:
    - `KIND_BIN = "bin"`, `KIND_UF2 = "uf2"`, `KIND_PIO_ENV = "pio_env"`
    - `Artifact(kind: str, path: str, sha256: str | None = None)`, frozen
    - `Staged(fw: str, artifacts: tuple[Artifact, ...] = (), fw_sha: str | None = None, version: str | None = None)`, frozen, with `first_of(kinds: Iterable[str]) -> Artifact | None`
    - `sidecar_field(hashes: Mapping[str, str | None]) -> dict[str, dict[str, str | None]]`
    - `recorded_kinds(side: Mapping[str, Any]) -> set[str]`
    - `recorded_sha256(side: Mapping[str, Any], kind: str, *, primary: str) -> str | None`
  - `build.staged(paths, mcu_type: str, family: FirmwareFamily) -> Staged`
  - `providers.cmake.staged(paths, type_name: str, family) -> Staged`
  - `providers.cmake.read_record(paths, type_name: str, fw: str) -> dict | None`
  - `providers.pio.staged(paths, type_name: str, family) -> Staged`
  - `Provider.staged(self, paths, type_name, family) -> Staged`, on all three providers
  - `providers.staged(paths, type_name: str, family) -> Staged`, which dispatches on `family.builder`
  - `BuildResult.uf2_sha256: str | None = None`

- [ ] **Step 1: Set up the worktree and confirm the baseline**

```bash
git worktree add ../mcu-updater-feat-typed-artifacts -b feat/typed-artifacts develop
cd ../mcu-updater-feat-typed-artifacts
uv venv --python 3.11 && .venv/Scripts/python.exe -m pip install -e . pytest ruff mypy
.venv/Scripts/python.exe -m pytest -q
```

Expected: all tests pass (2122 passed and 16 skipped at the time of writing).
Every later command in this plan runs from the worktree.

- [ ] **Step 2: Write the failing tests for the model and for each builder's `staged()`**

Create `tests/test_artifacts.py`:

```python
"""What a build staged, by kind, and how each builder's sidecar vouches for it.

A kind names what a flasher consumes. The sidecar records one hash per kind;
a sidecar written before that vouches only for its builder's primary kind,
through the legacy `bin_sha256`.
"""

from __future__ import annotations

import hashlib
import json
import os
import types

from mcu_updater import build, providers
from mcu_updater.artifacts import (
    KIND_BIN,
    KIND_PIO_ENV,
    KIND_UF2,
    Artifact,
    Staged,
    recorded_kinds,
    recorded_sha256,
    sidecar_field,
)
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.providers import cmake, pio

KLIPPER = FirmwareFamily(name="klipper", flashers=("flashtool",))
ROADRUNNER = FirmwareFamily(name="roadrunner", builder="cmake", flashers=("bootsel",))
KNOMI = FirmwareFamily(name="knomi", builder="platformio", flashers=("esptool",))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _sidecar(paths, mcu_type: str, fw: str, record: dict) -> None:
    path = paths.sidecar_file(mcu_type, fw)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


# --- the model ---------------------------------------------------------------


def test_first_of_follows_the_kinds_order_not_the_staged_order():
    staged = Staged(
        fw="klipper",
        artifacts=(Artifact(KIND_UF2, "/a.uf2"), Artifact(KIND_BIN, "/a.bin")),
    )

    assert staged.first_of((KIND_BIN, KIND_UF2)) == Artifact(KIND_BIN, "/a.bin")
    assert staged.first_of((KIND_PIO_ENV,)) is None


def test_the_sidecar_field_records_one_hash_per_kind():
    assert sidecar_field({KIND_BIN: "b", KIND_UF2: None}) == {
        "bin": {"sha256": "b"},
        "uf2": {"sha256": None},
    }


def test_recorded_sha256_reads_the_per_kind_entry():
    side = {"bin_sha256": "legacy", "artifacts": sidecar_field({KIND_BIN: "b", KIND_UF2: "u"})}

    assert recorded_sha256(side, KIND_UF2, primary=KIND_BIN) == "u"
    assert recorded_sha256(side, KIND_BIN, primary=KIND_BIN) == "b"
    assert recorded_kinds(side) == {KIND_BIN, KIND_UF2}


def test_an_old_sidecar_vouches_only_for_its_primary_kind():
    """`bin_sha256` hashed the .bin for kconfig and the .uf2 for cmake. Reading
    it for any other kind would vouch for bytes it never described."""
    old = {"bin_sha256": "legacy"}

    assert recorded_sha256(old, KIND_BIN, primary=KIND_BIN) == "legacy"
    assert recorded_sha256(old, KIND_UF2, primary=KIND_BIN) is None
    assert recorded_sha256(old, KIND_UF2, primary=KIND_UF2) == "legacy"
    assert recorded_kinds(old) == set()


# --- kconfig -----------------------------------------------------------------


def test_kconfig_stages_the_bin_with_its_verified_hash(paths):
    _write(paths.bin_file("ebb36", "klipper"), b"built")
    _sidecar(paths, "ebb36", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"built"), "version": "v1"})

    staged = build.staged(paths, "ebb36", KLIPPER)

    assert staged == Staged(
        fw="klipper",
        artifacts=(Artifact(KIND_BIN, paths.bin_file("ebb36", "klipper"), _sha(b"built")),),
        fw_sha="abc",
        version="v1",
    )


def test_kconfig_withholds_the_hash_of_a_bin_replaced_behind_it(paths):
    """Review Focus 2: flashable, but the ledger must not file the sidecar's
    hash against bytes the sidecar never described."""
    _write(paths.bin_file("ebb36", "klipper"), b"somebody else's build")
    _sidecar(paths, "ebb36", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"built")})

    [artifact] = build.staged(paths, "ebb36", KLIPPER).artifacts

    assert artifact.kind == KIND_BIN
    assert artifact.sha256 is None


def test_kconfig_stages_a_uf2_only_when_the_sidecar_lists_it(paths):
    """Review Focus 3: a .uf2 beside a pre-artifacts sidecar may be older than
    the .bin, so it waits for one rebuild."""
    _write(paths.bin_file("pico", "klipper"), b"bin")
    _write(paths.uf2_file("pico", "klipper"), b"uf2")
    _sidecar(paths, "pico", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"bin")})

    staged = build.staged(paths, "pico", KLIPPER)

    assert [a.kind for a in staged.artifacts] == [KIND_BIN]


def test_kconfig_stages_a_listed_uf2_with_its_hash(paths):
    _write(paths.bin_file("pico", "klipper"), b"bin")
    _write(paths.uf2_file("pico", "klipper"), b"uf2")
    _sidecar(
        paths,
        "pico",
        "klipper",
        {
            "fw_sha": "abc",
            "bin_sha256": _sha(b"bin"),
            "artifacts": sidecar_field({KIND_BIN: _sha(b"bin"), KIND_UF2: _sha(b"uf2")}),
        },
    )

    staged = build.staged(paths, "pico", KLIPPER)

    assert staged.first_of((KIND_UF2,)) == Artifact(
        KIND_UF2, paths.uf2_file("pico", "klipper"), _sha(b"uf2")
    )


def test_kconfig_stages_nothing_when_nothing_was_built(paths):
    assert build.staged(paths, "ebb36", KLIPPER).artifacts == ()


# --- cmake -------------------------------------------------------------------


def _cmake_record(paths, data: bytes, **extra) -> None:
    uf2 = paths.uf2_file("roadrunner", "roadrunner")
    stat = os.stat(uf2)
    _sidecar(
        paths,
        "roadrunner",
        "roadrunner",
        {
            "provider": "cmake",
            "sha": "subtree-sha",
            "version": "v1.2.3",
            "dirty": False,
            "cmake_target": "roadrunner_v1_i2c_rgb",
            "bin_sha256": _sha(data),
            "bin_size": stat.st_size,
            "bin_mtime": stat.st_mtime,
            **extra,
        },
    )


def test_cmake_stages_an_owned_uf2_with_provenance(paths):
    uf2 = _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _cmake_record(paths, b"image")

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert staged == Staged(
        fw="roadrunner",
        artifacts=(Artifact(KIND_UF2, uf2, _sha(b"image")),),
        fw_sha="subtree-sha",
        version="v1.2.3",
    )


def test_cmake_withholds_provenance_for_a_dirty_record(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _cmake_record(paths, b"image", dirty=True)

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert [(a.kind, a.sha256) for a in staged.artifacts] == [(KIND_UF2, None)]
    assert staged.fw_sha is None
    assert staged.version is None


def test_cmake_stages_nothing_without_a_uf2(paths):
    assert cmake.staged(paths, "roadrunner", ROADRUNNER) == Staged(fw="roadrunner")


def test_cmake_stages_a_listed_bin_with_its_verified_hash(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    bin_path = _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(
        paths, b"image", artifacts=sidecar_field({KIND_UF2: _sha(b"image"), KIND_BIN: _sha(b"raw")})
    )

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert staged.first_of((KIND_BIN,)) == Artifact(KIND_BIN, bin_path, _sha(b"raw"))


def test_a_dirty_cmake_bin_is_staged_without_provenance(paths):
    """Review Focus 2: a dirty build is flashable - a dirty uf2 always has been
    - but nothing it recorded reaches the ledger."""
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(
        paths,
        b"image",
        dirty=True,
        artifacts=sidecar_field({KIND_UF2: _sha(b"image"), KIND_BIN: _sha(b"raw")}),
    )

    artifact = cmake.staged(paths, "roadrunner", ROADRUNNER).first_of((KIND_BIN,))

    assert artifact is not None
    assert artifact.sha256 is None


def test_an_old_cmake_sidecar_does_not_offer_a_bin(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(paths, b"image")

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert [a.kind for a in staged.artifacts] == [KIND_UF2]
    assert staged.artifacts[0].sha256 == _sha(b"image")


# --- platformio --------------------------------------------------------------


def _knomi(tmp_path):
    return types.SimpleNamespace(name="knomi", source=str(tmp_path / "knomi"), env="knomi_v2")


def test_an_unbuilt_platformio_env_is_still_staged(paths, tmp_path, monkeypatch):
    """`pio run -t upload` builds before it uploads, so refusing an unbuilt env
    would refuse every screen nobody had built by hand."""
    display = _knomi(tmp_path)
    monkeypatch.setattr(pio, "load", lambda paths: {"knomi": display})

    staged = pio.staged(paths, "knomi", KNOMI)

    assert staged == Staged(
        fw="knomi", artifacts=(Artifact(KIND_PIO_ENV, pio.firmware_bin(display), None),)
    )


def test_a_platformio_type_that_is_not_configured_stages_nothing(paths, monkeypatch):
    monkeypatch.setattr(pio, "load", lambda paths: {})

    assert pio.staged(paths, "knomi", KNOMI) == Staged(fw="knomi")


# --- dispatch ----------------------------------------------------------------


def test_providers_staged_asks_the_familys_builder(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("ebb36", "klipper"), b"built")

    assert [a.kind for a in providers.staged(paths, "roadrunner", ROADRUNNER).artifacts] == [KIND_UF2]
    assert [a.kind for a in providers.staged(paths, "ebb36", KLIPPER).artifacts] == [KIND_BIN]
```

Append to `tests/test_build.py`. Add `import hashlib` to the imports if it is
not already there.

```python
def test_a_build_records_a_hash_per_staged_kind(paths, settings, fake_root, monkeypatch):
    reg = _registry(paths)
    _write_config(paths)
    out = fake_root / "klipper" / "out"
    out.mkdir()
    (out / "klipper.bin").write_bytes(b"firmware")
    (out / "klipper.uf2").write_bytes(b"uf2 firmware")
    monkeypatch.setattr("mcu_updater.build.run_streamed", lambda command, **kwargs: 0)

    result = build(paths, reg, settings, "board", "klipper")

    assert result.uf2_sha256 == hashlib.sha256(b"uf2 firmware").hexdigest()
    side = read_sidecar(paths, "board", "klipper")
    assert side["artifacts"] == {
        "bin": {"sha256": hashlib.sha256(b"firmware").hexdigest()},
        "uf2": {"sha256": hashlib.sha256(b"uf2 firmware").hexdigest()},
    }
    # Kept for every reader written before `artifacts` existed.
    assert side["bin_sha256"] == hashlib.sha256(b"firmware").hexdigest()


def test_a_build_without_a_uf2_removes_a_stale_one(paths, settings, fake_root, monkeypatch):
    """Review Focus 4: the sidecar stops listing it, so leaving it would put an
    image nobody can vouch for one config edit away from BOOTSEL."""
    reg = _registry(paths)
    _write_config(paths)
    out = fake_root / "klipper" / "out"
    out.mkdir()
    (out / "klipper.bin").write_bytes(b"firmware")
    os.makedirs(paths.artifact_dir("board"), exist_ok=True)
    with open(paths.uf2_file("board", "klipper"), "wb") as fh:
        fh.write(b"an older build's uf2")
    monkeypatch.setattr("mcu_updater.build.run_streamed", lambda command, **kwargs: 0)

    result = build(paths, reg, settings, "board", "klipper")

    assert result.uf2_path is None
    assert not os.path.exists(paths.uf2_file("board", "klipper"))
    assert set(read_sidecar(paths, "board", "klipper")["artifacts"]) == {"bin"}


def test_a_uf2_changed_behind_the_sidecar_is_a_foreign_build(paths, settings, fake_root, monkeypatch):
    reg = _registry(paths)
    _write_config(paths)
    out = fake_root / "klipper" / "out"
    out.mkdir()
    (out / "klipper.bin").write_bytes(b"firmware")
    (out / "klipper.uf2").write_bytes(b"uf2 firmware")
    monkeypatch.setattr("mcu_updater.build.run_streamed", lambda command, **kwargs: 0)
    build(paths, reg, settings, "board", "klipper")

    with open(paths.uf2_file("board", "klipper"), "wb") as fh:
        fh.write(b"somebody else's uf2")

    assert artifact_status(paths, "board", "klipper").reason == "foreign_build"
```

Append to `tests/test_cmake.py`:

```python
def test_a_build_records_its_uf2_under_artifacts(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(cmake.build_mod, "run_streamed", _leaves(source, "roadrunner_v1_i2c_rgb"))
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"all", "roadrunner_v1_i2c_rgb"})

    cmake.build(paths, settings, _cmake_type(source))

    record = cmake.read_sidecar(paths, _cmake_type(source))
    assert record["artifacts"] == {"uf2": {"sha256": record["bin_sha256"]}}
    assert cmake.read_record(paths, "roadrunner", "roadrunner") == record
```

- [ ] **Step 3: Run the new tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_artifacts.py tests/test_build.py tests/test_cmake.py -q`
Expected: collection error in `test_artifacts.py`, with `ModuleNotFoundError: No module named 'mcu_updater.artifacts'`.

- [ ] **Step 4: Create `src/mcu_updater/artifacts.py`**

```python
"""What a build staged, by kind - so a flasher is handed the file it can write.

A kind names what a flasher *consumes*, never which builder made it: a `bin` is
a raw image whether kconfig-make or cmake produced it. `pio_env` is the one
exception, on purpose. The PlatformIO flasher runs `pio run -t upload`, which
uploads from PlatformIO's own build directory rather than taking a file, and
nothing but a PlatformIO build produces what it consumes. The kind says so
instead of pretending that flasher takes a `bin`.

Imports nothing from this package, so `flashers.spec` and `providers` can both
depend on it without a cycle.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

#: A raw image, written at the address it was linked for.
KIND_BIN = "bin"
#: A UF2 container. Every block carries its own target address.
KIND_UF2 = "uf2"
#: A built PlatformIO env. Builder-specific by design; see the module docstring.
KIND_PIO_ENV = "pio_env"


@dataclasses.dataclass(frozen=True)
class Artifact:
    """One staged file.

    `sha256` is the hash its build recorded, and only while the bytes on disk
    still match it. `None` means "flashable, but nothing vouches for it" - and
    that is what the ledger then files, rather than a hash of other bytes.
    """

    kind: str
    path: str
    sha256: str | None = None


@dataclasses.dataclass(frozen=True)
class Staged:
    """Everything one type's build left staged, and the provenance beside it.

    `fw_sha` and `version` are in each builder's own vocabulary already
    translated: kconfig's `fw_sha`, cmake's and PlatformIO's `sha`.
    """

    fw: str
    artifacts: tuple[Artifact, ...] = ()
    fw_sha: str | None = None
    version: str | None = None

    def first_of(self, kinds: Iterable[str]) -> Artifact | None:
        """The staged artifact of the first kind in `kinds` that has one.

        The *flasher's* order, not the build's: a flasher that takes two kinds
        lists the one it prefers first.
        """
        for kind in kinds:
            for artifact in self.artifacts:
                if artifact.kind == kind:
                    return artifact
        return None


def sidecar_field(hashes: Mapping[str, str | None]) -> dict[str, dict[str, str | None]]:
    """`{kind: sha256}` as a sidecar's `artifacts` value."""
    return {kind: {"sha256": sha} for kind, sha in hashes.items()}


def recorded_kinds(side: Mapping[str, Any]) -> set[str]:
    """The kinds a sidecar says its build staged.

    Empty for a sidecar written before `artifacts` existed. Such a sidecar
    vouches only for its builder's primary kind, which each builder stages by
    its presence on disk, not by this list.
    """
    field = side.get("artifacts")
    return set(field) if isinstance(field, dict) else set()


def recorded_sha256(side: Mapping[str, Any], kind: str, *, primary: str) -> str | None:
    """The hash a sidecar recorded for `kind`, or None.

    Falls back to the legacy `bin_sha256` only for the builder's `primary`
    kind. That key hashed the `.bin` for kconfig, the `.uf2` for cmake and
    `firmware.bin` for PlatformIO, so reading it for any other kind would vouch
    for bytes it never described. A missing hash is never a disagreement: it
    reads as no provenance, and accuses nobody.
    """
    field = side.get("artifacts")
    if isinstance(field, dict):
        entry = field.get(kind)
        if isinstance(entry, dict) and entry.get("sha256"):
            return str(entry["sha256"])
    if kind == primary:
        legacy = side.get("bin_sha256")
        return str(legacy) if legacy else None
    return None
```

- [ ] **Step 5: kconfig in `build.py` — record each kind, remove a stale uf2, check it for a foreign build, add `staged()`**

Grep first: `git grep -n "recorded_bin and sha256_file" scripts/mutations/`. Two
`artifact-provenance.json` guards anchor on the bin check. **Keep those two
lines verbatim**, and add the new check below them.

Add to the imports, after `from . import firmware`:

```python
from .artifacts import (
    KIND_BIN,
    KIND_UF2,
    Artifact,
    Staged,
    recorded_kinds,
    recorded_sha256,
    sidecar_field,
)
```

In `BuildResult`, directly after the `bin_sha256` field:

```python
    #: sha256 of the `.uf2` staged beside the binary, when `make` produced one.
    #: Recorded per kind under `artifacts` so a flasher writing the `.uf2`
    #: files the hash of what it wrote, not the `.bin`'s.
    uf2_sha256: str | None = None
```

Replace `to_sidecar` with:

```python
    def to_sidecar(self) -> dict[str, Any]:
        hashes: dict[str, str | None] = {KIND_BIN: self.bin_sha256}
        if self.uf2_path is not None:
            hashes[KIND_UF2] = self.uf2_sha256
        return {
            "fw_sha": self.fw_sha,
            "config_sha256": self.config_sha256,
            # Still written: every reader older than `artifacts` reads this,
            # and it keeps meaning "the .bin's hash".
            "bin_sha256": self.bin_sha256,
            "artifacts": sidecar_field(hashes),
            "duration": round(self.duration, 2),
            "timestamp": time.time(),
            "config_rewritten": self.config_rewritten,
            "app_address": self.app_address,
            "version": self.version,
            "extra_repo_shas": self.extra_repo_shas,
        }
```

In `artifact_status`, directly after the existing two-line bin check (the
`return ArtifactStatus(FOREIGN_BUILD)` below `recorded_bin and ...`), insert:

```python
    # The same rule for the .uf2 beside it. Only a sidecar that recorded a uf2
    # hash can accuse one, and only a .uf2 that is there can be accused.
    uf2_path = paths.uf2_file(mcu_type, fw)
    recorded_uf2 = recorded_sha256(side, KIND_UF2, primary=KIND_BIN)
    if recorded_uf2 and os.path.exists(uf2_path) and sha256_file(uf2_path) != recorded_uf2:
        return ArtifactStatus(FOREIGN_BUILD)
```

In `build()`, replace the uf2 staging block (from `uf2_out: str | None = None`
through `reporter("info", f"Also staged {uf2_out}")`) with:

```python
        uf2_out: str | None = None
        compiled_uf2 = family.built_artifact(paths, "uf2")
        if not dry_run and os.path.exists(compiled_uf2):
            uf2_out = paths.uf2_file(mcu_type, fw)
            shutil.copyfile(compiled_uf2, uf2_out)
            reporter("info", f"Also staged {uf2_out}")
        elif not dry_run:
            # A .uf2 an earlier build left is not this build's, and the sidecar
            # written below no longer lists it. Left in place it is an image
            # nothing vouches for, one `flashers:` edit away from BOOTSEL.
            with contextlib.suppress(FileNotFoundError):
                os.remove(paths.uf2_file(mcu_type, fw))
```

In the `BuildResult(...)` call, after `bin_sha256=sha256_file(bin_out),`, add:

```python
            uf2_sha256=sha256_file(uf2_out) if uf2_out else None,
```

Add after `artifact_status`:

```python
def _verified(path: str, recorded: str | None) -> str | None:
    """`recorded`, while the file on disk still hashes to it."""
    if recorded and sha256_file(path) == recorded:
        return recorded
    return None


def staged(paths: Paths, mcu_type: str, family: firmware.FirmwareFamily) -> Staged:
    """What a kconfig build left staged for `mcu_type`, by kind.

    `bin` whenever the file exists: a sidecar older than `artifacts` still
    vouches for it through `bin_sha256`. `uf2` only when the sidecar lists it:
    a `.uf2` staged before sidecars recorded kinds may be older than the `.bin`
    beside it, so it waits for one rebuild rather than being trusted.

    A hash is reported only while the bytes still match it, so the ledger never
    files a recorded hash against a file somebody replaced.
    """
    fw = family.name
    side = read_sidecar(paths, mcu_type, fw) or {}
    found: list[Artifact] = []
    bin_path = paths.bin_file(mcu_type, fw)
    if os.path.exists(bin_path):
        found.append(
            Artifact(KIND_BIN, bin_path, _verified(bin_path, recorded_sha256(side, KIND_BIN, primary=KIND_BIN)))
        )
    uf2_path = paths.uf2_file(mcu_type, fw)
    if KIND_UF2 in recorded_kinds(side) and os.path.exists(uf2_path):
        found.append(
            Artifact(KIND_UF2, uf2_path, _verified(uf2_path, recorded_sha256(side, KIND_UF2, primary=KIND_BIN)))
        )
    return Staged(
        fw=fw,
        artifacts=tuple(found),
        # A sidecar from before the field existed, or a build this tool did
        # not perform: the tree's head is the same answer one step less
        # directly, and is what the flashtool ledger has always fallen back on.
        fw_sha=side.get("fw_sha") or git_head(family.source_dir(paths)),
        version=side.get("version"),
    )
```

- [ ] **Step 6: cmake — `read_record`, record `artifacts`, add `staged()`**

In `src/mcu_updater/providers/cmake.py`, add these imports:

```python
from ..artifacts import (
    KIND_BIN,
    KIND_UF2,
    Artifact,
    Staged,
    recorded_kinds,
    recorded_sha256,
    sidecar_field,
)
```

Replace `read_sidecar` with a delegating pair. Keep the docstring and comments:

```python
def read_record(paths: Paths, type_name: str, fw: str) -> dict | None:
    """This type's build record, by name, or None when there is not a usable one.

    Degrades to None on every failure - missing, unreadable, non-dict and not
    ours all mean "no provenance", and telling them apart would not change any
    answer. Takes names rather than a `CmakeType` so selection can read it
    without the validating type load.
    """
    try:
        with open(paths.sidecar_file(type_name, fw), encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    # Another provider's record in the shared sidecar path is not ours to
    # interpret, and neither is one written before this key existed: both are
    # "no provenance", which costs one rebuild and never a wrong answer.
    if record.get("provider") != BUILDER:
        return None
    return record


def read_sidecar(paths: Paths, target: CmakeType) -> dict | None:
    """`read_record` for a loaded type."""
    return read_record(paths, target.name, target.firmware)
```

In `record_build`, compute the uf2 hash once and add `artifacts`. Replace
`"bin_sha256": build_mod.sha256_file(path),` with
`"bin_sha256": uf2_sha256,`. Insert before `record = {`:

```python
    uf2_sha256 = build_mod.sha256_file(path)
    hashes: dict[str, str | None] = {KIND_UF2: uf2_sha256}
```

(Task 3 adds the `bin` to `hashes`, in the same commit that starts staging one.)

and add this entry to the dict, directly after `"bin_mtime": stat.st_mtime,`:

```python
        # One hash per staged kind. `bin_sha256` above stays the .uf2's hash,
        # which is what every reader older than this field expects.
        "artifacts": sidecar_field(hashes),
```

Add after `sidecar_describes_image`:

```python
def staged(paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
    """What this type's cmake build left staged, by kind.

    The `uf2` is the primary artifact, offered whenever it exists. Its
    provenance is reported only when the record is ours, clean and describes
    those bytes - the ownership boundary `artifact_status` draws - so rejected
    evidence cannot return through the flash ledger. A `bin` is offered when
    the record lists one, and is flashable without provenance the same way a
    dirty `uf2` always has been.
    """
    fw = family.name
    uf2_path = paths.uf2_file(type_name, fw)
    try:
        stat = os.stat(uf2_path)
    except OSError:
        return Staged(fw=fw)
    record = read_record(paths, type_name, fw) or {}
    ours = bool(record) and not record.get("dirty") and sidecar_describes_image(record, uf2_path, stat)
    side = record if ours else {}
    found = [Artifact(KIND_UF2, uf2_path, recorded_sha256(side, KIND_UF2, primary=KIND_UF2))]
    bin_path = paths.bin_file(type_name, fw)
    if KIND_BIN in recorded_kinds(record) and os.path.exists(bin_path):
        recorded = recorded_sha256(side, KIND_BIN, primary=KIND_UF2)
        verified = recorded if recorded and build_mod.sha256_file(bin_path) == recorded else None
        found.append(Artifact(KIND_BIN, bin_path, verified))
    return Staged(
        fw=fw,
        artifacts=tuple(found),
        # `sha`, not `fw_sha`: the CMake sidecar's own spelling.
        fw_sha=side.get("sha"),
        version=side.get("version"),
    )
```

Add to the `Cmake` class, after `clean`:

```python
    def staged(self, paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
        return staged(paths, type_name, family)
```

- [ ] **Step 7: PlatformIO — record `artifacts`, add `staged()`**

In `src/mcu_updater/providers/pio.py`, add
`from ..artifacts import KIND_PIO_ENV, Artifact, Staged, recorded_sha256, sidecar_field`.

In `record_build`, insert `bin_sha256 = sha256_file(path)` before
`record = {`. Replace `"bin_sha256": sha256_file(path),` with
`"bin_sha256": bin_sha256,`. Then add `"artifacts": sidecar_field({KIND_PIO_ENV: bin_sha256}),`
after `"bin_mtime": stat.st_mtime,`.

Add after `firmware_bin`:

```python
def staged(paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
    """This type's env, as the one artifact the PlatformIO flasher takes.

    Offered for every configured env, built or not: `pio run -t upload` builds
    before it uploads, so an unbuilt env is still writable, and refusing it
    would refuse every screen nobody had built by hand. Provenance only when
    the image on disk is the one we recorded.
    """
    display = load(paths).get(type_name)
    if display is None:
        return Staged(fw=family.name)
    path = firmware_bin(display)
    record = read_sidecar(paths, display) or {}
    try:
        ours = bool(record) and _is_our_image(record, path, os.stat(path))
    except OSError:
        ours = False
    side = record if ours else {}
    return Staged(
        fw=family.name,
        artifacts=(Artifact(KIND_PIO_ENV, path, recorded_sha256(side, KIND_PIO_ENV, primary=KIND_PIO_ENV)),),
        fw_sha=side.get("sha"),
        version=side.get("version"),
    )
```

- [ ] **Step 8: The Provider protocol, the two thin adapters, and `providers.staged`**

In `src/mcu_updater/providers/spec.py`, add to the `TYPE_CHECKING` block:

```python
    from ..artifacts import Staged
    from ..firmware import FirmwareFamily
```

Add to the `Provider` Protocol, after `clean`:

```python
    def staged(self, paths: Paths, type_name: str, family: FirmwareFamily) -> Staged:
        """What this builder left staged for `type_name`, by kind.

        Read at selection and again when the ledger is filed, so it takes
        `paths` rather than an `Install`: a single-device flash must not pay
        for every provider's validating load to learn which file it writes.
        """
        ...
```

In `providers/kconfig_make.py`, add `from ..artifacts import Staged` and
`from ..paths import Paths`, then add to `KconfigMake`:

```python
    def staged(self, paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
        return build_mod.staged(paths, type_name, family)
```

In `providers/platformio.py`, add `from .. import firmware`,
`from ..artifacts import Staged` and `from ..paths import Paths`, then add to `PlatformIO`:

```python
    def staged(self, paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
        return pio_mod.staged(paths, type_name, family)
```

In `providers/registry.py`, add under `from __future__ import annotations`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..artifacts import Staged
    from ..firmware import FirmwareFamily
    from ..paths import Paths
```

(Merge the `typing` import into the existing import block, so ruff's `I` rule
passes.) Then add after `by_name`:

```python
def staged(paths: Paths, type_name: str, family: FirmwareFamily) -> Staged:
    """What `family`'s builder staged for `type_name`."""
    return by_name(family.builder).staged(paths, type_name, family)
```

In `providers/__init__.py`, import `staged` from `.registry` alongside
`by_name` and `select`. Add `"staged"` to `__all__`, keeping the list sorted.

- [ ] **Step 9: Guard the new checks**

Append these to the `mutations` list in `scripts/mutations/artifact-provenance.json`,
and add `"tests/test_artifacts.py"` to its `command` list before `"-q"`:

```json
    {
      "name": "a kconfig uf2 that disagrees with its recorded hash is foreign",
      "file": "src/mcu_updater/build.py",
      "find": "    if recorded_uf2 and os.path.exists(uf2_path) and sha256_file(uf2_path) != recorded_uf2:",
      "replace": "    if False:"
    },
    {
      "name": "a kconfig build with no uf2 removes the stale one",
      "file": "src/mcu_updater/build.py",
      "find": "                os.remove(paths.uf2_file(mcu_type, fw))",
      "replace": "                pass"
    },
    {
      "name": "an old sidecar vouches only for its primary kind",
      "file": "src/mcu_updater/artifacts.py",
      "find": "    if kind == primary:\n        legacy = side.get(\"bin_sha256\")",
      "replace": "    if True:\n        legacy = side.get(\"bin_sha256\")"
    },
    {
      "name": "a kconfig uf2 waits for a sidecar that lists it",
      "file": "src/mcu_updater/build.py",
      "find": "    if KIND_UF2 in recorded_kinds(side) and os.path.exists(uf2_path):",
      "replace": "    if os.path.exists(uf2_path):"
    },
    {
      "name": "a replaced file is flashed without the recorded hash",
      "file": "src/mcu_updater/build.py",
      "find": "    if recorded and sha256_file(path) == recorded:\n        return recorded\n    return None",
      "replace": "    return recorded"
    }
```

- [ ] **Step 10: Run the tests and the specs**

```bash
.venv/Scripts/python.exe -m pytest tests/test_artifacts.py tests/test_build.py tests/test_cmake.py -q
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/artifact-provenance.json
```

Expected: the tests pass, and every mutation in the spec is caught. If one
survives, strengthen the test that should catch it. Do not weaken the
mutation.

- [ ] **Step 11: Gate and commit**

Run the four gate commands. Then:

```bash
git add src/mcu_updater/artifacts.py src/mcu_updater/build.py src/mcu_updater/providers tests/test_artifacts.py tests/test_build.py tests/test_cmake.py scripts/mutations/artifact-provenance.json
git commit -m "feat(artifacts): builders report what they staged, one hash per kind" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Selection by kind, and flashers write the artifact they were handed

**Files:**
- Modify:
  - `src/mcu_updater/flashers/spec.py`, `registry.py`, `__init__.py`
  - `src/mcu_updater/flashers/flashtool.py`, `bootsel.py`, `dfu_util.py`, `esptool.py`
  - `src/mcu_updater/flashers/flash.py` (`flash_initial_bootloader`)
  - `src/mcu_updater/agent/methods/flash.py` (`flash`, `_cmake_flash`)
  - `src/mcu_updater/agent/methods/bulk.py` (`_cmake_boards_to_flash`)
  - `src/mcu_updater/cli.py` (`_cmake_targets`)
- Mutations:
  - Create `scripts/mutations/artifact-selection.json`.
  - Re-anchor `flasher-supports.json`, `flashlog-loop.json`, `batch-selection.json` and `bootsel-erase.json`.
  - Remove one guard from `loops.json`.
- Test: `tests/test_flasher_select.py`, `tests/test_flashlog_loop.py`, `tests/test_agent_bulk.py`

**Interfaces:**
- Consumes (from Task 1):
  - `Artifact`, `Staged`, and `KIND_BIN`/`KIND_UF2`/`KIND_PIO_ENV` from `mcu_updater.artifacts`
  - `providers.staged(paths, type_name, family) -> Staged`
- Produces:
  - `Flasher.accepts: tuple[str, ...]`, the kinds a flasher takes, most preferred first
  - `Flasher.target(self, paths, device, helper, artifact: Artifact, *, stop_services) -> FlashTarget`
  - `FlashTarget.artifact: Artifact | None = None`, as the last field
  - `flashers.artifact_path(target: FlashTarget) -> str`, which raises `FlashError` when there is no artifact
  - `flashers.staged_record(bench, target, *, fw: str, kind: str) -> FlashRecord`
  - `flashers.resolve(family, device, helper, staged: Staged) -> tuple[Flasher, Artifact] | None`
  - `flashers.select(paths, family, device, helper, *, stop_services, staged: Staged | None = None) -> FlashTarget`.
    `None` reads the staged set through `providers.staged`.
  - `NoFlasherError` data gains `missing: list[str]`, and the `refusal()` dict gains `"missing"`.
  - `bootsel.target_for(uf2_file: str | Artifact, *, chipset, paths=None, type_name="", serial="", fw="", helper=None, stop_services=())`
  - `flashtool.target_for(board, *, stop_services=(), artifact: Artifact | None = None)`
  - `dfu_util.target_for(fw_bin: str | Artifact, *, chipset, dfu_serial=None)`
  - Target detail:
    - Bootsel's helper-path detail is `{"chipset", "helper", "fw"}`.
    - Its plain-path detail is `{"chipset"}`.
    - No detail holds a file path any more.

- [ ] **Step 1: Write the failing selection tests**

In `tests/test_flasher_select.py`, add `import os` and
`from mcu_updater.artifacts import KIND_BIN, KIND_UF2, Artifact, Staged`.
Then add this helper under `_family`:

```python
def _staged(fw: str = "fam", **by_kind: str) -> Staged:
    """What a build staged, as `kind=path` pairs: `_staged(bin="/a.bin")`."""
    return Staged(fw=fw, artifacts=tuple(Artifact(kind, path) for kind, path in by_kind.items()))
```

Make `_roadrunner` and `_bare_rp2040` build their `Device` with no `detail`
and no `uf2` parameter, and call them with no arguments everywhere.

Then update the existing tests to the new signatures.

**`resolve` calls**

- Every `flashers.resolve(family, device, helper)` becomes
  `flashers.resolve(family, device, helper, _staged(bin="/x.bin", uf2="/x.uf2"))`.
  The call now returns a pair, so `.name` becomes `[0].name`.
- `test_the_first_flasher_in_the_familys_order_wins`: build its device with
  no `detail`.

**Every direct `flashers.select(...)` call gets `staged=`**

| Test | Add |
| --- | --- |
| `test_the_helper_path_target_stops_services` | `staged=_staged(uf2="/tmp/rr.uf2")` |
| `test_a_board_already_in_bootsel_stops_nothing` | `staged=_staged(uf2="/tmp/rr.uf2")` |
| `test_a_board_already_in_bootsel_is_copied_not_asked` | `staged=_staged(uf2="/tmp/rr.uf2")` |
| `test_the_flashtool_target_carries_the_board_dict` | `staged=_staged(bin="/tmp/b.bin")` |
| `test_the_dfu_target_carries_the_image_and_serial` | `staged=_staged(bin="/tmp/k.bin")` |
| `test_nothing_supporting_the_device_is_refused_by_name` | `staged=_staged(bin="/x.bin")` |
| `test_a_family_with_no_flashers_says_so` | `staged=_staged(bin="/x.bin")` |

**Assertions that change**

- `test_the_helper_path_target_stops_services`:
  - replace `assert target.detail["uf2_file"] == "/tmp/rr.uf2"` with
    `assert target.artifact == Artifact(KIND_UF2, "/tmp/rr.uf2")`
  - add `assert target.detail["fw"] == "fam"`
- `test_the_flashtool_target_carries_the_board_dict`: the target now also
  carries the device's family. Assert
  `dict(target.detail) == {**board, "fw": "fam"}` and
  `target.artifact == Artifact(KIND_BIN, "/tmp/b.bin")`.
- `test_the_dfu_target_carries_the_image_and_serial`: build its device with
  no `detail`, and replace the `fw_bin` assertion with
  `assert target.artifact == Artifact(KIND_BIN, "/tmp/k.bin")`.
- `test_nothing_supporting_the_device_is_refused_by_name`: add
  `assert exc.value.data["missing"] == []`.
- `test_a_batch_refuses_one_device_without_dropping_the_rest` now reads the
  real staged set. Before `select_each`, stage the board's image, then add
  `assert entry["missing"] == []`:

```python
    os.makedirs(paths.artifact_dir("ebb36"), exist_ok=True)
    with open(paths.bin_file("ebb36", "klipper"), "wb") as fh:
        fh.write(b"built")
```

Append the new tests:

```python
# --- by kind -----------------------------------------------------------------


def _running_rp2040() -> Device:
    return _device(chipset="rp2040", type="pico", id="usb-Klipper_rp2040_E66-if00")


def test_the_flasher_is_handed_the_kind_it_accepts():
    choice = flashers.resolve(
        _family("flashtool", "bootsel"), _running_rp2040(), _Requester(), _staged(bin="/p.bin", uf2="/p.uf2")
    )

    assert choice is not None
    flasher, artifact = choice
    assert flasher.name == "flashtool"
    assert artifact == Artifact(KIND_BIN, "/p.bin")


def test_a_missing_kind_falls_through_to_the_next_listed_flasher():
    """flashtool supports the board but was staged no bin; bootsel takes the uf2."""
    choice = flashers.resolve(
        _family("flashtool", "bootsel"), _running_rp2040(), _Requester(), _staged(uf2="/p.uf2")
    )

    assert choice is not None
    assert choice[0].name == "bootsel"
    assert choice[1] == Artifact(KIND_UF2, "/p.uf2")


def test_a_missing_kind_is_refused_by_name(paths):
    with pytest.raises(NoFlasherError) as exc:
        flashers.select(
            paths,
            _family("bootsel", name="klipper"),
            _running_rp2040(),
            _Requester(),
            stop_services=("klipper",),
            staged=_staged(fw="klipper", bin="/p.bin"),
        )

    message = str(exc.value)
    assert "bootsel could write pico" in message
    assert "[firmware klipper] staged no uf2" in message
    assert "build it first" in message
    assert exc.value.data["missing"] == ["uf2"]


def test_an_unlisted_uf2_is_refused_as_missing(paths):
    """Review Focus 3, end to end: a .uf2 beside a sidecar from before
    `artifacts` is not offered, so `flashers: bootsel` asks for a rebuild
    rather than writing an image nobody can vouch for."""
    os.makedirs(paths.artifact_dir("pico"), exist_ok=True)
    for path in (paths.bin_file("pico", "klipper"), paths.uf2_file("pico", "klipper")):
        with open(path, "wb") as fh:
            fh.write(b"image")
    with open(paths.sidecar_file("pico", "klipper"), "w", encoding="utf-8") as fh:
        fh.write('{"fw_sha": "abc", "bin_sha256": "legacy"}')
    families = {"klipper": _family("bootsel", name="klipper")}
    device = Device(
        type="pico", id="usb-Klipper_rp2040_E66-if00", chipset="rp2040",
        state=STATE_BOOTSEL, fw="klipper", kind=KIND_BARE,
    )

    with pytest.raises(NoFlasherError) as exc:
        flashers.select_device(paths, families, device, stop_services=())

    assert exc.value.data["missing"] == ["uf2"]


def test_every_missing_kind_is_listed_in_the_familys_order(paths):
    with pytest.raises(NoFlasherError) as exc:
        flashers.select(
            paths,
            _family("flashtool", "bootsel", name="klipper"),
            _running_rp2040(),
            _Requester(),
            stop_services=(),
            staged=_staged(fw="klipper"),
        )

    assert exc.value.data["missing"] == ["bin", "uf2"]
    assert str(exc.value).startswith("flashtool could write")


def test_a_refusal_carries_its_missing_kinds_into_the_batch():
    exc = NoFlasherError("no", missing=["uf2"])

    assert flashers.refusal(_running_rp2040(), exc)["missing"] == ["uf2"]


def test_a_klipper_board_in_bootsel_goes_to_bootsel_with_its_uf2(paths):
    """flashtool is not narrowed by state, but a bare board is not its kind."""
    board = _device(chipset="rp2040", state=STATE_BOOTSEL, kind=KIND_BARE, type="pico", id="")

    target = flashers.select(
        paths,
        _family("flashtool", "bootsel", name="klipper"),
        board,
        None,
        stop_services=("klipper",),
        staged=_staged(fw="klipper", bin="/p.bin", uf2="/p.uf2"),
    )

    assert target.flasher == "bootsel"
    assert target.artifact == Artifact(KIND_UF2, "/p.uf2")
    assert flashers.needs_services_stopped(target) is False


def test_a_cmake_family_listing_flashtool_gets_a_board_not_a_keyerror(paths):
    """Regression: the CMake callers built `detail={"uf2_file": ...}`, and
    flashtool's target read `detail["type"]` from it."""
    device = Device(
        type="roadrunner", id=RR_SERIAL, chipset="rp2040", state=STATE_KLIPPER, fw="roadrunner"
    )

    target = flashers.select(
        paths,
        _family("flashtool", name="roadrunner"),
        device,
        None,
        stop_services=("klipper",),
        staged=_staged(fw="roadrunner", bin="/rr.bin", uf2="/rr.uf2"),
    )

    assert target.flasher == "flashtool"
    assert target.type == "roadrunner"
    assert target.id == RR_SERIAL
    assert target.detail["serial"] == RR_SERIAL
    assert target.detail["chipset"] == "rp2040"
    assert target.detail["fw"] == "roadrunner"
    assert target.artifact == Artifact(KIND_BIN, "/rr.bin")


def test_a_cmake_family_listing_flashtool_without_a_bin_is_refused(paths):
    device = Device(
        type="roadrunner", id=RR_SERIAL, chipset="rp2040", state=STATE_KLIPPER, fw="roadrunner"
    )

    with pytest.raises(NoFlasherError) as exc:
        flashers.select(
            paths,
            _family("flashtool", name="roadrunner"),
            device,
            None,
            stop_services=(),
            staged=_staged(fw="roadrunner", uf2="/rr.uf2"),
        )

    assert exc.value.data["missing"] == ["bin"]


def test_select_device_reads_what_the_familys_builder_staged(paths):
    families = {"klipper": _family("flashtool", name="klipper")}
    os.makedirs(paths.artifact_dir("ebb36"), exist_ok=True)
    bin_path = paths.bin_file("ebb36", "klipper")
    with open(bin_path, "wb") as fh:
        fh.write(b"built")

    target = flashers.select_device(paths, families, _board_device(), stop_services=())

    assert target.artifact == Artifact(KIND_BIN, bin_path)


def test_select_device_refuses_a_board_whose_build_staged_nothing(paths):
    families = {"klipper": _family("flashtool", name="klipper")}

    with pytest.raises(NoFlasherError) as exc:
        flashers.select_device(paths, families, _board_device(), stop_services=())

    assert exc.value.data["missing"] == ["bin"]
```

In `tests/test_flashlog_loop.py`, add these imports:

```python
from mcu_updater import firmware
from mcu_updater.artifacts import KIND_BIN, KIND_UF2, Artifact
from mcu_updater.devices import STATE_KLIPPER

from .conftest import seed_base_firmwares
```

Update `_Fake` in two places:
- Add `accepts: tuple[str, ...] = ("bin",)`.
- Its `target` takes `(self, paths, device, helper, artifact, *, stop_services)`
  and passes `artifact=artifact` on to `FlashTarget`.

Replace `test_flashtool_describes_the_kconfig_sidecar` with the tests below,
which also add the Review Focus 1 test and the Klipper-through-BOOTSEL ledger test:

```python
def _stage_kconfig(paths, data: bytes, *, fw_sha: str) -> str:
    os.makedirs(paths.artifact_dir("ebb36"), exist_ok=True)
    bin_path = paths.bin_file("ebb36", "klipper")
    with open(bin_path, "wb") as fh:
        fh.write(data)
    with open(paths.sidecar_file("ebb36", "klipper"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "fw_sha": fw_sha,
                "bin_sha256": hashlib.sha256(data).hexdigest(),
                "version": "CARTOGRAPHER 6.2.0",
            },
            fh,
        )
    return bin_path


def test_flashtool_describes_the_kconfig_sidecar(bench, paths):
    seed_base_firmwares(paths)
    bin_path = _stage_kconfig(paths, b"built", fw_sha="built-klipper-sha")
    target = flashers.flashtool.target_for(
        {"type": "ebb36", "serial": "S1", "chipset": "stm32g0b1xx", "fw": "klipper"},
        artifact=Artifact(KIND_BIN, bin_path),
    )

    record = flashers.Flashtool().record(bench, target)

    assert record == flashers.FlashRecord(
        key="S1",
        mcu_type="ebb36",
        fw="klipper",
        bin_sha256=hashlib.sha256(b"built").hexdigest(),
        fw_sha="built-klipper-sha",
        version="CARTOGRAPHER 6.2.0",
    )


def test_the_record_describes_the_bytes_staged_when_it_is_filed(bench, paths):
    """Review Focus 1: a rebuild between selection and the ledger. What was
    written is what the staged path held at write time, so that is what the
    record must describe - not a snapshot taken at selection."""
    seed_base_firmwares(paths)
    _stage_kconfig(paths, b"first build", fw_sha="first-sha")
    target = flashers.select(
        paths,
        firmware.resolve(paths, "klipper"),
        flashers.Device(
            type="ebb36", id="S1", chipset="stm32g0b1xx", state=STATE_KLIPPER, fw="klipper"
        ),
        None,
        stop_services=("klipper",),
    )

    _stage_kconfig(paths, b"second build", fw_sha="second-sha")
    record = flashers.Flashtool().record(bench, target)

    assert record is not None
    assert record.bin_sha256 == hashlib.sha256(b"second build").hexdigest()
    assert record.fw_sha == "second-sha"


class _Requester:
    name = "requester"

    def request_bootsel(self, bench, *, serial, chipset, ctx):
        return helpers.BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

    def wait_ready(self, bench, *, serial, chipset, ctx, type_name="", fw=""):
        return None


def test_bootsel_files_the_uf2_hash_for_klipper(bench, paths):
    """The ledger names the file that was written, and for Klipper through
    BOOTSEL that is the .uf2 - not the .bin every older record described."""
    seed_base_firmwares(paths)
    os.makedirs(paths.artifact_dir("pico"), exist_ok=True)
    staged = ((paths.bin_file("pico", "klipper"), b"bin"), (paths.uf2_file("pico", "klipper"), b"uf2"))
    for path, data in staged:
        with open(path, "wb") as fh:
            fh.write(data)
    with open(paths.sidecar_file("pico", "klipper"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "fw_sha": "klipper-sha",
                "bin_sha256": hashlib.sha256(b"bin").hexdigest(),
                "artifacts": {
                    "bin": {"sha256": hashlib.sha256(b"bin").hexdigest()},
                    "uf2": {"sha256": hashlib.sha256(b"uf2").hexdigest()},
                },
            },
            fh,
        )
    target = flashers.bootsel.target_for(
        Artifact(KIND_UF2, paths.uf2_file("pico", "klipper")),
        chipset="rp2040",
        type_name="pico",
        serial="P1",
        fw="klipper",
        helper=_Requester(),
    )

    record = flashers.Bootsel().record(bench, target)

    assert record == flashers.FlashRecord(
        key="P1",
        mcu_type="pico",
        fw="klipper",
        bin_sha256=hashlib.sha256(b"uf2").hexdigest(),
        fw_sha="klipper-sha",
        version=None,
    )
```

Two more edits in the same file:
- `_cmake_bootsel_target`: add `fw="roadrunner",` to its `target_for` call.
- `test_bootsel_files_the_board_when_the_staged_image_is_gone`: replace
  `os.unlink(target.detail["uf2_file"])` with `os.unlink(target.artifact.path)`.

In `tests/test_agent_bulk.py`, replace
`test_a_cmake_board_carries_the_staged_uf2` with the test below. Add
`from mcu_updater import firmware` and
`from mcu_updater.agent.methods.bulk import _board_request` to the imports if
they are not already there.

```python
def test_a_cmake_board_is_handed_its_staged_uf2_by_selection(bulk, paths, fake_root):
    """The board dict names the board, not its file: selection asks the builder."""
    _declare_cmake(paths, fake_root, serials=[RR_SERIAL], helper=True, staged=True)
    make_device(fake_root / "bus", "Klipper", RR_CHIPSET, RR_SERIAL)

    [board] = bulk._cmake_boards_to_flash("all")
    targets, refused = flashers.select_each(paths, firmware.load(paths), [_board_request(board)])

    assert "uf2_file" not in board
    assert refused == []
    assert targets[0].artifact is not None
    assert targets[0].artifact.path == paths.uf2_file(RR, RR)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_flasher_select.py tests/test_flashlog_loop.py tests/test_agent_bulk.py -q`

Expected: failures. The typical errors are:
- `TypeError: resolve() takes 3 positional arguments but 4 were given`
- `TypeError: select() got an unexpected keyword argument 'staged'`
- `AttributeError: 'FlashTarget' object has no attribute 'artifact'`


- [ ] **Step 3: `flashers/spec.py`: the artifact on the target, `accepts`, the new `target()`, and two helpers**

Add these imports:

```python
from ..artifacts import Artifact
from ..errors import FlashError
```

In the `Device` docstring, replace the `detail` paragraph with:

```python
    `detail` is the caller's addressing payload, carried onto the target for
    the flasher that ends up owning it: the board dict for flashtool,
    `{"display", "screen"}` for esptool. It never names a file. Which file a
    flasher writes is selection's answer (`FlashTarget.artifact`), read from
    what the family's builder staged, so no caller can hand a flasher a file
    of a kind it cannot write.
```

In `FlashTarget`, add this field directly after `needs_services_stopped`:

```python
    #: The staged file this flasher writes, chosen by selection from what the
    #: family's builder staged: the first of `Flasher.accepts` that exists.
    #: `None` only for a target built by hand in a test. Read the path through
    #: `artifact_path`, which names the problem instead of an AttributeError.
    artifact: Artifact | None = None
```

In the `Flasher` Protocol, add this after `needs_services_stopped`:

```python
    #: Artifact kinds (`artifacts.KIND_*`) this flasher can write, most
    #: preferred first. Selection hands `target()` the first of these the
    #: family's builder staged, and passes over a flasher with none of them.
    accepts: tuple[str, ...]
```

Replace the Protocol's `target` with:

```python
    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        """`device` as the target this flasher writes, carrying `artifact`.
        Only called after `supports` said yes and `artifact` is of a kind in
        `accepts`."""
        ...
```

In the Protocol's `record` docstring, replace the last paragraph (from
"Reads the build record its own builder wrote.") with:

```python
        Reads what the family's builder has staged *now*, through
        `staged_record`, rather than anything captured at selection: the
        bytes a write sent are the bytes at the staged path when it ran, and
        the op lock keeps a build from landing between the write and this.
```

Add these after `chipset_matches`:

```python
def artifact_path(target: FlashTarget) -> str:
    """The file `target` writes, or a named refusal when it has none."""
    if target.artifact is None:
        raise FlashError(
            f"no staged image was chosen for {target.type} {target.id} - it was "
            f"not selected through its family.",
            type=target.type,
            id=target.id,
        )
    return target.artifact.path


def staged_record(bench: Bench, target: FlashTarget, *, fw: str, kind: str) -> FlashRecord:
    """The ledger entry for `target`, from what `fw`'s builder has staged.

    `kind` is the kind that was written, so a board written from the `.uf2`
    files the `.uf2`'s hash. A hash is only ever the one the build recorded
    *and* the bytes still match (see `providers.staged`), so a file replaced
    behind its sidecar files no hash rather than a wrong one.
    """
    from .. import firmware, providers

    family = firmware.resolve(bench.paths, fw)
    staged = providers.staged(bench.paths, target.type, family)
    artifact = staged.first_of((kind,))
    return FlashRecord(
        key=target.id,
        mcu_type=target.type,
        fw=family.name,
        bin_sha256=artifact.sha256 if artifact is not None else None,
        fw_sha=staged.fw_sha,
        version=staged.version,
    )
```

Replace `__all__` with:

```python
__all__ = [
    "KIND_BARE",
    "KIND_CANBUS",
    "KIND_SCREEN",
    "KIND_SERIAL",
    "Bench",
    "Device",
    "FlashRecord",
    "FlashTarget",
    "Flasher",
    "artifact_path",
    "chipset_matches",
    "staged_record",
]
```

In `flashers/__init__.py`, import `artifact_path` and `staged_record` from
`.spec`, and add both to `__all__` in sorted position.

- [ ] **Step 4: `flashers/registry.py`: resolve by kind, and refuse by the missing kind**

Grep first: `git grep -n "if flasher is None" scripts/mutations/`. This finds
two specs:
- `flasher-supports.json`, whose "select refuses when nothing supports the device" mutation is moved in Step 10.
- `batch-selection.json`, which anchors in `flash.py` (Step 9).

Add `from ..artifacts import Artifact, Staged` to the imports.

Replace `resolve` and `select` with the code below, and add the two private
helpers `_unstaged` and `_refusal_error`:

```python
def resolve(
    family: FirmwareFamily, device: Device, helper: Helper | None, staged: Staged
) -> tuple[Flasher, Artifact] | None:
    """The first flasher in `family.flashers` that supports `device` and was
    staged a kind it accepts, with that artifact - or None.

    The family's order, not the registry's: the same RP2040 is flashtool's in
    `[firmware klipper]` and bootsel's in `[firmware roadrunner]`, and a global
    first match could only ever reach one of them. A flasher whose kind was not
    staged is passed over, so `flashers: flashtool, bootsel` writes a board
    already in BOOTSEL from the `.uf2` without a second list.
    """
    for name in family.flashers:
        flasher = by_name(name)
        if not flasher.supports(device, helper):
            continue
        artifact = staged.first_of(flasher.accepts)
        if artifact is not None:
            return flasher, artifact
    return None


def _unstaged(
    family: FirmwareFamily, device: Device, helper: Helper | None
) -> list[tuple[str, tuple[str, ...]]]:
    """Listed flashers that could write `device`, with the kinds they take.

    Only asked after `resolve` found nothing, so every flasher here is one
    whose kind was not staged.
    """
    return [
        (name, by_name(name).accepts)
        for name in family.flashers
        if by_name(name).supports(device, helper)
    ]


def _refusal_error(
    family: FirmwareFamily, device: Device, waiting: list[tuple[str, tuple[str, ...]]]
) -> NoFlasherError:
    """Why nothing in the list writes `device`, naming what would fix it.

    A flasher that could write the device but was staged nothing it takes is
    a missing build, not a missing flasher, and the message says which.
    """
    missing = list(dict.fromkeys(kind for _, kinds in waiting for kind in kinds))
    subject = f"{device.type} {device.id or device.chipset} while it is {device.state}"
    if waiting:
        name, kinds = waiting[0]
        message = (
            f"{name} could write {subject}, but [firmware {family.name}] staged "
            f"no {' or '.join(kinds)} - build it first."
        )
    else:
        listed = ", ".join(family.flashers) or "(none)"
        message = f"nothing in [firmware {family.name}] (flashers: {listed}) can write {subject}."
    return NoFlasherError(
        message,
        family=family.name,
        flashers=list(family.flashers),
        type=device.type,
        id=device.id,
        chipset=device.chipset,
        state=device.state,
        missing=missing,
    )


def select(
    paths: Paths,
    family: FirmwareFamily,
    device: Device,
    helper: Helper | None,
    *,
    stop_services: tuple[str, ...],
    staged: Staged | None = None,
) -> FlashTarget:
    """The target that writes `device`, chosen by its family.

    `stop_services` is required so no caller can forget it: a flasher that
    needs Klipper down and gets an empty list stops nothing.

    `staged` is what the family's builder staged for `device.type`. Left out,
    it is read here, which is what every production caller does: which file a
    board gets is the builder's answer, never the caller's.

    Raises `NoFlasherError` naming the family and its list when nothing in it
    can write the device, or naming the missing kind when something could but
    was staged nothing it takes. A batch reports that as the device's failure;
    a single-device call raises it.
    """
    if staged is None:
        from .. import providers

        staged = providers.staged(paths, device.type, family)
    choice = resolve(family, device, helper, staged)
    if choice is None:
        raise _refusal_error(family, device, _unstaged(family, device, helper))
    flasher, artifact = choice
    return flasher.target(paths, device, helper, artifact, stop_services=stop_services)
```

Replace `refusal` with:

```python
def refusal(device: Device, exc: NoFlasherError) -> dict[str, Any]:
    """A device nothing could write, in a batch's `failures[]` shape.

    The uniform slots `FlashTarget.to_json` has, with no flasher because none
    was chosen, the refusal's own sentence as the error, and the kinds a build
    would have to stage for a listed flasher to take it - empty when no listed
    flasher could write the device at all.
    """
    return {
        "type": device.type,
        "id": device.id,
        "flasher": None,
        "error": str(exc),
        "missing": list(exc.data.get("missing", [])),
    }
```

`test_nothing_supporting_the_device_is_refused_by_name` already reads
`exc.value.data["family"]`, so keyword arguments to `NoFlasherError` already
land in `.data`. No change to `errors.py` is needed.

- [ ] **Step 5: flashtool takes a `bin`, builds its board from the device, and writes and records the artifact**

In `flashers/flashtool.py`, add `from ..artifacts import KIND_BIN, Artifact`,
and add `artifact_path` and `staged_record` to the `.spec` import.

After `needs_services_stopped = True`, add:

```python
    accepts: tuple[str, ...] = (KIND_BIN,)
```

Replace `target` with:

```python
    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        # The board dict every write below reads, built from the device so a
        # caller whose detail carries none of it (a CMake family listing
        # flashtool) is still a board. A caller's own dict wins key by key:
        # its shape is on the wire, and `force` rides only there.
        identity = "uuid" if device.kind == KIND_CANBUS else "serial"
        board = {
            "type": device.type,
            identity: device.id,
            "chipset": device.chipset,
            "fw": device.fw,
            **device.detail,
        }
        return target_for(board, stop_services=stop_services, artifact=artifact)
```

In `write`, add `fw_bin=artifact_path(target),` to both calls:
- in `flash_katapult_can`, after `interface=target.detail.get("interface"),`
- in `flash_katapult`, after `force=bool(target.detail.get("force", False)),`

Before changing `record`, grep for its anchor:
`git grep -n 'side.get(\\"fw_sha\\")' scripts/mutations/`. This finds
`flashlog-loop.json`, which Step 10 re-anchors. Then replace `record` with:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        kind = target.artifact.kind if target.artifact is not None else KIND_BIN
        return staged_record(bench, target, fw=target.detail.get("fw") or "klipper", kind=kind)
```

Change `target_for`'s signature to:

```python
def target_for(
    board: dict[str, Any],
    *,
    stop_services: tuple[str, ...] = (),
    artifact: Artifact | None = None,
) -> FlashTarget:
```

and add `artifact=artifact,` after `detail=board,` in its `FlashTarget(...)`.

- [ ] **Step 6: bootsel takes a `uf2`, and records from what is staged**

Grep first: `git grep -n -e "target_type is None" -e "sidecar_describes_image" -e 'side.get(\\"sha\\")' scripts/mutations/`.
This finds three `flashlog-loop.json` anchors in `bootsel.py`. Step 10 moves them.

In `flashers/bootsel.py`, add `from ..artifacts import KIND_UF2, Artifact`,
and add `artifact_path` and `staged_record` to the `.spec` import.

After `needs_services_stopped = False`, add:

```python
    accepts: tuple[str, ...] = (KIND_UF2,)
```

Replace `target`. Keep the line `if device.state in self.states or requester is None:`
exactly as it is, because `flasher-supports.json` anchors on it:

```python
    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        requester = helpers.bootsel_requester(helper)
        if device.state in self.states or requester is None:
            return target_for(artifact, chipset=device.chipset, paths=paths)
        return target_for(
            artifact,
            chipset=device.chipset,
            type_name=device.type,
            serial=device.id,
            fw=device.fw,
            helper=requester,
            stop_services=stop_services,
        )
```

In `write`, replace `uf2 = target.detail["uf2_file"]` with `uf2 = artifact_path(target)`.

Replace `record` with:

```python
    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The image this board now holds, from what its family staged.

        `None` for a board that was already in BOOTSEL: first install writes a
        bootloader to a bare board whose `type` is a chipset string and whose
        id may be empty, and there is no tracked device to file that under.
        Only a helper-requested target names a tracked board and its family.
        """
        fw = target.detail.get("fw")
        if "helper" not in target.detail or not fw:
            return None
        return staged_record(bench, target, fw=fw, kind=KIND_UF2)
```

Change `target_for`'s signature to:

```python
def target_for(
    uf2_file: str | Artifact,
    *,
    chipset: str,
    paths: Paths | None = None,
    type_name: str = "",
    serial: str = "",
    fw: str = "",
    helper: BootselRequester | None = None,
    stop_services: tuple[str, ...] = (),
) -> FlashTarget:
```

Keep its docstring, and add this closing paragraph to it:

```
    `uf2_file` is normally the artifact selection chose. A plain path is
    taken as a `uf2` with no recorded hash, for callers holding a file rather
    than a build. `fw` names the family whose staged image the ledger reads
    back after the write.
```

Replace its body with:

```python
    artifact = uf2_file if isinstance(uf2_file, Artifact) else Artifact(KIND_UF2, uf2_file)
    if helper is not None:
        return FlashTarget(
            flasher=Bootsel.name,
            type=type_name,
            id=serial,
            stop_services=stop_services,
            detail={"chipset": chipset, "helper": helper, "fw": fw},
            needs_services_stopped=True,
            artifact=artifact,
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
        detail={"chipset": chipset},
        artifact=artifact,
    )
```

- [ ] **Step 7: dfu_util and esptool**

**`flashers/dfu_util.py`**

Add `from ..artifacts import KIND_BIN, Artifact`, add `artifact_path` to the
`.spec` import, and add `accepts: tuple[str, ...] = (KIND_BIN,)` after the
class's `needs_services_stopped`. Then replace `target` with:

```python
    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        return target_for(artifact, chipset=device.chipset, dfu_serial=device.id or None)
```

In `write`, replace `target.detail["fw_bin"],` with `artifact_path(target),`.

Replace `target_for` with:

```python
def target_for(
    fw_bin: str | Artifact, *, chipset: str, dfu_serial: str | None = None
) -> FlashTarget:
    """A bare board, as a target.

    `id` is the DFU serial when one was named. Without it there is genuinely no
    id - a DFU device has no `/dev/serial/by-id` name - and the write refuses
    rather than guessing whenever more than one board answers.
    """
    artifact = fw_bin if isinstance(fw_bin, Artifact) else Artifact(KIND_BIN, fw_bin)
    return FlashTarget(
        flasher=DfuUtil.name,
        type=chipset,
        id=dfu_serial or "",
        detail={"dfu_serial": dfu_serial, "chipset": chipset},
        artifact=artifact,
    )
```

**`flashers/esptool.py`**

Add `from ..artifacts import KIND_PIO_ENV, Artifact`, and add
`accepts: tuple[str, ...] = (KIND_PIO_ENV,)` after the class's
`needs_services_stopped`. Give `target` an `artifact: Artifact` parameter
between `helper` and `*`, and carry it through:

```python
        return dataclasses.replace(
            target, detail={**device.detail, **target.detail}, artifact=artifact
        )
```

Leave `write` and `record` unchanged. `pio run -t upload` uploads from the
env's own build directory, and that directory is what the `pio_env` artifact
names.

- [ ] **Step 8: Callers stop building file payloads**

Grep first: `git grep -n '"uf2_file": uf2,' scripts/mutations/`. This finds
the `loops.json` mutation "the cmake board dict carries the staged uf2".
Delete that mutation object: its key no longer exists, and the new bulk test
covers the same behaviour through selection.

Then edit the four call sites:

- **`agent/methods/flash.py`, `flash()`**
  - Keep the `bin_file` pre-check. It is the "build it first" RPC error that the panel switches on.
  - In `run`'s result, replace `"fw_bin": fw_bin,` with `"fw_bin": flashers.artifact_path(target),`.
- **`agent/methods/flash.py`, `_cmake_flash()`**
  - Delete the `detail={"uf2_file": fw_bin},` line from `flashers.Device(...)`.
  - In `run`'s result, replace `"fw_bin": fw_bin,` with `"fw_bin": flashers.artifact_path(target),`.
  - Keep the `uf2_file` pre-check.
- **`agent/methods/bulk.py`, `_cmake_boards_to_flash()`**
  - Delete the line `uf2 = self.paths.uf2_file(name, payload["firmware"])`.
  - Delete the dict entry `"uf2_file": uf2,`.
- **`cli.py`, `_cmake_targets()`**
  - Delete `detail={"uf2_file": fw_bin},`.
  - Keep the pre-check.

The bulk dict goes on the wire, because `fw.flash_all` returns it. Confirm
the UI does not read the key you removed:

Run: `git grep -n uf2_file ui/src`
Expected: no output.

If there is output, stop and report it. Removing the key would then need an
`API_VERSION` bump, and this plan does not include one.

- [ ] **Step 9: First install resolves against what it just built**

Grep first: `git grep -n -e "if flasher is None" -e "uf2_file" scripts/mutations/`.
This finds `batch-selection.json` and `bootsel-erase.json`. Step 10 moves both.

In `flashers/flash.py` `flash_initial_bootloader`, replace everything from
`    from .. import flashers` through the line
`        target = flasher.target(paths, device, None, stop_services=())` with:

```python
    from .. import flashers
    from ..artifacts import KIND_BIN, KIND_UF2, Artifact, Staged

    state = STATE_BOOTSEL if chipset.startswith("rp2040") else STATE_DFU
    katapult = firmware.resolve(paths, "katapult")
    if state == STATE_BOOTSEL and uf2_bin is None:
        # Before selection, not after: with no uf2 staged, selection would
        # pass bootsel over and blame the chipset instead of the build.
        raise FlashError(
            f"no .uf2 was built for {chipset}. BOOTSEL mass storage ignores "
            f"a .bin - build again once the tree produces one.",
            chipset=chipset,
        )
    device = flashers.Device(
        type=chipset,
        id=target_serial or "",
        chipset=chipset,
        state=state,
        fw=katapult.name,
        kind=flashers.KIND_BARE,
    )
    # What the caller just built, as the staged set selection chooses from.
    # Not `providers.staged`: a first install writes the image it was handed,
    # which is not necessarily one this type's build left staged.
    built = Staged(
        fw=katapult.name,
        artifacts=(Artifact(KIND_BIN, fw_bin),)
        + ((Artifact(KIND_UF2, uf2_bin),) if uf2_bin else ()),
    )
    choice = flashers.resolve(katapult, device, None, built)
    if choice is None:
        raise UnsupportedChipsetError(
            f"don't know how to perform a first-time flash for chipset '{chipset}'. "
            f"Flash katapult manually, then use 'add-serial' once it enumerates.",
            chipset=chipset,
        )
    flasher, artifact = choice

    with tempfile.TemporaryDirectory(prefix="mcu-updater-bootsel-") as staging:
        if artifact.kind == KIND_UF2:
            erasing = _stage_erasing_uf2(artifact.path, katapult_config, staging, chipset)
            reporter(
                "info",
                "Staged Katapult with the application sector erased, so the board "
                "cannot chain-load whatever it ran before.",
            )
            artifact = Artifact(KIND_UF2, erasing)
        target = flasher.target(paths, device, None, artifact, stop_services=())
```

The rest of the function, from `        bench = flashers.Bench(` onward, stays as
it is. If ruff reports `dataclasses` as unused in `flash.py`, remove that
import.

- [ ] **Step 10: Move the mutation anchors, and add the selection guards**

**`scripts/mutations/flasher-supports.json`**, mutation "select refuses when
nothing supports the device":

```json
      "find": "    if choice is None:\n        raise _refusal_error(",
      "replace": "    if False:\n        raise _refusal_error("
```

**`scripts/mutations/batch-selection.json`**, mutation "first install refuses
what katapult's list cannot write":

```json
      "find": "    if choice is None:\n        raise UnsupportedChipsetError(",
      "replace": "    if False:\n        raise UnsupportedChipsetError("
```

**`scripts/mutations/bootsel-erase.json`**, mutation "BOOTSEL copies the
erasing UF2, not Katapult alone":

```json
      "find": "            artifact = Artifact(KIND_UF2, erasing)",
      "replace": "            artifact = Artifact(KIND_UF2, artifact.path)"
```

**`scripts/mutations/flashlog-loop.json`**

Add `"tests/test_artifacts.py"` to `command`, before `"-q"`. Then replace
these four mutation objects:

```json
    {
      "name": "flashtool reads the kconfig sidecar's commit",
      "file": "src/mcu_updater/build.py",
      "find": "        fw_sha=side.get(\"fw_sha\") or git_head(family.source_dir(paths)),",
      "replace": "        fw_sha=side.get(\"sha\") or git_head(family.source_dir(paths)),"
    },
    {
      "name": "bootsel reads the cmake sidecar's commit",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "        fw_sha=side.get(\"sha\"),",
      "replace": "        fw_sha=side.get(\"fw_sha\"),"
    },
    {
      "name": "bootsel files only provenance that describes the staged image",
      "_comment": "Protect the ownership and clean-tree checks so rejected CMake evidence cannot return through the flash ledger.",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "    ours = bool(record) and not record.get(\"dirty\") and sidecar_describes_image(record, uf2_path, stat)",
      "replace": "    ours = bool(record)"
    },
    {
      "name": "a bare board has nothing to file",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "        if \"helper\" not in target.detail or not fw:\n            return None",
      "replace": "        if False:\n            return None"
    }
```

**Create `scripts/mutations/artifact-selection.json`:**

```json
{
  "_comment": "A flasher is handed only a file of a kind it accepts, a missing kind is refused by name, and cmake always offers the uf2 it built.",
  "file": "src/mcu_updater/flashers/registry.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_flasher_select.py",
    "tests/test_artifacts.py",
    "tests/test_flashlog_loop.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a flasher is only handed a kind it accepts",
      "find": "        artifact = staged.first_of(flasher.accepts)\n        if artifact is not None:",
      "replace": "        artifact = staged.first_of(flasher.accepts)\n        if True:"
    },
    {
      "name": "a missing kind is refused by name",
      "find": "    if waiting:",
      "replace": "    if False:"
    },
    {
      "name": "cmake always offers the uf2 it built",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "    found = [Artifact(KIND_UF2, uf2_path, recorded_sha256(side, KIND_UF2, primary=KIND_UF2))]",
      "replace": "    found: list[Artifact] = []"
    }
  ]
}
```

Check that each anchor occurs exactly once in its file. For example,
`git grep -c 'fw_sha=side.get("sha"),' src/mcu_updater/providers/cmake.py`
must print `1`.

- [ ] **Step 11: Find what else still calls the old signatures**

```bash
git grep -n -e "\.target(paths" -e "flashers.resolve(" -e "def target(self" -- src tests
git grep -n -e '"uf2_file"' -e 'fw_bin"\]' -- src tests
```

After this task:
- Every `target(` definition takes `artifact` before `*`.
- Every `flashers.resolve(` call passes a `Staged`.

The second grep should report exactly two hits. Leave both:
- `tests/test_flash.py`: the private `detail` of the `late` fake flasher.
- `tests/test_agent_flash.py`: `job.result["fw_bin"].endswith("roadrunner.uf2")`, which is still true.

Fix any other hit so it goes through the artifact.

If a display test now fails with `missing: ["pio_env"]`, its fixture
declares the family without `builder: platformio`. Fix the fixture. Do not
weaken the rule.

- [ ] **Step 12: Run the suite, then each spec, one at a time**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/artifact-selection.json
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/flasher-supports.json
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/flashlog-loop.json
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/batch-selection.json
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/bootsel-erase.json
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/loops.json
```

Expected:
- The suite passes, including `test_no_mutation_is_left_live_in_the_source`.
- Every mutation in every spec is caught.

- [ ] **Step 13: Gate and commit**

Run the four gate commands, then:

```bash
git add src tests scripts/mutations
git commit -m "feat(flashers): select a flasher by the kind its family staged, and refuse a missing kind by name" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: CMake stages its bin, so a CMake family can list flashtool

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py` (`build`, `record_build`, `artifact_status`, new `fresh_bin`)
- Modify: `scripts/mutations/artifact-provenance.json`
- Test: `tests/test_cmake.py`

**Interfaces:**
- Consumes (Task 1):
  - `artifacts.KIND_BIN` and `KIND_UF2`
  - `recorded_sha256(side, kind, *, primary)`
  - `sidecar_field(hashes)`
  - `cmake.staged(paths, type_name, family) -> Staged`, which already offers a `bin` the record lists
  - The `hashes: dict[str, str | None]` local in `cmake.record_build`
- Consumes (Task 2): `flashers.select`, which hands flashtool the `bin` a CMake family staged. Task 2's `test_a_cmake_family_listing_flashtool_gets_a_board_not_a_keyerror` already proves this with a hand-written sidecar. This task makes a real build produce that sidecar.
- Produces:
  - `cmake.fresh_bin(source: str, cmake_target: str) -> str | None`
  - `paths.bin_file(type, fw)` is staged by a CMake build when the tree links a `.bin`
  - The sidecar's `artifacts` gains `"bin"`

A stale `.bin` can come from two places, and this task guards both:
- **The staged copy** in `paths.bin_file`. It is left behind when the tree
  stops producing a `.bin`. The build removes it.
- **The one in `build/`**. cmake does not delete an output it no longer
  declares, so after the tree drops `pico_add_extra_outputs` (or its `.bin`
  line), last month's `build/<target>.bin` survives every later build.
  pico-sdk writes the `.bin` as a post-link step of `<target>.elf`, so a
  `.bin` this link produced is never older than the `.elf`. `fresh_bin`
  refuses one that is.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cmake.py`, add `import hashlib` and `import types` to the
imports. Add `from mcu_updater.artifacts import KIND_BIN`. Then
append:

```python
def _links(source, name, *, raw=True):
    """A run_streamed stand-in that links `name` the way pico-sdk does: the
    .elf, then the .bin and .uf2 made from it."""

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        build = source / "build"
        (build / f"{name}.elf").write_bytes(b"elf")
        if raw:
            (build / f"{name}.bin").write_bytes(f"{name} raw".encode())
        (build / f"{name}.uf2").write_bytes(name.encode())
        return 0

    return fake_run


def _build_with(paths, settings, repo, monkeypatch, fake_run):
    source = repo / "rp2040"
    (source / "build").mkdir(exist_ok=True)
    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run(source))
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"all", "roadrunner_v1_i2c_rgb"})
    cmake.build(paths, settings, _cmake_type(source))
    return source


def test_a_build_stages_the_bin_beside_the_uf2(paths, settings, repo, monkeypatch):
    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb")
    )

    with open(paths.bin_file("roadrunner", "roadrunner"), "rb") as fh:
        assert fh.read() == b"roadrunner_v1_i2c_rgb raw"
    record = cmake.read_sidecar(paths, _cmake_type(source))
    assert record["artifacts"] == {
        "bin": {"sha256": hashlib.sha256(b"roadrunner_v1_i2c_rgb raw").hexdigest()},
        "uf2": {"sha256": record["bin_sha256"]},
    }


def test_a_built_bin_is_offered_to_a_family_that_lists_flashtool(paths, settings, repo, monkeypatch):
    _build_with(paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb"))

    family = types.SimpleNamespace(name="roadrunner")
    artifact = cmake.staged(paths, "roadrunner", family).first_of((KIND_BIN,))

    assert artifact is not None
    assert artifact.path == paths.bin_file("roadrunner", "roadrunner")


def test_a_build_without_a_bin_removes_a_stale_one(paths, settings, repo, monkeypatch):
    """Review Focus 4: the record stops listing it, so leaving it would offer
    flashtool an older image beside today's uf2 the moment the family lists it."""
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"an older build's bin")

    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    assert not os.path.exists(paths.bin_file("roadrunner", "roadrunner"))
    assert set(cmake.read_sidecar(paths, _cmake_type(source))["artifacts"]) == {"uf2"}


def test_a_bin_older_than_its_elf_is_not_staged(paths, settings, repo, monkeypatch):
    """cmake does not delete an output the tree stopped declaring, so a .bin
    from before the tree dropped it survives in build/ - and predates the link."""
    build = repo / "rp2040" / "build"
    build.mkdir()
    stale = build / "roadrunner_v1_i2c_rgb.bin"
    stale.write_bytes(b"last month's bin")
    os.utime(stale, (1_000_000, 1_000_000))

    _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    assert not os.path.exists(paths.bin_file("roadrunner", "roadrunner"))


def test_a_dry_run_leaves_a_staged_bin_alone(paths, settings, repo, monkeypatch):
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"yesterday's bin")
    rehearsal = dataclasses.replace(settings, dry_run=True)

    _build_with(
        paths, rehearsal, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb", raw=False)
    )

    with open(paths.bin_file("roadrunner", "roadrunner"), "rb") as fh:
        assert fh.read() == b"yesterday's bin"


def test_a_cmake_bin_changed_behind_the_record_is_a_foreign_build(paths, settings, repo, monkeypatch):
    """flashtool writes the bin without looking at the uf2, so a bin replaced
    behind the record is foreign even while the uf2 still matches."""
    source = _build_with(
        paths, settings, repo, monkeypatch, lambda source: _links(source, "roadrunner_v1_i2c_rgb")
    )
    with open(paths.bin_file("roadrunner", "roadrunner"), "wb") as fh:
        fh.write(b"somebody else's bin")

    status = cmake.artifact_status(paths, _cmake_type(source), cmake.source_state(str(source)))

    assert status.reason == FOREIGN_BUILD
```

`FOREIGN_BUILD` and `dataclasses` are already imported in `test_cmake.py`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cmake.py -q -k "bin"`

Expected failures:
- `test_a_build_stages_the_bin_beside_the_uf2` fails with `FileNotFoundError`.
- `test_a_cmake_bin_changed_behind_the_record_is_a_foreign_build` fails on the `reason` assertion.
- `test_a_build_without_a_bin_removes_a_stale_one` fails because the file still exists.

`test_a_bin_older_than_its_elf_is_not_staged` and
`test_a_dry_run_leaves_a_staged_bin_alone` pass already. They guard the
implementation, so keep them.

- [ ] **Step 3: Implement**

Grep first: `git grep -n -e "shutil.copyfile(produced, staged)" -e 'reporter("info", f"Staged {staged}")' -e "if record.get(\"dirty\"):" scripts/mutations/`.
None of these lines is rewritten below, only added after. Check anyway,
because an anchor that spans the insertion point breaks.

In `src/mcu_updater/providers/cmake.py`:

1. Add `import contextlib` to the imports.

2. Add after `staged_uf2`:

```python
def fresh_bin(source: str, cmake_target: str) -> str | None:
    """This link's raw image, or None when it made none.

    pico-sdk writes `<target>.bin` as a post-link step of `<target>.elf`, so a
    `.bin` this link produced is never older than the `.elf`. One that is was
    left by an earlier build of a tree that has since stopped making it -
    cmake does not delete outputs it no longer declares - and staging it would
    put an older image beside today's `.uf2`. No `.elf` means nothing to
    compare against, which is also None: an image nobody can date is not
    offered to a flasher.
    """
    base = os.path.join(build_dir(source), cmake_target)
    try:
        bin_mtime = os.stat(base + ".bin").st_mtime
        elf_mtime = os.stat(base + ".elf").st_mtime
    except OSError:
        return None
    return base + ".bin" if bin_mtime >= elf_mtime else None
```

3. In `build()`, directly after `reporter("info", f"Staged {staged}")`, add:

```python
    # The raw image beside the uf2, when this link made one, so a family can
    # list flashtool or dfu_util as well as bootsel. Removed when it made
    # none, so an older build's `.bin` is never offered beside today's `.uf2`.
    # Below the dry-run return above, so a rehearsal never removes one.
    produced_bin = fresh_bin(source, target.cmake_target)
    bin_staged = paths.bin_file(target.name, target.firmware)
    if produced_bin is not None:
        shutil.copyfile(produced_bin, bin_staged)
        reporter("info", f"Staged {bin_staged}")
    else:
        with contextlib.suppress(FileNotFoundError):
            os.remove(bin_staged)
```

4. In `record_build`, directly after the Task 1 line
   `hashes: dict[str, str | None] = {KIND_UF2: uf2_sha256}`, add:

```python
    bin_path = paths.bin_file(target.name, target.firmware)
    if os.path.exists(bin_path):
        # Only ever this build's: `build()` removes a staged `.bin` its link
        # did not produce before it gets here.
        hashes[KIND_BIN] = build_mod.sha256_file(bin_path)
```

5. In `artifact_status`, directly before `    if record.get("dirty"):`, add:

```python
    # The bin is a second image from the same link, and flashtool writes it
    # without looking at the uf2 - so a bin replaced behind the record is a
    # foreign build even while the uf2 still matches.
    bin_path = paths.bin_file(target.name, target.firmware)
    recorded_bin = recorded_sha256(record, KIND_BIN, primary=KIND_UF2)
    if recorded_bin and os.path.exists(bin_path) and build_mod.sha256_file(bin_path) != recorded_bin:
        return ArtifactStatus(FOREIGN_BUILD)
```

- [ ] **Step 4: Guard it**

Append these to `mutations` in `scripts/mutations/artifact-provenance.json`:

```json
    {
      "name": "a cmake bin that disagrees with its recorded hash is foreign",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "    if recorded_bin and os.path.exists(bin_path) and build_mod.sha256_file(bin_path) != recorded_bin:",
      "replace": "    if False:"
    },
    {
      "name": "a cmake build with no fresh bin removes the staged one",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "            os.remove(bin_staged)",
      "replace": "            pass"
    },
    {
      "name": "a bin older than its elf is not staged",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "    return base + \".bin\" if bin_mtime >= elf_mtime else None",
      "replace": "    return base + \".bin\""
    },
    {
      "name": "the cmake record hashes the staged bin",
      "file": "src/mcu_updater/providers/cmake.py",
      "find": "        hashes[KIND_BIN] = build_mod.sha256_file(bin_path)",
      "replace": "        pass"
    }
```

Check that each `find` occurs exactly once in `cmake.py` (`git grep -c -F`).

- [ ] **Step 5: Run the tests and the spec**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cmake.py tests/test_artifacts.py tests/test_flasher_select.py -q
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/artifact-provenance.json
```

Expected: the tests pass, and every mutation is caught.

- [ ] **Step 6: Gate and commit**

Run the four gate commands, then:

```bash
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py scripts/mutations/artifact-provenance.json
git commit -m "feat(cmake): stage the bin beside the uf2, so a cmake family can list flashtool" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: A klipper helper that puts a running RP2040 into BOOTSEL

**Files:**
- Create: `src/mcu_updater/helpers/klipper.py`
- Modify: `src/mcu_updater/helpers/spec.py` (`BootselRequester.wait_ready`)
- Modify: `src/mcu_updater/helpers/roadrunner.py` (`wait_ready` signature)
- Modify: `src/mcu_updater/helpers/registry.py`, `src/mcu_updater/firmware.py:64` (registration)
- Modify: `src/mcu_updater/discovery/bootsel.py` (add `mounts_on`)
- Modify: `src/mcu_updater/flashers/bootsel.py` (`settled` passes `type_name` and `fw`, and `write` warns on an offset image)
- Modify: `scripts/mutations/artifact-selection.json`
- Test: create `tests/test_klipper_helper.py`. Modify `tests/test_flash.py` (two fakes), `tests/test_flasher_select.py` (one fake, one new test).

**Interfaces:**
- Consumes (Task 2):
  - `flashers.bootsel.target_for(uf2_file, *, chipset, paths=None, type_name="", serial="", fw="", helper=None, stop_services=())`
  - The helper-path target's `detail["fw"]`
  - `flashers.select(..., staged=)`
  - `NoFlasherError.data["missing"]`
- Consumes (existing code):
  - `byid.find_device(paths, chipset, serial, fw=None) -> BusDevice | None`
  - `byid.scan(paths) -> list[BusDevice]`
  - `byid.wait_for_device(paths, chipset, serial, fw, *, timeout, poll=0.5, settle=0.0, cancel=None) -> BusDevice`
  - `byid.canonical_serial` and `byid.KLIPPER_FW_NAME`
  - `bootsel.serial_topology_for(paths, port) -> str`
  - `flashers.flash.find_flashtool(paths, settings) -> str`
  - `build.run_streamed(cmd, *, cwd, reporter, cancel=None, dry_run=False) -> int`
  - `profiles.answer_lines(path)` and `profiles.answer_map(lines)`. Keys drop `CONFIG_`, and `# CONFIG_X is not set` reads as `"n"`.
  - `uf2.image_extent(bytes) -> (start, length)`, which raises `uf2.Uf2Error`
- Produces:
  - `helpers.klipper.KlipperHelper`, with `name = "klipper"`
  - `helpers.klipper.predicted_serial(config_path: str, *, current: str) -> str`
  - `discovery.bootsel.mounts_on(paths, topology) -> list[str]`
  - `BootselRequester.wait_ready(bench, *, serial, chipset, ctx, type_name, fw)`
  - `flashers.bootsel.FLASH_BASE = 0x10000000`

Selection already refuses a CAN board for this helper. `Bootsel.supports`
takes the helper path only for a `KIND_SERIAL` device, so a CAN RP2040 with
`flashers: bootsel` and `helper: klipper` gets `NoFlasherError` with
`missing == []`. That is spec correction 1: there is no CAN rule to write,
only a test that pins the refusal.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_klipper_helper.py`:

```python
"""The klipper helper: a running RP2040 into BOOTSEL, and back as Klipper.

Every hardware edge is monkeypatched, so none of this is posix_only. The
request's real behaviour - flashtool's `-r` exit code on a running RP2040,
and where a board with Katapult lands - is a bench item, not a test.
"""

from __future__ import annotations

import dataclasses
import os
import struct
import sys

import pytest

from mcu_updater import device_info, flashers
from mcu_updater.artifacts import KIND_UF2, Artifact
from mcu_updater.discovery.byid import BusDevice
from mcu_updater.errors import DeviceNotFoundError, FlashError
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import bootsel as bootsel_flasher
from mcu_updater.helpers import BootselHandoff, klipper
from mcu_updater.helpers.klipper import KlipperHelper, predicted_serial

SERIAL = "E66138935F1234AB-if00"
TOPOLOGY = "platform-3f980000.usb-usb-0:1.2"
RUNNING = BusDevice("Klipper", "rp2040", SERIAL, f"/dev/serial/by-id/usb-Klipper_rp2040_{SERIAL}")
IN_KATAPULT = BusDevice("katapult", "rp2040", SERIAL, f"/dev/serial/by-id/usb-katapult_rp2040_{SERIAL}")


@pytest.fixture
def bench(paths, settings):
    return flashers.Bench(paths=paths, settings=settings, controller=lambda name=None: None)


@pytest.fixture
def reported():
    return []


@pytest.fixture
def ctx(reported):
    return flashers.PlainContext(lambda stream, line: reported.append((stream, line)))


@pytest.fixture
def flashtool(tmp_path, monkeypatch):
    path = tmp_path / "flashtool.py"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr("mcu_updater.flashers.flash.find_flashtool", lambda paths, settings: str(path))
    return str(path)


@pytest.fixture
def requested(monkeypatch, flashtool):
    """A running Klipper RP2040 on TOPOLOGY. Returns every argv run."""
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        return 0

    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: RUNNING)
    monkeypatch.setattr(klipper.bootsel, "serial_topology_for", lambda paths, port: TOPOLOGY)
    monkeypatch.setattr(klipper.build_mod, "run_streamed", run)
    monkeypatch.setattr(klipper, "REQUEST_TIMEOUT", 0.0)
    return calls


def _config(paths, text: str) -> None:
    path = paths.config_file("pico", "klipper")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


HARDCODED = '# CONFIG_USB_SERIAL_NUMBER_CHIPID is not set\nCONFIG_USB_SERIAL_NUMBER="12345"\n'
CHIP_ID = 'CONFIG_USB_SERIAL_NUMBER_CHIPID=y\nCONFIG_USB_SERIAL_NUMBER="12345"\n'


# --- request_bootsel ---------------------------------------------------------


def test_a_running_board_is_asked_into_bootsel_through_flashtool(bench, ctx, requested, flashtool, monkeypatch):
    monkeypatch.setattr(klipper.bootsel, "mounts_on", lambda paths, topology: ["/media/pi/RPI-RP2"])

    handoff = KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)

    assert handoff == BootselHandoff(topology=TOPOLOGY)
    assert requested == [[sys.executable, flashtool, "-d", RUNNING.path, "-r"]]


def test_a_board_that_lands_in_katapult_is_refused_and_nothing_is_written(
    bench, ctx, requested, tmp_path, monkeypatch
):
    """A board with Katapult answers Klipper's bootloader request with
    Katapult, not BOOTSEL. Waiting for a volume that is not coming would time
    out with the wrong reason; copying anywhere would be worse."""
    monkeypatch.setattr(klipper.bootsel, "mounts_on", lambda paths, topology: [])
    monkeypatch.setattr(klipper.byid, "scan", lambda paths: [IN_KATAPULT])
    copied: list[str] = []
    monkeypatch.setattr(bootsel_flasher, "copy_uf2", lambda uf2, mount, ctx: copied.append(mount))
    target = bootsel_flasher.target_for(
        Artifact(KIND_UF2, _uf2(tmp_path, 0x10000000)),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )

    with pytest.raises(FlashError, match="Katapult"):
        bootsel_flasher.Bootsel().write(bench, None, target, ctx)

    assert copied == []


def test_a_board_already_in_katapult_is_refused_before_any_request(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: IN_KATAPULT)

    with pytest.raises(FlashError, match="flashtool before bootsel"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)

    assert requested == []


def test_a_chipset_that_is_not_an_rp2040_is_refused(bench, ctx, requested):
    with pytest.raises(FlashError, match="rp2040"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="stm32g0b1xx", ctx=ctx)

    assert requested == []


def test_a_board_that_is_not_on_usb_is_not_found(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.byid, "find_device", lambda paths, chipset, serial, fw=None: None)

    with pytest.raises(DeviceNotFoundError):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)


def test_a_failed_request_is_a_flash_error(bench, ctx, requested, monkeypatch):
    monkeypatch.setattr(klipper.build_mod, "run_streamed", lambda argv, **kwargs: 2)

    with pytest.raises(FlashError, match="exited 2"):
        KlipperHelper().request_bootsel(bench, serial=SERIAL, chipset="rp2040", ctx=ctx)


# --- wait_ready --------------------------------------------------------------


@pytest.fixture
def waited(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def wait(paths, chipset, serial, fw, **kwargs):
        calls.append((chipset, serial, fw))
        return RUNNING

    monkeypatch.setattr(klipper.byid, "wait_for_device", wait)
    return calls


def test_wait_ready_follows_a_hardcoded_serial(bench, ctx, reported, paths, waited):
    """Review Focus 5: the new image answers to CONFIG_USB_SERIAL_NUMBER, so
    waiting for the old serial would time out on a board that came back fine."""
    _config(paths, HARDCODED)

    KlipperHelper().wait_ready(bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper")

    assert waited == [("rp2040", "12345-if00", "Klipper")]
    assert any(stream == "warn" and "12345-if00" in line for stream, line in reported)


def test_wait_ready_keeps_a_chip_id_serial(bench, ctx, reported, paths, waited):
    _config(paths, CHIP_ID)

    KlipperHelper().wait_ready(bench, serial=SERIAL, chipset="rp2040", ctx=ctx, type_name="pico", fw="klipper")

    assert waited == [("rp2040", SERIAL, "Klipper")]
    assert not [line for stream, line in reported if stream == "warn"]


def test_a_config_that_does_not_mention_the_chip_id_uses_it(paths):
    """`USB_SERIAL_NUMBER_CHIPID` defaults to y on the rp2040, so a minimal
    config that leaves it out still answers with the chip ID."""
    _config(paths, 'CONFIG_USB_SERIAL_NUMBER="12345"\n')

    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


def test_no_config_keeps_the_current_serial(paths):
    assert predicted_serial(paths.config_file("pico", "klipper"), current=SERIAL) == SERIAL


def test_a_klipper_family_with_the_helper_still_reads_boards_through_klipper():
    """The README puts `helper: klipper` on a whole family. It has no
    `DeviceInfoReader`, so every board in it must keep Klipper's reader, not
    lose its status verdict."""
    plain = FirmwareFamily(name="klipper", flashers=("flashtool",))
    helped = FirmwareFamily(name="klipper", flashers=("bootsel",), helper="klipper")

    assert type(device_info.reader_for(helped)) is type(device_info.reader_for(plain))


def test_settled_hands_the_helper_the_type_and_family(bench, ctx, paths, waited):
    _config(paths, HARDCODED)
    target = bootsel_flasher.target_for(
        Artifact(KIND_UF2, "/unused.uf2"),
        chipset="rp2040",
        type_name="pico",
        serial=SERIAL,
        fw="klipper",
        helper=KlipperHelper(),
    )

    bootsel_flasher.Bootsel().settled(bench, target, ctx)

    assert waited == [("rp2040", "12345-if00", "Klipper")]


# --- the offset warning ------------------------------------------------------


def _uf2(tmp_path, address: int):
    block = (
        struct.pack("<IIIIIIII", 0x0A324655, 0x9E5D5157, 0x2000, address, 256, 0, 1, 0xE48BFF56)
        + b"\x00" * 476
        + struct.pack("<I", 0x0AB16F30)
    )
    path = tmp_path / f"image-{address:x}.uf2"
    path.write_bytes(block)
    return str(path)


def test_an_image_above_the_start_of_flash_is_warned_about(bench, ctx, reported, tmp_path):
    rehearsal = dataclasses.replace(bench, settings=dataclasses.replace(bench.settings, dry_run=True))
    target = bootsel_flasher.target_for(_uf2(tmp_path, 0x10004000), chipset="rp2040")

    bootsel_flasher.Bootsel().write(rehearsal, None, target, ctx)

    warnings = [line for stream, line in reported if stream == "warn"]
    assert len(warnings) == 1
    assert "0x10004000" in warnings[0]
    assert "bootloader" in warnings[0]


def test_an_image_at_the_start_of_flash_is_not(bench, ctx, reported, tmp_path):
    rehearsal = dataclasses.replace(bench, settings=dataclasses.replace(bench.settings, dry_run=True))
    target = bootsel_flasher.target_for(_uf2(tmp_path, 0x10000000), chipset="rp2040")

    bootsel_flasher.Bootsel().write(rehearsal, None, target, ctx)

    assert not [line for stream, line in reported if stream == "warn"]
```

In `tests/test_flasher_select.py`, add
`from mcu_updater.helpers.klipper import KlipperHelper` to the imports, then
append:

```python
def test_a_can_board_is_refused_by_the_klipper_helper_with_nothing_missing(paths):
    """Spec correction 1: bootsel's helper path is serial-only, so a CAN
    RP2040 is refused by selection itself - and no build would fix it."""
    board = _device(chipset="rp2040", kind=KIND_CANBUS, type="pico", id="0e0d81e4210c")

    with pytest.raises(NoFlasherError) as exc:
        flashers.select(
            paths,
            _family("bootsel", name="klipper"),
            board,
            KlipperHelper(),
            stop_services=("klipper",),
            staged=_staged(fw="klipper", uf2="/p.uf2"),
        )

    assert exc.value.data["missing"] == []
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_klipper_helper.py tests/test_flasher_select.py -q`

Expected: collection error, with
`ModuleNotFoundError: No module named 'mcu_updater.helpers.klipper'`.

- [ ] **Step 3: `wait_ready` learns the type and family**

In `helpers/spec.py`, replace `BootselRequester.wait_ready` with:

```python
    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any, type_name: str, fw: str
    ) -> None:
        """Wait until the flashed firmware confirms its durable identity.

        `type_name` and `fw` name the image just written, for a helper whose
        board can come back under a serial that image chose (Klipper's
        `CONFIG_USB_SERIAL_NUMBER`).
        """
        ...
```

In `helpers/roadrunner.py`, change `wait_ready`'s signature to:

```python
    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any, type_name: str = "", fw: str = ""
    ) -> None:
```

Its body stays as it is. A provisioned Roadrunner's serial is its own,
whatever the image.

In `flashers/bootsel.py` `settled`, extend the `requester.wait_ready(` call:

```python
            requester.wait_ready(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
                type_name=target.type,
                fw=target.detail.get("fw", ""),
            )
```

Update the three fakes that name their parameters so they accept the new keywords:
- `tests/test_flash.py`, around lines 1691 and 1737
- `tests/test_flasher_select.py:46`

In each one, change `def wait_ready(self, bench, *, serial, chipset, ctx):` to
`def wait_ready(self, bench, *, serial, chipset, ctx, type_name="", fw=""):`.
The fakes that take `*_args, **_kwargs` need no change.

- [ ] **Step 4: `mounts_on`**

In `discovery/bootsel.py`, add directly after `mount_for_topology`:

```python
def mounts_on(paths: Paths, topology: str) -> list[str]:
    """Every marker-bearing BOOTSEL mount on ``topology`` right now.

    No waiting, for a caller that is watching for more than one outcome at
    once - a volume, or the board turning up somewhere else instead.
    """
    return [mount for mount in bootsel_scan(paths) if _mount_matches_topology(mount, topology)]
```

- [ ] **Step 5: The helper**

Create `src/mcu_updater/helpers/klipper.py`:

```python
"""Klipper's BOOTSEL requester: a running RP2040 into ROM BOOTSEL, and back.

For a board with no Katapult, or one whose owner would rather not use it. The
request is Katapult's own `flashtool.py -r`, which asks Klipper to reboot into
its bootloader. Klipper's rp2040 port tries Katapult first only when the
running image was built with a bootloader offset *and* Katapult's signature is
at the start of flash; otherwise it falls back to the ROM's BOOTSEL, and the
volume mounts on the same USB port the serial device held. When both hold,
the board lands in Katapult instead - and this refuses rather than wait for a
volume that is not coming.

Only USB: Klipper's CAN bootloader request goes to Katapult or nowhere, and
`Bootsel.supports` already keeps a CAN board off this path.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from .. import build as build_mod
from .. import profiles
from ..discovery import bootsel, byid
from ..errors import DeviceNotFoundError, FlashError, ToolMissingError
from ..flashers.spec import Bench
from ..paths import REENUMERATE_TIMEOUT, Paths
from .spec import BootselHandoff

#: How long a board is given to leave Klipper after the request, for BOOTSEL
#: or for Katapult. Timing out is not a failure here: `mount_for_topology`
#: waits again for the volume and names what it found instead.
REQUEST_TIMEOUT = 15.0
REQUEST_POLL = 0.25


class KlipperHelper:
    """Ask a running Klipper RP2040 for BOOTSEL, and wait for it back."""

    name: str = "klipper"

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff:
        paths = bench.paths
        if not chipset.startswith("rp2040"):
            raise FlashError(
                f"the klipper helper can only ask an rp2040 for BOOTSEL; {serial} is a {chipset}.",
                chipset=chipset,
                serial=serial,
            )
        device = byid.find_device(paths, chipset, serial)
        if device is None:
            raise DeviceNotFoundError(
                f"{chipset} {serial} is not on USB. It has to be running Klipper to "
                f"be asked for BOOTSEL.",
                chipset=chipset,
                serial=serial,
            )
        if device.is_katapult:
            raise FlashError(
                f"{serial} is in Katapult, not Klipper, and only Klipper can be asked "
                f"for BOOTSEL. List flashtool before bootsel in this family so a board "
                f"in Katapult is written through it.",
                serial=serial,
            )
        # Before the request: the serial device is gone once the board reboots,
        # and its USB port is the only thing that ties the volume to this board.
        topology = bootsel.serial_topology_for(paths, device.path)

        from ..flashers.flash import find_flashtool

        flashtool = find_flashtool(paths, bench.settings)
        if not os.path.exists(flashtool):
            raise ToolMissingError(
                f"flashtool.py not found at {flashtool}. Is katapult installed?",
                tool="flashtool.py",
                path=flashtool,
            )
        ctx.reporter("info", f"Asking {serial} to reboot into BOOTSEL...")
        rc = build_mod.run_streamed(
            [sys.executable, flashtool, "-d", device.path, "-r"],
            cwd=paths.home,
            reporter=ctx.reporter,
        )
        if rc != 0:
            raise FlashError(
                f"flashtool.py -r exited {rc} asking {serial} for BOOTSEL. Nothing "
                f"was written.",
                serial=serial,
                returncode=rc,
            )
        _await_bootsel(paths, topology, serial=serial)
        return BootselHandoff(topology=topology)

    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any, type_name: str, fw: str
    ) -> None:
        paths = bench.paths
        expected = predicted_serial(paths.config_file(type_name, fw), current=serial)
        if expected != serial:
            ctx.reporter(
                "warn",
                f"This image sets CONFIG_USB_SERIAL_NUMBER, so the board comes back as "
                f"{expected}, not {serial}. Update the serial: line in printer.cfg, and "
                f"the serial this board is tracked under, to match.",
            )
        byid.wait_for_device(
            paths, chipset, expected, byid.KLIPPER_FW_NAME, timeout=REENUMERATE_TIMEOUT, settle=1.0
        )


def predicted_serial(config_path: str, *, current: str) -> str:
    """The serial a board flashed from this config presents on USB.

    Klipper's rp2040 port answers with its flash chip's ID - the serial it
    has now - unless the config turns `USB_SERIAL_NUMBER_CHIPID` off, and
    then with the literal `USB_SERIAL_NUMBER`. The chip-ID answer defaults to
    y, so a config that does not mention it keeps the current serial. The
    current serial's interface suffix (`-if00`) is kept, because it comes
    from udev, not the firmware.
    """
    answers = profiles.answer_map(profiles.answer_lines(config_path))
    if answers.get("USB_SERIAL_NUMBER_CHIPID", "y") != "n":
        return current
    literal = answers.get("USB_SERIAL_NUMBER", "").strip().strip('"')
    if not literal:
        return current
    return literal + current[len(byid.canonical_serial(current)) :]


def _await_bootsel(paths: Paths, topology: str, *, serial: str) -> None:
    """Wait for the board to leave Klipper, and refuse if it went to Katapult."""
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while True:
        if bootsel.mounts_on(paths, topology):
            return
        if _katapult_on(paths, topology):
            raise FlashError(
                f"{serial} rebooted into Katapult, not BOOTSEL: the running Klipper was "
                f"built for Katapult, so its bootloader request goes to it. Nothing was written. "
                f"Power-cycle the board to boot Klipper again, and list flashtool "
                f"before bootsel in this family to write it through Katapult.",
                serial=serial,
                topology=topology,
            )
        if time.monotonic() >= deadline:
            return
        time.sleep(REQUEST_POLL)


def _katapult_on(paths: Paths, topology: str) -> bool:
    """Is a Katapult device on the USB port this board held?"""
    for dev in byid.scan(paths):
        if not dev.is_katapult:
            continue
        try:
            if bootsel.serial_topology_for(paths, dev.path) == topology:
                return True
        except FlashError:
            continue
    return False
```

Register it in two places:
- `helpers/registry.py`: add `from .klipper import KlipperHelper`, and make
  the tuple
  `HELPERS: tuple[Helper, ...] = (CartographerHelper(), KlipperHelper(), KnomiSerialHelper(), RoadrunnerHelper())`.
- `firmware.py:64`: make the tuple
  `HELPERS: tuple[str, ...] = ("cartographer", "klipper", "knomi_serial", "roadrunner")`.

`helpers/__init__.py` exports no implementation classes, so leave it as it
is. The module-level `from ..flashers.spec import Bench` matches
`helpers/roadrunner.py`. `find_flashtool` is imported lazily because
`flashers.flash` imports `helpers`.

- [ ] **Step 6: Warn on an image that leaves flash's start to a bootloader**

In `flashers/bootsel.py`, add `from ..uf2 import Uf2Error, image_extent` to
the imports. Then add after `ensure_uf2`:

```python
#: Where the RP2040 maps flash. An image that starts above it expects
#: something below it - a bootloader - to jump into it.
FLASH_BASE = 0x10000000


def _warn_if_offset(uf2: str, ctx: Any) -> None:
    """Say so when an image leaves the start of flash to a bootloader.

    Not a refusal. A UF2 write replaces only the blocks it carries, so a board
    with Katapult keeps it and chain-loads this image - which is the normal
    case for a Klipper built with a bootloader offset. On a board without one,
    the start of flash is left as it was and nothing boots this. Only the
    board knows which it is.
    """
    try:
        with open(uf2, "rb") as fh:
            start, _length = image_extent(fh.read())
    except (OSError, Uf2Error):
        return
    if start > FLASH_BASE:
        ctx.reporter(
            "warn",
            f"{os.path.basename(uf2)} starts at {start:#x}, above the start of flash, "
            f"so it expects a bootloader below it. A board with Katapult keeps it and "
            f"boots this image; a board without one will not boot it - build with no "
            f"bootloader offset for that board.",
        )
```

In `Bootsel.write`, directly after `        ensure_uf2(uf2)`, add
`        _warn_if_offset(uf2, ctx)`. It goes before the dry-run branch, so a
rehearsal shows the warning too.

- [ ] **Step 7: Guard it**

In `scripts/mutations/artifact-selection.json`, add
`"tests/test_klipper_helper.py"` to `command`, before `"-q"`. Then append to
`mutations`:

```json
    {
      "name": "a board that lands in katapult is refused",
      "file": "src/mcu_updater/helpers/klipper.py",
      "find": "        if _katapult_on(paths, topology):",
      "replace": "        if False:"
    },
    {
      "name": "a chip-id serial is kept, a literal one is followed",
      "file": "src/mcu_updater/helpers/klipper.py",
      "find": "    if answers.get(\"USB_SERIAL_NUMBER_CHIPID\", \"y\") != \"n\":\n        return current",
      "replace": "    if False:\n        return current"
    },
    {
      "name": "the klipper helper refuses a board already in katapult",
      "file": "src/mcu_updater/helpers/klipper.py",
      "find": "        if device.is_katapult:",
      "replace": "        if False:"
    },
    {
      "name": "an image above the start of flash is warned about",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "    if start > FLASH_BASE:",
      "replace": "    if False:"
    },
    {
      "name": "settled hands the helper the image's family",
      "file": "src/mcu_updater/flashers/bootsel.py",
      "find": "                fw=target.detail.get(\"fw\", \"\"),",
      "replace": "                fw=\"\","
    }
```

The "chip-id" mutation makes a CHIP_ID config predict `12345-if00`, which
`test_wait_ready_keeps_a_chip_id_serial` catches. The "settled" mutation
makes the path `config_file("pico", "")`. That is `<type_dir>/pico/.config`,
which does not exist, so the wait is for the old serial.
`test_settled_hands_the_helper_the_type_and_family` catches that. If the
mutation survives anyway, change its `replace` to something the test does
catch. Do not drop the guard.

- [ ] **Step 8: Run the tests and the spec**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/mutation_test.py scripts/mutations/artifact-selection.json
```

Expected: the suite passes, and every mutation is caught.

- [ ] **Step 9: Gate and commit**

Run the four gate commands, then:

```bash
git add src/mcu_updater/helpers src/mcu_updater/firmware.py src/mcu_updater/discovery/bootsel.py src/mcu_updater/flashers/bootsel.py tests/test_klipper_helper.py tests/test_flash.py tests/test_flasher_select.py scripts/mutations/artifact-selection.json
git commit -m "feat(helpers): a klipper helper that puts a running rp2040 into bootsel" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/agent-api.md`
- Modify: `docs/decisions.md`
- Modify: `docs/cmake-provider.md`
- Modify: `docs/layout.md` (module listing)

**Interfaces:**
- Consumes: the behaviour Tasks 1-4 shipped:
  - The kinds `bin`, `uf2` and `pio_env`
  - `accepts` on each flasher
  - The `missing` refusal field
  - The `klipper` helper
  - The CMake `.bin`
  - The removed `uf2_file` board-dict key
- Produces: docs that match it. The wire change is additive: `missing` is
  new, and `uf2_file` is gone from a dict the UI never read (Task 2 Step 8
  checked). So no `API_VERSION` bump is needed.

Line numbers below are from `develop` at `985cce0`. Find each passage by its
quoted text, not by its line number.

- [ ] **Step 1: README - Features and TODO**

In `## Features`, replace the line that starts
`- [x] Per-firmware \`flashers:\` lists` with:

```markdown
- [x] Per-firmware `flashers:` lists - a family declares which tools may write it, tried in order; each tool takes the first file its builder staged of a kind it accepts (`bin`, `uf2`, `pio_env`), and a device no tool can write is refused by name - naming the missing build when that is the reason
- [x] Klipper through BOOTSEL - `helper: klipper` puts a running RP2040 into BOOTSEL with Katapult's `flashtool.py -r`, for boards without Katapult
```

In `## TODO`, delete the whole `- [ ] **BUG** A family that declares a
flasher its builder cannot feed crashes rather than refusing.` item. Task 2
fixed it: that pairing now refuses by name, or works once the CMake build
stages a `.bin` (Task 3).

- [ ] **Step 2: README - the config reference**

In the `flashers` bullet (it starts `- **\`flashers\`** - required on every`),
replace its text with:

```markdown
- **`flashers`** - required on every `[firmware ...]`. The flashers that may write
  this family, tried in order: `flashtool`, `esptool`, `dfu_util`, `bootsel`.
  Each takes one kind of staged file - `flashtool` and `dfu_util` a `.bin`,
  `bootsel` a `.uf2`, `esptool` a PlatformIO env - and one whose file was not
  staged is passed over for the next. So `flashtool, bootsel` writes a running
  Klipper board through Katapult and one already in BOOTSEL from its `.uf2`.
  A section without the key refuses the config with the line to add.
```

In the `helper` bullet, after the sentence
`Roadrunner uses \`helper: roadrunner\`.`, insert:

```markdown
  `helper: klipper` does the same for a Klipper RP2040 with no Katapult:
  `flashers: bootsel` with `helper: klipper` asks the running board for
  BOOTSEL and copies its `.uf2` (see "Klipper through BOOTSEL" below).
```

In the paragraph that starts `Both keys on \`[firmware ...]\` are optional`,
replace everything from `Which flasher writes the board is still` to the end
of that paragraph with:

```markdown
Which flasher writes the board is the family's `flashers:` list, tried in
order: the first one that can write the device *and* was staged a file it
takes. A family whose builder made no file any listed flasher takes is
refused with the kind it is missing - "bootsel could write ... but [firmware
klipper] staged no uf2 - build it first".
```

- [ ] **Step 3: README - Klipper through BOOTSEL**

Directly before `## Layout`, after the `### RP2040 cmake trees` section, add:

````markdown
### Klipper through BOOTSEL

An RP2040 that runs Klipper with no Katapult - because Katapult will not work
on the board, or its owner would rather not use it - is written through
BOOTSEL:

```ini
[firmware klipper]
source: ~/klipper
flashers: bootsel
helper: klipper
```

Build it with no bootloader offset (`Bootloader offset: No bootloader` in
menuconfig) so it produces a `klipper.uf2` that starts at the start of flash.
A `.uf2` built for Katapult's offset is written anyway, with a warning: on a
board without Katapult nothing boots it.

To flash, the helper stops Klipper and asks the running board for BOOTSEL with
Katapult's `flashtool.py -r` (Katapult's source is still needed for its
`flashtool.py`, not on the board). It copies the `.uf2` to the volume on the
same USB port, then waits for the board to come back as Klipper. If the config
sets a literal `CONFIG_USB_SERIAL_NUMBER`, the board comes back under that
serial, not its chip ID, and the flash says so. Update `printer.cfg` to match.

A board that has Katapult *and* runs a Klipper built for Katapult's offset
lands in Katapult, not BOOTSEL, when asked. The flash refuses with nothing
written. List `flashtool` before `bootsel`
(`flashers: flashtool, bootsel`) and a board with Katapult is written through
it from the `.bin`, while one already in BOOTSEL still gets the `.uf2`.

USB only: a CAN board cannot be asked for BOOTSEL this way, and selection
refuses it by name.
````

In `### RP2040 cmake trees`, directly after the fenced config example, add:

```markdown
A tree that also links a `.bin` (pico-sdk's `pico_add_extra_outputs`) has it
staged beside the `.uf2`, so a CMake family may list `flashtool` or
`dfu_util` as well as `bootsel`.
```

- [ ] **Step 4: `docs/agent-api.md`**

1. In the `fw.flash` cmake paragraph, replace
   `whose \`data\` carries \`family\`, \`flashers\`, \`type\`, \`id\`, \`chipset\` and \`state\`.`
   with:

   ```markdown
   whose `data` carries `family`, `flashers`, `type`, `id`, `chipset`, `state`
   and `missing` - the kinds (`bin`, `uf2`, `pio_env`) a listed flasher could
   have written the board from, had the family's build staged one. Empty when
   no listed flasher could write the board at all; non-empty means "build it
   first", not "change the list".
   ```

2. In the bulk paragraph that starts `**A cmake type's declared \`serials:\`
   are included too**`, delete the sentence
   `Its board dict carries \`uf2_file\` beside the usual keys, because a BOOTSEL write copies an image rather than driving a bootloader protocol.`
   Replace it with:

   ```markdown
   Its board dict has the same keys as a kconfig board's; which staged file a
   flasher writes is chosen by selection, from the kinds the family's build
   staged, never carried in the dict.
   ```

3. In the `failures[]` refusal paragraph (it starts
   `Selection goes through each device's`), replace the JSON example and the
   sentence before it with:

   ````markdown
   batch. It appears in the job's `failures[]` with `"flasher": null`, an
   `error` naming the family and its list, and `missing` - the kinds a build
   would have to stage for a listed flasher to take it:

   ```json
   {"type": "bttebb36", "id": "2900...", "flasher": null, "missing": [],
    "error": "nothing in [firmware klipper] (flashers: dfu_util) can write bttebb36 2900... while it is klipper."}
   {"type": "pico", "id": "E661...", "flasher": null, "missing": ["uf2"],
    "error": "bootsel could write pico E661... while it is klipper, but [firmware klipper] staged no uf2 - build it first."}
   ```
   ````

- [ ] **Step 5: `docs/decisions.md`**

Under `### Do not infer builder/flasher compatibility from the staged
filename`, append this paragraph after the existing two:

```markdown
Resolved without a matrix (2026-09-24): a builder now reports what it staged
*by kind* - `bin`, `uf2`, `pio_env` - and each flasher declares the kinds it
`accepts`. That is the machine-readable evidence the paragraph above asks for:
the builder says which file it made, not a filename guessed at by selection.
Selection hands a flasher the first staged file of a kind it takes, and a
family whose builder staged nothing its flashers take is refused with the
missing kind. It still does not judge what is *inside* a file: a `.bin`
flashtool is handed is trusted to be an application, exactly as before. Do
not grow `accepts` into a content check.
```

- [ ] **Step 6: `docs/cmake-provider.md`**

In `## Helper-backed flashing`:

1. Replace `The RP2040 flashtool route is untouched for families that list \`flashtool\`.`
   with:

   ```markdown
   A tree that also links a `.bin` has it staged beside the `.uf2` and recorded
   in the sidecar's `artifacts`, so a CMake family may list `flashtool` too:
   flashtool is handed the `.bin`, bootsel the `.uf2`. A `.bin` older than the
   `.elf` it came from is left out - cmake does not delete an output the tree
   stopped declaring - and the staged copy is removed when a build makes none.
   ```

2. Replace the whole paragraph that starts `That list is authoritative and is
   not checked against what the builder stages` with:

   ```markdown
   That list is authoritative - see [decisions.md](decisions.md), "Do not infer
   builder/flasher compatibility from the staged filename". A listed flasher
   whose kind this build did not stage is passed over, and a family left with
   none is refused with the missing kind, never a crash.
   ```

- [ ] **Step 7: The module listings**

This task adds these files:
- `src/mcu_updater/artifacts.py`
- `src/mcu_updater/helpers/klipper.py`
- `tests/test_artifacts.py`
- `tests/test_klipper_helper.py`
- `scripts/mutations/artifact-selection.json`

Find where the codebase lists its modules:

Run: `git grep -n -e "helpers/roadrunner" -e "providers/cmake" -- docs/layout.md README.md`

In each listing that names its siblings, add a one-line entry for each new
file, in the same style:
- `artifacts.py`: "the artifact kinds, `Artifact` and `Staged`: what a builder staged, by kind"
- `helpers/klipper.py`: "Klipper's BOOTSEL requester: a running RP2040 into BOOTSEL through `flashtool.py -r`"

Add the tests and the mutation spec only where the listing already names
tests or specs.

- [ ] **Step 8: Check and commit**

Run: `git grep -n -e uf2_file -e "KeyError" -- README.md docs/agent-api.md docs/cmake-provider.md docs/decisions.md`

Expected: no hit that describes the removed board-dict key or the fixed
crash. The `uf2_file` pre-check in `fw.flash` still exists in code but is not
documented by that name.

Run the four gate commands, then:

```bash
git add README.md docs/agent-api.md docs/decisions.md docs/cmake-provider.md docs/layout.md
git commit -m "docs: typed artifacts, and klipper through bootsel" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Bench checks (after Task 5, on the bench board only)

These need hardware, and no test can stand in for them. Run each one on the
bench RP2040, never the toolhead.

1. **`flashtool.py -d <by-id> -r` on a running Klipper RP2040 with no
   Katapult.** Check two things:
   - It exits 0.
   - The board comes up as RPI-RP2 on the same USB port.

   If it exits non-zero even though the board reboots, the request's exit-code
   check needs an allowance.
2. **The same request on an RP2040 with Katapult, running an offset
   Klipper.** The board lands in Katapult, and the flash refuses with nothing
   written. Katapult survives. Then check that the error's advice works:
   power-cycle the board and confirm it boots Klipper.
3. **Klipper through BOOTSEL, end to end,** on a bare RP2040 with
   `flashers: bootsel` and `helper: klipper`. Check each of these:
   - The copy lands on the matched volume.
   - The board comes back as Klipper.
   - No offset warning appears, because the build has no bootloader offset.
4. **`serial_topology_for` given a by-id path** for both the Klipper device
   and the Katapult device. It must resolve to the same topology.
5. **A Roadrunner flash shows no offset warning.** Confirm its `.uf2` starts
   at `0x10000000`.
6. **The Roadrunner tree's `build/` holds `<target>.bin` and `<target>.elf`**
   beside `<target>.uf2`. If there is no `.bin`, a CMake family cannot list
   `flashtool` until the tree adds `pico_add_extra_outputs`. That is
   documented, not a bug.
7. ~~**An offset Klipper `.uf2` onto a Katapult RP2040 held in BOOTSEL by
   hand**, with `flashers: flashtool, bootsel`. Check three things:
   - Selection picks bootsel, with the `.uf2`.
   - The offset warning appears.
   - The board comes back in Katapult or Klipper, with Katapult intact.

   This is the only route that writes Klipper through BOOTSEL onto a board
   with Katapult. A running board is always asked through Katapult (item 2).~~
   Unreachable, since a tracked Klipper board in BOOTSEL is never handed to
   selection (ruled on in review, not in the original design).
