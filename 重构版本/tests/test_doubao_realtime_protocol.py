import gzip
import json

from robot_modbus_lite.doubao_realtime_protocol import (
    CLIENT_AUDIO_ONLY_REQUEST,
    CLIENT_FULL_REQUEST,
    GZIP,
    JSON,
    MSG_WITH_EVENT,
    NO_SERIALIZATION,
    SERVER_FULL_RESPONSE,
    SAY_HELLO,
    generate_header,
    parse_response,
)


def test_generate_header_defaults_match_doubao_protocol():
    header = generate_header()

    assert header == bytes([0x11, (CLIENT_FULL_REQUEST << 4) | MSG_WITH_EVENT, (JSON << 4) | GZIP, 0x00])


def test_generate_header_audio_request_uses_no_serialization():
    header = generate_header(message_type=CLIENT_AUDIO_ONLY_REQUEST, serial_method=NO_SERIALIZATION)

    assert header[1] >> 4 == CLIENT_AUDIO_ONLY_REQUEST
    assert header[2] >> 4 == NO_SERIALIZATION
    assert header[2] & 0x0F == GZIP


def test_parse_server_full_response_with_json_payload():
    session_id = b"session-1"
    payload = gzip.compress(json.dumps({"results": [{"text": "你好"}]}, ensure_ascii=False).encode("utf-8"))
    packet = bytearray()
    packet.extend(generate_header(message_type=SERVER_FULL_RESPONSE))
    packet.extend((451).to_bytes(4, "big"))
    packet.extend(len(session_id).to_bytes(4, "big"))
    packet.extend(session_id)
    packet.extend(len(payload).to_bytes(4, "big"))
    packet.extend(payload)

    parsed = parse_response(bytes(packet))

    assert parsed["message_type"] == "SERVER_FULL_RESPONSE"
    assert parsed["event"] == 451
    assert parsed["session_id"] == "session-1"
    assert parsed["payload_msg"]["results"][0]["text"] == "你好"


def test_say_hello_event_constant_matches_demo_tts_request():
    assert SAY_HELLO == 300
