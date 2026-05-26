import pytest

from robot_modbus_lite.broadcast_queue import BroadcastMessage
from robot_modbus_lite.speech_broadcast import (
    CallableSpeechSink,
    Pyttsx3SpeechSink,
    SpeechBroadcastDeliveryService,
    WindowsSapiSpeechSink,
)


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

    class FakeEngine:
        def say(self, text: str) -> None:
            calls.append(("say", text))

        def runAndWait(self) -> None:
            calls.append(("runAndWait", ""))

    sink = Pyttsx3SpeechSink(engine=FakeEngine())

    sink.speak("报警")

    assert calls == [("say", "报警"), ("runAndWait", "")]


def test_windows_sapi_speech_sink_uses_dispatch_factory_each_call():
    calls = []

    class FakeVoice:
        def Speak(self, text: str) -> None:
            calls.append(("Speak", text))

    def dispatch(name: str):
        calls.append(("Dispatch", name))
        return FakeVoice()

    sink = WindowsSapiSpeechSink(dispatch_factory=dispatch)

    sink.speak("第一条")
    sink.speak("第二条")

    assert calls == [
        ("Dispatch", "SAPI.SpVoice"),
        ("Speak", "第一条"),
        ("Dispatch", "SAPI.SpVoice"),
        ("Speak", "第二条"),
    ]
