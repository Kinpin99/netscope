import { useState, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { getDevices } from '../api/topology'
import { getHealthScores } from '../api/alerts'
import { StatusDot, HealthScore, PulseStrip, Skeleton, EmptyState } from '../components/Shared'

export default function Devices() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sort, setSort] = useState('name')

  const fetchDevices = useCallback(() => getDevices(), [])
  const fetchScores = useCallback(() => getHealthScores(), [])
  const { data: devices, loading } = usePolling(fetchDevices, 20_000)
  const { data: scores } = usePolling(fetchScores, 20_000)

  // merge health scores into device list
  const merged = useMemo(() => {
    if (!devices) return []
    return devices.map(d => ({
      ...d,
      health_score: scores?.[d.ip] ?? d.health_score,
    }))
  }, [devices, scores])

  const filtered = useMemo(() => {
    let list = merged.filter(d => {
      const q = search.toLowerCase()
      const matchSearch = !q || d.name.toLowerCase().includes(q) || d.ip.includes(q)
      const matchStatus = statusFilter === 'all' || d.status === statusFilter
      return matchSearch && matchStatus
    })
    list.sort((a, b) => {
      if (sort === 'health') return (b.health_score ?? -1) - (a.health_score ?? -1)
      if (sort === 'status') return a.status.localeCompare(b.status)
      return a.name.localeCompare(b.name)
    })
    return list
  }, [merged, search, statusFilter, sort])

  const selectStyle = {
    padding: '6px 10px', fontSize: 11, background: 'var(--panel)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
    color: 'var(--text)', outline: 'none',
  }

  return (
    <div>
      {/* controls */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 300 }}>
          <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)' }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name or IP..."
            style={{ ...selectStyle, width: '100%', paddingLeft: 30 }}
          />
        </div>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={selectStyle}>
          <option value="all">All Status</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="degraded">Degraded</option>
          <option value="unknown">Unknown</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} style={selectStyle}>
          <option value="name">Sort: Name</option>
          <option value="health">Sort: Health</option>
          <option value="status">Sort: Status</option>
        </select>
      </div>

      {/* table */}
      <div className="panel">
        {loading ? (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {Array.from({ length: 8 }, (_, i) => <Skeleton key={i} width="100%" height={36} />)}
          </div>
        ) : !filtered.length ? (
          <EmptyState message="No devices found" />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr style={{ background: 'var(--panel-alt)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-dim)' }}>
                <th style={{ textAlign: 'left', padding: '10px 16px', fontWeight: 600 }}>Name</th>
                <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>IP</th>
                <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Building</th>
                <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Type</th>
                <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Status</th>
                <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Health</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((d) => (
                <tr
                  key={d.ip}
                  onClick={() => navigate(`/devices/${d.ip}`)}
                  style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'var(--panel-alt)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <td style={{ padding: '10px 16px', fontWeight: 500 }}>{d.name}</td>
                  <td className="mono dim" style={{ padding: '10px 8px' }}>{d.ip}</td>
                  <td className="dim" style={{ padding: '10px 8px' }}>{d.building || '—'}</td>
                  <td className="dim" style={{ padding: '10px 8px', textTransform: 'capitalize' }}>{d.device_type?.replace('_', ' ')}</td>
                  <td style={{ padding: '10px 8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <StatusDot status={d.status} size={6} />
                      <span style={{ fontSize: 10, color: 'var(--text-dim)', textTransform: 'capitalize' }}>{d.status}</span>
                    </div>
                  </td>
                  <td style={{ padding: '10px 8px' }}>
                    <HealthScore score={d.health_score} size="sm" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
