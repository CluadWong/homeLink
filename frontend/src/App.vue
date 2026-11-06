<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

import AppNav from './components/AppNav.vue'
import StatusStrip from './components/StatusStrip.vue'
import { refreshGateway, refreshState } from './composables/useStatusSummary'

let timer: number | undefined

async function refreshSummary(): Promise<void> {
  await Promise.all([refreshState(), refreshGateway()])
}

onMounted(() => {
  void refreshSummary()
  timer = window.setInterval(() => {
    void refreshSummary()
  }, 15_000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="app-shell">
    <header class="masthead">
      <div class="brand">
        <span class="brand-mark">JJ</span>
        <div class="brand-text">
          <h1>红外管家</h1>
          <p>SINGLE-CHANNEL IR CONTROLLER</p>
        </div>
      </div>
      <StatusStrip />
      <AppNav />
    </header>

    <main class="app-main">
      <RouterView />
    </main>

    <footer class="app-footer">
      <span>JJ—BUTLER / LOCAL ONLY</span>
      <span>红外单向 · 状态仅记录「发出过什么」</span>
    </footer>
  </div>
</template>
