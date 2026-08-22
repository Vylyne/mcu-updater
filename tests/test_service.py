"""Service control and the crash journal.

The property under test throughout: klipper must end up running again.
"""

from __future__ import annotations

import pytest

from mcu_updater.errors import PrintInProgressError, ServiceControlError
from mcu_updater.service import (
    Journal,
    MoonrakerService,
    NullService,
    assert_printer_idle,
    make_controller,
    reconcile,
    services_stopped,
)
from mcu_updater.settings import Settings


class FakeService(NullService):
    def __init__(self, active: bool = True) -> None:
        super().__init__("klipper")
        self._active = active

    def stop(self, reporter=lambda s, line: None) -> None:
        self.actions.append("stop")
        self._active = False

    def start(self, reporter=lambda s, line: None) -> None:
        self.actions.append("start")
        self._active = True

    def is_active(self) -> bool:
        return self._active


class FailingStop(NullService):
    """Accepts the stop request but never actually goes down."""

    def stop(self, reporter=lambda s, line: None) -> None:
        self.actions.append("stop")
        # _active deliberately left True


def test_stops_and_restarts_around_the_block(paths):
    svc = FakeService(active=True)
    with services_stopped(paths, [svc], "flash"):
        assert svc.is_active() is False
    assert svc.actions == ["stop", "start"]
    assert svc.is_active() is True


def test_restarts_even_when_the_block_raises(paths):
    svc = FakeService(active=True)
    with pytest.raises(RuntimeError):
        with services_stopped(paths, [svc], "flash"):
            raise RuntimeError("flash exploded")
    assert svc.actions == ["stop", "start"]
    assert svc.is_active() is True


def test_an_already_stopped_service_is_left_stopped(paths):
    """The user stopped it for a reason; don't helpfully start it."""
    svc = FakeService(active=False)
    with services_stopped(paths, [svc], "flash"):
        pass
    assert svc.actions == []
    assert svc.is_active() is False


def test_journal_is_written_during_and_cleared_after(paths):
    journal = Journal(paths)
    svc = FakeService(active=True)
    assert journal.pending() is None

    with services_stopped(paths, [svc], "update-all"):
        entry = journal.pending()
        assert entry is not None
        assert entry["services"] == ["klipper"]
        assert entry["label"] == "update-all"

    assert journal.pending() is None


def test_journal_survives_a_hard_failure_and_is_reconciled(paths):
    """Simulates the process being SIGKILLed mid-flash."""
    Journal(paths).record_stop(["klipper"], "flash bttebb36")
    svc = FakeService(active=False)

    assert reconcile(paths, lambda name: svc) is True
    assert svc.actions == ["start"]
    assert Journal(paths).pending() is None


def test_reconcile_is_a_no_op_with_nothing_pending(paths):
    svc = FakeService(active=True)
    assert reconcile(paths, lambda name: svc) is False
    assert svc.actions == []


def test_reconcile_does_not_restart_an_already_running_service(paths):
    Journal(paths).record_stop(["klipper"], "flash")
    svc = FakeService(active=True)
    assert reconcile(paths, lambda name: svc) is True
    assert svc.actions == []  # already up; just clear the journal


def test_journal_ignores_a_corrupt_file(paths):
    with open(paths.journal_file, "w", encoding="utf-8") as fh:
        fh.write("not json")
    assert Journal(paths).pending() is None


# --------------------------------------------------------------------------
# multiple services: order, journaling, hard failure
# --------------------------------------------------------------------------


def test_multiple_services_stop_in_order_and_restart_in_reverse(paths):
    order: list[str] = []
    klipper = FakeService(active=True)
    klipper.name = "klipper"
    watcher = FakeService(active=True)
    watcher.name = "knomi_serial"
    for svc in (klipper, watcher):
        orig_stop, orig_start = svc.stop, svc.start

        def stop(reporter=lambda s, line: None, _svc=svc, _orig=orig_stop):
            order.append(f"stop {_svc.name}")
            _orig(reporter)

        def start(reporter=lambda s, line: None, _svc=svc, _orig=orig_start):
            order.append(f"start {_svc.name}")
            _orig(reporter)

        svc.stop, svc.start = stop, start

    with services_stopped(paths, [klipper, watcher], "flash"):
        pass

    assert order == ["stop klipper", "stop knomi_serial", "start knomi_serial", "start klipper"]


def test_a_service_that_will_not_stop_raises_before_the_write_runs(paths):
    """Verified-stopped, hard failure: unlike the old best-effort watcher pause,
    every controller in the list must actually go down."""
    klipper = FakeService(active=True)
    klipper.name = "klipper"
    stuck = FailingStop("knomi_serial")
    ran = False

    with pytest.raises(ServiceControlError) as exc:
        with services_stopped(paths, [klipper, stuck], "flash", verify_timeout=0.1):
            ran = True
    assert not ran, "the body must never run once a stop fails to verify"
    assert "knomi_serial" in str(exc.value)
    # Both put back, including the one that stopped cleanly.
    assert klipper.is_active() is True
    assert Journal(paths).pending() is None


def test_a_controller_already_stopped_is_not_journaled_or_touched(paths):
    klipper = FakeService(active=True)
    already_down = FakeService(active=False)
    already_down.name = "knomi_serial"

    with services_stopped(paths, [klipper, already_down], "flash"):
        pass

    assert already_down.actions == []
    assert klipper.actions == ["stop", "start"]


def test_journal_round_trips_a_list(paths):
    journal = Journal(paths)
    journal.record_stop(["klipper", "knomi_serial"], "flash")
    entry = journal.pending()
    assert entry is not None
    assert entry["services"] == ["klipper", "knomi_serial"]


def test_journal_understands_a_legacy_single_service_entry(paths):
    """Written by an older version of this tool, before the journal became a
    list. `pending()` must still recognise it so a crash mid-upgrade is still
    reconciled."""
    import json

    with open(paths.journal_file, "w", encoding="utf-8") as fh:
        json.dump({"service": "klipper", "label": "flash x", "at": 0, "pid": 1}, fh)

    entry = Journal(paths).pending()
    assert entry is not None
    assert entry["services"] == ["klipper"]


def test_reconcile_restarts_a_legacy_entry(paths):
    import json

    with open(paths.journal_file, "w", encoding="utf-8") as fh:
        json.dump({"service": "klipper", "label": "flash x", "at": 0, "pid": 1}, fh)
    svc = FakeService(active=False)

    assert reconcile(paths, lambda name: svc) is True
    assert svc.actions == ["start"]
    assert Journal(paths).pending() is None


def test_reconcile_restarts_several_in_reverse(paths):
    Journal(paths).record_stop(["klipper", "knomi_serial"], "flash")
    made: dict[str, FakeService] = {}
    order: list[str] = []

    def factory(name):
        svc = made.setdefault(name, FakeService(active=False))
        svc.name = name
        orig = svc.start

        def start(reporter=lambda s, line: None, _name=name, _orig=orig):
            order.append(_name)
            _orig(reporter)

        svc.start = start
        return svc

    assert reconcile(paths, factory) is True
    assert order == ["knomi_serial", "klipper"]


# --------------------------------------------------------------------------
# backend selection and fallback
# --------------------------------------------------------------------------


def test_dry_run_always_gets_the_null_backend(paths):
    svc = make_controller(Settings(dry_run=True, service_backend="systemd"))
    assert isinstance(svc, NullService)


def test_moonraker_backend_needs_a_call_channel(paths):
    """The CLI has no Moonraker connection, so it must fall back to systemd."""
    from mcu_updater.service import SystemdService

    svc = make_controller(Settings(service_backend="moonraker"), call=None)
    assert isinstance(svc, SystemdService)


def test_moonraker_start_falls_back_when_moonraker_is_gone():
    """If Moonraker died between our stop and start, the printer must still come back."""
    calls = []

    def broken_call(method, params):
        calls.append(method)
        raise OSError("socket closed")

    fallback = FakeService(active=False)
    svc = MoonrakerService(broken_call, "klipper", fallback=fallback)
    svc.start()

    assert calls == ["machine.services.start"]
    assert fallback.actions == ["start"]
    assert fallback.is_active() is True


def test_moonraker_stop_uses_the_api_when_it_works():
    calls = []
    fallback = FakeService(active=True)
    svc = MoonrakerService(lambda m, p: calls.append((m, p)), "klipper", fallback=fallback)
    svc.stop()
    assert calls == [("machine.services.stop", {"service": "klipper"})]
    assert fallback.actions == []


# --------------------------------------------------------------------------
# print safety gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_refuses_to_flash_during_a_print(state):
    with pytest.raises(PrintInProgressError) as exc:
        assert_printer_idle(Settings(), activity=lambda: {"print_state": state, "idle_state": "Ready"})
    assert exc.value.data["state"] == state


@pytest.mark.parametrize("state", ["standby", "complete", "cancelled", "error", None])
def test_allows_flashing_when_idle(state):
    assert_printer_idle(Settings(), activity=lambda: {"print_state": state, "idle_state": "Ready"})


def test_force_overrides_the_gate():
    assert_printer_idle(Settings(), activity=lambda: {"print_state": "printing"}, force=True)


def test_setting_overrides_the_gate():
    assert_printer_idle(
        Settings(allow_flash_while_printing=True), activity=lambda: {"print_state": "printing"}
    )


def test_no_print_state_source_is_a_no_op():
    """The CLI can't query Moonraker, so the check is best-effort there."""
    assert_printer_idle(Settings(), activity=None)


def test_a_failing_state_query_never_blocks_a_flash():
    def boom():
        raise OSError("moonraker unreachable")

    warnings = []
    assert_printer_idle(
        Settings(),
        activity=boom,
        reporter=lambda s, line: warnings.append((s, line)),
    )
    assert any(s == "warn" for s, _ in warnings)
