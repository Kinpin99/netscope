import { useState, useCallback, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { getDevice, postBaseline, deleteBaseline } from '../api/devices'
import { getTrafficRecent } from '../api/traffic'
import { getAlerts } from '../api/alerts'
import { healthColor, formatBytes, formatTime } from '../utils/format'
import { StatusDot, HealthScore, PulseStrip, AlertItem, EmptyState, Skeleton } from '../components/Shared'
import BandwidthChart from '../components/BandwidthChart'
import PacketChart from '../components/PacketChart'

export default function DeviceDetail() {
  const { ip } = useParams()
  const [tab, setTab] = useState('metrics')
  const [range, setRange] = useState(60)
  const [baselineLoading, setBaselineLoading] = useState(false)
  const [baselineMsg, setBaselineMsg] = useState(null)

  const fetchDevice = useCallback(() => getDevice(ip), [ip])
  const fetchTraffic = useCallback(() => getTrafficRecent(range), [range])
  const fetchAlerts = useCallback(() => getAlerts({ device_ip: ip, last_hours: 48 }), [ip])

  const { data: device, loading: devLoading, refresh: refreshDevice } = usePolling(fetchDevice, 15_000)
  const { data: traffic, loading: trafficLoading } = usePolling(fetchTraffic, 15_000)
  const { data: alerts } = usePolling(fetchAlerts, 15_000)

  // pull just this device's traffic data
  const deviceTraffic = useMemo(() => {
    if (!traffic?.devices?.[ip]) return []
    return traffic.devices[ip]
  }, [traffic, ip])

  const handleBaseline = async () => {
    setBaselineLoading(true)
    setBaselineMsg(null)
    try {
      if (device.has_per_device_profile) {
        await deleteBaseline(ip)
        setBaselineMsg('Baseline removed')
      } else {
        await postBaseline(ip)
        setBaselineMsg('Baseline trained')
      }
      refreshDevice()
    } catch (err) {
      setBaselineMsg(err.message)
    } finally {
      setBaselineLoading(false)
    }
  }

  if (devLoading && !device) return <Skeleton width="100%" height={300} />
  if (!device) return <EmptyState message={`Device ${ip} not found`} />

  const tabStyle = (t) => ({
    padding: '8px 16px', fontSize: 11, fontWeight: 500,
    borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
    color: tab === t ? 'var(--text)' : 'var(--text-dim)',
    background: 'transparent',
  })

  const rangeBtn = (mins, label) => ({
    padding: '4px 10px', fontSize: 10, borderRadius: 'var(--radius)',
    background: range === mins ? 'var(--accent)' : 'var(--panel)',
    color: range === mins ? '#0B0E14' : 'var(--text-dim)',
    border: '1px solid var(--border)',
  })

  return (
    <div>
      {/* header */}
      <div className="panel" style={{ padding: 20, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <StatusDot status={device.status} />
              <span style={{ fontSize: 18, fontWeight: 700 }}>{device.name}</span>
            </div>
            <div className="mono dim" style={{ fontSize: 11, marginBottom: 4 }}>
              {device.ip} · {device.device_type?.replace('_', ' ')} · {device.building || 'Unassigned'}
            </div>
            <PulseStrip samples={[device.health_score]} tickWidth={6} maxHeight={16} />
          </div>
          <div style={{ textAlign: 'right' }}>
            <HealthScore score={device.health_score} size="lg" />
            <div style={{ marginTop: 8 }}>
              <button
                onClick={handleBaseline}
                disabled={baselineLoading}
                style={{
                  padding: '5px 12px', fontSize: 10, fontWeight: 600,
                  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                  background: 'var(--panel-alt)', color: 'var(--text)',
                  display: 'flex', alignItems: 'center', gap: 4,
                  opacity: baselineLoading ? 0.5 : 1,
                }}
              >
                {baselineLoading && <Loader2 size={10} style={{ animation: 'spin 1s linear infinite' }} />}
                {device.has_per_device_profile ? 'Remove Baseline' : 'Train Baseline'}
              </button>
              {baselineMsg && <div style={{ fontSize: 10, marginTop: 4, color: 'var(--text-dim)' }}>{baselineMsg}</div>}
            </div>
          </div>
        </div>
      </div>

      {/* tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', marginBottom: 16 }}>
        <button style={tabStyle('metrics')} onClick={() => setTab('metrics')}>Metrics</button>
        <button style={tabStyle('alerts')} onClick={() => setTab('alerts')}>Alerts</button>
      </div>

      {/* metrics tab */}
      {tab === 'metrics' && (
        <div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
            {[{ m: 15, l: '15m' }, { m: 60, l: '1h' }, { m: 360, l: '6h' }].map(({ m, l }) => (
              <button key={m} style={rangeBtn(m, l)} onClick={() => setRange(m)}>{l}</button>
            ))}
          </div>

          {trafficLoading && !deviceTraffic.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Skeleton width="100%" height={180} />
              <Skeleton width="100%" height={120} />
            </div>
          ) : !deviceTraffic.length ? (
            <EmptyState message="No traffic data for this device in the selected range." />
          ) : (
            <>
              <div className="panel" style={{ marginBottom: 12 }}>
                <div className="panel-head"><span className="panel-title">Bandwidth In / Out</span></div>
                <div style={{ padding: 16 }}>
                  <BandwidthChart data={deviceTraffic} height={180} />
                </div>
              </div>
              <div className="panel">
                <div className="panel-head"><span className="panel-title">Packets In / Out</span></div>
                <div style={{ padding: 16 }}>
                  <PacketChart data={deviceTraffic} height={120} />
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* alerts tab */}
      {tab === 'alerts' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {alerts?.length ? (
            alerts.map(a => <AlertItem key={a.alert_id} alert={a} />)
          ) : (
            <EmptyState message="No alerts for this device in the last 48 hours." />
          )}
        </div>
      )}
    </div>
  )
}
