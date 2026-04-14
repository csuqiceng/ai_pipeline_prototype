from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .iflytek_iat import (
    IFlytekIATClient,
    IFlytekIATConfig,
    IFlytekMicrophoneConfig,
)
from .license_manager import LicenseManager


def _ok_payload(text: str, chunks: list[str] | None = None) -> int:
    print(json.dumps({"ok": True, "text": text, "chunks": chunks or []}, ensure_ascii=False))
    return 0


def _error_payload(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="讯飞 IAT 子进程 worker")
    parser.add_argument("--mode", choices=["audio", "mic"], required=True)
    parser.add_argument("--input")
    parser.add_argument("--duration", type=float, default=4.0)
    parser.add_argument("--backend")
    parser.add_argument("--device", type=int)
    parser.add_argument("--debug-save-path")
    parser.add_argument("--stop-flag-path")
    parser.add_argument("--result-path")
    parser.add_argument("--use-license", action="store_true",
                        help="使用订阅模式（代理模式）")
    parser.add_argument("--cache-dir",
                        help="授权缓存目录（订阅模式时需要）")
    args = parser.parse_args(argv)

    try:
        if args.use_license:
            cache_dir = Path(args.cache_dir) if args.cache_dir else Path(__file__).resolve().parent
            license_manager = LicenseManager(cache_dir)
            client = IFlytekIATClient.from_license(license_manager)
        else:
            client = IFlytekIATClient(IFlytekIATConfig.from_env())
        if args.mode == "audio":
            if not args.input:
                return _write_result_and_exit(args.result_path, {"ok": False, "error": "音频识别缺少 --input 参数。"}, 1)
            result = client.transcribe_file(args.input)
            return _write_result_and_exit(args.result_path, {"ok": True, "text": result.text.strip(), "chunks": result.chunks}, 0)

        result = client.transcribe_microphone(
            IFlytekMicrophoneConfig(
                duration_sec=args.duration,
                preferred_backend=args.backend,
                device=args.device,
                debug_save_path=args.debug_save_path,
                stop_flag_path=args.stop_flag_path,
            )
        )
        return _write_result_and_exit(args.result_path, {"ok": True, "text": result.text.strip(), "chunks": result.chunks}, 0)
    except Exception as exc:
        return _write_result_and_exit(args.result_path, {"ok": False, "error": str(exc)}, 1)


def _write_result_and_exit(result_path: str | None, payload: dict[str, object], code: int) -> int:
    if result_path:
        Path(result_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
