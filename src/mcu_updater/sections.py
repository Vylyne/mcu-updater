"""Which section spellings name a type, and which provider each one means.

``[mcu carto_v4]`` and ``[display knomi_toolchanger]`` were two spellings of one
idea: a class of device this host builds firmware for. The difference between
them is which build system compiles it and which flasher writes it - and that is
exactly what the Provider and Flasher seams turned into data. Encoding it in the
section name meant a third kind of device needed a third prefix, a third reader
and a third branch in everything that walked them.

So the section is ``[type <name>]`` with a ``provider:`` key, and the two old
prefixes are aliases for the provider they always implied. This module is the
only thing that knows that, which is what keeps the aliasing from spreading:
:mod:`~mcu_updater.config` and :mod:`~mcu_updater.pio` each ask for their own
provider's sections and never learn there is more than one way to spell one.

**The old spellings are not deprecated.** They are in hand-edited files on
printers nobody is watching, they cost two entries in a table, and a warning on
every agent start would be noise about a config that is working. A section is
rewritten in whatever spelling it already uses, so nothing churns a file the user
did not ask to change.
"""

from __future__ import annotations

import dataclasses

from .cfgdoc import CfgDocument

#: Provider registry keys. Duplicated from `providers.registry` rather than
#: imported: config parsing is underneath the provider layer, and importing
#: upward to read two string constants would make the seam circular.
KCONFIG_MAKE = "kconfig_make"
PLATFORMIO = "platformio"

#: The spelling new sections are written in.
PREFIX = "type"

#: What a section named this way has always meant. First match wins on read;
#: order is irrelevant since a section has exactly one prefix.
LEGACY_PREFIXES: dict[str, str] = {
    "mcu": KCONFIG_MAKE,
    "display": PLATFORMIO,
}

#: What `[type <name>]` means with no `provider:` key. kconfig, because that is
#: what a type meant for every release before this one - a bare `[type x]`
#: reading as PlatformIO would silently change what an edited `[mcu x]` builds.
DEFAULT_PROVIDER = KCONFIG_MAKE


@dataclasses.dataclass(frozen=True)
class TypeSection:
    """One declared type: what it is called, what builds it, where it is written.

    `section` is the header as it appears in the file, not a header derived from
    `name` - that is the whole point of carrying it. A caller that rebuilt it
    would write `[type foo]` over a file that said `[mcu foo]`, turning every
    read of an untouched config into a diff.
    """

    name: str
    provider: str
    section: str


def read(doc: CfgDocument, provider: str | None = None) -> list[TypeSection]:
    """Every declared type, in file order, optionally only one provider's.

    An unknown `provider:` value is *kept*, not dropped or defaulted. Whoever
    asked for a specific provider will not match it, so it costs nothing here -
    and the type still exists as far as listing and validation are concerned,
    which is what makes "no provider called that" a message somebody can act on
    rather than a section that silently vanished.
    """
    out: list[TypeSection] = []
    for section in doc.section_names(PREFIX):
        name = section[len(PREFIX) :].strip()
        if not name:
            continue
        declared = (doc.get(section, "provider") or "").strip() or DEFAULT_PROVIDER
        out.append(TypeSection(name=name, provider=declared, section=section))
    for prefix, implied in LEGACY_PREFIXES.items():
        for section in doc.section_names(prefix):
            name = section[len(prefix) :].strip()
            if not name:
                continue
            out.append(TypeSection(name=name, provider=implied, section=section))
    if provider is not None:
        out = [t for t in out if t.provider == provider]
    return out


def section_for(doc: CfgDocument, name: str, provider: str) -> str:
    """The header to write `name` under: the one it already has, or a new one.

    Keeping an existing section where it is matters more than it looks. `save()`
    rewrites the whole document, so returning `[type foo]` for a file that says
    `[mcu foo]` would not just churn the diff - it would leave the old section
    behind unless every caller also remembered to remove it, which is a
    duplicate-section error on the next load.
    """
    for declared in read(doc):
        if declared.name == name and declared.provider == provider:
            return declared.section
    return f"{PREFIX} {name}"


def is_type_section(section: str) -> bool:
    """Does this header declare a type, under any spelling?

    For the save path, which removes sections whose type is gone. It must not
    match `[firmware ...]` or `[updater]`, which are different axes that happen
    to live in the same file.
    """
    head = section.split(maxsplit=1)[0] if section.split() else ""
    return head == PREFIX or head in LEGACY_PREFIXES


__all__ = [
    "DEFAULT_PROVIDER",
    "KCONFIG_MAKE",
    "LEGACY_PREFIXES",
    "PLATFORMIO",
    "PREFIX",
    "TypeSection",
    "is_type_section",
    "read",
    "section_for",
]
