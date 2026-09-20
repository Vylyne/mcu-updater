"""What a board reports it is running, read through its firmware's handler.

Spec §5 of the one-pipeline design. A family's helper supplies the reader when
it has one; everything else - klipper, katapult, a fork with no oddity - reads
through `KLIPPER`, built in here because it is not firmware-specific code.

**Absence is never mismatch.** Every field of `DeviceInfo` is optional, and a
field a firmware does not report stays None. A comparison that treated None as
a value would flag a board running exactly what we flashed, permanently, with
no flash able to clear it.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

from .discovery.byid import canonical_serial
from .errors import ConfigCorruptError

if TYPE_CHECKING:
    from .firmware import FirmwareFamily
    from .helpers.spec import DeviceInfoReader

#: Klipper's object graph answered.
SOURCE_KLIPPER = "klipper"
#: The board answered over its own protocol, because Klipper did not hold it.
SOURCE_INFO = "info"


@dataclasses.dataclass(frozen=True)
class DeviceInfo:
    """One board's report of its firmware, from whichever source answered."""

    source: str
    version: str | None
    digest_algorithm: int | None = None
    digest: int | None = None
    image_start: int | None = None
    image_length: int | None = None

    def has_digest(self) -> bool:
        """Whether this report can be checked against a built image.

        A digest needs its algorithm and the range it covers. `DIGEST_NONE` is
        0, so a board that says "no algorithm" has no digest, and an
        `image_start` of 0 is a real start.
        """
        return (
            bool(self.digest_algorithm)
            and self.digest is not None
            and self.image_start is not None
            and self.image_length is not None
        )


def serial_key(serial: str) -> str:
    """One spelling for a serial, so the two sources can actually meet.

    The board burns one string and reports it twice: through the USB serial
    descriptor, which reaches us via `/dev/serial/by-id` with udev's interface
    marker on the end, and through the identity register, which reaches us via
    the klippy extra verbatim. A join that compared them raw would miss on the
    suffix or on case - and a missed join is indistinguishable from "Klipper
    did not answer", so it would silently send every board to the wire.
    """
    return canonical_serial(serial.strip()).upper()


def digest_int(value: object) -> int | None:
    """Klipper's `"%#010x"` digest as the int every other side of this uses.

    The extra sends a hex string on purpose - an identifier to compare, not a
    quantity - while INFO sends four little-endian bytes and `record_build`
    stores an int. Int is canonical and each reader converts on the way in,
    because a comparison that ever saw `"0xbbe38aa9" != 3185217705` would call
    a correctly flashed board stale forever: reflashing cannot clear a
    formatting difference. Anything unparseable is absence, not mismatch.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def image_int(value: object) -> int | None:
    """A reported image bound, or None when the firmware reported none."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


#: git describe embeds the commit as a g<hex> token. Anything after it - notably
#: `-dirty`, which a makefile-patched build always carries - is noise here.
_KLIPPER_SHA_RE = re.compile(r"(?:^|-)g([0-9a-f]{7,40})(?:-|$)")


class KlipperReader:
    """Klipper and every tree that stamps Klipper's `git describe`.

    A board that stamps a hand-maintained literal instead - Cartographer's
    `CONFIG_VERSION` - has no g<hex> token, and its family's helper says so
    explicitly (`helpers.cartographer`).
    """

    name: str = "klipper"
    klipper_prefix: str = "mcu"

    def running_sha(self, version: str | None) -> str | None:
        match = _KLIPPER_SHA_RE.search(version or "")
        return match.group(1) if match else None

    def is_dirty(self, version: str | None) -> bool:
        """Never. A makefile-patched build is always `-dirty`, and a board that
        read as dirty for that would want a flash no flash can satisfy."""
        return False


KLIPPER = KlipperReader()


def reader_for(family: FirmwareFamily | None) -> DeviceInfoReader:
    """The reader a family's boards are read through.

    Never raises and never returns `None`: a family with no helper, a helper
    without the capability, and a helper name no registered helper answers to
    all read through Klipper. The last of those is a config error, and it is
    deliberately not raised here - `reader_for` is called from `fw.status`,
    where `dispatch` turns any `UpdaterError` into one `RpcError` for the
    whole call, so raising would blank the panel for every MCU of every
    provider over one typo. The write path resolves the helper itself and
    refuses by name, which is where the operator learns what is wrong.
    """
    from . import helpers

    if family is None or not family.helper:
        return KLIPPER
    try:
        helper = helpers.for_name(family.helper, family=family.name)
    except ConfigCorruptError:
        return KLIPPER
    reader = helpers.device_info_reader(helper)
    return reader if reader is not None else KLIPPER
