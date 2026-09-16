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


def _read_missing(paths: Paths) -> tuple[str, list[str]]:
    """The config text and which of `FAMILIES` it does not yet declare."""
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    doc = CfgDocument(text)
    missing = [fw for fw in FAMILIES if not doc.has_section(f"firmware {fw}")]
    return text, missing


def seed_firmware_sections(paths: Paths, sources: dict[str, str]) -> list[str]:
    """Add whichever of klipper and katapult the config does not declare.

    `sources` maps a family to the tree install.sh found. A family missing from
    it, or mapped to "", is written as ``~/<name>``. Returns the families added.

    Checked once outside the lock first, so a no-op re-run - the common case,
    since install.sh may run again on a host that already has both sections -
    never takes the lock at all. The lock is non-blocking with a single 50ms
    retry, so a re-run that happened to overlap a panel write would otherwise
    exit install.sh with 1 for a run that had nothing to do.
    """
    text, missing = _read_missing(paths)
    if not missing:
        return []

    with ExclusiveLock(paths, path=paths.registry_lock_file).acquire("seed firmware sections"):
        # Re-read under the lock: `text`/`missing` above may already be stale
        # by the time it was acquired, the same reason `Registry.mutate` reads
        # inside its own lock rather than trusting a caller's earlier load.
        text, missing = _read_missing(paths)
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
