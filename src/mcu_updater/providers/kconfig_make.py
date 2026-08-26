"""kconfig + make: the platform the whole tool was designed around.

A thin adapter, and deliberately so. :mod:`~mcu_updater.build` keeps every line
of its body - the makefile patching, the provenance sidecar, the streamed
process plumbing - because this is not a rewrite. It is the recognition that the
thing `build.py` does was always one instance of something, and this names the
something.

That also keeps the diff reviewable and every mutation anchor in `build.py`
alive. Nothing here decides anything; it translates a `BuildTarget` into the
arguments `build.py` has always taken.
"""

from __future__ import annotations

import os
import threading

from .. import build as build_mod
from .. import firmware
from ..build import Reporter
from ..states import ArtifactStatus
from .spec import BuildTarget, Install


class KconfigMake:
    """Builds one firmware family for one `[mcu ...]` type."""

    name = "kconfig_make"
    label = "kconfig + make"

    def targets(self, install: Install) -> list[BuildTarget]:
        """Every (type, family) pair the registry describes.

        **Pairs, not types.** A type builds every family it uses - its
        application and, when it has one, katapult - and those are not the same
        family for every type. Iterating types and applying one global family to
        all of them is what silently skipped a `firmware: cartographer` board
        from a fleet build: it had no klipper `.config`, so the family it did not
        run was the one asked for, and the batch reported success.

        `families()` rather than `fw_order()`: a cartographer board carries
        klipper config keys it does not use, and building them would compile the
        wrong tree.

        The bootloader is enumerated but marked on-demand, so "rebuild katapult
        everywhere" still works and nothing rebuilds it by accident. See
        `BuildTarget.on_demand` for why that asymmetry is the right one.
        """
        families = firmware.load(install.paths)
        out: list[BuildTarget] = []
        for name in install.registry.names():
            for family in install.registry.get(name).families():
                out.append(
                    BuildTarget(
                        self.name,
                        name,
                        family,
                        on_demand=firmware.resolve(install.paths, family, families).bootloader,
                    )
                )
        return out

    def blocked(self, install: Install, target: BuildTarget) -> str | None:
        """Has this pair been through menuconfig?

        The one gate, and nothing else. A missing source tree is *not* checked
        here on purpose: that is a real failure worth reporting rather than a
        setup step to wait for, and the build raises a `SourceTreeMissingError`
        naming the directory, which is a better answer than a silent skip.
        """
        fw = self._family(target)
        if not os.path.exists(install.paths.config_file(target.name, fw)):
            return (
                f"'{target.name}' has no saved {fw} configuration yet - "
                f"run menuconfig for it once first."
            )
        return None

    def artifact_status(self, install: Install, target: BuildTarget) -> ArtifactStatus:
        fw = self._family(target)
        mcu = install.registry.get(target.name)
        return build_mod.artifact_status(
            install.paths, target.name, fw, extra_repos=mcu.fw_get(fw).extra_repos
        )

    def build(
        self,
        install: Install,
        target: BuildTarget,
        *,
        reporter: Reporter,
        cancel: threading.Event | None = None,
    ) -> None:
        build_mod.build(
            install.paths,
            install.registry,
            install.settings,
            target.name,
            self._family(target),
            reporter=reporter,
            cancel=cancel,
        )

    def describe(self, target: BuildTarget) -> str:
        return f"{target.fw} for {target.name}"

    @staticmethod
    def _family(target: BuildTarget) -> str:
        """The family, insisted upon rather than defaulted.

        Every target this provider makes carries one. An empty `fw` arriving
        here means a target was hand-built for the wrong provider, and
        defaulting to klipper would turn that into the exact silent-wrong-tree
        build the pair shape exists to prevent.
        """
        if not target.fw:
            raise ValueError(
                f"{target.name!r} reached the kconfig_make provider with no firmware "
                f"family; every target it enumerates carries one."
            )
        return target.fw
