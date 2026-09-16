"""The interactive numbered menu shown when the tool is run with no arguments.

A direct port of the original's menu layer. It drives the same handler functions
the CLI does, by building an ``argparse.Namespace`` by hand - so there is exactly
one implementation of each action and the two front ends cannot drift.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import cli, typelist
from .devices import BusDevice, find_untracked
from .errors import UpdaterError
from .typelist import TypeEntry

# --------------------------------------------------------------------------
# which types each menu offers
#
# Exactly the types the CLI handler behind a menu accepts - read from cli.py,
# not assumed - in one place, so widening a handler is one edit here. Status,
# add/remove serial and remove type go through `tracking`, which covers every
# declared type. Menuconfig and add-mcu are kconfig operations
# (`registry().get`). Build and flash dispatch on the provider owning the name.
# --------------------------------------------------------------------------

KCONFIG_BUILDER = "kconfig_make"

#: Builders `build_fw_cmd` dispatches on: its platformio and cmake branches,
#: then the kconfig registry.
BUILD_BUILDERS = frozenset({KCONFIG_BUILDER, "cmake", "platformio"})

#: Builders `flash_fw_cmd` dispatches on, through `providers.provider_of`.
FLASH_BUILDERS = frozenset({KCONFIG_BUILDER, "cmake", "platformio"})

#: Builders `flash_fw_cmd` flashes a whole type for. A CMake type is refused
#: type-level ("Flash its boards individually") before anything is written.
FLASH_TYPE_LEVEL_BUILDERS = frozenset({KCONFIG_BUILDER, "platformio"})


def every_type(entry: TypeEntry) -> bool:
    return True


def kconfig_type(entry: TypeEntry) -> bool:
    return entry.builder == KCONFIG_BUILDER


def buildable_type(entry: TypeEntry) -> bool:
    return entry.builder in BUILD_BUILDERS


def flashable_type(entry: TypeEntry) -> bool:
    return entry.builder in FLASH_BUILDERS


#: Builders whose by-id name carries the chipset, so detected boards are
#: narrowed to the type's own. A Katapult or Klipper name does; a Roadrunner's
#: (`usb-Vylyne_Roadrunner_<serial>`) does not, so every other builder lists
#: every untracked board, the way `status` does.
CHIPSET_FILTERED_BUILDERS = frozenset({KCONFIG_BUILDER})

#: Builders whose `build_fw_cmd` path reads `-f`. Its cmake and platformio
#: branches ignore it, so the menu does not ask.
BUILD_FW_BUILDERS = frozenset({KCONFIG_BUILDER})


def filters_by_chipset(entry: TypeEntry) -> bool:
    return entry.builder in CHIPSET_FILTERED_BUILDERS


def build_asks_fw(entry: TypeEntry) -> bool:
    return entry.builder in BUILD_FW_BUILDERS


def _entries() -> list[TypeEntry]:
    """Every declared type, whatever builds it. Always a fresh read."""
    return typelist.load(cli.ctx().paths)


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------


def prompt_choice(title: str, options: list[str], allow_cancel: bool = True) -> int | None:
    """Numbered list, looping until a valid selection. Returns a 0-based index,
    or None if cancelled (entering 0)."""
    while True:
        print(f"\n{title}:")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        if allow_cancel:
            print("  0. Cancel")
        raw = input("> ").strip()
        if allow_cancel and raw == "0":
            return None
        try:
            choice = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= choice <= len(options):
            return choice - 1
        print("Out of range, try again.")


def prompt_yn(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def prompt_nonempty(prompt: str) -> str:
    while True:
        val = input(f"{prompt}: ").strip()
        if val:
            return val
        print("This can't be blank.")


# --------------------------------------------------------------------------
# pickers
# --------------------------------------------------------------------------


def pick_mcu_type(
    entries: list[TypeEntry] | None = None,
    allow_new: bool = True,
    accepts: Callable[[TypeEntry], bool] = every_type,
) -> str | None:
    """Picker over the declared types `accepts` lets through.

    `entries` defaults to a fresh `typelist.load`. With allow_new, appends an
    "Add a new MCU type" entry that runs the add-type flow inline and returns
    the new name - so a flow that needs a type never dead-ends just because none
    exist yet. That flow declares a kconfig type, which every menu offering it
    accepts.
    """
    if entries is None:
        entries = _entries()
    types = [entry for entry in entries if accepts(entry)]
    if not types:
        if not allow_new:
            print("No MCU types configured yet.")
            return None
        print("No MCU types configured yet - let's add one.")
        return menu_add_mcu_type()

    options = [
        f"{entry.name}  (chipset={entry.chipset or '?'}, {len(entry.serials)} serial(s))"
        for entry in types
    ]
    if allow_new:
        options.append("+ Add a new MCU type")
    idx = prompt_choice("Select MCU type", options)
    if idx is None:
        return None
    if allow_new and idx == len(types):
        return menu_add_mcu_type()
    return types[idx].name


def pick_fw_target() -> str | None:
    from . import firmware
    from .cli import ctx

    targets = firmware.names(ctx().paths)
    idx = prompt_choice("Select firmware target", list(targets))
    if idx is None:
        return None
    return targets[idx]


def _untracked_for(entry: TypeEntry, entries: list[TypeEntry]) -> list[BusDevice]:
    """Boards on the bus that no declared type tracks.

    The discovery `status` uses - `find_untracked` against every declared
    type's serials, whatever builds it - so a board tracked under a CMake type
    is never offered for adoption somewhere else. Narrowed to this type's
    chipset only where the by-id name carries one (`filters_by_chipset`).
    """
    known = {serial for e in entries for serial in e.serials}
    chipset = (entry.chipset or None) if filters_by_chipset(entry) else None
    return find_untracked(cli.ctx().paths, known, chipset=chipset)


def pick_serial_for_type(entry: TypeEntry, entries: list[TypeEntry]) -> str | None:
    """Tracked serials, plus untracked devices detected on the bus, plus manual
    entry. Used by Flash, where either is a valid target.

    Not `canbus_uuids`: `flash -s` resolves against `serials:` only, and would
    offer to add a uuid there."""
    tracked = list(entry.serials)
    untracked = _untracked_for(entry, entries)

    options = [f"{s} (tracked)" for s in tracked]
    options += [f"{d.serial} (untracked, detected on bus)" for d in untracked]
    options.append("Enter serial manually")

    idx = prompt_choice(f"Select a device under '{entry.name}'", options)
    if idx is None:
        return None
    if idx < len(tracked):
        return tracked[idx]
    if idx < len(tracked) + len(untracked):
        return untracked[idx - len(tracked)].serial
    return prompt_nonempty("Serial string")


def pick_tracked_serial(entry: TypeEntry) -> str | None:
    """Only already-tracked serials - used by Remove-serial, which removes from
    `serials:` and never from `canbus_uuids:`."""
    tracked = list(entry.serials)
    if not tracked:
        print(f"No serials tracked under '{entry.name}'.")
        return None
    idx = prompt_choice(f"Select a serial to remove from '{entry.name}'", tracked)
    if idx is None:
        return None
    return tracked[idx]


# --------------------------------------------------------------------------
# action bridge
# --------------------------------------------------------------------------


def call_action(func: Callable[[argparse.Namespace], None], ns: argparse.Namespace) -> None:
    """Invoke a CLI handler from the menu.

    Catches SystemExit and UpdaterError so a failed sub-action returns control to
    the menu loop instead of ending the session - the handler's own output has
    already explained what happened. KeyboardInterrupt is deliberately not caught
    here; it propagates to run_menu() and ends the session, per ^C convention.
    """
    try:
        func(ns)
    except UpdaterError as exc:
        print(f"ERROR: {exc}")
        print("(action did not complete successfully - see messages above)")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code not in (0, None):
            print("(action did not complete successfully - see messages above)")


# --------------------------------------------------------------------------
# menu entries
# --------------------------------------------------------------------------


def menu_status() -> None:
    call_action(cli.status_cmd, argparse.Namespace())


def menu_add_mcu_type() -> str:
    type_name = prompt_nonempty("MCU type name (e.g. bttebb36)")
    chipset = prompt_nonempty("Chipset (e.g. stm32g0b1xx)")
    klipper_args = input("Extra klipper make args (blank for none): ").strip()
    katapult_args = input("Extra katapult make args (blank for none): ").strip()
    no_katapult = prompt_yn("Skip katapult (no bootloader)?", default=False)
    call_action(
        cli.add_mcu_type,
        argparse.Namespace(
            type=type_name,
            chipset=chipset,
            klipper_args=klipper_args,
            katapult_args=katapult_args,
            no_katapult=no_katapult,
            force=False,
        ),
    )
    return type_name


def menu_remove_mcu_type() -> None:
    mcu_type = pick_mcu_type(allow_new=False, accepts=every_type)
    if mcu_type is None:
        return
    call_action(cli.remove_mcu_type, argparse.Namespace(type=mcu_type, force=False))


def menu_add_serial() -> None:
    mcu_type = pick_mcu_type(allow_new=True, accepts=every_type)
    if mcu_type is None:
        return
    entries = _entries()  # refresh - pick_mcu_type may have just created this type
    entry = next((e for e in entries if e.name == mcu_type), None)
    if entry is None:
        return
    untracked = _untracked_for(entry, entries)

    options = [f"{d.serial} (detected on bus)" for d in untracked]
    options.append("Enter serial manually")
    idx = prompt_choice(f"Select a serial to add to '{mcu_type}'", options)
    if idx is None:
        return
    serial = untracked[idx].serial if idx < len(untracked) else prompt_nonempty("Serial string")
    call_action(cli.add_serial, argparse.Namespace(type=mcu_type, serial=serial))


def menu_remove_serial() -> None:
    entries = _entries()
    mcu_type = pick_mcu_type(entries, allow_new=False, accepts=every_type)
    if mcu_type is None:
        return
    entry = next(e for e in entries if e.name == mcu_type)
    serial = pick_tracked_serial(entry)
    if serial is None:
        return
    call_action(cli.remove_serial, argparse.Namespace(type=mcu_type, serial=serial))


def menu_add_mcu() -> None:
    mcu_type = pick_mcu_type(allow_new=True, accepts=kconfig_type)
    if mcu_type is None:
        return
    call_action(cli.add_mcu, argparse.Namespace(type=mcu_type))


def menu_menuconfig() -> None:
    mcu_type = pick_mcu_type(allow_new=True, accepts=kconfig_type)
    if mcu_type is None:
        return
    fw = pick_fw_target()
    if fw is None:
        return
    call_action(
        cli.make_menuconfig_cmd, argparse.Namespace(type=mcu_type, fw=fw, no_pause=False)
    )


def menu_build() -> None:
    mcu_type = pick_mcu_type(allow_new=True, accepts=buildable_type)
    if mcu_type is None:
        return
    entry = next((e for e in _entries() if e.name == mcu_type), None)
    if entry is None:
        return
    fw: str | None = None
    if build_asks_fw(entry):
        fw = pick_fw_target()
        if fw is None:
            return
    call_action(cli.build_fw_cmd, argparse.Namespace(type=mcu_type, fw=fw, jobs=None))


def menu_flash() -> None:
    entries = _entries()
    mcu_type = pick_mcu_type(entries, allow_new=False, accepts=flashable_type)
    if mcu_type is None:
        return
    entry = next(e for e in entries if e.name == mcu_type)
    serials = list(entry.serials)

    # Only the scopes `flash_fw_cmd` accepts for this type's builder.
    whole_type = entry.builder in FLASH_TYPE_LEVEL_BUILDERS
    scope_options = []
    if whole_type:
        scope_options.append("Flash every tracked serial under this type")
    if serials:
        scope_options.append("Flash one specific device")
    if not scope_options:
        print(f"No serials tracked under '{mcu_type}'.")
        return
    idx = prompt_choice(f"Flash scope for '{mcu_type}'", scope_options)
    if idx is None:
        return

    if whole_type and idx == 0:
        ns = argparse.Namespace(type=mcu_type, serial=None, yes=False, force=False)
    else:
        serial = pick_serial_for_type(entry, entries)
        if serial is None:
            return
        ns = argparse.Namespace(type=mcu_type, serial=serial, yes=False, force=False)
    call_action(cli.flash_fw_cmd, ns)


def menu_update_all() -> None:
    call_action(cli.update_all, argparse.Namespace(yes=False, jobs=None))


MENU_ITEMS: list[tuple[str, Callable[[], object]]] = [
    ("List MCU types / status", menu_status),
    ("Add MCU type", menu_add_mcu_type),
    ("Remove MCU type", menu_remove_mcu_type),
    ("Add serial to existing type", menu_add_serial),
    ("Remove serial from a type", menu_remove_serial),
    ("Guided add-mcu (new physical board)", menu_add_mcu),
    ("Menuconfig", menu_menuconfig),
    ("Build firmware", menu_build),
    ("Flash device(s)", menu_flash),
    ("Update all (rebuild + reflash everything)", menu_update_all),
]


def run_menu() -> None:
    try:
        while True:
            print("\n=== Klipper/Katapult Firmware Manager ===")
            if cli.ctx().settings.dry_run:
                print("  (dry-run mode: nothing will actually be built or flashed)")
            for i, (label, _) in enumerate(MENU_ITEMS, 1):
                print(f"  {i}. {label}")
            print("  0. Exit")
            raw = input("> ").strip()
            if raw == "0":
                print("Goodbye.")
                return
            try:
                choice = int(raw)
            except ValueError:
                print("Please enter a number.")
                continue
            if not (1 <= choice <= len(MENU_ITEMS)):
                print("Out of range, try again.")
                continue
            MENU_ITEMS[choice - 1][1]()
            input("\nPress Enter to continue...")
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")
