<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { showToast } from 'vant'

import { control, fetchDevices, fetchRooms } from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { Device, DeviceFunction, Room } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { refreshState } from '../composables/useStatusSummary'

const devices = ref<Device[]>([])
const rooms = ref<Room[]>([])
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const actionError = ref<ApiError | null>(null)
const executingId = ref<string | null>(null)
const lastSent = ref<{ deviceName: string; functionName: string } | null>(null)

const roomLabels = computed(() => {
  const map: Record<string, string> = {}
  for (const room of rooms.value) map[room.name] = room.display_name
  return map
})

function roomLabel(name: string): string {
  return roomLabels.value[name] ?? name
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const [devicePayload, roomPayload] = await Promise.all([fetchDevices(), fetchRooms()])
    devices.value = devicePayload.devices
    rooms.value = roomPayload.rooms
  } catch (error) {
    loadError.value = toApiError(error)
  } finally {
    loading.value = false
  }
}

async function execute(device: Device, item: DeviceFunction): Promise<void> {
  if (executingId.value) return
  executingId.value = item.id
  actionError.value = null
  lastSent.value = null
  try {
    await control(item.id)
    lastSent.value = { deviceName: device.name, functionName: item.name }
    showToast({ message: '已发送', duration: 1600 })
    // 契约要求：执行成功后重拉状态；不做乐观 UI。
    await refreshState()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    executingId.value = null
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="01"
      title="调用"
      description="每个功能就是一个学码动作。点「执行」只回放该功能最新一条红外码；没有参数表单。"
    />

    <LoadingBlock v-if="loading" text="正在读取设备与功能" />

    <template v-else>
      <ErrorBanner :error="loadError" show-retry @retry="load" />
      <ErrorBanner :error="actionError" />

      <div v-if="lastSent" class="sent-strip" role="status">
        <strong>已发送</strong>
        <span>{{ lastSent.deviceName }} · {{ lastSent.functionName }}</span>
        <span class="sent-note">红外是单向的，无法确认设备是否执行</span>
      </div>

      <EmptyBlock
        v-if="!loadError && devices.length === 0"
        title="还没有设备"
        note="设备全部在运行期创建；先添加设备并学码，调用页才会有可执行的功能。"
      >
        <RouterLink class="action-link" to="/devices">去添加设备</RouterLink>
      </EmptyBlock>

      <div v-else-if="!loadError" class="device-grid">
        <article v-for="device in devices" :key="device.id" class="card device-card">
          <header class="card-head">
            <div>
              <h3 class="card-title">{{ device.name }}</h3>
              <p class="card-meta">{{ roomLabel(device.room) }}</p>
            </div>
            <span class="count-tag">{{ device.functions.length }} 功能</span>
          </header>

          <div v-if="device.functions.length === 0" class="inline-empty">
            这个设备还没有功能。
            <RouterLink to="/learn">去学码</RouterLink>
          </div>

          <ul v-else class="function-list">
            <li v-for="item in device.functions" :key="item.id" class="function-row">
              <div class="function-name">
                <span class="function-label">{{ item.name }}</span>
              </div>
              <button
                class="execute-btn"
                type="button"
                :disabled="executingId !== null"
                @click="execute(device, item)"
              >
                {{ executingId === item.id ? '发送中…' : '执行' }}
              </button>
            </li>
          </ul>
        </article>
      </div>

      <p class="page-footnote">
        「已发送」只代表红外信号发出了，不代表设备已经执行；回放后状态一律记为「未知」。
      </p>
    </template>
  </section>
</template>
