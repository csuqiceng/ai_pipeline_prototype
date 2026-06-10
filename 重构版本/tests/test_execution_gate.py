from robot_modbus_lite.execution_gate.gate import (
    EXECUTION_GATE_CHECK_ORDER,
    ExecutionGateInput,
    evaluate_execution_gate,
)


def test_execution_gate_exposes_stable_short_circuit_order():
    assert EXECUTION_GATE_CHECK_ORDER == (
        "wake_word",
        "permission",
        "params_complete",
        "bounds",
        "safety_precheck",
        "pending_confirm",
        "confirmed",
    )


def test_execution_gate_allows_non_execution_without_wake_word():
    result = evaluate_execution_gate(ExecutionGateInput(action_type="query", is_execution=False))

    assert result.ok is True
    assert result.state == "gate_skipped_non_execution"


def test_execution_gate_rejects_execution_without_wake_word_first():
    result = evaluate_execution_gate(ExecutionGateInput(action_type="move", is_execution=True, has_wake_word=False))

    assert result.ok is False
    assert result.state == "wake_word_required"
    assert result.errors[0]["code"] == "WAKE_WORD_REQUIRED"


def test_execution_gate_short_circuits_missing_params_before_safety():
    result = evaluate_execution_gate(
        ExecutionGateInput(
            action_type="move",
            is_execution=True,
            has_wake_word=True,
            missing_fields=("target_x",),
            bounds_ok=False,
            safety_ok=False,
        )
    )

    assert result.ok is False
    assert result.state == "missing_params"
    assert result.errors[0]["fields"] == ["target_x"]


def test_execution_gate_short_circuits_permission_before_missing_params():
    result = evaluate_execution_gate(
        ExecutionGateInput(
            action_type="move",
            is_execution=True,
            has_wake_word=True,
            permission_ok=False,
            missing_fields=("target_x",),
            bounds_ok=False,
            safety_ok=False,
        )
    )

    assert result.ok is False
    assert result.state == "permission_denied"


def test_execution_gate_short_circuits_bounds_before_safety():
    result = evaluate_execution_gate(
        ExecutionGateInput(
            action_type="move",
            is_execution=True,
            has_wake_word=True,
            bounds_ok=False,
            safety_ok=False,
        )
    )

    assert result.ok is False
    assert result.state == "bounds_failed"


def test_execution_gate_requires_confirmation_after_precheck():
    result = evaluate_execution_gate(
        ExecutionGateInput(
            action_type="move",
            is_execution=True,
            has_wake_word=True,
            missing_fields=(),
            bounds_ok=True,
            safety_ok=True,
            requires_confirmation=True,
            has_pending_confirm=False,
        )
    )

    assert result.ok is False
    assert result.state == "confirmation_required"


def test_execution_gate_allows_execution_after_confirmed():
    result = evaluate_execution_gate(
        ExecutionGateInput(
            action_type="move",
            is_execution=True,
            has_wake_word=True,
            missing_fields=(),
            bounds_ok=True,
            safety_ok=True,
            requires_confirmation=True,
            has_pending_confirm=True,
            confirmed=True,
        )
    )

    assert result.ok is True
    assert result.state == "execution_allowed"
