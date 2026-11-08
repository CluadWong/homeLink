<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { showConfirmDialog, showDialog, showFailToast, showToast } from 'vant'

import {
  createDevice,
  createRoom,
  deleteDevice,
  deleteFunction,
  deleteRoom,
  fetchDevices,
  fetchRooms,
  patchDevice,
  patchRoom,
} from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { Device, DeviceFunction, Room } from '../api/types'
import EmptyBlock from '../components/EmptyBlock.vue'
import ErrorBanner from '../components/ErrorBanner.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { refreshState } from '../composables/useStatusSummary'
import { deriveRoomName, sortOrderValue } from '../utils/format'

const devices = ref<Device[]>([])
const rooms = ref<Room[]>([])
const loading = ref(true)
const loadError = ref<ApiError | null>(null)
const actionError = ref<ApiError | null>(null)
const busy = ref(false)

const deviceSheetOpen = ref(false)
const editingDeviceId = ref<string | null>(null)
const deviceForm = ref({ name: '', room: '', notes: '' })

const roomPickerOpen = ref(false)
const roomSheetOpen = ref(false)
const editingRoomName = ref<string | null>(null)
const roomForm = ref({ displayName: '', sortOrder: '0' })

const serviceOffline = computed(() => loadError.value?.code === 'AGENT_UNREACHABLE')

const deviceGroups = computed(() => {
  const groups = rooms.value.map((room) => ({
    room,
    devices: devices.value.filter((device) => device.room === room.name),
  }))
  const orphaned = devices.value.filter(
    (device) => !rooms.value.some((room) => room.name === device.room),
  )
  if (orphaned.length) {
    groups.push({
      room: { name: '__orphaned', display_name: '未归属房间', sort_order: 999, device_count: orphaned.length },
      devices: orphaned,
    })
  }
  return groups
})

function deviceRoomLabel(roomName: string): string {
  return rooms.value.find((room) => room.name === roomName)?.display_name ?? roomName
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
    devices.value = []
    rooms.value = []
  } finally {
    loading.value = false
  }
}

async function afterWrite(): Promise<void> {
  await Promise.all([load(), refreshState()])
}

// ---------------------------------------------------------------- 设备

function openAddDevice(): void {
  if (rooms.value.length === 0) {
    showFailToast('还没有房间，请先在下方「房间管理」里新建一个房间')
    return
  }
  editingDeviceId.value = null
  deviceForm.value = { name: '', room: rooms.value[0].name, notes: '' }
  actionError.value = null
  deviceSheetOpen.value = true
}

function openEditDevice(device: Device): void {
  editingDeviceId.value = device.id
  deviceForm.value = { name: device.name, room: device.room, notes: device.notes ?? '' }
  actionError.value = null
  deviceSheetOpen.value = true
}

function chooseDeviceRoom(room: Room): void {
  deviceForm.value.room = room.name
  roomPickerOpen.value = false
}

async function submitDevice(): Promise<void> {
  const name = deviceForm.value.name.trim()
  const room = deviceForm.value.room
  if (!name) {
    showFailToast('设备名不能为空')
    return
  }
  if (!room) {
    showFailToast('请选择房间')
    return
  }
  busy.value = true
  actionError.value = null
  try {
    if (editingDeviceId.value) {
      await patchDevice(editingDeviceId.value, {
        name,
        room,
        notes: deviceForm.value.notes,
      })
      showToast({ message: '设备已更新', duration: 1500 })
    } else {
      await createDevice({ name, room, notes: deviceForm.value.notes || undefined })
      showToast({ message: '设备已添加', duration: 1500 })
    }
    deviceSheetOpen.value = false
    await afterWrite()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function removeDevice(device: Device): Promise<void> {
  try {
    await showConfirmDialog({
      title: '删除设备',
      message: `「${device.name}」及其全部学码功能、红外码会一并删除，无法恢复。`,
      confirmButtonText: '确认删除',
      confirmButtonColor: '#b3261e',
    })
  } catch {
    return
  }
  busy.value = true
  actionError.value = null
  try {
    await deleteDevice(device.id)
    showToast({ message: '设备已删除', duration: 1500 })
    await afterWrite()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function removeFunction(device: Device, item: DeviceFunction): Promise<void> {
  try {
    await showConfirmDialog({
      title: '删除学码功能',
      message: `「${item.name}」是学码功能，删除后会同时删除它的红外码，无法恢复。`,
      confirmButtonText: '确认删除',
      confirmButtonColor: '#b3261e',
    })
  } catch {
    return
  }
  busy.value = true
  actionError.value = null
  try {
    const payload = await deleteFunction(device.id, item.id)
    showToast({
      message: `功能已删除，同时清除 ${payload.deleted.codes} 条红外码`,
      duration: 2000,
    })
    await afterWrite()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- 房间

function openAddRoom(): void {
  editingRoomName.value = null
  roomForm.value = { displayName: '', sortOrder: `${rooms.value.length}` }
  actionError.value = null
  roomSheetOpen.value = true
}

function openEditRoom(room: Room): void {
  editingRoomName.value = room.name
  roomForm.value = { displayName: room.display_name, sortOrder: `${room.sort_order}` }
  actionError.value = null
  roomSheetOpen.value = true
}

async function submitRoom(): Promise<void> {
  const displayName = roomForm.value.displayName.trim()
  if (!displayName) {
    showFailToast('房间名不能为空')
    return
  }
  busy.value = true
  actionError.value = null
  try {
    if (editingRoomName.value) {
      await patchRoom(editingRoomName.value, {
        display_name: displayName,
        sort_order: sortOrderValue(roomForm.value.sortOrder),
      })
      showToast({ message: '房间已更新', duration: 1500 })
    } else {
      const internalName = deriveRoomName(
        displayName,
        rooms.value.map((room) => room.name),
      )
      await createRoom({
        name: internalName,
        display_name: displayName,
        sort_order: sortOrderValue(roomForm.value.sortOrder),
      })
      showToast({ message: '房间已添加', duration: 1500 })
    }
    roomSheetOpen.value = false
    await afterWrite()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

async function removeRoom(room: Room): Promise<void> {
  if (room.device_count > 0) {
    await showDialog({
      title: '不能删除这个房间',
      message: `「${room.display_name}」下还有 ${room.device_count} 台设备。请先把设备移到其他房间，或先删除这些设备，再删除房间。`,
      confirmButtonText: '知道了',
    })
    return
  }
  try {
    await showConfirmDialog({
      title: '删除房间',
      message: `确认删除空房间「${room.display_name}」？`,
      confirmButtonText: '确认删除',
      confirmButtonColor: '#b3261e',
    })
  } catch {
    return
  }
  busy.value = true
  actionError.value = null
  try {
    await deleteRoom(room.name)
    showToast({ message: '房间已删除', duration: 1500 })
    await afterWrite()
  } catch (error) {
    actionError.value = toApiError(error)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="page">
    <PageHeader
      index="02"
      title="设备"
      description="设备全部在运行期创建；设备不可启停，这里只管理名称、房间、备注与学码功能。"
    />

    <LoadingBlock v-if="loading" text="正在读取设备与房间" />

    <template v-else>
      <ErrorBanner :error="loadError" show-retry @retry="load" />
      <ErrorBanner :error="actionError" />

      <div v-if="serviceOffline" class="offline-panel">
        <p class="offline-note">管家服务连不上，设备与房间都不可读写。</p>
      </div>

      <div v-if="!loadError" class="toolbar">
        <button class="primary-btn" type="button" @click="openAddDevice">添加设备</button>
        <span class="toolbar-note">共 {{ devices.length }} 台设备 / {{ rooms.length }} 个房间</span>
      </div>

      <EmptyBlock
        v-if="!loadError && devices.length === 0"
        title="还没有设备"
        note="先添加一个房间（如已有房间可直接添加设备），设备添加后去学码页录制功能。"
      >
        <button class="action-link" type="button" @click="openAddDevice">添加第一台设备</button>
      </EmptyBlock>

      <div v-if="!loadError" class="room-groups">
        <section v-for="group in deviceGroups" :key="group.room.name" class="room-group">
          <header class="room-head">
            <h3>{{ group.room.display_name }}</h3>
            <span class="count-tag">{{ group.devices.length }} 台</span>
          </header>

          <p v-if="group.devices.length === 0" class="room-empty">暂无设备</p>

          <article v-for="device in group.devices" :key="device.id" class="card device-card">
            <header class="card-head">
              <div>
                <h4 class="card-title">{{ device.name }}</h4>
                <p class="card-meta">
                  {{ deviceRoomLabel(device.room) }}
                  <template v-if="device.notes"> · {{ device.notes }}</template>
                </p>
              </div>
              <div class="row-actions">
                <button class="text-btn" type="button" :disabled="busy" @click="openEditDevice(device)">
                  编辑
                </button>
                <button class="text-btn is-danger" type="button" :disabled="busy" @click="removeDevice(device)">
                  删除
                </button>
              </div>
            </header>

            <div v-if="device.functions.length === 0" class="inline-empty">
              还没有学码功能。<RouterLink :to="{ path: '/learn', query: { device_id: device.id } }">去学码</RouterLink>
            </div>

            <ul v-else class="function-list">
              <li v-for="item in device.functions" :key="item.id" class="function-row">
                <span class="function-label">{{ item.name }}</span>
                <button
                  class="text-btn is-danger"
                  type="button"
                  :disabled="busy"
                  @click="removeFunction(device, item)"
                >
                  删除功能
                </button>
              </li>
            </ul>
          </article>
        </section>
      </div>

      <section v-if="!loadError" class="card room-manage">
        <header class="card-head">
          <div>
            <h3 class="card-title">房间管理</h3>
            <p class="card-meta">只改显示名与排序；内部标识由前端自动派生，不需要填写英文名。</p>
          </div>
          <button class="text-btn" type="button" @click="openAddRoom">新建房间</button>
        </header>

        <ul class="room-list">
          <li v-for="room in rooms" :key="room.name" class="room-row">
            <div>
              <strong>{{ room.display_name }}</strong>
              <span class="room-count">{{ room.device_count }} 台设备</span>
            </div>
            <div class="row-actions">
              <button class="text-btn" type="button" :disabled="busy" @click="openEditRoom(room)">改名/排序</button>
              <button
                class="text-btn"
                :class="{ 'is-danger': room.device_count === 0 }"
                type="button"
                :disabled="busy"
                @click="removeRoom(room)"
              >
                删除
              </button>
            </div>
          </li>
        </ul>
      </section>
    </template>

    <!-- 设备表单 -->
    <van-popup v-model:show="deviceSheetOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>{{ editingDeviceId ? '编辑设备' : '添加设备' }}</h3>
          <button class="text-btn" type="button" @click="deviceSheetOpen = false">关闭</button>
        </header>

        <van-field
          v-model="deviceForm.name"
          label="设备名"
          placeholder="例如：客厅空调"
          maxlength="40"
          required
        />
        <button class="picker-trigger" type="button" @click="roomPickerOpen = true">
          <span>房间</span>
          <span class="picker-value">
            {{ deviceForm.room ? deviceRoomLabel(deviceForm.room) : '请选择房间' }}
          </span>
        </button>
        <van-field
          v-model="deviceForm.notes"
          label="备注"
          type="textarea"
          rows="2"
          autosize
          maxlength="200"
          placeholder="可选"
        />

        <div class="sheet-actions">
          <button class="primary-btn" type="button" :disabled="busy" @click="submitDevice">
            {{ busy ? '保存中…' : '保存' }}
          </button>
          <button class="ghost-btn" type="button" :disabled="busy" @click="deviceSheetOpen = false">取消</button>
        </div>
      </div>
    </van-popup>

    <!-- 房间选择 -->
    <van-popup v-model:show="roomPickerOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>选择房间</h3>
          <button class="text-btn" type="button" @click="roomPickerOpen = false">关闭</button>
        </header>
        <button
          v-for="room in rooms"
          :key="room.name"
          class="picker-row"
          :class="{ 'is-selected': deviceForm.room === room.name }"
          type="button"
          @click="chooseDeviceRoom(room)"
        >
          <span>{{ room.display_name }}</span>
          <span class="picker-sub">{{ room.device_count }} 台设备</span>
        </button>
      </div>
    </van-popup>

    <!-- 房间表单 -->
    <van-popup v-model:show="roomSheetOpen" position="bottom" round>
      <div class="sheet-body">
        <header class="sheet-head">
          <h3>{{ editingRoomName ? '编辑房间' : '新建房间' }}</h3>
          <button class="text-btn" type="button" @click="roomSheetOpen = false">关闭</button>
        </header>
        <van-field
          v-model="roomForm.displayName"
          label="房间名"
          placeholder="例如：书房"
          maxlength="30"
          required
        />
        <van-field
          v-model="roomForm.sortOrder"
          label="排序"
          type="digit"
          placeholder="数字越小越靠前"
        />
        <div class="sheet-actions">
          <button class="primary-btn" type="button" :disabled="busy" @click="submitRoom">
            {{ busy ? '保存中…' : '保存' }}
          </button>
          <button class="ghost-btn" type="button" :disabled="busy" @click="roomSheetOpen = false">取消</button>
        </div>
      </div>
    </van-popup>
  </section>
</template>
