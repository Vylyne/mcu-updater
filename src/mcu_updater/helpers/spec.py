"""The narrowly scoped contract for firmware-specific BOOTSEL requests."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..device_info import DeviceInfo
    from ..flashers.spec import Bench
    from ..paths import Paths


@dataclasses.dataclass(frozen=True)
class BootselHandoff:
    """Transient USB topology evidence for the matching BOOTSEL mount."""

    topology: str


@runtime_checkable
class Helper(Protocol):
    """A registered firmware helper.

    What a helper can do is the capability Protocols below, each asked for
    through its accessor in `helpers` (`helpers.bootsel_requester(helper)`).
    Nothing compares a helper's `name` to decide what to do with it.
    """

    name: str


@runtime_checkable
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


@runtime_checkable
class DeviceInfoReader(Protocol):
    """Reads a commit and a dirty flag out of the version string a board stamps."""

    name: str
    #: The Klipper section prefix whose objects are this firmware's boards.
    klipper_prefix: str

    def running_sha(self, version: str | None) -> str | None: ...

    def is_dirty(self, version: str | None) -> bool: ...


@runtime_checkable
class ImageReporter(Protocol):
    """Reports a board's image digest, Klipper first and the wire second."""

    name: str
    klipper_prefix: str
    #: The object fields to ask Klipper for; nothing else, inside fw.status's
    #: sub-second budget.
    klipper_fields: tuple[str, ...]

    def from_klipper(self, values: Mapping[str, Any]) -> tuple[str, DeviceInfo] | None:
        """(the serial the board reported, its report), or None with no serial."""
        ...

    def wire_source(self, paths: Paths) -> Callable[[str], DeviceInfo | None]:
        """A per-serial reader that opens the port. Never from `fw.status`."""
        ...
