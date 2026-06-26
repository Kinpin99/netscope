import { api, USE_MOCK } from './client'
import { mockDeviceDetail } from './mock/mockDevices'

export const getDevice = (ip) =>
  USE_MOCK ? Promise.resolve(mockDeviceDetail(ip)) : api.get(`/devices/${ip}`)

export const postBaseline = (ip) =>
  USE_MOCK ? Promise.resolve(mockDeviceDetail(ip)) : api.post(`/devices/${ip}/baseline`, {})

export const deleteBaseline = (ip) =>
  USE_MOCK ? Promise.resolve(null) : api.delete(`/devices/${ip}/baseline`)
