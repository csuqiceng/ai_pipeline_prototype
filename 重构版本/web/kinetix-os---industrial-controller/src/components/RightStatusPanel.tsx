import type { ReactNode } from 'react';
import { Activity, Cpu, History, ShieldCheck, Wifi } from 'lucide-react';
import type { ConversationEvent, ExecutionState, RobotSnapshot } from '../api/types';
import { connectionLabel, connectionTone, runningStateLabel } from '../state/robotStore';
import StatusRow from './StatusRow';

interface RightStatusPanelProps {
  snapshot: RobotSnapshot;
  execution?: ExecutionState;
  recentEvents?: ConversationEvent[];
}

export default function RightStatusPanel({ snapshot, execution, recentEvents = [] }: RightStatusPanelProps) {
  const { safety, motion, position, connection } = snapshot;

  return (
    <aside className="w-64 shrink-0 overflow-y-auto border-l border-border-divider bg-white pb-10">
      <StatusSection icon={Activity} title="执行状态">
        {execution?.task_id ? (
          <div className="space-y-2">
            <StatusRow label="任务ID" value={execution.task_id} />
            <StatusRow label="进度" value={`${execution.progress}%`} />
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-container-high">
              <div className={`h-full rounded-full transition-all duration-500 ${execution.progress >= 100 ? 'bg-primary' : 'bg-secondary'}`} style={{ width: `${execution.progress}%` }} />
            </div>
            <StatusRow label="阶段" value={execution.current_stage ?? '-'} />
          </div>
        ) : (
          <p className="text-[10px] text-status-disabled">当前无活动任务</p>
        )}
      </StatusSection>

      <StatusSection icon={ShieldCheck} title="安全状态">
        <div className="space-y-2">
          <StatusRow label="系统状态" value={runningStateLabel(motion.running_state)} />
          <StatusRow label="急停按钮" value={safety.estop ? '触发' : '正常'} tone={safety.estop ? 'error' : 'success'} />
          <StatusRow label="暂停状态" value={safety.paused ? '已暂停' : '正常'} tone={safety.paused ? 'warning' : 'success'} />
          <StatusRow label="报警信息" value={safety.alarm_active ? '有报警' : '无'} tone={safety.alarm_active ? 'error' : 'success'} />
        </div>
      </StatusSection>

      <StatusSection icon={Cpu} title="运动参数">
        <div className="space-y-2">
          <StatusRow label="当前速度" value={`${motion.speed_percent}%`} />
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-container-high">
            <div className="h-full rounded-full bg-secondary transition-all duration-500" style={{ width: `${motion.speed_percent}%` }} />
          </div>
          <StatusRow label="R半径" value={`${position.cartesian.r.toFixed(1)} mm`} />
          <StatusRow label="Z高度" value={`${position.cartesian.z.toFixed(1)} mm`} />
          <StatusRow label="任务ID" value={motion.task_id ?? '-'} />
        </div>
      </StatusSection>

      <StatusSection icon={Wifi} title="通讯状态">
        <div className="space-y-2">
          <StatusRow label="控制器" value={connectionLabel(connection.controller)} tone={connectionTone(connection.controller)} />
          <StatusRow label="Modbus TCP" value={connectionLabel(connection.modbus_tcp)} tone={connectionTone(connection.modbus_tcp)} />
          <StatusRow label="EtherCAT" value={connectionLabel(connection.ethercat)} tone={connectionTone(connection.ethercat)} />
          <StatusRow label="实时反馈" value={connectionLabel(connection.realtime_feedback)} tone={connectionTone(connection.realtime_feedback)} />
        </div>
      </StatusSection>

      <StatusSection icon={History} title="实时信息">
        {recentEvents.length > 0 ? (
          <div className="space-y-3">
            {recentEvents.slice(0, 3).map((event) => (
              <div key={event.id} className="text-[10px] leading-relaxed">
                <div className="mb-1 font-mono text-text-secondary">{event.timestamp}</div>
                <p className="line-clamp-3 text-on-surface">{event.text}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-status-disabled">暂无实时信息</p>
        )}
      </StatusSection>
    </aside>
  );
}

function StatusSection({ icon: Icon, title, children }: { icon: any; title: string; children: ReactNode }) {
  return (
    <section className="border-b border-border-divider p-4 last:border-b-0">
      <div className="mb-3 flex items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded bg-primary-container/10">
          <Icon size={12} className="text-primary-container" />
        </div>
        <h3 className="text-[11px] font-bold uppercase tracking-wider text-text-secondary">{title}</h3>
      </div>
      {children}
    </section>
  );
}
