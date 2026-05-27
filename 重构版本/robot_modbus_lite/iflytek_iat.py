"""讯飞语音识别客户端和麦克风采集后端。"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

import requests


class IFlytekRTASRError(RuntimeError):
    """讯飞语音识别配置或运行失败异常。"""
    pass


def expected_env_locations() -> list[Path]:
    """处理期望环境变量。"""
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    return [package_root / ".env", project_root / ".env"]


@dataclass
class IFlytekIATConfig:
    """讯飞凭证和接口地址配置。"""
    app_id: str
    api_key: str
    api_secret: str
    request_timeout: int = 30
    language: str = "zh_cn"
    domain: str = "iat"
    accent: str = "mandarin"
    format: str = "audio/L16;rate=16000"
    encoding: str = "raw"
    vad_eos: int = 800
    vinfo: int = 1

    @classmethod
    def from_env(cls) -> "IFlytekIATConfig":
        """处理环境变量。"""
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
    """一次语音识别的文本和调试文件结果。"""
    text: str
    chunks: list[str] = field(default_factory=list)


@dataclass
class IFlytekMicrophoneConfig:
    """麦克风采集后端、采样率和设备配置。"""
    duration_sec: float = 4.0
    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2
    preferred_backend: str | None = None
    device: int | None = None
    warmup_sec: float = 0.5
    debug_save_path: str | None = None
    stop_flag_path: str | None = None


class IFlytekIATClient:
    """讯飞语音识别客户端，负责上传音频并收集文本。"""
    def __init__(self, config: IFlytekIATConfig) -> None:
        """初始化对象。"""
        self.config = config
        self._license_manager = None
        self._use_proxy = False
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

    @classmethod
    def from_license(cls, license_manager) -> "IFlytekIATClient":
        """处理授权。"""
        instance = cls.__new__(cls)
        instance.config = None
        instance._client = None
        instance._license_manager = license_manager
        instance._use_proxy = True
        return instance

    def transcribe_file(self, file_path: str, *, chunk_callback: Callable[[str], None] | None = None) -> IFlytekIATResult:
        """处理文件。"""
        if self._use_proxy:
            return self._transcribe_via_proxy(file_path)

        path = Path(file_path)
        if not path.exists():
            raise IFlytekRTASRError(f"未找到音频文件: {path}")

        chunks: list[str] = []
        try:
            audio_file = BytesIO(path.read_bytes())
            for chunk in self._client.stream(audio_file):
                normalized = self._extract_text(chunk)
                if normalized:
                    chunks.append(normalized)
                    if chunk_callback is not None:
                        chunk_callback("".join(chunks).strip())
        except Exception as exc:
            raise IFlytekRTASRError(f"讯飞 IAT 调用失败: {exc}") from exc

        return IFlytekIATResult(text="".join(chunks).strip(), chunks=chunks)

    def _transcribe_via_proxy(self, file_path: str) -> IFlytekIATResult:
        """处理代理。"""
        token = self._license_manager.get_access_token()
        if not token:
            raise IFlytekRTASRError("授权无效或已过期，请重新激活")

        path = Path(file_path)
        if not path.exists():
            raise IFlytekRTASRError(f"未找到音频文件: {path}")

        audio_base64 = base64.b64encode(path.read_bytes()).decode()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "audio_data": audio_base64,
            "audio_format": path.suffix.lstrip(".") or "raw",
        }

        proxy_url = f"{self._license_manager.SERVER_URL}/api/v1/proxy/voice/transcribe"

        response = requests.post(proxy_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 401:
            raise IFlytekRTASRError("授权已过期，请重新激活")
        elif response.status_code == 403:
            raise IFlytekRTASRError("当前授权未启用语音功能")
        elif response.status_code == 429:
            raise IFlytekRTASRError("今日语音配额已用尽")

        response.raise_for_status()
        result = response.json()

        text = result.get("data", {}).get("text", "")
        return IFlytekIATResult(text=text.strip(), chunks=[text.strip()] if text.strip() else [])

    def transcribe_microphone(
        self,
        mic_config: IFlytekMicrophoneConfig | None = None,
        *,
        chunk_callback: Callable[[str], None] | None = None,
    ) -> IFlytekIATResult:
        """处理麦克风。"""
        mic_config = mic_config or IFlytekMicrophoneConfig()

        # 代理模式：先录音到临时文件，再通过代理上传
        if self._use_proxy:
            return self._transcribe_mic_via_proxy(mic_config)

        stream = self._open_microphone_stream(mic_config)
        chunks: list[str] = []
        try:
            for chunk in self._client.stream(stream):
                normalized = self._extract_text(chunk)
                if normalized:
                    chunks.append(normalized)
                    if chunk_callback is not None:
                        chunk_callback("".join(chunks).strip())
        except Exception as exc:
            raise IFlytekRTASRError(f"讯飞 IAT 麦克风调用失败: {exc}") from exc
        finally:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        return IFlytekIATResult(text="".join(chunks).strip(), chunks=chunks)

    def _transcribe_mic_via_proxy(self, mic_config: IFlytekMicrophoneConfig) -> IFlytekIATResult:
        """处理麦克风代理。"""
        import tempfile

        stream = self._open_microphone_stream(mic_config)

        # 丢弃预热阶段的噪声帧，避免开头识别丢失。
        warmup_frames = int(mic_config.sample_rate * mic_config.warmup_sec)
        if warmup_frames > 0:
            stream.read(warmup_frames * mic_config.channels * mic_config.sample_width_bytes)

        captured = bytearray()
        try:
            while True:
                data = stream.read(3200)
                if not data:
                    break
                captured.extend(data)
        finally:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        if not captured:
            return IFlytekIATResult(text="", chunks=[])

        # 保存为临时文件再通过代理上传
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tmp:
            tmp.write(bytes(captured))
            tmp_path = tmp.name

        try:
            return self._transcribe_via_proxy(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _extract_text(self, chunk: object) -> str:
        """处理相关数据。"""
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
        """处理麦克风。"""
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

        raise IFlytekRTASRError(
            "无法打开麦克风。请先安装 sounddevice 或 pyaudio，并确认麦克风可用。\n"
            f"尝试结果:\n" + "\n".join(errors)
        )


def _load_local_env_file() -> None:
    """加载本地环境变量文件。"""
    for env_path in expected_env_locations():
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
    """基于声音设备库的麦克风采集流。"""
    def __init__(self, config: IFlytekMicrophoneConfig) -> None:
        """初始化对象。"""
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法使用 sounddevice 麦克风后端。") from exc

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
        """读取相关数据。"""
        if self._closed or self._stopped or self._frames_remaining <= 0 or _should_stop(self._config):
            return b""
        frames_to_read = min(self._frames_remaining, max(1, bytes_requested // self._bytes_per_frame))
        try:
            data, _overflowed = self._stream.read(frames_to_read)
        except Exception:
            self._stopped = True
            return b""
        self._frames_remaining -= frames_to_read
        payload = bytes(data)
        self._captured.extend(payload)
        return payload

    def stop_stream(self) -> None:
        """停止相关数据。"""
        if self._closed or self._stopped:
            return
        try:
            self._stream.stop()
        finally:
            self._stopped = True

    def close(self) -> None:
        """关闭相关数据。"""
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
    """基于音频库的备用麦克风采集流。"""
    def __init__(self, config: IFlytekMicrophoneConfig) -> None:
        """初始化对象。"""
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
        """读取相关数据。"""
        if self._closed or self._stopped or self._frames_remaining <= 0 or _should_stop(self._config):
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
        """停止相关数据。"""
        if self._closed or self._stopped:
            return
        try:
            self._stream.stop_stream()
        finally:
            self._stopped = True

    def close(self) -> None:
        """关闭相关数据。"""
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


def _save_debug_audio(debug_save_path: str | None, payload: bytes) -> None:
    """保存音频。"""
    if debug_save_path:
        Path(debug_save_path).write_bytes(payload)


def _should_stop(config: IFlytekMicrophoneConfig) -> bool:
    """停止相关数据。"""
    if not config.stop_flag_path:
        return False
    return Path(config.stop_flag_path).exists()
