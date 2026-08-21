"""Things that are true of the repo rather than of the code.

Small, but this is the second "documented and broken" bug of its kind: the README
says to run `./scripts/mutation_test.py` and the file was not executable, so the
command in the docs failed with Permission denied for anyone who copied it.

The Windows dev box cannot notice - `os.access(path, os.X_OK)` is meaningless
there and the working tree carries no mode bits - so the check has to ask git,
which stores the bit either way.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tracked_modes() -> dict[str, str]:
    """Path -> git mode, e.g. "100755". Empty if this isn't a git checkout."""
    git = shutil.which("git")
    if git is None:
        return {}
    try:
        out = subprocess.run(
            [git, "ls-files", "-s"], cwd=REPO_ROOT, capture_output=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}

    modes = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        # "100755 <sha> 0\tpath"
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if path and parts:
            modes[path] = parts[0]
    return modes


def test_every_script_with_a_shebang_is_executable():
    """A shebang is a promise that `./the/script` works. Keep it."""
    modes = _tracked_modes()
    if not modes:
        pytest.skip("not a git checkout, or git unavailable")

    offenders = []
    for path, mode in sorted(modes.items()):
        full = REPO_ROOT / path
        if not full.is_file():
            continue
        try:
            first = full.open("rb").readline()
        except OSError:
            continue
        if first.startswith(b"#!") and mode != "100755":
            offenders.append(f"{path} (mode {mode})")

    assert offenders == [], (
        "these declare a shebang but are not executable, so `./<path>` fails:\n  "
        + "\n  ".join(offenders)
        + "\nFix with: git update-index --chmod=+x <path>"
    )


def test_the_check_can_actually_see_the_repo():
    """Guards the test above: an empty mode map would make it vacuously pass, and
    it is skipped rather than failed when git is missing - so assert that on a
    normal checkout it really did find files."""
    modes = _tracked_modes()
    if not modes:
        pytest.skip("not a git checkout, or git unavailable")
    assert len(modes) > 20
    assert any(path.endswith(".py") for path in modes)


def test_no_working_tree_file_has_crlf_endings():
    """CRLF in the working tree is invisible to everything except the tools that
    matter, so the suite has to be the thing that sees it.

    `.gitattributes` pins `* text=auto eol=lf`, which protects the *repository*:
    Git writes LF on checkout and normalises on commit. It does nothing about a
    file rewritten in place afterwards - and on Windows that is easy to do by
    accident, because `Path.write_text` and `open(..., "w")` translate `\\n` to
    `\\r\\n` unless you pass `newline=""`.

    It happened: a scripted edit rewrote all of `agent/methods.py` that way. The
    commit would still have been LF, so nothing downstream broke - but
    `mutation_test.py` reads files as bytes on purpose, so every multi-line
    anchor reported STALE, which reads as "your guard moved" rather than "your
    file has CRLF". And *this* file could not see it either: the test below
    reads with `read_text()`, which applies universal newlines and makes CRLF
    invisible. Green suite, broken harness.

    The scan lives in `scripts/check_line_endings.py` so the fix is one command
    rather than a manual sweep, and so both callers ask the same question.
    Deliberately not auto-fixed from here: a test run that rewrites source files
    is the same class of surprise as the ad-hoc mutation script that once
    stranded a sabotaged guard on disk. Detecting is the suite's job; changing
    files is something you ask for.
    """
    import importlib.util

    script = REPO_ROOT / "scripts" / "check_line_endings.py"
    spec = importlib.util.spec_from_file_location("check_line_endings", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if shutil.which("git") is None:
        pytest.skip("git unavailable")

    bad = module.offenders(REPO_ROOT)
    assert bad == [], (
        "these have non-LF line endings in the working tree:\n  "
        + "\n  ".join(f"{state:5} {path}" for path, state in bad)
        + "\nFix with: python scripts/check_line_endings.py --fix"
    )


def test_no_mutation_is_left_live_in_the_source():
    """The suite must fail if a sabotaged guard is sitting on disk.

    scripts/mutation_test.py edits files in place and restores in a `finally`,
    verifying by hash. That still loses if the process is *killed* - a shell
    timeout SIGTERMs it, the finally never completes, and the mutation stays.

    It happened: a loop over every spec hit a two-minute timeout, and the
    inline-comment strip in cfgdoc.py was committed and pushed missing. The
    config parser then kept `# comments` as part of the value, so
    `display_source: ~/knomi_serial  # shared` became a directory name with a
    comment in it and every display build failed on a folder that was right
    there.

    The suite is the thing that runs before every commit, so the check belongs
    here rather than in the harness that cannot be trusted to finish.
    """
    import glob
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    live = []
    for spec_path in sorted(glob.glob(str(root / "scripts" / "mutations" / "*.json"))):
        with open(spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        # The command's test paths, for the same reason as the mutation's file
        # path below. A spec pointing at a renamed test module reports "fix the
        # suite first - a red baseline makes every result meaningless", which
        # reads as a broken suite rather than a broken spec; the guards it names
        # are never run and never reported missing. `tests/test_displays.py`
        # became `tests/test_pio.py` and took ten guards down that way.
        for arg in spec.get("command", []):
            if arg.startswith("tests/") and not (root / arg).exists():
                live.append(f"{pathlib.Path(spec_path).name}: command names a missing {arg}")
        for mutation in spec["mutations"]:
            # Per-mutation, falling back to the spec's default: one spec covers a
            # behaviour area, and a behaviour area spans files.
            relative = mutation.get("file", spec.get("file"))
            assert relative, f"{spec_path}: {mutation['name']} names no file"
            target = root / relative
            if not target.exists():
                # A moved or deleted module used to skip silently, which meant a
                # rename quietly orphaned every guard in the spec: the harness
                # still reported them green because it never opened the file.
                # `providers/pio.py` was `displays.py` and took nine guards with
                # it. A path that names nothing is a broken spec, not an absent
                # one.
                live.append(f"{relative}: no such file - {mutation['name']}")
                continue
            source = target.read_text(encoding="utf-8")
            if mutation["find"] in source:
                continue
            name = mutation["name"]
            if mutation["replace"] and mutation["replace"] in source:
                live.append(f"{relative}: MUTATION STILL APPLIED - {name}")
            else:
                # Not sabotage, but the spec no longer describes the code and
                # would report STALE rather than guarding anything.
                live.append(f"{relative}: anchor no longer matches - {name}")

    assert not live, "mutation specs out of sync with the source:\n  " + "\n  ".join(live)


# --------------------------------------------------------------------------
# the packaging files reference each other by path
# --------------------------------------------------------------------------


def test_the_declared_system_dependencies_file_is_where_the_conf_says():
    """moonraker-update-manager.conf names the dependency list by path, and
    Moonraker resolves it relative to the repo. Move or rename the file and
    nothing fails loudly - the update manager simply installs nothing, and the
    first symptom is display discovery failing on a fresh host for want of
    pyserial.
    """
    conf = (REPO_ROOT / "scripts" / "moonraker-update-manager.conf").read_text(
        encoding="utf-8"
    )
    declared = [
        line.split(":", 1)[1].strip()
        for line in conf.splitlines()
        if line.startswith("system_dependencies:")
    ]
    assert len(declared) == 1, "exactly one system_dependencies key"
    assert (REPO_ROOT / declared[0]).is_file(), f"{declared[0]} does not exist"


def test_pyserial_is_declared_because_discovery_shells_out_for_it():
    """The agent is stdlib-only, so this is the one system package it asks for -
    and it is asked for on behalf of a subprocess, which makes it easy to think
    nothing needs it. `discovery.knomi_serial.discover` runs the system python3
    to ask the screens which they are, and that import is the whole reason this
    file exists.
    """
    import json

    conf = (REPO_ROOT / "scripts" / "moonraker-update-manager.conf").read_text(
        encoding="utf-8"
    )
    declared = next(
        line.split(":", 1)[1].strip()
        for line in conf.splitlines()
        if line.startswith("system_dependencies:")
    )
    with open(REPO_ROOT / declared, encoding="utf-8") as fh:
        deps = json.load(fh)

    assert "python3-serial" in deps.get("debian", [])
