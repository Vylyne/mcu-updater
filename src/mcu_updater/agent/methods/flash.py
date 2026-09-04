"""fw.flash, fw.dfu.scan, fw.add_mcu.start -- writing firmware to a board."""

from __future__ import annotations

import os
from typing import Any

from ... import firmware, flashers, providers, stop_services
from ...config import Registry
from ...errors import (
    DfuPermissionError,
    FlashError,
    ToolMissingError,
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

        name = args.get("name")
        if name and self._provider_of(str(name)) == providers.PlatformIO.name:
            return self._pio_flash(args)

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
        application = mcu.application(firmware.load(self.paths))

        fw_bin = self.paths.bin_file(mcu_type, application)
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
        from ...devices import find_device

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
        from ...service import assert_printer_idle

        assert_printer_idle(
            settings, activity=self._printer_activity, force=force, reporter=self._log_reporter
        )

        def run(ctx) -> dict[str, Any]:
            from ...devices import KLIPPER_FW_NAME, wait_for_device
            from ...errors import BootloaderTimeoutError
            from ...flashers.flash import flash_katapult
            from ...service import make_controller, services_stopped

            settings_now = self.settings()
            units = stop_services.for_mcu(self.paths, mcu, settings_now)
            controllers = [
                make_controller(settings_now, call=self._call_for_service, name=unit)
                for unit in units
            ]
            ctx.step(f"Stopping {', '.join(units) or 'nothing'}", 0, 4)
            with services_stopped(
                self.paths, controllers, f"flash {serial}", reporter=ctx.reporter
            ):
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
                    fw=application,
                    reporter=ctx.reporter,
                    force=force,
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

                ctx.step(f"Restarting {', '.join(units) or 'nothing'}", 3, 4)

            # services_stopped has started them by now. Being *active* is
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

        Routes through `flashtool.target_for` and the same `write_all`
        batch machinery `flash_all`/`update_all` use for a CAN board, rather
        than a second hand-written stop/write/wait sequence - one target,
        one flasher, the loop already written for a batch of one.
        """
        force = bool(args.get("force"))
        reg = self.registry()
        # resolve_uuid raises unknown_uuid / ambiguous_uuid / uuid_tracked_elsewhere.
        mcu_type = reg.resolve_uuid(uuid, str(name) if name else None)
        mcu = reg.get(mcu_type)
        application = mcu.application(firmware.load(self.paths))

        fw_bin = self.paths.bin_file(mcu_type, application)
        if not os.path.exists(fw_bin):
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

        from ...service import assert_printer_idle

        assert_printer_idle(
            settings, activity=self._printer_activity, force=force, reporter=self._log_reporter
        )

        # A Klipper mapping chooses one configured bus; config silence leaves
        # the flasher to try every currently-present CAN interface.
        bridge = cross.get("bridge") if cross is not None else None

        units = stop_services.for_mcu(self.paths, mcu, settings)
        target = flashers.flashtool.target_for(
            {
                "type": mcu_type,
                "uuid": uuid,
                "chipset": mcu.chipset,
                "fw": application,
                "force": force,
                "bridge": bridge,
                "interface": interface,
            },
            stop_services=units,
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
        # `id` is the uniform slot, `port` what this call has always taken.
        wanted = args.get("port") or args.get("id")
        targets = [
            d
            for d in listed["displays"]
            if d["present"] and (wanted is None or d["configured_path"] == str(wanted))
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
        units = stop_services.for_display(self.paths, display, settings)
        screens = [
            flashers.esptool.target_for(display, s, stop_services=units) for s in targets
        ]

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

        * only **untracked** devices - anything already in the registry is left
          exactly as it is. Not filtered to Katapult: a board that already
          carries a valid application chain-loads straight past Katapult on its
          first boot, so it can legitimately turn up running its own firmware
          instead - the pairing-key match below is what actually identifies it;
        * only an **unambiguous** match, for the same reason `_identify_dfu`
          refuses to name a colliding board: the DFU serial is derived by a sum
          and two boards could in principle share one;
        * only a pairing **within its TTL**, so a board found in a drawer next
          month is the stranger it has become;
        * only if the type still **exists**, since it can have been removed;
        * and the pairing is **consumed**, so it can never act twice.

        **Two candidate keys, not one.** STM32's `dfu_serial_for` is a real,
        derived transformation of the running serial - it never equals the raw
        serial itself. RP2040 has no such derivation (see docs/agent-api.md's
        "RP2040 pairing identity" note): the boot ROM's flash-chip id is
        *assumed*, unverified, to be the same string Katapult later runs under
        as the full canonical hardware serial. Interface suffixes belong only
        to the transport path, and a hardware serial may legitimately contain
        a hyphen, so the candidate is never shortened. If that assumption is
        wrong this candidate simply never
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
        from ...devices import dfu_serial_for

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
        from ...devices import dfu_devices
        from ...flashers.flash import DFU_VID_PID

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

    def _identify_bootsel(self, devices: list) -> None:
        """Name the boards in BOOTSEL that we already know about.

        Unlike `_identify_dfu` there is no derivation: this assumes - unverified
        on real hardware, see docs/agent-api.md's "RP2040 pairing identity" note
        - that the boot ROM's flash-chip unique id is the same string Katapult
        later reports as its own running USB serial, so a tracked rp2040 board's
        serial can be compared to a BOOTSEL device's id directly.

        If that assumption is wrong this simply never matches - every device's
        `known_serial`/`tracked_by` stays null, exactly what a genuinely new
        board looks like, never a wrong name. Same collision guard as
        `_identify_dfu`: two known boards mapping to one id names neither.

        Tracked identities are canonical hardware serials; the by-id interface
        suffix belongs only to the transport path. Compare the full string so
        a legitimate hyphen cannot create a prefix match.
        """
        owners: dict[str, list[tuple[str, str]]] = {}
        for name, mcu in self.registry().types.items():
            if not mcu.chipset.startswith("rp2040"):
                continue
            for serial in mcu.serials:
                owners.setdefault(serial, []).append((name, serial))

        for device in devices:
            device["known_serial"] = None
            device["tracked_by"] = None
            matches = owners.get(str(device.get("id") or ""), [])
            if len(matches) == 1:
                device["tracked_by"], device["known_serial"] = matches[0]

    def bootsel_scan(self, args: dict) -> dict[str, Any]:
        """What is sitting in BOOTSEL, and can this agent actually write it?

        Mirrors `dfu_scan`'s report-don't-raise shape. Diverges where BOOTSEL
        genuinely differs: no external tool to be missing or to deny access -
        reading `/dev/disk/by-id` and a mount point is plain filesystem access,
        so there is no `no_tool`/`permission_denied` here at all.

        Readiness gates on the **mount** count, not the device count, because
        that is exactly what the write itself
        (`flashers.bootsel._find_mount`) gates on - a board present but
        unmounted is not writable regardless of how many are attached.

        ``none``
            Nothing in BOOTSEL. Hold BOOTSEL and replug the board.
        ``not_mounted``
            A board is attached but nothing mounted its volume - this host has
            no automounter. Re-run install.sh to install the udev rule.
        ``ambiguous``
            More than one RPI-RP2 volume is mounted at once. Unlike DFU there
            is no serial to pick one by - the udev rule mounts every board to
            the same fixed path - so this is a refusal to report clearly, not
            something a caller can resolve by naming a device.
        """
        from ...devices import bootsel_devices, bootsel_id_for
        from ...devices import bootsel_scan as bootsel_mounts

        present = bootsel_devices(self.paths)
        devices = [{"id": bootsel_id_for(node), "node": node} for node in present]
        self._identify_bootsel(devices)

        mounts = bootsel_mounts(self.paths)
        out: dict[str, Any] = {
            "devices": devices,
            "count": len(devices),
            "mounts": mounts,
            "mount_count": len(mounts),
            "ready": False,
            "reason": None,
            "message": None,
        }

        if not present:
            out["reason"] = self.BOOTSEL_NONE
            out["message"] = (
                "No RP2040 in BOOTSEL is attached. Hold BOOTSEL and replug the board."
            )
            return out
        if not mounts:
            out["reason"] = self.BOOTSEL_NOT_MOUNTED
            out["message"] = (
                f"An RP2040 in BOOTSEL is attached ({', '.join(present)}) but "
                f"nothing mounted its volume - this host has no automounter. "
                f"Re-run install.sh to install the udev rule, which mounts each "
                f"board under /media/<user>/BOOTSEL/by-path/<port>."
            )
            return out
        if len(mounts) > 1:
            out["reason"] = self.BOOTSEL_AMBIGUOUS
            out["message"] = (
                f"{len(mounts)} RPI-RP2 volumes are mounted at once "
                f"({', '.join(mounts)}) - which one is this board? Unplug the "
                f"others and try again."
            )
            return out

        out["ready"] = True
        return out

    #: How long to wait for a freshly-flashed board to come back as Katapult.
    #: A class attribute so tests can shrink it without patching a call site,
    #: matching KLIPPY_READY_TIMEOUT and friends.
    ADD_MCU_REENUMERATE_TIMEOUT = float(REENUMERATE_TIMEOUT)

    def add_mcu_start(self, args: dict) -> dict[str, Any]:
        """Put Katapult on a bare board, then report what appeared on the bus.

        The one new method the guided flow needs. Adopting the result is
        `fw.serial.add` and putting Klipper on it is `fw.flash` - both already
        exist, and wrapping them here would be a second implementation to keep in
        step with the first.

        **STM32 goes over DFU, RP2040 over BOOTSEL mass storage** - two
        genuinely different mechanisms sharing one method, exactly as the CLI's
        `add-mcu` already does through `flash_initial_bootloader`. Neither board
        has an identity to adopt yet: a DFU board exposes no
        `/dev/serial/by-id` name at all, and a BOOTSEL board's only identity
        (the boot ROM's flash-chip id) is not the serial it will run under. So
        both branches snapshot the bus first and diff afterwards, rather than
        taking a serial as an argument.

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

        is_bootsel = mcu.chipset.startswith("rp2040")
        if not (mcu.chipset.startswith("stm32") or is_bootsel):
            raise RpcError(
                f"{name} is {mcu.chipset}, which this flow does not support. Only "
                f"STM32 (over DFU) and RP2040 (over BOOTSEL) boards can be set up "
                f"this way.",
                data={
                    "code": "unsupported_chipset",
                    "message": "no bare-board install path for this chipset",
                    "data": {"type": name, "chipset": mcu.chipset},
                },
            )

        katapult_bin = self.paths.bin_file(name, "katapult")
        uf2_bin = self.paths.uf2_file(name, "katapult")
        artifact_path = uf2_bin if is_bootsel else katapult_bin
        if not os.path.exists(artifact_path):
            raise RpcError(
                f"no built Katapult {'.uf2' if is_bootsel else 'firmware'} for "
                f"{name}. Build it first - this flow installs the bootloader, so "
                f"the bootloader has to exist.",
                data={
                    "code": "no_artifact",
                    "message": "katapult has not been built for this type",
                    "data": {"type": name, "fw": "katapult", "path": artifact_path},
                },
            )

        # Which board, decided here rather than in the job, so an ambiguous bus is
        # a synchronous refusal the caller can act on instead of a job that dies.
        target: str | None = None  # DFU serial, when relevant
        bootsel_id: str | None = None  # boot-ROM flash-chip id, when relevant

        if is_bootsel:
            bscan = self.bootsel_scan({})
            if not bscan["ready"]:
                raise RpcError(
                    bscan["message"] or "no board is ready in BOOTSEL.",
                    data={
                        "code": f"bootsel_{bscan['reason']}",
                        "message": bscan["message"],
                        "data": {"devices": bscan["devices"], "reason": bscan["reason"]},
                    },
                )
            if bscan["devices"]:
                bootsel_id = bscan["devices"][0].get("id") or None
        else:
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
        from ...devices import scan as scan_bus

        before = {d.serial for d in scan_bus(self.paths)}

        def run(ctx) -> dict[str, Any]:
            from ...devices import wait_for_new_device
            from ...flashers.flash import flash_initial_bootloader

            label = "BOOTSEL board" if is_bootsel else "DFU board"
            ctx.step(f"Flashing Katapult onto the {label} for {name}", 0, 2)
            flash_initial_bootloader(
                self.paths,
                self.settings(),
                mcu.chipset,
                katapult_bin,
                # Unconditional, exactly like the CLI's add-mcu - ignored by the
                # DFU branch, required by BOOTSEL's.
                uf2_bin=uf2_bin,
                reporter=ctx.reporter,
                target_serial=target,
            )

            # Recorded here - after the write, BEFORE the wait - because the wait
            # timing out is precisely the case this covers. A board on a marginal
            # port, or unplugged and brought back tomorrow, then still arrives
            # with its intent attached rather than as an anonymous stranger.
            pairing_key = bootsel_id if is_bootsel else target
            if pairing_key:
                from ...flashers.pairings import Pairings

                Pairings(self.paths).record(pairing_key, name)

            # Not filtered to Katapult: a board that already carries a valid
            # application (a re-bootloadered board, say) chain-loads straight
            # past Katapult on its first boot and can legitimately reappear
            # running its own firmware instead. Chipset + "wasn't on the bus
            # before" is what actually identifies it either way.
            ctx.step("Waiting for the board to re-enumerate", 1, 2)
            appeared = wait_for_new_device(
                self.paths,
                before,
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
                "bootsel_id": bootsel_id,
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

        job = runner.submit(
            "add_mcu", {"name": name, "dfu_serial": target, "bootsel_id": bootsel_id}, run
        )
        return {
            "job_id": job.id,
            "job": job.to_dict(),
            "type": name,
            "dfu_serial": target,
            "bootsel_id": bootsel_id,
        }
