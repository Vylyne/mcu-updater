"""fw.settings.* / fw.type.add|update|remove / fw.serial.add|remove -- registry mutation."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from ... import firmware
from ... import settings as settings_mod
from ...config import MakefilePatch, Registry
from ...devices import (
    scan,
)
from ...errors import (
    SerialTrackedElsewhereError,
    UuidTrackedElsewhereError,
)
from ...settings import save_settings
from ..rpc import ERR_INVALID_PARAMS, RpcError
from ._api import _Base

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _parse_extra_repos(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RpcError("extra_repos must be a list of paths", ERR_INVALID_PARAMS)
    return [str(p).strip() for p in value if str(p).strip()]


def _parse_makefile_patches(value: Any) -> list[MakefilePatch]:
    if not isinstance(value, list):
        raise RpcError(
            "makefile_patches must be a list of {file, line} objects", ERR_INVALID_PARAMS
        )
    patches: list[MakefilePatch] = []
    for raw in value:
        entry = raw if isinstance(raw, dict) else {}
        patch = MakefilePatch(
            file=str(entry.get("file") or "").strip(),
            line=str(entry.get("line") or "").strip(),
        )
        if not patch.is_valid():
            raise RpcError(
                f"a makefile patch needs both 'file' and 'line': {raw!r}",
                data={
                    "code": "invalid_makefile_patch",
                    "message": "incomplete makefile patch",
                    "data": {"patch": raw},
                },
            )
        patches.append(patch)
    return patches


def _extra_repo_warnings(repos: list[str]) -> list[str]:
    """One warning per path that isn't (yet) a git checkout.

    Not a refusal - `type_add` is deliberately reachable before hardware or
    even source exists (see `type_add`'s docstring), and a not-yet-cloned
    extra repo is the same kind of "fine for now" state. `git_head()` already
    returns None rather than raising for a missing or non-git path, so
    staleness itself degrades silently; this is the one place that says so.
    """
    from ...build import git_head

    return [
        f"{repo} has no git HEAD yet - staleness won't fire for it until it does."
        for repo in repos
        if git_head(repo) is None
    ]


class RegistryMixin(_Base):
    def settings_get(self, args: dict) -> dict[str, Any]:
        return {"settings": dataclasses.asdict(self.settings())}

    #: Settings the panel may change. Everything here is a *behaviour* preference.
    #:
    #: `stop_services` and `service_backend` are deliberately absent. They
    #: describe how this host is wired, not what the user wants, and getting
    #: them wrong breaks the agent's ability to stop what a flash needs down -
    #: `service_backend: null` in particular would let a real flash proceed
    #: *without* stopping anything, which fails at best and corrupts a board at
    #: worst. Nothing about a browser form makes that a sensible thing to
    #: offer; editing the cfg is the right amount of friction. Remote service
    #: control is privilege, full stop.
    #: `ui_accent_color` is the one exception to "everything here is a
    #: behaviour preference" - it is cosmetic, but still belongs on this list:
    #: it is still a browser-settable, agent-stored value, just not one the
    #: agent itself ever reads.
    SETTABLE = (
        "make_jobs",
        "clean_before_build",
        "reseed_on_build",
        "dry_run",
        "enable_flashing",
        "allow_flash_while_printing",
        "log_ring_size",
        "ui_accent_color",
    )

    #: Changing either of these changes what a flash is allowed to do, so they are
    #: logged at warning level: the agent log is the only audit trail there is.
    LOUD_SETTINGS = ("enable_flashing", "allow_flash_while_printing")

    def settings_set(self, args: dict) -> dict[str, Any]:
        """Change tool settings. Only the keys supplied are touched.

        Writes through `save_settings`, which load-modify-writes the ``[updater]``
        section via CfgDocument - so the ``[mcu ...]`` sections and every comment
        in the shared file survive.
        """
        patch = args.get("settings")
        if not isinstance(patch, dict) or not patch:
            raise RpcError(
                "'settings' must be a non-empty object of the values to change",
                ERR_INVALID_PARAMS,
            )

        unknown = [k for k in patch if k not in self.SETTABLE]
        if unknown:
            raise RpcError(
                f"cannot set {', '.join(sorted(unknown))} from here. Settable: "
                f"{', '.join(self.SETTABLE)}. 'stop_services' and 'service_backend' "
                f"describe how this host is wired and are edited in "
                f"{self.paths.settings_file}.",
                data={
                    "code": "setting_not_settable",
                    "message": "one or more settings cannot be changed remotely",
                    "data": {"rejected": sorted(unknown), "settable": list(self.SETTABLE)},
                },
            )

        current = self.settings()
        changed: dict[str, Any] = {}
        for key, raw in patch.items():
            value = self._coerce_setting(key, raw)
            if value != getattr(current, key):
                changed[key] = value
            setattr(current, key, value)

        save_settings(self.paths.settings_file, current)

        for key in self.LOUD_SETTINGS:
            if key in changed and self._log is not None:
                self._log.warning(f"{key} was changed to {changed[key]} from the panel")

        self._changed()
        return {"settings": dataclasses.asdict(current), "changed": sorted(changed)}

    @staticmethod
    def _coerce_setting(key: str, raw: Any) -> Any:
        """Validate one setting, refusing rather than clamping.

        Silently clamping a value the user typed means the UI shows one thing and
        the tool does another - the same class of quiet disagreement that made a
        working QGL refusal look like a dead agent.
        """
        # From the settings module rather than a second list here: a new boolean
        # added there and forgotten here would be refused as "must be a whole
        # number", which is a baffling thing to read about a switch.
        if key in settings_mod.BOOL_FIELDS:
            if not isinstance(raw, bool):
                raise RpcError(f"'{key}' must be true or false", ERR_INVALID_PARAMS)
            return raw

        # Same reasoning as the bool branch above: driven by the settings
        # module's own set, not a second hand-copied list. Must come before
        # the int fallthrough below - a string would otherwise be refused as
        # "must be a whole number", which is nonsense to read about a colour.
        if key in settings_mod.STR_FIELDS:
            if not isinstance(raw, str):
                raise RpcError(f"'{key}' must be a string", ERR_INVALID_PARAMS)
            if key == "ui_accent_color" and raw != "" and not _HEX_COLOR.match(raw):
                raise RpcError(
                    "'ui_accent_color' must be empty (the UI's own default) or a "
                    "6-digit hex colour like '#2196f3'",
                    ERR_INVALID_PARAMS,
                )
            return raw

        if isinstance(raw, bool) or not isinstance(raw, int):
            # bool is an int subclass, so it would otherwise sail through.
            raise RpcError(f"'{key}' must be a whole number", ERR_INVALID_PARAMS)

        if key == "make_jobs":
            # -1 and below mean "one job per CPU"; 0 means pass no -j flag at all.
            if raw < -1 or raw > 64:
                raise RpcError(
                    "'make_jobs' must be between -1 and 64 (-1 = one per CPU, 0 = no -j flag)",
                    ERR_INVALID_PARAMS,
                )
            return raw

        if key == "log_ring_size":
            if raw < 100 or raw > 100_000:
                raise RpcError("'log_ring_size' must be between 100 and 100000", ERR_INVALID_PARAMS)
            return raw

        raise RpcError(f"'{key}' cannot be set", ERR_INVALID_PARAMS)

    # -- registry mutation -------------------------------------------------

    def _changed(self) -> None:
        """Announce a mutation. Never lets a broadcast failure undo a good write.

        The registry is already saved by the time this runs, so raising here would
        report a failure for something that succeeded - and the client would then
        show stale state *and* an error.
        """
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception as exc:  # noqa: BLE001
            if self._log is not None:
                self._log.warning(f"could not emit state after a change: {exc}")

    @staticmethod
    def _require_str(args: dict, key: str) -> str:
        value = args.get(key)
        if not value or not str(value).strip():
            raise RpcError(f"'{key}' is required", ERR_INVALID_PARAMS)
        return str(value).strip()

    def serial_add(self, args: dict) -> dict[str, Any]:
        """Track a physical board under an existing type.

        Touches nothing but the registry: no build, no flash, no board.
        """
        name = self._require_str(args, "name")
        serial = self._require_str(args, "serial")

        # An unprovisioned Roadrunner's serial is `RR-UNPROVISIONED-<flash-uid>`
        # - the trailing 16 hex characters ARE the RP2040 flash UID, which this
        # plan's constraints forbid ever persisting. The panel hides its own
        # generic adopt affordance for this row (BusPanel.vue), but this is a
        # direct RPC too, so the refusal belongs here regardless of whether the
        # device is currently visible on the bus - unlike `not_an_mcu` below,
        # this is a property of the serial string itself, not of a live scan.
        from ...discovery.roadrunner import UNPROVISIONED_RE

        if UNPROVISIONED_RE.fullmatch(serial):
            raise RpcError(
                f"'{serial}' is an unprovisioned Roadrunner's diagnostic identity, "
                f"not a stable serial - provision it first with fw.roadrunner.provision, "
                f"then track the resulting RR-... serial.",
                data={
                    "code": "roadrunner_unprovisioned",
                    "message": "refusing to track an unprovisioned Roadrunner's diagnostic serial",
                    "data": {"serial": serial},
                },
            )

        # The panel only offers `adoptable` devices, but the panel is not the only
        # possible caller - enforce the same rule here so a direct RPC cannot add
        # a Knomi's CH340 as a board. Only refused when we can actually see it:
        # a serial for a board that is currently unplugged is legitimate.
        present = next((d for d in scan(self.paths) if d.serial == serial), None)
        if present is not None and not present.is_mcu:
            raise RpcError(
                f"{serial} is on the bus but does not look like a Klipper or Katapult "
                f"board (it enumerates as '{present.fw}'). Refusing to track it - "
                f"building firmware for a USB serial adapter cannot end well.",
                data={
                    "code": "not_an_mcu",
                    "message": "device is not a Klipper or Katapult board",
                    "data": {"serial": serial, "fw": present.fw, "path": present.path},
                },
            )

        with Registry.mutate(self.paths, f"add serial {serial}") as reg:
            mcu = reg.get(name)  # UnknownTypeError if the type doesn't exist
            # One board tracked under two types would get flashed twice with
            # different firmware, so this is refused rather than merged.
            elsewhere = [t for t in reg.find_types_for_serial(serial) if t != name]
            if elsewhere:
                raise SerialTrackedElsewhereError(
                    f"serial '{serial}' is already tracked under '{elsewhere[0]}'. "
                    f"Remove it from there first if it really belongs to '{name}'.",
                    serial=serial,
                    requested=name,
                    tracked_under=elsewhere,
                )
            added = reg.add_serial(name, serial)
            chipset = mcu.chipset

        self._changed()
        return {"name": name, "serial": serial, "added": added, "chipset": chipset}

    def _require_family(self, args: dict, key: str = "firmware") -> str:
        """The firmware family named in `args`, checked against what exists.

        Refused rather than accepted-and-broken: an undeclared family resolves
        to the conventional `~/<name>`, so a typo would silently produce a type
        that builds nothing and reports "never built" for good. `Registry.load`
        already refuses the same thing when the file is read by hand; this is
        the same rule applied to the same value arriving from a browser.
        """
        value = str(args.get(key) or "").strip()
        if not value:
            return "klipper"  # same default an absent `firmware:` key gets
        known = self._fw_names()
        if value not in known:
            raise RpcError(
                f"'{value}' is not a known firmware family. Known: "
                f"{', '.join(known)}. Declare it with a [firmware {value}] "
                f"section in {self.paths.main_config} first.",
                data={
                    "code": "unknown_firmware",
                    "message": "no such firmware family",
                    "data": {"firmware": value, "known": list(known)},
                },
            )
        return value

    def type_add(self, args: dict) -> dict[str, Any]:
        """Register a board model.

        **No hardware has to be present.** A type describes a model, not a
        board on the bus, and declaring one first is how you reach menuconfig
        for a probe that is still in the post - which is the order the work
        actually happens in. The panel offered this only from a device it could
        already see, which made a board you did not have yet unreachable.

        The name is validated by the model, not here - it becomes both a config
        section and a directory, and the CLI must apply the same rule.
        """
        name = self._require_str(args, "name")
        chipset = self._require_str(args, "chipset")
        installed = args.get("katapult_installed")
        application = self._require_family(args)

        # Parsed before the mutation, not after - a bad patch must not leave
        # the type half-created.
        extra_repos: dict[str, list[str]] = {}
        makefile_patches: dict[str, list[MakefilePatch]] = {}
        for fw in ("klipper", "katapult"):
            if f"{fw}_extra_repos" in args:
                extra_repos[fw] = _parse_extra_repos(args[f"{fw}_extra_repos"])
            if f"{fw}_makefile_patches" in args:
                makefile_patches[fw] = _parse_makefile_patches(args[f"{fw}_makefile_patches"])

        with Registry.mutate(self.paths, f"add type {name}") as reg:
            mcu = reg.add_type(
                name,
                chipset,
                klipper_args=str(args.get("klipper_extra_args") or "").strip(),
                katapult_args=str(args.get("katapult_extra_args") or "").strip(),
                katapult_installed=True if installed is None else bool(installed),
                application=application,
            )
            for fw, repos in extra_repos.items():
                mcu.fw(fw).extra_repos = repos
            for fw, patches in makefile_patches.items():
                mcu.fw(fw).makefile_patches = patches

        self._changed()
        result: dict[str, Any] = {"name": name, "chipset": chipset, "firmware": application}
        warnings = [w for repos in extra_repos.values() for w in _extra_repo_warnings(repos)]
        if warnings:
            result["warnings"] = warnings
        return result

    def type_update(self, args: dict) -> dict[str, Any]:
        """Edit a type in place. Only the keys supplied are touched.

        Renaming is deliberately not offered. The name is also a directory holding
        the saved menuconfig answers, so a rename is a filesystem migration rather
        than a config edit - and the answers are the one thing here that cannot be
        regenerated.
        """
        name = self._require_str(args, "name")
        if args.get("new_name"):
            raise RpcError(
                "renaming a type isn't supported here: the name is also the "
                "directory holding its saved menuconfig answers. Add a type with "
                "the new name, move that directory across, then remove the old one.",
                data={"code": "rename_unsupported", "message": "renaming is a migration"},
            )

        warnings: list[str] = []
        families = firmware.load(self.paths)
        with Registry.mutate(self.paths, f"update type {name}") as reg:
            mcu = reg.get(name)

            if "chipset" in args:
                chipset = self._require_str(args, "chipset")
                if chipset != mcu.chipset:
                    # Staleness compares the source commit and a hash of the
                    # .config, neither of which changes when the chipset does - so
                    # a binary built for the old chip would keep reporting itself
                    # as fresh. Say so rather than let it be flashed.
                    if self.artifact(name, mcu.application(families)).get("has_bin"):
                        warnings.append(
                            f"the built firmware for '{name}' was compiled for "
                            f"{mcu.chipset}. Rebuild before flashing - staleness "
                            f"cannot detect a chipset change on its own."
                        )
                    mcu.chipset = chipset

            if "firmware" in args:
                application = self._require_family(args)
                current = mcu.application(families)
                if application != current:
                    # Same reasoning as a chipset change, and stronger: the
                    # artifact was built from a different source tree entirely,
                    # and nothing in the provenance record would notice.
                    if self.artifact(name, current).get("has_bin"):
                        warnings.append(
                            f"the built firmware for '{name}' came from "
                            f"{current}. Rebuild before flashing - "
                            f"staleness compares a tree against itself and "
                            f"cannot detect the tree being swapped."
                        )
                    # The application changes; whatever bootloader this type
                    # already carried (if any) is unaffected.
                    boot = mcu.bootloader(families)
                    mcu.firmwares = [application] + ([boot] if boot else [])

            for fw in mcu.fw_order():
                key = f"{fw}_extra_args"
                if key in args:
                    mcu.fw(fw).extra_args = str(args.get(key) or "").strip()

                repos_key = f"{fw}_extra_repos"
                if repos_key in args:
                    repos = _parse_extra_repos(args[repos_key])
                    mcu.fw(fw).extra_repos = repos
                    warnings.extend(_extra_repo_warnings(repos))

                patches_key = f"{fw}_makefile_patches"
                if patches_key in args:
                    mcu.fw(fw).makefile_patches = _parse_makefile_patches(args[patches_key])

            if "katapult_installed" in args:
                installed = bool(args.get("katapult_installed"))
                without_katapult = [f for f in mcu.firmwares if f != "katapult"]
                mcu.firmwares = without_katapult + (["katapult"] if installed else [])

            result: dict[str, Any] = {
                "name": name,
                "chipset": mcu.chipset,
                "firmware": mcu.application(families),
            }

        self._changed()
        result["warnings"] = warnings
        return result

    def type_remove(self, args: dict) -> dict[str, Any]:
        """Stop tracking a board model.

        Removes the registry section and nothing else. The saved menuconfig
        answers stay on disk, which matters because they are the one thing here
        that genuinely cannot be regenerated - so re-adding the same name gets
        everything back.

        Refuses while boards are still tracked under it unless forced: removing a
        type with live boards is far more often a misclick than an intention.
        """
        name = self._require_str(args, "name")
        force = bool(args.get("force"))

        with Registry.mutate(self.paths, f"remove type {name}") as reg:
            mcu = reg.get(name)
            count = len(mcu.serials)
            if count and not force:
                raise RpcError(
                    f"'{name}' still tracks {count} board(s). Remove them first, or "
                    f"confirm to remove the type and its serials together.",
                    data={
                        "code": "type_has_serials",
                        "message": "type still tracks boards",
                        "data": {"type": name, "serials": list(mcu.serials)},
                    },
                )
            reg.remove_type(name)

        self._changed()
        return {
            "name": name,
            "removed_serials": count,
            # The panel promises this, so it is part of the contract rather than
            # only a comment.
            "kept_config_dir": self.paths.type_dir(name),
        }

    def serial_remove(self, args: dict) -> dict[str, Any]:
        """Stop tracking a board.

        Deliberately non-destructive, and the panel should say so: the board keeps
        its firmware, the type keeps its saved .config and its built artifacts, and
        re-adding the serial makes it flashable again with nothing to rebuild.
        """
        name = self._require_str(args, "name")
        serial = self._require_str(args, "serial")

        with Registry.mutate(self.paths, f"remove serial {serial}") as reg:
            reg.get(name)  # UnknownTypeError if the type doesn't exist
            removed = reg.remove_serial(name, serial)

        self._changed()
        return {"name": name, "serial": serial, "removed": removed}

    def canbus_add(self, args: dict) -> dict[str, Any]:
        """Track a CAN-addressed board under an existing type.

        Mirrors `serial_add`'s validation shape - type-exists check,
        cross-type double-tracking refusal - minus the `is_mcu` bridge-chip
        check: every CAN admin responder is inherently a Klipper- or
        Katapult-speaking node (the protocol itself names the application in
        its reply to `--query`), unlike a USB CH340 bridge chip that merely
        looks like a board on `/dev/serial/by-id`. There is no non-board case
        to guard against here.
        """
        name = self._require_str(args, "name")
        uuid = self._require_str(args, "uuid")

        with Registry.mutate(self.paths, f"add canbus uuid {uuid}") as reg:
            mcu = reg.get(name)  # UnknownTypeError if the type doesn't exist
            # One board tracked under two types would get flashed twice with
            # different firmware, so this is refused rather than merged - same
            # rule `serial_add` enforces for by-id serials.
            elsewhere = [t for t in reg.find_types_for_uuid(uuid) if t != name]
            if elsewhere:
                raise UuidTrackedElsewhereError(
                    f"CAN uuid '{uuid}' is already tracked under '{elsewhere[0]}'. "
                    f"Remove it from there first if it really belongs to '{name}'.",
                    uuid=uuid,
                    requested=name,
                    tracked_under=elsewhere,
                )
            added = reg.add_canbus_uuid(name, uuid)
            chipset = mcu.chipset

        self._changed()
        return {"name": name, "uuid": uuid, "added": added, "chipset": chipset}

    def canbus_remove(self, args: dict) -> dict[str, Any]:
        """Stop tracking a CAN-addressed board.

        Deliberately non-destructive, mirroring `serial_remove`: the type
        keeps its saved .config and its built artifacts, and re-adding the
        uuid makes it flashable again with nothing to rebuild.
        """
        name = self._require_str(args, "name")
        uuid = self._require_str(args, "uuid")

        with Registry.mutate(self.paths, f"remove canbus uuid {uuid}") as reg:
            reg.get(name)  # UnknownTypeError if the type doesn't exist
            removed = reg.remove_canbus_uuid(name, uuid)

        self._changed()
        return {"name": name, "uuid": uuid, "removed": removed}

    def bus_ignore(self, args: dict) -> dict[str, Any]:
        """Hide a bus device from the "new board?" flow. Idempotent.

        A dedicated RPC rather than `fw.settings.set` - see `Settings.ignored_serials`
        for why. Flag, not filter: the device stays in `fw.bus.scan`'s `devices`,
        just marked, so a mis-ignored board is recoverable from the panel rather
        than only by hand-editing the cfg on the printer.
        """
        serial = self._require_str(args, "serial")
        current = self.settings()
        if serial not in current.ignored_serials:
            current.ignored_serials.append(serial)
            save_settings(self.paths.settings_file, current)
            self._changed()
        return {"serial": serial, "ignored": True}

    def bus_unignore(self, args: dict) -> dict[str, Any]:
        """Reverse `bus_ignore`. Idempotent."""
        serial = self._require_str(args, "serial")
        current = self.settings()
        if serial in current.ignored_serials:
            current.ignored_serials.remove(serial)
            save_settings(self.paths.settings_file, current)
            self._changed()
        return {"serial": serial, "ignored": False}

    def canbus_ignore(self, args: dict) -> dict[str, Any]:
        """Hide every sighting of a CAN UUID from the new-board flow. Idempotent."""
        uuid = self._require_str(args, "uuid")
        current = self.settings()
        if uuid not in current.ignored_canbus_uuids:
            current.ignored_canbus_uuids.append(uuid)
            save_settings(self.paths.settings_file, current)
            self._changed()
        return {"uuid": uuid, "ignored": True}

    def canbus_unignore(self, args: dict) -> dict[str, Any]:
        """Reverse `canbus_ignore`. Idempotent."""
        uuid = self._require_str(args, "uuid")
        current = self.settings()
        if uuid in current.ignored_canbus_uuids:
            current.ignored_canbus_uuids.remove(uuid)
            save_settings(self.paths.settings_file, current)
            self._changed()
        return {"uuid": uuid, "ignored": False}

    # -- jobs --------------------------------------------------------------
