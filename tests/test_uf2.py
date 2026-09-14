"""Reconstructing the flash image a UF2 carries, and digesting it."""

from __future__ import annotations

import pathlib
import struct

import pytest

from mcu_updater import uf2

#: The golden vector from `roadrunner/docs/roadrunner-usb-admin-protocol.md`.
#: Both sides test against these literals rather than against each other -
#: two implementations agreeing proves only that they are wrong together.
VECTOR_START = 0x10000000
VECTOR_LENGTH = 600
VECTOR_CRC = 0xBBE38AA9
#: What a host gets if it rounds the length up to the padded 768 bytes.
ROUNDED_UP_CRC = 0xDE16D2D8
#: What a host gets if it digests the container instead of the image.
CONTAINER_CRC = 0xAF9862DC

IMAGE = bytes((i * 7 + 3) & 0xFF for i in range(VECTOR_LENGTH))


def _block(
    addr: int,
    payload: bytes,
    *,
    flags: int = 0x2000,
    block_no: int = 0,
    total: int = 3,
    payload_size: int | None = None,
    magic0: int = uf2.UF2_MAGIC_START0,
    magic1: int = uf2.UF2_MAGIC_START1,
    magic_end: int = uf2.UF2_MAGIC_END,
) -> bytes:
    body = payload + b"\x00" * (476 - len(payload))
    size = len(payload) if payload_size is None else payload_size
    header = struct.pack(
        "<8I", magic0, magic1, flags, addr, size, block_no, total, 0xE48BFF56
    )
    return header + body + struct.pack("<I", magic_end)


def _vector_uf2(**kwargs: object) -> bytes:
    """The three-block container the golden vector describes.

    256 image bytes, 256 image bytes, then 88 image bytes followed by 168
    bytes of padding - so the container holds 768 bytes and only the reported
    length says where the image stops.
    """
    padded = IMAGE + b"\x00" * 168
    return b"".join(
        _block(
            VECTOR_START + n * 256, padded[n * 256 : (n + 1) * 256], block_no=n, **kwargs
        )
        for n in range(3)
    )


def test_the_golden_vector_digests_to_the_published_crc() -> None:
    assert uf2.image_digest(_vector_uf2(), VECTOR_START, VECTOR_LENGTH) == VECTOR_CRC


def test_rounding_the_length_up_to_the_padding_is_a_different_number() -> None:
    # Named so a future reader does not "fix" the length to a block multiple.
    assert uf2.image_digest(_vector_uf2(), VECTOR_START, 768) == ROUNDED_UP_CRC
    assert ROUNDED_UP_CRC != VECTOR_CRC


def test_digesting_the_container_is_a_different_number() -> None:
    assert uf2.crc32_iso_hdlc(_vector_uf2()) == CONTAINER_CRC
    assert CONTAINER_CRC != VECTOR_CRC


def test_the_reconstructed_image_is_the_image() -> None:
    assert uf2.extract_image(_vector_uf2(), VECTOR_START, VECTOR_LENGTH) == IMAGE


def test_a_payload_shorter_than_the_tooling_convention_is_honoured() -> None:
    # 256 is a convention of the tooling, not a rule of the format. A writer
    # that emits 200-byte payloads must digest to the same number.
    blocks = [
        _block(VECTOR_START + off, IMAGE[off : off + 200], block_no=n, total=3)
        for n, off in enumerate(range(0, VECTOR_LENGTH, 200))
    ]
    assert uf2.image_digest(b"".join(blocks), VECTOR_START, VECTOR_LENGTH) == VECTOR_CRC


def test_a_short_payload_does_not_read_into_the_padding() -> None:
    # The over-read has to be *visible*: the short block comes last in the
    # file but not last in address order, so the bytes a 256-byte assumption
    # would drag in from its padding land on top of a block already written
    # correctly. Ordered the other way, a later block repairs the damage and
    # the assumption survives.
    first = _block(VECTOR_START, IMAGE[:256], block_no=0)
    tail = _block(VECTOR_START + 344, IMAGE[344:600], block_no=1)
    short = _block(VECTOR_START + 256, IMAGE[256:344], block_no=2)
    digested = uf2.image_digest(first + tail + short, VECTOR_START, VECTOR_LENGTH)
    assert digested == VECTOR_CRC


def test_a_not_main_flash_block_inside_the_range_is_skipped() -> None:
    # Its target address lands *inside* the image, so the flag is the only
    # thing keeping its bytes out. Addressed outside, the range filter would
    # discard them anyway and this would prove nothing.
    metadata = _block(
        VECTOR_START + 300, b"\xa5" * 64, flags=0x2001, block_no=3, total=4
    )
    assert uf2.image_digest(
        _vector_uf2() + metadata, VECTOR_START, VECTOR_LENGTH
    ) == VECTOR_CRC


def test_a_hole_in_the_range_is_an_error_not_a_fill() -> None:
    # Filling a gap with 0xff yields a plausible wrong number instead of a
    # visible failure, which is the worse of the two by a wide margin.
    padded = IMAGE + b"\x00" * 168
    holed = _block(VECTOR_START, padded[:256], block_no=0) + _block(
        VECTOR_START + 512, padded[512:768], block_no=2
    )
    with pytest.raises(uf2.Uf2Error, match="0x10000100"):
        uf2.image_digest(holed, VECTOR_START, VECTOR_LENGTH)


def test_a_payload_size_past_the_block_is_refused() -> None:
    # Without the guard this reads past the payload into the trailing magic
    # and raises something other than Uf2Error - or, worse, does not raise.
    over = _block(VECTOR_START, IMAGE[:256], payload_size=500)
    with pytest.raises(uf2.Uf2Error, match="500"):
        uf2.extract_image(over, VECTOR_START, VECTOR_LENGTH)


def test_a_bad_first_magic_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="magic"):
        uf2.extract_image(
            _block(VECTOR_START, IMAGE[:256], magic0=0), VECTOR_START, 256
        )


def test_a_bad_second_magic_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="magic"):
        uf2.extract_image(
            _block(VECTOR_START, IMAGE[:256], magic1=0), VECTOR_START, 256
        )


def test_a_bad_trailing_magic_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="magic"):
        uf2.extract_image(
            _block(VECTOR_START, IMAGE[:256], magic_end=0), VECTOR_START, 256
        )


def test_a_file_that_is_not_a_block_multiple_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="512"):
        uf2.extract_image(_vector_uf2()[:-8], VECTOR_START, VECTOR_LENGTH)


def test_an_empty_range_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="positive"):
        uf2.extract_image(_vector_uf2(), VECTOR_START, 0)


def test_an_unsupported_algorithm_is_refused() -> None:
    with pytest.raises(uf2.Uf2Error, match="algorithm"):
        uf2.image_digest(
            _vector_uf2(), VECTOR_START, VECTOR_LENGTH, algorithm=uf2.DIGEST_NONE
        )


def test_the_extent_is_what_the_container_actually_covers() -> None:
    # `record_build` has no board to ask, so the artifact describes its own
    # range. 768, not 600: the padding is real flash the writer will place.
    assert uf2.image_extent(_vector_uf2()) == (VECTOR_START, 768)


def test_the_extent_ignores_not_main_flash_blocks() -> None:
    metadata = _block(0x20000000, b"\xa5" * 64, flags=0x2001, block_no=3, total=4)
    assert uf2.image_extent(_vector_uf2() + metadata) == (VECTOR_START, 768)


def test_an_extent_with_no_main_flash_blocks_is_refused() -> None:
    only_metadata = _block(VECTOR_START, b"\xa5" * 64, flags=0x2001, total=1)
    with pytest.raises(uf2.Uf2Error, match="no flash"):
        uf2.image_extent(only_metadata)


def test_digest_fields_describe_the_staged_artifact(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "roadrunner.uf2"
    path.write_bytes(_vector_uf2())
    assert uf2.digest_fields(str(path)) == {
        "digest_algorithm": uf2.DIGEST_CRC32_ISO_HDLC,
        "digest": ROUNDED_UP_CRC,
        "image_start": VECTOR_START,
        "image_length": 768,
    }


def test_digest_fields_are_absent_when_the_file_is_not_a_uf2(
    tmp_path: pathlib.Path,
) -> None:
    # Absence is never mismatch. A build that cannot be described still
    # builds, and the comparison falls through to the version string.
    path = tmp_path / "roadrunner.uf2"
    path.write_bytes(b"not a uf2")
    assert uf2.digest_fields(str(path)) == {}


def test_digest_fields_are_absent_when_the_file_is_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope.uf2"
    assert uf2.digest_fields(str(missing)) == {}
