from robot_modbus_lite.response_builder import ResponseBuilder, ResponseMessage


def test_text_receipt_contains_expected_kind_and_text():
    builder = ResponseBuilder()

    message = builder.receipt("move-1", input_mode="text")

    assert message == ResponseMessage(
        kind="receipt",
        text="收到，正在解析指令。",
        priority="normal",
        context_id="move-1",
    )


def test_voice_receipt_uses_voice_specific_text():
    builder = ResponseBuilder()

    message = builder.receipt("voice-1", input_mode="voice")

    assert message.text == "系统在线，正在识别。"
    assert message.kind == "receipt"


def test_progress_message_includes_stage_and_percent():
    builder = ResponseBuilder()

    message = builder.progress("plan-1", stage="安全预检", percent=50)

    assert message.kind == "progress"
    assert message.text == "安全预检进度 50%。"
    assert message.context_id == "plan-1"


def test_reassurance_message_reports_device_and_communication_status():
    builder = ResponseBuilder()

    message = builder.reassurance(
        "正在进行安全预检",
        device_status="正常",
        communication_status="正常",
        context_id="precheck:reassurance",
    )

    assert message.kind == "progress"
    assert message.text == "设备状态正常，通讯正常，正在进行安全预检。"
    assert message.context_id == "precheck:reassurance"


def test_alert_message_uses_high_priority():
    builder = ResponseBuilder()

    message = builder.alert("报警发生：ERR_001")

    assert message.kind == "alert"
    assert message.priority == "high"
    assert message.text == "报警发生：ERR_001"


def test_log_broadcast_for_successful_estop_system_command():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "系统",
            "action": "系统命令 sys_estop",
            "result": "成功",
            "detail": "任务1001",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "急停命令已执行。"
    assert message.context_id == "system:sys_estop"


def test_log_broadcast_for_successful_cancel_system_command():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "系统",
            "action": "系统命令 sys_cancel",
            "result": "成功",
            "detail": "任务1002",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "当前任务取消命令已发送。"
    assert message.context_id == "system:sys_cancel"


def test_log_broadcast_for_rejected_emergency_code():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "应急",
            "action": "应急编码校验",
            "result": "拒绝",
            "detail": "missing_code | 急停",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "应急指令已拒绝：missing_code。"
    assert message.context_id == "emergency:rejected"


def test_feedback_alarm_broadcast_uses_stable_context_id():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "反馈",
            "action": "实时状态变化",
            "result": "提示",
            "detail": "报警 1001",
        }
    )

    assert message is not None
    assert message.text == "设备状态变化：报警 1001"
    assert message.context_id == "feedback:alarm:报警 1001"


def test_precheck_rejection_broadcasts_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "安全预检",
            "action": "确认执行",
            "result": "拒绝",
            "detail": "目标 X 超出软限位",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "安全预检未通过：目标 X 超出软限位"
    assert message.context_id == "precheck:l1:rejected"


def test_motion_plan_rejection_broadcasts_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "运动预演",
            "action": "确认执行",
            "result": "拒绝",
            "detail": "路径奇异点检查失败",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "运动规划预演未通过：路径奇异点检查失败"
    assert message.context_id == "precheck:l2:rejected"


def test_connection_failure_broadcasts_communication_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "连接",
            "action": "检测连接",
            "result": "失败",
            "detail": "timeout",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "通讯异常：timeout"
    assert message.context_id == "connection:failed"


def test_natural_language_sequence_completion_broadcasts_result():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "自然语言",
            "action": "动作序列完成",
            "result": "成功",
            "detail": "共执行 2 步",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "执行完成：共执行 2 步"
    assert message.context_id == "nlp:sequence:completed"


def test_flow_completion_broadcasts_result():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "流程",
            "action": "流程完成 demo",
            "result": "成功",
            "detail": "共完成 3 步",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "流程完成：共完成 3 步"
    assert message.context_id == "flow:completed"


def test_flow_failure_broadcasts_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "流程",
            "action": "并行组失败 第2步",
            "result": "失败",
            "detail": "夹爪失败",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "流程异常：夹爪失败"
    assert message.context_id == "flow:failed"


def test_six_axis_completion_broadcasts_result():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "六轴",
            "action": "执行完成 move_a",
            "result": "成功",
            "detail": "LONG(34)=1",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "动作执行完成：move_a。"
    assert message.context_id == "six_axis:move_a:completed"


def test_six_axis_alarm_completion_broadcasts_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "六轴",
            "action": "完成+报警 move_a",
            "result": "警告",
            "detail": "报警码 12",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "动作完成但存在报警：move_a，报警码 12"
    assert message.context_id == "six_axis:move_a:alarm"


def test_operator_stop_current_broadcasts_local_cancel_result():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "用户页面",
            "action": "停止当前任务",
            "result": "成功",
            "detail": "已发送 Func104 取消当前函数",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "已发送取消当前任务命令。"
    assert message.context_id == "operator:stop_current"


def test_operator_stop_current_without_running_task_broadcasts_hint():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "用户页面",
            "action": "停止流程",
            "result": "提示",
            "detail": "当前没有正在运行的流程",
        }
    )

    assert message is not None
    assert message.kind == "result"
    assert message.text == "当前没有正在运行的任务。"
    assert message.context_id == "operator:stop_current:none"


def test_blocked_direct_system_action_broadcasts_failure_alert():
    builder = ResponseBuilder()

    message = builder.from_log_entry(
        {
            "category": "系统",
            "action": "alarm_reset",
            "result": "失败",
            "detail": "流程执行中",
        }
    )

    assert message is not None
    assert message.kind == "alert"
    assert message.text == "系统命令失败：流程执行中。"
    assert message.context_id == "system:alarm_reset:failed"
