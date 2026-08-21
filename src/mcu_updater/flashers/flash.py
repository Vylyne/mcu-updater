"""Flashing boards.

Two paths:

* **katapult** - the normal case. A board already running Klipper is asked to
  reboot into its bootloader, waited for, then written via katapult's
  ``flashtool.py``.
* **dfu-util** - the first-ever flash of a bare STM32, which has no bootloader
  yet to speak flashtool's protocol.

Cancellation is deliberately *not* plumbed into the write step. Interrupting
``flashtool -f`` part-way through leaves a board with half a firmware image.
Callers cancel between devices, never during one.

flashtool *does* have a safe way to ask first: ``-s``/``--status`` runs the
same bootloader handshake as ``-f`` - including the "Application Start:" line
this module checks - but skips the send/verify/finish steps, so nothing is
written and the board is left exactly as it was found. ``flash_katapult`` uses
it to refuse a mismatched write before ``-f`` is ever called
(``_verify_offset_before_write``), then checks again from what ``-f`` itself
reported, as a second line of defence against the board changing in between
(``_report_offset_mismatch``).
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Any

from .. import firmware
from ..build import Reporter, null_reporter, run_streamed
from ..devices import (
    KATAPULT_FW_NAME,
    STATE_BOOTSEL,
    STATE_DFU,
    STATE_KATAPULT,
    BusDevice,
    dfu_devices,
    dfu_selector,
    expected_path,
    wait_for_device,
    wait_for_new_device,
)
from ..discovery.confirm import confirm
from ..discovery.registry import SOURCES
from ..discovery.spec import Confidence
from ..errors import (
    AmbiguousDfuError,
    DeviceNotFoundError,
    FlashError,
    OffsetMismatchError,
    OperationCancelled,
    ToolMissingError,
)
from ..paths import HUMAN_ACTION_TIMEOUT, REENUMERATE_TIMEOUT, Paths
from ..settings import Settings
from .batch import PlainContext
from .spec import Bench

DFU_VID_PID = "0483:df11"


def find_flashtool(paths: Paths, settings: Settings) -> str:
    """Katapult's flashtool.py: the configured path, or the ~/katapult convention."""
    if settings.flashtool_path:
        return firmware.expand_home(settings.flashtool_path, paths.home)
    return paths.flashtool


def device_for(
    bench: Bench, chipset: str, serial: str
) -> tuple[BusDevice | None, Confidence | None, str | None]:
    """Look up one board via `discovery.confirm`, in the shape `esptool.port_for`
    already uses for displays: `(device, confidence, refusal reason)`. `device`
    is `None` and `reason` is set when the board cannot be confirmed present -
    never raises, same as `port_for`.

    A `UNIQUE_BUS_ID` by-id sighting is the confirmed-at-write-time counterpart
    to a display's `ANSWERED` listen-pass sighting: die-derived, not remembered.
    """
    sightings = confirm(bench, sources=SOURCES)
    found = sightings.get(serial)
    if found is None:
        return None, None, (
            f"no device found for {serial} (looked for chipset {chipset} "
            f"with that serial under any firmware name, e.g. "
            f"{expected_path('*', chipset, serial)}). Is it plugged in?"
        )
    sighting, confidence = found
    seen_chipset = sighting.detail.get("chipset")
    if seen_chipset != chipset:
        return None, None, (
            f"a device answered as {serial} but reports chipset "
            f"{seen_chipset!r}, not {chipset!r} - refusing to flash a "
            f"mismatched board."
        )
    dev = BusDevice(
        fw=str(sighting.detail.get("fw", "")),
        chipset=chipset,
        serial=serial,
        path=sighting.address,
    )
    return dev, confidence, None


def flash_katapult(
    paths: Paths,
    settings: Settings,
    mcu_type: str,
    chipset: str,
    serial: str,
    fw_bin: str | None = None,
    *,
    fw: str | None = None,
    reporter: Reporter = null_reporter,
    timeout: float = REENUMERATE_TIMEOUT,
    force: bool = False,
) -> None:
    """Flash one board through katapult's flashtool.py.

    If the board is currently running Klipper rather than sitting in its
    bootloader, this requests the bootloader first and waits for it to
    re-enumerate - flashtool's documented two-step process for devices it can't
    put into bootloader mode itself.

    `force` overrides the offset checks below (downgrading a refusal to a
    logged warning) for the case where the operator genuinely knows better.

    Raises on any failure; returns None on success.
    """
    flashtool = find_flashtool(paths, settings)
    if not os.path.exists(flashtool):
        raise ToolMissingError(
            f"flashtool.py not found at {flashtool}. Is katapult installed?",
            tool="flashtool.py",
            path=flashtool,
        )

    # Which family this board *runs*. Named by the caller because only it has
    # the McuType; klipper is the default every type had before firmware
    # became a declared list. Getting this wrong writes one firmware at a
    # board expecting another, so it is not inferred from anything.
    fw = fw or "klipper"
    if fw_bin is None:
        fw_bin = paths.bin_file(mcu_type, fw)
    if not os.path.exists(fw_bin):
        raise FlashError(
            f"firmware binary not found at {fw_bin}. Build it first.",
            type=mcu_type,
            serial=serial,
            path=fw_bin,
        )

    # Confirmed at write time, not just remembered - the same ledger Step 26
    # gave a display. `device_for` reduces chipset+serial to at most one
    # sighting; state (bootloader or running) replaces the old two-call
    # katapult-then-unconstrained lookup, via the bootloader-predicate rule
    # (`discovery.spec.state_for_firmware`) rather than a fixed firmware name -
    # so a fork's own name (e.g. Cartographer's `usb-Cartographer_...`) is
    # still found, the fix `7bbf152` shipped for the old two-call shape.
    bench = Bench(paths=paths, settings=settings, controller=_no_services)
    dev, confidence, reason = device_for(bench, chipset, serial)
    if reason is not None:
        raise DeviceNotFoundError(
            reason,
            type=mcu_type,
            serial=serial,
            chipset=chipset,
        )
    assert dev is not None  # device_for: reason is None iff dev is not None
    if dev.state != STATE_KATAPULT:
        running = dev
        reporter("info", f"{serial} is running {running.fw} - requesting bootloader...")
        run_streamed(
            [sys.executable, flashtool, "-d", running.path, "-r"],
            cwd=paths.home,
            reporter=reporter,
            dry_run=settings.dry_run,
            fake_delay=0.0,
        )
        if settings.dry_run:
            # Nothing actually rebooted, so there is nothing to wait for. Carry
            # on with the klipper node standing in rather than returning early,
            # so a rehearsal still covers the write step it is meant to rehearse.
            reporter("info", f"[dry-run] would wait for {serial} to re-enumerate as Katapult")
            dev = running
        else:
            reporter("info", f"Waiting for {serial} to re-enumerate as a Katapult device...")
            # settle: udev creating the symlink is not atomic with the device
            # being openable, so flashing the instant it appears can race.
            dev = wait_for_device(
                paths, chipset, serial, KATAPULT_FW_NAME, timeout=timeout, settle=0.5
            )

    side: dict = {}
    if not settings.dry_run:
        from ..build import FlashLog, git_head, read_sidecar

        side = read_sidecar(paths, mcu_type, fw) or {}
        _verify_offset_before_write(
            paths, settings, flashtool, dev, fw_bin, mcu_type, fw, serial, side, reporter, force
        )

    # Captured as well as forwarded: used for the post-write check below, a
    # second line of defence against the board changing between the probe
    # above and the write here.
    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    reporter("info", f"Flashing {serial} ({mcu_type}) via {dev.path}...")
    rc = run_streamed(
        [sys.executable, flashtool, "-d", dev.path, "-f", fw_bin],
        cwd=paths.home,
        reporter=capture,
        # No cancel: see module docstring. Never interrupt a write.
        dry_run=settings.dry_run,
        fake_delay=0.0,
    )
    if rc != 0:
        raise FlashError(
            f"flashtool.py failed for {serial} (exit {rc}).",
            type=mcu_type,
            serial=serial,
            returncode=rc,
        )

    # Note which binary this board now holds. A board only ever reports its
    # application commit, so without this record two builds from the same commit -
    # a changed .config, an edited makefile-patch source - are indistinguishable,
    # and "flash only the stale ones" would skip exactly the boards a patch
    # change affected.
    if not settings.dry_run:
        # side and the FlashLog/git_head imports came from the pre-write block
        # above, which runs under the same `not settings.dry_run` condition.
        FlashLog(paths).record(
            serial,
            mcu_type=mcu_type,
            fw=fw,
            bin_sha256=side.get("bin_sha256"),
            fw_sha=side.get("fw_sha")
            or git_head(firmware.resolve(paths, fw).source_dir(paths)),
            confidence=confidence.reason if confidence is not None else None,
            version=side.get("version"),
        )

        _report_offset_mismatch(reporter, serial, mcu_type, fw, side, transcript)

    reporter("info", f"Flashed {serial} successfully.")


#: Katapult's own words, from flashtool.py's handshake with the bootloader:
#: ``f"Application Start: 0x{self.app_start_addr:4X}\n"``. That format is a
#: *minimum* width, not zero-padded, so a short address can print with a space
#: after ``0x`` (e.g. ``0x 800``) - real STM32 addresses never need it, but the
#: pattern tolerates it rather than assuming ``0x08000000``-style padding.
_APP_START_RE = re.compile(r"Application Start:\s*0x\s*([0-9A-Fa-f]+)")


def _parse_application_start(transcript: list[str]) -> int | None:
    """The board's own launch address, from flashtool's handshake output."""
    match = _APP_START_RE.search("\n".join(transcript))
    if match is None:
        return None
    try:
        return int(match.group(1), 16)
    except ValueError:
        return None


def _verify_offset_before_write(
    paths: Paths,
    settings: Settings,
    flashtool: str,
    dev: BusDevice,
    fw_bin: str,
    mcu_type: str,
    fw: str,
    serial: str,
    side: dict,
    reporter: Reporter,
    force: bool,
) -> None:
    """Refuse a mismatched write before ``-f`` is ever called.

    Uses flashtool's ``-s``/``--status`` probe: it runs the same
    ``connect_btl()`` handshake as ``-f`` - including the "Application Start:"
    line this checks - but skips send/verify/finish, so nothing is written and
    the board is left exactly as found. Safe to act on, unlike ``-f``'s own
    output (see module docstring).
    """
    app_address = side.get("app_address")
    if app_address is None:
        # Nothing of ours to compare against - an older build, or a tree that
        # does not define the symbol. Skip the probe; it could not mean anything.
        return

    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    # -f names the real binary even though -s never sends it: flashtool checks
    # a klipper.bin's own embedded MCU identity against what katapult reports,
    # but only when it can find one there, and only for the actual firmware
    # about to be written.
    reporter("info", f"Checking {serial}'s bootloader offset before writing...")
    rc = run_streamed(
        [sys.executable, flashtool, "-d", dev.path, "-f", fw_bin, "-s"],
        cwd=paths.home,
        reporter=capture,
        dry_run=settings.dry_run,
        fake_delay=0.0,
    )
    if rc != 0:
        raise FlashError(
            f"flashtool.py's status check failed for {serial} (exit {rc}). Not "
            f"attempting to write.",
            type=mcu_type,
            serial=serial,
            returncode=rc,
        )

    board_address = _parse_application_start(transcript)
    if board_address is None:
        message = (
            f"could not read {serial}'s own Application Start address from "
            f"flashtool's status check, so whether it would boot the {fw} "
            f"firmware about to be written could not be verified."
        )
        if force:
            reporter("warn", f"{message} Proceeding anyway (forced).")
        else:
            raise OffsetMismatchError(message, type=mcu_type, serial=serial, fw=fw)
    elif app_address != board_address:
        message = (
            f"{serial} ({mcu_type}) is about to be flashed with {fw} linked to "
            f"run at {app_address:#x}, but its bootloader reports it will jump "
            f"to {board_address:#x}. It would not come back running {fw} - "
            f"re-derive {fw}'s bootloader offset, or check what bootloader is "
            f"actually on this board."
        )
        if force:
            reporter("warn", f"{message} Proceeding anyway (forced).")
        else:
            raise OffsetMismatchError(
                message,
                type=mcu_type,
                serial=serial,
                fw=fw,
                app_address=f"{app_address:#x}",
                board_address=f"{board_address:#x}",
            )


def _report_offset_mismatch(
    reporter: Reporter,
    serial: str,
    mcu_type: str,
    fw: str,
    side: dict,
    transcript: list[str],
) -> None:
    """Second line of defence, after the write - covers the board changing
    between the probe above and the write itself, or an older caller that
    passes force=True through the probe. The write has already happened
    either way, so this can only tell the operator, as soon as it is known,
    that the board just written to will not come back running what was just
    flashed - rather than leaving them to work that out from a Klipper MCU
    that never connects.
    """
    app_address = side.get("app_address")
    if app_address is None:
        # Nothing of ours to compare against - an older build, or a tree that
        # does not define the symbol. Not a finding, so not reported.
        return
    board_address = _parse_application_start(transcript)
    if board_address is None:
        reporter(
            "warn",
            f"could not read {serial}'s own Application Start address from "
            f"flashtool's output, so whether it will boot the {fw} firmware "
            f"just written could not be verified.",
        )
    elif app_address != board_address:
        reporter(
            "error",
            f"{serial} ({mcu_type}) was just flashed with {fw} linked to run at "
            f"{app_address:#x}, but its bootloader reports it will jump to "
            f"{board_address:#x}. It will not come back running {fw} - "
            f"re-derive {fw}'s bootloader offset, or check what bootloader is "
            f"actually on this board.",
        )


# --------------------------------------------------------------------------
# DFU (first-time bootloader install on a bare STM32)
# --------------------------------------------------------------------------


def list_dfu_devices(*, reporter: Reporter = null_reporter) -> list[str]:
    """The raw `dfu-util -l` line per device. See `devices.dfu_devices` for the
    fields; enumeration itself lives there now, alongside the rest of the bus
    discovery this module used to duplicate."""
    return [str(d["raw"]) for d in dfu_devices(reporter=reporter)]


#: dfu-util's own statement that the image is on the board.
_DFU_WRITE_OK_RE = re.compile(r"file downloaded successfully|download done", re.IGNORECASE)

#: The failure that follows a successful `:leave`, across dfu-util versions. The
#: device is gone by design, so the request it is complaining about was never
#: going to be answered.
_DFU_LEAVE_NOISE_RE = re.compile(
    r"error during download get_status"
    r"|unable to read dfu status after completion"
    r"|lost device after",
    re.IGNORECASE,
)


def _dfu_left_successfully(transcript: list[str]) -> bool:
    """Did the write succeed and only the post-`leave` status read fail?

    Requires *both* signals. An unrecognised error after a successful download
    still fails: reporting a bricked board as flashed is far worse than the false
    failure this exists to stop, and the caller's re-enumeration wait is what
    ultimately confirms it either way.
    """
    text = "\n".join(transcript)
    return bool(_DFU_WRITE_OK_RE.search(text)) and bool(_DFU_LEAVE_NOISE_RE.search(text))


def wait_for_dfu(
    *,
    reporter: Reporter = null_reporter,
    timeout: float = HUMAN_ACTION_TIMEOUT,
    poll: float = 1.0,
    cancel: threading.Event | None = None,
) -> list[str]:
    """Poll for a DFU device to appear, giving a human time to hold BOOT0."""
    deadline = time.monotonic() + timeout
    reporter("info", "Waiting for a device in DFU mode (hold BOOT0 and replug)...")
    while True:
        found = list_dfu_devices(reporter=reporter)
        if found:
            return found
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("cancelled while waiting for a DFU device")
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)


def flash_dfu_stm32(
    paths: Paths,
    settings: Settings,
    fw_bin: str,
    *,
    reporter: Reporter = null_reporter,
    target_serial: str | None = None,
) -> None:
    """Write a .bin to an STM32 sitting in DFU mode.

    With several boards in DFU, `target_serial` says which one; without it, this
    refuses rather than guessing. The original targeted ``0483:df11``
    unconditionally, so with two boards attached - or an unrelated STM32 dev board
    plugged in - it flashed whichever answered first.

    Note `-a 0` is deliberately by number and not by name. It looks like the
    fragile choice, and is in fact the robust one: an STM32G0B1 reports the *same*
    name ("@Internal Flash /0x08000000/64*02Kg") for all three of its altsettings,
    so matching on the name would be the ambiguous option.
    """
    if not os.path.exists(fw_bin):
        raise FlashError(f"firmware binary not found at {fw_bin}.", path=fw_bin)

    reporter("info", "Looking for an STM32 device in DFU mode via dfu-util...")
    devices = dfu_devices(reporter=reporter)
    for device in devices:
        reporter("info", f"  {device['raw']}")
    found = [str(d["raw"]) for d in devices]

    if not found:
        raise DeviceNotFoundError(
            "no DFU device detected. Hold BOOT0 (or fit the boot jumper) and replug "
            "the board, then try again."
        )

    if target_serial is not None:
        matches = [d for d in devices if d.get("serial") == target_serial]
        if not matches:
            raise DeviceNotFoundError(
                f"no DFU device with serial {target_serial} is attached. It may have "
                f"been unplugged, or left DFU mode.",
                serial=target_serial,
            )
        chosen = matches[0]
    elif len(devices) > 1:
        raise AmbiguousDfuError(
            f"{len(devices)} devices are in DFU mode - refusing to guess which one to "
            f"flash. Name one by its serial, or unplug all but the target board.",
            devices=found,
        )
    else:
        chosen = devices[0]

    # Always pin the write, even for a lone board: between the scan above and the
    # command below, a second board could be jumpered and plugged in.
    selector = dfu_selector(chosen)
    if selector:
        reporter("info", f"Targeting {selector[0]} {selector[1]}")

    reporter("info", "DFU device found. Flashing via dfu-util...")

    # Keep the output as well as the exit code: dfu-util's own words are the only
    # way to tell a real failure from the expected one below.
    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    rc = run_streamed(
        [
            "dfu-util",
            "-a",
            "0",
            "-d",
            DFU_VID_PID,
            *selector,
            "-D",
            fw_bin,
            "-s",
            "0x08000000:force:mass-erase:leave",
        ],
        cwd=paths.home,
        reporter=capture,
        dry_run=settings.dry_run,
        fake_delay=0.0,
    )
    if rc != 0:
        if _dfu_left_successfully(transcript):
            # Expected, not a failure. `:leave` asks the STM32 to exit DFU and
            # start the application it just received, so the device detaches
            # before dfu-util can read its status one last time - and dfu-util
            # exits 74 (EX_IOERR) over a request that could not possibly succeed.
            # The write itself already reported "File downloaded successfully".
            #
            # Deliberately not treated as fatal rather than suppressed: the caller
            # then waits for the board to re-enumerate as Katapult, which is the
            # real verdict on whether this worked. Raising here aborted *before*
            # that check, turning a good flash into a reported failure.
            reporter(
                "warn",
                f"dfu-util exited {rc} on its post-'leave' status read. That is "
                f"expected - the board detached to boot the new firmware. The "
                f"download itself succeeded.",
            )
        else:
            raise FlashError(f"dfu-util flashing failed (exit {rc}).", returncode=rc)
    reporter("info", "Flash command sent. Device should reboot into Katapult shortly.")


def flash_initial_bootloader(
    paths: Paths,
    settings: Settings,
    chipset: str,
    fw_bin: str,
    *,
    uf2_bin: str | None = None,
    reporter: Reporter = null_reporter,
    target_serial: str | None = None,
) -> None:
    """Install a first bootloader on a bare board of this chipset.

    Which ROM bootloader a factory-bare board of this chipset speaks is a
    single fact about the silicon, not a lookup table: every STM32 answers DFU,
    every RP2040 answers BOOTSEL. `flashers.select_for` is the actual dispatch -
    driven through the same `Flasher` protocol a batch uses, so a route added
    for this path is a route a batch could take too, with nothing here to edit
    when it lands.

    `uf2_bin` is separate from `fw_bin`: BOOTSEL mass storage only accepts a
    `.uf2` - a `.bin` copied there is silently ignored - and a build only
    produces one when the tree does. DFU never looks at it; a caller flashing
    an STM32 can leave it unset.
    """
    from .. import flashers

    state = STATE_BOOTSEL if chipset.startswith("rp2040") else STATE_DFU
    flasher = flashers.select_for(chipset, state)

    if state == STATE_BOOTSEL:
        if uf2_bin is None:
            raise FlashError(
                f"no .uf2 was built for {chipset}. BOOTSEL mass storage ignores "
                f"a .bin - build again once the tree produces one.",
                chipset=chipset,
            )
        target = flashers.bootsel.target_for(uf2_bin, chipset=chipset, paths=paths)
    else:
        target = flashers.dfu_util.target_for(
            fw_bin, chipset=chipset, dfu_serial=target_serial
        )

    bench = flashers.Bench(
        paths=paths,
        settings=settings,
        # Nothing on this path touches a service: the board is in its ROM
        # bootloader (DFU or BOOTSEL), not on the Klipper bus, which is what
        # both flashers' `needs_klipper_stopped: False` says.
        controller=_no_services,
    )
    with flasher.prepared(bench, [target], PlainContext(reporter)) as session:
        flasher.write(bench, session, target, PlainContext(reporter))
        flasher.settled(bench, target, PlainContext(reporter))



def _no_services(name: str | None = None) -> Any:
    raise AssertionError(
        "the bootstrap flash path controls no services; "
        f"something asked for {name!r}"
    )


def adoptable_devices(
    paths: Paths,
    known_serials: set,
    chipset: str,
    *,
    timeout: float = REENUMERATE_TIMEOUT,
) -> list[BusDevice]:
    """Katapult devices that appeared and aren't tracked yet.

    Replaces the original's fixed `time.sleep(3)` with a real poll.
    """
    return wait_for_new_device(
        paths,
        known_serials,
        fw=KATAPULT_FW_NAME,
        chipset=chipset,
        timeout=timeout,
    )
