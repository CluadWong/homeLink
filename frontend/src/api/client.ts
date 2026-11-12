/** 统一 HTTP 客户端：注入 base URL 与 token，集中处理 trace、超时与错误形状。 */

import {
  AGENT_UNREACHABLE,
  ApiError,
} from './errors'
import type {
  AgentChatPayload,
  AppState,
  CapabilityListPayload,
  CaptureResult,
  ControlResult,
  CorrectResult,
  Device,
  GatewayStatus,
  LearnCancelResult,
  LearnSaveResult,
  ListAuditPayload,
  ListDevicesPayload,
  ListRoomsPayload,
  Room,
  SceneActionGroupInput,
  SceneListPayload,
  ScenePayload,
  SceneRunPayload,
  SceneRunsPayload,
  SceneTriggerInput,
  ScheduleListPayload,
} from './types'

const TOKEN_KEY = 'homelink_api_token'
const REQUEST_TIMEOUT_MS = 30_000
// 一轮对话要等本机模型推理（实测 P95 约 39 秒），单独放宽超时。
const CHAT_TIMEOUT_MS = 180_000
const TRACE_MAX_LENGTH = 64

const injectedToken = (import.meta.env.VITE_API_TOKEN ?? '').trim()
const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

/** sessionStorage 不可用时的兜底：只活在当前页面内存里。 */
let memoryToken = ''

function readRuntimeToken(): string {
  if (memoryToken) return memoryToken
  try {
    return (sessionStorage.getItem(TOKEN_KEY) ?? '').trim()
  } catch {
    return ''
  }
}

/** 每次请求一个 8 位十六进制 trace；服务端会回带同值。 */
function newTraceId(): string {
  const bytes = new Uint8Array(4)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** 三档优先级：部署注入 > 本次标签页临时输入 > 空（请求不带 Authorization，由后端 401）。 */
export function currentToken(): string {
  if (injectedToken) return injectedToken
  return readRuntimeToken()
}

export function hasInjectedToken(): boolean {
  return injectedToken.length > 0
}

/** 只写 sessionStorage：同标签页刷新仍有效，关闭标签页即清空；禁止持久化。 */
export function setRuntimeToken(token: string): void {
  const value = token.trim()
  memoryToken = value
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // 存储被禁用时只保留当前页面内存值；刷新或关标签页同样失效。
  }
}

function headersFor(hasBody: boolean): Record<string, string> {
  const trace = newTraceId()
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Trace-Id': trace.slice(0, TRACE_MAX_LENGTH),
  }
  const token = currentToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (hasBody) headers['Content-Type'] = 'application/json'
  return headers
}

function queryString(params?: Record<string, string | number | boolean | undefined>): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function request<T>(
  method: string,
  path: string,
  options: {
    body?: Record<string, unknown>
    params?: Record<string, string | number | boolean | undefined>
    timeoutMs?: number
  } = {},
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), options.timeoutMs ?? REQUEST_TIMEOUT_MS)
  const url = `${baseUrl}${path}${queryString(options.params)}`

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers: headersFor(options.body !== undefined),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(AGENT_UNREACHABLE, '请求没有及时返回，请稍后重试。')
    }
    throw new ApiError(AGENT_UNREACHABLE, '请求没有到达后端，请确认后端已启动、服务地址正确。')
  } finally {
    window.clearTimeout(timer)
  }

  const text = await response.text()
  let payload: unknown
  try {
    payload = text ? JSON.parse(text) : {}
  } catch {
    throw new ApiError('INTERNAL_ERROR', '服务端返回的内容无法识别。', response.headers.get('X-Trace-Id') ?? '')
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new ApiError('INTERNAL_ERROR', '服务端返回的数据格式不对。', response.headers.get('X-Trace-Id') ?? '')
  }

  const data = payload as Record<string, unknown>
  // GET /api/gateway 成功时也带 error（探测原因），因此只按 success === false 判错。
  if (response.status >= 400 || data.success === false) {
    throw new ApiError(
      String(data.error ?? fallbackCode(response.status)),
      String(data.message ?? ''),
      String(data.trace_id ?? response.headers.get('X-Trace-Id') ?? ''),
    )
  }
  return payload as T
}

function fallbackCode(status: number): string {
  if (status === 401) return 'UNAUTHORIZED'
  if (status === 404) return 'NOT_FOUND'
  if (status === 409) return 'CONFLICT'
  if (status === 503) return 'DEVICE_OFFLINE'
  return 'INTERNAL_ERROR'
}

// ---------------------------------------------------------------- 只读

export function fetchState(): Promise<AppState> {
  return request<AppState>('GET', '/api/state')
}

export function fetchDevices(room?: string): Promise<ListDevicesPayload> {
  return request<ListDevicesPayload>('GET', '/api/devices', { params: { room } })
}

export function fetchDevice(deviceId: string): Promise<Device> {
  return request<Device>('GET', `/api/devices/${encodeURIComponent(deviceId)}`)
}

export function fetchRooms(): Promise<ListRoomsPayload> {
  return request<ListRoomsPayload>('GET', '/api/rooms')
}

export function fetchGateway(refresh = false): Promise<GatewayStatus> {
  return request<GatewayStatus>('GET', '/api/gateway', {
    params: refresh ? { refresh: 'true' } : undefined,
  })
}

export function fetchAudit(params: {
  limit?: number
  result?: 'success' | 'failed' | 'all'
  deviceId?: string
}): Promise<ListAuditPayload> {
  return request<ListAuditPayload>('GET', '/api/audit', {
    params: {
      limit: params.limit ?? 50,
      result: params.result,
      device_id: params.deviceId,
    },
  })
}

// ---------------------------------------------------------------- 写入

export function createDevice(body: {
  name: string
  room: string
  notes?: string
}): Promise<Device> {
  return request<Device>('POST', '/api/devices', { body })
}

export function patchDevice(
  deviceId: string,
  body: { name?: string; room?: string; notes?: string },
): Promise<Device> {
  return request<Device>('PATCH', `/api/devices/${encodeURIComponent(deviceId)}`, { body })
}

export function deleteDevice(deviceId: string): Promise<Record<string, unknown>> {
  return request('DELETE', `/api/devices/${encodeURIComponent(deviceId)}`)
}

export function deleteFunction(
  deviceId: string,
  functionId: string,
): Promise<{ deleted: { function_id: string; codes: number } }> {
  return request(
    'DELETE',
    `/api/devices/${encodeURIComponent(deviceId)}/functions/${encodeURIComponent(functionId)}`,
  )
}

export function createRoom(body: {
  name: string
  display_name: string
  sort_order?: number
}): Promise<{ room: Room }> {
  return request('POST', '/api/rooms', { body })
}

export function patchRoom(
  name: string,
  body: { display_name?: string; sort_order?: number },
): Promise<{ room: Room }> {
  return request('PATCH', `/api/rooms/${encodeURIComponent(name)}`, { body })
}

export function deleteRoom(name: string): Promise<Record<string, unknown>> {
  return request('DELETE', `/api/rooms/${encodeURIComponent(name)}`)
}

export function control(functionId: string): Promise<{ success: true; result: ControlResult; trace_id: string }> {
  return request('POST', '/api/control', { body: { function_id: functionId } })
}

export function correct(body: {
  device_id: string
  state: string
  reason?: string
}): Promise<{ success: true } & CorrectResult> {
  return request('POST', '/api/correct', { body })
}

// ---------------------------------------------------------------- 场景（Agent 契约，结果在 result 里）

export function fetchScenes(): Promise<SceneListPayload> {
  return request<SceneListPayload>('GET', '/api/agent/scenes')
}

export function fetchCapabilities(): Promise<CapabilityListPayload> {
  return request<CapabilityListPayload>('GET', '/api/agent/capabilities')
}

export function createScene(body: {
  name: string
  enabled: boolean
  action_groups: SceneActionGroupInput[]
  triggers: SceneTriggerInput[]
}): Promise<ScenePayload> {
  return request<ScenePayload>('POST', '/api/agent/scenes', { body })
}

export function patchScene(
  sceneId: string,
  body: {
    name?: string
    enabled?: boolean
    action_groups?: SceneActionGroupInput[]
    triggers?: SceneTriggerInput[]
  },
): Promise<ScenePayload> {
  return request<ScenePayload>('PATCH', `/api/agent/scenes/${encodeURIComponent(sceneId)}`, {
    body,
  })
}

export function deleteScene(sceneId: string): Promise<Record<string, unknown>> {
  return request('DELETE', `/api/agent/scenes/${encodeURIComponent(sceneId)}`)
}

export function runScene(sceneId: string, confirm: boolean): Promise<SceneRunPayload> {
  return request<SceneRunPayload>('POST', `/api/agent/scenes/${encodeURIComponent(sceneId)}/run`, {
    body: { confirm },
  })
}

export function fetchSceneRuns(sceneId: string, limit = 5): Promise<SceneRunsPayload> {
  return request<SceneRunsPayload>(
    'GET',
    `/api/agent/scenes/${encodeURIComponent(sceneId)}/runs`,
    { params: { limit } },
  )
}

export function fetchSchedules(): Promise<ScheduleListPayload> {
  return request<ScheduleListPayload>('GET', '/api/agent/schedules')
}

// ---------------------------------------------------------------- 管家对话（后端代理 agentd）

export function sendAgentChat(body: {
  message: string
  session_id?: string
}): Promise<AgentChatPayload> {
  return request<AgentChatPayload>('POST', '/api/agent/chat', {
    body,
    timeoutMs: CHAT_TIMEOUT_MS,
  })
}

// ---------------------------------------------------------------- 学码

export function learnStart(deviceId: string): Promise<{ success: true; device_id: string; message: string; trace_id: string }> {
  return request('POST', '/api/learn/start', { body: { device_id: deviceId } })
}

export function learnCapture(deviceId: string): Promise<CaptureResult> {
  return request<CaptureResult>('GET', '/api/learn/capture', { params: { device_id: deviceId } })
}

export function learnSave(
  deviceId: string,
  name: string,
  rawCode: number[],
): Promise<{ success: true } & LearnSaveResult> {
  return request('POST', '/api/learn/save', {
    body: { device_id: deviceId, name, raw_code: rawCode },
  })
}

export function learnCancel(deviceId: string): Promise<{ success: true } & LearnCancelResult> {
  return request('POST', '/api/learn/cancel', { body: { device_id: deviceId } })
}

/**
 * 页面即将卸载（刷新 / 关闭标签）时的取消：`keepalive` 让请求在文档销毁后仍能完成。
 * 只发请求、不等待结果；服务端 TTL 仍会兜底。
 */
export function learnCancelOnPageHide(deviceId: string): void {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'X-Trace-Id': newTraceId(),
  }
  const token = currentToken()
  if (token) headers.Authorization = `Bearer ${token}`
  void fetch(`${baseUrl}/api/learn/cancel`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ device_id: deviceId }),
    keepalive: true,
  }).catch(() => {
    // 卸载阶段无法处理失败；会话由服务端 TTL 回收。
  })
}
