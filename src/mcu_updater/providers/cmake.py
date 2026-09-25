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

import contextlib
import dataclasses
import json
import os
import shlex
import shutil
import subprocess
import threading
import time

from .. import build as build_mod
from .. import firmware, typelist, uf2
from ..artifacts import (
    KIND_BIN,
    KIND_UF2,
    Artifact,
    Staged,
    recorded_kinds,
    recorded_sha256,
    sidecar_field,
)
from ..build import Reporter, null_reporter
from ..errors import BuildError, ConfigError
from ..paths import Paths
from ..settings import Settings
from ..states import (
    BUILT_DIRTY,
    CONFIG_CHANGED,
    FOREIGN_BUILD,
    NEVER_BUILT,
    NO_PROVENANCE,
    SOURCE_CHANGED,
    ArtifactStatus,
)
from .spec import BuildTarget, Install

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
    #: Configure-step arguments from the family's `cmake_args:`, as written.
    #: Not verbatim: `expand_args` splits them shell-style - quoting groups
    #: words and is consumed - and substitutes `${git_describe}`. Held here
    #: unexpanded, because the value that reaches cmake depends on what the
    #: tree describes as at the moment of the build.
    cmake_args: str = ""
    #: Projected from the family's `submodules:`. Held on the type for the
    #: same reason `cmake_args` is: `build()` is handed a target, not an
    #: `Install`, and looking a family back up from inside it would be a
    #: second config parse to answer something load() already knew.
    submodules: bool = False
    #: Device identity remains owned by the shared ``[type ...]`` document,
    #: even though CMake owns this type's build semantics.
    chipset: str = ""
    serials: list[str] = dataclasses.field(default_factory=list)
    #: Optional type-level service override; ``None`` inherits the family.
    stop_services: list[str] | None = None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "cmake_target": self.cmake_target,
            "source": self.source,
            "firmware": self.firmware,
            "cmake_args": self.cmake_args,
            "submodules": self.submodules,
            "chipset": self.chipset,
            "serials": list(self.serials),
            "stop_services": self.stop_services,
        }


def load(paths: Paths) -> dict[str, CmakeType]:
    """The cmake types: the one type list (:mod:`..typelist`), filtered by builder."""
    entries, families_map = typelist.read_config(paths)

    out: dict[str, CmakeType] = {}
    for entry in entries:
        if entry.builder != BUILDER:
            continue
        name, block = entry.name, entry.block
        typelist.refuse_renamed_keys(entry, path=paths.main_config)
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


def clean_build_dir(source: str) -> str | None:
    """Remove this tree's `build/`. Returns the path, or None if it was absent.

    The recovery for a build directory that outlived its toolchain.
    `CMakeCache.txt` pins the absolute path of every tool the configure step
    found, so a tree configured against a `picotool` that has since moved or
    been upgraded keeps failing with an error about the old one - through any
    number of rebuilds, because `needs_configure()` sees a cache naming the
    right source tree and correctly says the build system is generated. It is;
    it is just generated against a world that no longer exists.

    Deliberately narrow: this removes exactly `build/`, never the source tree
    around it. It is also the only destructive operation a provider offers, so
    it refuses anything that is not a directory rather than unlinking whatever
    happens to sit at that path.

    Nothing else is touched. The staged `.uf2` lives under `printer_data`, and
    the provenance sidecar beside it, so a clean costs a recompile and never an
    artifact or the record of where it came from.
    """
    path = build_dir(source)
    if not os.path.exists(path):
        return None
    if not os.path.isdir(path):
        raise BuildError(
            f"{path} is not a directory - refusing to remove it. Something "
            f"other than a cmake build directory is at that path."
        )
    shutil.rmtree(path)
    return path


def staged_uf2(source: str, cmake_target: str) -> str:
    """Where cmake leaves this target's image, before we stage it.

    Not routed through `firmware.built_artifact`, which hardcodes
    `out/<artifact>.<ext>` - a Klipper-Makefile convention this build system
    does not share.
    """
    return os.path.join(build_dir(source), f"{cmake_target}.uf2")


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


def source_problem(target: CmakeType, *, probe_targets: bool = True) -> str | None:
    """Why this type cannot be built, or None if it can be attempted.

    A module function, mirroring `pio.source_problem`, so a status payload can
    ask without assembling an `Install` it does not need.

    Only setup that has to happen *outside this tool*. A missing ARM
    toolchain, a syntax error or a full disk are all things to find out by
    trying: reporting them as failures is more useful than pretending we knew.

    `probe_targets=False` drops the last check, which is the only one that
    runs a subprocess. `declared_targets()` shells out to `cmake` with a 30
    second timeout, which is fine once before a build and unacceptable on the
    `fw.status` poll path - a panel refreshing every few seconds would hang
    for half a minute per cmake type on a host where cmake is missing or the
    build directory is wedged. The status row wants the cheap answers (no
    tree, no CMakeLists, an empty submodule); the build path wants all of
    them, and `build()` re-asks authoritatively after `make` regardless.
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
    # Not a problem for a tree that asked us to sync it: `build()` runs the
    # very command this message tells the user to run, before it configures.
    # Refusing here anyway would make `submodules: yes` unreachable in the one
    # case it exists for - the first build after a fresh clone.
    if not target.submodules:
        empty = _uninitialised_submodule(source)
        if empty is not None:
            return (
                f"submodule '{empty}' in {source} is empty - run "
                f"'git submodule update --init --recursive' in that tree first."
            )

    if not probe_targets:
        return None

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

    uf2_sha256 = build_mod.sha256_file(path)
    hashes: dict[str, str | None] = {KIND_UF2: uf2_sha256}
    bin_path = paths.bin_file(target.name, target.firmware)
    if os.path.exists(bin_path):
        # Only ever this build's: `build()` removes a staged `.bin` its link
        # did not produce before it gets here.
        hashes[KIND_BIN] = build_mod.sha256_file(bin_path)
    record = {
        # Which provider wrote this. The sidecar path is shared with
        # `kconfig_make`, whose record is a different schema in the same place
        # (`build.py`), and reading one as the other is a wrong answer about
        # what is on a board. Today the two schemas happen to disagree on a key
        # name - `sha` here, `fw_sha` there - and `bin_sha256` is already
        # common to both, so the safety is one rename away from evaporating.
        # `read_sidecar` requires this key, which makes it asserted rather
        # than lucky.
        "provider": BUILDER,
        "sha": state.sha,
        "version": state.version,
        "dirty": state.dirty,
        "cmake_target": target.cmake_target,
        "at": time.time(),
        "bin_sha256": uf2_sha256,
        "bin_size": stat.st_size,
        "bin_mtime": stat.st_mtime,
        # One hash per staged kind. `bin_sha256` above stays the .uf2's hash,
        # which is what every reader older than this field expects.
        "artifacts": sidecar_field(hashes),
        # What a board running this image should report back over INFO.
        # Absent for anything that would not parse as a UF2, and absent is
        # never mismatch - the comparison falls through to the version string,
        # the same as it does for a board too old to report a digest.
        **uf2.digest_fields(path),
    }
    sidecar = paths.sidecar_file(target.name, target.firmware)
    os.makedirs(os.path.dirname(sidecar), exist_ok=True)
    tmp = sidecar + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    os.replace(tmp, sidecar)


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

    # Before `source_state`, deliberately. A submodule at a commit other than
    # the one recorded shows up in the parent's `git status` as a modified
    # gitlink, so syncing after the sample would record provenance for a tree
    # that no longer exists - and syncing after the *build* would describe
    # bytes compiled from the old submodule with the new one's state.
    if target.submodules:
        reporter("info", "Syncing submodules...")
        rc = build_mod.run_streamed(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=source,
            reporter=reporter,
            cancel=cancel,
            dry_run=settings.dry_run,
        )
        if rc != 0:
            raise BuildError(
                f"git submodule update failed for '{target.name}': git exited "
                f"{rc}. The tree is left as it was; nothing was built.",
                type=target.name,
                fw=target.firmware,
                returncode=rc,
            )

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

    # Asked after `make`, not after the configure step above, because that
    # step is conditional: a tree with no `cmake_args:` that is already
    # configured never re-runs it, so a pre-make answer could be read off the
    # build system some *earlier* configure generated. `make` re-runs cmake
    # itself when CMakeLists.txt has moved (cmake_check_build_system), so by
    # here the declared target list describes this tree as it is now.
    #
    # This is the only check that the bytes about to be staged are bytes this
    # run could have produced. `make` builds `all` and succeeds when an
    # upstream rename drops the configured target; the previous build's `.uf2`
    # survives on disk - cmake does not remove outputs of removed targets - and
    # staging it would stamp an older commit's image with today's sha, which
    # `artifact_status()` would then call current. `blocked()` asks the same
    # question earlier for a readable refusal, but it asks a possibly-stale
    # build system and cannot be the thing that guarantees this.
    #
    # None means "could not be asked" - an unconfigured directory, no cmake on
    # PATH - not "declares nothing"; there the `os.path.exists` check below is
    # all there is, exactly as before.
    known = declared_targets(source)
    if known is not None and target.cmake_target not in known:
        raise BuildError(
            f"make succeeded, but this tree declares no target named "
            f"'{target.cmake_target}' - refusing to stage an image it did not "
            f"produce. Known: {', '.join(sorted(known))}.",
            type=target.name,
            fw=target.firmware,
        )

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

    # After the copy, so the record describes the bytes that now exist - and
    # the two halves of that record are deliberately sampled at different
    # times, so do not "fix" one to match the other:
    #
    # `version` stays the value read *before* the build. It is the string that
    # was substituted into `cmake_args:` and compiled into this binary through
    # `-D`, so it is what the board will report back; re-reading it here would
    # record a string the firmware does not carry and would make every flash
    # record disagree with the image.
    #
    # `sha` and `dirty` are re-read *after* `make`, because they answer "what
    # was compiled", not "what did we intend to compile" - the same reason
    # `pio.build()` reads its whole state after its build. A tree that moved
    # mid-compile (an editor save, a `git pull`, a checkout - all things people
    # do while a build they think is over is still running) produced an image
    # that is part one revision and part another, and no commit describes it.
    # `dirty` is therefore forced true when the subtree was unclean at *either*
    # end as well as when the commit moved between them. All three legs are
    # needed: a post-build sha on its own would be a *clean* record of a commit
    # that did not produce these bytes, and dropping the pre-build leg would do
    # the same for an edit that was compiled in and then discarded (a stash, a
    # checkout, an editor undo) - the tree ends clean at the sha it started on,
    # and nothing left behind says the image is not that commit. Both are
    # exactly the state `artifact_status()` reports as current and never
    # rebuilds.
    after = source_state(source)
    built = dataclasses.replace(
        after,
        version=state.version,
        dirty=state.dirty or after.dirty or after.sha != state.sha,
    )
    record_build(paths, target, built)
    return staged


def sidecar_describes_image(record: dict, path: str, stat: os.stat_result) -> bool:
    """Are the bytes on disk the bytes we recorded?

    Two tiers, same as `pio._is_our_image` and for the same reason: this runs
    on the `fw.status` poll path, so size and mtime answer almost every time
    for the cost of a stat, and the content hash only runs when something
    looks changed - which is exactly when the question is worth paying for.

    This is public because a flasher filing the image it just wrote must enforce
    the same ownership boundary without calling `artifact_status`, whose source
    comparison would add an unrelated git read after the hardware write.
    """
    if record.get("bin_size") == stat.st_size and record.get("bin_mtime") == stat.st_mtime:
        return True
    recorded = record.get("bin_sha256")
    if not recorded:
        return False
    return build_mod.sha256_file(path) == recorded


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
    if not sidecar_describes_image(record, path, stat):
        # Positive evidence needs a hash for the bytes on disk to contradict.
        # With one, a mismatch says somebody rebuilt behind us and is named as
        # such; without one - a record written before `bin_sha256` existed -
        # drifted size and mtime can only say we no longer know. Both land on
        # `ARTIFACT_UNPROVABLE`, so this is the label, never the verdict.
        if record.get("bin_sha256"):
            return ArtifactStatus(FOREIGN_BUILD)
        return ArtifactStatus(NO_PROVENANCE)
    # The bin is a second image from the same link, and flashtool writes it
    # without looking at the uf2 - so a bin replaced behind the record is a
    # foreign build even while the uf2 still matches.
    bin_path = paths.bin_file(target.name, target.firmware)
    recorded_bin = recorded_sha256(record, KIND_BIN, primary=KIND_UF2)
    if recorded_bin and os.path.exists(bin_path) and build_mod.sha256_file(bin_path) != recorded_bin:
        return ArtifactStatus(FOREIGN_BUILD)
    if record.get("dirty"):
        # The tree it came from is not recoverable, so current is unprovable
        # rather than merely unknown.
        return ArtifactStatus(BUILT_DIRTY)
    if record.get("cmake_target") != target.cmake_target:
        return ArtifactStatus(CONFIG_CHANGED)

    built, head = record.get("sha"), state.sha
    if not built or not head:
        return ArtifactStatus(NO_PROVENANCE)
    return ArtifactStatus() if built == head else ArtifactStatus(SOURCE_CHANGED)


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

    def clean(self, install: Install, target: BuildTarget) -> str | None:
        entry = install.cmake.get(target.name)
        if entry is None:
            raise BuildError(f"no cmake type '{target.name}' is configured.", type=target.name)
        return clean_build_dir(entry.source)

    def staged(self, paths: Paths, type_name: str, family: firmware.FirmwareFamily) -> Staged:
        return staged(paths, type_name, family)
