import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Activity, CheckCircle2, Mic, Play, RefreshCw, Search, Square, TimerReset } from 'lucide-react';
import {
  type ControlExecutionResponse,
  executeTemplate,
  fetchBridgeStatus,
  fetchDashboard,
  fetchFlows,
  fetchTemplates,
  fetchVoiceDevices,
  fetchVoiceStatus,
  parseNlpText,
  resetFlow,
  startVoice,
  startFlow,
  stepFlow,
  stopVoice,
  stopFlow,
} from '../api/client';
import type { BridgeStatus } from '../api/client';
import type { FlowRecord, TemplateRecord } from '../api/client';
import RightStatusPanel from '../components/RightStatusPanel';
import { connectionLabel, runningStateLabel, useRobotStore } from '../state/robotStore';

interface ExecutionMonitorProps {
  engineerMode?: boolean;
}

type RunTab = 'single' | 'flow' | 'nlp';

export default function ExecutionMonitor({ engineerMode = false }: ExecutionMonitorProps) {
  if (engineerMode) return <EngineerRunPage />;
  return <OperatorExecutionMonitor />;
}

function EngineerRunPage() {
  const { state, snapshot, cancelActivePlan, hydrateDashboard, submitCommand } = useRobotStore();
  const [templates, setTemplates] = useState<TemplateRecord[]>([]);
  const [flows, setFlows] = useState<FlowRecord[]>([]);
  const [voiceDevices, setVoiceDevices] = useState<{ id: string; name: string }[]>([]);
  const [selectedFlow, setSelectedFlow] = useState('');
  const [filterText, setFilterText] = useState('');
  const [typeFilter, setTypeFilter] = useState('全部');
  const [activeTab, setActiveTab] = useState<RunTab>('single');
  const [nlpText, setNlpText] = useState('查询当前状态');
  const [parseResult, setParseResult] = useState<Record<string, unknown> | null>(null);
  const [lastBridge, setLastBridge] = useState<ControlExecutionResponse['bridge'] | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus | null>(null);
  const [voiceRunning, setVoiceRunning] = useState(false);
  const [statusText, setStatusText] = useState('Web 工程师运行页已加载。');

  const refreshRuntimeData = () => {
    Promise.allSettled([fetchTemplates(), fetchFlows(), fetchVoiceDevices(), fetchBridgeStatus(), fetchVoiceStatus()]).then(([templateResult, flowResult, voiceResult, bridgeResult, voiceStatusResult]) => {
      if (templateResult.status === 'fulfilled') setTemplates(templateResult.value.records);
      if (flowResult.status === 'fulfilled') {
        setFlows(flowResult.value.flows);
        setSelectedFlow((current) => current || flowResult.value.flows[0]?.name || '');
      }
      if (voiceResult.status === 'fulfilled') {
        setVoiceDevices(voiceResult.value.devices.map((device) => ({ id: device.id, name: device.name })));
      }
      if (bridgeResult.status === 'fulfilled') setBridgeStatus(bridgeResult.value);
      if (voiceStatusResult.status === 'fulfilled') setVoiceRunning(voiceStatusResult.value.running);
      setStatusText('模板、流程和语音设备已刷新。');
    });
  };

  useEffect(() => {
    refreshRuntimeData();
  }, []);

  const commandTypes = useMemo(() => ['全部', ...Array.from(new Set(templates.map((item) => `Func${item.func_num}`)))], [templates]);

  const visibleTemplates = useMemo(() => {
    const keyword = filterText.trim().toLowerCase();
    return templates
      .filter((item) => typeFilter === '全部' || `Func${item.func_num}` === typeFilter)
      .filter((item) => {
        if (!keyword) return true;
        return `${item.query_key} ${item.keywords} ${item.description} ${item.func_num}`.toLowerCase().includes(keyword);
      })
      .slice(0, 36);
  }, [filterText, templates, typeFilter]);

  const selectedFlowRecord = flows.find((flow) => flow.name === selectedFlow) ?? flows[0];

  const parseText = () => {
    parseNlpText(nlpText)
      .then((result) => {
        setParseResult(result);
        setStatusText('自然语言解析完成。');
      })
      .catch((error) => {
        setParseResult({ error: error.message });
        setStatusText(`解析失败：${error.message}`);
      });
  };

  const runText = (text: string) => {
    parseNlpText(text)
      .then((result) => {
        setParseResult(result);
        const action = firstNlpAction(result);
        if (action?.action_type === 'template' && action.target) {
          runTemplate(action.target);
          return;
        }
        if (action?.action_type === 'flow' && action.target) {
          setSelectedFlow(action.target);
          startFlow(action.target)
            .then((response) => {
              hydrateDashboard(response.dashboard);
              setLastBridge(response.bridge ?? null);
              setStatusText(response.bridge?.message ?? `已提交流程：${action.target}`);
            })
            .catch((error) => setStatusText(`流程执行失败：${error.message}`));
          return;
        }
        submitCommand(text);
        setStatusText(`已提交自然语言计划：${text}`);
      })
      .catch((error) => {
        submitCommand(text);
        setStatusText(`解析失败，已按对话计划提交：${error.message}`);
      });
  };

  const runTemplate = (queryKey: string) => {
    executeTemplate(queryKey)
      .then((result) => {
        hydrateDashboard(result.dashboard);
        setLastBridge(result.bridge ?? null);
        setStatusText(result.bridge?.message ?? `已提交模板：${queryKey}`);
      })
      .catch((error) => setStatusText(`模板执行失败：${error.message}`));
  };

  const runFlow = (mode: 'start' | 'step') => {
    const flowName = selectedFlowRecord?.name;
    if (!flowName) {
      setStatusText('当前没有可执行流程。');
      return;
    }
    const request = mode === 'step' ? stepFlow(flowName) : startFlow(flowName);
    request
      .then((result) => {
        hydrateDashboard(result.dashboard);
        setLastBridge(result.bridge ?? null);
        setStatusText(result.bridge?.message ?? `${mode === 'step' ? '单步执行' : '开始流程'}：${flowName}`);
      })
      .catch((error) => setStatusText(`流程执行失败：${error.message}`));
  };

  const stopCurrentFlow = () => {
    stopFlow()
      .then((result) => {
        hydrateDashboard(result.dashboard);
        setStatusText('流程/计划已停止。');
      })
      .catch((error) => setStatusText(`停止失败：${error.message}`));
  };

  const checkConnection = () => {
    fetchBridgeStatus()
      .then((status) => {
        setBridgeStatus(status);
        setStatusText(`桥接模式 ${status.mode}，服务${status.service_attached ? '已挂载' : '未挂载'}，模拟控制器${status.mock_controller_connected ? '已连接' : '待连接'}。`);
      })
      .catch((error) => setStatusText(`连接检测失败：${error.message}`));
  };

  const readFeedback = () => {
    fetchDashboard()
      .then((dashboard) => {
        hydrateDashboard(dashboard);
        setStatusText('已读取当前反馈快照。');
      })
      .catch((error) => setStatusText(`读取反馈失败：${error.message}`));
  };

  const toggleVoice = () => {
    const request = voiceRunning ? stopVoice() : startVoice();
    request
      .then((status) => {
        setVoiceRunning(status.running);
        if (status.last_text) {
          setNlpText(status.last_text);
          setStatusText(`语音识别完成：${status.last_text}`);
          return;
        }
        if (status.running && status.phase === 'recognizing') {
          setStatusText('语音识别中，等待最终文本。');
          pollEngineerVoiceResult();
          return;
        }
        setStatusText(status.running ? '后端语音采集已启动，请说话。' : status.last_error ? `语音识别失败：${status.last_error}` : '后端语音采集已停止。');
      })
      .catch((error) => setStatusText(`语音服务失败：${error.message}`));
  };

  const pollEngineerVoiceResult = (attempt = 0) => {
    window.setTimeout(() => {
      fetchVoiceStatus()
        .then((status) => {
          setVoiceRunning(status.running);
          if (status.last_text) {
            setNlpText(status.last_text);
            setStatusText(`语音识别完成：${status.last_text}`);
            return;
          }
          if (status.running && attempt < 60) {
            pollEngineerVoiceResult(attempt + 1);
            return;
          }
          setStatusText(status.last_error ? `语音识别失败：${status.last_error}` : '语音识别未返回文本。');
        })
        .catch((error) => setStatusText(`语音服务失败：${error.message}`));
    }, 1000);
  };

  const resetCurrentFlow = () => {
    resetFlow()
      .then((result) => {
        hydrateDashboard(result.dashboard);
        setStatusText('流程状态已重置。');
      })
      .catch((error) => setStatusText(`重置失败：${error.message}`));
  };

  return (
    <div className="flex h-full overflow-hidden bg-[#f0f4f8]">
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
        <section className="industrial-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-on-surface">连接与反馈</h2>
            <span className="rounded bg-surface-container-high px-2 py-1 text-[10px] font-bold text-text-secondary">
              {statusText}
            </span>
          </div>
          <div className="grid grid-cols-12 gap-3">
            <Field label="控制器地址" value={bridgeStatus?.controller_host ?? '127.0.0.1'} className="col-span-3" />
            <SelectField label="控制器类型" value={bridgeModeLabel(bridgeStatus?.mode)} options={['模拟控制器', '服务构建', 'Dry Run']} className="col-span-3" />
            <Field label="协议" value="Modbus TCP (V2.2)" className="col-span-3" disabled />
            <StatusField label="连接状态" value={connectionLabel(snapshot.connection.controller)} className="col-span-3" />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <ToolbarButton icon={<CheckCircle2 size={14} />} label="检测连接" onClick={checkConnection} />
            <ToolbarButton icon={<RefreshCw size={14} />} label="读取反馈" onClick={readFeedback} />
            <ToolbarButton icon={<Square size={14} />} label="停止执行" tone="danger" onClick={cancelActivePlan} />
          </div>
        </section>

        <section className="industrial-card flex min-h-[430px] flex-col overflow-hidden">
          <div className="flex border-b border-border-divider bg-white px-4 pt-3">
            <TabButton active={activeTab === 'single'} label="单次执行" onClick={() => setActiveTab('single')} />
            <TabButton active={activeTab === 'flow'} label="流程执行" onClick={() => setActiveTab('flow')} />
            <TabButton active={activeTab === 'nlp'} label="自然语言执行" onClick={() => setActiveTab('nlp')} />
          </div>

          {activeTab === 'single' && (
            <div className="flex flex-1 flex-col gap-3 p-4">
              <div className="grid grid-cols-12 gap-3">
                <div className="col-span-8 flex items-center gap-2 rounded border border-border-divider bg-white px-3">
                  <Search size={15} className="text-text-secondary" />
                  <input
                    value={filterText}
                    onChange={(event) => setFilterText(event.target.value)}
                    className="h-10 min-w-0 flex-1 bg-transparent text-xs outline-none"
                    placeholder="按指令名、关键词、功能号筛选"
                  />
                </div>
                <select
                  value={typeFilter}
                  onChange={(event) => setTypeFilter(event.target.value)}
                  className="col-span-3 h-10 rounded border border-border-divider bg-white px-3 text-xs outline-none"
                >
                  {commandTypes.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
                <div className="flex items-center justify-end text-[10px] font-bold text-text-secondary">{visibleTemplates.length} / {templates.length}</div>
              </div>
              <div className="grid grid-cols-2 gap-3 overflow-y-auto pr-1 xl:grid-cols-3 2xl:grid-cols-4">
                {visibleTemplates.map((item) => (
                  <button
                    key={item.query_key}
                    onClick={() => runTemplate(item.query_key)}
                    className="min-h-24 rounded border border-border-divider bg-white p-3 text-left transition-all hover:border-primary-container/30 hover:bg-primary-container/5"
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-bold">{item.query_key}</span>
                      <span className="shrink-0 rounded bg-surface-container-high px-1.5 py-0.5 text-[10px] font-bold text-text-secondary">
                        Func{item.func_num}
                      </span>
                    </div>
                    <p className="line-clamp-2 text-[11px] leading-relaxed text-text-secondary">{item.description || item.keywords}</p>
                  </button>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'flow' && (
            <div className="grid flex-1 grid-cols-12 gap-4 p-4">
              <div className="col-span-4 rounded border border-border-divider bg-white p-4">
                <label className="mb-2 block text-[10px] font-bold text-text-secondary">流程选择</label>
                <select
                  value={selectedFlow}
                  onChange={(event) => setSelectedFlow(event.target.value)}
                  className="mb-4 h-10 w-full rounded border border-border-divider bg-white px-3 text-xs outline-none"
                >
                  {flows.map((flow) => (
                    <option key={flow.name}>{flow.name}</option>
                  ))}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <ToolbarButton icon={<Play size={14} />} label="开始流程" onClick={() => runFlow('start')} />
                  <ToolbarButton icon={<Play size={14} />} label="单步执行" onClick={() => runFlow('step')} />
                  <ToolbarButton icon={<Square size={14} />} label="停止流程" tone="danger" onClick={stopCurrentFlow} />
                  <ToolbarButton icon={<TimerReset size={14} />} label="重置流程" onClick={resetCurrentFlow} />
                </div>
              </div>
              <div className="col-span-8 rounded border border-border-divider bg-white p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-bold">流程步骤</h3>
                  <span className="text-[10px] font-bold text-text-secondary">间隔 {selectedFlowRecord?.step_delay_ms ?? 0} ms</span>
                </div>
                <div className="space-y-2">
                  {(selectedFlowRecord?.steps ?? []).map((step, index) => (
                    <div key={`${step}-${index}`} className="flex items-center gap-3 rounded border border-border-divider bg-surface px-3 py-2 text-xs">
                      <span className="flex h-6 w-6 items-center justify-center rounded bg-primary-container text-[10px] font-bold text-white">{index + 1}</span>
                      <span className="font-bold">{step}</span>
                    </div>
                  ))}
                  {!selectedFlowRecord && <p className="text-xs text-status-disabled">暂无流程。</p>}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'nlp' && (
            <div className="grid flex-1 grid-cols-12 gap-4 p-4">
              <div className="col-span-5 flex flex-col gap-3">
                <textarea
                  value={nlpText}
                  onChange={(event) => setNlpText(event.target.value)}
                  className="h-32 resize-none rounded border border-border-divider bg-white p-3 text-sm outline-none"
                  placeholder="输入自然语言指令"
                />
                <label className="flex items-center gap-2 text-xs font-bold text-on-surface">
                  <input type="checkbox" className="h-4 w-4 accent-primary-container" /> 使用 AI 增强解析
                </label>
                <select className="h-10 rounded border border-border-divider bg-white px-3 text-xs outline-none">
                  {(voiceDevices.length ? voiceDevices : [{ id: 'none', name: '未检测到麦克风设备' }]).map((device) => (
                    <option key={device.id}>{device.name}</option>
                  ))}
                </select>
                <div className="grid grid-cols-4 gap-2">
                  <ToolbarButton icon={<RefreshCw size={14} />} label="刷新设备" onClick={refreshRuntimeData} />
                  <ToolbarButton icon={<Search size={14} />} label="解析文本" onClick={parseText} />
                  <ToolbarButton icon={<Mic size={14} />} label={voiceRunning ? '停止录音' : '开始录音'} onClick={toggleVoice} />
                  <ToolbarButton icon={<Play size={14} />} label="执行" onClick={() => runText(nlpText)} />
                </div>
              </div>
              <pre className="col-span-7 overflow-auto rounded border border-border-divider bg-[#0f1720] p-4 text-xs leading-relaxed text-[#d5e7ef]">
                {JSON.stringify(parseResult ?? { status: '等待解析', text: nlpText }, null, 2)}
              </pre>
            </div>
          )}
        </section>

        <section className="grid grid-cols-12 gap-4">
          <StatusSummary snapshot={snapshot} />
          <ExecutionSummary progress={state.execution?.progress ?? 0} status={runningStateLabel(state.execution?.status ?? 'idle')} />
          <BridgeSummary bridge={lastBridge} />
          <RecentRecords events={state.recent_events.slice(-5)} />
        </section>
      </main>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}

function OperatorExecutionMonitor() {
  const { state, snapshot, cancelActivePlan, resetMock } = useRobotStore();
  const execution = state.execution;

  return (
    <div className="flex h-full overflow-hidden bg-[#f0f4f8]">
      <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-8">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-on-surface">执行监控</h2>
            <p className="mt-1 text-xs text-text-secondary">跟踪当前计划、预检状态和执行进度。</p>
          </div>
          <div className="flex gap-2">
            <ToolbarButton icon={<TimerReset size={15} />} label="刷新" onClick={resetMock} />
            <ToolbarButton icon={<Square size={15} />} label="停止" tone="danger" onClick={cancelActivePlan} />
          </div>
        </div>

        <section className="industrial-card p-6">
          <div className="mb-5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Activity className="text-primary-container" />
              <h3 className="font-bold">当前执行</h3>
            </div>
            <span className="rounded bg-surface-container-high px-2 py-1 text-[10px] font-bold text-text-secondary">
              {runningStateLabel(execution?.status ?? 'idle')}
            </span>
          </div>
          <div className="mb-5 h-2 overflow-hidden rounded-full bg-surface-container-high">
            <div className="h-full bg-primary transition-all duration-500" style={{ width: `${execution?.progress ?? 0}%` }} />
          </div>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-4">
            {(execution?.stages ?? []).map((stage) => (
              <div key={stage.id} className="rounded border border-border-divider bg-white p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="text-xs font-bold">{stage.name}</span>
                  <CheckCircle2 size={14} className={stage.status === 'complete' ? 'text-primary' : 'text-status-disabled'} />
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-container-high">
                  <div className="h-full bg-secondary" style={{ width: `${stage.progress}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}

function StatusSummary({ snapshot }: { snapshot: ReturnType<typeof useRobotStore>['snapshot'] }) {
  const position = snapshot.position.cartesian;
  return (
    <section className="industrial-card col-span-4 p-4">
      <h3 className="mb-3 text-sm font-bold">机械手状态</h3>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <InfoPill label="R" value={`${position.r.toFixed(1)} mm`} />
        <InfoPill label="Z" value={`${position.z.toFixed(1)} mm`} />
        <InfoPill label="速度" value={`${snapshot.motion.speed_percent}%`} />
        <InfoPill label="当前函数" value={snapshot.current_function || '-'} />
      </div>
    </section>
  );
}

function ExecutionSummary({ progress, status }: { progress: number; status: string }) {
  return (
    <section className="industrial-card col-span-2 p-4">
      <h3 className="mb-3 text-sm font-bold">执行摘要</h3>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-bold">{status}</span>
        <span className="text-text-secondary">{progress}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded bg-surface-container-high">
        <div className="h-full bg-primary" style={{ width: `${progress}%` }} />
      </div>
    </section>
  );
}

function BridgeSummary({ bridge }: { bridge: ControlExecutionResponse['bridge'] | null }) {
  const execution = bridge?.detail?.execution as
    | {
        ok?: boolean;
        results?: {
          query_key?: string;
          func_num?: number;
          feedback?: number[];
          logs?: { category?: string; action?: string; result?: string; detail?: string }[];
        }[];
      }
    | undefined;
  const lastResult = execution?.results?.[execution.results.length - 1];
  const logs = lastResult?.logs?.slice(-3) ?? [];

  return (
    <section className="industrial-card col-span-3 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold">模拟回执</h3>
        <span className="rounded bg-surface-container-high px-2 py-1 text-[10px] font-bold text-text-secondary">
          {bridge?.mode ?? '未执行'}
        </span>
      </div>
      <div className="space-y-2 text-[11px]">
        <InfoLine label="结果" value={execution ? (execution.ok ? '成功' : '失败') : '-'} />
        <InfoLine label="步骤" value={`${execution?.results?.length ?? 0}`} />
        <InfoLine label="反馈" value={formatFeedback(lastResult?.feedback)} />
        <div className="space-y-1 pt-1">
          {logs.map((log, index) => (
            <div key={`${log.action}-${index}`} className="truncate text-text-secondary">
              {log.category}/{log.action}: {log.result}
            </div>
          ))}
          {!logs.length && <div className="text-status-disabled">暂无模拟控制器日志。</div>}
        </div>
      </div>
    </section>
  );
}

function RecentRecords({ events }: { events: { id: string; timestamp: string; text: string }[] }) {
  return (
    <section className="industrial-card col-span-3 p-4">
      <h3 className="mb-3 text-sm font-bold">最近执行记录</h3>
      <div className="space-y-2">
        {events.map((event) => (
          <div key={event.id} className="flex gap-2 text-xs">
            <span className="w-16 shrink-0 font-mono text-text-secondary">{event.timestamp}</span>
            <span className="truncate">{event.text}</span>
          </div>
        ))}
        {!events.length && <p className="text-xs text-status-disabled">暂无记录。</p>}
      </div>
    </section>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="shrink-0 text-text-secondary">{label}</span>
      <span className="truncate font-mono font-bold">{value}</span>
    </div>
  );
}

function formatFeedback(feedback?: number[]) {
  if (!feedback?.length) return '-';
  return feedback.slice(0, 3).map((value) => Number(value).toFixed(1)).join(', ');
}

function Field({ label, value, className = '', disabled = false }: { label: string; value: string; className?: string; disabled?: boolean }) {
  return (
    <label className={className}>
      <span className="mb-1 block text-[10px] font-bold text-text-secondary">{label}</span>
      <input readOnly value={value} disabled={disabled} className="h-10 w-full rounded border border-border-divider bg-white px-3 text-xs outline-none disabled:bg-surface" />
    </label>
  );
}

function SelectField({ label, value, options, className = '' }: { label: string; value: string; options: string[]; className?: string }) {
  return (
    <label className={className}>
      <span className="mb-1 block text-[10px] font-bold text-text-secondary">{label}</span>
      <select value={value} onChange={() => undefined} className="h-10 w-full rounded border border-border-divider bg-white px-3 text-xs outline-none">
        {options.map((item) => (
          <option key={item}>{item}</option>
        ))}
      </select>
    </label>
  );
}

function StatusField({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <span className="mb-1 block text-[10px] font-bold text-text-secondary">{label}</span>
      <div className="flex h-10 items-center gap-2 rounded border border-border-divider bg-white px-3 text-xs font-bold">
        <span className="h-2 w-2 rounded-full bg-primary" />
        {value}
      </div>
    </div>
  );
}

function ToolbarButton({
  icon,
  label,
  onClick,
  tone = 'default',
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      onClick={onClick}
      className={`flex h-9 items-center justify-center gap-2 rounded border px-3 text-xs font-bold transition-all ${
        tone === 'danger'
          ? 'border-danger bg-danger text-white hover:brightness-110'
          : 'border-border-divider bg-white text-on-surface hover:bg-surface'
      }`}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function TabButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`mr-2 border-b-2 px-4 py-3 text-xs font-bold ${
        active ? 'border-primary-container text-primary-container' : 'border-transparent text-text-secondary hover:text-on-surface'
      }`}
    >
      {label}
    </button>
  );
}

function InfoPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border-divider bg-white px-3 py-2">
      <div className="mb-1 text-[10px] font-bold text-text-secondary">{label}</div>
      <div className="truncate font-mono font-bold">{value}</div>
    </div>
  );
}

function firstNlpAction(result: Record<string, unknown>): { action_type?: string; target?: string | null } | null {
  const actions = result.actions;
  if (!Array.isArray(actions) || actions.length === 0) return null;
  const first = actions[0];
  if (!first || typeof first !== 'object') return null;
  const payload = first as { action_type?: unknown; target?: unknown };
  return {
    action_type: typeof payload.action_type === 'string' ? payload.action_type : undefined,
    target: typeof payload.target === 'string' ? payload.target : null,
  };
}

function bridgeModeLabel(mode?: string) {
  if (mode === 'mock_controller') return '模拟控制器';
  if (mode === 'service') return '服务构建';
  return 'Dry Run';
}
