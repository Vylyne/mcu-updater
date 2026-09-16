"""The MCU registry: ``~/printer_data/config/mcu-updater/mcu-updater.cfg``.

Klipper-style, because it lives next to ``printer.cfg`` and gets hand-edited::

    # Toolhead boards. The buffer patch is specific to this batch.
    [mcu flylllplusbuffer]
    chipset: stm32f072xb
    serials:
        4C0033000957465331323720
        3F0037000957465331323720
    klipper_makefile_patches:
        src/Makefile -> src-y += buffer.c

Per-type keys, and that is all:

``chipset``
    Required. Drives flasher and build selection, not presence - see
    docs/decisions.md "Presence comes from the inventory".
``serials``
    One tracked board per line.
``canbus_uuids``
    One tracked CAN-addressed board's uuid per line - parallel to ``serials``
    but a separate key, since a CAN uuid has no chipset/interface segment the
    way a by-id serial does. No interface is stored: Linux CAN interface
    names (``can0``, ``can1``, ...) are enumeration order, not stable
    identity, so the flasher re-discovers one at write time instead.
``firmware``
    Required. Which families this board runs, comma- or space-separated - an
    application and, for a board with one, its bootloader, e.g.
    ``cartographer, katapult``. A type with no bootloader simply omits one.
``kconfig_make_profile``
    The vendor answer file this type's application config is seeded from, e.g.
    ``config.CartoV4USB``. Names a file in that firmware's own source tree, not
    one shipped here - see :mod:`mcu_updater.profiles`.
``<fw>_extra_args``
    Appended to the make command line.
``<fw>_makefile_patches``
    ``<file> -> <line>`` per line. Appended to that Makefile for the duration of
    one build, then reverted. This exists because Klipper's build system has no
    way to add ``src-y +=`` lines from the command line, and a permanent edit
    would leak into every other type sharing the chipset.

Writes go through :mod:`cfgdoc`, so comments, ordering and unrecognised keys
survive the panel editing the file.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
from collections.abc import Iterable, Iterator
from typing import Any

from . import firmware, sections, typelist
from .cfgdoc import CfgDocument
from .errors import (
    AmbiguousSerialError,
    AmbiguousUuidError,
    ConfigCorruptError,
    DuplicateTypeError,
    InvalidTypeNameError,
    SerialTrackedElsewhereError,
    UnknownSerialError,
    UnknownTypeError,
    UnknownUuidError,
    UuidTrackedElsewhereError,
)
from .paths import Paths

#: The only spelling a type section is written or read in. Matches
#: `sections.PREFIX` - kept as its own constant so this module does not import
#: `sections` just to name it in a docstring or a test.
SECTION_PREFIX = "type"
PATCH_SEPARATOR = "->"

@dataclasses.dataclass
class MakefilePatch:
    #: Relative to the firmware source tree, e.g. "src/stm32/Makefile".
    file: str
    line: str

    @classmethod
    def parse(cls, raw: str) -> MakefilePatch | None:
        if PATCH_SEPARATOR not in raw:
            return None
        target, _, line = raw.partition(PATCH_SEPARATOR)
        patch = cls(file=target.strip(), line=line.strip())
        return patch if patch.is_valid() else None

    def render(self) -> str:
        return f"{self.file} {PATCH_SEPARATOR} {self.line}"

    def to_json(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line}

    def is_valid(self) -> bool:
        return bool(self.file and self.line)


@dataclasses.dataclass
class FwConfig:
    extra_args: str = ""
    makefile_patches: list[MakefilePatch] = dataclasses.field(default_factory=list)
    #: Secondary source trees whose git SHA is tracked alongside the main
    #: source tree, e.g. a buffer_manager-style extra file pulled in by a
    #: makefile patch above. A commit in any of these is reported the same
    #: as a change in the main source - see build.artifact_status().
    extra_repos: list[str] = dataclasses.field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"extra_args": self.extra_args}
        if self.makefile_patches:
            out["makefile_patches"] = [p.to_json() for p in self.makefile_patches]
        if self.extra_repos:
            out["extra_repos"] = list(self.extra_repos)
        return out


def _is_bootloader(fw: str, families: dict[str, Any] | None) -> bool:
    """Whether a declared family is a bootloader.

    Mirrors `firmware.resolve()`'s own fallback for a family this dict has no
    section for - "katapult" is one by convention, nothing else is - without
    calling through it, so `McuType`'s own methods stay Paths-free like
    `fw_order()` already is: an McuType is handed around without a Paths.
    """
    if families and fw in families:
        return bool(families[fw].bootloader)
    return fw == "katapult"


@dataclasses.dataclass
class McuType:
    name: str
    chipset: str = ""
    serials: list[str] = dataclasses.field(default_factory=list)
    #: CAN-addressed boards tracked under this type, by uuid. Parallel to
    #: `serials` but a separate list - see the module docstring's
    #: `canbus_uuids` entry for why. No interface is stored here either.
    canbus_uuids: list[str] = dataclasses.field(default_factory=list)
    fws: dict[str, FwConfig] = dataclasses.field(default_factory=dict)
    #: Every family this board runs - an application and, for a board with
    #: one, its bootloader. Replaces the old single `firmware` string and the
    #: `katapult_installed` flag together: a type with no bootloader simply
    #: omits one from this list, rather than carrying a flag that says so.
    #:
    #: Defaults to klipper alone, which is what every type meant before this
    #: key existed.
    firmwares: list[str] = dataclasses.field(default_factory=lambda: ["klipper"])
    #: Vendor answer file this type's application config is seeded from, in
    #: that firmware's own tree. Empty means the answers are the user's own,
    #: which is what every type predating profiles is.
    #:
    #: Only a *record of intent*: whether the saved config still matches it is
    #: a question about files on disk, answered by `profiles.status()`. Keeping
    #: the intent in the hand-edited config and the verdict in the data tree is
    #: what lets a user declare a profile for a board they have not wired up.
    profile: str = ""
    #: Units to stop before a write to this type, overriding `[firmware ...]`
    #: and `[updater]`. `None` means this type said nothing - inherit the
    #: next level out. See `stop_services.py`.
    stop_services: list[str] | None = None

    def fw(self, fw: str) -> FwConfig:
        return self.fws.setdefault(fw, FwConfig())

    def fw_get(self, fw: str) -> FwConfig:
        """Read a firmware family's config without creating a slot.

        Distinct from `fw()`, which is `setdefault` and therefore *creates* a
        slot as a side effect of merely being asked for one. That was a real
        bug: a read-only loop over every globally declared family left every
        type carrying phantom slots for families it never declared, and the
        panel then reported a perfectly good board's firmware as never built.
        Use this wherever a family's config is
        only being read, never assigned into.
        """
        return self.fws.get(fw, FwConfig())

    def families(self) -> list[str]:
        """The families this type actually uses - exactly what it declares.

        Distinct from `fw_order()`, which is everything it *carries*. A board
        running cartographer has klipper config keys too - they are harmless and
        unused - and listing them as "not built" is noise about a firmware
        nobody intends to build for it.
        """
        return list(self.firmwares)

    def application(self, families: dict[str, Any] | None = None) -> str:
        """The family this board actually *runs*: the first declared family
        that is not a bootloader.

        A type carries several - klipper, katapult, and any declared family -
        but only one of them is the application, and that is the one whose
        source tree the board's reported version is compared against and
        whose binary a flash writes.

        `families` is the parsed `[firmware ...]` sections, for a caller that
        already has them - without it, an undeclared family's bootloader
        status falls back to the same convention `firmware.resolve` uses.
        """
        for fw in self.firmwares:
            if not _is_bootloader(fw, families):
                return fw
        return self.firmwares[0] if self.firmwares else "klipper"

    def bootloader(self, families: dict[str, Any] | None = None) -> str | None:
        """The bootloader family this type carries, if any.

        None means this board has no bootloader (`katapult_installed: false`,
        in the old spelling) - flashed some other way, or not flashed by this
        tool at all yet.
        """
        for fw in self.firmwares:
            if _is_bootloader(fw, families):
                return fw
        return None

    def fw_order(self) -> list[str]:
        """The families this type carries, in declaration order.

        Families it holds per-family keys for but no longer declares follow, so
        nothing a caller iterates is dropped.
        """
        declared = [fw for fw in self.firmwares if fw in self.fws]
        return declared + [fw for fw in self.fws if fw not in self.firmwares]

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chipset": self.chipset,
            "firmwares": list(self.firmwares),
            "profile": self.profile,
        }
        for fw in self.fw_order():
            cfg = self.fws.get(fw)
            if cfg is not None:
                out[fw] = cfg.to_json()
        out["serials"] = list(self.serials)
        out["canbus_uuids"] = list(self.canbus_uuids)
        return out


def section_name(mcu_type: str) -> str:
    return f"{SECTION_PREFIX} {mcu_type}"


#: A whitelist, not a blacklist. Every real type name is already alphanumeric
#: (sv08Mainboard, bttebb36, flylllplusbuffer, OctopusMAXEZ, hexa), so nothing is
#: given up - and a whitelist cannot be outflanked by a separator or an encoding
#: nobody thought of.
TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Long enough for any sane board name, short enough to stay a valid path
#: component on every filesystem.
TYPE_NAME_MAX = 64


def validate_type_name(name: str) -> str:
    """Check a type name is safe to use as a section header *and* a directory.

    A type name is not just a label. It becomes ``[mcu <name>]`` in the config and
    a directory under both the config and data trees, via
    ``os.path.join(config_dir, name)``. So ``../../foo`` would write outside them
    and ``a]b`` would produce a section header that no longer parses.

    That was only ever reachable by typing it as a CLI argument. It stops being
    theoretical the moment the panel offers a free-text name field, so this is
    enforced in the model rather than in either front end - the CLI and the agent
    then cannot disagree about what is allowed.
    """
    stripped = name.strip()
    if not stripped:
        raise InvalidTypeNameError("an MCU type name cannot be empty.", type=name)
    if stripped != name:
        raise InvalidTypeNameError(
            f"type name '{name}' has leading or trailing whitespace.", type=name
        )
    if len(stripped) > TYPE_NAME_MAX:
        raise InvalidTypeNameError(
            f"type name is too long ({len(stripped)} characters, max {TYPE_NAME_MAX}).",
            type=name,
        )
    # Caught by the whitelist too, but named separately so the message says what
    # is actually wrong rather than listing permitted characters.
    if stripped in (".", ".."):
        raise InvalidTypeNameError(f"'{name}' is not a usable name.", type=name)
    if not TYPE_NAME_RE.match(stripped):
        raise InvalidTypeNameError(
            f"type name '{name}' may only contain letters, digits, dot, dash and "
            f"underscore. It becomes both a config section and a directory name.",
            type=name,
        )
    return stripped


class Registry:
    """In-memory view of mcu-updater.cfg, backed by a comment-preserving document."""

    def __init__(self, types: dict[str, McuType], doc: CfgDocument) -> None:
        self.types = types
        self._doc = doc

    # --- construction / persistence ---

    @classmethod
    def load(cls, paths: Paths) -> Registry:
        path = paths.registry_file
        doc = typelist.read_doc(paths)
        if doc is None:
            return cls({}, CfgDocument())

        # Which families exist is itself config, and it is in this same
        # document - so read it from the doc already parsed rather than
        # reopening the file once per registry load.
        families_map = firmware.load_from_doc(doc)
        entries = typelist.read(doc, families_map)
        typelist.validate(entries, families_map, path=path)

        types: dict[str, McuType] = {}
        for entry in entries:
            if entry.builder != "kconfig_make":
                # Another builder's type. Its provider's view reads it from the
                # same list; this registry holds the kconfig_make types.
                continue
            name, block = entry.name, entry.block
            mcu = McuType(name=name, chipset=entry.chipset)
            mcu.serials = list(entry.serials)
            mcu.canbus_uuids = list(entry.canbus_uuids)
            mcu.firmwares = list(entry.firmwares)
            mcu.profile = (block.get("kconfig_make_profile") or "").strip()
            mcu.stop_services = block.get_csv("stop_services")
            # Only the families this type actually declares - not every
            # globally-declared [firmware ...] section. mcu.fw() is
            # setdefault, so iterating every family here would seed a phantom
            # slot for each one on every type.
            for fw in mcu.firmwares:
                cfg = mcu.fw(fw)
                cfg.extra_args = (block.get(f"{fw}_extra_args") or "").strip()
                for raw_patch in block.get_list(f"{fw}_makefile_patches"):
                    patch = MakefilePatch.parse(raw_patch)
                    if patch is None:
                        raise ConfigCorruptError(
                            f"{path}: could not parse a makefile patch for '{name}': "
                            f"{raw_patch!r}. Expected '<file> {PATCH_SEPARATOR} <line>'.",
                            path=path,
                            type=name,
                            value=raw_patch,
                        )
                    cfg.makefile_patches.append(patch)
                cfg.extra_repos = block.get_list(f"{fw}_extra_repos")
            types[name] = mcu

        return cls(types, doc)

    @classmethod
    @contextlib.contextmanager
    def mutate(cls, paths: Paths, label: str) -> Iterator[Registry]:
        """Load, modify and save as one atomic unit.

        ``with Registry.mutate(paths, "add serial") as reg: reg.add_serial(...)``

        The only way a registry reaches disk: `_save` is private so that no caller
        can write one it read outside the lock.

        The load happens *inside* the lock, deliberately. `_save()` rewrites the
        whole document, so saving a Registry that was read before someone else's
        edit erases that edit - and the agent and the CLI are separate processes
        that both write this file. Re-reading under the lock makes that impossible
        rather than unlikely.

        Uses its own lock file, so a build or flash holding the main lock for
        minutes does not block a sub-millisecond registry edit.

        Nothing is written if the body raises, so a validation failure leaves the
        file exactly as it was.
        """
        from .lock import ExclusiveLock

        with ExclusiveLock(paths, path=paths.registry_lock_file).acquire(label):
            reg = cls.load(paths)
            yield reg
            reg._save(paths)

    def _save(self, paths: Paths) -> None:
        """Atomic write, preserving everything the document already had.

        Writes the types this registry holds and deletes nothing. A section is
        deleted by `remove_type` / `remove_declared_type`, so a type this
        registry does not hold (another builder's) cannot be lost by a save.

        Validated with the same check `Registry.load` applies before any byte
        reaches disk - a mutation that sets `mcu.firmwares` to something
        `typelist.validate` would refuse (an undeclared family, a mixed-builder
        type, ...) must not produce a document the next `load` refuses, which
        would take every type down with it rather than only the bad one.
        """
        doc = self._doc
        families_map = firmware.load_from_doc(doc)
        fw_names = firmware.names_of(families_map)

        for name, mcu in self.types.items():
            # Whatever section a type already has, so an untouched config
            # never diffs. Only a type this document has never seen gets a
            # freshly written one.
            section = sections.section_for(doc, name)
            doc.set(section, "chipset", mcu.chipset)
            doc.set(section, "serials", list(mcu.serials))

            # Optional, unlike `serials` - most types have no CAN board at
            # all, and writing an empty `canbus_uuids:` stub into every
            # existing type section would be noise for a key most types will
            # never use.
            if mcu.canbus_uuids:
                doc.set(section, "canbus_uuids", list(mcu.canbus_uuids))
            else:
                doc.remove_option(section, "canbus_uuids")

            # Always written, never omitted as a restated default: load() now
            # refuses a type with no firmware: key at all, so _save() cannot
            # leave it implicit even for the plain-klipper case.
            doc.set(section, "firmware", ", ".join(mcu.firmwares))

            if mcu.profile.strip():
                doc.set(section, "kconfig_make_profile", mcu.profile.strip())
            else:
                doc.remove_option(section, "kconfig_make_profile")

            if mcu.stop_services is None:
                doc.remove_option(section, "stop_services")
            else:
                doc.set(section, "stop_services", ", ".join(mcu.stop_services))

            # Retired: whether a bootloader is present is now just whether one
            # is in `firmware:`. Dropped on every save rather than left stale.
            doc.remove_option(section, "katapult_installed")

            for fw in dict.fromkeys([*mcu.fws, *fw_names]):
                cfg = mcu.fws.get(fw)
                args_key = f"{fw}_extra_args"
                patch_key = f"{fw}_makefile_patches"
                if cfg is not None and cfg.extra_args.strip():
                    doc.set(section, args_key, cfg.extra_args.strip())
                else:
                    doc.remove_option(section, args_key)

                valid = [p for p in (cfg.makefile_patches if cfg else []) if p.is_valid()]
                if valid:
                    doc.set(section, patch_key, [p.render() for p in valid])
                else:
                    doc.remove_option(section, patch_key)

                repos_key = f"{fw}_extra_repos"
                repos = cfg.extra_repos if cfg else []
                if repos:
                    doc.set(section, repos_key, list(repos))
                else:
                    doc.remove_option(section, repos_key)

        # `read`/`validate` rather than a second validator - reusing the exact
        # walk `Registry.load` uses, over the document as it now stands (every
        # `doc.set`/`remove_option` above has already run). Raising here means
        # nothing below this line executes: no tmp file, no replace.
        typelist.validate(
            typelist.read(doc, families_map), families_map, path=paths.registry_file
        )

        os.makedirs(os.path.dirname(paths.registry_file), exist_ok=True)
        tmp = paths.registry_file + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(doc.render())
        os.replace(tmp, paths.registry_file)

    # --- lookups ---

    def __contains__(self, name: object) -> bool:
        return name in self.types

    def __len__(self) -> int:
        return len(self.types)

    def __bool__(self) -> bool:
        return bool(self.types)

    def names(self) -> list[str]:
        return sorted(self.types)

    def declared_type_names(self) -> list[str]:
        """All configured types, including those owned by another builder.

        A provider owns a type's build/configuration semantics, while the
        registry document owns physical identity.  Keeping those axes separate
        lets adoption edit a CMake or PlatformIO type without pretending its
        build belongs to Kconfig+make.
        """
        return sorted(declared.name for declared in sections.read(self._doc))

    def _declared_section(self, name: str) -> str:
        for declared in sections.read(self._doc):
            if declared.name == name:
                return declared.section
        raise UnknownTypeError(
            f"MCU type '{name}' does not exist.", type=name, known=self.declared_type_names()
        )

    def get_declared_chipset(self, name: str) -> str:
        """Chipset for any declared type, regardless of its build provider."""
        if name in self.types:
            return self.types[name].chipset
        return (self._doc.get(self._declared_section(name), "chipset") or "").strip()

    def get(self, name: str) -> McuType:
        try:
            return self.types[name]
        except KeyError:
            raise UnknownTypeError(
                f"MCU type '{name}' does not exist.", type=name, known=self.names()
            ) from None

    def all_serials(self) -> set[str]:
        out: set[str] = set()
        for mcu in self.types.values():
            out.update(mcu.serials)
        return out

    def find_types_for_serial(self, serial: str) -> list[str]:
        """Types tracking this serial. Normally 0 or 1; >1 is a misconfiguration."""
        return [name for name, mcu in self.types.items() if serial in mcu.serials]

    def find_declared_types_for_serial(self, serial: str) -> list[str]:
        """Every declared type tracking a serial, across all builders."""
        return [
            declared.name
            for declared in sections.read(self._doc)
            if serial in self._doc.get_list(declared.section, "serials")
        ]

    def find_types_for_uuid(self, uuid: str) -> list[str]:
        """Types tracking this CAN uuid. Normally 0 or 1; >1 is a misconfiguration.

        Separate from `find_types_for_serial` for the same reason
        `canbus_uuids` is a separate config key: a uuid and a by-id serial are
        different identity namespaces.
        """
        return [name for name, mcu in self.types.items() if uuid in mcu.canbus_uuids]

    def find_declared_types_for_uuid(self, uuid: str) -> list[str]:
        """Every declared type tracking a CAN uuid, across all builders."""
        return [
            declared.name
            for declared in sections.read(self._doc)
            if uuid in self._doc.get_list(declared.section, "canbus_uuids")
        ]

    def resolve_serial(self, serial: str, mcu_type: str | None = None) -> str:
        """Work out which type a serial belongs to.

        With an explicit `mcu_type`, verifies the pairing. Raises
        SerialTrackedElsewhereError if the serial belongs to a *different* type -
        that is a much stronger signal of a wrong selection than "this is a new
        device", so it is refused outright. Raises UnknownSerialError if it is
        simply untracked; the caller decides whether to offer adding it.
        """
        if mcu_type is not None:
            mcu = self.get(mcu_type)
            if serial in mcu.serials:
                return mcu_type
            elsewhere = self.find_types_for_serial(serial)
            if elsewhere:
                raise SerialTrackedElsewhereError(
                    f"serial '{serial}' is already tracked under '{elsewhere[0]}', "
                    f"not '{mcu_type}'. Did you mean -t {elsewhere[0]}?",
                    serial=serial,
                    requested=mcu_type,
                    tracked_under=elsewhere,
                )
            raise UnknownSerialError(
                f"serial '{serial}' isn't tracked under '{mcu_type}' yet.",
                serial=serial,
                requested=mcu_type,
            )

        matches = self.find_types_for_serial(serial)
        if not matches:
            raise UnknownSerialError(
                f"serial '{serial}' isn't tracked under any MCU type.", serial=serial
            )
        if len(matches) > 1:
            raise AmbiguousSerialError(
                f"serial '{serial}' is tracked under multiple types "
                f"({', '.join(sorted(matches))}) - pass -t to disambiguate.",
                serial=serial,
                tracked_under=sorted(matches),
            )
        return matches[0]

    def resolve_declared_serial(
        self, serial: str, mcu_type: str | None = None
    ) -> str:
        """Resolve a serial across every declared type, regardless of builder.

        This is the identity counterpart to :meth:`resolve_serial`: provider
        registries own build semantics, while the shared document owns the
        configured serial-to-type pairing.
        """
        if mcu_type is not None:
            section = self._declared_section(mcu_type)
            if serial in self._doc.get_list(section, "serials"):
                return mcu_type
            elsewhere = self.find_declared_types_for_serial(serial)
            if elsewhere:
                raise SerialTrackedElsewhereError(
                    f"serial '{serial}' is already tracked under '{elsewhere[0]}', "
                    f"not '{mcu_type}'. Did you mean -t {elsewhere[0]}?",
                    serial=serial,
                    requested=mcu_type,
                    tracked_under=elsewhere,
                )
            raise UnknownSerialError(
                f"serial '{serial}' isn't tracked under '{mcu_type}' yet.",
                serial=serial,
                requested=mcu_type,
            )

        matches = self.find_declared_types_for_serial(serial)
        if not matches:
            raise UnknownSerialError(
                f"serial '{serial}' isn't tracked under any MCU type.", serial=serial
            )
        if len(matches) > 1:
            raise AmbiguousSerialError(
                f"serial '{serial}' is tracked under multiple types "
                f"({', '.join(sorted(matches))}) - pass -t to disambiguate.",
                serial=serial,
                tracked_under=sorted(matches),
            )
        return matches[0]

    def resolve_uuid(self, uuid: str, mcu_type: str | None = None) -> str:
        """Work out which type a CAN uuid belongs to.

        `resolve_serial`'s counterpart for the uuid identity namespace - same
        shape, same three outcomes, mirrored rather than reused because a uuid
        and a by-id serial are different identity namespaces (the same
        false-cognate reasoning `canbus_uuids:` already follows as its own
        config key). Raises `UuidTrackedElsewhereError` if an explicit
        `mcu_type` does not match where the uuid is actually tracked,
        `UnknownUuidError` if it is untracked anywhere, `AmbiguousUuidError`
        if it is tracked under more than one type.
        """
        if mcu_type is not None:
            mcu = self.get(mcu_type)
            if uuid in mcu.canbus_uuids:
                return mcu_type
            elsewhere = self.find_types_for_uuid(uuid)
            if elsewhere:
                raise UuidTrackedElsewhereError(
                    f"CAN uuid '{uuid}' is already tracked under '{elsewhere[0]}', "
                    f"not '{mcu_type}'. Did you mean -t {elsewhere[0]}?",
                    uuid=uuid,
                    requested=mcu_type,
                    tracked_under=elsewhere,
                )
            raise UnknownUuidError(
                f"CAN uuid '{uuid}' isn't tracked under '{mcu_type}' yet.",
                uuid=uuid,
                requested=mcu_type,
            )

        matches = self.find_types_for_uuid(uuid)
        if not matches:
            raise UnknownUuidError(
                f"CAN uuid '{uuid}' isn't tracked under any MCU type.", uuid=uuid
            )
        if len(matches) > 1:
            raise AmbiguousUuidError(
                f"CAN uuid '{uuid}' is tracked under multiple types "
                f"({', '.join(sorted(matches))}) - pass -t to disambiguate.",
                uuid=uuid,
                tracked_under=sorted(matches),
            )
        return matches[0]

    # --- mutation ---

    def add_type(
        self,
        name: str,
        chipset: str,
        *,
        klipper_args: str = "",
        katapult_args: str = "",
        katapult_installed: bool = True,
        application: str = "klipper",
        profile: str = "",
        overwrite: bool = False,
    ) -> McuType:
        """Register a board model. **No hardware needs to exist.**

        Deliberately: a type is a description of a model, not of a board on the
        bus. Declaring one first is how you reach menuconfig for a board you
        have not wired up yet - which is the order the work actually happens in
        when a new probe arrives.

        Every family in the resulting `firmwares` must be declared in this
        registry's document; an undeclared one is refused with the section to
        add.
        """
        validate_type_name(name)
        if name in self.types and not overwrite:
            raise DuplicateTypeError(f"MCU type '{name}' already exists.", type=name)
        firmwares = [application]
        if katapult_installed and "katapult" not in firmwares:
            firmwares.append("katapult")
        declared = firmware.load_from_doc(self._doc)
        for fw in firmwares:
            if fw not in declared:
                raise ConfigCorruptError(
                    f"'{name}' names firmware '{fw}'. {firmware.missing_section_message(fw)}",
                    type=name,
                    value=fw,
                )
        mcu = McuType(
            name=name,
            chipset=chipset,
            firmwares=firmwares,
            profile=profile.strip(),
            serials=[],
            fws={
                "katapult": FwConfig(extra_args=katapult_args),
                "klipper": FwConfig(extra_args=klipper_args),
            },
        )
        self.types[name] = mcu
        return mcu

    def remove_type(self, name: str) -> McuType:
        mcu = self.get(name)
        del self.types[name]
        self._drop_section(name)
        return mcu

    def add_serial(self, name: str, serial: str) -> bool:
        """Returns True if it was added, False if already present."""
        mcu = self.get(name)
        if serial in mcu.serials:
            return False
        mcu.serials.append(serial)
        return True

    def add_declared_serial(self, name: str, serial: str) -> bool:
        """Add an identity without broadening provider build ownership."""
        if name in self.types:
            return self.add_serial(name, serial)
        section = self._declared_section(name)
        serials = self._doc.get_list(section, "serials")
        if serial in serials:
            return False
        self._doc.set(section, "serials", [*serials, serial])
        return True

    def remove_serial(self, name: str, serial: str) -> bool:
        """Returns True if it was removed, False if it wasn't tracked."""
        mcu = self.get(name)
        if serial not in mcu.serials:
            return False
        mcu.serials.remove(serial)
        return True

    def remove_declared_serial(self, name: str, serial: str) -> bool:
        """Remove an identity without broadening provider build ownership."""
        if name in self.types:
            return self.remove_serial(name, serial)
        section = self._declared_section(name)
        serials = self._doc.get_list(section, "serials")
        if serial not in serials:
            return False
        self._doc.set(section, "serials", [item for item in serials if item != serial])
        return True

    def declared_serials(self, name: str) -> list[str]:
        """A declared type's serials, whichever builder owns it."""
        if name in self.types:
            return list(self.types[name].serials)
        return self._doc.get_list(self._declared_section(name), "serials")

    def remove_declared_type(self, name: str) -> list[str]:
        """Delete a declared type, whichever builder owns it.

        Returns the serials it tracked, so a caller can say what went with it.
        """
        serials = self.declared_serials(name)
        self.types.pop(name, None)
        self._drop_section(name)
        return serials

    def _drop_section(self, name: str) -> None:
        """Delete a type's section, if the document has one yet."""
        if name in self.declared_type_names():
            self._doc.remove_section(self._declared_section(name))

    def add_canbus_uuid(self, name: str, uuid: str) -> bool:
        """Returns True if it was added, False if already present."""
        mcu = self.get(name)
        if uuid in mcu.canbus_uuids:
            return False
        mcu.canbus_uuids.append(uuid)
        return True

    def add_declared_canbus_uuid(self, name: str, uuid: str) -> bool:
        """Add a CAN identity without broadening provider build ownership."""
        if name in self.types:
            return self.add_canbus_uuid(name, uuid)
        section = self._declared_section(name)
        uuids = self._doc.get_list(section, "canbus_uuids")
        if uuid in uuids:
            return False
        self._doc.set(section, "canbus_uuids", [*uuids, uuid])
        return True

    def remove_canbus_uuid(self, name: str, uuid: str) -> bool:
        """Returns True if it was removed, False if it wasn't tracked."""
        mcu = self.get(name)
        if uuid not in mcu.canbus_uuids:
            return False
        mcu.canbus_uuids.remove(uuid)
        return True

    def remove_declared_canbus_uuid(self, name: str, uuid: str) -> bool:
        """Remove a CAN identity without broadening provider build ownership."""
        if name in self.types:
            return self.remove_canbus_uuid(name, uuid)
        section = self._declared_section(name)
        uuids = self._doc.get_list(section, "canbus_uuids")
        if uuid not in uuids:
            return False
        remaining = [item for item in uuids if item != uuid]
        if remaining:
            self._doc.set(section, "canbus_uuids", remaining)
        else:
            self._doc.remove_option(section, "canbus_uuids")
        return True

    def items(self) -> Iterable[tuple[str, McuType]]:
        return self.types.items()
