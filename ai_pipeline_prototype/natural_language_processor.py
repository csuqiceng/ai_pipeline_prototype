from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from .deepseek_client import DeepSeekClient
from .json_command import CommandType, JSONCommand, CommandParameters, Offset


class NaturalLanguageProcessor:
    """自然语言处理器，负责将自然语言输入转换为JSON指令"""

    def __init__(self, use_deepseek: bool = False, deepseek_api_key: str | None = None):
        # 定义关键词和模式
        self.command_patterns = {
            CommandType.MOVE: [
                r"移动到(.+)",
                r"走到(.+)",
                r"去到(.+)",
                r"移动至(.+)",
                r"走到(.+)位置",
                r"移动到(.+)位置"
            ],
            CommandType.GRASP: [
                r"抓取(.+)",
                r"抓住(.+)",
                r"夹起(.+)",
                r"拿起(.+)",
                r"握住(.+)"
            ],
            CommandType.RELEASE: [
                r"释放",
                r"放开",
                r"松开",
                r"放下"
            ],
            CommandType.HOME: [
                r"回零",
                r"回到原点",
                r"复位",
                r"回家"
            ],
            CommandType.STOP: [
                r"停止",
                r"停下",
                r"急停",
                r"暂停"
            ],
            CommandType.OFFSET_MOVE: [
                r"向上移动(.+)毫米",
                r"向下移动(.+)毫米",
                r"向左移动(.+)毫米",
                r"向右移动(.+)毫米",
                r"向前移动(.+)毫米",
                r"向后移动(.+)毫米",
                r"向上调(.+)",
                r"向下调(.+)",
                r"向左调(.+)",
                r"向右调(.+)"
            ],
            CommandType.PICK_PLACE: [
                r"把(.+)从(.+)移到(.+)",
                r"将(.+)从(.+)搬到(.+)",
                r"把(.+)从(.+)拿到(.+)",
                r"将(.+)从(.+)放到(.+)"
            ]
        }

        # 位置映射
        self.position_map = {
            "第一个位置": "POSITION_1",
            "第二个位置": "POSITION_2",
            "第三个位置": "POSITION_3",
            "左边": "LEFT_POSITION",
            "右边": "RIGHT_POSITION",
            "中间": "CENTER_POSITION",
            "原点": "HOME"
        }
        
        # DeepSeek API 支持
        self.use_deepseek = use_deepseek
        self.deepseek_client = DeepSeekClient() if use_deepseek else None

    def process(self, text: str) -> JSONCommand:
        """处理自然语言输入，返回JSON指令"""
        text = text.strip()
        
        # 使用DeepSeek API处理自然语言
        if self.use_deepseek and self.deepseek_client:
            try:
                command_dict = self.deepseek_client.parse_command(text)
                # 转换为JSONCommand对象
                command_type = CommandType(command_dict.get("command", "MOVE"))
                params_data = command_dict.get("parameters", {})
                
                # 处理offset
                offset_data = params_data.get("offset")
                offset = Offset(**offset_data) if offset_data else None
                
                # 处理其他参数
                parameters = CommandParameters(
                    target=params_data.get("target"),
                    offset=offset,
                    speed=params_data.get("speed", 50),
                    relative=params_data.get("relative", False),
                    force=params_data.get("force"),
                    position=params_data.get("position"),
                    tasks=params_data.get("tasks")
                )
                
                return JSONCommand(
                    command=command_type,
                    parameters=parameters,
                    timestamp=command_dict.get("timestamp", datetime.now().isoformat())
                )
            except Exception as e:
                # 如果DeepSeek API调用失败，回退到规则匹配
                print(f"DeepSeek API调用失败，回退到规则匹配: {e}")
        
        # 尝试匹配各种命令模式
        for command_type, patterns in self.command_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    params = self._extract_parameters(command_type, match, text)
                    return JSONCommand(
                        command=command_type,
                        parameters=params,
                        timestamp=datetime.now().isoformat()
                    )
        
        # 如果没有匹配到任何命令，返回默认的MOVE命令
        return JSONCommand(
            command=CommandType.MOVE,
            parameters=CommandParameters(),
            timestamp=datetime.now().isoformat()
        )

    def _extract_parameters(self, command_type: CommandType, match: re.Match, text: str) -> CommandParameters:
        """提取命令参数"""
        params = CommandParameters()
        
        if command_type == CommandType.MOVE:
            target = match.group(1).strip()
            # 映射位置名称到标准化位置
            params.target = self.position_map.get(target, target)
            
            # 提取速度信息
            speed_match = re.search(r"速度(.+)%", text)
            if speed_match:
                try:
                    params.speed = int(speed_match.group(1))
                except ValueError:
                    pass
            
        elif command_type == CommandType.GRASP:
            target = match.group(1).strip()
            params.target = target
            
            # 提取力度信息
            force_match = re.search(r"力度(.+)N", text)
            if force_match:
                try:
                    params.force = float(force_match.group(1))
                except ValueError:
                    pass
        
        elif command_type == CommandType.OFFSET_MOVE:
            offset_value = match.group(1).strip()
            try:
                distance = float(offset_value)
            except ValueError:
                # 处理模糊描述，如"一点"、"一些"等
                distance = 5.0  # 默认值
            
            offset = Offset()
            if "向上" in text:
                offset.z = distance
            elif "向下" in text:
                offset.z = -distance
            elif "向左" in text:
                offset.x = -distance
            elif "向右" in text:
                offset.x = distance
            elif "向前" in text:
                offset.y = distance
            elif "向后" in text:
                offset.y = -distance
            
            params.offset = offset
            params.relative = True
            
        elif command_type == CommandType.PICK_PLACE:
            if match.lastindex >= 3:
                target = match.group(1).strip()
                source = match.group(2).strip()
                destination = match.group(3).strip()
                
                params.target = target
                # 这里可以根据需要扩展，添加源位置和目标位置的参数
        
        return params

    def process_to_json(self, text: str) -> Dict[str, Any]:
        """处理自然语言输入，返回JSON格式的指令"""
        command = self.process(text)
        return command.to_dict()
