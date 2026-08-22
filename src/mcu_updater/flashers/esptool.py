"""esptool, through PlatformIO: the screens.

The flasher that made this seam worth having. Every other write here targets a
device by an identity it carries on the bus; a display is an indistinguishable
CH340 and has to be *rediscovered* at the moment of the write, which is only
possible once Klipper is down and the ports are free.

That is one step, in one place, and it is the whole reason `prepared()` exists
on the protocol. Without it the batch loop would have to know that screens need
a discovery pass and boards do not - which is the branching this removes.

:mod:`mcu_updater.providers.pio` keeps its body, including the parts with no MCU
counterpart at all: never letting PlatformIO choose its own upload port, and
following a udev symlink to the device PlatformIO can actually see.

**The display vocabulary here is a caller's, not a limit.** esptool writes any
ESP32 and PlatformIO builds for far more than screens; what is display-shaped is
this module's private `detail` payload and the Klipper-side list it comes from.
The protocol slot is already device-shaped - `FlashTarget` says `type` and `id`,
not `display` and `screen` - so generalising to "a PlatformIO env written to a
port" is a change contained to this file and its target builder. That it *is*
contained is the point of the seam; doing it before something needs it would be
guessing at the shape of the second caller.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from ..devices import STATE_ESP_ROM
from ..errors import FlashError, UpdaterError
from .spec import Bench, FlashTarget

if TYPE_CHECKING:
    # Annotation only. `discovery.spec` imports from this package, so a runtime
    # import here closes a cycle - and `from __future__ import annotations`
    # means nothing needs the symbol at run time. The sweep that produces these
    # is already imported lazily inside `_sightings_by_family` for the same
    # reason.
    from ..discovery.spec import Confidence


class Esptool:
    """Writes one device of one PlatformIO type."""

    name = "esptool"
    label = "esptool (PlatformIO)"
    chipsets: tuple[str, ...] = ("esp32",)
    states: tuple[str, ...] = (STATE_ESP_ROM,)
    #: The klippy module holds the port open for the write itself, and
    #: pyserial's exclusive open is an advisory flock that both it and esptool
    #: take. Unlike flashtool, this one is about the write and not about
    #: getting somewhere first.
    needs_services_stopped = True

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[dict[str, dict[str, Any]]]:
        """Ask the screens which they are, now that the ports are free.

        The watcher pause that used to happen here is gone: it is now part of
        the outer stop `write_all` opens over the batch's own
        `stop_services` union, verified and journaled rather than best-effort
        - see `flashers.batch.write_all` and `service.services_stopped`.

        Discovery needs the ports free, and it is the only moment identity
        can be *resolved* rather than remembered. The screen list was read
        before the stop, so its paths describe where these screens were; a
        remembered path is what the whole device-id scheme exists to avoid.

        Once per family rather than once per screen: a single listen covers
        every port at once, and doing it per screen would multiply the six
        seconds by the number of displays.
        """
        families = {}
        for target in targets:
            display = target.detail["display"]
            families[display.name] = display

        yield {
            name: discover(bench, display, ctx)
            for name, display in families.items()
        }

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from ..providers import pio as pio_mod

        display = target.detail["display"]
        screen = target.detail["screen"]

        port, confidence, problem = port_for(
            screen, (session or {}).get(display.name) or {}, ctx
        )
        if problem is not None:
            # Raised rather than collected, because a batch records a failure by
            # catching one. The check itself is unchanged: a screen that stayed
            # silent while every other one answered is not there, and writing to
            # the path it used to be on would write to whatever is on that path
            # now.
            raise FlashError(problem, type=display.name, port=port)

        result = pio_mod.upload(
            bench.paths, bench.settings, display, port, reporter=ctx.reporter
        )

        _record(bench, display, screen, confidence)

        return {"name": screen["name"], "port": port, **result}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait for. A screen is not on the Klipper bus, so there is
        no device node whose absence would bring Klipper up in an error state -
        which is the only thing the MCU wait is protecting against."""


def _record(
    bench: Bench, display: Any, screen: dict, confidence: Confidence | None
) -> None:
    """Note which image this screen now holds, and how it was identified.

    The display half of the ledger `flash.flash_katapult` has always kept for a
    board. Without it a screen's `confidence` on the wire is a literal null, so
    the strongest identification this tool performs - asking the screen itself,
    with the ports free - leaves no trace and reads as "we cannot vouch for
    this".

    Three ways to record nothing, all of them correct:

    * **A dry run.** Nothing was written, so nothing is true afterwards. Same
      guard, for the same reason, as the board path's.
    * **A screen with no hardware id.** There is no durable name to file it
      under, and the port is not one - see `build.display_key`.
    * **An unwritable log.** `FlashLog.record` swallows it: a lost record is not
      worth failing a flash that already succeeded.

    `confidence` is passed through rather than assumed. It is None whenever the
    port was a remembered one, and recording `answered` for a write we could not
    confirm would be the one lie this whole field exists to prevent.
    """
    if bench.settings.dry_run:
        return

    ident = (screen.get("device_id") or screen.get("reported_id") or "").lower()
    if not ident:
        return

    from ..build import FlashLog, display_key
    from ..providers import pio as pio_mod

    # The build already hashed the image and noted its commit; re-deriving them
    # here would be a second answer to a question with a recorded one.
    side = pio_mod.read_sidecar(bench.paths, display) or {}
    FlashLog(bench.paths).record(
        display_key(ident),
        mcu_type=display.name,
        fw=display.env,
        bin_sha256=side.get("bin_sha256"),
        # The display sidecar calls the tree commit `sha`; the flash log calls
        # it `fw_sha`. One rename at the boundary, rather than teaching either
        # side the other's vocabulary.
        fw_sha=side.get("sha"),
        confidence=confidence.reason if confidence is not None else None,
    )


@dataclasses.dataclass(frozen=True)
class _Answered:
    """One `discovery.Sighting`, in the shape `port_for` already reads.

    `port_for` was written against `providers.pio.WatcherDevice` and reads
    `.port` - kept exactly as it is, per this step's own rule, rather than
    switched onto `Sighting.address` under a different name.

    `confidence` is the `Confidence` that came with the sighting, carried
    whole rather than reduced to its reason: which source answered is a fact
    about the sweep, and by the time `port_for` runs the sweep is over. Kept as
    the object because that is what `flash.device_for` hands back for a board,
    and because `tone`/`safe_to_write` are derived from the reason rather than
    stored beside it - reducing to a string here would mean rebuilding it to
    ask anything but "which reason". A `Listen` sighting is `ANSWERED`.
    """

    port: str
    confidence: Confidence | None = None


def _sightings_by_family(bench: Bench, ctx: Any) -> dict[str, dict[str, _Answered]]:
    """One `discovery.confirm()` sweep per batch, cached on `ctx`, grouped back
    into per-family maps.

    `discover()` below is called once per display family - a single listen
    pass already covers every configured family's ports at once, so
    re-sweeping per family would multiply the six seconds by the number of
    families in the batch. `confirm()`'s sources scan every configured family
    in one pass regardless of who asks, so the sweep itself only needs to
    happen once; this caches that on `ctx`, which lives for exactly one
    `prepared()`/`write()` batch, so it needs no cache key and cannot leak
    between batches.

    Grouped by `detail["family"]` - `Sighting` carries no family field of its
    own (`detail` is deliberately a source's private payload, not a second
    identity scheme), so `Listen`/`Watcher` stash it there for exactly this.
    """
    cached = getattr(ctx, "_esptool_sightings_by_family", None)
    if cached is not None:
        return cached

    from ..discovery.confirm import confirm
    from ..discovery.registry import SOURCES

    result: dict[str, dict[str, _Answered]] = {}
    for sighting, confidence in confirm(bench, sources=SOURCES).values():
        family = sighting.detail.get("family")
        if not isinstance(family, str):
            continue
        result.setdefault(family, {})[sighting.id] = _Answered(
            port=sighting.address, confidence=confidence
        )

    ctx._esptool_sightings_by_family = result
    return result


def discover(bench: Bench, display: Any, ctx: Any) -> dict[str, Any]:
    """Ask every screen of one family which it is, now that the ports are free.

    Never fatal. Discovery needs pyserial and the display source tree, and a
    host missing either was flashing by configured path perfectly well before
    this existed - degrading to that is strictly what it used to do, whereas
    refusing to flash would be a new way to fail.

    Skipped entirely on a dry run: it opens real serial ports, and a rehearsal
    that touches hardware is not a rehearsal.
    """
    if bench.settings.dry_run:
        ctx.reporter("info", "[dry-run] would ask the displays which they are")
        return {}
    try:
        return _sightings_by_family(bench, ctx).get(display.name, {})
    except UpdaterError as exc:
        ctx.reporter(
            "warn",
            f"could not ask the displays which they are ({exc}) - falling back to "
            f"the ports Klipper reported before it stopped.",
        )
        return {}


def port_for(
    screen: dict, discovered: dict[str, Any], ctx: Any
) -> tuple[str, Confidence | None, str | None]:
    """Where to write this screen, how sure we are, and why not if no answer.

    `(port, confidence, refusal reason)` - the same three-tuple
    `flash.device_for` returns for a board, which was written to match this
    function and now matches it in shape as well as in spirit. `confidence` is
    None when nothing confirmed the identity and the port is a remembered one.

    Three cases, and the middle one is the point:

    * **Nothing was discovered at all** - no pyserial, no source tree, or a dry
      run. Fall back to the configured path, which is what every flash did
      before this. No worse than it was, and confirmed by nothing, so None.
    * **This screen answered** - write to the port it answered on, not the one
      it used to be on. If those differ it moved, and saying so is the only
      warning anybody would ever get. This is the case that carries a real
      confidence: the screen was asked directly, with the ports free.
    * **Others answered and this one did not** - it is not there. The ports were
      free and every other screen spoke, so a silent write to its old path would
      be a write to whatever is on that path now.

    A screen with no id at all is the fourth case and falls back rather than
    failing. A `serial:` section names a socket, and its identity only arrives
    from the module's own report - so a module too old to send one, or a screen
    that was silent when the list was read, has nothing to match on. Failing
    those would take flashing away from installs that have it today, to punish
    them for what their klippy module does not say.
    """
    configured = screen["configured_path"]
    if not discovered:
        return configured, None, None

    ident = (screen.get("device_id") or screen.get("reported_id") or "").lower()
    if not ident:
        ctx.reporter(
            "warn",
            f"{screen['name']} reports no hardware id, so the screen on "
            f"{configured} cannot be confirmed as the one meant. Writing to the "
            f"configured port.",
        )
        return configured, None, None

    found = discovered.get(ident)
    if found is None:
        return configured, None, (
            "did not answer when asked which displays are present, so its "
            "port cannot be confirmed. Writing to the port it used to be on "
            "could write to a different screen."
        )

    if found.port != configured:
        ctx.reporter(
            "warn",
            f"{screen['name']} ({ident}) answered on {found.port}, not "
            f"{configured} - it has moved. Writing to where it actually is.",
        )
    return found.port, found.confidence, None


def target_for(
    display: Any, screen: dict, *, stop_services: tuple[str, ...] = ()
) -> FlashTarget:
    """One screen Klipper reported, as a target.

    Both the family and the screen entry are carried whole. The family is what
    `pio run -e` needs; the screen entry is what `port_for` matches on, and it
    has to be the one read *before* the stop - only a running Klipper can
    produce it.

    `stop_services` is resolved by the caller (`stop_services.py`, against the
    display's own config, its firmware family and `[updater]`) - this factory
    just carries it onto the target, same as `flasher` and `type`.
    """
    return FlashTarget(
        flasher=Esptool.name,
        type=display.name,
        id=screen["configured_path"],
        stop_services=stop_services,
        detail={"display": display, "screen": screen},
    )
