"""knomi_serial's own reading of its klippy module's printer objects.

This is firmware-specific code, so the knomi vocabulary lives here: a
`[knomi_serial T0_knomi]` section names its port one of two ways, and
`serial:` writes it in printer.cfg directly. `device_id:` names the screen by
the id burned into its chip and leaves discovery to find the path, which the
module then reports back as `port`. The core never sees these fields. It gets
a `ListedDevice`.
"""

from __future__ import annotations

import os

from mcu_updater.helpers import DeviceLister, ListedDevice, device_lister
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper

KNOMI = KnomiSerialHelper()


def test_knomi_serial_is_a_device_lister_on_its_own_prefix():
    assert device_lister(KNOMI) is KNOMI
    assert isinstance(KNOMI, DeviceLister)
    assert KNOMI.klipper_prefix == "knomi_serial"


def test_the_label_keeps_the_sections_own_capitalisation():
    device = KNOMI.device_from_klipper("knomi_serial T0_knomi", {})
    assert device.section == "knomi_serial T0_knomi"
    assert device.label == "T0_knomi"


def test_a_serial_section_is_addressed_by_its_path(fake_root):
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")

    device = KNOMI.device_from_klipper("knomi_serial t0", {"port": str(port)})

    assert device.configured_id is None
    assert device.configured_path == str(port)
    assert device.id == str(port)
    assert device.present is True


def test_a_device_id_section_is_listed_before_discovery_finds_it():
    """The screen that needs flashing is exactly the one this must not be blind
    to, so a `device_id:` section with no port yet is listed rather than dropped."""
    device = KNOMI.device_from_klipper("knomi_serial t0", {"device_id": "19AA44"})

    assert device.id == "19AA44"
    assert device.configured_id == "19AA44"
    assert device.configured_path is None
    assert device.resolved_path is None
    assert device.present is False


def test_a_device_id_section_is_addressed_by_its_id_once_discovery_finds_it(fake_root):
    """The discovered path changes when the screen moves socket, so it is never
    the identity. The id is."""
    port = fake_root / "ttyUSB3"
    port.write_text("", encoding="utf-8")

    device = KNOMI.device_from_klipper("knomi_serial t0", {"device_id": "19aa44", "port": str(port)})

    assert device.id == "19aa44"
    assert device.configured_path == str(port)
    assert device.present is True


def test_a_missing_symlink_is_listed_as_not_present(fake_root):
    """The case the klippy module swallows: Klipper starts happily with a blank
    screen and no error anywhere."""
    device = KNOMI.device_from_klipper("knomi_serial t0", {"port": str(fake_root / "not-there")})
    assert device.present is False
    assert device.resolved_path is None


def test_a_symlink_is_resolved_to_the_real_device(fake_root):
    real = fake_root / "ttyUSB0"
    real.write_text("", encoding="utf-8")
    link = fake_root / "knomi_t0"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks unavailable")

    device = KNOMI.device_from_klipper("knomi_serial t0", {"port": str(link)})

    assert device.resolved_path == os.path.realpath(real)
    assert device.configured_path == str(link)


def test_the_reported_id_is_lowered_because_the_docs_say_not_to_trust_its_case():
    device = KNOMI.device_from_klipper("knomi_serial t0", {"reported_id": "19AA44"})
    assert device.reported_id == "19aa44"


def test_a_screen_that_never_answered_reports_no_identity():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"reported_id": ""}).reported_id is None


def test_a_protocol_mismatch_is_an_incompatible_device():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": False}).compatible is False
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": True}).compatible is True


def test_an_unknown_protocol_is_not_an_incompatible_device():
    """None until the device reports in. Reading it as a mismatch would send
    people to reflash a healthy screen."""
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": None}).compatible is None
    assert KNOMI.device_from_klipper("knomi_serial t0", {"protocol_match": "yes"}).compatible is None


def test_device_online_is_whether_the_screen_answers():
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": True}).answering is True
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": False}).answering is False
    assert KNOMI.device_from_klipper("knomi_serial t0", {"device_online": None}).answering is None


def test_a_module_too_old_to_report_leaves_every_live_field_unknown():
    device = KNOMI.device_from_klipper("knomi_serial t0", {})
    assert (device.version, device.reported_id, device.compatible, device.answering) == (
        None,
        None,
        None,
        None,
    )


def test_the_running_version_is_the_firmware_version():
    device = KNOMI.device_from_klipper("knomi_serial t0", {"firmware_version": "0.5.0+54.g5509d4f"})
    assert device.version == "0.5.0+54.g5509d4f"


def test_every_value_stays_in_raw_for_the_helper_alone():
    values = {"port": "/dev/x", "tool": 0, "page_count": 3}
    device = KNOMI.device_from_klipper("knomi_serial t0", values)
    assert dict(device.raw) == values
    assert "tool" not in device.to_json()


def _with_module(version):
    return KNOMI.device_from_klipper("knomi_serial t0", {"module_version": version})


def test_the_module_version_is_one_helper_extra():
    """One klippy module serves every screen of a type, so the first screen
    that reports a version speaks for the type."""
    extras = KNOMI.extras([_with_module(None), _with_module("0.5.0"), _with_module("0.4.0")])
    assert [e.to_json() for e in extras] == [
        {
            "seam": "helper",
            "name": "knomi_serial",
            "key": "module_version",
            "label": "Module",
            "value": "0.5.0",
        }
    ]


def test_no_module_version_means_no_extra():
    assert KNOMI.extras([_with_module(None)]) == []
    assert KNOMI.extras([]) == []


def test_the_notes_tell_unreachable_apart_from_none_configured():
    """ "No screens configured" and "we could not ask Klipper" must not look alike."""
    assert KNOMI.devices_note(reachable=False) == "Could not reach Klipper to check for screens."
    assert KNOMI.devices_note(reachable=True) == "No screens found under [knomi_serial ...]."


def test_a_listed_device_is_the_generic_type():
    assert isinstance(KNOMI.device_from_klipper("knomi_serial t0", {}), ListedDevice)
