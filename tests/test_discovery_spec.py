"""The Inventory axis's vocabulary: `Sighting`, `Confidence`, `Source`.

Nothing implements `Source` yet - `discovery.registry.SOURCES` is `()` until a
later step moves the bus sources behind it. This tests the model only, the
same way `test_states.py` tests `ArtifactStatus`/`DeviceStatus` before any
caller produces one.
"""

from __future__ import annotations

import dataclasses

import pytest

from mcu_updater import devices
from mcu_updater.discovery import spec
from mcu_updater.discovery.registry import SOURCES, by_name
from mcu_updater.discovery.spec import Confidence, Sighting, state_for_firmware

# --------------------------------------------------------------------------
# the bootloader-predicate rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fw", ["katapult", "Katapult", "canboot", "CanBoot"])
def test_a_bootloader_name_reports_the_bootloader_state(fw):
    assert state_for_firmware(fw) == spec.STATE_KATAPULT


@pytest.mark.parametrize("fw", ["klipper", "Klipper", "cartographer", "some_future_fork"])
def test_anything_else_defaults_to_running_an_application(fw):
    """The inversion is the point: a fork nobody has written yet still reports
    STATE_KLIPPER without this module knowing its name."""
    assert state_for_firmware(fw) == spec.STATE_KLIPPER


def test_state_constants_are_devices_states_verbatim():
    """A parallel vocabulary for the same facts is a second thing to keep in
    step - this module must reuse devices.STATE_*, not redefine them."""
    assert spec.STATE_KLIPPER == devices.STATE_KLIPPER
    assert spec.STATE_KATAPULT == devices.STATE_KATAPULT
    assert spec.STATE_OFFLINE == devices.STATE_OFFLINE
    assert spec.STATE_DFU == devices.STATE_DFU
    assert spec.STATE_BOOTSEL == devices.STATE_BOOTSEL
    assert spec.STATE_ESP_ROM == devices.STATE_ESP_ROM


# --------------------------------------------------------------------------
# Sighting
# --------------------------------------------------------------------------


def test_a_sighting_carries_identity_separately_from_address():
    sighting = Sighting(
        id="290055001850304158373620",
        address="/dev/serial/by-id/usb-Klipper_stm32g0b1xx_290055001850304158373620-if00",
        state=spec.STATE_KLIPPER,
        source="byid",
    )
    assert sighting.id != sighting.address


def test_a_sighting_with_no_identity_is_empty_string_not_none():
    """A caller keying a dict on `.id` must not have to check for None first."""
    sighting = Sighting(id="", address="1-2.3", state=spec.STATE_DFU, source="dfu")
    assert sighting.id == ""


def test_to_json_drops_detail():
    sighting = Sighting(
        id="abc123",
        address="/dev/ttyUSB0",
        state=spec.STATE_KLIPPER,
        source="byid",
        detail={"secret": "not for the wire"},
    )
    payload = sighting.to_json()
    assert "detail" not in payload
    assert payload == {
        "id": "abc123",
        "address": "/dev/ttyUSB0",
        "state": spec.STATE_KLIPPER,
        "source": "byid",
    }


# --------------------------------------------------------------------------
# Confidence - the reason is the fact
# --------------------------------------------------------------------------


CONFIDENCE_REASONS = (
    spec.ANSWERED,
    spec.UNIQUE_BUS_ID,
    spec.REMEMBERED,
    spec.POSITIONAL,
    spec.UNCONFIRMED,
)


@pytest.mark.parametrize("reason", CONFIDENCE_REASONS)
def test_every_reason_has_a_tone(reason):
    assert Confidence(reason).tone in ("ok", "unknown", "attention")


@pytest.mark.parametrize("reason", CONFIDENCE_REASONS)
def test_every_reason_has_a_label(reason):
    assert Confidence(reason).label
    assert Confidence(reason).label != reason


def test_an_unknown_reason_is_refused_rather_than_silently_carried():
    with pytest.raises(ValueError, match="unknown confidence reason"):
        Confidence("probably_fine")


def test_safe_to_write_is_never_true_on_absent_evidence():
    """The rule states.DeviceStatus.needs_flash already enforces, for the same
    reason: absence of evidence is not evidence."""
    for reason in (spec.REMEMBERED, spec.POSITIONAL, spec.UNCONFIRMED):
        assert Confidence(reason).safe_to_write is not True


def test_only_answered_or_a_unique_bus_id_is_safe_to_write():
    assert Confidence(spec.ANSWERED).safe_to_write is True
    assert Confidence(spec.UNIQUE_BUS_ID).safe_to_write is True


def test_safe_to_write_is_never_false_only_true_or_none():
    """A tri-state answer: 'confirmed' or 'cannot vouch for it', never a hard
    'definitely not there' - that would be a fourth claim this type never has
    grounds to make."""
    for reason in CONFIDENCE_REASONS:
        assert Confidence(reason).safe_to_write is not False


def test_a_confidence_is_frozen():
    confidence = Confidence(spec.ANSWERED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        confidence.reason = spec.UNCONFIRMED  # type: ignore[misc]


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_the_two_knomi_sources_are_registered():
    """Step 26 wires listen/watcher behind this seam. The three bus sources
    (byid, dfu, bootsel) still are not - that is Step 27's."""
    assert [s.name for s in SOURCES] == ["listen", "watcher"]


def test_by_name_refuses_an_unknown_source():
    with pytest.raises(KeyError, match="no discovery source"):
        by_name("byid")
