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
class Provisioner(Protocol):
    """Give an unprovisioned board its durable identity.

    Two questions, because the first is cheap and the second is irreversible:
    `is_unprovisioned` is a string test on a serial the caller already has, and
    `provision` writes to hardware.

    **The caller holds the op lock.** Provisioning finds its own device, writes
    to it and waits for it to re-enumerate under a new name - three steps that
    must not interleave with a flash or with a second provision, and the lock
    covering all three is the caller's to take (`lock.exclusive`), because the
    caller is the one that knows what to call the operation.
    """

    name: str

    def is_unprovisioned(self, serial: str) -> bool:
        """Does this serial look like an unprovisioned board's diagnostic one?"""
        ...

    def provision(self, paths: Paths, serial: str) -> str:
        """Provision the board answering to `serial`; return its new serial."""
        ...


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
