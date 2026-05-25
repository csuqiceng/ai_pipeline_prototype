"""Local AI iteration interface for non-Web data exchange."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dashboard import DashboardCache
from .param_manager import fields_by_layer, validate_param_patch
from .system_config import AxisRangeConfig, load_system_config, save_system_config, validate_system_config


class AIInterface:
    """Expose auditable local data and bounded parameter update hooks for future AI iteration."""

    DEFAULT_RULE_FILES = {
        "flow_phrase_aliases": "flow_phrase_aliases.json",
        "dashboard_query_aliases": "dashboard_query_aliases.json",
        "nlp_standard_words": "nlp_standard_words.json",
        "assistant_knowledge_base": "assistant_knowledge_base.json",
    }

    def __init__(
        self,
        *,
        dialog_log_dir: str | Path,
        system_config_path: str | Path | None = None,
        dashboard_source: Any | None = None,
        rule_paths: dict[str, str | Path] | None = None,
    ) -> None:
        self.dialog_log_dir = Path(dialog_log_dir)
        self.system_config_path = Path(system_config_path) if system_config_path is not None else Path("data/system_config.json")
        self.dashboard_source = dashboard_source
        self.rule_paths = {name: Path(path) for name, path in (rule_paths or {}).items()}

    def get_dialog_stream(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.dialog_log_dir.exists():
            return rows
        for path in sorted(self.dialog_log_dir.glob("dialog_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows

    def get_device_status_stream(self) -> list[dict[str, Any]]:
        if self.dashboard_source is None:
            return []
        snapshot = DashboardCache().update_from_source(self.dashboard_source)
        return [snapshot.to_dict()]

    def hot_update_rule(self, rule_name: str, rule_data: dict[str, Any]) -> dict[str, Any]:
        name = str(rule_name or "").strip()
        path = self._rule_path(name)
        if path is None:
            return {"ok": False, "message": f"不支持的规则文件：{name}"}
        if not isinstance(rule_data, dict):
            return {"ok": False, "message": "规则数据必须是 JSON 对象。"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rule_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "message": f"规则'{name}'已热更新。", "path": str(path)}

    def get_safety_params(self) -> dict[str, Any]:
        config = load_system_config(self.system_config_path)
        values = config.to_dict()
        layers = fields_by_layer()
        safety_fields = set(layers["optimizable"]) | {
            "x",
            "y",
            "z",
            "joint_limits",
            "emergency_codes",
        }
        return {
            "values": {key: values[key] for key in sorted(safety_fields) if key in values},
            "layers": layers,
        }

    def set_safety_params(self, params: dict[str, Any], permission_level: str) -> tuple[bool, str]:
        actor = str(permission_level or "").strip() or "ai_optimizer"
        if actor not in {"engineer", "ai_optimizer", "system"}:
            return False, f"未知权限级别：{permission_level}"
        validation = validate_param_patch(params, actor=actor)  # type: ignore[arg-type]
        if not validation.ok:
            return False, validation.message
        current = load_system_config(self.system_config_path)
        payload = current.to_dict()
        payload.update(params)
        updated = AxisRangeConfig.from_dict(payload)
        error = validate_system_config(updated)
        if error is not None:
            return False, error
        save_system_config(self.system_config_path, updated)
        return True, "安全参数已更新。"

    def _rule_path(self, rule_name: str) -> Path | None:
        if rule_name in self.rule_paths:
            return self.rule_paths[rule_name]
        filename = self.DEFAULT_RULE_FILES.get(rule_name)
        if filename is None:
            return None
        return Path("data") / filename
