#!/usr/bin/env python3
"""Narrow JSON command bridge for Roadrunner's direct USB CDC endpoint.

This script deliberately neither scans ports nor creates identities.  The agent
selects a confirmed tty and supplies the random UUID; the script only speaks the
wire protocol to that one open device.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

SYNC = b"RR"
VERSION = 1
INFO = 0x01
PROVISION = 0x03
CLEAR = 0x04
MAX_REQUEST = 64


class ProtocolError(ValueError):
    pass


def crc8(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x07) & 0xFF if value & 0x80 else (value << 1) & 0xFF
    return value


def frame(opcode: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_REQUEST:
        raise ProtocolError("request payload exceeds 64 bytes")
    body = SYNC + bytes((VERSION, opcode, len(payload))) + payload
    return body + bytes((crc8(body),))


def read_response(port: Any, opcode: int) -> tuple[int, bytes]:
    header = port.read(5)
    if len(header) != 5:
        raise ProtocolError("short response header")
    if header[:2] != SYNC or header[2] != VERSION or header[3] != (opcode | 0x80):
        raise ProtocolError("response header does not match request")
    payload = port.read(header[4])
    crc = port.read(1)
    if len(payload) != header[4] or len(crc) != 1:
        raise ProtocolError("short response payload")
    if crc8(header + payload) != crc[0]:
        raise ProtocolError("response CRC mismatch")
    if not payload:
        raise ProtocolError("response has no status")
    return payload[0], payload[1:]


def request(port: Any, opcode: int, payload: bytes = b"") -> tuple[int, bytes]:
    port.reset_input_buffer()
    port.write(frame(opcode, payload))
    port.flush()
    return read_response(port, opcode)


def _take(payload: bytes, at: int, maximum: int, field: str) -> tuple[bytes, int]:
    if at >= len(payload):
        raise ProtocolError(f"INFO is missing {field} length")
    size = payload[at]
    at += 1
    if size > maximum or at + size > len(payload):
        raise ProtocolError(f"INFO has invalid {field} length")
    return payload[at : at + size], at + size


def parse_info(status: int, payload: bytes) -> dict[str, object]:
    if len(payload) < 4 + 8:
        raise ProtocolError("INFO payload is too short")
    store_state, transport, led_order = payload[:3]
    at = 3
    model, at = _take(payload, at, 13, "model")
    version, at = _take(payload, at, 32, "firmware version")
    serial, at = _take(payload, at, 33, "serial")
    if at + 8 != len(payload):
        raise ProtocolError("INFO has invalid trailing data")
    try:
        model_text = model.decode("ascii")
        version_text = version.decode("ascii")
        serial_text = serial.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("INFO strings are not ASCII") from exc
    return {
        "protocol": VERSION,
        "provisioned": status == 0,
        "status": status,
        "store_state": store_state,
        "transport": transport,
        "led_order": led_order,
        "model": model_text,
        "fw_version": version_text,
        "serial": serial_text,
        "flash_uid": payload[at:].hex().upper(),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pyserial is required (install python3-serial)") from exc

    with serial.Serial(args.port, 115200, timeout=2) as port:
        if args.operation == "info":
            status, payload = request(port, INFO)
            return parse_info(status, payload)
        if args.operation == "provision":
            uuid = bytes.fromhex(args.uuid)
            status, payload = request(port, PROVISION, uuid)
            if status:
                raise ProtocolError(f"PROVISION_UUID refused with status {status}")
            if not payload:
                raise ProtocolError("PROVISION_UUID response is missing serial")
            length = payload[0]
            if length != len(payload) - 1:
                raise ProtocolError("PROVISION_UUID response has invalid serial length")
            return {"serial": payload[1:].decode("ascii")}
        status, payload = request(port, CLEAR, b"RRCL")
        if status:
            raise ProtocolError(f"CLEAR_IDENTITY refused with status {status}")
        if payload:
            raise ProtocolError("CLEAR_IDENTITY response has unexpected payload")
        return {"cleared": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("info", "provision", "clear"))
    parser.add_argument("port", help="resolved USB CDC tty path")
    parser.add_argument("uuid", nargs="?", help="32 lowercase/uppercase hexadecimal UUID")
    args = parser.parse_args(argv)
    if args.operation == "provision" and (args.uuid is None or len(args.uuid) != 32):
        parser.error("provision requires one 32-hex-character UUID")
    try:
        result = run(args)
    except (OSError, RuntimeError, ValueError, ProtocolError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
