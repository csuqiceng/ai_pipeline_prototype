import { createContext, useContext, useEffect, useMemo, useReducer } from 'react';
import type { ReactNode } from 'react';
import type {
  ActionPlan,
  ConnectionState,
  ConversationEvent,
  DashboardState,
  ExecutionState,
  PrecheckResult,
  RobotSnapshot,
} from '../api/types';
import * as webApi from '../api/client';
import { mockDashboardState } from './mockData';

type StoreAction =
  | { type: 'HYDRATE'; state: DashboardState }
  | { type: 'PATCH_SNAPSHOT'; snapshot: RobotSnapshot }
  | { type: 'REMOTE_ERROR'; message: string }
  | { type: 'SUBMIT_COMMAND'; text: string }
  | { type: 'ADOPT_PLAN' }
  | { type: 'ADVANCE_EXECUTION' }
  | { type: 'CANCEL_PLAN' }
  | { type: 'PAUSE_EXECUTION' }
  | { type: 'EMERGENCY_STOP' }
  | { type: 'RESET_MOCK' };

interface RobotStoreValue {
  state: DashboardState;
  snapshot: RobotSnapshot;
  submitCommand: (text: string) => void;
  adoptActivePlan: () => void;
  cancelActivePlan: () => void;
  pauseExecution: () => void;
  triggerEmergencyStop: () => void;
  resetMock: () => void;
  hydrateDashboard: (dashboard: DashboardState) => void;
}

const RobotStoreContext = createContext<RobotStoreValue | null>(null);

export function RobotStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(robotReducer, cloneDashboardState(mockDashboardState));
  const apiMode = webApi.dataMode === 'api';

  useEffect(() => {
    if (apiMode || state.execution?.status !== 'running') return;

    const timer = window.setInterval(() => {
      dispatch({ type: 'ADVANCE_EXECUTION' });
    }, 700);

    return () => window.clearInterval(timer);
  }, [apiMode, state.execution?.status, state.execution?.progress]);

  useEffect(() => {
    if (!apiMode) return;

    let cancelled = false;
    webApi
      .fetchDashboard()
      .then((dashboard) => {
        if (!cancelled) dispatch({ type: 'HYDRATE', state: dashboard });
      })
      .catch((error) => {
        if (!cancelled) dispatch({ type: 'REMOTE_ERROR', message: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, [apiMode]);

  useEffect(() => {
    if (!apiMode) return;

    let closedByProvider = false;
    let reconnectTimer: number | undefined;
    let retryCount = 0;
    let socket: WebSocket | undefined;

    const connect = () => {
      socket = new WebSocket(webApi.telemetryUrl());

      socket.onopen = () => {
        retryCount = 0;
      };

      socket.onmessage = (message) => {
        const parsed = webApi.parseTelemetryMessage(message);
        if (!parsed) return;
        if (parsed.type === 'dashboard') {
          dispatch({ type: 'HYDRATE', state: parsed.payload as DashboardState });
        }
        if (parsed.type === 'snapshot') {
          dispatch({ type: 'PATCH_SNAPSHOT', snapshot: parsed.payload as RobotSnapshot });
        }
        if (parsed.type === 'voice_input_complete') {
          const payload = parsed.payload as { text?: unknown };
          if (typeof payload.text === 'string' && payload.text.trim()) {
            webApi
              .submitConversationInput(payload.text.trim(), 'mock-session')
              .then((dashboard) => dispatch({ type: 'HYDRATE', state: dashboard }))
              .catch((error) => dispatch({ type: 'REMOTE_ERROR', message: error.message }));
          }
        }
      };

      socket.onerror = () => {
        socket?.close();
      };

      socket.onclose = () => {
        if (closedByProvider) return;
        const delay = retryCount < 10 ? 2000 : 5000;
        retryCount += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closedByProvider = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [apiMode]);

  const hydrateFromRemote = (request: Promise<DashboardState>) => {
    request
      .then((dashboard) => dispatch({ type: 'HYDRATE', state: dashboard }))
      .catch((error) => dispatch({ type: 'REMOTE_ERROR', message: error.message }));
  };

  const value = useMemo<RobotStoreValue>(
    () => ({
      state,
      snapshot: state.snapshot,
      submitCommand: (text) => {
        if (apiMode) hydrateFromRemote(webApi.submitConversationInput(text));
        else dispatch({ type: 'SUBMIT_COMMAND', text });
      },
      adoptActivePlan: () => {
        if (apiMode && state.active_plan) hydrateFromRemote(webApi.adoptPlan(state.active_plan.plan_id));
        else dispatch({ type: 'ADOPT_PLAN' });
      },
      cancelActivePlan: () => {
        if (apiMode) hydrateFromRemote(webApi.cancelPlan());
        else dispatch({ type: 'CANCEL_PLAN' });
      },
      pauseExecution: () => {
        if (apiMode) hydrateFromRemote(webApi.togglePause());
        else dispatch({ type: 'PAUSE_EXECUTION' });
      },
      triggerEmergencyStop: () => {
        if (apiMode) hydrateFromRemote(webApi.emergencyStop());
        else dispatch({ type: 'EMERGENCY_STOP' });
      },
      resetMock: () => {
        if (apiMode) hydrateFromRemote(webApi.resetMock());
        else dispatch({ type: 'RESET_MOCK' });
      },
      hydrateDashboard: (dashboard) => dispatch({ type: 'HYDRATE', state: dashboard }),
    }),
    [apiMode, state],
  );

  return <RobotStoreContext.Provider value={value}>{children}</RobotStoreContext.Provider>;
}

export function useRobotStore() {
  const value = useContext(RobotStoreContext);
  if (!value) {
    throw new Error('useRobotStore must be used inside RobotStateProvider');
  }
  return value;
}

export function useDashboardState() {
  return useRobotStore().state;
}

export function useRobotSnapshot(): RobotSnapshot {
  return useRobotStore().snapshot;
}

function robotReducer(state: DashboardState, action: StoreAction): DashboardState {
  switch (action.type) {
    case 'HYDRATE':
      return normalizeDashboard(action.state);
    case 'PATCH_SNAPSHOT':
      return { ...state, snapshot: action.snapshot };
    case 'REMOTE_ERROR':
      return appendEvents(state, [
        createEvent('system', 'message', `本地 Web API 暂不可用：${action.message}。当前页面继续使用最后一次状态。`),
      ]);
    case 'SUBMIT_COMMAND':
      return submitCommand(state, action.text);
    case 'ADOPT_PLAN':
      return adoptPlan(state);
    case 'ADVANCE_EXECUTION':
      return advanceExecution(state);
    case 'CANCEL_PLAN':
      return cancelPlan(state);
    case 'PAUSE_EXECUTION':
      return pauseExecution(state);
    case 'EMERGENCY_STOP':
      return emergencyStop(state);
    case 'RESET_MOCK':
      return cloneDashboardState(mockDashboardState);
    default:
      return state;
  }
}

function submitCommand(state: DashboardState, rawText: string): DashboardState {
  const text = rawText.trim();
  if (!text) return state;

  const userEvent = createEvent('user', 'message', text);

  if (hasActivePlan(state)) {
    return appendEvents(state, [
      userEvent,
      createEvent('assistant', 'receipt', '当前已有计划处于预检、确认或执行状态。请先确认、取消或等待当前计划结束。'),
    ]);
  }

  if (isQueryCommand(text)) {
    return appendEvents(state, [
      userEvent,
      createEvent('assistant', 'message', buildStatusReply(state.snapshot)),
    ]);
  }

  const plan = createPlan(text);
  const precheck = createPassingPrecheck(plan.plan_id, state.snapshot);
  const execution = createWaitingExecution(plan.plan_id);

  return {
    ...appendEvents(state, [
      userEvent,
      createEvent('assistant', 'receipt', '收到指令，已生成动作计划并完成模拟预检。请确认后执行。', plan.plan_id),
      createEvent('system', 'precheck_result', '安全预检通过：急停、报警、暂停、通讯状态均满足执行条件。', plan.plan_id),
    ]),
    active_plan: plan,
    precheck,
    execution,
    snapshot: {
      ...state.snapshot,
      timestamp: new Date().toISOString(),
      motion: {
        ...state.snapshot.motion,
        active_plan_id: plan.plan_id,
        running_state: 'waiting_confirm',
      },
    },
  };
}

function adoptPlan(state: DashboardState): DashboardState {
  if (!state.active_plan || !state.execution) return state;
  if (state.active_plan.status !== 'waiting_confirm') return state;

  const activePlan = {
    ...state.active_plan,
    status: 'running' as const,
  };

  return appendEvents(
    {
      ...state,
      active_plan: activePlan,
      execution: {
        ...state.execution,
        status: 'running',
        progress: 20,
        current_stage: '动作执行',
        eta_seconds: 6,
        stages: state.execution.stages.map((stage) =>
          stage.id === 'execute' ? { ...stage, status: 'running', progress: 20 } : stage,
        ),
      },
      snapshot: {
        ...state.snapshot,
        timestamp: new Date().toISOString(),
        motion: {
          ...state.snapshot.motion,
          task_id: `TASK-${Date.now().toString().slice(-5)}`,
          running_state: 'running',
        },
      },
    },
    [createEvent('assistant', 'execution_update', '已确认执行，模拟任务开始运行。', activePlan.plan_id)],
  );
}

function advanceExecution(state: DashboardState): DashboardState {
  if (!state.execution || state.execution.status !== 'running') return state;

  const progress = Math.min(100, state.execution.progress + 16);
  const completed = progress >= 100;
  const activePlan = state.active_plan
    ? {
        ...state.active_plan,
        status: completed ? ('complete' as const) : state.active_plan.status,
      }
    : undefined;

  const nextState: DashboardState = {
    ...state,
    active_plan: completed ? undefined : activePlan,
    execution: {
      ...state.execution,
      status: completed ? 'idle' : 'running',
      progress,
      current_stage: completed ? undefined : '动作执行',
      eta_seconds: completed ? undefined : Math.max(1, Math.ceil((100 - progress) / 16)),
      stages: state.execution.stages.map((stage) =>
        stage.id === 'execute'
          ? { ...stage, status: completed ? 'complete' : 'running', progress }
          : stage,
      ),
    },
    snapshot: {
      ...state.snapshot,
      timestamp: new Date().toISOString(),
      motion: {
        ...state.snapshot.motion,
        active_plan_id: completed ? undefined : state.snapshot.motion.active_plan_id,
        task_id: completed ? undefined : state.snapshot.motion.task_id,
        running_state: completed ? 'idle' : 'running',
      },
      position: completed ? nextMockPosition(state.snapshot) : state.snapshot.position,
    },
  };

  if (!completed) return nextState;

  return appendEvents(nextState, [
    createEvent('assistant', 'execution_update', '模拟执行完成，当前位置与执行状态已更新。', state.active_plan?.plan_id),
  ]);
}

function cancelPlan(state: DashboardState): DashboardState {
  if (!hasActivePlan(state)) {
    return appendEvents(state, [createEvent('assistant', 'message', '当前没有可取消的计划。')]);
  }

  return appendEvents(
    {
      ...state,
      active_plan: undefined,
      execution: createIdleExecution(),
      snapshot: {
        ...state.snapshot,
        timestamp: new Date().toISOString(),
        motion: {
          ...state.snapshot.motion,
          active_plan_id: undefined,
          task_id: undefined,
          running_state: 'idle',
        },
      },
    },
    [createEvent('assistant', 'execution_update', '当前计划已取消，系统回到空闲状态。')],
  );
}

function pauseExecution(state: DashboardState): DashboardState {
  const paused = !state.snapshot.safety.paused;
  const runningState = paused ? 'paused' : state.execution?.status === 'running' ? 'running' : state.snapshot.motion.running_state;

  return appendEvents(
    {
      ...state,
      snapshot: {
        ...state.snapshot,
        timestamp: new Date().toISOString(),
        safety: {
          ...state.snapshot.safety,
          paused,
        },
        motion: {
          ...state.snapshot.motion,
          running_state: runningState,
        },
      },
      execution: state.execution
        ? {
            ...state.execution,
            status: paused ? 'paused' : state.execution.status === 'paused' ? 'running' : state.execution.status,
          }
        : state.execution,
    },
    [createEvent('system', 'execution_update', paused ? '已进入暂停状态。' : '暂停已解除。')],
  );
}

function emergencyStop(state: DashboardState): DashboardState {
  return appendEvents(
    {
      ...state,
      active_plan: undefined,
      execution: {
        ...createIdleExecution(),
        status: 'blocked',
      },
      snapshot: {
        ...state.snapshot,
        timestamp: new Date().toISOString(),
        safety: {
          ...state.snapshot.safety,
          estop: true,
          alarm_active: true,
          alarm_level: 'emergency',
        },
        motion: {
          ...state.snapshot.motion,
          active_plan_id: undefined,
          task_id: undefined,
          running_state: 'blocked',
        },
      },
      active_alarms: [
        {
          id: `alarm-${Date.now()}`,
          level: 'emergency',
          lifecycle: 'occurred',
          title: '模拟急停触发',
          message: '操作员触发急停，当前动作计划已被阻断。',
          occurred_at: new Date().toISOString(),
          snapshot: state.snapshot,
        },
        ...state.active_alarms,
      ],
    },
    [createEvent('system', 'alarm_occurred', '急停已触发。模拟执行被立即阻断，请复位后继续。')],
  );
}

function appendEvents(state: DashboardState, events: ConversationEvent[]): DashboardState {
  return {
    ...state,
    recent_events: [...state.recent_events, ...events],
  };
}

function hasActivePlan(state: DashboardState) {
  return (
    state.active_plan?.status === 'waiting_confirm' ||
    state.active_plan?.status === 'running' ||
    state.snapshot.motion.running_state === 'running' ||
    state.snapshot.motion.running_state === 'waiting_confirm'
  );
}

function createPlan(text: string): ActionPlan {
  const planId = `plan-${Date.now()}`;

  return {
    plan_id: planId,
    session_id: 'mock-session',
    source_text: text,
    intent: text.includes('流程') ? 'flow' : 'motion',
    status: 'waiting_confirm',
    summary: `模拟动作计划：${text}`,
    steps: [
      { id: 'parse', title: '语义解析', description: '识别动作意图和目标参数。', status: 'complete', },
      { id: 'precheck', title: '安全预检', description: '检查急停、报警、暂停、通讯和运动边界。', status: 'complete' },
      { id: 'confirm', title: '等待确认', description: '操作员确认后进入执行。', status: 'ready' },
      { id: 'execute', title: '下发执行', description: '通过后端桥接控制服务下发。', status: 'pending' },
    ],
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 3 * 60 * 1000).toISOString(),
  };
}

function createPassingPrecheck(planId: string, snapshot: RobotSnapshot): PrecheckResult {
  const controllerPass = snapshot.connection.controller === 'online';

  return {
    plan_id: planId,
    status: controllerPass ? 'pass' : 'warning',
    suggestion: controllerPass ? undefined : '当前控制器离线，只允许 mock 演示，不允许真实下发。',
    items: [
      { id: 'estop', level: 'L1', label: '无紧急停止', status: snapshot.safety.estop ? 'fail' : 'pass', message: snapshot.safety.estop ? '急停已触发。' : '急停回路正常。' },
      { id: 'alarm', level: 'L1', label: '无活动报警', status: snapshot.safety.alarm_active ? 'fail' : 'pass', message: snapshot.safety.alarm_active ? '当前存在活动报警。' : '当前没有活动报警。' },
      { id: 'paused', level: 'L1', label: '未处于暂停状态', status: snapshot.safety.paused ? 'warning' : 'pass', message: snapshot.safety.paused ? '系统处于暂停状态。' : '系统未暂停。' },
      { id: 'controller', level: 'L1', label: '控制器在线', status: controllerPass ? 'pass' : 'warning', message: controllerPass ? '控制器连接正常。' : '等待接入本地控制服务。' },
      { id: 'range', level: 'L2', label: '目标范围预演', status: 'pass', message: 'mock 目标点位于 R/Z 安全范围内。' },
    ],
  };
}

function createWaitingExecution(planId: string): ExecutionState {
  return {
    plan_id: planId,
    status: 'waiting_confirm',
    progress: 0,
    current_stage: '等待确认',
    stages: [
      { id: 'receive', name: '接收指令', status: 'complete', progress: 100 },
      { id: 'parse', name: '语义解析', status: 'complete', progress: 100 },
      { id: 'precheck', name: '安全预检', status: 'complete', progress: 100 },
      { id: 'execute', name: '动作执行', status: 'pending', progress: 0 },
    ],
  };
}

function createIdleExecution(): ExecutionState {
  return {
    status: 'idle',
    progress: 0,
    stages: [
      { id: 'receive', name: '接收指令', status: 'pending', progress: 0 },
      { id: 'parse', name: '语义解析', status: 'pending', progress: 0 },
      { id: 'precheck', name: '安全预检', status: 'pending', progress: 0 },
      { id: 'execute', name: '动作执行', status: 'pending', progress: 0 },
    ],
  };
}

function createEvent(role: ConversationEvent['role'], type: ConversationEvent['type'], text: string, planId?: string): ConversationEvent {
  return {
    id: `evt-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
    session_id: 'mock-session',
    role,
    type,
    text,
    timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    plan_id: planId,
  };
}

function buildStatusReply(snapshot: RobotSnapshot) {
  return `当前状态：${runningStateLabel(snapshot.motion.running_state)}。R=${snapshot.position.cartesian.r.toFixed(1)}mm，Z=${snapshot.position.cartesian.z.toFixed(1)}mm，急停${snapshot.safety.estop ? '触发' : '正常'}，报警${snapshot.safety.alarm_active ? '有' : '无'}。`;
}

function isQueryCommand(text: string) {
  return /状态|查询|在哪|当前/.test(text);
}

function nextMockPosition(snapshot: RobotSnapshot): RobotSnapshot['position'] {
  const nextR = Math.min(snapshot.safety.safe_range.r_max, snapshot.position.cartesian.r + 18);
  const nextZ = Math.min(snapshot.safety.safe_range.z_max, snapshot.position.cartesian.z + 6);

  return {
    joint: {
      ...snapshot.position.joint,
      j1: nextR,
      j3: nextZ,
    },
    cartesian: {
      ...snapshot.position.cartesian,
      r: nextR,
      z: nextZ,
    },
  };
}

function cloneDashboardState(state: DashboardState): DashboardState {
  return structuredClone(state);
}

function normalizeDashboard(state: DashboardState): DashboardState {
  return {
    ...state,
    active_plan: state.active_plan ?? undefined,
    precheck: state.precheck ?? undefined,
    execution: state.execution ?? createIdleExecution(),
    recent_events: state.recent_events ?? [],
    active_alarms: state.active_alarms ?? [],
  };
}

export function connectionLabel(state: ConnectionState) {
  switch (state) {
    case 'online':
      return '正常';
    case 'degraded':
      return '降级';
    case 'offline':
      return '离线';
    default:
      return '未知';
  }
}

export function connectionTone(state: ConnectionState): 'success' | 'warning' | 'error' {
  if (state === 'online') return 'success';
  if (state === 'degraded') return 'warning';
  return 'error';
}

export function runningStateLabel(state: RobotSnapshot['motion']['running_state']) {
  switch (state) {
    case 'idle':
      return '空闲';
    case 'prechecking':
      return '预检中';
    case 'waiting_confirm':
      return '等待确认';
    case 'running':
      return '执行中';
    case 'paused':
      return '已暂停';
    case 'blocked':
      return '已阻断';
    default:
      return '未知';
  }
}
