import { api, USE_MOCK } from './client'
import { mockSystemStatus } from './mock/mockSystem'

export const getSystemStatus = () =>
  USE_MOCK ? Promise.resolve(mockSystemStatus) : api.get('/system/status')

export const postRetrain = () =>
  USE_MOCK ? Promise.resolve({ ok: true }) : api.post('/system/retrain', {})
