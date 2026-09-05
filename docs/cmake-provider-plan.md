# cmake build provider — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third build provider, `cmake`, so a Roadrunner RP2040 can be
declared in `mcu-updater.cfg` and built into a staged `.uf2`.

**Architecture:** A new `providers/cmake.py` implementing the existing
`Provider` protocol, modelled on `providers/pio.py` but with subtree-scoped git
provenance. A `cmake_target:` key on `[type ...]` names which of a tree's
outputs to stage; a `cmake_args:` key on `[firmware ...]` passes cache
variables through. Two closed-set builder checks in `config.py` invert so a
third builder does not silently delete the user's config section.

**Tech Stack:** Python 3.11 floor, stdlib only, pytest, ruff, mypy.

**Spec:** [docs/cmake-provider-design.md](cmake-provider-design.md) — read it
first; this plan argues from it and does not repeat its reasoning.

## Global Constraints

- **LF line endings everywhere.** Run `python scripts/check_line_endings.py`
  before every commit.
- **stdlib only.** `pyproject.toml` has `dependencies = []` and it stays that
  way. No `gitpython`, no `cmake` PyPI package.
- **Python 3.11 is the floor.** No 3.12+ stdlib APIs. `ruff` and `mypy` pin it
  from `pyproject.toml`; `pytest` does not, so a too-new API passes locally and
  fails on the printer.
- **Keep `from __future__ import annotations`** at the top of every module
  touched.
- **Never pip-install kconfiglib.** Not touched by this plan, but the rule
  stands.
- **Gate, before every commit:**
  ```bash
  python -m pytest -q
  python -m ruff check src tests scripts
  python -m mypy src
  python scripts/check_line_endings.py
  ```
- **Commit voice:** conventional-commit prefix, lowercase sentence after it, no
  trailing period. Example: `feat(providers): add the cmake build provider`.
- **Branch:** work on `develop` or a `feat/…` branch off it. Never `main`.
- **Naming, fixed by the spec:** the config keys are `cmake_target:` (on
  `[type ...]`) and `cmake_args:` (on `[firmware ...]`). Not `variant:`, not a
  bare `target:`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/mcu_updater/firmware.py` | *Modify.* One optional `cmake_args` field on `FirmwareFamily`, read from the section and carried in `to_json()`. |
| `src/mcu_updater/config.py` | *Modify.* Invert two closed-set builder checks so this registry owns only `kconfig_make` types. Fixes a live config-deletion bug. |
| `src/mcu_updater/providers/cmake.py` | *Create.* The whole provider: `CmakeType`, `load()`, `source_state()`, sidecar read/write, `blocked()` helpers, `build()`, `artifact_status()`, and the `Cmake` adapter class. |
| `src/mcu_updater/providers/spec.py` | *Modify.* `Install` gains a `cmake` section map beside `registry` and `displays`. |
| `src/mcu_updater/providers/registry.py` | *Modify.* One line: `Cmake()` appended to `PROVIDERS`. |
| `src/mcu_updater/providers/__init__.py` | *Modify.* Re-export `Cmake`, matching how `KconfigMake` and `PlatformIO` are exported. |
| `tests/test_cmake.py` | *Create.* The provider's own suite, mirroring `tests/test_pio.py`. |
| `tests/test_config.py` | *Modify.* The save-regression guard. |
| `tests/test_firmware.py` | *Modify.* `cmake_args` parsing. |
| `tests/test_providers.py` | *Modify.* The static-registry assertion gains `cmake`. |
| `mcu-updater.cfg`, `README.md`, `docs/layout.md`, `docs/agent-api.md` | *Modify.* User-facing documentation, per AGENTS.md "Finishing a plan". |

---

### Task 1: `cmake_args` on the firmware family

**Files:**
- Modify: `src/mcu_updater/firmware.py` — `FirmwareFamily` dataclass, `load_from_doc()`, `to_json()`
- Test: `tests/test_firmware.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FirmwareFamily.cmake_args: str` (empty when the key is absent),
  carried in `to_json()` under the key `"cmake_args"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_firmware.py`:

```python
def test_cmake_args_is_read_from_the_section(paths):
    """A cache variable is a fact about one tree, not about cmake, so it lives
    in config rather than in the provider."""
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(
            "[firmware roadrunner]\n"
            "source: ~/roadrunner/rp2040\n"
            "builder: cmake\n"
            "cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${git_describe}\n"
        )
    family = firmware.load(paths)["roadrunner"]
    assert family.builder == "cmake"
    assert family.cmake_args == "-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"


def test_cmake_args_defaults_to_empty_for_every_existing_family(paths):
    """Optional, so no existing install changes shape."""
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write("[firmware cartographer]\nsource: ~/cartographer-klipper\n")
    assert firmware.load(paths)["cartographer"].cmake_args == ""
    assert firmware.resolve(paths, "klipper").cmake_args == ""


def test_cmake_args_reaches_the_json_payload(paths):
    """`agent/methods/status.py` emits the family payload; a key the panel
    cannot see is a key nobody can debug."""
    family = firmware.FirmwareFamily(name="roadrunner", cmake_args="-DFOO=1")
    assert family.to_json()["cmake_args"] == "-DFOO=1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_firmware.py -k cmake_args -v`
Expected: FAIL — `TypeError: FirmwareFamily.__init__() got an unexpected keyword argument 'cmake_args'`, or `AttributeError`.

- [ ] **Step 3: Add the field**

In `src/mcu_updater/firmware.py`, add to the `FirmwareFamily` dataclass, after
the `builder` field:

```python
    #: Extra arguments for this tree's configure step, as one shell-quoted
    #: string. Only the cmake provider reads it - a cache variable is a fact
    #: about one tree and not about cmake, so it belongs in config rather
    #: than hardcoded in a general-purpose provider. `${git_describe}` is the
    #: one substitution; see `providers/cmake.py`.
    cmake_args: str = ""
```

In `load_from_doc()`, add to the `FirmwareFamily(...)` construction:

```python
            cmake_args=(doc.get(section, "cmake_args") or "").strip(),
```

In `to_json()`, add to the returned dict:

```python
            "cmake_args": self.cmake_args,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_firmware.py -v`
Expected: PASS, and no existing firmware test regresses.

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/firmware.py tests/test_firmware.py
git commit -m "feat(firmware): carry cmake_args on a firmware family"
```

---

### Task 2: stop `Registry.save()` deleting a third builder's section

**Files:**
- Modify: `src/mcu_updater/config.py:120-135` (`_is_platformio_only`), `:388-394` (the load-time skip), `:472` (the save-time skip)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `FirmwareFamily.builder` (already present).
- Produces: `_is_foreign_builder(paths, doc, section, families_map) -> bool` —
  True when a `[type ...]` section belongs to some provider other than
  `kconfig_make`. Replaces `_is_platformio_only`.

**Why this is first, before the provider exists:** it is a live bug the moment
any third builder is declared, and it destroys hand-written user config. The
tests below fail against today's code without any of `providers/cmake.py`
existing — a `[firmware x]` with `builder: cmake` is enough to trigger it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def _cfg_with_a_cmake_type() -> str:
    return (
        "[firmware roadrunner]\n"
        "source: ~/roadrunner/rp2040\n"
        "builder: cmake\n"
        "\n"
        "[type bttebb36]\n"
        "chipset: stm32g0b1xx\n"
        "firmware: klipper, katapult\n"
        "serials:\n"
        "    912345678901234567890\n"
        "\n"
        "[type roadrunner]\n"
        "chipset: rp2040\n"
        "firmware: roadrunner\n"
        "cmake_target: roadrunner_v1_i2c_rgb\n"
        "serials:\n"
        "    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
    )


def test_a_type_built_by_a_third_builder_is_not_loaded_into_this_registry(paths):
    """Provider is derived from the declared family's builder. A cmake type
    belongs to providers/cmake.py, exactly as a platformio one belongs to
    providers/pio.py."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(_cfg_with_a_cmake_type())
    registry = Registry.load(paths)
    assert "bttebb36" in registry.types
    assert "roadrunner" not in registry.types


def test_saving_does_not_delete_a_type_this_registry_does_not_own(paths):
    """The data-loss guard.

    `save()` removes any declared type absent from `self.types`, so a type
    excluded by `load()` is one `save()` would delete - silently, from a
    hand-edited file in printer_data/config. The platformio exclusion has
    always been paired with a matching save-time skip; a third builder needs
    the same, and gets it by inverting both checks rather than adding a second
    special case.
    """
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(_cfg_with_a_cmake_type())
    registry = Registry.load(paths)
    registry.save(paths)

    text = open(paths.registry_file, encoding="utf-8").read()
    assert "[type roadrunner]" in text
    assert "cmake_target: roadrunner_v1_i2c_rgb" in text
    assert "RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ" in text
    assert "[type bttebb36]" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k third_builder -v` and
`python -m pytest tests/test_config.py -k does_not_delete -v`

Expected: the first FAILs (`roadrunner` *is* in `registry.types`, because the
skip only fires for `{"platformio"}`); the second FAILs on
`assert "[type roadrunner]" in text` once the first is fixed. Run them in that
order and confirm both fail for the stated reason before writing any code.

- [ ] **Step 3: Invert the load-time check**

In `src/mcu_updater/config.py`, replace `_is_platformio_only` with:

```python
def _is_foreign_builder(
    paths: Paths, doc: CfgDocument, section: str, families_map: dict[str, Any]
) -> bool:
    """Whether a `[type ...]` section belongs to some *other* provider.

    Ownership is positive, not a list of exclusions: this registry holds the
    `kconfig_make` types and nothing else. Written as an exclusion it was a
    closed set of two builders, and every builder added after that silently
    fell through to here - which for `save()` means deleting the user's
    section. See docs/cmake-provider-design.md.
    """
    declared_fws = doc.get_csv(section, "firmware") or []
    if not declared_fws:
        # Vacuously not foreign, not "defaults to klipper" - load() refuses a
        # section with no firmware: key before this is ever reachable for one.
        return False
    return any(
        firmware.resolve(paths, fw, families_map).builder != "kconfig_make"
        for fw in declared_fws
    )
```

At `config.py:387-409`, replace the whole builders block. **The two clauses
swap order**: today the platformio skip comes first and the mixed-builder raise
second, which was safe only because a mixed set can never equal
`{"platformio"}`. Inverted to `!= {"kconfig_make"}`, a mixed set *does* match
the skip — so a broken section would be silently dropped instead of raising.
The raise has to come first.

```python
            builders = {
                firmware.resolve(paths, fw, families_map).builder for fw in declared_fws
            }
            if len(builders) > 1:
                # A type is built by exactly one provider - the seam that
                # compiles it is chosen from its families' builder, so a type
                # whose declared families disagree has no single answer.
                #
                # Checked *before* the ownership skip below. Under the old
                # `== {"platformio"}` form the order did not matter, because a
                # mixed set never equalled it; under `!= {"kconfig_make"}` a
                # mixed set matches, and letting the skip run first would turn
                # this error into a silently ignored section.
                raise ConfigCorruptError(
                    f"{path}: '{name}' declares firmware families built by "
                    f"different tools ({', '.join(sorted(builders))}): "
                    f"{', '.join(declared_fws)}. A type is built by exactly one "
                    f"provider - split it into two types if it genuinely needs "
                    f"both.",
                    path=path,
                    type=name,
                    value=declared_fws,
                )
            if declared_fws and builders != {"kconfig_make"}:
                # A type whose declared firmware is built by anything other
                # than kconfig+make belongs to that provider's registry, not
                # this one - providers/pio.py's load() and providers/cmake.py's
                # apply the same rule from their own side. A type with no
                # explicit firmware: at all defaults to klipper (kconfig_make)
                # and is unaffected.
                continue
```

At `config.py:472`, rename the call:

```python
            if _is_foreign_builder(paths, doc, declared.section, families_map):
```

Update its comment to say "not one of ours by its declared firmware's builder"
without naming platformio specifically.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py tests/test_pio.py tests/test_providers.py -v`
Expected: PASS. The platformio exclusion tests must still pass unchanged —
`platformio != "kconfig_make"`, so they are covered by the general rule.

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/config.py tests/test_config.py
git commit -m "fix(config): stop save() deleting a type built by a third builder"
```

---

### Task 3: `CmakeType` and `load()`

**Files:**
- Create: `src/mcu_updater/providers/cmake.py`
- Create: `tests/test_cmake.py`

**Interfaces:**
- Consumes: `FirmwareFamily.cmake_args` (Task 1), `sections.read()`,
  `firmware.load_from_doc()`, `firmware.resolve()`.
- Produces:
  - `CmakeType(name: str, cmake_target: str, source: str, firmware: str, cmake_args: str)`
  - `load(paths: Paths) -> dict[str, CmakeType]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cmake.py`:

```python
"""The cmake provider: config parsing, subtree-scoped provenance, staging.

Mirrors `test_pio.py`, which is the closest existing suite. The parts that are
deliberately *not* copied from it - subtree-scoped git, an artifact path the
provider owns - have their own tests saying why.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater.errors import ConfigError
from mcu_updater.providers import cmake


def write_config(paths, text: str) -> None:
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text)


ROADRUNNER_CFG = """
[firmware roadrunner]
source: {source}
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${{git_describe}}

[type roadrunner]
chipset: rp2040
firmware: roadrunner
cmake_target: roadrunner_v1_i2c_rgb
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
"""


def test_a_cmake_type_loads_with_its_target_and_args(paths, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    types = cmake.load(paths)
    assert set(types) == {"roadrunner"}
    rr = types["roadrunner"]
    assert rr.cmake_target == "roadrunner_v1_i2c_rgb"
    assert rr.firmware == "roadrunner"
    assert rr.source == str(tmp_path)
    assert rr.cmake_args == "-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"


def test_a_kconfig_type_is_not_ours(paths):
    """Provider is derived from the declared family's builder, exactly as
    pio.load() derives its own."""
    write_config(
        paths,
        "[type bttebb36]\nchipset: stm32g0b1xx\nfirmware: klipper, katapult\nserials:\n",
    )
    assert cmake.load(paths) == {}


def test_a_cmake_type_with_no_target_is_refused(paths, tmp_path):
    """The same refusal pio.load() gives a PlatformIO type with no env:. A
    default would mean guessing which of six images belongs on the board."""
    write_config(
        paths,
        f"[firmware roadrunner]\nsource: {tmp_path}\nbuilder: cmake\n\n"
        "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\nserials:\n",
    )
    with pytest.raises(ConfigError) as exc:
        cmake.load(paths)
    assert "roadrunner" in str(exc.value)
    assert "cmake_target" in str(exc.value)


def test_a_missing_config_file_is_not_an_error(paths):
    """No config means no cmake types, which is what every install has today."""
    assert cmake.load(paths) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcu_updater.providers.cmake'`.

- [ ] **Step 3: Create the module with `CmakeType` and `load()`**

Create `src/mcu_updater/providers/cmake.py`:

```python
"""CMake trees: configure once, build all targets, stage the one named.

The third build system, and the first whose `source:` is a *subdirectory* of
its repository. Both facts shape this module.

**One build produces every target.** Roadrunner's `CMakeLists.txt` declares six
executables - three transports times two LED orderings - and one `make`
produces all six `.uf2` files. So `cmake_target:` selects *which output is
staged*, not what gets compiled, and build-time and flash-time selection fall
out of one key. See docs/cmake-provider-design.md.

**Git is scoped to the source subtree.** `pio.source_state()` asks the whole
repository, which was always right there because knomi serial's `source:` is
its repo root. Here it is `~/roadrunner/rp2040`, and git run from a
subdirectory still answers about the whole repo - so the repo-wide form would
mark the built `.uf2` stale on every commit to `klippy/extras`, rebuilding it
to produce byte-identical output. The provenance questions are asked with an
explicit `-- .` pathspec; only `git describe` stays repo-wide, because a tag
is a fact about a repository and not about one directory in it.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess

from .. import firmware, sections
from ..cfgdoc import CfgDocument
from ..errors import ConfigError
from ..paths import Paths

#: What this provider's `builder:` value is, in `[firmware ...]`.
BUILDER = "cmake"


@dataclasses.dataclass
class CmakeType:
    """One cmake target, and the tree that builds it."""

    name: str
    #: The cmake target to stage, spelled exactly as `add_executable()` names
    #: it. CMake's own vocabulary - `make <target>`, `cmake --build --target`.
    #: Not a short form: expanding one would mean knowing a naming convention
    #: that belongs to one vendor's CMakeLists.
    cmake_target: str = ""
    source: str = ""
    #: The declared `firmware:` family. Required to load at all, so never
    #: empty on an instance `load()` returns.
    firmware: str = ""
    #: Configure-step arguments from the family's `cmake_args:`, verbatim.
    cmake_args: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "cmake_target": self.cmake_target,
            "source": self.source,
            "firmware": self.firmware,
            "cmake_args": self.cmake_args,
        }


def load(paths: Paths) -> dict[str, CmakeType]:
    """Read this provider's type sections from the shared config file.

    A type is ours if the family it declares is built by `cmake` - the same
    "provider is derived from the family's builder" rule `config.py` and
    `pio.load()` apply from their own sides.
    """
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError:
        return {}

    families_map = firmware.load_from_doc(doc)

    out: dict[str, CmakeType] = {}
    for declared in sections.read(doc):
        name, section = declared.name, declared.section
        declared_fws = doc.get_csv(section, "firmware") or []
        if not declared_fws:
            continue
        first_fw = declared_fws[0]
        family = firmware.resolve(paths, first_fw, families_map)
        if family.builder != BUILDER:
            continue

        cmake_target = (doc.get(section, "cmake_target") or "").strip()
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
        )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py
git commit -m "feat(providers): read cmake types and their cmake_target"
```

---

### Task 4: subtree-scoped `source_state()` and `${git_describe}`

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py`
- Test: `tests/test_cmake.py`

**Interfaces:**
- Consumes: `CmakeType` (Task 3).
- Produces:
  - `SourceState(sha: str | None, dirty: bool, version: str | None)`
  - `source_state(source: str) -> SourceState`
  - `expand_args(cmake_args: str, state: SourceState) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cmake.py`:

```python
def _git(directory, *args) -> str:
    return subprocess.run(
        ("git",) + args, cwd=directory, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo whose firmware source is a *subdirectory*, with a sibling.

    This is the Roadrunner's shape: rp2040/ beside klippy/, both edited
    independently. It is what makes repo-wide git wrong here.
    """
    root = tmp_path / "roadrunner"
    (root / "rp2040").mkdir(parents=True)
    (root / "klippy").mkdir()
    (root / "rp2040" / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (root / "klippy" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


def test_provenance_ignores_a_commit_outside_the_source_subtree(repo):
    """The reason this provider does not reuse pio.source_state().

    A commit to klippy/ moves repo HEAD but changes nothing the firmware is
    built from. Reporting it as a source change rebuilds the .uf2 to produce
    byte-identical output, on every unrelated commit.
    """
    source = str(repo / "rp2040")
    before = cmake.source_state(source)

    (repo / "klippy" / "extra.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch klippy only")

    after = cmake.source_state(source)
    assert after.sha == before.sha
    assert _git(repo, "rev-parse", "HEAD") != before.sha


def test_provenance_follows_a_commit_inside_the_source_subtree(repo):
    source = str(repo / "rp2040")
    before = cmake.source_state(source)

    (repo / "rp2040" / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch rp2040")

    assert cmake.source_state(source).sha != before.sha


def test_dirtiness_is_scoped_to_the_subtree_too(repo):
    """A dirty sibling must not stamp -dirty on a clean firmware build."""
    source = str(repo / "rp2040")
    (repo / "klippy" / "extra.py").write_text("uncommitted\n", encoding="utf-8")
    state = cmake.source_state(source)
    assert state.dirty is False
    assert state.version is not None and not state.version.endswith("-dirty")

    (repo / "rp2040" / "CMakeLists.txt").write_text("project(rr2)\n", encoding="utf-8")
    dirty = cmake.source_state(source)
    assert dirty.dirty is True
    assert dirty.version.endswith("-dirty")


def test_the_version_string_is_repo_wide_and_carries_the_hash(repo):
    """A tag is a fact about a repository, not one directory in it. With no
    tags, --always still yields the commit hash, which is what makes this
    usable on develop."""
    state = cmake.source_state(str(repo / "rp2040"))
    assert state.version
    assert state.version != "dev"

    _git(repo, "tag", "v1.0")
    assert cmake.source_state(str(repo / "rp2040")).version == "v1.0"


def test_a_non_checkout_yields_no_provenance(tmp_path):
    assert cmake.source_state(str(tmp_path)) == cmake.SourceState()
    assert cmake.source_state("") == cmake.SourceState()


def test_git_describe_is_substituted_into_the_configure_args(repo):
    state = cmake.source_state(str(repo / "rp2040"))
    args = cmake.expand_args("-DROADRUNNER_FIRMWARE_VERSION=${git_describe}", state)
    assert args == [f"-DROADRUNNER_FIRMWARE_VERSION={state.version}"]


def test_git_describe_falls_back_to_dev_outside_a_checkout(tmp_path):
    """`dev` is the CMakeLists' own default, so the fallback agrees with the
    firmware rather than inventing a third answer."""
    state = cmake.source_state(str(tmp_path))
    assert cmake.expand_args("-DV=${git_describe}", state) == ["-DV=dev"]


def test_empty_args_produce_no_arguments():
    assert cmake.expand_args("", cmake.SourceState()) == []


def test_args_are_split_as_a_shell_would(repo):
    """One config string, several arguments - and a quoted value stays one."""
    state = cmake.source_state(str(repo / "rp2040"))
    assert cmake.expand_args('-DA=1 -DB="two words"', state) == ["-DA=1", "-DB=two words"]
```

Add `import subprocess` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cmake.py -k "provenance or dirtiness or describe or version_string or non_checkout or args" -v`
Expected: FAIL — `AttributeError: module 'mcu_updater.providers.cmake' has no attribute 'source_state'`.

- [ ] **Step 3: Implement**

Append to `src/mcu_updater/providers/cmake.py`:

```python
#: The one substitution `cmake_args:` supports. Deliberately one: a general
#: templating language in a config file is a debugging surface nobody asked
#: for, and this is the only value that cannot be written down in advance.
GIT_DESCRIBE_TOKEN = "${git_describe}"

#: What the CMakeLists itself defaults to, so an unresolvable describe agrees
#: with the firmware rather than inventing a third answer.
UNKNOWN_VERSION_STRING = "dev"


@dataclasses.dataclass(frozen=True)
class SourceState:
    """What this source subtree would build right now.

    `sha` and `dirty` are subtree-scoped and decide rebuilds; `version` is
    repo-wide and is what gets compiled into the firmware for a board to
    report back. They routinely disagree, and both are recorded.
    """

    #: Last commit touching the source directory. None when it is not a
    #: checkout, or nothing has ever been committed there.
    sha: str | None = None
    #: Uncommitted changes *inside the source directory*.
    dirty: bool = False
    #: `git describe` for the whole repository, plus a `-dirty` suffix taken
    #: from the subtree-scoped status above. None when not a checkout.
    version: str | None = None


def _git(directory: str, *args: str) -> str | None:
    """Run git in `directory`, or None if it could not answer.

    Not a checkout, no git on PATH, and a timeout are all the same answer
    here: we cannot vouch for this tree. Mirrors `pio._git`.
    """
    try:
        out = subprocess.check_output(
            ("git",) + args, cwd=directory, stderr=subprocess.DEVNULL, timeout=10
        )
    except Exception:  # noqa: BLE001 - not a checkout, no git, or a timeout
        return None
    return out.decode("utf-8", "replace").strip()


def source_state(source: str) -> SourceState:
    """Read the source subtree's identity. Everything optional.

    The `-- .` pathspecs are the whole point; see the module docstring. Note
    that `git describe` takes no pathspec, which is why `--dirty` is not used
    and the suffix is appended from the subtree-scoped status instead - a
    dirty sibling directory must not stamp `-dirty` on a clean firmware build.
    """
    path = os.path.expanduser(source or "")
    if not path or not os.path.isdir(path):
        return SourceState()

    sha = _git(path, "log", "-1", "--format=%H", "--", ".") or None
    if sha is None:
        return SourceState()

    dirty = bool(_git(path, "status", "--porcelain", "--", "."))
    described = _git(path, "describe", "--tags", "--always") or UNKNOWN_VERSION_STRING
    version = f"{described}-dirty" if dirty else described
    return SourceState(sha=sha, dirty=dirty, version=version)


def expand_args(cmake_args: str, state: SourceState) -> list[str]:
    """The family's `cmake_args:`, split and substituted.

    Split with `shlex` so a quoted value stays one argument - these go
    straight into an argv, never through a shell.
    """
    if not cmake_args.strip():
        return []
    version = state.version or UNKNOWN_VERSION_STRING
    return [
        arg.replace(GIT_DESCRIBE_TOKEN, version) for arg in shlex.split(cmake_args)
    ]
```

Add `import shlex` to the module's imports, keeping them alphabetical:
`dataclasses`, `os`, `shlex`, `subprocess`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py
git commit -m "feat(providers): scope cmake provenance to the source subtree"
```

---

### Task 5: `blocked()` — the setup that happens outside this tool

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py`
- Test: `tests/test_cmake.py`

**Interfaces:**
- Consumes: `CmakeType` (Task 3).
- Produces:
  - `build_dir(source: str) -> str`
  - `staged_uf2(source: str, cmake_target: str) -> str`
  - `declared_targets(source: str) -> set[str] | None`
  - `source_problem(target: CmakeType) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cmake.py`:

```python
def _cmake_type(source, target="roadrunner_v1_i2c_rgb"):
    return cmake.CmakeType(
        name="roadrunner", cmake_target=target, source=str(source), firmware="roadrunner"
    )


def test_no_source_names_the_missing_key(tmp_path):
    problem = cmake.source_problem(cmake.CmakeType(name="roadrunner", cmake_target="t"))
    assert problem is not None
    assert "source:" in problem


def test_a_source_that_does_not_exist_says_so_differently(tmp_path):
    """Absent and non-existent are separated because the fixes differ - one is
    a missing key, the other a path that is there and wrong. Same split as
    pio.source_problem."""
    missing = tmp_path / "nope"
    problem = cmake.source_problem(_cmake_type(missing))
    assert problem is not None
    assert str(missing) in problem
    assert "source:" not in problem


def test_a_tree_with_no_cmakelists_is_blocked(tmp_path):
    problem = cmake.source_problem(_cmake_type(tmp_path))
    assert problem is not None
    assert "CMakeLists.txt" in problem


def test_an_uninitialised_submodule_is_named_with_its_fix(tmp_path):
    """The CMake error for this is unreadable, so it is worth catching first.
    Read from .gitmodules rather than hardcoding pico-sdk/, which is the Pico
    SDK's path and not a fact about cmake trees."""
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "pico-sdk"]\n\tpath = pico-sdk\n\turl = https://example/x\n',
        encoding="utf-8",
    )
    (tmp_path / "pico-sdk").mkdir()
    problem = cmake.source_problem(_cmake_type(tmp_path))
    assert problem is not None
    assert "pico-sdk" in problem
    assert "git submodule update --init --recursive" in problem


def test_a_populated_submodule_is_not_a_problem(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "pico-sdk"]\n\tpath = pico-sdk\n', encoding="utf-8"
    )
    (tmp_path / "pico-sdk").mkdir()
    (tmp_path / "pico-sdk" / "pico_sdk_init.cmake").write_text("", encoding="utf-8")
    assert cmake.source_problem(_cmake_type(tmp_path)) is None


def test_target_list_is_unknown_before_a_configure(tmp_path):
    """No build directory means nothing to ask. Returning None rather than an
    empty set matters: empty would read as 'this tree declares no targets' and
    block every type."""
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    assert cmake.declared_targets(str(tmp_path)) is None


def test_a_mistyped_target_is_blocked_once_the_list_is_known(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    monkeypatch.setattr(
        cmake, "declared_targets", lambda source: {"roadrunner_v1_i2c_rgb", "clean"}
    )
    problem = cmake.source_problem(_cmake_type(tmp_path, target="roadrunner_v1_i2c_rbg"))
    assert problem is not None
    assert "roadrunner_v1_i2c_rbg" in problem
    assert "roadrunner_v1_i2c_rgb" in problem


def test_a_known_target_is_not_blocked(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    monkeypatch.setattr(cmake, "declared_targets", lambda source: {"roadrunner_v1_i2c_rgb"})
    assert cmake.source_problem(_cmake_type(tmp_path)) is None


def test_the_target_help_output_is_parsed(tmp_path, monkeypatch):
    """`cmake --build <dir> --target help` prints one '... <name>' per line,
    with the default target annotated."""
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(
        cmake,
        "_run",
        lambda *a, **k: (
            "The following are some of the valid targets for this Makefile:\n"
            "... all (the default if no target is provided)\n"
            "... clean\n"
            "... roadrunner_v1_i2c_rgb\n"
            "... roadrunner_v1_uart_grb\n"
        ),
    )
    assert cmake.declared_targets(str(tmp_path)) == {
        "all",
        "clean",
        "roadrunner_v1_i2c_rgb",
        "roadrunner_v1_uart_grb",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cmake.py -k "source_problem or submodule or target_list or mistyped or known_target or target_help or no_source or cmakelists" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'source_problem'`.

- [ ] **Step 3: Implement**

Append to `src/mcu_updater/providers/cmake.py`:

```python
#: Where the configure step puts the build tree, relative to `source:`.
BUILD_SUBDIR = "build"


def build_dir(source: str) -> str:
    return os.path.join(os.path.expanduser(source or ""), BUILD_SUBDIR)


def staged_uf2(source: str, cmake_target: str) -> str:
    """Where cmake leaves this target's image, before we stage it.

    Not routed through `firmware.built_artifact`, which hardcodes
    `out/<artifact>.<ext>` - a Klipper-Makefile convention this build system
    does not share.
    """
    return os.path.join(build_dir(source), f"{cmake_target}.uf2")


def _run(argv: list[str], cwd: str) -> str | None:
    """Capture a short command's stdout, or None if it could not answer."""
    try:
        out = subprocess.check_output(
            argv, cwd=cwd, stderr=subprocess.DEVNULL, timeout=30
        )
    except Exception:  # noqa: BLE001 - no cmake, an unconfigured tree, a timeout
        return None
    return out.decode("utf-8", "replace")


def declared_targets(source: str) -> set[str] | None:
    """Every target this tree declares, or None when it cannot be asked.

    Only answerable *after* a configure - the target list lives in the
    generated build system, not in `CMakeLists.txt`. None rather than an empty
    set on purpose: empty would read as "this tree declares no targets" and
    block every type that names one.

    Parsing `CMakeLists.txt` instead was rejected - that is reimplementing
    CMake, and a first build on a fresh clone would have nothing to check
    against either way.
    """
    build = build_dir(source)
    if not os.path.isdir(build):
        return None
    text = _run(["cmake", "--build", build, "--target", "help"], cwd=build)
    if text is None:
        return None
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("..."):
            continue
        name = stripped[3:].strip().split(" ", 1)[0].strip()
        if name:
            found.add(name)
    return found or None


def _uninitialised_submodule(source: str) -> str | None:
    """A submodule declared in `.gitmodules` whose directory is empty.

    Named explicitly because the CMake failure for it is unreadable - the Pico
    SDK's include of `pico_sdk_init.cmake` fails several layers down. Read from
    `.gitmodules` rather than hardcoding `pico-sdk/`, which is one SDK's path
    and not a fact about cmake trees in general.
    """
    modules = os.path.join(source, ".gitmodules")
    try:
        with open(modules, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "path":
            continue
        rel = value.strip()
        if not rel:
            continue
        directory = os.path.join(source, rel)
        if os.path.isdir(directory) and not os.listdir(directory):
            return rel
    return None


def source_problem(target: CmakeType) -> str | None:
    """Why this type cannot be built, or None if it can be attempted.

    A module function, mirroring `pio.source_problem`, so a status payload can
    ask without assembling an `Install` it does not need.

    Only setup that has to happen *outside this tool*. A missing ARM
    toolchain, a syntax error or a full disk are all things to find out by
    trying: reporting them as failures is more useful than pretending we knew.
    """
    source = os.path.expanduser(target.source or "")
    if not source:
        return (
            f"'{target.name}' has no source tree configured - set 'source:' on "
            f"its firmware family."
        )
    if not os.path.isdir(source):
        return f"source directory {source} not found for '{target.name}'."
    if not os.path.isfile(os.path.join(source, "CMakeLists.txt")):
        return (
            f"no CMakeLists.txt in {source} - 'source:' should name the "
            f"directory holding it, not the repository root."
        )
    empty = _uninitialised_submodule(source)
    if empty is not None:
        return (
            f"submodule '{empty}' in {source} is empty - run "
            f"'git submodule update --init --recursive' in that tree first."
        )

    known = declared_targets(source)
    if known is not None and target.cmake_target not in known:
        return (
            f"'{target.name}' names cmake_target '{target.cmake_target}', which "
            f"this tree does not declare. Known: {', '.join(sorted(known))}."
        )
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS (22 tests).

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py
git commit -m "feat(providers): tell a cmake setup problem from a build failure"
```

---

### Task 6: `build()`, staging, and the provenance sidecar

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py`
- Test: `tests/test_cmake.py`

**Interfaces:**
- Consumes: `source_state()`, `expand_args()` (Task 4), `build_dir()`,
  `staged_uf2()` (Task 5).
- Produces:
  - `needs_configure(source: str) -> bool`
  - `record_build(paths: Paths, target: CmakeType, state: SourceState) -> None`
  - `read_sidecar(paths: Paths, target: CmakeType) -> dict | None`
  - `build(paths, settings, target, *, reporter=null_reporter, cancel=None) -> str`
    (returns the staged path)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cmake.py`:

```python
def test_a_configured_tree_is_not_reconfigured(tmp_path):
    """Re-running configure on every build is slow and changes nothing."""
    source = tmp_path / "rp2040"
    (source / "build").mkdir(parents=True)
    assert cmake.needs_configure(str(source)) is True

    (source / "build" / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\n", encoding="utf-8"
    )
    assert cmake.needs_configure(str(source)) is False


def test_a_cache_from_another_tree_forces_a_reconfigure(tmp_path):
    """A moved or copied build directory points at the wrong sources."""
    source = tmp_path / "rp2040"
    (source / "build").mkdir(parents=True)
    (source / "build" / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=/somewhere/else\n", encoding="utf-8"
    )
    assert cmake.needs_configure(str(source)) is True


def test_a_dry_run_stages_nothing_and_runs_both_steps(paths, settings, tmp_path, capture_reporter):
    """A rehearsal must never leave an artifact behind, or the next status
    poll vouches for a build that did not happen."""
    source = tmp_path / "rp2040"
    source.mkdir()
    (source / "CMakeLists.txt").write_text("project(rr)\n", encoding="utf-8")
    target = _cmake_type(source)

    dry = dataclasses.replace(settings, dry_run=True)
    cmake.build(paths, dry, target, reporter=capture_reporter.reporter)

    cmds = [line for stream, line in capture_reporter.lines if stream == "cmd"]
    assert any("cmake" in c and "-S" in c for c in cmds)
    assert any("make" in c for c in cmds)
    assert not os.path.exists(paths.uf2_file("roadrunner", "roadrunner"))


def test_a_successful_build_stages_the_named_target(paths, settings, repo, monkeypatch):
    """The whole point of cmake_target: one make produces six images and
    exactly one is staged."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    target = _cmake_type(source)

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        reporter("cmd", " ".join(cmd))
        for name in ("roadrunner_v1_i2c_rgb", "roadrunner_v1_uart_grb"):
            (source / "build" / f"{name}.uf2").write_bytes(name.encode())
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    staged = cmake.build(paths, settings, target)

    assert staged == paths.uf2_file("roadrunner", "roadrunner")
    assert open(staged, "rb").read() == b"roadrunner_v1_i2c_rgb"


def test_a_nonzero_exit_raises_build_error(paths, settings, tmp_path, monkeypatch):
    source = tmp_path / "rp2040"
    source.mkdir()
    monkeypatch.setattr(cmake.build_mod, "run_streamed", lambda *a, **k: 2)
    with pytest.raises(BuildError) as exc:
        cmake.build(paths, settings, _cmake_type(source))
    assert exc.value.data["returncode"] == 2


def test_a_missing_output_is_a_build_error_naming_the_target(paths, settings, repo, monkeypatch):
    """make succeeded but the named target produced nothing - a typo that
    survived because the tree was never configured when blocked() ran."""
    source = repo / "rp2040"
    (source / "build").mkdir()
    monkeypatch.setattr(cmake.build_mod, "run_streamed", lambda *a, **k: 0)
    with pytest.raises(BuildError) as exc:
        cmake.build(paths, settings, _cmake_type(source))
    assert "roadrunner_v1_i2c_rgb" in str(exc.value)


def test_the_sidecar_records_the_subtree_commit_and_the_bytes(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    (source / "build").mkdir()

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = _cmake_type(source)
    cmake.build(paths, settings, target)

    record = cmake.read_sidecar(paths, target)
    assert record is not None
    assert record["sha"] == cmake.source_state(str(source)).sha
    assert record["version"] == cmake.source_state(str(source)).version
    assert record["bin_sha256"] is not None
    assert record["dirty"] is False


def test_configure_args_carry_the_expanded_version(paths, settings, repo, monkeypatch):
    source = repo / "rp2040"
    seen: list[list[str]] = []

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        seen.append(list(cmd))
        (source / "build").mkdir(exist_ok=True)
        (source / "build" / "roadrunner_v1_i2c_rgb.uf2").write_bytes(b"IMAGE")
        return 0

    monkeypatch.setattr(cmake.build_mod, "run_streamed", fake_run)
    target = dataclasses.replace(
        _cmake_type(source), cmake_args="-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"
    )
    cmake.build(paths, settings, target)

    version = cmake.source_state(str(source)).version
    assert [f"-DROADRUNNER_FIRMWARE_VERSION={version}"] == [
        a for a in seen[0] if a.startswith("-DROADRUNNER")
    ]
```

Add to the test module's imports: `import dataclasses` and
`from mcu_updater.errors import BuildError, ConfigError`.

> **`capture_reporter` fixture:** check `tests/conftest.py` for an existing
> reporter-capturing fixture and use it. If none exists, add this to
> `tests/test_cmake.py` rather than to conftest:
>
> ```python
> @pytest.fixture
> def capture_reporter():
>     class Capture:
>         def __init__(self):
>             self.lines: list[tuple[str, str]] = []
>
>         def reporter(self, stream: str, line: str) -> None:
>             self.lines.append((stream, line))
>
>     return Capture()
> ```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cmake.py -k "configure or dry_run or stages or nonzero or missing_output or sidecar or configure_args" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'needs_configure'`.

- [ ] **Step 3: Implement**

Append to `src/mcu_updater/providers/cmake.py`:

```python
def needs_configure(source: str) -> bool:
    """Does the configure step have to run?

    A cache naming this same source tree means the build system is generated
    and current enough for `make` to pick up any `CMakeLists.txt` change
    itself - cmake re-runs configure on its own when it needs to. A cache
    naming a *different* tree is a build directory that was moved or copied,
    and building in it would compile somebody else's sources.
    """
    cache = os.path.join(build_dir(source), "CMakeCache.txt")
    want = os.path.realpath(os.path.expanduser(source or ""))
    try:
        with open(cache, encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.partition("=")
                if sep and key.split(":", 1)[0].strip() == "CMAKE_HOME_DIRECTORY":
                    return os.path.realpath(value.strip()) != want
    except OSError:
        return True
    return True


def record_build(paths: Paths, target: CmakeType, state: SourceState) -> None:
    """Note which commit produced the image now staged.

    Records a hash of the staged bytes, which is what makes "is this still our
    build?" answerable at all - see `pio.record_build` for the argument. Both
    git facts are kept: `sha` is subtree-scoped and decides rebuilds, `version`
    is repo-wide and is what the board reports back.
    """
    path = paths.uf2_file(target.name, target.firmware)
    try:
        stat = os.stat(path)
    except OSError:
        return

    record = {
        "sha": state.sha,
        "version": state.version,
        "dirty": state.dirty,
        "cmake_target": target.cmake_target,
        "at": time.time(),
        "bin_sha256": build_mod.sha256_file(path),
        "bin_size": stat.st_size,
        "bin_mtime": stat.st_mtime,
    }
    sidecar = paths.sidecar_file(target.name, target.firmware)
    os.makedirs(os.path.dirname(sidecar), exist_ok=True)
    tmp = sidecar + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    os.replace(tmp, sidecar)


def read_sidecar(paths: Paths, target: CmakeType) -> dict | None:
    """This type's build record, or None when there is not a usable one.

    Degrades to None on every failure - missing, unreadable and non-dict all
    mean "no provenance", and telling them apart would not change any answer.
    """
    try:
        with open(paths.sidecar_file(target.name, target.firmware), encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def build(
    paths: Paths,
    settings: Settings,
    target: CmakeType,
    *,
    reporter: Reporter = null_reporter,
    cancel: threading.Event | None = None,
) -> str:
    """Configure if needed, build every target, stage the one named.

    Returns the staged path. One `make` produces every image the tree
    declares; `cmake_target` selects which of them is staged, so build-time
    and flash-time selection come out of one config key.
    """
    source = os.path.expanduser(target.source or "")
    build_path = build_dir(source)
    state = source_state(source)

    if needs_configure(source):
        argv = ["cmake", "-S", source, "-B", build_path]
        argv += expand_args(target.cmake_args, state)
        reporter("info", f"Configuring {source}...")
        rc = build_mod.run_streamed(
            argv,
            cwd=source,
            reporter=reporter,
            cancel=cancel,
            dry_run=settings.dry_run,
        )
        if rc != 0:
            raise BuildError(
                f"cmake configure failed for '{target.name}': cmake exited {rc}.",
                type=target.name,
                fw=target.firmware,
                returncode=rc,
            )

    reporter("info", f"Building {target.cmake_target}...")
    rc = build_mod.run_streamed(
        ["make", "-C", build_path, *settings.make_flags()],
        cwd=source,
        reporter=reporter,
        cancel=cancel,
        dry_run=settings.dry_run,
    )
    if rc != 0:
        raise BuildError(
            f"cmake build failed for '{target.name}': make exited {rc}.",
            type=target.name,
            fw=target.firmware,
            returncode=rc,
        )

    staged = paths.uf2_file(target.name, target.firmware)
    if settings.dry_run:
        # Never stage on a rehearsal. An artifact left behind here is one the
        # next status poll would vouch for, having never been compiled.
        return staged

    produced = staged_uf2(source, target.cmake_target)
    if not os.path.exists(produced):
        raise BuildError(
            f"make succeeded but produced no {produced} - does this tree declare "
            f"a target named '{target.cmake_target}'?",
            type=target.name,
            fw=target.firmware,
        )
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    shutil.copyfile(produced, staged)
    reporter("info", f"Staged {staged}")

    # After the copy, so the record describes the bytes that now exist.
    record_build(paths, target, state)
    return staged
```

Add to the module's imports:

```python
import json
import shutil
import threading
import time

from .. import build as build_mod
from ..build import Reporter, null_reporter
from ..errors import BuildError, ConfigError
from ..settings import Settings
```

(Fold `BuildError` into the existing `..errors` import rather than adding a
second line.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS (30 tests).

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py
git commit -m "feat(providers): build a cmake tree and stage the named target"
```

---

### Task 7: `artifact_status()`

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py`
- Test: `tests/test_cmake.py`

**Interfaces:**
- Consumes: `read_sidecar()`, `record_build()` (Task 6), `SourceState` (Task 4).
- Produces: `artifact_status(paths: Paths, target: CmakeType, state: SourceState) -> ArtifactStatus`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cmake.py`:

```python
def _staged(paths, data=b"IMAGE"):
    path = paths.uf2_file("roadrunner", "roadrunner")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_no_image_is_never_built(paths, tmp_path):
    status = cmake.artifact_status(paths, _cmake_type(tmp_path), cmake.SourceState())
    assert status.reason == NEVER_BUILT
    assert not status.is_current


def test_an_image_with_no_sidecar_has_no_provenance(paths, tmp_path):
    """Claiming current about a binary we know nothing about is the failure -
    it flashes a board with firmware from before the fix you just made."""
    _staged(paths)
    status = cmake.artifact_status(paths, _cmake_type(tmp_path), cmake.SourceState())
    assert status.reason == NO_PROVENANCE


def test_an_image_somebody_else_rebuilt_has_no_provenance(paths, repo):
    source = repo / "rp2040"
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, state)

    _staged(paths, b"SOMETHING ELSE")
    assert cmake.artifact_status(paths, target, state).reason == NO_PROVENANCE


def test_a_build_from_a_dirty_subtree_is_built_dirty(paths, repo):
    source = repo / "rp2040"
    (source / "CMakeLists.txt").write_text("project(rr2)\n", encoding="utf-8")
    target = _cmake_type(source)
    state = cmake.source_state(str(source))
    assert state.dirty is True

    _staged(paths)
    cmake.record_build(paths, target, state)
    assert cmake.artifact_status(paths, target, state).reason == BUILT_DIRTY


def test_a_commit_to_the_subtree_makes_the_image_stale(paths, repo):
    source = repo / "rp2040"
    target = _cmake_type(source)
    built_from = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, built_from)

    (source / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change firmware")

    now = cmake.source_state(str(source))
    assert cmake.artifact_status(paths, target, now).reason == SOURCE_CHANGED


def test_a_commit_outside_the_subtree_leaves_the_image_current(paths, repo):
    """The regression pio's repo-wide source_state would fail."""
    source = repo / "rp2040"
    target = _cmake_type(source)
    built_from = cmake.source_state(str(source))
    _staged(paths)
    cmake.record_build(paths, target, built_from)

    (repo / "klippy" / "extra.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated")

    now = cmake.source_state(str(source))
    status = cmake.artifact_status(paths, target, now)
    assert status.is_current, status.reason
```

Add to the test module's imports:

```python
from mcu_updater.states import BUILT_DIRTY, NEVER_BUILT, NO_PROVENANCE, SOURCE_CHANGED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cmake.py -k "never_built or no_sidecar or somebody_else or dirty_subtree or makes_the_image_stale or leaves_the_image_current" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'artifact_status'`.

- [ ] **Step 3: Implement**

Append to `src/mcu_updater/providers/cmake.py`:

```python
def _is_our_image(record: dict, path: str, stat: os.stat_result) -> bool:
    """Are the bytes on disk the bytes we recorded?

    Two tiers, same as `pio._is_our_image` and for the same reason: this runs
    on the `fw.status` poll path, so size and mtime answer almost every time
    for the cost of a stat, and the content hash only runs when something
    looks changed - which is exactly when the question is worth paying for.
    """
    if record.get("bin_size") == stat.st_size and record.get("bin_mtime") == stat.st_mtime:
        return True
    recorded = record.get("bin_sha256")
    if not recorded:
        return False
    return build_mod.sha256_file(path) == recorded


def artifact_status(
    paths: Paths, target: CmakeType, state: SourceState
) -> ArtifactStatus:
    """Does the staged image match the source subtree?

    Both comparisons are subtree-scoped. The repo-wide `version` is recorded
    rather than compared: it moves on every commit anywhere in the repository,
    and comparing it would rebuild the firmware for a README change.

    Never a guess when provenance cannot be trusted. The cost of a wrong
    `current` is flashing a board with firmware from before the fix you just
    made.
    """
    path = paths.uf2_file(target.name, target.firmware)
    try:
        stat = os.stat(path)
    except OSError:
        return ArtifactStatus(NEVER_BUILT)

    record = read_sidecar(paths, target)
    if record is None:
        return ArtifactStatus(NO_PROVENANCE)
    if not _is_our_image(record, path, stat):
        return ArtifactStatus(NO_PROVENANCE)
    if record.get("dirty"):
        # The tree it came from is not recoverable, so current is unprovable
        # rather than merely unknown.
        return ArtifactStatus(BUILT_DIRTY)

    built, head = record.get("sha"), state.sha
    if not built or not head:
        return ArtifactStatus(NO_PROVENANCE)
    return ArtifactStatus() if built == head else ArtifactStatus(SOURCE_CHANGED)
```

Add to the module's imports:

```python
from ..states import (
    BUILT_DIRTY,
    NEVER_BUILT,
    NO_PROVENANCE,
    SOURCE_CHANGED,
    ArtifactStatus,
)
```

> **Note:** unlike `pio`, these shas are full 40-character `%H` values from
> both sides, so they compare with `==` rather than pio's shorter-of-the-two
> prefix trick. Do not copy that trick here — it exists because pio compares a
> short sha from a firmware version string against `rev-parse --short HEAD`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS (36 tests).

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/cmake.py tests/test_cmake.py
git commit -m "feat(providers): judge a staged cmake image against its subtree"
```

---

### Task 8: the `Cmake` adapter and the registry line

**Files:**
- Modify: `src/mcu_updater/providers/cmake.py` (append the adapter class)
- Modify: `src/mcu_updater/providers/spec.py` — `Install`
- Modify: `src/mcu_updater/providers/registry.py` — `PROVIDERS`
- Modify: `src/mcu_updater/providers/__init__.py` — re-export
- Test: `tests/test_providers.py`, `tests/test_cmake.py`

**Interfaces:**
- Consumes: every module function from Tasks 3–7.
- Produces: `Cmake` implementing `Provider` with `name = "cmake"`,
  `label = "CMake"`; `Install.cmake: dict[str, CmakeType]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_providers.py`, update the static-registry assertion:

```python
    assert [p.name for p in providers.PROVIDERS] == ["kconfig_make", "platformio", "cmake"]
```

Append to `tests/test_cmake.py`:

```python
def test_the_provider_enumerates_its_types(paths, settings, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    targets = Cmake().targets(install)
    assert [(t.provider, t.name, t.fw) for t in targets] == [
        ("cmake", "roadrunner", "roadrunner")
    ]


def test_the_provider_is_never_swept_on_demand(paths, settings, tmp_path):
    """A cmake target is an application, not a bootloader - a sweep builds it.
    Asserted because `on_demand` defaulting the other way would silently drop
    it from every fleet build."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    assert all(not t.on_demand for t in Cmake().targets(install))


def test_a_blocked_type_is_skipped_by_selection_not_failed(paths, settings, tmp_path):
    """tmp_path has no CMakeLists.txt, so the type is blocked. It must appear
    in `skipped` with a reason, never silently vanish - the silent version of
    exactly this is the bug the provider seam was written to fix."""
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    selection = providers.select(install, stale_only=False)
    skipped = {s.target.name: s.reason for s in selection.skipped}
    assert "roadrunner" in skipped
    assert "CMakeLists.txt" in skipped["roadrunner"]


def test_describe_names_the_type(paths, settings, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    install = Install.load(paths, settings)
    assert Cmake().describe(Cmake().targets(install)[0]) == "roadrunner"
```

Add to the test module's imports:

```python
from mcu_updater import providers
from mcu_updater.providers import Cmake, Install
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_providers.py tests/test_cmake.py -v`
Expected: FAIL — `ImportError: cannot import name 'Cmake'`, and the
static-registry assertion fails on the two-element list.

- [ ] **Step 3: Implement**

Append the adapter to `src/mcu_updater/providers/cmake.py`:

```python
class Cmake:
    """Builds one cmake target: one type whose declared family is cmake-built."""

    name = BUILDER
    label = "CMake"

    def targets(self, install: Install) -> list[BuildTarget]:
        return [
            BuildTarget(self.name, name, entry.firmware)
            for name, entry in install.cmake.items()
        ]

    def blocked(self, install: Install, target: BuildTarget) -> str | None:
        entry = install.cmake.get(target.name)
        if entry is None:
            return f"no cmake type '{target.name}' is configured."
        return source_problem(entry)

    def artifact_status(self, install: Install, target: BuildTarget) -> ArtifactStatus:
        entry = install.cmake[target.name]
        return artifact_status(install.paths, entry, source_state(entry.source))

    def build(
        self,
        install: Install,
        target: BuildTarget,
        *,
        reporter: Reporter,
        cancel: threading.Event | None = None,
    ) -> None:
        build(
            install.paths,
            install.settings,
            install.cmake[target.name],
            reporter=reporter,
            cancel=cancel,
        )

    def describe(self, target: BuildTarget) -> str:
        return target.name
```

> **Name shadowing:** the adapter's methods `build` and `artifact_status` have
> the same names as the module functions they call. Inside a method body the
> module-level name still resolves correctly (methods are not in the enclosing
> scope), so this works — but verify it with the tests rather than assuming. If
> it reads badly to the reviewer, rename the module functions to `build_tree`
> and `artifact_status_for` and update Tasks 6–7's call sites.

Add to the module's imports:

```python
from .spec import BuildTarget, Install
```

> This is a circular-import risk: `spec.py` imports `pio` at module level and
> would now need `cmake` too. Import `CmakeType` into `spec.py` under
> `TYPE_CHECKING` and call `cmake.load()` inside `Install.load()`'s body with a
> deferred import, mirroring how `discovery/bootsel.py` defers its `devices`
> import. Confirm `python -c "import mcu_updater.providers"` succeeds before
> committing.

In `src/mcu_updater/providers/spec.py`, add to `Install`:

```python
    #: Types this host builds with cmake.
    cmake: dict[str, CmakeType] = dataclasses.field(default_factory=dict)
```

and in `Install.load()`:

```python
        from . import cmake as cmake_mod

        return cls(
            paths=paths,
            settings=settings,
            registry=Registry.load(paths),
            displays=pio_mod.load(paths),
            cmake=cmake_mod.load(paths),
        )
```

In `src/mcu_updater/providers/registry.py`:

```python
from .cmake import Cmake
...
PROVIDERS: tuple[Provider, ...] = (KconfigMake(), PlatformIO(), Cmake())
```

Append rather than insert — the tuple is a batch order, and reordering it
inside a feature is a behaviour change nobody asked for.

In `src/mcu_updater/providers/__init__.py`, re-export `Cmake` and `CmakeType`
alongside `KconfigMake` and `PlatformIO`, matching the existing style.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -c "import mcu_updater.providers"
python -m pytest tests/test_providers.py tests/test_cmake.py tests/test_agent_bulk.py -v
```
Expected: PASS. `test_every_provider_answers_the_whole_protocol` now covers
`Cmake` for free — if it fails, a protocol method is missing or misspelled.

- [ ] **Step 5: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add src/mcu_updater/providers/ tests/test_providers.py tests/test_cmake.py
git commit -m "feat(providers): register cmake as the third build system"
```

---

### Task 9: documentation

**Files:**
- Modify: `mcu-updater.cfg`, `README.md`, `docs/layout.md`, `docs/agent-api.md`

Per AGENTS.md "Finishing a plan": a shipped feature with stale docs is
half-finished, and this is part of the plan rather than a follow-up.

- [ ] **Step 1: Add the worked example to `mcu-updater.cfg`**

After the `[firmware knomi_serial]` block, in that file's existing voice:

```ini
# CMake tree, one repo building several images at once. `source:` names the
# directory holding CMakeLists.txt, which for Roadrunner is rp2040/ inside the
# repo rather than the repo root.
[firmware roadrunner]
source: ~/roadrunner/rp2040
builder: cmake
# ${git_describe} is the one substitution - the repo's `git describe`, so the
# board's INFO reports a version you can trace back to a commit.
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${git_describe}
```

And after the `[type knomi]` block:

```ini
# RP2040 filament sensor, built by cmake. One `make` produces all six images -
# three transports times two neopixel orderings - and cmake_target: picks the
# one this board runs. Boards whose neopixels differ need a second [type]
# section with its own cmake_target: and serials:.
[type roadrunner]
chipset: rp2040
firmware: roadrunner
cmake_target: roadrunner_v1_i2c_rgb
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
```

- [ ] **Step 2: Update `README.md`**

Find the firmware-family key list and add `builder: cmake` as a third builder
value, plus `cmake_args:` with its `${git_describe}` substitution. Find the
type key list and add `cmake_target:`, noting it is the cmake target name
exactly as `add_executable()` spells it. Add one sentence: a mixed RGB/GRB
fleet takes two `[type]` sections, since `cmake_target:` is per-type.

Also check `README.md`'s `## Features` checkbox list and `## TODO` section —
if either mentions build providers or Roadrunner support, update them.

- [ ] **Step 3: Update `docs/layout.md`**

Add the staged artifact path for a cmake type:
`~/printer_data/mcu-updater/<type>/<fw>.uf2`, with its sidecar at
`<fw>.build.json`, noting the source `build/` directory stays in the source
tree and is not managed by this tool.

- [ ] **Step 4: Update `docs/agent-api.md`**

`cmake_args` joins `builder` in the firmware-family payload emitted at
`src/mcu_updater/agent/methods/status.py:203`. Add it to whatever table or
example documents that payload.

- [ ] **Step 5: Verify the examples parse**

Do not hand-check this. Add to `tests/test_cmake.py`:

```python
def test_the_shipped_example_config_declares_a_loadable_cmake_type(paths):
    """The example in mcu-updater.cfg is documentation people copy. A typo in
    it is a support ticket."""
    import shutil as _shutil

    _shutil.copyfile("mcu-updater.cfg", paths.main_config)
    types = cmake.load(paths)
    assert "roadrunner" in types
    assert types["roadrunner"].cmake_target == "roadrunner_v1_i2c_rgb"
```

> Check `tests/test_repo_hygiene.py` first — if it already has a pattern for
> reading repo files by path, follow that instead of a bare relative path,
> which depends on pytest's working directory.

Run: `python -m pytest tests/test_cmake.py -v`
Expected: PASS.

- [ ] **Step 6: Run the gate and commit**

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
git add mcu-updater.cfg README.md docs/layout.md docs/agent-api.md tests/test_cmake.py
git commit -m "docs: document the cmake builder, cmake_target and cmake_args"
```

---

## After the plan

**Mutation testing.** Per AGENTS.md, run `scripts/mutation_test.py` one spec at
a time — never in parallel, never a full sweep under a short shell timeout. An
interrupted sweep strands a live mutation in the source. Start with
`tests/test_cmake.py`, then `tests/test_config.py`. After any interrupted run,
read `test_no_mutation_is_left_live_in_the_source`'s output; do not settle for
"the command finished".

**What is deliberately not done here.** Everything in the spec's "Deliberately
not in scope": the closed-loop BOOTSEL flash, the `select_for` bootloader axis,
narrowing the compile to one `make` target, and retrofitting subtree scoping
onto `pio.source_state()`. A Roadrunner is flashed by hand after this plan —
hold `BOOT`, press and release `RESET`, release `BOOT`, copy the staged `.uf2`
to the mounted volume.

**Bench validation.** None of this writes to hardware, so no bench board is
needed. The first real check is `mcu-updater build roadrunner` on a printer
with the repo cloned, confirming a staged `.uf2` appears at
`~/printer_data/mcu-updater/roadrunner/roadrunner.uf2` and that a second run
reports it current rather than rebuilding.
