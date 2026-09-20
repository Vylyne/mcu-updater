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
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..devices import STATE_BOOTSEL, bootsel_devices, bootsel_id_for, bootsel_scan
from ..discovery.bootsel import mount_for_topology
from ..errors import (
    BootselNotMountedError,
    DeviceNotFoundError,
    FlashError,
    OperationCancelled,
    UpdaterError,
)
from .spec import KIND_SERIAL, Bench, Device, FlashRecord, FlashTarget, chipset_matches

if TYPE_CHECKING:
    from ..helpers.spec import BootselRequester, Helper
    from ..paths import Paths


def ensure_uf2(uf2: str) -> None:
    """Refuse a missing image before a BOOTSEL transition is requested."""
    if not os.path.exists(uf2):
        raise FlashError(f"firmware image not found at {uf2}.", path=uf2)


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
    chipsets: tuple[str, ...] = ("rp2040",)
    states: tuple[str, ...] = (STATE_BOOTSEL,)
    #: False for a board already in BOOTSEL: nothing holds its port. A target
    #: that asks a helper for BOOTSEL overrides this with True (see
    #: `target_for`), because that request goes over a port Klipper may hold.
    needs_services_stopped = False

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
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        uf2 = device.detail["uf2_file"]
        requester = helpers.bootsel_requester(helper)
        if device.state in self.states or requester is None:
            return target_for(uf2, chipset=device.chipset, paths=paths)
        return target_for(
            uf2,
            chipset=device.chipset,
            type_name=device.type,
            serial=device.id,
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
        uf2 = target.detail["uf2_file"]
        ensure_uf2(uf2)
        requester: BootselRequester | None = target.detail.get("helper")

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
            mount = mount_for_topology(bench.paths, handoff.topology)
        copy_uf2(uf2, mount, ctx)
        return {"mount": mount}

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The CMake image this board now holds.

        `None` for anything that is not a configured CMake type: first install
        writes a bootloader to a bare board whose `type` is a chipset string
        and whose id may be empty, and there is no tracked device to file that
        under.
        """
        from ..providers import cmake as cmake_mod

        target_type = cmake_mod.load(bench.paths).get(target.type)
        if target_type is None:
            return None
        side = cmake_mod.read_sidecar(bench.paths, target_type) or {}
        uf2 = target.detail["uf2_file"]
        if side.get("dirty") or not cmake_mod.sidecar_describes_image(side, uf2, os.stat(uf2)):
            side = {}
        return FlashRecord(
            key=target.id,
            mcu_type=target.type,
            fw=target_type.firmware,
            bin_sha256=side.get("bin_sha256"),
            # `sha`, not `fw_sha`: the CMake sidecar's own spelling.
            fw_sha=side.get("sha"),
            version=side.get("version"),
        )

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
    uf2_file: str,
    *,
    chipset: str,
    paths: Paths | None = None,
    type_name: str = "",
    serial: str = "",
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
    """
    if helper is not None:
        return FlashTarget(
            flasher=Bootsel.name,
            type=type_name,
            id=serial,
            stop_services=stop_services,
            detail={"uf2_file": uf2_file, "chipset": chipset, "helper": helper},
            needs_services_stopped=True,
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
        detail={"uf2_file": uf2_file, "chipset": chipset},
    )
