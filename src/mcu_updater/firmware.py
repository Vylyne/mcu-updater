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
from .paths import Paths

SECTION_PREFIX = "firmware"


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
