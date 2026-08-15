"""Writing a set of devices, once, with one service outage.

The loop itself, lifted out of the agent so the CLI runs the same one. It was
``AgentMethods._do_flash_all``, which meant ``mcu-updater flash`` and
``mcu-updater update-all`` had their own - and theirs called ``flash_katapult``
directly, so "every tracked MCU" was the whole truth about what they did and
nothing said screens were not in it. That is the bug ``build_all`` had before
the Provider seam, one layer down: the caller that knew only one implementation
quietly served only one.

**Selection stays outside**, as it does for the flashers themselves. What this
takes is a list somebody else decided on; what it owns is the grouping, the
stop, and the accounting.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..errors import OperationCancelled, UpdaterError
from .registry import by_flasher, group_by_stop
from .spec import Bench, FlashTarget

#: Called once the batch is over and the service is back, to decide whether it
#: really came back. The agent asks Moonraker and will issue a FIRMWARE_RESTART
#: if klippy came up in an error state; the CLI has nobody to ask, so
#: `klipper_stopped` restarting the unit is the whole of its answer.
#:
#: The return is deliberately `Any` and deliberately ignored. The agent's
#: `_await_klippy_ready` hands back the state it settled on, which is useful to
#: its own callers and means nothing to a batch that has already finished
#: writing - pinning this to `None` would only stop it being passed in.
ReadyCheck = Callable[[Any], Any]


class PlainContext:
    """The sliver of a job context a flasher uses, with no job behind it.

    The CLI has no `JobRunner`, so rather than making every flasher take a
    reporter *and* an optional context - two ways to say the same thing, which
    is how they end up disagreeing - it supplies the shape with nothing behind
    it. Lives here rather than in `flash.py` because `write_all` is now its
    other caller and a batch is where the shape is actually defined.
    """

    def __init__(self, reporter: Any) -> None:
        self.reporter = reporter
        self.cancel = None

    def check_cancelled(self) -> None:
        """Nothing to cancel: the CLI runs this synchronously in the foreground,
        so the only interruption available is the one that kills the process."""

    def step(self, label: str = "", index: int = 0, total: int = 0) -> None:
        """No progress bar to drive - the CLI is watching the stream itself."""


def write_all(
    bench: Bench,
    targets: list[FlashTarget],
    ctx: Any,
    *,
    on_ready: Optional[ReadyCheck] = None,
) -> dict[str, Any]:
    """Write every selected device, with Klipper stopped once for the batch.

    Once per batch rather than once per device: ten stop/start cycles would take
    far longer and give ten chances for the restart to be the thing that fails.

    **Grouped by requirement, not by kind.** A board and a screen both need
    Klipper down - for different reasons, neither of which this loop knows - so
    one stop covers both and neither path had to learn about the other. A write
    that needs no stop runs outside it rather than inheriting an outage it does
    not need.

    Cancellation is honoured *between* devices only. Interrupting a write leaves
    half an image on a board, so the check is at the top of each iteration and
    never inside one.
    """
    from ..service import klipper_stopped

    stopped, free = group_by_stop(targets)

    flashed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total = len(targets)
    done = 0

    def write_group(group: list[FlashTarget]) -> None:
        nonlocal done
        for flasher, mine in by_flasher(group):
            # Once per flasher, inside whatever stop it asked for. This is where
            # a port watcher gets paused and the screens are asked which they
            # are - the only moment identity can be resolved rather than
            # remembered.
            with flasher.prepared(bench, mine, ctx) as session:
                for target in mine:
                    # Between devices, never inside a write.
                    ctx.check_cancelled()
                    ctx.step(f"Flashing {target.id} ({target.type})", done, total)
                    done += 1
                    try:
                        extra = flasher.write(bench, session, target, ctx)
                        flashed.append({**target.to_json(), **extra})
                        # After the write and after it is recorded: a device that
                        # came back slowly is still flashed.
                        flasher.settled(bench, target, ctx)
                    except OperationCancelled:
                        raise
                    except UpdaterError as exc:
                        ctx.reporter("warn", f"{target.id}: {exc}")
                        failures.append({**target.to_json(), "error": str(exc)})

    write_group(free)
    if stopped:
        with klipper_stopped(
            bench.paths,
            bench.controller(None),
            f"flash {len(stopped)} device(s)",
            reporter=ctx.reporter,
        ):
            write_group(stopped)
    ctx.step(f"Flashed {len(flashed)} of {total}", total, total)

    if on_ready is not None:
        # klipper_stopped has started the service again by now; confirm it really
        # came back, which is the release gate for every flashing path.
        ctx.reporter("info", "Waiting for Klipper to be ready...")
        on_ready(ctx.reporter)
    return {"flashed": flashed, "failures": failures}


__all__ = ["PlainContext", "ReadyCheck", "write_all"]
