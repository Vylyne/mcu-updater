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
