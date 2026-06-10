import json

from tools.replay_interaction_loops import analyze_interaction_logs


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_replay_interaction_logs_accepts_finalized_non_execution_records(tmp_path):
    path = tmp_path / "interaction_session_ok.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "ok-1",
                "input": {"raw_text": "你好"},
                "nlp_result": {"engine": "chat", "intent": "chat"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "你好，我可以解释系统状态。"},
            }
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.total_records == 1
    assert report.violations == []
    assert report.ok is True


def test_replay_interaction_logs_flags_pending_empty_and_chat_action_promise(tmp_path):
    path = tmp_path / "interaction_session_bad.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "bad-1",
                "input": {"raw_text": "创建流程"},
                "nlp_result": {"engine": "pending", "intent": "pending"},
                "execution": {"result": "pending", "modbus_write": {}},
                "response": {"final": ""},
            },
            {
                "msg_id": "bad-2",
                "input": {"raw_text": "流程名字叫测试"},
                "nlp_result": {"engine": "chat", "intent": "chat"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "好的，已创建流程测试。"},
            },
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.ok is False
    codes = [(item.msg_id, item.code) for item in report.violations]
    assert ("bad-1", "NO_PENDING") in codes
    assert ("bad-1", "NO_EMPTY_FINAL") in codes
    assert ("bad-2", "NO_CHAT_ACTION_PROMISE") in codes


def test_replay_interaction_logs_flags_success_with_clarification_nlp_result(tmp_path):
    path = tmp_path / "interaction_session_mixed_result.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "mixed-1",
                "input": {"raw_text": "小正，移动到位置a"},
                "nlp_result": {
                    "engine": "agent_orchestrator",
                    "intent": "unknown",
                    "action_type": "clarification",
                    "reason": "请明确位置a的坐标或对应的预设位置名称。",
                },
                "execution": {
                    "result": "success",
                    "modbus_write": {
                        "query_key": "位置A",
                        "func_num": 108,
                    },
                },
                "response": {"final": "任务1002"},
            }
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.ok is False
    assert [(item.msg_id, item.code) for item in report.violations] == [
        ("mixed-1", "NO_SUCCESS_WITH_NON_EXECUTION_NLP")
    ]


def test_replay_interaction_logs_does_not_flag_clarification_examples_as_action_promise(tmp_path):
    path = tmp_path / "interaction_session_clarification_example.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "clarify-1",
                "input": {"raw_text": "小正，移动到位置a"},
                "nlp_result": {"engine": "agent_orchestrator", "intent": "unknown", "action_type": "clarification"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {
                    "final": "未识别位置“a”，请补充具体坐标、相对方向或已保存位置名称，如：移动到x=1000,y=0,z=1500"
                },
            }
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.ok is True


def test_replay_interaction_logs_flags_negative_flow_reference_answered_as_flow_detail(tmp_path):
    path = tmp_path / "interaction_session_negative_flow.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "neg-flow-1",
                "input": {"raw_text": "不是点头流程"},
                "nlp_result": {"engine": "context_query", "intent": "flow_query"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "流程“点头”共 10 步：\n01 移动到位置A\n02 小臂上下点头"},
            }
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.ok is False
    assert [(item.msg_id, item.code) for item in report.violations] == [
        ("neg-flow-1", "NO_NEGATED_FLOW_DETAIL")
    ]


def test_replay_interaction_logs_flags_followup_execute_without_pending_context(tmp_path):
    path = tmp_path / "interaction_session_followup_execute.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "follow-1",
                "input": {"raw_text": "我要执行我刚刚创建的命令"},
                "nlp_result": {"engine": "streaming_chat", "intent": "chat"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "当前流程是点头，请确认是否执行点头流程。"},
            }
        ],
    )

    report = analyze_interaction_logs([path])

    assert report.ok is False
    assert [(item.msg_id, item.code) for item in report.violations] == [
        ("follow-1", "NO_FOLLOWUP_EXECUTE_WITHOUT_PENDING")
    ]


def test_replay_interaction_logs_expands_directories(tmp_path):
    path = tmp_path / "interaction_session_nested.jsonl"
    legacy_path = tmp_path / "session_legacy.jsonl"
    _write_jsonl(
        path,
        [
            {
                "msg_id": "ok-1",
                "input": {"raw_text": "当前状态"},
                "nlp_result": {"engine": "status", "intent": "status_query"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "当前系统空闲。"},
            }
        ],
    )
    _write_jsonl(
        legacy_path,
        [
            {
                "role": "assistant",
                "text": "旧格式日志不是 interaction archive，不应被目录扫描误判。",
            }
        ],
    )

    report = analyze_interaction_logs([tmp_path])

    assert report.total_files == 1
    assert report.total_records == 1
    assert report.ok is True


def test_replay_interaction_logs_can_filter_session_files_by_date(tmp_path):
    old_path = tmp_path / "interaction_session_20260607_172840_bad.jsonl"
    new_path = tmp_path / "interaction_session_20260608_141148_ok.jsonl"
    _write_jsonl(
        old_path,
        [
            {
                "msg_id": "old-bad",
                "input": {"raw_text": "创建流程"},
                "nlp_result": {"engine": "pending", "intent": "pending"},
                "execution": {"result": "pending", "modbus_write": {}},
                "response": {"final": ""},
            }
        ],
    )
    _write_jsonl(
        new_path,
        [
            {
                "msg_id": "new-ok",
                "input": {"raw_text": "你好"},
                "nlp_result": {"engine": "chat", "intent": "chat"},
                "execution": {"result": "skipped", "modbus_write": {}},
                "response": {"final": "你好。"},
            }
        ],
    )

    report = analyze_interaction_logs([tmp_path], since_session="20260608_000000")

    assert report.total_files == 1
    assert report.total_records == 1
    assert report.ok is True
