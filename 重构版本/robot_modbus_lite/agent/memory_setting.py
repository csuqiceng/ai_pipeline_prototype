from __future__ import annotations

from collections.abc import Callable
from typing import Any

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser


class MemorySettingAgent:
    def __init__(
        self,
        *,
        memory: AtomicMemory,
        parser: AtomicParser | None = None,
        save_callback: Callable[[AtomicMemory], None] | None = None,
    ) -> None:
        self.memory = memory
        self.parser = parser or AtomicParser()
        self.save_callback = save_callback

    def apply(self, text: str) -> dict[str, object] | None:
        elements = self.parser.parse(text)
        if elements.family != "memory":
            return None

        name = str(elements.name or "")
        if name == "speed" and elements.spd_pct is not None:
            self.memory.set_speed(float(elements.spd_pct))
        elif name == "speed_up":
            self.memory.speed_up()
        elif name == "speed_down":
            self.memory.speed_down()
        elif name == "step_mm" and elements.step is not None:
            self.memory.set_step_mm(float(elements.step))
        elif name == "step_deg" and elements.step is not None:
            self.memory.set_step_deg(float(elements.step))
        elif name == "confirm_mode":
            mode_by_target = {0: "expert", 1: "skilled", 2: "beginner"}
            target = 2 if elements.target is None else int(elements.target)
            self.memory.set_confirm_mode(mode_by_target.get(target, "beginner"))
        else:
            return None

        if self.save_callback is not None:
            self.save_callback(self.memory)

        text_out = (
            "已更新原子函数记忆参数："
            f"速度={self.memory.current_speed}%，"
            f"加速度={self.memory.current_acc}%，"
            f"减速度={self.memory.current_dec}%，"
            f"直线步长={self.memory.current_step_mm}mm，"
            f"角度步长={self.memory.current_step_deg}度，"
            f"确认模式={self.memory.confirm_mode}。"
        )
        return {
            "kind": "memory_setting_answer",
            "text": text_out,
            "generates_command": False,
            "params": self.memory.to_dict(),
        }
