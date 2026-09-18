"""Helpers are capabilities, asked for by what they can do.

A caller that compared a helper's name ("is this a Roadrunner?") is the caller
branch the one-pipeline design removes. Each capability is a Protocol with an
accessor that answers None for a helper without it, so a family with no
BOOTSEL request simply has no BOOTSEL request - and a misspelt helper name is a
config error, never a silent None.
"""

from __future__ import annotations

import pytest

from mcu_updater import firmware, helpers
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper
from mcu_updater.helpers.registry import HELPERS
from mcu_updater.helpers.roadrunner import RoadrunnerHelper


def test_the_helper_names_are_exactly_the_registry():
    """`typelist` checks `helper:` against `firmware.HELPERS` without importing
    the implementations, so the two lists must not drift."""
    assert set(firmware.HELPERS) == {helper.name for helper in HELPERS}


def test_the_flasher_names_are_exactly_the_registry():
    from mcu_updater.flashers.registry import FLASHERS

    assert set(firmware.FLASHERS) == {f.name for f in FLASHERS}


def test_no_helper_configured_is_none():
    assert helpers.for_name("", family="klipper") is None


def test_a_misspelt_helper_names_the_known_ones():
    with pytest.raises(ConfigCorruptError) as exc:
        helpers.for_name("roadruner", family="roadrunner")
    message = str(exc.value)
    assert "unknown helper 'roadruner'" in message
    assert "known: cartographer, knomi_serial, roadrunner" in message


def test_each_registered_helper_resolves_by_name():
    assert isinstance(helpers.for_name("roadrunner", family="rr"), RoadrunnerHelper)
    assert isinstance(helpers.for_name("knomi_serial", family="knomi"), KnomiSerialHelper)


def test_a_helper_that_can_request_bootsel_is_offered_as_one():
    helper = helpers.for_name("roadrunner", family="rr")
    assert helpers.bootsel_requester(helper) is helper


def test_a_helper_without_the_capability_is_not():
    assert helpers.bootsel_requester(KnomiSerialHelper()) is None


def test_no_helper_has_no_capability():
    assert helpers.bootsel_requester(None) is None
