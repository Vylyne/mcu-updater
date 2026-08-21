"""Discovering boards, in whichever of the three shapes they enumerate as.

**Klipper and Katapult** show up under ``/dev/serial/by-id``, entries shaped
like ``usb-<fw>_<chipset>_<serial>``, e.g.::

    usb-Klipper_stm32g0b1xx_290055001850304158373620-if00
    usb-katapult_stm32f072xb_4C0033000957465331323720-if00

The firmware component's capitalisation is **not** dependable - it has been
observed as both ``Klipper`` and ``klipper`` depending on the board and klipper
version. The original code compared it case-insensitively when *discovering*
devices but rebuilt the path with an exact-case f-string when *flashing*, so a
board enumerating as lowercase was found and then declared missing.

Everything here therefore matches case-insensitively and returns the real
on-disk path rather than a reconstructed one.

**DFU** (a bare STM32's ROM bootloader) has no by-id entry at all - it is
queried directly via ``dfu-util -l``. See :func:`dfu_devices`.

**BOOTSEL** (an RP2040's ROM bootloader) has no by-id entry either, and no
``dfu-util`` protocol - it mounts as mass storage. See :func:`bootsel_scan`.
"""

from __future__ import annotations

import dataclasses
import glob
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterable

from .build import Reporter, null_reporter
from .errors import (
    BootloaderTimeoutError,
    DfuPermissionError,
    OperationCancelled,
    ToolMissingError,
)
from .paths import REENUMERATE_TIMEOUT, Paths

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


def parse_entry(name: str, directory: str) -> BusDevice | None:
    """Parse one by-id filename. Returns None if it isn't a recognisable device."""
    if not name.startswith(_PREFIX):
        return None
    parts = name[len(_PREFIX) :].split("_", 2)
    if len(parts) < 2:
        return None
    if len(parts) == 3:
        fw, chipset, serial = parts
    else:
        # Two-part name: no chipset component.
        fw, chipset, serial = parts[0], "", parts[1]
    if not serial:
        return None
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


def dfu_serial_for(serial: str) -> str | None:
    """What a board with this by-id serial calls itself while in DFU mode.

    An STM32 reports a *different* serial in DFU than it does running firmware,
    and the DFU one is **derived, not truncated** - which is why they look
    unrelated:

        27000E000551343438333339-if00   running Klipper or Katapult
        3941335F3434                    the same board in DFU

    ST's own `Get_SerialNum()` builds the DFU string from the 96-bit unique id:
    the first and third words are summed and printed as eight hex digits, then
    the **top** four nibbles of the second word are appended. Little-endian, as
    the words sit in memory.

    This matters because a board in DFU has no `/dev/serial/by-id` name, so
    without it there is nothing to connect `3941335F3434` to any board you know
    about - which is exactly the "which one is this?" problem that makes several
    boards in DFU at once so awkward.

    Returns None for anything that isn't a 96-bit id, rather than guessing.
    """
    uid = serial.split("-", 1)[0]
    if len(uid) != 24:
        return None
    try:
        raw = bytes.fromhex(uid)
    except ValueError:
        return None
    word0 = int.from_bytes(raw[0:4], "little")
    word1 = int.from_bytes(raw[4:8], "little")
    word2 = int.from_bytes(raw[8:12], "little")
    return f"{(word0 + word2) & 0xFFFFFFFF:08X}{word1 >> 16:04X}"


# --------------------------------------------------------------------------
# DFU (bare STM32 ROM bootloader) - a USB device, but not one that shows up
# under /dev/serial/by-id, so it needs `dfu-util -l` rather than `scan()`.
# --------------------------------------------------------------------------

#: `Found DFU: [0483:df11] ver=0200, devnum=51, cfg=1, intf=0, path="6-1.6.6.1.3",
#:  alt=0, name="@Internal Flash   /0x08000000/64*02Kg", serial="3941335F3434"`
#:
#: Matched on the VID:PID rather than the "Found DFU" prefix so a wording change
#: in dfu-util cannot silently reduce us to seeing nothing.
_DFU_LINE_RE = re.compile(
    r"\[(?P<vidpid>[0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]"
    r"(?=.*\bdevnum=(?P<devnum>\d+))?"
    r"(?=.*\bpath=\"(?P<path>[^\"]*)\")?"
    r"(?=.*\bserial=\"(?P<serial>[^\"]*)\")?"
)

#: libusb could see the device but not claim it. Almost always a missing udev
#: rule rather than anything the user did wrong with the boot jumper.
_DFU_DENIED_RE = re.compile(
    r"cannot open dfu device|LIBUSB_ERROR_ACCESS|insufficient permission|access denied",
    re.IGNORECASE,
)


def dfu_devices(*, reporter: Reporter = null_reporter) -> list[dict[str, str | None]]:
    """One entry per DFU *device* from `dfu-util -l`, parsed.

    Two things this must get right, both learned the hard way on real hardware:

    **One board is several lines.** dfu-util prints a line per DFU altsetting, so
    a single STM32 appears three times (alt=0/1/2) sharing one devnum, path and
    serial. Counting lines made the ambiguity guard refuse every single-board
    flash with "3 devices are in DFU mode".

    **"Nothing listed" is not the same as "nothing attached."** Without a udev
    rule, dfu-util prints ``Cannot open DFU device ... (LIBUSB_ERROR_ACCESS)``
    and no ``Found DFU`` line at all - so the old code reported "no DFU device
    detected. Hold BOOT0 and replug", sending the user to redo the one step that
    had actually worked. That case raises now.

    The fields are worth keeping rather than just the line: a DFU device has no
    ``/dev/serial/by-id`` name, so its USB serial and bus path are the only
    identity it has until it re-enumerates as Katapult.
    """
    try:
        res = subprocess.run(
            ["dfu-util", "-l"], capture_output=True, text=True, timeout=20
        )
    except FileNotFoundError as exc:
        raise ToolMissingError(
            "dfu-util is not installed. Try: sudo apt install dfu-util", tool="dfu-util"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolMissingError(f"could not run dfu-util: {exc}", tool="dfu-util") from exc

    out = (res.stdout or "") + (res.stderr or "")

    # Deduplicate by whatever identifies the physical board, in decreasing order
    # of trustworthiness. dict preserves insertion order, so the first line for
    # each device is the one reported.
    devices: dict[str, dict[str, str | None]] = {}
    for raw in out.splitlines():
        line = raw.strip()
        match = _DFU_LINE_RE.search(line)
        if match is None:
            continue
        key = (
            match.group("serial")
            or match.group("path")
            or match.group("devnum")
            or line  # nothing to group on: treat the line itself as the device
        )
        devices.setdefault(
            key,
            {
                "vidpid": match.group("vidpid"),
                "serial": match.group("serial"),
                "path": match.group("path"),
                "devnum": match.group("devnum"),
                "raw": line,
            },
        )

    if not devices and _DFU_DENIED_RE.search(out):
        raise DfuPermissionError(
            "dfu-util can see a board in DFU mode but cannot open it "
            "(LIBUSB_ERROR_ACCESS). The board and the boot jumper are fine - this "
            "is a permissions problem. Install the udev rule (install.sh offers "
            "to) or run the same command under sudo.",
            output=out.strip(),
        )

    return list(devices.values())


# --------------------------------------------------------------------------
# BOOTSEL (RP2040 ROM bootloader) - a mounted mass-storage volume, not a bus
# device at all, so neither `scan()` nor `dfu_devices()` can see it.
# --------------------------------------------------------------------------

#: udisks2's two automount conventions. The username segment is glob-matched
#: rather than assumed - "pi" has not been the default login on Raspberry Pi OS
#: since Bookworm, and this process does not otherwise know who is logged in.
DEFAULT_BOOTSEL_ROOT_GLOBS = ("/media/*", "/run/media/*")

#: The RP2040 boot ROM's own volume label.
BOOTSEL_VOLUME_NAME = "RPI-RP2"

#: Every UF2 bootloader publishes this file at the volume root. Required so an
#: unrelated drive that happens to share the label is never mistaken for one.
_BOOTSEL_MARKER = "INFO_UF2.TXT"


def bootsel_scan(paths: Paths) -> list[str]:
    """Mount path of every RPI-RP2 volume currently attached.

    Unlike DFU, a BOOTSEL board is not a USB device this process can query -
    it is a mounted filesystem, discovered the same way a human would: look
    for the drive. `MCU_UPDATER_FAKE_BUS` cannot stand in for that, so
    `paths.bootsel_root` is the equivalent seam: empty in production (search
    the standard automount locations), or one exact directory to look in
    instead - which a test points at a tmp_path, and which a real deployment
    with a non-standard automount setup could point at the real one.
    """
    if paths.bootsel_root:
        roots = [paths.bootsel_root]
    else:
        roots = [p for pattern in DEFAULT_BOOTSEL_ROOT_GLOBS for p in glob.glob(pattern)]

    found = []
    for root in sorted(roots):
        candidate = os.path.join(root, BOOTSEL_VOLUME_NAME)
        if os.path.isfile(os.path.join(candidate, _BOOTSEL_MARKER)):
            found.append(candidate)
    return found


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
