"""Tracking an unprovisioned board provisions it first.

Spec section 11. The two steps have the same precondition - the board is here,
untracked, and nobody else has the bus - so the only thing splitting them buys
is an order to get wrong.
"""

from __future__ import annotations

import sys

import pytest

from mcu_updater import helpers, tracking
from mcu_updater.config import Registry
from mcu_updater.errors import (
    BusyError,
    ConfigCorruptError,
    UnknownTypeError,
    UnprovisionedSerialError,
)
from mcu_updater.helpers import registry as helpers_registry
from mcu_updater.lock import exclusive

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-9F2C11A45E7B0033"

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="flock is unavailable on Windows; the lock degrades to a no-op there "
    "(dev machine only - the tool runs on Linux)",
)


class _FakeRoadrunner:
    """Stands in for the roadrunner helper: the real one talks to a board."""

    name = "roadrunner"
    label = "Roadrunner"

    def __init__(self, result: str = PROVISIONED, error: Exception | None = None):
        self.calls: list[str] = []
        self.result = result
        self.error = error

    def is_unprovisioned(self, serial: str) -> bool:
        return serial.startswith("RR-UNPROVISIONED-")

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def rr(paths, monkeypatch):
    """A `[type roadrunner]` whose family helper can provision."""
    helper = _FakeRoadrunner()
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware roadrunner]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "helper: roadrunner\n"
            "\n[type roadrunner]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
        )
    return helper


def test_tracking_an_unprovisioned_board_provisions_it_and_tracks_the_result(
    paths, rr
):
    tracked = tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == [UNPROVISIONED]
    assert tracked.serial == PROVISIONED
    assert tracked.provisioned_from == UNPROVISIONED
    assert tracked.added is True
    assert tracked.chipset == "rp2040"
    serials = Registry.load(paths).declared_serials("roadrunner")
    assert serials == [PROVISIONED], "the diagnostic identity must never be saved"


def test_tracking_an_already_provisioned_board_provisions_nothing(paths, rr):
    tracked = tracking.add_serial(paths, "roadrunner", PROVISIONED)

    assert rr.calls == []
    assert tracked.provisioned_from is None
    assert tracked.serial == PROVISIONED


def test_an_unknown_type_is_refused_before_any_board_is_written_to(paths, rr):
    """A typo in the type name must not provision hardware. The type is read
    first, and provisioning is the step after."""
    with pytest.raises(UnknownTypeError):
        tracking.add_serial(paths, "roadrunnr", UNPROVISIONED)

    assert rr.calls == []


@posix_only
def test_a_held_lock_refuses_the_track_and_never_retries(paths, rr):
    """Ruling 13. Provisioning is an irreversible write to a board; a caller
    that queued behind a running flash would perform it at a moment nobody
    chose."""
    with exclusive(paths, "something else entirely"):
        with pytest.raises(BusyError):
            tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == []
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_a_family_with_no_helper_still_refuses_the_diagnostic_serial(paths):
    """The old refusal, where it is still correct: with nothing able to
    provision, "provision it first" is the only useful thing to say.

    A `[firmware roadrunner]` section with no `helper:` line, not a deleted
    registry entry: deleting a *registered* name that a family still declares
    would reproduce the misspelt-helper case, which `helpers.for_name` refuses
    loudly on this write path (Task 6 dispatch correction 1) rather than
    falling back to "no provisioner"."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware roadrunner]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "\n[type roadrunner]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
        )

    with pytest.raises(UnprovisionedSerialError):
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_a_misconfigured_helper_name_refuses_loudly_rather_than_provisioning_nothing(
    paths, rr, monkeypatch
):
    """Task 6 dispatch correction 1: on a write path, an unregistered
    `helper:` name must not be swallowed into "no provisioner" - it is
    refused by name, the same as any other write-path misconfiguration."""
    monkeypatch.delitem(helpers_registry._BY_NAME, "roadrunner")

    with pytest.raises(ConfigCorruptError):
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == []
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_the_real_roadrunner_helper_offers_provisioning(paths):
    helper = helpers.for_name("roadrunner", family="roadrunner")
    prov = helpers.provisioner(helper)

    assert prov is not None
    assert prov.is_unprovisioned(UNPROVISIONED) is True
    assert prov.is_unprovisioned(PROVISIONED) is False


def test_a_helper_without_the_capability_offers_none(paths):
    assert helpers.provisioner(None) is None
    assert helpers.provisioner(helpers.for_name("knomi_serial", family="knomi")) is None
