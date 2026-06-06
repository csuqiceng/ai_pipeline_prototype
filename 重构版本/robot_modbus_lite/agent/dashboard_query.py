from __future__ import annotations

from robot_modbus_lite.dashboard_query_specs import match_dashboard_query_spec


class DashboardQueryAgent:
    def answer(self, text: str) -> dict[str, object] | None:
        spec = match_dashboard_query_spec(text)
        if spec is None:
            return None
        return {
            "kind": "dashboard_query_action",
            "action_type": "query",
            "target": spec.board_key,
            "text": f"命中{spec.board_name}，查询范围：{spec.answer_scope}",
            "raw_text": text,
            "generates_command": False,
        }
