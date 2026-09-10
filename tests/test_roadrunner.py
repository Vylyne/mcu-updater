"""Roadrunner direct-USB maintenance discovery and control."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import ERR_METHOD_NOT_FOUND, RpcError
from mcu_updater.discovery import bootsel as bootsel_discovery
from mcu_updater.discovery import roadrunner
from mcu_updater.errors import BootloaderTimeoutError, FlashError
from mcu_updater.flashers.spec import Bench
from mcu_updater.helpers import BootselHandoff, for_name
from mcu_updater.jobs import JobRunner
from mcu_updater.settings import load_settings

from .conftest import write_settings

UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"
PROVISIONED = "RR-0123456789ABCDEFGHJKMNPQRS"
#: A fake `time.sleep` step large enough that a faked-out re-enumeration
#: deadline (`roadrunner.REENUMERATE_TIMEOUT`) expires in a handful of loop
#: passes instead of sixty real 0.25s sleeps.
REENUMERATE_STEP = roadrunner.REENUMERATE_TIMEOUT / 4


def _info(*, provisioned: bool = False, serial: str = UNPROVISIONED) -> dict[str, object]:
    return {
        "protocol": 1,
        "provisioned": provisioned,
        "store_state": 1 if provisioned else 0,
        "transport": 1,
        "led_order": 1,
        "model": "roadrunner-v1",
        "fw_version": "dev",
        "serial": serial,
        "flash_uid": "50543165187A4D1C",
    }


def _candidate(
    paths, fake_root: Path, monkeypatch, *, manufacturer: str = "Vylyne", product: str = "Roadrunner"
) -> Path:
    entry = fake_root / "bus" / f"usb-Vylyne_Roadrunner_{UNPROVISIONED}-if00"
    entry.touch()
    tty = fake_root / "tty" / "ttyACM0" / "device"
    tty.mkdir(parents=True)
    monkeypatch.setattr(roadrunner.os.path, "realpath", lambda path, **_kwargs: str(fake_root / "ttyACM0"))
    usb_root = fake_root / "usb" / "1-3"
    usb_root.mkdir(parents=True)
    for name, value in {
        "idVendor": "2e8a\n",
        "idProduct": "000a\n",
        "manufacturer": f"{manufacturer}\n",
        "product": f"{product}\n",
        "serial": "not-an-identity\n",
    }.items():
        (usb_root / name).write_text(value, encoding="utf-8")
    topology = roadrunner.usb.UsbDevice(
        name="1-3", path=str(usb_root), vendor_id="2e8a", product_id="000a",
        product=product, manufacturer=manufacturer, serial="not-an-identity", speed="12", ports=0,
    )
    monkeypatch.setattr(roadrunner.usb, "collect", lambda _paths: [topology])
    monkeypatch.setattr(
        roadrunner.usb,
        "device_for_tty",
        lambda inventory, _paths, _tty: inventory[0],
    )
    return entry


def test_discovery_accepts_only_confirmed_unprovisioned_roadrunner(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info())

    found = roadrunner.discover(paths)

    assert len(found) == 1
    assert found[0].serial == UNPROVISIONED
    assert found[0].port == str(fake_root / "ttyACM0")
    assert found[0].topology.name == "1-3"
    assert not hasattr(found[0], "flash_uid")


@pytest.mark.parametrize(
    "bad",
    [
        {"protocol": 2},
        {"model": "not-roadrunner"},
        {"provisioned": True, "serial": PROVISIONED},
        {"serial": "RR-UNPROVISIONED-AAAAAAAAAAAAAAAA"},
    ],
)
def test_discovery_refuses_unconfirmed_info(paths, fake_root, monkeypatch, bad):
    _candidate(paths, fake_root, monkeypatch)
    data = _info()
    data.update(bad)
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: data)
    assert roadrunner.discover(paths) == []


@pytest.mark.parametrize(
    "manufacturer, product",
    [
        ("NotVylyne", "Roadrunner"),
        ("Vylyne", "NotRoadrunner"),
    ],
)
def test_discovery_refuses_wrong_manufacturer_or_product(paths, fake_root, monkeypatch, manufacturer, product):
    _candidate(paths, fake_root, monkeypatch, manufacturer=manufacturer, product=product)
    # If discovery ever called the helper on this candidate, this fake would
    # happily report a confirmed unprovisioned Roadrunner - so an empty
    # result here can only mean the manufacturer/product check refused the
    # candidate before the helper was ever consulted.
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info())
    assert roadrunner.discover(paths) == []


def test_helper_json_failure_is_distinct_from_a_bad_info_response(paths, monkeypatch):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, '{"error":"bad_crc"}\n', "")

    monkeypatch.setattr(roadrunner.subprocess, "run", run)
    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner._helper(paths, "info", "/dev/ttyACM0")
    assert exc.value.code == "roadrunner_helper"
    assert exc.value.data["error"] == "bad_crc"


def test_helper_rejects_a_bad_wire_crc_before_emitting_json():
    spec = importlib.util.spec_from_file_location(
        "roadrunner_usb", Path(__file__).parents[1] / "scripts" / "roadrunner_usb.py"
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class Port:
        def read(self, count):
            return {5: b"RR\x01\x81\x01", 1: b"\x00"}.get(count, b"")

    with pytest.raises(helper.ProtocolError, match="CRC"):
        helper.read_response(Port(), 1)


def test_explicit_probe_reports_invalid_info_not_no_candidate(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    bad_info = _info()
    bad_info["model"] = "wrong-model"
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: bad_info)
    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.find_untracked(paths, UNPROVISIONED)
    assert exc.value.code == "roadrunner_invalid_probe"


def test_provision_reenumerates_on_the_same_transient_topology(paths, fake_root, monkeypatch):
    _candidate(paths, fake_root, monkeypatch)
    initial = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    expected = "RR-0123456789ABCDEFGHJKMNPQRS"
    calls: list[tuple[str, str, str | None]] = []

    def helper(_paths, operation, port, uuid_hex=None):
        calls.append((operation, port, uuid_hex))
        if operation == "provision":
            return {"serial": expected}
        return _info(provisioned=True, serial=expected)

    monkeypatch.setattr(roadrunner, "_helper", helper)
    monkeypatch.setattr(
        roadrunner,
        "_await_same_topology",
        lambda *_args, **_kwargs: roadrunner.RoadrunnerDevice(expected, "/dev/ttyACM1", _topology()),
    )
    result = roadrunner.provision_roadrunner(paths, initial, bytes(range(16)))
    assert result.serial == expected
    assert calls == [("provision", "/dev/ttyACM0", "000102030405060708090a0b0c0d0e0f")]


def _fake_clock(monkeypatch, *, step: float = REENUMERATE_STEP) -> dict[str, float]:
    """A monotonic clock driven entirely by fake `time.sleep` calls.

    Neither `_await_reenumeration` nor its callers ever sleep in real time
    under this fixture: `time.monotonic` reads the fake clock, and
    `time.sleep` advances it instead of blocking.
    """
    clock = {"t": 0.0}
    monkeypatch.setattr(roadrunner.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(roadrunner.time, "sleep", lambda _seconds: clock.__setitem__("t", clock["t"] + step))
    return clock


def test_await_reenumeration_waits_then_resolves_on_the_same_topology(paths, monkeypatch):
    topology = _topology()
    expected = "RR-0123456789ABCDEFGHJKMNPQRS"
    port = "/dev/ttyACM1"
    polls: list[int] = []

    def fake_entry_candidates(_paths):
        polls.append(1)
        if len(polls) < 3:
            return []  # not yet re-enumerated
        return [(expected, port, topology)]

    monkeypatch.setattr(roadrunner, "_entry_candidates", fake_entry_candidates)
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info(provisioned=True, serial=expected))
    _fake_clock(monkeypatch)

    result = roadrunner._await_same_topology(paths, topology, expected, provisioned=True)

    assert result == roadrunner.RoadrunnerDevice(expected, port, topology)
    assert len(polls) == 3  # two empty polls, then the matching one


def test_await_reenumeration_times_out_when_nothing_reappears(paths, monkeypatch):
    topology = _topology()
    monkeypatch.setattr(roadrunner, "_entry_candidates", lambda _paths: [])
    helper_calls: list[object] = []
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: helper_calls.append(1))
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner._await_same_topology(paths, topology, "RR-0123456789ABCDEFGHJKMNPQRS", provisioned=True)

    assert exc.value.code == "roadrunner_timeout"
    assert helper_calls == []  # never even probed - nothing was on the bus to probe


def test_await_reenumeration_reports_mismatch_when_wrong_identity_reappears(paths, monkeypatch):
    topology = _topology()
    expected = "RR-0123456789ABCDEFGHJKMNPQRS"
    observed = "RR-ZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    monkeypatch.setattr(
        roadrunner, "_entry_candidates", lambda _paths: [(observed, "/dev/ttyACM9", topology)]
    )
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info(provisioned=True, serial=observed))
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner._await_same_topology(paths, topology, expected, provisioned=True)

    assert exc.value.code == "roadrunner_mismatch"
    assert exc.value.data["serial"] == expected
    assert exc.value.data["observed_serial"] == observed
    assert exc.value.data["observed_state"] == "provisioned"


def test_await_reenumeration_diagnoses_the_wire_identity_not_the_descriptor(paths, monkeypatch):
    """When the USB descriptor already shows the wanted serial but the wire
    protocol disagrees (e.g. a write that updated the string descriptor
    without the flash identity taking), the mismatch must report what INFO
    actually said - reporting the descriptor's (expected) serial back would
    be a useless "observed the identity you expected" diagnostic.
    """
    topology = _topology()
    expected = "RR-0123456789ABCDEFGHJKMNPQRS"
    stale_wire_serial = UNPROVISIONED
    monkeypatch.setattr(
        roadrunner, "_entry_candidates", lambda _paths: [(expected, "/dev/ttyACM2", topology)]
    )
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: _info(provisioned=False, serial=stale_wire_serial))
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner._await_same_topology(paths, topology, expected, provisioned=True)

    assert exc.value.code == "roadrunner_mismatch"
    assert exc.value.data["observed_serial"] == stale_wire_serial
    assert exc.value.data["observed_state"] == "unprovisioned"


def test_clear_reenumeration_times_out_when_nothing_reappears(paths, monkeypatch):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    monkeypatch.setattr(roadrunner, "_entry_candidates", lambda _paths: [])
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args, **_kwargs: {})
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.clear_roadrunner(paths, device)

    assert exc.value.code == "roadrunner_timeout"


def test_clear_reenumeration_reports_mismatch_when_still_provisioned(paths, monkeypatch):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    # Same topology re-enumerates, but it never sheds its provisioned serial -
    # e.g. the clear silently failed on-device. That is not "nothing came back".
    monkeypatch.setattr(
        roadrunner, "_entry_candidates", lambda _paths: [(PROVISIONED, "/dev/ttyACM0", _topology())]
    )
    monkeypatch.setattr(
        roadrunner,
        "_helper",
        lambda _paths, operation, port, uuid_hex=None: {}
        if operation == "clear"
        else _info(provisioned=True, serial=PROVISIONED),
    )
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.clear_roadrunner(paths, device)

    assert exc.value.code == "roadrunner_mismatch"
    assert exc.value.data["observed_serial"] == PROVISIONED
    assert exc.value.data["observed_state"] == "provisioned"


def test_request_bootsel_waits_for_the_old_cdc_topology_to_disappear(paths, monkeypatch):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    polls = [[(PROVISIONED, device.port, device.topology)], []]
    commands: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        roadrunner,
        "_helper",
        lambda _paths, operation, port, argument=None: commands.append(
            (operation, port, argument)
        )
        or {"bootsel": True},
    )
    monkeypatch.setattr(
        roadrunner, "_entry_candidates", lambda _paths, **_kwargs: polls.pop(0)
    )
    _fake_clock(monkeypatch)

    result = roadrunner.Roadrunner().request_bootsel(paths, device)

    assert result == device.topology
    assert commands == [("bootsel", device.port, PROVISIONED)]
    assert polls == []


def test_request_bootsel_times_out_while_the_old_cdc_topology_remains(paths, monkeypatch):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    commands: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        roadrunner,
        "_helper",
        lambda _paths, operation, port, argument=None: commands.append(
            (operation, port, argument)
        )
        or {"bootsel": True},
    )
    monkeypatch.setattr(
        roadrunner,
        "_entry_candidates",
        lambda _paths, **_kwargs: [(PROVISIONED, device.port, device.topology)],
    )
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.Roadrunner().request_bootsel(paths, device)

    assert exc.value.code == "roadrunner_timeout"
    assert "did not disappear" in str(exc.value)
    assert commands == [("bootsel", device.port, PROVISIONED)]


def test_request_bootsel_retries_unknown_inventory_before_confirmed_absence(
    paths, monkeypatch
):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    polls: list[OSError | list[tuple[str, str, roadrunner.usb.UsbDevice]]] = [
        OSError("USB sysfs unavailable"),
        [],
    ]
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: {"bootsel": True})

    def candidates(_paths, *, strict=False):
        assert strict
        result = polls.pop(0)
        if isinstance(result, OSError):
            raise result
        return result

    monkeypatch.setattr(roadrunner, "_entry_candidates", candidates)
    _fake_clock(monkeypatch)

    assert roadrunner.Roadrunner().request_bootsel(paths, device) == device.topology
    assert polls == []


def test_request_bootsel_times_out_when_inventory_remains_unknown(paths, monkeypatch):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    monkeypatch.setattr(roadrunner, "_helper", lambda *_args: {"bootsel": True})

    def unreadable(_paths, *, strict=False):
        assert strict
        raise OSError("USB sysfs unavailable")

    monkeypatch.setattr(roadrunner, "_entry_candidates", unreadable)
    _fake_clock(monkeypatch)

    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        roadrunner.Roadrunner().request_bootsel(paths, device)

    assert exc.value.code == "roadrunner_timeout"
    assert "could not confirm" in str(exc.value).lower()


def test_firmware_helper_confirms_the_provisioned_serial_before_request(
    paths, settings, monkeypatch
):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        roadrunner,
        "find_provisioned",
        lambda requested_paths, serial: events.append(("confirm", serial)) or device,
    )
    monkeypatch.setattr(
        roadrunner.Roadrunner,
        "request_bootsel",
        lambda _self, requested_paths, requested_device: events.append(
            ("request", requested_device)
        )
        or requested_device.topology,
    )
    monkeypatch.setattr(
        bootsel_discovery,
        "serial_topology_for",
        lambda requested_paths, port: events.append(("topology", port))
        or "platform-fd880000.usb-usb-0:1.3",
    )
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    result = helper.request_bootsel(
        bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
    )

    assert result == BootselHandoff(
        topology="platform-fd880000.usb-usb-0:1.3"
    )
    assert events == [
        ("confirm", PROVISIONED),
        ("topology", device.port),
        ("request", device),
    ]


def test_firmware_helper_refuses_before_bootsel_when_confirmation_fails(
    paths, settings, monkeypatch
):
    requested: list[object] = []
    monkeypatch.setattr(
        roadrunner,
        "find_provisioned",
        lambda _paths, serial: (_ for _ in ()).throw(
            roadrunner._error(
                "roadrunner_no_candidate",
                "No confirmed provisioned Roadrunner matched that serial",
                serial=serial,
            )
        ),
    )
    monkeypatch.setattr(
        roadrunner.Roadrunner,
        "request_bootsel",
        lambda *_args: requested.append(True),
    )
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    with pytest.raises(roadrunner.RoadrunnerError, match="No confirmed provisioned"):
        helper.request_bootsel(
            bench, serial="RR-BAD", chipset="rp2040", ctx=object()
        )

    assert requested == []


def test_firmware_helper_refuses_before_bootsel_without_serial_by_path_evidence(
    paths, settings, monkeypatch
):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    requested: list[object] = []
    monkeypatch.setattr(roadrunner, "find_provisioned", lambda *_args: device)
    monkeypatch.setattr(
        bootsel_discovery,
        "serial_topology_for",
        lambda *_args: (_ for _ in ()).throw(
            FlashError("no serial by-path topology matched the Roadrunner")
        ),
    )
    monkeypatch.setattr(
        roadrunner.Roadrunner,
        "request_bootsel",
        lambda *_args: requested.append(True),
    )
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    with pytest.raises(FlashError, match="no serial by-path"):
        helper.request_bootsel(
            bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
        )

    assert requested == []


def test_firmware_helper_wait_ready_retries_until_serial_and_info_are_confirmed(
    paths, settings, monkeypatch
):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM1", _topology())
    attempts: list[str] = []

    def find(_paths, serial):
        attempts.append(serial)
        if len(attempts) < 3:
            raise roadrunner._error(
                "roadrunner_no_candidate", "Roadrunner has not re-enumerated"
            )
        return device

    monkeypatch.setattr(roadrunner, "find_provisioned", find)
    _fake_clock(monkeypatch)
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    helper.wait_ready(
        bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
    )

    assert attempts == [PROVISIONED, PROVISIONED, PROVISIONED]


def test_firmware_helper_wait_ready_has_a_bounded_reenumeration_timeout(
    paths, settings, monkeypatch
):
    attempts: list[str] = []

    def missing(_paths, serial):
        attempts.append(serial)
        raise roadrunner._error(
            "roadrunner_no_candidate", "Roadrunner has not re-enumerated"
        )

    monkeypatch.setattr(roadrunner, "find_provisioned", missing)
    _fake_clock(monkeypatch)
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    with pytest.raises(BootloaderTimeoutError) as exc:
        helper.wait_ready(
            bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
        )

    assert exc.value.code == "bootloader_timeout"
    assert exc.value.data["serial"] == PROVISIONED
    assert len(attempts) == 5


@pytest.mark.parametrize("code", ["roadrunner_invalid_probe", "roadrunner_helper"])
def test_firmware_helper_wait_ready_retries_a_transient_probe_failure(
    paths, settings, monkeypatch, code
):
    """A board that is only halfway back looks exactly like a failed probe.

    During re-enumeration the `/dev/serial/by-id` symlink appears before the
    tty can reliably be opened, so an INFO probe that errors is not evidence of
    a bad identity - it is evidence of a board that is not ready yet. Retried
    to the deadline, like plain absence.
    """
    error = roadrunner._error(code, "Roadrunner readiness failed")
    attempts: list[str] = []

    def fail(_paths, serial):
        attempts.append(serial)
        raise error

    monkeypatch.setattr(roadrunner, "find_provisioned", fail)
    _fake_clock(monkeypatch)
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    with pytest.raises(BootloaderTimeoutError) as exc:
        helper.wait_ready(
            bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
        )

    assert exc.value.code == "bootloader_timeout"
    assert exc.value.data["serial"] == PROVISIONED
    # The cause rides along: retrying an invalid probe would otherwise report a
    # genuinely wrong identity as a bare timeout.
    assert exc.value.data["last_error"] == "Roadrunner readiness failed"
    assert len(attempts) == 5


@pytest.mark.parametrize("code", ["roadrunner_invalid_probe", "roadrunner_helper"])
def test_firmware_helper_wait_ready_accepts_a_board_that_settles_late(
    paths, settings, monkeypatch, code
):
    device = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM1", _topology())
    attempts: list[str] = []

    def find(_paths, serial):
        attempts.append(serial)
        if len(attempts) < 3:
            raise roadrunner._error(code, "Roadrunner readiness failed")
        return device

    monkeypatch.setattr(roadrunner, "find_provisioned", find)
    _fake_clock(monkeypatch)
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    helper.wait_ready(
        bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
    )

    assert attempts == [PROVISIONED, PROVISIONED, PROVISIONED]


def test_firmware_helper_wait_ready_stops_waiting_on_ambiguity(
    paths, settings, monkeypatch
):
    """Two devices answering to one serial is not a slow return, so the wait
    ends at once. Post-copy it is still only a warning - see
    `test_helper_bootsel_settled_warns_on_non_timeout_roadrunner_errors`."""
    error = roadrunner._error("roadrunner_ambiguous", "Roadrunner readiness failed")
    attempts: list[str] = []

    def fail(_paths, serial):
        attempts.append(serial)
        raise error

    monkeypatch.setattr(roadrunner, "find_provisioned", fail)
    _fake_clock(monkeypatch)
    bench = Bench(paths=paths, settings=settings, controller=lambda _name=None: None)

    helper = for_name("roadrunner", family="roadrunner")
    assert helper is not None
    with pytest.raises(roadrunner.RoadrunnerError) as exc:
        helper.wait_ready(
            bench, serial=PROVISIONED, chipset="rp2040", ctx=object()
        )

    assert exc.value is error
    assert attempts == [PROVISIONED]


def _ready_api(paths) -> Api:
    """A non-read-only, flashing-enabled agent - what every dispatch test here
    needs, now that `fw.roadrunner.provision`/`fw.roadrunner.clear` are gated
    on `enable_flashing`/read-only exactly like every other hardware-writing
    method (see `test_roadrunner_methods_are_gated_*` below for the gate
    itself).
    """
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    runner = JobRunner(paths, lambda: load_settings(paths.settings_file))
    return Api(paths, runner=runner)


def test_roadrunner_methods_are_not_advertised_by_default(paths):
    """Installing an update must never silently grant a browser the ability to
    provision or clear a board's identity - the same invariant `fw.flash`
    already upholds."""
    runner = JobRunner(paths, lambda: load_settings(paths.settings_file))
    api = Api(paths, runner=runner)  # enable_flashing omitted -> false

    capabilities = api.dispatch("fw.ping")["capabilities"]
    assert "fw.roadrunner.provision" not in capabilities
    assert "fw.roadrunner.clear" not in capabilities
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.code == ERR_METHOD_NOT_FOUND
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.clear", {"serial": PROVISIONED})
    assert exc.value.code == ERR_METHOD_NOT_FOUND


def test_roadrunner_methods_are_not_advertised_when_read_only(paths):
    """A read-only agent (no job runner) must withhold these too, even though
    neither call goes through the runner - read-only means no writes, not just
    no jobs."""
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    api = Api(paths)  # no runner -> read-only

    capabilities = api.dispatch("fw.ping")["capabilities"]
    assert "fw.roadrunner.provision" not in capabilities
    assert "fw.roadrunner.clear" not in capabilities
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.code == ERR_METHOD_NOT_FOUND
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.clear", {"serial": PROVISIONED})
    assert exc.value.code == ERR_METHOD_NOT_FOUND


def test_roadrunner_methods_are_advertised_once_enabled(paths):
    api = _ready_api(paths)
    assert "fw.roadrunner.provision" in api.dispatch("fw.ping")["capabilities"]
    assert "fw.roadrunner.clear" in api.dispatch("fw.ping")["capabilities"]


def test_agent_provisions_once_without_tracking(paths, monkeypatch):
    api = _ready_api(paths)
    original = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    result = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM1", _topology())
    generated: list[int] = []
    calls: list[object] = []
    monkeypatch.setattr(roadrunner, "find_untracked", lambda _paths, serial: original)
    monkeypatch.setattr(
        roadrunner,
        "provision_roadrunner",
        lambda _paths, device, uuid: calls.append((device, uuid)) or result,
    )
    monkeypatch.setattr(
        "mcu_updater.agent.methods.status.secrets.token_bytes",
        lambda size: generated.append(size) or bytes(range(size)),
    )

    response = api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})

    assert response == {"serial": PROVISIONED, "prior_serial": UNPROVISIONED, "state": "provisioned"}
    assert generated == [16]
    assert calls == [(original, bytes(range(16)))]
    assert api.registry().find_types_for_serial(PROVISIONED) == []


def test_agent_refuses_ambiguous_candidate_before_writing(paths, monkeypatch):
    api = _ready_api(paths)
    wrote: list[bool] = []
    monkeypatch.setattr(
        roadrunner,
        "find_untracked",
        lambda *_args: (_ for _ in ()).throw(
            roadrunner._error("roadrunner_ambiguous", "ambiguous")
        ),
    )
    monkeypatch.setattr(roadrunner, "provision_roadrunner", lambda *_args: wrote.append(True))
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.data["code"] == "roadrunner_ambiguous"
    assert wrote == []


def test_agent_does_not_retry_after_a_provision_timeout(paths, monkeypatch):
    api = _ready_api(paths)
    original = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM0", _topology())
    writes: list[bool] = []
    monkeypatch.setattr(roadrunner, "find_untracked", lambda *_args: original)
    monkeypatch.setattr(
        roadrunner,
        "provision_roadrunner",
        lambda *_args: writes.append(True)
        or (_ for _ in ()).throw(roadrunner._error("roadrunner_timeout", "timed out")),
    )
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.roadrunner.provision", {"serial": UNPROVISIONED})
    assert exc.value.data["code"] == "roadrunner_timeout"
    assert writes == [True]


def test_agent_clear_returns_to_unprovisioned_without_tracking(paths, monkeypatch):
    api = _ready_api(paths)
    original = roadrunner.RoadrunnerDevice(PROVISIONED, "/dev/ttyACM0", _topology())
    cleared = roadrunner.RoadrunnerDevice(UNPROVISIONED, "/dev/ttyACM1", _topology())
    monkeypatch.setattr(roadrunner, "find_provisioned", lambda *_args: original)
    monkeypatch.setattr(roadrunner, "clear_roadrunner", lambda *_args: cleared)
    response = api.dispatch("fw.roadrunner.clear", {"serial": PROVISIONED})
    assert response == {"serial": UNPROVISIONED, "prior_serial": PROVISIONED, "state": "unprovisioned"}
    assert api.registry().find_types_for_serial(UNPROVISIONED) == []


def test_agent_refuses_maintenance_for_a_cmake_tracked_roadrunner(paths):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(
            "[firmware roadrunner]\nsource: ~/roadrunner/rp2040\nbuilder: cmake\n"
            "\n[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"serials:\n    {PROVISIONED}\n"
        )
    with pytest.raises(RpcError) as exc:
        _ready_api(paths).dispatch("fw.roadrunner.clear", {"serial": PROVISIONED})
    assert exc.value.data["code"] == "roadrunner_tracked"
    assert exc.value.data["data"]["tracked_under"] == ["roadrunner"]


def _topology():
    return roadrunner.usb.UsbDevice(
        name="1-3", path="/sys/bus/usb/devices/1-3", vendor_id="2e8a", product_id="000a",
        product="Roadrunner", manufacturer="Vylyne", serial="diagnostic", speed="12", ports=0,
    )
