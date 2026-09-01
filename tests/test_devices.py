from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater import devices as devices_mod
from mcu_updater.devices import (
    STATE_BOOTSEL,
    STATE_DFU,
    STATE_ESP_ROM,
    STATE_KATAPULT,
    STATE_KLIPPER,
    STATE_OFFLINE,
    bootsel_devices,
    bootsel_scan,
    device_state,
    find_device,
    find_untracked,
    parse_entry,
    scan,
    wait_for_device,
)
from mcu_updater.errors import BootloaderTimeoutError

from .conftest import make_device


def test_parses_the_three_part_name():
    dev = parse_entry("usb-Klipper_stm32g0b1xx_2900550018-if00", "/bus")
    assert dev is not None
    assert (dev.fw, dev.chipset, dev.serial) == ("Klipper", "stm32g0b1xx", "2900550018")
    assert dev.path.endswith("usb-Klipper_stm32g0b1xx_2900550018-if00")


def test_parses_a_two_part_name_as_having_no_chipset():
    dev = parse_entry("usb-Klipper_2900550018-if00", "/bus")
    assert dev is not None
    assert dev.chipset == ""
    assert dev.serial == "2900550018"


def test_find_device_matches_the_canonical_serial_at_an_exact_by_id_ending(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "stm32g0b1xx", "SERIAL-if00")
    (bus / "usb-Klipper_stm32g0b1xx-NOT-SERIAL-if00").write_text("", encoding="utf-8")

    dev = find_device(paths, "stm32g0b1xx", "SERIAL", fw="klipper")

    assert dev is not None
    assert dev.path.endswith("usb-Klipper_stm32g0b1xx_SERIAL-if00")


def test_find_device_refuses_ambiguous_by_id_entries(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "stm32g0b1xx", "SERIAL-if00")
    make_device(bus, "katapult", "stm32g0b1xx", "SERIAL-if00")

    assert find_device(paths, "stm32g0b1xx", "SERIAL") is None


@pytest.mark.skipif(os.name == "nt", reason="symlink fixtures require POSIX")
def test_scan_prefers_the_raw_usb_hardware_serial_via_the_tty_sysfs_link(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "stm32g0b1xx", "BY-ID-if00")
    by_id = bus / "usb-Klipper_stm32g0b1xx_BY-ID-if00"
    by_id.unlink()
    tty = fake_root / "dev" / "ttyACM0"
    tty.parent.mkdir()
    tty.write_text("", encoding="utf-8")
    by_id.symlink_to(tty)

    usb_root = fake_root / "usb"
    adapter = usb_root / "1-2"
    adapter.mkdir(parents=True)
    (adapter / "idVendor").write_text("1d50\n", encoding="utf-8")
    (adapter / "idProduct").write_text("606f\n", encoding="utf-8")
    (adapter / "serial").write_text("RAW-HARDWARE-SERIAL\n", encoding="utf-8")
    interface = usb_root / "1-2:1.0"
    interface.mkdir()
    tty_device = fake_root / "sys" / "class" / "tty" / "ttyACM0" / "device"
    tty_device.parent.mkdir(parents=True)
    tty_device.symlink_to(interface, target_is_directory=True)
    paths = dataclasses.replace(
        paths, usb_sysfs=str(usb_root), tty_sysfs=str(tty_device.parents[1])
    )

    assert scan(paths)[0].serial == "RAW-HARDWARE-SERIAL"


@pytest.mark.skipif(os.name == "nt", reason="symlink fixtures require POSIX")
def test_find_device_resolves_the_raw_usb_hardware_serial_via_the_tty_sysfs_link(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "stm32g0b1xx", "BY-ID-if00")
    by_id = bus / "usb-Klipper_stm32g0b1xx_BY-ID-if00"
    by_id.unlink()
    tty = fake_root / "dev" / "ttyACM0"
    tty.parent.mkdir()
    tty.write_text("", encoding="utf-8")
    by_id.symlink_to(tty)

    usb_root = fake_root / "usb"
    adapter = usb_root / "1-2"
    adapter.mkdir(parents=True)
    (adapter / "idVendor").write_text("1d50\n", encoding="utf-8")
    (adapter / "idProduct").write_text("606f\n", encoding="utf-8")
    (adapter / "serial").write_text("RAW-HARDWARE-SERIAL\n", encoding="utf-8")
    interface = usb_root / "1-2:1.0"
    interface.mkdir()
    tty_device = fake_root / "sys" / "class" / "tty" / "ttyACM0" / "device"
    tty_device.parent.mkdir(parents=True)
    tty_device.symlink_to(interface, target_is_directory=True)
    paths = dataclasses.replace(
        paths, usb_sysfs=str(usb_root), tty_sysfs=str(tty_device.parents[1])
    )

    dev = find_device(paths, "stm32g0b1xx", "RAW-HARDWARE-SERIAL", fw="klipper")

    assert dev is not None
    assert dev.path.endswith("usb-Klipper_stm32g0b1xx_BY-ID-if00")


@pytest.mark.skipif(os.name == "nt", reason="symlink fixtures require POSIX")
def test_scan_falls_back_to_the_parsed_by_id_serial_when_tty_is_not_usb(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "stm32g0b1xx", "BY-ID-if00")
    by_id = bus / "usb-Klipper_stm32g0b1xx_BY-ID-if00"
    by_id.unlink()
    tty = fake_root / "dev" / "ttyS0"
    tty.parent.mkdir()
    tty.write_text("", encoding="utf-8")
    by_id.symlink_to(tty)

    paths = dataclasses.replace(paths, tty_sysfs=str(fake_root / "sys" / "class" / "tty"))

    assert scan(paths)[0].serial == "BY-ID"


def test_find_device_does_not_treat_an_interface_suffix_in_config_as_canonical(paths, fake_root):
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "SERIAL-if00")

    assert find_device(paths, "stm32g0b1xx", "SERIAL-if00", fw="klipper") is None


def test_find_untracked_does_not_treat_an_interface_suffix_in_known_ids_as_canonical(paths, fake_root):
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "SERIAL-if00")

    assert [device.serial for device in find_untracked(paths, {"SERIAL-if00"})] == ["SERIAL"]


def test_a_multi_word_vendor_name_is_not_mis_split():
    """usb-Raspberry_Pi_Pico_<serial> has three vendor/product words before the
    serial - unlike Klipper's own `fw_chipset_serial` convention. The old
    `split("_", 2)` corrupted this into fw="Raspberry", chipset="Pi",
    serial="Pico_4250305031363918-if00"."""
    dev = parse_entry("usb-Raspberry_Pi_Pico_4250305031363918-if00", "/bus")
    assert dev is not None
    assert (dev.fw, dev.chipset, dev.serial) == (
        "Raspberry_Pi_Pico",
        "",
        "4250305031363918",
    )


@pytest.mark.parametrize("name", ["not-a-usb-device", "usb-onlyonepart", "usb-", "README"])
def test_ignores_unparseable_entries(name):
    assert parse_entry(name, "/bus") is None


def test_lowercase_klipper_is_found(paths, fake_root):
    """The bug this whole module exists to fix.

    The original discovered devices case-insensitively but rebuilt the path with
    an exact-case f-string when flashing, so a board enumerating as lowercase
    `usb-klipper_...` was detected and then declared missing.
    """
    make_device(fake_root / "bus", "klipper", "rp2040", "E660-if00")
    dev = find_device(paths, "rp2040", "E660", fw="Klipper")
    assert dev is not None
    assert dev.is_klipper
    assert dev.path.endswith("usb-klipper_rp2040_E660-if00")


def test_uppercase_klipper_is_also_found(paths, fake_root):
    make_device(fake_root / "bus", "Klipper", "rp2040", "E660-if00")
    assert find_device(paths, "rp2040", "E660", fw="klipper") is not None


def test_canboot_counts_as_katapult(paths, fake_root):
    """Katapult was called CanBoot; older bootloaders still enumerate that way."""
    make_device(fake_root / "bus", "CanBoot", "stm32f072xb", "4C00-if00")
    dev = find_device(paths, "stm32f072xb", "4C00", fw="katapult")
    assert dev is not None
    assert dev.is_katapult
    assert dev.state == STATE_KATAPULT


def test_chipset_must_match(paths, fake_root):
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "S1-if00")
    assert find_device(paths, "rp2040", "S1") is None


def test_device_state_reports_klipper_katapult_offline(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "chipA", "running")
    make_device(bus, "katapult", "chipA", "bootloader")

    assert device_state(paths, "chipA", "running")[0] == STATE_KLIPPER
    assert device_state(paths, "chipA", "bootloader")[0] == STATE_KATAPULT
    assert device_state(paths, "chipA", "unplugged") == (STATE_OFFLINE, None)


def test_missing_bus_directory_is_empty_not_an_error(paths):
    # /dev/serial/by-id does not exist when no USB serial device is attached.
    assert scan(paths) == []


def test_find_untracked_excludes_known_serials(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "Klipper", "chipA", "known")
    make_device(bus, "katapult", "chipA", "fresh")

    found = find_untracked(paths, {"known"})
    assert [d.serial for d in found] == ["fresh"]


def test_find_untracked_can_filter_by_fw_and_chipset(paths, fake_root):
    bus = fake_root / "bus"
    make_device(bus, "katapult", "chipA", "kat-a")
    make_device(bus, "katapult", "chipB", "kat-b")
    make_device(bus, "Klipper", "chipA", "klip-a")

    assert [d.serial for d in find_untracked(paths, set(), fw="katapult")] == ["kat-a", "kat-b"]
    assert [d.serial for d in find_untracked(paths, set(), fw="katapult", chipset="chipB")] == [
        "kat-b"
    ]


def test_wait_for_device_returns_immediately_when_present(paths, fake_root):
    make_device(fake_root / "bus", "katapult", "chipA", "S1")
    dev = wait_for_device(paths, "chipA", "S1", "katapult", timeout=1, poll=0.05)
    assert dev.serial == "S1"


def test_wait_for_device_times_out_with_a_useful_error(paths):
    with pytest.raises(BootloaderTimeoutError) as exc:
        wait_for_device(paths, "chipA", "S1", "katapult", timeout=0.2, poll=0.05)
    assert exc.value.data["serial"] == "S1"
    assert exc.value.code == "bootloader_timeout"


def test_scan_is_sorted_and_stable(paths, fake_root):
    bus = fake_root / "bus"
    for serial in ("c", "a", "b"):
        make_device(bus, "Klipper", "chipA", serial)
    assert [d.serial for d in scan(paths)] == ["a", "b", "c"]


# --------------------------------------------------------------------------
# is_mcu - which entries may be offered for adoption
# --------------------------------------------------------------------------


def test_a_display_is_never_offered_for_adoption(paths, fake_root):
    """Seen on a real printer: `updatefw status` listed a Knomi's CH340 under
    "Untracked devices on the bus", and the TUI's add-serial picker offered it
    as a device to track.

    `is_mcu` existed for exactly this, but only the agent applied it - so the
    panel was safe and the CLI and menu were not, which is the front-ends
    disagreeing about what counts as a board. The filter belongs in the model.
    """
    bus = fake_root / "bus"
    (bus / "usb-1a86_USB_Serial-if00-port0").write_text("", encoding="utf-8")
    make_device(bus, "Klipper", "stm32g0b1xx", "a-real-board")

    assert [d.serial for d in find_untracked(paths, set())] == ["a-real-board"]


def test_a_board_in_its_bootloader_is_still_offered(paths, fake_root):
    """The filter must not go too far. A board sitting in Katapult is the most
    likely thing to want adopting - it is what add-mcu leaves behind."""
    bus = fake_root / "bus"
    make_device(bus, "katapult", "stm32g0b1xx", "just-flashed")

    assert [d.serial for d in find_untracked(paths, set())] == ["just-flashed"]


def test_a_ch340_adapter_parses_as_a_device_but_is_not_an_mcu(paths, fake_root):
    """usb-1a86_USB_Serial-if00 splits into three parts and so parses cleanly.
    It is a Knomi's serial adapter, not a board - and once the panel offers
    one-tap "track this", listing it is one tap from building Klipper firmware
    for a display.

    This is the module's own documented CH340 example, traced through the
    rewritten `parse_entry` (Bug 1): stripped of `usb-` and `rsplit("_", 1)`,
    it's name_blob="1a86_USB", serial="Serial-if00"; that blob has exactly one
    underscore, so it gets the ordinary fw/chipset split - fw="1a86",
    chipset="USB". The `is_mcu` denylist catches it on the bare vendor-ID hex.
    """
    bus = fake_root / "bus"
    (bus / "usb-1a86_USB_Serial-if00").write_text("", encoding="utf-8")

    found = scan(paths)
    assert len(found) == 1
    assert found[0].fw == "1a86"
    assert found[0].chipset == "USB"
    assert found[0].is_mcu is False


def test_a_custom_firmware_board_is_now_adoptable(paths, fake_root):
    """The bug this whole section exists to fix: a Pico (or any other
    custom-firmware board) is not on the old allowlist, but it is also not a
    known USB-serial-bridge chip, so the denylist must let it through."""
    bus = fake_root / "bus"
    (bus / "usb-Raspberry_Pi_Pico_4250305031363918-if00").write_text("", encoding="utf-8")

    found = scan(paths)
    assert len(found) == 1
    assert found[0].fw == "Raspberry_Pi_Pico"
    assert found[0].is_mcu is True


@pytest.mark.parametrize(
    ("fw", "expected"),
    [
        ("Klipper", True),
        ("klipper", True),  # lowercase happens; the bug-3 fix
        ("katapult", True),
        ("Katapult", True),
        ("CanBoot", True),  # pre-rename bootloaders are still out there
        ("1a86", False),
        ("PL2303", False),  # Prolific's chip name, not the brand "Prolific"
        ("FTDI", False),
    ],
)
def test_only_serial_bridge_chips_are_refused_adoption(paths, fake_root, fw, expected):
    """Inverted from an allowlist to a denylist: `is_mcu` no longer requires a
    recognised firmware name, it only refuses a recognised bridge-chip
    identifier. "Prolific" the brand name isn't in the denylist (only its
    vendor-ID hex `067b` and chip name `pl2303` are, since that's what a real
    descriptor string actually contains) - see KNOWN_SERIAL_BRIDGE_NAMES."""
    make_device(fake_root / "bus", fw, "stm32g0b1xx", "AAAA1111-if00")
    found = scan(paths)
    assert len(found) == 1
    assert found[0].is_mcu is expected


def test_a_board_in_its_bootloader_is_adoptable(paths, fake_root):
    """The most important true case: a board sitting in Katapult is exactly what
    add-mcu leaves behind, so it is the thing most likely to need adopting."""
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "NEWBOARD-if00")
    assert scan(paths)[0].is_mcu is True


# --------------------------------------------------------------------------
# the DFU serial
#
# An STM32 reports a different serial in DFU than it does running firmware, and
# it is derived rather than truncated - which is why they look unrelated and why
# a board in DFU seems to connect to nothing you know about.
# --------------------------------------------------------------------------


def test_the_dfu_serial_is_derived_from_the_running_one():
    """Captured from a real BTT EBB36: dfu-util reported 3941335F3434, and the
    same board came back with canonical serial 27000E000551343438333339."""
    from mcu_updater.devices import dfu_serial_for

    assert dfu_serial_for("27000E000551343438333339-if00") is None


def test_it_works_without_the_interface_suffix():
    from mcu_updater.devices import dfu_serial_for

    assert dfu_serial_for("27000E000551343438333339") == "3941335F3434"


def test_the_words_are_little_endian_and_the_second_takes_its_TOP_nibbles():
    """Both halves of ST's Get_SerialNum, pinned separately - either read the
    wrong way round still produces twelve plausible hex digits, which would
    mislabel a board rather than fail."""
    from mcu_updater.devices import dfu_serial_for

    # w0 = 0x00000001, w1 = 0xAABBCCDD, w2 = 0x00000002 -> sum 3, top of w1 AABB
    uid = "01000000" + "DDCCBBAA" + "02000000"
    assert dfu_serial_for(uid) == "00000003AABB"


def test_the_sum_wraps_at_32_bits():
    from mcu_updater.devices import dfu_serial_for

    uid = "FFFFFFFF" + "00000000" + "02000000"
    assert dfu_serial_for(uid) == "000000010000"


def test_anything_that_is_not_a_96_bit_id_gets_no_answer():
    """None rather than a guess: a wrong label is worse than no label."""
    from mcu_updater.devices import dfu_serial_for

    assert dfu_serial_for("short-if00") is None
    assert dfu_serial_for("ZZ000E000551343438333339-if00") is None
    assert dfu_serial_for("") is None


def test_the_five_states_are_all_distinct():
    """A collision would silently misroute flasher selection later - two bus
    shapes reading as the same state is worse than an unfamiliar one."""
    states = {STATE_KLIPPER, STATE_KATAPULT, STATE_OFFLINE, STATE_DFU, STATE_BOOTSEL, STATE_ESP_ROM}
    assert len(states) == 6


# --------------------------------------------------------------------------
# BOOTSEL - a mounted RPI-RP2 volume, not a bus device at all
# --------------------------------------------------------------------------


def test_bootsel_scan_finds_a_volume_carrying_the_uf2_marker(paths, tmp_path):
    root = tmp_path / "bootsel_root"
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("UF2 Bootloader v3.0\n", encoding="utf-8")

    found = bootsel_scan(dataclasses.replace(paths, bootsel_root=str(root)))
    assert found == [str(vol)]


def test_bootsel_scan_ignores_a_same_named_drive_with_no_marker(paths, tmp_path):
    """The label alone is not enough - an unrelated drive happening to be
    named RPI-RP2 must not be mistaken for an RP2040 in its ROM bootloader."""
    root = tmp_path / "bootsel_root"
    (root / "RPI-RP2").mkdir(parents=True)

    found = bootsel_scan(dataclasses.replace(paths, bootsel_root=str(root)))
    assert found == []


def test_bootsel_scan_is_empty_when_nothing_is_mounted(paths, tmp_path):
    found = bootsel_scan(dataclasses.replace(paths, bootsel_root=str(tmp_path / "nothing-here")))
    assert found == []


def test_bootsel_scan_searches_the_automount_globs_with_no_override(paths, tmp_path, monkeypatch):
    """No `bootsel_root` override (the production case) means searching the
    standard udisks2 automount locations - here stood in for by a glob over a
    fake `/media/<user>` layout, since the real paths aren't writable in a
    test."""
    media_user = tmp_path / "media" / "someuser"
    vol = media_user / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        devices_mod, "DEFAULT_BOOTSEL_ROOT_GLOBS", (str(tmp_path / "media" / "*"),)
    )

    assert bootsel_scan(paths) == [str(vol)]


# --------------------------------------------------------------------------
# bootsel_devices - present on the bus whether mounted or not
# --------------------------------------------------------------------------


def test_bootsel_devices_finds_an_unmounted_device(paths, tmp_path):
    # Real device nodes have a `:` (e.g. `...-0:0-part1`), but NTFS reads that
    # as an alternate-data-stream separator and truncates the filename there,
    # so this fixture drops it - the glob under test doesn't care either way.
    root = tmp_path / "bootsel_root"
    by_id = root / "by-id"
    by_id.mkdir(parents=True)
    node = by_id / "usb-RPI_RP2_E0C9125B0D9B-0-0-part1"
    node.write_text("", encoding="utf-8")

    found = bootsel_devices(dataclasses.replace(paths, bootsel_root=str(root)))
    assert found == [str(node)]


def test_bootsel_devices_is_empty_with_nothing_attached(paths, tmp_path):
    found = bootsel_devices(dataclasses.replace(paths, bootsel_root=str(tmp_path / "nothing-here")))
    assert found == []
