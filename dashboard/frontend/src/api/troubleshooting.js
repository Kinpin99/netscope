import { api, USE_MOCK } from './client'

const mockIncident = {
  id: 'demo-root-cause-001',
  title: 'Interface Down on core-router-01',
  summary: 'A link-down syslog event was correlated with downstream alerts.',
  severity: 'high',
  confidence: 82,
  root_cause_type: 'interface_down',
  root_cause_device: { ip: '10.0.0.5', name: 'core-router-01', building: 'HQ', device_type: 'router' },
  affected_devices: [
    { ip: '10.0.0.6', name: 'edge-switch-floor2', building: 'HQ', device_type: 'switch' },
  ],
  evidence: [
    { type: 'syslog', label: 'Syslog interface_down on core-router-01', severity: 'high', detail: 'Interface eth1 changed state to down' },
    { type: 'alert', label: 'bandwidth anomaly on 10.0.0.6', severity: 'medium', detail: 'network_congestion' },
  ],
  recommendations: [
    'Check the affected cable/uplink and switch port status.',
    'Verify power and transceiver status on both ends of the link.',
  ],
}

export async function getTroubleshootingIncidents(params = {}) {
  if (USE_MOCK) return { incidents: [mockIncident], syslog_event_count: 1, alert_count: 1, window_hours: params.last_hours || 24 }
  const qs = new URLSearchParams(params).toString()
  return api.get(`/troubleshooting/incidents${qs ? `?${qs}` : ''}`)
}

export async function getSyslogEvents(params = {}) {
  if (USE_MOCK) return { events: [] }
  const qs = new URLSearchParams(params).toString()
  return api.get(`/troubleshooting/syslogs${qs ? `?${qs}` : ''}`)
}

export async function ingestSyslog(line, device_ip = '') {
  if (USE_MOCK) return { line, device_ip, event_type: 'interface_down', severity: 'high' }
  return api.post('/troubleshooting/syslogs', { line, device_ip: device_ip || null })
}

export async function getTopologyGraph() {
  if (USE_MOCK) return { nodes: [], edges: [] }
  return api.get('/topology/graph')
}
