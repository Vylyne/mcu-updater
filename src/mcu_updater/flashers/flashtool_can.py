"""Katapult's flashtool.py, addressed by CAN uuid instead of a by-id serial.

Mirrors `flashtool.py`'s shape as closely as possible - see that module's
docstring first. What actually differs is narrow: `-d <path>` becomes
`-i <interface> -u <uuid>`, and there is no `-d` counterpart to discover up
front, because `canbus_uuids:` stores no interface (see
`discovery/canbus.py`'s module docstring for why not). This flasher discovers
the right interface itself, at write time, by trying the operation against
every interface `discovery.canbus.list_can_interfaces` currently reports -
the user's own "just broadcast to all networks and see what falls out"
suggestion, rather than a persisted mapping that would need to be kept
correct across OS interface renumbering.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from ..devices import STATE_KATAPULT, STATE_KLIPPER
from .spec import Bench, FlashTarget


class FlashtoolCan:
    """Writes one tracked CAN-addressed board through its Katapult bootloader.

    Declares the *same* `chipsets`/`states` as `Flashtool` - a CAN-addressed
    stm32 in STATE_KLIPPER is still an stm32 in STATE_KLIPPER - but must
    **never** be reachable through `flashers.registry.select_for`.
    `select_for` matches purely on declared chipsets/states with no separate
    per-device lookup, so if it were allowed to compete on that alone a
    CAN-addressed board would silently match plain `Flashtool` first (same
    declared capability, registered first) - a wrong route, not a raised
    error. Callers that mean to write a CAN board must go through this
    class's own `target_for` directly, never through `select_for`. This
    class still joins the `FLASHERS` tuple so `by_flasher`/`group_by_stop` in
    `flashers/batch.py` work generically over a batch that mixes CAN and
    by-id targets.
    """

    name = "flashtool_can"
    label = "Katapult flashtool (CAN)"
    #: Same two states `Flashtool` answers to, and the same reasoning: the
    #: write itself reboots the board from one into the other. See the class
    #: docstring above for why this must never be reached via `select_for`
    #: despite matching the identical pair `Flashtool` declares.
    chipsets: tuple[str, ...] = ("stm32", "rp2040")
    states: tuple[str, ...] = (STATE_KLIPPER, STATE_KATAPULT)
    #: Mirrors `Flashtool.needs_services_stopped` exactly, per the user's own
    #: design intent: the CAN flasher should look like the USB one wherever
    #: nothing about CAN actually forces a difference. There is also no
    #: evidence yet that a block-by-block CAN write completes reliably while
    #: klippy drives other traffic on the same bus at the same time, so this
    #: stays the safe default until that is actually tested on the bench.
    needs_services_stopped = True

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        """Nothing to set up. Katapult answers on the board's own node."""
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        from .flash import flash_katapult_can

        flash_katapult_can(
            bench.paths,
            bench.settings,
            target.type,
            target.id,
            fw=target.detail.get("fw"),
            reporter=ctx.reporter,
            # Absent for any caller that never sets it - a batch across more
            # than one board never should. Mirrors flashtool.py's own target_for.
            force=bool(target.detail.get("force", False)),
            # Known ahead of time only for an already-tracked, currently
            # connected board (the agent's `mcu_constants.CANBUS_BRIDGE`
            # cross-reference) - `None` means "discover by trial", which
            # `flash_katapult_can` does via the probe-then-write fallback
            # described in its own docstring.
            bridge=target.detail.get("bridge"),
        )
        # `uuid` as well as the uniform `id` - `id` *is* the uuid for this
        # flasher, but naming it `uuid` explicitly is what a caller reading
        # the wire result actually expects, the same reason a by-id result
        # also carries `serial` beside `id`.
        return {"uuid": target.id}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """No re-enumeration to wait for.

        `Flashtool.settled()` waits for a board to reappear on `/dev/serial/
        by-id` because starting Klipper before that node exists brings it up
        in an error state. A CAN node has no such node to wait for - Klipper
        finds it by broadcasting on the bus, which is exactly what a normal
        klippy start already does, whether the board answered a moment ago or
        an hour ago. Per `Flasher.settled`'s own contract ("never fatal, the
        write already succeeded") a no-op is the honest answer here, not a
        best-effort re-probe that could only ever report something the batch's
        own post-restart readiness check already covers better.
        """
        return None


def target_for(
    board: dict[str, Any], uuid: str, *, stop_services: tuple[str, ...] = ()
) -> FlashTarget:
    """One CAN-addressed board, as a target a batch can write.

    `uuid` is named explicitly rather than pulled out of `board["serial"]`
    the way `flashtool.target_for` reads its identity - a CAN uuid is not a
    serial, the same false-cognate reasoning that keeps `canbus_uuids:` its
    own config key rather than folding into `serials:`.

    No interface is threaded through here, on purpose: `canbus_uuids:` stores
    no interface (see `discovery/canbus.py`), so there is nothing for a
    caller to resolve at target-construction time. `FlashtoolCan.write`
    discovers it itself, per-write, by trying every interface currently
    reported by `discovery.canbus.list_can_interfaces`.

    `stop_services` is resolved by the caller against the board's type
    exactly as `flashtool.target_for`'s docstring describes - see
    `stop_services.py`. Nothing CAN-specific about that resolution.
    """
    return FlashTarget(
        flasher=FlashtoolCan.name,
        type=board["type"],
        id=uuid,
        stop_services=stop_services,
        detail=board,
    )


__all__ = ["FlashtoolCan", "target_for"]
