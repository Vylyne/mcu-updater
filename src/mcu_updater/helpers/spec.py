"""The narrowly scoped contract for firmware-specific BOOTSEL requests."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..build import Reporter
    from ..device_info import DeviceInfo
    from ..discovery.knomi_serial import WatcherDevice
    from ..extras import Extra
    from ..flashers.spec import Bench
    from ..paths import Paths
    from ..providers.pio import PioType
    from ..settings import Settings


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
        self,
        bench: Bench,
        *,
        serial: str,
        chipset: str,
        ctx: Any,
        type_name: str,
        fw: str,
        topology: str,
    ) -> None:
        """Wait until the flashed firmware confirms its durable identity.

        `type_name` and `fw` name the image just written, for a helper whose
        board can come back under a serial that image chose (Klipper's
        `CONFIG_USB_SERIAL_NUMBER`). `topology` is the USB port the board was
        asked on, which it comes back on whatever serial it chooses.
        """
        ...


@runtime_checkable
class DeviceInfoReader(Protocol):
    """Reads a commit and a dirty flag out of the version string a board stamps."""

    name: str
    #: The Klipper section prefix whose objects are this firmware's boards.
    klipper_prefix: str

    def running_sha(self, version: str | None) -> str | None: ...

    def is_dirty(self, version: str | None) -> bool: ...


@dataclasses.dataclass(frozen=True)
class TrackVerdict:
    """Whether a serial is a durable identity, and what to do when it is not."""

    ok: bool
    reason: str | None = None
    remedy: str | None = None


@runtime_checkable
class Trackable(Protocol):
    """Judges whether a serial is a durable identity for this firmware."""

    name: str

    def is_trackable(self, serial: str) -> TrackVerdict: ...


@runtime_checkable
class Provisioner(Protocol):
    """Give an unprovisioned board its durable identity.

    Trackability and provisioning are separate capabilities because judging a
    serial is a cheap string test while provisioning is an irreversible hardware
    write. A helper may refuse tracking without being able to apply the remedy.

    **The caller holds the op lock.** Provisioning finds its own device, writes
    to it and waits for it to re-enumerate under a new name - three steps that
    must not interleave with a flash or with a second provision, and the lock
    covering all three is the caller's to take (`lock.exclusive`), because the
    caller is the one that knows what to call the operation.

    Giving an identity and taking it back are one firmware feature, so they are
    one capability. `identity_state` is how a caller that names no family finds
    the helper to ask: a string test, like `is_trackable`, that never opens a
    port.
    """

    name: str

    def identity_state(self, serial: str) -> Literal["unprovisioned", "provisioned"] | None:
        """Whether `serial` is this firmware's identity, and in which state."""
        ...

    def provision(self, paths: Paths, serial: str) -> str:
        """Provision the board answering to `serial`; return its new serial."""
        ...

    def clear(self, paths: Paths, serial: str) -> str:
        """Return the board answering to `serial` to its unprovisioned
        identity; return the serial it came back under."""
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


@runtime_checkable
class Identifier(Protocol):
    """Answers which device is which, for hardware the host cannot name.

    Every other device in this tool is found by something the host can see
    without asking: a serial in `/dev/serial/by-id`, a DFU descriptor, an
    `RPI-RP2` volume. `discovery`'s sources exist to decide which of those
    sightings to trust. A KNOMI has none of them - the CH340K in
    front of it reports no USB serial at all - so the only stable name it
    has is one its *firmware* knows and will state if asked. That is
    firmware-specific by construction, which is what makes it a helper
    capability rather than a source.

    `ask` is the cost. False is the remembered answer: a file, instant, and
    safe while Klipper holds every port. True opens the free ports and reads
    what broadcasts back - authoritative, and only possible once the caller
    has stopped the services holding them. What True falls back to when it
    cannot ask is the helper's policy; a device it heard is returned with
    `answered` set. No default, so that cost is never acquired by omission.

    Keyed by device id. The value is knomi's own `WatcherDevice` because a
    five-field copy here would be a second description of one thing;
    knomi_serial is the only identifier, and a second one is when to
    generalise it.
    """

    name: str

    def identify(
        self,
        paths: Paths,
        settings: Settings,
        entry: PioType,
        *,
        ask: bool,
        reporter: Reporter,
    ) -> dict[str, WatcherDevice]: ...

    def remembered_at(self, paths: Paths, entry: PioType) -> str:
        """The file the remembered answers live in, or "" when there is none."""
        ...


@dataclasses.dataclass(frozen=True)
class ListedDevice:
    """One configured device, as the core uses it and nothing else.

    A firmware's own fields stay in `raw`, which only its helper reads (for its
    `extras()`), and which `to_json` never emits. The core may grow a field
    here when it already uses the fact. A firmware's field may not be added.
    """

    #: What the device is addressed by: its configured id, else its configured path.
    id: str | None
    #: Its printer object, in the capitalisation printer.cfg used.
    section: str
    #: Its short name: the section without the prefix.
    label: str
    #: The id printer.cfg names, or None.
    configured_id: str | None
    #: The hardware id the device reports: the flash log's `hwid:` key and the
    #: source of `confidence`.
    reported_id: str | None
    #: The port printer.cfg or discovery gave, or None.
    configured_path: str | None
    #: `configured_path` resolved, or None when it does not exist.
    resolved_path: str | None
    #: The firmware version the device reports, or None.
    version: str | None
    #: False when the device declares it cannot work with the host; drives the
    #: `protocol_mismatch` state. None is unknown.
    compatible: bool | None
    #: Whether the far end answers, as opposed to the port merely existing.
    #: None is unknown.
    answering: bool | None
    raw: Mapping[str, Any] = dataclasses.field(default_factory=dict, compare=False, repr=False)

    @property
    def present(self) -> bool:
        return self.resolved_path is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "label": self.label,
            "configured_id": self.configured_id,
            "reported_id": self.reported_id,
            "configured_path": self.configured_path,
            "resolved_path": self.resolved_path,
            "present": self.present,
            "version": self.version,
            "compatible": self.compatible,
            "answering": self.answering,
        }


@runtime_checkable
class DeviceLister(Protocol):
    """Lists a family's configured devices from Klipper's printer objects.

    Modelled on `ImageReporter`: the helper declares a prefix and interprets
    the object values, and the core owns the Moonraker query. The method is
    `device_from_klipper`, not `from_klipper`, because `ImageReporter` already
    owns that name with another signature, and one helper may be both.
    """

    name: str
    #: The Klipper section prefix whose objects are this firmware's devices.
    klipper_prefix: str

    def device_from_klipper(self, section: str, values: Mapping[str, Any]) -> ListedDevice:
        """One device from its printer object. `section` is the object's own
        capitalisation, which is what printer.cfg says."""
        ...

    def extras(self, devices: Sequence[ListedDevice]) -> list[Extra]:
        """Facts about one type's devices worth showing on its row."""
        ...

    def devices_note(self, *, reachable: bool) -> str:
        """Why a type of this family lists no devices. Called only when it lists none."""
        ...
