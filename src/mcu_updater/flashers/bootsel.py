"""BOOTSEL: the first firmware an RP2040 ever receives.

The RP2040 counterpart to :mod:`.dfu_util` - a factory-bare board has no
bootloader yet to speak flashtool's protocol, so this writes before one exists.
Unlike DFU, the ROM bootloader here speaks no protocol a tool can address a
write through: it mounts as mass storage
(:func:`mcu_updater.devices.bootsel_scan` finds the mount,
:func:`mcu_updater.devices.bootsel_devices` finds the device whether mounted
or not), and the "write" is a plain file copy - a `.uf2` dropped on the volume
is what makes the board flash itself and reboot.

It writes a board two ways. A board already in BOOTSEL is copied to the one
mounted volume. A running board whose family's helper can request BOOTSEL
(`helpers.BootselRequester`) is asked to enter it first, the copy goes to the
volume matching the USB topology the helper captured, and `settled` waits for
the helper to confirm the board came back as itself. The second way goes over
a port Klipper may hold, so its targets carry `needs_services_stopped=True`;
the first stops nothing.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..artifacts import KIND_UF2, Artifact
from ..devices import STATE_BOOTSEL, bootsel_devices, bootsel_id_for, bootsel_scan
from ..discovery.bootsel import mount_for_topology
from ..errors import (
    BootselNotMountedError,
    DeviceNotFoundError,
    FlashError,
    OperationCancelled,
    UpdaterError,
)
from ..uf2 import Uf2Error, image_extent
from .spec import (
    KIND_SERIAL,
    Bench,
    CandidateScan,
    Device,
    FlashRecord,
    FlashTarget,
    TrackedBoard,
    artifact_path,
    chipset_matches,
    name_tracked,
    staged_record,
)

if TYPE_CHECKING:
    from ..build import Reporter
    from ..helpers.spec import BootselRequester, Helper
    from ..paths import Paths

SCAN_NONE = "none"
SCAN_NOT_MOUNTED = "not_mounted"
SCAN_AMBIGUOUS = "ambiguous"


def ensure_uf2(uf2: str) -> None:
    """Refuse a missing image before a BOOTSEL transition is requested."""
    if not os.path.exists(uf2):
        raise FlashError(f"firmware image not found at {uf2}.", path=uf2)


#: Where the RP2040 maps flash. An image that starts above it expects
#: something below it - a bootloader - to jump into it.
FLASH_BASE = 0x10000000


def _image_start(uf2: str) -> int | None:
    """The image's start address, read once for both the warn and refuse paths.

    `None` for a file the copy step will fail on anyway (missing, unreadable) -
    `ensure_uf2` already ran, so only a race remains, and the copy reports that
    on its own. A `Uf2Error` is different: the bytes were read and are not a
    valid image, so this is the earliest point that can say so - ahead of any
    request or write, on both the helper and no-helper paths.
    """
    try:
        with open(uf2, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        start, _length = image_extent(data)
    except Uf2Error as exc:
        raise FlashError(f"{uf2} is not a valid UF2 image: {exc}", path=uf2) from exc
    return start


_COPY_CHUNK = 1 << 20

#: How long a board gets, after a clean copy, to take the image and reset. A
#: returned copy only means the kernel has the bytes; on a bench host the
#: re-enumerate wait that follows was starting before the board had received
#: them. Roadrunner's admin tool settled on the same figure.
APPLY_TIMEOUT = 60.0
#: The same wait after a copy that raised once every byte was written. Most of
#: those are the board resetting as the last block landed, so the volume is
#: normally gone already; a volume still there is not going to leave late.
APPLY_TIMEOUT_AFTER_ERROR = 10.0
APPLY_POLL = 0.25
#: The file the boot ROM publishes on the volume - the same marker
#: `bootsel_scan` keys on. Present means the board is still in BOOTSEL.
_BOOTSEL_MARKER = "INFO_UF2.TXT"


class _VolumeVanished(Exception):
    """Every byte of the image landed; the failure came after that.

    Internal to this module - it never leaves :func:`copy_uf2`.
    """

    def __init__(self, error: OSError) -> None:
        super().__init__(str(error))
        self.error = error


def _same_file(uf2: str, dest: str) -> bool:
    """True when writing `dest` would truncate the image being read.

    `shutil.copy2` refused this outright and the explicit copy has to keep
    doing so: opening one inode for reading and for writing empties it, the
    read then returns nothing, and a zero-byte file gets reported as a
    completed flash. Only reachable if a firmware directory overlaps
    `bootsel_root`, which nothing currently forbids.

    Identity first (`samefile` compares the inode, so hard links and symlinks
    are caught), resolved paths second for a filesystem whose inode numbers
    cannot be trusted. Neither may raise: this runs against a mount that is
    allowed to disappear, and a stat failure there means "not the same file",
    not "refuse the flash".
    """
    with contextlib.suppress(OSError, ValueError):
        if os.path.exists(dest) and os.path.samefile(uf2, dest):
            return True
    with contextlib.suppress(OSError, ValueError):
        return os.path.normcase(os.path.realpath(uf2)) == os.path.normcase(
            os.path.realpath(dest)
        )
    return False


def _open_dest(dest: str) -> Any:
    """The destination handle, unbuffered on purpose.

    `buffering=0` removes Python's own buffer, and that is precisely what it
    buys: nothing is holding the tail of the image when the read loop ends, so
    a failure at flush time cannot arrive *after* :func:`_copy_bytes` has
    already concluded every byte was handed over. A buffered writer would still
    be sitting on the tail there, and its flush failure - a genuinely
    incomplete write - would be indistinguishable from the board resetting once
    the last block landed.

    It does not remove the kernel page cache; :func:`_copy_bytes` does that
    with an `fsync`. See there for why that no longer risks refusing a write
    that landed.
    """
    return open(dest, "wb", buffering=0)


def _copy_bytes(uf2: str, dest: str) -> None:
    """Write the image, distinguishing an incomplete write from a late one.

    Raises `OSError` while bytes are still outstanding, and `_VolumeVanished`
    once every byte has been handed to the kernel. No `copystat` half:
    timestamps and permissions on a FAT volume that is about to disappear are
    meaningless, and reaching for them is what turned a normal ending into a
    reported failure.

    The `fsync` pushes the image out of the page cache before this returns, so
    the apply wait in :func:`copy_uf2` measures the board rather than the
    kernel's writeback delay. It used to be left out because forcing FAT
    metadata writeback can fail on a volume the board has already reset away,
    refusing a write that landed. It counts as the late case here for that
    reason - and a writeback error that really did truncate the image is no
    longer excused by that, because a board missing its last block never
    resets and the wait sees its volume stay.
    """
    complete = False
    try:
        with open(uf2, "rb") as src, _open_dest(dest) as out:
            while True:
                chunk = src.read(_COPY_CHUNK)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    view = view[out.write(view) :]
            complete = True
            os.fsync(out.fileno())
    except OSError as exc:
        if complete:
            raise _VolumeVanished(exc) from exc
        raise


def _volume_still_mounted(mount: str) -> bool:
    """Is the board still sitting in BOOTSEL at `mount`?

    `isfile` answers False rather than raising on a mount whose device has
    gone, which is exactly the answer wanted there.
    """
    return os.path.isfile(os.path.join(mount, _BOOTSEL_MARKER))


def _wait_for_apply(mount: str, timeout: float) -> bool:
    """Wait for the volume to go away - the board resetting into the image.

    True once it has gone, False if it is still there after `timeout`.
    """
    deadline = time.monotonic() + timeout
    while _volume_still_mounted(mount):
        if time.monotonic() >= deadline:
            return False
        time.sleep(APPLY_POLL)
    return True


def copy_uf2(uf2: str, mount: str, ctx: Any) -> None:
    """Copy one validated UF2 to a selected BOOTSEL volume.

    The boundary this draws is the whole point. The RP2040 boot ROM resets the
    board the instant the final UF2 block lands, so on real hardware the mount
    disappearing at the end of a *good* write is the normal path, not an edge
    case: anything after the data - a close, a metadata step - fails on a
    volume that is already gone. Reporting that as a failed copy would hide the
    `FlashLog` record behind `result["flashed"]`, tell the operator a correct
    board still needs flashing, and invite a re-flash.

    Bytes still outstanding when the error arrives are the other case, and stay
    a `FlashError`: a board unplugged mid-write, a full or erroring FAT volume.
    Raw, that `OSError` would leave `write_all` altogether - past the Klipper
    readiness gate it runs after the batch, which for a helper-requested BOOTSEL is
    exactly when Klipper's units have just been restarted underneath a board in
    an unknown state.

    Returning does not mean the board has the image, so this waits for the
    volume to go away before it does: `APPLY_TIMEOUT` after a clean copy,
    `APPLY_TIMEOUT_AFTER_ERROR` after a late error. Whatever waits for the
    board to re-enumerate runs after this, so its timeout no longer starts
    while the board is still taking the image. The wait lives here, not in
    `settled`, because both flashers already know the mount here and `settled`
    is handed none. A volume that stays is a warning, not a failure: the copy
    finished, and the readiness check that follows is the verdict on the board.

    Cancellation is deliberately not checked in here; it stays between writes.
    """
    ensure_uf2(uf2)
    dest = os.path.join(mount, os.path.basename(uf2))
    if _same_file(uf2, dest):
        raise FlashError(
            f"refusing to copy {os.path.basename(uf2)} onto itself at {dest}: "
            "the staged image and the BOOTSEL volume are the same file.",
            path=uf2,
            mount=mount,
        )
    ctx.reporter("info", f"Copying {uf2} to {dest}...")
    vanished: OSError | None = None
    try:
        _copy_bytes(uf2, dest)
    except _VolumeVanished as gone:
        vanished = gone.error
    except OSError as exc:
        raise FlashError(
            f"could not write {os.path.basename(uf2)} to {mount}: {exc}",
            path=uf2,
            mount=mount,
        ) from exc
    if vanished is None:
        timeout = APPLY_TIMEOUT
        ctx.reporter(
            "info", "Copied. Waiting for the board to take the image and reset..."
        )
    else:
        timeout = APPLY_TIMEOUT_AFTER_ERROR
        ctx.reporter(
            "info",
            f"The copy reported an error once every byte was written "
            f"({vanished}) - normally the board resetting as the last block "
            "landed. Checking that its volume has gone...",
        )
    if not _wait_for_apply(mount, timeout):
        late = "" if vanished is None else f" (the copy reported: {vanished})"
        ctx.reporter(
            "warn",
            f"{mount} is still mounted {timeout:g}s after the copy{late} - the "
            "board has not reset into the new image. The readiness check that "
            "follows decides whether it came back.",
        )
        return
    ctx.reporter(
        "info",
        "The volume has gone: the board took the image and is rebooting.",
    )


class Bootsel:
    """Writes an RP2040 through its BOOTSEL mass-storage bootloader."""

    name = "bootsel"
    label = "BOOTSEL (mass storage)"
    candidate_prefix = "bootsel"
    chipsets: tuple[str, ...] = ("rp2040",)
    states: tuple[str, ...] = (STATE_BOOTSEL,)
    #: False for a board already in BOOTSEL: nothing holds its port. A target
    #: that asks a helper for BOOTSEL overrides this with True (see
    #: `target_for`), because that request goes over a port Klipper may hold.
    needs_services_stopped = False
    accepts: tuple[str, ...] = (KIND_UF2,)

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A board in BOOTSEL, or a running board its helper can put there.

        The helper path does not match on chipset: a CMake `[type]` may leave
        `chipset:` empty, and the helper confirms its own board's protocol
        identity before anything is written. A board already in BOOTSEL has
        nothing to vouch for it but its chipset.
        """
        if device.state in self.states:
            return chipset_matches(self, device.chipset)
        return device.kind == KIND_SERIAL and helpers.bootsel_requester(helper) is not None

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        requester = helpers.bootsel_requester(helper)
        if device.state in self.states or requester is None:
            return target_for(artifact, chipset=device.chipset, paths=paths)
        return target_for(
            artifact,
            chipset=device.chipset,
            type_name=device.type,
            serial=device.id,
            fw=device.fw,
            helper=requester,
            stop_services=stop_services,
        )

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up for the batch."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        uf2 = artifact_path(target)
        ensure_uf2(uf2)
        requester: BootselRequester | None = target.detail.get("helper")
        start = _image_start(uf2)
        if start is not None and start > FLASH_BASE:
            if requester is not None:
                raise FlashError(
                    f"{uf2} starts at {start:#x}, after a bootloader offset, and a board "
                    f"asked for BOOTSEL by its firmware has no bootloader below it to boot "
                    f"it. Nothing was written. Rebuild with no bootloader offset "
                    f"(Bootloader offset: No bootloader), or list flashtool before bootsel "
                    f"to write the .bin through Katapult.",
                    serial=target.id,
                    type=target.type,
                )
            ctx.reporter(
                "warn",
                f"{os.path.basename(uf2)} starts at {start:#x}, above the start of flash, "
                f"so it expects a bootloader below it. A board with Katapult keeps it and "
                f"boots this image; a board without one will not boot it - build with no "
                f"bootloader offset for that board.",
            )

        if bench.settings.dry_run:
            if requester is None:
                ctx.reporter(
                    "info", f"[dry-run] would copy {uf2} to the mounted RPI-RP2 volume"
                )
            else:
                ctx.reporter(
                    "info",
                    f"[dry-run] would request BOOTSEL then copy {uf2} to its matched volume",
                )
            return {"mount": None}

        if requester is None:
            mount = _find_mount(bench.paths)
        else:
            handoff = requester.request_bootsel(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
            )
            target.detail["topology"] = handoff.topology
            mount = mount_for_topology(bench.paths, handoff.topology)
        copy_uf2(uf2, mount, ctx)
        return {"mount": mount}

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The image this board now holds, from what its family staged.

        `None` for a board that was already in BOOTSEL: first install writes a
        bootloader to a bare board whose `type` is a chipset string and whose
        id may be empty, and there is no tracked device to file that under.
        Only a helper-requested target names a tracked board and its family.
        """
        fw = target.detail.get("fw")
        if "helper" not in target.detail or not fw:
            return None
        return staged_record(bench, target, fw=fw, kind=KIND_UF2)

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Wait for a helper-requested board to confirm its identity.

        A board that was already in BOOTSEL reboots as Katapult under a serial
        it has never had; waiting for that is adoption, which this cannot do
        because it cannot name the device. Waiting for the board to take the
        image already happened inside `write`.
        """
        requester: BootselRequester | None = target.detail.get("helper")
        if requester is None or bench.settings.dry_run:
            return
        try:
            requester.wait_ready(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
                type_name=target.type,
                fw=target.detail.get("fw", ""),
                topology=target.detail.get("topology", ""),
            )
        except OperationCancelled:
            raise
        except UpdaterError as exc:
            # The UF2 copy already completed. Every boundary the spec enumerates
            # is pre-copy or at-copy; there is no post-write one, and step 4 of
            # the flow waits for the device "non-fatally" without qualification.
            # So *any* readiness outcome - a slow return, a probe that would not
            # answer, an identity that came back wrong, two devices answering to
            # one serial - is a warning here. Rewriting a completed write as a
            # failure would invite a re-flash of a board that is already correct.
            ctx.reporter("warn", str(exc))

    def scan_candidates(
        self, paths: Paths, *, tracked: Sequence[TrackedBoard], reporter: Reporter
    ) -> CandidateScan:
        """What is sitting in BOOTSEL, and can this agent actually write it?

        Mirrors `dfu_util.DfuUtil.scan_candidates`'s report-don't-raise shape.
        Diverges where BOOTSEL genuinely differs: no external tool to be
        missing or to deny access - reading `/dev/disk/by-id` and a mount
        point is plain filesystem access, so there is no
        `no_tool`/`permission_denied` here at all.

        Readiness gates on the **mount** count, not the device count, because
        that is exactly what the write itself (`_find_mount`) gates on - a
        board present but unmounted is not writable regardless of how many
        are attached.

        ``none``
            Nothing in BOOTSEL. Hold BOOTSEL and replug the board.
        ``not_mounted``
            A board is attached but nothing mounted its volume - this host has
            no automounter. Re-run install.sh to install the udev rule.
        ``ambiguous``
            More than one RPI-RP2 volume is mounted at once. Unlike DFU there
            is no serial to pick one by - the mounts are now distinguishable
            (each names its USB port), but nothing upstream can yet say which
            one this write is for, so this stays a refusal rather than a choice.

        Unlike DFU's derived serial, this is an **assumed** identity: the boot
        ROM's flash-chip unique id is assumed - unverified on real hardware,
        see the id-collision caveat in docs/agent-api.md's `fw.bootsel.scan`
        section - to be the same string Katapult later reports as its own
        running USB serial, so a tracked rp2040 board's serial can be compared
        to a BOOTSEL device's id directly.

        If that assumption is wrong this simply never matches - every device's
        `known_serial`/`tracked_by` stays null, exactly what a genuinely new
        board looks like, never a wrong name. Same collision guard as
        `DfuUtil.scan_candidates`: two *registry entries* mapping to one id
        names neither. It does not cover two *physical boards* sharing one id
        - see docs/agent-api.md's `fw.bootsel.scan` section.

        Tracked identities are canonical hardware serials; the by-id interface
        suffix belongs only to the transport path. Compare the full string so
        a legitimate hyphen cannot create a prefix match.
        """
        from ..discovery import usb

        present = bootsel_devices(paths)
        inventory = usb.collect(paths)
        devices: list[dict[str, Any]] = []
        for node in present:
            hardware = usb.device_for_block(inventory, paths, node)
            devices.append(
                {
                    "id": bootsel_id_for(node),
                    "node": node,
                    "port": hardware.name if hardware is not None else None,
                }
            )
        owners: dict[str, list[tuple[str, str]]] = {}
        for board in tracked:
            if board.chipset.startswith("rp2040"):
                owners.setdefault(board.serial, []).append((board.type, board.serial))
        name_tracked(devices, owners, "id")

        mounts = bootsel_scan(paths)
        extra: dict[str, Any] = {"mounts": mounts, "mount_count": len(mounts)}
        if not present:
            return CandidateScan(
                False,
                SCAN_NONE,
                "No RP2040 in BOOTSEL is attached. Hold BOOTSEL and replug the board.",
                devices,
                extra,
            )
        if not mounts:
            return CandidateScan(
                False,
                SCAN_NOT_MOUNTED,
                f"An RP2040 in BOOTSEL is attached ({', '.join(present)}) but "
                f"nothing mounted its volume - this host has no automounter. "
                f"Re-run install.sh to install the udev rule, which mounts each "
                f"board under /media/<user>/BOOTSEL/by-path/<port>.",
                devices,
                extra,
            )
        if len(mounts) > 1:
            return CandidateScan(
                False,
                SCAN_AMBIGUOUS,
                f"{len(mounts)} RPI-RP2 volumes are mounted at once "
                f"({', '.join(mounts)}) - which one is this board? Unplug the "
                f"others and try again.",
                devices,
                extra,
            )
        return CandidateScan(True, None, None, devices, extra)


def _find_mount(paths: Any) -> str:
    """The one RPI-RP2 volume to write, or a refusal.

    BOOTSEL has no protocol to address a specific board through - the write
    itself is a plain file copy, not a command aimed at a device - so unlike
    DFU's `target_serial` there is nothing here to disambiguate a *write* with.
    More than one mounted at once means guessing which board this write is
    for, which this refuses exactly the way `DfuUtil`'s ambiguity guard does.

    The mounts are no longer indistinguishable - each names its USB port since
    the topology-path udev rule - but nothing upstream of here can yet say
    *which* port this write is for, so the refusal stands rather than guessing.
    Giving it a port parameter is what turns this into a selection.

    A board can still be genuinely absent from the *volume* search while
    present on the bus - a headless host with no automounter mounts nothing at
    all - so an empty `bootsel_scan` is split against `bootsel_devices` to
    give the right one of two very different failures.
    """
    mounts = bootsel_scan(paths)
    if not mounts:
        present = bootsel_devices(paths)
        if present:
            raise BootselNotMountedError(
                f"an RP2040 in BOOTSEL is attached ({', '.join(present)}) but "
                f"nothing mounted its volume - this host has no automounter. "
                f"Re-run install.sh to install the udev rule, which mounts each "
                f"board under /media/<user>/BOOTSEL/by-path/<port>.",
                devices=present,
            )
        raise DeviceNotFoundError(
            "no RP2040 in BOOTSEL is attached. Hold BOOTSEL and replug the "
            "board, then try again."
        )
    if len(mounts) > 1:
        raise FlashError(
            f"{len(mounts)} RPI-RP2 volumes are mounted at once "
            f"({', '.join(mounts)}) - which one is this board? Unplug the "
            f"others and try again.",
            mounts=mounts,
        )
    return mounts[0]


def target_for(
    uf2_file: str | Artifact,
    *,
    chipset: str,
    paths: Paths | None = None,
    type_name: str = "",
    serial: str = "",
    fw: str = "",
    helper: BootselRequester | None = None,
    stop_services: tuple[str, ...] = (),
) -> FlashTarget:
    """An RP2040 to write, as a target.

    **With a helper**, a running board of a configured type: `serial` is its
    durable identity, the helper asks it to enter BOOTSEL, and the target
    stops `stop_services` because that request goes over a port Klipper may
    hold.

    **Without one**, a board already in BOOTSEL. The boot ROM publishes the
    flash chip's id as a USB mass-storage serial, and it is recorded here, but
    **it is not an identity**: two boards from one batch have been observed on
    hardware reporting the same `pico_get_unique_board_id()`. It is a label on
    the flash, nothing more, and nothing downstream keys on it - `write` and
    `settled` never read `target.id` on this path, and the add-mcu pairing key
    is looked up separately in `agent.methods.flash`. Correlating a board
    across the BOOTSEL reboot needs the USB topology path instead; see
    docs/bootsel-mountpoint-design.md.

    `paths` is only used for that lookup (via `bootsel_devices`); callers that
    omit it, or that hit zero or more than one device, get `id=""` - the
    multi-volume case is still refused in `write`.

    `uf2_file` is normally the artifact selection chose. A plain path is
    taken as a `uf2` with no recorded hash, for callers holding a file rather
    than a build. `fw` names the family whose staged image the ledger reads
    back after the write.
    """
    artifact = uf2_file if isinstance(uf2_file, Artifact) else Artifact(KIND_UF2, uf2_file)
    if helper is not None:
        return FlashTarget(
            flasher=Bootsel.name,
            type=type_name,
            id=serial,
            stop_services=stop_services,
            detail={"chipset": chipset, "helper": helper, "fw": fw},
            needs_services_stopped=True,
            artifact=artifact,
        )
    device_id = ""
    if paths is not None:
        present = bootsel_devices(paths)
        if len(present) == 1:
            device_id = bootsel_id_for(present[0]) or ""
    return FlashTarget(
        flasher=Bootsel.name,
        type=chipset,
        id=device_id,
        detail={"chipset": chipset},
        artifact=artifact,
    )
