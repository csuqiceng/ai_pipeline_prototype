from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


class DeepSeekClient:
    """DeepSeek API客户端"""

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.deepseek.com/v1/chat/completions"):
        # 优先使用传入的 api_key，如果没有则从环境变量中读取
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DeepSeek API key is required. Please set it in .env file or pass it as a parameter.")
        self.base_url = base_url

    def generate(self, prompt: str, model: str = "deepseek-chat") -> str:
        """调用DeepSeek API生成响应"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个机械手控制系统的自然语言处理助手，负责将用户的自然语言指令转换为结构化的JSON指令。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 512,
            "top_p": 0.95
        }

        response = requests.post(self.base_url, headers=headers, json=payload)
        response.raise_for_status()

        result = response.json()
        return result["choices"][0]["message"]["content"]

    def parse_command(self, text: str) -> Dict[str, Any]:
        """解析自然语言指令为JSON格式"""
        prompt = (
            "将以下自然语言指令转换为JSON格式的机械手控制命令。\n\n"
            "JSON格式要求：\n"
            "{\n"
            "  \"command\": \"命令类型\",\n"
            "  \"parameters\": {\n"
            "    \"target\": \"目标物体或位置\",\n"
            "    \"offset\": {\n"
            "      \"x\": 偏移量X,\n"
            "      \"y\": 偏移量Y,\n"
            "      \"z\": 偏移量Z,\n"
            "      \"rotation\": 旋转角度\n"
            "    },\n"
            "    \"speed\": 速度百分比,\n"
            "    \"relative\": 是否相对移动,\n"
            "    \"force\": 力度\n"
            "  },\n"
            "  \"timestamp\": \"时间戳\"\n"
            "}\n\n"
            "命令类型包括：MOVE、GRASP、RELEASE、HOME、STOP、OFFSET_MOVE、PICK_PLACE\n\n"
            "请只返回JSON格式，不要包含其他文字。\n\n"
            "指令：" + text
        )

        response = self.generate(prompt)
        # 提取JSON部分
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end != -1:
            json_str = response[start:end]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 如果解析失败，返回默认命令
                return {
                    "command": "MOVE",
                    "parameters": {
                        "target": text,
                        "offset": {
                            "x": 0.0,
                            "y": 0.0,
                            "z": 0.0,
                            "rotation": 0.0
                        },
                        "speed": 50,
                        "relative": False,
                        "force": None
                    },
                    "timestamp": "2026-04-02T12:00:00Z"
                }
        else:
            # 如果没有找到JSON，返回默认命令
            return {
                "command": "MOVE",
                "parameters": {
                    "target": text,
                    "offset": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": 0.0,
                        "rotation": 0.0
                    },
                    "speed": 50,
                    "relative": False,
                    "force": None
                },
                "timestamp": "2026-04-02T12:00:00Z"
            }
