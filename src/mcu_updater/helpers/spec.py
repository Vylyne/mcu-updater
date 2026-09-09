"""The narrowly scoped contract for firmware-specific BOOTSEL requests."""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from ..flashers.spec import Bench


@dataclasses.dataclass(frozen=True)
class BootselHandoff:
    """Transient USB topology evidence for the matching BOOTSEL mount."""

    topology: str


class BootselRequester(Protocol):
    """A firmware-specific way to request its board's BOOTSEL mode."""

    name: str

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff: ...
