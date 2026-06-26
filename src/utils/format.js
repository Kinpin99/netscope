// severity string → css variable
export const severityColor = (severity) => {
  const map = {
    critical: 'var(--sev-critical)',
    high:     'var(--sev-high)',
    medium:   'var(--sev-medium)',
    low:      'var(--sev-low)',
    info:     'var(--sev-info)',
  }
  return map[severity] ?? 'var(--sev-unknown)'
}

// health score (0-100 int or null) → css variable
export const healthColor = (score) => {
  if (score === null || score === undefined) return 'var(--sev-unknown)'
  if (score >= 90) return 'var(--accent)'
  if (score >= 78) return 'var(--sev-low)'
  if (score >= 65) return 'var(--sev-medium)'
  if (score >= 50) return 'var(--sev-high)'
  return 'var(--sev-critical)'
}

// anomaly score (0.0-1.0 float) → severity string
export const scoreToSeverity = (score) => {
  if (score === null || score === undefined || isNaN(score)) return 'info'
  if (score >= 0.85) return 'critical'
  if (score >= 0.75) return 'high'
  if (score >= 0.65) return 'medium'
  if (score >= 0.55) return 'low'
  return 'info'
}

export const detectorLabel = (detector) => ({
  bandwidth:       'Bandwidth',
  portscan:        'Port Scan',
  device_behavior: 'Device Behaviour',
  protocol:        'Protocol',
}[detector] ?? detector)

export const issueTypeLabel = (type) => ({
  network_congestion:    'Network Congestion',
  device_capacity:       'Device Capacity',
  connectivity_security: 'Connectivity Security',
  device_environment:    'Device Environment',
  network_performance:   'Network Performance',
}[type] ?? type)

export const formatBytes = (bytes) => {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`
  return `${bytes} B`
}

export const formatBps = (bps) => {
  if (bps === null || bps === undefined) return '—'
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(1)} Kbps`
  return `${bps} bps`
}

export const timeAgo = (epoch) => {
  const diff = Math.floor(Date.now() / 1000 - epoch)
  if (diff < 60)    return `${diff}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export const formatTime = (epoch) =>
  new Date(epoch * 1000).toLocaleTimeString('en-GB', { hour12: false })

export const formatScore = (score) =>
  score === null || score === undefined ? '—' : score.toFixed(3)
