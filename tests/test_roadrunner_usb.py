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


def _install_serial(monkeypatch: pytest.MonkeyPatch, port: _Port) -> None:
    monkeypatch.setitem(
        sys.modules,
        "serial",
        types.SimpleNamespace(Serial=lambda *_args, **_kwargs: port),
    )


def test_bridge_sends_reboot_bootsel_and_accepts_only_empty_success(monkeypatch):
    bridge = _load_bridge()
    # RR, protocol 1, response opcode 0x82, one-byte status 0, CRC-8/ATM.
    port = _Port(b"RR\x01\x82\x01\x00\x5a")
    _install_serial(monkeypatch, port)

    result = bridge.run(
        argparse.Namespace(operation="bootsel", port="/dev/ttyACM0", uuid=None)
    )

    assert result == {"bootsel": True}
    # RR, protocol 1, request opcode 0x02, empty payload, CRC-8/ATM.
    assert port.written == b"RR\x01\x02\x00\xaf"


def test_bridge_cli_accepts_bootsel(monkeypatch, capsys):
    bridge = _load_bridge()
    port = _Port(b"RR\x01\x82\x01\x00\x5a")
    _install_serial(monkeypatch, port)

    assert bridge.main(["bootsel", "/dev/ttyACM0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"bootsel": True}


def test_bridge_refuses_nonzero_bootsel_status(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"RR\x01\x82\x01\x07\x4f")
    _install_serial(monkeypatch, port)

    with pytest.raises(bridge.ProtocolError, match="status 7"):
        bridge.run(
            argparse.Namespace(operation="bootsel", port="/dev/ttyACM0", uuid=None)
        )


def test_bridge_refuses_bootsel_success_with_payload(monkeypatch):
    bridge = _load_bridge()
    port = _Port(b"RR\x01\x82\x02\x00\x99\xfa")
    _install_serial(monkeypatch, port)

    with pytest.raises(bridge.ProtocolError, match="unexpected payload"):
        bridge.run(
            argparse.Namespace(operation="bootsel", port="/dev/ttyACM0", uuid=None)
        )
