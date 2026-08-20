#!/usr/bin/env python3
"""Bring an old-schema mcu-updater.cfg up to the current one. One-shot.

    ./scripts/migrate_config.py                # print a diff, change nothing
    ./scripts/migrate_config.py --write         # apply it
    ./scripts/migrate_config.py path/to/x.cfg   # migrate a specific file

Defaults to the configured registry file (honouring MCU_UPDATER_HOME etc.,
same as the tool itself) when no path is given.

TRANSFORMS
----------
* ``[mcu x]`` / ``[display x]`` -> ``[type x]`` - the two old spellings this
  tool used before a type's provider was derived from its firmware.
* ``provider: platformio`` -> a ``[firmware <name>]`` section (``builder:
  platformio``, plus ``source:`` from ``[updater] pio_source`` /
  ``display_source`` if either was set), and on the type: ``firmware:``
  naming that family, ``env:`` from the type's own old name (which is what
  it silently meant - the section name *was* the PlatformIO env), and
  ``chipset: esp32`` if nothing already set one. ``provider:`` itself is
  then removed.
* ``firmware:`` becomes a required list on every remaining (non-PlatformIO)
  type: an absent key becomes ``klipper``; ``katapult`` is appended unless
  ``katapult_installed`` was explicitly ``false``; ``katapult_installed``
  itself is then removed, since the list says the same thing now.

Meant to run once, not repeatedly - the header rename and the
provider:-platformio transform are idempotent, but the firmware:-required
step generally is not. It has no honest way to tell "predates firmware:
entirely, apply the old katapult_installed-defaults-true convention" apart
from "already migrated, deliberately klipper-alone, katapult_installed was
removed by this script's own first run" - both look identical: a single
firmware: value and no katapult_installed key. A type that already has
katapult in its list, or still carries an explicit katapult_installed, is
unaffected by a second run; a type migrated to deliberately have no
bootloader is not safe to run this against twice. Comments, ordering and
unrelated keys survive untouched either way
(:mod:`mcu_updater.cfgdoc` is what this is built on).

WHY A SEPARATE SCRIPT, NOT PART OF LOADING
-------------------------------------------
``Registry.load()`` refuses a ``[type ...]`` with no ``firmware:`` key
outright - see docs/rebuild-plan.md Step 11. Silently upgrading the file at
load time would mean two processes racing to rewrite it (the CLI and the
agent both load on every request), and it would hide the exact thing this
key exists to surface: a config that still relies on the old implicit
"klipper, katapult" default has to say so on disk, not just in memory.

WHY ONE SHARED [firmware] SECTION FOR EVERY OLD-STYLE PLATFORMIO TYPE
-----------------------------------------------------------------------
The old model had exactly one ``pio_source`` setting, applied to every
``provider: platformio`` type - "one repo, shared by every env," as the real
printer's own config comments it. Migrating each type onto its own family
would invent a per-type distinction the old config never had. The family is
named after ``pio_source``'s last path component (``~/knomi_serial`` ->
``knomi_serial``), matching the target schema's own worked example. Without
``pio_source`` set, and with more than one such type, there is no honest way
to tell whether they share a tree - this refuses rather than guesses.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcu_updater import firmware, sections  # noqa: E402
from mcu_updater.cfgdoc import CfgDocument, parse_bool  # noqa: E402
from mcu_updater.errors import ConfigError  # noqa: E402
from mcu_updater.paths import Paths  # noqa: E402
from mcu_updater.sections import TypeSection  # noqa: E402
from mcu_updater.settings import SECTION  # noqa: E402

#: The two old section-header spellings a type could carry before `[type ...]`
#: was the only one. See sections.py's own module docstring for the history.
_OLD_PREFIXES = ("mcu", "display")


def _pio_family_name(pio_source: str, legacy_pio: list[TypeSection]) -> str:
    """Which `[firmware <name>]` section the old-style PlatformIO types share."""
    base = pio_source.strip()
    if base:
        parts = [p for p in base.replace("\\", "/").split("/") if p and p != "~"]
        if parts:
            return parts[-1]

    if len(legacy_pio) == 1:
        return legacy_pio[0].name

    names = ", ".join(t.name for t in legacy_pio)
    raise ConfigError(
        f"{len(legacy_pio)} PlatformIO types ({names}) declare 'provider: "
        f"platformio' but [updater] sets no pio_source (or display_source), so "
        f"there is no way to tell whether they share one source tree. Set "
        f"pio_source first, or add a [firmware <name>] section with builder: "
        f"platformio to each type by hand, then re-run.",
    )


def migrate(doc: CfgDocument, *, pio_source: str) -> list[str]:
    """Mutate `doc` in place. Returns a human-readable note per change made."""
    notes: list[str] = []
    dummy_paths = Paths.from_env()

    # 1. [mcu x] / [display x] -> [type x]
    for name in list(doc.section_names()):
        head, _, rest = name.partition(" ")
        if head in _OLD_PREFIXES and rest.strip():
            new = f"type {rest.strip()}"
            if doc.rename_section(name, new):
                notes.append(f"renamed [{name}] to [{new}]")

    # 2. old-style PlatformIO types: provider: platformio
    legacy_pio = [
        t
        for t in sections.read(doc)
        if (doc.get(t.section, "provider") or "").strip().lower() == "platformio"
    ]
    if legacy_pio:
        family_name = _pio_family_name(pio_source, legacy_pio)
        fw_section = f"firmware {family_name}"
        if not doc.has_section(fw_section):
            doc.set(fw_section, "builder", "platformio")
            if pio_source.strip():
                doc.set(fw_section, "source", pio_source.strip())
            notes.append(f"added [{fw_section}] (builder: platformio)")

        for t in legacy_pio:
            if not (doc.get(t.section, "env") or "").strip():
                doc.set(t.section, "env", t.name)
                notes.append(f"[{t.section}] env: {t.name}")
            if not (doc.get(t.section, "firmware") or "").strip():
                doc.set(t.section, "firmware", family_name)
                notes.append(f"[{t.section}] firmware: {family_name}")
            if not (doc.get(t.section, "chipset") or "").strip():
                doc.set(t.section, "chipset", "esp32")
                notes.append(f"[{t.section}] chipset: esp32")
            if doc.remove_option(t.section, "provider"):
                notes.append(f"[{t.section}] removed provider:")

    # 3. firmware: required, as a list - every type not built by platformio.
    # Read fresh: step 2 may just have added the [firmware ...] section a
    # type's builder resolves against.
    families_map = firmware.load_from_doc(doc)
    for t in sections.read(doc):
        raw = (doc.get(t.section, "firmware") or "").strip()
        first_fw = raw.split(",")[0].strip() if raw else ""
        if (
            first_fw
            and firmware.resolve(dummy_paths, first_fw, families_map).builder == "platformio"
        ):
            continue  # already migrated in step 2, or predates provider: entirely

        declared = [f.strip() for f in raw.split(",") if f.strip()]
        before = list(declared)
        if not declared:
            declared = ["klipper"]
        installed = parse_bool(doc.get(t.section, "katapult_installed"), True)
        if installed and "katapult" not in declared:
            declared.append("katapult")
        if declared != before:
            doc.set(t.section, "firmware", ", ".join(declared))
            notes.append(f"[{t.section}] firmware: {', '.join(declared)}")
        if doc.remove_option(t.section, "katapult_installed"):
            notes.append(f"[{t.section}] removed katapult_installed")

    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "path", nargs="?", help="config file to migrate (default: the configured registry file)"
    )
    parser.add_argument("--write", action="store_true", help="apply the changes (default: preview only)")
    args = parser.parse_args(argv)

    path = args.path or Paths.from_env().registry_file
    if not os.path.exists(path):
        print(f"{path}: no such file.", file=sys.stderr)
        return 1

    with open(path, encoding="utf-8") as fh:
        original = fh.read()

    doc = CfgDocument(original)
    # Read straight off the document, not through Settings: `pio_source` and
    # its old `display_source` spelling are themselves retired (see
    # docs/rebuild-plan.md Step 14), so this is the last place either is read
    # at all - purely as input to the migration this script performs.
    raw_pio_source = (
        doc.get(SECTION, "pio_source") or doc.get(SECTION, "display_source") or ""
    ).strip()
    try:
        notes = migrate(doc, pio_source=raw_pio_source)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    updated = doc.render()
    if updated == original:
        print(f"{path}: already up to date, nothing to migrate.")
        return 0

    # The notes list first and the raw diff after, not the other way round:
    # cfgdoc.py's line-based diff can visually place a new key just above the
    # *next* section's header when a comment block sits between them with no
    # blank line first - correct once reparsed (every note below names the
    # section it actually landed in), but confusing to read as a bare diff.
    print(f"{len(notes)} change(s):")
    for note in notes:
        print(f"  - {note}")
    print()

    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=path,
        tofile=f"{path} (migrated)",
    )
    sys.stdout.writelines(diff)

    if not args.write:
        print("\nRe-run with --write to apply.")
        return 0

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(updated)
    os.replace(tmp, path)
    print(f"\nwrote {path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
