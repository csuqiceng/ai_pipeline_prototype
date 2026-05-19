import { 
  Activity, 
  ShieldCheck, 
  Cpu, 
  Wifi, 
} from 'lucide-react';
import RightStatusPanel from '../components/RightStatusPanel';
import { connectionLabel, connectionTone, runningStateLabel, useRobotStore } from '../state/robotStore';

export default function SystemStatus() {
  const { state, snapshot } = useRobotStore();
  const { connection, motion, position, safety } = snapshot;

  return (
    <div className="flex h-full bg-[#f0f4f8] overflow-hidden">
      <div className="flex-1 p-8 flex flex-col gap-6 overflow-y-auto">
        <h2 className="text-xl font-bold text-on-surface">完整系统状态</h2>
        
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {/* Device Basic Status */}
          <StatusCard title="设备基础状态" icon={Cpu}>
            <DataRow label="系统状态" value={runningStateLabel(motion.running_state)} />
            <DataRow label="当前功能" value={snapshot.current_function ?? '-'} />
            <DataRow label="急停状态" value={safety.estop ? '触发' : '正常'} status={safety.estop ? 'error' : 'success'} />
            <DataRow label="暂停状态" value={safety.paused ? '已暂停' : '未暂停'} status={safety.paused ? 'warning' : 'success'} />
          </StatusCard>

          {/* Safety Boundary */}
          <StatusCard title="安全边界" icon={ShieldCheck}>
            <DataRow label="R范围" value={`${safety.safe_range.r_min} ~ ${safety.safe_range.r_max} mm`} />
            <DataRow label="Z范围" value={`${safety.safe_range.z_min} ~ ${safety.safe_range.z_max} mm`} />
            <DataRow label="当前R" value={`${position.cartesian.r.toFixed(1)} mm`} />
            <DataRow label="当前Z" value={`${position.cartesian.z.toFixed(1)} mm`} />
          </StatusCard>

          {/* Motion Limits */}
          <StatusCard title="运动限制" icon={Activity}>
            <DataRow label="最大速度" value="80%" />
            <DataRow label="最大加速度" value={`${motion.acceleration_percent}%`} />
            <DataRow label="当前速度" value={`${motion.speed_percent}%`} />
          </StatusCard>

          {/* Comm Diagnosis */}
          <StatusCard title="通讯诊断" icon={Wifi}>
            <DataRow label="Modbus TCP" value={connectionLabel(connection.modbus_tcp)} status={connectionTone(connection.modbus_tcp)} />
            <DataRow label="EtherCAT" value={connectionLabel(connection.ethercat)} status={connectionTone(connection.ethercat)} />
            <DataRow label="实时反馈" value={connectionLabel(connection.realtime_feedback)} status={connectionTone(connection.realtime_feedback)} />
          </StatusCard>
        </div>
      </div>

      <RightStatusPanel snapshot={snapshot} execution={state.execution} recentEvents={state.recent_events} />
    </div>
  );
}

function StatusCard({ title, icon: Icon, children }: any) {
  return (
    <section className="industrial-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-secondary/10">
          <Icon className="text-secondary" size={18} />
        </div>
        <h3 className="font-bold">{title}</h3>
      </div>
      <div className="space-y-4">
        {children}
      </div>
    </section>
  );
}

function DataRow({ label, value, status }: { label: string; value: string; status?: 'success' | 'warning' | 'error' }) {
  return (
    <div className="flex justify-between items-center text-sm">
      <span className="text-on-surface-variant">{label}</span>
      <span className={`font-mono font-bold ${
        status === 'success' ? 'text-primary' : 
        status === 'warning' ? 'text-warning' : 
        status === 'error' ? 'text-danger' : 
        'text-on-surface'
      }`}>
        {value}
      </span>
    </div>
  );
}
