"""Roadrunner's narrow direct-USB command bridge."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

PROVISIONED = "RR-0123456789ABCDEFGHJKMNPQRS"
REPLACEMENT = "RR-ZZZZZZZZZZZZZZZZZZZZZZZZZZ"


def _info_payload(serial: str = PROVISIONED) -> bytes:
    return (
        b"\x01\x01\x01"
        + b"\x0droadrunner-v1"
        + b"\x03dev"
        + bytes((len(serial),))
        + serial.encode("ascii")
        + bytes.fromhex("50543165187A4D1C")
    )


def _load_bridge() -> Any:
    spec = importlib.util.spec_from_file_location(
        "roadrunner_usb", Path(__file__).parents[1] / "scripts" / "roadrunner_usb.py"
    )
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


class _Port:
    def __init__(self, response: bytes) -> None:
        self.response = bytearray(response)
        self.written = b""

    def __enter__(self) -> _Port:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.written += data

    def flush(self) -> None:
        pass

    def read(self, count: int) -> bytes:
        data = bytes(self.response[:count])
        del self.response[:count]
        return data


def _install_serial(monkeypatch: pytest.MonkeyPatch, port: _Port) -> list[_Port]:
    opened: list[_Port] = []
    monkeypatch.setitem(
        sys.modules,
        "serial",
        types.SimpleNamespace(
            Serial=lambda *_args, **_kwargs: opened.append(port) or port
        ),
    )
    return opened


def test_bridge_confirms_info_and_reboots_on_the_same_open_endpoint(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"")
    opened = _install_serial(monkeypatch, port)
    requests: list[tuple[_Port, int, bytes]] = []

    def request(request_port, opcode, payload=b""):
        requests.append((request_port, opcode, payload))
        if opcode == 0x01:
            return 0, _info_payload()
        return 0, b""

    monkeypatch.setattr(bridge, "request", request)

    result = bridge.run(
        argparse.Namespace(operation="bootsel", port="/dev/ttyACM0", uuid=PROVISIONED)
    )

    assert result == {"bootsel": True}
    assert opened == [port]
    assert requests == [(port, 0x01, b""), (port, 0x02, b"")]


def test_bridge_does_not_reboot_a_replacement_endpoint(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"")
    _install_serial(monkeypatch, port)
    opcodes: list[int] = []

    def request(_port, opcode, _payload=b""):
        opcodes.append(opcode)
        if opcode != 0x01:
            pytest.fail("replacement endpoint received REBOOT_BOOTSEL")
        return 0, _info_payload(REPLACEMENT)

    monkeypatch.setattr(bridge, "request", request)

    with pytest.raises(bridge.ProtocolError, match="did not confirm"):
        bridge.run(
            argparse.Namespace(
                operation="bootsel", port="/dev/ttyACM0", uuid=PROVISIONED
            )
        )

    assert opcodes == [0x01]


def test_bridge_cli_accepts_bootsel(monkeypatch, capsys):
    bridge = _load_bridge()
    port = _Port(b"")
    _install_serial(monkeypatch, port)
    responses = iter(((0, _info_payload()), (0, b"")))
    monkeypatch.setattr(bridge, "request", lambda *_args, **_kwargs: next(responses))

    assert bridge.main(["bootsel", "/dev/ttyACM0", PROVISIONED]) == 0
    assert json.loads(capsys.readouterr().out) == {"bootsel": True}


def test_bridge_refuses_nonzero_bootsel_status(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"")
    _install_serial(monkeypatch, port)
    responses = iter(((0, _info_payload()), (7, b"")))
    monkeypatch.setattr(bridge, "request", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(bridge.ProtocolError, match="status 7"):
        bridge.run(
            argparse.Namespace(
                operation="bootsel", port="/dev/ttyACM0", uuid=PROVISIONED
            )
        )


def test_bridge_refuses_bootsel_success_with_payload(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"")
    _install_serial(monkeypatch, port)
    responses = iter(((0, _info_payload()), (0, b"unexpected")))
    monkeypatch.setattr(bridge, "request", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(bridge.ProtocolError, match="unexpected payload"):
        bridge.run(
            argparse.Namespace(
                operation="bootsel", port="/dev/ttyACM0", uuid=PROVISIONED
            )
        )


def _digest_fields(
    algorithm: int = 1,
    digest: int | None = 0xBBE38AA9,
    start: int = 0x10000000,
    length: int = 600,
) -> bytes:
    """The INFO fields a board built after the digest revision appends."""
    value = b"" if digest is None else digest.to_bytes(4, "little")
    return (
        bytes((algorithm, len(value)))
        + value
        + start.to_bytes(4, "little")
        + length.to_bytes(4, "little")
    )


def test_info_from_a_board_predating_the_digest_fields_still_parses():
    """The absence of the fields is how a pre-revision board is detected.

    Its payload ends at the flash UID. Reporting `digest_algorithm: 0` would
    mean something different - a current board that could not compute one.
    """
    bridge = _load_bridge()

    info = bridge.parse_info(0, _info_payload())

    assert info["serial"] == PROVISIONED
    assert info["flash_uid"] == "50543165187A4D1C"
    assert "digest_algorithm" not in info
    assert "image_start" not in info


def test_info_carrying_the_digest_fields_is_not_refused():
    """The regression this reader had: rejecting the whole reply, not one field.

    `parse_info` asserted the payload ended exactly at the flash UID, so every
    board built after the digest revision raised `ProtocolError` and was
    dropped from discovery entirely - strictly worse than the truncation the
    protocol document warns hosts about.

    The values are the document's golden vector, so a misread of endianness or
    field order shows up as a wrong number rather than as a pass.
    """
    bridge = _load_bridge()

    info = bridge.parse_info(0, _info_payload() + _digest_fields())

    assert info["digest_algorithm"] == bridge.DIGEST_CRC32_ISO_HDLC
    assert info["digest"] == 0xBBE38AA9
    assert info["image_start"] == 0x10000000
    assert info["image_length"] == 600
    assert info["flash_uid"] == "50543165187A4D1C"


def test_a_board_that_could_not_compute_a_digest_still_reports_its_range():
    bridge = _load_bridge()

    info = bridge.parse_info(
        0, _info_payload() + _digest_fields(algorithm=0, digest=None)
    )

    assert info["digest_algorithm"] == bridge.DIGEST_NONE
    assert info["digest"] is None
    assert info["image_length"] == 600


def test_unknown_trailing_fields_are_ignored_rather_than_refused():
    """Forward compatibility, learned the hard way one revision ago.

    The frame's CRC-8 already covers corruption, so refusing a longer payload
    catches nothing a bad wire would produce and breaks every board built
    against the next revision.
    """
    bridge = _load_bridge()

    info = bridge.parse_info(0, _info_payload() + _digest_fields() + b"\xff\xff")

    assert info["digest"] == 0xBBE38AA9


def test_a_digest_that_is_cut_short_is_still_refused():
    """Tolerating unknown fields is not tolerating a malformed known one."""
    bridge = _load_bridge()

    with pytest.raises(bridge.ProtocolError):
        bridge.parse_info(0, _info_payload() + bytes((1, 4)) + b"\x01\x02")


def test_no_digest_algorithm_with_a_digest_is_refused():
    bridge = _load_bridge()

    with pytest.raises(bridge.ProtocolError):
        bridge.parse_info(0, _info_payload() + _digest_fields(algorithm=0))


def test_a_flash_uid_cut_short_is_refused():
    """Eight bytes, not "whatever is left".

    Slicing past the end is silent in Python, so a truncated reply would have
    produced a short flash UID and been reported as a real one.
    """
    bridge = _load_bridge()

    with pytest.raises(bridge.ProtocolError):
        bridge.parse_info(0, _info_payload()[:-3])


def test_a_digest_image_range_cut_short_is_refused():
    """A complete digest does not imply a complete range.

    The digest is length-prefixed and validates itself; the two 32-bit range
    fields that follow are fixed-width and have nothing to check them but this.
    """
    bridge = _load_bridge()
    truncated = _digest_fields()[:-3]

    with pytest.raises(bridge.ProtocolError):
        bridge.parse_info(0, _info_payload() + truncated)
