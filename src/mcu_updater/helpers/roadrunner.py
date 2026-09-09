"""Roadrunner's confirmed direct-USB BOOTSEL requester."""

from __future__ import annotations

from typing import Any

from ..discovery import roadrunner
from ..flashers.spec import Bench
from .spec import BootselHandoff


class RoadrunnerHelper:
    """Confirm a provisioned Roadrunner before requesting its BOOTSEL mode."""

    name: str = "roadrunner"

    def request_bootsel(
        self, bench: Bench, *, serial: str, chipset: str, ctx: Any
    ) -> BootselHandoff:
        device = roadrunner.find_provisioned(bench.paths, serial)
        topology = roadrunner.Roadrunner().request_bootsel(bench.paths, device)
        return BootselHandoff(topology=topology.name)


__all__ = ["RoadrunnerHelper"]
