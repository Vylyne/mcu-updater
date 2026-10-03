"""Blanking the application's first sector inside the Katapult UF2 itself.

BOOTSEL has no erase command: the ROM writes the pages a UF2 names and leaves
every other sector as it was. A board that last ran some other firmware keeps
that image at the application address, and Katapult chain-loads anything that
looks like one - so the board comes back running the old firmware, or nothing
at all, instead of sitting in Katapult waiting for Klipper.
"""

from __future__ import annotations

import struct

import pytest

from mcu_updater import uf2_erase
from mcu_updater.uf2_erase import (
    PAGE_SIZE,
    SECTOR_SIZE,
    Uf2EraseError,
    launch_address,
    with_erased_sector,
)

MAGIC0 = 0x0A324655
MAGIC1 = 0x9E5D5157
MAGIC_END = 0x0AB16F30
FAMILY_FLAG = 0x00002000
RP2040_FAMILY = 0xE48BFF56
FLASH = 0x10000000
APP = 0x10004000


def _block(target, payload, *, flags=FAMILY_FLAG, family=RP2040_FAMILY, no=0, count=1):
    header = struct.pack(
        "<8I", MAGIC0, MAGIC1, flags, target, len(payload), no, count, family
    )
    data = payload + b"\0" * (476 - len(payload))
    return header + data + struct.pack("<I", MAGIC_END)


def _image(targets, fill=b"\xaa"):
    return b"".join(
        _block(t, fill * PAGE_SIZE, no=i, count=len(targets))
        for i, t in enumerate(targets)
    )


def _fields(uf2):
    out = []
    for off in range(0, len(uf2), 512):
        m0, m1, flags, target, size, no, count, family = struct.unpack_from("<8I", uf2, off)
        (end,) = struct.unpack_from("<I", uf2, off + 508)
        out.append(
            {
                "magic": (m0, m1, end),
                "flags": flags,
                "target": target,
                "size": size,
                "no": no,
                "count": count,
                "family": family,
                "payload": uf2[off + 32 : off + 32 + size],
            }
        )
    return out


def test_the_application_sector_is_written_as_erased_pages():
    katapult = [FLASH + i * PAGE_SIZE for i in range(4)]
    out = _fields(with_erased_sector(_image(katapult), APP))

    erased = [b for b in out if b["target"] >= APP]
    assert [b["target"] for b in erased] == [
        APP + i * PAGE_SIZE for i in range(SECTOR_SIZE // PAGE_SIZE)
    ]
    assert all(b["payload"] == b"\xff" * PAGE_SIZE for b in erased)
    assert all(b["size"] == PAGE_SIZE for b in erased)


def test_katapult_itself_is_carried_through_untouched():
    katapult = [FLASH + i * PAGE_SIZE for i in range(4)]
    out = _fields(with_erased_sector(_image(katapult), APP))

    kept = [b for b in out if b["target"] < APP]
    assert [b["target"] for b in kept] == katapult
    assert all(b["payload"] == b"\xaa" * PAGE_SIZE for b in kept)


def test_every_block_is_renumbered_into_one_file():
    """The ROM reboots once it has seen `numBlocks` distinct block numbers. A
    block count left at Katapult's own would reboot the board before the erase
    pages land - or never, if the numbers collide."""
    katapult = [FLASH + i * PAGE_SIZE for i in range(3)]
    out = _fields(with_erased_sector(_image(katapult), APP))

    total = 3 + SECTOR_SIZE // PAGE_SIZE
    assert len(out) == total
    assert [b["no"] for b in out] == list(range(total))
    assert {b["count"] for b in out} == {total}


def test_erase_blocks_carry_the_images_flags_and_family():
    """The ROM drops a block whose family id is not its own, so an erase page
    stamped with anything but Katapult's family is silently never written."""
    image = _block(FLASH, b"\xaa" * PAGE_SIZE, flags=FAMILY_FLAG, family=0x12345678)
    out = _fields(with_erased_sector(image, APP))

    assert {b["family"] for b in out} == {0x12345678}
    assert {b["flags"] for b in out} == {FAMILY_FLAG}
    assert {b["magic"] for b in out} == {(MAGIC0, MAGIC1, MAGIC_END)}


def test_blocks_come_out_in_address_order():
    """The ROM flushes a sector when the address moves on; coming back to one
    it already wrote erases it again. Ascending order is the safe order."""
    image = _image([APP + SECTOR_SIZE, FLASH])
    targets = [b["target"] for b in _fields(with_erased_sector(image, APP))]
    assert targets == sorted(targets)


def test_an_image_that_already_writes_the_sector_is_refused():
    """Katapult's own pages at the application address mean the address is
    wrong, not that the pages should be overwritten with 0xff."""
    with pytest.raises(Uf2EraseError, match="overlap"):
        with_erased_sector(_image([FLASH, APP + SECTOR_SIZE - PAGE_SIZE]), APP)


def test_an_address_off_a_sector_boundary_is_refused():
    with pytest.raises(Uf2EraseError, match="sector"):
        with_erased_sector(_image([FLASH]), APP + PAGE_SIZE)


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\0" * 511,
        b"\0" * 512,
        b"\0\0\0\0" + _block(FLASH, b"\xaa" * PAGE_SIZE)[4:],
        _block(FLASH, b"\xaa" * PAGE_SIZE)[:4] + b"\0\0\0\0" + _block(FLASH, b"\xaa" * PAGE_SIZE)[8:],
        _block(FLASH, b"\xaa" * PAGE_SIZE)[:-4] + b"\0\0\0\0",
    ],
    ids=["empty", "short", "zeros", "bad-start-magic-0", "bad-start-magic-1", "bad-end-magic"],
)
def test_a_file_that_is_not_a_uf2_is_refused(blob):
    with pytest.raises(Uf2EraseError):
        with_erased_sector(blob, APP)


def test_an_oversized_payload_is_refused():
    bad = bytearray(_block(FLASH, b"\xaa" * PAGE_SIZE))
    struct.pack_into("<I", bad, 16, 477)
    with pytest.raises(Uf2EraseError):
        with_erased_sector(bytes(bad), APP)


def test_an_image_of_only_non_flash_blocks_is_refused():
    """No main-flash block means no flags or family to copy, and nothing a
    bootloader install would be."""
    with pytest.raises(Uf2EraseError):
        with_erased_sector(_block(FLASH, b"\xaa", flags=0x1), APP)


def test_launch_address_is_read_from_katapults_config(tmp_path):
    cfg = tmp_path / "katapult.config"
    cfg.write_text(
        "# header\nCONFIG_MACH_RP2040=y\nCONFIG_LAUNCH_APP_ADDRESS=0x10004000\n",
        encoding="utf-8",
    )
    assert launch_address(str(cfg)) == APP


@pytest.mark.parametrize(
    "text",
    ["CONFIG_MACH_RP2040=y\n", "CONFIG_LAUNCH_APP_ADDRESS=zzz\n", "CONFIG_LAUNCH_APP_ADDRESS=\n"],
    ids=["absent", "not-hex", "empty"],
)
def test_launch_address_is_none_when_the_config_cannot_say(tmp_path, text):
    cfg = tmp_path / "katapult.config"
    cfg.write_text(text, encoding="utf-8")
    assert launch_address(str(cfg)) is None


def test_launch_address_is_none_for_a_missing_config(tmp_path):
    assert launch_address(str(tmp_path / "nope.config")) is None


def test_the_sector_constants_match_the_rp2040():
    assert (uf2_erase.SECTOR_SIZE, uf2_erase.PAGE_SIZE) == (4096, 256)
