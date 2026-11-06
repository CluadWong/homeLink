<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchAudit, fetchDevices } from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { AuditRecord, Device } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { formatTime } from '../utils/format'

type ResultFilter = 'all' | 'success' | 'failed'

const records = ref<AuditRecord[]>([])
const devices = ref<Device[]>([])
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const resultFilter = ref<ResultFilter>('all')
const deviceFilter = ref('')
const limit = ref(50)
const devicePickerOpen = ref(false)

const deviceNames = computed(() => {
  const map: Record<string, string> = {}
  for (const device of devices.value) map[device.id] = device.name
  return map
})

const deviceFilterLabel = computed(() => {
  if (!deviceFilter.value) return '全部设备'
  return deviceNames.value[deviceFilter.value] ?? deviceFilter.value
})

const serviceOffline = computed(() => loadError.value?.code === 'AGENT_UNREACHABLE')

const resultOptions: Array<{ value: ResultFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
]

const limitOptions = [50, 100, 200]

function actionLabel(action: string): string {
  if (action.endsWith('.control')) return '控制'
  const known: Record<string, string> = {
    'learn.start': '开始学码',
    'learn.save': '保存学码',
    'learn.cancel': '取消学码',
    'learn.idle_timeout': '学码超时',
  }
  return known[action] ?? action
}

function deviceLabel(deviceId: string | null): string {
  if (!deviceId) return '—'
  return deviceNames.value[deviceId] ?? deviceId
}

async function loadDevices(): Promise<void> {
  try {
    const payload = await fetchDevices()
    devices.value = payload.devices
  } catch {
    // 设备名映射失败不影响审计列表本身：回退为显示设备 id。
  }
}

async function loadAudit(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const payload = await fetchAudit({
      limit: limit.value,
      result: resultFilter.value,
      deviceId: deviceFilter.value || undefined,
    })
    records.value = payload.records
  } catch (error) {
    loadError.value = toApiError(error)
    records.value = []
  } finally {
    loading.value = false
  }
}

function setResultFilter(value: ResultFilter): void {
  if (resultFilter.value === value) return
  resultFilter.value = value
  void loadAudit()
}

function chooseDevice(deviceId: string): void {
  deviceFilter.value = deviceId
  devicePickerOpen.value = false
  void loadAudit()
}

function setLimit(value: number): void {
  if (limit.value === value) return
  limit.value = value
  void loadAudit()
}

function refresh(): void {
  void Promise.all([loadDevices(), loadAudit()])
}

onMounted(() => {
  void Promise.all([loadDevices(), loadAudit()])
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="06"
      title="审计"
      description="只读操作历史：按写入倒序，可按结果与设备筛选。没有删除或写入入口。"
    />

    <div class="filter-bar">
      <div class="segmented" role="group" aria-label="按结果筛选">
        <button
          v-for="option in resultOptions"
          :key="option.value"
          class="segment"
          :class="{ 'is-active': resultFilter === option.value }"
          type="button"
          @click="setResultFilter(option.value)"
        >
          {{ option.label }}
        </button>
      </div>

      <button class="picker-trigger is-compact" type="button" @click="devicePickerOpen = true">
        <span>设备</span>
        <span class="picker-value">{{ deviceFilterLabel }}</span>
      </button>

      <div class="segmented" role="group" aria-label="条数">
        <button
          v-for="option in limitOptions"
          :key="option"
          class="segment"
          :class="{ 'is-active': limit === option }"
          type="button"
          @click="setLimit(option)"
        >
          {{ option }}
        </button>
      </div>

      <button class="text-btn" type="button" :disabled="loading" @click="refresh">刷新</button>
    </div>

    <LoadingBlock v-if="loading" text="正在读取审计记录" />

    <template v-else>
      <div v-if="serviceOffline" class="offline-panel">
        <ErrorBanner :error="loadError" show-retry @retry="loadAudit" />
        <p class="offline-note">管家服务连不上，审计记录不可读。</p>
      </div>

      <template v-else>
        <ErrorBanner :error="loadError" show-retry @retry="loadAudit" />

        <EmptyBlock
          v-if="!loadError && records.length === 0"
          title="暂无记录"
          note="控制、学码与校正发生后，这里会按写入倒序显示。"
        />

        <ol v-else-if="!loadError" class="audit-list">
          <li v-for="(record, index) in records" :key="`${record.created_at}-${index}`" class="audit-row">
            <div class="audit-main">
              <span class="audit-time mono">{{ formatTime(record.created_at) }}</span>
              <strong class="audit-action">{{ actionLabel(record.action) }}</strong>
              <span class="audit-device">{{ deviceLabel(record.device_id) }}</span>
              <span class="result-pill" :class="record.result === 'success' ? 'is-ok' : 'is-bad'">
                {{ record.result === 'success' ? '成功' : '失败' }}
              </span>
            </div>
            <p v-if="record.reason" class="audit-reason">{{ record.reason }}</p>
            <details v-if="record.trace_id" class="trace">
              <summary>排查编号</summary>
              <span class="mono">{{ record.trace_id }}</span>
            </details>
          </li>
        </ol>
      </template>
    </template>

    <van-popup v-model:show="devicePickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>按设备筛选</h3>
          <button class="text-btn" type="button" @click="devicePickerOpen = false">关闭</button>
        </header>
        <button
          class="picker-row"
          :class="{ 'is-selected': deviceFilter === '' }"
          type="button"
          @click="chooseDevice('')"
        >
          <span>全部设备</span>
          <span class="picker-sub">不筛选</span>
        </button>
        <button
          v-for="device in devices"
          :key="device.id"
          class="picker-row"
          :class="{ 'is-selected': deviceFilter === device.id }"
          type="button"
          @click="chooseDevice(device.id)"
        >
          <span>{{ device.name }}</span>
        </button>
      </div>
    </van-popup>
  </section>
</template>
