"""One walk over every ``[type <name>]`` section, whatever builds it.

``Registry.load``, ``pio.load`` and ``cmake.load`` each walked these sections
and kept only the builder they knew, so a type could exist for the agent and
not for the CLI - a Roadrunner tracked in the UI was invisible to ``status``.
This module is the one walk. Those loaders are views over it until their
callers read the list directly.

Two halves, on purpose:

* :func:`read` never raises. An entry that names no firmware, or a family
  nobody declared, simply has no usable builder, so a question about one name
  is never broken by a malformed section elsewhere. ``providers.selection``
  depends on that.
* :func:`validate` is the strict half, and :func:`load` is both.

Builder-specific keys stay in :attr:`TypeEntry.block`. Each builder parses its
own block, so nothing here needs to know which builder is which.
"""

from __future__ import annotations

import dataclasses
import os

from . import firmware, sections
from .cfgdoc import CfgDocument
from .errors import ConfigCorruptError
from .paths import Paths


@dataclasses.dataclass(frozen=True)
class Block:
    """One ``[type]`` section, read through the document that holds it."""

    doc: CfgDocument
    section: str

    def get(self, key: str) -> str | None:
        return self.doc.get(self.section, key)

    def get_list(self, key: str) -> list[str]:
        return self.doc.get_list(self.section, key)

    def get_csv(self, key: str) -> list[str] | None:
        return self.doc.get_csv(self.section, key)

    def has(self, key: str) -> bool:
        return key in self.doc.options(self.section)


@dataclasses.dataclass(frozen=True)
class TypeEntry:
    """A declared type: its identity, and the block its builder reads."""

    name: str
    section: str
    firmwares: tuple[str, ...]
    chipset: str
    serials: tuple[str, ...]
    canbus_uuids: tuple[str, ...]
    #: ``None`` when the key is absent, which inherits the family's list.
    stop_services: tuple[str, ...] | None
    block: Block
    #: One per declared firmware, in declaration order.
    builders: tuple[str, ...]

    @property
    def builder(self) -> str:
        """The builder of the first declared family, or "" when there is none."""
        return self.builders[0] if self.builders else ""


#: Keys one seam module reads are spelled `<seam module>_<param>` (spec §2,
#: docs/decisions.md). Old spelling -> new spelling.
RENAMED_KEYS: dict[str, str] = {
    "env": "platformio_env",
    "profile": "kconfig_make_profile",
    "device_map": "knomi_serial_device_map",
}

#: Keys that are no longer read at all -> why.
REMOVED_KEYS: dict[str, str] = {
    "klipper_section": (
        "the Klipper object a firmware's klippy module registers is fixed by "
        "that module, not by config"
    ),
}


def refuse_renamed_keys(entry: TypeEntry, *, path: str) -> None:
    """Refuse an old key spelling, naming its replacement.

    Refused rather than read under both names: config migrations do not exist
    yet, and a silently accepted old key would outlive the day they do.
    """
    for old, new in RENAMED_KEYS.items():
        if entry.block.has(old):
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' uses {old}:, which is now spelled {new}:. "
                f"Rename the key by hand - config migrations do not exist yet.",
                path=path,
                type=entry.name,
                value=old,
            )
    for gone, why in REMOVED_KEYS.items():
        if entry.block.has(gone):
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' uses {gone}:, which is no longer read - "
                f"{why}. Delete the line.",
                path=path,
                type=entry.name,
                value=gone,
            )


def _builder_of(fw: str, families: dict[str, firmware.FirmwareFamily]) -> str:
    family = families.get(fw)
    return family.builder if family is not None else ""


def read(doc: CfgDocument, families: dict[str, firmware.FirmwareFamily]) -> list[TypeEntry]:
    """Every ``[type]`` section in `doc`, in file order. Never raises."""
    out: list[TypeEntry] = []
    for declared in sections.read(doc):
        block = Block(doc, declared.section)
        fws = tuple(block.get_csv("firmware") or ())
        stop = block.get_csv("stop_services")
        out.append(
            TypeEntry(
                name=declared.name,
                section=declared.section,
                firmwares=fws,
                chipset=(block.get("chipset") or "").strip(),
                serials=tuple(block.get_list("serials")),
                canbus_uuids=tuple(block.get_list("canbus_uuids")),
                stop_services=None if stop is None else tuple(stop),
                block=block,
                builders=tuple(_builder_of(fw, families) for fw in fws),
            )
        )
    return out


def read_doc(paths: Paths) -> CfgDocument | None:
    """The config document, or None when there is no file.

    Refuses an unreadable file and duplicate sections: only the first copy of a
    section is read, so everything in a later one would be silently ignored.
    """
    path = paths.registry_file
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError as exc:
        raise ConfigCorruptError(f"could not read {path}: {exc}", path=path) from exc
    if doc.duplicate_sections:
        dupes = ", ".join(f"[{name}]" for name in doc.duplicate_sections)
        raise ConfigCorruptError(
            f"{path}: duplicate section(s) {dupes}. Only the first copy is read, so "
            f"everything in the later one is silently ignored - merge them into one.",
            path=path,
            value=doc.duplicate_sections,
        )
    return doc


def read_config(
    paths: Paths,
) -> tuple[list[TypeEntry], dict[str, firmware.FirmwareFamily]]:
    """The type list and the families, leniently: no file is neither."""
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError:
        return [], {}
    families = firmware.load_from_doc(doc)
    return read(doc, families), families


def _refuse_family_keys(families: dict[str, firmware.FirmwareFamily], *, path: str) -> None:
    """Refuse a family with no flashers, or a flasher nothing implements.

    Every family, used by a type or not, and every offender of a kind at once -
    the rule the builder check follows. A family with no flashers cannot be
    written by anything, so both are checked here, on the `typelist.load` path
    every reader shares.

    A misspelt `helper:` is deliberately not checked here. `helpers.for_name`
    already raises for one, at the point a capability is actually asked for -
    and a status read reaching that point for one family must still report
    every other row, not blank the whole panel for a typo on a family it may
    not even use. There is also deliberately no check for a *missing*
    `helper:` at all: a family with none is valid for every builder, including
    `platformio` - `helpers.for_name("")` returns `None`, and every capability
    accessor answers `None` for it, so "this family has no helper" is already
    a representable, handled state.
    """
    known_flashers = ", ".join(firmware.FLASHERS)
    no_flashers = [family for family in families.values() if not family.flashers]
    if no_flashers:
        listed = "\n".join(
            f"  [firmware {family.name}] add: flashers: {firmware.suggested_flashers(family)}"
            for family in no_flashers
        )
        raise ConfigCorruptError(
            f"{path}: a firmware family with no flashers: list. It names the "
            f"flashers that may write the family, in the order they are tried "
            f"(known: {known_flashers}):\n{listed}",
            path=path,
            family=no_flashers[0].name,
            key="flashers",
            families=[family.name for family in no_flashers],
        )
    unknown_flashers = {
        family.name: [name for name in family.flashers if name not in firmware.FLASHERS]
        for family in families.values()
    }
    unknown_flashers = {name: bad for name, bad in unknown_flashers.items() if bad}
    if unknown_flashers:
        first = next(iter(unknown_flashers))
        listed = "\n".join(
            f"  [firmware {name}] flashers: {', '.join(bad)}"
            for name, bad in unknown_flashers.items()
        )
        raise ConfigCorruptError(
            f"{path}: a flasher that does not exist (known: {known_flashers}):\n"
            f"{listed}\nFix the spelling.",
            path=path,
            family=first,
            value=unknown_flashers[first][0],
            flashers=unknown_flashers,
        )


def validate(
    entries: list[TypeEntry],
    families: dict[str, firmware.FirmwareFamily],
    *,
    path: str,
) -> None:
    """Refuse what `read` let through."""
    # A `builder:` no provider implements left its types quietly unbuildable
    # and unflashable - every loop skipped them and nothing named the typo.
    # Every family is checked, used by a type or not, and all are reported at
    # once.
    unknown_builders = {
        name: family.builder
        for name, family in families.items()
        if family.builder not in firmware.BUILDERS
    }
    if unknown_builders:
        first_family, first_builder = next(iter(unknown_builders.items()))
        listed = "\n".join(
            f"  [firmware {name}] builder: {builder}" for name, builder in unknown_builders.items()
        )
        raise ConfigCorruptError(
            f"{path}: a builder no build provider implements (known: "
            f"{', '.join(firmware.BUILDERS)}):\n{listed}\nFix the spelling, or "
            f"remove the builder: line to use {firmware.DEFAULT_BUILDER}.",
            path=path,
            family=first_family,
            value=first_builder,
            builders=unknown_builders,
        )
    _refuse_family_keys(families, path=path)
    known = firmware.names_of(families)
    # Every undeclared family, not just the first: a config with two typos
    # should not take two reloads to fix.
    missing: dict[str, list[str]] = {}
    for entry in entries:
        refuse_renamed_keys(entry, path=path)
        if not entry.firmwares:
            # Refused, not defaulted to klipper. Silence used to mean klipper,
            # which is exactly the implicit behaviour this key exists to remove.
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' declares no firmware: key. Every type "
                f"must name at least one firmware family it runs, e.g. "
                f"'firmware: klipper'.",
                path=path,
                type=entry.name,
            )
        for fw in entry.firmwares:
            if fw not in known:
                # A typo here would otherwise build and flash klipper at a
                # board that runs something else.
                missing.setdefault(entry.name, []).append(fw)
        # An undeclared family has no builder ("" from `read`); that is the
        # typo reported above, not a second tool.
        builders = {builder for builder in entry.builders if builder}
        if len(builders) > 1:
            # One builder per type until the build loop (plan 2) makes that
            # unnecessary.
            raise ConfigCorruptError(
                f"{path}: '{entry.name}' declares firmware families built by "
                f"different tools ({', '.join(sorted(builders))}): "
                f"{', '.join(entry.firmwares)}. A type is built by exactly one "
                f"provider - split it into two types if it genuinely needs "
                f"both.",
                path=path,
                type=entry.name,
                value=list(entry.firmwares),
            )
    if missing:
        # `firmware: klipperr, klipperr` is one typo, not two.
        missing = {name: list(dict.fromkeys(fws)) for name, fws in missing.items()}
        misses = [(name, fw) for name, fws in missing.items() for fw in fws]
        first_type, first_fw = misses[0]
        families_missing = list(dict.fromkeys(fw for _, fw in misses))
        listed = "\n".join(f"  {name} -> {fw}" for name, fw in misses)
        snippets = "\n\n".join(firmware.missing_section_snippet(fw) for fw in families_missing)
        raise ConfigCorruptError(
            f"{path}: firmware that is not a known family (known: "
            f"{', '.join(known) or 'none'}):\n{listed}\n"
            f"Fix the spelling, or declare it.\n\n{snippets}\n\n"
            f"{firmware.MISSING_SECTION_TRAILER}",
            path=path,
            type=first_type,
            value=first_fw,
            missing=missing,
        )


def load(paths: Paths) -> list[TypeEntry]:
    """Every declared type, refusing a config that is not well-formed."""
    doc = read_doc(paths)
    if doc is None:
        return []
    families = firmware.load_from_doc(doc)
    entries = read(doc, families)
    validate(entries, families, path=paths.registry_file)
    return entries
