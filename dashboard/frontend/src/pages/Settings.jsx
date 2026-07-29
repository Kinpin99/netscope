import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, Pencil, Trash2, Save, X, ShieldAlert, RefreshCw } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { getConfiguredDevices, createConfiguredDevice, updateConfiguredDevice, deleteConfiguredDevice } from '../api/settings'
import { EmptyState, Skeleton } from '../components/Shared'

const DEVICE_TYPES = [
  ['router', 'Router'],
  ['switch', 'Switch'],
  ['access_point', 'Access point'],
  ['server', 'Server'],
  ['client', 'Client'],
  ['firewall', 'Firewall'],
  ['controller', 'Controller'],
  ['network_device', 'Other network device'],
]

const EMPTY_FORM = {
  ip: '',
  name: '',
  building: 'Unassigned',
  floor: '',
  device_type: 'network_device',
  role: '',
  serial_number: '',
  mac_address: '',
  snmp_enabled: false,
  snmp_host: '',
  snmp_community: 'public',
  snmp_port: 161,
  snmp_if_index: 1,
  snmp_if_speed_mbps: 100,
}

function formFromDevice(device) {
  return {
    ip: device.ip || '',
    name: device.name || '',
    building: device.building || 'Unassigned',
    floor: device.floor || '',
    device_type: device.device_type || 'network_device',
    role: device.role || '',
    serial_number: device.serial_number || '',
    mac_address: device.mac_address || '',
    snmp_enabled: Boolean(device.snmp?.enabled),
    snmp_host: device.snmp?.host || device.ip || '',
    snmp_community: device.snmp?.community || 'public',
    snmp_port: device.snmp?.port || 161,
    snmp_if_index: device.snmp?.if_index || 1,
    snmp_if_speed_mbps: Math.max(1, Math.round((device.snmp?.if_speed_bps || 100000000) / 1000000)),
  }
}

function payloadFromForm(form) {
  return {
    ip: form.ip.trim(),
    name: form.name.trim(),
    building: form.building.trim() || 'Unassigned',
    floor: form.floor.trim() || null,
    device_type: form.device_type,
    role: form.role.trim() || null,
    source: 'settings_page',
    serial_number: form.serial_number.trim() || null,
    mac_address: form.mac_address.trim() || null,
    snmp: {
      enabled: Boolean(form.snmp_enabled),
      host: (form.snmp_host || form.ip).trim(),
      community: form.snmp_community.trim() || 'public',
      port: Number(form.snmp_port) || 161,
      if_index: Number(form.snmp_if_index) || 1,
      if_speed_bps: Math.max(1, Number(form.snmp_if_speed_mbps) || 100) * 1000000,
      output_ip: form.ip.trim(),
    },
  }
}

function Field({ label, children, hint }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>{hint}</span>}
    </label>
  )
}

const inputStyle = {
  width: '100%', height: 36, padding: '0 10px', borderRadius: 'var(--radius)',
  border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)',
  outline: 'none', fontFamily: 'inherit', fontSize: 11,
}

export default function Settings() {
  const { user } = useAuth()
  const isAdmin = user?.role_code === 'admin' || String(user?.role || '').toLowerCase().includes('admin')
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState(EMPTY_FORM)
  const [editingIp, setEditingIp] = useState(null)
  const [formOpen, setFormOpen] = useState(false)

  const load = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      setDevices(await getConfiguredDevices())
      setError('')
    } catch (err) {
      setError(err?.message || 'Unable to load configured devices')
    } finally {
      setLoading(false)
    }
  }, [isAdmin])

  useEffect(() => {
    const id = window.setTimeout(load, 0)
    return () => window.clearTimeout(id)
  }, [load])

  const buildings = useMemo(() => Array.from(new Set(devices.map(d => d.building).filter(Boolean))).sort(), [devices])

  const updateField = (field, value) => setForm(current => ({ ...current, [field]: value }))

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setEditingIp(null)
    setFormOpen(true)
    setError('')
    setNotice('')
  }

  const openEdit = (device) => {
    setForm(formFromDevice(device))
    setEditingIp(device.ip)
    setFormOpen(true)
    setError('')
    setNotice('')
  }

  const closeForm = () => {
    setFormOpen(false)
    setEditingIp(null)
    setForm(EMPTY_FORM)
  }

  const saveDevice = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const payload = payloadFromForm(form)
      if (editingIp) await updateConfiguredDevice(editingIp, payload)
      else await createConfiguredDevice(payload)
      await load()
      closeForm()
      setNotice(`Device ${editingIp ? 'updated' : 'added'} successfully. Collectors will use the new inventory on their next start.`)
    } catch (err) {
      setError(err?.message || 'Unable to save device')
    } finally {
      setSaving(false)
    }
  }

  const removeDevice = async (device) => {
    const confirmed = window.confirm(`Remove ${device.name} (${device.ip}) from monitoring? This does not erase historical telemetry.`)
    if (!confirmed) return
    setError('')
    setNotice('')
    try {
      await deleteConfiguredDevice(device.ip)
      await load()
      setNotice(`${device.name} was removed from the configured inventory.`)
    } catch (err) {
      setError(err?.message || 'Unable to remove device')
    }
  }

  if (!isAdmin) {
    return (
      <div className="panel" style={{ padding: 24, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <ShieldAlert size={20} color="var(--sev-medium)" />
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Administrator access required</div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 5 }}>Only NOC administrators can change the monitored device inventory.</div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>System Settings</div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>Add and maintain monitored devices without editing config.yaml manually.</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={load} title="Refresh" style={{ width: 34, height: 34, display: 'grid', placeItems: 'center', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--panel)' }}>
            <RefreshCw size={14} />
          </button>
          <button onClick={openCreate} style={{ height: 34, padding: '0 12px', display: 'flex', alignItems: 'center', gap: 6, borderRadius: 'var(--radius)', background: 'var(--accent)', color: '#0B0E14', fontSize: 11, fontWeight: 600 }}>
            <Plus size={14} /> Add device
          </button>
        </div>
      </div>

      {error && <div className="login-banner login-banner--error" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div style={{ padding: '10px 12px', marginBottom: 12, borderRadius: 'var(--radius)', border: '1px solid rgba(74,222,128,.25)', background: 'rgba(74,222,128,.07)', color: 'var(--accent)', fontSize: 11 }}>{notice}</div>}

      {formOpen && (
        <form className="panel" onSubmit={saveDevice} style={{ padding: 16, marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{editingIp ? 'Edit device' : 'Add monitored device'}</div>
            <button type="button" onClick={closeForm} style={{ color: 'var(--text-dim)' }}><X size={16} /></button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12 }}>
            <Field label="Device name"><input style={inputStyle} value={form.name} onChange={e => updateField('name', e.target.value)} required placeholder="core-router-01" /></Field>
            <Field label="IP address"><input style={inputStyle} value={form.ip} onChange={e => updateField('ip', e.target.value)} required placeholder="10.0.0.5" /></Field>
            <Field label="Device type">
              <select style={inputStyle} value={form.device_type} onChange={e => updateField('device_type', e.target.value)}>
                {DEVICE_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="Building">
              <input style={inputStyle} list="settings-buildings" value={form.building} onChange={e => updateField('building', e.target.value)} placeholder="HQ" />
              <datalist id="settings-buildings">{buildings.map(item => <option key={item} value={item} />)}</datalist>
            </Field>
            <Field label="Floor"><input style={inputStyle} value={form.floor} onChange={e => updateField('floor', e.target.value)} placeholder="2" /></Field>
            <Field label="Role"><input style={inputStyle} value={form.role} onChange={e => updateField('role', e.target.value)} placeholder="Core Router" /></Field>
            <Field label="Serial number"><input style={inputStyle} value={form.serial_number} onChange={e => updateField('serial_number', e.target.value)} /></Field>
            <Field label="MAC address"><input style={inputStyle} value={form.mac_address} onChange={e => updateField('mac_address', e.target.value)} placeholder="00:11:22:33:44:55" /></Field>
          </div>

          <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontWeight: 600 }}>
              <input type="checkbox" checked={form.snmp_enabled} onChange={e => updateField('snmp_enabled', e.target.checked)} />
              Poll this device using SNMP / PRTG-style collection
            </label>

            {form.snmp_enabled && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12, marginTop: 12 }}>
                <Field label="SNMP host" hint="Defaults to the device IP."><input style={inputStyle} value={form.snmp_host} onChange={e => updateField('snmp_host', e.target.value)} placeholder={form.ip || '10.0.0.5'} /></Field>
                <Field label="Community"><input type="password" style={inputStyle} value={form.snmp_community} onChange={e => updateField('snmp_community', e.target.value)} /></Field>
                <Field label="Port"><input type="number" min="1" max="65535" style={inputStyle} value={form.snmp_port} onChange={e => updateField('snmp_port', e.target.value)} /></Field>
                <Field label="Interface index"><input type="number" min="1" style={inputStyle} value={form.snmp_if_index} onChange={e => updateField('snmp_if_index', e.target.value)} /></Field>
                <Field label="Interface speed (Mbps)"><input type="number" min="1" style={inputStyle} value={form.snmp_if_speed_mbps} onChange={e => updateField('snmp_if_speed_mbps', e.target.value)} /></Field>
              </div>
            )}
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
            <button type="button" onClick={closeForm} style={{ height: 34, padding: '0 12px', border: '1px solid var(--border)', borderRadius: 'var(--radius)', fontSize: 11 }}>Cancel</button>
            <button type="submit" disabled={saving} style={{ height: 34, padding: '0 13px', display: 'flex', alignItems: 'center', gap: 6, background: 'var(--accent)', color: '#0B0E14', borderRadius: 'var(--radius)', fontSize: 11, fontWeight: 600, opacity: saving ? .6 : 1 }}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save device'}
            </button>
          </div>
        </form>
      )}

      <div className="section-label">Configured Device Inventory</div>
      <div className="panel" style={{ overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 16 }}><Skeleton width="100%" height={180} /></div>
        ) : devices.length ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: 'var(--panel-alt)', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '.05em', fontSize: 9 }}>
                  <th style={{ textAlign: 'left', padding: '10px 14px' }}>Device</th>
                  <th style={{ textAlign: 'left', padding: '10px' }}>Type</th>
                  <th style={{ textAlign: 'left', padding: '10px' }}>Location</th>
                  <th style={{ textAlign: 'left', padding: '10px' }}>SNMP</th>
                  <th style={{ width: 84, padding: '10px' }}></th>
                </tr>
              </thead>
              <tbody>
                {devices.map(device => (
                  <tr key={device.ip} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '11px 14px' }}>
                      <div style={{ fontWeight: 600 }}>{device.name}</div>
                      <div className="mono dim" style={{ fontSize: 9, marginTop: 3 }}>{device.ip}</div>
                    </td>
                    <td style={{ padding: 10 }}>{DEVICE_TYPES.find(([value]) => value === device.device_type)?.[1] || device.device_type}</td>
                    <td style={{ padding: 10 }}>{device.building || 'Unassigned'}{device.floor ? ` · Floor ${device.floor}` : ''}</td>
                    <td style={{ padding: 10 }}>
                      <span style={{ color: device.snmp?.enabled ? 'var(--accent)' : 'var(--text-dim)' }}>{device.snmp?.enabled ? `Enabled · ifIndex ${device.snmp.if_index}` : 'Disabled'}</span>
                    </td>
                    <td style={{ padding: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 4 }}>
                        <button onClick={() => openEdit(device)} title="Edit" style={{ width: 28, height: 28, display: 'grid', placeItems: 'center', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}><Pencil size={13} /></button>
                        <button onClick={() => removeDevice(device)} title="Delete" style={{ width: 28, height: 28, display: 'grid', placeItems: 'center', borderRadius: 'var(--radius)', border: '1px solid rgba(224,92,92,.3)', color: 'var(--sev-critical)' }}><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState message="No configured devices. Add the first device to begin monitoring." />
        )}
      </div>

      <div style={{ marginTop: 12, fontSize: 10, lineHeight: 1.6, color: 'var(--text-dim)' }}>
        Changes are saved to the project inventory immediately. Restart long-running collectors after changing SNMP targets so they reload the updated configuration.
      </div>
    </div>
  )
}
