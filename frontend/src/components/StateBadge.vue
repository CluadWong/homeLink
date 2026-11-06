<script setup lang="ts">
import { computed } from 'vue'

import { stateText } from '../utils/format'

const props = defineProps<{
  state: string
  stateSource: 'unknown' | 'confirmed'
  compact?: boolean
}>()

const label = computed(() => stateText(props.state, props.stateSource))
const isUnknown = computed(() => props.state === 'unknown' || props.stateSource === 'unknown')
const fullText = computed(() =>
  props.state === 'unknown' ? '未知（红外无回执，无法确认设备状态）' : props.state,
)
</script>

<template>
  <span class="state-badge" :class="{ 'is-unknown': isUnknown, 'is-compact': compact }">
    <span class="state-dot" :class="isUnknown ? 'is-unknown' : 'is-confirmed'"></span>
    <span class="state-main" :title="fullText">{{ label }}</span>
    <span class="state-source">{{ isUnknown ? '未知' : '已确认' }}</span>
  </span>
</template>
