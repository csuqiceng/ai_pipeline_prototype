from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from ai_pipeline_prototype.dispatcher import TaskDispatcher
from ai_pipeline_prototype.controller_service import ControllerService
from ai_pipeline_prototype.factory import build_executor
from ai_pipeline_prototype.inputs import VisionInputAdapter, VoiceInputAdapter
from ai_pipeline_prototype.planner import PlanningError, TaskPlanner
from ai_pipeline_prototype.sdk_adapter import MotionSDKClient, MotionSDKConfig


def run_pick_and_place_demo(mode: str) -> None:
    voice_adapter = VoiceInputAdapter()
    vision_adapter = VisionInputAdapter()
    planner = TaskPlanner()
    dispatcher = TaskDispatcher(build_executor(mode))

    voice = voice_adapter.parse_text("抓取左边托盘里的工件放到右边工位")
    vision = vision_adapter.from_detection(
        camera_id="cam_01",
        target_found=True,
        target_id="part_01",
        position=[120.5, 230.0],
        angle=35.2,
        confidence=0.94,
        safe_region_ok=True,
    )

    print("=== Voice Input ===")
    print(json.dumps(voice.__dict__, ensure_ascii=False, indent=2))
    print("\n=== Vision Input ===")
    print(json.dumps(vision.__dict__, ensure_ascii=False, indent=2))

    try:
        task = planner.build_task(voice, vision)
    except PlanningError as exc:
        print(f"\n任务生成失败: {exc}")
        return

    print("\n=== Task JSON ===")
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))

    result = dispatcher.dispatch(task)
    print("\n=== Dispatch Result ===")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))


def run_go_home_demo(mode: str) -> None:
    voice_adapter = VoiceInputAdapter()
    planner = TaskPlanner()
    dispatcher = TaskDispatcher(build_executor(mode))

    voice = voice_adapter.parse_text("机械手回零")
    task = planner.build_task(voice, None)
    result = dispatcher.dispatch(task)

    print("\n=== Go Home Task ===")
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))


def run_failure_demo() -> None:
    voice_adapter = VoiceInputAdapter()
    vision_adapter = VisionInputAdapter()
    planner = TaskPlanner()
    dispatcher = TaskDispatcher(build_executor("sim", fail_on="grip"))

    voice = voice_adapter.parse_text("抓取左边托盘里的工件放到右边工位")
    vision = vision_adapter.from_detection(
        camera_id="cam_01",
        target_found=True,
        target_id="part_02",
        position=[100.0, 150.0],
        angle=10.0,
        confidence=0.91,
        safe_region_ok=True,
    )
    task = planner.build_task(voice, vision)
    result = dispatcher.dispatch(task)

    print("\n=== Failure Demo ===")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))


def run_controller_service_demo() -> None:
    client = MotionSDKClient(MotionSDKConfig())
    service = ControllerService(client)
    service.connect()
    service.move_to_pose(200.0, 100.0, 50.0, 60)
    service.set_gripper(True)
    service.report_alarm("E101", "夹具传感器状态异常", level="warning")

    print("\n=== Controller Service Status ===")
    print(json.dumps(asdict(service.get_status()), ensure_ascii=False, indent=2))
    print("\n=== Controller Alarm History ===")
    print(json.dumps([asdict(item) for item in service.get_alarm_history()], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI pipeline prototype demo")
    parser.add_argument(
        "--mode",
        choices=["sim", "sdk"],
        default="sim",
        help="executor backend: sim for local simulation, sdk for Motion SDK style adapter",
    )
    args = parser.parse_args()

    run_pick_and_place_demo(args.mode)
    run_go_home_demo(args.mode)
    run_failure_demo()
    run_controller_service_demo()


if __name__ == "__main__":
    main()
