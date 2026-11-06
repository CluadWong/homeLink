/**
 * 错误码 → 人话 + 下一步（docs/frontend-contract.md §4）。
 * 界面不出现裸错误码与堆栈；trace 默认折叠，只有排查类错误才展示。
 */

export const AGENT_UNREACHABLE = 'AGENT_UNREACHABLE'

export class ApiError extends Error {
  readonly code: string
  readonly serverMessage: string
  readonly traceId: string

  constructor(code: string, serverMessage = '', traceId = '') {
    super(serverMessage || code)
    this.name = 'ApiError'
    this.code = code
    this.serverMessage = serverMessage
    this.traceId = traceId
  }
}

export interface Friendly {
  /** 一句话：发生了什么 */
  title: string
  /** 下一步做什么 */
  next: string
  /** trace_id 是否默认展开（排查类错误才展示） */
  showTrace: boolean
}

const MATRIX: Record<string, Friendly> = {
  UNAUTHORIZED: {
    title: '鉴权失败',
    next: '检查部署注入的 token（VITE_API_TOKEN）；未注入时在设置里填写，仅本次标签页有效',
    showTrace: false,
  },
  INVALID_REQUEST: {
    title: '请求字段有问题',
    next: '检查是否缺字段，或带了不认识的字段',
    showTrace: false,
  },
  NOT_FOUND: {
    title: '找不到设备或功能',
    next: '刷新设备列表，确认它是否已被删除',
    showTrace: false,
  },
  CONFLICT: {
    title: '状态冲突',
    next: '刷新后重试',
    showTrace: false,
  },
  DEVICE_OFFLINE: {
    title: '设备没有响应',
    next: '去「状态」页看网关：确认板子已上电、连到 Wi-Fi、网关地址正确',
    showTrace: true,
  },
  DEVICE_UNAUTHORIZED: {
    title: '板子拒绝访问',
    next: '板子在线但鉴权没通过：核对网关用户名密码与板子配置',
    showTrace: true,
  },
  INTERNAL_ERROR: {
    title: '管家内部出错',
    next: '请把这一刻的排查编号报告给我们',
    showTrace: true,
  },
  // 前端自造：请求没有到达管家后端，和「网关离线」是两回事
  [AGENT_UNREACHABLE]: {
    title: '管家服务连不上',
    next: '确认后端已启动、服务地址正确，再刷新页面',
    showTrace: true,
  },
}

const UNKNOWN: Friendly = {
  title: '出了点问题',
  next: '稍后重试；若反复出现请报告',
  showTrace: true,
}

export function friendly(code: string, serverMessage = ''): Friendly {
  if (code === AGENT_UNREACHABLE) {
    const base = MATRIX[AGENT_UNREACHABLE]
    if (serverMessage.includes('超时') || serverMessage.includes('及时返回')) {
      return {
        title: '管家服务响应超时',
        next: '服务可能在忙或网络很慢；稍后重试，持续超时请检查后端日志',
        showTrace: true,
      }
    }
    return base
  }
  const base = MATRIX[code] ?? UNKNOWN
  if (!serverMessage || serverMessage === base.title || serverMessage === code) {
    return base
  }
  return { ...base, title: `${base.title}：${serverMessage}` }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (error instanceof Error) {
    return new ApiError(AGENT_UNREACHABLE, error.message)
  }
  return new ApiError('INTERNAL_ERROR', String(error))
}

export function isServiceUnreachable(error: unknown): boolean {
  return toApiError(error).code === AGENT_UNREACHABLE
}

export function isDeviceSide(error: unknown): boolean {
  const code = toApiError(error).code
  return code === 'DEVICE_OFFLINE' || code === 'DEVICE_UNAUTHORIZED'
}
