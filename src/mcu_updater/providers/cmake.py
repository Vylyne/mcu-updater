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
import shlex
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
