<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import { sendAgentChat } from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import PageHeader from '../components/PageHeader.vue'

interface ChatMessage {
  role: 'user' | 'agent'
  text: string
  at: string
}

const SESSION_KEY = 'homelink_agent_session'
const EXAMPLES = ['打开卧室风扇', '关闭卧室风扇', '卧室风扇现在是什么状态？']

const messages = ref<ChatMessage[]>([])
const draft = ref('')
const sending = ref(false)
const error = ref<ApiError | null>(null)
const listRef = ref<HTMLElement | null>(null)
const sessionId = ref(readSession())

const errorTitle = computed(() =>
  error.value?.code === 'DEVICE_OFFLINE' ? '管家服务没就绪' : '这一轮没有完成',
)

function readSession(): string {
  try {
    return (sessionStorage.getItem(SESSION_KEY) ?? '').trim()
  } catch {
    return ''
  }
}

function saveSession(id: string): void {
  sessionId.value = id
  try {
    if (id) sessionStorage.setItem(SESSION_KEY, id)
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    // 存储被禁用时只保留内存值；刷新后开新会话，不影响使用。
  }
}

function now(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const node = listRef.value
  if (node) node.scrollTop = node.scrollHeight
}

function reset(): void {
  messages.value = []
  error.value = null
  saveSession('')
}

async function send(text?: string): Promise<void> {
  const message = (text ?? draft.value).trim()
  if (!message || sending.value) return
  draft.value = ''
  error.value = null
  messages.value.push({ role: 'user', text: message, at: now() })
  sending.value = true
  void scrollToBottom()
  try {
    const payload = await sendAgentChat({
      message,
      session_id: sessionId.value || undefined,
    })
    saveSession(payload.result.session_id)
    messages.value.push({ role: 'agent', text: payload.result.reply, at: now() })
  } catch (err) {
    error.value = toApiError(err)
  } finally {
    sending.value = false
    void scrollToBottom()
  }
}
</script>

<template>
  <section class="page chat-page">
    <PageHeader
      index="00"
      title="管家"
      description="用一句话让管家做事：它会自己找设备、调能力。只支持文字；红外是单向的，管家只能确认「已发送」，不能确认设备真的执行。"
    />

    <div class="chat-toolbar">
      <span class="chat-session mono">
        {{ sessionId ? `会话 ${sessionId}` : '新会话' }}
      </span>
      <button
        class="text-btn"
        type="button"
        :disabled="sending || messages.length === 0"
        @click="reset"
      >
        新对话
      </button>
    </div>

    <div ref="listRef" class="chat-list">
      <div v-if="messages.length === 0" class="chat-empty">
        <p class="chat-empty-title">直接说你想让管家做什么</p>
        <p class="chat-empty-note">
          例如打开或关闭某个房间的电器。它能查设备、发红外、跑场景，也会如实告诉你哪一步没做成。
        </p>
        <div class="chat-examples">
          <button
            v-for="item in EXAMPLES"
            :key="item"
            class="chat-example"
            type="button"
            @click="send(item)"
          >
            {{ item }}
          </button>
        </div>
      </div>

      <div
        v-for="(item, index) in messages"
        :key="index"
        class="chat-msg"
        :class="item.role === 'user' ? 'is-user' : 'is-agent'"
      >
        <div class="chat-bubble">{{ item.text }}</div>
        <span class="chat-meta">{{ item.role === 'user' ? '你' : '管家' }} · {{ item.at }}</span>
      </div>

      <div v-if="sending" class="chat-msg is-agent">
        <div class="chat-bubble is-typing">正在思考…</div>
        <span class="chat-meta">本机模型推理通常需要几秒到几十秒</span>
      </div>
    </div>

    <div v-if="error" class="chat-error" role="alert">
      <strong>{{ errorTitle }}</strong>
      <span>{{ error.serverMessage || '管家没有返回内容，请稍后重试。' }}</span>
      <span v-if="error.traceId" class="chat-trace mono">排查编号 {{ error.traceId }}</span>
    </div>

    <form class="chat-compose" @submit.prevent="send()">
      <textarea
        v-model="draft"
        class="chat-input"
        rows="2"
        maxlength="2000"
        placeholder="例如：打开卧室风扇"
        @keydown.enter.exact.prevent="send()"
      />
      <button class="execute-btn" type="submit" :disabled="sending || !draft.trim()">
        {{ sending ? '发送中…' : '发送' }}
      </button>
    </form>

    <p class="page-footnote">
      会话只保存在本机内存与本次标签页；「新对话」会丢掉上下文，但已发出的红外不会撤回。
    </p>
  </section>
</template>
