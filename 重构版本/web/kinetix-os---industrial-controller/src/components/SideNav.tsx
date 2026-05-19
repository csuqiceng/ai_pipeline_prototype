import {
  AlertTriangle,
  Cpu,
  Database,
  LayoutGrid,
  MessageSquare,
  Monitor,
  PlayCircle,
  Power,
  ShieldCheck,
  Terminal,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { UserRole, ViewState } from '../types';

interface SideNavProps {
  role: UserRole;
  view: ViewState;
  onViewChange: (view: ViewState) => void;
  onLogout: () => void;
}

export default function SideNav({ role, view, onViewChange, onLogout }: SideNavProps) {
  return (
    <nav className="z-50 flex w-[128px] shrink-0 flex-col items-center gap-2 border-r border-border-divider bg-white py-6">
      <div className="mb-6 px-2 text-center">
        <div className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-primary-container/10">
          <Cpu size={18} className="text-primary-container" />
        </div>
        <h1 className="text-sm font-black leading-none tracking-normal text-primary-container">KINETIX</h1>
        <p className="mt-1.5 text-[9px] font-bold text-status-disabled">{role === 'ENGINEER' ? '工程师模式' : '操作员模式'}</p>
      </div>

      <div className="flex w-full flex-col gap-1 px-2">
        {role === 'OPERATOR' && (
          <>
            <NavButton icon={MessageSquare} label="智能对话" active={view === 'CHAT'} onClick={() => onViewChange('CHAT')} />
            <NavButton icon={ShieldCheck} label="安全预检" active={view === 'SAFETY'} onClick={() => onViewChange('SAFETY')} />
            <NavButton icon={Monitor} label="执行监控" active={view === 'MONITOR'} onClick={() => onViewChange('MONITOR')} />
            <NavButton icon={AlertTriangle} label="报警处理" active={view === 'ALARMS'} onClick={() => onViewChange('ALARMS')} />
            <NavButton icon={LayoutGrid} label="完整状态" active={view === 'STATUS'} onClick={() => onViewChange('STATUS')} />
          </>
        )}

        {role === 'ENGINEER' && (
          <>
            <NavButton icon={PlayCircle} label="运行" active={view === 'RUNNING'} onClick={() => onViewChange('RUNNING')} />
            <NavButton icon={Database} label="后台管理" active={view === 'BACKEND'} onClick={() => onViewChange('BACKEND')} />
            <NavButton icon={Terminal} label="日志" active={view === 'LOGS'} onClick={() => onViewChange('LOGS')} />
          </>
        )}
      </div>

      <div className="mt-auto flex w-full flex-col items-center gap-3 border-t border-border-divider pt-4">
        <span className="font-mono text-[8px] text-status-disabled">V3.4.1-STABLE</span>
        <button
          onClick={onLogout}
          className="flex flex-col items-center gap-1 p-2 text-[9px] font-bold text-status-disabled transition-colors hover:text-danger"
        >
          <Power size={20} />
          <span>安全退出</span>
        </button>
      </div>
    </nav>
  );
}

interface NavButtonProps {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}

function NavButton({ icon: Icon, label, active, onClick }: NavButtonProps) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-col items-center justify-center rounded-lg p-2 transition-all duration-200 ${
        active
          ? 'bg-primary-container font-bold text-white shadow-md ring-1 ring-primary/20'
          : 'text-text-secondary hover:bg-surface-container-high hover:text-on-surface'
      }`}
    >
      <Icon size={24} strokeWidth={active ? 3 : 2} />
      <span className="mt-1.5 text-center text-[10px] font-bold leading-tight">{label}</span>
    </button>
  );
}
