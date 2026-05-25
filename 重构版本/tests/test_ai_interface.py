import json
from types import SimpleNamespace

from robot_modbus_lite.ai_interface import AIInterface
from robot_modbus_lite.system_config import AxisRangeConfig, save_system_config


def test_ai_interface_reads_dialog_stream_from_daily_jsonl(tmp_path):
    log_dir = tmp_path / "dialog"
    log_dir.mkdir()
    (log_dir / "dialog_2026-05-25.jsonl").write_text(
        json.dumps({"role": "user", "text": "小正，状态", "result": "ok"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stream = AIInterface(dialog_log_dir=log_dir).get_dialog_stream()

    assert stream == [{"role": "user", "text": "小正，状态", "result": "ok"}]


def test_ai_interface_returns_device_status_snapshot_stream(tmp_path):
    source = SimpleNamespace(
        robot_x="1",
        robot_y="2",
        robot_z="3",
        robot_joints=(1, 2, 3, 4, 5, 6),
        alarm_code="0",
        alarm_text="正常",
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )

    stream = AIInterface(dialog_log_dir=tmp_path, dashboard_source=source).get_device_status_stream()

    assert len(stream) == 1
    assert stream[0]["boards"]["device_status"]["alarm"] is False
    assert stream[0]["position"]["x"] == "1"


def test_ai_interface_hot_updates_whitelisted_rule_file(tmp_path):
    rule_path = tmp_path / "flow_phrase_aliases.json"
    interface = AIInterface(dialog_log_dir=tmp_path, rule_paths={"flow_phrase_aliases": rule_path})

    result = interface.hot_update_rule("flow_phrase_aliases", {"小臂上下点头": [{"axis_no": 10}]})

    assert result["ok"] is True
    assert json.loads(rule_path.read_text(encoding="utf-8"))["小臂上下点头"][0]["axis_no"] == 10


def test_ai_interface_hot_updates_assistant_knowledge_base(tmp_path):
    rule_path = tmp_path / "assistant_knowledge_base.json"
    interface = AIInterface(dialog_log_dir=tmp_path, rule_paths={"assistant_knowledge_base": rule_path})

    result = interface.hot_update_rule(
        "assistant_knowledge_base",
        {"entries": [{"id": "x", "category": "usage", "keywords": ["帮助"], "content": "帮助内容"}]},
    )

    assert result["ok"] is True
    assert json.loads(rule_path.read_text(encoding="utf-8"))["entries"][0]["id"] == "x"


def test_ai_interface_rejects_unknown_rule_name(tmp_path):
    result = AIInterface(dialog_log_dir=tmp_path).hot_update_rule("unknown_rule", {"x": 1})

    assert result["ok"] is False
    assert "不支持" in result["message"]


def test_ai_interface_reads_and_updates_safety_params_with_layer_policy(tmp_path):
    config_path = tmp_path / "system_config.json"
    save_system_config(config_path, AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), safe_speed_max=100))
    interface = AIInterface(dialog_log_dir=tmp_path, system_config_path=config_path)

    params = interface.get_safety_params()
    ok, message = interface.set_safety_params({"safe_speed_max": 80}, permission_level="ai_optimizer")
    denied, denied_message = interface.set_safety_params({"emergency_codes": ["B2"]}, permission_level="ai_optimizer")

    assert params["values"]["safe_speed_max"] == 100
    assert ok is True
    assert "已更新" in message
    assert interface.get_safety_params()["values"]["safe_speed_max"] == 80
    assert denied is False
    assert "权限不足" in denied_message
