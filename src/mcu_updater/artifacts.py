"""What a build staged, by kind - so a flasher is handed the file it can write.

A kind names what a flasher *consumes*, never which builder made it: a `bin` is
a raw image whether kconfig-make or cmake produced it. `pio_env` is the one
exception, on purpose. The PlatformIO flasher runs `pio run -t upload`, which
uploads from PlatformIO's own build directory rather than taking a file, and
nothing but a PlatformIO build produces what it consumes. The kind says so
instead of pretending that flasher takes a `bin`.

Imports nothing from this package, so `flashers.spec` and `providers` can both
depend on it without a cycle.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

#: A raw image, written at the address it was linked for.
KIND_BIN = "bin"
#: A UF2 container. Every block carries its own target address.
KIND_UF2 = "uf2"
#: A built PlatformIO env. Builder-specific by design; see the module docstring.
KIND_PIO_ENV = "pio_env"


@dataclasses.dataclass(frozen=True)
class Artifact:
    """One staged file.

    `sha256` is the hash its build recorded, and only while the bytes on disk
    still match it. `None` means "flashable, but nothing vouches for it" - and
    that is what the ledger then files, rather than a hash of other bytes.
    """

    kind: str
    path: str
    sha256: str | None = None


@dataclasses.dataclass(frozen=True)
class Staged:
    """Everything one type's build left staged, and the provenance beside it.

    `fw_sha` and `version` are in each builder's own vocabulary already
    translated: kconfig's `fw_sha`, cmake's and PlatformIO's `sha`.
    """

    fw: str
    artifacts: tuple[Artifact, ...] = ()
    fw_sha: str | None = None
    version: str | None = None

    def first_of(self, kinds: Iterable[str]) -> Artifact | None:
        """The staged artifact of the first kind in `kinds` that has one.

        The *flasher's* order, not the build's: a flasher that takes two kinds
        lists the one it prefers first.
        """
        for kind in kinds:
            for artifact in self.artifacts:
                if artifact.kind == kind:
                    return artifact
        return None


def sidecar_field(hashes: Mapping[str, str | None]) -> dict[str, dict[str, str | None]]:
    """`{kind: sha256}` as a sidecar's `artifacts` value."""
    return {kind: {"sha256": sha} for kind, sha in hashes.items()}


def recorded_kinds(side: Mapping[str, Any]) -> set[str]:
    """The kinds a sidecar says its build staged.

    Empty for a sidecar written before `artifacts` existed. Such a sidecar
    vouches only for its builder's primary kind, which each builder stages by
    its presence on disk, not by this list.
    """
    field = side.get("artifacts")
    return set(field) if isinstance(field, dict) else set()


def recorded_sha256(side: Mapping[str, Any], kind: str, *, primary: str) -> str | None:
    """The hash a sidecar recorded for `kind`, or None.

    Falls back to the legacy `bin_sha256` only for the builder's `primary`
    kind. That key hashed the `.bin` for kconfig, the `.uf2` for cmake and
    `firmware.bin` for PlatformIO, so reading it for any other kind would vouch
    for bytes it never described. A missing hash is never a disagreement: it
    reads as no provenance, and accuses nobody.
    """
    field = side.get("artifacts")
    if isinstance(field, dict):
        entry = field.get(kind)
        if isinstance(entry, dict) and entry.get("sha256"):
            return str(entry["sha256"])
    if kind == primary:
        legacy = side.get("bin_sha256")
        return str(legacy) if legacy else None
    return None
