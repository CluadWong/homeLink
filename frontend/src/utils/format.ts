/** 展示层小工具：时间转本地、状态截断、房间 name 派生。 */

const STATE_MAX_CHARS = 12

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function truncateState(value: string): string {
  const chars = Array.from(value)
  if (chars.length <= STATE_MAX_CHARS) return value
  return `${chars.slice(0, STATE_MAX_CHARS).join('')}…`
}

/** 状态文案：unknown 永远不是「关」；非标准 state 原样显示，不做中英文映射。 */
export function stateText(state: string, source: 'unknown' | 'confirmed'): string {
  if (state === 'unknown' || source === 'unknown') return '未知'
  return truncateState(state)
}

/** 生成一个不会与现有房间冲突的内部 name；用户只看到 display_name。 */
export function deriveRoomName(displayName: string, existing: string[]): string {
  const base =
    displayName
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 24) || 'room'
  const taken = new Set(existing)
  if (!taken.has(base) && base.length >= 2) return base
  let candidate = base
  for (let index = 2; index < 100; index += 1) {
    candidate = `${base}_${index}`
    if (!taken.has(candidate)) return candidate
  }
  return `${base}_${Date.now().toString(36)}`
}

export function elapsedText(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

export function sortOrderValue(input: string): number {
  const parsed = Number.parseInt(input, 10)
  return Number.isFinite(parsed) ? parsed : 0
}
