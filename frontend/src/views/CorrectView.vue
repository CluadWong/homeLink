<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { showFailToast, showToast } from 'vant'

import { correct } from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { StateDevice } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import StateBadge from '../components/StateBadge.vue'
import { refreshState, useStatusSummary } from '../composables/useStatusSummary'

const { snapshot } = useStatusSummary()
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const actionError = ref<ApiError | null>(null)
const pickerOpen = ref(false)
const selectedId = ref('')
const stateChoice = ref<'on' | 'off' | 'unknown'>('unknown')
const reason = ref('')
const submitting = ref(false)
const lastResult = ref<{ deviceName: string; state: string } | null>(null)

const serviceOffline = computed(() => loadError.value?.code === 'AGENT_UNREACHABLE')
const selectedDevice = computed<StateDevice | null>(
  () => snapshot.devices.find((device) => device.id === selectedId.value) ?? null,
)

async function loadDevices(): Promise<void> {
  loading.value = true
  loadError.value = null
  await refreshState()
  if (snapshot.stateError) loadError.value = snapshot.stateError
  if (!selectedId.value && snapshot.devices.length > 0) {
    selectedId.value = snapshot.devices[0].id
  }
  loading.value = false
}

function chooseDevice(device: StateDevice): void {
  selectedId.value = device.id
  pickerOpen.value = false
  lastResult.value = null
}

async function submit(): Promise<void> {
  const device = selectedDevice.value
  if (!device) {
    showFailToast('请选择要校正的设备')
    return
  }
  const nextState = stateChoice.value
  if (!nextState) {
    showFailToast('请选择校正后的状态')
    return
  }

  submitting.value = true
  actionError.value = null
  try {
    const result = await correct({
      device_id: device.id,
      state: nextState,
      reason: reason.value.trim() || undefined,
    })
    lastResult.value = { deviceName: device.name, state: result.state }
    showToast({ message: '已记录校正', duration: 1500 })
    await refreshState()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void loadDevices()
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="05"
      title="校正"
      description="校正只修改状态记录，不发送红外。红外单向无回执，只有你手动校正才会出现「已确认」。"
    />

    <LoadingBlock v-if="loading" text="正在读取设备状态" />

    <template v-else>
      <div v-if="serviceOffline" class="offline-panel">
        <ErrorBanner :error="loadError" show-retry @retry="loadDevices" />
        <p class="offline-note">管家服务连不上，无法读取或写入状态。</p>
      </div>

      <template v-else>
        <ErrorBanner :error="loadError" show-retry @retry="loadDevices" />
        <ErrorBanner :error="actionError" />

        <EmptyBlock
          v-if="!loadError && snapshot.devices.length === 0"
          title="还没有设备"
          note="先添加设备并学码，状态页才会有可以校正的记录。"
        >
          <RouterLink class="action-link" to="/devices">去添加设备</RouterLink>
        </EmptyBlock>

        <div v-else class="card">
          <header class="card-head">
            <div>
              <h3 class="card-title">校正记录</h3>
              <p class="card-meta">提交前先确认当前记录；写入后会追加一条「已确认」状态。</p>
            </div>
          </header>

          <button class="picker-trigger" type="button" @click="pickerOpen = true">
            <span>设备</span>
            <span class="picker-value">
              {{ selectedDevice?.name ?? '请选择设备' }}
            </span>
          </button>

          <div v-if="selectedDevice" class="current-record">
            <span class="record-label">当前记录</span>
            <StateBadge
              :state="selectedDevice.state"
              :state-source="selectedDevice.state_source"
            />
          </div>

          <div class="field-block">
            <span class="field-label">校正为</span>
            <van-radio-group v-model="stateChoice" direction="horizontal" class="radio-row">
              <van-radio name="on">开</van-radio>
              <van-radio name="off">关</van-radio>
              <van-radio name="unknown">未知</van-radio>
            </van-radio-group>
            <p class="card-hint">
              后端接受非空自由字符串，界面只提供这三项，避免误输入；「未知」才是红外回放后的默认语义。
            </p>
          </div>

          <van-field
            v-model="reason"
            label="原因"
            placeholder="可选，例如：我手动确认空调已开"
            maxlength="128"
          />

          <div class="sheet-actions">
            <button class="primary-btn" type="button" :disabled="submitting" @click="submit">
              {{ submitting ? '提交中…' : '提交校正' }}
            </button>
          </div>

          <div v-if="lastResult" class="sent-strip is-light" role="status">
            <strong>已记录校正</strong>
            <span>{{ lastResult.deviceName }} · {{ lastResult.state }}</span>
            <span class="sent-note">这只是状态记录，没有发送任何红外信号。</span>
          </div>
        </div>
      </template>
    </template>

    <van-popup v-model:show="pickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>选择设备</h3>
          <button class="text-btn" type="button" @click="pickerOpen = false">关闭</button>
        </header>
        <button
          v-for="device in snapshot.devices"
          :key="device.id"
          class="picker-row"
          :class="{ 'is-selected': selectedId === device.id }"
          type="button"
          @click="chooseDevice(device)"
        >
          <span>{{ device.name }}</span>
          <StateBadge
            :state="device.state"
            :state-source="device.state_source"
            compact
          />
        </button>
      </div>
    </van-popup>
  </section>
</template>
