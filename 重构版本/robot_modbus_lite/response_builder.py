"""Standard operator-facing response messages."""

from __future__ import annotations

from dataclasses import dataclass

from .alarm_advice import AlarmAdviceBook


@dataclass(frozen=True)
class ResponseMessage:
    """A normalized language response for chat, voice, or logs."""

    kind: str
    text: str
    priority: str = "normal"
    context_id: str | None = None


class ResponseBuilder:
    """Builds consistent receipt, progress, result, and alert responses."""

    def receipt(self, context_id: str | None = None, *, input_mode: str = "text") -> ResponseMessage:
        text = "系统在线，正在识别。" if input_mode == "voice" else "收到，正在解析指令。"
        return ResponseMessage(kind="receipt", text=text, priority="normal", context_id=context_id)

    def progress(self, context_id: str | None = None, *, stage: str, percent: int | float | None = None) -> ResponseMessage:
        if percent is None:
            text = f"{stage}进行中。"
        else:
            bounded = max(0, min(100, int(round(float(percent)))))
            text = f"{stage}进度 {bounded}%。"
        return ResponseMessage(kind="progress", text=text, priority="normal", context_id=context_id)

    def reassurance(
        self,
        stage: str,
        *,
        device_status: str,
        communication_status: str,
        context_id: str | None = None,
    ) -> ResponseMessage:
        text = f"设备状态{device_status}，通讯{communication_status}，{stage}。"
        priority = "normal" if device_status == "正常" and communication_status == "正常" else "high"
        return ResponseMessage(kind="progress", text=text, priority=priority, context_id=context_id)

    def result(self, text: str, context_id: str | None = None, *, success: bool = True) -> ResponseMessage:
        return ResponseMessage(
            kind="result",
            text=text,
            priority="normal" if success else "high",
            context_id=context_id,
        )

    def alert(self, text: str, context_id: str | None = None) -> ResponseMessage:
        return ResponseMessage(kind="alert", text=text, priority="high", context_id=context_id)

    def from_log_entry(self, entry: dict[str, object]) -> ResponseMessage | None:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        result = str(entry.get("result", ""))
        detail = str(entry.get("detail", ""))

        if category == "应急" and action == "应急编码校验":
            if result == "拒绝":
                reason = detail.split("|", 1)[0].strip() or "未授权"
                return self.alert(f"应急指令已拒绝：{reason}。", context_id="emergency:rejected")
            if result == "成功":
                return self.alert("应急授权通过，正在执行急停。", context_id="emergency:authorized")
            return None

        if category == "自然语言" and action == "DeepSeek解析":
            if result == "开始":
                return self.progress(
                    "deepseek:fallback",
                    stage="本地规则未完全匹配，正在调用在线AI辅助识别",
                )
            if result == "成功":
                matched = detail.split("|", 1)[0].strip() or "已返回候选动作"
                return self.result(f"在线AI已匹配到：{matched}，正在进入安全链路。", context_id="deepseek:success")
            if result == "失败":
                return self.result("在线AI不可用或未通过校验，已回退本地规则。", context_id="deepseek:failed")
            return None

        if category == "系统" and action.startswith("系统命令 "):
            action_key = action.replace("系统命令 ", "", 1).strip()
            if result == "失败":
                return self.alert(f"系统命令失败：{detail or action_key}。", context_id=f"system:{action_key}:failed")
            if result != "成功":
                return None
            labels = {
                "sys_estop": "急停命令已执行。",
                "sys_pause": "系统已暂停。",
                "sys_resume": "系统已继续运行。",
                "sys_cancel": "当前任务取消命令已发送。",
                "alarm_reset": "报警复位已执行。",
            }
            text = labels.get(action_key)
            if not text:
                return None
            if action_key == "sys_estop":
                return self.alert(text, context_id=f"system:{action_key}")
            return self.result(text, context_id=f"system:{action_key}")

        if category == "系统" and action in {"sys_estop", "sys_pause", "sys_resume", "sys_cancel", "alarm_reset"}:
            if result == "失败":
                return self.alert(f"系统命令失败：{detail or action}。", context_id=f"system:{action}:failed")
            return None

        if category == "反馈" and action == "实时状态变化" and "报警" in detail:
            advice = self._alarm_advice_hint(detail)
            suffix = f"。建议：{advice}" if advice else ""
            return self.alert(f"设备状态变化：{detail}{suffix}", context_id=f"feedback:alarm:{detail}")

        if category == "安全预检" and result == "拒绝":
            return self.alert(f"安全预检未通过：{detail or action}", context_id="precheck:l1:rejected")

        if category == "运动预演" and result == "拒绝":
            return self.alert(f"运动规划预演未通过：{detail or action}", context_id="precheck:l2:rejected")

        if category == "连接" and result == "失败":
            return self.alert(f"通讯异常：{detail or action}", context_id="connection:failed")

        if category == "语音" and action == "麦克风识别":
            if result == "成功":
                return self.result(f"语音识别完成：{detail or '-'}", context_id="voice:recognized")
            if result == "失败":
                return self.alert(f"语音识别失败：{detail or '未返回有效文本'}。", context_id="voice:failed")

        if category == "用户页面" and action == "确认报警" and result == "成功":
            advice = self._alarm_advice_hint(detail)
            suffix = f"。建议：{advice}" if advice else ""
            return self.alert(f"报警已确认：{detail or '-'}{suffix}", context_id=f"alarm_ack:{detail or '-'}")

        if category == "用户页面" and action == "停止当前任务":
            if result == "成功":
                return self.result("已发送取消当前任务命令。", context_id="operator:stop_current")
            if result == "失败":
                return self.alert(f"取消当前任务失败：{detail or '-'}", context_id="operator:stop_current:failed")

        if category == "用户页面" and action == "停止流程" and result == "提示":
            return self.result("当前没有正在运行的任务。", context_id="operator:stop_current:none")

        if category == "自然语言":
            if action == "动作序列完成" and result == "成功":
                return self.result(f"执行完成：{detail or '动作序列已完成'}", context_id="nlp:sequence:completed")
            if action == "动作序列终止":
                return self.result(f"执行失败：{detail or '动作序列已终止'}", context_id="nlp:sequence:failed", success=False)

        if category == "流程":
            if action.startswith("流程完成 ") and result == "成功":
                return self.result(f"流程完成：{detail or action}", context_id="flow:completed")
            if result == "失败":
                return self.alert(f"流程异常：{detail or action}", context_id="flow:failed")

        if category == "六轴":
            if action.startswith("完成+报警 "):
                name = action.replace("完成+报警 ", "", 1).strip() or "-"
                advice = self._alarm_advice_hint(detail)
                suffix = f"。建议：{advice}" if advice else ""
                return self.alert(f"动作完成但存在报警：{name}，{detail or '请检查报警状态'}{suffix}", context_id=f"six_axis:{name}:alarm")
            if action.startswith("执行完成 ") and result == "成功":
                name = action.replace("执行完成 ", "", 1).strip() or "-"
                return self.result(f"动作执行完成：{name}。", context_id=f"six_axis:{name}:completed")

        return None

    @staticmethod
    def _alarm_advice_hint(detail: str) -> str:
        text = str(detail or "")
        if not text:
            return ""
        for code in (
            "E_STOP",
            "PAUSED",
            "OVER_SPEED",
            "OVER_ACCEL",
            "OVER_DECEL",
            "JOINT_LIMIT",
            "CART_LIMIT",
            "SINGULARITY",
            "COMM_STALE",
            "CONTROLLER_NOT_READY",
        ):
            if code in text:
                return AlarmAdviceBook.default().get(code).operator_hint
        keyword_codes = (
            ("急停", "E_STOP"),
            ("暂停", "PAUSED"),
            ("速度", "OVER_SPEED"),
            ("加速度", "OVER_ACCEL"),
            ("减速度", "OVER_DECEL"),
            ("限位", "JOINT_LIMIT"),
            ("奇异", "SINGULARITY"),
            ("通讯", "COMM_STALE"),
            ("未就绪", "CONTROLLER_NOT_READY"),
        )
        for keyword, code in keyword_codes:
            if keyword in text:
                return AlarmAdviceBook.default().get(code).operator_hint
        return ""
