"""esptool, through PlatformIO: the screens.

The flasher that made this seam worth having. Every other write here targets a
device by an identity it carries on the bus; a display is an indistinguishable
CH340 and has to be *rediscovered* at the moment of the write, which is only
possible once Klipper is down and the ports are free.

That is one step, in one place, and it is the whole reason `prepared()` exists
on the protocol. Without it the batch loop would have to know that screens need
a discovery pass and boards do not - which is the branching this removes.

:mod:`mcu_updater.displays` keeps its body, including the parts with no MCU
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
from collections.abc import Iterator
from typing import Any, Optional

from ..errors import FlashError, UpdaterError
from .spec import Bench, FlashTarget


class Esptool:
    """Writes one screen of one `[display ...]` family."""

    name = "esptool"
    label = "esptool (PlatformIO)"
    #: The klippy module holds the port open for the write itself, and
    #: pyserial's exclusive open is an advisory flock that both it and esptool
    #: take. Unlike flashtool, this one is about the write and not about
    #: getting somewhere first.
    needs_klipper_stopped = True

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[dict[str, dict[str, Any]]]:
        """Pause each family's watcher, then ask the screens which they are.

        In that order, and both inside the Klipper stop. It is the order the
        knomi_serial docs give: Klipper holds the port, and the watcher merely
        contends for it - it opens any port that appears and has not been
        identified yet, so if one turns up at the moment esptool wants it, one
        of them loses.

        Discovery comes last because it needs the ports free, and it is the only
        moment identity can be *resolved* rather than remembered. The screen
        list was read before the stop, so its paths describe where these screens
        were; a remembered path is what the whole device-id scheme exists to
        avoid.

        Once per family rather than once per screen: a single listen covers
        every port at once, and doing it per screen would multiply the six
        seconds by the number of displays.
        """
        from ..service import paused

        families = {}
        for target in targets:
            display = target.detail["display"]
            families[display.name] = display

        with contextlib.ExitStack() as stack:
            for display in families.values():
                if display.service:
                    stack.enter_context(
                        paused(bench.controller(display.service), reporter=ctx.reporter)
                    )
            yield {
                name: discover(bench, display, ctx)
                for name, display in families.items()
            }

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from .. import displays as displays_mod

        display = target.detail["display"]
        screen = target.detail["screen"]

        port, problem = port_for(screen, (session or {}).get(display.name) or {}, ctx)
        if problem is not None:
            # Raised rather than collected, because a batch records a failure by
            # catching one. The check itself is unchanged: a screen that stayed
            # silent while every other one answered is not there, and writing to
            # the path it used to be on would write to whatever is on that path
            # now.
            raise FlashError(problem, type=display.name, port=port)

        result = displays_mod.upload(
            bench.paths, bench.settings, display, port, reporter=ctx.reporter
        )

        previous = displays_mod.record_mac(
            bench.paths, port, result.get("mac"), display.env
        )
        if previous:
            # Not an error, and not fatal: the write succeeded. But a different
            # display answering on this port means something was re-cabled, and
            # nothing else would ever say so.
            ctx.reporter(
                "warn",
                f"{screen['name']} on {port} is now MAC {result.get('mac')}, "
                f"was {previous} - a display appears to have moved.",
            )
        return {"name": screen["name"], "port": port, **result, "moved_from": previous}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Nothing to wait for. A screen is not on the Klipper bus, so there is
        no device node whose absence would bring Klipper up in an error state -
        which is the only thing the MCU wait is protecting against."""


def discover(bench: Bench, display: Any, ctx: Any) -> dict[str, Any]:
    """Ask every screen of one family which it is, now that the ports are free.

    Never fatal. Discovery needs pyserial and the display source tree, and a
    host missing either was flashing by configured path perfectly well before
    this existed - degrading to that is strictly what it used to do, whereas
    refusing to flash would be a new way to fail.

    Skipped entirely on a dry run: it opens real serial ports, and a rehearsal
    that touches hardware is not a rehearsal.
    """
    from .. import displays as displays_mod

    if bench.settings.dry_run:
        ctx.reporter("info", "[dry-run] would ask the displays which they are")
        return {}
    try:
        return displays_mod.discover(
            bench.paths, bench.settings, display, reporter=ctx.reporter
        )
    except UpdaterError as exc:
        ctx.reporter(
            "warn",
            f"could not ask the displays which they are ({exc}) - falling back to "
            f"the ports Klipper reported before it stopped.",
        )
        return {}


def port_for(
    screen: dict, discovered: dict[str, Any], ctx: Any
) -> tuple[str, Optional[str]]:
    """Where to write this screen, and why not if there is no answer.

    Three cases, and the middle one is the point:

    * **Nothing was discovered at all** - no pyserial, no source tree, or a dry
      run. Fall back to the configured path, which is what every flash did
      before this. No worse than it was.
    * **This screen answered** - write to the port it answered on, not the one
      it used to be on. If those differ it moved, and saying so is the only
      warning anybody would ever get.
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
        return configured, None

    ident = (screen.get("device_id") or screen.get("reported_id") or "").lower()
    if not ident:
        ctx.reporter(
            "warn",
            f"{screen['name']} reports no hardware id, so the screen on "
            f"{configured} cannot be confirmed as the one meant. Writing to the "
            f"configured port.",
        )
        return configured, None

    found = discovered.get(ident)
    if found is None:
        return configured, (
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
    return found.port, None


def target_for(display: Any, screen: dict) -> FlashTarget:
    """One screen Klipper reported, as a target.

    Both the family and the screen entry are carried whole. The family is what
    `pio run -e` needs and what names the watcher; the screen entry is what
    `port_for` matches on, and it has to be the one read *before* the stop -
    only a running Klipper can produce it.
    """
    return FlashTarget(
        flasher=Esptool.name,
        type=display.name,
        id=screen["configured_path"],
        detail={"display": display, "screen": screen},
    )
