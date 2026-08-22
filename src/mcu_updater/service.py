"""Stopping and starting services around a flash, and surviving a crash.

Leaving klipper stopped is the one genuinely bad outcome this tool can produce:
the printer is dead until someone notices and SSHes in. Defence is layered.

1. ``services_stopped()`` restores state in a ``finally``.
2. ``MoonrakerService.start()`` falls back to systemd if Moonraker has gone away
   between the stop and the start.
3. A **journal** file records "we stopped these" before stopping, so a process
   that dies outright can be reconciled on next startup.
4. The systemd unit carries an ``ExecStopPost`` net for klipper specifically,
   for the case where even that doesn't run.

This module owns 1-3.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from typing import Any

from .build import Reporter, null_reporter
from .errors import PrintInProgressError, ServiceControlError
from .paths import Paths
from .settings import Settings


class ServiceController:
    """Interface for the three backends below."""

    name: str = "klipper"

    def stop(self, reporter: Reporter = null_reporter) -> None:
        raise NotImplementedError

    def start(self, reporter: Reporter = null_reporter) -> None:
        raise NotImplementedError

    def is_active(self) -> bool:
        raise NotImplementedError


class SystemdService(ServiceController):
    """`sudo systemctl <action> <unit>`, as the original did.

    Needs passwordless sudo for this one unit (install.sh offers to set that up).
    Used by the CLI, which has no Moonraker connection, and as the agent's
    last-resort fallback.
    """

    def __init__(self, name: str = "klipper") -> None:
        self.name = name

    def _run(self, action: str) -> int:
        try:
            res = subprocess.run(
                ["sudo", "systemctl", action, self.name],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return 1
        return res.returncode

    def stop(self, reporter: Reporter = null_reporter) -> None:
        reporter("info", f"Stopping {self.name} service...")
        if self._run("stop") != 0:
            reporter("warn", f"systemctl stop {self.name} did not report success")

    def start(self, reporter: Reporter = null_reporter) -> None:
        reporter("info", f"Starting {self.name} service...")
        if self._run("start") != 0:
            reporter("warn", f"systemctl start {self.name} did not report success")

    def is_active(self) -> bool:
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "--quiet", self.name], timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0


class MoonrakerService(ServiceController):
    """Service control via Moonraker's `machine.services.*` API.

    Preferred from the agent: no sudo needed (Moonraker is already privileged and
    gates this on moonraker.asvc, where klipper is allowed by default), and
    Moonraker emits notify_service_state_changed so Mainsail honestly shows
    "klipper: stopped" instead of dumping the user into a "lost connection"
    error state for the minutes a flash takes.

    ``call`` is injected - it's the agent's JSON-RPC peer - so this module stays
    free of transport concerns and testable.
    """

    def __init__(
        self,
        call: Callable[[str, dict], Any],
        name: str = "klipper",
        fallback: ServiceController | None = None,
    ) -> None:
        self._call = call
        self.name = name
        self.fallback = fallback if fallback is not None else SystemdService(name)

    def stop(self, reporter: Reporter = null_reporter) -> None:
        reporter("info", f"Stopping {self.name} via Moonraker...")
        try:
            self._call("machine.services.stop", {"service": self.name})
            return
        except Exception as exc:  # noqa: BLE001 - any failure means try systemd
            reporter("warn", f"Moonraker stop failed ({exc}); falling back to systemctl")
        self.fallback.stop(reporter)

    def start(self, reporter: Reporter = null_reporter) -> None:
        """Belt and braces: this must succeed or the printer stays dead.

        If Moonraker died between our stop and this start, the API is
        unreachable, so every failure falls through to systemd.
        """
        reporter("info", f"Starting {self.name} via Moonraker...")
        try:
            self._call("machine.services.start", {"service": self.name})
            return
        except Exception as exc:  # noqa: BLE001
            reporter("warn", f"Moonraker start failed ({exc}); falling back to systemctl")
        self.fallback.start(reporter)

    def is_active(self) -> bool:
        return self.fallback.is_active()


class NullService(ServiceController):
    """Narrates instead of acting. Used by dry-run and tests.

    Tracks its own state rather than always claiming to be active, because
    services_stopped() verifies that a stop actually took effect - a NullService
    that lied about being up would make every dry run fail that check.
    """

    def __init__(self, name: str = "klipper") -> None:
        self.name = name
        self.actions: list[str] = []
        self._active = True

    def stop(self, reporter: Reporter = null_reporter) -> None:
        self.actions.append("stop")
        self._active = False
        reporter("info", f"[dry-run] would stop {self.name}")

    def start(self, reporter: Reporter = null_reporter) -> None:
        self.actions.append("start")
        self._active = True
        reporter("info", f"[dry-run] would start {self.name}")

    def is_active(self) -> bool:
        return self._active


def make_controller(
    settings: Settings,
    *,
    call: Callable[[str, dict], Any] | None = None,
    name: str | None = None,
) -> ServiceController:
    """Pick a backend for one unit. `call` is only available inside the agent.

    `name` names the unit; every caller that actually stops something now
    passes one explicitly, resolved from `stop_services` - there is no
    per-install default unit name to fall back to any more (that was
    `Settings.service`, retired in favour of the list). `None` falls back to
    the literal `"klipper"`, for a caller that only wants *a* controller and
    does not care which unit, e.g. a status probe with nothing configured yet.

    The *backend* choice is deliberately shared: a dry run must stay a dry
    run for every unit, or a rehearsal would stop a real service. A unit
    Moonraker refuses to touch because it is not in moonraker.asvc simply
    fails to stop, which `services_stopped` now treats as fatal rather than
    best-effort - see its own docstring.
    """
    unit = name or "klipper"
    if settings.dry_run or settings.service_backend == "null":
        return NullService(unit)
    if settings.service_backend == "moonraker" and call is not None:
        return MoonrakerService(call, unit)
    return SystemdService(unit)


# --------------------------------------------------------------------------
# crash journal
# --------------------------------------------------------------------------


class Journal:
    """Records that we stopped some services, so a crash can be reconciled.

    Written once, before any of them are stopped, and cleared after they are
    all back. If the process is SIGKILLed in between, the next startup finds
    the entry and restarts whatever it names.

    The whole list is written up front rather than growing one entry at a
    time as each stop happens - a SIGKILL between two stops in the same batch
    must not leave the later ones unrecorded. `reconcile` restarting a unit
    that, it turns out, was never actually stopped is a safe no-op (`start`
    on an already-running unit), so naming one early costs nothing.
    """

    def __init__(self, paths: Paths) -> None:
        self.path = paths.journal_file

    def record_stop(self, services: list[str], label: str) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "services": list(services),
                    "label": label,
                    "at": time.time(),
                    "pid": os.getpid(),
                },
                fh,
            )
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def pending(self) -> dict[str, Any] | None:
        """The pending entry, or `None` if there isn't a usable one.

        A journal written by an older version of this tool names a single
        `"service"` rather than a `"services"` list - wrapped here into the
        same shape so `reconcile` never has to know the file predates this.
        """
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        services = data.get("services")
        if isinstance(services, list) and services:
            return data
        legacy = data.get("service")
        if legacy:
            return {**data, "services": [legacy]}
        return None


def reconcile(
    paths: Paths,
    controller_for: Callable[[str], ServiceController],
    *,
    reporter: Reporter = null_reporter,
) -> bool:
    """On startup: if a previous run died with services stopped, start them.

    Restarts in reverse order, the same order `services_stopped` itself
    restarts in - so a batch that stopped [klipper, knomi_serial] comes back
    knomi_serial then klipper, however the crash unwound it.

    `controller_for` builds a controller for one unit name; the journal now
    names an arbitrary list rather than always klipper, so there is no single
    controller to hand in any more.

    Returns True if it took action.
    """
    journal = Journal(paths)
    entry = journal.pending()
    if entry is None:
        return False

    services = list(entry.get("services") or [])
    age = time.time() - float(entry.get("at") or 0)
    reporter(
        "warn",
        f"found an unfinished operation from {age:.0f}s ago "
        f"({entry.get('label', 'unknown')}) that stopped "
        f"{', '.join(services)}. Making sure they're running again.",
    )
    for name in reversed(services):
        svc = controller_for(name)
        if not svc.is_active():
            svc.start(reporter)
    journal.clear()
    return True


#: How long to wait for a stop to actually take effect before giving up.
STOP_VERIFY_TIMEOUT = 20.0


@contextlib.contextmanager
def paused(
    svc: ServiceController | None, *, reporter: Reporter = null_reporter
) -> Iterator[None]:
    """Stop a *secondary* service for the duration of the block. Best effort.

    For things that merely contend for a port rather than making the operation
    unsafe - the knomi_serial watcher opens any port that appears and has not
    been identified yet, and pyserial's exclusive open is an advisory flock, so
    if a port turns up at the moment esptool wants it one of them loses.

    Two deliberate differences from `services_stopped`, both because this
    service is not the dangerous one:

    **It never journals.** Unlike the old single-slot journal this used to be
    the workaround for, the journal now holds a list and could name this
    service perfectly well - but a best-effort pause that was never verified
    stopped is not a fact worth a crash recovery acting on, so this still
    does not record one. Anything that genuinely must come back after a
    SIGKILL belongs in `stop_services` and `services_stopped` instead, not
    here.

    **It never verifies, and never raises.** If the watcher will not stop, the
    worst case is the flake this avoids: the upload fails cleanly and a retry
    works. Refusing to flash at all would be a worse outcome than the problem.

    **A unit that is not there is not an error.** `is_active` is false for a
    unit systemd has never heard of, so a host without the watcher installed
    takes this path and says nothing. `None` means the caller has nothing to
    pause at all, which is the same nothing rather than a special case it has
    to write around.
    """
    if svc is None or not svc.is_active():
        yield
        return

    svc.stop(reporter)
    try:
        yield
    finally:
        svc.start(reporter)


@contextlib.contextmanager
def services_stopped(
    paths: Paths,
    controllers: list[ServiceController],
    label: str,
    *,
    reporter: Reporter = null_reporter,
    verify: bool = True,
    verify_timeout: float = STOP_VERIFY_TIMEOUT,
) -> Iterator[None]:
    """Stop every controller in order, then put them back in reverse.

    The generalisation of the old single-service `klipper_stopped`: a write
    now names an ordered list of units - klipper first by convention, then
    whatever else it needs the port from - and this stops each in turn,
    verifies each, and restarts every one it touched in reverse order in a
    `finally`, whatever happened above. Order is config order; that is the
    whole reason it is a list and not a set.

    Idempotent per controller, same as before: one already stopped on entry
    is left stopped on exit rather than being helpfully started - it was
    stopped for a reason, whether by the user or by an earlier stage of the
    same batch.

    **The stop is verified for every controller, not just the first one.** A
    unit that will not go down - no passwordless sudo, not in
    moonraker.asvc, a wedged service - raises before the write, because a
    firmware write racing a service that still holds the port is not a clean
    "port busy" failure, it is a corrupted flash.
    """
    journal = Journal(paths)
    to_stop = [svc for svc in controllers if svc.is_active()]
    for svc in controllers:
        if svc not in to_stop:
            reporter("info", f"{svc.name} is already stopped - leaving it that way.")

    if to_stop:
        journal.record_stop([svc.name for svc in to_stop], label)

    def _restart_all() -> None:
        # Belt and braces: this is the single most important loop in the
        # project. Whatever happened above, every service it touched has to
        # come back - including one whose own stop never verified, since it
        # may yet have complied and must not be left ambiguous.
        try:
            for svc in reversed(to_stop):
                svc.start(reporter)
        finally:
            journal.clear()

    for svc in to_stop:
        svc.stop(reporter)
        if verify:
            deadline = time.monotonic() + verify_timeout
            while svc.is_active():
                if time.monotonic() >= deadline:
                    _restart_all()
                    raise ServiceControlError(
                        f"could not stop '{svc.name}' within {verify_timeout:.0f}s - "
                        f"refusing to continue, because flashing while it holds the "
                        f"serial port is unsafe. Under the moonraker backend, check "
                        f"that '{svc.name}' is listed in ~/printer_data/moonraker.asvc. "
                        f"Under the systemd backend, check passwordless sudo is set up "
                        f"for it - add these lines to /etc/sudoers.d/mcu-updater:\n"
                        f"    <user> ALL=(root) NOPASSWD: /bin/systemctl stop {svc.name}\n"
                        f"    <user> ALL=(root) NOPASSWD: /bin/systemctl start {svc.name}",
                        service=svc.name,
                    )
                time.sleep(0.5)
            reporter("info", f"{svc.name} confirmed stopped.")

    try:
        yield
    finally:
        _restart_all()


def assert_printer_idle(
    settings: Settings,
    *,
    activity: Callable[[], dict] | None = None,
    force: bool = False,
    reporter: Reporter = null_reporter,
) -> None:
    """Refuse to flash while the printer is doing anything.

    **`print_stats.state` is not enough.** It only tracks a virtual_sdcard print
    job, so it reads "standby" throughout a manual home, a quad-gantry-level, or
    any macro run from the console - and stopping klipper mid-motion there is just
    as destructive as interrupting a print. `idle_timeout.state` is the field that
    means "klipper is executing commands", and it goes to "Printing" for all of
    those.

    `activity` returns {"print_state": ..., "idle_state": ...}, either value
    possibly None. It is only available where there's a Moonraker connection, so
    the CLI passes None and this becomes a no-op there.
    """
    if force or settings.allow_flash_while_printing or activity is None:
        return
    try:
        state = activity() or {}
    except Exception as exc:  # noqa: BLE001 - never let the check itself break a flash
        reporter("warn", f"could not determine printer state ({exc}); continuing")
        return

    print_state = state.get("print_state")
    if print_state in ("printing", "paused"):
        raise PrintInProgressError(
            f"a print is currently {print_state} - refusing to flash. Cancel the print "
            f"first, or pass force if you are certain.",
            state=print_state,
            reason="print",
        )

    # Catches homing, QGL, bed mesh, a macro, a manual move - anything where
    # klipper is mid-command and yanking the MCU out would leave it shut down.
    if state.get("idle_state") == "Printing":
        raise PrintInProgressError(
            "the printer is busy executing commands (homing, QGL, a macro, or a "
            "move) - refusing to flash. Wait for it to finish, or pass force if you "
            "are certain.",
            state=print_state,
            idle_state="Printing",
            reason="busy",
        )
