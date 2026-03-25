from __future__ import annotations

from .executor import ExecutionError, RobotExecutor
from .models import DispatchResult, DispatchState, Task


class DispatchError(Exception):
    pass


class TaskDispatcher:
    """任务调度器原型。通过执行接口调用控制层，便于后续接 Motion SDK。"""

    def __init__(self, executor: RobotExecutor) -> None:
        self.executor = executor

    def dispatch(self, task: Task) -> DispatchResult:
        history: list[str] = []

        try:
            self._validate(task, history)

            if task.task_type == "stop_task":
                command = self.executor.stop()
                self._transition(DispatchState.STOPPING, history, f"执行停止指令: {command}")
                return DispatchResult(
                    task_id=task.task_id,
                    final_state=DispatchState.DONE,
                    success=True,
                    message="停止任务已受理。",
                    history=history,
                )

            if task.task_type == "go_home":
                command = self.executor.home()
                self._transition(DispatchState.HOMING, history, f"执行回零指令: {command}")
                return DispatchResult(
                    task_id=task.task_id,
                    final_state=DispatchState.DONE,
                    success=True,
                    message="回零任务已受理。",
                    history=history,
                )

            move_pick = self.executor.move_to(task.pick_point, task.speed)
            self._transition(DispatchState.MOVE_TO_PICK, history, f"移动到抓取点: {move_pick}")

            grip = self.executor.grip(task.target_id)
            self._transition(DispatchState.GRIP, history, f"闭合夹具: {grip}")

            move_place = self.executor.move_to(task.place_point, task.speed)
            self._transition(DispatchState.MOVE_TO_PLACE, history, f"移动到放置点: {move_place}")

            release = self.executor.release()
            self._transition(DispatchState.RELEASE, history, f"松开夹具: {release}")
            self._transition(DispatchState.DONE, history, "任务完成")

            return DispatchResult(
                task_id=task.task_id,
                final_state=DispatchState.DONE,
                success=True,
                message="任务执行流程模拟完成。",
                history=history,
            )
        except (DispatchError, ExecutionError) as exc:
            history.append(f"{DispatchState.ERROR.value}: {exc}")
            return DispatchResult(
                task_id=task.task_id,
                final_state=DispatchState.ERROR,
                success=False,
                message=str(exc),
                history=history,
            )

    def _validate(self, task: Task, history: list[str]) -> None:
        self._transition(DispatchState.VALIDATING, history, "开始任务校验")

        if task.safety_check and task.task_type in {"pick", "pick_and_place"}:
            if not task.pick_point or not task.place_point:
                raise DispatchError("抓取或放置点缺失，不能执行。")

        if task.pick_point and not self._point_in_range(task.pick_point):
            raise DispatchError(f"抓取点越界: {task.pick_point}")

        if task.place_point and not self._point_in_range(task.place_point):
            raise DispatchError(f"放置点越界: {task.place_point}")

    def _point_in_range(self, point: list[float]) -> bool:
        x, y, z = point
        return 0 <= x <= 500 and 0 <= y <= 500 and 0 <= z <= 200

    def _transition(self, state: DispatchState, history: list[str], action: str) -> None:
        history.append(f"{state.value}: {action}")
