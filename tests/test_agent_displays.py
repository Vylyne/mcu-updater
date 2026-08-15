"""ESP32 displays: what Klipper is configured for, and whether it is there.

Named for the class rather than for Knomi, because a second, differently shaped
ESP32-S3 display is coming and would be a second PlatformIO env in the same tree
- so the knomi-specific name would have been wrong within weeks.

Read-only, and first, for the same reason `fw.dfu.scan` came before
`fw.add_mcu.start`: it establishes what is actually true on the host before
anything writes.

The device list is Klipper's, not ours. `[knomi_serial T0_knomi]` already names
how to find its port - `serial:` directly, or `device_id:` plus discovery - so a
second copy in our registry would only be something to disagree with.

`present` is the field that earns this method. The klippy module catches a failed
open and runs in no-op mode, so a missing symlink or a display that never
enumerated leaves Klipper reporting no error whatsoever - just a blank screen.
Nothing else in the system notices.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater.agent.methods import Api

from .conftest import display_objects, serve_klipper


def _moonraker(sections: dict, reachable: bool = True):
    return serve_klipper(display_objects(sections), reachable=reachable)


@pytest.fixture
def api(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    return Api(paths)


def test_the_displays_come_from_klippers_config(api, fake_root):
    port = str(fake_root / "knomi_t0")
    with open(port, "w", encoding="utf-8") as fh:
        fh.write("")

    api._call = _moonraker(
        {
            "knomi_serial t0_knomi": {"serial": port},
            "mcu ebbt0": {"serial": "/dev/serial/by-id/usb-Klipper_x_y-if00"},
            "printer": {"kinematics": "corexy"},
        }
    )
    res = api.dispatch("fw.display.list")

    assert res["reachable"] is True
    assert [d["name"] for d in res["displays"]] == ["t0_knomi"]
    assert res["displays"][0]["present"] is True


def test_other_sections_are_ignored(api):
    """`configfile.settings` is the whole printer.cfg - mcu sections, kinematics,
    every macro. Only knomi_serial ones are ours."""
    api._call = _moonraker(
        {
            "mcu": {"serial": "/dev/x"},
            "mcu ebbt0": {"serial": "/dev/y"},
            "knomi_serial_helper": {"serial": "/dev/z"},
            "printer": {},
        }
    )
    assert api.dispatch("fw.display.list")["displays"] == []


def test_a_missing_symlink_is_reported_not_hidden(api, fake_root):
    """The case the klippy module swallows. Its no-op fallback means Klipper
    starts perfectly happily with a blank display and no error anywhere, so this
    is the only thing that would ever say so."""
    api._call = _moonraker(
        {"knomi_serial t0_knomi": {"serial": str(fake_root / "knomi_t0_gone")}}
    )
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["present"] is False
    assert display["resolved_path"] is None
    # ...and it still says what was asked for, so the fix is obvious.
    assert display["configured_path"].endswith("knomi_t0_gone")


def test_a_symlink_is_resolved_to_the_real_device(api, fake_root):
    """The whole scheme is "a stable name udev keeps pointed at the right tty",
    so the resolved target is what tells you which tty it landed on."""
    real = fake_root / "ttyUSB0"
    real.write_text("", encoding="utf-8")
    link = fake_root / "knomi_t0"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this platform")

    api._call = _moonraker({"knomi_serial t0_knomi": {"serial": str(link)}})
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["present"] is True
    assert display["resolved_path"].endswith("ttyUSB0")
    assert display["configured_path"].endswith("knomi_t0")


def test_several_displays_come_back_in_a_stable_order(api, fake_root):
    """Six of them eventually, and a list that reorders between polls makes the
    panel jump around."""
    sections = {}
    for name in ("t2_knomi", "t0_knomi", "t1_knomi"):
        port = fake_root / f"knomi_{name}"
        port.write_text("", encoding="utf-8")
        sections[f"knomi_serial {name}"] = {"serial": str(port)}

    api._call = _moonraker(sections)
    names = [d["name"] for d in api.dispatch("fw.display.list")["displays"]]
    assert names == ["t0_knomi", "t1_knomi", "t2_knomi"]


def test_a_section_that_reports_neither_is_shown_rather_than_hidden(api):
    """It used to be skipped, on the grounds that a half-edited config should
    not produce an entry pointing at nothing. That distinction is no longer
    available: the section list comes from the printer objects, and a section
    reporting neither key is indistinguishable from a module too old to report
    them - which must still be listed.

    Klipper also refuses to start when a section names neither, so the config
    this guarded against cannot reach a running printer. Showing it with
    everything unknown is the honest answer, and the visible one.
    """
    api._call = _moonraker({"knomi_serial t0_knomi": {"heater_hotend": "extruder"}})
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["name"] == "t0_knomi"
    assert display["configured_path"] is None
    assert display["present"] is False


# --------------------------------------------------------------------------
# device_id: addressing - the path is discovered, not configured
# --------------------------------------------------------------------------


def test_a_device_id_section_appears_before_discovery_finds_it(api):
    """`device_id:` has no path in printer.cfg at all - discovery has to find
    one first. A display that still needs flashing is precisely the one this
    must not be blind to, so it belongs in the list from the start."""
    api._call = _moonraker({"knomi_serial t0_knomi": {"device_id": "19AA44"}})
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["addressed_by"] == "device_id"
    assert display["device_id"] == "19AA44"
    assert display["present"] is False
    assert display["configured_path"] is None
    assert display["resolved_path"] is None


def test_a_device_id_section_uses_the_port_discovery_found(api, fake_root):
    """Once Klipper's own discovery has matched the id to a port, that port
    comes back through get_status() - the only place it exists, since none of
    it is in printer.cfg."""
    port = str(fake_root / "ttyUSB3")
    with open(port, "w", encoding="utf-8") as fh:
        fh.write("")

    api._call = _moonraker_live(
        {"knomi_serial t0_knomi": {"device_id": "19AA44"}},
        {"knomi_serial T0_knomi": {"port": port, "device_id": "19AA44"}},
    )
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["addressed_by"] == "device_id"
    assert display["present"] is True
    assert display["configured_path"] == port
    assert display["resolved_path"].endswith("ttyUSB3")


def test_a_serial_section_reports_the_path_its_config_named(api, fake_root):
    """One field answers for both kinds of section, because the module already
    merged them: `port` is the configured `serial:` where there is one and the
    discovered path otherwise. There is no second value left to disagree with -
    this used to assert that `serial:` beat a differing live `port`, and the
    module cannot produce that pair.
    """
    configured = str(fake_root / "knomi_t0")
    with open(configured, "w", encoding="utf-8") as fh:
        fh.write("")

    api._call = _moonraker_live(
        {"knomi_serial t0_knomi": {"serial": configured}},
        {"knomi_serial T0_knomi": {}},
    )
    display = api.dispatch("fw.display.list")["displays"][0]

    assert display["addressed_by"] == "serial"
    assert display["device_id"] is None, "a serial: section names a socket"
    assert display["configured_path"] == configured


def test_an_unreachable_klipper_says_so_rather_than_claiming_none(api):
    """"No displays configured" and "we could not ask" must not look the same -
    that conflation is what made a board 90 commits behind report up to date."""
    api._call = _moonraker({}, reachable=False)
    res = api.dispatch("fw.display.list")

    assert res["reachable"] is False
    assert res["displays"] == []


def test_it_works_with_no_moonraker_at_all(paths, live_registry_text):
    """A read-only install with no call channel still answers, rather than
    raising."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    res = Api(paths).dispatch("fw.display.list")

    assert res["reachable"] is False
    assert res["displays"] == []


def test_it_is_available_to_a_read_only_agent(api):
    """It reads config and stats paths. Nothing here writes."""
    assert "fw.display.list" in api.dispatch("fw.ping")["capabilities"]


# --------------------------------------------------------------------------
# in fw.status, so the panel paints in one call
# --------------------------------------------------------------------------


def test_a_printer_with_no_displays_pays_nothing(api):
    """Not even the configfile query - an absent feature should cost an absent
    key, not a round trip."""
    calls = []
    api._call = lambda method, params, timeout: calls.append(method) or {}

    assert api.dispatch("fw.status")["displays"] == []
    assert "printer.objects.query" in calls  # for the mcu join
    # ...but display_status short-circuited before adding its own.
    assert calls.count("printer.objects.query") <= 2


def test_configured_displays_appear_in_status(api, paths, fake_root, live_registry_text):
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display knomi_toolchanger]\nsource: {fake_root}\n")

    api._call = _moonraker({"knomi_serial t0_knomi": {"serial": str(port)}})
    entry = api.dispatch("fw.status")["displays"][0]

    assert entry["env"] == "knomi_toolchanger"
    assert [s["name"] for s in entry["screens"]] == ["t0_knomi"]
    assert entry["screens"][0]["present"] is True


def test_a_screen_carries_no_identity_history(api, paths, fake_root):
    """Which board sat on which port is knomi_serial's business, not ours. It
    reports `device_id` - the eFuse id the screen itself announces - and we keep
    no record of our own to disagree with it."""
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display knomi_toolchanger]\nsource: {fake_root}\n")

    api._call = _moonraker({"knomi_serial t0_knomi": {"serial": str(port)}})
    screen = api.dispatch("fw.status")["displays"][0]["screens"][0]

    assert not {"mac", "flashed_at", "moved_from", "moved_at"} & set(screen)
    assert "device_id" in screen


def test_screens_are_matched_to_their_type_by_klipper_section(api, paths, fake_root):
    """Two display types with different klippy modules must not collect each
    other's screens."""
    for name in ("knomi_t0", "other_a"):
        (fake_root / name).write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[display knomi_toolchanger]\nsource: {fake_root}\n"
            f"\n[display otherscreen]\nsource: {fake_root}\nklipper_section: other_display\n"
        )

    api._call = _moonraker(
        {
            "knomi_serial t0_knomi": {"serial": str(fake_root / "knomi_t0")},
            "other_display a": {"serial": str(fake_root / "other_a")},
        }
    )
    by_env = {d["env"]: d for d in api.dispatch("fw.status")["displays"]}

    assert [s["name"] for s in by_env["knomi_toolchanger"]["screens"]] == ["t0_knomi"]
    assert [s["name"] for s in by_env["otherscreen"]["screens"]] == ["a"]


# --------------------------------------------------------------------------
# live status from the module's get_status
#
# knomi_serial grew a get_status reporting what the screen itself says: its
# firmware version, whether it is actually answering, and whether it speaks the
# protocol the module expects. Every one of those is unobtainable from outside,
# which is why they are worth a second source.
#
# The join has a trap in it. `configfile.settings` lowercases section names
# while the printer object keeps the case printer.cfg used, so querying by the
# name settings hands you returns nothing at all - silently - for anyone who
# capitalises. Which is everyone: [knomi_serial T0_knomi].
# --------------------------------------------------------------------------


def _moonraker_live(sections: dict, objects: dict):
    return serve_klipper(display_objects(sections, objects))


def _configured(port: str) -> dict:
    return {"knomi_serial t0_knomi": {"serial": port}}


def test_the_true_capitalisation_comes_from_the_printer_object(api, fake_root):
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {"knomi_serial T0_knomi": {"firmware_version": "0.4.0"}},
    )
    screen = api.display_list({})["displays"][0]

    # Not "t0_knomi" - that is only what settings lowercased it to.
    assert screen["section"] == "knomi_serial T0_knomi"
    assert screen["name"] == "T0_knomi"


def test_the_object_is_queried_by_its_real_name(api, fake_root):
    """Querying by the settings name returns nothing, and says nothing about it."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})
    api._call = call
    api.display_list({})

    asked = call.queries[0]["objects"]  # type: ignore[attr-defined]
    assert "knomi_serial T0_knomi" in asked
    assert "knomi_serial t0_knomi" not in asked


def test_what_the_screen_reports_reaches_the_caller(api, fake_root):
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {
            "knomi_serial T0_knomi": {
                "connected": True,
                "device_online": True,
                "firmware_version": "0.4.0",
                "module_version": "0.4.0",
                "protocol_match": True,
                "sleep_state": "awake",
                "free_heap": 121764,
                "device_uptime": 3601,
            }
        },
    )
    screen = api.display_list({})["displays"][0]

    assert screen["firmware_version"] == "0.4.0"
    assert screen["device_online"] is True
    assert screen["protocol_match"] is True
    assert screen["free_heap"] == 121764
    assert screen["device_uptime"] == 3601


def test_a_module_too_old_to_report_leaves_every_live_field_unknown(api, fake_root):
    """None, not False. "We cannot tell" and "the screen is not there" are
    different answers, and rendering the second for the first would invent a
    fault on a working display."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})
    screen = api.display_list({})["displays"][0]

    assert screen["present"] is True  # the port still resolves
    for field in ("connected", "device_online", "firmware_version", "protocol_match"):
        assert screen[field] is None, field


def test_a_port_that_resolves_is_not_the_same_as_a_screen_that_answers(api, fake_root):
    """The whole reason for a second source: `present` only means the symlink
    resolved. The far end can be unplugged and it stays true."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {"knomi_serial T0_knomi": {"connected": True, "device_online": False}},
    )
    screen = api.display_list({})["displays"][0]

    assert screen["present"] is True
    assert screen["connected"] is True
    assert screen["device_online"] is False


def _with_display_type(api, paths, fake_root):
    """display_status short-circuits with no [display] section - add one."""
    with open(paths.registry_file, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display knomi_toolchanger]\nsource: {fake_root}\n")


def test_a_protocol_mismatch_makes_the_type_need_flashing(api, paths, fake_root):
    """The device declares its own protocol version, so this is authoritative -
    the only "reflash this" a display can produce."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()
    _with_display_type(api, paths, fake_root)

    api._call = _moonraker_live(
        _configured(port),
        {"knomi_serial T0_knomi": {"protocol_match": False, "module_version": "0.4.0"}},
    )
    display = api.display_status()[0]

    assert display["needs_flash"] is True
    assert display["module_version"] == "0.4.0"


def test_an_unknown_protocol_is_not_treated_as_a_mismatch(api, paths, fake_root):
    """None until the device reports in. Offering "reflash" on a screen that has
    simply not spoken yet would send people to reflash a healthy display."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()
    _with_display_type(api, paths, fake_root)

    api._call = _moonraker_live(
        _configured(port), {"knomi_serial T0_knomi": {"protocol_match": None}}
    )
    assert api.display_status()[0]["needs_flash"] is False


def test_the_object_list_is_fetched_once_for_mcus_and_displays(api, fake_root):
    """It is a whole extra round trip, and fw.status has a sub-second budget."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    calls = []
    inner = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})

    def counting(method, params, timeout):
        calls.append(method)
        return inner(method, params, timeout)

    api._call = counting
    api.display_list({})
    api._mcu_object_names()
    api.display_list({})

    assert calls.count("printer.objects.list") == 1


def test_the_tool_the_screen_belongs_to_reaches_the_caller(api, fake_root):
    """knomi_serial grew per-tool fields. They come from the host's cluster, not
    from the device, so they answer even while a screen is silent - and they are
    the only thing tying a screen to a toolhead without reading printer.cfg."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {
            "knomi_serial T0_knomi": {
                "tool": 0,
                "used": True,
                "filament_color": "FF8800",
                "filament_type": "PLA",
            }
        },
    )
    screen = api.display_list({})["displays"][0]

    assert screen["tool"] == 0
    assert screen["used"] is True
    assert screen["filament_color"] == "FF8800"
    assert screen["filament_type"] == "PLA"


def test_tool_zero_is_not_confused_with_no_tool(api, fake_root):
    """T0 is a real tool and a falsy int. Anything treating it as absent drops
    the first toolhead, which is the one most likely to exist."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {"tool": 0}})
    assert api.display_list({})["displays"][0]["tool"] == 0


def test_a_module_without_the_tool_fields_reports_them_as_unknown(api, fake_root):
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})
    screen = api.display_list({})["displays"][0]

    for field in ("tool", "used", "filament_color", "filament_type"):
        assert screen[field] is None, field


# --------------------------------------------------------------------------
# the identity a display actually has
#
# A CH340K reports no USB serial number, so every path names a socket rather
# than a device. The screen's own id - six hex characters from the low three
# bytes of its eFuse MAC - is burned in and survives a reflash, an erase_flash
# and a move to another socket. It is the only stable name a display has.
# --------------------------------------------------------------------------


def test_a_serial_addressed_screen_still_reports_its_own_identity(api, fake_root):
    """The gap this closes. `device_id` is what printer.cfg names, and a
    `serial:` section names a path - so it was null for exactly the displays
    whose identity nothing else could supply."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {"knomi_serial T0_knomi": {"reported_id": "19aa44", "device_id": None}},
    )
    display = api.display_list({})["displays"][0]

    assert display["addressed_by"] == "serial"
    assert display["device_id"] is None, "printer.cfg names a socket, not a display"
    assert display["reported_id"] == "19aa44"


def test_a_reported_id_is_lowered_because_the_docs_say_not_to_trust_the_case(
    api, fake_root
):
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port), {"knomi_serial T0_knomi": {"reported_id": "19AA44"}}
    )
    assert api.display_list({})["displays"][0]["reported_id"] == "19aa44"


def test_a_screen_that_has_never_answered_reports_no_identity(api, fake_root):
    """Not the empty string. A display that cannot be reached is precisely the
    one that may need reflashing, and "" would compare equal to nothing."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})
    assert api.display_list({})["displays"][0]["reported_id"] is None


def test_a_pushed_config_is_separable_from_an_applied_one(api, fake_root):
    """A screen can be current on firmware and still showing the pages from
    before your last edit. `config_applied` is the only thing that says so."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {
            "knomi_serial T0_knomi": {
                "config_crc": "DEADBEEF",
                "device_config_crc": "0BADCAFE",
                "config_applied": False,
                "page_count": 3,
            }
        },
    )
    display = api.display_list({})["displays"][0]

    assert display["config_applied"] is False
    assert display["config_crc"] == "DEADBEEF"
    assert display["device_config_crc"] == "0BADCAFE"
    assert display["page_count"] == 3


def test_a_protocol_mismatch_can_say_which_way_round_it_is(api, fake_root):
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(
        _configured(port),
        {
            "knomi_serial T0_knomi": {
                "protocol_match": False,
                "protocol_version": 5,
                "device_protocol_version": 4,
            }
        },
    )
    display = api.display_list({})["displays"][0]

    assert display["protocol_match"] is False
    assert display["protocol_version"] == 5
    assert display["device_protocol_version"] == 4


def test_every_new_field_is_none_against_a_module_too_old_to_report_it(api, fake_root):
    """The existing contract: absence means unknown, never false. A module
    predating these fields answers nothing for them and must not read as a
    screen with a mismatched config or a failed protocol check."""
    port = str(fake_root / "knomi_t0")
    open(port, "w").close()

    api._call = _moonraker_live(_configured(port), {"knomi_serial T0_knomi": {}})
    display = api.display_list({})["displays"][0]

    for field in (
        "reported_id",
        "config_applied",
        "config_crc",
        "device_config_crc",
        "page_count",
        "protocol_version",
        "device_protocol_version",
    ):
        assert display[field] is None, field


# --------------------------------------------------------------------------
# the watcher's map
#
# The one source that answers while Klipper is down - which is exactly when
# flashing needs it, because esptool wants the port to itself, so Klipper has
# to be stopped, and stopping Klipper removes the only other source.
# --------------------------------------------------------------------------

import json as _json  # noqa: E402

from mcu_updater.providers import pio as pio_mod  # noqa: E402


def _declare_display(paths, env="knomi_toolchanger"):
    """A [display ...] section. The sample registry has none, and without one
    the watcher map has no family to belong to."""
    from mcu_updater.cfgdoc import CfgDocument

    with open(paths.main_config, encoding="utf-8") as fh:
        doc = CfgDocument(fh.read())
    doc.set(f"display {env}", "source", "/nowhere")
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())
    return env


def _write_map(paths, payload):
    path = os.path.join(paths.printer_data, "knomi", "devices.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(payload, fh)
    return path


GOOD_MAP = {
    "version": 1,
    "devices": {"19aa44": {"port": "/dev/ttyUSB0", "fw": "0.5.0+54.g5509d4f", "var": "knomi"}},
}


def _display(env="knomi"):
    return pio_mod.PioType(name=env, env=env, source="/nowhere")


def test_the_map_is_read_when_klipper_cannot_answer(api, paths):
    env = _declare_display(paths)
    _write_map(paths, GOOD_MAP)
    api._call = _moonraker({}, reachable=False)

    res = api.display_list({})
    assert res["reachable"] is False
    assert [d["device_id"] for d in res["watcher"][env]["devices"]] == ["19aa44"]


def test_the_map_is_not_consulted_while_klipper_is_answering(api, paths, monkeypatch):
    """Deciding whether the map is stale means asking systemd, which is a fork
    per call - and this method rides along in every fw.status poll."""
    _write_map(paths, GOOD_MAP)

    def boom(*a, **k):
        raise AssertionError("asked systemd while the authoritative source was up")

    monkeypatch.setattr("mcu_updater.service.SystemdService.is_active", boom)
    api._call = _moonraker({})

    assert api.display_list({})["watcher"] is None


def test_an_entry_carries_what_the_screen_reported(paths):
    _write_map(paths, GOOD_MAP)
    devices = pio_mod.read_device_map(paths, _display())

    assert list(devices) == ["19aa44"]
    entry = devices["19aa44"]
    assert entry.port == "/dev/ttyUSB0"
    assert entry.firmware_version == "0.5.0+54.g5509d4f"
    assert entry.build_variant == "knomi"


def test_ids_are_lowered_so_they_compare(paths):
    _write_map(paths, {"version": 1, "devices": {"19AA44": {"port": "/dev/ttyUSB0"}}})
    assert list(pio_mod.read_device_map(paths, _display())) == ["19aa44"]


def test_a_version_we_do_not_know_is_ignored_rather_than_guessed_at(paths):
    """The format is somebody else's to change, and a half-understood port is a
    write to the wrong display."""
    _write_map(paths, {"version": 2, "devices": {"19aa44": {"port": "/dev/ttyUSB0"}}})
    assert pio_mod.read_device_map(paths, _display()) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"devices": {"19aa44": {"port": "/dev/ttyUSB0"}}},  # no version at all
        {"version": 1},  # no devices
        {"version": 1, "devices": []},  # wrong shape
        {"version": 1, "devices": {"19aa44": "not a dict"}},
        {"version": 1, "devices": {"19aa44": {"fw": "0.5.0"}}},  # no port
    ],
)
def test_anything_unusable_is_an_empty_map_not_an_error(paths, payload):
    """Every one of these means "we cannot tell you where these displays are",
    and the caller's answer to that is the same in each case."""
    _write_map(paths, payload)
    assert pio_mod.read_device_map(paths, _display()) == {}


def test_a_missing_or_corrupt_file_is_not_an_error(paths):
    assert pio_mod.read_device_map(paths, _display()) == {}

    path = os.path.join(paths.printer_data, "knomi", "devices.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert pio_mod.read_device_map(paths, _display()) == {}


def test_a_family_can_declare_it_has_no_map(paths):
    _write_map(paths, GOOD_MAP)
    display = pio_mod.PioType(name="x", env="x", source="/nowhere", device_map="")
    assert pio_mod.device_map_path(paths, display) == ""
    assert pio_mod.read_device_map(paths, display) == {}


def test_an_absolute_map_path_is_used_as_given(paths, tmp_path):
    elsewhere = tmp_path / "elsewhere.json"
    with open(elsewhere, "w", encoding="utf-8") as fh:
        _json.dump(GOOD_MAP, fh)
    display = pio_mod.PioType(
        name="x", env="x", source="/nowhere", device_map=str(elsewhere)
    )
    assert pio_mod.read_device_map(paths, display)["19aa44"].port == "/dev/ttyUSB0"


def test_a_port_that_is_gone_proves_the_entry_is_stale(paths, fake_root):
    """No systemd needed: if the node the map names has disappeared, the entry
    is definitively out of date. The converse does not hold - a port that still
    exists may since have become a different display."""
    live = str(fake_root / "ttyUSB0")
    open(live, "w").close()
    _write_map(
        paths,
        {
            "version": 1,
            "devices": {
                "19aa44": {"port": live},
                "19aa45": {"port": str(fake_root / "gone")},
            },
        },
    )
    devices = pio_mod.read_device_map(paths, _display())

    assert devices["19aa44"].present is True
    assert devices["19aa45"].present is False, "a vanished port is not present"
    assert "19aa45" in devices, "still listed - a display we cannot find is the news"


def test_the_map_file_mtime_is_reported(api, paths):
    env = _declare_display(paths)
    _write_map(paths, GOOD_MAP)
    api._call = _moonraker({}, reachable=False)

    assert api.display_list({})["watcher"][env]["updated"] is not None
