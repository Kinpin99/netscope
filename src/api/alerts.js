import { api, USE_MOCK } from './client'
import { mockOpenAlerts, mockAllAlerts, mockDistribution, mockHealthScores } from './mock/mockAlerts'

export const getOpenAlerts = () =>
  USE_MOCK ? Promise.resolve(mockOpenAlerts) : api.get('/alerts/open')

export const getAlerts = (params = {}) => {
  if (USE_MOCK) {
    let filtered = [...mockAllAlerts]
    if (params.severity) filtered = filtered.filter(a => a.severity === params.severity)
    if (params.status) filtered = filtered.filter(a => a.status === params.status)
    if (params.device_ip) filtered = filtered.filter(a => a.entity_id === params.device_ip)
    return Promise.resolve(filtered)
  }
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v) qs.set(k, v) })
  return api.get(`/alerts?${qs}`)
}

export const getDistribution = (params = {}) => {
  if (USE_MOCK) return Promise.resolve(mockDistribution)
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v) qs.set(k, v) })
  return api.get(`/alerts/distribution?${qs}`)
}

export const getHealthScores = () =>
  USE_MOCK ? Promise.resolve(mockHealthScores) : api.get('/alerts/health-scores')
