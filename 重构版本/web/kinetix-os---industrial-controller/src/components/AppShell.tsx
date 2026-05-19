import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { RobotSnapshot } from '../api/types';
import type { UserRole, ViewState } from '../types';
import SideNav from './SideNav';
import TopStatusBar from './TopStatusBar';

interface AppShellProps {
  role: UserRole;
  view: ViewState;
  snapshot: RobotSnapshot;
  children: ReactNode;
  onViewChange: (view: ViewState) => void;
  onLogout: () => void;
}

export default function AppShell({ role, view, snapshot, children, onViewChange, onLogout }: AppShellProps) {
  return (
    <div className="flex h-screen overflow-hidden">
      <SideNav role={role} view={view} onViewChange={onViewChange} onLogout={onLogout} />
      <main className="relative flex flex-1 flex-col overflow-hidden">
        <TopStatusBar snapshot={snapshot} />
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            className="flex-1 overflow-hidden"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
          >
            {children}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
