# One Pipeline, Plan 1: Config and Inventory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One type list feeds every reader, klipper and katapult are declared like any other firmware, device presence comes from one injected inventory, and the CLI's `status`/`add-serial`/`remove-serial`/`remove-type` cover every type, which fixes the Roadrunner board that is tracked in the UI but invisible to the CLI.

**Architecture:** A new core module `typelist.py` walks every `[type]` section once. `Registry.load`, `pio.load` and `cmake.load` become builder-filtered views over it. Builder-specific keys take the `<seam module>_<param>` spelling and old spellings are refused. `firmware.resolve()` stops inventing families, `install.sh` seeds the two base sections through a new `seed.py`, and the flashtool path falls back to the declared katapult source. A new core module `inventory.py` joins declared identities with an injected by-id/CAN sweep. The agent's status paths and the CLI both read it, and a new `tracking.py` holds the serial/type mutations both surfaces share.

**Tech Stack:** Python 3.11+ stdlib only, pytest, ruff, mypy, bash (`install.sh`), Vue/TypeScript UI (vitest, eslint, vite) for one type-field removal.

**Spec:** `docs/superpowers/specs/2026-09-14-one-pipeline-design.md` (sections 1, 2 and 4, plus "Order of work" steps 1-4 and "Testing"). Executors read both.

## Global Constraints

- LF line endings in every file; `python scripts/check_line_endings.py` passes.
- Standard library only at runtime; Python 3.11 floor; every module starts with `from __future__ import annotations`.
- Mutation testing uses `scripts/mutation_test.py`, one spec per invocation, never interrupted, never a throwaway script.
- Before rewriting any line, grep `scripts/mutations/` for it; re-anchor every spec that finds it **in the same commit**.
- Work on a topic branch in its own worktree. Never `git worktree remove --force`. Never bare `git stash` / `git stash pop`.
- Conventional lowercase commit subjects. Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Docs change in the same task as the behaviour they describe. Do not read `docs/backlog.md`.
- Do not push, merge, or touch the bench printer's checkout or services.
- Wire shapes do not change: `targets[]`, RPC names and params, the `fw.status` keys `"displays"`, `"profile"`, `"env"`, `"klipper_section"`, `"device_map"` in payloads, on-disk paths (including `data_dir/displays/<env>.build.json`), and CLI flags.
- Config keys (spec §2): `env:` → `platformio_env:`, `profile:` → `kconfig_make_profile:`, `device_map:` → `knomi_serial_device_map:`, `klipper_section:` removed. `cmake_target:` and `[firmware] cmake_args:` are unchanged. An old spelling is refused with a message naming the new one. No migrations.
- The seeded sections are exactly:
  ```ini
  [firmware klipper]
  source: ~/klipper
  flashers: flashtool

  [firmware katapult]
  source: ~/katapult
  flashers: dfu_util, bootsel
  ```
  Nothing reads `flashers:` in this plan.

Gates, run from the worktree root (the UI gates from `ui/`):

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
# per spec touched, one at a time:
python scripts/mutation_test.py scripts/mutations/<spec>.json
# UI (Task 6 only):
cd ui && npx vitest run && npx eslint . && npx vite build
```

---

## Rulings

These are decided. The ones marked ★ change behaviour or scope and the user should know about them.

1. **The views stay builder-selected.** `Registry.load` still holds only `kconfig_make` types, `pio.load` only `platformio`, and `cmake.load` only `cmake`. Removing the views is plan 2.
2. ★ **The mixed-builder refusal stays** (it moves into `typelist.validate`) until plan 2's loops make one type per builder unnecessary.
3. ★ **`Registry.save()` stops deleting sections.** A type's section is deleted by `remove_type` / `remove_declared_type`, never as a side effect of a save. `_is_foreign_builder` goes away.
4. ★ **The key renames need a hand edit** on any printer with a PlatformIO or kconfig type that sets `env:` or `profile:`. The refusal message says what to rename.
5. ★ **`add_type` refuses undeclared families.** The registry checks its own document and names the section to add.
6. ★ **A declared family with no `source:` still builds from `~/<name>`.** `FirmwareFamily.source_dir` falls back to `os.path.join(paths.home, self.name)`. `Paths.fw_dir` is removed.
7. ★ **`firmware.names()` becomes `tuple(sorted(families))`.** There are no built-ins, so klipper no longer lists before katapult. The picker, `--fw` choices and the `firmware_families` payload become alphabetical. `McuType.fw_order()` becomes declaration order, as spec §1 says.
8. ★ **The inventory matches by exact serial only, without a chipset filter**, and keeps "exactly one sighting is a match". A kconfig board whose by-id chipset segment differs from its type's `chipset:` now shows online where `device_state` showed it offline. That is the same rule `_cmake_target` already uses.
9. **The inventory is identity × presence in this plan.** Running state (FlashLog, Klipper objects, device info) joins it in plan 2. `bus()` keeps its own scan. CAN rows carry presence (`uuid.lower()` is in the canbus results) and state `"unknown"`; `type_status`'s CAN loop is unchanged.
10. **CLI `status` prints serial rows only** (no CAN rows), as it does today, but now for every type.
11. ★ **CLI `add-serial` refuses a serial tracked under another type and takes the registry lock**, the same as `fw.serial.add`. Today the CLI does neither.
12. **The `paths` fixture does not auto-seed.** Tests that need klipper/katapult declared say so with `seed_base_firmwares(paths)` or `with_base_firmwares(text)`. That keeps the refusal testable.
13. **Agent `fw.type.remove` works for any builder** (Task 9). The `roadrunner_unprovisioned` refusal in `serial_add` stays until plan 2 removes it.
14. **`McuType._is_bootloader` keeps its `fw == "katapult"` fallback.** It is Paths-free and used where no families dict was passed.
15. ★ **Undeclared katapult on `fw.canbus.scan`:** `find_flashtool` raises `ConfigCorruptError` (naming `[firmware katapult]`). `canbus_scan` catches it and reports it in `message`, keeping report-don't-raise. `discovery/canbus.py` and the flash paths (`flash.py:152`, `:506`) let it propagate, because a flash against a misdeclared config should stop.
16. **`providers.selection._declared_builders` stays lenient.** It reads `typelist.read_config`, which never raises for an undeclared family. It must never be routed through `firmware.resolve()`, which raises after Task 6 and would let one bad section break resolving another name.
17. **`_pio_target`'s device `"firmware": None`** (status.py:1253) stays; `docs/agent-api.md` documents it. Task 4 changes only the `what=` label.
18. ★ **The Task 6 test sweep is large.** Around 26 test files build registries on a fresh tree. The task gives a mechanical procedure rather than a line list.

**Out of scope (plan 2):** device-info handlers; `flashers:` lists, `supports()`, and the missing-`flashers:` refusal; auto-provision and provision-on-track (including removing `roadrunner_unprovisioned`, agent `registry.py:236-255`); the one verdict; build/flash/update-all loops and retiring their refusals; removing the three views; spec §3 identity (by-id paths, `device_id`, knomi `identify`); running state in the inventory.

**Worktree setup (before Task 1):**

```bash
cd C:/git/github/mcu-updater-one-pipeline
git worktree add ../mcu-updater-one-pipeline-config -b feat/one-pipeline-config docs/one-pipeline
cd ../mcu-updater-one-pipeline-config
python -m pytest -q   # expect: all pass, the baseline
```

## File map

| File | Status | Responsibility |
| --- | --- | --- |
| `src/mcu_updater/typelist.py` | create (T1) | The one walk over `[type]` sections; lenient `read`, strict `validate`, key-spelling refusal (T3) |
| `src/mcu_updater/config.py` | modify (T1,2,3,6) | `Registry.load` becomes a view; save ownership; `kconfig_make_profile`; `fw_order`; `add_type` validation |
| `src/mcu_updater/providers/pio.py`, `providers/cmake.py` | modify (T1,3) | Views over the type list; `platformio_env`, `knomi_serial_device_map` |
| `src/mcu_updater/providers/selection.py` | modify (T1) | Lenient builder map from the type list |
| `src/mcu_updater/sections.py` | modify (T2) | Drop `is_type_section`; docstring |
| `src/mcu_updater/paths.py`, `stop_services.py`, `providers/spec.py`, `providers/platformio.py`, `agent/methods/status.py`, `agent/methods/bulk.py`, `agent/methods/_api.py`, `flashers/flash.py`, `cli.py` | modify (T4) | Display vocabulary removed |
| `src/mcu_updater/seed.py` | create (T5) | Seeds `[firmware klipper]`/`[firmware katapult]` |
| `install.sh` | modify (T5) | Finds/clones sources, runs the seeder |
| `src/mcu_updater/firmware.py` | modify (T5,6) | `SEEDED_KEYS`; strict `resolve`; no `BUILTIN` |
| `src/mcu_updater/flashers/flash.py` | modify (T7) | Flashtool fallback from the katapult family |
| `src/mcu_updater/inventory.py` | create (T8) | Declared identity × injected sweep |
| `src/mcu_updater/tracking.py` | create (T9) | Serial/type mutations shared by agent and CLI |
| `mcu-updater.cfg`, `tests/fixtures/registry.cfg` | modify (T3,5) | New key spellings; base sections |
| `tests/conftest.py` | modify (T5) | `BASE_FIRMWARES`, `with_base_firmwares`, `seed_base_firmwares` |
| New tests | create | `test_typelist.py`, `test_seed.py`, `test_install_script.py`, `test_flashtool_path.py`, `test_inventory.py` |
| New mutation specs | create | `type-keys.json` (T3), `flashtool-path.json` (T7), `inventory.json` (T8), `cli-every-type.json` (T9) |

---

### Task 1: One type loader, and the three loaders become views

**Files:**
- Create: `src/mcu_updater/typelist.py`
- Modify: `src/mcu_updater/config.py:55` (import), `:330-447` (`Registry.load`)
- Modify: `src/mcu_updater/providers/pio.py:139-203` (`load`)
- Modify: `src/mcu_updater/providers/cmake.py:99-145` (`load`)
- Modify: `src/mcu_updater/providers/selection.py:36-72` (`_declared_builders`)
- Modify: `scripts/mutations/pio-provider-selection.json`, `scripts/mutations/application-firmware.json`
- Modify: `docs/decisions.md` (new entry)
- Test: `tests/test_typelist.py`

**Interfaces:**
- Produces:
  - `typelist.Block(doc: CfgDocument, section: str)`, with `.get(key) -> str | None`, `.get_list(key) -> list[str]`, `.get_csv(key) -> list[str] | None`, `.has(key) -> bool`
  - `typelist.TypeEntry(name, section, firmwares: tuple[str, ...], chipset: str, serials: tuple[str, ...], canbus_uuids: tuple[str, ...], stop_services: tuple[str, ...] | None, block: Block, builders: tuple[str, ...])`, with the property `builder -> str` (`""` when no firmware)
  - `typelist.read(doc, families) -> list[TypeEntry]` (never raises)
  - `typelist.read_doc(paths) -> CfgDocument | None` (raises `ConfigCorruptError` on an unreadable file or duplicate sections)
  - `typelist.read_config(paths) -> tuple[list[TypeEntry], dict[str, FirmwareFamily]]` (lenient: `([], {})` on `OSError`)
  - `typelist.validate(entries, families, *, path: str) -> None`
  - `typelist.load(paths) -> list[TypeEntry]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_typelist.py`:

```python
"""The one type list: every [type] section, whatever builds it.

Three loaders used to walk these sections and keep only the builder each knew.
The CLI used the kconfig one, so a Roadrunner type existed for the agent and
not for `status`. These tests pin the single walk and prove the three
remaining loaders are views over it.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater import providers, typelist
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.config import Registry
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.providers import cmake as cmake_mod
from mcu_updater.providers import pio as pio_mod

FAMILIES = (
    "[firmware klipper]\nsource: ~/klipper\n\n"
    "[firmware katapult]\nsource: ~/katapult\n\n"
    "[firmware knomi_serial]\nsource: ~/knomi_serial\nbuilder: platformio\n\n"
    "[firmware roadrunner]\nsource: ~/rr\nbuilder: cmake\n\n"
)

EXAMPLE_ORDER = [
    "mmb_can",
    "bttebb36",
    "flylllplusbuffer",
    "hexadistrofusion",
    "OctopusMAXEZ",
    "cartographer",
    "knomi",
    "roadrunner",
]


def _put_config(paths, text: str) -> None:
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def test_every_type_is_listed_in_file_order(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    assert [entry.name for entry in typelist.load(paths)] == EXAMPLE_ORDER


def test_each_entry_carries_the_builder_of_its_family(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    builders = {entry.name: entry.builder for entry in typelist.load(paths)}
    assert builders["roadrunner"] == "cmake"
    assert builders["knomi"] == "platformio"
    assert builders["bttebb36"] == "kconfig_make"


def test_a_cmake_type_keeps_its_identity_and_its_own_block(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    rr = next(entry for entry in typelist.load(paths) if entry.name == "roadrunner")
    assert rr.firmwares == ("roadrunner",)
    assert rr.serials == ("RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ",)
    assert rr.block.has("cmake_target")
    assert not rr.block.has("platformio_env")


def test_read_never_refuses():
    doc = CfgDocument("[type orphan]\nfirmware: nowhere\n\n[type bare]\nchipset: x\n")
    entries = typelist.read(doc, {})
    assert [(e.name, e.firmwares, e.builder) for e in entries] == [
        ("orphan", ("nowhere",), "kconfig_make"),
        ("bare", (), ""),
    ]


REFUSALS = [
    pytest.param(FAMILIES + "[type board]\nchipset: stm32f072xb\n", "declares no firmware: key", id="no-firmware"),
    pytest.param(FAMILIES + "[type board]\nfirmware: klipperr\n", "not a known family", id="unknown"),
    pytest.param(
        FAMILIES + "[type board]\nfirmware: klipper, roadrunner\n", "different tools", id="mixed"
    ),
]


@pytest.mark.parametrize("text,needle", REFUSALS)
@pytest.mark.parametrize("load", [typelist.load, Registry.load], ids=["typelist", "registry"])
def test_the_strict_load_refuses(paths, load, text, needle):
    _put_config(paths, text)
    with pytest.raises(ConfigCorruptError, match=needle):
        load(paths)


def test_no_config_file_is_an_empty_list(paths):
    assert typelist.load(paths) == []


def test_the_three_views_partition_the_one_list(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    every = {entry.name for entry in typelist.load(paths)}
    platformio = set(pio_mod.load(paths))
    cmake = set(cmake_mod.load(paths))
    kconfig = set(Registry.load(paths).names())
    assert platformio == {"knomi"}
    assert cmake == {"roadrunner"}
    assert kconfig == every - platformio - cmake


def test_selection_is_not_broken_by_another_types_bad_section(paths):
    """A PlatformIO type with no env is pio.load's refusal, not selection's."""
    _put_config(
        paths,
        FAMILIES
        + "[type knomi]\nfirmware: knomi_serial\n\n"
        + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n",
    )
    assert providers.provider_of(paths, "board") == providers.KconfigMake.name
```

Note: `test_a_cmake_type_keeps_its_identity_and_its_own_block` checks `platformio_env`, so it passes before and after Task 3.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_typelist.py -q`
Expected: collection error, `ImportError: cannot import name 'typelist'`.

- [ ] **Step 3: Create `src/mcu_updater/typelist.py`**

```python
"""One walk over every ``[type <name>]`` section, whatever builds it.

``Registry.load``, ``pio.load`` and ``cmake.load`` each walked these sections
and kept only the builder they knew, so a type could exist for the agent and
not for the CLI - a Roadrunner tracked in the UI was invisible to ``status``.
This module is the one walk. Those loaders are views over it until their
callers read the list directly.

Two halves, on purpose:

* :func:`read` never raises. An entry that names no firmware, or a family
  nobody declared, simply has no usable builder, so a question about one name
  is never broken by a malformed section elsewhere. ``providers.selection``
  depends on that.
* :func:`validate` is the strict half, and :func:`load` is both.

Builder-specific keys stay in :attr:`TypeEntry.block`. Each builder parses its
own block, so nothing here needs to know which builder is which.
"""

from __future__ import annotations

import dataclasses
import os

from . import firmware, sections
from .cfgdoc import CfgDocument
from .errors import ConfigCorruptError
from .paths import Paths


@dataclasses.dataclass(frozen=True)
class Block:
    """One ``[type]`` section, read through the document that holds it."""

    doc: CfgDocument
    section: str

    def get(self, key: str) -> str | None:
        return self.doc.get(self.section, key)

    def get_list(self, key: str) -> list[str]:
        return self.doc.get_list(self.section, key)

    def get_csv(self, key: str) -> list[str] | None:
        return self.doc.get_csv(self.section, key)

    def has(self, key: str) -> bool:
        return key in self.doc.options(self.section)


@dataclasses.dataclass(frozen=True)
class TypeEntry:
    """A declared type: its identity, and the block its builder reads."""

    name: str
    section: str
    firmwares: tuple[str, ...]
    chipset: str
    serials: tuple[str, ...]
    canbus_uuids: tuple[str, ...]
    #: ``None`` when the key is absent, which inherits the family's list.
    stop_services: tuple[str, ...] | None
    block: Block
    #: One per declared firmware, in declaration order.
    builders: tuple[str, ...]

    @property
    def builder(self) -> str:
        """The builder of the first declared family, or "" when there is none."""
        return self.builders[0] if self.builders else ""


def _builder_of(fw: str, families: dict[str, firmware.FirmwareFamily]) -> str:
    family = families.get(fw)
    return family.builder if family is not None else firmware.DEFAULT_BUILDER


def read(doc: CfgDocument, families: dict[str, firmware.FirmwareFamily]) -> list[TypeEntry]:
    """Every ``[type]`` section in `doc`, in file order. Never raises."""
    out: list[TypeEntry] = []
    for declared in sections.read(doc):
        block = Block(doc, declared.section)
        fws = tuple(block.get_csv("firmware") or ())
        stop = block.get_csv("stop_services")
        out.append(
            TypeEntry(
                name=declared.name,
                section=declared.section,
                firmwares=fws,
                chipset=(block.get("chipset") or "").strip(),
                serials=tuple(block.get_list("serials")),
                canbus_uuids=tuple(block.get_list("canbus_uuids")),
                stop_services=None if stop is None else tuple(stop),
                block=block,
                builders=tuple(_builder_of(fw, families) for fw in fws),
            )
        )
    return out


def read_doc(paths: Paths) -> CfgDocument | None:
    """The config document, or None when there is no file.

    Refuses an unreadable file and duplicate sections: only the first copy of a
    section is read, so everything in a later one would be silently ignored.
    """
    path = paths.registry_file
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError as exc:
        raise ConfigCorruptError(f"could not read {path}: {exc}", path=path) from exc
    if doc.duplicate_sections:
        dupes = ", ".join(f"[{name}]" for name in doc.duplicate_sections)
        raise ConfigCorruptError(
            f"{path}: duplicate section(s) {dupes}. Only the first copy is read, so "
            f"everything in the later one is silently ignored - merge them into one.",
            path=path,
            value=doc.duplicate_sections,
        )
    return doc


def read_config(
    paths: Paths,
) -> tuple[list[TypeEntry], dict[str, firmware.FirmwareFamily]]:
    """The type list and the families, leniently: no file is neither."""
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError:
        return [], {}
    families = firmware.load_from_doc(doc)
    return read(doc, families), families


def validate(
    entries: list[TypeEntry],
    families: dict[str, firmware.FirmwareFamily],
    *,
    path: str,
) -> None:
    """Refuse what `read` let through."""
    known = firmware.names_of(families)
    for entry in entries:
        if not entry.firmwares:
            # Refused, not defaulted to klipper. Silence used to mean klipper,
            # which is exactly the implicit behaviour this key exists to remove.
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' declares no firmware: key. Every type "
                f"must name at least one firmware family it runs, e.g. "
                f"'firmware: klipper'.",
                path=path,
                type=entry.name,
            )
        for fw in entry.firmwares:
            if fw not in known:
                # A typo here would otherwise build and flash klipper at a board
                # that runs something else.
                raise ConfigCorruptError(
                    f"{path}: '{entry.name}' declares firmware '{fw}', which is not "
                    f"a known family. Known: {', '.join(known)}. Declare it with a "
                    f"[firmware {fw}] section, or fix the spelling.",
                    path=path,
                    type=entry.name,
                    value=fw,
                )
        if len(set(entry.builders)) > 1:
            # One builder per type until the build loop (plan 2) makes that
            # unnecessary.
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' declares firmware families built by "
                f"different tools ({', '.join(sorted(set(entry.builders)))}): "
                f"{', '.join(entry.firmwares)}. A type is built by exactly one "
                f"provider - split it into two types if it genuinely needs "
                f"both.",
                path=path,
                type=entry.name,
                value=list(entry.firmwares),
            )


def load(paths: Paths) -> list[TypeEntry]:
    """Every declared type, refusing a config that is not well-formed."""
    doc = read_doc(paths)
    if doc is None:
        return []
    families = firmware.load_from_doc(doc)
    entries = read(doc, families)
    validate(entries, families, path=paths.registry_file)
    return entries
```

- [ ] **Step 4: Make `Registry.load` a view**

In `src/mcu_updater/config.py`, change line 55 to `from . import firmware, sections, typelist`. Replace the body of `Registry.load` (lines 332-447, from `path = paths.registry_file` through `return cls(types, doc)`) with:

```python
        path = paths.registry_file
        doc = typelist.read_doc(paths)
        if doc is None:
            return cls({}, CfgDocument())

        # Which families exist is itself config, and it is in this same
        # document - so read it from the doc already parsed rather than
        # reopening the file once per registry load.
        families_map = firmware.load_from_doc(doc)
        entries = typelist.read(doc, families_map)
        typelist.validate(entries, families_map, path=path)

        types: dict[str, McuType] = {}
        for entry in entries:
            if entry.builder != "kconfig_make":
                # Another builder's type. Its provider's view reads it from the
                # same list; this registry holds the kconfig_make types.
                continue
            name, block = entry.name, entry.block
            mcu = McuType(name=name, chipset=entry.chipset)
            mcu.serials = list(entry.serials)
            mcu.canbus_uuids = list(entry.canbus_uuids)
            mcu.firmwares = list(entry.firmwares)
            mcu.profile = (block.get("profile") or "").strip()
            mcu.stop_services = block.get_csv("stop_services")
            # Only the families this type actually declares - not every
            # globally-declared [firmware ...] section. mcu.fw() is
            # setdefault, so iterating every family here would seed a phantom
            # slot for each one on every type.
            for fw in mcu.firmwares:
                cfg = mcu.fw(fw)
                cfg.extra_args = (block.get(f"{fw}_extra_args") or "").strip()
                for raw_patch in block.get_list(f"{fw}_makefile_patches"):
                    patch = MakefilePatch.parse(raw_patch)
                    if patch is None:
                        raise ConfigCorruptError(
                            f"{path}: could not parse a makefile patch for '{name}': "
                            f"{raw_patch!r}. Expected '<file> {PATCH_SEPARATOR} <line>'.",
                            path=path,
                            type=name,
                            value=raw_patch,
                        )
                    cfg.makefile_patches.append(patch)
                cfg.extra_repos = block.get_list(f"{fw}_extra_repos")
            types[name] = mcu

        return cls(types, doc)
```

- [ ] **Step 5: Make `pio.load` a view**

Replace `load` in `src/mcu_updater/providers/pio.py` (139-203) with the following. The `stop_services`/legacy logic and the `PioType(...)` construction are unchanged apart from reading through `block`:

```python
def load(paths: Paths) -> dict[str, PioType]:
    """The PlatformIO types: the one type list, filtered by builder.

    A view over :mod:`..typelist`, kept until its callers read the list
    directly. Lenient about other sections, like the list's own `read`; an
    ill-formed PlatformIO section is still refused here.
    """
    entries, families_map = typelist.read_config(paths)

    out: dict[str, PioType] = {}
    for entry in entries:
        if entry.builder != "platformio":
            continue
        name, block = entry.name, entry.block
        first_fw = entry.firmwares[0]
        family = firmware.resolve(paths, first_fw, families_map)
        source = family.source_dir(paths)

        env = (block.get("env") or "").strip()
        if not env:
            raise ConfigError(
                f"'{name}' is a PlatformIO type but names no env: - the "
                f"PlatformIO environment to build is not optional.",
                type=name,
            )

        stop_services = block.get_csv("stop_services")
        if stop_services is None:
            # Legacy `service:` key. Its meaning does not carry over
            # mechanically: today it means "pause this *in addition to*
            # klipper" (klipper stops unconditionally, globally), and
            # `stop_services:` means "stop *only* these" - so a bare
            # `service: knomi_serial` becomes `["klipper", "knomi_serial"]`,
            # not `["knomi_serial"]`. Absent takes the default watcher, same
            # as it always did; present-but-blank means no watcher at all,
            # which is still just klipper.
            legacy = block.get("service")
            if legacy is None:
                stop_services = None  # no key at all: inherit the next level
            else:
                unit = legacy.strip()
                stop_services = ["klipper", unit] if unit else ["klipper"]
        device_map = block.get("device_map")
        out[name] = PioType(
            name=name,
            env=env,
            source=source,
            firmware=first_fw,
            klipper_section=(block.get("klipper_section") or "knomi_serial").strip(),
            stop_services=stop_services,
            device_map=(
                "knomi/devices.json" if device_map is None else device_map
            ).strip(),
        )
    return out
```

Change the import `from .. import firmware, sections` to `from .. import firmware, typelist`, and drop `CfgDocument` if ruff reports it unused.

- [ ] **Step 6: Make `cmake.load` a view**

Replace `load` in `src/mcu_updater/providers/cmake.py` (99-145):

```python
def load(paths: Paths) -> dict[str, CmakeType]:
    """The cmake types: the one type list (:mod:`..typelist`), filtered by builder."""
    entries, families_map = typelist.read_config(paths)

    out: dict[str, CmakeType] = {}
    for entry in entries:
        if entry.builder != BUILDER:
            continue
        name, block = entry.name, entry.block
        first_fw = entry.firmwares[0]
        family = firmware.resolve(paths, first_fw, families_map)

        cmake_target = (block.get("cmake_target") or "").strip()
        if not cmake_target:
            raise ConfigError(
                f"'{name}' is a cmake type but names no cmake_target: - one "
                f"tree builds several targets, and which of them belongs on "
                f"this board is not something to guess at.",
                type=name,
            )

        out[name] = CmakeType(
            name=name,
            cmake_target=cmake_target,
            source=family.source_dir(paths),
            firmware=first_fw,
            cmake_args=family.cmake_args,
            submodules=family.submodules,
            chipset=entry.chipset,
            serials=list(entry.serials),
            stop_services=block.get_csv("stop_services"),
        )
    return out
```

Change `from .. import firmware, sections, uf2` to `from .. import firmware, typelist, uf2`, and drop `CfgDocument` if ruff reports it unused.

- [ ] **Step 7: Point selection at the list**

Replace `_declared_builders` in `src/mcu_updater/providers/selection.py` (56-72, the body after the docstring):

```python
    entries, _ = typelist.read_config(paths)
    return {entry.name: entry.builder for entry in entries if entry.builder}
```

Add this paragraph at the end of its docstring:

```
    Read through `typelist.read_config`, which never raises for an undeclared
    family. Do not route this through `firmware.resolve()`: that refuses an
    undeclared name, and one bad section must not break resolving another.
```

Change the imports `from .. import firmware, sections` and `from ..cfgdoc import CfgDocument` to `from .. import typelist`.

- [ ] **Step 8: Run the new tests and the suite**

Run: `python -m pytest tests/test_typelist.py -q`
Expected: all pass.
Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Re-anchor the mutation specs**

In `scripts/mutations/pio-provider-selection.json`:
- Replace the `_comment`'s last sentence with: `The rule is general, not a platformio special case: every view filters the one type list (typelist.py) by builder.`
- Add `"tests/test_typelist.py",` to `command` after `"tests/test_providers.py",`.
- Entry "families built by different tools are refused": add `"file": "src/mcu_updater/typelist.py",`, set `find` to `"        if len(set(entry.builders)) > 1:"` and `replace` to `"        if False:"`.
- Entry "a type built by a foreign builder is excluded from this registry": set `find` to `"            if entry.builder != \"kconfig_make\":"`.
- Entry "a type is selected by its declared firmware's builder": set `find` to `"        if entry.builder != \"platformio\":"`.
- Entry "an absent service: key is not the same as a blank one": in both `find` and `replace`, change `legacy = doc.get(section, \"service\")` to `legacy = block.get(\"service\")`.

In `scripts/mutations/application-firmware.json`:
- Add `"tests/test_typelist.py",` to `command`.
- Entry "the declared firmware list is kept, not discarded on load": set `find` to `"            mcu.firmwares = list(entry.firmwares)"`.
- Entry "a family nobody declared is refused rather than accepted": add `"file": "src/mcu_updater/typelist.py",` and set `find` to `"            if fw not in known:"`.

- [ ] **Step 10: Run both mutation specs**

Run: `python scripts/mutation_test.py scripts/mutations/pio-provider-selection.json`
Expected: every mutant killed.
Run: `python scripts/mutation_test.py scripts/mutations/application-firmware.json`
Expected: every mutant killed.

- [ ] **Step 11: Record the decision**

Append to `docs/decisions.md`:

```markdown
### One walk over `[type]` sections

`typelist.py` is the only code that walks `[type]` sections. `Registry.load`,
`pio.load` and `cmake.load` are views that filter its list by builder, until
their callers read the list directly. Three private walks were how a Roadrunner
type existed for the agent and not for the CLI. A new reader of type sections
reads the list; it does not open the file.

`typelist.read` never raises and `typelist.validate` is strict. Anything that
answers a question about one name (`providers.selection`) uses the lenient
half, so one malformed section cannot break another type.
```

- [ ] **Step 12: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Expected: clean.

```bash
git add src/mcu_updater/typelist.py src/mcu_updater/config.py src/mcu_updater/providers/pio.py src/mcu_updater/providers/cmake.py src/mcu_updater/providers/selection.py tests/test_typelist.py scripts/mutations/pio-provider-selection.json scripts/mutations/application-firmware.json docs/decisions.md
git commit -m "refactor(config): one walk over type sections, loaders become views

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Every type section has one owner

**Files:**
- Modify: `src/mcu_updater/config.py:120-139` (delete `_is_foreign_builder`), `:481-490` (save removal loop), `:813-816` (`remove_type`), plus new methods after `remove_declared_serial` (`:854`)
- Modify: `src/mcu_updater/sections.py` (docstring 1-18, delete `is_type_section` 62-70 and its `__all__` entry)
- Modify: `tests/test_sections.py:50-51`
- Modify: `scripts/mutations/pio-provider-selection.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `typelist.load(paths)` (Task 1).
- Produces:
  - `Registry.declared_serials(name: str) -> list[str]` (any builder; `UnknownTypeError` if undeclared)
  - `Registry.remove_declared_type(name: str) -> list[str]` (deletes the section for any builder and returns its serials)
  - `Registry.remove_type(name)` now also deletes the type's section.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (add `import os`, `import pytest`, `from mcu_updater import typelist` and `from mcu_updater.errors import UnknownTypeError` to its imports if missing):

```python
def _put_config(paths, text: str) -> None:
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _config_text(paths) -> str:
    with open(paths.main_config, encoding="utf-8") as fh:
        return fh.read()


def test_removing_a_kconfig_type_leaves_every_other_builders_sections(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    reg = Registry.load(paths)
    reg.remove_type("hexadistrofusion")
    reg.save(paths)
    text = _config_text(paths)
    assert "[type hexadistrofusion]" not in text
    assert "[type knomi]" in text
    assert "[type roadrunner]" in text
    assert "[firmware roadrunner]" in text


def test_a_save_never_deletes_a_section_it_was_not_asked_to(paths, example_registry_text):
    """Ownership is removal, not absence from `types`: a view that does not
    hold a type must not be able to delete it by saving."""
    _put_config(paths, example_registry_text)
    reg = Registry.load(paths)
    reg.types.pop("bttebb36")
    reg.save(paths)
    assert "[type bttebb36]" in _config_text(paths)


def test_removing_a_type_that_was_never_saved_is_not_an_error(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    reg = Registry.load(paths)
    reg.add_type("fresh", "stm32f072xb")
    reg.remove_type("fresh")
    assert "fresh" not in reg.names()


def test_declared_serials_answers_for_every_builder(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    reg = Registry.load(paths)
    assert reg.declared_serials("roadrunner") == ["RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    assert reg.declared_serials("bttebb36") == reg.get("bttebb36").serials
    assert isinstance(reg.declared_serials("knomi"), list)
    with pytest.raises(UnknownTypeError):
        reg.declared_serials("nope")


def test_a_declared_type_is_removed_whatever_builds_it(paths, example_registry_text):
    _put_config(paths, example_registry_text)
    reg = Registry.load(paths)
    for name in ("roadrunner", "knomi", "bttebb36"):
        expected = reg.declared_serials(name)
        assert reg.remove_declared_type(name) == expected
    reg.save(paths)
    remaining = {entry.name for entry in typelist.load(paths)}
    assert not {"roadrunner", "knomi", "bttebb36"} & remaining
    assert "[firmware roadrunner]" in _config_text(paths)
```

If `tests/test_config.py` already defines `_put_config` or `_config_text`, reuse those instead of redefining them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -q -k "removing or save_never or declared"`
Expected: FAIL. `a_save_never_deletes` fails because the section is gone, and `declared_serials`/`remove_declared_type` fail with `AttributeError`.

- [ ] **Step 3: Implement**

In `config.py`:
- Delete `_is_foreign_builder` (120-139).
- In `save`, delete the whole `for declared in sections.read(doc):` removal loop (481-490), and replace the docstring with:

```python
        """Atomic write, preserving everything the document already had.

        Writes the types this registry holds and deletes nothing. A section is
        deleted by `remove_type` / `remove_declared_type`, so a type this
        registry does not hold (another builder's) cannot be lost by a save.
        """
```

Replace `remove_type` (813-816) and add the new methods after `remove_declared_serial`:

```python
    def remove_type(self, name: str) -> McuType:
        mcu = self.get(name)
        del self.types[name]
        self._drop_section(name)
        return mcu
```

```python
    def declared_serials(self, name: str) -> list[str]:
        """A declared type's serials, whichever builder owns it."""
        if name in self.types:
            return list(self.types[name].serials)
        return self._doc.get_list(self._declared_section(name), "serials")

    def remove_declared_type(self, name: str) -> list[str]:
        """Delete a declared type, whichever builder owns it.

        Returns the serials it tracked, so a caller can say what went with it.
        """
        serials = self.declared_serials(name)
        self.types.pop(name, None)
        self._drop_section(name)
        return serials

    def _drop_section(self, name: str) -> None:
        """Delete a type's section, if the document has one yet."""
        if name in self.declared_type_names():
            self._doc.remove_section(self._declared_section(name))
```

Run `rg -n "types\.pop|del .*\.types\[" src` and route any source hit through `remove_type`. Test hits stay as they are.

In `sections.py`, delete `is_type_section` and its `__all__` entry. In the module docstring, replace the sentence naming `_is_foreign_builder` with: `Which builder owns a section is the type list's question (typelist.py), not this module's.` In `tests/test_sections.py`, delete lines 50-51 (the two `is_type_section` asserts).

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Re-anchor the mutation spec**

In `scripts/mutations/pio-provider-selection.json`, replace the entry "a foreign-builder type's section survives a save" with:

```json
    {
      "name": "a type's section is deleted by its removal, and only by that",
      "find": "            self._doc.remove_section(self._declared_section(name))",
      "replace": "            pass"
    },
```

Run: `python scripts/mutation_test.py scripts/mutations/pio-provider-selection.json`
Expected: every mutant killed.

- [ ] **Step 6: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add src/mcu_updater/config.py src/mcu_updater/sections.py tests/test_config.py tests/test_sections.py scripts/mutations/pio-provider-selection.json
git commit -m "fix(config): a save no longer deletes type sections it does not hold

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Builder keys take their seam module's prefix

**Files:**
- Modify: `src/mcu_updater/typelist.py` (refusal and constants)
- Modify: `src/mcu_updater/config.py:30-33` (docstring), the `Registry.load` profile read, and the `save` profile write (`:514-517`)
- Modify: `src/mcu_updater/providers/pio.py` (`load` reads, `klipper_section` parse, error text)
- Modify: `src/mcu_updater/providers/cmake.py` (`load` calls the refusal)
- Modify: `mcu-updater.cfg:103,111`, `tests/fixtures/registry.cfg:62,69`
- Modify: test key lines (codemod, below); `tests/test_pio.py:107-115`; delete `tests/test_agent_displays.py:288-310`; `tests/test_agent_displays.py:671`
- Modify: `README.md` (285-287, 535-555), `docs/agent-api.md` (1287-1289), `docs/layout.md` (87, 96-97), `docs/decisions.md` (192-193 + scheme paragraph)
- Create: `scripts/mutations/type-keys.json`
- Test: `tests/test_typelist.py`

**Interfaces:**
- Consumes: `TypeEntry.block.has` (Task 1).
- Produces: `typelist.RENAMED_KEYS: dict[str, str]`, `typelist.REMOVED_KEYS: dict[str, str]`, `typelist.refuse_renamed_keys(entry: TypeEntry, *, path: str) -> None`. `PioType` loses its `klipper_section` constructor input from config: the attribute keeps its default `"knomi_serial"` and is still emitted on the wire.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_typelist.py`:

```python
OLD_SPELLINGS = [
    pytest.param("[type knomi]\nfirmware: knomi_serial\nenv: knomi\n", "env", "platformio_env", id="env"),
    pytest.param(
        "[type board]\nchipset: stm32f072xb\nfirmware: klipper\nprofile: config.X\n",
        "profile",
        "kconfig_make_profile",
        id="profile",
    ),
    pytest.param(
        "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\ndevice_map: a.json\n",
        "device_map",
        "knomi_serial_device_map",
        id="device_map",
    ),
]


@pytest.mark.parametrize("section,old,new", OLD_SPELLINGS)
def test_an_old_key_spelling_is_refused_naming_the_new_one(paths, section, old, new):
    _put_config(paths, FAMILIES + section)
    with pytest.raises(ConfigCorruptError) as exc:
        typelist.load(paths)
    assert f"{old}:" in str(exc.value)
    assert f"{new}:" in str(exc.value)


KLIPPER_SECTION = (
    FAMILIES
    + "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\nklipper_section: knomi_serial\n"
)


@pytest.mark.parametrize("load", [typelist.load, pio_mod.load], ids=["typelist", "pio"])
def test_klipper_section_is_refused_as_no_longer_read(paths, load):
    _put_config(paths, KLIPPER_SECTION)
    with pytest.raises(ConfigCorruptError, match="klipper_section:, which is no longer read"):
        load(paths)


def test_the_new_spellings_are_read(paths):
    _put_config(
        paths,
        FAMILIES
        + "[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\n"
        + "knomi_serial_device_map: elsewhere/devices.json\n\n"
        + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n"
        + "kconfig_make_profile: config.X\n",
    )
    knomi = pio_mod.load(paths)["knomi"]
    assert knomi.env == "knomi"
    assert knomi.device_map == "elsewhere/devices.json"
    assert knomi.klipper_section == "knomi_serial"
    assert Registry.load(paths).get("board").profile == "config.X"


def test_a_saved_profile_uses_the_new_spelling(paths):
    _put_config(paths, FAMILIES + "[type board]\nchipset: stm32f072xb\nfirmware: klipper\n")
    reg = Registry.load(paths)
    reg.get("board").profile = "config.Y"
    reg.save(paths)
    with open(paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert "kconfig_make_profile: config.Y" in text
    assert "\nprofile:" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_typelist.py -q`
Expected: the new tests FAIL (no refusal; new keys not read).

- [ ] **Step 3: Add the refusal to `typelist.py`**

After `TypeEntry`, add:

```python
#: Keys one seam module reads are spelled `<seam module>_<param>` (spec §2,
#: docs/decisions.md). Old spelling -> new spelling.
RENAMED_KEYS: dict[str, str] = {
    "env": "platformio_env",
    "profile": "kconfig_make_profile",
    "device_map": "knomi_serial_device_map",
}

#: Keys that are no longer read at all -> why.
REMOVED_KEYS: dict[str, str] = {
    "klipper_section": (
        "the Klipper object a firmware's klippy module registers is fixed by "
        "that module, not by config"
    ),
}


def refuse_renamed_keys(entry: TypeEntry, *, path: str) -> None:
    """Refuse an old key spelling, naming its replacement.

    Refused rather than read under both names: config migrations do not exist
    yet, and a silently accepted old key would outlive the day they do.
    """
    for old, new in RENAMED_KEYS.items():
        if entry.block.has(old):
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' uses {old}:, which is now spelled {new}:. "
                f"Rename the key by hand - config migrations do not exist yet.",
                path=path,
                type=entry.name,
                value=old,
            )
    for gone, why in REMOVED_KEYS.items():
        if entry.block.has(gone):
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' uses {gone}:, which is no longer read - "
                f"{why}. Delete the line.",
                path=path,
                type=entry.name,
                value=gone,
            )
```

In `validate`, make the first statement of the `for entry in entries:` loop:

```python
        refuse_renamed_keys(entry, path=path)
```

- [ ] **Step 4: Read and write the new spellings**

- `config.py` `Registry.load`: `mcu.profile = (block.get("kconfig_make_profile") or "").strip()`.
- `config.py` `save` (514-517):

```python
            if mcu.profile.strip():
                doc.set(section, "kconfig_make_profile", mcu.profile.strip())
            else:
                doc.remove_option(section, "kconfig_make_profile")
```

- `config.py` module docstring (30-33): rename the "``profile``" key entry to "``kconfig_make_profile``".
- `pio.py` `load`: after `name, block = entry.name, entry.block` add `        typelist.refuse_renamed_keys(entry, path=paths.main_config)`. Then:
  - `        env = (block.get("platformio_env") or "").strip()`
  - error text `f"'{name}' is a PlatformIO type but names no platformio_env: - the "`
  - `        device_map = block.get("knomi_serial_device_map")`
  - delete the `klipper_section=(...)` argument line, so `PioType` keeps its default.
- `pio.py` module docstring line 6: replace `[display <env>]` with `[type <name>]`.
- `cmake.py` `load`: after `name, block = entry.name, entry.block` add `        typelist.refuse_renamed_keys(entry, path=paths.main_config)`.

- [ ] **Step 5: Update configs and test key lines**

- `mcu-updater.cfg:103` becomes `kconfig_make_profile: config.CartoV4USB`, and `:111` becomes `platformio_env: knomi                 # the PlatformIO env to build`. Run `rg -n "^\s*#?\s*(env|profile|device_map|klipper_section)\s*:" mcu-updater.cfg` and rename any commented examples the same way (delete `klipper_section` lines).
- `tests/fixtures/registry.cfg:62` → `kconfig_make_profile:` and `:69` → `platformio_env: knomi`.
- `tests/test_agent_displays.py:671` → `doc.set(f"type {env}", "platformio_env", env)`.
- Codemod the test strings. Find them with:

```bash
rg -n '(\\n|"|^\s*)(env|profile|device_map|klipper_section):' tests --glob '!test_cfgdoc.py'
```

  In each hit that is config text (not a JSON/dict key such as `"profile":`), rename `env:`→`platformio_env:`, `profile:`→`kconfig_make_profile:`, `device_map:`→`knomi_serial_device_map:`, and delete `klipper_section:` lines. `tests/test_cfgdoc.py` is excluded because it tests the parser, not the keys. Expected files (hit count): test_agent_display_jobs 3, test_agent_targets 4, test_agent_bulk 1, test_cli 3, test_sections 3, test_pio 12, test_providers 4, test_agent_displays 5, test_provider_selection 1.
- `tests/test_pio.py:107-115`: replace `test_the_klipper_section_defaults_to_knomi_serial` with a test that a type without the key loads with `klipper_section == "knomi_serial"`. Keep its existing fixtures, and drop the part that set the key.
- Delete `tests/test_agent_displays.py:288-310` (`test_screens_are_matched_to_their_type_by_klipper_section`). The key it configured no longer exists.

- [ ] **Step 6: Run the suite**

Run: `python -m pytest -q`
Expected: all pass. A remaining failure means an old spelling in a test string the rg missed. Re-run the rg without the path filter.

- [ ] **Step 7: Create the mutation spec**

Create `scripts/mutations/type-keys.json`:

```json
{
  "_comment": "Builder keys are spelled <seam module>_<param>, and the old spellings are refused rather than read. A reader left on the old name silently drops a user's setting (a profile no longer recorded, a PlatformIO type refused for a missing env it has); a refusal that stops firing lets an old key sit in a config that the next migration will not know to rewrite.",
  "file": "src/mcu_updater/typelist.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_typelist.py",
    "tests/test_pio.py",
    "tests/test_config.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a renamed key is refused",
      "find": "        if entry.block.has(old):",
      "replace": "        if False:"
    },
    {
      "name": "a removed key is refused",
      "find": "        if entry.block.has(gone):",
      "replace": "        if False:"
    },
    {
      "name": "the strict load checks key spellings",
      "find": "        refuse_renamed_keys(entry, path=path)",
      "replace": "        pass"
    },
    {
      "name": "the PlatformIO view checks key spellings itself",
      "file": "src/mcu_updater/providers/pio.py",
      "find": "        typelist.refuse_renamed_keys(entry, path=paths.main_config)",
      "replace": "        pass"
    },
    {
      "name": "platformio_env is the key read",
      "file": "src/mcu_updater/providers/pio.py",
      "find": "        env = (block.get(\"platformio_env\") or \"\").strip()",
      "replace": "        env = (block.get(\"env\") or \"\").strip()"
    },
    {
      "name": "knomi_serial_device_map is the key read",
      "file": "src/mcu_updater/providers/pio.py",
      "find": "        device_map = block.get(\"knomi_serial_device_map\")",
      "replace": "        device_map = block.get(\"device_map\")"
    },
    {
      "name": "kconfig_make_profile is the key read",
      "file": "src/mcu_updater/config.py",
      "find": "            mcu.profile = (block.get(\"kconfig_make_profile\") or \"\").strip()",
      "replace": "            mcu.profile = (block.get(\"profile\") or \"\").strip()"
    },
    {
      "name": "kconfig_make_profile is the key written",
      "file": "src/mcu_updater/config.py",
      "find": "                doc.set(section, \"kconfig_make_profile\", mcu.profile.strip())",
      "replace": "                doc.set(section, \"profile\", mcu.profile.strip())"
    }
  ]
}
```

Also re-anchor `scripts/mutations/pio-provider-selection.json`'s "env is required" entry: it still finds `        if not env:`, which is unchanged, so no edit is needed. Confirm with `rg -n "if not env" src/mcu_updater/providers/pio.py`.

Run: `python scripts/mutation_test.py scripts/mutations/type-keys.json`
Expected: every mutant killed.
Run: `python scripts/mutation_test.py scripts/mutations/pio-provider-selection.json`
Expected: every mutant killed.

- [ ] **Step 8: Docs**

- `README.md` 285-287: the per-type key `profile` becomes `kconfig_make_profile`.
- `README.md` 535: `env: knomi_toolchanger      ; REQUIRED...` becomes `platformio_env: knomi_toolchanger      ; REQUIRED...`. In 538-543, "`env:` is required" becomes "`platformio_env:` is required".
- `README.md` table 547: `env` → `platformio_env`. Delete row 549 (`klipper_section`). Row 551: `device_map` → `knomi_serial_device_map`. In 553-555, "Every key but `env`" becomes "Every key but `platformio_env`".
- `docs/agent-api.md` 1287: `env: knomi_toolchanger` → `platformio_env: knomi_toolchanger`. Delete line 1289 (the `# klipper_section:` comment). Line 519's wire `klipper_section` stays.
- `docs/layout.md` 87: `| \`profile\` |` → `| \`kconfig_make_profile\` |`. In 96-97, "the env named by `env:`" becomes "the env named by `platformio_env:`".
- `docs/decisions.md` 192-193: replace the sentence with "`platformio_env:` follows the same rule: `env` is what `platformio.ini` calls the section, and the `platformio_` prefix says which module reads it." Append this paragraph to that decision:

```markdown
A key read by one seam module is spelled `<seam module>_<param>`:
`platformio_env`, `cmake_target`, `kconfig_make_profile`,
`knomi_serial_device_map`. The param keeps the upstream word; the prefix says
which builder or helper reads it. Keys every type has (`firmware`, `chipset`,
`serials`, `canbus_uuids`, `stop_services`) and keys every family can have
(`source`, `builder`, `helper`, `flashers`, `submodules`) stay unprefixed, and
per-family kconfig keys keep their family prefix (`klipper_extra_args`). An
old spelling is refused with the new one named, never read under both.
```

- [ ] **Step 9: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add -A src tests scripts/mutations mcu-updater.cfg README.md docs/agent-api.md docs/layout.md docs/decisions.md
git commit -m "feat(config)!: prefix builder keys with their seam module, refuse old spellings

env: is now platformio_env:, profile: is kconfig_make_profile:, device_map:
is knomi_serial_device_map:, and klipper_section: is no longer read.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Remove the display vocabulary

**Files:**
- Modify: `src/mcu_updater/providers/spec.py` (`Install.displays` → `Install.platformio`, comment at 100)
- Modify: `src/mcu_updater/providers/platformio.py:61-98`, `src/mcu_updater/cli.py` (386, 387, 441, 567, 655, 704, 869, 909, 915), `src/mcu_updater/agent/methods/_api.py:77`, `src/mcu_updater/agent/methods/status.py` (532, 578, 1180, 1188, 1205, 1240, 1276-1340, 2019, 2207, 2230), `src/mcu_updater/agent/methods/bulk.py` (49, 307, 311, 582), `src/mcu_updater/agent/methods/flash.py:480`, `src/mcu_updater/paths.py:155-163`, `src/mcu_updater/stop_services.py` (3, 39, 99-117), `src/mcu_updater/providers/pio.py` (384, 437), `src/mcu_updater/firmware.py:104`, `src/mcu_updater/flashers/spec.py` (77, 88), `src/mcu_updater/sections.py:3`, `src/mcu_updater/cfgdoc.py:36`
- Modify tests: test_pio 678, 695-696, 707; test_states 290, 328; test_providers 198-199; test_stop_services 13, 16, 55, 114-125; test_cli 565; test_paths (new test)

**Interfaces:**
- Produces:
  - `Install.platformio: dict[str, PioType]`
  - `Paths.platformio_sidecar(env: str) -> str`, which still returns `os.path.join(self.data_dir, "displays", f"{env}.build.json")`
  - `stop_services.for_platformio(paths, pio_type, settings, families=None)` and `stop_services.DEFAULT_PLATFORMIO`
  - `Api._platformio_confidence`, `Api._platformio_device_status`, `Api._platformio_state`, and `bulk._platformio_json`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_paths.py`:

```python
def test_the_platformio_sidecar_keeps_its_on_disk_path(paths):
    """Renamed accessor, same file: a build record already on a printer must
    still be found."""
    assert paths.platformio_sidecar("knomi") == os.path.join(
        paths.data_dir, "displays", "knomi.build.json"
    )
```

(Add `import os` if missing.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_paths.py -q -k platformio_sidecar`
Expected: FAIL with `AttributeError: 'Paths' object has no attribute 'platformio_sidecar'`.

- [ ] **Step 3: Rename**

Apply the renames. Keep **local variable names** such as `display` and `displays` as they are, because `scripts/mutations/display-flash.json` and `provider-family-axis.json` anchor on them. Rename only attributes, functions, constants, labels and comments:

| Old | New |
| --- | --- |
| `Install.displays` (field, `Install.load`, all `install.displays` uses) | `Install.platformio` |
| `Paths.display_sidecar` | `Paths.platformio_sidecar` |
| `stop_services.for_display` | `stop_services.for_platformio` (parameter `display: PioType` → `pio_type: PioType`) |
| `stop_services.DEFAULT_DISPLAY` | `stop_services.DEFAULT_PLATFORMIO` |
| `Api._screen_confidence` / `_screen_device_status` / `_screen_state` | `_platformio_confidence` / `_platformio_device_status` / `_platformio_state` |
| `bulk._screen_json` | `bulk._platformio_json` |
| `what="display firmware"` (status.py 1205, 1240) | `what=f"{payload['firmware']} firmware"` |
| `f"no display type '{target.name}' is configured."` (platformio.py:78) | `f"no platformio type '{target.name}' is configured."` |
| comments `[display ...]`, `[display <env>]`, `[mcu ...]` | `[type ...]` |

For `what=`: `pio_status` spreads `display.to_json()`, which carries `"firmware"`, so `payload['firmware']` is always present. **Do not change** the device dict's `"firmware": None` at status.py:1253; `docs/agent-api.md` documents it. In `stop_services.for_platformio`, update the docstring's "falling back to `DEFAULT_DISPLAY`". In `sections.py:3`, rewrite the history sentence to "``[mcu carto_v4]`` and ``[display knomi_toolchanger]`` were two spellings of one thing; both are now ``[type ...]``." That keeps the history while making clear it is history. `cfgdoc.py:36`: use `[type knomi_toolchanger]` in the inline-comment example.

- [ ] **Step 4: Verify nothing is left**

Run: `rg -n "display_sidecar|for_display|DEFAULT_DISPLAY|_screen_(confidence|device_status|state|json)|\.displays\b|display firmware|display type|\[display" src tests ui/src scripts`
Expected: the only hits are the two package-docstring mentions in `src/mcu_updater/discovery/__init__.py:11` and `discovery/knomi_serial/__init__.py:12` ("a second display firmware"), which describe hardware, not config vocabulary, and stay. Wire string keys such as `"displays"` do not match `\.displays\b`.

Run: `rg -n "display_sidecar|for_display|DEFAULT_DISPLAY|_screen_|install\.displays" scripts/mutations`
Expected: no hits (verified while planning). If any appear, re-anchor them in this commit.

- [ ] **Step 5: Run the suite and the anchored specs**

Run: `python -m pytest -q`
Expected: all pass.
Run: `python scripts/mutation_test.py scripts/mutations/display-flash.json`
Expected: every mutant killed. A "find not present" error means a local variable was renamed after all: restore it, or re-anchor.
Run: `python scripts/mutation_test.py scripts/mutations/provider-family-axis.json`
Expected: every mutant killed.

- [ ] **Step 6: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add -A src tests scripts/mutations
git commit -m "refactor: retire the display vocabulary for type and firmware names

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: install.sh seeds klipper and katapult

**Files:**
- Create: `src/mcu_updater/seed.py`
- Modify: `src/mcu_updater/firmware.py` (add `SEEDED_KEYS` after `DEFAULT_BUILDER`; docstring 26-29)
- Modify: `install.sh` (new `seed_firmware_sections` after `check_paths`, remove the flashtool warn at 184-186, call it in `check_config` after line 480)
- Modify: `mcu-updater.cfg:24-26`, `tests/fixtures/registry.cfg` (after `[updater]`)
- Modify: `tests/conftest.py`
- Test: `tests/test_seed.py`, `tests/test_install_script.py`

**Interfaces:**
- Produces:
  - `firmware.SEEDED_KEYS: dict[str, tuple[tuple[str, str], ...]]`
  - `seed.FAMILIES = ("klipper", "katapult")`
  - `seed.home_relative(path: str, home: str) -> str`
  - `seed.section_lines(fw: str, source: str) -> str`
  - `seed.seed_firmware_sections(paths: Paths, sources: dict[str, str]) -> list[str]` (the families it added)
  - `seed.main(argv=None) -> int`
  - `tests/conftest.py`: `BASE_FIRMWARES: str`, `with_base_firmwares(text: str) -> str`, `seed_base_firmwares(paths) -> None`

- [ ] **Step 1: Add the conftest helpers**

Append to `tests/conftest.py` (add `import re` to its imports):

```python
#: What install.sh seeds on a host whose trees are at the conventional paths.
BASE_FIRMWARES = (
    "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n\n"
    "[firmware katapult]\nsource: ~/katapult\nflashers: dfu_util, bootsel\n\n"
)


def with_base_firmwares(text: str) -> str:
    """`text` with klipper and katapult declared ahead of its first family or type.

    Only for text that declares neither: adding a second copy of a section is a
    duplicate-section refusal.
    """
    match = re.search(r"^\[(firmware|type)\s", text, re.MULTILINE)
    if match is None:
        separator = "\n" if text and not text.endswith("\n") else ""
        return text + separator + BASE_FIRMWARES
    return text[: match.start()] + BASE_FIRMWARES + text[match.start() :]


def seed_base_firmwares(paths: Paths) -> None:
    """Declare klipper and katapult in the fake install, the way install.sh does."""
    from mcu_updater import seed

    seed.seed_firmware_sections(paths, {})
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_seed.py`:

```python
"""Seeding the two firmware sections every install now declares.

install.sh runs this with the source paths it found. The properties that
matter: an existing section is never touched (it may point at a fork), a re-run
changes nothing, and the sections land where a person reading the file expects
them - after [updater], before the first family.
"""

from __future__ import annotations

import os

from mcu_updater import firmware, seed

from .conftest import BASE_FIRMWARES


def _put_config(paths, text: str) -> None:
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _config_text(paths) -> str:
    with open(paths.main_config, encoding="utf-8") as fh:
        return fh.read()


def test_both_sections_are_written_to_a_missing_file(paths):
    assert seed.seed_firmware_sections(paths, {}) == ["klipper", "katapult"]
    assert _config_text(paths) == BASE_FIRMWARES
    assert set(firmware.load(paths)) == {"klipper", "katapult"}


def test_an_existing_section_is_never_touched(paths):
    _put_config(paths, "[firmware klipper]\nsource: ~/klipper-fork\n")
    assert seed.seed_firmware_sections(paths, {"klipper": "/elsewhere"}) == ["katapult"]
    text = _config_text(paths)
    assert "source: ~/klipper-fork" in text
    assert "/elsewhere" not in text


def test_sections_go_after_updater_and_above_the_first_familys_comment(paths):
    _put_config(
        paths,
        "[updater]\n#make_jobs: 0\n\n# a fork\n[firmware cartographer]\nsource: ~/carto\n",
    )
    seed.seed_firmware_sections(paths, {})
    text = _config_text(paths)
    assert (
        text.index("[updater]")
        < text.index("[firmware klipper]")
        < text.index("[firmware katapult]")
        < text.index("# a fork")
        < text.index("[firmware cartographer]")
    )
    assert list(firmware.load(paths)) == ["klipper", "katapult", "cartographer"]


def test_a_rerun_changes_nothing(paths):
    seed.seed_firmware_sections(paths, {})
    before = _config_text(paths)
    assert seed.seed_firmware_sections(paths, {}) == []
    assert _config_text(paths) == before


def test_a_source_under_home_is_written_with_a_tilde(paths):
    seed.seed_firmware_sections(paths, {"katapult": os.path.join(paths.home, "forks", "katapult")})
    assert "source: ~/forks/katapult\n" in _config_text(paths)


def test_a_source_outside_home_stays_absolute(paths):
    outside = os.path.abspath(os.path.join(paths.home, os.pardir, "elsewhere", "katapult"))
    seed.seed_firmware_sections(paths, {"katapult": outside})
    assert f"source: {outside}\n" in _config_text(paths)


def test_main_reports_each_family(paths, fake_root, monkeypatch, capsys):
    monkeypatch.setenv("MCU_UPDATER_HOME", str(fake_root))
    assert seed.main(["--katapult", str(fake_root / "katapult")]) == 0
    out = capsys.readouterr().out
    assert "[firmware klipper] added" in out
    assert "[firmware katapult] added" in out
    assert seed.main([]) == 0
    assert "[firmware klipper] already declared" in capsys.readouterr().out


def test_the_documented_example_and_the_fixture_already_declare_both(
    paths, example_registry_text, live_registry_text
):
    for text in (example_registry_text, live_registry_text):
        _put_config(paths, text)
        assert seed.seed_firmware_sections(paths, {}) == []
```

Create `tests/test_install_script.py`:

```python
"""install.sh is not run by the suite, so pin the lines that wire it to seed.py."""

from __future__ import annotations

from .conftest import REPO_ROOT


def _script() -> str:
    return (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def test_install_runs_the_seeder_before_validating_the_config():
    text = _script()
    assert "-m mcu_updater.seed" in text
    assert text.index("    seed_firmware_sections\n") < text.index("reg = Registry.load(paths)")


def test_install_offers_a_single_branch_katapult_clone():
    assert "git clone --single-branch https://github.com/Arksine/katapult" in _script()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_seed.py tests/test_install_script.py -q`
Expected: FAIL. `ImportError: cannot import name 'seed'`, and the install script asserts fail.

- [ ] **Step 4: Add `SEEDED_KEYS` to `firmware.py`**

After `DEFAULT_BUILDER`:

```python
#: Keys install.sh writes into the two sections it seeds, beside `source:`.
#: Nothing reads `flashers:` yet - the flash loop does, in the next plan. Kept
#: here so the lines a refusal tells a user to add match what install.sh writes.
SEEDED_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "klipper": (("flashers", "flashtool"),),
    "katapult": (("flashers", "dfu_util, bootsel"),),
}
```

Replace the module docstring paragraph at 26-29 ("Deliberately not here: which flasher...") with:

```
`flashers:` is written for klipper and katapult by install.sh but not read yet:
until the flash loop reads each family's list, the flasher is still chosen by
chipset and state.
```

- [ ] **Step 5: Create `src/mcu_updater/seed.py`**

```python
"""Declare klipper and katapult in mcu-updater.cfg, if they are not already.

Every firmware family is declared, these two included - nothing is invented
for a name without a section. install.sh runs this with the source paths it
found (or cloned), so an install gets both sections without anyone typing them.

A section that already exists is never touched. It may point at a fork, and
re-running install.sh must not undo that.

    python -m mcu_updater.seed --klipper ~/klipper --katapult ~/katapult
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence

from . import firmware
from .cfgdoc import CfgDocument
from .errors import UpdaterError
from .lock import ExclusiveLock
from .paths import Paths

#: The families this writes, in the order it writes them.
FAMILIES = ("klipper", "katapult")

_FIRST_BLOCK_RE = re.compile(r"^\[(firmware|type)\s", re.MULTILINE)


def home_relative(path: str, home: str) -> str:
    """`path` as `~/...` when it is under `home`, so the config reads like one a person wrote."""
    full = os.path.normpath(os.path.abspath(path))
    root = os.path.normpath(os.path.abspath(home))
    if full == root:
        return "~"
    if full.startswith(root + os.sep):
        return "~/" + os.path.relpath(full, root).replace(os.sep, "/")
    return full


def section_lines(fw: str, source: str) -> str:
    lines = [f"[firmware {fw}]", f"source: {source}"]
    lines += [f"{key}: {value}" for key, value in firmware.SEEDED_KEYS.get(fw, ())]
    return "\n".join(lines) + "\n\n"


def _insert_at(text: str) -> int | None:
    """Where the first family or type starts, counting the comment lines directly above it."""
    match = _FIRST_BLOCK_RE.search(text)
    if match is None:
        return None
    start = match.start()
    while start > 0:
        line_start = text.rfind("\n", 0, start - 1) + 1
        if not text[line_start : start - 1].startswith("#"):
            break
        start = line_start
    return start


def seed_firmware_sections(paths: Paths, sources: dict[str, str]) -> list[str]:
    """Add whichever of klipper and katapult the config does not declare.

    `sources` maps a family to the tree install.sh found. A family missing from
    it, or mapped to "", is written as ``~/<name>``. Returns the families added.
    """
    with ExclusiveLock(paths, path=paths.registry_lock_file).acquire("seed firmware sections"):
        try:
            with open(paths.main_config, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            text = ""
        doc = CfgDocument(text)
        missing = [fw for fw in FAMILIES if not doc.has_section(f"firmware {fw}")]
        if not missing:
            return []
        block = "".join(
            section_lines(
                fw,
                home_relative(sources.get(fw) or os.path.join(paths.home, fw), paths.home),
            )
            for fw in missing
        )
        at = _insert_at(text)
        if at is None:
            if text and not text.endswith("\n"):
                text += "\n"
            if text and not text.endswith("\n\n"):
                text += "\n"
            new_text = text + block
        else:
            new_text = text[:at] + block + text[at:]
        os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
        tmp = paths.main_config + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        os.replace(tmp, paths.main_config)
        return missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcu_updater.seed",
        description="Declare [firmware klipper] and [firmware katapult] if they are missing.",
    )
    parser.add_argument("--klipper", default="", help="Klipper source tree (default ~/klipper)")
    parser.add_argument("--katapult", default="", help="Katapult source tree (default ~/katapult)")
    args = parser.parse_args(argv)
    paths = Paths.from_env()
    try:
        added = seed_firmware_sections(
            paths, {"klipper": args.klipper, "katapult": args.katapult}
        )
    except (UpdaterError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for fw in FAMILIES:
        print(f"[firmware {fw}] {'added' if fw in added else 'already declared'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

If `ExclusiveLock(...).acquire(label)` is not usable as a context manager, copy the acquire/release pattern `Registry.mutate` uses (config.py:449-473) instead.

- [ ] **Step 6: Wire install.sh**

Delete the flashtool warning at `install.sh:184-186` (the `if [ ! -f "${KATAPULT_PATH}/scripts/flashtool.py" ]` block). Add this function after `check_paths`:

```bash
# Every firmware family is declared now, klipper and katapult included, with
# the source paths this host actually has rather than assumed ones. Sections
# that already exist are left alone - they may point at a fork.
function seed_firmware_sections {
    local klipper="${KLIPPER_PATH}" katapult="${KATAPULT_PATH}" answer="" output="" line=""
    if [ ! -d "${klipper}" ]; then
        warn "${klipper} not found"
        read -r -p "  ?     Path to your Klipper checkout [${klipper}]: " answer || answer=""
        if [ -n "${answer}" ]; then
            klipper="${answer/#\~/${HOME}}"
        fi
    fi
    if [ ! -d "${katapult}" ]; then
        if ask "Katapult not found at ${katapult}. Clone it (single branch)?" n; then
            if git clone --single-branch https://github.com/Arksine/katapult "${katapult}"; then
                ok "cloned katapult into ${katapult}"
            else
                err "git clone failed"
            fi
        fi
        if [ ! -d "${katapult}" ]; then
            answer=""
            read -r -p "  ?     Path to an existing Katapult checkout or fork [${katapult}]: " answer || answer=""
            if [ -n "${answer}" ]; then
                katapult="${answer/#\~/${HOME}}"
            fi
        fi
    fi
    if [ ! -f "${katapult}/scripts/flashtool.py" ]; then
        warn "${katapult}/scripts/flashtool.py not found - flashing unavailable"
    fi
    if output="$(PYTHONPATH="${INSTALL_PATH}/src" "${PYTHON_BIN}" -m mcu_updater.seed \
        --klipper "${klipper}" --katapult "${katapult}" 2>&1)"; then
        while IFS= read -r line; do
            if [ -n "${line}" ]; then
                ok "${line}"
            fi
        done <<< "${output}"
    else
        while IFS= read -r line; do
            if [ -n "${line}" ]; then
                err "${line}"
            fi
        done <<< "${output}"
        exit 1
    fi
}
```

In `check_config`, insert after line 480 (the end of the `mcus.cfg` refusal block) and before the "A broken registry is surfaced here" comment:

```bash

    seed_firmware_sections
```

That makes the call line exactly `    seed_firmware_sections` followed by a newline, which is what `test_install_script.py` looks for.

Run: `bash -n install.sh`
Expected: no output (syntax OK).

- [ ] **Step 7: Declare the base sections in both cfgs**

In `mcu-updater.cfg`, replace lines 24-26 (the "klipper and katapult need no section" comment) with:

```ini
# A firmware family: a source tree, how it is built, what it emits. Every
# family a type names is declared, klipper and katapult included - install.sh
# writes these two with the paths it finds. `flashers:` is the order flashers
# are tried in (not read yet).
[firmware klipper]
source: ~/klipper
flashers: flashtool

# No flashtool here: replacing katapult over katapult needs a deployer, which
# this tool does not manage. Katapult is written through a ROM bootloader.
[firmware katapult]
source: ~/katapult
flashers: dfu_util, bootsel
```

In `tests/fixtures/registry.cfg`, insert the two sections (without comments) after the `[updater]` block (after line 8, with a blank line on each side), exactly as in `BASE_FIRMWARES`.

- [ ] **Step 8: Run the tests**

Run: `python -m pytest tests/test_seed.py tests/test_install_script.py -q`
Expected: all pass.
Run: `python -m pytest -q`
Expected: all pass. `resolve()` is still lenient, so nothing else changes yet.

- [ ] **Step 9: Docs**

`README.md` Requirements (86-89): replace the Katapult bullet with "Katapult: install.sh writes `[firmware katapult]` with the tree it finds at `~/katapult`, offers a single-branch clone if there is none, or takes a path to an existing checkout or fork." Keep the `flashtool_path` override sentence; Task 7 rewrites it.

- [ ] **Step 10: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add src/mcu_updater/seed.py src/mcu_updater/firmware.py install.sh mcu-updater.cfg tests/fixtures/registry.cfg tests/conftest.py tests/test_seed.py tests/test_install_script.py README.md
git commit -m "feat(install): seed the klipper and katapult firmware sections

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Every firmware is declared

**Files:**
- Modify: `src/mcu_updater/firmware.py` (docstring 20-24, import 39, delete `BUILTIN` 43-47, `source_dir` 122, `names` docstring 188-198, `names_of` 201-203, new `missing_section_message`, `resolve` 206-221)
- Modify: `src/mcu_updater/typelist.py` (`_builder_of`, unknown-family message)
- Modify: `src/mcu_updater/config.py` (`fw_order` 243-251, `save` loop 528, `add_type` 768-811)
- Modify: `src/mcu_updater/agent/methods/profiles.py:43,132`, `src/mcu_updater/agent/methods/status.py:258-260`
- Modify: `src/mcu_updater/cli.py:39,995-1003`, `src/mcu_updater/paths.py:30-31,184-186`
- Modify: `ui/src/api/mcutype.ts:22`, `ui/src/components/TypeDialog.spec.ts:17,28`
- Modify: `docs/agent-api.md:562-573`, `README.md:232-238,406`
- Modify: `scripts/mutations/firmware-source.json`, `scripts/mutations/application-firmware.json`, `scripts/mutations/profiles.json:96-99`
- Test: `tests/test_firmware.py` (docstring, 53-94, 291-311, and seeding in 314-340), plus the sweep below

**Interfaces:**
- Consumes: `firmware.SEEDED_KEYS`, and `seed_base_firmwares` / `with_base_firmwares` (Task 5).
- Produces:
  - `firmware.missing_section_message(fw: str) -> str`
  - `firmware.resolve()` raises `ConfigCorruptError(message, path=paths.main_config, value=fw)` for an undeclared family
  - `firmware.names_of(families) == tuple(sorted(families))`
  - `typelist._builder_of` returns `""` for an undeclared family
  - `Registry.add_type` raises `ConfigCorruptError` for an undeclared family
  - `firmware.BUILTIN`, `paths.FW_TARGETS` and `Paths.fw_dir` no longer exist

- [ ] **Step 1: Rewrite the convention tests as declaration tests**

In `tests/test_firmware.py`:
- Replace the module docstring (1-12) with: `"""Firmware families: where a tree lives, and what it builds.\n\nEvery family is declared, klipper and katapult included. The properties these\ntests hold: an undeclared name is refused with the lines to add, a declared\nsection's keys are each optional, and an override is never silently ignored.\n"""`
- Add `from mcu_updater.errors import ConfigCorruptError` and `from .conftest import seed_base_firmwares` to the imports.
- Replace lines 53-94 with:

```python
def test_an_undeclared_family_is_refused_with_the_lines_to_add(paths):
    with pytest.raises(ConfigCorruptError) as exc:
        firmware.resolve(paths, "klipper")
    message = str(exc.value)
    assert "[firmware klipper]" in message
    assert "source: ~/klipper" in message
    assert "flashers: flashtool" in message
    assert "install.sh" in message


def test_a_missing_config_file_declares_nothing(paths):
    assert firmware.load(paths) == {}
    assert firmware.names(paths) == ()


def test_a_declared_family_with_no_source_builds_from_home(paths):
    _write_firmware(paths, "klipper")
    family = firmware.resolve(paths, "klipper")
    assert family.source_dir(paths) == os.path.join(paths.home, "klipper")
    assert family.built_artifact(paths) == os.path.join(paths.home, "klipper", "out", "klipper.bin")
    assert family.built_artifact(paths, "uf2") == os.path.join(
        paths.home, "klipper", "out", "klipper.uf2"
    )


def test_the_artifact_defaults_to_the_family_name(paths):
    assert FirmwareFamily(name="klipper").artifact_name() == "klipper"


def test_a_declared_family_defaults_to_kconfig_make(paths):
    _write_firmware(paths, "invented")
    assert firmware.resolve(paths, "invented").builder == "kconfig_make"


def test_a_declared_katapult_is_a_bootloader_without_saying_so(paths):
    _write_firmware(paths, "katapult")
    assert firmware.resolve(paths, "katapult").bootloader is True


def test_every_other_declared_family_is_not_a_bootloader(paths):
    _write_firmware(paths, "klipper")
    _write_firmware(paths, "invented")
    assert firmware.resolve(paths, "klipper").bootloader is False
    assert firmware.resolve(paths, "invented").bootloader is False
```

- Replace lines 291-311 with:

```python
def test_nothing_is_built_in(paths):
    _write_firmware(paths, "cartographer", artifact="klipper")
    assert firmware.names(paths) == ("cartographer",)


def test_declared_families_are_ordered_independently_of_the_file(paths):
    """Otherwise the artifacts payload and the CLI listing reorder themselves
    depending on where somebody happened to add a section."""
    _write_firmware(paths, "zzz")
    _write_firmware(paths, "klipper")
    _write_firmware(paths, "aaa")
    assert firmware.names(paths) == ("aaa", "klipper", "zzz")


def test_a_types_families_keep_their_declared_order(paths):
    _write_firmware(paths, "cartographer", artifact="klipper")
    seed_base_firmwares(paths)
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n[type probe]\nchipset: stm32g431xx\nfirmware: katapult, cartographer\n")
    assert Registry.load(paths).get("probe").fw_order() == ["katapult", "cartographer"]
```

- In the two tests at 314-340, add `seed_base_firmwares(paths)` as the first line of each.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_firmware.py -q`
Expected: FAIL. `resolve` does not raise, `names` still lists built-ins, and `fw_order` puts built-ins first.

- [ ] **Step 3: Make `firmware.py` strict**

- Replace docstring lines 20-24 with:

```
**Every family is declared**, klipper and katapult included. `resolve()`
refuses a name with no section, with the lines to add, rather than inventing
the ``~/<name>`` convention for it - inventing is how a misspelt family used to
build from a directory nobody meant. Within a section every key is optional: a
section with no ``source:`` still means ``~/<name>``. install.sh writes the
klipper and katapult sections with the paths it finds.
```

- Line 39: `from .paths import Paths`, plus `from .errors import ConfigCorruptError`.
- Delete the `BUILTIN` block (43-47).
- `source_dir` last line: `        return os.path.join(paths.home, self.name)`.
- `names` docstring: `"""Every declared family, sorted so the answer does not depend on where in the file somebody added a section. Nothing is built in: a config that declares nothing knows no families."""`.
- `names_of` body: `    return tuple(sorted(families))`.
- Add before `resolve`:

```python
def missing_section_message(fw: str) -> str:
    """How to fix an undeclared family: the lines to paste."""
    lines = [f"[firmware {fw}]", f"source: ~/{fw}"]
    lines += [f"{key}: {value}" for key, value in SEEDED_KEYS.get(fw, ())]
    body = "\n".join(f"    {line}" for line in lines)
    return (
        f"No [firmware {fw}] section is declared. Add one to mcu-updater.cfg:\n"
        f"{body}\n"
        f"Re-running install.sh writes the klipper and katapult sections for you."
    )
```

- `resolve`: keep its signature. Replace the first docstring paragraph with "The declared family for `fw`. Refuses a name with no ``[firmware <fw>]`` section." and keep the `families` paragraph. Body:

```python
    if families is None:
        families = load(paths)
    family = families.get(fw)
    if family is None:
        raise ConfigCorruptError(missing_section_message(fw), path=paths.main_config, value=fw)
    return family
```

- [ ] **Step 4: Follow the strictness through**

- `typelist._builder_of`: `    return family.builder if family is not None else ""`. In `tests/test_typelist.py::test_read_never_refuses`, change `("orphan", ("nowhere",), "kconfig_make")` to `("orphan", ("nowhere",), "")`.
- `typelist.validate`, unknown-family message:

```python
                raise ConfigCorruptError(
                    f"{path}: '{entry.name}' declares firmware '{fw}', which is not "
                    f"a known family. Known: {', '.join(known) or 'none'}. Fix the "
                    f"spelling, or declare it. {firmware.missing_section_message(fw)}",
                    path=path,
                    type=entry.name,
                    value=fw,
                )
```

- `config.py` `fw_order` (243-251):

```python
    def fw_order(self) -> list[str]:
        """The families this type carries, in declaration order.

        Families it holds per-family keys for but no longer declares follow, so
        nothing a caller iterates is dropped.
        """
        declared = [fw for fw in self.firmwares if fw in self.fws]
        return declared + [fw for fw in self.fws if fw not in self.firmwares]
```

- `config.py` `save`: `            for fw in fw_names:` becomes `            for fw in dict.fromkeys([*mcu.fws, *fw_names]):`, so keys for a type's own slots are still written or cleared.
- `config.py` `add_type`: replace the docstring paragraph at 787-791 with "Every family in the resulting `firmwares` must be declared in this registry's document; an undeclared one is refused with the section to add." Insert after the `katapult_installed` block (after line 798):

```python
        declared = firmware.load_from_doc(self._doc)
        for fw in firmwares:
            if fw not in declared:
                raise ConfigCorruptError(
                    f"'{name}' names firmware '{fw}'. {firmware.missing_section_message(fw)}",
                    type=name,
                    value=fw,
                )
```

- `agent/methods/profiles.py:43` and `:132`: `if fw not in families and fw not in firmware.BUILTIN:` becomes `if fw not in families:`.
- `agent/methods/status.py:258-260`: delete the two comment lines and `"builtin": name in firmware.BUILTIN,`.
- `cli.py`: delete `FW_TARGETS` from `from .paths import FW_TARGETS, Paths` (line 39). In `build_parser` (995-1003), use `choices = list(fw_choices) if fw_choices else None` and add to its docstring: "`None` accepts any name; an undeclared one is refused when it is resolved, with the section to add."
- `paths.py`: delete `FW_TARGETS` (30-31) and `fw_dir` (184-186).
- Run `rg -n "FW_TARGETS|BUILTIN|fw_dir\(" src` and fix any remaining source hit. A tree for a family is `firmware.resolve(paths, fw, families).source_dir(paths)`.
- UI: delete `builtin: boolean;` (`ui/src/api/mcutype.ts:22`) and both `builtin: true,` lines (`ui/src/components/TypeDialog.spec.ts:17,28`). Run `rg -n "builtin" ui/src` and expect no hits.

- [ ] **Step 5: The test sweep**

Run: `python -m pytest -q -p no:randomly 2>&1 | tail -60`. For each failing test, apply the first rule that fits:

1. **The test builds a registry on a fresh tree.** It calls `Registry.load` then `add_type`, or appends `[type ...]`/`[firmware ...]` text to a file that did not declare klipper/katapult: call `seed_base_firmwares(paths)` first (import it with `from .conftest import seed_base_firmwares`). **Known:** the `c` fixture in `tests/test_cli.py:32-52` (first line before `Registry.load`); `tests/test_stop_services.py` tests that build an `McuType` with `firmwares=["klipper"]` and call `for_mcu` without `families`.
2. **The test writes whole config text that lacks both sections:** wrap the text with `with_base_firmwares(text)`. Do not wrap `live_registry_text`/`example_registry_text`, which declare both since Task 5.
3. **The test uses `paths.fw_dir(x)`:** replace it with `os.path.join(paths.home, x)`. **Known:** test_agent_profiles 300, 335, 560, 566; test_agent_bulk 838; test_agent_targets 492; test_build 184, 207, 215; test_profiles 107, 197, 399-401, 424, 523, 639, 653, 761; test_providers 172, 199. In `tests/test_paths.py`, delete the `fw_dir` assertion at line 110.
4. **The test asserts invented-family behaviour** (resolving an undeclared name): declare the family first with the file's own helper. If the test's point *was* the invention, delete it and say so in the commit body.
5. **The test asserts `builtin`, `BUILTIN` or `FW_TARGETS`:** delete that assertion, or use the declared names.

Repeat until `python -m pytest -q` passes. Do not add seeding to the `paths` fixture (Ruling 12).

- [ ] **Step 6: Re-anchor the mutation specs**

`scripts/mutations/firmware-source.json`:
- Entry "the built artifact is looked for in the family's own tree": `replace` becomes `"        return os.path.join(os.path.join(paths.home, self.name), \"out\", f\"{self.artifact_name()}.{ext}\")"`.
- Delete the entries "an unconfigured family still resolves to the convention" and "an unconfigured katapult still resolves as a bootloader".
- Append:

```json
    {
      "name": "an undeclared family is refused, never invented",
      "find": "    if family is None:\n        raise ConfigCorruptError(missing_section_message(fw), path=paths.main_config, value=fw)",
      "replace": "    if False:\n        raise ConfigCorruptError(missing_section_message(fw), path=paths.main_config, value=fw)"
    },
    {
      "name": "a declared family with no source: builds from ~/<name>",
      "find": "        return os.path.join(paths.home, self.name)",
      "replace": "        return paths.home"
    },
    {
      "name": "families are listed sorted, not in file order",
      "find": "    return tuple(sorted(families))",
      "replace": "    return tuple(families)"
    }
```

`scripts/mutations/application-firmware.json`, append:

```json
    {
      "name": "a type's families keep their declared order",
      "find": "        declared = [fw for fw in self.firmwares if fw in self.fws]",
      "replace": "        declared = sorted(fw for fw in self.firmwares if fw in self.fws)"
    }
```

`scripts/mutations/profiles.json` (entry at 96-99, `file` paths.py, finding `        return os.path.join(self.type_dir(mcu_type), f"{fw}.custom.config")`): in its `replace`, change `self.fw_dir(fw)` to `os.path.join(self.home, fw)`, so the mutant still points at the tree and does not die on an `AttributeError`.

Run each, one at a time:
`python scripts/mutation_test.py scripts/mutations/firmware-source.json`
`python scripts/mutation_test.py scripts/mutations/application-firmware.json`
`python scripts/mutation_test.py scripts/mutations/profiles.json`
Expected: every mutant killed in each.

- [ ] **Step 7: Docs**

- `docs/agent-api.md` 562-573: remove the `"builtin"` member from the example object at 565 (keep the JSON valid), and delete the sentence at 572-573 ("`builtin` marks `klipper` and `katapult`...").
- `README.md` 232-238: replace the paragraph with "Every family a type names is declared, `klipper` and `katapult` included. install.sh writes those two with the source paths it finds. A config missing one is refused with the exact lines to add. Within a section every key is optional: no `source:` means `~/<name>`."
- `README.md` 406: replace with "A declared family with no `source:` builds from `~/<name>` and leaves `out/<artifact>.bin`."

- [ ] **Step 8: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`
Run (in `ui/`): `npx vitest run && npx eslint . && npx vite build`
Expected: all clean.

```bash
git add -A src tests scripts/mutations ui/src docs/agent-api.md README.md
git commit -m "feat(firmware)!: every family is declared, klipper and katapult included

resolve() refuses an undeclared family with the lines to add. BUILTIN,
FW_TARGETS and Paths.fw_dir are gone; names() is sorted and a type's
families keep their declared order.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The flashtool path comes from the katapult family

**Files:**
- Modify: `src/mcu_updater/flashers/flash.py:78-82` (`find_flashtool`)
- Modify: `src/mcu_updater/paths.py:172-174` (delete `flashtool`)
- Modify: `src/mcu_updater/agent/methods/status.py:1638-1641` (`canbus_scan`)
- Modify tests: every `paths.flashtool` user (below), `tests/test_agent_canbus.py`
- Create: `scripts/mutations/flashtool-path.json`
- Modify: `README.md` Requirements (86-89)
- Test: `tests/test_flashtool_path.py`

**Interfaces:**
- Consumes: strict `firmware.resolve` (Task 6); `BASE_FIRMWARES`, `seed_base_firmwares` (Task 5).
- Produces: `find_flashtool(paths, settings) -> str`, which raises `ConfigCorruptError` when `flashtool_path` is unset and katapult is undeclared. `Paths.flashtool` no longer exists.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flashtool_path.py`:

```python
"""Where katapult's flashtool.py is looked for.

`flashtool_path` if set, else the declared katapult tree's scripts/. Never the
hardcoded ~/katapult, which is how a fork's flashtool went unused.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.errors import ConfigCorruptError
from mcu_updater.flashers.flash import find_flashtool

from .conftest import BASE_FIRMWARES


def _put_config(paths, text: str) -> None:
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def test_a_configured_flashtool_path_wins(paths, settings):
    _put_config(paths, BASE_FIRMWARES)
    configured = dataclasses.replace(settings, flashtool_path="~/tools/flashtool.py")
    assert find_flashtool(paths, configured) == os.path.join(paths.home, "tools", "flashtool.py")


def test_a_configured_path_needs_no_katapult_section(paths, settings):
    configured = dataclasses.replace(settings, flashtool_path="/opt/flashtool.py")
    assert find_flashtool(paths, configured) == "/opt/flashtool.py"


def test_the_declared_katapult_source_is_used(paths, settings):
    _put_config(paths, BASE_FIRMWARES.replace("source: ~/katapult", "source: ~/katapult-fork"))
    assert find_flashtool(paths, settings) == os.path.join(
        paths.home, "katapult-fork", "scripts", "flashtool.py"
    )


def test_an_undeclared_katapult_is_refused_naming_the_section(paths, settings):
    _put_config(paths, "[firmware klipper]\nsource: ~/klipper\n")
    with pytest.raises(ConfigCorruptError, match=r"\[firmware katapult\]"):
        find_flashtool(paths, settings)
```

Append to `tests/test_agent_canbus.py`:

```python
def test_an_undeclared_katapult_is_reported_not_raised(paths, fake_root):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write("[firmware klipper]\nsource: ~/klipper\n")
    api = Api(paths)
    api.paths = dataclasses.replace(
        api.paths, can_sysfs_net=_make_can_interface(fake_root, "can0")
    )

    res = api.dispatch("fw.canbus.scan")
    assert res["devices"] == []
    assert "[firmware katapult]" in (res["message"] or "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_flashtool_path.py tests/test_agent_canbus.py -q`
Expected: FAIL. The declared-source test gets `~/katapult/...`, the undeclared case does not raise, and the canbus test sees the "flashtool.py not found" message.

- [ ] **Step 3: Implement**

`flashers/flash.py` (make sure `import os` is present):

```python
def find_flashtool(paths: Paths, settings: Settings) -> str:
    """Katapult's flashtool.py: `flashtool_path` if set, else the declared katapult tree's.

    Raises ConfigCorruptError, naming the section to add, when neither is set
    and katapult is not declared. A flash stops on that. `fw.canbus.scan`
    reports it instead, because that method answers rather than fails.
    """
    if settings.flashtool_path:
        return firmware.expand_home(settings.flashtool_path, paths.home)
    katapult = firmware.resolve(paths, "katapult")
    return os.path.join(katapult.source_dir(paths), "scripts", "flashtool.py")
```

`paths.py`: delete the `flashtool` property (172-174).

`agent/methods/status.py` `canbus_scan`, replacing line 1638:

```python
        try:
            flashtool = find_flashtool(self.paths, settings)
        except ConfigCorruptError as exc:
            out["message"] = str(exc)
            return out
```

- [ ] **Step 4: Move the test helpers off `paths.flashtool`**

Run: `rg -n "\.flashtool\b" src tests`. Replace every test use of `paths.flashtool` (or `api.paths.flashtool`) with `os.path.join(paths.home, "katapult", "scripts", "flashtool.py")`, adding `import os` where needed. Known sites: `tests/test_agent_canbus.py:36-39` (`_make_flashtool`), `tests/test_discovery_canbus.py:165-168`, `tests/test_agent_flash_can.py:41-44`, `tests/test_agent_methods.py:1774`, `tests/test_flash.py:45,64-69,79`, `tests/test_flash_can.py:58,97`, `tests/test_agent_flash.py:134,180`, `tests/test_agent_bulk.py:170`. Delete the `flashtool` assertion at `tests/test_paths.py:111`. A test that now fails with the `[firmware katapult]` refusal is on a fresh tree: add `seed_base_firmwares(paths)` (Task 6, rule 1). The `live_registry_text` fixtures declare katapult, so `test_missing_flashtool_is_reported_not_raised` keeps its meaning: the section resolves, and `fake_root` has no `katapult/scripts/flashtool.py`.

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Mutation spec**

Create `scripts/mutations/flashtool-path.json`:

```json
{
  "_comment": "Where flashtool.py is looked for. A wrong answer is a flash that reports katapult missing on a host that has it, or a flashtool run from a tree the user stopped using. And fw.canbus.scan answers rather than fails: an undeclared katapult is a message, never an RpcError that blanks the scan.",
  "file": "src/mcu_updater/flashers/flash.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_flashtool_path.py",
    "tests/test_agent_canbus.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a configured flashtool_path wins",
      "find": "    if settings.flashtool_path:\n        return firmware.expand_home(settings.flashtool_path, paths.home)",
      "replace": "    if False:\n        return firmware.expand_home(settings.flashtool_path, paths.home)"
    },
    {
      "name": "the fallback is the declared katapult source, not ~/katapult",
      "find": "    return os.path.join(katapult.source_dir(paths), \"scripts\", \"flashtool.py\")",
      "replace": "    return os.path.join(paths.home, \"katapult\", \"scripts\", \"flashtool.py\")"
    },
    {
      "name": "an undeclared katapult is reported by fw.canbus.scan, not raised",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "        except ConfigCorruptError as exc:\n            out[\"message\"] = str(exc)\n            return out",
      "replace": "        except OSError as exc:\n            out[\"message\"] = str(exc)\n            return out"
    }
  ]
}
```

Run: `python scripts/mutation_test.py scripts/mutations/flashtool-path.json`
Expected: every mutant killed.

- [ ] **Step 6: Docs**

`README.md` Requirements (86-89): rewrite the flashtool sentence as "`flashtool.py` is found under the declared `[firmware katapult]` `source:` (`<source>/scripts/flashtool.py`), or at `flashtool_path` in `[updater]` if that is set."

- [ ] **Step 7: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add -A src tests scripts/mutations README.md
git commit -m "feat(flash): find flashtool.py under the declared katapult source

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The inventory

**Files:**
- Create: `src/mcu_updater/inventory.py`
- Modify: `src/mcu_updater/agent/methods/status.py`: imports (14, 18-26); `type_status` (342-398); `status` (494-525); `targets` (716-738); `_cmake_target` (1040-1113)
- Create: `scripts/mutations/inventory.json`
- Test: `tests/test_inventory.py`, `tests/test_agent_targets.py`

**Interfaces:**
- Consumes: `typelist.TypeEntry`, `typelist.read`, `typelist.read_config` (Task 1); `discovery.byid.BusDevice`, `STATE_OFFLINE`.
- Produces:
  - `inventory.SERIAL = "serial"`, `inventory.CANBUS_UUID = "canbus_uuid"`, `inventory.STATE_UNKNOWN = "unknown"`
  - `inventory.Sweep(byid: tuple[BusDevice, ...] = (), canbus: Mapping[str, Mapping[str, Any]] = {})`
  - `inventory.Row(type, builder, kind, id, present, state, path, device=None, canbus=None)`
  - `inventory.build(entries: Iterable[TypeEntry], sweep: Sweep) -> list[Row]`
  - `inventory.index(rows: Iterable[Row]) -> dict[tuple[str, str, str], Row]`, keyed `(type, kind, id)`
  - `Api.inventory(canbus=None) -> list[Row]`
  - `Api.type_status(reg, name, versions=None, canbus=None, rows=None)`, `Api.targets(reg, types, displays, rows=None)`, `Api._cmake_target(payload, allowed, families, rows=None)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inventory.py`:

```python
"""Declared identity joined with one sweep of the bus.

Pure over injected inputs: no scan happens here, which is what lets the agent
reuse the sweep it already holds and lets these tests need no hardware.
"""

from __future__ import annotations

from mcu_updater import firmware, inventory, typelist
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.discovery.byid import STATE_OFFLINE, BusDevice

from .conftest import BASE_FIRMWARES

TEXT = (
    BASE_FIRMWARES
    + "[firmware roadrunner]\nsource: ~/rr\nbuilder: cmake\n\n"
    + "[type board]\nchipset: stm32f072xb\nfirmware: klipper, katapult\n"
    + "serials:\n    AAAA\ncanbus_uuids:\n    0123456789AB\n\n"
    + "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
    + "cmake_target: roadrunner_v1_usbserial\nserials:\n    RR-ONE\n"
)


def _entries():
    doc = CfgDocument(TEXT)
    return typelist.read(doc, firmware.load_from_doc(doc))


def _dev(serial, fw="Klipper", chipset="stm32f072xb"):
    return BusDevice(
        fw=fw, chipset=chipset, serial=serial, path=f"/dev/serial/by-id/usb-{fw}_{chipset}_{serial}"
    )


def test_every_declared_identity_gets_a_row_in_type_order():
    rows = inventory.build(_entries(), inventory.Sweep())
    assert [(r.type, r.builder, r.kind, r.id) for r in rows] == [
        ("board", "kconfig_make", inventory.SERIAL, "AAAA"),
        ("board", "kconfig_make", inventory.CANBUS_UUID, "0123456789AB"),
        ("roadrunner", "cmake", inventory.SERIAL, "RR-ONE"),
    ]
    assert [(r.present, r.state) for r in rows] == [
        (False, STATE_OFFLINE),
        (False, inventory.STATE_UNKNOWN),
        (False, STATE_OFFLINE),
    ]


def test_presence_and_state_come_from_the_sweep_for_every_builder():
    board, rr = _dev("AAAA"), _dev("RR-ONE", fw="Vylyne", chipset="Roadrunner")
    rows = inventory.index(inventory.build(_entries(), inventory.Sweep(byid=(board, rr))))
    row = rows[("board", inventory.SERIAL, "AAAA")]
    assert (row.present, row.state, row.path, row.device) == (True, board.state, board.path, board)
    row = rows[("roadrunner", inventory.SERIAL, "RR-ONE")]
    assert (row.present, row.state, row.path) == (True, rr.state, rr.path)


def test_the_by_id_chipset_segment_is_not_a_filter():
    rows = inventory.index(
        inventory.build(_entries(), inventory.Sweep(byid=(_dev("AAAA", chipset="stm32g0b1xx"),)))
    )
    assert rows[("board", inventory.SERIAL, "AAAA")].present is True


def test_two_sightings_of_one_serial_are_not_a_match():
    sweep = inventory.Sweep(byid=(_dev("AAAA"), _dev("AAAA", fw="katapult")))
    row = inventory.index(inventory.build(_entries(), sweep))[("board", inventory.SERIAL, "AAAA")]
    assert (row.present, row.state, row.path) == (False, STATE_OFFLINE, None)


def test_a_can_node_is_present_when_the_canbus_results_hold_it():
    cross = {"version": "v0.12.0"}
    sweep = inventory.Sweep(canbus={"0123456789ab": cross})
    row = inventory.index(inventory.build(_entries(), sweep))[
        ("board", inventory.CANBUS_UUID, "0123456789AB")
    ]
    assert (row.present, row.state, row.canbus) == (True, inventory.STATE_UNKNOWN, cross)
```

Append to `tests/test_agent_targets.py` (add `from mcu_updater import inventory` and `from .conftest import make_device` if missing):

```python
def test_a_cmake_row_takes_presence_from_the_inventory_it_is_given(paths, tmp_path):
    _cmake_config(paths, tmp_path, serial="RR-INJECTED")
    api = Api(paths, runner=_runner())
    payload = next(p for p in api.cmake_status() if p["serials"] == ["RR-INJECTED"])
    row = inventory.Row(
        type=payload["name"],
        builder="cmake",
        kind=inventory.SERIAL,
        id="RR-INJECTED",
        present=True,
        state="klipper",
        path="/dev/injected",
    )
    target = api._cmake_target(
        payload, set(api.available_methods()), firmware.load(paths), inventory.index([row])
    )
    device = target["devices"][0]
    assert device["present"] is True
    assert device["path"] == "/dev/injected"


def test_type_status_takes_board_state_from_the_inventory(paths, fake_root, live_registry_text):
    """The by-id chipset segment no longer hides a board (plan ruling 8)."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    reg = Registry.load(paths)
    name = next(n for n in reg.names() if reg.get(n).serials)
    serial = reg.get(name).serials[0]
    make_device(fake_root / "bus", "Klipper", "notthechipset", serial)

    out = Api(paths).type_status(reg, name, versions={}, canbus={})
    assert out["serials"][0]["state"] == "klipper"
```

Use the imports `tests/test_agent_targets.py` already has for `Api`, `Registry` and `firmware`; add any that are missing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_inventory.py tests/test_agent_targets.py -q -k "inventory or type_status_takes"`
Expected: FAIL. `ImportError: cannot import name 'inventory'`.

- [ ] **Step 3: Create `src/mcu_updater/inventory.py`**

```python
"""Declared identity joined with one sweep of the bus.

The one place a declared board meets what is plugged in. Every provider's rows
come from here: `type_status`, `_cmake_target` and the CLI's `status` used to
each scan and match privately, with different rules (a chipset filter in one,
none in another). Here there is one rule: exactly one sighting with the
declared serial is a match.

The sweep is injected - the same way `type_status` takes versions and canbus
results - so the join is testable without hardware and the agent can reuse a
sweep it already holds. Running state (FlashLog, device info) joins in a later
step; today a row is identity and presence.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from .discovery.byid import STATE_OFFLINE

if TYPE_CHECKING:
    from .discovery.byid import BusDevice
    from .typelist import TypeEntry

#: A row's `kind`: which declared identity it is.
SERIAL = "serial"
CANBUS_UUID = "canbus_uuid"

#: A CAN node's state: a missing version cannot tell an offline node from one
#: waiting in Katapult, so it is never guessed.
STATE_UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class Sweep:
    """What was seen: the by-id scan and the canbus results, keyed by lowercased uuid."""

    byid: tuple[BusDevice, ...] = ()
    canbus: Mapping[str, Mapping[str, Any]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Row:
    """One declared identity, and whether the sweep saw it."""

    type: str
    builder: str
    kind: str
    id: str
    present: bool
    state: str
    path: str | None
    device: BusDevice | None = None
    canbus: Mapping[str, Any] | None = None


def build(entries: Iterable[TypeEntry], sweep: Sweep) -> list[Row]:
    """A row per declared serial and canbus uuid, in type order."""
    rows: list[Row] = []
    for entry in entries:
        for serial in entry.serials:
            matches = [device for device in sweep.byid if device.serial == serial]
            match = matches[0] if len(matches) == 1 else None
            rows.append(
                Row(
                    type=entry.name,
                    builder=entry.builder,
                    kind=SERIAL,
                    id=serial,
                    present=match is not None,
                    state=match.state if match is not None else STATE_OFFLINE,
                    path=match.path if match is not None else None,
                    device=match,
                )
            )
        for uuid in entry.canbus_uuids:
            cross = sweep.canbus.get(uuid.lower())
            rows.append(
                Row(
                    type=entry.name,
                    builder=entry.builder,
                    kind=CANBUS_UUID,
                    id=uuid,
                    present=cross is not None,
                    state=STATE_UNKNOWN,
                    path=None,
                    canbus=cross,
                )
            )
    return rows


def index(rows: Iterable[Row]) -> dict[tuple[str, str, str], Row]:
    """Rows keyed by `(type, kind, id)`."""
    return {(row.type, row.kind, row.id): row for row in rows}
```

- [ ] **Step 4: Wire the agent's status paths**

In `agent/methods/status.py`:
- Line 14: add `typelist` to `from ... import API_VERSION, __version__, firmware, helpers, profiles, providers`, and add `from ... import inventory as inventory_mod` below it. Remove `device_state,` from the `...devices` import (line 23).
- Add a method next to `bus`:

```python
    def inventory(
        self, canbus: dict[str, dict[str, Any]] | None = None
    ) -> list[inventory_mod.Row]:
        """Every declared identity joined with one by-id sweep.

        Lenient about the type list: a config error is raised by the registry
        load every status path already makes, not a second time here.
        """
        entries, _ = typelist.read_config(self.paths)
        sweep = inventory_mod.Sweep(byid=tuple(scan(self.paths)), canbus=canbus or {})
        return inventory_mod.build(entries, sweep)
```

- `type_status`: add the parameter `rows: dict[tuple[str, str, str], inventory_mod.Row] | None = None` after `canbus`. Add a docstring sentence: "`rows` is the indexed inventory; passed in by `status()` so one sweep serves every type." Replace lines 383-386 with:

```python
        if rows is None:
            rows = inventory_mod.index(self.inventory())
        serials = []
        for serial in mcu.serials:
            row = rows.get((name, inventory_mod.SERIAL, serial))
            state = row.state if row is not None else STATE_OFFLINE
            path = row.path if row is not None else None
            entry = {"serial": serial, "state": state, "path": path}
```

- `status` (498-499 and 525):

```python
        canbus = self._latest_canbus_info
        rows = inventory_mod.index(self.inventory(canbus))
        types = [self.type_status(reg, n, versions, canbus, rows) for n in reg.names()]
```

  and `"targets": self.targets(reg, types, displays, rows),`.
- `targets`: add the parameter `rows: dict[tuple[str, str, str], inventory_mod.Row] | None = None`. As the first body line, add `if rows is None: rows = inventory_mod.index(self.inventory())` (as two lines). Change the cmake comprehension to `self._cmake_target(payload, allowed, families, rows)`.
- `_cmake_target`: add the parameter `rows: dict[tuple[str, str, str], inventory_mod.Row] | None = None`. Replace lines 1068-1073 with:

```python
        if rows is None:
            rows = inventory_mod.index(self.inventory())
        devices: list[dict[str, Any]] = []
        for serial in payload["serials"]:
            device_row = rows.get((name, inventory_mod.SERIAL, serial))
            present = device_row is not None and device_row.present
```

  Replace lines 1112-1113 with:

```python
                    "state": device_row.state if device_row is not None else STATE_OFFLINE,
                    "path": device_row.path if device_row is not None else None,
```

`bus()` keeps its own `scan` (Ruling 9).

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_inventory.py tests/test_agent_targets.py tests/test_agent_methods.py -q`
Expected: all pass. The `targets[]`-equals-legacy projection test at `test_agent_targets.py:644-682` proves the wire is unchanged.
Run: `python -m pytest -q`
Expected: all pass. A failure that expects a chipset-mismatched board to be offline is Ruling 8. Update that test's expectation and name the ruling in a comment.

- [ ] **Step 6: Mutation spec**

Create `scripts/mutations/inventory.json`:

```json
{
  "_comment": "The inventory is the one place a declared board meets what is on the bus. A match rule that loosens reports the wrong board as present; a caller that stops reading the row reports every board offline; a builder filter reintroduces the tracked-in-the-UI-but-not-the-CLI split this module exists to end.",
  "file": "src/mcu_updater/inventory.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_inventory.py",
    "tests/test_agent_targets.py",
    "tests/test_agent_methods.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "exactly one sighting is a match, never the first of several",
      "find": "            match = matches[0] if len(matches) == 1 else None",
      "replace": "            match = matches[0] if matches else None"
    },
    {
      "name": "every builder's types are joined, not just kconfig's",
      "find": "    for entry in entries:",
      "replace": "    for entry in [e for e in entries if e.builder == \"kconfig_make\"]:"
    },
    {
      "name": "a kconfig board's state is read from its inventory row",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "            state = row.state if row is not None else STATE_OFFLINE",
      "replace": "            state = STATE_OFFLINE"
    },
    {
      "name": "a cmake board's presence is read from its inventory row",
      "file": "src/mcu_updater/agent/methods/status.py",
      "find": "            present = device_row is not None and device_row.present",
      "replace": "            present = False"
    }
  ]
}
```

Run: `python scripts/mutation_test.py scripts/mutations/inventory.json`
Expected: every mutant killed.

Also grep `scripts/mutations/` for the replaced lines (`rg -n "sightings = scan|matches = \[device for device in sightings|device_state\(self.paths, mcu.chipset" scripts/mutations`). If any spec anchors them, re-anchor it in this commit.

- [ ] **Step 7: Docs**

Add to `docs/decisions.md`:

```markdown
### Presence comes from the inventory

`inventory.py` joins the declared identities from the one type list with one
injected sweep. A status path, the CLI or anything else that asks "is this
board plugged in" reads a row; it does not scan and match on its own. The rule
is exact serial, exactly one sighting. The by-id chipset segment is not a
filter: it is the firmware's choice of name, not the board's identity.
```

- [ ] **Step 8: Gates and commit**

Run: `python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add src/mcu_updater/inventory.py src/mcu_updater/agent/methods/status.py tests/test_inventory.py tests/test_agent_targets.py scripts/mutations docs/decisions.md
git commit -m "feat(status): join declared identities and the bus sweep in one inventory

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The CLI reads the inventory

**Files:**
- Create: `src/mcu_updater/tracking.py`
- Modify: `src/mcu_updater/agent/methods/registry.py:274-287` (`serial_add`), `:466-479` (`type_remove`), `:500-502` (`serial_remove`)
- Modify: `src/mcu_updater/cli.py:22-30` (imports), `:228-263` (`add_serial`, `remove_mcu_type`, `remove_serial`), `:300-337` (`status_cmd` after the `--can` block)
- Create: `scripts/mutations/cli-every-type.json`
- Modify: `README.md` Features (34-73) and TODO (80)
- Test: `tests/test_cli.py`, `tests/test_agent_methods.py`

**Interfaces:**
- Consumes: `Registry.declared_serials`, `Registry.remove_declared_type` (Task 2); `typelist.load` (Task 1); `inventory.build/index/Sweep/SERIAL` (Task 8); `providers.PROVIDERS`, `providers.by_name`, `providers.Install.load`, `Provider.targets/artifact_status`.
- Produces:
  - `tracking.add_serial(paths, name, serial) -> tuple[bool, str]` (added, chipset); raises `UnknownTypeError`, `SerialTrackedElsewhereError`
  - `tracking.remove_serial(paths, name, serial) -> bool`
  - `tracking.remove_type(paths, name) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (add `from mcu_updater.errors import SerialTrackedElsewhereError` and `from .conftest import make_device`):

```python
def _declare_roadrunner(paths, serial: str) -> None:
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware roadrunner]\nsource: ~/roadrunner/rp2040\nbuilder: cmake\n\n"
            "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_usbserial\nserials:\n    {serial}\n"
        )


def test_status_lists_a_cmake_type_and_its_board(c, fake_root, capsys):
    """The open bug: tracked in the UI, invisible to the CLI."""
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    out = capsys.readouterr().out
    assert "\nroadrunner  (chipset=rp2040)" in out
    assert "  roadrunner: " in out
    assert "  - RR-ONE: offline" in out

    make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-ONE")
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    assert "  - RR-ONE: online" in capsys.readouterr().out


def test_status_lists_a_platformio_type(c, pio_type, capsys):
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    assert f"\n{ENV}  (chipset=?)" in capsys.readouterr().out


def test_add_serial_tracks_a_board_under_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.add_serial(argparse.Namespace(type="roadrunner", serial="RR-NEW"))
    assert Registry.load(c.paths).declared_serials("roadrunner") == ["RR-ONE", "RR-NEW"]


def test_add_serial_refuses_a_board_tracked_under_another_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    with pytest.raises(SerialTrackedElsewhereError):
        cli.add_serial(argparse.Namespace(type="roadrunner", serial="AAAA-if00"))


def test_remove_serial_untracks_a_board_under_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.remove_serial(argparse.Namespace(type="roadrunner", serial="RR-ONE"))
    assert Registry.load(c.paths).declared_serials("roadrunner") == []


def test_remove_type_removes_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.remove_mcu_type(argparse.Namespace(type="roadrunner", force=True))
    assert "roadrunner" not in {e.name for e in typelist.load(c.paths)}
```

Add `from mcu_updater import typelist` to `test_cli.py`'s imports.

Append to `tests/test_agent_methods.py`, next to the `fw.type.remove` tests at 912-949 (they use the file's `api` fixture; add `from mcu_updater import typelist` if missing):

```python
def test_type_remove_removes_a_cmake_type(api, paths):
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware roadrunner]\nsource: ~/rr\nbuilder: cmake\n\n"
            "[type rr]\nchipset: rp2040\nfirmware: roadrunner\ncmake_target: t\nserials:\n    RR-X\n"
        )
    res = api.dispatch("fw.type.remove", {"name": "rr", "force": True})
    assert res["removed_serials"] == 1
    assert "rr" not in {e.name for e in typelist.load(paths)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cli.py tests/test_agent_methods.py -q -k "roadrunner or cmake_type or tracked_under_another"`
Expected: FAIL. `status` does not list roadrunner, and `add_serial`/`remove_serial`/`remove_mcu_type`/`fw.type.remove` raise `UnknownTypeError`.

- [ ] **Step 3: Create `src/mcu_updater/tracking.py`**

```python
"""Tracking boards and types, for every builder, from the agent and the CLI alike.

`fw.serial.add` already worked across builders, and the CLI's `add-serial` did
not - it looked the type up in the kconfig registry, so a Roadrunner type did
not exist for it. One implementation, called from both, is how the two stop
disagreeing.
"""

from __future__ import annotations

from .config import Registry
from .errors import SerialTrackedElsewhereError
from .paths import Paths


def add_serial(paths: Paths, name: str, serial: str) -> tuple[bool, str]:
    """Track `serial` under type `name`. Returns (added, the type's chipset)."""
    with Registry.mutate(paths, f"add serial {serial}") as reg:
        chipset = reg.get_declared_chipset(name)  # UnknownTypeError if absent
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
    return added, chipset


def remove_serial(paths: Paths, name: str, serial: str) -> bool:
    """Stop tracking `serial` under `name`. False if it was not tracked there."""
    with Registry.mutate(paths, f"remove serial {serial}") as reg:
        reg.get_declared_chipset(name)  # UnknownTypeError if the type doesn't exist
        return reg.remove_declared_serial(name, serial)


def remove_type(paths: Paths, name: str) -> list[str]:
    """Delete type `name`, whichever builder owns it. Returns the serials it tracked."""
    with Registry.mutate(paths, f"remove type {name}") as reg:
        return reg.remove_declared_type(name)
```

- [ ] **Step 4: Point the agent at it**

`agent/methods/registry.py`:
- `serial_add`: replace lines 274-287 (the `with Registry.mutate` block) with `        added, chipset = tracking.add_serial(self.paths, name, serial)`. The `roadrunner_unprovisioned` and `not_an_mcu` refusals above it stay.
- `serial_remove`: replace lines 500-502 with `        removed = tracking.remove_serial(self.paths, name, serial)`.
- `type_remove`: replace lines 466-479 with:

```python
        with Registry.mutate(self.paths, f"remove type {name}") as reg:
            serials = reg.declared_serials(name)  # UnknownTypeError if absent
            count = len(serials)
            if count and not force:
                raise RpcError(
                    f"'{name}' still tracks {count} board(s). Remove them first, or "
                    f"confirm to remove the type and its serials together.",
                    data={
                        "code": "type_has_serials",
                        "message": "type still tracks boards",
                        "data": {"type": name, "serials": serials},
                    },
                )
            reg.remove_declared_type(name)
```

- Add `from ... import tracking` to the imports, and drop `SerialTrackedElsewhereError` if ruff reports it unused.

- [ ] **Step 5: Point the CLI at it**

`cli.py` imports:

```python
from . import __version__, firmware, flashers, inventory, profiles, providers, stop_services, tracking, typelist
from .build import build, menuconfig_tty
from .config import Registry
from .devices import (
    STATE_KATAPULT,
    STATE_KLIPPER,
    STATE_OFFLINE,
    find_untracked,
    scan,
)
```

Keep `artifact_status` in the `.build` import only if `rg -n "artifact_status\(" src/mcu_updater/cli.py` still shows a use outside `status_cmd`. Before adding the import, confirm `rg -n "\binventory\b" src/mcu_updater/cli.py` shows no existing local of that name.

Replace `add_serial`, `remove_mcu_type` and `remove_serial` (228-263):

```python
def add_serial(args: argparse.Namespace) -> None:
    c = ctx()
    added, _ = tracking.add_serial(c.paths, args.type, args.serial)
    if added:
        print(f"Added serial {args.serial} to {args.type}")
    else:
        print(f"Serial {args.serial} already exists under {args.type}")


def remove_mcu_type(args: argparse.Namespace) -> None:
    c = ctx()
    n = len(c.registry().declared_serials(args.type))  # UnknownTypeError if absent

    # Asked before the lock is taken: a prompt must not hold the registry.
    if not args.force:
        if not _confirm(f"Remove type '{args.type}' and its {n} tracked serial(s)?"):
            print("Aborted.")
            return

    tracking.remove_type(c.paths, args.type)
    print(f"Removed MCU Type: {args.type}")


def remove_serial(args: argparse.Namespace) -> None:
    c = ctx()
    if tracking.remove_serial(c.paths, args.type, args.serial):
        print(f"Removed serial {args.serial} from {args.type}")
    else:
        print(f"Serial {args.serial} isn't tracked under {args.type} - nothing to do.")
```

In `status_cmd`, delete `reg = c.registry()` (line 269) and replace everything from `if not reg:` (300) to the end of the function (337) with:

```python
    entries = typelist.load(c.paths)
    if not entries:
        print("No MCU types configured yet.")
        return

    install = providers.Install.load(c.paths, c.settings)
    targets_by_type: dict[str, list[providers.BuildTarget]] = {}
    for provider in providers.PROVIDERS:
        for target in provider.targets(install):
            targets_by_type.setdefault(target.name, []).append(target)
    rows = inventory.index(inventory.build(entries, inventory.Sweep(byid=tuple(scan(c.paths)))))

    for entry in entries:
        print(f"\n{entry.name}  (chipset={entry.chipset or '?'})")

        # What this type builds, from its own provider - not every family that
        # exists, which would be noise about firmware nobody builds for it.
        for target in targets_by_type.get(entry.name, []):
            label = target.fw or target.provider
            try:
                status = providers.by_name(target.provider).artifact_status(install, target)
            except UpdaterError as exc:
                print(f"  {label}: unknown ({exc})")
                continue
            if status.reason == NEVER_BUILT:
                print(f"  {label}: not built")
            elif not status.is_current:
                print(f"  {label}: STALE ({status.reason})")
            else:
                print(f"  {label}: up to date")

        if not entry.serials:
            print("  (no tracked serials)")
            continue
        for serial in entry.serials:
            row = rows.get((entry.name, inventory.SERIAL, serial))
            state = row.state if row is not None else STATE_OFFLINE
            label = {
                STATE_KLIPPER: "online (klipper)",
                STATE_KATAPULT: "online (katapult/bootloader)",
            }.get(state, "offline" if state == STATE_OFFLINE else f"online ({state})")
            print(f"  - {serial}: {label}")

    untracked = find_untracked(c.paths, {s for e in entries for s in e.serials})
    if untracked:
        print("\nUntracked devices on the bus:")
        for dev in untracked:
            print(f"  - {dev.serial}  (fw={dev.fw}, chipset={dev.chipset or '?'})")
```

If `providers.Install.load`'s signature differs from `(paths, settings)`, match the call `cli.py` already makes near line 386.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_cli.py tests/test_agent_methods.py -q`
Expected: all pass.
Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Mutation spec**

Create `scripts/mutations/cli-every-type.json`:

```json
{
  "_comment": "The CLI and the agent track boards through one implementation, for every builder. A CLI that looks types up in the kconfig registry is the Roadrunner board tracked in the UI and invisible to the CLI; a tracking path that stops refusing a serial held by another type gets one board flashed twice with different firmware.",
  "file": "src/mcu_updater/tracking.py",
  "command": [
    "python",
    "-m",
    "pytest",
    "tests/test_cli.py",
    "tests/test_agent_methods.py",
    "-q"
  ],
  "mutations": [
    {
      "name": "a serial tracked under another type is refused",
      "find": "        if elsewhere:",
      "replace": "        if False:"
    },
    {
      "name": "a serial is added to whichever builder's type declares it",
      "find": "        added = reg.add_declared_serial(name, serial)",
      "replace": "        added = reg.add_serial(name, serial)"
    },
    {
      "name": "remove-type removes whichever builder's type it names",
      "find": "        return reg.remove_declared_type(name)",
      "replace": "        return reg.remove_type(name).serials"
    },
    {
      "name": "status reads each board's state from the inventory",
      "file": "src/mcu_updater/cli.py",
      "find": "            row = rows.get((entry.name, inventory.SERIAL, serial))",
      "replace": "            row = None"
    }
  ]
}
```

Run: `python scripts/mutation_test.py scripts/mutations/cli-every-type.json`
Expected: every mutant killed.

Grep `scripts/mutations/` for the lines this task moved out of `registry.py` and `cli.py` (`rg -n "find_declared_types_for_serial|add_declared_serial|device_state\(c.paths|reg.remove_type" scripts/mutations`), and re-anchor any hit to `tracking.py` in this commit.

- [ ] **Step 8: Docs**

- `README.md` Features: after "- [x] Board tracking by `/dev/serial/by-id` serial" (line 60), add `- [x] The CLI's \`status\`, \`add-serial\`, \`remove-serial\` and \`remove-type\` cover every type, whatever builds it`.
- `README.md` TODO line 80: change `**NEEDS PLAN**` to `**IN PROGRESS**` and append ` Plan 1 (config and inventory) has landed; plan 2 is device-info handlers, flasher lists and the loops.`
- Run `rg -n "Registry\.load|pio\.load|cmake\.load|\[display" AGENTS.md README.md docs --glob '!docs/superpowers/**' --glob '!docs/backlog.md'` and correct any sentence that describes separate type loaders as the design. Leave historical specs and plans alone.

- [ ] **Step 9: Gates and commit**

Run: `python -m pytest -q && python -m ruff check src tests scripts && python -m mypy src && python scripts/check_line_endings.py`

```bash
git add -A src tests scripts/mutations README.md AGENTS.md docs
git commit -m "fix(cli): status and tracking commands cover every type

The CLI read the kconfig registry, so a Roadrunner type tracked in the UI did
not exist for status, add-serial, remove-serial or remove-type. It now reads
the type list and the inventory, and tracks through the same code as the agent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run every gate once more on the branch head:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
bash -n install.sh
for spec in pio-provider-selection application-firmware type-keys firmware-source profiles display-flash provider-family-axis flashtool-path inventory cli-every-type; do
  python scripts/mutation_test.py "scripts/mutations/${spec}.json" || break
done
(cd ui && npx vitest run && npx eslint . && npx vite build)
```

Expected: all green, and every mutant killed in every spec. Run the loop in the foreground and do not interrupt it.

- [ ] Confirm the wire is unchanged: `python -m pytest tests/test_agent_targets.py -q -k legacy` passes (the projection-equals-legacy test).
- [ ] Hand off with `superpowers:finishing-a-development-branch`. Do not push or merge without asking. Printers need two things before this is deployed: a re-run of install.sh (seeds the sections) and a hand rename of `env:`/`profile:`. The refusal messages say both.
