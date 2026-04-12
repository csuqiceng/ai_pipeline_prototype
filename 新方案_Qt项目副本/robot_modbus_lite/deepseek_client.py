from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests


def _load_local_env_file() -> None:
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    for env_path in (package_root / ".env", project_root / ".env"):
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


_env_loaded = False


def _ensure_env_loaded() -> None:
    global _env_loaded
    if not _env_loaded:
        _load_local_env_file()
        _env_loaded = True


class DeepSeekClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.deepseek.com/v1/chat/completions"):
        _ensure_env_loaded()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DeepSeek API key is required. Please set DEEPSEEK_API_KEY.")
        self.base_url = base_url

    def generate(self, prompt: str, model: str = "deepseek-chat") -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个机械手控制系统的自然语言处理助手，负责将用户输入归类到模板、流程或系统动作。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
            "top_p": 0.95,
        }
        response = requests.post(self.base_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def parse_json(self, prompt: str, model: str = "deepseek-chat") -> dict[str, Any] | None:
        response = self.generate(prompt, model=model)
        # Try markdown code block first (```json ... ```)
        code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Fallback: extract first top-level {...} with balanced braces
        depth = 0
        start = None
        for i, ch in enumerate(response):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(response[start : i + 1])
                    except json.JSONDecodeError:
                        start = None
        return None
