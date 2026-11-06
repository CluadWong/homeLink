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
  draft_discarded: boolean
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
