from pathlib import Path
from types import SimpleNamespace

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.command_dispatch_mixin import CommandDispatchMixin
from robot_modbus_lite.memory_params import MemoryManager
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.nlp_mixin import NlpMixin
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionRegistry
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


class DummyNlp(NlpMixin):
    pass


class DummyDispatch(CommandDispatchMixin, NlpMixin):
    pass


def test_nlp_loads_atomic_memory_from_runtime_data(tmp_path):
    state_path = tmp_path / "data" / "atomic_state.json"
    memory = AtomicMemory()
    memory.set_speed(80)
    memory.save_position("A", (1, 2, 3, 4, 5, 6))
    memory.save(state_path)

    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy.service = SimpleNamespace(list_flow_names=lambda: ())
    dummy._deepseek_client = None
    dummy._append_log = lambda *args, **kwargs: None

    adapter = dummy._build_voice_nlp_adapter()

    assert adapter.atomic_memory.current_speed == 80.0
    assert adapter.atomic_memory.get_position("A") == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_nlp_build_adapter_migrates_legacy_atomic_positions_to_registry(tmp_path):
    state_path = tmp_path / "data" / "atomic_state.json"
    memory = AtomicMemory()
    memory.save_position("A", (1, 2, 3, 4, 5, 6))
    memory.save(state_path)

    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy.service = SimpleNamespace(list_flow_names=lambda: ())
    dummy._deepseek_client = None
    dummy._append_log = lambda *args, **kwargs: None

    dummy._build_voice_nlp_adapter()

    registry = PositionRegistry(
        Path(tmp_path) / "data" / "position_registry.json",
        permission=PermissionService("engineer"),
    )
    assert registry.get("A").pose == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_nlp_reuses_voice_adapter_to_keep_multiturn_flow_draft_state(tmp_path):
    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy.service = SimpleNamespace(list_flow_names=lambda: ())
    dummy._deepseek_client = None
    dummy._append_log = lambda *args, **kwargs: None

    adapter = dummy._build_voice_nlp_adapter()
    adapter._pending_flow_draft_payload = {"intent": "create_flow", "flowName": "打招呼"}
    adapter._pending_flow_missing_gesture = "小臂上下点头"

    reused = dummy._build_voice_nlp_adapter()

    assert reused is adapter
    assert reused._pending_flow_draft_payload == {"intent": "create_flow", "flowName": "打招呼"}
    assert reused._pending_flow_missing_gesture == "小臂上下点头"


def test_nlp_register_atomic_record_persists_last_record(tmp_path):
    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy._atomic_memory = AtomicMemory()
    dummy._append_log = lambda *args, **kwargs: None
    record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=107,
        params={"axis_no": 8, "pos_val": 3.0},
    )
    plan = SimpleNamespace(atomic_records={record.query_key: record})
    dummy._nlp_current_plan = plan

    assert dummy._nlp_register_atomic_record(record.query_key) is True

    loaded = AtomicMemory.load(Path(tmp_path) / "data" / "atomic_state.json")
    assert loaded.last_record is None
    assert loaded.current_speed == 50.0
    assert dummy.table[record.query_key] is record


def test_atomic_memory_persists_last_direction(tmp_path):
    memory = AtomicMemory()
    memory.record_direction(func_num=107, axis_no=6, direction=1, step=3)
    path = Path(tmp_path) / "data" / "atomic_state.json"
    memory.save(path)

    loaded = AtomicMemory.load(path)

    assert loaded.last_direction == (107.0, 6.0, 1.0)
    assert loaded.last_step == 3.0


def test_nlp_runs_multiple_atomic_actions_in_order(tmp_path, monkeypatch):
    import robot_modbus_lite.nlp_mixin as nlp_mixin

    monkeypatch.setattr(nlp_mixin.QTimer, "singleShot", lambda _ms, callback: callback())
    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy._atomic_memory = AtomicMemory()
    dummy._append_log = lambda *args, **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "busy", busy)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy.flow_running = False
    executed = []
    dummy._execute_query_key = lambda key, on_done=None: (executed.append((key, dummy.table[key].func_num)), on_done and on_done(True))
    plan = VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=dummy._atomic_memory).parse(
        "小正，上升3毫米然后IO1开"
    )
    dummy.nlp_sequence_running = True

    dummy._execute_nlp_plan(plan)

    assert [func_num for _, func_num in executed] == [107, 120]
    assert len(dummy.table) == 2


def test_nlp_save_position_writes_structured_registry(tmp_path):
    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy.service = SimpleNamespace(list_flow_names=lambda: ())
    dummy._deepseek_client = None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_current_pose_tuple = lambda: (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    plan = dummy._build_voice_nlp_adapter().parse("小正，保存当前位置为位置A")
    assert plan.actions
    assert plan.actions[0].target == "position_save:A"
    assert dummy._nlp_apply_memory_action(plan.actions[0], plan=plan) is True

    loaded_memory = AtomicMemory.load(Path(tmp_path) / "data" / "atomic_state.json")
    assert loaded_memory.get_position("A") is None
    dummy._save_atomic_memory()
    loaded_memory = AtomicMemory.load(Path(tmp_path) / "data" / "atomic_state.json")
    assert loaded_memory.get_position("A") == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    registry = PositionRegistry(
        Path(tmp_path) / "data" / "position_registry.json",
        permission=PermissionService("engineer"),
    )
    entry = registry.get("A")
    assert entry is not None
    assert entry.pose == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert entry.spd == 50


def test_nlp_successful_atomic_action_updates_memory_params(tmp_path, monkeypatch):
    import robot_modbus_lite.nlp_mixin as nlp_mixin

    monkeypatch.setattr(nlp_mixin.QTimer, "singleShot", lambda _ms, callback: callback())
    dummy = DummyNlp()
    dummy.runtime_root = tmp_path
    dummy.table = {}
    dummy._atomic_memory = AtomicMemory()
    dummy._append_log = lambda *args, **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "busy", busy)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy.flow_running = False
    dummy._execute_query_key = lambda key, on_done=None: on_done and on_done(True)
    plan = VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=dummy._atomic_memory).parse("小正，上升3毫米")
    dummy.nlp_sequence_running = True

    dummy._execute_nlp_plan(plan)

    memory = MemoryManager(Path(tmp_path) / "data" / "memory_params.json").memory
    assert memory.total_commands == 1
    assert memory.last_jog_speed_pct == 50


def test_command_dispatch_success_updates_memory_params_without_nlp_duplicate(tmp_path):
    dummy = DummyDispatch()
    dummy.runtime_root = tmp_path
    dummy.history = []
    dummy.task_id = 1
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._build_record_dispatch_snapshot = lambda record: {"query_key": record.query_key}
    dummy._refresh_all = lambda: None
    dummy._refresh_status_labels = lambda: None
    dummy._fmt = lambda value: str(value)
    record = QueryRecord(
        query_key="manual_jog",
        func_num=107,
        params={"axis_no": 8, "pos_val": 3.0, "spd_pct": 40},
    )
    dummy.table = {"manual_jog": record}

    dummy._after_send(record, True, "")
    dummy._update_memory_params_from_action("manual_jog")

    memory = MemoryManager(Path(tmp_path) / "data" / "memory_params.json").memory
    assert memory.total_commands == 1
    assert memory.last_jog_speed_pct == 40
