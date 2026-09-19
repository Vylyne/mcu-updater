"""Provisioning a board that appears, for families that asked for it.

Spec section 10. Opt in, on the bus watcher's thread, never from a status
poll, and never twice for one board - an unprovisioned serial stops matching
the moment provisioning succeeds.
"""

from __future__ import annotations

import pytest

from mcu_updater import firmware, helpers, lock, typelist
from mcu_updater.discovery.roadrunner import RoadrunnerError
from mcu_updater.errors import BusyError, ConfigCorruptError
from mcu_updater.helpers import registry as helpers_registry

from .conftest import write_settings

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-9F2C11A45E7B0033"


class _FakeRoadrunner:
    """Stands in for the roadrunner helper: the real one talks to a board."""

    name = "roadrunner"
    label = "Roadrunner"

    def __init__(
        self,
        error: Exception | None = None,
        *,
        remedy: str = "provision",
    ) -> None:
        self.calls: list[str] = []
        self.error = error
        self.remedy = remedy

    def is_trackable(self, serial: str) -> helpers.TrackVerdict:
        if serial.startswith("RR-UNPROVISIONED-"):
            return helpers.TrackVerdict(
                ok=False,
                reason="fake helper says provision this identity first",
                remedy=self.remedy,
            )
        return helpers.TrackVerdict(ok=True)

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        if self.error is not None:
            raise self.error
        return PROVISIONED


class _ProvisionerOnly:
    name = "roadrunner"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        return PROVISIONED


class _TrackableOnly:
    name = "roadrunner"

    def is_trackable(self, serial: str) -> helpers.TrackVerdict:
        return helpers.TrackVerdict(ok=False, remedy="provision")


def _family(
    paths, *, auto: str, name: str = "roadrunner", helper: str = "roadrunner"
) -> None:
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware {name}]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            f"helper: {helper}\n"
            f"{auto}"
        )


@pytest.fixture
def rr(monkeypatch):
    helper = _FakeRoadrunner()
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    return helper


def _sweep(*serials: str) -> dict[str, object]:
    return {serial: object() for serial in serials}


def test_a_family_that_did_not_ask_provisions_nothing(paths, rr):
    """The default: a firmware section does not authorise a hardware write."""
    from mcu_updater import provisioning

    _family(paths, auto="")

    assert (
        provisioning.auto_provision(
            paths, _sweep(UNPROVISIONED), may_provision=True
        )
        is False
    )
    assert rr.calls == []


def test_an_opted_in_family_provisions_a_board_that_appeared(paths, rr):
    from mcu_updater import provisioning

    lines: list[tuple[str, str]] = []
    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(
        paths,
        _sweep(UNPROVISIONED, "usb-Klipper_stm32-if00"),
        may_provision=True,
        reporter=lambda stream, line: lines.append((stream, line)),
    )

    assert rr.calls == [UNPROVISIONED], "only the helper-rejected identity is written"
    assert retry is False
    assert ("info", f"Provisioned {UNPROVISIONED} as {PROVISIONED}.") in lines


def test_the_default_policy_refuses_an_unattended_hardware_write(paths, rr):
    from mcu_updater import provisioning

    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(paths, _sweep(UNPROVISIONED))

    assert retry is False, "a deployment refusal is not a transient retry"
    assert rr.calls == []


def test_a_verdict_with_an_unknown_remedy_is_not_actionable(paths, monkeypatch):
    from mcu_updater import provisioning

    helper = _FakeRoadrunner(remedy="replace-board")
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    _family(paths, auto="auto_provision: true\n")

    provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED), may_provision=True
    )

    assert helper.calls == []


@pytest.mark.parametrize("helper", [_ProvisionerOnly(), _TrackableOnly()])
def test_auto_provision_requires_both_helper_capabilities(paths, monkeypatch, helper):
    from mcu_updater import provisioning

    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    _family(paths, auto="auto_provision: true\n")

    provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED), may_provision=True
    )

    assert getattr(helper, "calls", []) == []


def test_a_held_lock_is_skipped_and_asked_for_again(paths, rr, monkeypatch):
    """The unchanged board needs an explicit retry on the next watcher poll."""
    from mcu_updater import provisioning

    def _busy(paths, label):
        raise BusyError(f"busy: {label}")

    monkeypatch.setattr(lock, "exclusive", _busy)
    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED), may_provision=True
    )

    assert retry is True
    assert rr.calls == []


def test_a_board_that_refuses_its_probe_is_reported_and_not_retried(
    paths, monkeypatch
):
    from mcu_updater import provisioning

    helper = _FakeRoadrunner(error=RoadrunnerError("INFO did not confirm the device"))
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    lines: list[tuple[str, str]] = []
    _family(paths, auto="auto_provision: true\n")

    retry = provisioning.auto_provision(
        paths,
        _sweep(UNPROVISIONED),
        may_provision=True,
        reporter=lambda stream, line: lines.append((stream, line)),
    )

    assert retry is False
    assert any(stream == "warn" and UNPROVISIONED in line for stream, line in lines)


def test_one_board_is_provisioned_exactly_once(paths, rr):
    from mcu_updater import provisioning

    _family(paths, auto="auto_provision: true\n")

    provisioning.auto_provision(
        paths, _sweep(UNPROVISIONED), may_provision=True
    )
    provisioning.auto_provision(paths, _sweep(PROVISIONED), may_provision=True)

    assert rr.calls == [UNPROVISIONED]


def test_one_sweep_provisions_a_shared_helper_serial_once(paths, rr):
    from mcu_updater import provisioning

    _family(paths, auto="auto_provision: true\n", name="roadrunner-main")
    _family(paths, auto="auto_provision: true\n", name="roadrunner-feature")

    provisioning.auto_provision(paths, _sweep(UNPROVISIONED), may_provision=True)

    assert rr.calls == [UNPROVISIONED]


def test_one_sweep_provisions_a_serial_claimed_by_different_helpers_once(
    paths, rr, monkeypatch
):
    from mcu_updater import provisioning

    other = _FakeRoadrunner()
    other.name = "other"
    monkeypatch.setitem(helpers_registry._BY_NAME, "other", other)
    _family(paths, auto="auto_provision: true\n", name="roadrunner-main")
    _family(
        paths,
        auto="auto_provision: true\n",
        name="roadrunner-feature",
        helper="other",
    )

    provisioning.auto_provision(paths, _sweep(UNPROVISIONED), may_provision=True)

    assert rr.calls == [UNPROVISIONED]
    assert other.calls == []


def test_a_trackability_failure_does_not_skip_later_families(paths, rr, monkeypatch):
    from mcu_updater import provisioning

    lines: list[tuple[str, str]] = []
    broken = _FakeRoadrunner()
    broken.name = "broken"
    monkeypatch.setitem(helpers_registry._BY_NAME, "broken", broken)
    monkeypatch.setattr(
        broken,
        "is_trackable",
        lambda serial: (_ for _ in ()).throw(RuntimeError("broken helper")),
    )
    _family(
        paths,
        auto="auto_provision: true\n",
        name="broken-family",
        helper="broken",
    )
    _family(paths, auto="auto_provision: true\n", name="roadrunner-family")

    provisioning.auto_provision(
        paths,
        _sweep(UNPROVISIONED),
        may_provision=True,
        reporter=lambda stream, line: lines.append((stream, line)),
    )

    assert broken.calls == []
    assert rr.calls == [UNPROVISIONED]
    assert ("warn", "[firmware broken-family]: broken helper") in lines


def test_the_key_is_refused_on_a_family_that_cannot_auto_provision(paths):
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware knomi]\n"
            "builder: platformio\n"
            "flashers: esptool\n"
            "helper: knomi_serial\n"
            "auto_provision: true\n"
        )

    entries, families = typelist.read_config(paths)
    with pytest.raises(ConfigCorruptError) as excinfo:
        typelist.validate(entries, families, path=paths.main_config)

    assert excinfo.value.code == "config_corrupt"
    assert excinfo.value.data["key"] == "auto_provision"
    assert excinfo.value.data["families"] == ["knomi"]


def test_the_provisioning_helper_list_matches_both_capabilities():
    """Config validation must promise only what the runtime loop can do."""
    from mcu_updater.helpers.registry import HELPERS

    assert set(firmware.PROVISIONING_HELPERS) == {
        helper.name
        for helper in HELPERS
        if helpers.provisioner(helper) is not None
        and helpers.trackable(helper) is not None
    }


# --- the watcher --------------------------------------------------------------


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, name: str, payload: object) -> None:
        self.events.append((name, payload))


class _Dev:
    """The three attributes `_fingerprint` and the handler read."""

    def __init__(self, serial: str, fw: str = "Klipper", chipset: str = "stm32"):
        self.serial, self.fw, self.chipset = serial, fw, chipset


def _watcher(paths, monkeypatch, found, handler=None):
    from mcu_updater.agent.events import BusWatcher

    monkeypatch.setattr("mcu_updater.devices.scan", lambda p: found)
    return BusWatcher(
        paths, _Emitter(), serialize=lambda devices: [], on_change=handler
    )


def test_the_watcher_hands_the_handler_the_sweep_it_found(paths, monkeypatch):
    found = [
        _Dev("usb-Klipper_stm32-if00"),
        _Dev(UNPROVISIONED, "Roadrunner", "rp2040"),
    ]
    seen: list[dict] = []
    watcher = _watcher(paths, monkeypatch, found, lambda devices: seen.append(devices))

    watcher._poll()

    assert [sorted(sweep) for sweep in seen] == [
        sorted(device.serial for device in found)
    ]


def test_a_handler_asking_for_a_retry_runs_again_on_an_unchanged_bus(
    paths, monkeypatch
):
    calls: list[dict] = []

    def handler(devices):
        calls.append(devices)
        return len(calls) == 1

    watcher = _watcher(paths, monkeypatch, [_Dev("S1")], handler)

    watcher._poll()
    watcher._poll()
    watcher._poll()

    assert len(calls) == 2, "asked again once, then left alone"


def test_an_unchanged_bus_emits_nothing_even_when_the_handler_reran(
    paths, monkeypatch
):
    watcher = _watcher(paths, monkeypatch, [_Dev("S1")], lambda devices: True)

    watcher._poll()
    watcher._poll()

    assert [name for name, _ in watcher.emitter.events] == ["bus"]


# --- the service boundary -----------------------------------------------------


def test_a_flashing_disabled_agent_auto_provisions_nothing(paths, rr, monkeypatch):
    """The service must pass the same deployment gate its API advertises."""
    from mcu_updater.agent.service import Agent

    _family(paths, auto="auto_provision: true\n")
    write_settings(paths, enable_flashing=False)
    agent = Agent(paths)
    adopted: list[bool] = []
    monkeypatch.setattr(agent.api, "adopt_paired", lambda: adopted.append(True))

    retry = agent._on_bus_change(_sweep(UNPROVISIONED))

    assert retry is False
    assert adopted == [True], "late adoption is a registry write and still runs"
    assert rr.calls == []


def test_a_flashing_enabled_agent_runs_opted_in_auto_provision(paths, rr, monkeypatch):
    from mcu_updater.agent.service import Agent

    _family(paths, auto="auto_provision: true\n")
    write_settings(paths, enable_flashing=True)
    agent = Agent(paths)
    monkeypatch.setattr(agent.api, "adopt_paired", lambda: [])

    retry = agent._on_bus_change(_sweep(UNPROVISIONED))

    assert retry is False
    assert rr.calls == [UNPROVISIONED]


def test_a_status_poll_never_reaches_auto_provision(paths, monkeypatch):
    """Ruling 14: status remains read-only even when provisioning exists."""
    from mcu_updater import provisioning
    from mcu_updater.agent.service import Agent

    agent = Agent(paths)

    calls: list[object] = []

    def _record(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(provisioning, "auto_provision", _record)

    agent.api.dispatch("fw.status")

    assert calls == []
