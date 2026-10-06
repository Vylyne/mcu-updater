"""Setting up a new PlatformIO device: find its port, write it, hear its id.

Nothing here is a ROM flow. The upload tool resets an ESP32 into its ROM by
itself, so a blank device, a running one and a crashed one all look the same
on the bus - a serial port behind a bridge chip, usually with no USB serial.
Three things follow, and each is a property these tests hold:

**The port is found by the ids the type's board declares**, not by listing
every serial port and hoping: the printer's own MCUs are serial ports too.

**A configured device's port is never chosen unasked.** It matches the same
ids as the new one, so what tells them apart is that printer.cfg names it.

**The write is an ordinary write of that type.** Same stop list, same print
gate, same batch - with one addition: the device is asked which it is
afterwards, because that is the first moment it can say.
"""

from __future__ import annotations

import types

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.artifacts import KIND_PIO_ENV, Artifact
from mcu_updater.build import FlashLog
from mcu_updater.discovery.knomi_serial import WatcherDevice
from mcu_updater.discovery.usb import SerialPort, UsbDevice
from mcu_updater.errors import FlashError
from mcu_updater.flashers import KIND_BARE, KIND_PORT, Device, PlatformIO, TrackedBoard
from mcu_updater.jobs import JobRunner
from mcu_updater.providers import pio as pio_mod

from .conftest import display_objects, serve_klipper, write_settings
from .test_agent_add_mcu import EBB, _stage_katapult
from .test_agent_dfu import ONE_BOARD, patch_dfu
from .test_agent_platformio_flash import _services
from .test_platformio_flasher import _bench, _Broken, _ctx, _device, _env, _Identifier

TYPE = "knomi"
CH340 = ("1a86", "7522")


def _port(n: int, socket: str, *, vid="1a86", pid="7522", serial=None, kind="USB") -> SerialPort:
    device = UsbDevice(
        name=socket,
        path=f"/sys/bus/usb/devices/{socket}",
        vendor_id=vid,
        product_id=pid,
        product="USB Serial",
        manufacturer=None,
        serial=serial,
        speed=None,
        ports=0,
    )
    return SerialPort(tty=f"tty{kind}{n}", device=device, interface=f"{socket}:1.0")


#: One of the printer's own MCUs: a serial port, and not what is looked for.
MCU = _port(0, "3-1.2", vid="1d50", pid="614e", serial="MCUSERIAL", kind="ACM")
#: Two configured devices and two new ones, all the same bridge chip.
T0, T1 = _port(0, "3-1.6.5"), _port(1, "3-1.6.6")
NEW, OTHER = _port(2, "3-1.6.7"), _port(3, "3-1.6.8")


def _quiet(level, message):
    pass


@pytest.fixture
def bus(monkeypatch):
    """What is plugged in, and what the type's board says it enumerates as."""
    state = types.SimpleNamespace(ports=[MCU, NEW], hwids=(frozenset({CH340}), None))
    monkeypatch.setattr("mcu_updater.discovery.usb.serial_ports", lambda paths: list(state.ports))
    monkeypatch.setattr(pio_mod, "board_hwids", lambda settings, entry: state.hwids)
    return state


@pytest.fixture
def cfg(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    return paths


def _scan(paths, tracked=(), type_name=TYPE):
    return PlatformIO().scan_candidates(paths, tracked=tracked, reporter=_quiet, type_name=type_name)


def _configured(port: SerialPort, label: str, *, type_name=TYPE, ident="") -> TrackedBoard:
    return TrackedBoard(type_name, ident, "esp32", path=port.path, label=label)


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------


def test_the_one_port_with_the_boards_ids_is_the_new_device(cfg, bus):
    scan = _scan(cfg)

    assert scan.ready
    assert scan.devices == [
        {
            "tty": "/dev/ttyUSB2",
            "port": "3-1.6.7",
            "interface": "3-1.6.7:1.0",
            "vid_pid": "1a86:7522",
            "product": "USB Serial",
            "tracked_by": None,
            "known_serial": None,
            "label": None,
        }
    ]
    assert scan.chosen == scan.devices[0]
    assert scan.port == "3-1.6.7"


def test_the_scan_says_which_ids_it_looked_for_and_what_to_pick_by(cfg, bus):
    out = _scan(cfg).to_json()

    assert out["hwids"] == ["1a86:7522"]
    assert out["pick"] == "interface"


def test_a_port_carries_no_serial_key(cfg, bus):
    """`serial` is what a DFU device is named by on `fw.add_mcu.start`; a
    bridge chip's serial is not this device's, and most report none."""
    bus.ports = [_port(2, "3-1.6.7", serial="0001")]

    assert "serial" not in _scan(cfg).devices[0]


def test_a_configured_port_is_listed_and_labelled_and_never_the_one_chosen(cfg, bus):
    """Three identical bridges. Only printer.cfg knows two of them."""
    bus.ports = [T0, T1, NEW]

    scan = _scan(cfg, [_configured(T0, "t0_knomi", ident="aaa111"), _configured(T1, "t1_knomi")])

    assert scan.ready
    assert [(d["tty"], d["tracked_by"], d["label"], d["known_serial"]) for d in scan.devices] == [
        ("/dev/ttyUSB0", TYPE, "t0_knomi", "aaa111"),
        ("/dev/ttyUSB1", TYPE, "t1_knomi", None),
        ("/dev/ttyUSB2", None, None, None),
    ]
    assert scan.chosen["tty"] == "/dev/ttyUSB2"


def test_a_configured_port_is_recognised_through_its_udev_link(cfg, bus, tmp_path):
    """printer.cfg names `/dev/knomi_t0`; the bus names `ttyUSB0`."""
    node = tmp_path / "ttyUSB0"
    node.write_text("", encoding="utf-8")
    link = tmp_path / "knomi_t0"
    try:
        link.symlink_to(node)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    bus.ports = [T0, NEW]

    scan = _scan(cfg, [TrackedBoard(TYPE, "", "esp32", path=str(link), label="t0_knomi")])

    assert scan.ready
    assert scan.chosen["tty"] == "/dev/ttyUSB2"
    assert scan.devices[0]["label"] == "t0_knomi"


def test_two_unconfigured_ports_are_ambiguous(cfg, bus):
    bus.ports = [NEW, OTHER]

    scan = _scan(cfg)

    assert (scan.ready, scan.reason) == (False, "ambiguous")
    assert scan.chosen is None
    assert len(scan.devices) == 2
    assert "2 unconfigured ports" in scan.message


def test_only_configured_ports_is_not_a_new_device(cfg, bus):
    bus.ports = [T0, T1]

    scan = _scan(cfg, [_configured(T0, "t0_knomi"), _configured(T1, "t1_knomi")])

    assert (scan.ready, scan.reason) == (False, "all_tracked")
    assert scan.chosen is None
    assert len(scan.devices) == 2


def test_no_matching_port_names_the_ids_that_were_looked_for(cfg, bus):
    """The MCU's port is attached and is not listed: someone whose device is
    plugged in needs to see that the manifest's ids are the mismatch."""
    bus.ports = [MCU]

    scan = _scan(cfg)

    assert (scan.ready, scan.reason, scan.devices) == (False, "none", [])
    assert "1a86:7522" in scan.message
    assert "build.hwids" in scan.message


def test_unknown_ids_list_every_port_and_choose_none(cfg, bus):
    """Without the ids, a sole port is merely the only port - here it would
    be the printer's own MCU."""
    bus.hwids = (None, "PlatformIO not found")
    bus.ports = [MCU]

    scan = _scan(cfg)

    assert (scan.ready, scan.reason) == (False, "unfiltered")
    assert scan.chosen is None
    assert [d["tty"] for d in scan.devices] == ["/dev/ttyACM0"]
    assert "PlatformIO not found" in scan.message
    assert scan.to_json()["hwids"] is None


def test_unknown_ids_and_no_port_at_all_is_none(cfg, bus):
    bus.hwids = (None, "PlatformIO not found")
    bus.ports = []

    scan = _scan(cfg)

    assert (scan.ready, scan.reason) == (False, "none")
    assert "PlatformIO not found" in scan.message


def test_a_port_whose_usb_serial_is_a_tracked_board_is_that_board(cfg, bus):
    """Matters when the ids are unknown and everything is listed: a tracked
    MCU is not a candidate for anything."""
    bus.hwids = (None, "no manifest")
    bus.ports = [MCU, NEW]

    scan = _scan(cfg, [TrackedBoard("bttebb36", "MCUSERIAL", "stm32g0b1xx")])

    assert [d["tty"] for d in scan.devices] == ["/dev/ttyUSB2"]


def test_a_port_two_sections_claim_names_neither(cfg, bus):
    """Still not a free port. But which section is wrong is not the scan's to
    say, so it names the types and no label."""
    bus.ports = [T0, NEW]
    tracked = [_configured(T0, "t0_knomi", ident="aaa111"), _configured(T0, "spare", type_name="other")]

    scan = _scan(cfg, tracked)

    assert scan.ready and scan.chosen["tty"] == "/dev/ttyUSB2"
    claimed = scan.devices[0]
    assert (claimed["tracked_by"], claimed["label"], claimed["known_serial"]) == ("knomi, other", None, None)


@pytest.mark.parametrize("type_name", [None, "bttebb36", "nosuchtype"])
def test_a_scan_not_about_a_platformio_type_has_nothing_to_look_for(cfg, bus, type_name):
    scan = _scan(cfg, type_name=type_name)

    assert (scan.ready, scan.reason, scan.devices) == (False, "no_type", [])


# --------------------------------------------------------------------------
# the flasher
# --------------------------------------------------------------------------


def _bare(chipset="esp32s3", state="esp_rom"):
    return Device(type=TYPE, id="", chipset=chipset, state=state, fw="knomi_serial", kind=KIND_BARE)


def test_a_bare_esp32_is_one_it_writes():
    assert PlatformIO().supports(_bare(), None)
    assert PlatformIO().supports(_bare("esp32"), None)


@pytest.mark.parametrize(
    ("chipset", "state"),
    [("rp2040", "esp_rom"), ("", "esp_rom"), ("esp32", "dfu"), ("esp32", "bootsel")],
)
def test_a_bare_board_it_cannot_reset_into_its_rom_is_not(chipset, state):
    """`pio run -t upload` puts an ESP32 in its ROM by itself. Nothing does
    that for an STM32 or an RP2040, whichever build system made the image."""
    assert not PlatformIO().supports(_bare(chipset, state), None)


def test_first_write_is_the_scanned_port_as_an_ordinary_port_device(cfg):
    from mcu_updater.settings import Settings

    device, units = PlatformIO().first_write(
        cfg, Settings(), TYPE, "knomi_serial", {"tty": "/dev/ttyUSB2", "label": None}
    )

    assert (device.kind, device.id, device.type, device.fw) == (
        KIND_PORT,
        "/dev/ttyUSB2",
        TYPE,
        "knomi_serial",
    )
    assert device.detail["port"] == "/dev/ttyUSB2"
    assert device.detail["device_id"] == ""
    assert device.detail["first_install"] is True
    assert device.detail["env"].env == "knomi"
    # The type's own list, unshortened: the fixture's [type knomi] names both.
    assert units == ("klipper", "knomi_serial")


def _new_device(env, port="/dev/ttyUSB2"):
    device = _device(env, port, device_id="", name=env.name)
    return Device(
        type=device.type,
        id=device.id,
        chipset="",
        state="esp_rom",
        fw=device.fw,
        kind=KIND_PORT,
        detail={**device.detail, "section": "", "first_install": True},
    )


def _target(paths, device, helper):
    return PlatformIO().target(paths, device, helper, Artifact(KIND_PIO_ENV, "/nowhere"), stop_services=())


@pytest.fixture
def uploads(monkeypatch):
    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, e, port, **k: ports.append(port) or {"port": port, "chip": "ESP32-S3"},
    )
    return ports


def _heard(**by_id):
    return {i: WatcherDevice(device_id=i, port=p, present=True, answered=True) for i, p in by_id.items()}


def _write_new(paths, helper, **settings):
    env = _env()
    target = _target(paths, _new_device(env), helper)
    bench, ctx = _bench(paths, **settings), _ctx()
    with PlatformIO().prepared(bench, [target], ctx) as session:
        asked_before = list(getattr(helper, "asked", []))
        result = PlatformIO().write(bench, session, target, ctx)
    return result, target, ctx, asked_before


def test_a_new_device_is_not_asked_who_it_is_before_it_is_written(paths, uploads):
    """It has no identity yet, and the listen costs seconds."""
    ident = _Identifier(knomi_toolchanger=_heard(abc123="/dev/ttyUSB2"))

    _result, _target_, _ctx_, asked_before = _write_new(paths, ident)

    assert asked_before == []
    assert ident.asked == [("knomi_toolchanger", True)], "asked once, after the write"


def test_a_configured_device_in_the_same_batch_is_still_asked_first(paths, uploads):
    env = _env()
    ident = _Identifier()
    targets = [
        _target(paths, _new_device(env), ident),
        _target(paths, _device(env, "/dev/ttyUSB0"), ident),
    ]

    with PlatformIO().prepared(_bench(paths), targets, _ctx()) as session:
        assert set(session) == {env.name}


def test_the_device_is_written_at_the_scanned_port_and_reports_its_id(paths, uploads):
    ident = _Identifier(knomi_toolchanger=_heard(abc123="/dev/ttyUSB2", aaa111="/dev/ttyUSB0"))

    result, target, _ctx_, _asked = _write_new(paths, ident)

    assert uploads == ["/dev/ttyUSB2"]
    assert result["port"] == "/dev/ttyUSB2"
    assert result["reported_id"] == "abc123"
    assert result["confidence"] == "answered"
    assert result["chip"] == "ESP32-S3"
    # What the ledger files the write under: `record` reads it off the target.
    assert target.detail["device_id"] == "abc123"


def test_the_write_is_filed_under_the_id_the_device_reported(paths, uploads):
    ident = _Identifier(knomi_toolchanger=_heard(abc123="/dev/ttyUSB2"))

    _result, target, _ctx_, _asked = _write_new(paths, ident)

    record = PlatformIO().record(_bench(paths), target)
    assert record is not None and record.key == "hwid:abc123"


def test_a_device_that_says_nothing_is_written_and_filed_nowhere(paths, uploads):
    """An old image, or a device that is slow to boot. The write happened;
    there is simply no durable name to file it under."""
    ident = _Identifier(knomi_toolchanger=_heard(aaa111="/dev/ttyUSB0"))

    result, target, _ctx_, _asked = _write_new(paths, ident)

    assert uploads == ["/dev/ttyUSB2"]
    assert result["reported_id"] is None
    assert result["confidence"] is None
    assert PlatformIO().record(_bench(paths), target) is None


def test_a_remembered_entry_for_the_port_is_not_the_new_devices_answer(paths, uploads):
    """Whatever was on this port before the write is what a remembered entry
    describes. Only an answer heard after it counts."""
    remembered = {"old999": WatcherDevice(device_id="old999", port="/dev/ttyUSB2", present=True)}
    ident = _Identifier(knomi_toolchanger=remembered)

    result, target, _ctx_, _asked = _write_new(paths, ident)

    assert result["reported_id"] is None
    assert target.detail["device_id"] == ""


def test_a_family_that_cannot_identify_its_devices_reports_no_id(paths, uploads):
    result, target, _ctx_, _asked = _write_new(paths, None)

    assert uploads == ["/dev/ttyUSB2"]
    assert result["reported_id"] is None
    assert PlatformIO().record(_bench(paths), target) is None


def test_failing_to_ask_afterwards_does_not_fail_the_write(paths, uploads):
    """The image is on the device. Reporting the opposite because a listen
    could not run would send someone to re-flash a device that is fine."""
    result, _target_, ctx, _asked = _write_new(paths, _Broken())

    assert uploads == ["/dev/ttyUSB2"]
    assert result["reported_id"] is None
    assert any(stream == "warn" and "could not ask the new" in line for stream, line in ctx.said)


def test_a_dry_run_of_a_new_device_asks_nobody(paths, uploads):
    ident = _Identifier(knomi_toolchanger=_heard(abc123="/dev/ttyUSB2"))

    result, target, _ctx_, _asked = _write_new(paths, ident, dry_run=True)

    assert ident.asked == []
    assert "reported_id" not in result
    assert target.detail["device_id"] == ""


# --------------------------------------------------------------------------
# through the agent
# --------------------------------------------------------------------------


def _sections(fake_root) -> dict:
    """printer.cfg's two configured devices, on ports that exist under the
    names the bus gives them."""
    out = {}
    for label, port in (("t0_knomi", T0), ("t1_knomi", T1)):
        node = fake_root / port.tty
        node.write_text("", encoding="utf-8")
        out[f"knomi_serial {label}"] = {"serial": str(node)}
    return out


def _klipper(fake_root, **kwargs):
    return serve_klipper(display_objects(_sections(fake_root)), **kwargs)


@pytest.fixture
def api(paths, live_registry_text, fake_root, bus, monkeypatch):
    """An agent on a printer with two configured devices of the type and one
    new one plugged in."""
    (fake_root / "knomi_serial").mkdir()
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="false", service_backend="null", enable_flashing="true")
    bus.ports = [MCU, T0, T1, NEW]
    monkeypatch.setattr(
        "mcu_updater.helpers.knomi_serial.discover",
        lambda paths, settings, entry, *a, **k: {
            "abc123": WatcherDevice(device_id="abc123", port="/dev/ttyUSB2", present=True)
        },
    )

    runner = JobRunner(
        paths,
        lambda: __import__("mcu_updater.settings", fromlist=["load_settings"]).load_settings(
            paths.settings_file
        ),
    )
    agent = Api(paths, runner=runner, call=_klipper(fake_root))
    agent.KLIPPY_READY_TIMEOUT = 2.0
    agent.KLIPPY_RESTART_TIMEOUT = 2.0
    agent.KLIPPY_POLL_INTERVAL = 0.05
    yield agent
    runner._cancel.set()
    runner.wait(timeout=20)


def _finished(api, res):
    assert api.runner.wait(timeout=30)
    return api.runner.get(res["job_id"])


def _code(exc) -> str:
    return exc.value.data["code"]


def test_the_wizard_scan_names_the_configured_ports_from_klipper(api):
    out = api.dispatch("fw.add_mcu.scan", {"name": TYPE})

    assert (out["flasher"], out["ready"], out["pick"]) == ("platformio", True, "interface")
    assert out["count"] == 3
    assert [(d["tty"], d["tracked_by"], d["label"]) for d in out["devices"]] == [
        ("/dev/ttyUSB0", TYPE, "t0_knomi"),
        ("/dev/ttyUSB1", TYPE, "t1_knomi"),
        ("/dev/ttyUSB2", None, None),
    ]


def test_a_port_has_one_owner_however_many_types_its_family_has(api, paths):
    """Every type of a family lists all of that family's sections, so a
    second type would otherwise make every configured port doubly claimed -
    and unlabelled."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[type knomi_v2]\nchipset: esp32\nfirmware: knomi_serial\nplatformio_env: knomi_v2\n")

    for name in (TYPE, "knomi_v2"):
        out = api.dispatch("fw.add_mcu.scan", {"name": name})
        assert [(d["tracked_by"], d["label"]) for d in out["devices"][:2]] == [
            (name, "t0_knomi"),
            (name, "t1_knomi"),
        ]


def test_with_klipper_unreachable_configured_ports_read_as_new(api, fake_root):
    """Nothing else knows which are configured. The scan is then ambiguous
    rather than wrong: three free ports is never ready."""
    api._call = _klipper(fake_root, reachable=False)

    out = api.dispatch("fw.add_mcu.scan", {"name": TYPE})

    assert (out["ready"], out["reason"]) == (False, "ambiguous")
    assert [d["tracked_by"] for d in out["devices"]] == [None, None, None]


def test_the_new_device_is_written_and_the_job_says_what_it_is(api, paths, uploads, monkeypatch):
    order: list[str] = []
    _made, factory = _services(order)
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)

    res = api.dispatch("fw.add_mcu.start", {"name": TYPE})
    job = _finished(api, res)

    assert job.state == "succeeded", job.error
    assert uploads == ["/dev/ttyUSB2"], "the unconfigured port, and only it"
    result = job.result
    assert (result["type"], result["fw"], result["flasher"]) == (TYPE, "knomi_serial", "platformio")
    assert result["port"] == "3-1.6.7"
    assert result["written"] == {"path": "/dev/ttyUSB2", "reported_id": "abc123", "chip": "ESP32-S3"}
    assert "reports id abc123" in result["note"]
    # Nothing re-enumerates under a serial, so there is nothing to adopt.
    assert (result["candidates"], result["already_tracked"]) == ([], [])
    assert (result["dfu_serial"], result["bootsel_id"]) == (None, None)
    # An ordinary write of the type: its whole stop list, restored after.
    assert order == ["stop klipper", "stop knomi_serial", "start knomi_serial", "start klipper"]
    assert FlashLog(paths).all()["hwid:abc123"]["confidence"] == "answered"


def test_a_device_that_reports_no_id_is_configured_by_its_port(api, uploads, monkeypatch):
    monkeypatch.setattr("mcu_updater.helpers.knomi_serial.discover", lambda *a, **k: {})

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE}))

    assert job.state == "succeeded", job.error
    assert job.result["written"]["reported_id"] is None
    assert "by its port" in job.result["note"]


def test_a_dry_run_does_not_claim_to_have_written(api, paths, uploads):
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE}))

    assert job.state == "succeeded", job.error
    assert job.result["note"].startswith("[dry-run] would have written")
    assert job.result["written"]["reported_id"] is None
    assert FlashLog(paths).all() == {}


def test_a_printing_printer_refuses_before_a_job(api, fake_root, uploads):
    """Klipper is stopped for this write like any other of the type - which
    a ROM first install never needs, so this gate is new to the method."""
    api._call = _klipper(fake_root, idle_state="Printing")

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": TYPE})

    assert _code(exc) == "print_in_progress"
    assert api.runner.current() is None
    assert uploads == []


def test_two_new_devices_are_refused_with_the_list_to_pick_from(api, bus, uploads):
    bus.ports = [MCU, T0, T1, NEW, OTHER]

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": TYPE})

    assert _code(exc) == "platformio_ambiguous"
    assert len(exc.value.data["data"]["devices"]) == 4
    assert api.runner.current() is None
    assert uploads == []


def test_a_picked_device_is_the_one_written(api, bus, uploads, monkeypatch):
    bus.ports = [MCU, T0, T1, NEW, OTHER]
    monkeypatch.setattr("mcu_updater.helpers.knomi_serial.discover", lambda *a, **k: {})

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE, "pick": "3-1.6.8:1.0"}))

    assert job.state == "succeeded", job.error
    assert uploads == ["/dev/ttyUSB3"]
    assert job.result["port"] == "3-1.6.8"


def test_a_configured_port_can_be_picked_to_write_it_again_as_new(api, uploads, monkeypatch):
    """Never chosen unasked; allowed when named. A device whose image is too
    broken to answer is exactly the one somebody needs to do this to."""
    monkeypatch.setattr("mcu_updater.helpers.knomi_serial.discover", lambda *a, **k: {})

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE, "pick": "3-1.6.5:1.0"}))

    assert job.state == "succeeded", job.error
    assert uploads == ["/dev/ttyUSB0"]


def test_a_pick_that_matches_nothing_is_refused_with_what_was_scanned(api, uploads):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": TYPE, "pick": "9-9:1.0"})

    assert _code(exc) == "device_not_found"
    data = exc.value.data["data"]
    assert (data["pick"], data["key"], len(data["devices"])) == ("9-9:1.0", "interface", 3)
    assert uploads == []


def test_the_printers_own_mcu_cannot_be_picked(api, uploads):
    """It is not in the list the pick is matched against - the ids keep it
    out - so naming its interface names nothing."""
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": TYPE, "pick": MCU.interface})

    assert _code(exc) == "device_not_found"
    assert uploads == []


def test_unknown_ids_are_refused_until_a_port_is_picked(api, bus, uploads, monkeypatch):
    bus.hwids = (None, "PlatformIO not found")
    bus.ports = [NEW]
    monkeypatch.setattr("mcu_updater.helpers.knomi_serial.discover", lambda *a, **k: {})

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": TYPE})
    assert _code(exc) == "platformio_unfiltered"
    assert uploads == []

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE, "pick": NEW.interface}))
    assert job.state == "succeeded", job.error
    assert uploads == ["/dev/ttyUSB2"]


def test_a_failed_upload_fails_the_job_with_its_own_error(api, monkeypatch):
    """One device, so the job is its write: not a batch that "succeeded" with
    a failure listed inside it."""

    def refuse(p, s, e, port, **k):
        raise FlashError(f"upload failed for '{e.name}' on {port}: pio exited 1.", type=e.name, port=port)

    monkeypatch.setattr("mcu_updater.providers.pio.upload", refuse)

    job = _finished(api, api.dispatch("fw.add_mcu.start", {"name": TYPE}))

    assert job.state == "failed"
    assert "pio exited 1" in str(job.error)


def test_a_pick_means_nothing_to_a_scan_that_declares_no_key(api, paths, monkeypatch):
    """DFU devices are named by `dfu_serial`. `pick` against that scan is
    refused, not quietly ignored in favour of whatever is ready."""
    _stage_katapult(paths, EBB)
    patch_dfu(monkeypatch, stdout=ONE_BOARD)

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.add_mcu.start", {"name": EBB, "pick": "6-1.6.6.1.3"})

    assert _code(exc) == "device_not_found"
    assert exc.value.data["data"]["key"] is None
    assert api.runner.current() is None


def test_a_rom_scan_does_not_ask_klipper_which_ports_are_configured(api, monkeypatch):
    """It names its finds by serial and never reads the list, so it is not
    made to wait on Klipper for one."""
    patch_dfu(monkeypatch, stdout=ONE_BOARD)
    monkeypatch.setattr(
        api, "platformio_devices", lambda *a: pytest.fail("asked Klipper on behalf of a DFU scan")
    )

    out = api.dispatch("fw.add_mcu.scan", {"name": EBB})

    assert (out["flasher"], out["ready"]) == ("dfu_util", True)
