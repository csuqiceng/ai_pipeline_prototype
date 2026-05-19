import { useState } from 'react';
import { 
  CheckCircle2,
  ClipboardCheck,
  RotateCw, 
  Send,
  Pause,
  AlertOctagon,
  Mic,
  XCircle,
} from 'lucide-react';
import type { ConversationEvent } from '../api/types';
import { fetchVoiceStatus, startVoice, stopVoice } from '../api/client';
import RightStatusPanel from '../components/RightStatusPanel';
import { useRobotStore } from '../state/robotStore';

export default function OperatorDashboard() {
  const [command, setCommand] = useState('');
  const [voiceRunning, setVoiceRunning] = useState(false);
  const [voiceHint, setVoiceHint] = useState('语音输入');
  const {
    state,
    snapshot,
    submitCommand,
    adoptActivePlan,
    cancelActivePlan,
    pauseExecution,
    triggerEmergencyStop,
    resetMock,
  } = useRobotStore();

  const handleSubmit = () => {
    const text = command.trim();
    if (!text) return;
    submitCommand(text);
    setCommand('');
  };

  const toggleVoice = () => {
    const request = voiceRunning ? stopVoice() : startVoice();
    request
      .then((status) => {
        setVoiceRunning(status.running);
        if (status.last_text) {
          submitCommand(status.last_text);
          setVoiceHint('语音输入');
          return;
        }
        if (status.running && status.phase === 'recognizing') {
          setVoiceHint('识别中');
          pollVoiceResult();
          return;
        }
        setVoiceHint(status.running ? '语音采集中' : status.last_error ? '语音失败' : '语音输入');
      })
      .catch((error) => setVoiceHint(`语音异常：${error.message}`));
  };

  const pollVoiceResult = (attempt = 0) => {
    window.setTimeout(() => {
      fetchVoiceStatus()
        .then((status) => {
          setVoiceRunning(status.running);
          if (status.last_text) {
            submitCommand(status.last_text);
            setVoiceHint('语音输入');
            return;
          }
          if (status.running && attempt < 60) {
            pollVoiceResult(attempt + 1);
            return;
          }
          setVoiceHint(status.last_error ? '语音失败' : '语音输入');
        })
        .catch((error) => setVoiceHint(`语音异常：${error.message}`));
    }, 1000);
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* Main Conversation Area */}
      <section className="flex-1 flex flex-col min-w-0 bg-[#f0f4f8]">
        {/* Chat Messages */}
        <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-6">
          <div className="text-center py-10">
            <h2 className="text-xl font-bold text-primary-container mb-2">机械臂自然语言交互系统</h2>
            <p className="text-xs text-text-secondary">通过文本或语音与机器人交互</p>
            <div className="flex flex-wrap justify-center gap-2 mt-6">
              {['移动到位置 A', 'J1 旋转 30 度', '返回原点', '运行流程 1'].map((chip) => (
                <button
                  key={chip}
                  onClick={() => {
                    submitCommand(chip);
                    setCommand('');
                  }}
                  className="rounded-full border border-border-divider bg-white px-4 py-1.5 text-xs font-medium text-on-surface-variant transition-all hover:border-primary-container/40 hover:bg-primary-container/5 hover:text-primary-container"
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
          
          {state.recent_events.map((event) => (
            <DialogueMessage key={event.id} event={event} />
          ))}

          {state.active_plan && (
            <PlanConfirmationCard
              summary={state.active_plan.summary}
              precheckStatus={state.precheck?.status ?? 'pending'}
              canConfirm={state.active_plan.status === 'waiting_confirm' && !snapshot.safety.estop}
              onConfirm={adoptActivePlan}
              onCancel={cancelActivePlan}
            />
          )}
        </div>

        {/* Bottom Input Area matching screenshot 1 */}
        <div className="p-4 bg-white border-t border-border-divider flex flex-col gap-3 shrink-0">
          <textarea 
            className="w-full bg-transparent border-none focus:ring-0 text-sm py-2 resize-none h-20 scrollbar-hide border border-transparent focus:border-border-divider rounded"
            placeholder="输入指令或提问..."
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
          <div className="flex gap-2 h-10">
            <button
              onClick={toggleVoice}
              className="flex-[2] bg-surface-container-high text-on-surface-variant rounded flex items-center justify-center gap-2 font-bold text-xs hover:bg-surface-container-highest transition-all"
            >
              <Mic size={16} /> {voiceHint}
            </button>
            <button
              onClick={handleSubmit}
              disabled={!command.trim()}
              className="flex-[5] bg-primary-container text-white rounded flex items-center justify-center gap-2 font-bold text-xs hover:brightness-110 active:scale-[0.99] transition-all disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send size={16} /> 发送
            </button>
            <button
              onClick={pauseExecution}
              className="w-10 bg-warning/10 text-warning border border-warning/20 rounded flex items-center justify-center hover:bg-warning/20"
            >
              <Pause size={18} />
            </button>
            <button
              onClick={resetMock}
              className="w-10 bg-primary/10 text-primary border border-primary/20 rounded flex items-center justify-center hover:bg-primary/20"
            >
              <RotateCw size={18} />
            </button>
            <button
              onClick={triggerEmergencyStop}
              className="px-4 bg-danger text-white rounded flex items-center justify-center gap-2 font-bold text-xs hover:brightness-110"
            >
              <AlertOctagon size={16} /> 急停
            </button>
          </div>
        </div>
      </section>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}

function PlanConfirmationCard({
  summary,
  precheckStatus,
  canConfirm,
  onConfirm,
  onCancel,
}: {
  summary: string;
  precheckStatus: string;
  canConfirm: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const precheckPassed = precheckStatus === 'pass';

  return (
    <div className="max-w-[85%] self-start rounded-lg border border-primary/20 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-xs font-bold text-primary-container">
        <ClipboardCheck size={16} />
        动作计划确认
      </div>
      <p className="text-sm leading-relaxed text-on-surface">{summary}</p>
      <div className="mt-3 flex items-center gap-2 text-[11px] font-bold">
        <CheckCircle2 size={13} className={precheckPassed ? 'text-primary' : 'text-warning'} />
        <span className={precheckPassed ? 'text-primary' : 'text-warning'}>
          预检状态：{precheckPassed ? '通过' : '待确认'}
        </span>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          onClick={onConfirm}
          disabled={!canConfirm}
          className="flex items-center gap-2 rounded bg-primary-container px-4 py-2 text-xs font-bold text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CheckCircle2 size={14} /> 确认执行
        </button>
        <button
          onClick={onCancel}
          className="flex items-center gap-2 rounded border border-border-divider bg-white px-4 py-2 text-xs font-bold text-on-surface hover:bg-surface"
        >
          <XCircle size={14} /> 取消计划
        </button>
      </div>
    </div>
  );
}

function DialogueMessage({ event }: { event: ConversationEvent }) {
  const isSystem = event.role !== 'user';
  const author = event.role === 'user' ? '操作员 (Unit 4)' : '系统操作员';

  return (
    <div className={`
      relative rounded-lg max-w-[85%] border
      ${isSystem
        ? 'bg-white border-l-4 border-l-primary-container self-start p-4'
        : 'bg-surface-container-low border-r-4 border-r-secondary self-end p-4 text-right'}
    `}>
      <div className={`flex items-center gap-2 mb-1.5 text-[10px] font-bold uppercase tracking-wider ${isSystem ? 'text-primary-container' : 'text-secondary'}`}>
        <span>{author}</span>
        <span className="ml-auto opacity-50 font-mono">{event.timestamp}</span>
      </div>
      <p className="text-sm leading-relaxed text-on-surface">{event.text}</p>
    </div>
  );
}
