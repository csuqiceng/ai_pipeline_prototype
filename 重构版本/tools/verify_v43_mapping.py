"""真实控制器地址映射读取检查工具。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from robot_modbus_lite.models import VrReadRequest
from robot_modbus_lite.zmotion_client import ZMotionClientError, ZMotionVrClient


EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


@dataclass
class CheckResult:
    """真实控制器地址检查的一项结果。"""
    section: str
    name: str
    status: str
    address: str
    value: Any
    detail: str


def _print_section(name: str) -> None:
    """处理段落。"""
    print(f"\n[{name}]")


def _print_result(result: CheckResult) -> None:
    """处理结果。"""
    print(f"{result.status:<4} {result.name}")
    print(f"     address: {result.address}")
    print(f"     value: {result.value}")
    print(f"     detail: {result.detail}")


def _read_check(
    results: list[CheckResult],
    *,
    section: str,
    name: str,
    address: str,
    reader: Callable[[], Any],
    validator: Callable[[Any], tuple[str, str]] | None = None,
) -> Any:
    """读取相关数据。"""
    try:
        value = reader()
        if validator is None:
            status, detail = "PASS", "读取成功。"
        else:
            status, detail = validator(value)
    except Exception as exc:
        value = None
        status = "FAIL"
        detail = f"读取失败: {exc}"
    result = CheckResult(section, name, status, address, value, detail)
    results.append(result)
    _print_result(result)
    return value


def _len_validator(expected_len: int, item_name: str) -> Callable[[Any], tuple[str, str]]:
    """处理校验器。"""
    def validate(value: Any) -> tuple[str, str]:
        """校验相关数据。"""
        if not isinstance(value, list):
            return "FAIL", f"返回值不是列表，无法确认 {item_name}。"
        if len(value) != expected_len:
            return "FAIL", f"期望 {expected_len} 个值，实际 {len(value)} 个。"
        return "PASS", f"返回 {expected_len} 个值，{item_name} 可读。"

    return validate


def _ieee_320_validator(long36: list[int] | None) -> Callable[[Any], tuple[str, str]]:
    """处理校验器。"""
    def validate(value: Any) -> tuple[str, str]:
        """校验相关数据。"""
        if not isinstance(value, list) or len(value) != 1:
            return "FAIL", "IEEE(320) 返回长度异常。"
        if not long36 or len(long36) != 1:
            return "WARN", "LONG(36) 未成功读取，无法比较一致性。"
        ieee320 = int(float(value[0]))
        system_state = int(long36[0])
        if ieee320 == system_state:
            return "PASS", "IEEE(320) 与 LONG(36) 当前值一致。"
        return "WARN", f"IEEE(320)={ieee320} 与 LONG(36)={system_state} 不一致，需结合固件刷新周期判断。"

    return validate


def _write_json(path: Path, results: list[CheckResult], summary: dict[str, Any]) -> None:
    """写入配置文件。"""
    payload = {
        "summary": summary,
        "results": [asdict(result) for result in results],
        "note": "本结果只代表本次地址可读性和一致性检查，不等同于硬件固件行为完全验证通过。",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """解析参数。"""
    parser = argparse.ArgumentParser(
        description="V4.3 M0.5 真实硬件 Modbus IEEE/LONG 地址映射只读验证脚本。"
    )
    parser.add_argument("--host", required=True, help="控制器 IP 地址。")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="仓库根目录，用于定位 ZMotion SDK。")
    parser.add_argument("--json", dest="json_path", help="可选: 输出验证结果 JSON 文件。")
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=5.0,
        help="预留参数: SDK 调用自身可能阻塞，本脚本第一版不强制中断 SDK 调用。",
    )
    parser.add_argument(
        "--write-test",
        action="store_true",
        help="预留参数: 第一版不会执行任何写入。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行命令行入口逻辑。"""
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return EXIT_USAGE if int(exc.code) != 0 else EXIT_OK

    repo_root = Path(args.repo_root).resolve()
    results: list[CheckResult] = []
    client: ZMotionVrClient | None = None

    print("V4.3 M0.5 地址映射验证")
    print("默认只读: 本脚本不会写 LONG(34/36/38)、IEEE(320/322/1600~1622) 或任何控制器地址。")
    print("提示: 通过/失败仅代表本次地址可读性和一致性检查，不等同于硬件固件行为完全验证通过。")
    if args.write_test:
        print("WARN --write-test 当前仅为预留参数，第一版不执行任何写入。")

    _print_section("connect")
    try:
        client = ZMotionVrClient(args.host, repo_root=repo_root)
        client.connect()
        connect_result = CheckResult(
            "connect",
            "connect controller",
            "PASS",
            args.host,
            args.host,
            "控制器连接成功。",
        )
        results.append(connect_result)
        _print_result(connect_result)
    except (ZMotionClientError, OSError, RuntimeError) as exc:
        connect_result = CheckResult(
            "connect",
            "connect controller",
            "FAIL",
            args.host,
            None,
            f"连接失败: {exc}",
        )
        results.append(connect_result)
        _print_result(connect_result)
        summary = {"pass": 0, "warn": 0, "fail": 1}
        if args.json_path:
            _write_json(Path(args.json_path), results, summary)
        return EXIT_FAIL

    try:
        assert client is not None

        _print_section("long-status")
        long34 = _read_check(
            results,
            section="long-status",
            name="read LONG(34)",
            address="LONG(34), count=1",
            reader=lambda: client.read_modbus_long(VrReadRequest(34, 1)),
            validator=_len_validator(1, "LONG(34)"),
        )
        long36 = _read_check(
            results,
            section="long-status",
            name="read LONG(36)",
            address="LONG(36), count=1",
            reader=lambda: client.read_modbus_long(VrReadRequest(36, 1)),
            validator=_len_validator(1, "LONG(36)"),
        )
        _read_check(
            results,
            section="long-status",
            name="read LONG(38)",
            address="LONG(38), count=1",
            reader=lambda: client.read_modbus_long(VrReadRequest(38, 1)),
            validator=_len_validator(1, "LONG(38)"),
        )
        _read_check(
            results,
            section="long-status",
            name="read LONG(34..36) count behavior",
            address="LONG(34), count=3",
            reader=lambda: client.read_modbus_long(VrReadRequest(34, 3)),
            validator=_len_validator(3, "LONG count 行为"),
        )

        _print_section("ieee-system")
        _read_check(
            results,
            section="ieee-system",
            name="read IEEE(320)",
            address="IEEE(320), count=1",
            reader=lambda: client.read_modbus_float(VrReadRequest(320, 1)),
            validator=_ieee_320_validator(long36 if isinstance(long36, list) else None),
        )
        _read_check(
            results,
            section="ieee-system",
            name="read IEEE(322)",
            address="IEEE(322), count=1",
            reader=lambda: client.read_modbus_float(VrReadRequest(322, 1)),
            validator=_len_validator(1, "IEEE(322) 当前函数号"),
        )

        _print_section("mpos")
        _read_check(
            results,
            section="mpos",
            name="read MPOS joints",
            address="IEEE(1600), count=6 -> 1600,1602,...,1610",
            reader=lambda: client.read_modbus_float(VrReadRequest(1600, 6)),
            validator=_len_validator(6, "MPOS 关节反馈"),
        )
        _read_check(
            results,
            section="mpos",
            name="read MPOS pose",
            address="IEEE(1612), count=6 -> 1612,1614,...,1622",
            reader=lambda: client.read_modbus_float(VrReadRequest(1612, 6)),
            validator=_len_validator(6, "MPOS 位姿反馈"),
        )

        _print_section("address-step")
        if isinstance(long34, list) and isinstance(long36, list):
            detail = "LONG(34) 与 LONG(36) 单点读取均成功；批量 count=3 结果需现场确认 LONG(35) 是否为预期中间地址。"
            result = CheckResult("address-step", "LONG/IEEE address spaces", "WARN", "LONG/IEEE", None, detail)
        else:
            result = CheckResult("address-step", "LONG/IEEE address spaces", "FAIL", "LONG/IEEE", None, "关键 LONG 地址读取失败。")
        results.append(result)
        _print_result(result)

    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    pass_count = sum(1 for result in results if result.status == "PASS")
    warn_count = sum(1 for result in results if result.status == "WARN")
    fail_count = sum(1 for result in results if result.status == "FAIL")
    summary = {"pass": pass_count, "warn": warn_count, "fail": fail_count}

    _print_section("summary")
    print(f"PASS={pass_count} WARN={warn_count} FAIL={fail_count}")
    print("提示: 本脚本通过不等同于硬件固件行为完全验证通过，M9 时序和 IEEE(32) 锁存仍需现场单独记录。")

    if args.json_path:
        _write_json(Path(args.json_path), results, summary)
        print(f"JSON written: {args.json_path}")

    return EXIT_FAIL if fail_count else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
