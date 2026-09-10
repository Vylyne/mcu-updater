"""The narrowly scoped contract for firmware-specific BOOTSEL requests."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..flashers.spec import Bench


@dataclasses.dataclass(frozen=True)
class BootselHandoff:
    """Transient USB topology evidence for the matching BOOTSEL mount."""

    topology: str


class BootselRequester(Protocol):
    """Firmware-specific BOOTSEL entry and post-write readiness."""

    name: str

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff: ...

    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> None:
        """Wait until the flashed firmware confirms its durable identity."""
        ...
