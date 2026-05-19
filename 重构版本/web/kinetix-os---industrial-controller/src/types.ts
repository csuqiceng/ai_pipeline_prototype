export type UserRole = 'ENGINEER' | 'OPERATOR';

export type EngineerView = 'RUNNING' | 'BACKEND' | 'LOGS';
export type OperatorView = 'CHAT' | 'SAFETY' | 'MONITOR' | 'ALARMS' | 'STATUS';

export type ViewState = 'LOGIN' | EngineerView | OperatorView;

export interface JointState {
  id: string;
  name: string;
  min: number;
  max: number;
  current: number;
  locked: boolean;
  warning?: boolean;
}

export interface TelemetryData {
  mainPower: number;
  servoTemp: number;
  status: 'NOMINAL' | 'WARNING' | 'CRITICAL';
}

export interface LogEntry {
  id: string;
  timestamp: string;
  message: string;
  type: 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR';
}

export interface TaskStage {
  id: string;
  name: string;
  description: string;
  status: 'COMPLETE' | 'RUNNING' | 'PENDING';
  progress: number;
  subTasks?: { name: string; progress: number }[];
}
