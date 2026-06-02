from types import SimpleNamespace
import queue

from robot_modbus_lite.voice_mixin import VoiceMixin


class DummyVoiceSession(VoiceMixin):
    pass


def test_voice_session_does_not_ignore_audio_while_tts_busy():
    dummy = DummyVoiceSession()
    dummy._operator_speech_async_busy = True

    assert dummy._voice_session_should_ignore_audio() is False


def test_voice_session_ignores_audio_while_ai_is_answering():
    dummy = DummyVoiceSession()

    dummy.nlp_sequence_running = True
    assert dummy._voice_session_should_ignore_audio() is True

    dummy.nlp_sequence_running = False
    dummy._operator_streaming_chat_active = True
    assert dummy._voice_session_should_ignore_audio() is True


def test_voice_session_does_not_ignore_audio_while_speech_sink_is_speaking():
    dummy = DummyVoiceSession()
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)

    assert dummy._voice_session_should_ignore_audio() is False


def test_voice_session_filters_speaker_echo_while_tts_is_speaking():
    dummy = DummyVoiceSession()
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "我是问答助手，可以回答系统能力和使用方式。"

    assert dummy._voice_session_should_drop_echo_text("我是问答助手，可以回答系统能力和使用方式") is True


def test_voice_session_keeps_interrupt_command_while_tts_is_speaking():
    dummy = DummyVoiceSession()
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "我是问答助手，可以回答系统能力和使用方式。"

    assert dummy._voice_session_should_drop_echo_text("停一下") is False


def test_voice_session_segment_stops_current_speech_before_asr():
    dummy = DummyVoiceSession()
    stops = []
    dummy._voice_session_active = True
    dummy._operator_interrupt_current_speech_for_user_input = lambda: stops.append("stop")
    dummy._voice_session_process_next_segment = lambda: None

    dummy._on_mic_audio_segment(b"user-voice")

    assert stops == ["stop"]


def test_voice_session_drops_segment_while_ai_is_answering():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy._operator_streaming_chat_active = True
    handled = []
    dummy._operator_handle_voice_session_text = handled.append

    dummy._on_mic_audio_segment(b"ai-voice")

    assert handled == []
    assert not hasattr(dummy, "_voice_session_segment_queue") or dummy._voice_session_segment_queue.empty()


def test_voice_session_segment_is_transcribed_and_routed_to_operator_handler():
    dummy = DummyVoiceSession()
    handled = []
    logs = []
    statuses = []
    dummy._voice_session_active = True
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._transcribe_pcm_via_local_client = lambda pcm, **_kwargs: {"text": "你好", "timing": {"voice_total_ms": 12}}
    dummy._operator_handle_voice_session_text = lambda text: handled.append(text)
    dummy._run_in_background = lambda work, on_result: on_result(work())

    dummy._on_mic_audio_segment(b"pcm")

    assert handled == ["你好"]
    assert statuses[-1] == "语音会话等待说话。"
    assert any(entry[0:3] == ("语音会话", "分段识别", "成功") for entry in logs)


def test_voice_session_passes_partial_callback_to_transcriber():
    dummy = DummyVoiceSession()
    seen_callback = []
    dummy._voice_session_active = True
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_handle_voice_session_text = lambda _text: None
    dummy._voice_session_update_partial_text = lambda text: seen_callback.append(text)
    dummy._run_on_main_thread = lambda callback: callback()

    def transcribe(_pcm, *, partial_callback=None):
        partial_callback("你")
        partial_callback("你好")
        return {"text": "你好", "timing": {}}

    dummy._transcribe_pcm_via_local_client = transcribe
    dummy._run_in_background = lambda work, on_result: on_result(work())

    dummy._on_mic_audio_segment(b"pcm")

    assert seen_callback == ["你", "你好"]


def test_voice_session_routes_to_doubao_asr_when_provider_is_doubao(monkeypatch):
    dummy = DummyVoiceSession()
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    calls = []

    class FakeClient:
        def transcribe_pcm(self, pcm, *, partial_callback=None):
            calls.append(pcm)
            if partial_callback is not None:
                partial_callback("移动")
            return {"text": "移动到位置A", "timing": {"voice_mode": "doubao_asr"}}

    dummy._get_doubao_voice_client = lambda: FakeClient()

    result = dummy._transcribe_pcm_for_voice_session(b"pcm")

    assert result["text"] == "移动到位置A"
    assert result["timing"]["voice_mode"] == "doubao_asr"
    assert calls == [b"pcm"]


def test_doubao_voice_session_poll_sends_raw_audio_chunks(monkeypatch):
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy._append_log = lambda *args, **kwargs: None
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    sent = []
    paused = []

    class FakeThread:
        def __init__(self):
            self._chunks = [b"chunk-1", None]

        def pop_audio_capture(self):
            return None

        def set_session_paused(self, value):
            paused.append(value)

        def pop_audio_chunk(self):
            return self._chunks.pop(0)

        def pop_audio_segment(self):
            raise AssertionError("doubao streaming mode must not use local VAD segments")

    class FakeStreamingSession:
        def send_audio(self, pcm):
            sent.append(pcm)

    dummy._mic_recorder_thread = FakeThread()
    dummy._doubao_streaming_asr_session = FakeStreamingSession()

    dummy._poll_voice_session_segments()

    assert paused == [False]
    assert sent == [b"chunk-1"]


def test_doubao_voice_session_reconnects_when_streaming_session_closed(monkeypatch):
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy._append_log = lambda *args, **kwargs: None
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    sent = []
    reconnects = []

    class FakeThread:
        def __init__(self):
            self._chunks = [b"chunk-after-idle", None]

        def pop_audio_capture(self):
            return None

        def set_session_paused(self, _value):
            return None

        def pop_audio_chunk(self):
            return self._chunks.pop(0)

        def pop_audio_segment(self):
            raise AssertionError("doubao streaming mode must not use local VAD segments")

    class ClosedSession:
        def is_alive(self):
            return False

        def send_audio(self, _pcm):
            raise AssertionError("closed session must not receive new audio")

    class FreshSession:
        def is_alive(self):
            return True

        def send_audio(self, pcm):
            sent.append(pcm)

    def reconnect():
        reconnects.append("reconnect")
        dummy._doubao_streaming_asr_session = FreshSession()

    dummy._mic_recorder_thread = FakeThread()
    dummy._doubao_streaming_asr_session = ClosedSession()
    dummy._start_doubao_streaming_asr_session = reconnect

    dummy._poll_voice_session_segments()

    assert reconnects == ["reconnect"]
    assert sent == [b"chunk-after-idle"]


def test_doubao_streaming_final_text_drops_speaker_echo():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "我是问答助手，可以回答系统能力和使用方式。"
    handled = []
    logs = []
    statuses = []
    dummy._operator_finish_voice_recognition_status = lambda _text: None
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_handle_voice_session_text = handled.append

    dummy._handle_doubao_streaming_final_text("我是问答助手，可以回答系统能力和使用方式")

    assert handled == []
    assert any(entry[0:3] == ("语音会话", "豆包回声过滤", "提示") for entry in logs)
    assert statuses[-1] == "语音会话等待说话。"


def test_doubao_streaming_final_text_clears_voice_status_when_echo_is_filtered():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "系统在线，自然语言理解功能正常。"
    cleared = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_clear_voice_recognition_status = lambda: cleared.append("clear")
    dummy._operator_handle_voice_session_text = lambda _text: None

    dummy._handle_doubao_streaming_final_text("系统在线自然语")

    assert cleared == ["clear"]
    assert any(entry[0:3] == ("语音会话", "豆包回声过滤", "提示") for entry in logs)


def test_doubao_streaming_speech_start_does_not_interrupt_tts_before_final_text():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "系统在线，自然语言理解功能正常。"
    events = []
    dummy._operator_interrupt_current_speech_for_user_input = lambda: events.append("interrupt")
    dummy._operator_begin_voice_recognition_status = lambda: events.append("begin")
    dummy.status_label = SimpleNamespace(setText=lambda text: events.append(("status", text)))

    dummy._handle_doubao_streaming_speech_start()

    assert "interrupt" not in events
    assert "begin" in events


def test_doubao_streaming_final_text_interrupts_tts_after_non_echo_text():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=True)
    dummy._operator_current_spoken_text = "系统在线，自然语言理解功能正常。"
    events = []
    dummy._operator_interrupt_current_speech_for_user_input = lambda: events.append("interrupt")
    dummy._operator_finish_voice_recognition_status = lambda text: events.append(("finish", text))
    dummy.status_label = SimpleNamespace(setText=lambda text: events.append(("status", text)))
    dummy._append_log = lambda *args, **kwargs: events.append(args)
    dummy._operator_handle_voice_session_text = lambda text: events.append(("handle", text))

    dummy._handle_doubao_streaming_final_text("帮我查询今天的天气")

    assert "interrupt" in events
    assert ("handle", "帮我查询今天的天气") in events


def test_doubao_streaming_final_text_drops_echo_after_speech_start_interrupt():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy.operator_speech_sink = SimpleNamespace(is_speaking=False)
    dummy._operator_current_spoken_text = ""
    dummy._operator_recent_spoken_text = "系统在线，自然语言理解功能正常。"
    dummy._operator_recent_spoken_until_sec = 100.0
    dummy._operator_now_seconds = lambda: 50.0
    handled = []
    logs = []
    statuses = []
    dummy._operator_finish_voice_recognition_status = lambda _text: None
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_handle_voice_session_text = handled.append

    dummy._handle_doubao_streaming_final_text("系统在线自然语")

    assert handled == []
    assert any(entry[0:3] == ("语音会话", "豆包回声过滤", "提示") for entry in logs)


def test_doubao_streaming_mode_skips_local_segmenter(monkeypatch):
    dummy = DummyVoiceSession()
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    starts = []

    class FailingSegmenter:
        def reset(self):
            starts.append("reset")

        def feed(self, *_args, **_kwargs):
            raise AssertionError("doubao streaming mode should not feed local VAD")

    class FakeThread:
        _session_enabled = True
        _session_paused = False
        _doubao_streaming_mode = True
        _chunk_queue = queue.Queue()
        _segment_queue = []
        _segmenter = FailingSegmenter()

        def handle_input_frame(self, frame):
            return dummy._mic_stream_handle_session_frame(self, frame)

    thread = FakeThread()

    thread.handle_input_frame(b"pcm")

    assert thread._chunk_queue.get_nowait() == b"pcm"
    assert thread._segment_queue == []


def test_start_voice_session_uses_doubao_streaming_session(monkeypatch):
    dummy = DummyVoiceSession()
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    logs = []
    starts = []

    class FakeThread:
        def enable_session_mode(self, *, doubao_streaming=False):
            starts.append(("enable", doubao_streaming))

    class FakeSession:
        def __init__(self, *args, **kwargs):
            starts.append(("session", kwargs))

        def start(self):
            starts.append("start")

    dummy._ensure_mic_stream = lambda: setattr(dummy, "_mic_recorder_thread", FakeThread())
    dummy._create_iflytek_client = lambda: (_ for _ in ()).throw(AssertionError("doubao streaming must not initialize iflytek"))
    dummy._start_voice_session_poll_timer = lambda: starts.append("timer")
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy.status_label = SimpleNamespace(setText=lambda text: starts.append(("status", text)))
    dummy.mic_toggle_btn = SimpleNamespace(setText=lambda text: starts.append(("button", text)), setEnabled=lambda enabled: None)
    monkeypatch.setattr("robot_modbus_lite.doubao_voice_client.DoubaoStreamingAsrSession", FakeSession)

    dummy._start_voice_session()

    assert dummy._voice_session_active is True
    assert ("enable", True) in starts
    assert "start" in starts
    assert any("豆包" in entry[3] for entry in logs)


def test_voice_session_partial_callback_is_scheduled_on_main_thread():
    dummy = DummyVoiceSession()
    scheduled = []
    updated = []
    dummy._run_on_main_thread = lambda callback: scheduled.append(callback)
    dummy._voice_session_update_partial_text = lambda text: updated.append(text)

    dummy._voice_session_schedule_partial_text("你好")

    assert updated == []
    assert len(scheduled) == 1
    scheduled[0]()
    assert updated == ["你好"]


def test_voice_session_partial_text_updates_input_before_final_result():
    dummy = DummyVoiceSession()
    partials = []
    dummy.operator_command_edit = SimpleNamespace(setText=partials.append, hasFocus=lambda: False)

    dummy._voice_session_update_partial_text("你好")
    dummy._voice_session_update_partial_text("你好小正")

    assert partials == ["你好", "你好小正"]


def test_voice_session_partial_text_does_not_overwrite_focused_manual_input():
    dummy = DummyVoiceSession()
    partials = []
    statuses = []
    dummy.operator_command_edit = SimpleNamespace(setText=partials.append, hasFocus=lambda: True)
    dummy._operator_update_voice_recognition_status = statuses.append

    dummy._voice_session_update_partial_text("小正移动到位置A")

    assert partials == []
    assert statuses == ["小正移动到位置A"]


def test_voice_session_shows_recognition_status_and_replaces_on_success():
    dummy = DummyVoiceSession()
    events = []
    dummy._voice_session_active = True
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_begin_voice_recognition_status = lambda: events.append(("begin", ""))
    dummy._operator_update_voice_recognition_status = lambda text: events.append(("update", text))
    dummy._operator_finish_voice_recognition_status = lambda text: events.append(("finish", text))
    dummy._transcribe_pcm_via_local_client = lambda _pcm, **_kwargs: {"text": "小镇移动到位置A", "timing": {}}
    dummy._operator_handle_voice_session_text = lambda _text: None
    dummy._run_in_background = lambda work, on_result: on_result(work())

    dummy._on_mic_audio_segment(b"pcm")

    assert events == [("begin", ""), ("finish", "小镇移动到位置A")]


def test_voice_session_updates_recognition_status_with_partial_text():
    dummy = DummyVoiceSession()
    events = []
    dummy._operator_update_voice_recognition_status = lambda text: events.append(text)

    dummy._voice_session_update_partial_text("小镇")

    assert events == ["小镇"]


def test_voice_session_clears_recognition_status_on_empty_result():
    dummy = DummyVoiceSession()
    events = []
    dummy._voice_session_active = True
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_begin_voice_recognition_status = lambda: events.append("begin")
    dummy._operator_clear_voice_recognition_status = lambda: events.append("clear")
    dummy._transcribe_pcm_via_local_client = lambda _pcm, **_kwargs: {"text": "", "timing": {}}
    dummy._run_in_background = lambda work, on_result: on_result(work())

    dummy._on_mic_audio_segment(b"pcm")

    assert events == ["begin", "clear"]


def test_voice_session_segment_queue_is_serial():
    dummy = DummyVoiceSession()
    dummy._voice_session_active = True
    dummy._voice_session_asr_busy = True
    dummy._append_log = lambda *args, **kwargs: None

    dummy._on_mic_audio_segment(b"first")
    dummy._on_mic_audio_segment(b"second")

    assert dummy._voice_session_segment_queue.qsize() == 2


def test_transcribe_pcm_uses_fresh_iflytek_client_for_each_segment(tmp_path):
    dummy = DummyVoiceSession()
    dummy._VOICE_FRAME_MS = 20
    dummy._VOICE_SILENCE_THRESHOLD = 1
    dummy._VOICE_PADDING_MS = 0
    clients = []
    reset_flags = []

    class FakeResult:
        text = "你好"

    class FakeClient:
        def transcribe_file(self, _path, *, chunk_callback=None):
            if chunk_callback is not None:
                chunk_callback("你好")
            return FakeResult()

    def get_client(*, reset=False):
        reset_flags.append(reset)
        client = FakeClient()
        clients.append(client)
        dummy._iflytek_local_client = client
        return client, 1

    dummy._get_local_iflytek_client = get_client
    dummy._iflytek_local_client = object()

    result = dummy._transcribe_pcm_via_local_client((100).to_bytes(2, "little", signed=True) * 1600)

    assert result["text"] == "你好"
    assert reset_flags[0] is True
    assert len(clients) == 1
    assert dummy._iflytek_local_client is None
