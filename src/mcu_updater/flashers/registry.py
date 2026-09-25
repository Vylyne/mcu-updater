"""Which flashers exist, which one a family picks for a device, and how a
batch splits around the Klipper stop.

**Static, and not discovered** - for the same reason the provider registry is.
A `pkgutil` walk would mean this process imports whatever `.py` somebody dropped
in, and this process holds the exclusive lock, writes firmware to boards, and
has NOPASSWD `systemctl` for Klipper. The tuple is the seam.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..artifacts import KIND_BIN, KIND_UF2, Artifact, Staged
from ..errors import NoFlasherError
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import Device, Flasher, FlashTarget

if TYPE_CHECKING:
    from ..firmware import FirmwareFamily
    from ..helpers.spec import Helper
    from ..paths import Paths

#: Every flasher. Order is not a batch order - a batch keeps the order its
#: selection produced - so this is just the set.
#:
FLASHERS: tuple[Flasher, ...] = (
    Flashtool(),
    Esptool(),
    DfuUtil(),
    Bootsel(),
)

_BY_NAME: dict[str, Flasher] = {f.name: f for f in FLASHERS}


def by_name(name: str) -> Flasher:
    flasher = _BY_NAME.get(name)
    if flasher is None:
        raise KeyError(f"no flasher {name!r}; known: {sorted(_BY_NAME)}")
    return flasher


def needs_services_stopped(target: FlashTarget) -> bool:
    """The target's own answer when it has one, else its flasher's."""
    if target.needs_services_stopped is not None:
        return target.needs_services_stopped
    return by_name(target.flasher).needs_services_stopped


def group_by_stop(
    targets: list[FlashTarget],
) -> tuple[list[FlashTarget], list[FlashTarget]]:
    """Split a batch into (needs Klipper down, does not), preserving order.

    The whole point of the flag. Grouping by *requirement* rather than by kind
    is what lets one `services_stopped` cover boards and screens without either
    loop knowing the other exists - and what would let a write that needs no
    stop stay outside it rather than inheriting one it does not need.
    """
    stopped: list[FlashTarget] = []
    free: list[FlashTarget] = []
    for target in targets:
        (stopped if needs_services_stopped(target) else free).append(target)
    return stopped, free


def stop_services_union(targets: list[FlashTarget]) -> list[str]:
    """The first-seen-order union of `stop_services` across a group.

    One outage covers the whole group rather than one per distinct set - ten
    stop/start cycles is the thing `write_all`'s docstring already argues
    against, and stopping a unit a given target did not ask for is harmless
    since it comes back. Order matters (`services_stopped` stops in list
    order and restarts in reverse), so this is a dict used as an ordered set,
    not a `set()`.
    """
    seen: dict[str, None] = {}
    for target in targets:
        for unit in target.stop_services:
            seen.setdefault(unit, None)
    return list(seen)


def by_flasher(targets: list[FlashTarget]) -> list[tuple[Flasher, list[FlashTarget]]]:
    """Group targets by the flasher that owns them, in first-seen order.

    Order preserved rather than sorted, because a batch's order came from its
    selection - the registry's order for boards, the config file's for screens -
    and reordering it inside a refactor is a behaviour change nobody asked for.
    """
    order: list[str] = []
    groups: dict[str, list[FlashTarget]] = {}
    for target in targets:
        if target.flasher not in groups:
            order.append(target.flasher)
            groups[target.flasher] = []
        groups[target.flasher].append(target)
    return [(by_name(name), groups[name]) for name in order]


# --------------------------------------------------------------------------
# selection: the family's list, in order
# --------------------------------------------------------------------------


def resolve(
    family: FirmwareFamily, device: Device, helper: Helper | None, staged: Staged
) -> tuple[Flasher, Artifact] | None:
    """The first flasher in `family.flashers` that supports `device` and was
    staged a kind it accepts, with that artifact - or None.

    The family's order, not the registry's: the same RP2040 is flashtool's in
    `[firmware klipper]` and bootsel's in `[firmware roadrunner]`, and a global
    first match could only ever reach one of them. A flasher whose kind was not
    staged is passed over, so `flashers: flashtool, bootsel` reaches bootsel,
    through the family's helper, when no `.bin` was staged - flashtool is not
    narrowed by state, so a running or offline board always goes to it first
    when one was.
    """
    for name in family.flashers:
        flasher = by_name(name)
        if not flasher.supports(device, helper):
            continue
        artifact = staged.first_of(flasher.accepts)
        if artifact is not None:
            return flasher, artifact
    return None


def _unstaged(
    family: FirmwareFamily, device: Device, helper: Helper | None
) -> list[tuple[str, tuple[str, ...]]]:
    """Listed flashers that could write `device`, with the kinds they take.

    Only asked after `resolve` found nothing, so every flasher here is one
    whose kind was not staged.
    """
    return [
        (name, by_name(name).accepts)
        for name in family.flashers
        if by_name(name).supports(device, helper)
    ]


def _refusal_error(
    family: FirmwareFamily,
    device: Device,
    waiting: list[tuple[str, tuple[str, ...]]],
    staged: Staged,
) -> NoFlasherError:
    """Why nothing in the list writes `device`, naming what would fix it.

    A flasher that could write the device but was staged nothing it takes is
    a missing build, not a missing flasher, and the message says which. When
    the build staged the *other* image, rebuilding as configured makes the
    same one again, so the message names the setting that decides which image
    an RP2040 build makes instead.
    """
    missing = list(dict.fromkeys(kind for _, kinds in waiting for kind in kinds))
    subject = f"{device.type} {device.id or device.chipset} while it is {device.state}"
    kinds_staged = {artifact.kind for artifact in staged.artifacts}
    if waiting:
        name, kinds = waiting[0]
        if KIND_BIN in kinds_staged and KIND_UF2 not in kinds_staged:
            message = (
                f"{name} could write {subject}, but [firmware {family.name}] staged a .bin "
                f"and no .uf2, and rebuilding as configured will not make one: on an "
                f"RP2040, only a build with no bootloader offset produces a .uf2. Set the "
                f"bootloader offset to none and rebuild, or list a flasher that takes the .bin."
            )
        elif KIND_UF2 in kinds_staged and KIND_BIN not in kinds_staged:
            message = (
                f"{name} could write {subject}, but [firmware {family.name}] staged a .uf2 "
                f"and no .bin, and rebuilding as configured will not make one: on an "
                f"RP2040, only a build with a bootloader offset produces a .bin. Set the "
                f"bootloader offset (16KiB for Katapult) and rebuild, or list bootsel to "
                f"write the .uf2."
            )
        else:
            message = (
                f"{name} could write {subject}, but [firmware {family.name}] staged "
                f"no {' or '.join(kinds)} - build it first."
            )
    else:
        listed = ", ".join(family.flashers) or "(none)"
        message = f"nothing in [firmware {family.name}] (flashers: {listed}) can write {subject}."
    return NoFlasherError(
        message,
        family=family.name,
        flashers=list(family.flashers),
        type=device.type,
        id=device.id,
        chipset=device.chipset,
        state=device.state,
        missing=missing,
    )


def select(
    paths: Paths,
    family: FirmwareFamily,
    device: Device,
    helper: Helper | None,
    *,
    stop_services: tuple[str, ...],
    staged: Staged | None = None,
) -> FlashTarget:
    """The target that writes `device`, chosen by its family.

    `stop_services` is required so no caller can forget it: a flasher that
    needs Klipper down and gets an empty list stops nothing.

    `staged` is what the family's builder staged for `device.type`. Left out,
    it is read here, which is what every production caller does: which file a
    board gets is the builder's answer, never the caller's.

    Raises `NoFlasherError` naming the family and its list when nothing in it
    can write the device, or naming the missing kind when something could but
    was staged nothing it takes. A batch reports that as the device's failure;
    a single-device call raises it.
    """
    if staged is None:
        from .. import providers

        staged = providers.staged(paths, device.type, family)
    choice = resolve(family, device, helper, staged)
    if choice is None:
        raise _refusal_error(family, device, _unstaged(family, device, helper), staged)
    flasher, artifact = choice
    return flasher.target(paths, device, helper, artifact, stop_services=stop_services)


def select_device(
    paths: Paths,
    families: dict[str, FirmwareFamily],
    device: Device,
    *,
    stop_services: tuple[str, ...],
) -> FlashTarget:
    """`select`, for a caller holding a device rather than its family.

    `device.fw` names the family and the family names the helper, so every
    caller asks the same two questions in the same order. Raises
    `ConfigCorruptError` for an undeclared family and `NoFlasherError` as
    `select` does.
    """
    from .. import firmware, helpers

    family = firmware.resolve(paths, device.fw, families)
    helper = helpers.for_name(family.helper, family=family.name)
    return select(paths, family, device, helper, stop_services=stop_services)


def refusal(device: Device, exc: NoFlasherError) -> dict[str, Any]:
    """A device nothing could write, in a batch's `failures[]` shape.

    The uniform slots `FlashTarget.to_json` has, with no flasher because none
    was chosen, the refusal's own sentence as the error, and the kinds a build
    would have to stage for a listed flasher to take it - empty when no listed
    flasher could write the device at all.
    """
    return {
        "type": device.type,
        "id": device.id,
        "flasher": None,
        "error": str(exc),
        "missing": list(exc.data.get("missing", [])),
    }


def select_each(
    paths: Paths,
    families: dict[str, FirmwareFamily],
    requests: Iterable[tuple[Device, tuple[str, ...]]],
) -> tuple[list[FlashTarget], list[dict[str, Any]]]:
    """Select a batch: (targets, refusals), each in request order.

    A device nothing can write is a refusal, not an exception. Spec §8: it is
    reported with the batch's failures and does not abort the rest. Hand the
    refusals to `write_all(refused=...)`.
    """
    targets: list[FlashTarget] = []
    refused: list[dict[str, Any]] = []
    for device, units in requests:
        try:
            targets.append(
                select_device(paths, families, device, stop_services=units)
            )
        except NoFlasherError as exc:
            refused.append(refusal(device, exc))
    return targets, refused
