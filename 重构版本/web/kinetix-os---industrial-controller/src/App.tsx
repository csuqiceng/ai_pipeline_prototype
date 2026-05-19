import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import AppShell from './components/AppShell';
import { RobotStateProvider, useRobotSnapshot } from './state/robotStore';
import type { UserRole, ViewState } from './types';
import Login from './views/Login';
import OperatorDashboard from './views/OperatorDashboard';
import SafetyPreCheck from './views/SafetyPreCheck';
import ExecutionMonitor from './views/ExecutionMonitor';
import SystemStatus from './views/SystemStatus';
import TemplateManagement from './views/TemplateManagement';
import SystemLogs from './views/SystemLogs';
import AlarmHandling from './views/AlarmHandling';

export default function App() {
  return (
    <RobotStateProvider>
      <AppContent />
    </RobotStateProvider>
  );
}

function AppContent() {
  const [view, setView] = useState<ViewState>('LOGIN');
  const [role, setRole] = useState<UserRole | null>(null);
  const snapshot = useRobotSnapshot();

  const handleLogin = (selectedRole: UserRole) => {
    setRole(selectedRole);
    setView(selectedRole === 'ENGINEER' ? 'RUNNING' : 'CHAT');
  };

  const handleLogout = () => {
    setRole(null);
    setView('LOGIN');
  };

  return (
    <div className="min-h-screen bg-bg-page font-sans">
      <AnimatePresence mode="wait">
        {view === 'LOGIN' || !role ? (
          <motion.div key="login" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Login onLogin={handleLogin} />
          </motion.div>
        ) : (
          <AppShell role={role} view={view} snapshot={snapshot} onViewChange={setView} onLogout={handleLogout}>
            {view === 'CHAT' && <OperatorDashboard />}
            {view === 'SAFETY' && <SafetyPreCheck />}
            {view === 'MONITOR' && <ExecutionMonitor />}
            {view === 'ALARMS' && <AlarmHandling />}
            {view === 'STATUS' && <SystemStatus />}
            {view === 'RUNNING' && <ExecutionMonitor engineerMode />}
            {view === 'BACKEND' && <TemplateManagement />}
            {view === 'LOGS' && <SystemLogs />}
          </AppShell>
        )}
      </AnimatePresence>
    </div>
  );
}
