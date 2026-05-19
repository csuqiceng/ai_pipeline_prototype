export type ConnectionState = 'online' | 'offline' | 'degraded';
export type RunningState = 'idle' | 'prechecking' | 'waiting_confirm' | 'running' | 'paused' | 'blocked';
export type AlarmLevel = 'warning' | 'critical' | 'emergency';
export type AlarmLifecycle = 'occurred' | 'acknowledged' | 'reset_requested' | 'reset_done' | 'cleared';
export type ConversationRole = 'user' | 'assistant' | 'system';
export type ConversationEventType =
  | 'message'
  | 'receipt'
  | 'plan_created'
  | 'precheck_result'
  | 'execution_update'
  | 'voice_input_complete'
  | 'alarm_occurred'
  | 'alarm_confirmed'
  | 'alarm_reset';

export interface JointPosition {
  j1: number;
  j2: number;
  j3: number;
  j4: number;
  j5: number;
  j6: number;
}

export interface CartesianPosition {
  x: number;
  y: number;
  z: number;
  r: number;
}

export interface SafetyState {
  estop: boolean;
  paused: boolean;
  alarm_active: boolean;
  alarm_level?: AlarmLevel;
  safe_range: {
    r_min: number;
    r_max: number;
    z_min: number;
    z_max: number;
  };
}

export interface MotionState {
  speed_percent: number;
  acceleration_percent: number;
  task_id?: string;
  active_plan_id?: string;
  running_state: RunningState;
}

export interface ConnectionStatus {
  controller: ConnectionState;
  modbus_tcp: ConnectionState;
  ethercat: ConnectionState;
  realtime_feedback: ConnectionState;
  voice: ConnectionState;
}

export interface RobotSnapshot {
  timestamp: string;
  connection: ConnectionStatus;
  safety: SafetyState;
  motion: MotionState;
  position: {
    joint: JointPosition;
    cartesian: CartesianPosition;
  };
  current_function?: string;
}

export interface DashboardState {
  snapshot: RobotSnapshot;
  active_plan?: ActionPlan;
  precheck?: PrecheckResult;
  execution?: ExecutionState;
  recent_events: ConversationEvent[];
  active_alarms: AlarmEvent[];
}

export interface ActionPlanStep {
  id: string;
  title: string;
  description: string;
  status: 'pending' | 'ready' | 'running' | 'complete' | 'blocked';
}

export interface ActionPlan {
  plan_id: string;
  session_id: string;
  source_text: string;
  intent: 'query' | 'motion' | 'flow' | 'system' | 'emergency';
  status: 'draft' | 'prechecking' | 'waiting_confirm' | 'approved' | 'running' | 'complete' | 'rejected' | 'cancelled';
  summary: string;
  target?: Record<string, unknown>;
  steps: ActionPlanStep[];
  created_at: string;
  expires_at?: string;
}

export interface PrecheckItem {
  id: string;
  level: 'L1' | 'L2' | 'L3';
  label: string;
  status: 'pass' | 'warning' | 'fail' | 'pending';
  message: string;
}

export interface PrecheckResult {
  plan_id: string;
  status: 'pass' | 'warning' | 'fail' | 'pending';
  items: PrecheckItem[];
  suggestion?: string;
}

export interface ExecutionStage {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'complete' | 'failed' | 'cancelled';
  progress: number;
}

export interface ExecutionState {
  plan_id?: string;
  task_id?: string;
  status: RunningState;
  progress: number;
  current_stage?: string;
  eta_seconds?: number;
  stages: ExecutionStage[];
}

export interface ConversationEvent {
  id: string;
  session_id: string;
  role: ConversationRole;
  type: ConversationEventType;
  text: string;
  timestamp: string;
  plan_id?: string;
}

export interface AlarmEvent {
  id: string;
  level: AlarmLevel;
  lifecycle: AlarmLifecycle;
  title: string;
  message: string;
  occurred_at: string;
  acknowledged_at?: string;
  reset_at?: string;
  snapshot?: RobotSnapshot;
}
