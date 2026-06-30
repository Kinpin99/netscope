import { useState } from 'react'
import { ChevronDown, ChevronRight, AlertCircle } from 'lucide-react'
import { severityColor, healthColor, detectorLabel, issueTypeLabel, timeAgo, formatScore } from '../utils/format'

/* ── SeverityBadge ── */
export function SeverityBadge({ severity }) {
  const color = severityColor(severity)
  return (
    <span
      className="mono"
      style={{
        background: `color-mix(in srgb, ${color} 10%, transparent)`,
        color,
        border: `1px solid color-mix(in srgb, ${color} 25%, transparent)`,
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        textTransform: 'uppercase',
        padding: '2px 7px',
        letterSpacing: '0.05em',
        display: 'inline-block',
      }}
    >
      {severity}
    </span>
  )
}

/* ── StatusDot ── */
export function StatusDot({ status, size = 8 }) {
  const colors = {
    online: 'var(--accent)',
    degraded: 'var(--sev-medium)',
    offline: 'var(--sev-critical)',
    unknown: 'var(--sev-unknown)',
  }
  const c = colors[status] || colors.unknown
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: c,
        boxShadow: status === 'online' ? `0 0 6px ${c}` : 'none',
        flexShrink: 0,
      }}
    />
  )
}

/* ── HealthScore ── */
export function HealthScore({ score, size = 'md' }) {
  const color = healthColor(score)
  const fontSize = size === 'lg' ? 28 : size === 'sm' ? 14 : 20
  return (
    <span className="mono" style={{ color, fontSize, fontWeight: 500, letterSpacing: '-0.02em' }}>
      {score === null || score === undefined ? '—' : score}
    </span>
  )
}

/* ── PulseStrip ── */
export function PulseStrip({ samples = [], tickWidth = 3, maxHeight = 24 }) {
  if (!samples.length) return null
  const gap = 1
  const totalW = samples.length * (tickWidth + gap) - gap
  return (
    <svg width={totalW} height={maxHeight} style={{ display: 'block' }}>
      {samples.map((score, i) => {
        const h = score !== null && score !== undefined ? (score / 100) * maxHeight : 4
        const color = healthColor(score)
        return (
          <rect
            key={i}
            x={i * (tickWidth + gap)}
            y={maxHeight - h}
            width={tickWidth}
            height={h}
            rx={1}
            fill={color}
            opacity={score === null ? 0.3 : 0.85}
          />
        )
      })}
    </svg>
  )
}

/* ── AlertItem ── */
export function AlertItem({ alert }) {
  const [expanded, setExpanded] = useState(false)
  const color = severityColor(alert.severity)
  const Icon = expanded ? ChevronDown : ChevronRight

  return (
    <div style={{ border: `1px solid var(--border)`, borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 14px', textAlign: 'left', background: 'transparent',
        }}
      >
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0, boxShadow: alert.severity === 'critical' ? `0 0 5px ${color}` : 'none' }} />
        <SeverityBadge severity={alert.severity} />
        <span style={{ flex: 1, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {detectorLabel(alert.detector)} · {issueTypeLabel(alert.issue_type)} · <span className="mono">{alert.entity_id}</span>
        </span>
        <span className="mono dim" style={{ fontSize: 10, flexShrink: 0 }}>
          {formatScore(alert.anomaly_score)}
        </span>
        <span className="mono dim" style={{ fontSize: 10, flexShrink: 0, width: 55, textAlign: 'right' }}>
          {timeAgo(alert.window)}
        </span>
        <Icon size={14} color="var(--text-dim)" />
      </button>

      {expanded && (
        <div style={{ padding: '8px 14px 14px', borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--text-dim)', marginBottom: 12, flexWrap: 'wrap' }}>
            <span>Entity: <span className="mono" style={{ color: 'var(--text)' }}>{alert.entity_id}</span></span>
            <span>Detector: <span style={{ color: 'var(--text)' }}>{detectorLabel(alert.detector)}</span></span>
            <span>Score: <span className="mono" style={{ color: 'var(--text)' }}>{formatScore(alert.anomaly_score)}</span></span>
            <span>Status: <span style={{ color: 'var(--text)' }}>{alert.status}</span></span>
          </div>

          {alert.features && Object.keys(alert.features).length > 0 && (
            <div>
              <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-dim)', marginBottom: 8 }}>
                Features
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 24px' }}>
                {Object.entries(alert.features).map(([key, val]) => (
                  <div key={key} className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, padding: '3px 0' }}>
                    <span className="dim">{key}</span>
                    <span>{typeof val === 'number' ? val.toLocaleString() : String(val)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── EmptyState ── */
export function EmptyState({ message }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '40px 20px', color: 'var(--text-dim)' }}>
      <AlertCircle size={20} />
      <span style={{ fontSize: 12 }}>{message}</span>
    </div>
  )
}

/* ── ErrorBanner ── */
export function ErrorBanner({ message, onRetry }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', background: 'color-mix(in srgb, var(--sev-critical) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--sev-critical) 20%, transparent)', borderRadius: 'var(--radius)', fontSize: 11, color: 'var(--sev-critical)', marginBottom: 12 }}>
      <span style={{ flex: 1 }}>{message}</span>
      {onRetry && <button onClick={onRetry} style={{ fontSize: 11, color: 'var(--accent)', textDecoration: 'underline' }}>Retry</button>}
    </div>
  )
}

/* ── Skeleton ── */
export function Skeleton({ width, height, style = {} }) {
  return (
    <div
      className="skeleton"
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
        ...style,
      }}
    />
  )
}
