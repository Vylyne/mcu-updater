"""Seeding a type's menuconfig answers from the tree that defines the board.

A Cartographer V4's ``.config`` is 138 lines. Seven of them are answers::

    CONFIG_LOW_LEVEL_OPTIONS=y
    CONFIG_MACH_STM32=y
    CONFIG_MACH_STM32G431=y
    CONFIG_STM32_CLOCK_REF_24M=y
    CONFIG_SCANNER=y
    CONFIG_CARTOGRAPHER_G431_ENABLE=y
    CONFIG_VERSION="CARTOGRAPHER 6.2.0"

The other 131 - ``USBSERIAL``, ``CANSERIAL``, ``FLASH_APPLICATION_ADDRESS``,
``CLOCK_FREQ``, every ``WANT_*`` - are computed from those seven by Kconfig
itself. The USB and CAN builds differ by exactly one answer
(``STM32_CANBUS_PA11_PA12``); the "lite" build differs by exactly one more
(``FOR_K1``, which means Creality K1 and not "feature-reduced"). Asking a user
to produce that file by hand, in an ncurses UI, is asking them to get seven
things right out of a menu that offers hundreds.

**The answers are harvested from the firmware tree, never stored here.**
Cartographer ships ``config.CartoV4USB`` and friends in their fork's root, and
that file is the vendor's statement about their own board. Copying those seven
lines into this repository would make us the owner of somebody else's hardware
definition and guarantee it goes stale - visibly so, because ``CONFIG_VERSION``
is maintained by hand in those files (the tree's own Kconfig default still says
``6.0.0`` while every shipped config says ``6.2.0``). Reading them out of the
tree means ``git pull`` in their fork picks up the next bump for free.

**Katapult is derived, not seeded.** There is no vendor config for it, and
writing a second table describing the same board is how the two drift into
disagreement. So the bootloader's answers are taken from the application's:
every answer the bootloader tree also defines is carried across, and anything
it does not know about - ``SCANNER``, ``CARTOGRAPHER_G431_ENABLE``, ``VERSION``
- is dropped by that same test rather than by a hand-maintained skip list.

**The one invariant is checked rather than assumed.** Katapult's
``LAUNCH_APP_ADDRESS`` is the address it jumps to; the application's
``FLASH_APPLICATION_ADDRESS`` is where the application was linked to run. Those
two agreeing is the whole of "the board boots". They are separate answers in
separate trees, each of which builds and flashes perfectly happily when wrong,
so :func:`derive_bootloader` compares them and refuses rather than producing a
matched pair of binaries that brick the board between them.

Nothing here locks anything. A profile-managed config is an ordinary ``.config``
that the ncurses menuconfig and the panel's editor can both still change; what
this adds is that the change becomes *visible* (:func:`status` reports
``customised``) instead of silent. A lock users cannot override gets worked
around by editing the file on disk, which is strictly worse - then nobody knows.

**Editing one is not a dead end.** Picking a vendor profile means *tracking* it:
the vendor bumps their config and you get the bump. Editing it means you are on
*your own* profile, and the bump becomes informational. That second half only
works if the user's answers have somewhere to live, which is what
:func:`capture_custom` is: a custom profile is shaped exactly like a vendor seed
- a short list of answer lines - so it is offered by :func:`available`, resolved
by :func:`find`, and consumed by :func:`apply_seed` through the code path that
already existed. Switching back and forth is then lossless, and
:func:`refuse_if_customised` stops being the end of the road.

It stores the *minimal answers* rather than the whole file, for the same reason
:func:`apply_seed` re-emits rather than copies: the other 131 lines are
recomputed from the current tree on reload, and two answer lists make "what did
you change" a set comparison rather than a Kconfig parse.
"""

from __future__ import annotations

import dataclasses
import glob
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Optional

from . import firmware
from .errors import (
    OffsetMismatchError,
    ProfileCustomisedError,
    ProfileError,
    ProfileNotFoundError,
)
from .paths import Paths
from .providers import kconfig
from .states import TONE_ATTENTION, TONE_OK

#: Vendor seed files live in the tree root and are named ``config.<Variant>``.
#: Cartographer's fork ships eight of them. Upstream Klipper ships none, which
#: is the correct answer for upstream Klipper - there is no such thing as "the"
#: config for a tree that builds for two hundred boards.
SEED_PREFIX = "config."

#: The user's own answers, offered beside the vendor's under a reserved name.
#: Deliberately spelled like a seed file: it *is* one, it just lives in the data
#: tree instead of the firmware tree, so :func:`valid_seed_name`'s whitelist
#: holds for it unchanged and no caller needs a second code path to name it. A
#: vendor shipping this exact name is shadowed rather than shown twice.
CUSTOM_PROFILE = "config.custom"

#: Shipped by whoever defines the board.
ORIGIN_VENDOR = "vendor"
#: Captured from what the user actually answered.
ORIGIN_CUSTOM = "custom"

#: Which profile a custom one was forked from, recorded in its own header. In
#: the file rather than in the record beside it, because it is a fact about
#: *this* profile that has to survive the type switching away to another one and
#: back - and a comment is invisible to both kconfiglib and :func:`parse_answer`.
PARENT_TAG = "# forked-from:"

#: The parent's own answers, at the moment of the fork, one per tagged line.
#:
#: Kept rather than re-read from the vendor's file because the two are not the
#: same kind of list. A vendor seed is hand-maintained and carries computed
#: lines - ``USBSERIAL``, ``CLOCK_FREQ`` - that a minimal capture correctly
#: omits, so diffing one against the other reports a dozen changes nobody made.
#: Reducing the vendor's file to its minimal answers would cost a Kconfig parse
#: every time anybody asked "what did I change" - a question ``fw.status``
#: answers, and every state event rebuilds that. Seven lines of duplication buys
#: an exact answer for free.
BASE_TAG = "# base:"

#: Where the application was linked to run, in the application's tree.
APP_ADDRESS_SYMBOL = "FLASH_APPLICATION_ADDRESS"

#: Where the bootloader jumps, in the bootloader's tree. Same name on every
#: architecture Katapult supports (stm32, rp2040, lpc176x), which is what makes
#: the agreement check architecture-independent.
LAUNCH_ADDRESS_SYMBOL = "LAUNCH_APP_ADDRESS"


# --------------------------------------------------------------------------
# what a tree offers
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Seed:
    """One answer file that a type can be seeded from.

    Two origins, one shape. A `vendor` seed sits in the firmware tree and is the
    vendor's statement about their own board; a `custom` one sits in the data
    tree and is the user's about theirs. Everything downstream - `find`,
    `apply_seed`, the picker - treats them identically, which is the whole point
    of giving a custom profile the shape of a seed rather than machinery of its
    own.
    """

    #: Basename as it appears in the tree, e.g. ``config.CartoV4USB``, or
    #: :data:`CUSTOM_PROFILE` for the user's own.
    name: str
    fw: str
    path: str
    origin: str = ORIGIN_VENDOR
    #: Custom only: the profile this was forked from, if it was forked from one.
    #: What lets a UI say "yours, forked from CartoV4USB" - and what makes going
    #: back a named button rather than a `force` flag.
    parent: Optional[str] = None
    #: Custom only: what `parent` answered at the moment of the fork, so
    #: :func:`overrides` compares two minimal lists rather than a minimal one
    #: against a hand-maintained file. A tuple because this is frozen.
    base: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Optional[str]]:
        return {
            "name": self.name,
            "fw": self.fw,
            "path": self.path,
            "origin": self.origin,
            "parent": self.parent,
        }


def valid_seed_name(name: str) -> str:
    """Check a seed name is a basename in the tree root and nothing else.

    The name reaches here from a browser, and it is about to be joined onto a
    source tree path. ``../../.ssh/id_rsa`` is not a config file, but it would
    be read as one and its contents parsed, so this is a whitelist rather than
    a check for separators: it must look like a file the vendor ships.
    """
    stripped = name.strip()
    if not stripped:
        raise ProfileError("a profile name cannot be empty.", profile=name)
    if stripped != os.path.basename(stripped) or os.path.isabs(stripped):
        raise ProfileError(
            f"'{name}' is not a plain file name. A profile names a file in the "
            f"firmware tree's own root, not a path.",
            profile=name,
        )
    if not stripped.startswith(SEED_PREFIX):
        raise ProfileError(
            f"'{name}' is not a profile: these are the vendor's own "
            f"'{SEED_PREFIX}<variant>' files in the root of the firmware tree.",
            profile=name,
        )
    return stripped


def available(
    paths: Paths,
    fw: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    *,
    mcu_type: Optional[str] = None,
) -> list[Seed]:
    """Every profile a type could be seeded from: the tree's, and its own.

    An absent or unreadable tree yields an empty list rather than raising: "this
    firmware offers no profiles" and "this firmware is not installed" are
    answered by different things, and a listing call should not be the one to
    break the news.

    `mcu_type` is what brings the custom slot into the answer, and it is optional
    because "what does this tree ship" is still a question worth asking without
    one. The custom profile comes first: it is this board's own, and a picker
    that buries it under eight vendor variants sorted alphabetically is one where
    nobody finds their own answers again.
    """
    family = firmware.resolve(paths, fw, families)
    fw_dir = family.source_dir(paths)
    out: list[Seed] = []
    if mcu_type:
        own = read_custom(paths, mcu_type, fw)
        if own is not None:
            out.append(own)
    for path in sorted(glob.glob(os.path.join(fw_dir, SEED_PREFIX + "*"))):
        # A vendor shipping our reserved name is shadowed rather than listed
        # twice under one name, which would make the picker ambiguous about
        # which of the two a click means.
        if os.path.isfile(path) and os.path.basename(path) != CUSTOM_PROFILE:
            out.append(Seed(name=os.path.basename(path), fw=fw, path=path))
    return out


def find(
    paths: Paths,
    fw: str,
    name: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    *,
    mcu_type: Optional[str] = None,
) -> Seed:
    """Locate one profile, naming the real alternatives when it isn't there."""
    wanted = valid_seed_name(name)
    seeds = available(paths, fw, families, mcu_type=mcu_type)
    for seed in seeds:
        if seed.name == wanted:
            return seed
    fw_dir = firmware.resolve(paths, fw, families).source_dir(paths)
    raise ProfileNotFoundError(
        f"{fw} has no profile named '{wanted}'. Looked in {fw_dir}."
        + (
            f" It ships: {', '.join(s.name for s in seeds)}."
            if seeds
            else " That tree ships no profiles at all - is it the right one, and pulled?"
        ),
        profile=wanted,
        fw=fw,
        path=fw_dir,
        available=[s.name for s in seeds],
    )


# --------------------------------------------------------------------------
# the user's own profile
# --------------------------------------------------------------------------


def read_custom(paths: Paths, mcu_type: str, fw: str) -> Optional[Seed]:
    """This type's own saved answers for `fw`, if it has any.

    None rather than an exception for every way of not having one - never
    captured, deleted, unreadable. A missing custom profile is the normal state
    of nearly every type, and the caller's next line is the same in all of them.
    """
    path = paths.custom_profile_file(mcu_type, fw)
    if not os.path.isfile(path):
        return None
    parent, base = _read_header(path)
    return Seed(
        name=CUSTOM_PROFILE,
        fw=fw,
        path=path,
        origin=ORIGIN_CUSTOM,
        parent=parent,
        base=base,
    )


def _read_header(path: str) -> tuple[Optional[str], tuple[str, ...]]:
    """The ``# forked-from:`` and ``# base:`` lines a capture wrote.

    Stops at the first answer line: the header is a header, and scanning a whole
    file for tags that belong at the top invites finding one in a comment
    somebody pasted in.
    """
    parent: Optional[str] = None
    base: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if parse_answer(line) is not None:
                    break
                text = line.strip()
                if text.startswith(PARENT_TAG):
                    parent = text[len(PARENT_TAG) :].strip() or None
                elif text.startswith(BASE_TAG):
                    answer = text[len(BASE_TAG) :].strip()
                    if answer:
                        base.append(answer)
    except OSError:
        return None, ()
    return parent, tuple(base)


def capture_custom(
    paths: Paths,
    mcu_type: str,
    fw: str,
    *,
    answers: Optional[list[str]] = None,
    parent: Optional[str] = None,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
) -> Seed:
    """Save what this type currently answers as a profile of its own.

    `answers` is the minimal answer set. Pass it whenever the tree is already
    parsed - a save from :class:`~mcu_updater.providers.kconfig.KconfigSession` has it in
    hand, and re-deriving it there would spend a second parse to learn something
    already known. Omitting it costs one parse, which is the price of catching an
    edit made out of band by ``make menuconfig``.

    Raises rather than degrading quietly, unlike :func:`write_record`. That one
    loses a *verdict* and the type reads as unmanaged; this one loses the only
    copy of the user's answers, and the caller about to overwrite their
    ``.config`` needs to hear that it failed before it does so.
    """
    if answers is None:
        config = paths.config_file(mcu_type, fw)
        if not os.path.isfile(config):
            raise ProfileError(
                f"'{mcu_type}' has no saved {fw} config at {config}, so there are "
                f"no answers to keep.",
                type=mcu_type,
                fw=fw,
                path=config,
            )
        fw_dir = firmware.resolve(paths, fw, families).source_dir(paths)
        _module, kconf = kconfig.parse_tree(fw_dir, config)
        answers = kconfig.minimal_answers(kconf, fw_dir)

    resolved, base = _forked_from(paths, mcu_type, fw, parent)
    path = paths.custom_profile_file(mcu_type, fw)
    lines = [
        f"# {mcu_type}'s own {fw} answers, saved by mcu-updater.",
        f"{PARENT_TAG} {resolved}" if resolved else PARENT_TAG,
        f"# captured: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        *(f"{BASE_TAG} {line}" for line in base),
        *answers,
    ]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise ProfileError(
            f"could not save '{mcu_type}'s own {fw} answers to {path}: {exc}",
            type=mcu_type,
            fw=fw,
            path=path,
        ) from exc
    return Seed(
        name=CUSTOM_PROFILE,
        fw=fw,
        path=path,
        origin=ORIGIN_CUSTOM,
        parent=resolved,
        base=base,
    )


def _forked_from(
    paths: Paths, mcu_type: str, fw: str, parent: Optional[str]
) -> tuple[Optional[str], tuple[str, ...]]:
    """Which profile a capture should say it came from, and what that answered.

    A recapture while already on the custom profile keeps the *original* fork
    point rather than recording "forked from myself" - which would lose the one
    name that makes "back to CartoV4USB" offerable, and would do so on the second
    edit, when nobody is watching. The baseline is kept with it, for the same
    reason and by the same rule.

    A first capture takes both from the record, which at that moment still
    describes the parent seeding - so both sides of the comparison are minimal
    answer lists produced the same way. Nothing is invented when the record does
    not name a parent: an empty baseline means :func:`overrides` reports nothing
    rather than reporting a diff against a file that is not comparable.
    """
    record = read_record(paths, mcu_type, fw)
    recorded = record.get("profile") if record else None
    existing = read_custom(paths, mcu_type, fw)

    resolved = parent if parent and parent != CUSTOM_PROFILE else None
    if resolved is None and existing is not None:
        return existing.parent, existing.base
    if resolved is None:
        resolved = recorded if recorded != CUSTOM_PROFILE else None
    if resolved is None:
        return None, ()

    if existing is not None and existing.parent == resolved and existing.base:
        return resolved, existing.base
    if record is not None and recorded == resolved:
        return resolved, tuple(str(line) for line in record.get("answers") or [])
    return resolved, ()


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------


@dataclasses.dataclass
class SeedResult:
    """What one seeding actually did."""

    type: str
    fw: str
    profile: str
    config_path: str
    #: The minimal answers, as ``CONFIG_X=y`` lines. What a UI should show.
    answers: list[str]
    #: sha256 of the seed file we read, so a vendor bump is detectable.
    source_sha256: Optional[str] = None
    #: sha256 of the .config we wrote, so a later hand-edit is detectable.
    config_sha256: Optional[str] = None
    backup: Optional[str] = None
    #: Derivation only: answers carried from the application's config, and
    #: those the bootloader tree does not define. Empty for a plain seed.
    carried: list[str] = dataclasses.field(default_factory=list)
    dropped: list[str] = dataclasses.field(default_factory=list)
    #: Set when answers that were about to be overwritten were kept first, as
    #: this type's own profile. Reported rather than silent: "your edits are at
    #: config.custom" is the difference between a `force` a user can undo and one
    #: they only find out about afterwards.
    kept: Optional[str] = None
    #: Derivation only: the two addresses that were compared, once they agreed.
    #: An int, because `_address` parses the hex and compares numerically -
    #: 0x8002000 and 0x08002000 are the same address and two trees need not
    #: spell it the same way.
    app_address: Optional[int] = None

    def to_record(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "source_sha256": self.source_sha256,
            "config_sha256": self.config_sha256,
            "answers": list(self.answers),
            "carried": list(self.carried),
            "dropped": list(self.dropped),
            "app_address": self.app_address,
            "at": time.time(),
        }

    def to_json(self) -> dict[str, Any]:
        out = self.to_record()
        out.update(
            {
                "type": self.type,
                "fw": self.fw,
                "config_path": self.config_path,
                "backup": self.backup,
                "kept": self.kept,
            }
        )
        return out


def apply_seed(
    paths: Paths,
    mcu_type: str,
    fw: str,
    name: str,
    *,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    force: bool = False,
) -> SeedResult:
    """Write this type's answers for `fw` from the tree's own seed file.

    The seed is *loaded and re-emitted* rather than copied. That is deliberate
    and is the whole reason this goes through kconfiglib: loading applies the
    vendor's answers and lets the current tree recompute everything that
    follows from them, so a seed written against last year's Kconfig picks up
    symbols added since instead of leaving them absent. It is exactly what
    ``make olddefconfig`` does, minus needing a terminal.

    Answers this would overwrite are kept first, as this type's own profile. That
    is what makes `force` something other than a data-loss switch: an edit made
    out of band - ``make menuconfig`` over SSH, an editor in Mainsail - has never
    been through :func:`capture_custom`, and this is the last moment anybody can
    save it.
    """
    seed = find(paths, fw, name, families, mcu_type=mcu_type)
    family = firmware.resolve(paths, fw, families)
    fw_dir = family.source_dir(paths)
    target = paths.config_file(mcu_type, fw)

    refuse_if_customised(paths, mcu_type, fw, force=force)
    kept = None
    if seed.origin != ORIGIN_CUSTOM:
        # Skipped when seeding *from* the custom slot: that is "discard my edits
        # and go back to my saved answers", and capturing first would overwrite
        # the file we are about to read with the edits being discarded.
        kept = _keep_current_answers(paths, mcu_type, fw, families)

    _module, kconf = kconfig.parse_tree(fw_dir, seed.path)
    answers = kconfig.minimal_answers(kconf, fw_dir)
    backup = kconfig.save_config(kconf, fw_dir, target)

    result = SeedResult(
        type=mcu_type,
        fw=fw,
        profile=seed.name,
        config_path=target,
        answers=answers,
        source_sha256=_sha256(seed.path),
        config_sha256=_sha256(target),
        backup=backup,
        kept=kept.name if kept is not None else None,
    )
    write_record(paths, mcu_type, fw, result)
    return result


def _keep_current_answers(
    paths: Paths,
    mcu_type: str,
    fw: str,
    families: Optional[dict[str, firmware.FirmwareFamily]],
) -> Optional[Seed]:
    """Capture answers about to be lost, if there are any that are not ours.

    Only for a ``customised`` config. One that still matches its record holds
    nothing but what a profile put there, so capturing it would file the vendor's
    answers under the user's name and put a profile in the picker that is a copy
    of the one above it.
    """
    state = status(paths, mcu_type, fw, families)
    if state.reason != CUSTOMISED:
        return None
    return capture_custom(
        paths, mcu_type, fw, parent=state.profile, families=families
    )


def derive_bootloader(
    paths: Paths,
    mcu_type: str,
    app_fw: str,
    boot_fw: str = firmware.BOOTLOADER,
    *,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    force: bool = False,
) -> SeedResult:
    """Write the bootloader's answers from the application's, and check them.

    Carried across: every answer in the application's minimal config that the
    bootloader tree *also defines*. That test is what drops ``SCANNER`` and
    ``VERSION`` while keeping ``MACH_STM32G431``, ``STM32_CLOCK_REF_24M`` and
    the communication interface - without a list in this file that would have
    to be revised for each new board.

    Then the one thing that matters is verified: that Katapult will jump to the
    address the application was linked for. See :class:`OffsetMismatchError`.
    """
    app_dir = firmware.resolve(paths, app_fw, families).source_dir(paths)
    app_config = paths.config_file(mcu_type, app_fw)
    if not os.path.isfile(app_config):
        raise ProfileError(
            f"'{mcu_type}' has no saved {app_fw} config at {app_config}, so there "
            f"is nothing to derive {boot_fw}'s from. Seed the application first.",
            type=mcu_type,
            fw=app_fw,
            path=app_config,
        )

    boot_dir = firmware.resolve(paths, boot_fw, families).source_dir(paths)
    target = paths.config_file(mcu_type, boot_fw)
    refuse_if_customised(paths, mcu_type, boot_fw, force=force)

    app_module, app_kconf = kconfig.parse_tree(app_dir, app_config)
    answers = kconfig.minimal_answers(app_kconf, app_dir)
    app_address = _address(app_kconf, APP_ADDRESS_SYMBOL)

    # Parsed once bare, only to ask which symbols this tree defines at all.
    _probe_module, probe = kconfig.parse_tree(boot_dir)
    carried, dropped = _partition(answers, probe)

    # Applied through a file rather than a sequence of set_value calls: see
    # kconfig.parse_tree. An assignment made while its symbol is still
    # invisible is remembered; one made through set_value in the wrong order
    # is not, and a silently dropped clock reference is a board that never
    # enumerates.
    boot_module, boot_kconf = _load_answers(boot_dir, carried)

    refused = _refused(boot_module, boot_kconf, carried)
    if refused:
        carried = [line for line in carried if line not in refused]
        dropped.extend(refused)

    launch = _address(boot_kconf, LAUNCH_ADDRESS_SYMBOL)
    _check_addresses(mcu_type, app_fw, boot_fw, app_address, launch)

    backup = kconfig.save_config(boot_kconf, boot_dir, target)
    result = SeedResult(
        type=mcu_type,
        fw=boot_fw,
        profile=f"derived:{app_fw}",
        config_path=target,
        answers=kconfig.minimal_answers(boot_kconf, boot_dir),
        source_sha256=_sha256(app_config),
        config_sha256=_sha256(target),
        backup=backup,
        carried=carried,
        dropped=dropped,
        app_address=app_address,
    )
    write_record(paths, mcu_type, boot_fw, result)
    return result


def reseed_if_moved(
    paths: Paths,
    mcu_type: str,
    fw: str,
    *,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Take the vendor's updated answers, if that is all that has changed.

    Called from :func:`mcu_updater.build.build`, so every way of starting a
    build gets it - the panel, the CLI, a fleet build, update-all - and "when is
    it safe to rewrite somebody's config" is answered once rather than per entry
    point. Never from :func:`status`, which only reports: a writer on the path
    that describes a config is the failure class ``config_rewritten`` exists to
    surface rather than to join.

    Only on ``seed_moved``. That state means the saved config still matches our
    record and it is the vendor's file that moved, so nothing of the user's is
    being discarded. A ``customised`` config is left alone even when asked: you
    are on your own profile, and the bump is informational until you say
    otherwise.

    Returns the profile that was taken, or None if there was nothing to take.
    """
    state = status(paths, mcu_type, fw, families)
    if state.reason != SEED_MOVED or not state.profile:
        return None

    if state.profile.startswith("derived:"):
        # A bootloader config is a function of the application's, so its "seed"
        # moving means the application was reseeded. Re-derive rather than seed,
        # or the pair drifts apart on exactly the answer the offset check exists
        # to catch.
        app_fw = state.profile[len("derived:") :]
        if log:
            log(f"re-deriving {fw} from {app_fw}, which was reseeded")
        derive_bootloader(paths, mcu_type, app_fw, fw, families=families)
    else:
        if log:
            log(f"reseeding from {state.profile}, which the vendor updated")
        apply_seed(paths, mcu_type, fw, state.profile, families=families)
    return state.profile


def _check_addresses(
    mcu_type: str,
    app_fw: str,
    boot_fw: str,
    app_address: Optional[int],
    launch: Optional[int],
) -> None:
    """Refuse a pair that would build cleanly and not boot.

    One side having the symbol and the other not is refused too. That is the
    case where the check silently becomes a no-op, and a check that quietly
    stops checking is worse than no check - it reads as verified.
    """
    if app_address is None and launch is None:
        # Neither tree has the concept. Nothing to agree about.
        return
    if app_address is None or launch is None:
        missing = (
            f"{app_fw} has no {APP_ADDRESS_SYMBOL}"
            if app_address is None
            else f"{boot_fw} has no {LAUNCH_ADDRESS_SYMBOL}"
        )
        raise OffsetMismatchError(
            f"cannot verify where {boot_fw} would hand control to {app_fw} for "
            f"'{mcu_type}': {missing}. Refusing to write a config whose one "
            f"safety check cannot run - set both by hand and check them yourself.",
            type=mcu_type,
            app_fw=app_fw,
            boot_fw=boot_fw,
        )
    if app_address != launch:
        raise OffsetMismatchError(
            f"{boot_fw} would jump to {launch:#x} but {app_fw} is linked to run "
            f"at {app_address:#x} for '{mcu_type}'. Both build and flash fine "
            f"and the board would not come back. Set {boot_fw}'s application "
            f"offset to match, or change the application's bootloader offset.",
            type=mcu_type,
            app_fw=app_fw,
            boot_fw=boot_fw,
            app_address=f"{app_address:#x}",
            launch_address=f"{launch:#x}",
        )


def _load_answers(fw_dir: str, lines: list[str]) -> tuple[Any, Any]:
    with tempfile.TemporaryDirectory(prefix="mcu-updater-derive-") as tmp:
        path = os.path.join(tmp, "derived.config")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        return kconfig.parse_tree(fw_dir, path)


# --------------------------------------------------------------------------
# reading answer lines
# --------------------------------------------------------------------------


def parse_answer(line: str) -> Optional[tuple[str, str]]:
    """``CONFIG_X=y`` or ``# CONFIG_X is not set`` -> ``("X", "y" | "n")``.

    The ``is not set`` form matters: a minimal config uses it for a bool whose
    default is y and whose answer is n, so treating it as a comment would carry
    a symbol across at the wrong value rather than not at all.
    """
    text = line.strip()
    if text.startswith("# CONFIG_") and text.endswith(" is not set"):
        return text[len("# CONFIG_") : -len(" is not set")].strip(), "n"
    if not text.startswith("CONFIG_") or "=" not in text:
        return None
    name, _, value = text.partition("=")
    return name[len("CONFIG_") :].strip(), value.strip()


def answer_lines(path: str) -> list[str]:
    """The answer lines of a seed file, headers and blank lines dropped.

    Deliberately not a Kconfig load. Reading a seed as text is what keeps "which
    of these eight profiles differ, and how" on the cheap path - eight small
    files and a set comparison, against eight tree parses.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return [line.strip() for line in fh if parse_answer(line) is not None]
    except OSError:
        return []


def answer_map(lines: Iterable[str]) -> dict[str, str]:
    """``["CONFIG_X=y"]`` -> ``{"X": "y"}``, last one winning."""
    out: dict[str, str] = {}
    for line in lines:
        parsed = parse_answer(line)
        if parsed is not None:
            out[parsed[0]] = parsed[1]
    return out


def distinguishing(seeds: Sequence[Seed]) -> dict[str, list[dict[str, Optional[str]]]]:
    """Per profile, the answers that set it apart from the others offered.

    ``config.CartoV4USB`` and ``config.CartoV4CAN`` differ by one answer out of
    seven, and their names are the only other thing telling them apart - so a
    picker showing all seven shows six identical lines under every entry and
    buries the one that matters.

    **Disagreement counts; absence does not.** A symbol is distinguishing when
    two profiles that both answer it answer it differently. One file mentioning
    ``USBSERIAL`` and the next omitting it is not a statement about the board:
    vendor seeds are hand-maintained and carry computed lines inconsistently,
    and a custom profile is minimal by construction, so treating "not mentioned"
    as a value makes every entry differ from every other in a dozen places -
    which is the noise this exists to remove. Each entry then lists only the
    answers it actually gives.
    """
    parsed = {seed.name: answer_map(answer_lines(seed.path)) for seed in seeds}
    if len(parsed) < 2:
        return {name: [] for name in parsed}

    symbols = {sym for answers in parsed.values() for sym in answers}
    differing = {
        sym
        for sym in symbols
        if len({a[sym] for a in parsed.values() if sym in a}) > 1
    }
    return {
        name: [
            {
                "symbol": sym,
                "value": answers[sym],
                # Rendered as-is by anything without a Kconfig tree to hand.
                "line": _answer_line(sym, answers[sym]),
            }
            for sym in sorted(differing & set(answers))
        ]
        for name, answers in parsed.items()
    }


def _answer_line(symbol: str, value: Optional[str]) -> str:
    if value is None:
        return f"# CONFIG_{symbol} unanswered"
    if value == "n":
        return f"# CONFIG_{symbol} is not set"
    return f"CONFIG_{symbol}={value}"


def overrides(
    paths: Paths,
    mcu_type: str,
    fw: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
) -> list[dict[str, Optional[str]]]:
    """What this type's own profile changed, against the one it forked from.

    The question a customised target actually raises - "fine, but what did I
    change?" - answered without a Kconfig parse, because both sides are minimal
    answer lists captured the same way. Empty when the type has no captured
    profile, or when the capture has no baseline to measure against: an honest
    nothing rather than a diff against a file that is not comparable.
    """
    own = read_custom(paths, mcu_type, fw)
    if own is None or not own.base:
        return []

    before = answer_map(own.base)
    after = answer_map(answer_lines(own.path))
    return [
        {
            "symbol": sym,
            "was": before.get(sym),
            "now": after.get(sym),
            "line": _answer_line(sym, after.get(sym)),
        }
        for sym in sorted(set(before) | set(after))
        if before.get(sym) != after.get(sym)
    ]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _partition(answers: list[str], probe: Any) -> tuple[list[str], list[str]]:
    """Split answer lines by whether the target tree defines the symbol.

    ``probe.syms`` also holds symbols that are merely *referenced* somewhere -
    a condition mentioning a symbol another tree owns puts it there with no
    defining node - so membership alone is not the same question. Hence the
    ``nodes`` test.

    That test is a first filter and not the authoritative one. Deleting it
    alone leaves the whole suite passing, because :func:`_refused` catches the
    same case a step later: an undefined symbol's ``str_value`` in kconfiglib
    is its own name, which never equals the value being carried, so it is
    rejected there and ends up in ``dropped`` either way. It stays because it
    is the cheap check that says *why* - "this tree has no such setting" rather
    than "this tree would not take that value" - and because a filter that
    reads the answer's meaning belongs before one that reads its effect.
    """
    carried: list[str] = []
    dropped: list[str] = []
    for line in answers:
        parsed = parse_answer(line)
        if parsed is None:
            continue
        sym = probe.syms.get(parsed[0])
        (carried if sym is not None and sym.nodes else dropped).append(line)
    return carried, dropped


def _refused(module: Any, kconf: Any, carried: list[str]) -> list[str]:
    """Carried lines the target tree did not actually take.

    kconfiglib remembers a user value even where it cannot apply it - a symbol
    held by a ``select``, or one whose dependencies this tree does not satisfy -
    so "we wrote it into the file" is not the same as "it took". Reported rather
    than raised: most of these are legitimately inapplicable, and the offset
    check is what stands between a partial carry and an unbootable board.
    """
    serializer = kconfig.Serializer(module)
    out: list[str] = []
    for line in carried:
        parsed = parse_answer(line)
        if parsed is None:
            continue
        name, wanted = parsed
        sym = kconf.syms.get(name)
        if sym is None:
            out.append(line)
            continue
        kind = serializer.type_name(sym.orig_type)
        if not kconfig.same_value(kind, _unquote(wanted), sym.str_value):
            out.append(line)
    return out


def _address(kconf: Any, name: str) -> Optional[int]:
    """A hex symbol's value as an int, or None if this tree has no such symbol.

    Compared numerically rather than as text on purpose: ``0x8002000`` and
    ``0x08002000`` are the same address and two trees need not spell it the
    same way.
    """
    sym = kconf.syms.get(name)
    if sym is None or not sym.nodes:
        return None
    raw = (sym.str_value or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


def _sha256(path: str) -> Optional[str]:
    from .build import sha256_file

    return sha256_file(path)


def write_record(paths: Paths, mcu_type: str, fw: str, result: SeedResult) -> None:
    """Note what was seeded. Never raises - losing this degrades the answer to
    "unmanaged", which is what every install predating profiles reads as."""
    path = paths.profile_file(mcu_type, fw)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result.to_record(), fh, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def read_record(paths: Paths, mcu_type: str, fw: str) -> Optional[dict[str, Any]]:
    try:
        with open(paths.profile_file(mcu_type, fw), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def forget(paths: Paths, mcu_type: str, fw: str) -> bool:
    """Detach a type from its profile, leaving the .config exactly as it is."""
    try:
        os.unlink(paths.profile_file(mcu_type, fw))
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------

#: No profile was ever applied. The normal state of every type predating this,
#: and not a problem - hence an OK tone rather than an amber one.
UNMANAGED = "unmanaged"
#: The .config no longer matches what the profile put there. Not necessarily
#: wrong; just no longer something this tool can vouch for.
CUSTOMISED = "customised"
#: The seed file in the firmware tree changed since it was applied - the vendor
#: bumped their config. Reseeding is what resolves it.
SEED_MOVED = "seed_moved"

#: `customised` is an OK tone, not an unknown one. It reported "this tool cannot
#: vouch for these answers" while there was nowhere to put them; now that
#: :func:`capture_custom` gives them a home, being on your own profile is a
#: destination rather than drift, and painting it amber would nag at the one
#: state a user deliberately chose.
_PROFILE_TONE: dict[Optional[str], str] = {
    None: TONE_OK,
    UNMANAGED: TONE_OK,
    CUSTOMISED: TONE_OK,
    SEED_MOVED: TONE_ATTENTION,
}

PROFILE_REASONS = tuple(r for r in _PROFILE_TONE if r is not None)

_PROFILE_LABEL: dict[Optional[str], str] = {
    None: "Matches profile",
    UNMANAGED: "Not profile-managed",
    CUSTOMISED: "Your own answers",
    SEED_MOVED: "Profile updated - reseed available",
}

#: What a type tracking its own captured profile reads as. Distinct from
#: `customised` by one thing only: those answers were saved, so switching away
#: and back is lossless. "Matches profile" is true of it and tells nobody whose.
_CUSTOM_LABEL = "Your own profile"


@dataclasses.dataclass(frozen=True)
class ProfileStatus:
    """Whether a saved .config still says what its profile said."""

    reason: Optional[str] = None
    profile: Optional[str] = None
    #: Only for a type tracking :data:`CUSTOM_PROFILE`: what that was forked
    #: from. Elsewhere `profile` already names the fork point, because a
    #: customised config's record still names the vendor seed it drifted from.
    parent: Optional[str] = None

    def __post_init__(self) -> None:
        if self.reason not in _PROFILE_TONE:
            raise ValueError(
                f"unknown profile reason {self.reason!r}; "
                f"expected None or one of {', '.join(PROFILE_REASONS)}"
            )

    @property
    def managed(self) -> bool:
        return self.reason != UNMANAGED

    @property
    def custom(self) -> bool:
        """Whether the profile being tracked is this type's own."""
        return self.profile == CUSTOM_PROFILE

    @property
    def tone(self) -> str:
        return _PROFILE_TONE[self.reason]

    @property
    def label(self) -> str:
        if self.custom and self.reason is None:
            return _CUSTOM_LABEL
        return _PROFILE_LABEL[self.reason]

    def to_json(self) -> dict[str, Any]:
        return {
            "managed": self.managed,
            "profile": self.profile,
            "custom": self.custom,
            "parent": self.parent,
            "reason": self.reason,
            "tone": self.tone,
            "label": self.label,
        }


def status(
    paths: Paths,
    mcu_type: str,
    fw: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
    *,
    config_sha: Optional[str] = None,
) -> ProfileStatus:
    """Does this type's .config still hold what its profile put there?

    ``customised`` takes precedence over ``seed_moved``: both can be true at
    once, and the one that changes what a caller may safely do is the local
    edit. Reseeding over it is the thing that would lose work.

    **Read-only, and cheap, because every client's whole picture is rebuilt from
    the call that asks it.** `fw.status` is recomputed on every state event - a
    mutation, a finished job, a service-state change, a reconnect - so a Kconfig
    parse here would be paid on all of them. It stays two file hashes.

    And it never reseeds on the vendor's behalf. A verdict that quietly rewrote
    the thing it was asked to describe is the failure class ``config_rewritten``
    exists to surface, not to join; taking a vendor bump belongs in
    :func:`reseed_if_moved`, which a build calls.

    `config_sha` is for a caller that has already hashed the ``.config`` -
    :meth:`Api.artifact` asks this and `artifact_status` about the same file at
    the same moment. None means "read it here".
    """
    record = read_record(paths, mcu_type, fw)
    if record is None:
        return ProfileStatus(UNMANAGED)

    name = record.get("profile")
    # One extra small read, and only for a type on its own profile: everywhere
    # else `profile` already names the thing the UI would call the parent.
    parent = (
        _read_header(paths.custom_profile_file(mcu_type, fw))[0]
        if name == CUSTOM_PROFILE
        else None
    )
    current = (
        config_sha if config_sha is not None else _sha256(paths.config_file(mcu_type, fw))
    )
    if current != record.get("config_sha256"):
        return ProfileStatus(CUSTOMISED, profile=name, parent=parent)

    seeded = record.get("source_sha256")
    source = _current_source_sha(paths, mcu_type, fw, name, families)
    if seeded and source and seeded != source:
        return ProfileStatus(SEED_MOVED, profile=name, parent=parent)
    return ProfileStatus(profile=name, parent=parent)


def _current_source_sha(
    paths: Paths,
    mcu_type: str,
    fw: str,
    name: Optional[str],
    families: Optional[dict[str, firmware.FirmwareFamily]],
) -> Optional[str]:
    """Hash of whatever this profile was seeded *from*, as it stands now.

    Three shapes: a vendor seed file in the firmware tree, this type's own
    captured profile, or - for a derived bootloader config - the application's
    own ``.config``, which is what a derivation is a function of. A tree that is
    missing or has since dropped the file yields None, which reads as "cannot
    tell" rather than "moved".
    """
    if not name:
        return None
    if name.startswith("derived:"):
        return _sha256(paths.config_file(mcu_type, name[len("derived:") :]))
    try:
        return _sha256(find(paths, fw, name, families, mcu_type=mcu_type).path)
    except (ProfileError, OSError):
        return None


def refuse_if_customised(paths: Paths, mcu_type: str, fw: str, *, force: bool) -> None:
    """Never overwrite answers this tool did not write.

    A config matching its record is ours to rewrite - that is what makes
    reseeding after a vendor bump a safe, repeatable operation. Anything else -
    a hand-built config from before profiles existed, or one edited since - is
    the user's, and is refused rather than backed up and replaced. Still refused
    now that :func:`capture_custom` would keep those answers: the capture makes
    `force` recoverable, not automatic, and "which of my two profiles am I on"
    is a question the user should have answered rather than found out.

    Public because it is worth asking *before* committing to the work as well as
    during it. Seeding is a job, and "this config is yours, pass force" is a
    refusal rather than a failure - a caller wants that back immediately so it
    can re-ask, not three Kconfig parses later on a job that died. Cheap enough
    to ask twice: two file hashes and no parse. The call inside the write is
    still the authority; this one is only allowed to be early.
    """
    target = paths.config_file(mcu_type, fw)
    if force or not os.path.exists(target):
        return
    state = status(paths, mcu_type, fw)
    if state.reason in (None, SEED_MOVED):
        return
    detail = (
        f"it was seeded from '{state.profile}' and has been edited since"
        if state.reason == CUSTOMISED
        else "it was not created from a profile"
    )
    raise ProfileCustomisedError(
        f"'{mcu_type}' already has a {fw} config at {target} and {detail}. "
        f"Seeding would discard those answers, and they are the one thing here "
        f"that cannot be regenerated. Pass force to replace it anyway - they are "
        f"kept as '{CUSTOM_PROFILE}', this type's own profile, and the previous "
        f"file is kept as a .bak.",
        type=mcu_type,
        fw=fw,
        path=target,
        reason=state.reason,
        profile=state.profile,
    )
