import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Download, FileDown, FileJson, FileUp, Plus, RefreshCw, Save, Settings2, Trash2 } from 'lucide-react';
import {
  deleteFlow,
  deleteTemplate,
  fetchAvoidanceConfig,
  fetchFlows,
  fetchSystemConfig,
  fetchTemplates,
  saveAvoidanceConfig,
  saveFlow,
  saveSystemConfig,
  saveTemplate,
} from '../api/client';
import type { AvoidanceConfig, FlowRecord, TemplateRecord } from '../api/client';

type BackendTab = 'json' | 'system' | 'avoidance' | 'flows';

const FUNC_NUMS = [108, 106, 107, 11, 104, 109, 110, 120];

export default function TemplateManagement() {
  const [templates, setTemplates] = useState<TemplateRecord[]>([]);
  const [flows, setFlows] = useState<FlowRecord[]>([]);
  const [systemConfig, setSystemConfig] = useState<Record<string, unknown>>({});
  const [avoidanceConfig, setAvoidanceConfig] = useState<AvoidanceConfig | null>(null);
  const [selectedKey, setSelectedKey] = useState('');
  const [originalKey, setOriginalKey] = useState('');
  const [activeTab, setActiveTab] = useState<BackendTab>('json');
  const [templateForm, setTemplateForm] = useState<TemplateRecord>(blankTemplate());
  const [paramsJson, setParamsJson] = useState('{}');
  const [systemJson, setSystemJson] = useState('{}');
  const [avoidanceJson, setAvoidanceJson] = useState('{}');
  const [selectedFlowName, setSelectedFlowName] = useState('');
  const [flowForm, setFlowForm] = useState<FlowRecord>(blankFlow());
  const [flowStepsText, setFlowStepsText] = useState('');
  const [loading, setLoading] = useState(true);
  const [statusText, setStatusText] = useState('后台页面已加载。');
  const [error, setError] = useState('');

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.query_key === selectedKey) ?? templates[0],
    [selectedKey, templates],
  );

  const selectedFlow = useMemo(
    () => flows.find((flow) => flow.name === selectedFlowName) ?? flows[0],
    [flows, selectedFlowName],
  );

  const refresh = () => {
    setLoading(true);
    setError('');
    Promise.all([fetchTemplates(), fetchFlows(), fetchSystemConfig(), fetchAvoidanceConfig()])
      .then(([templatePayload, flowPayload, configPayload, avoidancePayload]) => {
        setTemplates(templatePayload.records);
        setFlows(flowPayload.flows);
        setSystemConfig(configPayload);
        setAvoidanceConfig(avoidancePayload);
        setSystemJson(JSON.stringify(configPayload, null, 2));
        setAvoidanceJson(JSON.stringify(avoidancePayload, null, 2));
        const nextKey = selectedKey || templatePayload.records[0]?.query_key || '';
        const nextFlow = selectedFlowName || flowPayload.flows[0]?.name || '';
        setSelectedKey(nextKey);
        setSelectedFlowName(nextFlow);
        setStatusText('配置数据已刷新。');
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (!selectedTemplate) return;
    setTemplateForm(structuredClone(selectedTemplate));
    setOriginalKey(selectedTemplate.query_key);
    setParamsJson(JSON.stringify(selectedTemplate.params ?? {}, null, 2));
  }, [selectedTemplate]);

  useEffect(() => {
    if (!selectedFlow) return;
    setFlowForm(structuredClone(selectedFlow));
    setFlowStepsText(selectedFlow.steps.join('\n'));
  }, [selectedFlow]);

  const saveCurrentTemplate = () => {
    try {
      const record = {
        ...templateForm,
        query_key: templateForm.query_key.trim(),
        func_num: Number(templateForm.func_num),
        safety_level: Number(templateForm.safety_level),
        params: JSON.parse(paramsJson),
      };
      saveTemplate(originalKey || record.query_key, record)
        .then((payload) => {
          setTemplates(payload.records);
          setSelectedKey(record.query_key);
          setOriginalKey(record.query_key);
          setStatusText(`已保存模板：${record.query_key}`);
        })
        .catch((err) => setError(err.message));
    } catch (err) {
      setError(`参数 JSON 格式错误：${String(err)}`);
    }
  };

  const createTemplate = () => {
    const next = blankTemplate(`新模板${Date.now().toString().slice(-4)}`);
    setTemplateForm(next);
    setParamsJson(JSON.stringify(next.params, null, 2));
    setSelectedKey('');
    setOriginalKey(next.query_key);
    setStatusText('已创建空白模板，编辑后点击保存模板。');
  };

  const removeTemplate = () => {
    const key = originalKey || templateForm.query_key;
    if (!key) return;
    deleteTemplate(key)
      .then((payload) => {
        setTemplates(payload.records);
        setSelectedKey(payload.records[0]?.query_key || '');
        setStatusText(`已删除模板：${key}`);
      })
      .catch((err) => setError(err.message));
  };

  const saveCurrentSystemConfig = () => {
    try {
      const parsed = JSON.parse(systemJson) as Record<string, unknown>;
      saveSystemConfig(parsed)
        .then((payload) => {
          setSystemConfig(payload);
          setSystemJson(JSON.stringify(payload, null, 2));
          setStatusText('已保存系统参数。');
        })
        .catch((err) => setError(err.message));
    } catch (err) {
      setError(`系统参数 JSON 格式错误：${String(err)}`);
    }
  };

  const saveCurrentAvoidanceConfig = () => {
    try {
      const parsed = JSON.parse(avoidanceJson) as AvoidanceConfig;
      saveAvoidanceConfig(parsed)
        .then((payload) => {
          setAvoidanceConfig(payload);
          setAvoidanceJson(JSON.stringify(payload, null, 2));
          setStatusText('已保存安全中间点配置。');
        })
        .catch((err) => setError(err.message));
    } catch (err) {
      setError(`安全中间点 JSON 格式错误：${String(err)}`);
    }
  };

  const saveCurrentFlow = () => {
    const nextFlow = {
      ...flowForm,
      name: flowForm.name.trim(),
      step_delay_ms: Number(flowForm.step_delay_ms),
      steps: flowStepsText
        .split('\n')
        .map((step) => step.trim())
        .filter(Boolean),
    };
    saveFlow(selectedFlowName || nextFlow.name, nextFlow)
      .then((payload) => {
        setFlows(payload.flows);
        setSelectedFlowName(nextFlow.name);
        setStatusText(`已保存流程：${nextFlow.name}`);
      })
      .catch((err) => setError(err.message));
  };

  const createFlow = () => {
    const next = blankFlow(`新流程${Date.now().toString().slice(-4)}`);
    setFlowForm(next);
    setFlowStepsText(next.steps.join('\n'));
    setSelectedFlowName('');
    setActiveTab('flows');
    setStatusText('已创建空白流程，编辑后点击保存流程。');
  };

  const removeFlow = () => {
    const name = selectedFlowName || flowForm.name;
    if (!name) return;
    deleteFlow(name)
      .then((payload) => {
        setFlows(payload.flows);
        setSelectedFlowName(payload.flows[0]?.name || '');
        setStatusText(`已删除流程：${name}`);
      })
      .catch((err) => setError(err.message));
  };

  return (
    <div className="flex h-full overflow-hidden bg-[#f0f4f8]">
      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-hidden p-6">
        <section className="grid grid-cols-12 gap-4">
          <div className="industrial-card col-span-7 p-4">
            <div className="mb-3 flex items-center gap-2">
              <FileJson size={18} className="text-primary-container" />
              <h2 className="text-base font-bold text-on-surface">当前选中模板</h2>
            </div>
            <div className="grid grid-cols-4 gap-3 text-xs">
              <InfoCell label="名称" value={templateForm.query_key || '-'} />
              <InfoCell label="函数号" value={`Func${templateForm.func_num || '-'}`} />
              <InfoCell label="安全等级" value={String(templateForm.safety_level || '-')} />
              <InfoCell label="状态" value={loading ? '加载中' : statusText} />
            </div>
          </div>

          <div className="industrial-card col-span-5 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-bold text-on-surface">后台操作</h2>
              <button onClick={refresh} className="flex h-8 items-center gap-2 rounded border border-border-divider bg-white px-3 text-xs font-bold hover:bg-surface">
                <RefreshCw size={14} /> 刷新
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <ActionButton icon={<Plus size={14} />} label="新增模板" onClick={createTemplate} />
              <ActionButton icon={<Save size={14} />} label="保存模板" onClick={saveCurrentTemplate} />
              <ActionButton icon={<Trash2 size={14} />} label="删除模板" danger onClick={removeTemplate} />
              <ActionButton icon={<FileUp size={14} />} label="新增流程" onClick={createFlow} />
              <ActionButton icon={<FileDown size={14} />} label="保存流程" onClick={saveCurrentFlow} />
              <ActionButton icon={<Download size={14} />} label="删除流程" danger onClick={removeFlow} />
            </div>
          </div>
        </section>

        {error && (
          <button onClick={() => setError('')} className="rounded border border-danger/20 bg-danger/5 p-3 text-left text-xs font-bold text-danger">
            {error}
          </button>
        )}

        <section className="grid min-h-0 flex-1 grid-cols-12 gap-4">
          <div className="industrial-card col-span-3 flex min-h-0 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-border-divider px-4 py-3">
              <h3 className="text-sm font-bold">指令模板列表</h3>
              <span className="text-[10px] font-bold text-text-secondary">{templates.length}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {templates.map((item) => (
                <button
                  key={item.query_key}
                  onClick={() => setSelectedKey(item.query_key)}
                  className={`mb-1 flex w-full items-center justify-between rounded border px-3 py-2 text-left text-xs transition-all ${
                    selectedKey === item.query_key
                      ? 'border-primary-container/30 bg-primary-container/10 text-primary-container'
                      : 'border-transparent hover:border-primary-container/20 hover:bg-primary-container/5'
                  }`}
                >
                  <span className="truncate font-bold">{item.query_key}</span>
                  <span className="ml-2 shrink-0 opacity-60">F{item.func_num}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="industrial-card col-span-4 min-h-0 overflow-y-auto p-4">
            <div className="mb-4 flex items-center gap-2">
              <Settings2 size={18} className="text-secondary" />
              <h3 className="text-sm font-bold">模板编辑</h3>
            </div>
            <TemplateEditor form={templateForm} paramsJson={paramsJson} onFormChange={setTemplateForm} onParamsJsonChange={setParamsJson} />
          </div>

          <div className="industrial-card col-span-5 flex min-h-0 flex-col overflow-hidden">
            <div className="flex border-b border-border-divider bg-white px-4 pt-3">
              <TabButton active={activeTab === 'json'} label="JSON预览" onClick={() => setActiveTab('json')} />
              <TabButton active={activeTab === 'system'} label="系统参数" onClick={() => setActiveTab('system')} />
              <TabButton active={activeTab === 'avoidance'} label="安全中间点" onClick={() => setActiveTab('avoidance')} />
              <TabButton active={activeTab === 'flows'} label="流程管理" onClick={() => setActiveTab('flows')} />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {activeTab === 'json' && <JsonPreview template={templateForm} paramsJson={paramsJson} />}
              {activeTab === 'system' && <JsonEditor title="系统参数" value={systemJson} onChange={setSystemJson} onSave={saveCurrentSystemConfig} />}
              {activeTab === 'avoidance' && (
                <JsonEditor
                  title={`安全中间点 (${Object.keys(avoidanceConfig?.safe_points ?? {}).length})`}
                  value={avoidanceJson}
                  onChange={setAvoidanceJson}
                  onSave={saveCurrentAvoidanceConfig}
                />
              )}
              {activeTab === 'flows' && (
                <FlowsPanel
                  flows={flows}
                  selectedName={selectedFlowName}
                  form={flowForm}
                  stepsText={flowStepsText}
                  onSelect={setSelectedFlowName}
                  onFormChange={setFlowForm}
                  onStepsTextChange={setFlowStepsText}
                  onSave={saveCurrentFlow}
                />
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function TemplateEditor({
  form,
  paramsJson,
  onFormChange,
  onParamsJsonChange,
}: {
  form: TemplateRecord;
  paramsJson: string;
  onFormChange: (form: TemplateRecord) => void;
  onParamsJsonChange: (value: string) => void;
}) {
  const patch = (patchValue: Partial<TemplateRecord>) => onFormChange({ ...form, ...patchValue });

  return (
    <div className="space-y-3">
      <EditableInput label="名称" value={form.query_key} onChange={(value) => patch({ query_key: value })} />
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold text-text-secondary">函数号</span>
          <select
            value={form.func_num}
            onChange={(event) => patch({ func_num: Number(event.target.value), function_name: `FUNC${event.target.value}` })}
            className="h-9 w-full rounded border border-border-divider bg-white px-3 text-xs outline-none"
          >
            {FUNC_NUMS.map((funcNum) => (
              <option key={funcNum} value={funcNum}>
                Func{funcNum}
              </option>
            ))}
          </select>
        </label>
        <EditableInput label="安全等级" value={String(form.safety_level)} onChange={(value) => patch({ safety_level: Number(value) || 5 })} />
      </div>
      <EditableInput label="函数名" value={form.function_name || ''} onChange={(value) => patch({ function_name: value })} />
      <EditableTextarea label="关键词" value={form.keywords || ''} onChange={(value) => patch({ keywords: value })} rows={2} />
      <EditableTextarea label="描述" value={form.description || ''} onChange={(value) => patch({ description: value })} rows={3} />
      <EditableTextarea label="参数 JSON" value={paramsJson} onChange={onParamsJsonChange} rows={12} mono />
    </div>
  );
}

function JsonPreview({ template, paramsJson }: { template: TemplateRecord; paramsJson: string }) {
  let params: unknown = {};
  try {
    params = JSON.parse(paramsJson);
  } catch {
    params = { error: 'params_json_invalid' };
  }
  return (
    <pre className="min-h-full overflow-auto rounded border border-border-divider bg-[#0f1720] p-4 text-xs leading-relaxed text-[#d5e7ef]">
      {JSON.stringify({ ...template, params }, null, 2)}
    </pre>
  );
}

function JsonEditor({ title, value, onChange, onSave }: { title: string; value: string; onChange: (value: string) => void; onSave: () => void }) {
  return (
    <div className="flex h-full min-h-[460px] flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold">{title}</h3>
        <button onClick={onSave} className="flex h-9 items-center gap-2 rounded border border-border-divider bg-white px-3 text-xs font-bold hover:bg-surface">
          <Save size={14} /> 保存
        </button>
      </div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-0 flex-1 resize-none rounded border border-border-divider bg-[#0f1720] p-4 font-mono text-xs leading-relaxed text-[#d5e7ef] outline-none"
      />
    </div>
  );
}

function FlowsPanel({
  flows,
  selectedName,
  form,
  stepsText,
  onSelect,
  onFormChange,
  onStepsTextChange,
  onSave,
}: {
  flows: FlowRecord[];
  selectedName: string;
  form: FlowRecord;
  stepsText: string;
  onSelect: (name: string) => void;
  onFormChange: (flow: FlowRecord) => void;
  onStepsTextChange: (value: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="grid min-h-[460px] grid-cols-12 gap-3">
      <div className="col-span-4 overflow-y-auto rounded border border-border-divider bg-white p-2">
        {flows.map((flow) => (
          <button
            key={flow.name}
            onClick={() => onSelect(flow.name)}
            className={`mb-1 w-full rounded px-3 py-2 text-left text-xs font-bold ${
              selectedName === flow.name ? 'bg-primary-container/10 text-primary-container' : 'hover:bg-surface'
            }`}
          >
            {flow.name}
          </button>
        ))}
      </div>
      <div className="col-span-8 flex flex-col gap-3">
        <EditableInput label="流程名称" value={form.name} onChange={(value) => onFormChange({ ...form, name: value })} />
        <EditableInput
          label="步骤间隔 ms"
          value={String(form.step_delay_ms)}
          onChange={(value) => onFormChange({ ...form, step_delay_ms: Number(value) || 0 })}
        />
        <EditableTextarea label="流程步骤（一行一个模板名称）" value={stepsText} onChange={onStepsTextChange} rows={12} />
        <button onClick={onSave} className="flex h-9 items-center justify-center gap-2 rounded border border-border-divider bg-white px-3 text-xs font-bold hover:bg-surface">
          <Save size={14} /> 保存流程
        </button>
      </div>
    </div>
  );
}

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border-divider bg-white px-3 py-2 text-xs">
      <div className="mb-1 truncate font-mono text-[10px] font-bold text-text-secondary">{label}</div>
      <div className="truncate font-bold text-on-surface">{value}</div>
    </div>
  );
}

function EditableInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold text-text-secondary">{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} className="h-9 w-full rounded border border-border-divider bg-white px-3 text-xs outline-none" />
    </label>
  );
}

function EditableTextarea({
  label,
  value,
  onChange,
  rows,
  mono = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows: number;
  mono?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold text-text-secondary">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        className={`w-full resize-y rounded border border-border-divider bg-white p-3 text-xs outline-none ${mono ? 'font-mono' : ''}`}
      />
    </label>
  );
}

function ActionButton({ icon, label, onClick, danger = false }: { icon: ReactNode; label: string; onClick: () => void; danger?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`flex h-9 items-center justify-center gap-2 rounded border px-2 text-xs font-bold ${
        danger ? 'border-danger/30 bg-danger/5 text-danger hover:bg-danger/10' : 'border-border-divider bg-white text-on-surface hover:bg-surface'
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
      className={`mr-1 border-b-2 px-3 py-3 text-[11px] font-bold ${
        active ? 'border-primary-container text-primary-container' : 'border-transparent text-text-secondary hover:text-on-surface'
      }`}
    >
      {label}
    </button>
  );
}

function blankTemplate(queryKey = ''): TemplateRecord {
  return {
    query_key: queryKey,
    func_num: 108,
    function_name: 'FUNC108_LINEAR_MOVE',
    keywords: '',
    description: '',
    safety_level: 5,
    params: {
      target_x: 0,
      target_y: 0,
      target_z: 0,
      target_rx: 0,
      target_ry: 0,
      target_rz: 0,
      spd_pct: 50,
      acc_pct: 60,
      dec_pct: 60,
      stop_cmd: 0,
      fuzzy_pos: 0,
      fuzzy_spd: 0,
      fuzzy_acc: 0,
      fuzzy_dec: 0,
      move_type: 0,
    },
    summary: '',
  };
}

function blankFlow(name = ''): FlowRecord {
  return {
    name,
    steps: [],
    step_delay_ms: 1000,
  };
}
