"""PlatformIO: one env, uploaded to a port.

`pio run -t upload` for any PlatformIO env, at the port its caller configured.
Every other write here targets a device by an identity it carries on the bus.
A device reached at a configured port may carry none the host can read, so if
its family has a way to tell its devices apart, they are asked again at the
moment of the write - which is only possible once the services holding the
ports are down.

That is one step, in one place, and it is the whole reason `prepared()` exists
on the protocol. Who can answer is the family's helper, through the
`helpers.Identifier` capability; this module only asks, and names no vendor. A
family with no identifier is written at its configured ports, which is what
every write did before identity existed.

:mod:`mcu_updater.providers.pio` keeps the upload itself, including the parts
with no MCU counterpart at all: never letting PlatformIO choose its own upload
port, and following a udev symlink to the device PlatformIO can actually see.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..artifacts import KIND_PIO_ENV, Artifact
from ..devices import STATE_ESP_ROM
from ..errors import FlashError, UpdaterError
from .spec import (
    KIND_BARE,
    KIND_PORT,
    Bench,
    CandidateScan,
    Device,
    FlashRecord,
    FlashTarget,
    TrackedBoard,
    chipset_matches,
)

if TYPE_CHECKING:
    # Annotation only. `discovery.spec` imports from this package, so a runtime
    # import here closes a cycle - and `from __future__ import annotations`
    # means nothing needs the symbol at run time. `port_for` imports the module
    # it builds one from lazily, for the same reason.
    from ..build import Reporter
    from ..discovery.spec import Confidence
    from ..discovery.usb import SerialPort
    from ..helpers.spec import Helper, Identifier
    from ..paths import Paths
    from ..providers.pio import PioType
    from ..settings import Settings

#: `scan_candidates` reasons - see its docstring for where each sends the user.
SCAN_NO_TYPE = "no_type"
SCAN_NONE = "none"
SCAN_AMBIGUOUS = "ambiguous"
SCAN_ALL_TRACKED = "all_tracked"
SCAN_UNFILTERED = "unfiltered"

#: The device key `fw.add_mcu.start` names one candidate by. The USB interface
#: rather than the tty: `ttyUSB1` is whatever was plugged in second, and can be
#: a different device by the time the write starts.
PICK_KEY = "interface"


class PlatformIO:
    """Writes one device of one PlatformIO type."""

    name = "platformio"
    label = "PlatformIO upload"
    chipsets: tuple[str, ...] = ("esp32",)
    states: tuple[str, ...] = (STATE_ESP_ROM,)
    #: The klippy module holds the port open for the write itself, and
    #: pyserial's exclusive open is an advisory flock that both it and the
    #: upload take. Unlike flashtool, this one is about the write and not about
    #: getting somewhere first.
    needs_services_stopped = True
    accepts: tuple[str, ...] = (KIND_PIO_ENV,)
    #: A not-ready scan is refused as `platformio_<reason>`.
    candidate_prefix = "platformio"
    candidate_hint = (
        "Plug the device in. Nothing has to be held or jumpered: the upload resets it into its ROM itself."
    )

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A PlatformIO device reached through its configured port. Its
        identity, if its family has a way to know one, is confirmed at write
        time, in `prepared`.

        And a bare one of a chipset it uploads to - which is not a second kind
        of write. The upload tool resets the chip into its ROM by itself, so a
        new device is written exactly as a configured one is, at a port
        `scan_candidates` found instead of one a section names; `first_write`
        turns the one into the other.
        """
        if device.kind == KIND_BARE:
            return device.state in self.states and chipset_matches(self, device.chipset)
        return device.kind == KIND_PORT

    def target(
        self,
        paths: Paths,
        device: Device,
        helper: Helper | None,
        artifact: Artifact,
        *,
        stop_services: tuple[str, ...],
    ) -> FlashTarget:
        """The device as its caller described it, plus who can say which one
        it is.

        The identifier comes from the helper selection already resolved, so
        `prepared` needs no second config load. `None` for a family without
        the capability.
        """
        return FlashTarget(
            flasher=self.name,
            type=device.type,
            id=device.id,
            stop_services=stop_services,
            # Anything else the caller put in `detail` (bulk's `reason`) rides
            # along; the flasher's own key wins.
            detail={**device.detail, "identifier": helpers.identifier(helper)},
            artifact=artifact,
        )

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[dict[str, dict[str, Any]]]:
        """Ask each type's devices which they are, now that the ports are free.

        The watcher pause that used to happen here is part of the outer stop
        `write_all` opens over the batch's own `stop_services` union, verified
        and journaled - see `flashers.batch.write_all` and
        `service.services_stopped`.

        The device list was read before the stop, so its ports describe where
        these devices were; a remembered port is what the whole device-id
        scheme exists to avoid. This is the one moment identity can be
        resolved rather than remembered, so the family's identifier is asked
        with `ask=True`: the ports are free, and the caller wants to be sure.
        What that costs, and what it falls back to, is the helper's policy.

        Once per type rather than once per device: a single listen covers
        every port at once, and doing it per device would multiply its cost by
        the number of devices.

        Skipped entirely on a dry run: asking can open real serial ports, and
        a rehearsal that touches hardware is not a rehearsal.
        """
        if bench.settings.dry_run:
            ctx.reporter("info", "[dry-run] would ask the devices which they are")
            yield {}
            return
        by_type: dict[str, tuple[PioType, Identifier | None]] = {}
        for target in targets:
            if target.detail.get("first_install"):
                # Nothing to confirm before the write: a device being set up
                # has no identity yet, and its port was chosen by the scan
                # rather than remembered. It is asked afterwards - `write`.
                continue
            env = target.detail["env"]
            by_type[env.name] = (env, target.detail["identifier"])
        yield {name: _identify(bench, env, identifier, ctx) for name, (env, identifier) in by_type.items()}

    def write(self, bench: Bench, session: Any, target: FlashTarget, ctx: Any) -> dict[str, Any]:
        from ..providers import pio as pio_mod

        env = target.detail["env"]
        first = bool(target.detail.get("first_install"))
        if first:
            # The port the scan found, as chosen. `port_for` reconciles a
            # remembered port with what answered; here nothing was remembered.
            port, confidence, problem = target.detail["port"], None, None
        else:
            port, confidence, problem = port_for(target.detail, (session or {}).get(env.name) or {}, ctx)
        if problem is not None:
            # Raised rather than collected, because a batch records a failure by
            # catching one. The check itself is unchanged: a device that stayed
            # silent while every other one answered is not there, and writing to
            # the port it used to be on would write to whatever is on that port
            # now.
            raise FlashError(problem, type=env.name, port=port)

        result = pio_mod.upload(bench.paths, bench.settings, env, port, reporter=ctx.reporter)

        if first and not bench.settings.dry_run:
            # Asked now, while the ports are still free: this is the first
            # moment the device has anything to say, and once the services are
            # back it is one more identical port. Its answer is what the user
            # configures it by, and what the ledger files this write under.
            from ..discovery import spec as discovery

            reported = _announced(bench, env, target.detail["identifier"], port, ctx)
            result = {**result, "reported_id": reported}
            if reported is not None:
                target.detail["device_id"] = reported
                confidence = discovery.Confidence(discovery.ANSWERED)

        return {
            "name": target.detail["name"],
            "port": port,
            # Taken back off by `write_all` - the ports are free exactly once,
            # inside this batch's stop, and this is the only moment the answer
            # exists.
            "confidence": confidence.reason if confidence is not None else None,
            **result,
        }

    def record(self, bench: Bench, target: FlashTarget) -> FlashRecord | None:
        """The image this device now holds, filed under its hardware id.

        `None` for a device with no hardware id: the port it was written on is
        not a durable name for it - see `build.hardware_id_key`.
        """
        from ..build import hardware_id_key
        from ..providers import pio as pio_mod

        env = target.detail["env"]
        ident = target.detail["device_id"]
        if not ident:
            return None
        # The upload just hashed the image it wrote and noted its commit;
        # re-deriving them here would be a second answer to a question with a
        # recorded one.
        side = pio_mod.read_sidecar(bench.paths, env) or {}
        return FlashRecord(
            key=hardware_id_key(ident),
            mcu_type=env.name,
            fw=env.env,
            bin_sha256=side.get("bin_sha256"),
            # The PlatformIO sidecar calls the tree commit `sha`; the flash log
            # calls it `fw_sha`. One rename at the boundary.
            fw_sha=side.get("sha"),
        )

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait for. A device written here is not on the Klipper
        bus, so there is no device node whose absence would bring Klipper up
        in an error state - which is the only thing the MCU wait is protecting
        against."""

    def scan_candidates(
        self,
        paths: Paths,
        *,
        tracked: Sequence[TrackedBoard],
        reporter: Reporter,
        type_name: str | None = None,
    ) -> CandidateScan:
        """Which serial port is the new device of this type on?

        Reports rather than raises, like the ROM scanners. Unlike them there
        is no boot mode to look for: an ESP32 behind a bridge chip looks the
        same blank, running, or mid-crash, so what is listed is every USB
        serial port whose ids are ones the type's board enumerates under
        (`providers.pio.board_hwids`), read from sysfs because by-id shows
        only one of several bridges that report no serial.

        A port a configured device already sits on (`tracked` entries that
        carry a `path`) is listed and labelled, and is never the one chosen
        unasked; a port whose USB serial a tracked board answers to is that
        board and is not listed at all.

        ``no_type``
            Asked without a type, or for one that is not a PlatformIO type.
            Which ports to list is the type's project's to say.
        ``none``
            No port matches. Plug the device in - or, if it is plugged in,
            the manifest's `build.hwids` do not describe it, and the message
            names the ids that were looked for.
        ``ambiguous``
            Several unconfigured ports match and nothing can say which is the
            new device. `pick` names the key to choose one by.
        ``all_tracked``
            Every matching port is a configured device. One can still be
            picked, to write it again as new.
        ``unfiltered``
            The board's USB ids could not be read (`message` says why), so
            every USB serial port is listed and one has to be picked. Never
            ready: without the ids, a sole port is merely the only port.
        """
        from ..discovery import usb
        from ..providers import pio as pio_mod
        from ..settings import load_settings

        shown: list[str] | None = None

        def refuse(reason: str, message: str, devices: list[dict[str, Any]] | None = None) -> CandidateScan:
            return CandidateScan(False, reason, message, devices or [], extra={"hwids": shown}, pick=PICK_KEY)

        entry = None
        if type_name is not None:
            try:
                entry = pio_mod.load(paths).get(type_name)
            except UpdaterError as exc:
                return refuse(SCAN_NO_TYPE, str(exc))
        if entry is None:
            return refuse(
                SCAN_NO_TYPE,
                "a PlatformIO device is found by the USB ids its type's board declares, "
                "so this scan has to be asked about a PlatformIO type.",
            )

        hwids, problem = pio_mod.board_hwids(load_settings(paths.settings_file), entry)
        shown = None if hwids is None else sorted(f"{vid}:{pid}" for vid, pid in hwids)

        serials = {board.serial for board in tracked if board.serial and not board.path}
        ports = [
            port
            for port in usb.serial_ports(paths)
            if (hwids is None or port.vid_pid in hwids) and (port.device.serial or "") not in serials
        ]
        devices = [_candidate(port, tracked) for port in ports]
        free = [index for index, device in enumerate(devices) if device["tracked_by"] is None]

        if not devices:
            if hwids is None:
                return refuse(SCAN_NONE, f"No USB serial port is attached. ({problem}.)")
            return refuse(
                SCAN_NONE,
                f"No serial port with a USB id {type_name}'s board declares ({', '.join(shown or [])}) "
                f"is attached. Plug the device in; if it is, those ids are not the ones it "
                f"enumerates under, and build.hwids in its board manifest needs correcting.",
            )
        if hwids is None:
            return refuse(
                SCAN_UNFILTERED,
                f"{type_name}'s board could not be asked which USB ids it enumerates under "
                f"({problem}), so every USB serial port is listed. Pick the one the device is on.",
                devices,
            )
        if not free:
            return refuse(
                SCAN_ALL_TRACKED,
                "Every matching port is a device that is already configured. Plug the new "
                "one in, or pick one of these to write it again as new.",
                devices,
            )
        if len(free) > 1:
            return refuse(
                SCAN_AMBIGUOUS,
                f"{len(free)} unconfigured ports match and nothing can say which is the new "
                f"device. Pick the one at the USB port it is plugged into.",
                devices,
            )
        return CandidateScan(True, None, None, devices, extra={"hwids": shown}, target=free[0], pick=PICK_KEY)

    def first_write(
        self, paths: Paths, settings: Settings, type_name: str, fw: str, found: Mapping[str, Any]
    ) -> tuple[Device, tuple[str, ...]]:
        """The scan's device as the port device every other write here takes.

        The type's own resolved stop list, unshortened. The unit that watches
        for these devices has to be down for the upload to have the port; and
        a picked port that *is* configured is held by Klipper outright.
        """
        from .. import stop_services
        from ..providers import pio as pio_mod

        entry = pio_mod.load(paths)[type_name]
        tty = str(found["tty"])
        device = Device(
            type=type_name,
            id=tty,
            chipset="",
            state=STATE_ESP_ROM,
            fw=fw,
            kind=KIND_PORT,
            detail={
                "env": entry,
                "port": tty,
                # None yet. `write` fills it in from the device's own answer.
                "device_id": "",
                "name": str(found.get("label") or type_name),
                "section": "",
                "first_install": True,
            },
        )
        return device, stop_services.for_platformio(paths, entry, settings)


def _candidate(port: SerialPort, tracked: Sequence[TrackedBoard]) -> dict[str, Any]:
    """One port as a scan device. No `serial` key: that is what a DFU device
    is named by, and a bridge chip's is not this device's."""
    # Compared by tty name, the way `usb.device_for_block` compares a node: a
    # tracked path is whatever a section says, usually a udev symlink.
    owners = [
        board
        for board in tracked
        if board.path and os.path.basename(os.path.realpath(board.path)) == port.tty
    ]
    # Two sections claiming one port: it is still not a free port, so the
    # types are named, but neither section is - see `name_tracked`.
    owner = owners[0] if len(owners) == 1 else None
    vid, pid = port.vid_pid
    return {
        "tty": port.path,
        "port": port.device.name,
        PICK_KEY: port.interface,
        "vid_pid": f"{vid}:{pid}",
        "product": port.device.product,
        "tracked_by": ", ".join(sorted({board.type for board in owners})) or None,
        "known_serial": (owner.serial or None) if owner else None,
        "label": (owner.label or None) if owner else None,
    }


def _announced(bench: Bench, env: PioType, identifier: Identifier | None, port: str, ctx: Any) -> str | None:
    """The id the device just written at `port` says it has, or None.

    Only an answer heard now counts. A remembered entry for this port
    describes whatever sat on it before the write.
    """
    if identifier is None:
        return None
    try:
        heard = identifier.identify(bench.paths, bench.settings, env, ask=True, reporter=ctx.reporter)
    except UpdaterError as exc:
        ctx.reporter("warn", f"could not ask the new '{env.name}' device which it is ({exc}).")
        return None
    real = os.path.realpath(port)
    for ident, device in heard.items():
        if device.answered and device.port and os.path.realpath(device.port) == real:
            return ident
    return None


def _identify(bench: Bench, env: PioType, identifier: Identifier | None, ctx: Any) -> dict[str, Any]:
    """One type's answer, or `{}` - never an exception.

    A host that cannot ask was writing to configured ports perfectly well
    before identity existed, and degrading to that is strictly what it used to
    do; refusing to flash would be a new way to fail. The knomi helper already
    softens its own listen this way. Holding it here too means the guarantee
    does not rest on every identifier getting it right.
    """
    if identifier is None:
        ctx.reporter(
            "warn",
            f"nothing can confirm which '{env.name}' device is on which port - "
            f"writing to the configured ports. A helper: on its [firmware ...] "
            f"section that can identify its devices would.",
        )
        return {}
    try:
        return identifier.identify(bench.paths, bench.settings, env, ask=True, reporter=ctx.reporter)
    except UpdaterError as exc:
        ctx.reporter(
            "warn",
            f"could not ask the '{env.name}' devices which they are ({exc}) - "
            f"writing to their configured ports.",
        )
        return {}


def port_for(
    device: Mapping[str, Any], discovered: dict[str, Any], ctx: Any
) -> tuple[str, Confidence | None, str | None]:
    """Where to write this device, how sure we are, and why not if no answer.

    `device` is the target's `detail`. `(port, confidence, refusal reason)` -
    the same three-tuple `flash.device_for` returns for a board. `confidence`
    is None when nothing confirmed the identity and the port is a remembered
    one.

    Three cases, and the middle one is the point:

    * **Nothing was identified at all** - no identifier, one that could not
      run or heard nothing, or a dry run. Fall back to the configured port,
      which is what every write did before this. No worse than it was, and
      confirmed by nothing, so None.
    * **This device was found** - write to the port it was found on, not the
      one it used to be on. If those differ it moved, and saying so is the only
      warning anybody would ever get. The confidence says whether it answered
      just now or was remembered.
    * **Others were found and this one was not** - it is not there. If a
      listen ran, the ports were free and every other device spoke. If only
      the remembered map was available, it is the only word on what is
      present, and it does not name this device. Either way, a silent write
      to its old port could hit whatever is there now.

    A device with no id at all is the fourth case and falls back rather than
    failing. A `serial:` section names a socket, and its identity only arrives
    from the module's own report - so a module too old to send one, or a device
    that was silent when the list was read, has nothing to match on. Failing
    those would take flashing away from installs that have it today, to punish
    them for what their klippy module does not say.
    """
    from ..discovery import spec as discovery

    configured = device["port"]
    if not discovered:
        return configured, None, None

    ident = device["device_id"]
    if not ident:
        ctx.reporter(
            "warn",
            f"{device['name']} reports no hardware id, so the device on "
            f"{configured} cannot be confirmed as the one meant. Writing to the "
            f"configured port.",
        )
        return configured, None, None

    found = discovered.get(ident)
    if found is None:
        if any(entry.answered for entry in discovered.values()):
            reason = (
                "did not answer when asked which devices are present, so its "
                "port cannot be confirmed. Writing to the port it used to be "
                "on could write to a different device."
            )
        else:
            reason = (
                "is not among the devices its family last saw, and they "
                "could not be asked directly, so its port cannot be "
                "confirmed. Writing to the port it used to be on could "
                "write to a different device."
            )
        return configured, None, reason

    if found.port != configured:
        ctx.reporter(
            "warn",
            f"{device['name']} ({ident}) is on {found.port}, not "
            f"{configured} - it has moved. Writing to where it actually is.",
        )
    reason = discovery.ANSWERED if found.answered else discovery.REMEMBERED
    return found.port, discovery.Confidence(reason), None
