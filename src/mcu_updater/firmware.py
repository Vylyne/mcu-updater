"""Firmware families: where a tree lives, and what it builds.

Two facts were hardcoded into :mod:`paths` as conventions:

* the source tree is ``~/<fw>`` - so klipper is ``~/klipper``;
* the build drops ``out/<fw>.bin`` - so klipper produces ``out/klipper.bin``.

Both hold for klipper and katapult, and both break on the first vendor fork.
Cartographer's firmware is a klipper fork living in
``~/MCU-Firmware---Based-on-Klipper``, and because it *is* klipper, its Makefile
still emits ``out/klipper.bin``. A family whose name matches neither its
directory nor its output is not an edge case; it is what every fork looks like.

So both become overridable::

    [firmware cartographer]
    source: ~/MCU-Firmware---Based-on-Klipper
    artifact: klipper

**Every key is optional and the section itself is optional.** With no
``[firmware]`` section anywhere, every family resolves to exactly the
conventions above, which is why this can land without touching a single
existing install. `resolve()` always returns a family rather than None for the
same reason - callers never have to branch on "was it configured".

Deliberately not here: which flasher a family uses. That is chosen by chipset
rather than by firmware - one family can need dfu-util on an STM32 board and
BOOTSEL on an RP2040 one - so a `flasher:` key here would let a user pick a
combination that cannot work.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional

from .cfgdoc import CfgDocument
from .paths import FW_TARGETS, Paths

SECTION_PREFIX = "firmware"

#: Always present, in this order. klipper is what a board runs and katapult is
#: what puts it there; enough of this tool is about that specific pair -
#: `katapult_installed`, the bootloader request, the version join - that neither
#: can be removed by editing a config file. Declaring a family adds to these.
BUILTIN = FW_TARGETS

#: What a board runs unless its `[mcu ...]` section says otherwise. Every type
#: predating the `firmware:` key runs klipper, so this is also what keeps those
#: sections meaning exactly what they meant before it existed.
DEFAULT_APPLICATION = "klipper"

#: The family that puts an application on a board rather than being one. Kept
#: separate from the application because a type needs *both*: `[mcu carto_v4]`
#: runs cartographer and is still flashed through katapult.
BOOTLOADER = "katapult"


@dataclasses.dataclass(frozen=True)
class FirmwareFamily:
    """One firmware target, and where its tree and output live."""

    name: str
    #: Source tree. Empty means the convention: ``~/<name>``.
    source: str = ""
    #: Basename of what the build leaves in ``out/``. Empty means ``<name>``.
    #: A fork keeps its parent's output name - cartographer builds klipper.bin.
    artifact: str = ""

    def source_dir(self, paths: Paths) -> str:
        """The tree to run `make` in."""
        if self.source:
            return os.path.expanduser(self.source)
        return paths.fw_dir(self.name)

    def artifact_name(self) -> str:
        return self.artifact or self.name

    def built_artifact(self, paths: Paths, ext: str = "bin") -> str:
        """Where this family's build leaves its output, before we stage it."""
        return os.path.join(self.source_dir(paths), "out", f"{self.artifact_name()}.{ext}")

    def kconfiglib(self, paths: Paths) -> str:
        return os.path.join(self.source_dir(paths), "lib", "kconfiglib", "kconfiglib.py")

    def to_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "artifact": self.artifact_name(),
        }


def load_from_doc(doc: CfgDocument) -> dict[str, FirmwareFamily]:
    """The `[firmware <name>]` sections of an already-parsed document.

    Split out because the registry parses the same file for its own sections
    and should not open it twice to answer which families exist.
    """
    out: dict[str, FirmwareFamily] = {}
    for section in doc.section_names(SECTION_PREFIX):
        name = section[len(SECTION_PREFIX) :].strip()
        if not name:
            continue
        out[name] = FirmwareFamily(
            name=name,
            source=(doc.get(section, "source") or "").strip(),
            artifact=(doc.get(section, "artifact") or "").strip(),
        )
    return out


def load(paths: Paths) -> dict[str, FirmwareFamily]:
    """Read `[firmware <name>]` sections from the shared config file.

    An unreadable or absent file is not an error: it means no overrides, which
    is the same thing every install has today.
    """
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError:
        return {}
    return load_from_doc(doc)


def names(paths: Paths, families: Optional[dict[str, FirmwareFamily]] = None) -> tuple[str, ...]:
    """Every firmware family this install knows about.

    Built-ins first and in their own order - `klipper` before `katapult`, which
    is the order the CLI has always listed and the artifacts payload has always
    carried - then anything declared in config, sorted so the answer does not
    depend on where in the file somebody added a section.
    """
    if families is None:
        families = load(paths)
    return names_of(families)


def names_of(families: dict[str, FirmwareFamily]) -> tuple[str, ...]:
    """`names()` for a caller that already has the parsed sections."""
    return BUILTIN + tuple(sorted(n for n in families if n not in BUILTIN))


def resolve(
    paths: Paths, fw: str, families: Optional[dict[str, FirmwareFamily]] = None
) -> FirmwareFamily:
    """The family for `fw`, configured or conventional.

    Never returns None. A family with no section behaves exactly as it did
    before this module existed, so every call site can use the result
    unconditionally instead of re-implementing the fallback.

    `families` is accepted so a caller already holding the parsed sections does
    not re-read the file per firmware - the agent answers `fw.status` for every
    type on every poll, and that is two file reads per board otherwise.
    """
    if families is None:
        families = load(paths)
    return families.get(fw) or FirmwareFamily(name=fw)
