from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ACTION_PROMISE_PATTERN = re.compile(
    r"(已|已经|好的[，,]?)"
    r".{0,12}"
    r"(创建|新建|添加|保存|登记|注册|执行|下发|写入|完成)"
    r".{0,12}"
    r"(流程|步骤|指令|命令|动作|计划)?"
)


@dataclass(frozen=True)
class ReplayViolation:
    code: str
    file: str
    line_no: int
    msg_id: str
    raw_text: str
    detail: str


@dataclass(frozen=True)
class ReplayReport:
    total_files: int = 0
    total_records: int = 0
    violations: list[ReplayViolation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def analyze_interaction_logs(paths: Iterable[str | Path], *, since_session: str = "") -> ReplayReport:
    files = _expand_jsonl_paths(paths, since_session=since_session)
    violations: list[ReplayViolation] = []
    total_records = 0
    for file_path in files:
        for line_no, payload in _iter_jsonl_records(file_path):
            total_records += 1
            violations.extend(_violations_for_record(file_path, line_no, payload))
    return ReplayReport(
        total_files=len(files),
        total_records=total_records,
        violations=violations,
    )


def _expand_jsonl_paths(paths: Iterable[str | Path], *, since_session: str = "") -> list[Path]:
    expanded: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            expanded.extend(sorted(path.glob("interaction_session_*.jsonl")))
        elif path.exists():
            expanded.append(path)
    if since_session:
        cutoff = _normalize_session_stamp(since_session)
        expanded = [path for path in expanded if _session_stamp(path) >= cutoff]
    return expanded


def _session_stamp(path: Path) -> str:
    match = re.search(r"interaction_session_(\d{8}_\d{6})", path.name)
    return match.group(1) if match else ""


def _normalize_session_stamp(value: str) -> str:
    compact = str(value or "").strip().replace("-", "").replace(":", "").replace(" ", "_")
    if re.fullmatch(r"\d{8}", compact):
        return f"{compact}_000000"
    if re.fullmatch(r"\d{8}_\d{6}", compact):
        return compact
    raise ValueError("since_session must be YYYYMMDD or YYYYMMDD_HHMMSS")


def _iter_jsonl_records(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            yield line_no, {"msg_id": "", "input": {"raw_text": ""}, "_invalid_json": True}
            continue
        if isinstance(payload, dict):
            yield line_no, payload


def _violations_for_record(path: Path, line_no: int, payload: dict[str, Any]) -> list[ReplayViolation]:
    msg_id = str(payload.get("msg_id", "") or "")
    raw_text = str((payload.get("input") or {}).get("raw_text", "") or "")
    if payload.get("_invalid_json"):
        return [
            ReplayViolation(
                code="INVALID_JSON",
                file=str(path),
                line_no=line_no,
                msg_id=msg_id,
                raw_text=raw_text,
                detail="JSONL 行不是有效 JSON。",
            )
        ]

    violations: list[ReplayViolation] = []
    nlp_result = payload.get("nlp_result") if isinstance(payload.get("nlp_result"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    final_text = str(response.get("final", "") or "").strip()

    pending_fields = []
    if str(nlp_result.get("engine", "") or "") == "pending":
        pending_fields.append("nlp_result.engine")
    if str(nlp_result.get("intent", "") or "") == "pending":
        pending_fields.append("nlp_result.intent")
    if str(execution.get("result", "") or "") == "pending":
        pending_fields.append("execution.result")
    if pending_fields:
        violations.append(
            ReplayViolation(
                code="NO_PENDING",
                file=str(path),
                line_no=line_no,
                msg_id=msg_id,
                raw_text=raw_text,
                detail="最终记录仍为 pending: " + ", ".join(pending_fields),
            )
        )

    if not final_text:
        violations.append(
            ReplayViolation(
                code="NO_EMPTY_FINAL",
                file=str(path),
                line_no=line_no,
                msg_id=msg_id,
                raw_text=raw_text,
                detail="response.final 为空。",
            )
        )

    if _looks_like_chat_action_promise(nlp_result, execution, final_text):
        violations.append(
            ReplayViolation(
                code="NO_CHAT_ACTION_PROMISE",
                file=str(path),
                line_no=line_no,
                msg_id=msg_id,
                raw_text=raw_text,
                detail="非执行回复承诺了创建、保存或执行类动作。",
            )
        )
    if _success_has_non_execution_nlp_result(nlp_result, execution):
        violations.append(
            ReplayViolation(
                code="NO_SUCCESS_WITH_NON_EXECUTION_NLP",
                file=str(path),
                line_no=line_no,
                msg_id=msg_id,
                raw_text=raw_text,
                detail="执行成功记录仍保留澄清、闲聊或未知 NLP 结果，说明解析结果和执行结果不一致。",
            )
        )
    return violations


def _looks_like_chat_action_promise(
    nlp_result: dict[str, Any],
    execution: dict[str, Any],
    final_text: str,
) -> bool:
    if not final_text:
        return False
    execution_result = str(execution.get("result", "") or "")
    modbus_write = execution.get("modbus_write") if isinstance(execution.get("modbus_write"), dict) else {}
    if execution_result not in {"", "skipped", "blocked", "cancelled"}:
        return False
    if modbus_write:
        return False
    intent = str(nlp_result.get("intent", "") or "")
    engine = str(nlp_result.get("engine", "") or "")
    if intent not in {"", "chat", "unknown", "suggestion"} and engine not in {"chat", "llm", "deepseek_chat"}:
        return False
    return bool(ACTION_PROMISE_PATTERN.search(final_text))


def _success_has_non_execution_nlp_result(
    nlp_result: dict[str, Any],
    execution: dict[str, Any],
) -> bool:
    if str(execution.get("result", "") or "") != "success":
        return False
    modbus_write = execution.get("modbus_write") if isinstance(execution.get("modbus_write"), dict) else {}
    if not modbus_write:
        return False
    action_type = str(nlp_result.get("action_type", "") or "")
    intent = str(nlp_result.get("intent", "") or "")
    return action_type in {"clarification", "chat", "unknown"} or intent in {"chat", "unknown"}


def format_report(report: ReplayReport) -> str:
    lines = [
        "# Interaction Replay Report",
        "",
        f"- files: {report.total_files}",
        f"- records: {report.total_records}",
        f"- violations: {len(report.violations)}",
    ]
    if not report.violations:
        lines.append("- status: PASS")
        return "\n".join(lines)
    lines.append("- status: FAIL")
    lines.append("")
    for violation in report.violations:
        lines.append(
            f"- {violation.code} {violation.file}:{violation.line_no} "
            f"msg_id={violation.msg_id or '-'} input={violation.raw_text or '-'} "
            f"detail={violation.detail}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay archived interaction JSONL logs and check dialogue invariants.")
    parser.add_argument(
        "paths",
        nargs="*",
        default=[Path("data") / "exported_logs"],
        help="JSONL file or directory. Defaults to data/exported_logs.",
    )
    parser.add_argument("-o", "--output", help="Optional markdown report path.")
    parser.add_argument(
        "--since-session",
        default="",
        help="Only analyze interaction_session files whose timestamp is >= YYYYMMDD or YYYYMMDD_HHMMSS.",
    )
    args = parser.parse_args(argv)

    report = analyze_interaction_logs(args.paths, since_session=args.since_session)
    text = format_report(report)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
