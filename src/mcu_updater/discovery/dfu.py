"""DFU - a bare STM32's ROM bootloader.

Unlike Klipper and Katapult, DFU has no ``/dev/serial/by-id`` entry at all -
it is queried directly via ``dfu-util -l``. See :func:`dfu_devices`.
"""

from __future__ import annotations

import re
import subprocess

from ..build import Reporter, null_reporter
from ..errors import DfuPermissionError, ToolMissingError

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


def dfu_serial_for(serial: str) -> str | None:
    """The shorter DFU serial derived from a board's canonical running identity.

    An STM32 reports a *different* serial in DFU than it does running firmware,
    and the DFU one is **derived, not truncated** - which is why they look
    unrelated:

        27000E000551343438333339        canonical running identity
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
    uid = serial
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

    # Deduplicate by what identifies this physical attachment. The USB path is
    # the port the scan actually found and remains the same across one board's
    # altsettings. Prefer it over the ROM serial: some STM32s report the
    # non-unique placeholder FFFFFFFEFFFF, so a serial-first key can collapse
    # two physical boards into one apparent device. dict preserves insertion
    # order, so the first line for each device is the one reported.
    devices: dict[str, dict[str, str | None]] = {}
    for raw in out.splitlines():
        line = raw.strip()
        match = _DFU_LINE_RE.search(line)
        if match is None:
            continue
        key = (
            match.group("path")
            or match.group("devnum")
            or match.group("serial")
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


def dfu_selector(device: dict) -> list[str]:
    """dfu-util arguments pinning a write to one physical board.

    The bus path is first because it names the physical port found by this scan.
    A well-formed STM32 USB serial survives a replug, but it is not safe as the
    first selector: some ROMs report the same FFFFFFFEFFFF placeholder on every
    die. devnum changes every time the device enumerates, so it remains the last
    fallback. All three beat targeting the VID:PID alone, which picks whichever
    board answers first.
    """
    if device.get("path"):
        return ["-p", str(device["path"])]
    if device.get("serial"):
        return ["-S", str(device["serial"])]
    if device.get("devnum"):
        return ["-n", str(device["devnum"])]
    return []
