import { api } from './client'

export const getConfiguredDevices = () =>
  api.get('/settings/devices').then(r => r?.devices || [])

export const createConfiguredDevice = (payload) =>
  api.post('/settings/devices', payload).then(r => r?.device || r)

export const updateConfiguredDevice = (originalIp, payload) =>
  api.put(`/settings/devices/${encodeURIComponent(originalIp)}`, payload).then(r => r?.device || r)

export const deleteConfiguredDevice = (ip) =>
  api.delete(`/settings/devices/${encodeURIComponent(ip)}`)
