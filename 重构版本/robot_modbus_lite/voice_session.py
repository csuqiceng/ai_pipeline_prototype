"""Continuous voice-session segmentation helpers."""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from math import sqrt


@dataclass
class VoiceSessionSegmenter:
    """Small energy-based VAD for splitting PCM into utterance segments."""

    silence_threshold: int = 350
    frame_ms: int = 20
    start_voice_ms: int = 300
    end_silence_ms: int = 1000
    min_segment_ms: int = 500
    max_segment_ms: int = 15000
    _prestart_frames: list[bytes] = field(default_factory=list)
    _segment_frames: list[bytes] = field(default_factory=list)
    _voiced_ms: int = 0
    _silence_ms: int = 0
    _segment_ms: int = 0
    _active: bool = False

    def reset(self) -> None:
        self._prestart_frames.clear()
        self._segment_frames.clear()
        self._voiced_ms = 0
        self._silence_ms = 0
        self._segment_ms = 0
        self._active = False

    def feed(self, pcm_frame: bytes, *, paused: bool = False) -> bytes | None:
        if paused:
            self.reset()
            return None
        frame = bytes(pcm_frame or b"")
        if not frame:
            return None
        voiced = self._is_voiced(frame)
        if not self._active:
            if not voiced:
                self._prestart_frames.clear()
                self._voiced_ms = 0
                return None
            self._prestart_frames.append(frame)
            self._voiced_ms += self.frame_ms
            if self._voiced_ms < self.start_voice_ms:
                return None
            self._active = True
            self._segment_frames = list(self._prestart_frames)
            self._segment_ms = len(self._segment_frames) * self.frame_ms
            self._prestart_frames.clear()
            self._silence_ms = 0
            return self._finish_if_too_long()

        self._segment_frames.append(frame)
        self._segment_ms += self.frame_ms
        if voiced:
            self._silence_ms = 0
        else:
            self._silence_ms += self.frame_ms
            if self._silence_ms >= self.end_silence_ms:
                return self._finish_segment()
        return self._finish_if_too_long()

    def _finish_if_too_long(self) -> bytes | None:
        if self._segment_ms >= self.max_segment_ms:
            return self._finish_segment(force=True)
        return None

    def _finish_segment(self, *, force: bool = False) -> bytes | None:
        segment_ms = self._segment_ms
        frames = list(self._segment_frames)
        self.reset()
        if not force and segment_ms < self.min_segment_ms:
            return None
        if segment_ms < self.min_segment_ms:
            return None
        return b"".join(frames)

    def _is_voiced(self, pcm_frame: bytes) -> bool:
        samples = array("h")
        usable = len(pcm_frame) - (len(pcm_frame) % samples.itemsize)
        if usable <= 0:
            return False
        samples.frombytes(pcm_frame[:usable])
        if not samples:
            return False
        rms = sqrt(sum(float(sample) * float(sample) for sample in samples) / len(samples))
        return rms >= self.silence_threshold
