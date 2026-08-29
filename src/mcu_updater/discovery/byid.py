"""``/dev/serial/by-id`` - Klipper and Katapult, however they enumerate.

Entries look like ``usb-<fw>_<chipset>_<serial>``, e.g.::

    usb-Klipper_stm32g0b1xx_290055001850304158373620-if00
    usb-katapult_stm32f072xb_4C0033000957465331323720-if00

The firmware component's capitalisation is **not** dependable - it has been
observed as both ``Klipper`` and ``klipper`` depending on the board and klipper
version. The original code compared it case-insensitively when *discovering*
devices but rebuilt the path with an exact-case f-string when *flashing*, so a
board enumerating as lowercase was found and then declared missing.

Everything here therefore matches case-insensitively and returns the real
on-disk path rather than a reconstructed one.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..errors import BootloaderTimeoutError, OperationCancelled
from ..paths import REENUMERATE_TIMEOUT, Paths

if TYPE_CHECKING:
    from ..flashers.spec import Bench
    from .spec import Sighting

_PREFIX = "usb-"

#: Katapult was called CanBoot before it was renamed; older bootloaders still
#: enumerate under the old name.
KATAPULT_NAMES = ("katapult", "canboot")
KLIPPER_NAMES = ("klipper",)

#: Display-only. Never use these to build a path you then test for existence.
KLIPPER_FW_NAME = "Klipper"
KATAPULT_FW_NAME = "katapult"

STATE_KLIPPER = "klipper"
STATE_KATAPULT = "katapult"
STATE_OFFLINE = "offline"
#: A bare STM32's ROM bootloader - no application, no Katapult, nothing on
#: `/dev/serial/by-id` at all. Discovered via `dfu-util -l`, not `scan()`.
STATE_DFU = "dfu"
#: An RP2040's ROM bootloader, exposed as a mounted mass-storage volume rather
#: than a serial device. Discovered via `bootsel_scan()`, not `scan()`.
STATE_BOOTSEL = "bootsel"
#: An ESP32's ROM bootloader. esptool enters and leaves it itself over the
#: normal serial port (RTS/DTR strapping), so unlike DFU and BOOTSEL this has
#: no bus presence of its own to scan for - the constant exists so a flasher's
#: declared `states` can name it.
STATE_ESP_ROM = "esp_rom"


@dataclasses.dataclass(frozen=True)
class BusDevice:
    fw: str
    chipset: str
    serial: str
    path: str

    @property
    def is_klipper(self) -> bool:
        return self.fw.lower() in KLIPPER_NAMES

    @property
    def is_katapult(self) -> bool:
        return self.fw.lower() in KATAPULT_NAMES

    @property
    def state(self) -> str:
        if self.is_klipper:
            return STATE_KLIPPER
        if self.is_katapult:
            return STATE_KATAPULT
        return self.fw.lower()

    @property
    def is_mcu(self) -> bool:
        """Could this plausibly be a board we manage firmware for?

        The by-id name format is generic enough that anything with two
        underscores parses. A CH340 serial adapter enumerates as
        ``usb-1a86_USB_Serial-if00``, which splits into fw=``1a86``,
        chipset=``USB``, serial=``Serial-if00`` - a perfectly well-formed
        `BusDevice` that is not a board at all.

        That mattered once the panel grew a one-tap "track this" next to the
        untracked list: a Knomi display sitting in that list is one tap from
        being added to the registry and having Klipper firmware built and
        flashed at it.

        Katapult counts, deliberately and importantly. A board in its bootloader
        is the single most likely thing to want adopting - that is exactly what
        `add-mcu` leaves behind on success.
        """
        return self.is_klipper or self.is_katapult


class Byid:
    """The by-id scan, as a `discovery.spec.Source`.

    A by-id serial is die-derived - the kernel names it, not an application
    that has to be running and cooperative to answer - so a match here is
    `UNIQUE_BUS_ID`, the same strength `discovery.confirm` gives a knomi
    display that just answered a listen pass. Deferred import of
    `discovery.spec`: that module imports `.. devices`, which re-exports this
    module, so importing it at module scope here would be a cycle - the same
    shape Step 24 already hit and resolved for `dfu_selector`.
    """

    name = "byid"
    label = "USB serial by-id"
    states: tuple[str, ...] = (STATE_KLIPPER, STATE_KATAPULT)
    #: Reads what udev has already created; opens nothing, so unlike the
    #: knomi listen pass it never contends with Klipper or the watcher for a
    #: port.
    needs_ports_free = False

    def sight(self, bench: Bench) -> list[Sighting]:
        from .spec import Sighting as _Sighting
        from .spec import state_for_firmware

        return [
            _Sighting(
                id=dev.serial,
                address=dev.path,
                state=state_for_firmware(dev.fw),
                source=self.name,
                detail={"chipset": dev.chipset, "fw": dev.fw},
            )
            for dev in scan(bench.paths)
        ]


def parse_entry(name: str, directory: str) -> BusDevice | None:
    """Parse one by-id filename. Returns None if it isn't a recognisable device.

    The serial is always the last underscore-delimited token
    (`rsplit("_", 1)`), which is safe regardless of how many words precede it.
    What remains (`name_blob`) is split into fw/chipset on its *first*
    underscore, but only when it has at most one - i.e. at most two words -
    which is the convention `usb-<fw>_<chipset>_<serial>` actually promises.
    That covers every renamed Klipper fork too (e.g. Cartographer's own
    `usb-Cartographer_stm32g431xx_<serial>`, `7bbf152`'s fix): the split isn't
    gated on recognising the firmware name, because plenty of boards this tool
    manages run firmware it has never heard of and still follow the
    convention. A blob with *two or more* underscores (three or more words,
    e.g. `usb-Raspberry_Pi_Pico_<serial>`) is genuinely ambiguous - there is no
    reliable place to cut a vendor/product string into "name" and "chipset" -
    so the whole blob becomes `fw` with no `chipset` rather than guessing.
    """
    if not name.startswith(_PREFIX):
        return None
    rest = name[len(_PREFIX) :]
    if "_" not in rest:
        return None
    name_blob, serial = rest.rsplit("_", 1)
    if not serial or not name_blob:
        return None
    if name_blob.count("_") <= 1:
        fw, _, chipset = name_blob.partition("_")
    else:
        fw, chipset = name_blob, ""
    return BusDevice(fw=fw, chipset=chipset, serial=serial, path=os.path.join(directory, name))


def scan(paths: Paths) -> list[BusDevice]:
    """Every parseable device currently on the bus. Empty list if there's no bus."""
    directory = paths.serial_by_id
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        dev = parse_entry(name, directory)
        if dev is not None:
            out.append(dev)
    return out


def find_device(
    paths: Paths,
    chipset: str,
    serial: str,
    fw: str | None = None,
) -> BusDevice | None:
    """Locate one board by chipset+serial, optionally constrained to a firmware.

    `fw` may be a single name or one of the KLIPPER_NAMES/KATAPULT_NAMES
    groups; matching is case-insensitive. Returns the device with its actual
    path, or None.
    """
    wanted: tuple[str, ...] | None
    if fw is None:
        wanted = None
    elif fw.lower() in KATAPULT_NAMES:
        wanted = KATAPULT_NAMES
    elif fw.lower() in KLIPPER_NAMES:
        wanted = KLIPPER_NAMES
    else:
        wanted = (fw.lower(),)

    for dev in scan(paths):
        if dev.serial != serial:
            continue
        if chipset and dev.chipset != chipset:
            continue
        if wanted is not None and dev.fw.lower() not in wanted:
            continue
        return dev
    return None


def device_state(paths: Paths, chipset: str, serial: str) -> tuple[str, str | None]:
    """(state, path) for a tracked serial: klipper, katapult, or offline."""
    dev = find_device(paths, chipset, serial)
    if dev is None:
        return STATE_OFFLINE, None
    return dev.state, dev.path


def find_untracked(
    paths: Paths,
    known_serials: Iterable[str],
    *,
    fw: str | None = None,
    chipset: str | None = None,
) -> list[BusDevice]:
    """Boards on the bus whose serial isn't tracked under any MCU type.

    Filtered by `is_mcu`, so a CH340 behind a Knomi never appears here. Every
    caller is asking "what could I adopt?" - the CLI status listing, both TUI
    pickers, and the add-mcu wait - and a display offered as an adoptable board
    is one keystroke from being tracked and having Klipper built and flashed at
    it.

    The agent already filtered this way (`bus_scan` exposes `is_mcu` and the
    adoptable list applies it); the CLI and the TUI did not, so the two front
    ends disagreed about what counted as a board. That is the split
    `validate_type_name` avoids by living in the model, and this now does too.
    """
    known = set(known_serials)
    wanted_group: tuple[str, ...] | None = None
    if fw is not None:
        if fw.lower() in KATAPULT_NAMES:
            wanted_group = KATAPULT_NAMES
        elif fw.lower() in KLIPPER_NAMES:
            wanted_group = KLIPPER_NAMES
        else:
            wanted_group = (fw.lower(),)

    out = []
    for dev in scan(paths):
        if not dev.is_mcu:
            continue
        if dev.serial in known:
            continue
        if wanted_group is not None and dev.fw.lower() not in wanted_group:
            continue
        if chipset and dev.chipset != chipset:
            continue
        out.append(dev)
    return out


def expected_path(fw_name: str, chipset: str, serial: str) -> str:
    """Reconstructed path, for error messages only.

    Never test this for existence - the firmware name's case isn't reliable.
    Use find_device() instead.
    """
    return f"/dev/serial/by-id/{_PREFIX}{fw_name}_{chipset}_{serial}"


def _sleep_checked(seconds: float, cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.wait(seconds):
        raise OperationCancelled("cancelled while waiting for a device")
    elif cancel is None:
        time.sleep(seconds)


def wait_for_device(
    paths: Paths,
    chipset: str,
    serial: str,
    fw: str,
    *,
    timeout: float = REENUMERATE_TIMEOUT,
    poll: float = 0.5,
    settle: float = 0.0,
    cancel: threading.Event | None = None,
) -> BusDevice:
    """Poll until a specific board shows up under `fw`, or raise.

    `settle` adds a pause after first sighting: udev creating the symlink is not
    atomic with respect to the device being openable, so flashing immediately
    can race.
    """
    deadline = time.monotonic() + timeout
    while True:
        dev = find_device(paths, chipset, serial, fw=fw)
        if dev is not None:
            if settle:
                _sleep_checked(settle, cancel)
            return dev
        if time.monotonic() >= deadline:
            raise BootloaderTimeoutError(
                f"{serial} never appeared as a {fw} device within {timeout:.0f}s "
                f"(expected something like {expected_path(fw, chipset, serial)}).",
                serial=serial,
                chipset=chipset,
                fw=fw,
                timeout=timeout,
            )
        _sleep_checked(poll, cancel)


def wait_for_new_device(
    paths: Paths,
    baseline: Iterable[str],
    *,
    fw: str | None = None,
    chipset: str | None = None,
    timeout: float = REENUMERATE_TIMEOUT,
    poll: float = 0.5,
    settle: float = 1.0,
    cancel: threading.Event | None = None,
) -> list[BusDevice]:
    """Poll for any device not in `baseline` to appear.

    Replaces the original's fixed `time.sleep(3)` after a DFU flash. Returns the
    new devices, or an empty list on timeout - appearing is the interesting
    event, so a caller that gets nothing back reports it rather than erroring.
    """
    known = set(baseline)
    deadline = time.monotonic() + timeout
    while True:
        found = find_untracked(paths, known, fw=fw, chipset=chipset)
        if found:
            if settle:
                _sleep_checked(settle, cancel)
            return find_untracked(paths, known, fw=fw, chipset=chipset) or found
        if time.monotonic() >= deadline:
            return []
        _sleep_checked(poll, cancel)
