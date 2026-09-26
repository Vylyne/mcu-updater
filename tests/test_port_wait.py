"""The add-mcu wait is keyed on the USB port, not the by-id chipset segment.

A Roadrunner enumerates as `usb-Vylyne_Roadrunner_<serial>`; its segment is
`Roadrunner`, not `rp2040`, so a chipset filter reported "No board appeared"
after every successful install. The port is what survives the reboot.
"""

from __future__ import annotations

from mcu_updater.discovery import usb
from mcu_updater.discovery.byid import find_untracked, port_of, scan, wait_for_new_device

from .conftest import bootsel_device_node, make_device, on_port

PORT = "1-1.2"


def test_a_new_device_on_the_named_port_is_found(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert [d.serial for d in find_untracked(here, set(), port=PORT)] == ["AAAA"]


def test_a_new_device_on_another_port_is_not_found(paths, fake_root):
    here = on_port(paths, fake_root, "1-1.3")
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert find_untracked(here, set(), port=PORT) == []


def test_the_chipset_segment_is_not_a_filter(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-UNPROVISIONED-1")

    appeared = wait_for_new_device(here, set(), port=PORT, timeout=0.1, settle=0)
    assert [d.serial for d in appeared] == ["RR-UNPROVISIONED-1"]


def test_no_port_is_every_new_board(paths, fake_root):
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    assert [d.serial for d in find_untracked(paths, set())] == ["AAAA"]


def test_port_of_names_the_usb_device(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    make_device(fake_root / "bus", "katapult", "stm32g0b1xx", "AAAA")

    (dev,) = scan(here)
    assert port_of(here, dev) == PORT
    assert port_of(paths, dev) is None


def test_a_bootsel_volume_resolves_to_its_port(paths, fake_root):
    here = on_port(paths, fake_root, PORT)
    node = bootsel_device_node(fake_root / "bootsel_root")

    found = usb.device_for_block(usb.collect(here), here, node)
    assert found is not None and found.name == PORT
