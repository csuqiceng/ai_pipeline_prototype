# KINETIX OS 工业机械手控制系统

本目录是 HTML/React 前端原型，用于逐步替换当前 PySide6 页面。当前阶段先做本地 mock 闭环，后续通过 Python 本地服务接入机械手状态、NLP、预检、执行和日志。

## 运行

前置条件：Node.js。

```powershell
npm install
npm run dev
```

默认开发端口：`http://127.0.0.1:3000`。

## 构建

```powershell
npm run build
```

构建产物位于 `dist/`，后续会由 PyInstaller 收集到 EXE 发布目录的 `_internal/web_dist/`。

## 数据模式

`.env.local` 可配置：

```text
VITE_API_BASE_URL=http://127.0.0.1:8765
VITE_WS_URL=ws://127.0.0.1:8765/ws/telemetry
VITE_DATA_MODE=mock
```

`VITE_DATA_MODE=mock` 使用前端内置状态机，适合离线演示。`VITE_DATA_MODE=api` 会连接本地 Python Web API 和 WebSocket。

后端 mock API 启动命令：

```powershell
python -m robot_modbus_lite.web_server --host 127.0.0.1 --port 8765
```
