from types import SimpleNamespace

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


def test_voice_session_segment_stops_current_speech_before_asr():
    dummy = DummyVoiceSession()
    stops = []
    dummy._voice_session_active = True
    dummy._operator_interrupt_current_speech_for_user_input = lambda: stops.append("stop")
    dummy._voice_session_process_next_segment = lambda: None

    dummy._on_mic_audio_segment(b"user-voice")

    assert stops == ["stop"]


def test_voice_session_voice_start_interrupts_speech_before_segment_done():
    dummy = DummyVoiceSession()
    stops = []
    dummy._voice_session_active = True
    dummy._operator_interrupt_current_speech_for_user_input = lambda: stops.append("stop")

    class Thread:
        def __init__(self):
            self.paused = []
            self.voice_start_count = 1
            self.reset_count = 0

        def pop_audio_capture(self):
            return None

        def set_session_paused(self, value):
            self.paused.append(value)

        def pop_voice_start(self):
            if self.voice_start_count:
                self.voice_start_count -= 1
                return True
            return False

        def reset_session_segmenter(self):
            self.reset_count += 1

        def pop_audio_segment(self):
            return None

    dummy._mic_recorder_thread = Thread()

    dummy._poll_voice_session_segments()

    assert stops == ["stop"]
    assert dummy._mic_recorder_thread.reset_count == 1


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
