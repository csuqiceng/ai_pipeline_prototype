from robot_modbus_lite.agent.compound import CompoundCommandCoordinator, CompoundStepMachine


def test_compound_splits_simple_sequential_command():
    result = CompoundCommandCoordinator().split("走到X1000，然后等待2秒，再IO1开")

    assert result.kind == "compound_sequence"
    assert result.steps == ("走到X1000", "等待2秒", "IO1开")


def test_compound_allows_continuous_path_step_as_actionable():
    result = CompoundCommandCoordinator().split("规划路径走到X1000，然后等待2秒")

    assert result.kind == "compound_sequence"
    assert result.steps == ("规划路径走到X1000", "等待2秒")


def test_compound_does_not_split_when_any_part_is_not_actionable():
    class FakeUnderstandingAgent:
        def understand(self, text):
            intent = "move_linear" if "X1000" in text else "unknown"
            return type("Result", (), {"intent": intent})()

    coordinator = CompoundCommandCoordinator(understanding_agent=FakeUnderstandingAgent())

    result = coordinator.split("走到X1000然后告诉我结果")

    assert result.kind == "not_compound"


def test_compound_rejects_parallel_or_conditional_commands():
    coordinator = CompoundCommandCoordinator()

    assert coordinator.split("同时走到X1000并且IO1开").kind == "unsupported_compound"
    assert coordinator.split("如果没有报警就走到X1000").kind == "unsupported_compound"


def test_compound_builds_step_results_without_executing():
    class FakeRestrictedService:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text}

    coordinator = CompoundCommandCoordinator(
        restricted_service=FakeRestrictedService(),
        clock=lambda: 100.0,
        id_factory=lambda: "compound:test",
    )

    result = coordinator.plan("走到X1000，然后等待2秒")

    assert result.kind == "compound_plan_draft"
    assert result.plan_id == "compound:test"
    assert result.raw_text == "走到X1000，然后等待2秒"
    assert result.created_at == 100.0
    assert result.steps == ("走到X1000", "等待2秒")
    assert result.step_results == (
        {"kind": "waiting_confirmation", "text": "走到X1000"},
        {"kind": "waiting_confirmation", "text": "等待2秒"},
    )


def test_compound_step_machine_starts_at_first_waiting_confirmation_step():
    plan = CompoundCommandCoordinator(
        restricted_service=type(
            "Service",
            (),
            {"parse": lambda self, text: {"kind": "waiting_confirmation", "text": text}},
        )(),
        id_factory=lambda: "compound:test",
        clock=lambda: 100.0,
    ).plan("走到X1000，然后等待2秒")

    session = CompoundStepMachine.from_plan(plan)

    assert session.plan_id == "compound:test"
    assert session.status == "waiting_step_confirmation"
    assert session.current_index == 0
    assert session.current_step_text == "走到X1000"
    assert tuple(step.status for step in session.steps) == ("waiting_confirmation", "pending")


def test_compound_step_machine_confirms_and_advances_one_step_at_a_time():
    plan = CompoundCommandCoordinator(
        restricted_service=type(
            "Service",
            (),
            {"parse": lambda self, text: {"kind": "waiting_confirmation", "text": text}},
        )(),
        id_factory=lambda: "compound:test",
        clock=lambda: 100.0,
    ).plan("走到X1000，然后等待2秒")
    session = CompoundStepMachine.from_plan(plan)

    confirmed = session.confirm_current()
    advanced = confirmed.mark_current_completed()
    final = advanced.confirm_current().mark_current_completed()

    assert confirmed.status == "step_confirmed"
    assert confirmed.steps[0].status == "confirmed"
    assert advanced.status == "waiting_step_confirmation"
    assert advanced.current_index == 1
    assert advanced.current_step_text == "等待2秒"
    assert final.status == "completed"
    assert tuple(step.status for step in final.steps) == ("completed", "completed")


def test_compound_step_machine_blocks_when_any_step_precheck_failed():
    plan = CompoundCommandCoordinator(
        restricted_service=type(
            "Service",
            (),
            {
                "parse": lambda self, text: {"kind": "precheck_failed", "message": "L1预检未通过"}
                if "X1000" in text
                else {"kind": "waiting_confirmation", "text": text}
            },
        )(),
        id_factory=lambda: "compound:test",
        clock=lambda: 100.0,
    ).plan("走到X1000，然后等待2秒")

    session = CompoundStepMachine.from_plan(plan)

    assert session.status == "blocked"
    assert session.current_index == 0
    assert "L1预检未通过" in session.reason
    assert tuple(step.status for step in session.steps) == ("blocked", "pending")
