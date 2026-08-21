"""fw.flash, fw.dfu.scan, fw.add_mcu.start -- writing firmware to a board."""

from __future__ import annotations

import os
from typing import Any

from ... import firmware, flashers, providers
from ...config import Registry
from ...errors import (
    DfuPermissionError,
    ToolMissingError,
    UpdaterError,
)
from ...paths import REENUMERATE_TIMEOUT
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
            from ...service import klipper_stopped, make_controller

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
        from ...devices import KATAPULT_FW_NAME, dfu_serial_for, find_untracked
        from ...flashers.pairings import Pairings

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
        from ...devices import scan as scan_bus

        before = {d.serial for d in scan_bus(self.paths)}

        def run(ctx) -> dict[str, Any]:
            from ...devices import KATAPULT_FW_NAME, wait_for_new_device
            from ...flashers.flash import flash_initial_bootloader

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
                from ...flashers.pairings import Pairings

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
