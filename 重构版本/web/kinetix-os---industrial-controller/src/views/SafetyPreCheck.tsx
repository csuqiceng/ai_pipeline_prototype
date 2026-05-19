import { Activity, CheckCircle2, Clock3, ShieldCheck } from 'lucide-react';
import RightStatusPanel from '../components/RightStatusPanel';
import { useRobotStore } from '../state/robotStore';

export default function SafetyPreCheck() {
  const { state, snapshot } = useRobotStore();
  const precheck = state.precheck;

  return (
    <div className="flex h-full overflow-hidden bg-[#f0f4f8]">
      <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-8">
        <div>
          <h2 className="text-xl font-bold text-on-surface">安全预检</h2>
          <p className="mt-1 text-xs text-text-secondary">运动指令执行前的 L1/L2/L3 分层检查入口。</p>
        </div>

        <section className="industrial-card p-6">
          <div className="mb-6 flex items-center gap-3">
            <ShieldCheck className="text-primary" />
            <h3 className="font-bold">状态检查</h3>
          </div>

          <div className="space-y-4">
            {precheck?.items.map((item) => (
              <CheckItem key={item.id} label={item.label} message={item.message} status={item.status} />
            )) ?? <p className="text-sm text-text-secondary">暂无预检结果。请先从智能对话页提交动作指令。</p>}
          </div>
        </section>

        <section className="industrial-card p-6">
          <div className="mb-4 flex items-center gap-3">
            <Activity className="text-secondary" />
            <h3 className="font-bold">参数检查</h3>
          </div>
          {state.active_plan ? (
            <div className="space-y-2 text-sm text-on-surface-variant">
              <p className="font-bold text-on-surface">{state.active_plan.summary}</p>
              <p>{precheck?.suggestion ?? 'mock 目标点位于安全范围内，可返回对话页确认执行。'}</p>
            </div>
          ) : (
            <p className="text-sm text-on-surface-variant">
              当前处于 mock 模式。接入后端后，此处展示目标点范围、逆解状态、路线预演和建议中间点。
            </p>
          )}
        </section>
      </div>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}

function CheckItem({
  label,
  message,
  status,
}: {
  label: string;
  message: string;
  status: 'pass' | 'warning' | 'fail' | 'pending';
}) {
  const passed = status === 'pass';
  const failed = status === 'fail';
  const bgColor = passed ? 'bg-success-light' : failed ? 'bg-danger-light' : 'bg-warning-light';

  return (
    <div className={`flex items-center justify-between gap-6 rounded-lg border border-border-divider p-3 ${bgColor}`}>
      <div>
        <span className="text-sm font-medium">{label}</span>
        <p className="mt-1 text-xs text-text-secondary">{message}</p>
      </div>
      <span className={`flex shrink-0 items-center gap-1 text-xs font-bold ${passed ? 'text-primary' : failed ? 'text-danger' : 'text-warning'}`}>
        {passed ? <CheckCircle2 size={14} /> : <Clock3 size={14} />}
        {passed ? '通过' : failed ? '失败' : '待定'}
      </span>
    </div>
  );
}
