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
    """
    DeepSeek 客户端

    支持两种模式:
    1. 自带 Key 模式: 用户配置自己的 DEEPSEEK_API_KEY
    2. 订阅模式: 通过 LicenseManager 获取 Token，走后台代理
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com/v1/chat/completions",
        model: str = "deepseek-chat"
    ):
        _ensure_env_loaded()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url
        self.model = model
        self._license_manager = None
        self._use_proxy = False

        if not self.api_key:
            raise ValueError("需要配置 DEEPSEEK_API_KEY 或使用 from_license() 工厂方法")

    @classmethod
    def from_license(cls, license_manager, model: str = "deepseek-chat") -> "DeepSeekClient":
        """工厂方法：从授权管理器创建客户端（代理模式）"""
        instance = cls.__new__(cls)
        instance.api_key = None
        instance.base_url = f"{license_manager.SERVER_URL}/api/v1/proxy/deepseek/chat"
        instance.model = model
        instance._license_manager = license_manager
        instance._use_proxy = True
        return instance

    @classmethod
    def from_env(cls, model: str = "deepseek-chat") -> "DeepSeekClient":
        """工厂方法：从环境变量创建客户端（自带 Key 模式）"""
        _ensure_env_loaded()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置")
        return cls(api_key=api_key, model=model)

    def generate(self, prompt: str, model: str | None = None) -> str:
        """生成回复"""
        if self._use_proxy:
            return self._generate_via_proxy(prompt, model or self.model)
        else:
            return self._generate_direct(prompt, model or self.model)

    def _generate_via_proxy(self, prompt: str, model: str) -> str:
        """通过后台代理生成回复"""
        token = self._license_manager.get_access_token()
        if not token:
            raise RuntimeError("授权无效或已过期，请重新激活")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个机械手控制系统的自然语言处理助手，负责将用户输入归类到模板、流程或系统动作。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }

        response = requests.post(self.base_url, headers=headers, json=payload, timeout=30)

        if response.status_code == 401:
            raise RuntimeError("授权已过期，请重新激活")
        elif response.status_code == 403:
            raise RuntimeError("当前授权未启用 DeepSeek 功能")
        elif response.status_code == 429:
            raise RuntimeError("本月配额已用尽")

        response.raise_for_status()
        result = response.json()
        return result["data"]["choices"][0]["message"]["content"]

    def _generate_direct(self, prompt: str, model: str) -> str:
        """直接调用 DeepSeek API"""
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

    def parse_json(self, prompt: str, model: str | None = None) -> dict[str, Any] | None:
        """生成并解析 JSON 响应"""
        response = self.generate(prompt, model)

        code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1).strip())
            except json.JSONDecodeError:
                pass

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
