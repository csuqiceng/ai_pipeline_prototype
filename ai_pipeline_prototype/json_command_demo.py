from __future__ import annotations

import argparse
import json
from datetime import datetime

from ai_pipeline_prototype.app_service import PipelineAppService


def run_json_command_demo() -> None:
    """演示JSON指令执行"""
    service = PipelineAppService()
    
    # 测试MOVE指令
    move_command = {
        "command": "MOVE",
        "parameters": {
            "target": "POSITION_1",
            "offset": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.5,
                "rotation": 0.0
            },
            "speed": 50,
            "relative": True
        },
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 MOVE 指令 ===")
    print("输入指令:")
    print(json.dumps(move_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(move_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    
    # 测试HOME指令
    home_command = {
        "command": "HOME",
        "parameters": {},
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 HOME 指令 ===")
    print("输入指令:")
    print(json.dumps(home_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(home_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    
    # 测试GRASP指令
    grasp_command = {
        "command": "GRASP",
        "parameters": {
            "target": "OBJECT_1",
            "force": 5.0
        },
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 GRASP 指令 ===")
    print("输入指令:")
    print(json.dumps(grasp_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(grasp_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    
    # 测试RELEASE指令
    release_command = {
        "command": "RELEASE",
        "parameters": {},
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 RELEASE 指令 ===")
    print("输入指令:")
    print(json.dumps(release_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(release_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    
    # 测试PICK_PLACE指令
    pick_place_command = {
        "command": "PICK_PLACE",
        "parameters": {
            "target": "OBJECT_1",
            "speed": 60
        },
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 PICK_PLACE 指令 ===")
    print("输入指令:")
    print(json.dumps(pick_place_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(pick_place_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    
    # 测试STOP指令
    stop_command = {
        "command": "STOP",
        "parameters": {},
        "timestamp": datetime.now().isoformat()
    }
    
    print("=== 测试 STOP 指令 ===")
    print("输入指令:")
    print(json.dumps(stop_command, ensure_ascii=False, indent=2))
    
    result = service.execute_json_command(stop_command)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()


def run_json_file_demo(file_path: str) -> None:
    """从文件中读取JSON指令并执行"""
    service = PipelineAppService()
    
    with open(file_path, 'r', encoding='utf-8') as f:
        commands = json.load(f)
    
    if not isinstance(commands, list):
        commands = [commands]
    
    for i, command in enumerate(commands):
        print(f"=== 执行第 {i+1} 条指令 ===")
        print("输入指令:")
        print(json.dumps(command, ensure_ascii=False, indent=2))
        
        result = service.execute_json_command(command)
        print("执行结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="JSON指令执行演示")
    parser.add_argument("--file", help="JSON指令文件路径")
    args = parser.parse_args()
    
    if args.file:
        run_json_file_demo(args.file)
    else:
        run_json_command_demo()


if __name__ == "__main__":
    main()
