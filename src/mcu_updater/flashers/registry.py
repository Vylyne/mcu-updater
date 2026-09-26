"""Which flashers exist, which one a family picks for a device, and how a
batch splits around the Klipper stop.

**Static, and not discovered** - for the same reason the provider registry is.
A `pkgutil` walk would mean this process imports whatever `.py` somebody dropped
in, and this process holds the exclusive lock, writes firmware to boards, and
has NOPASSWD `systemctl` for Klipper. The tuple is the seam.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from ..artifacts import KIND_BIN, KIND_UF2, Artifact, Staged
from ..errors import NoFlasherError
from ..firmware import missing_section_message
from .bootsel import Bootsel
from .dfu_util import DfuUtil
from .esptool import Esptool
from .flashtool import Flashtool
from .spec import KIND_BARE, KIND_SERIAL, CandidateScanner, Device, Flasher, FlashTarget

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


def candidate_scanner(flasher: Flasher) -> CandidateScanner | None:
    """`flasher` as a `CandidateScanner`, or None when it cannot find a new
    board. The one place that asks - the way helper capabilities are reached."""
    return flasher if isinstance(flasher, CandidateScanner) else None


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


def _bootsel_route(family: FirmwareFamily, device: Device, helper: Helper | None) -> str:
    """The config that would let bootsel write a staged `.uf2` to `device`.

    Only asked when bootsel was not chosen, so either it is unlisted or it
    cannot reach a running board. Listing it alone does not do that: bootsel
    writes a running board only through a helper that asks it for BOOTSEL,
    so a family with no helper is told to add `helper: klipper` too. Empty
    when no config would help: a CAN board cannot be asked for BOOTSEL, and a
    family with some other helper cannot also name `klipper`.
    """
    from .. import helpers

    if device.kind != KIND_SERIAL:
        return ""
    if helpers.bootsel_requester(helper) is not None:
        return f", or add bootsel to [firmware {family.name}]'s flashers to write the .uf2"
    if helper is not None:
        return ""
    if "bootsel" in family.flashers:
        return (
            f", or add `helper: klipper` to [firmware {family.name}] so bootsel can ask "
            f"the running board for BOOTSEL and write the .uf2"
        )
    listed = ", ".join((*family.flashers, "bootsel"))
    return (
        f", or add `flashers: {listed}` and `helper: klipper` to "
        f"[firmware {family.name}] to write the .uf2 through BOOTSEL"
    )


def _refusal_error(
    family: FirmwareFamily,
    device: Device,
    waiting: list[tuple[str, tuple[str, ...]]],
    staged: Staged,
    helper: Helper | None,
) -> NoFlasherError:
    """Why nothing in the list writes `device`, naming what would fix it.

    A flasher that could write the device but was staged nothing it takes is
    a missing build, not a missing flasher, and the message says which. When
    the build staged the *other* image, rebuilding as configured makes the
    same one again, so the message names the setting that decides which image
    an RP2040 build makes instead.
    """
    from .. import firmware

    missing = list(dict.fromkeys(kind for _, kinds in waiting for kind in kinds))
    subject = f"{device.type} {device.id or device.chipset} while it is {device.state}"
    # The offset is a Kconfig answer, so only a kconfig build is told to change
    # it. A CMake tree's `.bin` comes from its own CMakeLists, not an offset.
    kinds_staged = (
        {artifact.kind for artifact in staged.artifacts}
        if family.builder == firmware.DEFAULT_BUILDER
        else set()
    )
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
                f"bootloader offset (16KiB for Katapult) and rebuild"
                f"{_bootsel_route(family, device, helper)}."
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
        raise _refusal_error(
            family, device, _unstaged(family, device, helper), staged, helper
        )
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


@dataclasses.dataclass(frozen=True)
class FirstInstall:
    """Which flasher sets a bare board of one type up, or why none can."""

    #: The install family - bootloader, else application. "" when the type
    #: declares no firmwares at all.
    fw: str
    #: None exactly when nothing on the family's list can do it.
    flasher: str | None
    #: The ROM state that flasher writes ("dfu", "bootsel"); "" with no flasher.
    state: str
    #: Set exactly when `flasher` is None, naming the line to change.
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {"fw": self.fw or None, "flasher": self.flasher, "reason": self.reason}


class _Declared(Protocol):
    """A `typelist.TypeEntry` or an `McuType` - read-only, so both fit."""

    @property
    def name(self) -> str: ...
    @property
    def chipset(self) -> str: ...
    @property
    def firmwares(self) -> Sequence[str]: ...


def _bare(entry: _Declared, fw: str, state: str) -> Device:
    return Device(
        type=entry.name, id="", chipset=entry.chipset, state=state, fw=fw, kind=KIND_BARE
    )


def first_install(entry: _Declared, families: dict[str, FirmwareFamily]) -> FirstInstall:
    """Which flasher on this type's install family can find *and* write a
    bare board of it - the first, in list order, that is a `CandidateScanner`
    and whose `supports()` takes a bare device in one of its own states.

    No builder and no flasher name is compared: `flashtool` and `esptool`
    refuse `KIND_BARE` and cannot scan, so they are never chosen.

    Pure - no bus, no subprocess, no file read - because `fw.status` asks it
    for every row on every poll. Never raises: a row it cannot answer for
    still renders, with the reason.
    """
    from .flash import _no_first_install_writer, install_family

    if not entry.firmwares:
        return FirstInstall(
            "", None, "", f"[type {entry.name}] declares no firmware, so there is nothing to install."
        )
    fw = install_family(entry.firmwares, families)
    family = families.get(fw)
    if family is None:
        return FirstInstall(fw, None, "", missing_section_message(fw))

    listed = [_BY_NAME[n] for n in family.flashers if n in _BY_NAME]
    scanners = [f for f in listed if candidate_scanner(f) is not None]
    for flasher in scanners:
        for state in flasher.states:
            if flasher.supports(_bare(entry, fw, state), None):
                return FirstInstall(fw, flasher.name, state, None)

    if scanners and not entry.chipset:
        return FirstInstall(
            fw,
            None,
            "",
            f"[type {entry.name}] declares no chipset:, and a bare board has "
            f"nothing else to say which boot ROM it speaks. Add chipset: to it.",
        )
    for flasher in FLASHERS:
        if flasher in listed or candidate_scanner(flasher) is None:
            continue
        for state in flasher.states:
            if flasher.supports(_bare(entry, fw, state), None):
                return FirstInstall(
                    fw, None, "", _no_first_install_writer(family, entry.chipset, state)
                )
    if scanners:
        return FirstInstall(
            fw,
            None,
            "",
            f"none of the flashers on [firmware {fw}] that can find a new board "
            f"({', '.join(f.name for f in scanners)}) writes a bare "
            f"{entry.chipset}. Flash it by hand, then track it once it enumerates.",
        )
    return FirstInstall(
        fw,
        None,
        "",
        f"nothing on [firmware {fw}]'s flashers: ({', '.join(family.flashers)}) can "
        f"scan for a new board, so this type cannot be set up from bare yet. "
        f"Flash it by hand, then track it once it enumerates.",
    )


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
