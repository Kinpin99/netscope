import { useCallback, useMemo, useState } from 'react'
import { Search, Wifi } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { getAccessPoints } from '../api/accessPoints'
import { EmptyState, HealthScore, Skeleton, StatusDot } from '../components/Shared'

const selectStyle = {
  padding: '6px 10px', fontSize: 11, background: 'var(--panel)',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  color: 'var(--text)', outline: 'none',
}

export default function AccessPoints() {
  const [search, setSearch] = useState('')
  const fetchAPs = useCallback(() => getAccessPoints(), [])
  const { data, loading } = usePolling(fetchAPs, 20000)
  const aps = data || []

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return aps.filter(ap => !q || (ap.name || '').toLowerCase().includes(q) || (ap.ip || '').includes(q) || (ap.building || '').toLowerCase().includes(q))
  }, [aps, search])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16, gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>Access Point Management</h2>
          <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-dim)' }}>
            Visibility for APs enrolled through config or automated onboarding. Control-plane configuration is simulated in this prototype.
          </p>
        </div>
        <div style={{ position: 'relative', width: 260 }}>
          <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)' }} />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search APs..." style={{ ...selectStyle, width: '100%', paddingLeft: 30 }} />
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} width="100%" height={130} />)}
        </div>
      ) : !filtered.length ? (
        <EmptyState message="No access points are currently enrolled. Approve a pending AP from Onboarding to add one." />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          {filtered.map(ap => (
            <div key={ap.ip} className="panel" style={{ padding: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ display: 'flex', gap: 10 }}>
                  <div style={{ width: 34, height: 34, borderRadius: 8, background: 'var(--panel-alt)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Wifi size={17} color="var(--accent)" />
                  </div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{ap.name}</div>
                    <div className="mono dim" style={{ fontSize: 10 }}>{ap.ip}</div>
                  </div>
                </div>
                <HealthScore score={ap.health_score} size="sm" />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 14, fontSize: 11 }}>
                <Info label="Building" value={ap.building || '—'} />
                <Info label="Floor" value={ap.floor || '—'} />
                <Info label="Role" value={ap.role || 'Access Point'} />
                <Info label="Onboarding" value={ap.onboarding_status || ap.source || 'manual/config'} />
                <Info label="Serial" value={ap.serial_number || '—'} mono />
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2 }}>Status</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><StatusDot status={ap.status} size={6} /><span style={{ fontSize: 11 }}>{ap.status}</span></div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Info({ label, value, mono }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2 }}>{label}</div>
      <div className={mono ? 'mono' : ''} style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
    </div>
  )
}
