<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { showFailToast, showToast } from 'vant'

import {
  control,
  fetchDevices,
  fetchRooms,
  learnCancel,
  learnCancelOnPageHide,
  learnCapture,
  learnSave,
  learnStart,
} from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { CaptureResult, Device, LearnSaveResult, Room } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { refreshState } from '../composables/useStatusSummary'
import { elapsedText } from '../utils/format'

type Phase = 'idle' | 'starting' | 'waiting' | 'captured' | 'done' | 'ended'
type ReplayState = 'idle' | 'running' | 'sent' | 'failed'

const CLIENT_TIMEOUT_SECONDS = 600
/** 与后端 LearnSaveRequest.name 的 40 字符上限保持一致。 */
const FUNCTION_NAME_MAX_LENGTH = 40
const router = useRouter()
const route = useRoute()

const devices = ref<Device[]>([])
const rooms = ref<Room[]>([])
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const devicePickerOpen = ref(false)
const selectedDeviceId = ref('')

const phase = ref<Phase>('idle')
const actionError = ref<ApiError | null>(null)

const activeDeviceId = ref<string | null>(null)
const capture = ref<CaptureResult | null>(null)
const capturedCode = ref<number[] | null>(null)
const functionName = ref('')
const savedResult = ref<LearnSaveResult | null>(null)
const elapsedSeconds = ref(0)
const endedMessage = ref('')
const saving = ref(false)
const replayState = ref<ReplayState>('idle')
const replayError = ref<ApiError | null>(null)

let pollTimer: number | undefined
let tickTimer: number | undefined
let startedAt = 0
let pollInFlight = false
let disposed = false

const selectedDevice = computed(
  () => devices.value.find((device) => device.id === selectedDeviceId.value) ?? null,
)

function roomLabelFor(roomName: string): string {
  return rooms.value.find((room) => room.name === roomName)?.display_name ?? roomName
}

const selectedDeviceLabel = computed(() => {
  const device = selectedDevice.value
  if (!device) return '请选择设备'
  return `${device.name} · ${roomLabelFor(device.room)}`
})

const serviceOffline = computed(() => loadError.value?.code === 'AGENT_UNREACHABLE')

const reasonGuidance = computed(() => {
  const reason = capture.value?.reason
  if (!reason) return ''
  if (reason === 'no_signal') return '还没收到，请再按一次遥控器（对准接收头，保持片刻）。'
  if (reason === 'malformed') {
    return capture.value?.message || '收到的波形无法解析，请重新按一次。'
  }
  return '网关连接不上：去「状态」页确认网关在线，轮询仍在继续。'
})

const canLeaveToCall = computed(() => phase.value === 'done')

function stopTimers(): void {
  if (pollTimer) window.clearInterval(pollTimer)
  if (tickTimer) window.clearInterval(tickTimer)
  pollTimer = undefined
  tickTimer = undefined
}

function startTick(): void {
  if (tickTimer) window.clearInterval(tickTimer)
  tickTimer = window.setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
    if (elapsedSeconds.value >= CLIENT_TIMEOUT_SECONDS) {
      void endByTimeout()
    }
  }, 1000)
}

async function loadDevices(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const [devicePayload, roomPayload] = await Promise.all([fetchDevices(), fetchRooms()])
    devices.value = devicePayload.devices
    rooms.value = roomPayload.rooms
    applyPreselection()
  } catch (error) {
    loadError.value = toApiError(error)
    devices.value = []
    rooms.value = []
  } finally {
    loading.value = false
  }
}

/** 设备页「去学码」带 `?device_id=` 跳转过来时自动预选那台设备。 */
function applyPreselection(): void {
  const fromQuery = typeof route.query.device_id === 'string' ? route.query.device_id : ''
  if (fromQuery && devices.value.some((device) => device.id === fromQuery)) {
    selectedDeviceId.value = fromQuery
    return
  }
  if (!devices.value.some((device) => device.id === selectedDeviceId.value)) {
    selectedDeviceId.value = ''
  }
}

async function cancelQuietly(deviceId: string): Promise<void> {
  try {
    await learnCancel(deviceId)
  } catch {
    // 清理失败不覆盖主错误；离开页面时也只能尽力而为。
  }
}

async function beginLearn(): Promise<void> {
  const device = selectedDevice.value
  if (!device) {
    showFailToast('请先选择一台设备')
    return
  }
  const targetName = functionName.value.trim()
  if (!targetName) {
    showFailToast('先给要学的功能起个名字，例如「开机」')
    return
  }
  if (targetName.length > FUNCTION_NAME_MAX_LENGTH) {
    showFailToast(`功能名最多 ${FUNCTION_NAME_MAX_LENGTH} 个字符`)
    return
  }

  stopTimers()
  phase.value = 'starting'
  actionError.value = null
  savedResult.value = null
  replayState.value = 'idle'
  replayError.value = null
  capture.value = null
  capturedCode.value = null
  endedMessage.value = ''

  try {
    await learnStart(device.id)
    if (disposed) {
      await cancelQuietly(device.id)
      return
    }
    activeDeviceId.value = device.id
    startedAt = Date.now()
    elapsedSeconds.value = 0
    phase.value = 'waiting'
    startTick()
    startPolling()
  } catch (error) {
    actionError.value = toApiError(error)
    await cancelQuietly(device.id)
    activeDeviceId.value = null
    phase.value = 'idle'
  }
}

function startPolling(): void {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = window.setInterval(() => {
    void pollOnce()
  }, 1000)
  void pollOnce()
}

async function pollOnce(): Promise<void> {
  const deviceId = activeDeviceId.value
  if (phase.value !== 'waiting' || !deviceId || pollInFlight) return
  pollInFlight = true
  try {
    const result = await learnCapture(deviceId)
    if (phase.value !== 'waiting') return
    capture.value = result
    if (result.captured && result.raw_code && result.raw_code.length > 0) {
      capturedCode.value = result.raw_code
      elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
      stopTimers()
      phase.value = 'captured'
    }
  } catch (error) {
    const apiError = toApiError(error)
    if (phase.value !== 'waiting') return
    stopTimers()
    if (apiError.code === 'DEVICE_UNAUTHORIZED') {
      actionError.value = apiError
      await cancelQuietly(deviceId)
      activeDeviceId.value = null
      phase.value = 'idle'
      return
    }
    if (apiError.code === 'NOT_FOUND') {
      // 未 start、已 save/cancel 或服务端 10 分钟 TTL 回收
      activeDeviceId.value = null
      endedMessage.value = '本次学码已结束（会话不存在或已超时），请重新开始。'
      phase.value = 'ended'
      return
    }
    actionError.value = apiError
    await cancelQuietly(deviceId)
    activeDeviceId.value = null
    phase.value = 'idle'
  } finally {
    pollInFlight = false
  }
}

async function endByTimeout(): Promise<void> {
  if (phase.value !== 'waiting') return
  const deviceId = activeDeviceId.value
  stopTimers()
  activeDeviceId.value = null
  endedMessage.value = '本次学码已结束（超过 10 分钟未完成），请重试。'
  phase.value = 'ended'
  if (deviceId) await cancelQuietly(deviceId)
}

async function saveFunction(): Promise<void> {
  const deviceId = activeDeviceId.value
  const rawCode = capturedCode.value
  const name = functionName.value.trim()
  if (!deviceId || !rawCode) {
    showFailToast('还没有捕获到波形')
    return
  }
  if (!name) {
    showFailToast('功能名不能为空')
    return
  }

  phase.value = 'captured'
  saving.value = true
  actionError.value = null
  try {
    const result = await learnSave(deviceId, name, rawCode)
    savedResult.value = result
    replayState.value = 'idle'
    replayError.value = null
    activeDeviceId.value = null
    capture.value = null
    stopTimers()
    phase.value = 'done'
    showToast({ message: '功能已保存', duration: 1500 })
    await refreshState()
  } catch (error) {
    const apiError = toApiError(error)
    actionError.value = apiError
    if (apiError.code === 'NOT_FOUND') {
      await cancelQuietly(deviceId)
      activeDeviceId.value = null
      capture.value = null
      endedMessage.value = '学码会话已结束，本次没有保存成功，请重新开始。'
      phase.value = 'ended'
    }
  } finally {
    saving.value = false
  }
}

/** 保存成功后手动试一次：真实模式下这就是一次真实红外发送，绝不自动触发。 */
async function tryOnce(): Promise<void> {
  const result = savedResult.value
  if (!result || replayState.value === 'running') return
  replayState.value = 'running'
  replayError.value = null
  try {
    await control(result.function_id)
    replayState.value = 'sent'
    showToast({ message: '已发送', duration: 1600 })
    await refreshState()
  } catch (error) {
    replayError.value = toApiError(error)
    replayState.value = 'failed'
  }
}

async function cancelCurrent(): Promise<void> {
  const deviceId = activeDeviceId.value
  stopTimers()
  capture.value = null
  capturedCode.value = null
  activeDeviceId.value = null
  phase.value = 'idle'
  actionError.value = null
  if (!deviceId) return
  try {
    const result = await learnCancel(deviceId)
    showToast({ message: result.message, duration: 2000 })
    await refreshState()
  } catch (error) {
    actionError.value = toApiError(error)
  }
}

function resetForAnother(): void {
  stopTimers()
  phase.value = 'idle'
  capture.value = null
  capturedCode.value = null
  functionName.value = ''
  savedResult.value = null
  replayState.value = 'idle'
  replayError.value = null
  actionError.value = null
  endedMessage.value = ''
  elapsedSeconds.value = 0
  activeDeviceId.value = null
}

function goCall(): void {
  void router.push('/call')
}

function leaveLearnPage(): void {
  disposed = true
  const deviceId = activeDeviceId.value
  stopTimers()
  activeDeviceId.value = null
  if (deviceId && !canLeaveToCall.value) {
    void cancelQuietly(deviceId)
  }
}

/** 刷新 / 关闭标签不属于路由离开，Vue 不会卸载组件；用 pagehide + keepalive 补上取消。 */
function cancelOnPageHide(): void {
  if (activeDeviceId.value) learnCancelOnPageHide(activeDeviceId.value)
}

onBeforeRouteLeave(() => {
  leaveLearnPage()
})

onBeforeUnmount(() => {
  window.removeEventListener('pagehide', cancelOnPageHide)
  leaveLearnPage()
})

onMounted(() => {
  window.addEventListener('pagehide', cancelOnPageHide)
  void loadDevices()
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="03"
      title="学码"
      description="三步：① 选择已有设备并给功能起名 → ② 开始接收、按遥控器 → ③ 保存后试一次。接收期间每秒查询一次；离开本页会自动取消学码。"
    />

    <LoadingBlock v-if="loading" text="正在读取设备" />

    <template v-else>
      <ErrorBanner :error="loadError" show-retry @retry="loadDevices" />
      <ErrorBanner :error="actionError" />

      <div v-if="serviceOffline" class="offline-panel">
        <p class="offline-note">管家服务连不上，无法开启学码。</p>
      </div>

      <template v-if="!serviceOffline && phase === 'idle'">
        <EmptyBlock
          v-if="!loadError && devices.length === 0"
          title="还没有设备"
          note="学码前要先有一台设备。先去设备页添加设备，再回来给它学一个功能。"
        >
          <RouterLink class="action-link" to="/devices">去设备页添加</RouterLink>
        </EmptyBlock>

        <div v-else class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">第一步：选设备、定功能名</h3>
              <p class="card-meta">从已有设备里选一台；功能名在这里定好，保存时直接使用。</p>
            </div>
          </header>

          <button class="picker-trigger" type="button" @click="devicePickerOpen = true">
            <span>设备</span>
            <span class="picker-value">{{ selectedDeviceLabel }}</span>
          </button>
          <van-field
            v-model="functionName"
            label="功能名"
            placeholder="例如：开机"
            maxlength="40"
            required
          />

          <div class="sheet-actions">
            <button class="primary-btn" type="button" @click="beginLearn">开始学码</button>
          </div>
          <p class="card-hint">
            开始后请对准接收头，按下遥控器上要学习的那一个键；界面每秒查询一次。
          </p>
        </div>
      </template>

      <LoadingBlock v-else-if="phase === 'starting'" text="正在开启接收" />

      <div v-else-if="phase === 'waiting'" class="card learn-live">
        <div class="live-head">
          <span class="live-dot"></span>
          <div>
            <h3 class="card-title">第二步：请对准接收头按下遥控器</h3>
            <p class="card-meta">
              设备「{{ selectedDevice?.name }}」· 功能「{{ functionName.trim() }}」·
              {{ selectedDevice ? roomLabelFor(selectedDevice.room) : '' }}
            </p>
          </div>
          <span class="live-clock mono">{{ elapsedText(elapsedSeconds) }}</span>
        </div>

        <p class="live-message">{{ capture?.message || '正在等待红外信号…' }}</p>
        <p v-if="reasonGuidance" class="live-guidance">{{ reasonGuidance }}</p>
        <p v-if="elapsedSeconds >= 30" class="live-hint">
          迟迟没有捕获时，确认遥控器电量、接收头朝向；10 分钟后会话会自动结束。
        </p>

        <div class="sheet-actions">
          <button class="ghost-btn" type="button" @click="cancelCurrent">取消学码</button>
        </div>
      </div>

      <div v-else-if="phase === 'captured'" class="card">
        <header class="card-head">
          <div>
            <h3 class="card-title">第三步：保存「{{ functionName.trim() }}」</h3>
            <p class="card-meta">
              已捕获 {{ capturedCode?.length ?? 0 }} 段波形；保存后可在功能列表里直接调用。
            </p>
          </div>
          <span class="count-tag">已捕获</span>
        </header>

        <p class="card-hint">功能列表只展示你起的名字，界面不推断这个按键具体是什么动作。</p>

        <div class="sheet-actions">
          <button class="primary-btn" type="button" :disabled="saving" @click="saveFunction">
            {{ saving ? '保存中…' : '保存功能' }}
          </button>
          <button class="ghost-btn" type="button" :disabled="saving" @click="cancelCurrent">
            取消
          </button>
        </div>
      </div>

      <div v-else-if="phase === 'done'" class="card done-card">
        <span class="done-mark">✓</span>
        <h3 class="card-title">功能已保存</h3>
        <p class="done-line">{{ savedResult?.name }} · {{ savedResult?.function_id }}</p>
        <p v-if="savedResult && !savedResult.receiver_stopped" class="warning-line">
          功能已保存，本次学码会话已结束，但接收状态没有关闭：{{ savedResult.receiver_error }}。
          请到状态页检查网关；板子恢复后直接点「再学一个」重新开始即可，重新开始会再次下发开启学习模式。
        </p>

        <div class="replay-block">
          <p class="card-hint">
            「试一次」会真的发一次红外信号（mock 模式下只记录波形、不触发真实设备）；
            只在你点击时执行，不会自动发送。
          </p>
          <div class="sheet-actions">
            <button
              class="primary-btn"
              type="button"
              :disabled="replayState === 'running'"
              @click="tryOnce"
            >
              {{ replayState === 'running' ? '发送中…' : '试一次' }}
            </button>
            <button class="ghost-btn" type="button" @click="goCall">去调用</button>
            <button class="ghost-btn" type="button" @click="resetForAnother">再学一个</button>
          </div>

          <div v-if="replayState === 'sent'" class="sent-strip is-light" role="status">
            <strong>已发送</strong>
            <span>{{ savedResult?.name }}</span>
            <span class="sent-note">红外是单向的，无法确认设备是否执行</span>
          </div>
          <template v-if="replayState === 'failed' && replayError">
            <ErrorBanner :error="replayError" />
            <p class="replay-code">错误码 <span class="mono">{{ replayError.code }}</span></p>
          </template>
        </div>
      </div>

      <div v-else-if="phase === 'ended'" class="card ended-card">
        <span class="ended-mark">∅</span>
        <h3 class="card-title">学码已结束</h3>
        <p class="done-line">{{ endedMessage }}</p>
        <p class="card-hint">没有捕获到波形时不会写入任何功能或红外码。</p>
        <div class="sheet-actions">
          <button class="primary-btn" type="button" @click="resetForAnother">重新开始</button>
        </div>
      </div>
    </template>

    <van-popup v-model:show="devicePickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>选择设备</h3>
          <button class="text-btn" type="button" @click="devicePickerOpen = false">关闭</button>
        </header>
        <button
          v-for="device in devices"
          :key="device.id"
          class="picker-row"
          :class="{ 'is-selected': selectedDeviceId === device.id }"
          type="button"
          @click="selectedDeviceId = device.id; devicePickerOpen = false"
        >
          <span>{{ device.name }}</span>
          <span class="picker-sub">{{ roomLabelFor(device.room) }}</span>
        </button>
      </div>
    </van-popup>
  </section>
</template>
