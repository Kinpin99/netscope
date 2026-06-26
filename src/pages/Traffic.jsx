import { useState, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePolling } from '../hooks/usePolling'
import { getTrafficRecent, getLiveScores } from '../api/traffic'
import { formatBytes, formatTime, formatScore, detectorLabel, scoreToSeverity } from '../utils/format'
import { SeverityBadge, EmptyState, Skeleton } from '../components/Shared'
import BandwidthChart from '../components/BandwidthChart'

export default function Traffic() {
  const navigate = useNavigate()
  const [range, setRange] = useState(30)

  const fetchTraffic = useCallback(() => getTrafficRecent(range), [range])
  const fetchScores = useCallback(() => getLiveScores(3), [])
  const { data: traffic, loading: trafficLoading } = usePolling(fetchTraffic, 15_000)
  const { data: liveScores } = usePolling(fetchScores, 15_000)

  // aggregate all devices per window
  const aggregated = useMemo(() => {
    if (!traffic?.devices) return []
    const windowMap = {}
    Object.values(traffic.devices).forEach(series => {
      series.forEach(pt => {
        if (!windowMap[pt.window]) windowMap[pt.window] = { window: pt.window, bytes_in: 0, bytes_out: 0 }
        windowMap[pt.window].bytes_in += pt.bytes_in
        windowMap[pt.window].bytes_out += pt.bytes_out
      })
    })
    return Object.values(windowMap).sort((a, b) => a.window - b.window)
  }, [traffic])

  // filter live scores to only notable ones
  const notableScores = useMemo(() => {
    if (!liveScores) return []
    return liveScores.filter(s => s.anomaly_score > 0.3).sort((a, b) => b.anomaly_score - a.anomaly_score)
  }, [liveScores])

  // per-device mini charts
  const perDevice = useMemo(() => {
    if (!traffic?.devices) return []
    return Object.entries(traffic.devices).map(([ip, series]) => ({ ip, series }))
  }, [traffic])

  const rangeBtn = (mins, label) => ({
    padding: '4px 10px', fontSize: 10, borderRadius: 'var(--radius)',
    background: range === mins ? 'var(--accent)' : 'var(--panel)',
    color: range === mins ? '#0B0E14' : 'var(--text-dim)',
    border: '1px solid var(--border)',
  })

  return (
    <div>
      {/* aggregate bandwidth */}
      <div className="section-label">Network-Wide Bandwidth</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {[{ m: 15, l: '15m' }, { m: 30, l: '30m' }, { m: 60, l: '1h' }, { m: 360, l: '6h' }].map(({ m, l }) => (
          <button key={m} style={rangeBtn(m, l)} onClick={() => setRange(m)}>{l}</button>
        ))}
      </div>
      <div className="panel" style={{ marginBottom: 24 }}>
        <div style={{ padding: 16 }}>
          {trafficLoading && !aggregated.length ? (
            <Skeleton width="100%" height={200} />
          ) : aggregated.length ? (
            <BandwidthChart data={aggregated} height={200} />
          ) : (
            <EmptyState message="No traffic data available." />
          )}
        </div>
      </div>

      {/* live detector scores */}
      <div className="section-label">Live Detector Scores — last 3 minutes</div>
      <div className="panel" style={{ marginBottom: 24 }}>
        {notableScores.length ? (
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
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td className="mono" style={{ padding: '10px 16px' }}>{s.entity_id}</td>
                  <td style={{ padding: '10px 8px' }}>{detectorLabel(s.detector)}</td>
                  <td className="mono" style={{ padding: '10px 8px' }}>{formatScore(s.anomaly_score)}</td>
                  <td style={{ padding: '10px 8px' }}><SeverityBadge severity={scoreToSeverity(s.anomaly_score)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState message="No notable scores in the last 3 minutes." />
        )}
        <div style={{ padding: '8px 16px', fontSize: 10, color: 'var(--text-dim)', borderTop: '1px solid var(--border)' }}>
          Live scores — not persisted as alerts unless they meet the minimum alertable threshold (≥ 0.55).
        </div>
      </div>

      {/* per-device grid */}
      <div className="section-label">Per-Device Bandwidth</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
        {perDevice.map(({ ip, series }) => (
          <div
            key={ip}
            className="panel"
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/devices/${ip}`)}
          >
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 11, fontWeight: 500 }}>{ip}</span>
              <span className="mono dim" style={{ fontSize: 10 }}>
                {formatBytes(series[series.length - 1]?.bytes_in || 0)}/s
              </span>
            </div>
            <div style={{ padding: 10 }}>
              <BandwidthChart data={series} height={80} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
