"""fw.flash, fw.dfu.scan, fw.add_mcu.start/scan -- writing firmware to a board."""

from __future__ import annotations

import os
from typing import Any

from ... import firmware, flashers, helpers, inventory, providers, stop_services
from ...config import Registry
from ...errors import (
    FlashError,
    UpdaterError,
)
from ...paths import REENUMERATE_TIMEOUT
from ...settings import Settings
from ..rpc import ERR_INVALID_PARAMS, RpcError
from ._api import _Base


class FlashMixin(_Base):
    def flash(self, args: dict) -> dict[str, Any]:
        """Flash one board. Returns a job id immediately.

        Every refusal happens *here*, synchronously, before a job exists - so the
        caller gets a real explanation instead of a job that fails a second later.
        In order: capability gate, argument validation, type/serial pairing,
        artifact present, board actually attached, the family able to write it,
        and finally the print gate.
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

        name = args.get("name")
        if name:
            provider = self._provider_of(str(name))
            if provider == providers.PlatformIO.name:
                return self._pio_flash(args)
            if provider == providers.Cmake.name:
                return self._cmake_flash(args, str(name), runner, settings)

        # `uuid` is the third identity form, alongside `serial`/`port` - a
        # CAN-addressed board rather than a by-id one. Checked before `serial`
        # is required, since a caller naming a uuid has nothing to put there.
        uuid = args.get("uuid")
        if uuid:
            return self._flash_can(args, str(uuid), name, runner, settings)

        # `id` is the uniform slot - `FlashTarget.id` is a serial for a board and
        # a port for a screen - and `serial` is what this method has always been
        # called with. Both, so a caller reading `targets[].devices[].id` off the
        # wire can hand it straight back.
        serial = args.get("serial") or args.get("id")
        if not serial:
            raise RpcError("'serial' is required", ERR_INVALID_PARAMS)
        serial = str(serial)
        force = bool(args.get("force"))

        reg = self.registry()
        # resolve_serial raises unknown_serial / ambiguous_serial /
        # serial_tracked_elsewhere, all of which the panel switches on by code.
        mcu_type = reg.resolve_serial(serial, str(name) if name else None)
        mcu = reg.get(mcu_type)
        families = firmware.load(self.paths)
        application = mcu.application(families)

        # Refused here only when the build staged nothing at all. A build that
        # staged some other kind than a listed flasher takes is selection's to
        # refuse, by kind and with the fix named.
        fw_bin = self.paths.bin_file(mcu_type, application)
        family = firmware.resolve(self.paths, application, families)
        if not providers.staged(self.paths, mcu_type, family).artifacts:
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
        from ...devices import find_device

        present = find_device(self.paths, mcu.chipset, serial)
        if present is None:
            raise RpcError(
                f"{serial} is not attached (looked for chipset {mcu.chipset}). "
                f"Is it plugged in and powered?",
                data={
                    "code": "device_not_found",
                    "message": "board is not on the bus",
                    "data": {"serial": serial, "chipset": mcu.chipset},
                },
            )

        # The family decides who writes this board, before a job exists.
        target = flashers.select_device(
            self.paths,
            families,
            flashers.Device(
                type=mcu_type,
                id=serial,
                chipset=mcu.chipset,
                state=present.state,
                fw=application,
                detail={
                    "type": mcu_type,
                    "serial": serial,
                    "chipset": mcu.chipset,
                    "fw": application,
                    "force": force,
                },
            ),
            stop_services=stop_services.for_mcu(self.paths, mcu, settings, families),
        )

        # Last gate. Covers a running print *and* any other klipper activity -
        # homing, QGL, a macro - because stopping klipper mid-motion is just as
        # destructive as interrupting a print, and print_stats alone misses it.
        from ...service import assert_printer_idle

        assert_printer_idle(
            settings, activity=self._printer_activity, force=force, reporter=self._log_reporter
        )

        def run(ctx) -> dict[str, Any]:
            # The batch loop, for one target: it stops the units, writes, waits
            # for the board to come back and restarts them. No cancel is
            # threaded into the write - interrupting flashtool leaves half an
            # image on the board.
            state_holder: dict[str, Any] = {}

            def on_ready(reporter: Any) -> None:
                state_holder["klippy_state"] = self._await_klippy_ready(reporter)

            errors: list[UpdaterError] = []
            flashers.write_all(
                self._bench(self.settings()),
                [target],
                ctx,
                on_ready=on_ready,
                errors=errors,
            )
            if errors:
                raise errors[0]
            return {
                "type": mcu_type,
                "serial": serial,
                "fw_bin": flashers.artifact_path(target),
                "klippy_state": state_holder.get("klippy_state"),
            }

        job = runner.submit("flash", {"name": mcu_type, "serial": serial}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _cmake_flash(
        self,
        args: dict,
        name: str,
        runner: Any,
        settings: Settings,
    ) -> dict[str, Any]:
        """Write one configured CMake UF2 through its firmware helper."""
        serial_arg = args.get("serial") or args.get("id")
        if not serial_arg:
            raise RpcError("'serial' is required", ERR_INVALID_PARAMS)
        serial = str(serial_arg)
        force = bool(args.get("force"))

        reg = self.registry()
        mcu_type = reg.resolve_declared_serial(serial, name)

        from ...providers import cmake as cmake_mod

        target_type = cmake_mod.load(self.paths)[mcu_type]
        families = firmware.load(self.paths)
        family = firmware.resolve(self.paths, target_type.firmware, families)
        helper = helpers.for_name(family.helper, family=family.name)

        fw_bin = self.paths.uf2_file(mcu_type, target_type.firmware)
        if not os.path.exists(fw_bin):
            raise RpcError(
                f"no built firmware for {mcu_type} at {fw_bin}. Build it first.",
                data={
                    "code": "no_artifact",
                    "message": "firmware has not been built",
                    "data": {"type": mcu_type, "path": fw_bin},
                },
            )

        # The provisioned serial is the durable identity. Roadrunner's USB
        # descriptor is not a chipset string, so constrain only by exact serial
        # here; the configured helper performs its own protocol confirmation
        # after services release the port.
        from ...devices import find_device

        present = find_device(self.paths, "", serial)
        if present is None:
            raise RpcError(
                f"{serial} is not attached. Is it plugged in and powered?",
                data={
                    "code": "device_not_found",
                    "message": "board is not on the bus",
                    "data": {"serial": serial},
                },
            )

        units = stop_services.for_cmake(self.paths, target_type, settings, families)
        # The family decides who writes this board. A family whose list or
        # helper cannot is a NoFlasherError here, before a job exists.
        target = flashers.select(
            self.paths,
            family,
            flashers.Device(
                type=mcu_type,
                id=serial,
                chipset=target_type.chipset,
                state=present.state,
                fw=family.name,
            ),
            helper,
            stop_services=units,
        )

        from ...service import assert_printer_idle

        assert_printer_idle(
            settings,
            activity=self._printer_activity,
            force=force,
            reporter=self._log_reporter,
        )

        def run(ctx) -> dict[str, Any]:
            state_holder: dict[str, Any] = {}

            def on_ready(reporter: Any) -> None:
                state_holder["klippy_state"] = self._await_klippy_ready(reporter)

            settings_now = self.settings()
            result = flashers.write_all(
                self._bench(settings_now), [target], ctx, on_ready=on_ready
            )

            if result["failures"]:
                raise FlashError(
                    result["failures"][0]["error"], type=mcu_type, serial=serial
                )

            return {
                "type": mcu_type,
                "serial": serial,
                "fw_bin": flashers.artifact_path(target),
                "klippy_state": state_holder.get("klippy_state"),
            }

        job = runner.submit("flash", {"name": mcu_type, "serial": serial}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _flash_can(
        self, args: dict, uuid: str, name: Any, runner: Any, settings: Settings
    ) -> dict[str, Any]:
        """`fw.flash {uuid}` - the CAN identity form.

        Same refusal ordering as the serial path above, up to where a uuid's
        lack of a chipset-segment identity forces a difference: there is no
        by-id equivalent of "is this specific uuid on the bus right now" to
        check synchronously (see `flashers.flashtool` - finding out
        *is* the flash attempt, via its own per-interface trial), so only "no
        CAN interface exists on this host at all" is refused up front; a uuid
        that simply does not answer is discovered inside the job.

        Routes through `flashers.select_device` and the same `write_all`
        batch machinery `flash_all`/`update_all` use for a CAN board, rather
        than a second hand-written stop/write/wait sequence - one target,
        one flasher, the loop already written for a batch of one.
        """
        force = bool(args.get("force"))
        reg = self.registry()
        # resolve_uuid raises unknown_uuid / ambiguous_uuid / uuid_tracked_elsewhere.
        mcu_type = reg.resolve_uuid(uuid, str(name) if name else None)
        mcu = reg.get(mcu_type)
        families = firmware.load(self.paths)
        application = mcu.application(families)

        # Refused here only when the build staged nothing at all. A build that
        # staged some other kind than a listed flasher takes is selection's to
        # refuse, by kind and with the fix named.
        fw_bin = self.paths.bin_file(mcu_type, application)
        family = firmware.resolve(self.paths, application, families)
        if not providers.staged(self.paths, mcu_type, family).artifacts:
            raise RpcError(
                f"no built firmware for {mcu_type} at {fw_bin}. Build it first.",
                data={
                    "code": "no_artifact",
                    "message": "firmware has not been built",
                    "data": {"type": mcu_type, "path": fw_bin},
                },
            )

        cross = self.canbus_info().get(uuid.lower())
        interface = cross.get("interface") if cross is not None else None
        if interface is None:
            from ...discovery.canbus import list_can_interfaces

            interfaces = list_can_interfaces(self.paths)
        else:
            interfaces = [interface]
        if not interfaces:
            raise RpcError(
                f"no CAN interface is present on this host, so {uuid} cannot be "
                f"reached. Is a USB-CAN adapter connected?",
                data={
                    "code": "device_not_found",
                    "message": "no CAN interface present on this host",
                    "data": {"uuid": uuid},
                },
            )

        # A Klipper mapping chooses one configured bus; config silence leaves
        # the flasher to try every currently-present CAN interface.
        bridge = cross.get("bridge") if cross is not None else None

        board = {
            "type": mcu_type,
            "uuid": uuid,
            "chipset": mcu.chipset,
            "fw": application,
            "force": force,
            "bridge": bridge,
            "interface": interface,
        }
        target = flashers.select_device(
            self.paths,
            families,
            flashers.Device(
                type=mcu_type,
                id=uuid,
                chipset=mcu.chipset,
                state=(
                    "klipper"
                    if cross is not None and cross.get("version") is not None
                    else "unknown"
                ),
                fw=application,
                kind=flashers.KIND_CANBUS,
                detail=board,
            ),
            stop_services=stop_services.for_mcu(self.paths, mcu, settings, families),
        )

        from ...service import assert_printer_idle

        assert_printer_idle(
            settings, activity=self._printer_activity, force=force, reporter=self._log_reporter
        )

        def run(ctx) -> dict[str, Any]:
            # `on_ready` here, not `_do_flash_all`'s hardcoded one - a
            # single-board `fw.flash` job has always surfaced `klippy_state`
            # in its own result, and `write_all`'s own on_ready return is
            # deliberately discarded, so this captures it locally instead.
            state_holder: dict[str, Any] = {}

            def on_ready(reporter: Any) -> None:
                state_holder["klippy_state"] = self._await_klippy_ready(reporter)

            result = flashers.write_all(
                self._bench(self.settings()), [target], ctx, on_ready=on_ready
            )
            if result["failures"]:
                # A batch reports failures rather than raising per-device -
                # right for a fleet sweep, wrong for a job that promised its
                # caller a single board's own success or failure.
                raise FlashError(
                    result["failures"][0]["error"], type=mcu_type, uuid=uuid
                )
            return {
                "type": mcu_type,
                "uuid": uuid,
                "fw_bin": fw_bin,
                "klippy_state": state_holder.get("klippy_state"),
            }

        job = runner.submit("flash", {"name": mcu_type, "uuid": uuid}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _pio_flash(self, args: dict) -> dict[str, Any]:
        """Write one PlatformIO env to the devices configured for it.

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
        types = self.pio_types()
        if name not in types:
            raise RpcError(
                f"no PlatformIO type '{name}' is configured.",
                data={"code": "unknown_type", "message": "no such type",
                      "data": {"name": name, "known": sorted(types)}},
            )
        display = types[name]

        # Read the devices NOW, while Klipper can still answer.
        listed = self.device_list({})
        # Either spelling of the one identity. `port` is what this call has
        # always taken; `id` is the uniform slot, and either can name the
        # configured path, printer.cfg's burned-in device id, or the identity
        # the firmware itself reported. The path stays exact because it is a
        # POSIX filesystem path; only identities are compared case-insensitively.
        wanted = args.get("port") or args.get("id")
        want = None if wanted is None else str(wanted)
        targets = [
            d
            for d in listed["displays"]
            if d["present"]
            and (
                want is None
                or want == d["configured_path"]
                or want.lower() in {i.lower() for i in (d["device_id"], d["reported_id"]) if i}
            )
        ]
        if not targets:
            raise RpcError(
                "no device is reachable to flash. Check that the configured ports "
                "exist - fw.device.list shows which are missing.",
                data={
                    "code": "nothing_to_do",
                    "message": "no reachable displays",
                    "data": {"displays": listed["displays"], "reachable": listed["reachable"]},
                },
            )

        from ...service import assert_printer_idle

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
        units = stop_services.for_platformio(self.paths, display, settings)
        families = firmware.load(self.paths)
        screens, refused = flashers.select_each(
            self.paths,
            families,
            [
                (
                    flashers.Device(
                        type=display.name,
                        id=s["configured_path"],
                        chipset="",
                        state=inventory.STATE_UNKNOWN,
                        fw=display.firmware,
                        kind=flashers.KIND_SCREEN,
                        detail={"display": display, "screen": s},
                    ),
                    units,
                )
                for s in targets
            ],
        )

        def run(ctx) -> dict[str, Any]:
            result = self._do_flash_all(ctx, screens, refused=refused)
            # Projected back onto this method's own documented shape rather
            # than leaking the uniform one. The batch says `type`/`id`; a
            # display caller has always been told `name`/`port`, and `id` for a
            # screen *is* its configured port.
            named = {d["configured_path"]: d["name"] for d in targets}
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

        * only **untracked** devices - anything already in the registry is left
          exactly as it is. Not filtered to Katapult: both install routes erase
          the old application now, but a board bootloadered by an older version
          or by hand can still chain-load straight past Katapult and turn up
          running that firmware instead - the pairing-key match below is what
          actually identifies it;
        * only an **unambiguous** match, for the same reason
          `flashers.dfu_util.DfuUtil.scan_candidates` refuses to name a
          colliding board: the DFU serial is derived by a sum and two boards
          could in principle share one;
        * only a pairing **within its TTL**, so a board found in a drawer next
          month is the stranger it has become;
        * only if the type still **exists**, since it can have been removed;
        * and the pairing is **consumed**, so it can never act twice.

        **Two candidate keys, not one.** STM32's `dfu_serial_for` is a real,
        derived transformation of the running serial - it never equals the raw
        serial itself. RP2040 has no such derivation (see the id-collision
        caveat in docs/agent-api.md's `fw.bootsel.scan` section): the boot
        ROM's flash-chip id is *assumed*, unverified, to be the same string
        Katapult later runs under as the full canonical hardware serial.
        Interface suffixes belong only to the transport path, and a hardware
        serial may legitimately contain a hyphen, so the candidate is never
        shortened. If that assumption is wrong this candidate simply never
        matches an entry - nothing is ever recorded under a bare running UID
        for an STM32 board either, so trying it for every device is harmless
        in both directions.

        Returns what it adopted, for the log - a registry edit nobody can see
        happening is the thing to avoid.
        """
        from ...devices import dfu_serial_for, find_untracked
        from ...flashers.pairings import Pairings

        pairings = Pairings(self.paths, ttl=self.PAIRING_TTL)
        if not pairings.all():
            return []

        reg = self.registry()
        untracked = find_untracked(self.paths, reg.all_serials())
        if not untracked:
            pairings.prune()
            return []

        def candidate_keys(serial: str) -> list[str]:
            derived = dfu_serial_for(serial)
            return [derived, serial] if derived else [serial]

        # Which known keys map to more than one board on the bus. Cheap, and it
        # is the only way a wrong adoption could happen.
        seen: dict[str, int] = {}
        for device in untracked:
            for key in candidate_keys(device.serial):
                seen[key] = seen.get(key, 0) + 1

        adopted: list[dict[str, str]] = []
        for device in untracked:
            mcu_type = None
            used_key = None
            for key in candidate_keys(device.serial):
                if seen.get(key, 0) != 1:
                    continue
                found = pairings.type_for(key)
                if found:
                    mcu_type, used_key = found, key
                    break
            if not mcu_type or used_key is None or mcu_type not in reg.names():
                continue
            try:
                with Registry.mutate(self.paths, f"adopt {device.serial} as {mcu_type}") as live:
                    if not live.add_serial(mcu_type, device.serial):
                        continue
            except UpdaterError as exc:
                if self._log is not None:
                    self._log.warning(f"could not adopt {device.serial} as {mcu_type}: {exc}")
                continue

            pairings.forget(used_key)
            adopted.append({"type": mcu_type, "serial": device.serial, "pairing_key": used_key})
            if self._log is not None:
                self._log.info(
                    f"adopted {device.serial} as {mcu_type} - it is the board whose "
                    f"bootloader was installed as {mcu_type} (pairing key {used_key})"
                )

        pairings.prune()
        if adopted:
            self._changed()
        return adopted

    def _tracked_boards(self) -> list[flashers.TrackedBoard]:
        """Every tracked serial in the type list, of every builder - what a
        scan names its finds by. Lenient: a scan is a diagnosis, and a config
        problem is reported elsewhere."""
        from ... import typelist
        from ...flashers import TrackedBoard

        entries, _families = typelist.read_config(self.paths)
        return [TrackedBoard(e.name, s, e.chipset) for e in entries for s in e.serials]

    def _candidate_report(self, scanner: flashers.CandidateScanner) -> dict[str, Any]:
        """One `CandidateScanner`'s scan, as its wire result."""
        scan = scanner.scan_candidates(
            self.paths, tracked=self._tracked_boards(), reporter=self._log_reporter
        )
        return scan.to_json()

    @staticmethod
    def _scanner_named(name: str) -> flashers.CandidateScanner:
        """The flasher `name` as the `CandidateScanner` a scan method runs."""
        scanner = flashers.candidate_scanner(flashers.by_name(name))
        if scanner is None:
            raise RuntimeError(f"{name} is not a CandidateScanner")
        return scanner

    def dfu_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in DFU mode, and can this agent actually open it?
        Reports rather than raises; the reasons and where each sends the user
        live on `flashers.dfu_util.DfuUtil.scan_candidates`."""
        return self._candidate_report(self._scanner_named("dfu_util"))

    def bootsel_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in BOOTSEL, and can this agent actually write it?
        Reports rather than raises; see `flashers.bootsel.Bootsel.scan_candidates`."""
        return self._candidate_report(self._scanner_named("bootsel"))

    #: How long to wait for a freshly-flashed board to come back on the bus.
    #: A class attribute so tests can shrink it without patching a call site,
    #: matching KLIPPY_READY_TIMEOUT and friends.
    ADD_MCU_REENUMERATE_TIMEOUT = float(REENUMERATE_TIMEOUT)

    def add_mcu_start(self, args: dict) -> dict[str, Any]:
        """Put a type's first image on a bare board, then report what appeared
        on the bus.

        The first image is the type's bootloader (Katapult) when it has one, and
        otherwise its own application - `flashers.flash.install_family`. A
        type with `katapult_installed: false` gets its Klipper build written
        directly, which has to start at the start of flash; one built for a
        bootloader offset is refused here, before a job exists.

        The one new method the guided flow needs. Adopting the result is
        `fw.serial.add` and putting Klipper on a bootloadered board is
        `fw.flash` - both already exist, and wrapping them here would be a
        second implementation to keep in step with the first.

        **The install family's `flashers:` list picks the mechanism**
        (`flashers.first_install`), never the builder or a chipset prefix. That
        flasher's own `CandidateScanner` finds the board, and the post-write
        wait is keyed on the USB port the scan saw. A bare board has no
        identity to adopt yet, so the bus is snapshotted first and diffed
        afterwards, rather than taking a serial as an argument.

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

        from ... import typelist
        from ...artifacts import KIND_BIN, KIND_UF2
        from ...config import McuType
        from ...errors import UnknownTypeError
        from ...flashers.flash import refuse_unbootable_first_image

        # The type list, not the kconfig Registry: a cmake or PIO type is a
        # type like any other, and its flashers - never its builder - say
        # whether a bare board of it can be set up.
        entries = {e.name: e for e in typelist.load(self.paths)}
        entry = entries.get(name)
        if entry is None:
            raise UnknownTypeError(
                f"MCU type '{name}' does not exist.", type=name, known=sorted(entries)
            )
        families = firmware.load(self.paths)
        choice = flashers.first_install(entry, families)
        if choice.flasher is None:
            raise RpcError(
                choice.reason or f"{name} cannot be set up from a bare board.",
                data={
                    "code": "unsupported_chipset",
                    "message": "nothing on this type's install family can set up a bare board",
                    "data": {
                        "type": name,
                        "chipset": entry.chipset,
                        "fw": choice.fw or None,
                        "flashers": (
                            list(families[choice.fw].flashers) if choice.fw in families else []
                        ),
                    },
                },
            )
        flasher = flashers.by_name(choice.flasher)
        scanner = flashers.candidate_scanner(flasher)
        if scanner is None:  # first_install only ever names a scanner
            raise RuntimeError(f"{choice.flasher} is not a CandidateScanner")

        # What goes on the board first: the bootloader, or with none the
        # application itself. Every path and message below follows from it.
        install = choice.fw
        family = families[install]
        bare = flashers.Device(
            type=name,
            id="",
            chipset=entry.chipset,
            state=choice.state,
            fw=install,
            kind=flashers.KIND_BARE,
        )
        # A build made earlier, which is exactly what `providers.staged`
        # describes - the same staged set `fw.flash` chooses from. The CLI's
        # add-mcu hands over what it has just built instead, since that is the
        # image it means to write. docs/decisions.md ("First install is gated
        # by flashers") records why neither is to be "fixed" to match the
        # other.
        staged = providers.staged(self.paths, name, family)
        picked = flashers.resolve(family, bare, None, staged)
        if picked is None:
            kind = flasher.accepts[0]
            kconfig = family.builder == firmware.DEFAULT_BUILDER
            path = (
                (self.paths.uf2_file if kind == KIND_UF2 else self.paths.bin_file)(name, install)
                if kconfig
                else None
            )
            if path is not None and os.path.exists(path):
                advice = (
                    f"The .{kind} on disk predates build records that list what a "
                    f"build made, so it may be older than the rest - rebuild "
                    f"{install} for {name} once."
                )
            elif family.bootloader:
                advice = (
                    "Build it first - this flow installs the bootloader, so the "
                    "bootloader has to exist."
                )
            elif kconfig and kind == KIND_UF2 and any(a.kind == KIND_BIN for a in staged.artifacts):
                # An RP2040 Klipper build makes a .bin only for an offset, so
                # building again as configured makes the same .bin again.
                advice = (
                    f"Its build made a .bin, which an RP2040 build does only for a "
                    f"bootloader offset. Rebuild {name} with no bootloader offset "
                    f"(Bootloader offset: No bootloader)."
                )
            else:
                advice = "Build it first."
            raise RpcError(
                f"no built {install} .{kind} for {name}. {advice}",
                data={
                    "code": "no_artifact",
                    "message": f"{install} has not been built for this type",
                    "data": {"type": name, "fw": install, "path": path},
                },
            )
        resolved_flasher, artifact = picked
        if resolved_flasher.name != scanner.name:
            # `resolve` walks the family's own `flashers:` list independently
            # of `first_install`'s scanner choice above - a family that lists
            # more than one flasher able to write a bare device of this state
            # could otherwise stage an artifact for one flasher while the scan
            # (and the port/pairing key it produced) belongs to another.
            # Refused here, before a job exists, the same way an unstaged
            # artifact is.
            raise RpcError(
                f"{name}'s staged {install} would be written by "
                f"{resolved_flasher.label}, not {scanner.name}, which found the "
                f"board. Nothing was written.",
                data={
                    "code": "no_artifact",
                    "message": "the staged artifact resolves to a different "
                    "flasher than the one that scanned the board",
                    "data": {
                        "type": name,
                        "fw": install,
                        "scanned_flasher": scanner.name,
                        "resolved_flasher": resolved_flasher.name,
                    },
                },
            )
        if not family.bootloader:
            # Nothing below an application boots it, so one built for an offset
            # is refused now - synchronously, like no_artifact, not in a job.
            refuse_unbootable_first_image(self.paths, name, install, artifact)
        # Katapult's saved .config says where BOOTSEL erases the old
        # application. An application image replaces what boots, so has none.
        boot_config = self.paths.config_file(name, install) if family.bootloader else None

        # Which board, decided here rather than in the job, so an ambiguous bus is
        # a synchronous refusal the caller can act on instead of a job that dies.
        scan_result = scanner.scan_candidates(
            self.paths, tracked=self._tracked_boards(), reporter=self._log_reporter
        )
        scan = scan_result.to_json()
        target = args.get("dfu_serial")
        if target is not None:
            # Only a DFU device carries a `serial` to name it by; naming one on
            # any other flasher's scan finds nothing, and says so.
            target = str(target)
            chosen = next((d for d in scan["devices"] if d.get("serial") == target), None)
            if chosen is None:
                raise RpcError(
                    f"no board with serial {target} is in DFU mode.",
                    data={
                        "code": "device_not_found",
                        "message": "the named DFU device is not attached",
                        "data": {"dfu_serial": target, "devices": scan["devices"]},
                    },
                )
            # The caller named this one device among however many are on the
            # bus, so its own port/id are trustworthy regardless of count -
            # unlike the implicit pick below.
            port: str | None = chosen.get("port") or None
            bootsel_id: str | None = chosen.get("id") or None
        elif not scan["ready"]:
            raise RpcError(
                scan["message"] or f"no board is ready for {scanner.name}.",
                data={
                    "code": f"{scanner.candidate_prefix}_{scan['reason']}",
                    "message": scan["message"],
                    "data": {"devices": scan["devices"], "reason": scan["reason"]},
                },
            )
        else:
            chosen = scan["devices"][0]
            # `Bootsel.scan_candidates` gates `ready` on the mount count, not
            # the device count, so a second, unmounted board can leave `scan`
            # with two devices while still reporting `ready`. `devices[0]` is
            # sorted by by-id name, not by which one is mounted, so it need
            # not be the board the write actually goes to. `CandidateScan.port`
            # already refuses to name a port unless exactly one device is in
            # the scan; `bootsel_id` - the pairing key `adopt_paired` later acts
            # on - gets the same guard, or a wrong-board pairing gets recorded.
            port = scan_result.port
            bootsel_id = (chosen.get("id") or None) if len(scan_result.devices) == 1 else None
        # Wire names kept from when there were two branches: a DFU device has
        # a `serial`, a BOOTSEL one an `id`, and each is null for the other.
        dfu_serial: str | None = chosen.get("serial") or None

        # Every serial actually on the bus right now - NOT "everything untracked".
        #
        # The distinction matters: a board being re-bootloadered is often already
        # tracked, sitting offline because it had no firmware. Baselining
        # on untracked-only meant it came back, was correctly excluded as tracked,
        # and the job reported "no new device appeared" - sending the user to hunt
        # for a failure when the flash had worked perfectly.
        from ...devices import scan as scan_bus

        before = {d.serial for d in scan_bus(self.paths)}

        def run(ctx) -> dict[str, Any]:
            from ...devices import wait_for_new_device
            from ...flashers.flash import flash_initial_bootloader

            ctx.step(
                f"Flashing {install} onto the {choice.state.upper()} board for {name}", 0, 2
            )
            flash_initial_bootloader(
                self.paths,
                self.settings(),
                entry.chipset,
                artifact.path if artifact.kind == KIND_BIN else None,
                fw=install,
                mcu_type=name,
                state=choice.state,
                uf2_bin=artifact.path if artifact.kind == KIND_UF2 else None,
                # Where BOOTSEL erases the old application; DFU mass-erases.
                katapult_config=boot_config,
                reporter=ctx.reporter,
                target_serial=dfu_serial,
            )

            # Recorded here - after the write, BEFORE the wait - because the wait
            # timing out is precisely the case this covers. A board on a marginal
            # port, or unplugged and brought back tomorrow, then still arrives
            # with its intent attached rather than as an anonymous stranger.
            pairing_key = bootsel_id or dfu_serial
            if pairing_key:
                from ...flashers.pairings import Pairings

                Pairings(self.paths).record(pairing_key, name)

            # Keyed on the USB port the scan saw: across a reboot the port is
            # the durable key, not the serial or the by-id chipset segment (a
            # Roadrunner comes back as `usb-Vylyne_Roadrunner_...`). Not
            # filtered to what was written either - an image that survives an
            # install chain-loads past it and reappears running that instead.
            ctx.step("Waiting for the board to re-enumerate", 1, 2)
            if port is None:
                ctx.reporter(
                    "warn",
                    f"{scanner.name} could not say which USB port the board is on, "
                    f"so any new board that appears is reported as this one.",
                )
            appeared = wait_for_new_device(
                self.paths,
                before,
                port=port,
                timeout=self.ADD_MCU_REENUMERATE_TIMEOUT,
            )

            # Split by whether the type list already tracks it. Both mean the
            # flash worked; only one leaves anything for the user to do.
            tracked = {s for e in typelist.read_config(self.paths)[0] for s in e.serials}
            candidates = [d for d in appeared if d.serial not in tracked]
            already = [d for d in appeared if d.serial in tracked]

            ctx.step(f"Found {len(appeared)} board(s)", 2, 2)
            where = f"in {install}" if family.bootloader else f"running {install}"
            then = (
                f" Flash {McuType(name=name, firmwares=list(entry.firmwares)).application(families)}"
                f" onto it when ready."
                if family.bootloader
                else ""
            )
            for device in already:
                ctx.reporter(
                    "info",
                    f"{device.serial} is back {where} and already tracked - "
                    f"nothing to adopt.{then}",
                )
            if not appeared:
                # Not raised: the write may well have succeeded and the board may
                # simply be slow or on a marginal port. Saying what to look at
                # beats failing a job that probably worked.
                ctx.reporter(
                    "warn",
                    f"No board appeared {where}. Check `ls /dev/serial/by-id/` - "
                    "if it is there, adopt it directly with fw.serial.add.",
                )
            return {
                "type": name,
                "chipset": entry.chipset,
                # What was written: the bootloader, or the application on a
                # type that has none. Additive; no API_VERSION change.
                "fw": install,
                # Which flasher first_install chose, and the port the wait was
                # keyed on (null when the scan could not trace one). Additive.
                "flasher": scanner.name,
                "port": port,
                "dfu_serial": dfu_serial,
                "bootsel_id": bootsel_id,
                "candidates": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in candidates
                ],
                # Appeared, but the type list already tracks it - the
                # re-bootloader case. Distinct from an empty result, which
                # means nothing came back at all.
                "already_tracked": [
                    {"serial": d.serial, "path": d.path, "state": d.state} for d in already
                ],
            }

        job = runner.submit(
            "add_mcu", {"name": name, "dfu_serial": dfu_serial, "bootsel_id": bootsel_id}, run
        )
        return {
            "job_id": job.id,
            "job": job.to_dict(),
            "type": name,
            "dfu_serial": dfu_serial,
            "bootsel_id": bootsel_id,
        }

    def add_mcu_scan(self, args: dict) -> dict[str, Any]:
        """The scan `fw.add_mcu.start` would run for this type, as a report -
        what the wizard calls instead of choosing between `fw.dfu.scan` and
        `fw.bootsel.scan` itself. Reports rather than raises, like those two;
        a type nothing can set up is `reason: "no_scanner"` with
        `first_install`'s reason as its message."""
        from ... import typelist
        from ...errors import UnknownTypeError

        name = self._require_str(args, "name")
        entries, families = typelist.read_config(self.paths)
        entry = next((e for e in entries if e.name == name), None)
        if entry is None:
            raise UnknownTypeError(
                f"MCU type '{name}' does not exist.",
                type=name,
                known=sorted(e.name for e in entries),
            )
        choice = flashers.first_install(entry, families)
        scanner = (
            flashers.candidate_scanner(flashers.by_name(choice.flasher))
            if choice.flasher
            else None
        )
        if scanner is None:
            return {
                "devices": [],
                "count": 0,
                "ready": False,
                "reason": "no_scanner",
                "message": choice.reason,
                "flasher": None,
            }
        return {**self._candidate_report(scanner), "flasher": scanner.name}
