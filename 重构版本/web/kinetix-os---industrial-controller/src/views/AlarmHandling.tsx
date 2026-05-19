import { Activity, ShieldCheck } from 'lucide-react';
import RightStatusPanel from '../components/RightStatusPanel';
import { useRobotStore } from '../state/robotStore';

export default function AlarmHandling() {
  const { state, snapshot } = useRobotStore();
  const hasAlarm = state.active_alarms.length > 0;

  return (
    <div className="flex h-full bg-[#f0f4f8] overflow-hidden">
      <div className="flex-1 p-8 flex flex-col gap-6 overflow-y-auto">
        <h2 className="text-xl font-bold text-on-surface">报警处理中心</h2>
        
        <div className={`${hasAlarm ? 'bg-danger/10 border-danger/20 text-danger' : 'bg-success-light border-primary/20 text-primary'} border p-6 rounded-lg flex items-center gap-4`}>
           <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${hasAlarm ? 'bg-danger/10' : 'bg-primary/10'}`}>
             <ShieldCheck size={24} />
           </div>
           <div>
              <h3 className="font-bold text-lg">{hasAlarm ? '检测到活动报警' : '系统状态正常'}</h3>
              <p className="text-sm opacity-80">{hasAlarm ? '请确认报警详情并按流程处理。' : '未检测到活动的报警或异常警告。'}</p>
           </div>
        </div>

        <section className={`industrial-card p-6 ${hasAlarm ? '' : 'opacity-40'}`}>
           <div className="flex items-center gap-3 mb-6">
              <Activity className="text-secondary" />
              <h3 className="font-bold">活动报警</h3>
           </div>
           {hasAlarm ? (
             <div className="space-y-3">
               {state.active_alarms.map((alarm) => (
                 <div key={alarm.id} className="rounded border border-danger/20 bg-danger/5 p-4">
                   <div className="mb-1 text-sm font-bold text-danger">{alarm.title}</div>
                   <p className="text-xs leading-relaxed text-on-surface-variant">{alarm.message}</p>
                 </div>
               ))}
             </div>
           ) : (
             <div className="flex flex-col items-center justify-center py-20 text-status-disabled italic">
                当前无报警信息
             </div>
           )}
        </section>
      </div>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}
