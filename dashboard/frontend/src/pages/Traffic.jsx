import { useState, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePolling } from '../hooks/usePolling'
import { getTrafficRecent, getLiveScores } from '../api/traffic'
import { formatBytes, formatScore, detectorLabel, scoreToSeverity } from '../utils/format'
import { SeverityBadge, EmptyState, Skeleton } from '../components/Shared'
import BandwidthChart from '../components/BandwidthChart'

const MAX_CHART_POINTS = 180
const DEFAULT_DEVICE_LIMIT = 12

function normaliseSeries(series) {
  if (!Array.isArray(series)) return []
  const cleaned = series
    .filter(pt => Number.isFinite(Number(pt?.window)))
    .map(pt => ({
      window: Number(pt.window),
      bytes_in: Number.isFinite(Number(pt.bytes_in)) ? Number(pt.bytes_in) : 0,
      bytes_out: Number.isFinite(Number(pt.bytes_out)) ? Number(pt.bytes_out) : 0,
      packets_in: Number.isFinite(Number(pt.packets_in)) ? Number(pt.packets_in) : 0,
      packets_out: Number.isFinite(Number(pt.packets_out)) ? Number(pt.packets_out) : 0,
    }))
    .sort((a, b) => a.window - b.window)

  if (cleaned.length <= MAX_CHART_POINTS) return cleaned
  const step = Math.ceil(cleaned.length / MAX_CHART_POINTS)
  return cleaned.filter((_, index) => index % step === 0 || index === cleaned.length - 1)
}

export default function Traffic() {
  const navigate = useNavigate()
  const [range, setRange] = useState(30)
  const [devicePage, setDevicePage] = useState(0)

  const fetchTraffic = useCallback(() => getTrafficRecent(range), [range])
  const fetchScores = useCallback(() => getLiveScores(3), [])
  const { data: traffic, loading: trafficLoading, error: trafficError } = usePolling(fetchTraffic, 15_000)
  const { data: liveScores, error: scoresError } = usePolling(fetchScores, 30_000)

  const deviceSeries = useMemo(() => {
    const devices = traffic?.devices && typeof traffic.devices === 'object' ? traffic.devices : {}
    return Object.entries(devices)
      .map(([ip, series]) => {
        const normalised = normaliseSeries(series)
        const total = normalised.reduce((sum, point) => sum + point.bytes_in + point.bytes_out, 0)
        return { ip, series: normalised, total }
      })
      .filter(item => item.series.length)
      .sort((a, b) => b.total - a.total)
  }, [traffic])

  const aggregated = useMemo(() => {
    if (Array.isArray(traffic?.network) && traffic.network.length) {
      return normaliseSeries(traffic.network)
    }
    const windowMap = new Map()
    deviceSeries.forEach(({ series }) => {
      series.forEach(pt => {
        const current = windowMap.get(pt.window) || { window: pt.window, bytes_in: 0, bytes_out: 0 }
        current.bytes_in += pt.bytes_in
        current.bytes_out += pt.bytes_out
        windowMap.set(pt.window, current)
      })
    })
    return normaliseSeries(Array.from(windowMap.values()))
  }, [deviceSeries, traffic])

  const notableScores = useMemo(() => {
    const scores = Array.isArray(liveScores) ? liveScores : []
    return scores
      .filter(s => Number.isFinite(Number(s?.anomaly_score)) && Number(s.anomaly_score) > 0.3)
      .sort((a, b) => Number(b.anomaly_score) - Number(a.anomaly_score))
      .slice(0, 100)
  }, [liveScores])

  const totalDevicePages = Math.max(1, Math.ceil(deviceSeries.length / DEFAULT_DEVICE_LIMIT))
  const safeDevicePage = Math.min(devicePage, totalDevicePages - 1)
  const visibleDevices = deviceSeries.slice(safeDevicePage * DEFAULT_DEVICE_LIMIT, (safeDevicePage + 1) * DEFAULT_DEVICE_LIMIT)

  const rangeBtn = (mins) => ({
    padding: '4px 10px', fontSize: 10, borderRadius: 'var(--radius)',
    background: range === mins ? 'var(--accent)' : 'var(--panel)',
    color: range === mins ? '#0B0E14' : 'var(--text-dim)',
    border: '1px solid var(--border)',
  })

  return (
    <div>
      {(trafficError || scoresError) && (
        <div className="panel" style={{ padding: '10px 14px', marginBottom: 14, borderColor: 'rgba(224,92,92,.35)', color: 'var(--sev-high)', fontSize: 11 }}>
          Live refresh encountered a temporary error. The last successful data remains displayed.
        </div>
      )}

      <div className="section-label">Network-Wide Bandwidth</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {[{ m: 15, l: '15m' }, { m: 30, l: '30m' }, { m: 60, l: '1h' }, { m: 360, l: '6h' }].map(({ m, l }) => (
          <button key={m} style={rangeBtn(m)} onClick={() => { setRange(m); setDevicePage(0) }}>{l}</button>
        ))}
      </div>
      <div className="panel" style={{ marginBottom: 24, minWidth: 0 }}>
        <div style={{ padding: 16, minWidth: 0 }}>
          {trafficLoading && !aggregated.length ? (
            <Skeleton width="100%" height={200} />
          ) : aggregated.length ? (
            <BandwidthChart data={aggregated} height={200} />
          ) : (
            <EmptyState message="No traffic data available." />
          )}
        </div>
      </div>

      <div className="section-label">Live Detector Scores — last 3 minutes</div>
      <div className="panel" style={{ marginBottom: 24 }}>
        {notableScores.length ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: 'var(--panel-alt)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-dim)' }}>
                  <th style={{ textAlign: 'left', padding: '10px 16px', fontWeight: 600 }}>Entity</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Detector</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Score</th>
                  <th style={{ textAlign: 'left', padding: '10px 8px', fontWeight: 600 }}>Severity</th>
                </tr>
              </thead>
              <tbody>
                {notableScores.map((s, i) => (
                  <tr key={`${s.entity_id}-${s.detector}-${s.window || i}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="mono" style={{ padding: '10px 16px' }}>{s.entity_id}</td>
                    <td style={{ padding: '10px 8px' }}>{detectorLabel(s.detector)}</td>
                    <td className="mono" style={{ padding: '10px 8px' }}>{formatScore(Number(s.anomaly_score))}</td>
                    <td style={{ padding: '10px 8px' }}><SeverityBadge severity={scoreToSeverity(Number(s.anomaly_score))} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState message="No notable scores in the last 3 minutes." />
        )}
        <div style={{ padding: '8px 16px', fontSize: 10, color: 'var(--text-dim)', borderTop: '1px solid var(--border)' }}>
          Scores at or above 0.55 are persisted after the current one-minute window completes.
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="section-label" style={{ flex: 1 }}>Per-Device Bandwidth</div>
        {deviceSeries.length > DEFAULT_DEVICE_LIMIT && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, fontSize: 10, color: 'var(--text-dim)' }}>
            <button disabled={safeDevicePage === 0} onClick={() => setDevicePage(page => Math.max(0, page - 1))} style={{ color: safeDevicePage === 0 ? 'var(--text-dim)' : 'var(--accent)', opacity: safeDevicePage === 0 ? .5 : 1 }}>Previous</button>
            <span>Page {safeDevicePage + 1} of {totalDevicePages}</span>
            <button disabled={safeDevicePage >= totalDevicePages - 1} onClick={() => setDevicePage(page => Math.min(totalDevicePages - 1, page + 1))} style={{ color: safeDevicePage >= totalDevicePages - 1 ? 'var(--text-dim)' : 'var(--accent)', opacity: safeDevicePage >= totalDevicePages - 1 ? .5 : 1 }}>Next</button>
          </div>
        )}
      </div>
      {visibleDevices.length ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
          {visibleDevices.map(({ ip, series }) => (
            <div key={ip} className="panel" style={{ cursor: 'pointer', minWidth: 0 }} onClick={() => navigate(`/devices/${ip}`)}>
              <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 11, fontWeight: 500 }}>{ip}</span>
                <span className="mono dim" style={{ fontSize: 10 }}>
                  {formatBytes((series[series.length - 1]?.bytes_in || 0) + (series[series.length - 1]?.bytes_out || 0))}/min
                </span>
              </div>
              <div style={{ padding: 10, minWidth: 0 }}>
                <BandwidthChart data={series} height={80} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState message="No per-device traffic data available." />
      )}
    </div>
  )
}
