"""fw.build_all / fw.flash_all / fw.update_all -- fleet-wide operations."""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from ... import firmware, flashers, providers, stop_services
from ...build import read_sidecar
from ...config import Registry
from ...devices import (
    STATE_OFFLINE,
    device_state,
)
from ...errors import (
    OperationCancelled,
    UpdaterError,
)
from ...settings import Settings
from ..rpc import ERR_INVALID_PARAMS, RpcError
from ._api import _Base


def _board_target(board: dict) -> flashers.FlashTarget:
    """One entry from `_boards_to_flash`/`_canbus_boards_to_flash`, as
    something a batch can write.

    A one-line alias so the bulk callers say the same thing, and so the board
    selection's dict shape - which is on the wire - stays the selection's
    business rather than leaking a second copy into each of them.

    `stop_services` rides inside the same dict (set by whichever selection
    produced it, resolved once per type) rather than as a second argument
    here, since `board` is already the one place that selection's per-target
    facts live.

    Branches on which identity key the dict carries - `uuid` for a CAN board,
    `serial` for a by-id one - rather than a `kind` field, since the two
    selections never overlap for one board (a type's `serials:` and
    `canbus_uuids:` name different physical devices).
    """
    stop_services = tuple(board.get("stop_services") or ())
    if "uuid" in board:
        return flashers.flashtool_can.target_for(board, board["uuid"], stop_services=stop_services)
    return flashers.flashtool.target_for(board, stop_services=stop_services)


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


class BulkMixin(_Base):
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
        only: str | None = None,
        fw: str | None = None,
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

    def _boards_to_flash(self, reg: Registry, scope: str, only: str | None = None) -> list[dict]:
        """Which boards a flash_all should write, with the reason for each.

        Offline boards are never included: a flash needs the device on the bus, so
        including them would only produce a guaranteed failure partway through a
        batch that has already stopped Klipper.
        """
        from ...build import FlashLog, git_head

        versions = self.mcu_info()
        families = firmware.load(self.paths)
        settings = self.settings()
        # Resolved per type below: a board running cartographer must be
        # compared against its own fork, not upstream klipper.
        flashlog = FlashLog(self.paths)

        out: list[dict] = []
        for name in reg.names():
            if only is not None and name != only:
                continue
            mcu = reg.get(name)
            application = mcu.application(families)
            if not os.path.exists(self.paths.bin_file(name, application)):
                continue
            fw_head = git_head(
                firmware.resolve(self.paths, application, families).source_dir(self.paths)
            )
            # Once per type, not per serial - every board of this type shares
            # the same resolved list.
            units = stop_services.for_mcu(self.paths, mcu, settings, families)
            artifact_sha = (read_sidecar(self.paths, name, application) or {}).get("bin_sha256")
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
                            "fw": application,
                            "stop_services": list(units),
                            "state": state,
                            "reason": info["reason"] if scope != "all" else "forced",
                        }
                    )
        return out

    def _canbus_boards_to_flash(
        self, reg: Registry, scope: str, only: str | None = None
    ) -> list[dict]:
        """The CAN counterpart to `_boards_to_flash`: which tracked
        `canbus_uuids:` a flash_all should write, with the reason for each.

        Included, not excluded - the user's explicit call for CAN in bulk
        operations, at the cost of a slower liveness check than the by-id
        scan's instant presence test. Two tiers, cheaper first:

        - **Cross-reference hit, online**: `canbus_info()`'s `configfile`
          join found a `[mcu <name>] canbus_uuid:` declaration for this uuid
          and that mcu object reports a version - judged exactly like a
          tracked serial, via the same `flash_state`.
        - **Everything else falls to the unconditional-inclusion tier**:
          printer.cfg does not declare this uuid at all, Klipper could not be
          asked, or it *is* declared but reports no live version. That last
          case is deliberately **not** treated as `STATE_OFFLINE` the way a
          missing by-id device is: absence of `mcu_version` here covers both
          "genuinely offline" and "sitting in Katapult, unreachable to
          klippy" indistinguishably - and the latter is `flash_state`'s own
          `in_bootloader`, "the strongest possible signal that it wants
          firmware". Excluding it would silently drop exactly the board most
          in need of a flash. `flash_katapult_can`'s single `-f` invocation
          handles a native CAN node in either state itself (see its own
          docstring), so only the attempt can actually tell them apart - a
          timeout on every interface is what "genuinely offline" looks like,
          an accepted, stated cost rather than a reason to guess here.
        """
        from ...build import FlashLog, git_head

        canbus = self.canbus_info()
        families = firmware.load(self.paths)
        settings = self.settings()
        flashlog = FlashLog(self.paths)

        out: list[dict] = []
        for name in reg.names():
            if only is not None and name != only:
                continue
            mcu = reg.get(name)
            if not mcu.canbus_uuids:
                continue
            application = mcu.application(families)
            if not os.path.exists(self.paths.bin_file(name, application)):
                continue
            fw_head = git_head(
                firmware.resolve(self.paths, application, families).source_dir(self.paths)
            )
            units = stop_services.for_mcu(self.paths, mcu, settings, families)
            artifact_sha = (read_sidecar(self.paths, name, application) or {}).get("bin_sha256")
            for uuid in mcu.canbus_uuids:
                # configfile.settings lowercases everything; canbus_uuids: is
                # stored verbatim (canbus_add never normalises case), so this
                # is the join key both sides actually agree on.
                cross = canbus.get(uuid.lower())
                if cross is not None and cross["version"] is not None:
                    # Preferred tier: online, and Klipper says what it's
                    # running - judged exactly like a tracked serial.
                    info = self.flash_state(
                        uuid,
                        {uuid: {"version": cross["version"], "mcu": cross["mcu"]}},
                        fw_head,
                        artifact_sha=artifact_sha,
                        flashlog=flashlog,
                    )
                    if scope != "all" and info["needs_flash"] is not True:
                        continue
                    out.append(
                        {
                            "type": name,
                            "uuid": uuid,
                            "chipset": mcu.chipset,
                            "fw": application,
                            "stop_services": list(units),
                            "state": "klipper",
                            "bridge": cross["bridge"],
                            "reason": info["reason"] if scope != "all" else "forced",
                        }
                    )
                    continue
                # Fallback tier: no configfile cross-reference at all, or one
                # that cannot tell "offline" apart from "in Katapult" (see
                # the method docstring). Never excluded either way - `bridge`
                # still carries through when config at least named the mcu
                # object, even though its liveness could not be judged.
                out.append(
                    {
                        "type": name,
                        "uuid": uuid,
                        "chipset": mcu.chipset,
                        "fw": application,
                        "stop_services": list(units),
                        "state": "unknown",
                        "bridge": cross["bridge"] if cross is not None else None,
                        "reason": "forced" if scope == "all" else "unknown_liveness",
                    }
                )
        return out

    def _screens_to_flash(
        self, scope: str, only: str | None = None
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
        known = self.pio_types()
        settings = self.settings()
        out: list[flashers.FlashTarget] = []
        for payload in self.pio_status():
            if only is not None and payload["name"] != only:
                continue
            if not payload["has_firmware"]:
                continue
            # The live object, not one rebuilt from the payload: `to_json` is a
            # wire projection and reversing it is the thing this codebase keeps
            # deciding not to do.
            display = known[payload["name"]]
            units = stop_services.for_display(self.paths, display, settings)
            for screen in payload["screens"]:
                if not screen["present"]:
                    continue
                status = self._screen_device_status(screen)
                if scope != "all" and status.needs_flash is not True:
                    continue
                target = flashers.esptool.target_for(display, screen, stop_services=units)
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

        built: list[dict[str, str | None]] = []
        failures: list[dict[str, str | None]] = []
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
        from ...service import ServiceController, make_controller

        def controller(name: str | None = None) -> ServiceController:
            return make_controller(settings, call=self._call_for_service, name=name)

        return flashers.Bench(
            paths=self.paths, settings=settings, controller=controller
        )

    def _do_flash_all(
        self, ctx: Any, targets: list[flashers.FlashTarget]
    ) -> dict[str, Any]:
        """Write every selected device. The loop itself is `flashers.write_all`.

        What is left here is the half that is the agent's: the bench this host
        presents, and the readiness check that asks Moonraker whether klippy came
        back - and issues a FIRMWARE_RESTART when it came back in an error state.
        The CLI has nobody to ask, which is exactly why that is a hook rather
        than a step in the loop.
        """
        return flashers.write_all(
            self._bench(self.settings()),
            targets,
            ctx,
            on_ready=self._await_klippy_ready,
        )

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
        if only in reg.names() or only in self.pio_types():
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

        # By-id and CAN both, and neither excludes the other - a type may
        # legitimately track both `serials:` and `canbus_uuids:`.
        boards = self._boards_to_flash(reg, scope, only) + self._canbus_boards_to_flash(
            reg, scope, only
        )
        # Read now, while Klipper can still answer - the same constraint
        # `_pio_flash` has always had, and the reason this is selection
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
        from ...service import assert_printer_idle

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

        Both halves cover every provider, because each is literally `build_all`
        and `flash_all` - which is the composition paying off rather than a
        special case. A screen gained the build half when the provider seam
        landed and the flash half when the flasher seam did, both times without
        this method being edited.
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

        from ...service import assert_printer_idle

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
            reg_now = self.registry()
            boards = self._boards_to_flash(reg_now, scope, only) + self._canbus_boards_to_flash(
                reg_now, scope, only
            )
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
