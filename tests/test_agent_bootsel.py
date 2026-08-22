"""The BOOTSEL probe: what is waiting to be adopted, and can we write it.

`fw.bootsel.scan` mirrors `fw.dfu.scan`'s report-don't-raise shape - describing
the situation *is* its job - but diverges where BOOTSEL genuinely differs: no
external tool to be missing or to deny access, and readiness gates on the
*mount* count rather than the device count, because that is exactly what the
write itself (`flashers.bootsel._find_mount`) gates on.
"""

from __future__ import annotations

import dataclasses

import pytest

from mcu_updater.agent.methods import Api

from .conftest import bootsel_device_node, mounted_bootsel_volume

PICO = "testrp2040"
PICO_CHIPSET = "rp2040"


@pytest.fixture
def api(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    return Api(paths)


# --------------------------------------------------------------------------
# fw.bootsel.scan
# --------------------------------------------------------------------------


def test_a_mounted_board_is_ready(api, fake_root):
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="E0C9125B0D9B")
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))

    res = api.dispatch("fw.bootsel.scan")

    assert res["ready"] is True
    assert res["reason"] is None
    assert res["count"] == 1
    assert res["mount_count"] == 1
    assert res["devices"][0]["id"] == "E0C9125B0D9B"


def test_nothing_attached_says_to_hold_bootsel(api, fake_root):
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(fake_root / "nothing-here"))
    res = api.dispatch("fw.bootsel.scan")

    assert res["reason"] == "none"
    assert res["ready"] is False
    assert res["count"] == 0
    assert "bootsel" in (res["message"] or "").lower()


def test_an_unmounted_board_is_never_reported_as_no_board(api, fake_root):
    """The regression that matters most here, same as DFU's permission_denied
    case: a board attached but unmounted must not read as "nothing here",
    because the fix (the udev rule) is completely different from "replug"."""
    root = fake_root / "bootsel_root"
    node = bootsel_device_node(root)
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))

    res = api.dispatch("fw.bootsel.scan")

    assert res["reason"] == "not_mounted"
    assert res["ready"] is False
    assert res["count"] == 1
    assert res["mount_count"] == 0
    assert res["devices"][0]["node"] == node
    message = (res["message"] or "").lower()
    assert "install.sh" in message or "udev" in message
    assert "replug" not in message


def test_two_mounted_boards_is_ambiguous_with_no_way_to_choose(api, fake_root, monkeypatch):
    """Unlike DFU, there is no serial to disambiguate a mount-based write with -
    the udev rule mounts every board to the same fixed path. This is a plain
    refusal, not a choice the caller can make."""
    import mcu_updater.discovery.bootsel as bootsel_discovery
    from mcu_updater import devices as devices_mod

    media = fake_root / "media"
    for user in ("alice", "bob"):
        vol = media / user / "RPI-RP2"
        vol.mkdir(parents=True)
        (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    monkeypatch.setattr(devices_mod, "DEFAULT_BOOTSEL_ROOT_GLOBS", (str(media / "*"),))

    by_id = fake_root / "by-id"
    by_id.mkdir()
    for serial in ("AAAAAAAAAAAA", "BBBBBBBBBBBB"):
        (by_id / f"usb-RPI_RP2_{serial}-0-0-part1").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        bootsel_discovery, "_BOOTSEL_DISK_BY_ID_GLOB", str(by_id / "usb-RPI_RP2_*-part1")
    )
    api.paths = dataclasses.replace(api.paths, bootsel_root="")

    res = api.dispatch("fw.bootsel.scan")

    assert res["reason"] == "ambiguous"
    assert res["ready"] is False
    assert res["mount_count"] == 2
    assert len(res["devices"]) == 2


def test_the_probe_never_raises(api, fake_root):
    """Every branch must return a renderable answer - a scan that throws leaves
    the panel with an error banner and no idea what to tell the user."""
    mounted_root, _vol = mounted_bootsel_volume(fake_root, name="mounted")
    bootsel_device_node(mounted_root)
    unmounted_root = fake_root / "unmounted"
    bootsel_device_node(unmounted_root)

    for root in (fake_root / "nothing-here", unmounted_root, mounted_root):
        api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))
        res = api.dispatch("fw.bootsel.scan")
        assert set(res) >= {
            "devices", "count", "mounts", "mount_count", "ready", "reason", "message",
        }
        assert isinstance(res["ready"], bool)


def test_the_probe_is_available_to_a_read_only_agent(api):
    """It only reads /dev/disk/by-id and a mount point - diagnosing why a
    board cannot be seen is exactly what a read-only install still needs."""
    caps = api.dispatch("fw.ping")["capabilities"]
    assert "fw.bootsel.scan" in caps


# --------------------------------------------------------------------------
# naming the board in BOOTSEL
#
# Unlike DFU's derived serial, this is an ASSUMED identity: the boot ROM's
# flash-chip id is assumed (unverified on real hardware) to be the same string
# Katapult later runs under. See docs/agent-api.md's "RP2040 pairing identity"
# note and `FlashMixin.adopt_paired`.
# --------------------------------------------------------------------------


def test_a_tracked_board_in_bootsel_is_named(api, fake_root):
    """The tracked, running serial always carries a `-if00` interface suffix
    (`parse_entry`); the boot-ROM id never does. The match has to look past
    that, or it never fires on a real board."""
    api.dispatch("fw.type.add", {"name": PICO, "chipset": PICO_CHIPSET})
    api.dispatch("fw.serial.add", {"name": PICO, "serial": "E0C9125B0D9B-if00"})

    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="E0C9125B0D9B")
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))

    device = api.dispatch("fw.bootsel.scan")["devices"][0]
    assert device["tracked_by"] == PICO
    assert device["known_serial"] == "E0C9125B0D9B-if00"


def test_an_unrecognised_board_is_simply_unnamed(api, fake_root):
    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="BRANDNEWBOARD")
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))

    device = api.dispatch("fw.bootsel.scan")["devices"][0]
    assert device["tracked_by"] is None
    assert device["known_serial"] is None


def test_two_known_boards_sharing_an_id_name_neither(api, fake_root):
    """Same collision guard as `_identify_dfu` - an unlabelled board is a small
    annoyance, a board labelled as the wrong one is how you flash the toolhead
    you meant to leave alone. This assumed identity has no derivation to
    collide by construction, but the guard still has to hold if two tracked
    boards were ever (mis)recorded under the same UID (different interface
    suffix, e.g. re-tracked from a different by-id entry)."""
    api.dispatch("fw.type.add", {"name": PICO, "chipset": PICO_CHIPSET})
    api.dispatch("fw.type.add", {"name": "pico2", "chipset": PICO_CHIPSET})
    api.dispatch("fw.serial.add", {"name": PICO, "serial": "SHARED123456-if00"})
    # A second type cannot claim the same serial through fw.serial.add (that is
    # serial_tracked_elsewhere's job), so the collision is only reachable by
    # two registry entries sharing a UID under different literal serials -
    # construct it directly.
    from mcu_updater.config import Registry

    with Registry.mutate(api.paths, "test setup") as live:
        live.add_serial("pico2", "SHARED123456-if01")

    root, _vol = mounted_bootsel_volume(fake_root)
    bootsel_device_node(root, serial="SHARED123456")
    api.paths = dataclasses.replace(api.paths, bootsel_root=str(root))

    device = api.dispatch("fw.bootsel.scan")["devices"][0]
    assert device["tracked_by"] is None, "ambiguous means unnamed, never a guess"
    assert device["known_serial"] is None
