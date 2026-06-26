const now = Math.floor(Date.now() / 1000)
const ips = ['10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.1.1', '10.0.1.2', '10.0.1.3', '10.0.1.4', '10.0.2.1']

export const generateTrafficRecent = (minutes = 30) => {
  const windowSec = 60
  const count = minutes
  const devices = {}

  ips.forEach((ip) => {
    const base = 500000 + Math.random() * 2000000
    devices[ip] = Array.from({ length: count }, (_, i) => {
      const t = now - (count - i) * windowSec
      const dayCurve = 0.6 + 0.4 * Math.sin((i / count) * Math.PI)
      const noise = () => (Math.random() - 0.5) * 0.2

      let bytesIn = base * dayCurve * (1 + noise())
      let bytesOut = base * 0.7 * dayCurve * (1 + noise())

      // inject a spike at ~window 40% through for 10.0.0.3
      if (ip === '10.0.0.3' && Math.abs(i / count - 0.4) < 0.05) {
        bytesIn *= 4.5
      }

      return {
        window: t,
        bytes_in: Math.round(bytesIn),
        bytes_out: Math.round(bytesOut),
        packets_in: Math.round(bytesIn / 1400),
        packets_out: Math.round(bytesOut / 1400),
      }
    })
  })

  return { window_sec: windowSec, devices }
}

export const mockLiveScores = [
  { detector: 'portscan', entity_id: '10.0.1.4', window: now - 30, anomaly_score: 0.91, profile_used: 'global' },
  { detector: 'bandwidth', entity_id: '10.0.0.3', window: now - 30, anomaly_score: 0.72, profile_used: 'global' },
  { detector: 'device_behavior', entity_id: '10.0.1.2', window: now - 30, anomaly_score: 0.64, profile_used: 'per_device' },
  { detector: 'protocol', entity_id: '10.0.0.1', window: now - 30, anomaly_score: 0.48, profile_used: 'global' },
  { detector: 'bandwidth', entity_id: '10.0.2.1', window: now - 30, anomaly_score: 0.33, profile_used: 'global' },
]
