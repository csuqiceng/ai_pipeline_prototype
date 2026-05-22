from pathlib import Path
from types import SimpleNamespace

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.nlp_mixin import NlpMixin
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


class DummyNlp(NlpMixin):
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
