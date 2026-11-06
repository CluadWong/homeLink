<script setup lang="ts">
import { computed } from 'vue'

import { useStatusSummary } from '../composables/useStatusSummary'

const { snapshot } = useStatusSummary()

const gatewayState = computed(() => {
  if (snapshot.stateError?.code === 'AGENT_UNREACHABLE') {
    return { tone: 'bad', label: '网关状态不可知', note: '管家服务连不上' }
  }
  if (snapshot.gatewayError) {
    return { tone: 'bad', label: '网关状态读取失败', note: snapshot.gatewayError.serverMessage }
  }
  if (!snapshot.gateway) {
    return { tone: 'idle', label: '网关检测中', note: '正在探测' }
  }
  const gateway = snapshot.gateway
  const notes: string[] = []
  if (gateway.simulated) notes.push('模拟模式')
  if (gateway.pending_restart) notes.push('地址待重启')
  if (gateway.reachable) {
    return { tone: 'ok', label: '网关在线', note: notes.join(' · ') || `${gateway.latency_ms} ms` }
  }
  return {
    tone: 'bad',
    label: '网关离线',
    note: gateway.error || '后端正常，板子无响应',
  }
})

const deviceText = computed(() => {
  if (snapshot.stateError) return '设备概况不可知'
  return `${snapshot.devices.length} 台设备`
})
</script>

<template>
  <div class="status-strip" role="status">
    <RouterLink to="/devices" class="status-cell">
      <span class="status-label">设备</span>
      <span class="status-value">{{ deviceText }}</span>
    </RouterLink>
    <RouterLink to="/state" class="status-cell status-cell--wide">
      <span class="status-label">网关</span>
      <span class="status-value">
        <span class="status-dot" :class="`is-${gatewayState.tone}`"></span>
        {{ gatewayState.label }}
      </span>
      <span class="status-note">{{ gatewayState.note }}</span>
    </RouterLink>
  </div>
</template>
