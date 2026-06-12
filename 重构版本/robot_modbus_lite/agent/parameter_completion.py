"""Parameter completion for restricted Agent command drafts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from robot_modbus_lite.agent.address_resolver import AddressResolver
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingResult
from robot_modbus_lite.agent.drafts import CommandDraft


POSE_KEYS = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
SPEED_KEYS = ("spd_pct", "acc_pct", "dec_pct")
DELTA_KEYS = {
    "target_x": "delta_x",
    "target_y": "delta_y",
    "target_z": "delta_z",
    "target_rx": "delta_rx",
    "target_ry": "delta_ry",
    "target_rz": "delta_rz",
}


class ParameterCompletionError(RuntimeError):
    """Raised when a command cannot be completed into a safe draft."""


@dataclass(frozen=True)
class ControllerSnapshot:
    current_pose: dict[str, float] = field(default_factory=dict)
    safety_params: dict[str, float] = field(default_factory=dict)
    is_moving: bool = False
    read_ok: bool = True
    moving_reasons: tuple[str, ...] = ()


class ParameterCompletionAgent:
    """Complete deterministic Agent understanding results into command drafts."""

    def __init__(
        self,
        snapshot_provider: Callable[[], ControllerSnapshot],
        address_resolver: AddressResolver | None = None,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._address_resolver = address_resolver or AddressResolver()

    def complete(self, result: CommandUnderstandingResult) -> CommandDraft:
        if result.func_id in {106, 107}:
            raise ParameterCompletionError("当前阶段不支持 Func106/107 点动草案，请使用 Func108 笛卡尔运动。")
        if result.func_id in {109, 110, 120}:
            return self._complete_auxiliary(result)
        motion_func_ids = {self._address_resolver.absolute_motion_func, self._address_resolver.continuous_path_func}
        if result.func_id not in motion_func_ids or result.intent not in {"move_linear", "continuous_path"}:
            raise ParameterCompletionError(f"当前阶段只支持 Func108/112 运动参数补全: {result.intent}")

        snapshot = self._snapshot_provider()
        if snapshot.is_moving:
            raise ParameterCompletionError(self._moving_error_message(snapshot, "请等待停止后再生成移动草案。"))
        if not snapshot.read_ok:
            raise ParameterCompletionError("控制器实时值不可用，无法补全运动参数。")

        params: dict[str, float | int | str] = {}
        sources: dict[str, str] = {}
        self._complete_pose(result, snapshot, params, sources)
        self._complete_speed(result, snapshot, params, sources)
        self._fill_linear_defaults(params, sources)
        self._fill_protocol_aliases(result, params, sources)

        return CommandDraft(
            draft_id=uuid4().hex[:8],
            func_id=int(result.func_id),
            intent=result.intent,
            params=params,
            param_sources=sources,
            raw_text=result.raw_text,
            confidence=result.confidence,
        )

    @staticmethod
    def _complete_auxiliary(result: CommandUnderstandingResult) -> CommandDraft:
        if result.func_id in {109, 110}:
            if "delay_sec" not in result.extracted_params:
                raise ParameterCompletionError("缺少延时时间，无法生成延时草案。")
            params: dict[str, float | int | str] = {"delay_sec": float(result.extracted_params["delay_sec"])}
        elif result.func_id == 120:
            missing = [key for key in ("io_no", "io_action") if key not in result.extracted_params]
            if missing:
                raise ParameterCompletionError(f"缺少IO参数，无法生成IO草案: {', '.join(missing)}")
            params = {
                "io_no": int(result.extracted_params["io_no"]),
                "io_action": int(result.extracted_params["io_action"]),
            }
        else:
            raise ParameterCompletionError(f"不支持的辅助指令: {result.intent}")
        return CommandDraft(
            draft_id=uuid4().hex[:8],
            func_id=int(result.func_id),
            intent=result.intent,
            params=params,
            param_sources={key: "specified" for key in params},
            raw_text=result.raw_text,
            confidence=result.confidence,
        )

    def _complete_jog(self, result: CommandUnderstandingResult) -> CommandDraft:
        missing = [key for key in ("axis_no", "pos_val") if key not in result.extracted_params]
        if missing:
            raise ParameterCompletionError(f"缺少点动参数，无法生成草案: {', '.join(missing)}")

        snapshot = self._snapshot_provider()
        if snapshot.is_moving:
            raise ParameterCompletionError(self._moving_error_message(snapshot, "请等待停止后再生成点动草案。"))
        if not snapshot.read_ok:
            raise ParameterCompletionError("控制器实时值不可用，无法补全点动参数。")

        params: dict[str, float | int | str] = {
            "axis_no": int(result.extracted_params["axis_no"]),
            "pos_val": float(result.extracted_params["pos_val"]),
            "fuzzy_pos": int(result.extracted_params.get("fuzzy_pos", 0)),
            "fuzzy_spd": int(result.extracted_params.get("fuzzy_spd", 1)),
            "fuzzy_acc": int(result.extracted_params.get("fuzzy_acc", 1)),
            "fuzzy_dec": int(result.extracted_params.get("fuzzy_dec", 1)),
            "stop_cmd": int(result.extracted_params.get("stop_cmd", 0)),
        }
        sources: dict[str, str] = {
            "axis_no": "specified",
            "pos_val": "specified",
            "fuzzy_pos": "specified" if "fuzzy_pos" in result.extracted_params else "default",
            "fuzzy_spd": "specified" if "fuzzy_spd" in result.extracted_params else "default",
            "fuzzy_acc": "specified" if "fuzzy_acc" in result.extracted_params else "default",
            "fuzzy_dec": "specified" if "fuzzy_dec" in result.extracted_params else "default",
            "stop_cmd": "specified" if "stop_cmd" in result.extracted_params else "default",
        }
        self._complete_speed(result, snapshot, params, sources)
        return CommandDraft(
            draft_id=uuid4().hex[:8],
            func_id=int(result.func_id),
            intent=result.intent,
            params=params,
            param_sources=sources,
            raw_text=result.raw_text,
            confidence=result.confidence,
        )

    @staticmethod
    def _moving_error_message(snapshot: ControllerSnapshot, suffix: str) -> str:
        reasons = tuple(str(item).strip() for item in getattr(snapshot, "moving_reasons", ()) if str(item).strip())
        detail = f"触发字段：{'; '.join(reasons)}。" if reasons else ""
        return f"当前设备运动中，{detail}{suffix}"

    @staticmethod
    def _complete_pose(
        result: CommandUnderstandingResult,
        snapshot: ControllerSnapshot,
        params: dict[str, float | int | str],
        sources: dict[str, str],
    ) -> None:
        missing: list[str] = []
        relative_template_style = int(result.extracted_params.get("position_increment", 0) or 0) == 1
        for key in POSE_KEYS:
            if key in result.extracted_params:
                params[key] = result.extracted_params[key]
                value = float(result.extracted_params[key])
                if relative_template_style:
                    sources[key] = "incremental" if value != 0.0 else "inherited"
                else:
                    sources[key] = "specified"
                continue
            delta_key = DELTA_KEYS[key]
            if delta_key in result.extracted_params:
                if key not in snapshot.current_pose:
                    missing.append(key)
                    continue
                params[key] = float(snapshot.current_pose[key]) + float(result.extracted_params[delta_key])
                sources[key] = "incremental"
                continue
            if key in snapshot.current_pose:
                params[key] = float(snapshot.current_pose[key])
                sources[key] = "inherited"
                continue
            missing.append(key)
        if missing:
            raise ParameterCompletionError(f"缺少实时位姿，无法继承: {', '.join(missing)}")

    @staticmethod
    def _complete_speed(
        result: CommandUnderstandingResult,
        snapshot: ControllerSnapshot,
        params: dict[str, float | int | str],
        sources: dict[str, str],
    ) -> None:
        missing: list[str] = []
        for key in SPEED_KEYS:
            if key in result.extracted_params:
                params[key] = result.extracted_params[key]
                sources[key] = "specified"
                continue
            if key in snapshot.safety_params:
                params[key] = float(snapshot.safety_params[key])
                sources[key] = "controller"
                continue
            missing.append(key)
        if missing:
            raise ParameterCompletionError(f"缺少控制器安全速度参数，无法补全: {', '.join(missing)}")

    @staticmethod
    def _fill_linear_defaults(params: dict[str, float | int | str], sources: dict[str, str]) -> None:
        defaults = {
            "stop_cmd": 0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 0,
            "fuzzy_acc": 0,
            "fuzzy_dec": 0,
            "move_type": 0,
        }
        for key, value in defaults.items():
            if key in params:
                continue
            params[key] = value
            sources[key] = "default"

    @staticmethod
    def _fill_protocol_aliases(
        result: CommandUnderstandingResult,
        params: dict[str, float | int | str],
        sources: dict[str, str],
    ) -> None:
        params["position_increment"] = int(result.extracted_params.get("position_increment", 0) or 0)
        sources["position_increment"] = "specified" if "position_increment" in result.extracted_params else "default"
        if "fuzzy_pos" in result.extracted_params:
            params["fuzzy_pos"] = int(result.extracted_params.get("fuzzy_pos", 0) or 0)
            sources["fuzzy_pos"] = "specified"
        elif int(params["position_increment"] or 0) == 1:
            params["fuzzy_pos"] = 1
            sources["fuzzy_pos"] = "specified"
