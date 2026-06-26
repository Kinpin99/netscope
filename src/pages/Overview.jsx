import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, ArrowUpRight } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { useAlerts } from '../context/AlertContext'
import { useSystem } from '../context/SystemContext'
import { getBuildings } from '../api/topology'
import { healthColor } from '../utils/format'
import { SeverityBadge, StatusDot, HealthScore, PulseStrip, AlertItem, EmptyState, Skeleton } from '../components/Shared'

function BuildingCard({ building }) {
  const [open, setOpen] = useState(building.devices.length <= 5)
  const navigate = useNavigate()
  const avgColor = healthColor(building.avg_health_score)

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <button
        onClick={() => setOpen(!open)}
        style={{ width: '100%', padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', background: 'transparent' }}
      >
        {open ? <ChevronDown size={14} color="var(--text-dim)" /> : <ChevronRight size={14} color="var(--text-dim)" />}
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{building.building}</div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{building.device_count} devices</span>
            <span>·</span>
            <span>{building.open_issue_count} open issues</span>
            {building.max_severity && building.open_issue_count > 0 && (
              <>
                <span>·</span>
                <SeverityBadge severity={building.max_severity} />
              </>
            )}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>avg health</div>
          <HealthScore score={building.avg_health_score} size="sm" />
        </div>
      </button>

      {/* pulse strip */}
      <div style={{ padding: '0 16px 10px' }}>
        <PulseStrip samples={building.devices.map(d => d.health_score)} />
      </div>

      {/* device list */}
      {open && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {building.devices.map((d) => (
            <button
              key={d.ip}
              onClick={() => navigate(`/devices/${d.ip}`)}
              style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 16px', textAlign: 'left', fontSize: 11,
                borderBottom: '1px solid var(--border)', background: 'transparent',
              }}
            >
              <StatusDot status={d.status} size={6} />
              <span style={{ flex: 1, fontWeight: 500 }}>{d.name}</span>
              <span className="mono dim" style={{ fontSize: 10, width: 80 }}>{d.ip}</span>
              <HealthScore score={d.health_score} size="sm" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Overview() {
  const navigate = useNavigate()
  const { status } = useSystem()
  const { openAlerts } = useAlerts()
  const fetchBuildings = useCallback(() => getBuildings(), [])
  const { data: buildings, loading } = usePolling(fetchBuildings, 15_000)
  const isObservation = status?.phase === 'observation'

  return (
    <div>
      {/* buildings */}
      <div className="section-label">Buildings</div>
      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Skeleton width="100%" height={120} />
          <Skeleton width="100%" height={120} />
          <Skeleton width="100%" height={120} />
        </div>
      ) : buildings?.length ? (
        buildings.map((b) => <BuildingCard key={b.building} building={b} />)
      ) : (
        <EmptyState message="No buildings found" />
      )}

      {/* open alerts */}
      <div style={{ marginTop: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div className="section-label" style={{ marginBottom: 0, flex: 1 }}>Open Alerts</div>
          <button
            onClick={() => navigate('/alerts')}
            style={{ fontSize: 11, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 4 }}
          >
            view all <ArrowUpRight size={12} />
          </button>
        </div>
        {openAlerts.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {openAlerts.map((a) => <AlertItem key={a.alert_id} alert={a} />)}
          </div>
        ) : (
          <EmptyState message={isObservation ? 'Alerts are unavailable during the observation phase. The system needs more data before it can detect anomalies.' : 'No open alerts — network looks clean.'} />
        )}
      </div>
    </div>
  )
}
