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

**Every family is declared**, klipper and katapult included. `resolve()`
refuses a name with no section, with the lines to add, rather than inventing
the ``~/<name>`` convention for it - inventing is how a misspelt family used to
build from a directory nobody meant. Within a section every key is optional: a
section with no ``source:`` still means ``~/<name>``. install.sh writes the
klipper and katapult sections with the paths it finds.

`flashers:` is required on every section, and `typelist.validate` refuses a
missing or misspelt one with the line to fix. `helper:` is optional and, when
present, must name a registered helper - a family whose hardware carries its
own identity or needs firmware-specific access names one, and everything else
does not. An unknown name raises from `helpers.for_name` where a capability is
asked for, so one typo costs that family rather than the whole config.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from .cfgdoc import CfgDocument, parse_bool
from .errors import ConfigCorruptError
from .paths import Paths

SECTION_PREFIX = "firmware"

#: What builds a family unless its `[firmware ...]` section says otherwise.
#: Klipper, Katapult and every fork of either use Kconfig + `make`; PlatformIO
#: and CMake families always name themselves explicitly.
DEFAULT_BUILDER = "kconfig_make"

#: Every `builder:` value a build provider implements - `providers.registry`'s
#: names, spelled out here because that package imports this module. A test
#: holds the two equal, so a new provider cannot be added without this line.
BUILDERS: tuple[str, ...] = ("cmake", "kconfig_make", "platformio")

#: Every name a `flashers:` list may use - `flashers.registry`'s names, spelled
#: out here because that package imports hardware code this module must not. A
#: test holds the two equal.
FLASHERS: tuple[str, ...] = ("bootsel", "dfu_util", "esptool", "flashtool")

#: Every `helper:` value a registered helper answers to - `helpers.registry`'s
#: names, for the same reason. A test holds the two equal.
HELPERS: tuple[str, ...] = ("cartographer", "knomi_serial", "roadrunner")

#: The `flashers:` line a refusal suggests, by what builds the family. A
#: suggestion for a message only: selection reads the family's own list.
_SUGGESTED_FLASHERS: dict[str, str] = {
    "cmake": "bootsel",
    "kconfig_make": "flashtool",
    "platformio": "esptool",
}

#: Keys install.sh writes into the two sections it seeds, beside `source:`.
#: `flashers:` is required on every section. Kept here so the lines a refusal
#: tells a user to add match what install.sh writes.
SEEDED_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "klipper": (("flashers", "flashtool"),),
    "katapult": (("flashers", "dfu_util, bootsel"),),
}


def expand_home(path: str, home: str) -> str:
    """Expand a leading ``~`` against `home`, leaving everything else alone.

    Only the bare ``~`` form, which is the one that means "this printer's home
    directory". ``~someone`` is left to ``expanduser``, because that names a
    specific account and is not ours to reinterpret.
    """
    text = path.strip()
    if text == "~":
        return home
    if text.startswith("~/") or text.startswith("~\\"):
        return os.path.join(home, text[2:])
    return os.path.expanduser(text)


@dataclasses.dataclass(frozen=True)
class FirmwareFamily:
    """One firmware target, and where its tree and output live."""

    name: str
    #: Source tree. Empty means the convention: ``~/<name>``.
    source: str = ""
    #: Basename of what the build leaves in ``out/``. Empty means ``<name>``.
    #: A fork keeps its parent's output name - cartographer builds klipper.bin.
    artifact: str = ""
    #: What builds this tree. ``kconfig_make`` for Klipper, Katapult and every
    #: fork of either; ``platformio`` is the other one today.
    builder: str = DEFAULT_BUILDER
    #: Extra arguments for this tree's configure step, as one shell-quoted
    #: string. Only the cmake provider reads it - a cache variable is a fact
    #: about one tree and not about cmake, so it belongs in config rather
    #: than hardcoded in a general-purpose provider. `${git_describe}` is the
    #: one substitution; see `providers/cmake.py`.
    cmake_args: str = ""
    #: Firmware-specific helper capability. Empty means this family has none.
    helper: str = ""
    #: The flashers that may write this family, in the order they are tried.
    #: Required: `typelist.validate` refuses a section without one. The first
    #: whose `supports()` accepts a device writes it.
    flashers: tuple[str, ...] = ()
    #: Sync this tree's git submodules before building it. Opt-in, and off
    #: everywhere it is not written, because it is not free: `git submodule
    #: update --init --recursive` resets an *already* initialized submodule
    #: to the recorded commit, discarding a checkout somebody made on purpose
    #: while hacking on a vendored dependency. Only the cmake provider reads
    #: it - a tree that vendors its SDK that way is a fact about that tree.
    submodules: bool = False
    #: A bootloader, not an application - Katapult, not Klipper or a fork of
    #: it. Determines whether a sweep builds this family only when named
    #: (`providers.spec.on_demand`) and, with the application, whether the two
    #: are the pair the flash-time offset checks compare.
    bootloader: bool = False
    #: Units to stop before a write of this family, overriding `[updater]`
    #: and overridden by a `[type ...]` that names its own.
    #: `None` means this family said nothing - inherit the next level out.
    #: See `stop_services.py`.
    stop_services: list[str] | None = None

    def source_dir(self, paths: Paths) -> str:
        """The tree to run `make` in.

        ``~`` is expanded against ``paths.home`` rather than by
        ``os.path.expanduser``. They agree on a normal install and disagree
        everywhere it matters: ``expanduser`` reads the process environment, so
        a configured ``source: ~/klipper-fork`` silently escaped the one seam
        the whole project is testable through - ``MCU_UPDATER_HOME`` - and
        resolved against the real home instead. Every other path here already
        went through Paths; this was the one that did not.
        """
        if self.source:
            return expand_home(self.source, paths.home)
        return os.path.join(paths.home, self.name)

    def artifact_name(self) -> str:
        return self.artifact or self.name

    def built_artifact(self, paths: Paths, ext: str = "bin") -> str:
        """Where this family's build leaves its output, before we stage it."""
        return os.path.join(self.source_dir(paths), "out", f"{self.artifact_name()}.{ext}")

    def kconfiglib(self, paths: Paths) -> str:
        return os.path.join(self.source_dir(paths), "lib", "kconfiglib", "kconfiglib.py")

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "artifact": self.artifact_name(),
            "builder": self.builder,
            "cmake_args": self.cmake_args,
            "bootloader": self.bootloader,
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
            builder=(doc.get(section, "builder") or "").strip() or DEFAULT_BUILDER,
            cmake_args=(doc.get(section, "cmake_args") or "").strip(),
            helper=(doc.get(section, "helper") or "").strip(),
            flashers=tuple(doc.get_csv(section, "flashers") or ()),
            submodules=bool(parse_bool(doc.get(section, "submodules"), False)),
            # Absent means "whatever this name defaults to" - True only for
            # katapult - not a blanket False, so overriding one key on an
            # existing [firmware katapult] section can't silently turn its
            # bootloader status off.
            bootloader=bool(parse_bool(doc.get(section, "bootloader"), name == "katapult")),
            stop_services=doc.get_csv(section, "stop_services"),
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


def names(paths: Paths, families: dict[str, FirmwareFamily] | None = None) -> tuple[str, ...]:
    """Every declared family, sorted so the answer does not depend on where in
    the file somebody added a section. Nothing is built in: a config that
    declares nothing knows no families."""
    if families is None:
        families = load(paths)
    return names_of(families)


def names_of(families: dict[str, FirmwareFamily]) -> tuple[str, ...]:
    """`names()` for a caller that already has the parsed sections."""
    return tuple(sorted(families))


def suggested_flashers(family: FirmwareFamily) -> str:
    """The `flashers:` value to suggest for a family that has none.

    What install.sh seeds for the two sections it writes, otherwise what a
    family of this kind is normally written with.
    """
    seeded = dict(SEEDED_KEYS.get(family.name, ())).get("flashers")
    if seeded:
        return seeded
    if family.bootloader:
        return "dfu_util, bootsel"
    return _SUGGESTED_FLASHERS.get(family.builder, "flashtool")


#: Where to look next, whichever families are missing - said once per message.
MISSING_SECTION_TRAILER = (
    "Re-running install.sh writes the klipper and katapult sections for you.\n"
    "Every section is shown, commented, in mcu-updater.cfg and README.md in the "
    "mcu-updater checkout."
)


def missing_section_snippet(fw: str) -> str:
    """The section one undeclared family needs, without the shared trailer."""
    lines = [f"[firmware {fw}]", f"source: ~/{fw}"]
    lines += [f"{key}: {value}" for key, value in SEEDED_KEYS.get(fw, ())]
    body = "\n".join(f"    {line}" for line in lines)
    return f"No [firmware {fw}] section is declared. Add one to mcu-updater.cfg:\n{body}"


def missing_section_message(fw: str) -> str:
    """How to fix an undeclared family: the lines to paste, then where to look."""
    return f"{missing_section_snippet(fw)}\n{MISSING_SECTION_TRAILER}"


def resolve(
    paths: Paths, fw: str, families: dict[str, FirmwareFamily] | None = None
) -> FirmwareFamily:
    """The declared family for `fw`. Refuses a name with no ``[firmware <fw>]``
    section.

    `families` is accepted so a caller already holding the parsed sections does
    not re-read the file per firmware - the agent answers `fw.status` for every
    type on every poll, and that is two file reads per board otherwise.
    """
    if families is None:
        families = load(paths)
    family = families.get(fw)
    if family is None:
        raise ConfigCorruptError(missing_section_message(fw), path=paths.main_config, value=fw)
    return family
