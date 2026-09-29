"""KNOMI screens running knomi-serial.

Reads device info through `providers.pio`, the same regex the display's own
staleness check uses - see `test_the_knomi_reader_is_the_platformio_one` for
why the two must not disagree. Identity arrives in Task 10. It is registered
first so a `platformio` family can declare it and have that declaration
checked when the config loads.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..discovery.knomi_serial import device_map_path, discover, read_device_map
from ..errors import UpdaterError
from ..extras import Extra
from ..providers import pio
from .spec import ListedDevice

if TYPE_CHECKING:
    from ..build import Reporter
    from ..discovery.knomi_serial import WatcherDevice
    from ..paths import Paths
    from ..settings import Settings


class KnomiSerialHelper:
    """Identity and firmware access for a BTT KNOMI v2 screen.

    Named by a `[firmware ...]` section's `helper:`. The KNOMI needs one
    because the CH340K in front of it reports no USB serial, so every unit
    enumerates identically and the only stable name it has is one its own
    firmware will state if asked. That is a property of this hardware, not
    of `builder: platformio` - a PlatformIO board with a real serial is an
    ordinary by-id device and names no helper at all.
    """

    name: str = "knomi_serial"
    klipper_prefix: str = "knomi_serial"

    def running_sha(self, version: str | None) -> str | None:
        return pio.running_sha(version)

    def is_dirty(self, version: str | None) -> bool:
        return pio.is_dirty(version)

    def identify(
        self,
        paths: Paths,
        settings: Settings,
        entry: pio.PioType,
        *,
        ask: bool,
        reporter: Reporter,
    ) -> dict[str, WatcherDevice]:
        """What is remembered, or what the devices say now.

        `ask=False` is the watcher's map and nothing else. `devices.json` is
        written by knomi_serial's own watcher for the ports Klipper does not
        hold, answers instantly, and costs no port - which is what a status
        poll, and a CLI choosing what to flash, want.

        `ask=True` is a caller saying the ports are free and it wants to be
        sure: the listen pass, six seconds of held ports, and the only answer
        taken at flash time. It is not merged with the map. A device that stays
        silent while others answer is not there, and one the listen never heard
        must not come back with a remembered port dressed as a confirmed one -
        so what was heard is marked `answered`, and nothing else is.

        The map is the fallback only when the listen cannot run at all: it
        needs pyserial out of the module's source tree, and a host missing it
        must reach the caller's own "neither source could tell" refusal - which
        names both sources - rather than a tool error. Only its present entries
        are offered: the watcher is stopped while the ports are free, so the map
        is frozen, and a port whose node is gone is not an answer.
        """
        if not ask:
            return read_device_map(paths, entry)
        reporter("info", f"Asking the '{entry.name}' devices which they are...")
        try:
            heard = discover(paths, settings, entry, reporter=reporter)
        except UpdaterError as exc:
            reporter("warn", f"could not ask the devices ({exc}) - using the watcher's map instead")
            return {i: d for i, d in read_device_map(paths, entry).items() if d.present}
        return {i: dataclasses.replace(d, answered=True) for i, d in heard.items()}

    def remembered_at(self, paths: Paths, entry: pio.PioType) -> str:
        return device_map_path(paths, entry)

    def device_from_klipper(self, section: str, values: Mapping[str, Any]) -> ListedDevice:
        """One `[knomi_serial ...]` printer object, as the core's `ListedDevice`.

        The module refuses both `serial:` and `device_id:` and requires one, so
        which of them loaded says how the section is addressed. `port` is the
        module's merged value: the configured `serial:` where there is one, the
        path discovery found otherwise. A `device_id:` section therefore has no
        port until discovery finds it, and it still belongs in the list rather
        than vanishing, because a screen that cannot be found is the one to
        say so about. See knomi_serial's docs/protocol.md, "The device map".

        Every live field is None against a module too old for `get_status`, so
        None means unknown here, never False.
        """
        configured_id = values.get("device_id") or None
        configured_path = values.get("port") or None
        # A symlink is the point: the whole scheme is "a stable name udev keeps
        # pointed at the right tty". A discovered path is already a real tty, so
        # for it this is only an existence check.
        resolved = None
        if configured_path:
            try:
                if os.path.exists(configured_path):
                    resolved = os.path.realpath(configured_path)
            except OSError:
                resolved = None
        # False means the screen speaks a different wire protocol than the
        # module expects: the one authoritative "this needs reflashing" a
        # screen can produce, because the device itself declares it.
        compatible = values.get("protocol_match")
        answering = values.get("device_online")
        return ListedDevice(
            # A `device_id:` section is addressed by the id burned into its chip:
            # the discovered path changes when the screen moves socket.
            id=configured_id or configured_path,
            section=section,
            label=section.split(" ", 1)[1] if " " in section else section,
            configured_id=configured_id,
            # Six hex characters from the low three bytes of the screen's eFuse
            # MAC. Burned in, so it survives a reflash, an erase_flash and a move
            # to another socket: the only stable name a KNOMI has, because the
            # CH340K in front of it reports no USB serial. Lowered because the
            # vendor's own docs say not to depend on its case.
            reported_id=(values.get("reported_id") or "").lower() or None,
            configured_path=configured_path,
            resolved_path=resolved,
            version=values.get("firmware_version"),
            compatible=compatible if isinstance(compatible, bool) else None,
            answering=answering if isinstance(answering, bool) else None,
            raw=dict(values),
        )

    def extras(self, devices: Sequence[ListedDevice]) -> list[Extra]:
        """The klippy module's version: one module serves every screen of a
        type, so the first screen that reports one speaks for them all."""
        version = next(
            (d.raw.get("module_version") for d in devices if d.raw.get("module_version")),
            None,
        )
        if version is None:
            return []
        return [Extra("helper", self.name, "module_version", "Module", str(version))]

    def devices_note(self, *, reachable: bool) -> str:
        if not reachable:
            return "Could not reach Klipper to check for screens."
        return f"No screens found under [{self.klipper_prefix} ...]."


__all__ = ["KnomiSerialHelper"]
