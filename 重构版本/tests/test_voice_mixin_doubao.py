from types import SimpleNamespace

import pytest

from robot_modbus_lite.iflytek_iat import IFlytekIATClient, IFlytekMicrophoneConfig
from robot_modbus_lite.voice_mixin import VoiceMixin


class DummyVoice(VoiceMixin):
    def __init__(self):
        self._use_license_voice = False
        self._mic_process = None
        self._proxy_mic_capturing = False
        self._mic_recorder_thread = None
        self._local_voice_streaming = False
        self._voice_session_active = False
        self._voice_session_asr_busy = False
        self._doubao_streaming_asr_session = None
        self.logs = []
        self.critical = []
        self.status_label = SimpleNamespace(setText=lambda text: setattr(self, "status_text", text))
        self.mic_toggle_btn = SimpleNamespace(
            setText=lambda text: setattr(self, "mic_text", text),
            setEnabled=lambda enabled: setattr(self, "mic_enabled", bool(enabled)),
        )
        self.nlp_input_edit = SimpleNamespace(setPlainText=lambda text: setattr(self, "nlp_text", text))

    def _append_log(self, *args, **kwargs):
        self.logs.append((args, kwargs))

    def _show_critical(self, title, text):
        self.critical.append((title, text))

    def _run_in_background(self, work, on_result):
        try:
            on_result(work())
        except Exception as exc:
            on_result(exc)


def test_voice_asr_provider_defaults_to_doubao(monkeypatch):
    monkeypatch.delenv("VOICE_ASR_PROVIDER", raising=False)

    assert DummyVoice()._voice_asr_provider_name() == "doubao"


def test_voice_asr_provider_ignores_legacy_iflytek_setting(monkeypatch):
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "iflytek")

    assert DummyVoice()._voice_asr_provider_name() == "doubao"


def test_start_voice_session_default_doubao_does_not_create_iflytek(monkeypatch):
    monkeypatch.delenv("VOICE_ASR_PROVIDER", raising=False)
    dummy = DummyVoice()
    calls = []

    class FakeThread:
        def enable_session_mode(self, *, doubao_streaming=False):
            calls.append(("enable_session", doubao_streaming))

    dummy._create_iflytek_client = lambda: pytest.fail("iflytek should not be initialized")
    dummy._ensure_mic_stream = lambda: setattr(dummy, "_mic_recorder_thread", FakeThread())
    dummy._start_doubao_streaming_asr_session = lambda: calls.append(("doubao_streaming", True))
    dummy._start_voice_session_poll_timer = lambda: calls.append(("poll", True))

    dummy._start_voice_session()

    assert ("doubao_streaming", True) in calls
    assert ("enable_session", True) in calls
    assert dummy._voice_session_active is True


def test_manual_recording_default_doubao_does_not_create_iflytek(monkeypatch):
    monkeypatch.delenv("VOICE_ASR_PROVIDER", raising=False)
    dummy = DummyVoice()
    calls = []

    class FakeThread:
        def start_capturing(self):
            calls.append("capture")

    dummy._create_iflytek_client = lambda: pytest.fail("iflytek should not be initialized")
    dummy._ensure_mic_stream = lambda: setattr(dummy, "_mic_recorder_thread", FakeThread())

    dummy._start_microphone_recording()

    assert calls == ["capture"]
    assert dummy._proxy_mic_capturing is True
    assert dummy.mic_text == "停止录音"


def test_manual_pcm_recognition_default_doubao_uses_doubao_client(monkeypatch):
    monkeypatch.delenv("VOICE_ASR_PROVIDER", raising=False)
    dummy = DummyVoice()
    calls = []

    class FakeDoubaoClient:
        def transcribe_pcm(self, pcm_data, *, partial_callback=None):
            calls.append(pcm_data)
            return {"text": "移动到位置A", "timing": {"voice_mode": "doubao_asr"}}

    dummy._doubao_voice_client = FakeDoubaoClient()

    dummy._recognize_via_local(b"pcm")

    assert calls == [b"pcm"]
    assert dummy.nlp_text == "移动到位置A"
    assert dummy.status_text == "麦克风识别完成"


def test_iflytek_microphone_error_no_longer_mentions_pyaudio(monkeypatch):
    client = IFlytekIATClient.__new__(IFlytekIATClient)

    class BrokenSoundDevice:
        def __init__(self, _config):
            raise RuntimeError("sounddevice unavailable")

    monkeypatch.setattr("robot_modbus_lite.iflytek_iat._SoundDeviceMicStream", BrokenSoundDevice)

    with pytest.raises(Exception) as excinfo:
        client._open_microphone_stream(IFlytekMicrophoneConfig())

    message = str(excinfo.value)
    assert "sounddevice" in message
    assert "pyaudio" not in message.lower()
