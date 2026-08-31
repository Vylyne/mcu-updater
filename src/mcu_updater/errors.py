"""Typed failures.

Every class carries a stable ``code`` string. That code is the contract: it
becomes ``error.data.code`` over JSON-RPC and is what the Mainsail panel
switches on to decide what to show. Renaming one is a breaking API change, so
treat these strings as public.

Extra context goes in ``**data`` rather than being formatted into the message,
so a UI can render it structurally (e.g. offering "did you mean -t X?" as a
button rather than parsing it out of English prose).
"""

from __future__ import annotations

from typing import Any


class UpdaterError(Exception):
    """Base for everything this package raises deliberately."""

    code = "error"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self.message = message
        self.data: dict[str, Any] = data

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "data": self.data}


# --- configuration / registry ---


class ConfigError(UpdaterError):
    code = "config"


class ConfigCorruptError(ConfigError):
    """The registry exists but cannot be interpreted.

    Raised rather than silently returning an empty registry: "no MCU types
    configured" looks identical to a healthy fresh install, and the next
    add-type would happily write a fresh file over the top of the real one.
    """

    code = "config_corrupt"


class UnknownTypeError(ConfigError):
    code = "unknown_type"


class InvalidTypeNameError(ConfigError):
    """A type name that cannot safely be used.

    The name is not just a label: it becomes a ``[mcu <name>]`` section header and
    a directory under both the config and data trees. So a name containing a path
    separator or ``..`` would write outside them, which matters now that the name
    can arrive from a browser rather than only from an argument someone typed.
    """

    code = "invalid_type_name"


class DuplicateTypeError(ConfigError):
    code = "duplicate_type"


class UnknownSerialError(ConfigError):
    code = "unknown_serial"


class AmbiguousSerialError(ConfigError):
    """One serial tracked under more than one type - always a misconfiguration."""

    code = "ambiguous_serial"


class SerialTrackedElsewhereError(ConfigError):
    code = "serial_tracked_elsewhere"


class UuidTrackedElsewhereError(ConfigError):
    """A CAN uuid already tracked under a different type.

    Kept separate from `SerialTrackedElsewhereError` rather than reused for it
    - a uuid and a by-id serial are different identity namespaces, the same
    false-cognate reasoning that keeps `canbus_uuids:` its own config key
    instead of folding into `serials:`.
    """

    code = "uuid_tracked_elsewhere"


class UnknownUuidError(ConfigError):
    """A CAN uuid not tracked under any type. `resolve_uuid`'s counterpart to
    `UnknownSerialError`, for the same reason `find_types_for_uuid` is
    separate from `find_types_for_serial`."""

    code = "unknown_uuid"


class AmbiguousUuidError(ConfigError):
    """One CAN uuid tracked under more than one type - always a
    misconfiguration, same as `AmbiguousSerialError`."""

    code = "ambiguous_uuid"


# --- build ---


class SourceTreeMissingError(UpdaterError):
    code = "source_missing"


class ConfigNotFoundError(UpdaterError):
    """No saved .config for this type/fw - menuconfig has never been run."""

    code = "no_saved_config"


class BuildError(UpdaterError):
    code = "build_failed"


class TtyRequiredError(UpdaterError):
    """Hard barrier between the ncurses path and the daemon.

    make menuconfig needs a real terminal; the agent must never reach it.
    """

    code = "tty_required"


# --- flash ---


class FlashError(UpdaterError):
    code = "flash_failed"


class DeviceNotFoundError(FlashError):
    code = "device_not_found"


class BootloaderTimeoutError(FlashError):
    code = "bootloader_timeout"


class AmbiguousDfuError(FlashError):
    """More than one *device* sitting in DFU mode.

    dfu-util targeting 0483:df11 would flash whichever answers first, which
    with two boards attached means flashing the wrong one.

    Counted by distinct device, never by line: `dfu-util -l` prints one line per
    DFU *altsetting*, so a single STM32 shows up three times (alt=0/1/2) with the
    same devnum, path and serial.
    """

    code = "ambiguous_dfu"


class BootselNotMountedError(FlashError):
    """An RP2040 in BOOTSEL is attached but nothing mounted its volume.

    Distinct from `device_not_found` for the same reason `DfuPermissionError`
    is: "hold BOOTSEL and replug" sends the user to redo a step that already
    worked, when the real fix is a udev rule (or a manual mount) on a headless
    host with no automounter.
    """

    code = "bootsel_not_mounted"


class DfuPermissionError(FlashError):
    """dfu-util can see the board but cannot open it.

    Distinct from `device_not_found` on purpose: "no DFU device detected, hold
    BOOT0 and replug" sends the user to re-do something that already worked,
    when the real fix is a udev rule.
    """

    code = "dfu_permission_denied"


class ToolMissingError(UpdaterError):
    code = "tool_missing"


class UnsupportedChipsetError(UpdaterError):
    code = "unsupported_chipset"


class ServiceControlError(UpdaterError):
    """Could not stop or start the klipper service.

    Raised rather than continuing: flashing while klipper holds the serial port
    is unsafe, so a failed stop must abort the operation.
    """

    code = "service_control"


class FlashingDisabledError(UpdaterError):
    """The agent's flash capability is switched off in mcu-updater.cfg.

    Off by default so that installing an update never silently grants a browser
    the ability to reflash the printer.
    """

    code = "flashing_disabled"


# --- concurrency / safety ---


class BusyError(UpdaterError):
    code = "busy"


class PrintInProgressError(BusyError):
    code = "print_in_progress"


class OperationCancelled(UpdaterError):
    code = "cancelled"


# --- profiles ---


class ProfileError(UpdaterError):
    code = "profile"


class ProfileNotFoundError(ProfileError):
    """The named seed file isn't in the firmware tree.

    Almost always a tree that hasn't been pulled rather than a typo - the
    vendor ships these files, so `data.available` carries what is actually
    there so a UI can offer the real list instead of echoing the bad name.
    """

    code = "profile_not_found"


class ProfileCustomisedError(ProfileError):
    """Reseeding would discard answers this tool did not put there.

    Refused rather than backed up and overwritten. The saved answers are the
    one thing here that cannot be regenerated, and a user who edited them did
    so on purpose - so the override is explicit and theirs to give.
    """

    code = "profile_customised"


class OffsetMismatchError(ProfileError):
    """The bootloader and the application disagree about where the app starts.

    Katapult's ``LAUNCH_APP_ADDRESS`` is the address it jumps to; the
    application's ``FLASH_APPLICATION_ADDRESS`` is where it was linked to run.
    A pair that disagrees produces two binaries that each build cleanly, flash
    cleanly, and leave a board that does not come back - so this is refused at
    the point the second config is written rather than discovered afterwards.
    """

    code = "offset_mismatch"


# --- kconfig ---


class KconfigError(UpdaterError):
    code = "kconfig"


class KconfigSessionError(KconfigError):
    code = "no_session"
