"""A Katapult UF2 that also blanks the application's first sector.

DFU installs Katapult with `mass-erase`, so whatever the board ran before is
gone. BOOTSEL has no such command: the RP2040 ROM writes the pages a UF2 names
and leaves every other sector exactly as it was. A board that last ran some
other firmware keeps that image at the application address, and Katapult
chain-loads anything that looks like an application - so the board comes back
running the old firmware, or a half-overwritten one that boots nothing, instead
of sitting in Katapult waiting for Klipper.

The fix is to name the application's first sector in the same file, as pages of
`0xff`. The ROM erases a sector before writing it, so the vector table Katapult
checks for is gone and Katapult stays put. Only that one sector: it is what
Katapult inspects, and a whole-flash erase would mean megabytes of blocks copied
over mass storage for nothing.

Deliberately not a general UF2 library - it reads a container only well enough
to append to it.
"""

from __future__ import annotations

import struct

from . import profiles

UF2_BLOCK_SIZE = 512
UF2_HEADER_SIZE = 32
#: 512 less the 32-byte header and the 4-byte trailing magic.
UF2_MAX_PAYLOAD = UF2_BLOCK_SIZE - UF2_HEADER_SIZE - 4
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
#: Block is not part of the flash image - metadata, or a file container.
UF2_FLAG_NOT_MAIN_FLASH = 0x00000001

#: The RP2040's flash erase unit, and the page size its ROM expects per block.
SECTOR_SIZE = 4096
PAGE_SIZE = 256

_HEADER = struct.Struct("<8I")
_END = struct.Struct("<I")


class Uf2EraseError(Exception):
    """The image could not be extended with an erased sector."""


def launch_address(katapult_config: str) -> int | None:
    """Where Katapult looks for an application, from its saved `.config`.

    None when the file is missing or does not say - never a guess, because
    blanking the wrong sector either misses the old application or erases
    Katapult's own tail.
    """
    answers = profiles.answer_map(profiles.answer_lines(katapult_config))
    raw = answers.get(profiles.LAUNCH_ADDRESS_SYMBOL, "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def with_erased_sector(uf2: bytes, address: int) -> bytes:
    """`uf2` plus one sector of `0xff` pages at `address`, as a single file.

    Every block is renumbered and given the new total: the ROM reboots once it
    has seen `numBlocks` distinct block numbers, so Katapult's original count
    would reboot the board before the erase landed. Blocks are emitted in
    address order, because the ROM flushes a sector when the address moves on
    and coming back to one it already wrote erases it a second time.
    """
    if address % SECTOR_SIZE:
        raise Uf2EraseError(
            f"application address {address:#x} is not on a {SECTOR_SIZE}-byte "
            f"sector boundary"
        )

    blocks = _blocks(uf2)
    main = [b for b in blocks if not b[0] & UF2_FLAG_NOT_MAIN_FLASH]
    if not main:
        raise Uf2EraseError("the image writes nothing to flash")

    end = address + SECTOR_SIZE
    for _flags, target, payload, _family in main:
        if target < end and target + len(payload) > address:
            raise Uf2EraseError(
                f"the image already writes {target:#x}, which would overlap the "
                f"application sector at {address:#x}"
            )

    flags, _target, _payload, family = main[0]
    erased = [
        (flags, page, b"\xff" * PAGE_SIZE, family)
        for page in range(address, end, PAGE_SIZE)
    ]
    ordered = sorted(blocks + erased, key=lambda b: b[1])

    count = len(ordered)
    out = bytearray()
    for number, (flags, target, payload, family) in enumerate(ordered):
        out += _HEADER.pack(
            UF2_MAGIC_START0,
            UF2_MAGIC_START1,
            flags,
            target,
            len(payload),
            number,
            count,
            family,
        )
        out += payload.ljust(UF2_BLOCK_SIZE - UF2_HEADER_SIZE - _END.size, b"\0")
        out += _END.pack(UF2_MAGIC_END)
    return bytes(out)


def _blocks(uf2: bytes) -> list[tuple[int, int, bytes, int]]:
    """Every block as `(flags, target, payload, family)`, magic checked."""
    if not uf2 or len(uf2) % UF2_BLOCK_SIZE:
        raise Uf2EraseError(
            f"not a UF2 container: {len(uf2)} bytes is not a positive multiple "
            f"of {UF2_BLOCK_SIZE}"
        )
    out = []
    for offset in range(0, len(uf2), UF2_BLOCK_SIZE):
        magic0, magic1, flags, target, size, _no, _count, family = _HEADER.unpack_from(
            uf2, offset
        )
        (magic_end,) = _END.unpack_from(uf2, offset + UF2_BLOCK_SIZE - _END.size)
        if (
            magic0 != UF2_MAGIC_START0
            or magic1 != UF2_MAGIC_START1
            or magic_end != UF2_MAGIC_END
        ):
            raise Uf2EraseError(f"block at byte {offset} has bad magic")
        if size > UF2_MAX_PAYLOAD:
            raise Uf2EraseError(f"block at byte {offset} claims a {size}-byte payload")
        start = offset + UF2_HEADER_SIZE
        out.append((flags, target, uf2[start : start + size], family))
    return out
