"""Which sections declare a type: naming, and nothing else.

``[mcu carto_v4]`` and ``[display knomi_toolchanger]`` were two spellings of one
idea, aliased here so :mod:`~mcu_updater.config` and
:mod:`~mcu_updater.providers.pio` could each read their own kind without
learning there was more than one way to spell one. The ``provider:`` key that
came after them was the same idea moved into the file: which build system a
type belongs to, decided by a key on the type rather than by which class of
tree it happens to be.

Both are gone. A type's provider is derived from its declared firmware's
builder (see :mod:`~mcu_updater.firmware`, :mod:`~mcu_updater.config`'s
``_is_platformio_only``, and ``providers/pio.py``'s ``load()``) - a fact about
the ``[firmware ...]`` section it names, not about how its own section is
spelled or what key it carries. This module now only knows one spelling,
``[type <name>]``, and only answers "which sections declare a type" - naming
and validation, not which of them build with what.
"""

from __future__ import annotations

import dataclasses

from .cfgdoc import CfgDocument

#: The only spelling a type section is written or read in.
PREFIX = "type"


@dataclasses.dataclass(frozen=True)
class TypeSection:
    """One declared type: what it is called, where it is written.

    ``section`` is the header as it appears in the file, not one derived from
    ``name`` - `save()` writes back to whatever section a type already has
    rather than rebuilding the header, so an untouched config never diffs.
    """

    name: str
    section: str


def read(doc: CfgDocument) -> list[TypeSection]:
    """Every declared type, in file order."""
    out: list[TypeSection] = []
    for section in doc.section_names(PREFIX):
        name = section[len(PREFIX) :].strip()
        if not name:
            continue
        out.append(TypeSection(name=name, section=section))
    return out


def section_for(doc: CfgDocument, name: str) -> str:
    """The header to write `name` under: the one it already has, or a new one."""
    for declared in read(doc):
        if declared.name == name:
            return declared.section
    return f"{PREFIX} {name}"


def is_type_section(section: str) -> bool:
    """Does this header declare a type?

    For the save path, which removes sections whose type is gone. It must not
    match `[firmware ...]` or `[updater]`, which are different axes that happen
    to live in the same file.
    """
    head = section.split(maxsplit=1)[0] if section.split() else ""
    return head == PREFIX


__all__ = [
    "PREFIX",
    "TypeSection",
    "is_type_section",
    "read",
    "section_for",
]
