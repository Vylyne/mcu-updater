"""PlatformIO devices: the core's half of listing them.

The core owns one Moonraker query and one `reachable` answer. What a device
*is* - its addressing rule, its fields, whether it is compatible - belongs to
the family's helper, through `DeviceLister`. So nothing here imports
knomi_serial: every test runs against `FakeLister`, and a core line that knew a
knomi field name would fail them.

`firmware.HELPERS` is a static tuple that config loading checks, so the fake
stands in under a registered name. The name is the only knomi thing about it.
"""

from __future__ import annotations

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import ERR_METHOD_NOT_FOUND, RpcError
from mcu_updater.extras import Extra
from mcu_updater.helpers import ListedDevice
from mcu_updater.helpers import registry as helpers_registry

from .conftest import serve_klipper, write_main_config

PREFIX = "fake_dev"


class FakeLister:
    name = "knomi_serial"
    klipper_prefix = PREFIX

    def device_from_klipper(self, section, values):
        path = values.get("path")
        hwid = values.get("hwid")
        return ListedDevice(
            id=hwid or path,
            section=section,
            label=section.split(" ", 1)[1],
            configured_id=hwid,
            reported_id=values.get("reports"),
            configured_path=path,
            resolved_path=path if values.get("present") else None,
            version=values.get("version"),
            compatible=values.get("ok"),
            answering=values.get("up"),
            raw=dict(values),
        )

    def extras(self, devices):
        if not devices:
            return []
        return [Extra("helper", self.name, "count", "Devices", len(devices))]

    def devices_note(self, *, reachable):
        return "fake: none configured" if reachable else "fake: unreachable"


def _config(source, *types, family="fakefw", helper="knomi_serial"):
    text = (
        f"[firmware {family}]\nsource: {source}\nbuilder: platformio\n"
        + (f"helper: {helper}\n" if helper else "")
        + "flashers: platformio\n"
    )
    for name in types:
        text += f"\n[type {name}]\nchipset: esp32\nfirmware: {family}\nplatformio_env: {name}\n"
    return text


@pytest.fixture
def fake(monkeypatch):
    lister = FakeLister()
    monkeypatch.setitem(helpers_registry._BY_NAME, "knomi_serial", lister)
    return lister


@pytest.fixture
def api(paths, fake_root, fake):
    (fake_root / "fakefw").mkdir()
    write_main_config(paths, _config(fake_root / "fakefw", "fake_a"))
    return Api(paths)


def _row(api, name="fake_a"):
    return {t["name"]: t for t in api.dispatch("fw.status")["targets"]}[name]


# --------------------------------------------------------------------------
# the listing
# --------------------------------------------------------------------------


def test_devices_are_the_listers_prefix_and_nothing_else(api):
    api._call = serve_klipper(
        {
            "fake_dev t0": {"path": "/dev/a", "present": True},
            "fake_dev_other x": {"path": "/dev/b"},
            "mcu ebbt0": {},
        }
    )

    listed, reachable = api.platformio_devices()

    assert reachable is True
    assert [d.section for d in listed["fake_a"]] == ["fake_dev t0"]


def test_devices_come_back_in_a_stable_order_whatever_their_case(api):
    """A list that reorders between polls makes the panel jump around."""
    api._call = serve_klipper({f"fake_dev {n}": {} for n in ("t2", "T0", "t1")})

    listed, _ = api.platformio_devices()

    assert [d.label for d in listed["fake_a"]] == ["T0", "t1", "t2"]


def test_each_object_reaches_the_lister_under_its_own_capitalisation(api):
    """The printer object keeps printer.cfg's case; querying by a lowered name
    returns nothing at all, silently."""
    call = serve_klipper({"fake_dev T0": {"version": "1.0"}})
    api._call = call

    listed, _ = api.platformio_devices()

    assert listed["fake_a"][0].section == "fake_dev T0"
    assert listed["fake_a"][0].version == "1.0"
    assert list(call.queries[-1]["objects"]) == ["fake_dev T0"]


def test_an_unreachable_klipper_lists_nothing_and_says_so(api):
    """Review Focus 1. "No devices configured" and "we could not ask Klipper"
    must not look alike, and neither may raise out of fw.status."""
    api._call = serve_klipper({"fake_dev t0": {}}, reachable=False)

    row = _row(api)

    assert row["devices"] == []
    assert row["devices_note"] == "fake: unreachable"


def test_a_query_klipper_cannot_answer_is_unreachable_not_empty(api):
    """The object list can come from the cache while the query itself fails."""
    inner = serve_klipper({"fake_dev t0": {}})

    def call(method, params, timeout):
        if method == "printer.objects.query":
            return {}
        return inner(method, params, timeout)

    api._call = call

    assert api.platformio_devices() == ({}, False)


def test_a_reachable_klipper_with_no_sections_uses_the_reachable_note(api):
    api._call = serve_klipper({})

    row = _row(api)

    assert row["devices"] == []
    assert row["devices_note"] == "fake: none configured"


def test_two_types_sharing_a_prefix_both_list_it_and_it_is_queried_once(paths, fake_root, fake):
    """Review Focus 3. A prefix belongs to a family's helper, not to a type, so
    two types of one family list the same sections - and one query serves both."""
    (fake_root / "fakefw").mkdir()
    write_main_config(paths, _config(fake_root / "fakefw", "fake_a", "fake_b"))
    api = Api(paths)
    call = serve_klipper({"fake_dev t0": {}, "fake_dev t1": {}})
    api._call = call

    listed, _ = api.platformio_devices()

    assert [d.label for d in listed["fake_a"]] == ["t0", "t1"]
    assert [d.label for d in listed["fake_b"]] == ["t0", "t1"]
    assert len(call.queries) == 1


def test_a_family_with_no_lister_lists_nothing_and_names_itself(paths, fake_root):
    (fake_root / "plainfw").mkdir()
    write_main_config(paths, _config(fake_root / "plainfw", "plain", family="plainfw", helper=None))
    api = Api(paths)
    call = serve_klipper({"fake_dev t0": {}})
    api._call = call

    row = _row(api, "plain")

    assert row["devices"] == []
    assert row["devices_note"] == (
        "No devices are listed for this type: [firmware plainfw] names no "
        "helper that can list them."
    )
    assert api.platformio_devices() == ({}, True)


def test_no_platformio_types_costs_no_query_of_their_own(paths):
    """An absent feature should cost an absent key, not a round trip."""
    write_main_config(paths, "")
    calls = []
    api = Api(paths)
    api._call = lambda method, params, timeout: calls.append(method) or {}

    assert api.platformio_status() == []
    assert calls == []


def test_the_object_list_is_fetched_once_for_mcus_and_platformio(api):
    """A whole extra round trip, and fw.status has a sub-second budget."""
    calls = []
    inner = serve_klipper({"fake_dev t0": {}})

    def counting(method, params, timeout):
        calls.append(method)
        return inner(method, params, timeout)

    api._call = counting
    api.platformio_devices()
    api._mcu_object_names()
    api.platformio_devices()

    assert calls.count("printer.objects.list") == 1


def test_fw_device_list_is_gone(api):
    """Its data rides `targets[]` and `fw.target.get`; a second, unversioned
    copy of it on the wire is what this refactor removes."""
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.device.list")
    assert exc.value.code == ERR_METHOD_NOT_FOUND
    assert "fw.device.list" not in api.dispatch("fw.ping")["capabilities"]


# --------------------------------------------------------------------------
# the status payload and the row
# --------------------------------------------------------------------------


def test_a_device_reaches_status_as_its_generic_fields_only(api):
    api._call = serve_klipper(
        {"fake_dev t0": {"path": "/dev/a", "present": True, "private": 7}}
    )

    device = api.platformio_status()[0]["devices"][0]

    assert set(device) == set(ListedDevice.__dataclass_fields__) - {"raw"} | {
        "present",
        "reason",
        "confidence",
    }
    assert "private" not in device


def test_an_incompatible_device_makes_the_type_need_flashing(api):
    """The device declaring it cannot work with the host is authoritative."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "ok": False}})

    assert api.platformio_status()[0]["needs_flash"] is True
    assert _row(api)["devices"][0]["reason"] == "protocol_mismatch"


def test_unknown_compatibility_is_not_a_mismatch(api):
    """None until the device reports in; reading it as a mismatch would send
    people to reflash a healthy device."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "ok": None}})

    assert api.platformio_status()[0]["needs_flash"] is False


def test_the_listers_extras_reach_the_row(api):
    api._call = serve_klipper({"fake_dev t0": {}, "fake_dev t1": {}})

    assert _row(api)["extras"] == [
        {"seam": "helper", "name": "knomi_serial", "key": "count", "label": "Devices", "value": 2}
    ]


def test_no_devices_means_no_extras_and_a_note(api):
    api._call = serve_klipper({})

    row = _row(api)

    assert row["extras"] == []
    assert row["devices_note"] is not None


def test_a_row_with_devices_carries_no_note(api):
    api._call = serve_klipper({"fake_dev t0": {}})

    assert _row(api)["devices_note"] is None


def test_a_platformio_row_names_its_firmware_family(api):
    api._call = serve_klipper({})

    assert _row(api)["firmware"] == "fakefw"


def test_a_platformio_row_carries_no_extra(api):
    api._call = serve_klipper({})

    assert "extra" not in _row(api)


def test_an_undiscovered_device_id_section_is_a_blocked_row_not_a_missing_one(paths, api):
    """Review Focus 2. A section addressed by id, which discovery has not
    found yet: listed, addressed by its id, and its flash refused rather than
    aimed at a stale path."""
    from mcu_updater.jobs import JobRunner
    from mcu_updater.settings import load_settings

    from .conftest import write_settings

    write_settings(paths, enable_flashing="true", service_backend="null")
    runner = JobRunner(paths, lambda: load_settings(paths.settings_file))
    api = Api(paths, runner=runner, call=serve_klipper({"fake_dev t0": {"hwid": "aa11"}}))

    device = _row(api)["devices"][0]
    flash = next(a for a in device["actions"] if a["id"] == "flash")

    assert (device["id"], device["present"], device["path"], device["state"]) == (
        "aa11",
        False,
        None,
        "missing",
    )
    assert flash["params"] == {"name": "fake_a", "port": "aa11"}
    assert flash["blocked"]["code"] in (Api.BLOCKED_NO_DEVICE, Api.BLOCKED_NO_ARTIFACT)


@pytest.mark.parametrize(
    ("up", "state"), [(True, "online"), (False, "silent"), (None, "reachable")]
)
def test_a_present_devices_state_is_whether_it_answers(api, up, state):
    """A port that opens is not a device that answers, and an unknown answer
    must not read as a fault."""
    api._call = serve_klipper({"fake_dev t0": {"path": "/dev/a", "present": True, "up": up}})

    assert _row(api)["devices"][0]["state"] == state
