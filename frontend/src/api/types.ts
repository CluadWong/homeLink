/** 线协议类型：字段名严格按 docs/api-contract.md，不额外发明字段。 */

export interface DeviceFunction {
  id: string
  name: string
}

export interface Device {
  id: string
  name: string
  room: string
  adapter: string
  notes: string | null
  extra: Record<string, unknown>
  functions: DeviceFunction[]
}

export interface Room {
  name: string
  display_name: string
  sort_order: number
  device_count: number
}

export type StateSource = 'unknown' | 'confirmed'

export interface StateDevice {
  id: string
  name: string
  state: string
  state_source: StateSource
  updated_at: string | null
}

export interface Sensors {
  available: boolean
  temperature: number | null
  humidity: number | null
  presence: number | null
}

export interface AppState {
  devices: StateDevice[]
  sensors: Sensors
}

export interface GatewayStatus {
  mode: string
  base_url: string
  reachable: boolean
  checked_at: string
  latency_ms: number
  error: string | null
  last_success_at: string | null
  simulated: boolean
  pending_restart: boolean
  trace_id?: string
}

export interface AuditRecord {
  created_at: string
  action: string
  device_id: string | null
  result: 'success' | 'failed'
  reason: string | null
  trace_id: string
}

export type CaptureReason = 'no_signal' | 'malformed' | 'gateway_unreachable'

export interface CaptureResult {
  captured: boolean
  device_id: string
  raw_code: number[] | null
  message: string
  reason: CaptureReason | null
  trace_id: string
}

export interface ControlResult {
  function_id: string
  device_id: string
  state: string
  message: string
}

export interface LearnSaveResult {
  function_id: string
  name: string
  receiver_stopped: boolean
  receiver_error: string | null
}

export interface LearnCancelResult {
  device_id: string
  message: string
}

export interface CorrectResult {
  device_id: string
  state: string
  state_source: StateSource
}

export interface ListDevicesPayload {
  success: true
  devices: Device[]
  trace_id: string
}

export interface ListRoomsPayload {
  success: true
  rooms: Room[]
  trace_id: string
}

export interface ListAuditPayload {
  success: true
  records: AuditRecord[]
  trace_id: string
}

// ---------------------------------------------------------------- Agent 场景

export type SceneTriggerType = 'manual' | 'schedule' | 'voice' | 'device_state'

export interface SceneTarget {
  device_id: string
  device_name: string
  room: string
  function_id: string | null
  command: string
}

export interface SceneActionGroup {
  group_id: string
  name: string
  capability: string
  targets: SceneTarget[]
}

export interface SceneTrigger {
  trigger_id: string
  type: SceneTriggerType
  enabled: boolean
  config: Record<string, unknown>
}

export interface Scene {
  scene_id: string
  name: string
  enabled: boolean
  action_groups: SceneActionGroup[]
  triggers: SceneTrigger[]
  items: Array<{ device_id: string; function_id: string | null; command: string }>
  contains_high_risk: boolean
  risk_level: 'low' | 'high'
  created_at: string
  updated_at: string
}

export interface CapabilityDevice {
  device_id: string
  name: string
  room: string
  function_id: string
}

export interface Capability {
  capability: string
  device_count: number
  devices: CapabilityDevice[]
}

export type SceneRunStatus = 'sent' | 'failed' | 'offline'

export interface SceneRunItemResult {
  group_id: string
  capability: string
  device_id: string
  device_name: string
  command: string
  status: SceneRunStatus
  request_id?: string
  error_code?: string
  message: string
}

export interface SceneRunResult {
  scene_id: string
  status: 'sent' | 'partial' | 'failed'
  results: SceneRunItemResult[]
  summary: { total: number; sent: number; failed: number; offline: number }
}

export interface SceneListPayload {
  success: true
  result: { count: number; scenes: Scene[] }
  trace_id: string
}

export interface ScenePayload {
  success: true
  result: Scene
  trace_id: string
}

export interface SceneRunPayload {
  success: true
  result: SceneRunResult
  trace_id: string
}

/** 一次历史执行的单设备记录（只含真正下发过的设备）。 */
export interface SceneRunRecordItem {
  device_id: string
  command: string
  status: SceneRunStatus
  error_code: string | null
  error_message: string | null
}

export interface SceneRunRecord {
  trace_id: string
  started_at: string
  status: 'sent' | 'partial' | 'failed'
  summary: { total: number; sent: number; failed: number }
  results: SceneRunRecordItem[]
}

export interface SceneRunsPayload {
  success: true
  result: { scene_id: string; runs: SceneRunRecord[] }
  trace_id: string
}

/** 定时任务的上次执行状态（由 agentd 写入 agent.db，后端代理读出）。 */
export interface ScheduleRecord {
  id: string
  scene_id: string
  cron: string
  enabled: boolean
  confirm: boolean
  created_at: string
  last_run_at: string | null
  last_status: 'ok' | 'error' | null
  last_error: string | null
  source: string
  trigger_id: string | null
}

export interface ScheduleListPayload {
  success: true
  result: { count: number; schedules: ScheduleRecord[] }
  trace_id: string
}

export interface CapabilityListPayload {
  success: true
  result: { count: number; capabilities: Capability[] }
  trace_id: string
}

export interface SceneActionGroupInput {
  name: string
  capability: string
  targets: Array<{ device_id: string }>
}

export interface SceneTriggerInput {
  type: SceneTriggerType
  enabled: boolean
  config: Record<string, unknown>
}
