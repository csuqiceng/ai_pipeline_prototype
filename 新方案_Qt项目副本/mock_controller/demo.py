from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robot_modbus_lite.models import VrReadRequest, VrWriteRequest

from mock_controller import MockZMotionVrClient
from mock_controller.protocol import (
    ALM_NORMAL,
    ALM_OUT_OF_RANGE,
    ALM_SPEED_LIMIT,
    CMD,
    RESULT_FAIL,
    RESULT_OK,
    STATUS_FAULT,
    STATUS_IDLE,
    STATUS_RUNNING,
    VR_OFFSET,
)


def print_vr(client: MockZMotionVrClient) -> None:
    snap = client.snapshot()
    print("  ┌─ VR 寄存器快照 ────────────────────────────┐")
    print(f"  │ CMD_CODE  = {int(snap['CMD_CODE']):>6}  ({CMD.name(int(snap['CMD_CODE']))})")
    print(f"  │ TASK_ID   = {int(snap['TASK_ID']):>6}")
    print(f"  │ POS_X/Y/Z = {snap['POS_X']:>8.1f} / {snap['POS_Y']:>8.1f} / {snap['POS_Z']:>8.1f}")
    print(f"  │ ROT       = {snap['ROT_RX']:>6.1f} / {snap['ROT_RY']:>6.1f} / {snap['ROT_RZ']:>6.1f}")
    print(f"  │ SPD/ACC   = {int(snap['SPD_PCT']):>3}% / {int(snap['ACC_PCT']):>3}%")
    print(f"  │ DEV_ID    = {int(snap['DEV_ID']):>6}")
    print(f"  │ IO_GRIP   = {int(snap['IO_GRIP']):>6}  ({'夹紧' if snap['IO_GRIP'] else '松开'})")
    print(f"  │ IO_DOOR   = {int(snap['IO_DOOR']):>6}  ({'开' if snap['IO_DOOR'] else '关'})")
    print(f"  │ SAFETY_LV = {int(snap['SAFETY_LV']):>6}")
    print(f"  │ RESULT    = {int(snap['RESULT']):>6}  ({'成功' if snap['RESULT'] == 0 else '失败'})")
    status_names = {0: "空闲", 1: "运行中", 2: "暂停", 3: "故障"}
    print(f"  │ STATUS    = {int(snap['STATUS']):>6}  ({status_names.get(int(snap['STATUS']), '?')})")
    print(f"  │ CUR_X/Y/Z = {snap['CUR_X']:>8.1f} / {snap['CUR_Y']:>8.1f} / {snap['CUR_Z']:>8.1f}")
    print(f"  │ ALM_CODE  = {int(snap['ALM_CODE']):>6}")
    print(f"  │ IO_STAT   = {int(snap['IO_STAT']):>6}  (0b{int(snap['IO_STAT']):03b})")
    print("  └────────────────────────────────────────────┘")


def test_connect_disconnect(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 1: 连接 / 断开 ===")
    client.connect()
    print(f"  连接状态: {client.connected}")
    client.disconnect()
    print(f"  断开状态: {client.connected}")

    try:
        client.write_vr(VrWriteRequest(start_vr=0, values=(1.0,)))
        print("  [异常] 未连接时写入应该抛异常")
    except RuntimeError as e:
        print(f"  [正确] 未连接时写入抛异常: {e}")

    client.connect()


def test_move_abs(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 2: MOVE_ABS 绝对移动 ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2001,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_X"].index, values=(120.5,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Y"].index, values=(80.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Z"].index, values=(45.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["ROT_RX"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["ROT_RY"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["ROT_RZ"].index, values=(90.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SPD_PCT"].index, values=(30,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["ACC_PCT"].index, values=(40,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  参数已写入，触发命令...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.MOVE_ABS,)))
    time.sleep(0.8)
    print_vr(client)


def test_move_rel(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 3: MOVE_REL 相对移动 (Z+50) ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2002,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_X"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Y"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Z"].index, values=(50.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SPD_PCT"].index, values=(20,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  触发相对移动...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.MOVE_REL,)))
    time.sleep(0.8)
    print_vr(client)


def test_home(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 4: HOME 回原点 ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2003,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SPD_PCT"].index, values=(30,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  触发回原点...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.HOME,)))
    time.sleep(0.5)
    print_vr(client)


def test_grip(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 5: GRIP_SET 夹紧 → 松开 ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2004,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["IO_GRIP"].index, values=(1,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  触发夹紧...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.GRIP_SET,)))
    time.sleep(0.3)
    print_vr(client)

    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2005,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["IO_GRIP"].index, values=(0,)))
    print("  触发松开...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.GRIP_SET,)))
    time.sleep(0.3)
    print_vr(client)


def test_door(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 6: DOOR_CTRL 开门 → 关门 (机床1) ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2006,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["DEV_ID"].index, values=(1,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["IO_DOOR"].index, values=(1,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  触发开门(机床1)...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.DOOR_CTRL,)))
    time.sleep(0.3)
    print_vr(client)

    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2007,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["IO_DOOR"].index, values=(0,)))
    print("  触发关门(机床1)...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.DOOR_CTRL,)))
    time.sleep(0.3)
    print_vr(client)


def test_wait(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 7: WAIT_MS 等待 500ms ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2008,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["EXT_P1"].index, values=(500,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    t0 = time.time()
    print("  触发延时...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.WAIT_MS,)))
    time.sleep(0.8)
    elapsed = time.time() - t0
    print(f"  实际耗时: {elapsed:.2f}s")
    print_vr(client)


def test_safety_reject(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 8: 安全校验拒绝 (安全等级=2 + 高速) ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2009,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_X"].index, values=(100.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Y"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Z"].index, values=(50.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SPD_PCT"].index, values=(80,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(2,)))
    print("  安全等级=2, 速度=80%, 触发移动(应被拒绝)...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.MOVE_ABS,)))
    time.sleep(0.3)
    print_vr(client)


def test_out_of_range(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 9: 坐标越界 (X=999) ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2010,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_X"].index, values=(999.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Y"].index, values=(0.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["POS_Z"].index, values=(50.0,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SPD_PCT"].index, values=(30,)))
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["SAFETY_LV"].index, values=(5,)))
    print("  X=999 超出范围, 触发移动(应被拒绝)...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.MOVE_ABS,)))
    time.sleep(0.3)
    print_vr(client)


def test_emg_reset(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 10: EMG_RESET 复位报警 ===")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["TASK_ID"].index, values=(2011,)))
    print("  触发报警复位...")
    client.write_vr(VrWriteRequest(start_vr=VR_OFFSET["CMD_CODE"].index, values=(CMD.EMG_RESET,)))
    time.sleep(0.3)
    print_vr(client)


def test_read_vr_batch(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 11: 批量读取 VR[16..25] (状态区) ===")
    values = client.read_vr(VrReadRequest(start_vr=16, count=10))
    labels = ["RESULT", "STATUS", "CUR_X", "CUR_Y", "CUR_Z", "CUR_RX", "CUR_RY", "CUR_RZ", "ALM_CODE", "IO_STAT"]
    for label, val in zip(labels, values):
        print(f"  {label:>8} = {val}")


def test_batch_write(client: MockZMotionVrClient) -> None:
    print("\n=== 测试 12: 批量写入 (模拟当前 GUI 的写入方式) ===")
    client.write_vr(VrWriteRequest(start_vr=501, values=(1001, 3001, 200.0, 50.0, 10.0, 180.0, 0.0, 0.0)))
    read_back = client.read_vr(VrReadRequest(start_vr=501, count=8))
    print(f"  写入 VR[501..508]: {[1001, 3001, 200.0, 50.0, 10.0, 180.0, 0.0, 0.0]}")
    print(f"  读回 VR[501..508]: {read_back}")
    assert read_back == [1001.0, 3001.0, 200.0, 50.0, 10.0, 180.0, 0.0, 0.0], "批量读写不一致!"
    print("  [通过] 批量写入/读回一致")


def main() -> None:
    print("╔══════════════════════════════════════════════════╗")
    print("║  Mock 控制器 - 模拟测试 (按最终协议)            ║")
    print("║  VR[0]-VR[25] 全寄存器 + 命令执行模拟           ║")
    print("╚══════════════════════════════════════════════════╝")

    client = MockZMotionVrClient(host="127.0.0.1")

    def on_cmd(code: int, fields: dict) -> None:
        print(f"  [回调] 收到命令 {CMD.name(code)} (code={code})")

    client.set_on_command(on_cmd)

    try:
        test_connect_disconnect(client)
        test_move_abs(client)
        test_move_rel(client)
        test_home(client)
        test_grip(client)
        test_door(client)
        test_wait(client)
        test_safety_reject(client)
        test_out_of_range(client)
        test_emg_reset(client)
        test_read_vr_batch(client)
        test_batch_write(client)

        print("\n" + "=" * 50)
        print("  全部测试完成!")
        print("=" * 50)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
