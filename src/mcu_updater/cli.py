"""Command line interface.

This module and :mod:`tui` are the only places allowed to call ``input()``,
``sys.exit()`` or bare ``print()``. Everything below them raises and reports.

Behavioural parity with the original single-file script is deliberate: same nine
subcommands, same flags, same messages, same exit codes, and a bare invocation
still drops into the interactive menu. New flags (``--dry-run``, ``-j``,
``--force``, the ``status`` subcommand) are additive.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import sys
from collections.abc import Sequence
from typing import Optional

from . import __version__, firmware, flashers, profiles, providers
from .build import build, menuconfig_tty, staleness
from .config import Registry
from .devices import (
    STATE_KATAPULT,
    STATE_KLIPPER,
    device_state,
    find_untracked,
)
from .errors import (
    ConfigNotFoundError,
    DuplicateTypeError,
    UnknownSerialError,
    UpdaterError,
)
from .flashers.flash import adoptable_devices, flash_initial_bootloader
from .layout import migrate_type_dirs
from .lock import exclusive
from .paths import FW_TARGETS, Paths
from .service import (
    Journal,
    ServiceController,
    klipper_stopped,
    make_controller,
    paused,
    reconcile,
)
from .settings import Settings, load_settings

# --------------------------------------------------------------------------
# process-wide context
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Context:
    paths: Paths
    settings: Settings

    def registry(self) -> Registry:
        """Always a fresh read - the file may have changed under us."""
        return Registry.load(self.paths)


_ctx: Optional[Context] = None


def ctx() -> Context:
    """The context main() built. A module global so handlers can keep their
    ``(args)`` signature, which is what lets the menu drive them with a
    hand-built Namespace."""
    global _ctx
    if _ctx is None:
        paths = Paths.from_env()
        _ctx = Context(paths=paths, settings=load_settings(paths.settings_file))
    return _ctx


def stdout_reporter(stream: str, line: str) -> None:
    if stream in ("stderr", "warn"):
        print(line, file=sys.stderr)
    elif stream == "error":
        print(f"ERROR: {line}", file=sys.stderr)
    elif stream == "cmd":
        print(f"+ {line}")
    else:
        print(line)


def _confirm(prompt: str) -> bool:
    resp = input(f"{prompt} [y/N]: ").strip().lower()
    return resp in ("y", "yes")


# --------------------------------------------------------------------------
# registry commands
# --------------------------------------------------------------------------


def add_mcu_type(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()

    if args.type in reg and not args.force:
        print(
            f"MCU Type '{args.type}' already exists:\n"
            f"{json.dumps(reg.get(args.type).to_json(), indent=2)}"
        )
        if not _confirm("Overwrite?"):
            print("Aborting add.")
            return

    try:
        reg.add_type(
            args.type,
            args.chipset,
            klipper_args=args.klipper_args,
            katapult_args=args.katapult_args,
            katapult_installed=not args.no_katapult,
            overwrite=True,
        )
    except DuplicateTypeError:  # pragma: no cover - overwrite=True can't raise it
        raise
    reg.save(c.paths)
    print(f"Successfully added/updated MCU Type: {args.type}")


def list_profiles(args: argparse.Namespace) -> None:
    c = ctx()
    mcu = c.registry().get(args.type)
    families = firmware.load(c.paths)
    application = mcu.application(families)
    seeds = profiles.available(c.paths, application, families, mcu_type=args.type)
    # From the verdict rather than from the registry key. The two disagree the
    # moment a config is edited or the tool is used on a type that predates
    # profiles, and this printed a `*` beside a hand-written `profile:` while the
    # line below it said "Not profile-managed".
    applied = profiles.status(c.paths, args.type, application, families)
    current = applied.profile
    if applied.reason == profiles.CUSTOMISED:
        # The config came from that profile and no longer holds it, so marking it
        # as the one in use would contradict the verdict printed underneath. Your
        # own captured answers are what is actually loaded, if they were kept.
        own = profiles.read_custom(c.paths, args.type, application)
        current = own.name if own is not None else None
    differences = profiles.distinguishing(seeds)

    print(f"{args.type} runs {application}.")
    if not seeds:
        print(
            f"  Its tree ({firmware.resolve(c.paths, application, families).source_dir(c.paths)}) "
            f"ships no profiles.\n"
            f"  Upstream Klipper ships none by design - there is no single config for a "
            f"tree that builds\n  for two hundred boards. Vendor forks ship one per variant."
        )
    else:
        print("  Available:")
        for seed in seeds:
            mark = "*" if seed.name == current else " "
            note = ""
            if seed.origin == profiles.ORIGIN_CUSTOM:
                note = (
                    f"  (yours, forked from {seed.parent})"
                    if seed.parent
                    else "  (yours)"
                )
            print(f"   {mark} {seed.name}{note}")
            # The one or two answers that tell this apart from its neighbours.
            # Printing all seven under each of eight entries hides them.
            for row in differences.get(seed.name, []):
                print(f"       {row['line']}")

    for fw in mcu.families():
        state = profiles.status(c.paths, args.type, fw, families)
        detail = f" (from {state.profile})" if state.profile else ""
        if state.reason == profiles.CUSTOMISED and state.profile:
            detail = f" (forked from {state.parent or state.profile})"
        elif state.custom and state.parent:
            detail = f" (yours, forked from {state.parent})"
        print(f"  {fw}: {state.label}{detail}")


def apply_profile(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()
    mcu = reg.get(args.type)
    families = firmware.load(c.paths)
    fw = args.fw or mcu.application(families)
    boot_fw = mcu.bootloader(families)

    applied = profiles.apply_seed(
        c.paths, args.type, fw, args.profile, families=families, force=args.force
    )
    print(f"Seeded {args.type} ({fw}) from {applied.profile}:")
    for line in applied.answers:
        print(f"    {line}")
    if applied.kept:
        print(f"  Your previous answers are kept as '{applied.kept}' - apply it to go back.")
    if applied.backup:
        print(f"  Previous config kept at {applied.backup}")

    if args.no_derive or boot_fw is None:
        print("  no bootloader to derive.")
    else:
        derived = profiles.derive_bootloader(
            c.paths, args.type, fw, boot_fw, families=families, force=args.force
        )
        print(f"\nDerived {boot_fw} from it:")
        for line in derived.carried:
            print(f"    {line}")
        if derived.dropped:
            print("  Not carried (not settings that tree has):")
            for line in derived.dropped:
                print(f"    {line}")
        if derived.app_address is not None:
            print(
                f"  Verified: {boot_fw} jumps to "
                f"{derived.app_address:#x}, where {fw} is linked to run."
            )

    if fw == mcu.application(families):
        with Registry.mutate(c.paths, f"profile for {args.type}") as writable:
            writable.get(args.type).profile = applied.profile

    print(f"\nRun 'build -t {args.type} -f {fw}' next.")


def add_serial(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()
    reg.get(args.type)  # raises UnknownTypeError
    if reg.add_serial(args.type, args.serial):
        reg.save(c.paths)
        print(f"Added serial {args.serial} to {args.type}")
    else:
        print(f"Serial {args.serial} already exists under {args.type}")


def remove_mcu_type(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()
    mcu = reg.get(args.type)

    if not args.force:
        n = len(mcu.serials)
        if not _confirm(f"Remove type '{args.type}' and its {n} tracked serial(s)?"):
            print("Aborted.")
            return

    reg.remove_type(args.type)
    reg.save(c.paths)
    print(f"Removed MCU Type: {args.type}")


def remove_serial(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()
    reg.get(args.type)
    if reg.remove_serial(args.type, args.serial):
        reg.save(c.paths)
        print(f"Removed serial {args.serial} from {args.type}")
    else:
        print(f"Serial {args.serial} isn't tracked under {args.type} - nothing to do.")


def status_cmd(args: argparse.Namespace) -> None:
    """Read-only overview. Promoted from menu-only to a real subcommand."""
    c = ctx()
    reg = c.registry()
    if not reg:
        print("No MCU types configured yet.")
        return

    for name in reg.names():
        mcu = reg.get(name)
        print(f"\n{name}  (chipset={mcu.chipset or '?'})")

        # What this type actually uses, not every family that exists. A board
        # running cartographer carries klipper config keys too, and listing them
        # as "not built" is noise about firmware nobody intends to build for it.
        for fw in mcu.families():
            stale, reason = staleness(c.paths, name, fw)
            if reason == "never_built":
                print(f"  {fw}: not built")
            elif stale:
                print(f"  {fw}: STALE ({reason})")
            else:
                print(f"  {fw}: up to date")

        if not mcu.serials:
            print("  (no tracked serials)")
            continue
        for serial in mcu.serials:
            state, _ = device_state(c.paths, mcu.chipset, serial)
            label = {
                STATE_KLIPPER: "online (klipper)",
                STATE_KATAPULT: "online (katapult/bootloader)",
            }.get(state, "offline" if state == "offline" else f"online ({state})")
            print(f"  - {serial}: {label}")

    untracked = find_untracked(c.paths, reg.all_serials())
    if untracked:
        print("\nUntracked devices on the bus:")
        for dev in untracked:
            print(f"  - {dev.serial}  (fw={dev.fw}, chipset={dev.chipset or '?'})")


# --------------------------------------------------------------------------
# build commands
# --------------------------------------------------------------------------


def make_menuconfig_cmd(args: argparse.Namespace) -> None:
    c = ctx()
    c.registry().get(args.type)
    menuconfig_tty(c.paths, args.type, args.fw, pause=not getattr(args, "no_pause", False))


def _build_interactive(
    c: Context,
    mcu_type: str,
    fw: str,
    jobs: Optional[int] = None,
    reseed: Optional[bool] = None,
):
    """Build, offering menuconfig first if this type has never been configured.

    The original did this inside do_build via an `interactive` flag. It lives here
    now so the core stays safe to call from a daemon, which must never touch an
    ncurses UI.
    """
    reg = c.registry()
    try:
        return build(
            c.paths, reg, c.settings, mcu_type, fw,
            reporter=stdout_reporter, jobs=jobs, reseed=reseed,
        )
    except ConfigNotFoundError:
        print(f"Configuration file not found for {mcu_type} ({fw}). Launching menuconfig...")
        menuconfig_tty(c.paths, mcu_type, fw)
        return build(
            c.paths, reg, c.settings, mcu_type, fw,
            reporter=stdout_reporter, jobs=jobs, reseed=reseed,
        )


def build_fw_cmd(args: argparse.Namespace) -> None:
    c = ctx()
    install = providers.Install.load(c.paths, c.settings)

    # A PlatformIO type's own env already names the board, the partition table
    # and the flags, so `-f` is not merely optional there, it is meaningless,
    # and there is no menuconfig to fall back to.
    if args.type in install.displays:
        display = install.displays[args.type]
        target = providers.BuildTarget(
            providers.PlatformIO.name, args.type, display.firmware
        )
        provider = providers.by_name(providers.PlatformIO.name)
        blocked = provider.blocked(install, target)
        if blocked:
            print(f"ERROR: {blocked}", file=sys.stderr)
            sys.exit(1)
        with exclusive(c.paths, f"build {args.type}"):
            provider.build(install, target, reporter=stdout_reporter)
        return

    with exclusive(c.paths, f"build {args.fw}/{args.type}"):
        _build_interactive(
            c,
            args.type,
            args.fw,
            jobs=getattr(args, "jobs", None),
            # None means "whatever reseed_on_build says", which is the same
            # answer the panel and a fleet build get. --no-reseed declines for
            # this run without touching the setting.
            reseed=False if getattr(args, "no_reseed", False) else None,
        )


# --------------------------------------------------------------------------
# flash commands
#
# Selection lives here, in the caller, because that is what both seams say: a
# provider answers questions about files it produces, a flasher writes one
# device, and which devices exist is the Inventory axis that stays deferred.
# What the CLI hands `flashers.write_all` is a list it decided on itself.
# --------------------------------------------------------------------------


def _bench(c: Context) -> flashers.Bench:
    """This host, as a flasher needs to see it.

    A controller *factory* rather than a controller: a PlatformIO family names
    its own port watcher, and a batch spanning two needs two. Sharing the
    factory is what keeps a dry run from stopping a real service.
    """
    def controller(name: Optional[str] = None) -> ServiceController:
        return make_controller(c.settings, name=name)

    return flashers.Bench(paths=c.paths, settings=c.settings, controller=controller)


def _board_targets(
    c: Context, mcu_type: str, serials: list[str], *, force: bool = False
) -> list:
    """Tracked boards of one kconfig type, as things a batch can write.

    `force` overrides a refused bootloader offset check (flash_katapult's own
    `force` parameter) and defaults off - a caller flashing more than one board
    at a time should never pass it, since a blanket override across a fleet is
    exactly what that check exists to prevent. Single-device `flash --force` is
    the only caller that sets it.
    """
    mcu = c.registry().get(mcu_type)
    application = mcu.application()
    return [
        flashers.flashtool.target_for(
            {
                "type": mcu_type,
                "serial": serial,
                "chipset": mcu.chipset,
                "fw": application,
                "force": force,
            }
        )
        for serial in serials
    ]


def _pio_targets(
    c: Context,
    name: str,
    only_id: Optional[str] = None,
    *,
    allow_discovery: bool = False,
) -> list:
    """Devices of one PlatformIO type: the watcher's map, or ask them ourselves.

    **Not from Klipper.** The agent reads its list from the klippy module's own
    printer objects, and the CLI has no Moonraker to ask.

    So, in order of what it costs: the watcher's `id -> port` map, which answers
    instantly and is the source written for exactly this moment; and failing
    that, `pio.discover`, which is the *authoritative* one - each device
    broadcasts its id every couple of seconds unprompted, so this opens the free
    ports and reads what answered. Their own docs are explicit that identity
    belongs at flash time rather than to a remembered path, and the map is a
    remembered path.

    Discovery needs the ports free, which is why `allow_discovery` exists rather
    than it simply always being tried: the caller has to have stopped Klipper and
    paused the watcher first. `klipper_stopped` is idempotent, so the batch's own
    stop inside that one correctly no-ops.

    An empty answer from both is reported as "cannot tell", not as "no devices".
    Flashing nothing and calling it success is the failure this whole area exists
    to prevent.
    """
    from .providers import pio

    display = pio.load(c.paths, default_source=c.settings.pio_source)[name]
    found = pio.read_device_map(c.paths, display)
    if not found and allow_discovery:
        # The ports are free by now, which is the one moment this is possible.
        print(f"No device map for '{name}' - asking the devices which they are...")
        try:
            found = pio.discover(c.paths, c.settings, display, reporter=stdout_reporter)
        except UpdaterError as exc:
            # Best effort, as it is in the esptool flasher: discovery needs
            # pyserial and the source tree, and a host missing either should get
            # the message below naming both sources rather than a tool error
            # from the fallback.
            stdout_reporter("warn", f"could not ask the devices ({exc})")
    if not found:
        where = pio.device_map_path(c.paths, display) or "(no device_map configured)"
        watcher = f"the '{display.service}' watcher" if display.service else "a watcher"
        raise UpdaterError(
            f"nothing found for '{name}'. Neither the device map at {where} nor "
            f"asking the devices directly turned anything up - so either {watcher} "
            f"is not running and nothing answered on the free ports, or there is "
            f"nothing plugged in."
        )
    return [
        flashers.esptool.target_for(
            display,
            {
                "name": device.device_id,
                "section": f"{display.klipper_section} {device.device_id}",
                "configured_path": device.port,
                "device_id": device.device_id,
                "present": device.present,
            },
        )
        for device in sorted(found.values(), key=lambda d: d.port)
        if device.present and (only_id is None or only_id in (device.port, device.device_id))
    ]


@contextlib.contextmanager
def _ports_free(c: Context, names: Sequence[str], label: str):
    """Klipper down and every named family's watcher paused, so discovery works.

    Hoisted out of the batch because the CLI has to *select* inside the stop, not
    just write inside it: with no Moonraker to ask, asking the devices themselves
    is its fallback and that needs the ports free. `klipper_stopped` is
    idempotent, so `write_all`'s own stop inside this one sees it already stopped
    and correctly leaves it that way - one stop/start cycle, not two.
    """
    from .providers import pio

    displays = pio.load(c.paths, default_source=c.settings.pio_source)
    with klipper_stopped(
        c.paths, make_controller(c.settings), label, reporter=stdout_reporter
    ):
        with contextlib.ExitStack() as stack:
            for name in names:
                unit = displays[name].service if name in displays else ""
                if unit:
                    stack.enter_context(
                        paused(
                            make_controller(c.settings, name=unit),
                            reporter=stdout_reporter,
                        )
                    )
            yield


def _run_batch(c: Context, targets: list, label: str) -> int:
    """Write a batch and print what happened. Returns an exit code.

    The same `flashers.write_all` the agent submits as a job, with a context that
    has no job behind it. `on_ready` is deliberately absent: the agent asks
    Moonraker whether klippy really came back and will issue a FIRMWARE_RESTART,
    and the CLI has nobody to ask - `klipper_stopped` restarting the unit is the
    whole of its answer.
    """
    result = flashers.write_all(
        _bench(c), targets, flashers.PlainContext(stdout_reporter)
    )
    for failure in result["failures"]:
        print(f"ERROR: {failure['id']}: {failure['error']}", file=sys.stderr)
    if result["failures"]:
        print(
            f"\n{len(result['flashed'])} of {len(targets)} written; "
            f"{len(result['failures'])} failed.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{label}: {len(result['flashed'])} device(s) written.")
    return 0


def flash_fw_cmd(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()

    if not args.serial and not args.type:
        print("ERROR: provide -s <serial>, -t <type>, or both.", file=sys.stderr)
        sys.exit(1)

    if not args.yes and not _confirm(
        f"Flashing requires stopping the '{c.settings.service}' service "
        f"(aborts any active print!). Continue?"
    ):
        print("Aborted.")
        return

    # A PlatformIO type: its devices are ports, not tracked serials, and the
    # batch knows how to write them. Nothing below this applies - there is no
    # serial to resolve and no registry entry to add one to.
    if args.type and args.type not in reg.names():
        with exclusive(c.paths, f"flash type {args.type}"):
            with _ports_free(c, [args.type], f"flash {args.type}"):
                targets = _pio_targets(
                    c, args.type, only_id=args.serial, allow_discovery=True
                )
                if not targets:
                    print(f"No device is reachable for '{args.type}'.", file=sys.stderr)
                    sys.exit(1)
                code = _run_batch(c, targets, f"flash {args.type}")
        sys.exit(code)

    # Whole type: flash every tracked board under it.
    if args.type and not args.serial:
        mcu = reg.get(args.type)
        if not mcu.serials:
            print(f"No serials tracked under '{args.type}'.", file=sys.stderr)
            sys.exit(1)

        with exclusive(c.paths, f"flash type {args.type}"):
            code = _run_batch(
                c, _board_targets(c, args.type, mcu.serials), f"flash {args.type}"
            )
        sys.exit(code)

    # Single device.
    if args.type:
        try:
            mcu_type = reg.resolve_serial(args.serial, args.type)
        except UnknownSerialError:
            # Untracked under this type, and not tracked elsewhere (that case
            # raises SerialTrackedElsewhereError and is refused outright).
            if not _confirm(
                f"Serial '{args.serial}' isn't tracked under '{args.type}' yet. Add it now?"
            ):
                print("Aborted.")
                sys.exit(1)
            reg.add_serial(args.type, args.serial)
            reg.save(c.paths)
            print(f"Added serial {args.serial} to {args.type}")
            mcu_type = args.type
    else:
        mcu_type = reg.resolve_serial(args.serial)
        print(f"Resolved serial {args.serial} -> type '{mcu_type}'")

    with exclusive(c.paths, f"flash {mcu_type}/{args.serial}"):
        code = _run_batch(
            c,
            _board_targets(c, mcu_type, [args.serial], force=args.force),
            f"flash {args.serial}",
        )
    sys.exit(code)


def update_all(args: argparse.Namespace) -> None:
    """Rebuild what is stale, then write it - across every build system.

    This walked the `[mcu ...]` registry because that was the only list it had,
    so "update everything" meant "update every board" and left every PlatformIO
    device on whatever it happened to be running. Nothing said so. That is the
    same bug `build_all` had before the Provider seam, one layer down.

    Both halves go through the seams now, so a provider or flasher added later
    is picked up here without this function being edited.
    """
    c = ctx()
    install = providers.Install.load(c.paths, c.settings)
    if not install.registry and not install.displays:
        print("No types configured.", file=sys.stderr)
        sys.exit(1)

    # Everything not provably current, which is the only safe collapse: an image
    # we cannot vouch for is exactly the one worth rebuilding.
    selection = providers.select(install, stale_only=True)
    kinds = sorted({t.provider for t in selection.build})
    summary = ", ".join(providers.by_name(k).label for k in kinds) or "nothing"

    if not args.yes and not _confirm(
        f"This stops the '{c.settings.service}' service (aborts any active print!), "
        f"rebuilds what is stale ({summary}) and writes it to every tracked "
        f"device, then restarts it. Continue?"
    ):
        print("Aborted.")
        return

    failures: list[tuple[str, Optional[str]]] = []

    with exclusive(c.paths, "update-all"):
        for skipped in selection.skipped:
            # Named, never silent. A type dropped from a fleet build without a
            # word is the bug the Provider seam was written for.
            print(f"SKIP {skipped.target.name}: {skipped.reason}", file=sys.stderr)

        built: list[str] = []
        for target in selection.build:
            provider = providers.by_name(target.provider)
            print(f"\n=== {provider.describe(target)} ===")
            try:
                provider.build(install, target, reporter=stdout_reporter)
                built.append(target.name)
            except UpdaterError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                failures.append((target.name, None))

        # Selected after building, because a build is what makes a device stale -
        # and inside the stop, because with no Moonraker to ask, asking the
        # devices themselves is how a PlatformIO family gets enumerated.
        with _ports_free(c, sorted(install.displays), "update-all"):
            targets: list = []
            for name in sorted(install.registry.names()):
                targets += _board_targets(c, name, install.registry.get(name).serials)
            for name in sorted(install.displays):
                try:
                    targets += _pio_targets(c, name, allow_discovery=True)
                except UpdaterError as exc:
                    # Not fatal: the boards are still worth writing, and a host with
                    # no watcher running is a configuration gap rather than a fault.
                    print(f"SKIP {name}: {exc}", file=sys.stderr)
                    failures.append((name, "no devices found"))

            if not targets:
                print("\nNothing to write.")
            else:
                result = flashers.write_all(
                    _bench(c), targets, flashers.PlainContext(stdout_reporter)
                )
                for failure in result["failures"]:
                    print(f"ERROR: {failure['id']}: {failure['error']}", file=sys.stderr)
                    failures.append((failure["type"], failure["id"]))
                print(f"\nWrote {len(result['flashed'])} of {len(targets)} device(s).")

    if failures:
        print("\nCompleted with failures:")
        for failed_type, failed_id in failures:
            print(
                f"  - {failed_type}"
                + (f" / {failed_id}" if failed_id else " (build failed)")
            )
        sys.exit(1)
    print(f"\nBuilt {len(built)} type(s) and flashed everything tracked.")


def add_mcu(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()
    mcu = reg.get(args.type)
    chipset = mcu.chipset

    with exclusive(c.paths, f"add-mcu {args.type}"):
        # A brand new type has no saved .config, so this launches menuconfig.
        result = _build_interactive(c, args.type, "katapult")

        before = set(reg.all_serials()) | {
            d.serial for d in find_untracked(c.paths, reg.all_serials())
        }
        flash_initial_bootloader(
            c.paths, c.settings, chipset, result.bin_path, reporter=stdout_reporter
        )

        print("Waiting for the device to enumerate as Katapult...")
        candidates = adoptable_devices(c.paths, before, chipset)

    if not candidates:
        print(
            f"No new, unassigned Katapult device found for chipset '{chipset}'. "
            f"Check `ls /dev/serial/by-id/` and use 'add-serial' manually."
        )
        return

    reg = c.registry()
    for dev in candidates:
        if _confirm(
            f"Found unassigned Katapult device: {dev.serial} ({dev.path}). "
            f"Add it to '{args.type}'?"
        ):
            if reg.add_serial(args.type, dev.serial):
                reg.save(c.paths)
                print(f"Added serial {dev.serial} to {args.type}")


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser(fw_choices: Optional[Sequence[str]] = None) -> argparse.ArgumentParser:
    """The CLI. `fw_choices` is what `--fw` will accept.

    Passed in rather than read here because a declared `[firmware x]` family is
    a legitimate target, and argparse needs the list at construction time. It
    defaults to the built-ins so a caller without a Paths - every test that
    builds a parser to check wiring - still gets a working one.
    """
    choices = list(fw_choices) if fw_choices else list(FW_TARGETS)
    parser = argparse.ArgumentParser(
        description="Klipper/Katapult Firmware Management Utility"
    )
    parser.add_argument("--version", action="version", version=f"mcu-updater {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Echo commands and fake their output instead of building/flashing",
    )
    subparsers = parser.add_subparsers(title="Commands", dest="command", required=True)

    p = subparsers.add_parser("add-type", help="Add a new MCU type configuration")
    p.add_argument("-t", "--type", required=True, help="Unique MCU Type Name (e.g., bttebb36)")
    p.add_argument("-c", "--chipset", required=True, help="Chipset (e.g., stm32g0b1xx)")
    p.add_argument("--klipper-args", default="", help="Extra make arguments for Klipper")
    p.add_argument("--katapult-args", default="", help="Extra make arguments for Katapult")
    p.add_argument("--no-katapult", action="store_true", help="Set Katapult installed to false")
    p.add_argument("--force", action="store_true", help="Overwrite without prompting")
    p.set_defaults(func=add_mcu_type)

    p = subparsers.add_parser("add-serial", help="Add a serial number to an existing MCU type")
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument("-s", "--serial", required=True, help="The device serial string")
    p.set_defaults(func=add_serial)

    p = subparsers.add_parser(
        "remove-type", help="Remove an MCU type configuration and its tracked serials"
    )
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument("--force", action="store_true", help="Skip the confirmation prompt")
    p.set_defaults(func=remove_mcu_type)

    p = subparsers.add_parser("remove-serial", help="Remove a tracked serial from an MCU type")
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument("-s", "--serial", required=True, help="The device serial string")
    p.set_defaults(func=remove_serial)

    p = subparsers.add_parser("status", help="Show tracked types, staleness, and bus state")
    p.set_defaults(func=status_cmd)

    p = subparsers.add_parser(
        "profiles", help="List the vendor answer files this type's firmware tree ships"
    )
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.set_defaults(func=list_profiles)

    p = subparsers.add_parser(
        "apply-profile",
        help="Seed a type's menuconfig answers from its firmware tree, "
        "deriving katapult's to match",
    )
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument(
        "-p", "--profile", required=True, help="Seed file name, e.g. config.CartoV4USB"
    )
    p.add_argument(
        "-f", "--fw", default=None,
        help="Firmware target (default: whichever family the type declares it runs)",
    )
    p.add_argument(
        "--no-derive",
        action="store_true",
        help="Seed the application only, leaving katapult's config alone",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing config that was not written from a profile "
        "(the previous one is kept as .bak)",
    )
    p.set_defaults(func=apply_profile)

    p = subparsers.add_parser("menuconfig", help="Launch make menuconfig for a specific target")
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument("-f", "--fw", required=True, choices=choices, help="Firmware target")
    p.add_argument("--no-pause", action="store_true", help="Skip the 'press Enter' prompt")
    p.set_defaults(func=make_menuconfig_cmd)

    p = subparsers.add_parser("build", help="Compile the firmware for a specific target")
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.add_argument("-f", "--fw", required=True, choices=choices, help="Firmware target")
    p.add_argument("-j", "--jobs", type=int, default=None, help="Parallel make jobs (0 disables -j)")
    p.add_argument(
        "--no-reseed",
        action="store_true",
        help="Build the saved config as it stands, even if the profile it came "
        "from has been updated since",
    )
    p.set_defaults(func=build_fw_cmd)

    p = subparsers.add_parser(
        "flash", help="Flash a single tracked device with its built klipper.bin"
    )
    p.add_argument(
        "-t", "--type", default=None,
        help="MCU Type Name (optional - inferred from the serial if omitted)",
    )
    p.add_argument("-s", "--serial", default=None, help="Device serial (must already be tracked)")
    p.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p.add_argument(
        "--force", action="store_true",
        help="Override a refused bootloader offset check. Single device only - "
        "never applies when flashing a whole type",
    )
    p.set_defaults(func=flash_fw_cmd)

    p = subparsers.add_parser(
        "update-all",
        help="Build + flash klipper for every tracked MCU type/device, "
        "stopping/restarting klipper around it",
    )
    p.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p.add_argument("-j", "--jobs", type=int, default=None, help="Parallel make jobs")
    p.set_defaults(func=update_all)

    p = subparsers.add_parser(
        "add-mcu", help="Interactive routine to setup, build, and flash a new MCU"
    )
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.set_defaults(func=add_mcu)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    global _ctx

    argv = list(sys.argv[1:] if argv is None else argv)
    paths = Paths.from_env()

    try:
        moved = migrate_type_dirs(paths)
        settings = load_settings(paths.settings_file)
    except UpdaterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if moved:
        print(
            f"Moved per-type config into {paths.type_root}: {', '.join(moved)}",
            file=sys.stderr,
        )

    parser = build_parser(firmware.names(paths))

    # Bare invocation drops into the interactive menu, as it always has.
    if not argv:
        _ctx = Context(paths=paths, settings=settings)
        _reconcile_quietly(_ctx)
        from . import tui

        tui.run_menu()
        return

    args = parser.parse_args(argv)
    if getattr(args, "dry_run", False):
        settings.dry_run = True
    _ctx = Context(paths=paths, settings=settings)
    _reconcile_quietly(_ctx)

    try:
        args.func(args)
    except UpdaterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


def _reconcile_quietly(c: Context) -> None:
    """If a previous run died with klipper stopped, put it back."""
    if Journal(c.paths).pending() is None:
        return
    if os.name == "nt":  # no systemd on the dev box
        return
    try:
        reconcile(c.paths, make_controller(c.settings), reporter=stdout_reporter)
    except Exception as exc:  # noqa: BLE001 - never block the requested command
        print(f"WARNING: could not reconcile previous run: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    main()
