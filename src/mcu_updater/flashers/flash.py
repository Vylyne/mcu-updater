"""Flashing boards.

Two paths:

* **katapult** - the normal case. Katapult's ``flashtool.py`` transitions a
  board from its running firmware and writes it in one ``-f`` operation.
* **dfu-util** - the first-ever flash of a bare STM32, which has no bootloader
  yet to speak flashtool's protocol.

Cancellation is deliberately *not* plumbed into the write step. Interrupting
``flashtool -f`` part-way through leaves a board with half a firmware image.
Callers cancel between devices, never during one.

flashtool *does* have a safe way to ask first: ``-s``/``--status`` runs the
same bootloader handshake as ``-f`` - including the "Application Start:" line
this module checks - but skips the send/verify/finish steps, so **nothing is
written**. ``flash_katapult`` uses it to refuse a mismatched write before ``-f``
is ever called (``_verify_offset_before_write``), then checks again from what
``-f`` itself reported, as a second line of defence against the board changing
in between (``_report_offset_mismatch``).

Nothing written is not the same as *nothing changed*, and this cost a flash on
hardware (2026-09-05). The handshake ``-s`` shares with ``-f`` includes the
app-to-bootloader transition: against a board running Klipper, flashtool prints
"Requesting USB bootloader", the board re-enumerates as katapult, and ``-s`` -
having no send/verify/**finish** - never jumps it back. So the by-id path the
probe was called with is gone by the time ``-f`` runs, and the write dies with
"No Serial Device found". `_verify_offset_before_write` therefore re-resolves
the board after a probe that moved it, and hands back the path it is reachable
on *now*. Only the USB path has this problem: CAN addresses a board as
``-i <iface> -u <uuid>``, which survives the transition unchanged.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from typing import Any

from .. import firmware, profiles, uf2_erase
from ..build import Reporter, null_reporter, run_streamed
from ..devices import (
    STATE_BOOTSEL,
    STATE_DFU,
    STATE_KATAPULT,
    BusDevice,
    dfu_devices,
    dfu_selector,
    expected_path,
    wait_for_new_device,
)
from ..discovery.byid import Byid
from ..discovery.confirm import confirm
from ..discovery.registry import SOURCES
from ..discovery.spec import Confidence, Source, state_for_firmware
from ..errors import (
    AmbiguousDfuError,
    BootloaderTimeoutError,
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
    bench: Bench,
    chipset: str,
    serial: str,
    sources: Sequence[Source] = SOURCES,
) -> tuple[BusDevice | None, Confidence | None, str | None]:
    """Look up one board via `discovery.confirm`, in the shape `esptool.port_for`
    already uses for displays: `(device, confidence, refusal reason)`. `device`
    is `None` and `reason` is set when the board cannot be confirmed present -
    never raises, same as `port_for`.

    A `UNIQUE_BUS_ID` by-id sighting is the confirmed-at-write-time counterpart
    to a display's `ANSWERED` listen-pass sighting: die-derived, not remembered.

    `sources` narrows which sources are asked. The default is every one of
    them, for the single up-front lookup; a caller polling in a loop passes a
    narrower tuple, since `Listen` opens serial ports on every pass and doing
    that repeatedly at a board that is mid-re-enumeration is not free.
    """
    sightings = confirm(bench, sources=sources)
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

    ``-f`` performs the transition from a running application into Katapult
    itself, so there is no separate reboot request before the write.

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

    # Confirmed at write time, not just remembered - the same ledger a
    # display gets. `device_for` reduces chipset+serial to at most one
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
    side: dict = {}
    if not settings.dry_run:
        from ..build import FlashLog, git_head, read_sidecar

        side = read_sidecar(paths, mcu_type, fw) or {}
        # The probe can move the board: it returns the device to write to,
        # which is a *different* by-id path when it rebooted one into katapult.
        dev = _verify_offset_before_write(
            paths, settings, flashtool, bench, dev, fw_bin, mcu_type, chipset,
            fw, serial, side, reporter, force, timeout,
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

#: Katapult's own words when it finds a board running its application and has
#: to reboot it into the bootloader first: ``f"Requesting {req_type} bootloader
#: for {dev}..."`` (``USB``/``CAN``). ``-s`` has no finish step to jump it back,
#: so a probe that prints this leaves the board sitting in katapult - under a
#: different by-id path than the one it was called with. See the module
#: docstring; this is what makes the re-resolve below mandatory, not defensive.
_BOOTLOADER_REQUEST_RE = re.compile(r"Requesting \S+ bootloader", re.IGNORECASE)


def _parse_application_start(transcript: list[str]) -> int | None:
    """The board's own launch address, from flashtool's handshake output."""
    match = _APP_START_RE.search("\n".join(transcript))
    if match is None:
        return None
    try:
        return int(match.group(1), 16)
    except ValueError:
        return None


def _await_bootloader_device(
    bench: Bench, chipset: str, serial: str, timeout: float
) -> BusDevice | None:
    """Re-resolve `serial` after a probe left it sitting in katapult.

    Polls `device_for` rather than taking one reading, and insists on katapult
    state rather than accepting the first sighting: flashtool's own "Waiting
    for USB Reconnect..done" covers USB enumeration, not udev, so the
    bootloader's by-id symlink appears after the probe returns - and the
    board's now-dead Klipper symlink can still be present for a moment
    alongside it. Returns None on timeout; the caller decides what that means.

    The transcript's own "Katapult detected on /dev/ttyACM3" is deliberately
    not used: that is a raw tty, which is racy across a re-enumeration and, on
    a host with several boards, names a *different* board on the next boot.

    Only `Byid` is asked, not the full `SOURCES` sweep the one-off lookup in
    `flash_katapult` uses: a bootloader's by-id symlink is the only evidence
    that could answer this question anyway, and `Listen` would open serial
    ports on every one of these passes, at a bus that is mid-re-enumeration.
    """
    deadline = time.monotonic() + timeout
    while True:
        dev, _confidence, _reason = device_for(bench, chipset, serial, (Byid(),))
        if dev is not None and state_for_firmware(dev.fw) == STATE_KATAPULT:
            return dev
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _verify_offset_before_write(
    paths: Paths,
    settings: Settings,
    flashtool: str,
    bench: Bench,
    dev: BusDevice,
    fw_bin: str,
    mcu_type: str,
    chipset: str,
    fw: str,
    serial: str,
    side: dict,
    reporter: Reporter,
    force: bool,
    timeout: float,
) -> BusDevice:
    """Refuse a mismatched write before ``-f`` is ever called, and return the
    device the write should address.

    Uses flashtool's ``-s``/``--status`` probe: it runs the same
    ``connect_btl()`` handshake as ``-f`` - including the "Application Start:"
    line this checks - but skips send/verify/finish, so **nothing is written**.
    Safe to act on, unlike ``-f``'s own output (see module docstring).

    Nothing written, but not necessarily nothing changed: the shared handshake
    also performs the app-to-bootloader transition, and ``-s`` has no finish
    step to jump the board back. A board that was running Klipper is therefore
    left sitting in katapult, under a different by-id path - so this returns
    the device to write to rather than assuming the caller's is still valid.
    The returned device is the caller's own when the probe did not move it (a
    board already in its bootloader, or no probe at all).
    """
    app_address = side.get("app_address")
    if app_address is None:
        # Nothing of ours to compare against - an older build, or a tree that
        # does not define the symbol. Skip the probe; it could not mean anything.
        return dev

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
    # Did the probe move the board? Decided from flashtool's own words rather
    # than from what is on the bus now, so the answer does not depend on
    # winning a race with udev. Computed before the exit-code check below,
    # because a probe that requested the bootloader and *then* failed - the
    # reconnect timing out is the flakiest step in the sequence - has still
    # moved the board, and that failure is the one most likely to be misread.
    moved = bool(_BOOTLOADER_REQUEST_RE.search("\n".join(transcript)))
    # Every refusal below leaves the board wherever the probe put it. Saying so
    # is the difference between "your board is in its bootloader, re-run" and
    # an operator reading a refusal as a brick.
    aftermath = (
        f" Nothing was written, but asking cost a reboot: {serial} is sitting "
        f"in katapult now, not running {fw}. It will come back on the next "
        f"flash, or on a power cycle."
        if moved
        else ""
    )

    if rc != 0:
        raise FlashError(
            f"flashtool.py's status check failed for {serial} (exit {rc}). Not "
            f"attempting to write." + aftermath,
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
            raise OffsetMismatchError(
                message + aftermath, type=mcu_type, serial=serial, fw=fw
            )
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
                message + aftermath,
                type=mcu_type,
                serial=serial,
                fw=fw,
                app_address=f"{app_address:#x}",
                board_address=f"{board_address:#x}",
            )

    if not moved:
        return dev

    reporter("info", f"{serial} is in katapult after the check; re-resolving it...")
    rebooted = _await_bootloader_device(bench, chipset, serial, timeout)
    if rebooted is None:
        # Never fall back to the caller's path. It is the path of a Klipper
        # device that no longer exists, and handing it to -f is exactly the
        # "No Serial Device found" failure this whole re-resolve exists to stop.
        raise BootloaderTimeoutError(
            f"the offset check rebooted {serial} into katapult, but no katapult "
            f"device answering as {serial} appeared within {timeout:g}s, so "
            f"there is no path to write to. Nothing was written and the board "
            f"is not damaged - it is most likely still in its bootloader: "
            f"re-run the flash and it will be found there and written directly.",
            type=mcu_type,
            serial=serial,
            chipset=chipset,
        )
    return rebooted


def flash_katapult_can(
    paths: Paths,
    settings: Settings,
    mcu_type: str,
    uuid: str,
    fw_bin: str | None = None,
    *,
    fw: str | None = None,
    reporter: Reporter = null_reporter,
    force: bool = False,
    bridge: bool | None = None,
    interface: str | None = None,
) -> None:
    """Flash one CAN-addressed board through katapult's flashtool.py.

    `flash_katapult`'s CAN counterpart, mirrored as closely as the identity
    difference allows: `-d <path>` becomes `-i <interface> -u <uuid>`, tried
    against every interface `discovery.canbus.list_can_interfaces` currently
    reports when no configured interface is supplied. A configured interface
    is authoritative and is tried exactly once; otherwise a timeout/failure on
    one interface means "wrong bus, try the next", not a real failure.

    `bridge` is the one thing this cannot always discover by trial: `True`
    when the caller already knows (via Klipper's own `mcu_constants.
    CANBUS_BRIDGE`, for an already-tracked and currently-connected board) that
    the target is a USB-CAN bridge rather than a native CAN node. `None`
    means "unknown" - an unclaimed board from Phase 1's discovery flow has no
    Klipper object to ask at all - and this falls back to discovering it by
    trial, same as the interface itself.

    **Offset guard, native node.** `-i <iface> -u <uuid> -f <fw_bin> -s` runs
    the same `connect_btl()` handshake and prints the same "Application
    Start:" line `_APP_START_RE` already parses, without writing - so for a
    native CAN node this probe doubles as both the interface-discovery
    attempt *and* the pre-write offset guard `_verify_offset_before_write`
    gives a by-id board, in one step: whichever interface answers the probe
    is confirmed as both "the right bus" and "safe to write".

    **Offset guard, bridge target.** A bare `-s` probe against a bridge's own
    uuid tries to speak the bootloader handshake directly to a device that
    isn't a CAN-bootloader node at all - it's the adapter providing the
    interface - and would not produce a useful handshake before the jump. So
    for a known bridge (`bridge is True`), or once the probe above has found
    no answer on any interface (which a bridge's own uuid never will), this
    skips the pre-flight probe entirely and lets the real `-f` write - which
    *does* transparently jump a bridge and reflash it over USB - serve as the
    only interface-discovery attempt, relying solely on the post-write
    mismatch check (`_report_offset_mismatch`) for that target. This is a
    real, accepted reduction in safety margin for a bridge target specifically
    - not a gap for a native node, which keeps the full pre-write guard - and
    is recorded here and in `docs/decisions.md` rather than silently dropped.

    Raises on any failure; returns None on success.
    """
    from ..discovery.canbus import list_can_interfaces

    flashtool = find_flashtool(paths, settings)
    if not os.path.exists(flashtool):
        raise ToolMissingError(
            f"flashtool.py not found at {flashtool}. Is katapult installed?",
            tool="flashtool.py",
            path=flashtool,
        )

    fw = fw or "klipper"
    if fw_bin is None:
        fw_bin = paths.bin_file(mcu_type, fw)
    if not os.path.exists(fw_bin):
        raise FlashError(
            f"firmware binary not found at {fw_bin}. Build it first.",
            type=mcu_type,
            uuid=uuid,
            path=fw_bin,
        )

    interfaces = [interface] if interface is not None else list_can_interfaces(paths)
    if not interfaces:
        raise DeviceNotFoundError(
            f"no CAN interfaces found on this host for {uuid} (looked for a "
            f"network device whose sysfs type is ARPHRD_CAN). Is a USB-CAN "
            f"adapter connected?",
            type=mcu_type,
            uuid=uuid,
        )

    side: dict = {}
    app_address = None
    if not settings.dry_run:
        from ..build import FlashLog, git_head, read_sidecar

        side = read_sidecar(paths, mcu_type, fw) or {}
        app_address = side.get("app_address")

    # The probe-as-discovery attempt: only meaningful for a native node (see
    # docstring above), and only when there is something of ours to check the
    # answer against.
    probed_interface: str | None = None
    if not settings.dry_run and bridge is not True and app_address is not None:
        probe_transcript: list[str] = []

        def capture_probe(stream: str, line: str) -> None:
            probe_transcript.append(line)
            reporter(stream, line)

        for candidate in interfaces:
            probe_transcript.clear()
            reporter("info", f"Probing {uuid} on {candidate} before writing...")
            rc = run_streamed(
                [sys.executable, flashtool, "-i", candidate, "-u", uuid, "-f", fw_bin, "-s"],
                cwd=paths.home,
                reporter=capture_probe,
                dry_run=False,
                fake_delay=0.0,
            )
            if rc != 0:
                # Wrong bus, or nothing answered this uuid here - try the next
                # interface rather than treating this as a real failure.
                continue
            # Same reboot the USB path accounts for: -s shares -f's handshake,
            # which transitions a running application into the bootloader, and
            # has no finish step to jump it back. Only the *path* problem is
            # USB-only - `-i <iface> -u <uuid>` still addresses the board - but
            # a refusal here leaves a native node in katapult just the same.
            can_moved = bool(
                _BOOTLOADER_REQUEST_RE.search("\n".join(probe_transcript))
            )
            can_aftermath = (
                f" Nothing was written, but asking cost a reboot: {uuid} is "
                f"sitting in katapult now, not running {fw}. It will come back "
                f"on the next flash, or on a power cycle."
                if can_moved
                else ""
            )

            board_address = _parse_application_start(probe_transcript)
            if board_address is None:
                message = (
                    f"could not read {uuid}'s own Application Start address from "
                    f"flashtool's status check on {candidate}, so whether it "
                    f"would boot the {fw} firmware about to be written could not "
                    f"be verified."
                )
                if force:
                    reporter("warn", f"{message} Proceeding anyway (forced).")
                else:
                    raise OffsetMismatchError(
                        message + can_aftermath, type=mcu_type, uuid=uuid, fw=fw
                    )
            elif app_address != board_address:
                message = (
                    f"{uuid} ({mcu_type}) is about to be flashed with {fw} linked "
                    f"to run at {app_address:#x}, but its bootloader reports it "
                    f"will jump to {board_address:#x}. It would not come back "
                    f"running {fw} - re-derive {fw}'s bootloader offset, or check "
                    f"what bootloader is actually on this board."
                )
                if force:
                    reporter("warn", f"{message} Proceeding anyway (forced).")
                else:
                    raise OffsetMismatchError(
                        message + can_aftermath,
                        type=mcu_type,
                        uuid=uuid,
                        fw=fw,
                        app_address=f"{app_address:#x}",
                        board_address=f"{board_address:#x}",
                    )
            probed_interface = candidate
            break
        # No interface answered the probe at all: either this is a bridge
        # (whose uuid never answers a bare -s, see docstring) or the board is
        # genuinely offline. Either way, fall through to the real write below
        # and let it serve as the interface-discovery attempt instead.
        if probed_interface is None and bridge is False:
            raise FlashError(
                f"flashtool.py's status check failed for known native CAN node "
                f"{uuid} on every selected interface. Not attempting to write.",
                type=mcu_type,
                uuid=uuid,
            )

    # The real write: tried against each interface in turn until one accepts
    # it. For a probed native node this is a single attempt on the interface
    # already confirmed above; for a bridge, or when the probe found nothing,
    # this loop *is* the interface discovery.
    interfaces_to_try = [probed_interface] if probed_interface is not None else interfaces
    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    chosen: str | None = None
    for candidate in interfaces_to_try:
        transcript.clear()
        reporter("info", f"Flashing {uuid} ({mcu_type}) via {candidate}...")
        rc = run_streamed(
            [sys.executable, flashtool, "-i", candidate, "-u", uuid, "-f", fw_bin],
            cwd=paths.home,
            reporter=capture,
            # No cancel: see module docstring. Never interrupt a write.
            dry_run=settings.dry_run,
            fake_delay=0.0,
        )
        if rc == 0:
            chosen = candidate
            break
        reporter("info", f"{uuid} did not answer on {candidate} - trying another interface.")

    if chosen is None:
        raise FlashError(
            f"flashtool.py failed for {uuid} on every discovered CAN interface "
            f"({', '.join(interfaces_to_try) or 'none'}).",
            type=mcu_type,
            uuid=uuid,
        )

    if not settings.dry_run:
        # side/FlashLog/git_head came from the pre-write block above, which
        # runs under the same `not settings.dry_run` condition.
        FlashLog(paths).record(
            uuid,
            mcu_type=mcu_type,
            fw=fw,
            bin_sha256=side.get("bin_sha256"),
            fw_sha=side.get("fw_sha")
            or git_head(firmware.resolve(paths, fw).source_dir(paths)),
            # Finding a uuid answer on the bus at all - probe or write - is
            # itself the confirmation; there is no separate by-id sighting to
            # carry a `Confidence.reason` the way a serial's does.
            confidence="canbus_uuid",
            version=side.get("version"),
        )

        _report_offset_mismatch(reporter, uuid, mcu_type, fw, side, transcript)

    reporter("info", f"Flashed {uuid} successfully.")


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
        if len(matches) > 1:
            raise AmbiguousDfuError(
                f"{len(matches)} DFU devices report the same serial "
                f"{target_serial} on different USB paths - refusing to guess "
                f"which one to flash. Unplug all but the target board and try "
                f"again.",
                serial=target_serial,
                devices=[str(d["raw"]) for d in matches],
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
    katapult_config: str | None = None,
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

    **Both routes erase what the board ran before.** DFU does it with
    `mass-erase`; BOOTSEL has no erase command, so the copied `.uf2` is
    Katapult plus the application's first sector as `0xff` pages (see
    `uf2_erase`). Without that, a board that last ran other firmware keeps it
    at the application address and Katapult chain-loads it. `katapult_config`
    is Katapult's saved `.config`, which says where that address is; BOOTSEL
    refuses without it rather than copying Katapult alone.
    """
    from .. import flashers

    state = STATE_BOOTSEL if chipset.startswith("rp2040") else STATE_DFU
    flasher = flashers.select_for(chipset, state)

    with tempfile.TemporaryDirectory(prefix="mcu-updater-bootsel-") as staging:
        if state == STATE_BOOTSEL:
            if uf2_bin is None:
                raise FlashError(
                    f"no .uf2 was built for {chipset}. BOOTSEL mass storage ignores "
                    f"a .bin - build again once the tree produces one.",
                    chipset=chipset,
                )
            staged = _stage_erasing_uf2(uf2_bin, katapult_config, staging, chipset)
            reporter(
                "info",
                "Staged Katapult with the application sector erased, so the board "
                "cannot chain-load whatever it ran before.",
            )
            target = flashers.bootsel.target_for(staged, chipset=chipset, paths=paths)
        else:
            target = flashers.dfu_util.target_for(
                fw_bin, chipset=chipset, dfu_serial=target_serial
            )

        bench = flashers.Bench(
            paths=paths,
            settings=settings,
            # Nothing on this path touches a service: the board is in its ROM
            # bootloader (DFU or BOOTSEL), not on the Klipper bus, which is what
            # both flashers' `needs_services_stopped: False` says.
            controller=_no_services,
        )
        with flasher.prepared(bench, [target], PlainContext(reporter)) as session:
            flasher.write(bench, session, target, PlainContext(reporter))
            flasher.settled(bench, target, PlainContext(reporter))


def _stage_erasing_uf2(
    uf2_bin: str, katapult_config: str | None, staging: str, chipset: str
) -> str:
    """Katapult's `.uf2` with the application sector blanked, written under
    `staging` with the artifact's own file name. The artifact is not modified."""
    address = None if katapult_config is None else uf2_erase.launch_address(katapult_config)
    if address is None:
        raise FlashError(
            f"cannot tell where Katapult expects the application: "
            f"{katapult_config or 'no Katapult .config'} has no readable "
            f"{profiles.LAUNCH_ADDRESS_SYMBOL}. Without it the old application "
            f"cannot be erased, and the board could boot straight past Katapult. "
            f"Build Katapult for this type again.",
            chipset=chipset,
            path=katapult_config,
        )
    try:
        with open(uf2_bin, "rb") as fh:
            image = fh.read()
    except OSError as exc:
        raise FlashError(
            f"firmware image not found at {uf2_bin}: {exc}", path=uf2_bin
        ) from exc
    try:
        extended = uf2_erase.with_erased_sector(image, address)
    except uf2_erase.Uf2EraseError as exc:
        raise FlashError(
            f"cannot erase the application sector through {uf2_bin}: {exc}",
            path=uf2_bin,
        ) from exc
    staged = os.path.join(staging, os.path.basename(uf2_bin))
    with open(staged, "wb") as fh:
        fh.write(extended)
    return staged



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
    """Devices of this chipset that appeared and aren't tracked yet.

    Not filtered to Katapult: both install routes erase the old application
    now, but a board bootloadered by an older version or by hand can still
    carry one, chain-load straight past Katapult on its first boot, and
    reappear running that firmware instead. Matching is chipset + "wasn't on
    the bus before" - the same thing a bare board's first boot gives for free.

    Replaces the original's fixed `time.sleep(3)` with a real poll.
    """
    return wait_for_new_device(
        paths,
        known_serials,
        chipset=chipset,
        timeout=timeout,
    )
