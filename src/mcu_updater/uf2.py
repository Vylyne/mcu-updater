"""The flash image a UF2 carries, and the digest a board should report back.

A `.uf2` is not the firmware. It is a stream of 512-byte blocks, each with a
32-byte header, a target address and *up to 476* bytes of payload; a digest of
the file is not a digest of what lands in flash. So a host that wants to ask
"are the bytes on the board the bytes I wrote?" has to reconstruct the flash
range out of the container first.

`roadrunner/scripts/uf2_image_digest.py` is the normative reference and this is
a port of it, not a second design. Both sides test against the golden vector in
`roadrunner/docs/roadrunner-usb-admin-protocol.md` rather than against each
other, because two implementations agreeing proves only that they are wrong
together.

Two rules are worth stating here because getting either wrong yields a
plausible number instead of a visible failure:

**Read `payloadSize` from every block.** 256 is what the RP2040 tooling emits;
it is not a rule of the format, and a fixture that assumes it passes while real
firmware fails.

**A gap is an error.** Filling an uncovered byte with `0xff` produces a digest
that looks like an answer and is not one.
"""

from __future__ import annotations

import binascii
import struct

UF2_BLOCK_SIZE = 512
UF2_HEADER_SIZE = 32
#: 512 less the 32-byte header and the 4-byte trailing magic.
UF2_MAX_PAYLOAD = UF2_BLOCK_SIZE - UF2_HEADER_SIZE - 4
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30

#: Block is not part of the flash image - metadata, or a file container.
UF2_FLAG_NOT_MAIN_FLASH = 0x00000001

#: Algorithm ids as the INFO payload's digest-algorithm byte spells them.
#: Kept in step with `scripts/roadrunner_usb.py`, which parses that byte.
DIGEST_NONE = 0
DIGEST_CRC32_ISO_HDLC = 1


class Uf2Error(Exception):
    """The container could not be reconstructed over the requested range."""


def crc32_iso_hdlc(data: bytes) -> int:
    """CRC-32/ISO-HDLC, the variant `zlib.crc32` computes.

    Reflected polynomial `0xEDB88320`, init and final XOR `0xFFFFFFFF`,
    reflected in and out. Named rather than called "CRC32", because "CRC32"
    alone names half a dozen mutually incompatible functions.
    """
    return binascii.crc32(data) & 0xFFFFFFFF


def _blocks(uf2: bytes) -> list[tuple[int, int, int, int]]:
    """Every block as `(offset, flags, target, payload_size)`, magic checked.

    Validation lives here so `extract_image` and `image_extent` cannot drift
    on what counts as a readable container - they are two questions about the
    same bytes, and one accepting a file the other rejects would mean an
    artifact we can describe but not digest.
    """
    if len(uf2) % UF2_BLOCK_SIZE != 0 or not uf2:
        raise Uf2Error(
            f"not a UF2 container: {len(uf2)} bytes is not a positive "
            f"multiple of {UF2_BLOCK_SIZE}"
        )

    out = []
    for offset in range(0, len(uf2), UF2_BLOCK_SIZE):
        magic0, magic1, flags, target, payload_size = struct.unpack_from(
            "<5I", uf2, offset
        )
        (magic_end,) = struct.unpack_from("<I", uf2, offset + UF2_BLOCK_SIZE - 4)
        if (
            magic0 != UF2_MAGIC_START0
            or magic1 != UF2_MAGIC_START1
            or magic_end != UF2_MAGIC_END
        ):
            raise Uf2Error(f"block at byte {offset} has bad magic")
        if payload_size > UF2_MAX_PAYLOAD:
            raise Uf2Error(
                f"block at byte {offset} claims a {payload_size}-byte payload"
            )
        out.append((offset, flags, target, payload_size))
    return out


def extract_image(uf2: bytes, start: int, length: int) -> bytes:
    """Reconstruct the flash bytes a UF2 places in `[start, start + length)`.

    `start` and `length` are the values the *board* reported, whenever there
    is a board to ask. A host must not substitute its own: the linked image
    does not end on a block boundary, so the last block is padded and only the
    reported length says where the image stops.
    """
    if length <= 0:
        raise Uf2Error(f"image length must be positive, got {length}")

    image = bytearray(length)
    covered = bytearray(length)

    for offset, flags, target, payload_size in _blocks(uf2):
        if flags & UF2_FLAG_NOT_MAIN_FLASH:
            continue
        for index in range(payload_size):
            slot = target + index - start
            if not 0 <= slot < length:
                continue
            image[slot] = uf2[offset + UF2_HEADER_SIZE + index]
            covered[slot] = 1

    if not all(covered):
        missing = covered.index(0)
        raise Uf2Error(
            f"UF2 does not cover the whole image: no block supplies "
            f"{start + missing:#010x}"
        )
    return bytes(image)


def image_extent(uf2: bytes) -> tuple[int, int]:
    """The `(start, length)` this container's own flash blocks describe.

    `record_build` has no board to ask - the artifact is being staged, not
    run - so it records the range the artifact covers, padding included. That
    is not a host substituting its own range for the board's: it is the
    artifact describing itself, so a later comparison can notice that the two
    ranges *disagree* rather than silently digesting different spans and
    reporting a mismatch it cannot explain.
    """
    spans = [
        (target, target + payload_size)
        for _offset, flags, target, payload_size in _blocks(uf2)
        if not flags & UF2_FLAG_NOT_MAIN_FLASH and payload_size
    ]
    if not spans:
        raise Uf2Error("UF2 has no flash blocks")
    start = min(span[0] for span in spans)
    return start, max(span[1] for span in spans) - start


def image_digest(
    uf2: bytes, start: int, length: int, algorithm: int = DIGEST_CRC32_ISO_HDLC
) -> int:
    """The digest a board running this UF2 over `[start, length)` reports."""
    if algorithm != DIGEST_CRC32_ISO_HDLC:
        raise Uf2Error(f"unsupported digest algorithm {algorithm}")
    return crc32_iso_hdlc(extract_image(uf2, start, length))


def digest_fields(path: str) -> dict[str, int]:
    """Describe a staged `.uf2`, or say nothing at all about it.

    The keys match the INFO payload's names on purpose, so the stored record
    and the board's report are compared field to field with no translation in
    between.

    Returns `{}` for anything it cannot read or parse. Absence is never
    mismatch: a build whose artifact cannot be described still built, and the
    comparison falls through to the version string exactly as it does for a
    board too old to report a digest. Raising here would fail builds over a
    provenance nicety.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        start, length = image_extent(data)
        digest = image_digest(data, start, length)
    except (OSError, Uf2Error, struct.error):
        return {}
    return {
        "digest_algorithm": DIGEST_CRC32_ISO_HDLC,
        "digest": digest,
        "image_start": start,
        "image_length": length,
    }
