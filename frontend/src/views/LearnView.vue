<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { showFailToast, showToast } from 'vant'

import {
  createDevice,
  fetchRooms,
  learnCancel,
  learnCancelOnPageHide,
  learnCapture,
  learnSave,
  learnStart,
} from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { CaptureResult, LearnSaveResult, Room } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { refreshState } from '../composables/useStatusSummary'
import { elapsedText } from '../utils/format'

type Phase = 'idle' | 'starting' | 'waiting' | 'captured' | 'done' | 'ended'

const CLIENT_TIMEOUT_SECONDS = 600
const router = useRouter()

const rooms = ref<Room[]>([])
const roomsLoading = ref(true)
const roomsError = ref<ApiError | null>(null)
const roomPickerOpen = ref(false)

const draftName = ref('')
const draftRoom = ref('')
const phase = ref<Phase>('idle')
const actionError = ref<ApiError | null>(null)

const draftDeviceId = ref<string | null>(null)
const capture = ref<CaptureResult | null>(null)
const capturedCode = ref<number[] | null>(null)
const functionName = ref('')
const savedResult = ref<LearnSaveResult | null>(null)
const elapsedSeconds = ref(0)
const endedMessage = ref('')
const saving = ref(false)

let pollTimer: number | undefined
let tickTimer: number | undefined
let startedAt = 0
let pollInFlight = false
let disposed = false

const roomLabel = computed(
  () => rooms.value.find((room) => room.name === draftRoom.value)?.display_name ?? '请选择房间',
)

const roomsOffline = computed(() => roomsError.value?.code === 'AGENT_UNREACHABLE')

const reasonGuidance = computed(() => {
  const reason = capture.value?.reason
  if (!reason) return ''
  if (reason === 'no_signal') return '还没收到，请再按一次遥控器（对准接收头，保持片刻）。'
  if (reason === 'malformed') return '收到的波形无法解析，请重新按一次。'
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

async function loadRooms(): Promise<void> {
  roomsLoading.value = true
  roomsError.value = null
  try {
    const payload = await fetchRooms()
    rooms.value = payload.rooms
    if (!draftRoom.value && rooms.value.length > 0) draftRoom.value = rooms.value[0].name
  } catch (error) {
    roomsError.value = toApiError(error)
  } finally {
    roomsLoading.value = false
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
  const name = draftName.value.trim()
  if (!name) {
    showFailToast('先给这台设备起个名字')
    return
  }
  if (!draftRoom.value) {
    showFailToast('请选择房间')
    return
  }

  stopTimers()
  phase.value = 'starting'
  actionError.value = null
  savedResult.value = null
  capture.value = null
  capturedCode.value = null
  functionName.value = ''
  endedMessage.value = ''

  let deviceId: string | null = null
  try {
    const device = await createDevice({
      name,
      room: draftRoom.value,
      learn_draft: true,
    })
    deviceId = device.id
    draftDeviceId.value = deviceId
    await learnStart(deviceId)
    if (disposed) {
      draftDeviceId.value = null
      await cancelQuietly(deviceId)
      return
    }
    startedAt = Date.now()
    elapsedSeconds.value = 0
    phase.value = 'waiting'
    startTick()
    startPolling()
  } catch (error) {
    actionError.value = toApiError(error)
    if (deviceId) await cancelQuietly(deviceId)
    draftDeviceId.value = null
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
  const deviceId = draftDeviceId.value
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
      draftDeviceId.value = null
      phase.value = 'idle'
      return
    }
    if (apiError.code === 'NOT_FOUND') {
      // 未 start、已 save/cancel 或服务端 10 分钟 TTL 回收
      draftDeviceId.value = null
      endedMessage.value = '本次学码已结束（会话不存在或已超时），请重新开始。'
      phase.value = 'ended'
      return
    }
    actionError.value = apiError
    await cancelQuietly(deviceId)
    draftDeviceId.value = null
    phase.value = 'idle'
  } finally {
    pollInFlight = false
  }
}

async function endByTimeout(): Promise<void> {
  if (phase.value !== 'waiting') return
  const deviceId = draftDeviceId.value
  stopTimers()
  draftDeviceId.value = null
  endedMessage.value = '本次学码已结束（超过 10 分钟未完成），请重试。'
  phase.value = 'ended'
  if (deviceId) await cancelQuietly(deviceId)
}

async function saveFunction(): Promise<void> {
  const deviceId = draftDeviceId.value
  const rawCode = capturedCode.value
  const name = functionName.value.trim()
  if (!deviceId || !rawCode) {
    showFailToast('还没有捕获到波形')
    return
  }
  if (!name) {
    showFailToast('给这个功能起个名字，例如「开机」')
    return
  }

  phase.value = 'captured'
  saving.value = true
  actionError.value = null
  try {
    const result = await learnSave(deviceId, name, rawCode)
    savedResult.value = result
    draftDeviceId.value = null
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
      draftDeviceId.value = null
      capture.value = null
      endedMessage.value = '学码会话已结束，本次没有保存成功，请重新开始。'
      phase.value = 'ended'
    }
  } finally {
    saving.value = false
  }
}

async function cancelCurrent(): Promise<void> {
  const deviceId = draftDeviceId.value
  stopTimers()
  capture.value = null
  capturedCode.value = null
  draftDeviceId.value = null
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
  actionError.value = null
  endedMessage.value = ''
  elapsedSeconds.value = 0
  draftDeviceId.value = null
}

function goCall(): void {
  void router.push('/call')
}

function leaveLearnPage(): void {
  disposed = true
  const deviceId = draftDeviceId.value
  stopTimers()
  draftDeviceId.value = null
  if (deviceId && !canLeaveToCall.value) {
    void cancelQuietly(deviceId)
  }
}

/** 刷新 / 关闭标签不属于路由离开，Vue 不会卸载组件；用 pagehide + keepalive 补上取消。 */
function cancelOnPageHide(): void {
  if (draftDeviceId.value) learnCancelOnPageHide(draftDeviceId.value)
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
  void loadRooms()
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="03"
      title="学码"
      description="先创建草稿设备，再开会话接收红外。接收期间每秒查询一次；离开本页会自动取消并撤掉空草稿。"
    />

    <LoadingBlock v-if="roomsLoading" text="正在读取房间" />

    <template v-else>
      <ErrorBanner :error="roomsError" show-retry @retry="loadRooms" />
      <ErrorBanner :error="actionError" />

      <div v-if="roomsOffline" class="offline-panel">
        <p class="offline-note">管家服务连不上，无法创建草稿设备或开启学码。</p>
      </div>

      <template v-if="!roomsOffline && phase === 'idle'">
        <EmptyBlock
          v-if="!roomsError && rooms.length === 0"
          title="还没有房间"
          note="学码前要先有房间与设备名。先去设备页建一个房间。"
        >
          <RouterLink class="action-link" to="/devices">去设备页</RouterLink>
        </EmptyBlock>

        <div v-else class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">第一步：建设备并开始接收</h3>
              <p class="card-meta">草稿设备在保存成功后才转正；失败、取消、超时都会撤掉空壳。</p>
            </div>
          </header>

          <van-field
            v-model="draftName"
            label="设备名"
            placeholder="例如：客厅空调"
            maxlength="40"
            required
          />
          <button class="picker-trigger" type="button" @click="roomPickerOpen = true">
            <span>房间</span>
            <span class="picker-value">{{ roomLabel }}</span>
          </button>

          <div class="sheet-actions">
            <button class="primary-btn" type="button" @click="beginLearn">开始学码</button>
          </div>
          <p class="card-hint">
            开始后请对准接收头，按下遥控器上要学习的那一个键；界面每秒查询一次，捕获成功后再命名保存。
          </p>
        </div>
      </template>

      <LoadingBlock v-else-if="phase === 'starting'" text="正在创建草稿设备并开启接收" />

      <div v-else-if="phase === 'waiting'" class="card learn-live">
        <div class="live-head">
          <span class="live-dot"></span>
          <div>
            <h3 class="card-title">请对准接收头按下遥控器</h3>
            <p class="card-meta">设备「{{ draftName.trim() }}」· {{ roomLabel }}</p>
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
            <h3 class="card-title">已捕获 {{ capturedCode?.length ?? 0 }} 段波形</h3>
            <p class="card-meta">请给这个功能命名后保存；保存成功后草稿设备转为普通设备。</p>
          </div>
          <span class="count-tag">已捕获</span>
        </header>

        <van-field
          v-model="functionName"
          label="功能名"
          placeholder="例如：开机 / 调温"
          maxlength="40"
          required
        />
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
          功能已保存，但接收状态没有关闭：{{ savedResult.receiver_error }}。请到状态页检查网关；
          重复学码前先取消一次。
        </p>
        <div class="sheet-actions">
          <button class="primary-btn" type="button" @click="goCall">去调用</button>
          <button class="ghost-btn" type="button" @click="resetForAnother">再学一个</button>
        </div>
      </div>

      <div v-else-if="phase === 'ended'" class="card ended-card">
        <span class="ended-mark">∅</span>
        <h3 class="card-title">学码已结束</h3>
        <p class="done-line">{{ endedMessage }}</p>
        <p class="card-hint">没有捕获到波形时不会留下任何空壳设备或半条码。</p>
        <div class="sheet-actions">
          <button class="primary-btn" type="button" @click="resetForAnother">重新开始</button>
        </div>
      </div>
    </template>

    <van-popup v-model:show="roomPickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>选择房间</h3>
          <button class="text-btn" type="button" @click="roomPickerOpen = false">关闭</button>
        </header>
        <button
          v-for="room in rooms"
          :key="room.name"
          class="picker-row"
          :class="{ 'is-selected': draftRoom === room.name }"
          type="button"
          @click="draftRoom = room.name; roomPickerOpen = false"
        >
          <span>{{ room.display_name }}</span>
          <span class="picker-sub">{{ room.device_count }} 台设备</span>
        </button>
      </div>
    </van-popup>
  </section>
</template>
