import { useMemo, useState } from 'react'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts'

const PALETTE = ['#4ADE80', '#70A5EB', '#F3C969', '#A78BFA', '#F472B6', '#FB7185', '#2DD4BF', '#94A3B8']

const LABELS = {
  access_point: 'Access points',
  network_device: 'Network devices',
  router: 'Routers',
  switch: 'Switches',
  server: 'Servers',
  client: 'Clients',
  firewall: 'Firewalls',
  controller: 'Controllers',
  healthy: 'Healthy',
  degraded: 'Degraded',
  critical: 'Critical',
  unknown: 'Unknown',
}

function titleCase(value) {
  if (!value) return 'Unknown'
  return LABELS[value] || String(value).replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function compactGroups(groups) {
  const sorted = Object.entries(groups)
    .map(([name, value]) => ({ name: titleCase(name), value }))
    .filter(item => item.value > 0)
    .sort((a, b) => b.value - a.value)
  if (sorted.length <= 7) return sorted
  const first = sorted.slice(0, 6)
  const other = sorted.slice(6).reduce((sum, item) => sum + item.value, 0)
  return [...first, { name: 'Other', value: other }]
}

const CompositionTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const item = payload[0]
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '9px 12px', fontSize: 11 }}>
      <div style={{ fontWeight: 600 }}>{item.name}</div>
      <div className="mono dim" style={{ marginTop: 3 }}>{item.value} devices</div>
    </div>
  )
}

export default function NetworkCompositionChart({ buildings = [] }) {
  const [mode, setMode] = useState('type')

  const devices = useMemo(() => buildings.flatMap(building => Array.isArray(building?.devices) ? building.devices : []), [buildings])

  const dataByMode = useMemo(() => {
    const type = {}
    const building = {}
    const status = {}
    devices.forEach(device => {
      const typeKey = device.device_type || 'unknown'
      const buildingKey = device.building || 'Unassigned'
      const statusKey = device.status || 'unknown'
      type[typeKey] = (type[typeKey] || 0) + 1
      building[buildingKey] = (building[buildingKey] || 0) + 1
      status[statusKey] = (status[statusKey] || 0) + 1
    })
    return {
      type: compactGroups(type),
      building: compactGroups(building),
      status: compactGroups(status),
    }
  }, [devices])

  const data = dataByMode[mode]
  const tabs = [
    { key: 'type', label: 'Device Types' },
    { key: 'building', label: 'Buildings' },
    { key: 'status', label: 'Status' },
  ]

  return (
    <div className="panel" style={{ marginBottom: 24, overflow: 'hidden' }}>
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', padding: '0 14px' }}>
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setMode(tab.key)}
            style={{
              padding: '12px 14px 10px', fontSize: 11,
              color: mode === tab.key ? 'var(--accent)' : 'var(--text-dim)',
              borderBottom: mode === tab.key ? '2px solid var(--accent)' : '2px solid transparent',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div style={{ position: 'relative', height: 300, minWidth: 0 }}>
        {data.length ? (
          <>
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <PieChart>
                <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="45%" innerRadius={64} outerRadius={104} paddingAngle={1} stroke="var(--panel)" strokeWidth={2} isAnimationActive={false}>
                  {data.map((entry, index) => <Cell key={`${entry.name}-${index}`} fill={PALETTE[index % PALETTE.length]} />)}
                </Pie>
                <Tooltip content={<CompositionTooltip />} />
                <Legend verticalAlign="bottom" iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 10, paddingBottom: 8 }} />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ position: 'absolute', left: '50%', top: '44%', transform: 'translate(-50%, -50%)', textAlign: 'center', pointerEvents: 'none' }}>
              <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>{devices.length}</div>
              <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>monitored devices</div>
            </div>
          </>
        ) : (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-dim)', fontSize: 11 }}>No inventory data available.</div>
        )}
      </div>
    </div>
  )
}
