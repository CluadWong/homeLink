<script setup lang="ts">
import { computed, ref } from 'vue'

import { currentToken, hasInjectedToken, setRuntimeToken } from '../api/client'

const pages = [
  { index: '01', path: '/call', label: '调用' },
  { index: '02', path: '/devices', label: '设备' },
  { index: '03', path: '/learn', label: '学码' },
  { index: '04', path: '/state', label: '状态' },
  { index: '05', path: '/correct', label: '校正' },
  { index: '06', path: '/audit', label: '审计' },
  { index: '07', path: '/scenes', label: '场景' },
]

const settingsOpen = ref(false)
const tokenDraft = ref('')
const revision = ref(0)

const injected = hasInjectedToken()
const tokenState = computed(() => {
  // revision 只为在手动保存后触发重新计算
  void revision.value
  if (injected) return '部署注入'
  return currentToken() ? '本次标签页临时填写' : '未设置'
})
const tokenMissing = computed(() => !injected && !currentToken())
const serviceAddress = computed(() =>
  (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '') || '同源 /api（开发期代理 → 127.0.0.1:8000）',
)

function saveToken(): void {
  setRuntimeToken(tokenDraft.value)
  tokenDraft.value = ''
  revision.value += 1
}
</script>

<template>
  <div class="nav-wrap">
    <nav class="nav" aria-label="主导航">
      <RouterLink
        v-for="page in pages"
        :key="page.path"
        :to="page.path"
        class="nav-item"
        active-class="is-active"
      >
        <span class="nav-index">{{ page.index }}</span>
        <span>{{ page.label }}</span>
      </RouterLink>
      <button class="nav-settings" type="button" @click="settingsOpen = !settingsOpen">
        设置
      </button>
    </nav>

    <section v-if="settingsOpen" class="settings-panel">
      <div class="settings-row">
        <span class="settings-key">服务地址</span>
        <span class="settings-value mono">{{ serviceAddress }}</span>
      </div>
      <div class="settings-row">
        <span class="settings-key">token 状态</span>
        <span class="settings-value" :class="{ 'is-warn': tokenMissing }">{{ tokenState }}</span>
      </div>
      <div v-if="!injected" class="settings-token">
        <label class="settings-key" for="runtime-token">手动填写一次</label>
        <div class="settings-token-row">
          <input
            id="runtime-token"
            v-model="tokenDraft"
            class="settings-input"
            type="password"
            autocomplete="off"
            placeholder="粘贴后端 api.token"
          />
          <button class="text-btn" type="button" @click="saveToken">保存</button>
        </div>
        <p class="settings-hint">
          只存本次标签页（sessionStorage），关闭标签页即失效；正常部署应通过
          <code>VITE_API_TOKEN</code> 注入。
        </p>
      </div>
      <p v-else class="settings-hint">token 已由部署注入，刷新后自动从构建配置重建。</p>
    </section>
  </div>
</template>
