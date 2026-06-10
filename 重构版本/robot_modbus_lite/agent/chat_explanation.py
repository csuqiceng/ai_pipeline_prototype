from __future__ import annotations

from robot_modbus_lite.atomic_capabilities import atomic_capability_rows, atomic_capability_summary


class ChatExplanationAgent:
    _CONTROL_KEYWORDS = (
        "执行",
        "走到",
        "移动",
        "到达",
        "急停",
        "暂停",
        "继续",
        "恢复",
        "复位",
        "清除报警",
        "等待",
        "延时",
        "IO",
        "io",
        "打开",
        "关闭",
    )

    def answer(self, text: str) -> dict[str, object] | None:
        compact = str(text or "").replace(" ", "")
        if not compact:
            return None
        if any(keyword in compact for keyword in self._CONTROL_KEYWORDS):
            return None

        if any(word in compact for word in ("天气", "气温", "下雨", "外面")):
            return {
                "kind": "chat_answer",
                "text": "我无法查询外部天气或实时互联网信息，只能基于当前机械手系统资料回答状态、流程、位置、报警和安全确认相关问题。没有触发机械手动作。",
                "generates_command": False,
            }

        if any(phrase in compact for phrase in ("你好", "您好", "hello", "hi")):
            return {
                "kind": "chat_answer",
                "text": "你好，我是机械手自然语言交互助手。可以帮你解释状态、整理流程草案和生成待确认的安全指令；这类问候不会触发机械手动作。",
                "generates_command": False,
            }

        if "L2" in compact or "运动规划" in compact or "规划预演" in compact or "预演" in compact:
            return {
                "kind": "chat_answer",
                "text": "L2 是运动规划预演：系统只检查和说明计划状态，不会直接触发机械手动作。",
                "generates_command": False,
            }

        if "为什么要确认" in compact or "为什么需要确认" in compact or "确认有什么用" in compact:
            return {
                "kind": "chat_answer",
                "text": "确认用于让操作者核对完整参数、安全预检结果和即将执行的动作。未确认前，Agent 不会触发机械手执行。",
                "generates_command": False,
            }

        if "提示" in compact and any(word in compact for word in ("失败", "错误", "阻断", "什么意思", "什么情况")):
            return {
                "kind": "chat_answer",
                "text": "这类提示是在说明当前指令处理状态；如果提示包含失败或阻断，表示系统没有生成可执行动作，也不会触发机械手动作。",
                "generates_command": False,
            }

        if any(
            phrase in compact
            for phrase in (
                "支持哪些原子命令",
                "支持哪些二次原子",
                "二次原子函数能力",
                "原子命令能力",
                "原子函数清单",
                "你能做什么",
                "能做什么",
                "帮助",
            )
        ):
            return {
                "kind": "chat_answer",
                "text": self._capability_text(),
                "generates_command": False,
            }

        if any(phrase in compact for phrase in ("你是谁", "你是什么", "自我介绍", "介绍一下")):
            return {
                "kind": "chat_answer",
                "text": "我是机械手自然语言交互助手，用来理解操作指令、解释状态和生成待确认的安全草案。说明类回答不会触发机械手动作。",
                "generates_command": False,
            }

        if any(phrase in compact for phrase in ("怎么使用", "如何使用", "使用方式", "怎么操作")):
            return {
                "kind": "chat_answer",
                "text": "可以直接输入坐标、IO、延时、关节或虚拟轴指令；涉及动作时系统会先生成草案并等待确认。也可以询问状态、报警、L2含义和支持哪些原子命令。",
                "generates_command": False,
            }

        return None

    @staticmethod
    def _capability_text() -> str:
        summary = atomic_capability_summary()
        rows = atomic_capability_rows()
        names = "、".join(str(row["name"]) for row in rows[:8])
        return (
            "二次原子函数能力包括："
            f"{names}。"
            f"当前共 {summary['total']} 项能力，已实现 {summary['implemented']} 项，基础实现 {summary['basic']} 项，"
            f"保护性拒绝 {summary['guarded']} 项，延期 {summary['deferred']} 项。"
            "这是能力说明，不会触发机械手动作。"
        )
