import type { DashboardState } from './types';

const runtimeOrigin = typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8765';
const runtimeWsBase =
  typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : 'ws://127.0.0.1:8765';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? runtimeOrigin;
const WS_URL = import.meta.env.VITE_WS_URL ?? `${runtimeWsBase}/ws/telemetry`;

export const dataMode = import.meta.env.VITE_DATA_MODE ?? 'mock';

interface ConversationInputResponse {
  accepted: boolean;
  dashboard: DashboardState;
}

export interface ControlExecutionResponse {
  accepted: boolean;
  dashboard: DashboardState;
  bridge?: {
    accepted: boolean;
    dispatch_id: string;
    mode: string;
    message: string;
    detail?: Record<string, unknown>;
  };
}

export interface BridgeStatus {
  mode: string;
  queue_size: number;
  entrypoints: string[];
  service_attached: boolean;
  controller_host?: string | null;
  mock_controller_connected?: boolean;
}

export interface VoiceStatus {
  running: boolean;
  phase: string;
  mode: string;
  last_text: string;
  last_error: string;
  result_path?: string;
  worker_log_path?: string;
  started_at?: string;
  finished_at?: string;
}

interface TelemetryMessage {
  type: 'dashboard' | 'snapshot' | string;
  payload: unknown;
  timestamp: string;
}

export function telemetryUrl() {
  return WS_URL;
}

export async function fetchDashboard(): Promise<DashboardState> {
  return request<DashboardState>('/api/dashboard');
}

export async function submitConversationInput(text: string, sessionId = 'mock-session'): Promise<DashboardState> {
  const response = await request<ConversationInputResponse>('/api/conversation/input', {
    method: 'POST',
    body: JSON.stringify({
      text,
      session_id: sessionId,
      input_mode: 'text',
    }),
  });
  return response.dashboard;
}

export async function adoptPlan(planId: string): Promise<DashboardState> {
  return request<DashboardState>('/api/plans/adopt', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId }),
  });
}

export async function cancelPlan(): Promise<DashboardState> {
  return request<DashboardState>('/api/plans/cancel', { method: 'POST' });
}

export async function togglePause(): Promise<DashboardState> {
  return request<DashboardState>('/api/system/pause', { method: 'POST' });
}

export async function emergencyStop(): Promise<DashboardState> {
  return request<DashboardState>('/api/system/emergency-stop', { method: 'POST' });
}

export async function resetMock(): Promise<DashboardState> {
  return request<DashboardState>('/api/system/reset-mock', { method: 'POST' });
}

export async function executeTemplate(queryKey: string): Promise<ControlExecutionResponse> {
  return request(`/api/control/templates/${encodeURIComponent(queryKey)}/execute`, { method: 'POST' });
}

export async function startFlow(flowName: string): Promise<ControlExecutionResponse> {
  return request(`/api/control/flows/${encodeURIComponent(flowName)}/start`, { method: 'POST' });
}

export async function stepFlow(flowName: string): Promise<ControlExecutionResponse> {
  return request(`/api/control/flows/${encodeURIComponent(flowName)}/step`, { method: 'POST' });
}

export async function stopFlow(): Promise<ControlExecutionResponse> {
  return request('/api/control/flows/stop', { method: 'POST' });
}

export async function resetFlow(): Promise<ControlExecutionResponse> {
  return request('/api/control/flows/reset', { method: 'POST' });
}

export async function fetchBridgeStatus(): Promise<BridgeStatus> {
  return request('/api/control/bridge/status');
}

export interface TemplateRecord {
  query_key: string;
  func_num: number;
  function_name: string;
  keywords: string;
  description: string;
  safety_level: number;
  params: Record<string, number | string | unknown[]>;
  summary: string;
}

export interface FlowRecord {
  name: string;
  steps: string[];
  step_delay_ms: number;
}

export interface AvoidanceSafePoint {
  name: string;
  x: number;
  y: number;
  z: number;
  rx: number;
  ry: number;
  rz: number;
  speed_percent: number;
  acc_percent: number;
  description: string;
}

export interface AvoidanceConfig {
  mode: string;
  rx_threshold: number;
  ry_threshold: number;
  rz_threshold: number;
  low_z_threshold: number;
  xy_move_threshold: number;
  safe_points: Record<string, AvoidanceSafePoint>;
  rules: Record<string, unknown>[];
}

export interface WebLogEntry {
  time: string;
  ts: string;
  category: string;
  action: string;
  result: string;
  detail: string;
  [key: string]: unknown;
}

export async function fetchTemplates(): Promise<{ records: TemplateRecord[]; count: number }> {
  return request('/api/templates');
}

export async function saveTemplate(originalKey: string, record: TemplateRecord): Promise<{ records: TemplateRecord[]; count: number }> {
  return request(`/api/templates/${encodeURIComponent(originalKey)}`, {
    method: 'PUT',
    body: JSON.stringify(record),
  });
}

export async function deleteTemplate(queryKey: string): Promise<{ records: TemplateRecord[]; count: number }> {
  return request(`/api/templates/${encodeURIComponent(queryKey)}`, { method: 'DELETE' });
}

export async function fetchFlows(): Promise<{ flows: FlowRecord[] }> {
  return request('/api/flows');
}

export async function saveFlow(originalName: string, flow: FlowRecord): Promise<{ flows: FlowRecord[] }> {
  return request(`/api/flows/${encodeURIComponent(originalName)}`, {
    method: 'PUT',
    body: JSON.stringify(flow),
  });
}

export async function deleteFlow(name: string): Promise<{ flows: FlowRecord[] }> {
  return request(`/api/flows/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

export async function fetchSystemConfig(): Promise<Record<string, unknown>> {
  return request('/api/system/config');
}

export async function saveSystemConfig(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request('/api/system/config', {
    method: 'PUT',
    body: JSON.stringify({ config }),
  });
}

export async function fetchAvoidanceConfig(): Promise<AvoidanceConfig> {
  return request('/api/avoidance-config');
}

export async function saveAvoidanceConfig(config: AvoidanceConfig): Promise<AvoidanceConfig> {
  return request('/api/avoidance-config', {
    method: 'PUT',
    body: JSON.stringify(config),
  });
}

export async function fetchRecentLogs(limit = 100): Promise<{ entries: WebLogEntry[]; log_path: string }> {
  return request(`/api/logs/recent?limit=${limit}`);
}

export async function parseNlpText(text: string, useDeepseek = false): Promise<Record<string, unknown>> {
  return request('/api/nlp/parse', {
    method: 'POST',
    body: JSON.stringify({ text, use_deepseek: useDeepseek }),
  });
}

export async function fetchVoiceDevices(): Promise<{
  devices: { id: string; name: string; channels: number; default_samplerate: number }[];
  selected_device_id?: string | null;
}> {
  return request('/api/voice/devices');
}

export async function fetchVoiceStatus(): Promise<VoiceStatus> {
  return request('/api/voice/status');
}

export async function startVoice(): Promise<VoiceStatus> {
  return request('/api/voice/start', { method: 'POST' });
}

export async function stopVoice(): Promise<VoiceStatus> {
  return request('/api/voice/stop', { method: 'POST' });
}

export function parseTelemetryMessage(raw: MessageEvent<string>): TelemetryMessage | null {
  try {
    return JSON.parse(raw.data) as TelemetryMessage;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const detail = await readError(response);
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

async function readError(response: Response) {
  try {
    const body = await response.json();
    return body?.detail?.message ?? body?.message ?? response.statusText;
  } catch {
    return response.statusText;
  }
}
