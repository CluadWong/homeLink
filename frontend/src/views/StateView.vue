<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { showToast } from 'vant'

import type { ApiError } from '../api/errors'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import StateBadge from '../components/StateBadge.vue'
import {
  refreshGateway,
  refreshState,
  useStatusSummary,
} from '../composables/useStatusSummary'
import { formatTime } from '../utils/format'

const { snapshot } = useStatusSummary()
const firstLoading = ref(true)
const refreshingGateway = ref(false)
let timer: number | undefined

const serviceOffline = computed(
  () => snapshot.stateError?.code === 'AGENT_UNREACHABLE',
)
const stateError = computed<ApiError | null>(() => snapshot.stateError)
const gatewayError = computed<ApiError | null>(() => snapshot.gatewayError)

const gatewayTone = computed(() => {
  if (!snapshot.gateway) return 'idle'
  return snapshot.gateway.reachable ? 'ok' : 'bad'
})

const sensorRows = computed(() => {
  const sensors = snapshot.sensors
  if (!sensors || !sensors.available) {
    return [
      { label: '温度', value: '未接入' },
      { label: '湿度', value: '未接入' },
      { label: '人体感应', value: '未接入' },
    ]
  }
  return [
    { label: '温度', value: sensors.temperature === null ? '未接入' : `${sensors.temperature}` },
    { label: '湿度', value: sensors.humidity === null ? '未接入' : `${sensors.humidity}` },
    { label: '人体感应', value: sensors.presence === null ? '未接入' : `${sensors.presence}` },
  ]
})

async function refreshAll(): Promise<void> {
  await Promise.all([refreshState(), refreshGateway()])
  firstLoading.value = false
}

async function recheckGateway(): Promise<void> {
  if (refreshingGateway.value) return
  refreshingGateway.value = true
  try {
    await refreshGateway(true)
    showToast({ message: '已重新检测网关', duration: 1200 })
  } finally {
    refreshingGateway.value = false
  }
}

onMounted(() => {
  void refreshAll()
  timer = window.setInterval(() => {
    void refreshAll()
  }, 5_000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="04"
      title="状态"
      description="状态是虚拟记录：红外单向无回执，所以回放后一律「未知」，只有用户校正才是「已确认」。"
    />

    <LoadingBlock v-if="firstLoading" text="正在读取设备状态与网关" />

    <template v-else>
      <div v-if="serviceOffline" class="offline-panel">
        <ErrorBanner :error="stateError" show-retry @retry="refreshAll" />
        <p class="offline-note">
          管家服务连不上时页面不展示任何陈旧状态。请确认后端已启动、地址正确后再重试。
        </p>
      </div>

      <template v-else>
        <ErrorBanner :error="stateError" show-retry @retry="refreshAll" />

        <section class="card gateway-panel">
          <header class="card-head">
            <div>
              <h3 class="card-title">网关</h3>
              <p class="card-meta">探测结果只用于展示，不是控制的前置条件。</p>
            </div>
            <button class="text-btn" type="button" :disabled="refreshingGateway" @click="recheckGateway">
              {{ refreshingGateway ? '检测中…' : '重新检测' }}
            </button>
          </header>

          <ErrorBanner :error="gatewayError" />

          <template v-if="snapshot.gateway">
            <div class="gateway-line">
              <span class="status-dot" :class="`is-${gatewayTone}`"></span>
              <strong>{{ snapshot.gateway.reachable ? '网关在线' : '网关离线' }}</strong>
              <span v-if="snapshot.gateway.simulated" class="mode-tag">模拟</span>
              <span v-if="snapshot.gateway.pending_restart" class="mode-tag is-warn">地址待重启</span>
            </div>

            <dl class="kv-grid">
              <div>
                <dt>模式</dt>
                <dd class="mono">{{ snapshot.gateway.mode }}</dd>
              </div>
              <div>
                <dt>地址</dt>
                <dd class="mono">{{ snapshot.gateway.base_url || '未配置（mock 不连板子）' }}</dd>
              </div>
              <div>
                <dt>延迟</dt>
                <dd class="mono">{{ snapshot.gateway.latency_ms }} ms</dd>
              </div>
              <div>
                <dt>检测时间</dt>
                <dd class="mono">{{ formatTime(snapshot.gateway.checked_at) }}</dd>
              </div>
              <div>
                <dt>最近成功</dt>
                <dd class="mono">{{ formatTime(snapshot.gateway.last_success_at) }}</dd>
              </div>
            </dl>

            <p v-if="snapshot.gateway.reachable === false" class="warning-line">
              后端正常，但板子没有响应：{{ snapshot.gateway.error || '无更多信息' }}。
              请确认板子电源、Wi-Fi 与网关地址。
            </p>
            <p v-if="snapshot.gateway.pending_restart" class="warning-line">
              新地址已保存但尚未生效：当前探测结果仍来自旧地址，重启服务后才会切换。
            </p>
          </template>
        </section>

        <section v-if="!stateError" class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">设备状态</h3>
              <p class="card-meta">「未知」不等于「关」；回放只记录信号发出过，不猜测真实状态。</p>
            </div>
            <RouterLink class="action-link" to="/correct">去校正</RouterLink>
          </header>

          <EmptyBlock
            v-if="snapshot.devices.length === 0"
            title="还没有设备"
            note="添加设备并学码后，这里会显示每台设备的最新状态记录。"
          >
            <RouterLink class="action-link" to="/devices">去添加设备</RouterLink>
          </EmptyBlock>

          <ul v-else class="state-list">
            <li v-for="device in snapshot.devices" :key="device.id" class="state-row">
              <div class="state-device">
                <strong>{{ device.name }}</strong>
                <span class="mono state-time">{{ formatTime(device.updated_at) }}</span>
              </div>
              <StateBadge :state="device.state" :state-source="device.state_source" />
            </li>
          </ul>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">传感器</h3>
              <p class="card-meta">本期不接入传感器；三项读数恒为 null，界面显示「未接入」，不显示 0。</p>
            </div>
          </header>
          <div class="sensor-grid">
            <div v-for="row in sensorRows" :key="row.label" class="sensor-cell">
              <span class="sensor-label">{{ row.label }}</span>
              <span class="sensor-value" :class="{ 'is-off': row.value === '未接入' }">{{ row.value }}</span>
            </div>
          </div>
        </section>
      </template>
    </template>
  </section>
</template>
