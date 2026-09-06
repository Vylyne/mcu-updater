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
import json
import os
import shlex
import shutil
import subprocess
import threading
import time

from .. import build as build_mod
from .. import firmware, sections
from ..build import Reporter, null_reporter
from ..cfgdoc import CfgDocument
from ..errors import BuildError, ConfigError
from ..paths import Paths
from ..settings import Settings
from ..states import (
    BUILT_DIRTY,
    NEVER_BUILT,
    NO_PROVENANCE,
    SOURCE_CHANGED,
    ArtifactStatus,
)

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

    The dirty check also excludes `BUILD_SUBDIR`. It lives inside the source
    subtree by design (see `build_dir`), and once a build has run it holds
    generated artifacts git has never seen - without the exclusion, every
    call to this function *after the first build* would report the tree
    dirty forever, on account of output the tree itself produced. What this
    function answers is "what would this tree build", which must not be
    perturbed by "what this tree already built".
    """
    path = os.path.expanduser(source or "")
    if not path or not os.path.isdir(path):
        return SourceState()

    sha = _git(path, "log", "-1", "--format=%H", "--", ".") or None
    if sha is None:
        return SourceState()

    dirty = bool(
        _git(path, "status", "--porcelain", "--", ".", f":(exclude){BUILD_SUBDIR}")
    )
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
    except (OSError, ValueError):
        # ValueError also catches UnicodeDecodeError: a cache we cannot read
        # is no more trustworthy than one that is not there.
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
    args = expand_args(target.cmake_args, state)

    # A cache is only stale in the filesystem sense `needs_configure` checks
    # for - but cmake's `-D` cache entries persist across invocations once
    # set, and are only ever refreshed by passing them again. A tree with
    # `cmake_args:` (almost always carrying `${git_describe}`) must therefore
    # be reconfigured on *every* build, or every board flashed after the
    # first carries the first build's version string forever - the one
    # channel this design uses to correlate a board against its source. A
    # tree with no `cmake_args:` has nothing to refresh, so the brief's
    # "re-running configure changes nothing" skip still applies to it.
    if needs_configure(source) or args:
        argv = ["cmake", "-S", source, "-B", build_path, *args]
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
