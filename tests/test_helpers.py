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
from mcu_updater.build import null_reporter
from mcu_updater.discovery.knomi_serial import WatcherDevice
from mcu_updater.errors import ConfigCorruptError
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper
from mcu_updater.helpers.registry import HELPERS
from mcu_updater.helpers.roadrunner import RoadrunnerHelper


def _device(device_id: str, port: str) -> WatcherDevice:
    return WatcherDevice(device_id=device_id, port=port, present=True)


def _never_asked(*a, **kw):
    raise AssertionError("opened the ports when the caller had not allowed it")


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


# --------------------------------------------------------------------------
# identity
#
# A board is found by the serial udev puts in /dev/serial/by-id. A KNOMI is
# behind a CH340K that reports no serial at all, so the only stable name it
# has is one the firmware will state if asked. That is a firmware-specific
# capability, which is what makes it a helper.
# --------------------------------------------------------------------------


def _pio_entry(name="knomi_toolchanger"):
    from mcu_updater.providers.pio import PioType

    return PioType(name=name, env=name, source="/nowhere", firmware="knomi_serial")


def test_only_the_knomi_helper_can_identify_devices():
    assert helpers.identifier(KnomiSerialHelper()) is not None
    assert helpers.identifier(RoadrunnerHelper()) is None
    assert helpers.identifier(None) is None


def test_the_remembered_map_answers_without_opening_a_port(paths, settings, monkeypatch):
    """`ask=False` is what a status poll passes. It must never cost a port."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", _never_asked)

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=False, reporter=null_reporter
    )
    assert list(found) == ["aaa111"]


def test_nothing_remembered_and_no_permission_to_ask_is_empty(paths, settings, monkeypatch):
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(handler, "discover", _never_asked)

    assert (
        KnomiSerialHelper().identify(
            paths, settings, _pio_entry(), ask=False, reporter=null_reporter
        )
        == {}
    )


def test_the_remembered_answer_wins_over_asking(paths, settings, monkeypatch):
    """Cheapest source first. The listen pass costs six seconds of held ports,
    so it runs when the map has nothing - not alongside it."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(
        handler,
        "read_device_map",
        lambda p, e: {"aaa111": _device("aaa111", "/dev/ttyUSB0")},
    )
    monkeypatch.setattr(handler, "discover", _never_asked)

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=True, reporter=null_reporter
    )
    assert list(found) == ["aaa111"]


def test_an_empty_map_asks_the_devices_themselves(paths, settings, monkeypatch):
    """The map is a remembered path; the broadcast is the authority. Each
    device announces its id every couple of seconds unprompted, so with the
    ports free this is a fact rather than a memory."""
    from mcu_updater.helpers import knomi_serial as handler

    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(
        handler,
        "discover",
        lambda p, s, e, **kw: {"bbb222": _device("bbb222", "/dev/ttyUSB1")},
    )

    found = KnomiSerialHelper().identify(
        paths, settings, _pio_entry(), ask=True, reporter=null_reporter
    )
    assert list(found) == ["bbb222"]


def test_asking_is_best_effort_and_never_raises(paths, settings, monkeypatch):
    """Discovery needs pyserial out of the module's own source tree. A host
    without it must get the caller's "neither source could tell" refusal,
    which names both sources, not a tool error from the fallback."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.helpers import knomi_serial as handler

    def boom(*a, **kw):
        raise ToolMissingError("no python3 here", tool="python3")

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(handler, "read_device_map", lambda p, e: {})
    monkeypatch.setattr(handler, "discover", boom)

    found = KnomiSerialHelper().identify(
        paths,
        settings,
        _pio_entry(),
        ask=True,
        reporter=lambda stream, line: said.append((stream, line)),
    )
    assert found == {}
    assert [s for s, _ in said] == ["info", "warn"]
    assert "no python3 here" in said[-1][1]


def test_where_the_answers_are_remembered_is_the_handlers_to_say(paths):
    """The CLI's refusal names the file it read. Asking the handler is what
    keeps `providers.pio` out of that sentence."""
    entry = _pio_entry()
    assert KnomiSerialHelper().remembered_at(paths, entry).endswith("devices.json")
