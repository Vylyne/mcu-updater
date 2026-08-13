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
"""

from __future__ import annotations

import dataclasses
import glob
import json
import os
import tempfile
import time
from typing import Any, Optional

from . import firmware, kconfig
from .errors import (
    OffsetMismatchError,
    ProfileCustomisedError,
    ProfileError,
    ProfileNotFoundError,
)
from .paths import Paths
from .states import TONE_ATTENTION, TONE_OK, TONE_UNKNOWN

#: Vendor seed files live in the tree root and are named ``config.<Variant>``.
#: Cartographer's fork ships eight of them. Upstream Klipper ships none, which
#: is the correct answer for upstream Klipper - there is no such thing as "the"
#: config for a tree that builds for two hundred boards.
SEED_PREFIX = "config."

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
    """One vendor-supplied answer file, sitting in a firmware tree."""

    #: Basename as it appears in the tree, e.g. ``config.CartoV4USB``.
    name: str
    fw: str
    path: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "fw": self.fw, "path": self.path}


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
    paths: Paths, fw: str, families: Optional[dict[str, firmware.FirmwareFamily]] = None
) -> list[Seed]:
    """Every seed file a firmware tree ships, sorted by name.

    An absent or unreadable tree yields an empty list rather than raising: "this
    firmware offers no profiles" and "this firmware is not installed" are
    answered by different things, and a listing call should not be the one to
    break the news.
    """
    family = firmware.resolve(paths, fw, families)
    fw_dir = family.source_dir(paths)
    out: list[Seed] = []
    for path in sorted(glob.glob(os.path.join(fw_dir, SEED_PREFIX + "*"))):
        if os.path.isfile(path):
            out.append(Seed(name=os.path.basename(path), fw=fw, path=path))
    return out


def find(
    paths: Paths,
    fw: str,
    name: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
) -> Seed:
    """Locate one seed, naming the real alternatives when it isn't there."""
    wanted = valid_seed_name(name)
    seeds = available(paths, fw, families)
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
    """
    seed = find(paths, fw, name, families)
    family = firmware.resolve(paths, fw, families)
    fw_dir = family.source_dir(paths)
    target = paths.config_file(mcu_type, fw)

    _refuse_if_customised(paths, mcu_type, fw, target, force=force)

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
    )
    write_record(paths, mcu_type, fw, result)
    return result


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
    _refuse_if_customised(paths, mcu_type, boot_fw, target, force=force)

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

_PROFILE_TONE: dict[Optional[str], str] = {
    None: TONE_OK,
    UNMANAGED: TONE_OK,
    CUSTOMISED: TONE_UNKNOWN,
    SEED_MOVED: TONE_ATTENTION,
}

PROFILE_REASONS = tuple(r for r in _PROFILE_TONE if r is not None)

_PROFILE_LABEL: dict[Optional[str], str] = {
    None: "Matches profile",
    UNMANAGED: "Not profile-managed",
    CUSTOMISED: "Customised",
    SEED_MOVED: "Profile updated - reseed available",
}


@dataclasses.dataclass(frozen=True)
class ProfileStatus:
    """Whether a saved .config still says what its profile said."""

    reason: Optional[str] = None
    profile: Optional[str] = None

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
    def tone(self) -> str:
        return _PROFILE_TONE[self.reason]

    @property
    def label(self) -> str:
        return _PROFILE_LABEL[self.reason]

    def to_json(self) -> dict[str, Any]:
        return {
            "managed": self.managed,
            "profile": self.profile,
            "reason": self.reason,
            "tone": self.tone,
            "label": self.label,
        }


def status(
    paths: Paths,
    mcu_type: str,
    fw: str,
    families: Optional[dict[str, firmware.FirmwareFamily]] = None,
) -> ProfileStatus:
    """Does this type's .config still hold what its profile put there?

    ``customised`` takes precedence over ``seed_moved``: both can be true at
    once, and the one that changes what a caller may safely do is the local
    edit. Reseeding over it is the thing that would lose work.
    """
    record = read_record(paths, mcu_type, fw)
    if record is None:
        return ProfileStatus(UNMANAGED)

    name = record.get("profile")
    current = _sha256(paths.config_file(mcu_type, fw))
    if current != record.get("config_sha256"):
        return ProfileStatus(CUSTOMISED, profile=name)

    seeded = record.get("source_sha256")
    source = _current_source_sha(paths, mcu_type, fw, name, families)
    if seeded and source and seeded != source:
        return ProfileStatus(SEED_MOVED, profile=name)
    return ProfileStatus(profile=name)


def _current_source_sha(
    paths: Paths,
    mcu_type: str,
    fw: str,
    name: Optional[str],
    families: Optional[dict[str, firmware.FirmwareFamily]],
) -> Optional[str]:
    """Hash of whatever this profile was seeded *from*, as it stands now.

    Two shapes: a vendor seed file in the firmware tree, or - for a derived
    bootloader config - the application's own ``.config``, which is what a
    derivation is a function of. A tree that is missing or has since dropped
    the file yields None, which reads as "cannot tell" rather than "moved".
    """
    if not name:
        return None
    if name.startswith("derived:"):
        return _sha256(paths.config_file(mcu_type, name[len("derived:") :]))
    try:
        return _sha256(find(paths, fw, name, families).path)
    except (ProfileError, OSError):
        return None


def _refuse_if_customised(
    paths: Paths, mcu_type: str, fw: str, target: str, *, force: bool
) -> None:
    """Never overwrite answers this tool did not write.

    A config matching its record is ours to rewrite - that is what makes
    reseeding after a vendor bump a safe, repeatable operation. Anything else -
    a hand-built config from before profiles existed, or one edited since - is
    the user's, and is refused rather than backed up and replaced. `force` still
    keeps one generation of backup, because :func:`kconfig.save_config` does.
    """
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
        f"that cannot be regenerated. Pass force to replace it anyway - the "
        f"previous file is kept as a .bak.",
        type=mcu_type,
        fw=fw,
        path=target,
        reason=state.reason,
        profile=state.profile,
    )
