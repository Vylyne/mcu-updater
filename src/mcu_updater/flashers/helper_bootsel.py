"""Closed-loop BOOTSEL flashing through a firmware-specific requester."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from ..discovery.bootsel import mount_for_topology
from ..errors import BootloaderTimeoutError
from ..helpers.spec import BootselRequester
from .bootsel import copy_uf2, ensure_uf2
from .spec import Bench, FlashTarget


class HelperBootsel:
    """Ask a known firmware to enter BOOTSEL, then write its matched mount."""

    name = "helper_bootsel"
    label = "BOOTSEL (firmware handoff)"
    chipsets: tuple[str, ...] = ("rp2040",)
    states: tuple[str, ...] = ()
    needs_services_stopped = True

    @contextlib.contextmanager
    def prepared(
        self, bench: Bench, targets: list[FlashTarget], ctx: Any
    ) -> Iterator[None]:
        yield None

    def write(
        self, bench: Bench, session: Any, target: FlashTarget, ctx: Any
    ) -> dict[str, Any]:
        uf2 = target.detail["uf2_file"]
        ensure_uf2(uf2)
        if bench.settings.dry_run:
            ctx.reporter(
                "info",
                f"[dry-run] would request BOOTSEL then copy {uf2} to its matched volume",
            )
            return {"mount": None}

        helper: BootselRequester = target.detail["helper"]
        handoff = helper.request_bootsel(
            bench,
            serial=target.id,
            chipset=target.detail["chipset"],
            ctx=ctx,
        )
        mount = mount_for_topology(bench.paths, handoff.topology)
        copy_uf2(uf2, mount, ctx)
        return {"mount": mount}

    def settled(self, bench: Bench, target: FlashTarget, ctx: Any) -> None:
        """Wait for helper-confirmed identity before stopped services restart."""
        if bench.settings.dry_run:
            return

        helper: BootselRequester = target.detail["helper"]
        try:
            helper.wait_ready(
                bench,
                serial=target.id,
                chipset=target.detail["chipset"],
                ctx=ctx,
            )
        except BootloaderTimeoutError as exc:
            # The UF2 copy already completed. Match the other flashers: a slow
            # return is worth a warning, not rewriting success as write failure.
            ctx.reporter("warn", str(exc))


def target_for(
    uf2_file: str,
    *,
    type_name: str,
    serial: str,
    chipset: str,
    helper: BootselRequester,
    stop_services: tuple[str, ...] = (),
) -> FlashTarget:
    """A configured firmware target that can request its own BOOTSEL mode."""
    return FlashTarget(
        flasher=HelperBootsel.name,
        type=type_name,
        id=serial,
        stop_services=stop_services,
        detail={"uf2_file": uf2_file, "chipset": chipset, "helper": helper},
    )


__all__ = ["HelperBootsel", "target_for"]
