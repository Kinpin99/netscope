import { useState, useMemo, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { getAlerts, getDistribution } from '../api/alerts'
import { issueTypeLabel } from '../utils/format'
import { SeverityBadge, AlertItem, EmptyState, Skeleton } from '../components/Shared'

export default function Alerts() {
  const [lastHours, setLastHours] = useState(24)
  const [severity, setSeverity] = useState('all')
  const [detector, setDetector] = useState('all')
  const [status, setStatus] = useState('all')
  const [deviceFilter, setDeviceFilter] = useState('')

  const params = useMemo(() => {
    const p = { last_hours: lastHours }
    if (severity !== 'all') p.severity = severity
    if (status !== 'all') p.status = status
    if (deviceFilter) p.device_ip = deviceFilter
    return p
  }, [lastHours, severity, status, deviceFilter])

  const fetchAlerts = useCallback(() => getAlerts(params), [params])
  const fetchDist = useCallback(() => getDistribution({ last_hours: lastHours }), [lastHours])

  const { data: alerts, loading } = usePolling(fetchAlerts, 30_000)
  const { data: distData } = usePolling(fetchDist, 60_000)

  // client-side detector filter (not a backend param in the mock layer)
  const filtered = useMemo(() => {
    if (!alerts) return []
    if (detector === 'all') return alerts
    return alerts.filter(a => a.detector === detector)
  }, [alerts, detector])

  const selectStyle = {
    padding: '6px 10px', fontSize: 11, background: 'var(--panel)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
    color: 'var(--text)', outline: 'none',
  }

  return (
    <div>
      {/* filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={lastHours} onChange={(e) => setLastHours(+e.target.value)} style={selectStyle}>
          <option value={1}>Last hour</option>
          <option value={6}>Last 6h</option>
          <option value={24}>Last 24h</option>
          <option value={168}>Last 7d</option>
        </select>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} style={selectStyle}>
          <option value="all">All Severity</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select value={detector} onChange={(e) => setDetector(e.target.value)} style={selectStyle}>
          <option value="all">All Detectors</option>
          <option value="bandwidth">Bandwidth</option>
          <option value="portscan">Port Scan</option>
          <option value="device_behavior">Device Behaviour</option>
          <option value="protocol">Protocol</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={selectStyle}>
          <option value="all">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
        <span className="dim" style={{ fontSize: 10, marginLeft: 'auto' }}>{filtered.length} alerts</span>
      </div>

      {/* distribution table */}
      {distData?.distribution?.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div className="section-label">Issue Distribution — last {lastHours}h</div>
          <div className="panel">
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: 'var(--panel-alt)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-dim)' }}>
                  <th style={{ textAlign: 'left', padding: '10px 16px', fontWeight: 600 }}>Entity</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Building</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Issues</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Max Severity</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Types</th>
                </tr>
              </thead>
              <tbody>
                {distData.distribution.map((row) => (
                  <tr
                    key={row.entity_id}
                    style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                    onClick={() => setDeviceFilter(row.entity_id)}
                    onMouseEnter={(e) => e.currentTarget.style.background = 'var(--panel-alt)'}
                    onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                  >
                    <td style={{ padding: '10px 16px' }}>
                      <div style={{ fontWeight: 500 }}>{row.device_name}</div>
                      <div className="mono dim" style={{ fontSize: 10 }}>{row.entity_id}</div>
                    </td>
                    <td className="dim" style={{ padding: '10px 8px' }}>{row.building}</td>
                    <td className="mono" style={{ padding: '10px 8px' }}>{row.issue_count}</td>
                    <td style={{ padding: '10px 8px' }}><SeverityBadge severity={row.max_severity} /></td>
                    <td className="dim" style={{ padding: '10px 8px', fontSize: 10 }}>
                      {row.issue_types.map(issueTypeLabel).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* alert history */}
      <div className="section-label">Alert History</div>
      {loading && !alerts ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} width="100%" height={44} />)}
        </div>
      ) : !filtered.length ? (
        <EmptyState message="No alerts match the current filters." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {filtered.map(a => <AlertItem key={a.alert_id} alert={a} />)}
        </div>
      )}
    </div>
  )
}
