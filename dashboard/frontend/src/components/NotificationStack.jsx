import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { useAlerts } from '../context/AlertContext'
import { severityColor, detectorLabel, formatScore, timeAgo } from '../utils/format'

function Toast({ alert, onDismiss }) {
  const [hovered, setHovered] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    if (hovered) {
      clearTimeout(timerRef.current)
      return
    }
    timerRef.current = setTimeout(() => onDismiss(alert.id), 8000)
    return () => clearTimeout(timerRef.current)
  }, [alert.id, onDismiss, hovered])

  const color = severityColor(alert.severity)

  return (
    <div
      className="toast-enter"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderLeft: `3px solid ${color}`,
        borderRadius: 'var(--radius)',
        padding: '10px 14px',
        maxWidth: 340,
        position: 'relative',
      }}
    >
      <button
        onClick={() => onDismiss(alert.id)}
        style={{ position: 'absolute', top: 8, right: 8, color: 'var(--text-dim)' }}
      >
        <X size={12} />
      </button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: color }} />
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color }}>{alert.severity} ALERT</span>
      </div>
      <div style={{ fontSize: 11, marginBottom: 2 }}>
        {detectorLabel(alert.detector)} — <span className="mono">{alert.entity_id}</span>
      </div>
      <div className="mono dim" style={{ fontSize: 10 }}>
        Score {formatScore(alert.anomaly_score)} · {alert.detector} · {timeAgo(alert.window)}
      </div>
    </div>
  )
}

export default function NotificationStack() {
  const { notifications, dismissNotification } = useAlerts()

  if (!notifications.length) return null

  return (
    <div style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 200, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {notifications.map(alert => (
        <Toast key={alert.id} alert={alert} onDismiss={dismissNotification} />
      ))}
    </div>
  )
}
