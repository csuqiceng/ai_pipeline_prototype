import type { RobotSnapshot } from '../api/types';
import { connectionLabel, runningStateLabel } from '../state/robotStore';

interface TopStatusBarProps {
  snapshot: RobotSnapshot;
}

export default function TopStatusBar({ snapshot }: TopStatusBarProps) {
  const { connection, motion, position, safety } = snapshot;
  const connected = connection.controller === 'online' && connection.realtime_feedback === 'online';

  return (
    <header className="flex h-10 shrink-0 items-center justify-between overflow-hidden border-b border-border-divider bg-white px-4">
      <div className="scrollbar-hide flex items-center gap-5 overflow-x-auto whitespace-nowrap font-mono text-[10px] font-bold text-text-secondary">
        <div className={`flex items-center gap-1.5 rounded-full px-2.5 py-0.5 ${connected ? 'bg-success-light' : 'bg-danger-light'}`}>
          <div className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-primary' : 'bg-danger'}`} />
          <span className={connected ? 'text-primary' : 'text-danger'}>{connected ? 'ONLINE' : 'OFFLINE'}</span>
        </div>
        <div className="h-4 w-px bg-border-divider" />
        <div className="flex gap-3">
          <StatusChip label="急停" value={safety.estop ? '触发' : '正常'} tone={safety.estop ? 'danger' : 'success'} />
          <StatusChip label="暂停" value={safety.paused ? '已暂停' : '正常'} tone={safety.paused ? 'warning' : 'success'} />
          <StatusChip label="报警" value={safety.alarm_active ? '有' : '无'} tone={safety.alarm_active ? 'danger' : 'success'} />
          <span>通讯: {connectionLabel(connection.controller)}</span>
          <span>执行: {runningStateLabel(motion.running_state)}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4 font-mono text-[10px] font-bold">
        <div className="flex items-center gap-3 rounded-md border border-border-divider bg-surface-container-low px-3 py-1">
          <span className="text-primary-container">J1 {position.joint.j1.toFixed(1)}°</span>
          <span className="text-primary-container">J2 {position.joint.j2.toFixed(1)}°</span>
          <span className="text-primary-container">J3 {position.joint.j3.toFixed(1)}°</span>
          <span className="text-primary-container">J4 {position.joint.j4.toFixed(1)}°</span>
          <span className="text-primary-container">J5 {position.joint.j5.toFixed(1)}°</span>
          <span className="text-primary-container">J6 {position.joint.j6.toFixed(1)}°</span>
          <span className="mx-1 h-3 w-px bg-border-divider" />
          <span className="text-secondary">R {position.cartesian.r.toFixed(0)}mm</span>
          <span className="text-secondary">Z {position.cartesian.z.toFixed(0)}mm</span>
        </div>
      </div>
    </header>
  );
}

function StatusChip({ label, value, tone }: { label: string; value: string; tone: 'success' | 'warning' | 'danger' }) {
  const dotColor = tone === 'success' ? 'bg-primary' : tone === 'warning' ? 'bg-warning' : 'bg-danger';
  return (
    <span className="flex items-center gap-1">
      <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} />
      {label}: {value}
    </span>
  );
}
