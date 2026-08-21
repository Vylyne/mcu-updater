"""fw.ping / fw.status / fw.type.list / fw.device.list / fw.job.* -- the read surface."""

from __future__ import annotations

import dataclasses
import os
import platform
import re
import time
from collections.abc import Callable
from typing import Any

from ... import API_VERSION, __version__, firmware, profiles, providers
from ...build import read_sidecar
from ...config import Registry
from ...devices import (
    STATE_KATAPULT,
    STATE_OFFLINE,
    BusDevice,
    device_state,
    parse_entry,
    scan,
)
from ...errors import (
    UpdaterError,
)
from ...flashers.pairings import PAIRING_TTL as _PAIRING_TTL
from ...paths import Paths
from ...settings import Settings, load_settings
from ...states import (
    ARTIFACT_CHANGED,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    UNKNOWN_VERSION,
    ArtifactStatus,
    DeviceStatus,
)
from ..rpc import ERR_INVALID_PARAMS, MethodNotFound, RpcError
from ._api import _Base

#: How long a Moonraker query may block before we give up and report unknown.
#: Small on purpose - these are best-effort enrichments of fw.status, and the
#: whole call has a sub-second budget.
PROBE_TIMEOUT = 1.5


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _size(path: str) -> int | None:
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


def _running_sha(version: str) -> str | None:
    match = _FW_SHA_RE.search(version or "")
    return match.group(1) if match else None


def _serial_from_path(path: str) -> str | None:
    """The serial component of a /dev/serial/by-id path.

    Reuses the bus parser rather than string-slicing, so the two cannot disagree
    about what counts as a serial.
    """
    parsed = parse_entry(os.path.basename(path), os.path.dirname(path))
    return parsed.serial if parsed is not None else None


def serialize_device(dev: BusDevice, tracked_by: str | None = None) -> dict[str, Any]:
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


class StatusMixin(_Base):
    """Read-only view of the tool's state, exposed over JSON-RPC."""

    def __init__(
        self,
        paths: Paths,
        *,
        call: Callable[[str, Any, float], Any] | None = None,
        runner: Any | None = None,
        logger: Any = None,
        on_change: Callable[[], None] | None = None,
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
        self._kconfig_sessions: Any | None = None
        # Cached printer-object names; see _all_object_names.
        self._object_names: list[str] | None = None
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
                    "builder": family.builder,
                    "bootloader": family.bootloader,
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

    def klipper_service_state(self) -> str | None:
        info = self._probe("machine.system_info")
        try:
            return info["system_info"]["service_state"]["klipper"]["active_state"]
        except (TypeError, KeyError):
            return None

    def is_printing(self, activity: dict | None = None) -> bool | None:
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

        from ...build import artifact_status, git_head, sha256_file

        # Hashed once and handed to both. These two ask a different question of
        # the same file in the same breath - "is the binary current with its
        # inputs" and "do the inputs still say what the profile said" - and each
        # used to read it for itself, so one fw.status read every saved config
        # on every printer twice.
        config_sha = sha256_file(cfg)

        status = artifact_status(self.paths, mcu_type, fw, config_sha=config_sha)

        return {
            "has_config": os.path.exists(cfg),
            "config_mtime": _mtime(cfg),
            "has_bin": os.path.exists(binary),
            "bin_mtime": _mtime(binary),
            "bin_size": _size(binary),
            "has_uf2": os.path.exists(uf2),
            "built_fw_sha": side.get("fw_sha"),
            "current_fw_sha": git_head(firmware.resolve(self.paths, fw).source_dir(self.paths)),
            # The granular verdict - never_built, config_changed,
            # source_changed, no_provenance, or None for current. Not
            # collapsed: distinguishing "never built" from "built but
            # unverifiable" is exactly what the old (stale, stale_reason)
            # pair could not carry.
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
        versions: dict[str, dict[str, str]] | None = None,
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
        from ...build import git_head

        mcu = reg.get(name)
        if versions is None:
            versions = self.mcu_info()
        families = firmware.load(self.paths)
        application = mcu.application(families)
        fw_head = git_head(firmware.resolve(self.paths, application, families).source_dir(self.paths))

        # Read once per type, not per board: it is one small file, but a ten-board
        # type would otherwise open it ten times.
        from ...build import FlashLog

        flashlog = FlashLog(self.paths)
        artifact_sha = (read_sidecar(self.paths, name, application) or {}).get("bin_sha256")

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
            "firmware": application,
            "serials": serials,
            "artifacts": {fw: self.artifact(name, fw) for fw in mcu.fw_order()},
            "katapult_installed": mcu.bootloader(families) is not None,
            # True when at least one board is behind the source tree. Distinct from
            # the artifact being stale: "needs rebuilding" and "needs flashing" are
            # different questions, and reporting only the first is what let a board
            # 90 commits behind show as up to date.
            "needs_flash": any(s.get("needs_flash") for s in serials),
        }
        for fw in mcu.fw_order():
            cfg = mcu.fw_get(fw)
            block: dict[str, Any] = {
                "extra_args": cfg.extra_args,
                "makefile_patches": [p.to_json() for p in cfg.makefile_patches],
            }
            if fw == "katapult":
                block["installed"] = mcu.bootloader(families) is not None
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
        displays = self.pio_status()
        return {
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
            # types[] and displays[] said in one shape, so a panel can render
            # an MCU, a display and whatever comes next with a single
            # component. The two originals retired at API_VERSION 2.
            "targets": self.targets(reg, types, displays),
        }

    def pio_status(self) -> list[dict[str, Any]]:
        """Configured display types, each with the screens Klipper expects.

        Rolled into fw.status so the panel paints in one call, like everything
        else. Cheap when unconfigured: no `[display]` sections means no work at
        all, not even the configfile query.
        """
        from ...providers import pio as pio_mod

        types = self.pio_types()
        if not types:
            return []

        listed = self.device_list({})

        out = []
        for _name, display in sorted(types.items()):
            prefix = display.klipper_section
            # Once per type, not once per screen: they share a source tree, and
            # it costs three git calls.
            tree = pio_mod.source_state(display.source)
            art = pio_mod.artifact_status(self.paths, display, tree)
            screens = []
            for entry in listed["displays"]:
                if not entry["section"].startswith(prefix + " "):
                    continue
                device = pio_mod.device_status(entry.get("firmware_version"), tree)
                screens.append(
                    {
                        **entry,
                        # None (current), source_changed, device_dirty, or
                        # unknown_version. Compares the sha baked into what the
                        # screen reports running against the source tree's
                        # HEAD - so unlike the MCU artifact check, this is
                        # about the device rather than a built file.
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
                    "has_firmware": os.path.exists(pio_mod.firmware_bin(display)),
                    # None (current), never_built, config_changed,
                    # source_changed, built_dirty, foreign_build, or
                    # no_provenance. Real staleness, unlike has_firmware:
                    # flashing a display uploads whatever is in .pio/build
                    # without building, so a tree that moved since the last
                    # build writes old firmware to every screen with nothing
                    # to say so.
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
                    # this module; `source_changed` is it running an older
                    # commit than the tree. Neither is inferred from the other -
                    # a screen can be several commits old and still speak the
                    # protocol fine.
                    "needs_flash": any(
                        s.get("protocol_match") is False
                        or s.get("reason") == pio_mod.SOURCE_CHANGED
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
    def _aggregate(devices: list[dict[str, Any]]) -> bool | None:
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
        ] + [self._pio_target(payload, allowed) for payload in displays]

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
            # Which build system owns this type. `kind` said the same thing in a
            # vocabulary that only had two words in it - and a reader switching on
            # it was re-deriving what the provider registry already knows.
            "provider": providers.KconfigMake.name,
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

    def _pio_target(
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
                            "fw.flash",
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
        if "fw.build" in allowed:
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
                    "method": "fw.build",
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
                flash_method="fw.flash",
                update_method=None,
            )
        )

        return {
            "provider": providers.PlatformIO.name,
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
        extra: list[dict[str, Any]] | None = None,
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
        update_method: str | None = "fw.update_all",
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

    def lock_holder(self) -> dict[str, Any] | None:
        from ...lock import ExclusiveLock

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

    def _printer_activity(self) -> dict[str, str | None]:
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

    def _print_state(self) -> str | None:
        return self._printer_activity().get("print_state")

    def _klippy_state(self) -> tuple[str | None, str]:
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
        timeout: float | None = None,
        after_restart: float | None = None,
    ) -> str | None:
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

    def _poll_klippy(self, timeout: float) -> str | None:
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
        "fw.device.list": "device_list",
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

    def device_list(self, args: dict) -> dict[str, Any]:
        """The devices Klipper is configured for, and whether they are there.

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
        prefixes = {d.klipper_section for d in self.pio_types().values()}
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
        from ...providers import pio as pio_mod
        from ...service import make_controller

        settings = self.settings()
        out: dict[str, Any] = {}
        for name, display in self.pio_types().items():
            svc = (
                make_controller(settings, call=self._call_for_service, name=display.service)
                if display.service
                else None
            )
            devices = pio_mod.read_device_map(self.paths, display)
            out[name] = {
                "service": display.service or None,
                "active": svc.is_active() if svc is not None else None,
                # When the watcher last wrote. Weak evidence, and only in one
                # direction: an old file is suspicious, but a fresh one does not
                # mean the watcher is still running - it could have stopped a
                # second after writing - and an old one does not mean the map is
                # wrong, because nothing changing means nothing to write. Shown
                # so a human can judge; never branched on.
                "updated": _mtime(pio_mod.device_map_path(self.paths, display)),
                "devices": [d.to_json() for d in devices.values()],
            }
        return out

    def pio_types(self) -> dict:
        """Configured `[display <env>]` sections."""
        from ...providers import pio as pio_mod

        return pio_mod.load(self.paths)

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
        fw_head: str | None,
        *,
        state: str | None = None,
        artifact_sha: str | None = None,
        flashlog: Any | None = None,
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
        version: str | None,
        running: str | None,
        fw_head: str | None,
        *,
        state: str | None,
        artifact_sha: str | None,
        flashlog: Any | None,
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
