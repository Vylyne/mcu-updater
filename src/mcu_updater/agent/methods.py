"""The agent's JSON-RPC surface.

Every method takes one object and returns one object, and **every method returns
in well under a second**. That is a hard rule, not a guideline: Moonraker awaits
our reply with no timeout, so anything slow would hold a front end's HTTP request
open. Long-running work (build, flash) returns a job id immediately instead -
those arrive in a later phase.

The shapes here are the contract with the Mainsail panel. They are documented in
``docs/agent-api.md`` and version-gated by ``fw.ping``'s ``api_version``.
"""

from __future__ import annotations

import dataclasses
import os
import platform
import re
import time
from typing import Any, Callable, Optional

from .. import API_VERSION, __version__, firmware, flashers, profiles, providers
from .. import settings as settings_mod
from ..build import read_sidecar
from ..config import Registry
from ..devices import (
    STATE_KATAPULT,
    STATE_OFFLINE,
    BusDevice,
    device_state,
    parse_entry,
    scan,
)
from ..errors import (
    DfuPermissionError,
    OperationCancelled,
    SerialTrackedElsewhereError,
    ToolMissingError,
    UpdaterError,
)
from ..pairings import PAIRING_TTL as _PAIRING_TTL
from ..paths import REENUMERATE_TIMEOUT, Paths
from ..settings import Settings, load_settings, save_settings
from ..states import (
    ARTIFACT_CHANGED,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    UNKNOWN_VERSION,
    ArtifactStatus,
    DeviceStatus,
)
from .rpc import ERR_INVALID_PARAMS, ERR_METHOD_NOT_FOUND, MethodNotFound, RpcError

#: How long a Moonraker query may block before we give up and report unknown.
#: Small on purpose - these are best-effort enrichments of fw.status, and the
#: whole call has a sub-second budget.
PROBE_TIMEOUT = 1.5


def _board_target(board: dict) -> flashers.FlashTarget:
    """One entry from `_boards_to_flash`, as something a batch can write.

    A one-line alias so the two bulk callers say the same thing, and so the
    board selection's dict shape - which is on the wire - stays the selection's
    business rather than leaking a second copy into each of them.
    """
    return flashers.flashtool.target_for(board)


def _screen_json(target: flashers.FlashTarget) -> dict[str, Any]:
    """A selected screen, for a caller naming what is about to happen.

    The uniform slots plus the two facts a confirmation actually reads out: the
    klipper section a human recognises, and why this one was picked.
    """
    return {
        **target.to_json(),
        "name": target.detail["screen"]["name"],
        "section": target.detail["screen"]["section"],
        "reason": target.detail.get("reason"),
    }


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


#: git describe embeds the commit as a g<hex> token. Anything after it - notably
#: `-dirty`, which a makefile-patched build always carries - is noise here.
_FW_SHA_RE = re.compile(r"(?:^|-)g([0-9a-f]{7,40})(?:-|$)")

#: The MCU object list only changes when Klipper restarts, so it is worth caching:
#: it is a whole extra round trip and fw.status has a sub-second budget.
MCU_NAMES_TTL = 60.0


def _running_sha(version: str) -> Optional[str]:
    match = _FW_SHA_RE.search(version or "")
    return match.group(1) if match else None


def _serial_from_path(path: str) -> Optional[str]:
    """The serial component of a /dev/serial/by-id path.

    Reuses the bus parser rather than string-slicing, so the two cannot disagree
    about what counts as a serial.
    """
    parsed = parse_entry(os.path.basename(path), os.path.dirname(path))
    return parsed.serial if parsed is not None else None


def serialize_device(dev: BusDevice, tracked_by: Optional[str] = None) -> dict[str, Any]:
    return {
        "fw": dev.fw,
        "chipset": dev.chipset,
        "serial": dev.serial,
        "path": dev.path,
        "state": dev.state,
        "tracked_by": tracked_by,
        # Whether "track this" may be offered for it. Anything with two
        # underscores in its by-id name parses as a device, so the list also
        # contains USB serial adapters - and offering to adopt a Knomi's CH340 is
        # one tap from building Klipper firmware for a display.
        "is_mcu": dev.is_mcu,
    }


class Api:
    """Read-only view of the tool's state, exposed over JSON-RPC."""

    def __init__(
        self,
        paths: Paths,
        *,
        call: Optional[Callable[[str, Any, float], Any]] = None,
        runner: Optional[Any] = None,
        logger: Any = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self.paths = paths
        # Called after a mutation so every connected client re-syncs. Injected
        # rather than reached for, keeping this class free of the transport.
        self._on_change = on_change
        # Injected so this class never touches the transport directly, which is
        # what makes it testable without a Moonraker.
        self._call = call
        # None in a read-only deployment; the build methods then report that the
        # capability is absent rather than half-working.
        self.runner = runner
        self._log = logger
        # Created on first kconfig call; an agent that never opens one pays
        # neither the import nor the memory.
        self._kconfig_sessions: Optional[Any] = None
        # Cached printer-object names; see _all_object_names.
        self._object_names: Optional[list[str]] = None
        self._object_names_at = 0.0

    # -- helpers -----------------------------------------------------------

    def settings(self) -> Settings:
        """Re-read every time: the user may have edited mcu-updater.cfg."""
        try:
            return load_settings(self.paths.settings_file)
        except UpdaterError as exc:
            if self._log is not None:
                self._log.warning(f"the [updater] section is invalid, using defaults: {exc}")
            return Settings()

    def registry(self) -> Registry:
        return Registry.load(self.paths)

    def _fw_names(self) -> tuple[str, ...]:
        """Every firmware family, for validating an incoming `fw` parameter.

        Re-read like settings and the registry, for the same reason: somebody
        may have just added a `[firmware ...]` section, and refusing the family
        they declared would be a confusing way to find that out.

        Per-*type* loops use `McuType.fw_order()` instead - a loaded registry
        already carries each type's families, so iterating types costs no
        further reads on the `fw.status` path.
        """
        return firmware.names(self.paths)

    def firmware_families(self) -> list[dict[str, Any]]:
        """Every firmware family, with enough to populate a picker.

        The panel had no way to ask this. It has been using the *keys* of
        `kconfig_available` as a family list, which happens to work and is a
        coincidence rather than a contract: those values mean "has a parseable
        Kconfig", so a family built by anything other than kconfig+make would
        read as absent rather than as present-and-not-configurable.

        `present` and `configurable` are separate answers on purpose. A declared
        family whose tree has not been cloned yet is a real state - it is what
        every install looks like between adding the section and running git
        clone - and it wants "check out the source", not "unknown family".
        """
        families = firmware.load(self.paths)
        configurable = self.kconfig_available(families)
        out = []
        for name in firmware.names_of(families):
            family = firmware.resolve(self.paths, name, families)
            source = family.source_dir(self.paths)
            out.append(
                {
                    "name": name,
                    "source": source,
                    "artifact": family.artifact_name(),
                    "present": os.path.isdir(source),
                    "configurable": configurable.get(name, False),
                    # Neither can be removed by editing a config file, and the
                    # picker should not offer to.
                    "builtin": name in firmware.BUILTIN,
                }
            )
        return out

    def _probe(self, method: str, params: Any = None) -> Any:
        """Ask Moonraker something, tolerating any failure."""
        if self._call is None:
            return None
        try:
            return self._call(method, params, PROBE_TIMEOUT)
        except Exception:  # noqa: BLE001 - enrichment only, never fatal
            return None

    def klipper_service_state(self) -> Optional[str]:
        info = self._probe("machine.system_info")
        try:
            return info["system_info"]["service_state"]["klipper"]["active_state"]
        except (TypeError, KeyError):
            return None

    def is_printing(self, activity: Optional[dict] = None) -> Optional[bool]:
        state = (activity or self._printer_activity()).get("print_state")
        if state is None:
            return None
        return state in ("printing", "paused")

    # -- serialisers -------------------------------------------------------

    def artifact(self, mcu_type: str, fw: str) -> dict[str, Any]:
        cfg = self.paths.config_file(mcu_type, fw)
        binary = self.paths.bin_file(mcu_type, fw)
        uf2 = self.paths.uf2_file(mcu_type, fw)
        side = read_sidecar(self.paths, mcu_type, fw) or {}

        from ..build import artifact_status, git_head, legacy_staleness, sha256_file

        # Hashed once and handed to both. These two ask a different question of
        # the same file in the same breath - "is the binary current with its
        # inputs" and "do the inputs still say what the profile said" - and each
        # used to read it for itself, so one fw.status read every saved config
        # on every printer twice.
        config_sha = sha256_file(cfg)

        status = artifact_status(self.paths, mcu_type, fw, config_sha=config_sha)
        stale, reason = legacy_staleness(status)

        return {
            "has_config": os.path.exists(cfg),
            "config_mtime": _mtime(cfg),
            "has_bin": os.path.exists(binary),
            "bin_mtime": _mtime(binary),
            "bin_size": _size(binary),
            "has_uf2": os.path.exists(uf2),
            "built_fw_sha": side.get("fw_sha"),
            "current_fw_sha": git_head(self.paths.fw_dir(fw)),
            "stale": stale,
            "stale_reason": reason,
            # The same verdict, un-collapsed. `stale_reason` reports
            # "never_built" for a binary with no sidecar as well as for no
            # binary at all, which is a documented API string and stays that
            # way - so the distinction has to travel beside it, not instead.
            "reason": status.reason,
            "last_build_seconds": side.get("duration"),
            "last_build_at": side.get("timestamp"),
            # True when make ran olddefconfig over our saved answers, which
            # silently changes settings after a klipper git pull.
            "config_rewritten": bool(side.get("config_rewritten")),
            # Whether the saved answers still say what their profile said.
            # A third question, deliberately beside the other two rather than
            # folded into `reason`: a customised config is not a stale artifact
            # and does not want a rebuild, it wants somebody to know about it.
            "profile": profiles.status(
                self.paths, mcu_type, fw, config_sha=config_sha
            ).to_json(),
        }

    def type_status(
        self,
        reg: Registry,
        name: str,
        versions: Optional[dict[str, dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """One type's state, including what each of its boards is *running*.

        `versions` is passed in rather than looked up here, because it costs a
        Moonraker round trip and a caller with ten types would otherwise make
        ten of them.

        The source head is *not* hoisted, deliberately. It is a git call rather
        than a round trip, `git_head` caches by directory, and each type must be
        compared against the tree its own firmware is built from - a board
        running cartographer measured against upstream klipper reads as behind
        forever.
        """
        from ..build import git_head

        mcu = reg.get(name)
        if versions is None:
            versions = self.mcu_info()
        fw_head = git_head(firmware.resolve(self.paths, mcu.firmware).source_dir(self.paths))

        # Read once per type, not per board: it is one small file, but a ten-board
        # type would otherwise open it ten times.
        from ..build import FlashLog

        flashlog = FlashLog(self.paths)
        artifact_sha = (read_sidecar(self.paths, name, mcu.firmware) or {}).get("bin_sha256")

        serials = []
        for serial in mcu.serials:
            state, path = device_state(self.paths, mcu.chipset, serial)
            entry = {"serial": serial, "state": state, "path": path}
            entry.update(
                self.flash_state(
                    serial,
                    versions,
                    fw_head,
                    state=state,
                    artifact_sha=artifact_sha,
                    flashlog=flashlog,
                )
            )
            serials.append(entry)

        out: dict[str, Any] = {
            "name": name,
            "chipset": mcu.chipset,
            # Which family this type's *application* firmware comes from. Absent
            # until now, which left every consumer assuming klipper - the same
            # bug that was fixed on this side and is still live in the panel,
            # where a cartographer type reads "never built" forever because the
            # only artifact anyone looks at is artifacts.klipper.
            "firmware": mcu.firmware,
            "serials": serials,
            "artifacts": {fw: self.artifact(name, fw) for fw in mcu.fw_order()},
            "katapult_installed": mcu.katapult_installed,
            # True when at least one board is behind the source tree. Distinct from
            # the artifact being stale: "needs rebuilding" and "needs flashing" are
            # different questions, and reporting only the first is what let a board
            # 90 commits behind show as up to date.
            "needs_flash": any(s.get("needs_flash") for s in serials),
        }
        for fw in mcu.fw_order():
            cfg = mcu.fw(fw)
            block: dict[str, Any] = {
                "extra_args": cfg.extra_args,
                "makefile_patches": [p.to_json() for p in cfg.makefile_patches],
            }
            if fw == "katapult":
                block["installed"] = mcu.katapult_installed
            out[fw] = block
        return out

    def bus(self, reg: Registry) -> list[dict[str, Any]]:
        owner: dict[str, str] = {}
        for name, mcu in reg.items():
            for serial in mcu.serials:
                owner[serial] = name
        return [serialize_device(d, owner.get(d.serial)) for d in scan(self.paths)]

    # -- methods -----------------------------------------------------------

    def ping(self, args: dict) -> dict[str, Any]:
        s = self.settings()
        return {
            "api_version": API_VERSION,
            "version": __version__,
            "dry_run": s.dry_run,
            "enable_flashing": s.enable_flashing,
            "phase": 2 if self.runner is not None else 1,
            "capabilities": sorted(self.available_methods()),
            "host": {
                "nproc": os.cpu_count(),
                "python": platform.python_version(),
                "config_dir": self.paths.config_dir,
                "data_dir": self.paths.data_dir,
            },
            "now": time.time(),
        }

    def status(self, args: dict) -> dict[str, Any]:
        """One call paints the whole panel."""
        reg = self.registry()
        s = self.settings()
        current = self.runner.current() if self.runner else None
        activity = self._printer_activity()

        versions = self.mcu_info()
        # Built once and projected, rather than computed twice. `targets` says
        # the same things as these two in one shape; if it ever needs a fact
        # they do not carry, that is a missing key here, not there.
        types = [self.type_status(reg, n, versions) for n in reg.names()]
        displays = self.display_status()
        return {
            "types": types,
            "bus": self.bus(reg),
            "job": current.to_dict() if current else None,
            "recent": [j.to_dict() for j in self.runner.recent(10)] if self.runner else [],
            "locked_by": self.lock_holder(),
            # Per firmware tree, so the panel can hide the configure button
            # rather than offer one that fails on a host missing the source.
            "kconfig_available": self.kconfig_available(),
            # Every family that exists, for a picker to offer. Distinct from
            # kconfig_available, whose keys have been standing in for this.
            "firmware_families": self.firmware_families(),
            "klipper_service": self.klipper_service_state(),
            "printing": self.is_printing(activity),
            # idle_timeout.state. The panel needs this as well as `printing`:
            # print_stats stays "standby" through a manual home or QGL, and
            # stopping klipper mid-motion is just as destructive.
            "idle_state": activity.get("idle_state"),
            "settings": dataclasses.asdict(s),
            # True while nothing here can build or flash. The panel uses
            # `capabilities` from fw.ping for per-control gating.
            "read_only": self.runner is None,
            # ESP32 displays. Absent config means the key is simply an empty
            # list, so a printer with no screens pays nothing for the feature.
            "displays": displays,
            # The two above in one shape, so a panel can render an MCU, a
            # display and whatever comes next with a single component.
            "targets": self.targets(reg, types, displays),
        }

    def display_status(self) -> list[dict[str, Any]]:
        """Configured display types, each with the screens Klipper expects.

        Rolled into fw.status so the panel paints in one call, like everything
        else. Cheap when unconfigured: no `[display]` sections means no work at
        all, not even the configfile query.
        """
        from .. import displays as displays_mod

        types = self.display_types()
        if not types:
            return []

        listed = self.display_list({})

        out = []
        for _name, display in sorted(types.items()):
            prefix = display.klipper_section
            # Once per type, not once per screen: they share a source tree, and
            # it costs three git calls.
            tree = displays_mod.source_state(display.source)
            art = displays_mod.artifact_status(self.paths, display, tree)
            screens = []
            for entry in listed["displays"]:
                if not entry["section"].startswith(prefix + " "):
                    continue
                device = displays_mod.device_status(entry.get("firmware_version"), tree)
                screens.append(
                    {
                        **entry,
                        # current | behind | dirty | unknown. Compares the sha
                        # baked into what the screen reports running against the
                        # source tree's HEAD - so unlike the MCU artifact check,
                        # this is about the device rather than a built file.
                        "firmware_state": displays_mod.legacy_firmware_state(device),
                        # The same verdict in the shared vocabulary, named as the
                        # MCU rows name theirs. Both are on the wire because the
                        # FW_* word is what the panel reads today and the reason
                        # is what it will read once it renders one kind of row.
                        "reason": device.reason,
                    }
                )
            out.append(
                {
                    **display.to_json(),
                    "screens": screens,
                    # Built by PlatformIO, not by us - so this is "is there an
                    # image on disk", not staleness. PlatformIO decides whether a
                    # rebuild is needed, and it is fast when nothing changed.
                    "has_firmware": os.path.exists(displays_mod.firmware_bin(display)),
                    # current | source_changed | dirty | never_built | unknown.
                    # Real staleness, unlike has_firmware: fw.display.flash
                    # uploads whatever is in .pio/build without building, so a
                    # tree that moved since the last build writes old firmware
                    # to every screen with nothing to say so.
                    "artifact_state": displays_mod.legacy_artifact_state(art),
                    # The same verdict, un-collapsed. The ART_* word above folds
                    # foreign_build and no_provenance together, which is why this
                    # cannot be recovered from it by the reader.
                    "artifact_reason": art.reason,
                    # Why a build would be skipped, or None. The same answer the
                    # provider gives a fleet build, from the same function - so
                    # the panel's preview cannot name work the batch will pass
                    # over, which is the whole reason screens were kept out of
                    # bulk builds until now.
                    "build_blocked": providers.platformio.source_problem(display),
                    "reachable": listed["reachable"],
                    # One klippy module serves every screen of a type, so this is
                    # a property of the type. First screen that reports one -
                    # they cannot disagree, and None means a module too old to
                    # say.
                    "module_version": next(
                        (s["module_version"] for s in screens if s.get("module_version")),
                        None,
                    ),
                    # Two independent reasons to reflash, and either is enough.
                    # A protocol mismatch is the device saying it cannot talk to
                    # this module; `behind` is it running an older commit than
                    # the tree. Neither is inferred from the other - a screen can
                    # be several commits old and still speak the protocol fine.
                    "needs_flash": any(
                        s.get("protocol_match") is False
                        or s.get("firmware_state") == displays_mod.FW_BEHIND
                        for s in screens
                    ),
                    # What the tree would build right now, for the panel to show
                    # beside what the screens report.
                    "source_version": tree.head,
                    "source_dirty": tree.dirty,
                }
            )
        return out

    # -- the uniform projection --------------------------------------------
    #
    # `types[]` and `displays[]` say the same things in different words, and the
    # panel needs a component per shape to read them. `targets[]` is those two
    # projected onto one shape so that one component renders both - and renders
    # a cartographer probe, or whatever comes next, without being taught to.
    #
    # It is a *projection*, not a second source of truth: everything here is
    # derived from the payloads the other two keys are built from, in the same
    # status() call. If a fact appears here that cannot be found there, that is a
    # bug in this function rather than a reason to add a key.

    #: Why a control is offered but cannot be used. Same `{code, message, data}`
    #: shape as `UpdaterError.to_dict()` - so a greyed button and a failed call
    #: are one object with one renderer, and the error codes stay one vocabulary.
    #:
    #: Deliberately absent: "something else is running". That is global and
    #: transient, the panel already has it from `job` and `locked_by`, and
    #: folding it in would make every target's meaning change the moment a build
    #: started somewhere else.
    BLOCKED_NO_ARTIFACT = "no_artifact"
    BLOCKED_NO_CONFIG = "no_config"
    #: Also no saved config - but this tree ships profiles, so the fix is picking
    #: one rather than answering a menu. Distinct from `no_config` because the
    #: two send a user to different places, and `no_config`'s message is the one
    #: a tree with no profiles must keep saying, unchanged.
    BLOCKED_NO_PROFILE = "no_profile"
    BLOCKED_NO_DEVICE = "no_device"
    #: No source tree to build in. Distinct from `no_config`, which is a saved
    #: answer file that a build would read: this is the tree itself missing, and
    #: the fix is a `source:` key or a `git clone` rather than a menuconfig run.
    BLOCKED_NO_SOURCE = "no_source"

    @staticmethod
    def _blocked(code: str, message: str, **data: Any) -> dict[str, Any]:
        return {"code": code, "message": message, "data": data}

    @staticmethod
    def _artifact_json(status: ArtifactStatus) -> dict[str, Any]:
        """Q1's verdict, as the wire carries it.

        `tone` and `label` ride along rather than being left to the reader.
        Four separate colour maps and four sets of wording grew up in the panel
        answering this from the raw reasons; the point of having one vocabulary
        is that one wording serves every front end.
        """
        return {
            "state": status.state,
            "tone": status.tone,
            "label": status.label,
            "reason": status.reason,
        }

    @staticmethod
    def _device_json(status: DeviceStatus) -> dict[str, Any]:
        """Q2's verdict. `needs_flash` stays tri-state, and never False on
        absent evidence."""
        return {
            "needs_flash": status.needs_flash,
            "tone": status.tone,
            "label": status.label,
            "reason": status.reason,
        }

    @staticmethod
    def _aggregate(devices: list[dict[str, Any]]) -> Optional[bool]:
        """True if any device wants firmware, False if all provably don't, else None.

        The tri-state matters: `any()` reads None as falsey, so a type whose
        boards are all offline would report "nothing to do" about a fleet nobody
        can see. That is the same class of quiet lie as a stale artifact
        reporting itself current.
        """
        verdicts = [d["needs_flash"] for d in devices]
        if True in verdicts:
            return True
        if verdicts and all(v is False for v in verdicts):
            return False
        return None

    def targets(
        self,
        reg: Registry,
        types: list[dict[str, Any]],
        displays: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """`types[]` and `displays[]` in one shape."""
        allowed = set(self.available_methods())
        # Read once for the whole projection. Every type asks the same two
        # questions of it - can this tree be configured, and what does it ship -
        # and a ten-type printer would otherwise re-read the config file ten
        # times to be told the same thing.
        families = firmware.load(self.paths)
        configurable = self.kconfig_available(families)
        return [
            self._mcu_target(reg, payload, allowed, configurable, families)
            for payload in types
        ] + [self._display_target(payload, allowed) for payload in displays]

    def _mcu_target(
        self,
        reg: Registry,
        payload: dict[str, Any],
        allowed: set[str],
        configurable: dict[str, bool],
        families: dict[str, firmware.FirmwareFamily],
    ) -> dict[str, Any]:
        name = payload["name"]
        fw = payload["firmware"]
        artifact = payload["artifacts"].get(fw) or {}
        status = ArtifactStatus(artifact.get("reason"))

        devices = []
        for serial in payload["serials"]:
            present = serial["state"] != STATE_OFFLINE
            devices.append(
                {
                    "id": serial["serial"],
                    # The klipper [mcu] section, which is what makes a 24-hex
                    # serial a board you recognise. Named as System Loads does.
                    "name": serial.get("mcu"),
                    "present": present,
                    "state": serial["state"],
                    "path": serial.get("path"),
                    "version": serial.get("running_version"),
                    **self._device_json(DeviceStatus(serial.get("reason"))),
                    "actions": self._device_actions(
                        allowed,
                        flash=(
                            "fw.flash",
                            {"name": name, "serial": serial["serial"]},
                        ),
                        present=present,
                        has_artifact=bool(artifact.get("has_bin")),
                        what=f"{fw} firmware",
                        label=serial["serial"],
                        extra=(
                            [
                                {
                                    "id": "untrack",
                                    "label": "Stop tracking",
                                    "method": "fw.serial.remove",
                                    "params": {"name": name, "serial": serial["serial"]},
                                    "blocked": None,
                                }
                            ]
                            if "fw.serial.remove" in allowed
                            else []
                        ),
                    ),
                }
            )

        # What this type could be seeded from - the vendor's variants and its own
        # captured answers. A directory listing per type, which is the same order
        # of cost as the file hashes `profile` above it already spends.
        seeds = profiles.available(self.paths, fw, families, mcu_type=name)
        profile = self._profile_json(name, fw, artifact, families)

        actions: list[dict[str, Any]] = []
        if "fw.build" in allowed:
            actions.append(
                {
                    "id": "build",
                    "label": "Build",
                    "method": "fw.build",
                    # The family this type actually runs. Hardcoding klipper here
                    # is what makes a cartographer type build the wrong tree.
                    "params": {"name": name, "fw": fw},
                    "blocked": (
                        None
                        if artifact.get("has_config")
                        # A tree that ships profiles has a better first step than
                        # menuconfig, and saying "run menuconfig" in front of one
                        # is what sends people into a tree of hundreds of settings
                        # to find the seven the vendor already wrote down.
                        else self._blocked(
                            self.BLOCKED_NO_PROFILE,
                            f"'{name}' has no saved {fw} configuration yet. "
                            f"Choose one of the {len(seeds)} profiles "
                            f"{fw} ships, or configure it by hand.",
                            name=name,
                            fw=fw,
                        )
                        if seeds
                        else self._blocked(
                            self.BLOCKED_NO_CONFIG,
                            f"'{name}' has no saved {fw} configuration yet. "
                            "Run menuconfig for it first.",
                            name=name,
                            fw=fw,
                        )
                    ),
                }
            )
        actions.extend(self._profile_actions(name, fw, allowed, artifact, profile, seeds))
        for family in reg.get(name).families():
            if "fw.kconfig.open" in allowed and configurable.get(family):
                actions.append(
                    {
                        "id": f"configure:{family}",
                        "label": f"Configure {family}",
                        "method": "fw.kconfig.open",
                        "params": {"name": name, "fw": family},
                        "blocked": None,
                    }
                )
        actions.extend(
            self._flash_actions(
                name=name,
                allowed=allowed,
                has_artifact=bool(artifact.get("has_bin")),
                flashable=[d for d in devices if d["present"]],
                what=f"{fw} firmware",
            )
        )

        return {
            "kind": "mcu",
            "name": name,
            "descriptor": payload["chipset"],
            "firmware": fw,
            "artifact": self._artifact_json(status),
            # The third verdict, which this projection used to drop on the floor:
            # `types[]` has carried it since profiles existed, and a panel reading
            # `targets[]` had no way to know a config had been customised or a
            # vendor had bumped theirs.
            "profile": profile,
            "needs_flash": self._aggregate(devices),
            "devices": devices,
            "actions": actions,
        }

    def _profile_json(
        self,
        name: str,
        fw: str,
        artifact: dict[str, Any],
        families: dict[str, firmware.FirmwareFamily],
    ) -> dict[str, Any]:
        """The profile verdict, plus what was changed when something was.

        `changes` is only computed for a type on its own answers, which is the
        only state where anyone asks. It costs two small file reads and no parse
        - both sides are answer lists - so it is affordable on a call every state
        event recomputes, but spending even that on the eight types tracking a
        vendor profile unchanged would be paying for an empty list.
        """
        verdict = dict(artifact.get("profile") or {})
        if verdict.get("custom") or verdict.get("reason") == profiles.CUSTOMISED:
            verdict["changes"] = profiles.overrides(self.paths, name, fw, families)
        return verdict

    def _profile_actions(
        self,
        name: str,
        fw: str,
        allowed: set[str],
        artifact: dict[str, Any],
        profile: dict[str, Any],
        seeds: list[profiles.Seed],
    ) -> list[dict[str, Any]]:
        """Choosing a profile, and getting back off your own onto the vendor's.

        The picker carries `choices` rather than the eight options themselves:
        the dialog fetches them when it opens, which is a click and can afford
        the Kconfig parse that labels them, while `fw.status` - which every state
        event rebuilds, for every client - cannot. Generic on purpose: the panel
        renders a radio group for any action carrying `choices`, and never learns
        what a profile is.
        """
        if not seeds or "fw.profile.apply" not in allowed:
            return []

        out: list[dict[str, Any]] = [
            {
                "id": "profile",
                "label": "Choose profile" if not artifact.get("has_config") else "Change profile",
                "method": "fw.profile.apply",
                "params": {"name": name, "fw": fw},
                "blocked": None,
                "choices": {
                    "method": "fw.profile.list",
                    "params": {"name": name, "fw": fw, "detail": True},
                    "param": "profile",
                },
            }
        ]

        # Going back is a named button rather than a `force` flag, which is the
        # whole point of a custom profile recording what it was forked from: the
        # answers being left behind have somewhere to be.
        if profile.get("custom"):
            back, force = profile.get("parent"), profile.get("reason") == profiles.CUSTOMISED
        elif profile.get("reason") == profiles.CUSTOMISED:
            back, force = profile.get("profile"), True
        else:
            back, force = None, False

        if back and any(seed.name == back for seed in seeds):
            out.append(
                {
                    "id": "profile:revert",
                    "label": f"Back to {back}",
                    "method": "fw.profile.apply",
                    "params": {"name": name, "fw": fw, "profile": back, "force": force},
                    "blocked": None,
                }
            )
        return out

    def _display_target(
        self, payload: dict[str, Any], allowed: set[str]
    ) -> dict[str, Any]:
        name = payload["name"]
        status = ArtifactStatus(payload["artifact_reason"])

        devices = []
        for screen in payload["screens"]:
            device = self._screen_device_status(screen)
            devices.append(
                {
                    "id": screen["configured_path"],
                    # "knomi_serial t0_knomi" - the same slot the MCU rows use
                    # for their [mcu] section, and the same kind of fact.
                    "name": screen["section"],
                    "present": screen["present"],
                    "state": self._screen_state(screen),
                    "path": screen.get("resolved_path"),
                    "version": screen.get("firmware_version"),
                    **self._device_json(device),
                    "actions": self._device_actions(
                        allowed,
                        flash=(
                            "fw.display.flash",
                            {"name": name, "port": screen["configured_path"]},
                        ),
                        present=screen["present"],
                        has_artifact=bool(payload["has_firmware"]),
                        what="display firmware",
                        label=screen["name"],
                    ),
                }
            )

        actions: list[dict[str, Any]] = []
        if "fw.display.build" in allowed:
            # PlatformIO carries its own configuration in platformio.ini, so
            # there is no menuconfig step to be missing - but there is still a
            # tree to have cloned, and that is the same kind of once-per-target
            # setup. A fleet build skips a display without one, so the button
            # has to say so rather than offering work that gets passed over.
            problem = payload.get("build_blocked")
            actions.append(
                {
                    "id": "build",
                    "label": "Build",
                    "method": "fw.display.build",
                    "params": {"name": name},
                    "blocked": (
                        None
                        if not problem
                        else self._blocked(
                            self.BLOCKED_NO_SOURCE, problem, name=name
                        )
                    ),
                }
            )
        actions.extend(
            self._flash_actions(
                name=name,
                allowed=allowed,
                has_artifact=bool(payload["has_firmware"]),
                flashable=[d for d in devices if d["present"]],
                what="display firmware",
                flash_method="fw.display.flash",
                update_method=None,
            )
        )

        return {
            "kind": "display",
            "name": name,
            "descriptor": payload["env"],
            # Displays are built by PlatformIO from their own tree rather than
            # from a `[firmware ...]` family, so there is no family to name. The
            # build action carries what a caller actually needs.
            "firmware": None,
            "artifact": self._artifact_json(status),
            # Always null: PlatformIO carries its configuration in
            # platformio.ini, so there are no answers to seed and nothing for a
            # profile to be. Present rather than absent because one shape that a
            # reader can trust beats one it has to test for.
            "profile": None,
            "needs_flash": self._aggregate(devices),
            "devices": devices,
            "actions": actions,
            # Facts only a screen has. Kept apart from the shared shape rather
            # than diluting it - this is the one place branching is honest,
            # because these are extra things to say, not another way of saying
            # the same thing.
            "extra": {
                "module_version": payload["module_version"],
                "source_version": payload["source_version"],
                "source_dirty": payload["source_dirty"],
                "klipper_section": payload["klipper_section"],
                "reachable": payload["reachable"],
            },
        }

    @staticmethod
    def _screen_device_status(screen: dict[str, Any]) -> DeviceStatus:
        """Does this screen want firmware, and why?

        A screen has two independent ways to want it: a protocol mismatch is the
        device saying it cannot talk to this module at all, and `source_changed`
        is it running an older commit. The version comparison has no word for
        the first, so it is applied on top rather than folded in.

        One function because there are two callers and they must not disagree -
        the panel row and the fleet-flash selection. A screen the panel paints
        as needing firmware and the batch passes over is the same class of
        silent wrong answer as everything else here.
        """
        if screen.get("protocol_match") is False:
            return DeviceStatus(PROTOCOL_MISMATCH)
        if not screen["present"]:
            return DeviceStatus(OFFLINE)
        return DeviceStatus(screen.get("reason"))

    @staticmethod
    def _screen_state(screen: dict[str, Any]) -> str:
        """The slot an MCU row fills with klipper/katapult/offline.

        Three states again, but the middle one is the point: a port that opens
        is not a screen that answers. `present` stays true with the far end
        unplugged, which is exactly the failure the klippy module swallows.
        """
        if not screen["present"]:
            return "missing"
        online = screen.get("device_online")
        if online is True:
            return "online"
        if online is False:
            return "silent"
        # A module too old for get_status reports null, which is "unknown" and
        # must not read as "silent" - that would invent a fault on a display
        # working perfectly.
        return "reachable"

    def _device_actions(
        self,
        allowed: set[str],
        *,
        flash: tuple[str, dict[str, Any]],
        present: bool,
        has_artifact: bool,
        what: str,
        label: str,
        extra: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """What can be done to one device.

        Per device rather than only per target, because the reasons differ per
        device: one board of a type can be offline while its neighbour is
        waiting in Katapult. Carrying it here is also what lets a reader render
        a board row and a screen row with the same code - the flash of a board
        and the flash of a screen are different RPCs, and this is the only place
        that difference needs to exist.
        """
        method, params = flash
        out: list[dict[str, Any]] = []
        if method in allowed:
            if not has_artifact:
                blocked = self._blocked(
                    self.BLOCKED_NO_ARTIFACT,
                    f"no {what} has been built yet.",
                )
            elif not present:
                blocked = self._blocked(
                    self.BLOCKED_NO_DEVICE,
                    f"'{label}' is not connected.",
                )
            else:
                blocked = None
            out.append(
                {
                    "id": "flash",
                    "label": "Flash",
                    "method": method,
                    "params": params,
                    "blocked": blocked,
                }
            )
        out.extend(extra or [])
        return out

    def _flash_actions(
        self,
        *,
        name: str,
        allowed: set[str],
        has_artifact: bool,
        flashable: list[dict[str, Any]],
        what: str,
        flash_method: str = "fw.flash_all",
        update_method: Optional[str] = "fw.update_all",
    ) -> list[dict[str, Any]]:
        """Flash, and build-then-flash, with the same reason for refusing both.

        One function because the two share every precondition. They differed
        only in which of two nearly identical tooltips the panel wrote.
        """
        if not has_artifact:
            blocked = self._blocked(
                self.BLOCKED_NO_ARTIFACT,
                f"no {what} has been built for '{name}' yet.",
                name=name,
            )
        elif not flashable:
            blocked = self._blocked(
                self.BLOCKED_NO_DEVICE,
                f"nothing is connected to flash for '{name}'.",
                name=name,
            )
        else:
            blocked = None

        out = []
        if flash_method in allowed:
            params: dict[str, Any] = {"name": name}
            if flash_method == "fw.flash_all":
                # Judgement, not physics: `stale` skips boards already running
                # this build. Offline boards are excluded under every scope.
                params["scope"] = "stale"
            out.append(
                {
                    "id": "flash",
                    "label": "Flash",
                    "method": flash_method,
                    "params": params,
                    "blocked": blocked,
                }
            )
        if update_method and update_method in allowed:
            out.append(
                {
                    "id": "update",
                    "label": "Build and flash",
                    "method": update_method,
                    "params": {"name": name, "scope": "stale"},
                    # A rebuild is part of the operation, so a missing artifact
                    # is not a reason to refuse it - only having nowhere to
                    # write is.
                    "blocked": (
                        None
                        if flashable
                        else self._blocked(
                            self.BLOCKED_NO_DEVICE,
                            f"nothing is connected to flash for '{name}'.",
                            name=name,
                        )
                    ),
                }
            )
        return out

    def lock_holder(self) -> Optional[dict[str, Any]]:
        from ..lock import ExclusiveLock

        return ExclusiveLock(self.paths).holder()

    def type_list(self, args: dict) -> dict[str, Any]:

        reg = self.registry()
        versions = self.mcu_info()
        return {"types": [self.type_status(reg, n, versions) for n in reg.names()]}

    def bus_scan(self, args: dict) -> dict[str, Any]:
        """Everything on the bus, plus the subset worth offering to track.

        `adoptable` is what a "track this" affordance should iterate. It is a
        separate key rather than a filter applied to `devices` so the panel can
        still *show* the other entries - a user hunting for a board that has not
        appeared is better served by seeing what did appear than by an empty list.
        """
        reg = self.registry()
        devices = self.bus(reg)
        if args.get("only_untracked"):
            devices = [d for d in devices if d["tracked_by"] is None]
        chipset = args.get("chipset")
        if chipset:
            devices = [d for d in devices if d["chipset"] == chipset]
        return {
            "devices": devices,
            "adoptable": [
                d for d in devices if d["is_mcu"] and d["tracked_by"] is None
            ],
        }

    def artifacts(self, args: dict) -> dict[str, Any]:
        name = args.get("name")
        if not name:
            raise RpcError("'name' is required", ERR_INVALID_PARAMS)
        reg = self.registry()
        mcu = reg.get(str(name))  # raises UnknownTypeError for an unknown type
        return {fw: self.artifact(str(name), fw) for fw in mcu.fw_order()}

    def settings_get(self, args: dict) -> dict[str, Any]:
        return {"settings": dataclasses.asdict(self.settings())}

    #: Settings the panel may change. Everything here is a *behaviour* preference.
    #:
    #: `service` and `service_backend` are deliberately absent. They describe how
    #: this host is wired, not what the user wants, and getting them wrong breaks
    #: the agent's ability to stop Klipper - `service_backend: null` in particular
    #: would let a real flash proceed *without* stopping it, which fails at best
    #: and corrupts a board at worst. Nothing about a browser form makes that a
    #: sensible thing to offer; editing the cfg is the right amount of friction.
    SETTABLE = (
        "make_jobs",
        "clean_before_build",
        "reseed_on_build",
        "dry_run",
        "enable_flashing",
        "allow_flash_while_printing",
        "log_ring_size",
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
                f"{', '.join(self.SETTABLE)}. 'service' and 'service_backend' "
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
            return firmware.DEFAULT_APPLICATION
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

        with Registry.mutate(self.paths, f"add type {name}") as reg:
            reg.add_type(
                name,
                chipset,
                klipper_args=str(args.get("klipper_extra_args") or "").strip(),
                katapult_args=str(args.get("katapult_extra_args") or "").strip(),
                katapult_installed=True if installed is None else bool(installed),
                application=application,
            )

        self._changed()
        return {"name": name, "chipset": chipset, "firmware": application}

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
        with Registry.mutate(self.paths, f"update type {name}") as reg:
            mcu = reg.get(name)

            if "chipset" in args:
                chipset = self._require_str(args, "chipset")
                if chipset != mcu.chipset:
                    # Staleness compares the source commit and a hash of the
                    # .config, neither of which changes when the chipset does - so
                    # a binary built for the old chip would keep reporting itself
                    # as fresh. Say so rather than let it be flashed.
                    if self.artifact(name, "klipper").get("has_bin"):
                        warnings.append(
                            f"the built firmware for '{name}' was compiled for "
                            f"{mcu.chipset}. Rebuild before flashing - staleness "
                            f"cannot detect a chipset change on its own."
                        )
                    mcu.chipset = chipset

            if "firmware" in args:
                application = self._require_family(args)
                if application != mcu.firmware:
                    # Same reasoning as a chipset change, and stronger: the
                    # artifact was built from a different source tree entirely,
                    # and nothing in the provenance record would notice.
                    if self.artifact(name, mcu.firmware).get("has_bin"):
                        warnings.append(
                            f"the built firmware for '{name}' came from "
                            f"{mcu.firmware}. Rebuild before flashing - "
                            f"staleness compares a tree against itself and "
                            f"cannot detect the tree being swapped."
                        )
                    mcu.firmware = application

            for fw in mcu.fw_order():
                key = f"{fw}_extra_args"
                if key in args:
                    mcu.fw(fw).extra_args = str(args.get(key) or "").strip()

            if "katapult_installed" in args:
                installed = bool(args.get("katapult_installed"))
                # Only stored when false; absent means true, which keeps the file
                # free of restated defaults.
                mcu.fw("katapult").installed = None if installed else False

            result: dict[str, Any] = {
                "name": name,
                "chipset": mcu.chipset,
                "firmware": mcu.firmware,
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

    # -- jobs --------------------------------------------------------------

    def _require_runner(self):
        if self.runner is None:
            raise RpcError(
                "this agent is running read-only; no job runner is available",
                ERR_METHOD_NOT_FOUND,
            )
        return self.runner

    def build(self, args: dict) -> dict[str, Any]:
        """Start a build. Returns a job id immediately - never blocks."""
        runner = self._require_runner()
        name = args.get("name")
        fw = args.get("fw")
        known = self._fw_names()
        if not name or fw not in known:
            raise RpcError(
                f"'name' is required and 'fw' must be one of {list(known)}",
                ERR_INVALID_PARAMS,
            )
        name, fw = str(name), str(fw)

        reg = self.registry()
        reg.get(name)  # fail fast on an unknown type, before creating a job
        if not os.path.exists(self.paths.config_file(name, fw)):
            # menuconfig is ncurses and cannot run here. Say so precisely rather
            # than starting a job that dies immediately.
            raise RpcError(
                f"{name} has no saved {fw} config. Run "
                f"'updatefw menuconfig -t {name} -f {fw}' over SSH once first.",
                data={
                    "code": "no_saved_config",
                    "message": "menuconfig has never been run for this type",
                    "data": {"type": name, "fw": fw},
                },
            )

        jobs = args.get("jobs")
        clean = args.get("clean")
        # Tri-state. Absent means "whatever `reseed_on_build` says", which is how
        # this call, the CLI and a fleet build end up doing the same thing; the
        # dialog sends an explicit answer when it has asked the user for one.
        reseed = args.get("reseed")

        def run(ctx) -> dict[str, Any]:
            from ..build import build as do_build

            ctx.step(f"Building {fw} for {name}", 0, 1)
            result = do_build(
                self.paths,
                self.registry(),
                self.settings(),
                name,
                fw,
                reporter=ctx.reporter,
                cancel=ctx.cancel,
                jobs=int(jobs) if jobs is not None else None,
                clean=bool(clean) if clean is not None else None,
                reseed=bool(reseed) if reseed is not None else None,
            )
            ctx.step(f"Built {fw} for {name}", 1, 1)
            return {
                "type": name,
                "fw": fw,
                "bin_path": result.bin_path,
                "uf2_path": result.uf2_path,
                "duration": round(result.duration, 2),
                "fw_sha": result.fw_sha,
                "config_rewritten": result.config_rewritten,
                # Which profile was taken before building, if one was. Null on
                # every build that did not reseed, including one that was willing
                # to and found nothing to take.
                "reseeded": result.reseeded,
            }

        job = runner.submit("build", {"name": name, "fw": fw}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def flash(self, args: dict) -> dict[str, Any]:
        """Flash one board. Returns a job id immediately.

        Every refusal happens *here*, synchronously, before a job exists - so the
        caller gets a real explanation instead of a job that fails a second later.
        In order: capability gate, argument validation, type/serial pairing,
        artifact present, board actually attached, and finally the print gate.
        """
        runner = self._require_runner()
        settings = self.settings()

        # Deliberately off by default: updating the agent must never silently
        # grant a browser the ability to reflash the printer.
        if not settings.enable_flashing:
            raise RpcError(
                "flashing from the web UI is disabled. Set 'enable_flashing = true' in "
                f"{self.paths.settings_file} and restart the agent to allow it.",
                data={
                    "code": "flashing_disabled",
                    "message": "enable_flashing is false",
                    "data": {"settings_file": self.paths.settings_file},
                },
            )

        serial = args.get("serial")
        if not serial:
            raise RpcError("'serial' is required", ERR_INVALID_PARAMS)
        serial = str(serial)
        name = args.get("name")
        force = bool(args.get("force"))

        reg = self.registry()
        # resolve_serial raises unknown_serial / ambiguous_serial /
        # serial_tracked_elsewhere, all of which the panel switches on by code.
        mcu_type = reg.resolve_serial(serial, str(name) if name else None)
        mcu = reg.get(mcu_type)

        fw_bin = self.paths.bin_file(mcu_type, "klipper")
        if not os.path.exists(fw_bin):
            raise RpcError(
                f"no built firmware for {mcu_type} at {fw_bin}. Build it first.",
                data={
                    "code": "no_artifact",
                    "message": "firmware has not been built",
                    "data": {"type": mcu_type, "path": fw_bin},
                },
            )

        # Fail now if the board isn't on the bus, rather than after stopping
        # klipper. Katapult means it's already in the bootloader.
        from ..devices import find_device

        if find_device(self.paths, mcu.chipset, serial) is None:
            raise RpcError(
                f"{serial} is not attached (looked for chipset {mcu.chipset}). "
                f"Is it plugged in and powered?",
                data={
                    "code": "device_not_found",
                    "message": "board is not on the bus",
                    "data": {"serial": serial, "chipset": mcu.chipset},
                },
            )

        # Last gate. Covers a running print *and* any other klipper activity -
        # homing, QGL, a macro - because stopping klipper mid-motion is just as
        # destructive as interrupting a print, and print_stats alone misses it.
        from ..service import assert_printer_idle

        assert_printer_idle(
            settings, activity=self._printer_activity, force=force, reporter=self._log_reporter
        )

        def run(ctx) -> dict[str, Any]:
            from ..devices import KLIPPER_FW_NAME, wait_for_device
            from ..errors import BootloaderTimeoutError
            from ..flash import flash_katapult
            from ..service import klipper_stopped, make_controller

            settings_now = self.settings()
            svc = make_controller(settings_now, call=self._call_for_service)
            ctx.step(f"Stopping {svc.name}", 0, 4)
            with klipper_stopped(self.paths, svc, f"flash {serial}", reporter=ctx.reporter):
                ctx.step(f"Flashing {serial}", 1, 4)
                # No cancel is threaded into the write on purpose - interrupting
                # flashtool leaves half an image on the board.
                flash_katapult(
                    self.paths,
                    settings_now,
                    mcu_type,
                    mcu.chipset,
                    serial,
                    fw_bin=fw_bin,
                    fw=mcu.firmware,
                    reporter=ctx.reporter,
                )

                # The board reboots into the new firmware and re-enumerates over
                # USB, which takes a couple of seconds. Starting klipper before
                # the device node exists means klipper cannot find its MCU and
                # comes up in an error state.
                ctx.step(f"Waiting for {serial} to come back", 2, 4)
                if not settings_now.dry_run:
                    try:
                        wait_for_device(
                            self.paths,
                            mcu.chipset,
                            serial,
                            KLIPPER_FW_NAME,
                            timeout=REENUMERATE_TIMEOUT,
                            settle=1.0,
                        )
                        ctx.reporter("info", f"{serial} is back as a Klipper device.")
                    except BootloaderTimeoutError as exc:
                        # Not fatal here: klipper still has to be started, and it
                        # may yet find the board. The readiness check below is the
                        # real verdict.
                        ctx.reporter("warn", str(exc))

                ctx.step(f"Restarting {svc.name}", 3, 4)

            # klipper_stopped has started the service by now. Being *active* is
            # not the same as being ready, so confirm - and firmware-restart if
            # the MCU came back shut down.
            klippy_state = self._await_klippy_ready(ctx.reporter)
            ctx.step("Done", 4, 4)
            return {
                "type": mcu_type,
                "serial": serial,
                "fw_bin": fw_bin,
                "klippy_state": klippy_state,
            }

        job = runner.submit("flash", {"name": mcu_type, "serial": serial}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _log_reporter(self, stream: str, line: str) -> None:
        """Send a core Reporter's output to the agent log.

        Used for checks that run outside a job, where there is no job log to
        collect into but the message still matters - e.g. "could not determine
        print state, continuing".
        """
        if self._log is None:
            return
        if stream in ("warn", "error"):
            self._log.warning(line)
        else:
            self._log.debug(line)

    def _printer_activity(self) -> dict[str, Optional[str]]:
        """Both states that mean "don't touch the printer right now".

        print_stats.state only knows about virtual_sdcard print jobs, so it stays
        "standby" during a manual home or QGL. idle_timeout.state is the one that
        reads "Printing" whenever klipper is executing anything.
        """
        res = self._probe(
            "printer.objects.query",
            {"objects": {"print_stats": ["state"], "idle_timeout": ["state"]}},
        )
        status = (res or {}).get("status") or {}
        return {
            "print_state": (status.get("print_stats") or {}).get("state"),
            "idle_state": (status.get("idle_timeout") or {}).get("state"),
        }

    def _print_state(self) -> Optional[str]:
        return self._printer_activity().get("print_state")

    def _klippy_state(self) -> tuple[Optional[str], str]:
        info = self._probe("printer.info")
        if not isinstance(info, dict):
            return None, ""
        return info.get("state"), str(info.get("state_message") or "")

    #: How long to wait for klippy to report "ready" after the service starts,
    #: and again after a firmware restart. Class attributes so tests can shrink
    #: them without patching a call site.
    KLIPPY_READY_TIMEOUT = 45.0
    KLIPPY_RESTART_TIMEOUT = 60.0
    KLIPPY_POLL_INTERVAL = 1.0

    def _await_klippy_ready(
        self,
        reporter: Any,
        *,
        timeout: Optional[float] = None,
        after_restart: Optional[float] = None,
    ) -> Optional[str]:
        """Wait for klipper to actually be usable, restarting firmware if needed.

        `systemctl is-active klipper` going green is **not** the same as klipper
        being ready. A board that was mid-motion when we stopped the service comes
        back with its MCU in a shutdown state, so klippy reaches "error" or
        "shutdown" and the printer needs a FIRMWARE_RESTART before it will move.
        Doing that automatically is exactly what a human does by hand.
        """
        if self._call is None:
            return None
        timeout = self.KLIPPY_READY_TIMEOUT if timeout is None else timeout
        after_restart = self.KLIPPY_RESTART_TIMEOUT if after_restart is None else after_restart

        state = self._poll_klippy(timeout)
        if state == "ready":
            reporter("info", "Klipper is ready.")
            return state

        message = self._klippy_state()[1]
        reporter(
            "warn",
            f"Klipper came up in state '{state}'"
            + (f": {message}" if message else "")
            + " - issuing a firmware restart (the MCU was reset by the flash).",
        )
        try:
            self._call("printer.firmware_restart", None, 30.0)
        except Exception as exc:  # noqa: BLE001
            reporter("error", f"firmware restart failed: {exc}")
            return state

        state = self._poll_klippy(after_restart)
        if state == "ready":
            reporter("info", "Klipper is ready after the firmware restart.")
        else:
            # Deliberately not a job failure: the write itself succeeded, and
            # Mainsail's own Klippy panel will be showing this loudly. But say
            # exactly what to do next.
            reporter(
                "error",
                f"Klipper is still in state '{state}' after a firmware restart. "
                f"Check the Klippy panel, then run FIRMWARE_RESTART from the console.",
            )
        return state

    def _poll_klippy(self, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + timeout
        state = None
        while time.monotonic() < deadline:
            state, _ = self._klippy_state()
            if state == "ready":
                return state
            # "startup" just means it hasn't finished connecting yet.
            if state in ("error", "shutdown"):
                return state
            time.sleep(self.KLIPPY_POLL_INTERVAL)
        return state

    def _call_for_service(self, method: str, params: Any) -> Any:
        """Adapter: ServiceController wants (method, params); _call takes a timeout.

        Service calls get a longer budget than status probes - stopping klipper
        genuinely takes a moment, and timing out here would look like a failure
        and abort a flash that was about to be fine.
        """
        if self._call is None:
            raise RpcError("no moonraker connection")
        return self._call(method, params, 30.0)

    def job_get(self, args: dict) -> dict[str, Any]:
        """A job plus a slice of its log.

        `log_from` is how the panel recovers from a gap: batched log events carry
        the sequence of their first line, and any mismatch against what the client
        expected means it asks for the range it missed.
        """
        runner = self._require_runner()
        job_id = args.get("job_id")
        job = runner.get(str(job_id)) if job_id else runner.current()
        if job is None:
            raise RpcError(
                f"no such job: {job_id}" if job_id else "no job is running",
                data={"code": "unknown_job", "message": "job not found", "data": {}},
            )

        raw_from = args.get("log_from")
        try:
            log_from = max(int(raw_from), 0) if raw_from is not None else 0
        except (TypeError, ValueError):
            raise RpcError("'log_from' must be an integer", ERR_INVALID_PARAMS) from None

        lines, served_from, log_next = job.log_since(log_from)
        return {
            "job": job.to_dict(),
            "log": [line.to_dict() for line in lines],
            # May exceed log_from when the ring buffer already evicted the
            # requested range; the panel shows a "lines omitted" marker.
            "log_from": served_from,
            "log_next": log_next,
            "log_dropped": job.dropped,
        }

    def job_cancel(self, args: dict) -> dict[str, Any]:
        runner = self._require_runner()
        job_id = args.get("job_id")
        if not job_id:
            current = runner.current()
            if current is None:
                raise RpcError("no job is running", ERR_INVALID_PARAMS)
            job_id = current.id
        return runner.cancel(str(job_id))

    #: method name -> bound attribute name. Registered with Moonraker verbatim,
    #: and dotted names are fine (Moonraker's own example is
    #: "moontest.hello_world").
    METHODS: dict[str, str] = {
        "fw.ping": "ping",
        "fw.status": "status",
        "fw.type.list": "type_list",
        "fw.bus.scan": "bus_scan",
        "fw.dfu.scan": "dfu_scan",
        "fw.display.list": "display_list",
        "fw.display.build": "display_build",
        "fw.display.flash": "display_flash",
        "fw.artifacts": "artifacts",
        "fw.settings.get": "settings_get",
        "fw.settings.set": "settings_set",
        "fw.build": "build",
        "fw.flash": "flash",
        "fw.job.get": "job_get",
        "fw.job.cancel": "job_cancel",
        "fw.build_all": "build_all",
        "fw.flash_all": "flash_all",
        "fw.update_all": "update_all",
        "fw.add_mcu.start": "add_mcu_start",
        "fw.serial.add": "serial_add",
        "fw.serial.remove": "serial_remove",
        "fw.type.add": "type_add",
        "fw.type.update": "type_update",
        "fw.type.remove": "type_remove",
        "fw.kconfig.open": "kconfig_open",
        "fw.kconfig.menu": "kconfig_menu",
        "fw.kconfig.enter": "kconfig_enter",
        "fw.kconfig.up": "kconfig_up",
        "fw.kconfig.set": "kconfig_set",
        "fw.kconfig.help": "kconfig_help",
        "fw.kconfig.search": "kconfig_search",
        "fw.kconfig.reset": "kconfig_reset",
        "fw.kconfig.save": "kconfig_save",
        "fw.kconfig.close": "kconfig_close",
        "fw.profile.list": "profile_list",
        "fw.profile.apply": "profile_apply",
        "fw.profile.forget": "profile_forget",
    }

    #: Registered with Moonraker only when a runner is present, so a read-only
    #: deployment doesn't advertise controls it cannot honour.
    JOB_METHODS = (
        "fw.build",
        "fw.flash",
        "fw.job.get",
        "fw.job.cancel",
        "fw.build_all",
        "fw.flash_all",
        "fw.update_all",
        "fw.add_mcu.start",
        "fw.display.build",
        "fw.display.flash",
        # Seeding is a job for its runtime, not its danger - it writes a
        # .config and touches no hardware - but a job is a job, and a read-only
        # agent has no runner to submit one to.
        "fw.profile.apply",
    )

    #: Advertised only when enable_flashing is on. The panel hides its flash
    #: buttons accordingly, rather than offering something that gets refused.
    #: add_mcu.start writes a bootloader to a board, so it belongs here too - even
    #: though the board is not yet one of ours.
    FLASH_METHODS = (
        "fw.flash",
        "fw.flash_all",
        "fw.update_all",
        "fw.add_mcu.start",
        "fw.display.flash",
    )

    def available_methods(self) -> dict[str, str]:
        out = dict(self.METHODS)
        if self.runner is None:
            for name in self.JOB_METHODS:
                out.pop(name, None)
            return out
        if not self.settings().enable_flashing:
            for name in self.FLASH_METHODS:
                out.pop(name, None)
        return out




    # -- DFU: what is waiting to be adopted ---------------------------------

    #: Why a DFU flash cannot start right now. Stable codes; the panel switches on
    #: them, and each maps to a different physical thing for the user to do.
    DFU_NO_TOOL = "no_tool"
    DFU_PERMISSION_DENIED = "permission_denied"
    DFU_NONE = "none"
    DFU_AMBIGUOUS = "ambiguous"

    #: How long a bootloader-install pairing stays actionable. A class attribute
    #: so tests can shrink it without patching a call site, matching
    #: ADD_MCU_REENUMERATE_TIMEOUT and the klippy timeouts.
    PAIRING_TTL = _PAIRING_TTL

    # -- ESP32 displays -----------------------------------------------------

    def display_list(self, args: dict) -> dict[str, Any]:
        """The displays Klipper is configured for, and whether they are there.

        **The device list comes from Klipper, not from our registry.** A
        `[knomi_serial T0_knomi]` section names its own path one of two ways:
        `serial:` writes it in printer.cfg directly, and `device_id:` names the
        display by the id burned into its chip and leaves discovery to find the
        path - which the module then reports back in its own `get_status()`.
        Either way, keeping a second copy of the path here would only create
        something to disagree with. This reads the same `configfile.settings`
        payload `mcu_info` already fetches for the version join.

        A `device_id:` section belongs in the list even before discovery finds
        it - `present: false`, every live field `None` - because a display that
        needs flashing is precisely the one this must not be blind to. See
        knomi_serial's docs/protocol.md, "The device map".

        No identity beyond the port, deliberately: every display runs the same
        image, so which physical unit sits on which port does not change what
        gets written to it. The port is the whole story for flashing.

        `present` is the field that matters, because the klippy module hides this
        case. It catches a missing symlink or a device that never enumerated -
        which otherwise shows up as a display that is simply blank, with Klipper
        reporting no error at all.
        """
        # Every configured type's section prefix, not just Knomi's - a second
        # display with its own klippy module declares a different one. Falls back
        # to knomi_serial so this still answers before any [display] section
        # exists, which is how it gets used while setting one up.
        prefixes = {d.klipper_section for d in self.display_types().values()}
        prefixes.discard("")
        if not prefixes:
            prefixes = {"knomi_serial"}

        # The printer objects, in their real capitalisation. These *are* the
        # section list: a klippy extra whose section is in printer.cfg has an
        # object, because Klipper refuses to start when loading one raises. So
        # there is no state where configfile.settings knows about a display that
        # these do not - and asking for it fetched the whole parsed printer.cfg
        # a second time per poll, on top of the copy the MCU version join takes.
        objects: list[str] = []
        for prefix in sorted(prefixes):
            objects.extend(self._object_names_for(prefix))

        if not objects:
            # No sections. Still have to say whether we could *ask*: "no displays
            # configured" and "we could not reach Klipper" must not look alike,
            # and with nothing to query there is no answer to infer it from.
            reachable = bool(self._probe("printer.info"))
            return {
                "displays": [],
                "reachable": reachable,
                "watcher": None if reachable else self._watcher_map(),
            }

        query: dict[str, Any] = {}
        for name in objects:
            # None means every field. The module decides what it can report, and
            # a version of it older than get_status simply answers nothing -
            # which is why none of the live fields below are required.
            query[name] = None

        res = self._probe("printer.objects.query", {"objects": query})
        status = (res or {}).get("status")
        if not isinstance(status, dict):
            # Klipper cannot answer, which is exactly when the watcher's map is
            # the source - and exactly when flashing needs one, because esptool
            # wants the port to itself and stopping Klipper is what removed the
            # first source.
            return {"displays": [], "reachable": False, "watcher": self._watcher_map()}

        # Keyed on the lowered name so a section can be found however it was
        # capitalised. See _object_names_for.
        live_by_section = {name.lower(): (status.get(name) or {}) for name in objects}
        truecase_by_section = {name.lower(): name for name in objects}

        displays = []
        for section in sorted(live_by_section):
            values = live_by_section[section]

            # The module refuses both keys and requires one, so which of them
            # loaded says how this section is addressed - and the object reports
            # the configured id, not a discovered one.
            configured_device_id = (values or {}).get("device_id")
            addressed_by = "device_id" if configured_device_id else "serial"

            # The object's own capitalisation, which is what printer.cfg says
            # and what the user typed.
            true_section = truecase_by_section[section]
            live = values or {}

            # One field for both cases, because the module already merged them:
            # `port` is the configured `serial:` where there is one, and the
            # path discovery found otherwise. A device_id: section has no path
            # in its config at all, so until discovery finds it this is None -
            # and that section still belongs in the list rather than vanishing,
            # because a display that cannot be found is the one to say so about.
            configured = live.get("port")

            # A symlink is the point - resolve it, because the whole scheme is
            # "a stable name udev keeps pointed at the right tty". A discovered
            # device_id: path is already a real tty, not a symlink, so this is a
            # no-op for it and existence is the only thing being checked.
            resolved = None
            if configured:
                try:
                    if os.path.exists(configured):
                        resolved = os.path.realpath(configured)
                except OSError:
                    resolved = None

            displays.append(
                {
                    "name": true_section.split(" ", 1)[1],
                    "section": true_section,
                    # What printer.cfg names. None for a `serial:` section,
                    # which addresses a socket rather than a display.
                    "device_id": configured_device_id or live.get("device_id"),
                    # What the screen itself says it is: six hex characters from
                    # the low three bytes of its eFuse MAC. Burned in, so it
                    # survives a reflash, an erase_flash and a move to another
                    # socket - the only stable name a display has, because the
                    # CH340K in front of it reports no USB serial at all.
                    #
                    # Present for *every* reachable screen, including the
                    # `serial:` ones whose config carries no identity, which is
                    # the gap this fills. Compared case-insensitively wherever
                    # it is used: it is emitted lowercase at both ends, but the
                    # vendor's own docs say not to depend on that.
                    "reported_id": (live.get("reported_id") or "").lower() or None,
                    "addressed_by": addressed_by,
                    "configured_path": configured,
                    "resolved_path": resolved,
                    # The port exists. Necessary but nowhere near sufficient:
                    # the far end can be unplugged or wedged and this stays true.
                    "present": resolved is not None,
                    # --- live, from the module's get_status ---
                    #
                    # All None against a module too old to report them, so every
                    # consumer has to treat absence as "unknown" rather than
                    # "false". `connected` is the host having the port open;
                    # `device_online` is the screen actually answering.
                    "connected": live.get("connected"),
                    "device_online": live.get("device_online"),
                    "firmware_version": live.get("firmware_version"),
                    "module_version": live.get("module_version"),
                    # False means the screen speaks a different wire protocol
                    # than the module expects - the one authoritative "this needs
                    # reflashing" a display can produce, because the device
                    # itself declares it.
                    "protocol_match": live.get("protocol_match"),
                    # The two halves behind that verdict, so a mismatch can say
                    # which way round it is rather than only that it exists.
                    "protocol_version": live.get("protocol_version"),
                    "device_protocol_version": live.get("device_protocol_version"),
                    # Whether the config we pushed is the config it is running.
                    # Separates "I sent it" from "it took" - a screen can be
                    # perfectly current on firmware and still be showing the
                    # pages from before your last edit.
                    "config_applied": live.get("config_applied"),
                    "config_crc": live.get("config_crc"),
                    "device_config_crc": live.get("device_config_crc"),
                    # How many pages the screen actually built, which is the
                    # configured list minus any that would have been empty.
                    # Without it a `pages:` edit can only be checked by picking
                    # the display up.
                    "page_count": live.get("page_count"),
                    # What the host believes about the tool this screen belongs
                    # to. Not device state - the module fills these from the
                    # cluster, so they answer even while the screen is silent.
                    "tool": live.get("tool"),
                    "used": live.get("used"),
                    "filament_color": live.get("filament_color"),
                    "filament_type": live.get("filament_type"),
                    "build_variant": live.get("build_variant"),
                    "sleep_state": live.get("sleep_state"),
                    "screen": live.get("screen"),
                    "page": live.get("page"),
                    "free_heap": live.get("free_heap"),
                    "min_free_heap": live.get("min_free_heap"),
                    "device_uptime": live.get("device_uptime"),
                    "report_age": live.get("report_age"),
                }
            )

        # Not consulted while Klipper is up. Deciding whether the map is stale
        # means asking systemd whether the watcher is running, and that is a
        # fork per call on a method that rides along in every fw.status poll -
        # paid for an answer nobody needs while the authoritative source is
        # answering.
        return {"displays": displays, "reachable": True, "watcher": None}

    def _watcher_map(self) -> dict[str, Any]:
        """Each display family's watcher: is it running, and what has it found?

        Keyed by display type, because the watcher is a property of the family
        rather than of the host - a second display family brings its own.

        `active` is not decoration. The map carries no timestamps by design, so
        an entry means "identified during the watcher's current run, port still
        there" - a statement that is only true while it is running. A stopped
        watcher leaves a file that still parses and may name ports that have
        since moved, and nothing in it says so.
        """
        from .. import displays as displays_mod
        from ..service import make_controller

        settings = self.settings()
        out: dict[str, Any] = {}
        for name, display in self.display_types().items():
            svc = (
                make_controller(settings, call=self._call_for_service, name=display.service)
                if display.service
                else None
            )
            devices = displays_mod.read_device_map(self.paths, display)
            out[name] = {
                "service": display.service or None,
                "active": svc.is_active() if svc is not None else None,
                # When the watcher last wrote. Weak evidence, and only in one
                # direction: an old file is suspicious, but a fresh one does not
                # mean the watcher is still running - it could have stopped a
                # second after writing - and an old one does not mean the map is
                # wrong, because nothing changing means nothing to write. Shown
                # so a human can judge; never branched on.
                "updated": _mtime(displays_mod.device_map_path(self.paths, display)),
                "devices": [d.to_json() for d in devices.values()],
            }
        return out

    def display_types(self) -> dict:
        """Configured `[display <env>]` sections, with the shared source default."""
        from .. import displays as displays_mod

        # Attribute access, not getattr-with-a-default: `display_source` was
        # documented in the README before it existed on Settings, and the
        # forgiving lookup meant every display silently came back with no source
        # tree instead of anything saying so.
        return displays_mod.load(self.paths, default_source=self.settings().display_source)

    def display_build(self, args: dict) -> dict[str, Any]:
        """Compile one display env with PlatformIO. Touches no display."""
        runner = self._require_runner()
        name = self._require_str(args, "name")
        types = self.display_types()
        if name not in types:
            raise RpcError(
                f"no display type '{name}' is configured.",
                data={
                    "code": "unknown_type",
                    "message": "no such display type",
                    "data": {"name": name, "known": sorted(types)},
                },
            )
        display = types[name]

        def run(ctx) -> dict[str, Any]:
            from .. import displays as displays_mod

            ctx.step(f"Building {display.env}", 0, 1)
            path = displays_mod.build(
                self.paths, self.settings(), display, reporter=ctx.reporter, cancel=ctx.cancel
            )
            ctx.step(f"Built {display.env}", 1, 1)
            return {"name": name, "env": display.env, "firmware": path}

        job = runner.submit("display_build", {"name": name}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def display_flash(self, args: dict) -> dict[str, Any]:
        """Write a display env to its configured screens.

        **The device list is read before Klipper is stopped, not after.** It comes
        from the klippy module's own printer objects, which only a *running*
        Klipper can answer - so stopping first would leave nothing to flash. Every
        other flow in this file can query mid-job; this one cannot.

        Klipper is stopped for the batch because the klippy module holds the port
        open, and esptool cannot have it while it does.
        """
        runner = self._require_runner()
        settings = self.settings()
        if not settings.enable_flashing:
            raise RpcError(
                "flashing from the web UI is disabled. Set 'enable_flashing = true' in "
                f"{self.paths.settings_file} to allow it.",
                data={
                    "code": "flashing_disabled",
                    "message": "enable_flashing is false",
                    "data": {"settings_file": self.paths.settings_file},
                },
            )

        name = self._require_str(args, "name")
        types = self.display_types()
        if name not in types:
            raise RpcError(
                f"no display type '{name}' is configured.",
                data={"code": "unknown_type", "message": "no such display type",
                      "data": {"name": name, "known": sorted(types)}},
            )
        display = types[name]

        # Read the screens NOW, while Klipper can still answer.
        listed = self.display_list({})
        wanted = args.get("port")
        targets = [
            d
            for d in listed["displays"]
            if d["present"] and (wanted is None or d["configured_path"] == str(wanted))
        ]
        if not targets:
            raise RpcError(
                "no display is reachable to flash. Check that the configured ports "
                "exist - fw.display.list shows which are missing.",
                data={
                    "code": "nothing_to_do",
                    "message": "no reachable displays",
                    "data": {"displays": listed["displays"], "reachable": listed["reachable"]},
                },
            )

        from ..service import assert_printer_idle

        assert_printer_idle(
            settings,
            activity=self._printer_activity,
            force=bool(args.get("force")),
            reporter=self._log_reporter,
        )

        # Built from the list read *before* the stop, which is the only list a
        # running Klipper can produce. Everything after this - the stop, the
        # watcher pause, the discovery, the writes - is the same machinery a
        # fleet flash uses, because there was never anything display-shaped
        # about it beyond the two steps the esptool flasher now owns.
        screens = [flashers.esptool.target_for(display, s) for s in targets]

        def run(ctx) -> dict[str, Any]:
            result = self._do_flash_all(ctx, screens)
            # Projected back onto this method's own documented shape rather
            # than leaking the uniform one. The batch says `type`/`id`; a
            # display caller has always been told `name`/`port`, and `id` for a
            # screen *is* its configured port.
            named = {t.id: t.detail["screen"]["name"] for t in screens}
            return {
                "env": display.env,
                "flashed": result["flashed"],
                "failures": [
                    {
                        "name": named.get(f["id"], f["id"]),
                        "port": f["id"],
                        "error": f["error"],
                    }
                    for f in result["failures"]
                ],
            }

        job = runner.submit("display_flash", {"name": name, "count": len(targets)}, run)
        return {"job_id": job.id, "job": job.to_dict(), "displays": targets}

    def adopt_paired(self) -> list[dict[str, str]]:
        """Track boards that arrived late from a bootloader install we did.

        The completion of an operation the user already asked for, not a new
        decision: they chose the type and pressed the button, the write happened,
        and this is the board turning up afterwards. Doing nothing would mean the
        stated intent is lost to a 15-second timeout.

        Every condition below exists to keep it from ever being a *surprise*:

        * only Katapult devices that are **untracked** - anything already in the
          registry is left exactly as it is;
        * only an **unambiguous** match, for the same reason `_identify_dfu`
          refuses to name a colliding board: the DFU serial is derived by a sum
          and two boards could in principle share one;
        * only a pairing **within its TTL**, so a board found in a drawer next
          month is the stranger it has become;
        * only if the type still **exists**, since it can have been removed;
        * and the pairing is **consumed**, so it can never act twice.

        Returns what it adopted, for the log - a registry edit nobody can see
        happening is the thing to avoid.
        """
        from ..devices import KATAPULT_FW_NAME, dfu_serial_for, find_untracked
        from ..pairings import Pairings

        pairings = Pairings(self.paths, ttl=self.PAIRING_TTL)
        if not pairings.all():
            return []

        reg = self.registry()
        untracked = find_untracked(self.paths, reg.all_serials(), fw=KATAPULT_FW_NAME)
        if not untracked:
            pairings.prune()
            return []

        # Which known DFU serials map to more than one board on the bus. Cheap,
        # and it is the only way a wrong adoption could happen.
        seen: dict[str, int] = {}
        for device in untracked:
            key = dfu_serial_for(device.serial)
            if key:
                seen[key] = seen.get(key, 0) + 1

        adopted: list[dict[str, str]] = []
        for device in untracked:
            key = dfu_serial_for(device.serial)
            if not key or seen.get(key, 0) != 1:
                continue
            mcu_type = pairings.type_for(key)
            if not mcu_type or mcu_type not in reg.names():
                continue
            try:
                with Registry.mutate(self.paths, f"adopt {device.serial} as {mcu_type}") as live:
                    if not live.add_serial(mcu_type, device.serial):
                        continue
            except UpdaterError as exc:
                if self._log is not None:
                    self._log.warning(f"could not adopt {device.serial} as {mcu_type}: {exc}")
                continue

            pairings.forget(key)
            adopted.append({"type": mcu_type, "serial": device.serial, "dfu_serial": key})
            if self._log is not None:
                self._log.info(
                    f"adopted {device.serial} as {mcu_type} - it is the board whose "
                    f"bootloader was installed as {mcu_type} (DFU serial {key})"
                )

        pairings.prune()
        if adopted:
            self._changed()
        return adopted

    def _identify_dfu(self, devices: list) -> None:
        """Name the boards in DFU that we already know about.

        A DFU device has no `/dev/serial/by-id` name, so `3941335F3434` connects
        to nothing on its own - which is what makes several boards in DFU at once
        so awkward to tell apart. But the DFU serial is *derived* from the same
        unique id the running serial is built from, so every tracked board's DFU
        name can be computed and matched.

        A board that matches nothing is not an error - that is what a genuinely
        new board looks like, and saying so is useful in itself.

        The derivation sums two of the three id words, so a collision is possible
        in principle. Two known boards mapping to one DFU serial therefore names
        neither: an unlabelled board is a small annoyance, and a board labelled as
        the wrong one is how you flash the toolhead you meant to leave alone.
        """
        from ..devices import dfu_serial_for

        owners: dict[str, list[tuple[str, str]]] = {}
        for name, mcu in self.registry().types.items():
            for serial in mcu.serials:
                computed = dfu_serial_for(serial)
                if computed:
                    owners.setdefault(computed, []).append((name, serial))

        for device in devices:
            device["known_serial"] = None
            device["tracked_by"] = None
            matches = owners.get(str(device.get("serial") or ""), [])
            if len(matches) == 1:
                device["tracked_by"], device["known_serial"] = matches[0]

    def dfu_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in DFU mode, and can this agent actually open it?

        Deliberately **reports** failures instead of raising them. Every other
        method treats a refusal as an error because the caller asked for work to
        happen; here, describing the situation *is* the work. "dfu-util is not
        installed" is this method's answer, not its failure.

        The distinctions are not cosmetic - each sends the user somewhere else:

        ``no_tool``
            `apt install dfu-util`. Nothing to do with the board.
        ``permission_denied``
            libusb saw a board and could not claim it. **The boot jumper worked.**
            Reporting this as "no device found" is what once sent a user back to
            redo the one step that had succeeded. The udev rule tags `uaccess`,
            which grants the *seated* user - and this agent is a daemon, not a
            login session - so in practice it rides on `GROUP="plugdev"` and the
            service user being in that group.
        ``none``
            Genuinely nothing in DFU. Fit the boot jumper and replug.
        ``ambiguous``
            More than one board in DFU, and no serial was named to pick between
            them. Not a dead end: dfu-util takes `-S/-p/-n`, so naming one is
            enough - `ready` is false only because the *caller* has not chosen.
        """
        from ..flash import DFU_VID_PID, dfu_devices

        out: dict[str, Any] = {
            "vid_pid": DFU_VID_PID,
            "devices": [],
            "count": 0,
            "ready": False,
            "reason": None,
            "message": None,
        }

        try:
            devices = dfu_devices(reporter=self._log_reporter)
        except ToolMissingError as exc:
            out["reason"] = self.DFU_NO_TOOL
            out["message"] = str(exc)
            return out
        except DfuPermissionError as exc:
            out["reason"] = self.DFU_PERMISSION_DENIED
            out["message"] = str(exc)
            # The raw dfu-util output, because a permissions diagnosis is exactly
            # the case where the operator wants to see what the tool actually said.
            # UpdaterError keeps its extras in .data, not as attributes.
            out["output"] = exc.data.get("output")
            return out
        except UpdaterError as exc:
            out["reason"] = exc.code
            out["message"] = str(exc)
            return out

        self._identify_dfu(devices)
        out["devices"] = devices
        out["count"] = len(devices)
        if not devices:
            out["reason"] = self.DFU_NONE
            out["message"] = (
                "No board is in DFU mode. Fit the boot jumper (or hold BOOT0) and "
                "replug the board."
            )
            return out
        if len(devices) > 1:
            out["reason"] = self.DFU_AMBIGUOUS
            out["message"] = (
                f"{len(devices)} boards are in DFU mode. Pick the one to flash by its "
                f"serial, or unplug the others."
            )
            return out

        out["ready"] = True
        return out

    #: How long to wait for a freshly-flashed board to come back as Katapult.
    #: A class attribute so tests can shrink it without patching a call site,
    #: matching KLIPPY_READY_TIMEOUT and friends.
    ADD_MCU_REENUMERATE_TIMEOUT = float(REENUMERATE_TIMEOUT)

    def add_mcu_start(self, args: dict) -> dict[str, Any]:
        """Put Katapult on a board in DFU, then report what appeared on the bus.

        The one new method the guided flow needs. Adopting the result is
        `fw.serial.add` and putting Klipper on it is `fw.flash` - both already
        exist, and wrapping them here would be a second implementation to keep in
        step with the first.

        **A DFU board has no identity to adopt.** It exposes no
        `/dev/serial/by-id` name at all, so there is nothing to put in the
        registry until Katapult is on it and it re-enumerates. That is why this
        snapshots the bus first and diffs afterwards, rather than taking a serial
        as an argument: the serial does not exist yet.

        **Klipper is not stopped.** A board that is not in printer.cfg is not held
        by Klipper, so there is no port contention and no reason for an outage -
        the CLI's add-mcu has never stopped it either. The exclusive lock is still
        taken, so this cannot run beside a build or a flash.
        """
        runner = self._require_runner()
        settings = self.settings()

        if not settings.enable_flashing:
            raise RpcError(
                "flashing from the web UI is disabled, so a new board cannot be "
                f"set up. Set 'enable_flashing = true' in {self.paths.settings_file}.",
                data={
                    "code": "flashing_disabled",
                    "message": "enable_flashing is false",
                    "data": {"settings_file": self.paths.settings_file},
                },
            )

        name = self._require_str(args, "name")
        reg = self.registry()
        mcu = reg.get(name)  # unknown_type, before a job exists

        # Only STM32 has a DFU path. RP2040 needs BOOTSEL mass storage and a .uf2,
        # which is a different mechanism entirely - say so precisely rather than
        # failing inside the job with something about dfu-util.
        if not mcu.chipset.startswith("stm32"):
            raise RpcError(
                f"{name} is {mcu.chipset}, which does not use DFU. Only STM32 boards "
                f"can be set up this way.",
                data={
                    "code": "unsupported_chipset",
                    "message": "no DFU path for this chipset",
                    "data": {"type": name, "chipset": mcu.chipset},
                },
            )

        katapult_bin = self.paths.bin_file(name, "katapult")
        if not os.path.exists(katapult_bin):
            raise RpcError(
                f"no built Katapult firmware for {name}. Build it first - this flow "
                f"installs the bootloader, so the bootloader has to exist.",
                data={
                    "code": "no_artifact",
                    "message": "katapult has not been built for this type",
                    "data": {"type": name, "fw": "katapult", "path": katapult_bin},
                },
            )

        # Which board, decided here rather than in the job, so an ambiguous bus is
        # a synchronous refusal the caller can act on instead of a job that dies.
        scan = self.dfu_scan({})
        target = args.get("dfu_serial")
        if target is not None:
            target = str(target)
            if not any(d.get("serial") == target for d in scan["devices"]):
                raise RpcError(
                    f"no board with serial {target} is in DFU mode.",
                    data={
                        "code": "device_not_found",
                        "message": "the named DFU device is not attached",
                        "data": {"dfu_serial": target, "devices": scan["devices"]},
                    },
                )
        elif not scan["ready"]:
            raise RpcError(
                scan["message"] or "no board is ready in DFU mode.",
                data={
                    "code": f"dfu_{scan['reason']}",
                    "message": scan["message"],
                    "data": {"devices": scan["devices"], "reason": scan["reason"]},
                },
            )
        else:
            target = scan["devices"][0].get("serial")

        # Every serial actually on the bus right now - NOT "everything untracked".
        #
        # The distinction matters: a board being re-bootloadered is often already
        # in the registry, sitting offline because it had no firmware. Baselining
        # on untracked-only meant it came back, was correctly excluded as tracked,
        # and the job reported "no new device appeared" - sending the user to hunt
        # for a failure when the flash had worked perfectly.
        from ..devices import scan as scan_bus

        before = {d.serial for d in scan_bus(self.paths)}

        def run(ctx) -> dict[str, Any]:
            from ..devices import KATAPULT_FW_NAME, wait_for_new_device
            from ..flash import flash_initial_bootloader

            ctx.step(f"Flashing Katapult onto the DFU board for {name}", 0, 2)
            flash_initial_bootloader(
                self.paths,
                self.settings(),
                mcu.chipset,
                katapult_bin,
                reporter=ctx.reporter,
                target_serial=target,
            )

            # Recorded here - after the write, BEFORE the wait - because the wait
            # timing out is precisely the case this covers. A board on a marginal
            # port, or unplugged and brought back tomorrow, then still arrives
            # with its intent attached rather than as an anonymous stranger.
            if target:
                from ..pairings import Pairings

                Pairings(self.paths).record(target, name)

            ctx.step("Waiting for the board to re-enumerate as Katapult", 1, 2)
            appeared = wait_for_new_device(
                self.paths,
                before,
                fw=KATAPULT_FW_NAME,
                chipset=mcu.chipset,
                timeout=self.ADD_MCU_REENUMERATE_TIMEOUT,
            )

            # Split by whether the registry already knows it. Both mean the flash
            # worked; only one leaves anything for the user to do.
            tracked = set(self.registry().all_serials())
            candidates = [d for d in appeared if d.serial not in tracked]
            already = [d for d in appeared if d.serial in tracked]

            ctx.step(f"Found {len(appeared)} board(s)", 2, 2)
            for device in already:
                ctx.reporter(
                    "info",
                    f"{device.serial} is back in Katapult and already tracked - "
                    f"nothing to adopt. Flash Klipper onto it when ready.",
                )
            if not appeared:
                # Not raised: the write may well have succeeded and the board may
                # simply be slow or on a marginal port. Saying what to look at
                # beats failing a job that probably worked.
                ctx.reporter(
                    "warn",
                    "No board appeared in Katapult. Check `ls /dev/serial/by-id/` - "
                    "if it is there, adopt it directly with fw.serial.add.",
                )
            return {
                "type": name,
                "chipset": mcu.chipset,
                "dfu_serial": target,
                "candidates": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in candidates
                ],
                # Appeared, but the registry already has it - the re-bootloader
                # case. Distinct from an empty result, which means nothing came
                # back at all.
                "already_tracked": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in already
                ],
            }

        job = runner.submit("add_mcu", {"name": name, "dfu_serial": target}, run)
        return {"job_id": job.id, "job": job.to_dict(), "type": name, "dfu_serial": target}

    # -- bulk operations ----------------------------------------------------

    #: Scopes shared by every bulk operation.
    #:
    #: ``stale`` - only what the provenance says needs doing. Correct *because* of
    #:   the flash log: a rebuilt artifact makes its boards stale even when the
    #:   klipper commit has not moved, which is the case a version comparison
    #:   cannot see.
    #: ``all`` - everything in scope regardless. For when you know something the
    #:   provenance cannot, such as having edited an untracked source file.
    SCOPES = ("stale", "all")

    def _scope(self, args: dict) -> str:
        scope = str(args.get("scope") or "stale")
        if scope not in self.SCOPES:
            raise RpcError(
                f"'scope' must be one of {list(self.SCOPES)}", ERR_INVALID_PARAMS
            )
        return scope

    def _install(self) -> providers.Install:
        """This host's config, parsed once for a whole selection or batch."""
        return providers.Install.load(self.paths, self.settings())

    def _build_targets(
        self,
        install: providers.Install,
        scope: str,
        only: Optional[str] = None,
        fw: Optional[str] = None,
    ) -> providers.Selection:
        """What a build_all should touch, across every build system.

        The loop itself lives in :mod:`~mcu_updater.providers`, because it is not
        about the agent: enumerate, filter, judge, is the same work whoever asks.
        What is left here is the one translation that *is* the agent's - `scope`
        is this API's word, validated by `_scope`, and the providers take the
        decision rather than a second copy of the vocabulary.

        Walking providers rather than the `[mcu ...]` registry is what puts
        screens in a fleet build. The registry was the only list this had, so
        "build everything" meant "build every MCU" and every display was left on
        whatever it was running - silently, because nothing enumerated them to
        notice they were missing.

        `only` narrows to one target, which is what makes "update this one board
        type" the same operation with a filter rather than another loop. `fw`
        narrows to one family - "rebuild katapult everywhere" - as a filter over
        what each target already uses, never an instruction to build a family
        something does not run.
        """
        return providers.select(
            install, stale_only=(scope == "stale"), only=only, fw=fw
        )

    def _boards_to_flash(self, reg: Registry, scope: str, only: Optional[str] = None) -> list[dict]:
        """Which boards a flash_all should write, with the reason for each.

        Offline boards are never included: a flash needs the device on the bus, so
        including them would only produce a guaranteed failure partway through a
        batch that has already stopped Klipper.
        """
        from ..build import FlashLog, git_head, read_sidecar

        versions = self.mcu_info()
        # Resolved per type below: a board running cartographer must be
        # compared against its own fork, not upstream klipper.
        flashlog = FlashLog(self.paths)

        out: list[dict] = []
        for name in reg.names():
            if only is not None and name != only:
                continue
            mcu = reg.get(name)
            if not os.path.exists(self.paths.bin_file(name, mcu.firmware)):
                continue
            fw_head = git_head(
                firmware.resolve(self.paths, mcu.firmware).source_dir(self.paths)
            )
            artifact_sha = (read_sidecar(self.paths, name, mcu.firmware) or {}).get("bin_sha256")
            for serial in mcu.serials:
                state, _ = device_state(self.paths, mcu.chipset, serial)
                if state == STATE_OFFLINE:
                    continue
                info = self.flash_state(
                    serial,
                    versions,
                    fw_head,
                    state=state,
                    artifact_sha=artifact_sha,
                    flashlog=flashlog,
                )
                if scope == "all" or info["needs_flash"] is True:
                    out.append(
                        {
                            "type": name,
                            "serial": serial,
                            "chipset": mcu.chipset,
                            # Carried so the flash writes the family this board
                            # runs rather than assuming klipper.
                            "fw": mcu.firmware,
                            "state": state,
                            "reason": info["reason"] if scope != "all" else "forced",
                        }
                    )
        return out

    def _screens_to_flash(
        self, scope: str, only: Optional[str] = None
    ) -> list[flashers.FlashTarget]:
        """Which screens a flash_all should write, with the reason for each.

        **Selected here, at submission time, because only a running Klipper can
        answer.** The screen list comes from the klippy module's own printer
        objects, so it has to be read before anything stops - which is exactly
        why selection is the agent's job and not the flasher's.

        The same two exclusions as boards, for the same reasons: a display with
        nothing built has nothing to write, and a screen that is not there
        cannot be written to. `scope: all` overrides the judgement, never the
        physics.
        """
        known = self.display_types()
        out: list[flashers.FlashTarget] = []
        for payload in self.display_status():
            if only is not None and payload["name"] != only:
                continue
            if not payload["has_firmware"]:
                continue
            # The live object, not one rebuilt from the payload: `to_json` is a
            # wire projection and reversing it is the thing this codebase keeps
            # deciding not to do.
            display = known[payload["name"]]
            for screen in payload["screens"]:
                if not screen["present"]:
                    continue
                status = self._screen_device_status(screen)
                if scope != "all" and status.needs_flash is not True:
                    continue
                target = flashers.esptool.target_for(display, screen)
                out.append(
                    dataclasses.replace(
                        target,
                        detail={
                            **target.detail,
                            "reason": "forced" if scope == "all" else status.reason,
                        },
                    )
                )
        return out

    def _do_build_all(
        self, ctx: Any, targets: list[providers.BuildTarget]
    ) -> dict[str, Any]:
        """Build each target in turn, reporting failures rather than stopping.

        Matches what the CLI's update-all has always done. One type failing to
        compile is usually about that type, and abandoning the rest would turn a
        one-board problem into a whole-fleet one.

        Each target names its own provider and family, so one pass compiles
        cartographer for the probe, klipper for the boards and PlatformIO for the
        screens - rather than one build system for everything and silence about
        whatever did not fit.

        The config is parsed once for the batch rather than once per target.
        Still at job time, not submission time, so a setting changed while this
        sat queued is honoured; what it no longer does is answer two questions
        about two different configurations because somebody saved a file
        mid-build.
        """
        install = self._install()

        built: list[dict[str, Optional[str]]] = []
        failures: list[dict[str, Optional[str]]] = []
        total = len(targets)
        for index, target in enumerate(targets):
            provider = providers.by_name(target.provider)
            ctx.check_cancelled()
            ctx.step(f"Building {provider.describe(target)}", index, total)
            try:
                provider.build(install, target, reporter=ctx.reporter, cancel=ctx.cancel)
                built.append(target.to_json())
            except OperationCancelled:
                raise
            except UpdaterError as exc:
                # Named the way the provider names it: "carto_v4 failed" is
                # ambiguous once a type can build more than one family, and the
                # two failures want different fixes.
                ctx.reporter("warn", f"{provider.describe(target)}: {exc}")
                failures.append({**target.to_json(), "error": str(exc)})
        ctx.step(f"Built {len(built)} of {total}", total, total)
        return {"built": built, "failures": failures}

    def _bench(self, settings: Settings) -> flashers.Bench:
        """The host, as a flasher needs to see it.

        A controller *factory* rather than a controller: the units are not known
        until the batch is - a display family names its own port watcher, and a
        batch spanning two families needs two. Sharing the factory keeps the
        backend choice in one place, which is what stops a dry run from stopping
        a real service.
        """
        from ..service import ServiceController, make_controller

        def controller(name: Optional[str] = None) -> ServiceController:
            return make_controller(settings, call=self._call_for_service, name=name)

        return flashers.Bench(
            paths=self.paths, settings=settings, controller=controller
        )

    def _do_flash_all(
        self, ctx: Any, targets: list[flashers.FlashTarget]
    ) -> dict[str, Any]:
        """Write every selected device, with Klipper stopped once for the batch.

        Once per batch rather than once per device: ten stop/start cycles would
        take far longer and give ten chances for the restart to be the thing
        that fails.

        **Grouped by requirement, not by kind.** A board and a screen both need
        Klipper down - for different reasons, neither of which this loop knows -
        so one stop covers both and neither path had to learn about the other.
        A write that needs no stop runs outside it rather than inheriting an
        outage it does not need.

        Cancellation is honoured *between* devices only. Interrupting a write
        leaves half an image on a board, so the check is at the top of each
        iteration and never inside one.
        """
        from ..service import klipper_stopped

        settings = self.settings()
        bench = self._bench(settings)
        stopped, free = flashers.group_by_stop(targets)

        flashed: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        total = len(targets)
        done = 0

        def write_group(group: list[flashers.FlashTarget]) -> None:
            nonlocal done
            for flasher, mine in flashers.by_flasher(group):
                # Once per flasher, inside whatever stop it asked for. This is
                # where a port watcher gets paused and the screens are asked
                # which they are - the only moment identity can be resolved
                # rather than remembered.
                with flasher.prepared(bench, mine, ctx) as session:
                    for target in mine:
                        # Between devices, never inside a write.
                        ctx.check_cancelled()
                        ctx.step(f"Flashing {target.id} ({target.type})", done, total)
                        done += 1
                        try:
                            extra = flasher.write(bench, session, target, ctx)
                            flashed.append({**target.to_json(), **extra})
                            # After the write and after it is recorded: a device
                            # that came back slowly is still flashed.
                            flasher.settled(bench, target, ctx)
                        except OperationCancelled:
                            raise
                        except UpdaterError as exc:
                            ctx.reporter("warn", f"{target.id}: {exc}")
                            failures.append({**target.to_json(), "error": str(exc)})

        write_group(free)
        if stopped:
            with klipper_stopped(
                self.paths,
                bench.controller(None),
                f"flash {len(stopped)} device(s)",
                reporter=ctx.reporter,
            ):
                write_group(stopped)
        ctx.step(f"Flashed {len(flashed)} of {total}", total, total)

        # klipper_stopped has started the service again by now; confirm it really
        # came back, which is the release gate for every flashing path.
        ctx.reporter("info", "Waiting for Klipper to be ready...")
        self._await_klippy_ready(ctx.reporter)
        return {"flashed": flashed, "failures": failures}

    def build_all(self, args: dict) -> dict[str, Any]:
        """Build everything that needs it. Touches no board and stops nothing.

        Everything, across every build system: an MCU's kconfig families and a
        display's PlatformIO env are both things this host builds, and the only
        reason screens were left out was that the registry was the only list
        this had to walk.

        `fw` is an optional *filter* - "rebuild katapult everywhere" - not the
        family to build for everything. It used to be the latter, defaulting to
        klipper, which meant a type running any other application was skipped
        for want of a klipper config and the batch reported success regardless.
        A named `fw` also excludes displays, which is correct rather than
        incidental: a PlatformIO env has no family to be one of.
        """
        runner = self._require_runner()
        scope = self._scope(args)
        fw = args.get("fw")
        if fw is not None:
            fw = str(fw)
            known = self._fw_names()
            if fw not in known:
                raise RpcError(f"'fw' must be one of {list(known)}", ERR_INVALID_PARAMS)

        selection = self._build_targets(self._install(), scope, fw=fw)
        if not selection.build:
            detail = f" running {fw}" if fw else ""
            hint = "" if scope == "all" else " Use scope 'all' to rebuild regardless."
            raise RpcError(
                f"nothing to build: no target{detail} is both configured and in "
                f"need of building.{hint}",
                data={
                    "code": "nothing_to_do",
                    "message": "nothing needs building",
                    # What was passed over, and why. A batch that quietly drops
                    # an unconfigured target and reports success is the exact
                    # failure this area exists to stop being possible.
                    "data": {
                        "fw": fw,
                        "scope": scope,
                        "skipped": [s.to_json() for s in selection.skipped],
                    },
                },
            )

        def run(ctx) -> dict[str, Any]:
            return self._do_build_all(ctx, selection.build)

        # `types` stays a list of names for the panel, which shows what is being
        # worked on rather than how many compiles that is. `builds` carries the
        # targets for anything that wants the detail.
        names = sorted({t.name for t in selection.build})
        job = runner.submit(
            "build_all",
            {"fw": fw, "scope": scope, "types": names, "count": len(selection.build)},
            run,
        )
        return {
            "job_id": job.id,
            "job": job.to_dict(),
            "types": names,
            "builds": [t.to_json() for t in selection.build],
            "skipped": [s.to_json() for s in selection.skipped],
        }

    def _require_flashable_type(self, reg: Registry, only: str) -> str:
        """A name that must be something this host can flash, board or screen.

        Fails fast on a typo, before a job exists. Both registries are consulted
        because "flash this type" means the same thing whichever kind it names,
        and refusing a display here would be the kind filter creeping back in
        through the front door.
        """
        if only in reg.names() or only in self.display_types():
            return only
        reg.get(only)  # raises with the registry's own unknown_type payload
        return only

    def flash_all(self, args: dict) -> dict[str, Any]:
        """Flash everything that needs it, or everything of one type.

        Boards and screens both. `flash_all` walked the `[mcu ...]` registry
        because that was the only selection it had, so "Flash All" meant "flash
        all the boards" and every display was left behind without a word.

        `name` narrows it to a single type - that is `flash_type`, which is the same
        operation with a filter rather than a second implementation of it.
        """
        runner = self._require_runner()
        settings = self.settings()
        if not settings.enable_flashing:
            raise RpcError(
                "flashing from the web UI is disabled. Set 'enable_flashing = true' in "
                f"{self.paths.settings_file} to allow it.",
                data={
                    "code": "flashing_disabled",
                    "message": "enable_flashing is false",
                    "data": {"settings_file": self.paths.settings_file},
                },
            )

        scope = self._scope(args)
        only = args.get("name")
        reg = self.registry()
        if only is not None:
            only = self._require_flashable_type(reg, str(only))

        boards = self._boards_to_flash(reg, scope, only)
        # Read now, while Klipper can still answer - the same constraint
        # `display_flash` has always had, and the reason this is selection
        # rather than something the batch could work out for itself.
        screens = self._screens_to_flash(scope, only)
        if not boards and not screens:
            raise RpcError(
                "nothing to flash: every online device already matches its built "
                "firmware. Use scope 'all' to flash regardless.",
                data={
                    "code": "nothing_to_do",
                    "message": "no devices need flashing",
                    "data": {"scope": scope, "name": only},
                },
            )

        # The print gate, once, before a job exists - so the refusal carries a real
        # explanation rather than arriving as a job that dies a second later.
        from ..service import assert_printer_idle

        assert_printer_idle(
            settings,
            activity=self._printer_activity,
            force=bool(args.get("force")),
            reporter=self._log_reporter,
        )

        targets = [_board_target(b) for b in boards] + screens

        def run(ctx) -> dict[str, Any]:
            return self._do_flash_all(ctx, targets)

        job = runner.submit(
            "flash_all",
            {"scope": scope, "name": only, "count": len(targets)},
            run,
        )
        return {
            "job_id": job.id,
            "job": job.to_dict(),
            "boards": boards,
            # Beside `boards` rather than merged into it: the two selections
            # answer with different facts - a board has a chipset and a serial,
            # a screen has a port and a section - and flattening them would
            # invent nulls for half of each.
            "displays": [_screen_json(t) for t in screens],
        }

    def update_all(self, args: dict) -> dict[str, Any]:
        """Build what is stale, then flash what is behind - one Klipper stop.

        Composed from the same two routines the individual operations use, rather
        than a third implementation of the loop. Its purpose is a klipper update, so
        `stale` here means the source tree moved; the artifact-hash precision
        matters more to a single-type flash after a patch change.

        `name` narrows both halves to one type - "rebuild this board type and flash
        its boards", which is the same operation with a filter rather than a third
        one to keep in step.

        The build half now covers displays, because it is literally `build_all`.
        The flash half does not yet, because it is literally `flash_all` - so a
        stale screen is rebuilt here and still waits for its own flash. That is
        the composition being honest rather than a special case: both halves gain
        displays where they are defined, not where they are called from.
        """
        runner = self._require_runner()
        settings = self.settings()
        if not settings.enable_flashing:
            raise RpcError(
                "flashing from the web UI is disabled, so update-all cannot run. Set "
                f"'enable_flashing = true' in {self.paths.settings_file} to allow it.",
                data={
                    "code": "flashing_disabled",
                    "message": "enable_flashing is false",
                    "data": {"settings_file": self.paths.settings_file},
                },
            )

        scope = self._scope(args)
        only = args.get("name")
        install = self._install()
        if only is not None:
            only = self._require_flashable_type(install.registry, str(only))
        # Every family each type uses, not klipper for all of them. A fleet
        # update that rebuilds klipper and leaves the probe on last month's
        # cartographer is the failure this exists to prevent, and it was silent.
        targets = self._build_targets(install, scope, only).build

        from ..service import assert_printer_idle

        assert_printer_idle(
            settings,
            activity=self._printer_activity,
            force=bool(args.get("force")),
            reporter=self._log_reporter,
        )

        def run(ctx) -> dict[str, Any]:
            build_result = (
                self._do_build_all(ctx, targets)
                if targets
                else {"built": [], "failures": []}
            )
            # Selected *after* building, because a build is what makes a device
            # stale: choosing up front would use provenance the build has just
            # invalidated. Screens included - a fleet update that rebuilt a
            # screen's firmware and then declined to write it is half a job.
            boards = self._boards_to_flash(self.registry(), scope, only)
            devices = [_board_target(b) for b in boards] + self._screens_to_flash(
                scope, only
            )
            if not devices:
                ctx.reporter("info", "No device needs flashing.")
                return {"build": build_result, "flash": {"flashed": [], "failures": []}}

            # Gate again. The check before submission was minutes ago - a whole
            # fleet build - and the printer may have started moving since. This
            # is the last moment before Klipper gets stopped.
            assert_printer_idle(
                settings,
                activity=self._printer_activity,
                force=bool(args.get("force")),
                reporter=ctx.reporter,
            )
            return {"build": build_result, "flash": self._do_flash_all(ctx, devices)}

        names = sorted({t.name for t in targets})
        job = runner.submit("update_all", {"scope": scope, "name": only, "types": names}, run)
        return {"job_id": job.id, "job": job.to_dict(), "types": names, "name": only}

    # -- what is actually running on the boards -----------------------------

    def _all_object_names(self) -> list[str]:
        """Every Klipper printer object, cached, in the case printer.cfg used.

        Two reasons this is worth a cache rather than a probe per caller.
        `printer.objects.list` is a separate round trip and fw.status has a
        sub-second budget. And the answer only changes when Klipper restarts.

        One list serves every prefix, so adding displays costs no extra round
        trip on top of the MCU lookup that was already happening.
        """
        now = time.time()
        if self._object_names is not None and now - self._object_names_at < MCU_NAMES_TTL:
            return self._object_names
        res = self._probe("printer.objects.list")
        objects = (res or {}).get("objects")
        if not isinstance(objects, list):
            # Leave the cache alone on a failed probe: a stale list beats none, and
            # the next call will try again.
            return self._object_names or []
        names = [str(o) for o in objects]
        self._object_names = names
        self._object_names_at = now
        return names

    def _object_names_for(self, prefix: str) -> list[str]:
        """Printer objects under `prefix`, with their real capitalisation.

        **This is the only place the true name can be learned.** The printer
        object keeps the case printer.cfg used, while `configfile.settings`
        lowercases it - so `[knomi_serial T0_knomi]` is object
        ``knomi_serial T0_knomi`` but setting ``knomi_serial t0_knomi``. Querying
        an object by the name settings gave you returns nothing at all, silently,
        for anyone who capitalises.
        """
        return [
            name
            for name in self._all_object_names()
            if name == prefix or name.startswith(prefix + " ")
        ]

    def _mcu_object_names(self) -> list[str]:
        """Klipper's printer objects that are MCUs."""
        return self._object_names_for("mcu")

    def mcu_info(self) -> dict[str, dict[str, str]]:
        """Tracked serial -> the firmware version that board is actually running.

        This is the thing staleness could not tell you. `staleness()` compares the
        built .bin against the source tree, which answers "do I need to rebuild?".
        It says nothing about what is on the boards, so a board flashed months ago
        and never touched again reported "up to date" as long as nobody had pulled
        klipper since. Two boards of the *same type* can be on different versions,
        which a per-type answer cannot express at all.

        Klipper reports each MCU's `mcu_version` (a git describe, e.g.
        v0.13.0-711-gd7cea5bb) and its configured serial, so the two can be joined.
        """
        names = self._mcu_object_names()
        if not names:
            return {}

        query: dict[str, Any] = {"configfile": ["settings"]}
        for name in names:
            query[name] = ["mcu_version"]
        res = self._probe("printer.objects.query", {"objects": query})
        status = (res or {}).get("status")
        if not isinstance(status, dict):
            return {}

        settings = (status.get("configfile") or {}).get("settings") or {}
        # Klipper lowercases config section names, while the printer object keeps
        # the case from the config file - so `[mcu EBBT0]` is object "mcu EBBT0" and
        # setting "mcu ebbt0". Match on the lowered form.
        serial_by_section = {}
        for section, values in settings.items():
            if not (section == "mcu" or section.startswith("mcu ")):
                continue
            path = (values or {}).get("serial")
            if isinstance(path, str) and path:
                serial_by_section[section.lower()] = _serial_from_path(path)

        out: dict[str, dict[str, str]] = {}
        for name in names:
            version = (status.get(name) or {}).get("mcu_version")
            serial = serial_by_section.get(name.lower())
            if isinstance(version, str) and version and serial:
                # The object name verbatim - "mcu", "mcu EBBT0" - because that is
                # exactly what Mainsail's own System Loads panel shows, and a serial
                # is meaningless until you know which MCU it is.
                out[serial] = {"version": version, "mcu": name}
        return out

    def flash_state(
        self,
        serial: str,
        info: dict[str, dict[str, str]],
        fw_head: Optional[str],
        *,
        state: Optional[str] = None,
        artifact_sha: Optional[str] = None,
        flashlog: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Whether this board wants flashing, and why.

        `needs_flash` is None for "cannot tell" rather than False: an offline board
        or an unreachable Klippy is not evidence that a board is current, and
        claiming otherwise is the bug this whole area exists to fix.

        `reason` is the useful part, because the answers are not equivalent:

        ``in_bootloader``
            Sitting in Katapult, so it reports no klipper version at all. That is
            not "unknown" - a board waiting in its bootloader is the strongest
            possible signal that it wants firmware.
        ``source_changed``
            Running an older klipper commit than the source tree.
        ``artifact_changed``
            Same commit, different binary. This is the one a version comparison
            structurally cannot see: an edited makefile-patch source or a changed
            .config produces a different build from an identical commit, and the
            board cannot tell us which one it holds. Only our own flash record can.
        ``offline`` / ``unknown_version``
            Genuinely no answer.
        """
        entry = info.get(serial) or {}
        version = entry.get("version")
        mcu = entry.get("mcu")
        running = _running_sha(version or "")
        status = self._device_status(
            serial,
            version,
            running,
            fw_head,
            state=state,
            artifact_sha=artifact_sha,
            flashlog=flashlog,
        )
        return {
            "mcu": mcu,
            "running_version": version,
            "running_sha": running,
            "needs_flash": status.needs_flash,
            "reason": status.reason,
        }

    @staticmethod
    def _device_status(
        serial: str,
        version: Optional[str],
        running: Optional[str],
        fw_head: Optional[str],
        *,
        state: Optional[str],
        artifact_sha: Optional[str],
        flashlog: Optional[Any],
    ) -> DeviceStatus:
        """The verdict behind `flash_state`, in the shared vocabulary."""
        if state == STATE_OFFLINE:
            return DeviceStatus(OFFLINE)
        if state == STATE_KATAPULT:
            return DeviceStatus(IN_BOOTLOADER)
        if version is None or running is None or not fw_head:
            return DeviceStatus(UNKNOWN_VERSION)

        # `-dirty` is normal and must not read as a mismatch: a type with makefile
        # patches is dirty by construction, because the patch is in place while
        # klipper stamps its version.
        if not fw_head.startswith(running):
            return DeviceStatus(SOURCE_CHANGED)

        # The commit matches, so only our own record can distinguish two builds of
        # it. Used to *add* confidence and never to remove it: with no record, the
        # commit match stands rather than degrading every board to unknown.
        if flashlog is not None and artifact_sha:
            record = flashlog.entry_for(serial, running)
            flashed = (record or {}).get("bin_sha256")
            if record is not None and flashed and flashed != artifact_sha:
                return DeviceStatus(ARTIFACT_CHANGED)

        return DeviceStatus()

    # -- kconfig -----------------------------------------------------------

    def _sessions(self) -> Any:
        """The session store, created on first use.

        Lazily, because an agent that never opens a config should not pay for the
        import or hold the state.
        """
        if self._kconfig_sessions is None:
            from ..kconfig import SessionStore

            self._kconfig_sessions = SessionStore(self.paths)
        return self._kconfig_sessions

    def kconfig_available(
        self, families: Optional[dict[str, firmware.FirmwareFamily]] = None
    ) -> dict[str, bool]:
        """Which firmware trees can be configured from here.

        A stat per tree, so it is cheap enough for fw.status. Lets the panel hide
        the button rather than offer one that fails on a host where the source tree
        is missing.

        `families` is accepted so a caller already holding the parsed sections -
        `firmware_families`, on the same status call - does not re-read the
        config file to learn what it already knows.
        """
        from ..kconfig import kconfiglib_path

        if families is None:
            families = firmware.load(self.paths)

        out = {}
        for fw in firmware.names_of(families):
            fw_dir = firmware.resolve(self.paths, fw, families).source_dir(self.paths)
            out[fw] = os.path.isfile(kconfiglib_path(fw_dir)) and os.path.isfile(
                os.path.join(fw_dir, "src", "Kconfig")
            )
        return out

    def _session(self, args: dict) -> Any:
        return self._sessions().get(self._require_str(args, "session"))

    def kconfig_open(self, args: dict) -> dict[str, Any]:
        """Parse a firmware tree and start a configuration session.

        The one method here that can approach a second: a full Klipper Kconfig
        parse is a few hundred milliseconds on a Pi. Every other kconfig call works
        against the tree this leaves in memory, which is the reason sessions exist
        at all.
        """
        name = self._require_str(args, "name")
        fw = self._require_str(args, "fw")
        known = self._fw_names()
        if fw not in known:
            raise RpcError(f"'fw' must be one of {', '.join(known)}", ERR_INVALID_PARAMS)

        # The type has to exist: the answers are saved per type, and inventing a
        # directory for a typo is not a helpful thing to do.
        self.registry().get(name)

        store = self._sessions()
        if not bool(args.get("force")):
            clash = store.dirty_for(name, fw)
            if clash is not None:
                raise RpcError(
                    f"another session ({clash.id}) has unsaved changes to "
                    f"{name}/{fw}. Opening a second one risks one save discarding "
                    f"the other's work - finish or discard that one first, or pass "
                    f"force to take it over.",
                    data={
                        "code": "kconfig_session_conflict",
                        "message": "another session has unsaved changes",
                        "data": {"session": clash.id, "type": name, "fw": fw},
                    },
                )

        session = store.open(name, fw)
        with session.lock:
            payload = session.menu()
        payload["available"] = self.kconfig_available()
        return payload

    def kconfig_menu(self, args: dict) -> dict[str, Any]:
        """Re-read the current screen, for a client that lost its copy."""
        session = self._session(args)
        with session.lock:
            return session.menu()

    def kconfig_enter(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        node_id = self._require_str(args, "id")
        with session.lock:
            return session.enter(node_id)

    def kconfig_up(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        with session.lock:
            return session.up()

    def kconfig_set(self, args: dict) -> dict[str, Any]:
        """Assign one symbol and return the menu it leaves behind."""
        session = self._session(args)
        node_id = self._require_str(args, "id")
        if "value" not in args:
            raise RpcError("'value' is required", ERR_INVALID_PARAMS)
        with session.lock:
            return session.set_value(node_id, str(args.get("value")))

    def kconfig_help(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        node_id = self._require_str(args, "id")
        with session.lock:
            return session.help(node_id)

    def kconfig_search(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        with session.lock:
            return session.search(str(args.get("query") or ""))

    def kconfig_reset(self, args: dict) -> dict[str, Any]:
        """Discard unsaved edits by reparsing from disk."""
        session = self._session(args)
        with session.lock:
            return session.reset()

    def kconfig_save(self, args: dict) -> dict[str, Any]:
        """Write the answers, optionally kicking off a build.

        Takes the *build* lock, not the registry one, because this genuinely
        conflicts with a build: `build()` hashes the .config to record what a binary
        was compiled from, so changing it underneath would leave provenance that
        does not match the artifact - and staleness would then report a wrong
        binary as fresh.

        The save is also captured as this type's **own profile**. It is the
        moment a user stops tracking the vendor's answers and starts keeping
        their own, and it is nearly free here because the tree is parsed and the
        minimal answers come back from the save itself. Without it, editing a
        profile stays the dead end it is today: the drift is reported, and the
        answers that caused it have nowhere to live.
        """
        from ..lock import ExclusiveLock

        session = self._session(args)
        want_build = bool(args.get("build"))

        with session.lock:
            lock = ExclusiveLock(self.paths)
            try:
                lock.acquire(f"save config {session.mcu_type}/{session.fw}")
            except UpdaterError:
                raise
            try:
                result = session.save()
                result["custom_profile"] = self._capture_answers(
                    session.mcu_type, session.fw, result.get("answers") or []
                )
            finally:
                lock.release()
            result["menu"] = session.menu()

        self._changed()

        if want_build:
            # Deliberately after the lock is released: build() takes it itself, and
            # holding it across both would deadlock.
            started = self.build({"name": session.mcu_type, "fw": session.fw})
            result["job_id"] = started.get("job_id")
        return result

    def _capture_answers(
        self, mcu_type: str, fw: str, answers: list[str]
    ) -> Optional[str]:
        """Keep a just-saved set of answers as this type's own profile.

        Skipped where it would only make noise: a tree that ships no profiles has
        no picker to offer this in, and a type that has never been near one has
        nothing to fork from - for those, the `.config` is already the whole
        story and a second copy of it under a profile name is a file nobody asked
        for.

        Best effort. The answers are not at risk if this fails - they are in the
        `.config` that was just written, which is what the capture is a copy of.
        Failing the save over a bookkeeping write would be the tail wagging the
        dog, so the result reports null and the caller can see it did not happen.
        """
        if not answers:
            return None
        try:
            families = firmware.load(self.paths)
            # Read after the write, so `customised` here means "this save changed
            # something". A save that changed nothing leaves a config still
            # matching what its profile wrote, and copying that under the user's
            # name would put a duplicate of the vendor's entry in the picker.
            state = profiles.status(self.paths, mcu_type, fw, families)
            if state.managed and state.reason != profiles.CUSTOMISED:
                return None
            if not state.managed and not profiles.available(
                self.paths, fw, families, mcu_type=mcu_type
            ):
                return None
            kept = profiles.capture_custom(
                self.paths,
                mcu_type,
                fw,
                answers=answers,
                parent=state.profile,
                families=families,
            )
        except (UpdaterError, OSError):
            return None
        return kept.name

    def kconfig_close(self, args: dict) -> dict[str, Any]:
        session_id = self._require_str(args, "session")
        return {"session": session_id, "closed": self._sessions().close(session_id)}

    # -- profiles ----------------------------------------------------------

    def profile_list(self, args: dict) -> dict[str, Any]:
        """What a type could be seeded from, and what it currently is.

        Keyed on the type rather than on a firmware family, because "which
        profiles apply to this board" is the question a panel is actually
        asking - and the answer depends on which family the type declares it
        runs, not on which trees happen to be installed.

        Each entry carries the answers that **distinguish** it from the others.
        Cartographer's USB and CAN variants differ by one answer out of seven, so
        a picker listing all seven under each of eight entries hides the one line
        that decides anything. That comparison is text over eight small files, so
        it is free and unconditional.

        `detail: true` additionally labels those answers with the tree's own
        prompt text - "Use PA11/PA12 for CANbus" rather than
        `STM32_CANBUS_PA11_PA12`. That needs the Kconfig tree, so it is opt-in
        and costs one parse: affordable because opening a picker is a click, in
        the same budget `fw.kconfig.open` already spends, and deliberately kept
        off `fw.status`, which every state event recomputes for every client.
        """
        name = self._require_str(args, "name")
        reg = self.registry()
        mcu = reg.get(name)
        families = firmware.load(self.paths)
        fw = str(args.get("fw") or mcu.firmware).strip()
        if fw not in families and fw not in firmware.BUILTIN:
            raise RpcError(
                f"'fw' must be one of {', '.join(self._fw_names())}", ERR_INVALID_PARAMS
            )

        seeds = profiles.available(self.paths, fw, families, mcu_type=name)
        differences = profiles.distinguishing(seeds)
        labels = (
            self._prompt_labels(fw, families, differences)
            if bool(args.get("detail"))
            else {}
        )

        return {
            "type": name,
            "firmware": mcu.firmware,
            "fw": fw,
            "profile": mcu.profile,
            "available": [
                {
                    **seed.to_json(),
                    "distinguishing": [
                        {**row, "label": labels.get(str(row["symbol"]))}
                        for row in differences.get(seed.name, [])
                    ],
                }
                for seed in seeds
            ],
            "state": {
                f: profiles.status(self.paths, name, f, families).to_json()
                for f in mcu.families()
            },
        }

    def _prompt_labels(
        self,
        fw: str,
        families: dict[str, firmware.FirmwareFamily],
        differences: dict[str, list[dict[str, Any]]],
    ) -> dict[str, str]:
        """One parse, labelling every symbol every profile is told apart by.

        Degrades to no labels rather than failing the listing: a tree that cannot
        be parsed - not cloned, or missing its vendored kconfiglib - is a picker
        showing raw symbol names, which is worse than prompt text and far better
        than an error where the profiles should be.
        """
        from .. import kconfig as kconfig_mod

        symbols = {str(row["symbol"]) for rows in differences.values() for row in rows}
        if not symbols:
            return {}
        fw_dir = firmware.resolve(self.paths, fw, families).source_dir(self.paths)
        try:
            return kconfig_mod.prompts(fw_dir, sorted(symbols))
        except (UpdaterError, OSError):
            return {}

    def profile_apply(self, args: dict) -> dict[str, Any]:
        """Seed a type's answers from its firmware tree, bootloader included.

        The bootloader is derived by default rather than on request. Seeding
        only the application leaves a type whose two configs describe different
        boards, and the pair only has to disagree about one address for the
        result to be a board that does not come back - so the safe combination
        is the one that takes no extra argument.

        `derive` is still separable, because a type with no bootloader
        (`katapult_installed: false`) has nothing to derive and asking for it
        would be an error rather than a no-op.

        **A job, not a synchronous answer.** Seeding parses a Kconfig tree up to
        three times - the seed, a bare probe of the bootloader tree, then the
        carried answers - and one parse is a few hundred milliseconds on a Pi.
        Moonraker awaits our reply with no timeout, so a method that might sit
        past a second holds a browser's HTTP request open; the rule at the top
        of this file exists for exactly that. Every argument is still validated
        *before* the job exists, so a typo is refused immediately rather than
        arriving as a job that dies a second later.
        """
        runner = self._require_runner()
        name = self._require_str(args, "name")
        profile = self._require_str(args, "profile")
        force = bool(args.get("force"))

        reg = self.registry()
        mcu = reg.get(name)
        families = firmware.load(self.paths)
        fw = str(args.get("fw") or mcu.firmware).strip()
        if fw not in families and fw not in firmware.BUILTIN:
            raise RpcError(
                f"'fw' must be one of {', '.join(self._fw_names())}", ERR_INVALID_PARAMS
            )

        derive = args.get("derive")
        derive = mcu.katapult_installed if derive is None else bool(derive)
        # Named before the job starts, so an unknown profile is a refusal rather
        # than a failed job - and so the confirmation can say what it will write.
        seed = profiles.find(self.paths, fw, profile, families, mcu_type=name)
        # Likewise "that config is yours, pass force": a refusal a caller can
        # act on, not a failure it has to read out of a dead job. Two file
        # hashes and no Kconfig parse, so it is fine to ask here and again
        # inside the write, where it is the authority.
        for family in [fw] + ([firmware.BOOTLOADER] if derive else []):
            profiles.refuse_if_customised(self.paths, name, family, force=force)

        def run(ctx) -> dict[str, Any]:
            steps = 2 if derive else 1
            ctx.step(f"Seeding {name} ({fw}) from {seed.name}", 0, steps)
            applied = profiles.apply_seed(
                self.paths, name, fw, seed.name, families=families, force=force
            )
            for line in applied.answers:
                ctx.reporter("stdout", line)
            out: dict[str, Any] = {"applied": applied.to_json(), "derived": None}

            if derive:
                # Not wrapped in a try: a bootloader that cannot be derived is a
                # board that should not be flashed, and reporting the application
                # seeding as a success with a warning attached is how that gets
                # missed. The application's config stays - it is valid on its own.
                ctx.step(f"Deriving {firmware.BOOTLOADER} from {fw}", 1, steps)
                derived = profiles.derive_bootloader(
                    self.paths, name, fw, families=families, force=force
                )
                for line in derived.dropped:
                    ctx.reporter(
                        "info", f"{firmware.BOOTLOADER} does not define {line} - dropped"
                    )
                out["derived"] = derived.to_json()

            # The intent goes in the hand-edited config; the verdict stays in the
            # data tree. Only for the application - katapult's is always derived,
            # so recording a second key would be restating that.
            if fw == mcu.firmware:
                with Registry.mutate(self.paths, f"profile for {name}") as writable:
                    writable.get(name).profile = applied.profile

            ctx.step(f"Seeded {name}", steps, steps)
            self._changed()
            return out

        job = runner.submit(
            "profile_apply", {"name": name, "fw": fw, "profile": seed.name}, run
        )
        return {"job_id": job.id, "job": job.to_dict(), "type": name, "fw": fw}

    def profile_forget(self, args: dict) -> dict[str, Any]:
        """Detach a type from its profile, leaving every answer exactly as is.

        The escape hatch that makes the drift reporting tolerable: someone who
        has deliberately customised a config can say so once, instead of
        reading "Customised" as a warning for the life of the install.
        """
        name = self._require_str(args, "name")
        reg = self.registry()
        mcu = reg.get(name)
        fw = str(args.get("fw") or "").strip()
        targets = [fw] if fw else list(mcu.families())

        forgotten = [f for f in targets if profiles.forget(self.paths, name, f)]
        if not fw or mcu.firmware in targets:
            with Registry.mutate(self.paths, f"forget profile for {name}") as writable:
                writable.get(name).profile = ""

        self._changed()
        return {"type": name, "forgotten": forgotten}

    # -- dispatch ----------------------------------------------------------

    def dispatch(self, method: str, params: Any = None) -> Any:
        attr = self.available_methods().get(method)
        if attr is None:
            raise MethodNotFound(method)

        if params is None:
            args: dict = {}
        elif isinstance(params, dict):
            args = params
        elif isinstance(params, list):
            # Moonraker relays whatever the caller passed as "arguments". A
            # non-empty positional list is unusable here, so say so plainly
            # rather than silently ignoring it.
            if params:
                raise RpcError(
                    f"{method} takes named arguments, got a positional list",
                    ERR_INVALID_PARAMS,
                )
            args = {}
        else:
            raise RpcError(
                f"{method} expects an object of arguments, got {type(params).__name__}",
                ERR_INVALID_PARAMS,
            )

        try:
            return getattr(self, attr)(args)
        except UpdaterError as exc:
            # Surface the stable .code so the panel can switch on it instead of
            # parsing English.
            raise RpcError(exc.message, data=exc.to_dict()) from exc
