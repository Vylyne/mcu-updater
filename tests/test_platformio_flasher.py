"""The PlatformIO flasher asks the family's helper where each device is.

Rediscovery used to be a `discovery.confirm()` sweep inside this flasher, with
the knomi sources wired into it. It is the helper's question now, through the
`Identifier` capability - so a family with no way to tell its devices apart is
written at its configured ports, and this module names no vendor.
"""

from __future__ import annotations

import dataclasses
import types

import pytest

from mcu_updater.artifacts import KIND_PIO_ENV, Artifact
from mcu_updater.discovery.knomi_serial import WatcherDevice
from mcu_updater.errors import ToolMissingError
from mcu_updater.flashers import KIND_PORT, Bench, Device, PlatformIO
from mcu_updater.providers.pio import PioType
from mcu_updater.settings import Settings


def _env(name="knomi_toolchanger"):
    return PioType(name=name, env=name, source="/nowhere", firmware="knomi_serial")


def _device(env, port, device_id="aaa111", name="t0_knomi"):
    return Device(
        type=env.name,
        id=port,
        chipset="",
        state="unknown",
        fw=env.firmware,
        kind=KIND_PORT,
        detail={
            "env": env,
            "port": port,
            "device_id": device_id,
            "name": name,
            "section": f"knomi_serial {name}",
        },
    )


class _Identifier:
    """Answers from a fixed table, and records every question."""

    name = "fake"

    def __init__(self, **by_type):
        self.by_type = by_type
        self.asked: list[tuple[str, bool]] = []

    def identify(self, paths, settings, entry, *, ask, reporter):
        self.asked.append((entry.name, ask))
        return self.by_type.get(entry.name, {})

    def remembered_at(self, paths, entry):
        return ""


class _Broken(_Identifier):
    def identify(self, paths, settings, entry, *, ask, reporter):
        raise ToolMissingError("no pyserial", tool="discover")


def _bench(paths, **settings):
    return Bench(
        paths=paths,
        settings=Settings(service_backend="null", clean_before_build=False, **settings),
        controller=lambda unit: None,
    )


def _ctx():
    said: list[tuple[str, str]] = []
    return types.SimpleNamespace(
        reporter=lambda stream, line: said.append((stream, line)), said=said
    )


def _target(paths, device, helper):
    return PlatformIO().target(
        paths, device, helper, Artifact(KIND_PIO_ENV, "/nowhere"), stop_services=()
    )


def _write_one(paths, target):
    bench, ctx = _bench(paths), _ctx()
    with PlatformIO().prepared(bench, [target], ctx) as session:
        return PlatformIO().write(bench, session, target, ctx), ctx


@pytest.fixture
def uploads(monkeypatch):
    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, e, port, **k: ports.append(port) or {"port": port, "chip": None},
    )
    return ports


def test_prepared_asks_each_type_once_with_the_ports_free(paths):
    """One listen covers every port, so asking per device would multiply its
    cost - and a batch spanning two types asks each of them, once."""
    env_a, env_b = _env("knomi_a"), _env("knomi_b")
    ident = _Identifier()
    targets = [
        _target(paths, _device(env_a, "/dev/ttyUSB0"), ident),
        _target(paths, _device(env_a, "/dev/ttyUSB1", device_id="bbb222"), ident),
        _target(paths, _device(env_b, "/dev/ttyUSB2", device_id="ccc333"), ident),
    ]

    with PlatformIO().prepared(_bench(paths), targets, _ctx()) as session:
        assert set(session) == {"knomi_a", "knomi_b"}

    assert sorted(ident.asked) == [("knomi_a", True), ("knomi_b", True)]


def test_a_dry_run_asks_nobody(paths):
    """Asking can open real serial ports. A rehearsal that touches hardware is
    not a rehearsal."""
    ident = _Identifier()
    target = _target(paths, _device(_env(), "/dev/ttyUSB0"), ident)
    ctx = _ctx()

    with PlatformIO().prepared(_bench(paths, dry_run=True), [target], ctx) as session:
        assert session == {}

    assert ident.asked == []
    assert ("info", "[dry-run] would ask the devices which they are") in ctx.said


def test_a_family_with_no_identifier_is_written_at_its_configured_port(paths, uploads):
    """Nothing can say which device is which, so the configured port is the
    answer - what every write did before identity existed - and nothing
    claims it was confirmed. The caller is told so, once per type, rather
    than losing verification silently."""
    result, ctx = _write_one(paths, _target(paths, _device(_env(), "/dev/ttyUSB0"), None))

    assert uploads == ["/dev/ttyUSB0"]
    assert result["confidence"] is None
    assert ctx.said == [
        (
            "warn",
            "nothing can confirm which 'knomi_toolchanger' device is on which "
            "port - writing to the configured ports. A helper: on its "
            "[firmware ...] section that can identify its devices would.",
        )
    ]


def test_an_identifier_that_fails_leaves_the_configured_port(paths, uploads):
    """Never fatal, whatever the identifier: a host that cannot ask was
    flashing by configured port before, and refusing would be a new way to
    fail."""
    result, ctx = _write_one(
        paths, _target(paths, _device(_env(), "/dev/ttyUSB0"), _Broken())
    )

    assert uploads == ["/dev/ttyUSB0"]
    assert result["confidence"] is None
    assert [s for s, _ in ctx.said] == ["warn"]


@pytest.mark.parametrize("answered, reason", [(True, "answered"), (False, "remembered")])
def test_the_confidence_says_how_the_device_was_found(paths, uploads, answered, reason):
    env = _env()
    found = WatcherDevice(
        device_id="aaa111", port="/dev/ttyUSB0", present=True, answered=answered
    )
    ident = _Identifier(**{env.name: {"aaa111": found}})

    result, _ = _write_one(paths, _target(paths, _device(env, "/dev/ttyUSB0"), ident))

    assert result["confidence"] == reason


def test_a_device_that_did_not_answer_is_refused_when_others_did():
    """`discovered` came from a listen that ran: some entry answered, so the
    refusal's claim that this one was asked and stayed silent is true."""
    from mcu_updater.flashers.platformio import port_for

    found = WatcherDevice(device_id="other", port="/dev/ttyUSB1", present=True, answered=True)
    port, confidence, problem = port_for(
        {"name": "t0_knomi", "port": "/dev/ttyUSB0", "device_id": "aaa111"},
        {"other": found},
        _ctx(),
    )

    assert (port, confidence) == ("/dev/ttyUSB0", None)
    assert problem == (
        "did not answer when asked which devices are present, so its "
        "port cannot be confirmed. Writing to the port it used to be on "
        "could write to a different device."
    )


def test_a_device_absent_from_a_remembered_map_is_not_told_it_was_asked():
    """`discovered` here is the identifier's remembered map - no entry
    answered a listen, because none could run. Nothing was asked, so the
    refusal must not claim it was."""
    from mcu_updater.flashers.platformio import port_for

    found = WatcherDevice(device_id="other", port="/dev/ttyUSB1", present=True, answered=False)
    port, confidence, problem = port_for(
        {"name": "t0_knomi", "port": "/dev/ttyUSB0", "device_id": "aaa111"},
        {"other": found},
        _ctx(),
    )

    assert (port, confidence) == ("/dev/ttyUSB0", None)
    assert problem == (
        "is not among the devices its family last saw, and they could not be "
        "asked directly, so its port cannot be confirmed. Writing to the port "
        "it used to be on could write to a different device."
    )


def test_a_callers_own_detail_rides_along(paths):
    """bulk's `reason` is the caller's, and survives. The target's uniform
    slots are the device's own."""
    device = _device(_env(), "/dev/ttyUSB0")
    device = dataclasses.replace(device, detail={**device.detail, "reason": "forced"})

    target = _target(paths, device, None)

    assert target.detail["reason"] == "forced"
    assert target.detail["identifier"] is None
    assert (target.type, target.id, target.flasher) == (
        "knomi_toolchanger",
        "/dev/ttyUSB0",
        "platformio",
    )
