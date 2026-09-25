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
from collections.abc import Callable, Sequence

from . import (
    __version__,
    firmware,
    flashers,
    helpers,
    inventory,
    profiles,
    providers,
    stop_services,
    tracking,
    typelist,
)
from .build import build, menuconfig_tty
from .config import Registry
from .devices import (
    STATE_KATAPULT,
    STATE_KLIPPER,
    STATE_OFFLINE,
    device_state,
    find_device,
    find_untracked,
    scan,
)
from .errors import (
    ConfigNotFoundError,
    NoFlasherError,
    UnknownSerialError,
    UpdaterError,
)
from .flashers.flash import adoptable_devices, flash_initial_bootloader
from .lock import exclusive
from .paths import Paths
from .service import (
    Journal,
    ServiceController,
    make_controller,
    reconcile,
    services_stopped,
)
from .settings import Settings, load_settings
from .states import NEVER_BUILT

#: What `status` prints under a type that tracks no board at all - no USB
#: serial and no CAN uuid. One wording for every type, whatever builds it.
NO_TRACKED_DEVICES = "  (no tracked devices)"

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


_ctx: Context | None = None


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

    # The prompt above read without the lock, and must: a prompt must not hold
    # the registry. The write re-reads under it, so an edit made to any other
    # type while the prompt waited is kept. `overwrite` still replaces this
    # type's own declaration, its serials included. The pre-check above sees
    # only kconfig types; a name another builder declares is refused by
    # `add_type` itself, under the lock.
    with Registry.mutate(c.paths, f"add type {args.type}") as writable:
        writable.add_type(
            args.type,
            args.chipset,
            klipper_args=args.klipper_args,
            katapult_args=args.katapult_args,
            katapult_installed=not args.no_katapult,
            overwrite=True,
        )
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
    tracked = tracking.add_serial(c.paths, args.type, args.serial)
    if tracked.provisioned_from is not None:
        print(f"Provisioned {tracked.provisioned_from} as {tracked.serial}")
    if tracked.added:
        print(f"Added serial {tracked.serial} to {args.type}")
    else:
        print(f"Serial {tracked.serial} already exists under {args.type}")


def remove_mcu_type(args: argparse.Namespace) -> None:
    c = ctx()
    n = len(c.registry().declared_serials(args.type))  # UnknownTypeError if absent

    # Asked before the lock is taken: a prompt must not hold the registry.
    if not args.force:
        if not _confirm(f"Remove type '{args.type}' and its {n} tracked serial(s)?"):
            print("Aborted.")
            return

    tracking.remove_type(c.paths, args.type)
    print(f"Removed MCU Type: {args.type}")


def remove_serial(args: argparse.Namespace) -> None:
    c = ctx()
    if tracking.remove_serial(c.paths, args.type, args.serial):
        print(f"Removed serial {args.serial} from {args.type}")
    else:
        print(f"Serial {args.serial} isn't tracked under {args.type} - nothing to do.")


def _device_label(state: str) -> str:
    """How `status` describes one tracked board's inventory state."""
    if state == inventory.STATE_UNKNOWN:
        # Every CAN row: the inventory never guesses a CAN node's state, and
        # status does not query tracked nodes - "online (unknown)" would claim
        # something nothing looked at.
        return "state unknown (CAN nodes are not queried)"
    return {
        STATE_KLIPPER: "online (klipper)",
        STATE_KATAPULT: "online (katapult/bootloader)",
    }.get(state, "offline" if state == STATE_OFFLINE else f"online ({state})")


def status_cmd(args: argparse.Namespace) -> None:
    """Read-only overview. Promoted from menu-only to a real subcommand."""
    c = ctx()
    if getattr(args, "can", False):
        from .discovery import canbus

        print("\nCAN scan (may take a few seconds):")
        result = canbus.scan_all_result(c.paths, c.settings, reporter=stdout_reporter)
        print("  Interfaces:")
        for interface in result.interfaces:
            adapter = interface.adapter
            suffix = (
                f" (USB {adapter.vendor_id or '?'}:{adapter.product_id or '?'})"
                if adapter is not None
                else ""
            )
            print(f"  - {interface.name}{suffix}")
        if result.sightings:
            print("  Untracked CAN devices:")
            for sighting in result.sightings:
                print(
                    f"  - {sighting.uuid}  (interface={sighting.interface}, "
                    f"application={sighting.application}, state={sighting.state})"
                )
        if result.failures:
            print("  Query failures:")
            for failure in result.failures:
                code = f", returncode={failure.returncode}" if failure.returncode is not None else ""
                print(f"  - {failure.interface}: {failure.reason}{code}")
        if not result.interfaces:
            print("  No CAN interfaces found.")
        elif not result.sightings and not result.failures:
            print("  No unclaimed CAN devices answered.")

    entries = typelist.load(c.paths)
    if not entries:
        print("No MCU types configured yet.")
        return

    install = providers.Install.load(c.paths, c.settings)
    targets_by_type: dict[str, list[providers.BuildTarget]] = {}
    for provider in providers.PROVIDERS:
        for target in provider.targets(install):
            targets_by_type.setdefault(target.name, []).append(target)
    rows = inventory.index(inventory.build(entries, inventory.Sweep(byid=tuple(scan(c.paths)))))

    for entry in entries:
        print(f"\n{entry.name}  (chipset={entry.chipset or '?'})")

        # What this type builds, from its own provider - not every family that
        # exists, which would be noise about firmware nobody builds for it.
        for target in targets_by_type.get(entry.name, []):
            # A bootloader is built on demand, never by a sweep, so "not
            # built" would be noise on every kconfig type.
            if target.on_demand:
                continue
            label = target.fw or target.provider
            try:
                status = providers.by_name(target.provider).artifact_status(install, target)
            except UpdaterError as exc:
                print(f"  {label}: unknown ({exc})")
                continue
            if status.reason == NEVER_BUILT:
                print(f"  {label}: not built")
            elif not status.is_current:
                print(f"  {label}: STALE ({status.reason})")
            else:
                print(f"  {label}: up to date")

        if not entry.serials and not entry.canbus_uuids:
            print(NO_TRACKED_DEVICES)
            continue
        for serial in entry.serials:
            row = rows.get((entry.name, inventory.SERIAL, serial))
            state = row.state if row is not None else STATE_OFFLINE
            print(f"  - {serial}: {_device_label(state)}")
        for uuid in entry.canbus_uuids:
            row = rows.get((entry.name, inventory.CANBUS_UUID, uuid))
            state = row.state if row is not None else STATE_OFFLINE
            print(f"  - {uuid} (CAN): {_device_label(state)}")

    untracked = find_untracked(c.paths, {s for e in entries for s in e.serials})
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
    jobs: int | None = None,
    reseed: bool | None = None,
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
    if args.type in install.platformio:
        display = install.platformio[args.type]
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

    # A cmake type is in neither the `[mcu ...]` registry - `config.py` keeps
    # foreign builders out of it - nor the display map, so without this it
    # reached `_build_interactive` and was told it does not exist. Its family
    # names the tree and `cmake_target:` names the image, so `-f` says nothing
    # here either, and there is no menuconfig behind it to offer.
    if args.type in install.cmake:
        entry = install.cmake[args.type]
        target = providers.BuildTarget(
            providers.Cmake.name, args.type, entry.firmware
        )
        provider = providers.by_name(providers.Cmake.name)
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


def _target_for(
    install: providers.Install, name: str
) -> providers.BuildTarget | None:
    """The one target this type names, whichever provider owns it.

    The three lookups `build_fw_cmd` does inline, in one place and in the same
    order, so a caller that only needs "which provider is this" does not have
    to repeat them. None means no provider claims the name.
    """
    display = install.platformio.get(name)
    if display is not None:
        return providers.BuildTarget(providers.PlatformIO.name, name, display.firmware)
    entry = install.cmake.get(name)
    if entry is not None:
        return providers.BuildTarget(providers.Cmake.name, name, entry.firmware)
    # `Registry.get` raises rather than returning None, and this function's
    # whole job is to answer "does anything claim this name" without one.
    mcu = install.registry.types.get(name)
    if mcu is not None:
        fw = mcu.firmwares[0] if mcu.firmwares else ""
        return providers.BuildTarget(providers.KconfigMake.name, name, fw)
    return None


def clean_fw_cmd(args: argparse.Namespace) -> None:
    """Discard a target's generated build tree, for the ones that keep one.

    Dispatched through the provider rather than branching on cmake here: a
    provider that keeps no such tree answers None, and this prints that as the
    non-event it is. Takes the same exclusive lock a build does - removing a
    build directory out from under a running compile is the one way this could
    do damage.
    """
    c = ctx()
    install = providers.Install.load(c.paths, c.settings)

    target = _target_for(install, args.type)
    if target is None:
        print(f"ERROR: MCU type '{args.type}' does not exist.", file=sys.stderr)
        sys.exit(1)

    provider = providers.by_name(target.provider)
    with exclusive(c.paths, f"clean {args.type}"):
        removed = provider.clean(install, target)

    if removed is None:
        print(
            f"Nothing to clean for '{args.type}': {provider.label} keeps no "
            f"build directory of its own."
        )
        return
    print(f"Removed {removed}")
    print("The next build reconfigures from scratch.")


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
    def controller(name: str | None = None) -> ServiceController:
        return make_controller(c.settings, name=name)

    return flashers.Bench(paths=c.paths, settings=c.settings, controller=controller)


def _board_targets(
    c: Context, mcu_type: str, serials: list[str], *, force: bool = False
) -> tuple[list, list]:
    """Tracked boards of one kconfig type, selected through their family:
    `(targets, refused)` for `_run_batch`.

    `force` overrides a refused bootloader offset check (flash_katapult's own
    `force` parameter) and defaults off - a caller flashing more than one board
    at a time should never pass it, since a blanket override across a fleet is
    exactly what that check exists to prevent. Single-device `flash --force` is
    the only caller that sets it.
    """
    mcu = c.registry().get(mcu_type)
    families = firmware.load(c.paths)
    application = mcu.application(families)
    units = stop_services.for_mcu(c.paths, mcu, c.settings, families)
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=mcu_type,
                    id=serial,
                    chipset=mcu.chipset,
                    state=device_state(c.paths, mcu.chipset, serial)[0],
                    fw=application,
                    detail={
                        "type": mcu_type,
                        "serial": serial,
                        "chipset": mcu.chipset,
                        "fw": application,
                        "force": force,
                    },
                ),
                units,
            )
            for serial in serials
        ],
    )


def _canbus_targets(c: Context, mcu_type: str, uuids: list[str]) -> tuple[list, list]:
    """CAN-uuid counterpart to `_board_targets`, for a type's `canbus_uuids:`.

    Same resolved `stop_services` as this same type's by-id boards. The CLI
    has no Klipper mapping, so the flasher discovers the current interface at
    write time; `canbus_uuids:` stores no interface, and liveness is unknown.
    """
    mcu = c.registry().get(mcu_type)
    families = firmware.load(c.paths)
    application = mcu.application(families)
    units = stop_services.for_mcu(c.paths, mcu, c.settings, families)
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=mcu_type,
                    id=uuid,
                    chipset=mcu.chipset,
                    state=inventory.STATE_UNKNOWN,
                    fw=application,
                    kind=flashers.KIND_CANBUS,
                    detail={
                        "type": mcu_type,
                        "uuid": uuid,
                        "chipset": mcu.chipset,
                        "fw": application,
                    },
                ),
                units,
            )
            for uuid in uuids
        ],
    )


def _cmake_targets(c: Context, mcu_type: str, serial: str) -> tuple[list, list]:
    """One CMake-built board, as a thing the batch can write.

    The same selection the agent's `_cmake_flash` makes: the type's declared
    firmware family names the flashers that may write it, and the UF2 the
    build staged is what gets copied onto it. The missing-artifact refusal here
    is setup the operator has to fix before any write is possible, so it is
    said plainly rather than discovered as a missing-file error two layers
    down.

    `stop_services.for_cmake` is the only resolver that applies: `for_platformio`
    indexes the PlatformIO map - which is the `KeyError: 'roadrunner'` this
    whole change exists to remove - and `for_mcu` wants a kconfig `McuType`.

    No `FlashLog` record and no attachment check, matching the CLI's other flash
    paths: the family's `flashers:` list picks the writer (`flashers.select_each`),
    and a family that cannot write the board becomes a named refusal. The
    helper performs its own protocol confirmation once the services have
    released the port.
    """
    from .providers import cmake as cmake_mod

    # `.get`, not `[...]`: `provider_of` proved membership a moment ago, but it
    # re-read the config to do it, and a bare KeyError here would be exactly the
    # traceback this whole change exists to remove.
    target_type = cmake_mod.load(c.paths).get(mcu_type)
    if target_type is None:
        raise UpdaterError(f"CMake type '{mcu_type}' is no longer configured.")
    families = firmware.load(c.paths)
    family = firmware.resolve(c.paths, target_type.firmware, families)

    fw_bin = c.paths.uf2_file(mcu_type, target_type.firmware)
    if not os.path.exists(fw_bin):
        raise UpdaterError(f"no built firmware for {mcu_type} at {fw_bin}. Build it first.")

    present = find_device(c.paths, "", serial)
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=mcu_type,
                    id=serial,
                    chipset=target_type.chipset,
                    state=present.state if present is not None else STATE_OFFLINE,
                    fw=family.name,
                ),
                stop_services.for_cmake(c.paths, target_type, c.settings, families),
            ),
        ],
    )


def _pio_targets(
    c: Context,
    name: str,
    only_id: str | None = None,
) -> tuple[list, list]:
    """Devices of one PlatformIO type, from the firmware that knows them.

    **Not from Klipper.** The agent reads its list from the klippy module's own
    printer objects, and the CLI has no Moonraker to ask.

    So it asks the firmware's own identity handler, which knows the two
    sources this firmware has and what each costs: the watcher's `id -> port`
    map, written for exactly this moment, and failing that the broadcast
    listen pass, which is the *authoritative* one - each device announces its
    id every couple of seconds unprompted. Their own docs are explicit that
    identity belongs at flash time rather than to a remembered path, and the
    map is a remembered path.

    Asking needs the ports free. Both callers of this function are inside
    `_ports_free`, which is why `ask=True` is passed unconditionally rather
    than through a flag: a caller that had not stopped the services would be
    a caller in the wrong place, not a caller with the wrong argument.
    `services_stopped` is idempotent per unit, so the batch's own stop inside
    that one correctly no-ops.

    An empty answer from both is reported as "cannot tell", not as "no
    devices". Flashing nothing and calling it success is the failure this
    whole area exists to prevent.
    """
    from .providers import pio

    display = pio.load(c.paths)[name]
    families = firmware.load(c.paths)
    family = firmware.resolve(c.paths, display.firmware, families)
    identify = helpers.identifier(helpers.for_name(family.helper, family=family.name))
    if identify is None:
        raise UpdaterError(
            f"firmware family '{family.name}' names no helper that can identify "
            f"its devices, and a PlatformIO type is not yet joined against the "
            f"by-id sweep - so there is no way to tell which device of '{name}' "
            f"is which, and writing to a guessed port is what this refuses to do."
        )

    found = identify.identify(
        c.paths, c.settings, display, ask=True, reporter=stdout_reporter
    )
    units = stop_services.for_platformio(c.paths, display, c.settings)
    if not found:
        where = identify.remembered_at(c.paths, display) or "(nothing remembered)"
        own_watcher = [u for u in units if u != "klipper"]
        watcher = f"the '{own_watcher[0]}' watcher" if own_watcher else "a watcher"
        raise UpdaterError(
            f"nothing found for '{name}'. Neither the device map at {where} nor "
            f"asking the devices directly turned anything up - so either {watcher} "
            f"is not running and nothing answered on the free ports, or there is "
            f"nothing plugged in."
        )
    return flashers.select_each(
        c.paths,
        families,
        [
            (
                flashers.Device(
                    type=display.name,
                    id=device.port,
                    chipset="",
                    state=inventory.STATE_UNKNOWN,
                    fw=display.firmware,
                    kind=flashers.KIND_SCREEN,
                    detail={
                        "display": display,
                        "screen": {
                            "name": device.device_id,
                            "section": f"{display.klipper_section} {device.device_id}",
                            "configured_path": device.port,
                            "device_id": device.device_id,
                            "present": device.present,
                        },
                    },
                ),
                units,
            )
            for device in sorted(found.values(), key=lambda d: d.port)
            if device.present
            and (
                only_id is None
                or only_id == device.port
                or only_id.lower() == device.device_id.lower()
            )
        ],
    )


@contextlib.contextmanager
def _ports_free(c: Context, names: Sequence[str], label: str):
    """Every named family's units stopped, so discovery works.

    Hoisted out of the batch because the CLI has to *select* inside the stop, not
    just write inside it: with no Moonraker to ask, asking the devices themselves
    is its fallback and that needs the ports free. `services_stopped` is
    idempotent per unit, so `write_all`'s own stop inside this one sees each
    already stopped and correctly leaves it that way - one stop/start cycle,
    not two.

    The union covers klipper without saying so explicitly: every named
    display's resolved `stop_services` already includes it (by convention, or
    by the built-in default), so there is nothing left to stop unconditionally
    here the way there used to be.
    """
    from .providers import pio

    displays = pio.load(c.paths)
    units: list[str] = []
    for name in names:
        for unit in stop_services.for_platformio(c.paths, displays[name], c.settings):
            if unit not in units:
                units.append(unit)

    with services_stopped(
        c.paths,
        [make_controller(c.settings, name=unit) for unit in units],
        label,
        reporter=stdout_reporter,
    ):
        yield


@dataclasses.dataclass(frozen=True)
class _TypeFlashSource:
    """Provider-specific device enumeration behind one CLI selection call."""

    select: Callable[..., tuple[list, list]]
    needs_ports_free: bool = False
    empty_message: str = "No devices are tracked under '{name}'."
    empty_single_is_error: bool = False
    refuse_single_before_batch: bool = False
    refuse_fleet_before_batch: bool = False


def _kconfig_type_targets(
    c: Context,
    name: str,
    serial: str | None,
    force: bool,
    install: providers.Install | None = None,
) -> tuple[list, list]:
    if serial is not None:
        return _board_targets(c, name, [serial], force=force)
    mcu = (install.registry if install is not None else c.registry()).get(name)
    boards, refused = _board_targets(c, name, mcu.serials, force=force)
    can, can_refused = _canbus_targets(c, name, mcu.canbus_uuids)
    return boards + can, refused + can_refused


def _platformio_type_targets(
    c: Context,
    name: str,
    serial: str | None,
    force: bool,
    install: providers.Install | None = None,
) -> tuple[list, list]:
    # Nothing to look up in either: a PlatformIO type's devices are live ports
    # the firmware enumerates, not identities the config declares.
    return _pio_targets(c, name, only_id=serial)


def _cmake_type_targets(
    c: Context,
    name: str,
    serial: str | None,
    force: bool,
    install: providers.Install | None = None,
) -> tuple[list, list]:
    from .providers import cmake as cmake_mod

    entry = (install.cmake if install is not None else cmake_mod.load(c.paths)).get(name)
    if entry is None:
        raise UpdaterError(f"CMake type '{name}' is no longer configured.")
    if force:
        print(
            "Note: --force has no effect on a CMake flash, through bootsel or "
            "flashtool alike; it only overrides the kconfig family's "
            "bootloader-offset check."
        )
    targets: list = []
    refused: list = []
    for device_id in [serial] if serial is not None else entry.serials:
        selected, rejected = _cmake_targets(c, name, device_id)
        targets += selected
        refused += rejected
    return targets, refused


# Static for the same reason the provider and flasher registries are static:
# adding a builder is one reviewed adapter and one line, never caller branches.
_TYPE_FLASH_SOURCES = {
    providers.KconfigMake.name: _TypeFlashSource(
        _kconfig_type_targets,
        empty_message="No serials or CAN uuids tracked under '{name}'.",
    ),
    providers.PlatformIO.name: _TypeFlashSource(
        _platformio_type_targets,
        needs_ports_free=True,
        # "reachable", not "tracked": these devices are live ports the firmware
        # enumerates, so nothing was ever declared for them to be absent from.
        empty_message="No device is reachable for '{name}'.",
        empty_single_is_error=True,
    ),
    providers.Cmake.name: _TypeFlashSource(
        _cmake_type_targets,
        empty_message="No serials tracked under '{name}'.",
        refuse_single_before_batch=True,
        refuse_fleet_before_batch=True,
    ),
}


def _type_flash_source(c: Context, name: str) -> _TypeFlashSource:
    return _TYPE_FLASH_SOURCES[providers.provider_of(c.paths, name)]


def _run_batch(c: Context, targets: list, label: str, refused: list | tuple = ()) -> int:
    """Write a batch and print what happened. Returns an exit code.

    The same `flashers.write_all` the agent submits as a job, with a context that
    has no job behind it. `on_ready` is deliberately absent: the agent asks
    Moonraker whether klippy really came back and will issue a FIRMWARE_RESTART,
    and the CLI has nobody to ask - `services_stopped` restarting the units is
    the whole of its answer.

    `refused` is what selection could not give a flasher. The batch reports
    each as a failure.
    """
    result = flashers.write_all(
        _bench(c), targets, flashers.PlainContext(stdout_reporter), refused=refused
    )
    for failure in result["failures"]:
        print(f"ERROR: {failure['id']}: {failure['error']}", file=sys.stderr)
    if result["failures"]:
        print(
            f"\n{len(result['flashed'])} of {len(targets) + len(refused)} written; "
            f"{len(result['failures'])} failed.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{label}: {len(result['flashed'])} device(s) written.")
    return 0


def _run_type_flash(
    c: Context,
    name: str,
    serial: str | None,
    *,
    force: bool,
) -> int:
    """Flash one type, or one device of it, through that type's own source.

    Deliberately not a whole `Install`: this addresses one type and makes one
    lookup, so there is no pair of questions for a mid-flight config edit to
    answer differently - and `Install.load` runs the *validating* loads, which
    would let a malformed screen section break `flash -t <kconfig type>`.
    That is the blast radius `providers.selection` refuses for the same reason.
    The fleet sweep is where one snapshot is load-bearing, and it has one.
    """
    source = _type_flash_source(c, name)
    label = f"flash {serial or name}"
    ports = _ports_free(c, [name], label) if source.needs_ports_free else contextlib.nullcontext()
    with exclusive(c.paths, f"flash {name}" + (f"/{serial}" if serial else "")):
        with ports:
            targets, refused = source.select(c, name, serial, force)
            if not targets and not refused and (
                serial is None or source.empty_single_is_error
            ):
                print(source.empty_message.format(name=name), file=sys.stderr)
                return 1
            if serial is not None and refused and source.refuse_single_before_batch:
                raise NoFlasherError(refused[0]["error"])
            return _run_batch(c, targets, label, refused)


def flash_fw_cmd(args: argparse.Namespace) -> None:
    c = ctx()
    reg = c.registry()

    if not args.serial and not args.type:
        print("ERROR: provide -s <serial>, -t <type>, or both.", file=sys.stderr)
        sys.exit(1)

    if not args.yes and not _confirm(
        "Flashing requires stopping the affected service(s) "
        "(aborts any active print!). Continue?"
    ):
        print("Aborted.")
        return

    if args.type:
        source = _type_flash_source(c, args.type)
        # PlatformIO identities are live ports/device ids, not declared serials,
        # so even a narrowed request goes straight through its registered source.
        # `force` is never carried here: it overrides a bootloader offset check
        # that only the single-device kconfig write has, and `--force`'s own
        # help says it never applies to a whole type.
        if source.needs_ports_free or not args.serial:
            sys.exit(_run_type_flash(c, args.type, args.serial, force=False))

    # Single device. Identity, not build semantics: `resolve_serial` reads the
    # kconfig registry alone, which is why a serial configured under a CMake
    # type was reported as tracked under no type at all. The declared document
    # owns the serial-to-type pairing for every provider.
    if args.type:
        try:
            mcu_type = reg.resolve_declared_serial(args.serial, args.type)
        except UnknownSerialError:
            # Untracked under this type, and not tracked elsewhere (that case
            # raises SerialTrackedElsewhereError and is refused outright).
            if not _confirm(
                f"Serial '{args.serial}' isn't tracked under '{args.type}' yet. Add it now?"
            ):
                print("Aborted.")
                sys.exit(1)
            # Asked above, written here: the prompt holds no lock. `add-serial`'s
            # own path, so the write re-checks under the registry lock what the
            # read before the prompt could not promise still holds - tracked
            # under no other type, not an unprovisioned Roadrunner's diagnostic
            # identity - and a CMake type gains an identity without the kconfig
            # registry claiming its build. The flash below reads the registry
            # afresh, so the stale `reg` is not consulted again.
            tracked = tracking.add_serial(c.paths, args.type, args.serial)
            if tracked.provisioned_from is not None:
                print(f"Provisioned {tracked.provisioned_from} as {tracked.serial}")
            if tracked.added:
                print(f"Added serial {tracked.serial} to {args.type}")
            else:
                print(f"Serial {tracked.serial} is already tracked under {args.type}")
            mcu_type = args.type
            # Provisioning renames the board (its diagnostic identity is never
            # tracked - see `tracking.add_serial`), so every use of the serial
            # below - the flash targets and the lock label - must follow that
            # rename rather than keep flashing a serial the board no longer has.
            args.serial = tracked.serial
    else:
        mcu_type = reg.resolve_declared_serial(args.serial)
        print(f"Resolved serial {args.serial} -> type '{mcu_type}'")

    sys.exit(_run_type_flash(c, mcu_type, args.serial, force=args.force))


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
    if install.empty:
        print("No types configured.", file=sys.stderr)
        sys.exit(1)

    # Everything not provably current, which is the only safe collapse: an image
    # we cannot vouch for is exactly the one worth rebuilding.
    selection = providers.select(install, stale_only=True)
    kinds = sorted({t.provider for t in selection.build})
    summary = ", ".join(providers.by_name(k).label for k in kinds) or "nothing"

    if not args.yes and not _confirm(
        f"This stops the affected service(s) (aborts any active print!), "
        f"rebuilds what is stale ({summary}) and writes it to every tracked "
        f"device, then restarts them. Continue?"
    ):
        print("Aborted.")
        return

    failures: list[tuple[str, str | None]] = []

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
        with _ports_free(c, sorted(install.platformio), "update-all"):
            targets: list = []
            refused: list = []
            # Every configured name from the snapshot this command already
            # holds, so a file saved mid-sweep belongs to the next run rather
            # than to half of this one.
            for name in sorted(
                set(install.registry.names()) | set(install.platformio) | set(install.cmake)
            ):
                try:
                    source = _type_flash_source(c, name)
                    selected, rejected = source.select(c, name, None, False, install)
                except UpdaterError as exc:
                    # Not fatal: the rest of the fleet is still worth writing.
                    # A second slot that is not an id, because a type nothing
                    # could select has none - and leaving it empty would print
                    # "(build failed)" for a device the build never reached.
                    print(f"SKIP {name}: {exc}", file=sys.stderr)
                    failures.append((name, "no devices selected"))
                    continue
                targets += selected
                if source.refuse_fleet_before_batch:
                    for rejection in rejected:
                        print(f"SKIP {name}: {rejection['error']}", file=sys.stderr)
                        failures.append((name, rejection.get("id")))
                else:
                    refused += rejected

            if not targets and not refused:
                print("\nNothing to write.")
            else:
                result = flashers.write_all(
                    _bench(c),
                    targets,
                    flashers.PlainContext(stdout_reporter),
                    refused=refused,
                )
                for failure in result["failures"]:
                    print(f"ERROR: {failure['id']}: {failure['error']}", file=sys.stderr)
                    failures.append((failure["type"], failure["id"]))
                print(
                    f"\nWrote {len(result['flashed'])} of "
                    f"{len(targets) + len(refused)} device(s)."
                )

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
            c.paths,
            c.settings,
            chipset,
            result.bin_path,
            uf2_bin=result.uf2_path,
            katapult_config=c.paths.config_file(args.type, "katapult"),
            reporter=stdout_reporter,
        )

        print("Waiting for the device to enumerate as Katapult...")
        candidates = adoptable_devices(c.paths, before, chipset)

    if not candidates:
        print(
            f"No new, unassigned Katapult device found for chipset '{chipset}'. "
            f"Check `ls /dev/serial/by-id/` and use 'add-serial' manually."
        )
        return

    refused = False
    for dev in candidates:
        if _confirm(
            f"Found unassigned Katapult device: {dev.serial} ({dev.path}). "
            f"Add it to '{args.type}'?"
        ):
            # One refused board (tracked under another type, an unprovisioned
            # identity, the registry busy) says why and moves on: the rest were
            # just flashed too, and each still deserves its own prompt. Reported
            # the way `main` reports it, and in the exit code once all are asked.
            try:
                tracked = tracking.add_serial(c.paths, args.type, dev.serial)
            except UpdaterError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                refused = True
                continue
            if tracked.provisioned_from is not None:
                print(f"Provisioned {tracked.provisioned_from} as {tracked.serial}")
            if tracked.added:
                print(f"Added serial {tracked.serial} to {args.type}")
    if refused:
        sys.exit(1)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser(fw_choices: Sequence[str] | None = None) -> argparse.ArgumentParser:
    """The CLI. `fw_choices` is what `--fw` will accept.

    Passed in rather than read here because a declared `[firmware x]` family is
    a legitimate target, and argparse needs the list at construction time.
    `None` accepts any name; an undeclared one is refused when it is resolved,
    with the section to add.
    """
    choices = list(fw_choices) if fw_choices else None
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
    p.add_argument("--can", action="store_true", help="Also scan CAN interfaces (may take a few seconds)")
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
        "clean",
        help="Delete a target's generated build directory, for build systems "
        "that keep one",
    )
    p.add_argument("-t", "--type", required=True, help="MCU Type Name")
    p.set_defaults(func=clean_fw_cmd)

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


def main(argv: list[str] | None = None) -> None:
    global _ctx

    argv = list(sys.argv[1:] if argv is None else argv)
    paths = Paths.from_env()

    try:
        settings = load_settings(paths.settings_file)
    except UpdaterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

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
        reconcile(
            c.paths,
            lambda name: make_controller(c.settings, name=name),
            reporter=stdout_reporter,
        )
    except Exception as exc:  # noqa: BLE001 - never block the requested command
        print(f"WARNING: could not reconcile previous run: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    main()
