import { api } from './client'

export async function getOnboardingDevices(status) {
  const path = status ? `/onboarding/devices?status_filter=${encodeURIComponent(status)}` : '/onboarding/devices'
  const data = await api.get(path)
  return data.devices || []
}

export async function getPendingDevices() {
  const data = await api.get('/onboarding/pending')
  return data.devices || []
}

export async function approveDevice(deviceId, payload) {
  return api.post(`/onboarding/devices/${encodeURIComponent(deviceId)}/approve`, payload)
}

export async function rejectDevice(deviceId) {
  return api.post(`/onboarding/devices/${encodeURIComponent(deviceId)}/reject`, {})
}

export async function getGeneratedConfig(deviceId) {
  return api.get(`/onboarding/devices/${encodeURIComponent(deviceId)}/config`)
}

export async function phoneHome(payload) {
  return api.post('/onboarding/phone-home', payload)
}
