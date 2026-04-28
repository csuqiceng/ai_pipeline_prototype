# Func108 虚拟直线插补 — 上位机通讯参数说明

版本: V1.0 | 日期: 2026-04-27 | 控制器: VPLC516E | 函数文件: func108_linear_interp.bas

## 一、上位机下传参数（17个）

上位机通过Modbus IEEE寄存器下传运动参数，写IEEE(0)=108并触发IEEE(32)=1启动函数。

PIStudio Modbus地址 参数名 类型 说明 地址

IEEE(0) 40 func_id INT 函数编号，写入108触发

IEEE(2) 42 target_x FLOAT 目标位置X (mm)

IEEE(4) 44 target_y FLOAT 目标位置Y (mm)

IEEE(6) 46 target_z FLOAT 目标位置Z (mm)

IEEE(8) 48 target_rx FLOAT 目标姿态Rx (°)

IEEE(10) 410 target_ry FLOAT 目标姿态Ry (°)

IEEE(12) 412 target_rz FLOAT 目标姿态Rz (°)

IEEE(14) 414 spd FLOAT 运动速度 (mm/s)

IEEE(16) 416 acc_v FLOAT 加速度 (mm/s²)

IEEE(18) 418 dec_v FLOAT 减速度 (mm/s²)

IEEE(20) 420 stop_cmd INT 停止指令（见下方详细说明）

IEEE(22) 422 fuzzy_pos INT 位置模式: 0=绝对 1=增量

IEEE(24) 424 fuzzy_spd INT 速度模式: 0=绝对值 1=叠加当前值

IEEE(26) 426 fuzzy_acc INT 加速度模式: 0=绝对值 1=叠加当前值

IEEE(28) 428 fuzzy_dec INT 减速度模式: 0=绝对值 1=叠加当前值

IEEE(30) 430 move_type INT 运动模式: 0=直线插补 1=PTP关节

IEEE(32) 432 触发 INT 写1触发函数执行

### stop_cmd 停止指令详细说明:

stop_cmd值 ZBasic指令 说明

0 — 正常执行运动，不停止

1 RAPIDSTOP(2) 急停，立即停止所有轴

2 RAPIDSTOP(1) 快速停止，减速停

3 CANCEL 取消当前运动指令

4 CANCEL 取消当前运动指令

5 MOVEABS(DPOS) 移动到当前位置（等效暂停）

### fuzzy模糊标志详细说明:

参数 值=0（绝对模式） 值=1（增量/叠加模式）

fuzzy_pos target_x/y/z/rx/ry/rz 为绝对目标位置 target_x/y/z/rx/ry/rz 为增量，叠加当前DPOS

fuzzy_spd spd 为绝对速度值 spd 叠加当前SPEED: actual_spd = SPEED + spd

fuzzy_acc acc_v 为绝对加速度值 acc_v 叠加当前ACCEL: actual_acc = ACCEL + acc_v

fuzzy_dec dec_v 为绝对减速度值 dec_v 叠加当前DECEL: actual_dec = DECEL + dec_v

### move_type 运动模式说明:

move_type 模式 ZBasic实现 说明

0 直线插补 CONNFRAME + MOVEABS 笛卡尔空间直线运动，各轴联动

1 PTP关节 MOVER2_PABS 关节空间点到点，各轴独立运动

## 二、回传数据（20个）

控制器执行后通过Modbus IEEE寄存器回传状态和实时数据，上位机可轮询读取。

PIStudio Modbus地址 参数名 类型 说明 地址

IEEE(34) 434 函数状态 INT 状态位标志（见下方位定义）

IEEE(36) 436 func_id回传 INT 当前执行函数号

IEEE(38) 438 报警代码 INT 报警位标志（见下方位定义）

IEEE(40) 440 当前X FLOAT 虚拟轴6 DPOS (mm)

IEEE(42) 442 当前Y FLOAT 虚拟轴7 DPOS (mm)

IEEE(44) 444 当前Z FLOAT 虚拟轴8 DPOS (mm)

IEEE(46) 446 当前Rx FLOAT 虚拟轴9 DPOS (°)

IEEE(48) 448 当前Ry FLOAT 虚拟轴10 DPOS (°)

IEEE(50) 450 当前Rz FLOAT 虚拟轴11 DPOS (°)

IEEE(52) 452 当前速度 FLOAT FORCE_SPEED (mm/s)

IEEE(54) 454 剩余距离 FLOAT 运动剩余距离

IEEE(56) 456 运动状态 INT 0=空闲 1=运动中

IEEE(58) 458 J1角度 FLOAT 实际轴0 DPOS (°)

IEEE(60) 460 J2角度 FLOAT 实际轴1 DPOS (°)

IEEE(62) 462 J3角度 FLOAT 实际轴2 DPOS (°)

IEEE(64) 464 J4角度 FLOAT 实际轴3 DPOS (°)

IEEE(66) 466 J5角度 FLOAT 实际轴4 DPOS (°)

IEEE(68) 468 J6角度 FLOAT 实际轴5 DPOS (°)

IEEE(70) 470 ECAT状态 INT EtherCAT状态字

IEEE(72) 472 帧状态 INT FRAME_STATUS

### IEEE(34) 函数状态位定义:

位 值 含义 说明

Bit0 1 已收到 函数已被触发接收

Bit1 2 执行中 运动正在执行

Bit2 4 完成 运动正常完成

Bit3 8 错误 执行出错（如速度=0）

Bit6 64 有报警 参数被钳位或其他报警

常见状态组合: 4=正常完成 | 68=完成+钳位报警 | 72=错误+报警 | 8=速度为0错误

### IEEE(38) 报警代码位定义:

位 值 含义 触发条件

Bit0 1 半径超限 R3d被钳位到min_r或max_r

Bit1 2 高度超限 Z被钳位到min_h或max_h

位 值 含义 触发条件

Bit3 8 速度超限 速度被钳位到safe_spd，或速度=0

Bit4 16 加速度超限 加速度被钳位到safe_acc

Bit5 32 减速度超限 减速度被钳位到safe_dec

Bit6 64 ECAT报警 EtherCAT通讯异常

## 三、安全限位参数（IEEE 1700-1712）

这些参数由控制器初始化时设置，Func108执行时会读取进行安全钳位。

Modbus地址 PIStudio地址 参数名 说明

IEEE(1700) 41700 min_r 最小水平半径 (mm)，R3d < min_r时钳位

IEEE(1702) 41702 max_r 最大水平半径 (mm)，R3d > max_r时钳位

IEEE(1704) 41704 min_h 最低高度 (mm)，Z < min_h时钳位

IEEE(1706) 41706 max_h 最高高度 (mm)，Z > max_h时钳位

IEEE(1708) 41708 safe_spd 安全速度上限 (mm/s)，速度 > safe_spd时钳位

IEEE(1710) 41710 safe_acc 安全加速度上限 (mm/s²)

IEEE(1712) 41712 safe_dec 安全减速度上限 (mm/s²)

## 四、执行流程

步骤 操作 说明

1 读取参数 从IEEE(2-30)读取目标位置、速度、模糊标志、停止指令、运动模式

2 停止判断 若stop_cmd>0，执行对应停止操作后直接返回

3 坐标系切换 调用CoordSwitch_Cartesian()切换到笛卡尔坐标系

4 模糊处理 根据fuzzy_pos/spd/acc/dec标志，解释为增量或绝对模式

5 安全钳位 读取安全参数(IEEE 1700-1712)，对速度/加速度/减速度进行上限钳位

6 空间限位 计算R3d水平半径，对XY半径钳位(min_r/max_r)，对Z高度钳位(min_h/max_h)

7 执行运动 move_type=0: CONNFRAME+MOVEABS直线; move_type=1: MOVER2_PABS关节

8 等待完成 WAIT IDLE等待运动结束

9 回传状态 设置IEEE(34)和IEEE(38)返回执行结果

## 五、通讯示例

### 示例1: 绝对直线运动到(200, 100, 300)，速度50mm/s

操作 地址 写入值 说明

写入 IEEE(0) 108 函数编号

写入 IEEE(2) 200 目标X

写入 IEEE(4) 100 目标Y

写入 IEEE(6) 300 目标Z

写入 IEEE(8~12) 0 Rx/Ry/Rz=0

写入 IEEE(14) 50 速度50mm/s

写入 IEEE(16) 500 加速度500mm/s²

写入 IEEE(18) 500 减速度500mm/s²

操作 地址 写入值 说明

写入 IEEE(20) 0 不停止

写入 IEEE(22~28) 0 全部绝对模式

写入 IEEE(30) 0 直线插补

写入 IEEE(32) 1 触发执行

读取 IEEE(34) 4 正常完成

### 示例2: 增量运动X+50, Y+30，PTP关节模式

操作 地址 写入值 说明

写入 IEEE(0) 108 函数编号

写入 IEEE(2) 50 增量X=+50

写入 IEEE(4) 30 增量Y=+30

写入 IEEE(6~12) 0 Z/Rx/Ry/Rz=0

写入 IEEE(14) 30 速度30mm/s

写入 IEEE(22) 1 位置=增量模式

写入 IEEE(30) 1 PTP关节模式

写入 IEEE(32) 1 触发执行