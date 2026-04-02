from __future__ import annotations

import json

from ai_pipeline_prototype.natural_language_processor import NaturalLanguageProcessor
from ai_pipeline_prototype.app_service import PipelineAppService


def run_nlp_demo() -> None:
    """演示自然语言处理功能"""
    processor = NaturalLanguageProcessor()
    service = PipelineAppService()
    
    # 测试用例
    test_cases = [
        "移动到第一个位置",
        "移动到左边位置速度50%",
        "抓取物体A",
        "抓取物体B力度5N",
        "释放",
        "回零",
        "停止",
        "向上移动10毫米",
        "向左调5",
        "把物体从左边移到右边"
    ]
    
    for i, text in enumerate(test_cases):
        print(f"\n=== 测试用例 {i+1} ===")
        print(f"输入: {text}")
        
        # 处理自然语言输入
        command_json = processor.process_to_json(text)
        print("解析结果:")
        print(json.dumps(command_json, ensure_ascii=False, indent=2))
        
        # 执行指令
        result = service.execute_json_command(command_json)
        print("执行结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))


def run_interactive_demo() -> None:
    """交互式演示自然语言处理功能"""
    processor = NaturalLanguageProcessor()
    service = PipelineAppService()
    
    print("自然语言控制机械手演示")
    print("输入 '退出' 结束演示")
    print("示例输入:")
    print("  移动到第一个位置")
    print("  抓取物体A")
    print("  向上移动10毫米")
    print("  把物体从左边移到右边")
    print("  回零")
    print("  停止")
    
    while True:
        text = input("\n请输入指令: ")
        if text == "退出":
            break
        
        try:
            # 处理自然语言输入
            command_json = processor.process_to_json(text)
            print("解析结果:")
            print(json.dumps(command_json, ensure_ascii=False, indent=2))
            
            # 执行指令
            result = service.execute_json_command(command_json)
            print("执行结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"错误: {e}")


def main() -> None:
    import argparse
    
    parser = argparse.ArgumentParser(description="自然语言处理演示")
    parser.add_argument("--interactive", action="store_true", help="交互式演示")
    args = parser.parse_args()
    
    if args.interactive:
        run_interactive_demo()
    else:
        run_nlp_demo()


if __name__ == "__main__":
    main()
