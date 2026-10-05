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
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from .. import helpers
from ..artifacts import KIND_PIO_ENV, Artifact
from ..devices import STATE_ESP_ROM
from ..errors import FlashError, UpdaterError
from .spec import KIND_PORT, Bench, Device, FlashRecord, FlashTarget

if TYPE_CHECKING:
    # Annotation only. `discovery.spec` imports from this package, so a runtime
    # import here closes a cycle - and `from __future__ import annotations`
    # means nothing needs the symbol at run time. `port_for` imports the module
    # it builds one from lazily, for the same reason.
    from ..discovery.spec import Confidence
    from ..helpers.spec import Helper, Identifier
    from ..paths import Paths
    from ..providers.pio import PioType


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

    def supports(self, device: Device, helper: Helper | None) -> bool:
        """A PlatformIO device reached through its configured port. Its
        identity, if its family has a way to know one, is confirmed at write
        time, in `prepared`."""
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
            env = target.detail["env"]
            by_type[env.name] = (env, target.detail["identifier"])
        yield {name: _identify(bench, env, identifier, ctx) for name, (env, identifier) in by_type.items()}

    def write(self, bench: Bench, session: Any, target: FlashTarget, ctx: Any) -> dict[str, Any]:
        from ..providers import pio as pio_mod

        env = target.detail["env"]
        port, confidence, problem = port_for(target.detail, (session or {}).get(env.name) or {}, ctx)
        if problem is not None:
            # Raised rather than collected, because a batch records a failure by
            # catching one. The check itself is unchanged: a device that stayed
            # silent while every other one answered is not there, and writing to
            # the port it used to be on would write to whatever is on that port
            # now.
            raise FlashError(problem, type=env.name, port=port)

        result = pio_mod.upload(bench.paths, bench.settings, env, port, reporter=ctx.reporter)

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
