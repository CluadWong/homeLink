/**
 * 顶部常驻状态摘要的数据源：设备概况 + 网关状态。
 * 只轮询只读接口；请求失败时清空旧数据，不展示陈旧状态。
 */

import { reactive, ref } from 'vue'

import { fetchGateway, fetchState } from '../api/client'
import { toApiError, type ApiError } from '../api/errors'
import type { GatewayStatus, Sensors, StateDevice } from '../api/types'

const stateDevices = ref<StateDevice[]>([])
const sensors = ref<Sensors | null>(null)
const gateway = ref<GatewayStatus | null>(null)
const stateError = ref<ApiError | null>(null)
const gatewayError = ref<ApiError | null>(null)

const snapshot = reactive({
  devices: stateDevices,
  sensors,
  gateway,
  stateError,
  gatewayError,
})

let stateInFlight: Promise<void> | null = null
let gatewayInFlight: Promise<void> | null = null

export async function refreshState(): Promise<void> {
  if (stateInFlight) return stateInFlight
  stateInFlight = (async () => {
    try {
      const payload = await fetchState()
      stateDevices.value = payload.devices
      sensors.value = payload.sensors
      stateError.value = null
    } catch (error) {
      stateError.value = toApiError(error)
      stateDevices.value = []
      sensors.value = null
    } finally {
      stateInFlight = null
    }
  })()
  return stateInFlight
}

export async function refreshGateway(refresh = false): Promise<void> {
  if (gatewayInFlight) return gatewayInFlight
  gatewayInFlight = (async () => {
    try {
      const payload = await fetchGateway(refresh)
      gateway.value = payload
      gatewayError.value = null
    } catch (error) {
      gatewayError.value = toApiError(error)
      gateway.value = null
    } finally {
      gatewayInFlight = null
    }
  })()
  return gatewayInFlight
}

export function useStatusSummary() {
  return {
    snapshot,
    refreshState,
    refreshGateway,
  }
}
