from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


class IFlytekRTASRError(RuntimeError):
    pass


@dataclass
class IFlytekIATConfig:
    app_id: str
    api_key: str
    api_secret: str
    request_timeout: int = 30
    language: str = "zh_cn"
    domain: str = "iat"
    accent: str = "mandarin"
    format: str = "audio/L16;rate=16000"
    encoding: str = "raw"
    vad_eos: int = 2000
    vinfo: int = 1

    @classmethod
    def from_env(cls) -> "IFlytekIATConfig":
        _load_local_env_file()
        app_id = os.environ.get("IFLYTEK_APP_ID", "").strip()
        api_key = os.environ.get("IFLYTEK_API_KEY", "").strip()
        api_secret = os.environ.get("IFLYTEK_API_SECRET", "").strip()
        if not app_id:
            raise IFlytekRTASRError("缺少环境变量 IFLYTEK_APP_ID。")
        if not api_key:
            raise IFlytekRTASRError("缺少环境变量 IFLYTEK_API_KEY。")
        if not api_secret:
            raise IFlytekRTASRError("缺少环境变量 IFLYTEK_API_SECRET。")
        return cls(app_id=app_id, api_key=api_key, api_secret=api_secret)


@dataclass
class IFlytekIATResult:
    text: str
    chunks: list[str] = field(default_factory=list)


@dataclass
class IFlytekMicrophoneConfig:
    duration_sec: float = 4.0
    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2
    preferred_backend: str | None = None
    device: int | None = None
    warmup_sec: float = 0.2
    debug_save_path: str | None = None


class IFlytekIATClient:
    def __init__(self, config: IFlytekIATConfig) -> None:
        self.config = config
        try:
            from xfyunsdkspeech.iat_client import IatClient
        except Exception as exc:
            raise IFlytekRTASRError(
                f"加载已安装的讯飞官方 SDK 失败: {exc}。请先执行 `pip install xfyunsdkspeech`。"
            ) from exc

        self._client = IatClient(
            app_id=config.app_id,
            api_key=config.api_key,
            api_secret=config.api_secret,
            language=config.language,
            domain=config.domain,
            accent=config.accent,
            format=config.format,
            encoding=config.encoding,
            vad_eos=config.vad_eos,
            vinfo=config.vinfo,
            request_timeout=config.request_timeout,
        )

    def transcribe_file(self, file_path: str) -> IFlytekIATResult:
        path = Path(file_path)
        if not path.exists():
            raise IFlytekRTASRError(f"未找到音频文件: {path}")

        chunks: list[str] = []
        try:
            with path.open("rb") as audio_file:
                for chunk in self._client.stream(audio_file):
                    normalized = self._extract_text(chunk)
                    if normalized:
                        chunks.append(normalized)
        except Exception as exc:
            raise IFlytekRTASRError(f"讯飞 IAT 调用失败: {exc}") from exc

        return IFlytekIATResult(text="".join(chunks).strip(), chunks=chunks)

    def transcribe_microphone(self, mic_config: IFlytekMicrophoneConfig | None = None) -> IFlytekIATResult:
        mic_config = mic_config or IFlytekMicrophoneConfig()
        stream = self._open_microphone_stream(mic_config)
        chunks: list[str] = []
        try:
            for chunk in self._client.stream(stream):
                normalized = self._extract_text(chunk)
                if normalized:
                    chunks.append(normalized)
        except Exception as exc:
            raise IFlytekRTASRError(f"讯飞 IAT 麦克风调用失败: {exc}") from exc

        return IFlytekIATResult(text="".join(chunks).strip(), chunks=chunks)

    def _extract_text(self, chunk: object) -> str:
        if not isinstance(chunk, dict):
            return str(chunk).strip()

        result = []
        for ws_item in chunk.get("result", {}).get("ws", []):
            for cw_item in ws_item.get("cw", []):
                word = str(cw_item.get("w", "")).strip()
                if word:
                    result.append(word)
        return "".join(result).strip()

    def _open_microphone_stream(self, mic_config: IFlytekMicrophoneConfig):
        backends = []
        if mic_config.preferred_backend:
            backends.append(mic_config.preferred_backend)
        for candidate in ("sounddevice", "pyaudio"):
            if candidate not in backends:
                backends.append(candidate)

        errors: list[str] = []
        for backend in backends:
            try:
                if backend == "sounddevice":
                    return _SoundDeviceMicStream(mic_config)
                if backend == "pyaudio":
                    return _PyAudioMicStream(mic_config)
                errors.append(f"不支持的麦克风后端: {backend}")
            except Exception as exc:
                errors.append(f"{backend}: {exc}")

        joined = "\n".join(errors)
        raise IFlytekRTASRError(
            "无法打开麦克风。请先安装 sounddevice 或 pyaudio，并确认麦克风可用。\n"
            f"尝试结果:\n{joined}"
        )

    def list_microphone_devices(self, backend: str | None = None) -> list[dict[str, object]]:
        backends = []
        if backend:
            backends.append(backend)
        for candidate in ("sounddevice", "pyaudio"):
            if candidate not in backends:
                backends.append(candidate)

        errors: list[str] = []
        for candidate in backends:
            try:
                if candidate == "sounddevice":
                    return _list_sounddevice_input_devices()
                if candidate == "pyaudio":
                    return _list_pyaudio_input_devices()
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        joined = "\n".join(errors)
        raise IFlytekRTASRError(f"无法列出麦克风设备。\n{joined}")


def _load_local_env_file() -> None:
    package_root = Path(__file__).resolve().parent
    repo_root = package_root.parent
    env_candidates = [
        package_root / ".env",
        repo_root / ".env",
    ]
    for env_path in env_candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            normalized = line.strip()
            if not normalized or normalized.startswith("#") or "=" not in normalized:
                continue
            key, value = normalized.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


class _SoundDeviceMicStream:
    def __init__(self, config: IFlytekMicrophoneConfig) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法使用 sounddevice 麦克风后端。") from exc

        self._sd = sd
        self._config = config
        self._frames_remaining = int(config.duration_sec * config.sample_rate)
        self._bytes_per_frame = config.channels * config.sample_width_bytes
        self._captured = bytearray()
        self._closed = False
        self._stopped = False
        self._stream = sd.RawInputStream(
            samplerate=config.sample_rate,
            channels=config.channels,
            dtype="int16",
            blocksize=0,
            device=config.device,
        )
        self._stream.start()
        if config.warmup_sec > 0:
            time.sleep(config.warmup_sec)

    def read(self, bytes_requested: int) -> bytes:
        if self._closed or self._stopped or self._frames_remaining <= 0:
            return b""
        frames_to_read = min(self._frames_remaining, max(1, bytes_requested // self._bytes_per_frame))
        try:
            data, overflowed = self._stream.read(frames_to_read)
        except Exception:
            self._stopped = True
            return b""
        if overflowed:
            pass
        self._frames_remaining -= frames_to_read
        payload = bytes(data)
        self._captured.extend(payload)
        return payload

    def stop_stream(self) -> None:
        if self._closed or self._stopped:
            return
        try:
            self._stream.stop()
        finally:
            self._stopped = True

    def close(self) -> None:
        if self._closed:
            return
        try:
            if not self._stopped:
                self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        self._closed = True
        self._stopped = True
        _save_debug_audio(self._config.debug_save_path, bytes(self._captured))


class _PyAudioMicStream:
    def __init__(self, config: IFlytekMicrophoneConfig) -> None:
        try:
            import pyaudio
        except ImportError as exc:
            raise RuntimeError("未安装 pyaudio，无法使用 pyaudio 麦克风后端。") from exc

        self._pyaudio = pyaudio.PyAudio()
        self._config = config
        self._frames_remaining = int(config.duration_sec * config.sample_rate)
        self._bytes_per_frame = config.channels * config.sample_width_bytes
        self._captured = bytearray()
        self._closed = False
        self._stopped = False
        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=config.channels,
            rate=config.sample_rate,
            input=True,
            input_device_index=config.device,
            frames_per_buffer=max(1, 1280 // self._bytes_per_frame),
        )
        if config.warmup_sec > 0:
            time.sleep(config.warmup_sec)

    def read(self, bytes_requested: int) -> bytes:
        if self._closed or self._stopped or self._frames_remaining <= 0:
            return b""
        frames_to_read = min(self._frames_remaining, max(1, bytes_requested // self._bytes_per_frame))
        try:
            data = self._stream.read(frames_to_read, exception_on_overflow=False)
        except Exception:
            self._stopped = True
            return b""
        self._frames_remaining -= frames_to_read
        self._captured.extend(data)
        return data

    def stop_stream(self) -> None:
        if self._closed or self._stopped:
            return
        try:
            self._stream.stop_stream()
        finally:
            self._stopped = True

    def close(self) -> None:
        if self._closed:
            return
        try:
            if not self._stopped:
                self._stream.stop_stream()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        self._pyaudio.terminate()
        self._closed = True
        self._stopped = True
        _save_debug_audio(self._config.debug_save_path, bytes(self._captured))


def _list_sounddevice_input_devices() -> list[dict[str, object]]:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("未安装 sounddevice。") from exc

    result: list[dict[str, object]] = []
    for index, device in enumerate(sd.query_devices()):
        max_input = int(device.get("max_input_channels", 0))
        if max_input <= 0:
            continue
        result.append(
            {
                "index": index,
                "name": str(device.get("name", "")),
                "max_input_channels": max_input,
                "default_samplerate": device.get("default_samplerate"),
                "backend": "sounddevice",
            }
        )
    return result


def _list_pyaudio_input_devices() -> list[dict[str, object]]:
    try:
        import pyaudio
    except ImportError as exc:
        raise RuntimeError("未安装 pyaudio。") from exc

    audio = pyaudio.PyAudio()
    try:
        result: list[dict[str, object]] = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            max_input = int(info.get("maxInputChannels", 0))
            if max_input <= 0:
                continue
            result.append(
                {
                    "index": index,
                    "name": str(info.get("name", "")),
                    "max_input_channels": max_input,
                    "default_samplerate": info.get("defaultSampleRate"),
                    "backend": "pyaudio",
                }
            )
        return result
    finally:
        audio.terminate()


def _save_debug_audio(debug_save_path: str | None, payload: bytes) -> None:
    if not debug_save_path:
        return
    Path(debug_save_path).write_bytes(payload)
