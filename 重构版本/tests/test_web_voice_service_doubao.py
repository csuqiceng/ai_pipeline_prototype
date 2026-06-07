from robot_modbus_lite.web_voice_service import WebVoiceService


def test_web_voice_service_records_and_transcribes_with_doubao(monkeypatch):
    calls = []

    class FakeDoubaoClient:
        def transcribe_pcm(self, pcm_data):
            calls.append(pcm_data)
            return {"text": "走到位置A", "timing": {"voice_mode": "doubao_asr"}}

    monkeypatch.setattr("robot_modbus_lite.web_voice_service.DoubaoVoiceClient", FakeDoubaoClient)

    service = WebVoiceService()

    def fake_capture():
        service._recording_frames.append(b"pcm")
        service._stop_recording_event.wait(1)

    service._capture_microphone_until_stop = fake_capture

    started = service.start()
    assert started["running"] is True
    assert started["mode"] == "doubao_backend_capture"

    stopped = service.stop()

    assert calls == [b"pcm"]
    assert stopped["running"] is False
    assert stopped["phase"] == "idle"
    assert stopped["last_text"] == "走到位置A"
    assert stopped["last_error"] == ""
    assert service.consume_event()["text"] == "走到位置A"


def test_web_voice_service_capture_error_does_not_report_iflytek(monkeypatch):
    class FakeDoubaoClient:
        def transcribe_pcm(self, pcm_data):
            raise AssertionError("should not transcribe when capture failed")

    monkeypatch.setattr("robot_modbus_lite.web_voice_service.DoubaoVoiceClient", FakeDoubaoClient)

    service = WebVoiceService()

    def fake_capture():
        service._recording_error = RuntimeError("sounddevice unavailable")
        service._stop_recording_event.wait(1)

    service._capture_microphone_until_stop = fake_capture

    service.start()
    stopped = service.stop()

    assert stopped["running"] is False
    assert stopped["last_text"] == ""
    assert "sounddevice unavailable" in stopped["last_error"]
    assert "讯飞" not in stopped["last_error"]
