<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { showConfirmDialog, showFailToast, showToast } from 'vant'

import {
  createScene,
  deleteScene,
  fetchCapabilities,
  fetchScenes,
  patchScene,
  runScene,
} from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type {
  Capability,
  Scene,
  SceneRunResult,
  SceneTrigger,
  SceneTriggerInput,
} from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'

interface DraftGroup {
  name: string
  capability: string
  deviceIds: string[]
}

const scenes = ref<Scene[]>([])
const capabilities = ref<Capability[]>([])
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const actionError = ref<ApiError | null>(null)
const busy = ref(false)

const sheetOpen = ref(false)
const editingSceneId = ref<string | null>(null)
const nameDraft = ref('')
const enabledDraft = ref(true)
const groupsDraft = ref<DraftGroup[]>([])
const scheduleEnabled = ref(false)
const scheduleCron = ref('0 23 * * *')
const voiceEnabled = ref(false)
const voicePhrases = ref('')
// 设备状态触发器暂不在界面编辑，编辑时原样带回，避免整组替换时丢失。
const preservedTriggers = ref<SceneTriggerInput[]>([])

const capabilityPickerOpen = ref(false)
const pickerGroupIndex = ref(-1)

const runSheetOpen = ref(false)
const runResult = ref<SceneRunResult | null>(null)
const runSceneName = ref('')
const runningId = ref<string | null>(null)

const capabilityMap = computed(() => {
  const map = new Map<string, Capability>()
  for (const item of capabilities.value) map.set(item.capability, item)
  return map
})

const totalTargets = computed(() =>
  scenes.value.reduce((sum, scene) => sum + scene.items.length, 0),
)

const runStatusLabel: Record<string, string> = {
  sent: '已发送',
  failed: '失败',
  offline: '离线',
}

const overallLabel: Record<string, string> = {
  sent: '全部已发送',
  partial: '部分成功',
  failed: '全部失败',
}

function groupDevices(group: DraftGroup) {
  return capabilityMap.value.get(group.capability)?.devices ?? []
}

function sceneGroupLabel(scene: Scene): string {
  return scene.action_groups
    .map((group) => `${group.name} · ${group.targets.length} 台`)
    .join(' / ')
}

function triggerLabel(trigger: SceneTrigger): string {
  if (trigger.type === 'schedule') {
    const cron = typeof trigger.config.cron === 'string' ? trigger.config.cron : ''
    return `定时 ${cron}`.trim()
  }
  if (trigger.type === 'voice') {
    const phrases = trigger.config.phrases
    return Array.isArray(phrases) ? `语音 ${phrases.join(' / ')}` : '语音'
  }
  if (trigger.type === 'device_state') return '设备状态'
  return '手动'
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const [scenePayload, capabilityPayload] = await Promise.all([
      fetchScenes(),
      fetchCapabilities(),
    ])
    scenes.value = scenePayload.result.scenes
    capabilities.value = capabilityPayload.result.capabilities
  } catch (error) {
    loadError.value = toApiError(error)
  } finally {
    loading.value = false
  }
}

function newGroup(): DraftGroup {
  return { name: '', capability: '', deviceIds: [] }
}

function openCreate(): void {
  if (capabilities.value.length === 0) {
    showFailToast('还没有可用能力，先给设备学码')
    return
  }
  editingSceneId.value = null
  nameDraft.value = ''
  enabledDraft.value = true
  groupsDraft.value = [newGroup()]
  scheduleEnabled.value = false
  scheduleCron.value = '0 23 * * *'
  voiceEnabled.value = false
  voicePhrases.value = ''
  preservedTriggers.value = []
  actionError.value = null
  sheetOpen.value = true
}

function openEdit(scene: Scene): void {
  editingSceneId.value = scene.scene_id
  nameDraft.value = scene.name
  enabledDraft.value = scene.enabled
  groupsDraft.value = scene.action_groups.map((group) => ({
    name: group.name,
    capability: group.capability,
    deviceIds: group.targets.map((target) => target.device_id),
  }))
  const schedule = scene.triggers.find((trigger) => trigger.type === 'schedule')
  scheduleEnabled.value = Boolean(schedule)
  scheduleCron.value =
    typeof schedule?.config.cron === 'string' ? schedule.config.cron : '0 23 * * *'
  const voice = scene.triggers.find((trigger) => trigger.type === 'voice')
  voiceEnabled.value = Boolean(voice)
  const phrases = voice?.config.phrases
  voicePhrases.value = Array.isArray(phrases)
    ? phrases.filter((item): item is string => typeof item === 'string').join('，')
    : ''
  preservedTriggers.value = scene.triggers
    .filter((trigger) => trigger.type === 'device_state')
    .map((trigger) => ({
      type: trigger.type,
      enabled: trigger.enabled,
      config: trigger.config,
    }))
  actionError.value = null
  sheetOpen.value = true
}

function openCapabilityPicker(index: number): void {
  pickerGroupIndex.value = index
  capabilityPickerOpen.value = true
}

function chooseCapability(capability: string): void {
  const index = pickerGroupIndex.value
  capabilityPickerOpen.value = false
  if (index < 0) return
  const group = groupsDraft.value[index]
  group.capability = capability
  group.name = capability
  const supported = new Set(groupDevices(group).map((device) => device.device_id))
  group.deviceIds = group.deviceIds.filter((deviceId) => supported.has(deviceId))
}

function toggleDevice(group: DraftGroup, deviceId: string): void {
  if (group.deviceIds.includes(deviceId)) {
    group.deviceIds = group.deviceIds.filter((item) => item !== deviceId)
  } else {
    group.deviceIds.push(deviceId)
  }
}

function addGroup(): void {
  if (groupsDraft.value.length >= 20) {
    showFailToast('最多 20 个动作组')
    return
  }
  groupsDraft.value.push(newGroup())
}

function removeGroup(index: number): void {
  groupsDraft.value.splice(index, 1)
}

function buildTriggers(): SceneTriggerInput[] {
  const triggers: SceneTriggerInput[] = [
    { type: 'manual', enabled: true, config: {} },
    ...preservedTriggers.value,
  ]
  if (scheduleEnabled.value) {
    triggers.push({
      type: 'schedule',
      enabled: true,
      config: { cron: scheduleCron.value.trim() },
    })
  }
  if (voiceEnabled.value) {
    triggers.push({
      type: 'voice',
      enabled: true,
      config: {
        phrases: voicePhrases.value
          .split(/[，,]/)
          .map((item) => item.trim())
          .filter(Boolean),
      },
    })
  }
  return triggers
}

async function submit(): Promise<void> {
  const name = nameDraft.value.trim()
  if (!name) {
    showFailToast('场景名不能为空')
    return
  }
  if (groupsDraft.value.length === 0) {
    showFailToast('至少需要一个动作组')
    return
  }
  for (const group of groupsDraft.value) {
    if (!group.capability) {
      showFailToast('每个动作组都要选一个能力')
      return
    }
    if (group.deviceIds.length === 0) {
      showFailToast(`动作组「${group.name || group.capability}」至少选一台设备`)
      return
    }
  }
  if (scheduleEnabled.value && scheduleCron.value.trim().split(/\s+/).length !== 5) {
    showFailToast('定时触发器需要 5 段 cron 表达式')
    return
  }
  if (
    voiceEnabled.value &&
    !voicePhrases.value
      .split(/[，,]/)
      .some((item) => item.trim())
  ) {
    showFailToast('语音触发器至少填一个短语')
    return
  }

  const body = {
    name,
    enabled: enabledDraft.value,
    action_groups: groupsDraft.value.map((group) => ({
      name: group.name.trim() || group.capability,
      capability: group.capability,
      targets: group.deviceIds.map((deviceId) => ({ device_id: deviceId })),
    })),
    triggers: buildTriggers(),
  }

  busy.value = true
  actionError.value = null
  try {
    if (editingSceneId.value) {
      await patchScene(editingSceneId.value, body)
      showToast({ message: '场景已更新', duration: 1500 })
    } else {
      await createScene(body)
      showToast({ message: '场景已创建', duration: 1500 })
    }
    sheetOpen.value = false
    await load()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function toggleEnabled(scene: Scene): Promise<void> {
  if (busy.value) return
  busy.value = true
  actionError.value = null
  try {
    await patchScene(scene.scene_id, { enabled: !scene.enabled })
    await load()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function remove(scene: Scene): Promise<void> {
  try {
    await showConfirmDialog({
      title: '删除场景',
      message: `确认删除「${scene.name}」？删除后不可恢复。`,
      confirmButtonText: '删除',
      confirmButtonColor: '#b3261e',
    })
  } catch {
    return
  }
  busy.value = true
  actionError.value = null
  try {
    await deleteScene(scene.scene_id)
    showToast({ message: '场景已删除', duration: 1500 })
    await load()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function execute(scene: Scene): Promise<void> {
  if (runningId.value) return
  let confirm = false
  if (scene.contains_high_risk) {
    try {
      await showConfirmDialog({
        title: '高风险场景',
        message: `「${scene.name}」包含高风险设备，确认现在执行？`,
        confirmButtonText: '确认执行',
      })
      confirm = true
    } catch {
      return
    }
  }
  runningId.value = scene.scene_id
  actionError.value = null
  try {
    const payload = await runScene(scene.scene_id, confirm)
    runResult.value = payload.result
    runSceneName.value = scene.name
    runSheetOpen.value = true
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    runningId.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <PageHeader
      index="07"
      title="场景"
      description="场景是编排层：动作组只认能力，执行时逐台解析设备能力；单台失败或离线不影响其他设备。"
    />

    <LoadingBlock v-if="loading" label="正在读取场景…" />
    <ErrorBanner v-else-if="loadError" :error="loadError" show-retry @retry="load" />

    <template v-else>
      <div class="toolbar">
        <span class="toolbar-note">
          {{ scenes.length }} 个场景 · {{ totalTargets }} 个设备动作
        </span>
        <button class="primary-btn" type="button" @click="openCreate">新建场景</button>
      </div>

      <ErrorBanner v-if="actionError" :error="actionError" />

      <EmptyBlock
        v-if="scenes.length === 0"
        title="还没有场景"
        note="例如「离家」：把风扇放进一个动作组，一键关闭。"
      >
        <button class="primary-btn" type="button" @click="openCreate">新建场景</button>
      </EmptyBlock>

      <template v-else>
        <article v-for="scene in scenes" :key="scene.scene_id" class="card scene-card">
        <header class="card-head">
          <div>
            <h3 class="card-title">{{ scene.name }}</h3>
            <p class="card-meta">{{ sceneGroupLabel(scene) }}</p>
          </div>
          <span class="result-pill" :class="scene.enabled ? 'is-ok' : 'is-bad'">
            {{ scene.enabled ? '已启用' : '已停用' }}
          </span>
        </header>

        <div class="scene-groups">
          <div v-for="group in scene.action_groups" :key="group.group_id" class="scene-group">
            <div class="scene-group-head">
              <strong class="scene-group-name">{{ group.name }}</strong>
              <span class="scene-group-meta">能力：{{ group.capability }}</span>
            </div>
            <p class="scene-targets">
              <span
                v-for="target in group.targets"
                :key="target.device_id"
                class="scene-target"
                :class="{ 'is-missing': !target.function_id }"
              >
                {{ target.device_name }}
              </span>
            </p>
          </div>
        </div>

        <p class="scene-triggers">
          <span v-for="trigger in scene.triggers" :key="trigger.trigger_id" class="scene-trigger">
            {{ triggerLabel(trigger) }}{{ trigger.enabled ? '' : '（停用）' }}
          </span>
        </p>

        <div class="row-actions">
          <button
            class="primary-btn"
            type="button"
            :disabled="!scene.enabled || runningId !== null"
            @click="execute(scene)"
          >
            {{ runningId === scene.scene_id ? '执行中…' : '执行' }}
          </button>
          <button class="text-btn" type="button" :disabled="busy" @click="openEdit(scene)">
            编辑
          </button>
          <button class="text-btn" type="button" :disabled="busy" @click="toggleEnabled(scene)">
            {{ scene.enabled ? '停用' : '启用' }}
          </button>
          <button
            class="text-btn is-danger"
            type="button"
            :disabled="busy"
            @click="remove(scene)"
          >
            删除
          </button>
        </div>
      </article>
      </template>
    </template>

    <!-- 场景编辑 -->
    <van-popup v-model:show="sheetOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>{{ editingSceneId ? '编辑场景' : '新建场景' }}</h3>
          <button class="text-btn" type="button" @click="sheetOpen = false">关闭</button>
        </header>

        <van-field
          v-model="nameDraft"
          label="场景名"
          placeholder="例如：离家"
          maxlength="40"
          required
        />

        <div class="toggle-row">
          <span class="field-label">场景启用</span>
          <button
            class="toggle-btn"
            :class="{ 'is-on': enabledDraft }"
            type="button"
            @click="enabledDraft = !enabledDraft"
          >
            {{ enabledDraft ? '已启用' : '已停用' }}
          </button>
        </div>

        <div class="sheet-section-head">
          <span class="field-label">动作组</span>
          <button class="text-btn" type="button" @click="addGroup">添加动作组</button>
        </div>

        <div v-for="(group, index) in groupsDraft" :key="index" class="group-editor">
          <div class="group-editor-head">
            <button class="picker-trigger is-compact" type="button" @click="openCapabilityPicker(index)">
              <span>能力</span>
              <span class="picker-value">{{ group.capability || '请选择能力' }}</span>
            </button>
            <button
              v-if="groupsDraft.length > 1"
              class="text-btn is-danger"
              type="button"
              @click="removeGroup(index)"
            >
              移除
            </button>
          </div>

          <van-field
            v-model="group.name"
            label="动作组名"
            :placeholder="group.capability || '可选，默认用能力名'"
            maxlength="40"
          />

          <p v-if="!group.capability" class="sheet-note">先选能力，再勾选支持该能力的设备。</p>
          <p v-else class="sheet-note">
            未出现在列表中的设备，暂不支持「{{ group.capability }}」。
          </p>

          <div class="device-check-list">
            <button
              v-for="device in groupDevices(group)"
              :key="device.device_id"
              class="check-row"
              :class="{ 'is-checked': group.deviceIds.includes(device.device_id) }"
              type="button"
              @click="toggleDevice(group, device.device_id)"
            >
              <span class="check-box">{{ group.deviceIds.includes(device.device_id) ? '✓' : '' }}</span>
              <span>{{ device.name }}</span>
            </button>
            <p v-if="group.capability && groupDevices(group).length === 0" class="sheet-note">
              没有设备支持该能力，重新学码或换一个能力。
            </p>
          </div>
        </div>

        <div class="sheet-section-head">
          <span class="field-label">触发方式</span>
          <span class="sheet-note">手动执行始终可用</span>
        </div>

        <div class="toggle-row">
          <span>定时执行</span>
          <button
            class="toggle-btn"
            :class="{ 'is-on': scheduleEnabled }"
            type="button"
            @click="scheduleEnabled = !scheduleEnabled"
          >
            {{ scheduleEnabled ? '已开启' : '关闭' }}
          </button>
        </div>
        <van-field
          v-if="scheduleEnabled"
          v-model="scheduleCron"
          label="cron"
          placeholder="例如：0 23 * * *"
        />

        <div class="toggle-row">
          <span>语音控制</span>
          <button
            class="toggle-btn"
            :class="{ 'is-on': voiceEnabled }"
            type="button"
            @click="voiceEnabled = !voiceEnabled"
          >
            {{ voiceEnabled ? '已开启' : '关闭' }}
          </button>
        </div>
        <van-field
          v-if="voiceEnabled"
          v-model="voicePhrases"
          label="短语"
          placeholder="用逗号分隔，例如：离家，我出门了"
        />

        <ErrorBanner v-if="actionError" :error="actionError" />

        <div class="sheet-actions">
          <button class="primary-btn" type="button" :disabled="busy" @click="submit">
            {{ busy ? '保存中…' : '保存' }}
          </button>
          <button class="ghost-btn" type="button" :disabled="busy" @click="sheetOpen = false">
            取消
          </button>
        </div>
      </div>
    </van-popup>

    <!-- 能力选择 -->
    <van-popup v-model:show="capabilityPickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>选择能力</h3>
          <button class="text-btn" type="button" @click="capabilityPickerOpen = false">
            关闭
          </button>
        </header>
        <button
          v-for="item in capabilities"
          :key="item.capability"
          class="picker-row"
          type="button"
          @click="chooseCapability(item.capability)"
        >
          <span>{{ item.capability }}</span>
          <span class="picker-sub">{{ item.device_count }} 台设备</span>
        </button>
      </div>
    </van-popup>

    <!-- 执行结果：按设备粒度汇报，无回滚 -->
    <van-popup v-model:show="runSheetOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>执行结果 · {{ runSceneName }}</h3>
          <button class="text-btn" type="button" @click="runSheetOpen = false">关闭</button>
        </header>

        <template v-if="runResult">
          <p class="run-summary">
            {{ overallLabel[runResult.status] ?? runResult.status }} · 共
            {{ runResult.summary.total }} 台（成功 {{ runResult.summary.sent }} / 失败
            {{ runResult.summary.failed }} / 离线 {{ runResult.summary.offline }}）
          </p>
          <ul class="run-list">
            <li v-for="item in runResult.results" :key="`${item.device_id}-${item.capability}`" class="run-row">
              <div class="run-main">
                <strong>{{ item.device_name }}</strong>
                <span class="run-capability">{{ item.capability }}</span>
              </div>
              <span class="run-status" :class="`is-${item.status}`">
                {{ runStatusLabel[item.status] ?? item.status }}
              </span>
              <p class="run-message">{{ item.message }}</p>
            </li>
          </ul>
          <p class="sheet-note">已发出的信号不会因其他设备失败而撤销或重发。</p>
        </template>
      </div>
    </van-popup>
  </section>
</template>
