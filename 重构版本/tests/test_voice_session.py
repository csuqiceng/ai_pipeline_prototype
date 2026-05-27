from array import array

from robot_modbus_lite.voice_session import VoiceSessionSegmenter


def pcm_frame(value: int, samples: int = 160) -> bytes:
    data = array("h", [value] * samples)
    return data.tobytes()


def test_segmenter_emits_segment_after_voiced_start_and_trailing_silence():
    segmenter = VoiceSessionSegmenter(
        silence_threshold=350,
        frame_ms=20,
        start_voice_ms=60,
        end_silence_ms=80,
        min_segment_ms=80,
        max_segment_ms=1000,
    )

    emitted = []
    for _ in range(3):
        result = segmenter.feed(pcm_frame(600))
        if result:
            emitted.append(result)
    for _ in range(4):
        result = segmenter.feed(pcm_frame(0))
        if result:
            emitted.append(result)

    assert len(emitted) == 1
    assert len(emitted[0]) == 7 * len(pcm_frame(0))


def test_segmenter_exposes_active_state_after_voiced_start():
    segmenter = VoiceSessionSegmenter(
        silence_threshold=350,
        frame_ms=20,
        start_voice_ms=40,
        end_silence_ms=80,
        min_segment_ms=80,
        max_segment_ms=1000,
    )

    assert segmenter.is_active is False
    assert segmenter.feed(pcm_frame(600)) is None
    assert segmenter.is_active is False
    assert segmenter.feed(pcm_frame(600)) is None

    assert segmenter.is_active is True


def test_segmenter_drops_short_noise_burst():
    segmenter = VoiceSessionSegmenter(
        silence_threshold=350,
        frame_ms=20,
        start_voice_ms=20,
        end_silence_ms=40,
        min_segment_ms=100,
        max_segment_ms=1000,
    )

    emitted = []
    emitted.append(segmenter.feed(pcm_frame(600)))
    emitted.append(segmenter.feed(pcm_frame(0)))
    emitted.append(segmenter.feed(pcm_frame(0)))

    assert [item for item in emitted if item] == []


def test_segmenter_uses_rms_not_single_sample_spike():
    segmenter = VoiceSessionSegmenter(
        silence_threshold=350,
        frame_ms=20,
        start_voice_ms=20,
        end_silence_ms=40,
        min_segment_ms=20,
        max_segment_ms=1000,
    )
    samples = array("h", [0] * 159 + [2000])

    assert segmenter.feed(samples.tobytes()) is None


def test_segmenter_ignores_frames_while_paused_for_tts():
    segmenter = VoiceSessionSegmenter(
        silence_threshold=350,
        frame_ms=20,
        start_voice_ms=20,
        end_silence_ms=40,
        min_segment_ms=20,
        max_segment_ms=1000,
    )

    assert segmenter.feed(pcm_frame(800), paused=True) is None
    assert segmenter.feed(pcm_frame(0), paused=True) is None

    result = segmenter.feed(pcm_frame(800))
    assert result is None
    result = segmenter.feed(pcm_frame(0))
    assert result is None
    result = segmenter.feed(pcm_frame(0))
    assert result is not None
    assert len(result) == 3 * len(pcm_frame(0))
