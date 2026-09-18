"""Roadrunner's device info, and its confirmed direct-USB BOOTSEL requester."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from .. import device_info, uf2
from ..device_info import SOURCE_INFO, SOURCE_KLIPPER, DeviceInfo
from ..discovery import bootsel, roadrunner
from ..flashers.spec import Bench
from ..paths import Paths
from .spec import BootselHandoff

#: `git describe --tags --always --dirty`: `v1.2.0-3-gdeadbee`, a bare
#: `deadbee` in a repo with no tags, either with `-dirty`. A bare tag or `dev`
#: carries no commit.
_SHA_RE = re.compile(r"(?:^|-g)([0-9a-f]{7,40})(?:-dirty)?$")


class RoadrunnerHelper:
    """Confirm a provisioned Roadrunner before requesting its BOOTSEL mode."""

    name: str = "roadrunner"
    klipper_prefix: str = "high_resolution_filament_sensor"
    klipper_fields: tuple[str, ...] = ("identity", "firmware_image")

    def running_sha(self, version: str | None) -> str | None:
        match = _SHA_RE.search((version or "").strip())
        return match.group(1) if match else None

    def is_dirty(self, version: str | None) -> bool:
        return (version or "").strip().endswith("-dirty")

    def from_klipper(self, values: Mapping[str, Any]) -> tuple[str, DeviceInfo] | None:
        """One sensor object's `identity` and `firmware_image`, as a report.

        The extra's `get_status` answers both halves: `identity.firmware_version`
        is the same `${git_describe}` INFO reports, and `firmware_image` is the
        digest and the range it covers. So a board Klippy is holding needs no
        port opened - which matters because Klippy holding the port is exactly
        what stops the admin protocol from opening it. It reports the same on
        usbserial, i2c and uart.

        Both wire spellings are converted here rather than at the comparison:
        the digest arrives as a hex string and the algorithm as a name, and one
        unconverted value would read as a permanent mismatch on a board that is
        running precisely what we flashed.
        """
        identity = values.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        serial = identity.get("serial")
        if not isinstance(serial, str) or not serial.strip():
            # No serial is no join key. A board mid-connect reports the
            # object with nothing in it, and guessing which tracked serial
            # it is would attach one board's digest to another's row.
            return None
        image = values.get("firmware_image")
        image = image if isinstance(image, dict) else {}
        version = identity.get("firmware_version")
        return serial, DeviceInfo(
            source=SOURCE_KLIPPER,
            version=version if isinstance(version, str) and version else None,
            digest_algorithm=uf2.algorithm_id(image.get("algorithm")),
            digest=device_info.digest_int(image.get("digest")),
            image_start=device_info.image_int(image.get("start")),
            image_length=device_info.image_int(image.get("length")),
        )

    def wire_source(self, paths: Paths) -> Callable[[str], DeviceInfo | None]:
        read = roadrunner.wire_provenance(paths)

        def source(serial: str) -> DeviceInfo | None:
            info = read(serial)
            if not info:
                return None
            return DeviceInfo(
                source=SOURCE_INFO,
                version=info.get("fw_version"),
                digest_algorithm=info.get("digest_algorithm"),
                digest=info.get("digest"),
                image_start=info.get("image_start"),
                image_length=info.get("image_length"),
            )

        return source

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff:
        device = roadrunner.find_provisioned(bench.paths, serial)
        topology = bootsel.serial_topology_for(bench.paths, device.port)
        roadrunner.Roadrunner().request_bootsel(bench.paths, device)
        return BootselHandoff(topology=topology)

    def wait_ready(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> None:
        roadrunner.wait_for_provisioned(bench.paths, serial)


__all__ = ["RoadrunnerHelper"]
