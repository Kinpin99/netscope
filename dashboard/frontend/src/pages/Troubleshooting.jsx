import { useCallback, useMemo, useState } from 'react'
import { RefreshCcw, Send, Network, Wrench } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { getTroubleshootingIncidents, getSyslogEvents, ingestSyslog, getTopologyGraph } from '../api/troubleshooting'
import { EmptyState, ErrorBanner, SeverityBadge, Skeleton } from '../components/Shared'
import { timeAgo } from '../utils/format'
import { useAuth } from '../context/AuthContext'

const inputStyle = {
  padding: '7px 9px', fontSize: 11, background: 'var(--panel)',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  color: 'var(--text)', outline: 'none', width: '100%',
}

const buttonStyle = {
  padding: '7px 10px', fontSize: 11, border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--panel-alt)', color: 'var(--text)',
  display: 'inline-flex', alignItems: 'center', gap: 6,
}

function isAdmin(user) {
  return user?.role_code === 'admin' || String(user?.role || '').toLowerCase().includes('admin')
}

function IncidentCard({ incident }) {
  const [open, setOpen] = useState(false)
  const root = incident.root_cause_device
  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <button onClick={() => setOpen(!open)} style={{ width: '100%', textAlign: 'left', padding: 14, display: 'flex', gap: 12, alignItems: 'center' }}>
        <Wrench size={16} color="var(--text-dim)" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <SeverityBadge severity={incident.severity} />
            <span style={{ fontSize: 12, fontWeight: 600 }}>{incident.title}</span>
          </div>
          <div className="dim" style={{ fontSize: 11 }}>{incident.summary}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{ color: 'var(--accent)', fontSize: 16 }}>{incident.confidence}%</div>
          <div className="dim" style={{ fontSize: 10 }}>confidence</div>
        </div>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 14 }}>
            <div>
              <div className="section-label">Likely Root Cause</div>
              <div style={{ fontSize: 12, fontWeight: 600 }}>{root?.name || 'Unknown'}</div>
              <div className="mono dim" style={{ fontSize: 10 }}>{root?.ip || 'No topology match'}</div>
              <div className="dim" style={{ fontSize: 10 }}>{incident.root_cause_type?.replaceAll('_', ' ')}</div>
            </div>
            <div>
              <div className="section-label">Affected Devices</div>
              {(incident.affected_devices || []).length ? incident.affected_devices.map((d) => (
                <div key={d.ip || d.name} style={{ fontSize: 11, marginBottom: 4 }}>
                  {d.name || d.ip} <span className="mono dim">{d.ip}</span>
                </div>
              )) : <span className="dim" style={{ fontSize: 11 }}>No downstream devices identified.</span>}
            </div>
          </div>

          <div className="section-label">Evidence</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
            {(incident.evidence || []).map((ev, idx) => (
              <div key={idx} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '8px 10px' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 3 }}>
                  <SeverityBadge severity={ev.severity || 'info'} />
                  <span style={{ fontSize: 11 }}>{ev.label}</span>
                </div>
                {ev.detail && <div className="dim" style={{ fontSize: 10 }}>{ev.detail}</div>}
              </div>
            ))}
          </div>

          <div className="section-label">Recommended Actions</div>
          <ul style={{ paddingLeft: 18, color: 'var(--text-dim)', fontSize: 11 }}>
            {(incident.recommendations || []).map((rec, idx) => <li key={idx} style={{ marginBottom: 4 }}>{rec}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function Troubleshooting() {
  const { user } = useAuth()
  const [lastHours, setLastHours] = useState(24)
  const [syslogLine, setSyslogLine] = useState('<189>Jun 27 12:03:44 core-router-01 %LINK-3-UPDOWN: Interface eth1 changed state to down')
  const [deviceIp, setDeviceIp] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const params = useMemo(() => ({ last_hours: lastHours }), [lastHours])
  const fetchIncidents = useCallback(() => getTroubleshootingIncidents(params), [params])
  const fetchSyslogs = useCallback(() => getSyslogEvents({ last_hours: lastHours, limit: 25 }), [lastHours])
  const fetchGraph = useCallback(() => getTopologyGraph(), [])

  const { data, loading, error: loadError, refresh } = usePolling(fetchIncidents, 30_000)
  const { data: syslogData, refresh: refreshSyslogs } = usePolling(fetchSyslogs, 30_000)
  const { data: graph } = usePolling(fetchGraph, 60_000)

  const submitSyslog = async () => {
    setBusy(true)
    setError('')
    try {
      await ingestSyslog(syslogLine, deviceIp)
      await refresh()
      await refreshSyslogs()
    } catch (err) {
      setError(err.message || 'Failed to ingest syslog')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={lastHours} onChange={(e) => setLastHours(+e.target.value)} style={{ ...inputStyle, width: 130 }}>
          <option value={1}>Last hour</option>
          <option value={6}>Last 6h</option>
          <option value={24}>Last 24h</option>
          <option value={168}>Last 7d</option>
        </select>
        <button style={buttonStyle} onClick={refresh}><RefreshCcw size={13} /> Refresh</button>
        <span className="dim" style={{ fontSize: 10, marginLeft: 'auto' }}>
          {data?.syslog_event_count || 0} syslogs · {data?.alert_count || 0} alerts · {graph?.nodes?.length || 0} topology nodes
        </span>
      </div>

      {loadError && <ErrorBanner message={loadError.message || 'Failed to load incidents'} onRetry={refresh} />}

      {isAdmin(user) && (
        <div className="panel" style={{ padding: 14, marginBottom: 18 }}>
          <div className="section-label">Manual Syslog Test</div>
          {error && <ErrorBanner message={error} />}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 170px auto', gap: 10 }}>
            <input style={inputStyle} value={syslogLine} onChange={(e) => setSyslogLine(e.target.value)} placeholder="Paste a syslog line" />
            <input style={inputStyle} value={deviceIp} onChange={(e) => setDeviceIp(e.target.value)} placeholder="Device IP optional" />
            <button style={buttonStyle} disabled={busy} onClick={submitSyslog}><Send size={13} /> Ingest</button>
          </div>
          <div className="dim" style={{ marginTop: 8, fontSize: 10 }}>
            Production devices can also send UDP syslog to collectors/syslog_collector.py.
          </div>
        </div>
      )}

      <div className="section-label">Root Cause Incidents</div>
      {loading && !data ? (
        <div>{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} width="100%" height={70} style={{ marginBottom: 8 }} />)}</div>
      ) : !(data?.incidents || []).length ? (
        <EmptyState message="No troubleshooting incidents found in this time window." />
      ) : (
        (data.incidents || []).map((incident) => <IncidentCard key={incident.id} incident={incident} />)
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14, marginTop: 18 }}>
        <div className="panel" style={{ padding: 14 }}>
          <div className="section-label"><Network size={13} /> Topology Graph Summary</div>
          <div className="mono" style={{ fontSize: 18, color: 'var(--accent)' }}>{graph?.nodes?.length || 0}</div>
          <div className="dim" style={{ fontSize: 11 }}>nodes</div>
          <div className="mono" style={{ fontSize: 18, color: 'var(--accent)', marginTop: 10 }}>{graph?.edges?.length || 0}</div>
          <div className="dim" style={{ fontSize: 11 }}>directed dependency links</div>
          {graph?.warning && <div className="dim" style={{ fontSize: 10, marginTop: 10 }}>{graph.warning}</div>}
        </div>

        <div className="panel" style={{ padding: 14 }}>
          <div className="section-label">Recent Syslogs</div>
          {(syslogData?.events || []).length ? (syslogData.events || []).slice(0, 6).map((ev) => (
            <div key={ev.id} style={{ borderBottom: '1px solid var(--border)', padding: '7px 0' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <SeverityBadge severity={ev.severity || 'info'} />
                <span style={{ fontSize: 11 }}>{ev.event_type}</span>
                <span className="mono dim" style={{ fontSize: 10, marginLeft: 'auto' }}>{timeAgo(ev.received_at || ev.timestamp)}</span>
              </div>
              <div className="dim" style={{ fontSize: 10, marginTop: 3 }}>{ev.message}</div>
            </div>
          )) : <span className="dim" style={{ fontSize: 11 }}>No syslogs in this time window.</span>}
        </div>
      </div>
    </div>
  )
}
