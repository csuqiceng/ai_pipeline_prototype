from __future__ import annotations

from robot_modbus_lite.atomic_parser import AtomicParser


class PositionMemoryAgent:
    def __init__(self, *, parser: AtomicParser | None = None) -> None:
        self.parser = parser or AtomicParser()

    def apply(self, text: str) -> dict[str, object] | None:
        elements = self.parser.parse(text)
        if elements.family != "position" or not elements.name:
            return None
        if ":" not in elements.name:
            return None
        op, raw_name = str(elements.name).split(":", 1)
        name = raw_name.strip().upper()
        if not name:
            return None
        if op == "save":
            target = f"position_save:{name}"
            text_out = f"请求保存当前位置为位置{name}。执行后会写入本地位置库，不触发机械手动作。"
        elif op == "delete":
            target = f"position_delete:{name}"
            text_out = f"请求删除位置{name}。执行后会更新本地位置库，不触发机械手动作。"
        else:
            return None
        return {
            "kind": "position_memory_action",
            "action_type": "memory",
            "target": target,
            "text": text_out,
            "raw_text": text,
            "generates_robot_command": False,
        }
