import pytest
from types import SimpleNamespace

from robot_modbus_lite.broadcast_queue import BroadcastMessage
from robot_modbus_lite.operator_ui_mixin import OperatorUiMixin
from robot_modbus_lite.speech_broadcast import (
    CallableSpeechSink,
    DoubaoSpeechSink,
    Pyttsx3SpeechSink,
    SpeechBroadcastDeliveryService,
    WindowsSapiSpeechSink,
)


class DummyOperator(OperatorUiMixin):
    pass


def test_speech_delivery_speaks_messages_in_given_order():
    spoken = []
    service = SpeechBroadcastDeliveryService(sink=CallableSpeechSink(spoken.append))
    messages = [
        BroadcastMessage(seq=2, kind="alert", text="报警", priority="high"),
        BroadcastMessage(seq=1, kind="progress", text="预检中", priority="normal"),
    ]

    result = service.deliver(messages)

    assert result.success is True
    assert result.delivered_seq == (2, 1)
    assert spoken == ["报警", "预检中"]


def test_speech_delivery_stops_and_reports_failure_when_sink_raises():
    def fail_on_alert(text: str) -> None:
        if text == "报警":
            raise RuntimeError("speaker offline")

    service = SpeechBroadcastDeliveryService(sink=CallableSpeechSink(fail_on_alert))
    messages = [
        BroadcastMessage(seq=2, kind="alert", text="报警", priority="high"),
        BroadcastMessage(seq=1, kind="progress", text="预检中", priority="normal"),
    ]

    result = service.deliver(messages)

    assert result.success is False
    assert result.delivered_seq == ()
    assert "speaker offline" in result.error


def test_speech_delivery_requires_sink():
    service = SpeechBroadcastDeliveryService()

    result = service.deliver([BroadcastMessage(seq=1, kind="alert", text="报警", priority="high")])

    assert result.success is False
    assert result.error == "未配置语音播报输出接口。"


def test_pyttsx3_speech_sink_uses_injected_engine():
    calls = []
    speaking_states = []

    class FakeEngine:
        def say(self, text: str) -> None:
            calls.append(("say", text))

        def runAndWait(self) -> None:
            speaking_states.append(sink.is_speaking)
            calls.append(("runAndWait", ""))

    sink = Pyttsx3SpeechSink(engine=FakeEngine())

    sink.speak("报警")

    assert speaking_states == [True]
    assert sink.is_speaking is False
    assert calls == [("say", "报警"), ("runAndWait", "")]


def test_windows_sapi_speech_sink_uses_dispatch_factory_each_call():
    calls = []
    speaking_states = []

    class FakeVoice:
        def Speak(self, text: str) -> None:
            speaking_states.append(sink.is_speaking)
            calls.append(("Speak", text))

    def dispatch(name: str):
        calls.append(("Dispatch", name))
        return FakeVoice()

    sink = WindowsSapiSpeechSink(dispatch_factory=dispatch)

    sink.speak("第一条")
    sink.speak("第二条")

    assert speaking_states == [True, True]
    assert sink.is_speaking is False
    assert calls == [
        ("Dispatch", "SAPI.SpVoice"),
        ("Speak", "第一条"),
        ("Dispatch", "SAPI.SpVoice"),
        ("Speak", "第二条"),
    ]


def test_doubao_speech_sink_synthesizes_and_plays_text():
    played = []

    class FakeClient:
        def synthesize_text(self, text):
            assert text == "执行完成"
            return b"pcm-data"

    sink = DoubaoSpeechSink(client=FakeClient(), player=lambda pcm, sample_rate: played.append((pcm, sample_rate)), sample_rate=24000)

    sink.speak("执行完成")

    assert played == [(b"pcm-data", 24000)]
    assert sink.is_speaking is False


def test_doubao_speech_sink_streams_audio_chunks_when_available():
    played = []
    events = []

    class FakeClient:
        def stream_synthesize_text(self, text, chunk_callback):
            assert text == "执行完成"
            chunk_callback(b"chunk-1")
            chunk_callback(b"chunk-2")

    class FakeStreamPlayer:
        def __init__(self, sample_rate):
            assert sample_rate == 24000

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")

        def write(self, pcm):
            played.append(pcm)

    sink = DoubaoSpeechSink(client=FakeClient(), stream_player_factory=FakeStreamPlayer, sample_rate=24000)

    sink.speak("执行完成")

    assert events == ["enter", "exit"]
    assert played == [b"chunk-1", b"chunk-2"]
    assert sink.is_speaking is False


def test_doubao_speech_sink_stop_marks_cancel_requested():
    stopped = []
    sink = DoubaoSpeechSink(client=object(), player=lambda _pcm, _sample_rate: None, stop_player=lambda: stopped.append("stop"))

    sink.stop()

    assert sink.is_speaking is False
    assert stopped == ["stop"]


def test_operator_configures_doubao_tts_from_env(monkeypatch):
    dummy = DummyOperator.__new__(DummyOperator)
    dummy.axis_ranges = SimpleNamespace(operator_tts_enabled=True)
    dummy.operator_speech_sink = None
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "doubao")

    sink = dummy._operator_configure_tts_from_settings()

    assert isinstance(sink, DoubaoSpeechSink)


def test_operator_delivers_doubao_tts_async():
    sink = DoubaoSpeechSink(client=object(), player=lambda _pcm, _sample_rate: None)

    assert OperatorUiMixin._operator_should_deliver_speech_async(sink) is True
