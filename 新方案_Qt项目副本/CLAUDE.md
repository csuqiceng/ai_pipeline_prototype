# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dual-lathe robotic arm natural language programming control system. A Qt GUI desktop application that accepts Chinese natural language / voice commands, translates them into robot motion instructions, and sends them to either a simulated or real ZMotion controller via Modbus TCP.

## Run & Build

**Run from source (GUI):**
```bash
python gui_main.py
```

**Run with iflytek voice worker subprocess:**
```bash
python gui_main.py --iflytek-worker
```

**Build EXE (PyInstaller):**
```bat
build_qt.bat
```
Output: `打包输出/dist/RobotModbusLite/RobotModbusLite.exe`

The `.spec` file bundles `data/`, `Windows Python（64位）/` (ZMotion SDK DLLs), and `.env` into the package.

## Architecture

```
gui_main.py ── entry point, dispatches to qt_gui or iflytek_worker
main.py ────── CLI entry point (text-only, no GUI)

robot_modbus_lite/          Core business logic
  qt_gui.py                 PySide6 GUI (QMainWindow), all UI panels
  service.py                RobotModbusService — command building & protocol translation
  models.py                 Dataclasses: QueryRecord, StandardProtocolCommand, SixAxis*, VR models
  query_table.py            Load/save query table (JSON or TSV CSV)
  command_parser.py         Chinese text → query_key (alias matching)
  flow_store.py             Named flow sequences (load/save JSON)
  zmotion_client.py         Real ZMotion SDK wrapper (VR + Modbus TCP read/write)
  voice_nlp_adapter.py      Voice/DeepSeek NLP → structured actions
  deepseek_client.py        DeepSeek API client
  iflytek_iat.py / iflytek_worker.py  iFlytek real-time ASR
  avoidance_config.py       Safe point & avoidance zone config
  system_config.py          Axis range limits
  license_manager.py        Subscription license validation (AES-encrypted cache)

mock_controller/            Local simulator (no hardware needed)
  controller.py             MockController — in-memory VR/Modbus registers, threaded command execution
  protocol.py               Register layout, command codes, address constants, status bit masks
  client.py                 MockZMotionVrClient — ControllerClient-compatible adapter

data/                       Runtime data files
  query_table.json          Position/action lookup table (bootstrapped from CSV)
  flows.json                Saved flow sequences
  system_config.json        Axis range config
  avoidance_rules.json      Safe point definitions
```

## Key Design Patterns

**ControllerClient protocol** (`models.py`): Both `ZMotionVrClient` (real hardware) and `MockZMotionVrClient` (simulator) implement the same `ControllerClient` protocol interface. GUI switches between them based on user selection.

**Two protocol layers:**
- **VR protocol** (legacy): Write VR registers directly via ZMotion SDK (`ZAux_Direct_SetVrf`/`GetVrf`)
- **Modbus TCP protocol** (current, six-axis): Write IEEE float registers (`ZAux_Modbus_Set4x_Float`) + BIT registers for Func 104/106/107/108

**Command flow:** Text input → `command_parser` → `query_key` → `QueryRecord` from table → `service.build_*_command` → register writes via controller client. Six-axis commands go through `VR_TO_SIX_MAP` to translate standard codes (1001–6002) to hardware function numbers (104/106/107/108).

**Register layout** (`protocol.py`): All address constants, status bit masks, and safety limits are centralized here. Six-axis status uses bit flags at IEEE(34): Bit2=complete(4), Bit3=error(8), Bit6=alarm(64).

**Query table bootstrapping:** On first run, `query_table.json` is auto-generated from `机械臂AI地址表.csv`. Subsequent runs use JSON directly.

## Environment

- Python 3.10, PySide6, PyInstaller
- ZMotion SDK DLLs in `Windows Python（64位）/Windows Python（64位）/dll库文件/`
- `.env` file for API keys: `IFLYTEK_APP_ID`, `IFLYTEK_API_KEY`, `IFLYTEK_API_SECRET`, `DEEPSEEK_API_KEY`
- Windows-only (SDK DLLs, PyInstaller, console=False)
