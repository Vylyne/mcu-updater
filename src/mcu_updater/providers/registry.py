"""Which build systems exist, and which of their targets a batch should do.

**Static, and not discovered.** No `pkgutil` walk of this directory, no entry
points. Scanning for modules would mean this process imports whatever `.py`
somebody dropped in - and this process holds the exclusive lock, flashes
firmware, and has NOPASSWD `systemctl` for Klipper. Entry points are not
available either: `install.sh` runs from source over `PYTHONPATH` rather than
pip-installing, so there is no dist-info for anything to register in.

The tuple *is* the seam. Adding a build system is one module and one line here,
which was the point; being able to add one without editing this file was never
part of it.
"""

from __future__ import annotations

import dataclasses

from .kconfig_make import KconfigMake
from .platformio import PlatformIO
from .spec import BuildTarget, Install, Provider, Skipped

#: Every build system, in the order a batch works through them. kconfig first
#: because that is the order MCU builds have always happened in, and a batch
#: that reordered itself would be a behaviour change hiding inside a refactor.
PROVIDERS: tuple[Provider, ...] = (KconfigMake(), PlatformIO())

_BY_NAME: dict[str, Provider] = {p.name: p for p in PROVIDERS}


def by_name(name: str) -> Provider:
    provider = _BY_NAME.get(name)
    if provider is None:
        raise KeyError(f"no build provider {name!r}; known: {sorted(_BY_NAME)}")
    return provider


@dataclasses.dataclass(frozen=True)
class Selection:
    """What a batch would build, and what it could not.

    Two lists rather than one, because "up to date" and "cannot be built" are
    different answers that a single filtered list collapses into silence. Only
    the second is a `Skipped`: a target left out because it is already current is
    the batch working, and saying so would be noise on every run.
    """

    build: list[BuildTarget] = dataclasses.field(default_factory=list)
    skipped: list[Skipped] = dataclasses.field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.build)


def select(
    install: Install,
    *,
    stale_only: bool = True,
    only: str | None = None,
    fw: str | None = None,
) -> Selection:
    """Everything wanting a build, in the order a batch should do it.

    `only` narrows to a single target name, which is what makes "rebuild this
    one type" the same operation with a filter rather than another loop. `fw`
    narrows to a single family - "rebuild katapult everywhere" - and is a filter
    over what each target already uses, never an instruction to build a family
    something does not run. Every target carries a real `fw` (see
    `BuildTarget.fw`), so a filter naming a family nothing here builds simply
    matches nothing, the same as any other name that misses.

    An unnamed `fw` is a sweep, and a sweep passes over on-demand targets - the
    bootloader - rather than rebuilding what is already on the hardware doing its
    job. That is not a skip and is not reported as one: `skipped` is for things
    that *cannot* be built, and a katapult nobody asked for is a thing that need
    not be.

    `stale_only` is the `scope` parameter, already decided. The word stays in the
    agent, which validates it and puts it on the wire; a second copy of the
    vocabulary down here would be one more thing to keep in step.

    Anything not provably current counts as wanting a build. That is the only
    safe collapse: an image we cannot vouch for is exactly the one worth
    rebuilding, and treating "unprovable" as "current" would skip it.
    """
    out = Selection()
    for provider in PROVIDERS:
        for target in provider.targets(install):
            if only is not None and target.name != only:
                continue
            if fw is not None and target.fw != fw:
                continue
            if fw is None and target.on_demand:
                continue
            reason = provider.blocked(install, target)
            if reason is not None:
                out.skipped.append(Skipped(target, reason))
                continue
            if stale_only and provider.artifact_status(install, target).is_current:
                continue
            out.build.append(target)
    return out
