"""Declared identity joined with one sweep of the bus.

Pure over injected inputs: no scan happens here, which is what lets the agent
reuse the sweep it already holds and lets these tests need no hardware.
"""

from __future__ import annotations

from mcu_updater import firmware, inventory, typelist
from mcu_updater.cfgdoc import CfgDocument
from mcu_updater.discovery.byid import STATE_OFFLINE, BusDevice

from .conftest import BASE_FIRMWARES

TEXT = (
    BASE_FIRMWARES
    + "[firmware roadrunner]\nsource: ~/rr\nbuilder: cmake\nflashers: bootsel\n\n"
    + "[type board]\nchipset: stm32f072xb\nfirmware: klipper, katapult\n"
    + "serials:\n    AAAA\ncanbus_uuids:\n    0123456789AB\n\n"
    + "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
    + "cmake_target: roadrunner_v1_usbserial\nserials:\n    RR-ONE\n"
)


def _entries():
    doc = CfgDocument(TEXT)
    return typelist.read(doc, firmware.load_from_doc(doc))


def _dev(serial, fw="Klipper", chipset="stm32f072xb"):
    return BusDevice(
        fw=fw, chipset=chipset, serial=serial, path=f"/dev/serial/by-id/usb-{fw}_{chipset}_{serial}"
    )


def test_every_declared_identity_gets_a_row_in_type_order():
    rows = inventory.build(_entries(), inventory.Sweep())
    assert [(r.type, r.builder, r.kind, r.id) for r in rows] == [
        ("board", "kconfig_make", inventory.SERIAL, "AAAA"),
        ("board", "kconfig_make", inventory.CANBUS_UUID, "0123456789AB"),
        ("roadrunner", "cmake", inventory.SERIAL, "RR-ONE"),
    ]
    assert [(r.present, r.state) for r in rows] == [
        (False, STATE_OFFLINE),
        (False, inventory.STATE_UNKNOWN),
        (False, STATE_OFFLINE),
    ]


def test_presence_and_state_come_from_the_sweep_for_every_builder():
    board, rr = _dev("AAAA"), _dev("RR-ONE", fw="Vylyne", chipset="Roadrunner")
    rows = inventory.index(inventory.build(_entries(), inventory.Sweep(byid=(board, rr))))
    row = rows[("board", inventory.SERIAL, "AAAA")]
    assert (row.present, row.state, row.path, row.device) == (True, board.state, board.path, board)
    row = rows[("roadrunner", inventory.SERIAL, "RR-ONE")]
    assert (row.present, row.state, row.path) == (True, rr.state, rr.path)


def test_the_by_id_chipset_segment_is_not_a_filter():
    rows = inventory.index(
        inventory.build(_entries(), inventory.Sweep(byid=(_dev("AAAA", chipset="stm32g0b1xx"),)))
    )
    assert rows[("board", inventory.SERIAL, "AAAA")].present is True


def test_two_sightings_of_one_serial_are_not_a_match():
    sweep = inventory.Sweep(byid=(_dev("AAAA"), _dev("AAAA", fw="katapult")))
    row = inventory.index(inventory.build(_entries(), sweep))[("board", inventory.SERIAL, "AAAA")]
    assert (row.present, row.state, row.path) == (False, STATE_OFFLINE, None)


def test_a_can_node_is_present_when_the_canbus_results_hold_it():
    cross = {"version": "v0.12.0"}
    sweep = inventory.Sweep(canbus={"0123456789ab": cross})
    row = inventory.index(inventory.build(_entries(), sweep))[
        ("board", inventory.CANBUS_UUID, "0123456789AB")
    ]
    assert (row.present, row.state, row.canbus) == (True, inventory.STATE_UNKNOWN, cross)
