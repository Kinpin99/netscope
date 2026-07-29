import { useCallback, useMemo, useState } from 'react'
import { CheckCircle, XCircle, FileText, RefreshCcw } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { useAuth } from '../context/AuthContext'
import { getOnboardingDevices, approveDevice, rejectDevice, getGeneratedConfig } from '../api/onboarding'
import { EmptyState, ErrorBanner, Skeleton } from '../components/Shared'
import { timeAgo } from '../utils/format'

const inputStyle = {
  padding: '7px 9px', fontSize: 11, background: 'var(--panel)',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  color: 'var(--text)', outline: 'none', width: '100%',
}

const buttonStyle = {
  padding: '6px 10px', fontSize: 11, border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--panel-alt)', color: 'var(--text)',
  display: 'inline-flex', alignItems: 'center', gap: 6,
}

function statusColor(status) {
  const map = {
    pending: 'var(--sev-medium)',
    config_ready: 'var(--sev-info)',
    provisioned: 'var(--accent)',
    rejected: 'var(--sev-critical)',
    config_failed: 'var(--sev-critical)',
  }
  return map[status] || 'var(--text-dim)'
}

function StatusPill({ status }) {
  return (
    <span className="mono" style={{ color: statusColor(status), fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
      {status || 'unknown'}
    </span>
  )
}

function ApprovalForm({ device, onDone }) {
  const approval = device.approval || {}
  const [form, setForm] = useState({
    name: approval.name || device.hostname || `${device.device_type || 'device'}-${device.serial_number}`,
    device_type: approval.device_type || device.device_type || 'access_point',
    role: approval.role || 'Wireless Access Point',
    building: approval.building || 'Unassigned',
    floor: approval.floor || '',
    data_ip: approval.data_ip || device.data_ip || device.management_ip || '',
    management_ip: approval.management_ip || device.management_ip || '',
    snmp_community: approval.snmp_community || 'public',
    snmp_if_index: approval.snmp_if_index || 1,
    if_speed_bps: approval.if_speed_bps || 100000000,
    monitoring_enabled: approval.monitoring_enabled !== false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }))

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      await approveDevice(device.id, {
        ...form,
        snmp_if_index: Number(form.snmp_if_index || 1),
        if_speed_bps: Number(form.if_speed_bps || 100000000),
      })
      onDone?.()
    } catch (err) {
      setError(err.message || 'Approval failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)', marginTop: 12, paddingTop: 12 }}>
      {error && <ErrorBanner message={error} />}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Device Name<input style={inputStyle} value={form.name} onChange={e => set('name', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Type
          <select style={inputStyle} value={form.device_type} onChange={e => set('device_type', e.target.value)}>
            <option value="access_point">Access Point</option>
            <option value="switch">Switch</option>
            <option value="router">Router</option>
            <option value="server">Server</option>
          </select>
        </label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Role<input style={inputStyle} value={form.role} onChange={e => set('role', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Building<input style={inputStyle} value={form.building} onChange={e => set('building', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Floor<input style={inputStyle} value={form.floor} onChange={e => set('floor', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Data IP<input style={inputStyle} value={form.data_ip} onChange={e => set('data_ip', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>Management IP<input style={inputStyle} value={form.management_ip} onChange={e => set('management_ip', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>SNMP Community<input style={inputStyle} value={form.snmp_community} onChange={e => set('snmp_community', e.target.value)} /></label>
        <label style={{ fontSize: 10, color: 'var(--text-dim)' }}>SNMP ifIndex<input style={inputStyle} value={form.snmp_if_index} onChange={e => set('snmp_if_index', e.target.value)} /></label>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
        <button onClick={submit} disabled={busy} style={{ ...buttonStyle, color: 'var(--accent)' }}>
          <CheckCircle size={13} /> {busy ? 'Approving...' : 'Approve + Enroll' }
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-dim)' }}>
          <input type="checkbox" checked={form.monitoring_enabled} onChange={e => set('monitoring_enabled', e.target.checked)} />
          Register for monitoring
        </label>
      </div>
    </div>
  )
}

function DeviceCard({ device, isAdmin, onRefresh }) {
  const [expanded, setExpanded] = useState(false)
  const [config, setConfig] = useState(null)
  const [error, setError] = useState('')

  const loadConfig = async () => {
    setError('')
    try {
      const result = await getGeneratedConfig(device.id)
      setConfig(result)
      setExpanded(true)
    } catch (err) {
      setError(err.message || 'Unable to load config')
    }
  }

  const reject = async () => {
    setError('')
    try {
      await rejectDevice(device.id)
      onRefresh?.()
    } catch (err) {
      setError(err.message || 'Reject failed')
    }
  }

  return (
    <div className="panel" style={{ padding: 14 }}>
      {error && <ErrorBanner message={error} />}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
            <strong style={{ fontSize: 13 }}>{device.hostname || device.serial_number}</strong>
            <StatusPill status={device.status} />
          </div>
          <div className="mono dim" style={{ fontSize: 10 }}>{device.id}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {device.config_path && <button style={buttonStyle} onClick={loadConfig}><FileText size={13} />Config</button>}
          {isAdmin && device.status !== 'rejected' && <button style={{ ...buttonStyle, color: 'var(--sev-critical)' }} onClick={reject}><XCircle size={13} />Reject</button>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginTop: 12, fontSize: 11 }}>
        <Info label="Serial" value={device.serial_number} mono />
        <Info label="MAC" value={device.mac_address} mono />
        <Info label="Model" value={device.model} />
        <Info label="Type" value={device.device_type} />
        <Info label="Mgmt IP" value={device.management_ip} mono />
        <Info label="Data IP" value={device.data_ip || device.approval?.data_ip} mono />
        <Info label="Last Seen" value={device.last_seen ? timeAgo(device.last_seen) : '—'} />
        <Info label="Phone Homes" value={device.phone_home_count} mono />
      </div>

      {isAdmin && device.status === 'pending' && <ApprovalForm device={device} onDone={onRefresh} />}

      {expanded && config && (
        <pre style={{ marginTop: 12, padding: 12, background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'auto', fontSize: 11, lineHeight: 1.5 }}>
          {config.config_text || JSON.stringify(config.config, null, 2)}
        </pre>
      )}
    </div>
  )
}

function Info({ label, value, mono }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2 }}>{label}</div>
      <div className={mono ? 'mono' : ''} style={{ fontSize: 11 }}>{value || '—'}</div>
    </div>
  )
}

export default function Onboarding() {
  const { user } = useAuth()
  const isAdmin = user?.role_code === 'admin'
  const [filter, setFilter] = useState('all')
  const fetchDevices = useCallback(() => getOnboardingDevices(), [])
  const { data, loading, error, refresh } = usePolling(fetchDevices, 10000)

  const devices = data || []
  const filtered = useMemo(() => filter === 'all' ? devices : devices.filter(d => d.status === filter), [devices, filter])
  const counts = useMemo(() => devices.reduce((acc, d) => ({ ...acc, [d.status]: (acc[d.status] || 0) + 1 }), {}), [devices])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16, gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>Automated Onboarding</h2>
          <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-dim)' }}>
            ZTP-lite workflow for simulated APs, switches, routers, and servers. New devices phone home, wait for approval, receive generated config, then become monitored devices.
          </p>
        </div>
        <button style={buttonStyle} onClick={refresh}><RefreshCcw size={13} />Refresh</button>
      </div>

      {!isAdmin && <ErrorBanner message="Read-only view. Admin privileges are required to approve or reject devices." />}
      {error && <ErrorBanner message={error || 'Unable to load onboarding devices'} onRetry={refresh} />}

      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        {['all', 'pending', 'config_ready', 'provisioned', 'rejected'].map(item => (
          <button key={item} onClick={() => setFilter(item)} style={{ ...buttonStyle, background: filter === item ? 'rgba(74,222,128,0.08)' : 'var(--panel-alt)' }}>
            {item.replace('_', ' ')} {item !== 'all' && counts[item] ? `(${counts[item]})` : ''}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} width="100%" height={140} />)}
        </div>
      ) : !filtered.length ? (
        <EmptyState message="No onboarding devices found" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {filtered.map(device => <DeviceCard key={device.id} device={device} isAdmin={isAdmin} onRefresh={refresh} />)}
        </div>
      )}
    </div>
  )
}
