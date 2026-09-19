"""Tracking an unprovisioned board provisions it first.

Spec section 11. The two steps have the same precondition - the board is here,
untracked, and nobody else has the bus - so the only thing splitting them buys
is an order to get wrong.
"""

from __future__ import annotations

import pytest

from mcu_updater import helpers, lock, tracking
from mcu_updater.config import Registry
from mcu_updater.errors import (
    BusyError,
    ConfigCorruptError,
    SerialTrackedElsewhereError,
    UnknownTypeError,
    UnprovisionedSerialError,
)
from mcu_updater.helpers import registry as helpers_registry

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-9F2C11A45E7B0033"


class _FakeRoadrunner:
    """Stands in for the roadrunner helper: the real one talks to a board."""

    name = "roadrunner"
    label = "Roadrunner"

    def __init__(self, result: str = PROVISIONED, error: Exception | None = None):
        self.calls: list[str] = []
        self.result = result
        self.error = error

    def is_trackable(self, serial: str) -> helpers.TrackVerdict:
        if serial.startswith("RR-UNPROVISIONED-"):
            return helpers.TrackVerdict(
                ok=False,
                reason="fake helper says provision this identity first",
                remedy="provision",
            )
        return helpers.TrackVerdict(ok=True)

    def provision(self, paths, serial: str) -> str:
        self.calls.append(serial)
        if self.error is not None:
            raise self.error
        return self.result


class _TrackableOnly:
    """Rejects an unstable identity but deliberately cannot provision it."""

    name = "roadrunner"

    def __init__(self, *, remedy: str = "provision"):
        self.remedy = remedy

    def is_trackable(self, serial: str) -> helpers.TrackVerdict:
        return helpers.TrackVerdict(
            ok=False,
            reason="trackable-only helper refuses this identity",
            remedy=self.remedy,
        )


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


def test_a_held_lock_refuses_the_track_and_never_retries(paths, rr, monkeypatch):
    """Ruling 13. Provisioning is an irreversible write to a board; a caller
    that queued behind a running flash would perform it at a moment nobody
    chose.

    Pinned by monkeypatching `lock.exclusive` itself rather than by holding a
    real file lock from this same process: `flock` is a documented no-op on
    Windows (`test_lock.py`'s `posix_only` marker), so a real held lock would
    let this test pass without ever exercising the refusal it exists to
    catch, on the very machine that runs it. `tracking.add_serial` looks
    `exclusive` up from `mcu_updater.lock` at call time, so patching it here
    reaches the real call site without needing a real lock at all.
    """

    def _busy(paths, label):
        raise BusyError(f"busy: {label}")

    monkeypatch.setattr(lock, "exclusive", _busy)

    with pytest.raises(BusyError):
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == []
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_a_helper_without_trackable_capability_tracks_an_ordinary_serial(paths):
    """A helper without the capability has no durability opinion."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware knomi]\n"
            "source: ~/knomi\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "helper: knomi_serial\n"
            "\n[type knomi]\n"
            "firmware: knomi\n"
            "cmake_target: knomi\n"
            "chipset: rp2040\n"
        )

    helper = helpers.for_name("knomi_serial", family="knomi")
    assert helpers.trackable(helper) is None

    tracked = tracking.add_serial(paths, "knomi", "ordinary-serial")

    assert tracked.serial == "ordinary-serial"
    assert Registry.load(paths).declared_serials("knomi") == ["ordinary-serial"]


def test_a_trackable_helper_with_no_provisioner_refuses_with_its_reason(
    paths, rr, monkeypatch
):
    helper = _TrackableOnly()
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)

    with pytest.raises(UnprovisionedSerialError) as excinfo:
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert excinfo.value.message == "trackable-only helper refuses this identity"
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_an_unknown_trackability_remedy_is_a_refusal(paths, rr, monkeypatch):
    helper = _TrackableOnly(remedy="replace-board")
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)

    with pytest.raises(UnprovisionedSerialError) as excinfo:
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert excinfo.value.message == "trackable-only helper refuses this identity"
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


def test_the_real_roadrunner_helper_judges_trackability_without_hardware(paths):
    helper = helpers.for_name("roadrunner", family="roadrunner")
    judge = helpers.trackable(helper)

    assert judge is not None
    refused = judge.is_trackable(UNPROVISIONED)
    assert refused.ok is False
    assert refused.reason == (
        f"'{UNPROVISIONED}' is an unprovisioned Roadrunner's diagnostic identity, "
        "not a stable serial - provision it first (the web UI's Provision "
        "Roadrunner action, or fw.roadrunner.provision), then track the "
        "resulting RR-... serial."
    )
    assert refused.remedy == "provision"
    assert judge.is_trackable(PROVISIONED) == helpers.TrackVerdict(ok=True)


def test_a_helper_without_the_capability_offers_none(paths):
    assert helpers.provisioner(None) is None
    assert helpers.provisioner(helpers.for_name("knomi_serial", family="knomi")) is None


def test_may_provision_false_refuses_an_unprovisioned_serial_without_writing(paths, rr):
    """Fix 2: a deployment that must withhold the write - the agent, when it
    is read-only or `enable_flashing` is off, the same test that already
    withholds `fw.roadrunner.provision` - passes `may_provision=False` and
    gets the pre-Task-6 refusal, not a provisioned board."""
    with pytest.raises(UnprovisionedSerialError) as excinfo:
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED, may_provision=False)

    assert excinfo.value.message == "fake helper says provision this identity first"
    assert rr.calls == []
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_may_provision_false_still_tracks_an_already_provisioned_serial(paths, rr):
    """`may_provision=False` withholds the write, not tracking altogether: a
    serial that needs no provisioning tracks exactly as it would otherwise."""
    tracked = tracking.add_serial(paths, "roadrunner", PROVISIONED, may_provision=False)

    assert rr.calls == []
    assert tracked.serial == PROVISIONED
    assert tracked.provisioned_from is None
    assert Registry.load(paths).declared_serials("roadrunner") == [PROVISIONED]


def test_may_provision_true_is_the_default_and_still_provisions(paths, rr):
    """The default keeps today's behaviour for every caller that does not
    pass `may_provision` at all - an operator at the CLI, or a caller with no
    policy of its own."""
    tracked = tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == [UNPROVISIONED]
    assert tracked.serial == PROVISIONED


def test_a_failure_after_provisioning_names_the_new_serial_and_keeps_its_type(
    paths, rr
):
    """Fix 4 / review I2: a provisioned board that then fails to record must
    not vanish silently - the error raised names the serial that is now
    sitting on the board, and is still the same exception type (so a caller
    that switches on it, or its `.code`, is unaffected)."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[type roadrunner-other]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
            f"serials: {PROVISIONED}\n"
        )

    with pytest.raises(SerialTrackedElsewhereError) as excinfo:
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == [UNPROVISIONED], "the board was provisioned before the failure"
    assert PROVISIONED in excinfo.value.message
    assert excinfo.value.data.get("provisioned_serial") == PROVISIONED
    assert Registry.load(paths).declared_serials("roadrunner") == []


def test_a_serial_tracked_elsewhere_is_refused_before_it_is_provisioned(paths, rr):
    """Fix 5 / review I5: the requested serial, not only the one provisioning
    returns, is checked against "tracked elsewhere" before the write - a
    diagnostic identity already declared under another type (a hand-edited
    config, or an adoption path that writes through the registry directly)
    must not still get its board provisioned."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[type roadrunner-other]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
            f"serials: {UNPROVISIONED}\n"
        )

    with pytest.raises(SerialTrackedElsewhereError):
        tracking.add_serial(paths, "roadrunner", UNPROVISIONED)

    assert rr.calls == [], "refused before the irreversible write, not after"
