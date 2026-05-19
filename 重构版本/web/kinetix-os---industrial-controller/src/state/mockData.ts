import type {
  AlarmEvent,
  ConversationEvent,
  DashboardState,
  ExecutionState,
  PrecheckResult,
  RobotSnapshot,
} from '../api/types';

export const mockRobotSnapshot: RobotSnapshot = {
  timestamp: new Date().toISOString(),
  connection: {
    controller: 'online',
    modbus_tcp: 'online',
    ethercat: 'online',
    realtime_feedback: 'online',
    voice: 'online',
  },
  safety: {
    estop: false,
    paused: false,
    alarm_active: false,
    safe_range: {
      r_min: 200,
      r_max: 1500,
      z_min: 100,
      z_max: 1200,
    },
  },
  motion: {
    speed_percent: 30,
    acceleration_percent: 100,
    running_state: 'idle',
  },
  position: {
    joint: {
      j1: 1250,
      j2: 0,
      j3: 860,
      j4: 0.2,
      j5: 0,
      j6: 0,
    },
    cartesian: {
      x: 898.1,
      y: 866.2,
      z: 860,
      r: 1250,
    },
  },
};

export const mockExecutionState: ExecutionState = {
  status: 'idle',
  progress: 0,
  stages: [
    { id: 'receive', name: '接收指令', status: 'complete', progress: 100 },
    { id: 'parse', name: '语义解析', status: 'pending', progress: 0 },
    { id: 'precheck', name: '安全预检', status: 'pending', progress: 0 },
    { id: 'execute', name: '动作执行', status: 'pending', progress: 0 },
  ],
};

export const mockPrecheckResult: PrecheckResult = {
  plan_id: 'mock-plan-idle',
  status: 'pending',
  items: [
    {
      id: 'estop',
      level: 'L1',
      label: '无紧急停止',
      status: 'pass',
      message: '急停回路正常。',
    },
    {
      id: 'alarm',
      level: 'L1',
      label: '无活动报警',
      status: 'pass',
      message: '当前没有活动报警。',
    },
    {
      id: 'paused',
      level: 'L1',
      label: '未处于暂停状态',
      status: 'pass',
      message: '系统未暂停。',
    },
    {
      id: 'controller',
      level: 'L1',
      label: '控制器在线',
      status: 'pending',
      message: '等待接入本地控制服务。',
    },
  ],
};

export const mockConversationEvents: ConversationEvent[] = [
  {
    id: 'evt-001',
    session_id: 'mock-session',
    role: 'system',
    type: 'receipt',
    text: "预检程序已完成。所有安全参数均在正常范围内。等待序列 'Pick & Place Alpha' 的指令。",
    timestamp: '14:02:11',
  },
  {
    id: 'evt-002',
    session_id: 'mock-session',
    role: 'user',
    type: 'message',
    text: "启动序列 'Pick & Place Alpha'。保持速度在 0.5m/s。",
    timestamp: '14:03:45',
  },
];

export const mockAlarmEvents: AlarmEvent[] = [];

export const mockDashboardState: DashboardState = {
  snapshot: mockRobotSnapshot,
  precheck: mockPrecheckResult,
  execution: mockExecutionState,
  recent_events: mockConversationEvents,
  active_alarms: mockAlarmEvents,
};
