"""Doubao realtime dialogue binary protocol helpers."""

from __future__ import annotations

import gzip
import json
from typing import Any

PROTOCOL_VERSION = 0b0001

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010

SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

START_CONNECTION = 1
FINISH_CONNECTION = 2
START_SESSION = 100
FINISH_SESSION = 102
TASK_REQUEST = 200
SAY_HELLO = 300
CHAT_TTS_TEXT = 500

NO_SEQUENCE = 0b0000
NEG_SEQUENCE = 0b0010
MSG_WITH_EVENT = 0b0100

NO_SERIALIZATION = 0b0000
JSON = 0b0001

NO_COMPRESSION = 0b0000
GZIP = 0b0001


def generate_header(
    *,
    version: int = PROTOCOL_VERSION,
    message_type: int = CLIENT_FULL_REQUEST,
    message_type_specific_flags: int = MSG_WITH_EVENT,
    serial_method: int = JSON,
    compression_type: int = GZIP,
    reserved_data: int = 0x00,
    extension_header: bytes = b"",
) -> bytes:
    header_size = int(len(extension_header) / 4) + 1
    header = bytearray()
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    header.extend(extension_header)
    return bytes(header)


def parse_response(packet: bytes | str) -> dict[str, Any]:
    if isinstance(packet, str) or not packet:
        return {}
    header_size = packet[0] & 0x0F
    message_type = packet[1] >> 4
    message_type_specific_flags = packet[1] & 0x0F
    serialization_method = packet[2] >> 4
    message_compression = packet[2] & 0x0F
    payload = packet[header_size * 4:]
    result: dict[str, Any] = {}

    if message_type in {SERVER_FULL_RESPONSE, SERVER_ACK}:
        result["message_type"] = "SERVER_ACK" if message_type == SERVER_ACK else "SERVER_FULL_RESPONSE"
        start = 0
        if message_type_specific_flags & NEG_SEQUENCE > 0:
            result["seq"] = int.from_bytes(payload[:4], "big", signed=False)
            start += 4
        if message_type_specific_flags & MSG_WITH_EVENT > 0:
            result["event"] = int.from_bytes(payload[start:start + 4], "big", signed=False)
            start += 4
        payload = payload[start:]
        session_id_size = int.from_bytes(payload[:4], "big", signed=True)
        session_id = payload[4:session_id_size + 4].decode("utf-8", errors="replace")
        result["session_id"] = session_id
        payload = payload[4 + session_id_size:]
        payload_size = int.from_bytes(payload[:4], "big", signed=False)
        payload_msg = payload[4:4 + payload_size]
    elif message_type == SERVER_ERROR_RESPONSE:
        result["message_type"] = "SERVER_ERROR_RESPONSE"
        result["code"] = int.from_bytes(payload[:4], "big", signed=False)
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:8 + payload_size]
    else:
        return result

    if message_compression == GZIP and payload_msg:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON and payload_msg:
        result["payload_msg"] = json.loads(payload_msg.decode("utf-8"))
    elif serialization_method == NO_SERIALIZATION:
        result["payload_msg"] = payload_msg
    else:
        result["payload_msg"] = payload_msg.decode("utf-8", errors="replace") if payload_msg else ""
    result["payload_size"] = payload_size
    return result
