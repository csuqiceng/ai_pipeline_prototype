# 豆包语音接口替换方案评估

日期：2026-06-01

## 需求总结

当前项目已经有一套机械手自然语言交互链路：语音识别、文本解析、安全预检、确认、执行、结果播报。用户希望引入已经跑通的豆包实时语音会话能力，替换当前语音会话中的语音接口能力。

本次目标不是让豆包直接控制机械手，而是把豆包作为更自然的语音入口和语音出口：

```text
用户说话
-> 豆包负责听写/识别
-> 项目现有 NLP、安全预检、确认、执行链路负责决策
-> 项目生成回复文本
-> 豆包负责播报
```

## 当前项目语音实现

当前主项目语音链路集中在以下文件：

- `robot_modbus_lite/voice_mixin.py`
- `robot_modbus_lite/voice_session.py`
- `robot_modbus_lite/iflytek_iat.py`
- `robot_modbus_lite/iflytek_worker.py`
- `robot_modbus_lite/speech_broadcast.py`
- `robot_modbus_lite/operator_ui_mixin.py`
- `robot_modbus_lite/web_voice_service.py`

当前连续语音会话大致流程：

```text
sounddevice 麦克风采集
-> VoiceSessionSegmenter 本地能量 VAD 分段
-> 讯飞 IAT 识别每段 PCM
-> 得到文本
-> _operator_handle_voice_session_text()
-> 现有 NLP/安全/执行链路
-> Windows SAPI 或 pyttsx3 本地 TTS 播报
```

## 已确认实施范围

用户确认 ASR 和 TTS 都要接入豆包。实施仍保持安全边界：豆包负责听写和播报，项目负责 NLP、安全预检、确认和执行。

默认配置仍使用现有 provider：

```env
VOICE_ASR_PROVIDER=iflytek
VOICE_TTS_PROVIDER=local
```

启用豆包：

```env
VOICE_ASR_PROVIDER=doubao
VOICE_TTS_PROVIDER=doubao
```

现有实现的优势是安全链路完整，机械手控制行为都经过项目内部解析和约束。缺点是语音识别、语音播报体验和豆包端到端语音相比不够自然。

## 豆包实时语音能力

已在 `tests/realtime_dialog/python3.7` 中验证豆包 WebSocket demo 可运行。新版鉴权使用：

```text
X-Api-Key
X-Api-Resource-Id: volc.speech.dialog
X-Api-App-Key: PlgvMymc7f3tQnJ6
```

豆包 realtime 接口不是单纯 ASR/TTS，它是端到端语音对话服务：

```text
ASR 语音识别
-> 豆包大模型生成回复
-> TTS 语音合成
```

因此如果完整接管，会和项目现有 NLP/安全执行链路发生职责重叠。

## 为什么不建议豆包直接控制机械手

机械手控制属于高风险动作。豆包大模型可以理解自然语言，但它不是项目内部的机械手协议执行器，也不天然知道当前控制器状态、安全边界、权限等级、报警状态、软限位、流程状态。

直接让豆包决定动作会带来这些风险：

- 可能把闲聊误判成动作指令。
- 可能补全用户没有明确给出的参数。
- 可能绕过现有安全预检和确认流程。
- 行为结果难以稳定测试和追责。

更稳妥的边界是：

```text
豆包负责听和说。
项目负责理解、判断、确认、执行。
```

## 推荐实现方案

推荐分阶段接入，不一次性替换所有语音能力。

### 第一阶段：只替换 ASR

目标：豆包只负责把语音识别成文本，输出格式兼容当前项目。

保持现有接口形态：

```python
{"text": "识别结果", "timing": {...}}
```

接入点：

- 替换或扩展 `voice_mixin.py` 中 `_transcribe_pcm_via_local_client(...)` 的能力。
- 新增豆包 ASR 客户端模块。
- 保留 `_operator_handle_voice_session_text()`、`voice_nlp_adapter.py`、安全预检、执行链路不变。

建议新增文件：

```text
robot_modbus_lite/doubao_realtime_protocol.py
robot_modbus_lite/doubao_voice_client.py
```

这一阶段速度影响最小，安全风险最低。

### 第二阶段：替换 TTS

目标：项目仍然生成回复文本，但播报改用豆包声音。

接入点：

- 在 `speech_broadcast.py` 中新增 `DoubaoSpeechSink`。
- 替换当前 Windows SAPI / pyttsx3 播报。
- 仍然不让豆包决定回复内容。

流程：

```text
项目生成回复文本
-> DoubaoSpeechSink 调豆包 TTS
-> 本地播放 PCM
```

### 第三阶段：优化实时体验

第一版可串行跑通，后续再优化：

- 使用豆包 ASR 中间识别事件更新 UI。
- 控制类指令优先走本地规则，避免每句话都调大模型。
- 问答/解释类指令再调用大模型。
- TTS 只播短回复，长内容显示在界面。
- 做打断逻辑：用户说话时停止当前播报。

## 延迟评估

如果采用：

```text
豆包 ASR -> 项目 NLP/大模型 -> 豆包 TTS
```

延迟会比豆包端到端对话更高，因为链路多了一层项目内部处理。

主要耗时来自：

- 豆包 ASR：等待用户说完和静音判停。
- 项目 NLP：本地规则很快；外部大模型可能 1-5 秒。
- 豆包 TTS：取决于回复文本长度。

优化原则：

- 控制类命令优先走本地规则，不每次调大模型。
- 闲聊、说明书问答、报警解释再走大模型。
- 播报内容尽量短。

## 配置建议

不要把 API Key 硬编码进业务代码。建议放在 `.env`：

```env
DOUBAO_API_KEY=...
DOUBAO_RESOURCE_ID=volc.speech.dialog
DOUBAO_APP_KEY=PlgvMymc7f3tQnJ6
DOUBAO_SPEAKER=zh_male_yunzhou_jupiter_bigtts
DOUBAO_WS_URL=wss://openspeech.bytedance.com/api/v3/realtime/dialogue
```

## 推荐结论

建议采用“两步替换”：

1. 先做豆包 ASR，替换当前讯飞识别，保留现有项目 NLP、安全和执行链路。
2. ASR 稳定后，再做豆包 TTS，替换本地 SAPI/pyttsx3 播报。

暂不建议直接启用豆包端到端大模型控制机械手。
