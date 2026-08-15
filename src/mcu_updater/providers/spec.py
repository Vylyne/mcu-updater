"""What a build system has to answer, so a batch never has to know which one.

Two build systems were already here, written as parallel modules rather than as
two instances of one thing. :mod:`~mcu_updater.build` runs kconfig and ``make``
against a klipper-shaped tree; :mod:`~mcu_updater.providers.pio` runs PlatformIO
against an ESP32 one. Each grew its own ``build()``, its own
``artifact_status()``, and its own idea of what "configured enough to build"
means - so every caller wanting both had to know both, and every caller knowing
only one quietly served only one.

The cost was a bug, not an aesthetic complaint. ``build_all`` walked the
``[mcu ...]`` registry because that was the only list it had, so "Build All"
meant "build all the MCUs" and left every screen on whatever it happened to be
running. Nothing said so. There was no seam for it to walk instead.

This is that seam, and it is deliberately small: enumerate, judge, build. It
carries no opinion about *what* a target is - an MCU type with a firmware
family, a PlatformIO env, and later a prebuilt image somebody downloads are all
just names a provider knows how to act on.

**Flashing is not here.** A provider answers questions about files we produce;
writing them to hardware is a different axis with a different set of
implementations (see the plan's Flasher seam), and folding the two together
would mean a provider that fetches a prebuilt image also had to know how to put
it on a board.

**Discovery is not here either.** Which boards exist and where they are is the
Inventory axis, and it stays deferred: there are two implementations of it and
the third is not committed, which is the same criterion that says this one is
ready.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Optional, Protocol

from ..build import Reporter
from ..config import Registry
from ..paths import Paths
from ..settings import Settings
from ..states import ArtifactStatus
from . import pio as pio_mod


@dataclasses.dataclass(frozen=True)
class Install:
    """This host's configuration, parsed once and handed round.

    Providers get given their config rather than reading it, for two reasons.
    Selection asks each provider three questions per target, and re-parsing
    ``mcu-updater.cfg`` for each of them would be a dozen file reads to decide a
    batch. And a batch that re-read the config between two of its own questions
    could answer them about different configurations, which is the sort of bug
    that only shows up while somebody is editing the file.

    Both section maps are here even though no provider uses both. That is
    honest: this is *the config*, not one provider's slice of it, and a provider
    ignoring the half that is not about it costs nothing.
    """

    paths: Paths
    settings: Settings
    #: Types this host builds with kconfig and make.
    registry: Registry
    #: Types this host builds with PlatformIO, with `pio_source` already applied
    #: as the default so a provider never has to know about that fallback.
    displays: dict[str, pio_mod.PioType]

    @classmethod
    def load(cls, paths: Paths, settings: Settings) -> Install:
        return cls(
            paths=paths,
            settings=settings,
            registry=Registry.load(paths),
            displays=pio_mod.load(paths, default_source=settings.pio_source),
        )


@dataclasses.dataclass(frozen=True)
class BuildTarget:
    """One thing one provider can build.

    A key, not a model. `McuType` and `PioType` stay where they are and stay
    what they are; this only says which provider to ask and which of its things
    to ask about. Making it a domain object in its own right would mean a third
    description of a board to keep in step with the two that already exist.
    """

    #: Which provider owns it - the key into `providers.PROVIDERS`.
    provider: str
    #: The `[mcu <name>]` or `[display <name>]` section name.
    name: str
    #: The firmware family, for providers that build several per target. `None`
    #: is not "unknown": it means this provider's targets have no family axis at
    #: all, which is why a `fw` filter correctly excludes them rather than
    #: matching them by accident.
    fw: Optional[str] = None
    #: Built when something asks for it by name, never on a sweep.
    #:
    #: Katapult is the case that needs it. It is already on the hardware doing
    #: the one job it has - getting a board into its bootloader - so a fleet
    #: build that rebuilt it would spend a compile per board on a binary a fleet
    #: *flash* never writes. And the update it would eventually enable is the one
    #: with no way back: a CAN board is reachable only through the bootloader
    #: being replaced.
    #:
    #: Not a rule about katapult, though. It is a provider's own answer to "is
    #: this something a sweep should pick up", and the selector never has to know
    #: which family it is looking at.
    on_demand: bool = False

    def to_json(self) -> dict[str, Optional[str]]:
        # `type` rather than `name`, because that is what every other bulk
        # payload has always called it.
        return {"type": self.name, "fw": self.fw, "provider": self.provider}


@dataclasses.dataclass(frozen=True)
class Skipped:
    """A target a batch passed over, and the reason a human would want.

    Distinct from "already up to date", which is not a skip - it is the batch
    working correctly. This is only for targets that *cannot* be built at all,
    and it exists because the silent version of exactly this is the bug that
    started the whole restructure: a type with no saved config was dropped from
    a fleet build and the batch reported success.
    """

    target: BuildTarget
    reason: str

    def to_json(self) -> dict[str, Optional[str]]:
        return {**self.target.to_json(), "reason": self.reason}


class Provider(Protocol):
    """A build system, as everything outside it needs to see it."""

    #: Registry key, and what goes on the wire. Stable: a saved job payload
    #: names it.
    name: str
    #: What a human calls this build system, for a log line or a panel.
    label: str

    def targets(self, install: Install) -> list[BuildTarget]:
        """Everything this provider could build on this host, in a stable order.

        The provider enumerates its own, because it is the only thing that knows
        what one of its targets *is*. A caller assembling this list would be
        back to branching on kind.
        """
        ...

    def blocked(self, install: Install, target: BuildTarget) -> Optional[str]:
        """Why this cannot be built at all, or None if it can be attempted.

        Only for the once-per-target setup that has to happen outside this tool:
        a saved `.config` for kconfig, a cloned source tree for PlatformIO. It is
        deliberately *not* a prediction of whether the build will succeed - a
        missing toolchain, a syntax error, a full disk are all things to find out
        by trying, and reporting them as failures is more useful than pretending
        we knew.

        The distinction matters because a blocked target is skipped rather than
        failed. Nothing a batch can do about it, and failing the fleet over one
        unconfigured type is worse than proceeding without it.
        """
        ...

    def artifact_status(self, install: Install, target: BuildTarget) -> ArtifactStatus:
        """Is the built image current with respect to its inputs?

        Q1 from :mod:`~mcu_updater.states`, which is why the answer is that
        vocabulary and not a bool: "cannot tell" has to survive the trip, and a
        `stale` batch that treated unprovable as current would skip the builds
        most worth doing.
        """
        ...

    def build(
        self,
        install: Install,
        target: BuildTarget,
        *,
        reporter: Reporter,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        """Produce the image. Raises `UpdaterError` on failure.

        Returns nothing on purpose. The two implementations return different
        things - a `BuildResult` and a path - and a batch wants neither; what it
        wants is whether this raised. Anything needing the detail asks the
        provider's own module, which still has it.
        """
        ...

    def describe(self, target: BuildTarget) -> str:
        """How to name this target in progress narration.

        "cartographer for carto_v4" and "knomi_toolchanger" are both the whole
        truth about their target, and neither reads well in the other's shape.
        """
        ...
