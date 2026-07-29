import { api, USE_MOCK } from './client'
import { mockOpenAlerts, mockAllAlerts, mockDistribution, mockHealthScores } from './mock/mockAlerts'

const normaliseAlert = (alert = {}) => ({
  ...alert,
  alert_id: alert.alert_id || alert.id,
  anomaly_score: alert.anomaly_score ?? alert.last_score ?? alert.max_score ?? null,
  window: alert.window ?? alert.last_window ?? alert.created_at ?? null,
  features: alert.features || {},
})

const normaliseAlerts = (alerts) => (Array.isArray(alerts) ? alerts.map(normaliseAlert) : [])

export const getOpenAlerts = () =>
  USE_MOCK
    ? Promise.resolve(mockOpenAlerts)
    : api.get('/alerts/open').then(r => normaliseAlerts(r.alerts || []))

export const getAlerts = (params = {}) => {
  if (USE_MOCK) {
    let filtered = [...mockAllAlerts]
    if (params.severity) filtered = filtered.filter(a => a.severity === params.severity)
    if (params.status) filtered = filtered.filter(a => a.status === params.status)
    if (params.device_ip) filtered = filtered.filter(a => a.entity_id === params.device_ip)
    return Promise.resolve(filtered)
  }
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.set(k, v) })
  return api.get(`/alerts?${qs}`).then(r => normaliseAlerts(r.alerts || r))
}

export const getDistribution = (params = {}) => {
  if (USE_MOCK) return Promise.resolve(mockDistribution)
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.set(k, v) })
  return api.get(`/alerts/distribution?${qs}`)
}

export const getHealthScores = () =>
  USE_MOCK
    ? Promise.resolve(mockHealthScores)
    : api.get('/alerts/health-scores').then(r => {
        const raw = r.health_scores || r || {}
        return Object.fromEntries(
          Object.entries(raw).map(([ip, value]) => [ip, typeof value === 'object' ? value?.health_score : value])
        )
      })
