<script setup lang="ts">
import { computed } from 'vue'

import { friendly, type ApiError } from '../api/errors'

const props = withDefaults(
  defineProps<{
    error: ApiError | null
    showRetry?: boolean
  }>(),
  { showRetry: false },
)

const emit = defineEmits<{ retry: [] }>()

const info = computed(() => (props.error ? friendly(props.error.code, props.error.serverMessage) : null))
</script>

<template>
  <section v-if="error && info" class="error-banner" role="alert">
    <div class="error-head">
      <span class="error-mark">!</span>
      <strong>{{ info.title }}</strong>
    </div>
    <p class="error-next">下一步：{{ info.next }}</p>
    <details v-if="info.showTrace && error.traceId" class="trace">
      <summary>排查编号</summary>
      <span class="mono">{{ error.traceId }}</span>
    </details>
    <button v-if="showRetry" class="text-btn" type="button" @click="emit('retry')">重试</button>
  </section>
</template>
