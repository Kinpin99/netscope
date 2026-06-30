import { useState, useEffect } from 'react'
import { useSystem } from '../../context/SystemContext'

const phaseColors = {
  inference: 'var(--accent)',
  training: 'var(--sev-medium)',
  observation: 'var(--text-dim)',
}

export default function Topbar() {
  const { status } = useSystem()
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  // reset tick when status refreshes
  useEffect(() => { setTick(0) }, [status])

  const phase = status?.phase || 'observation'
  const dotColor = phaseColors[phase]

  return (
    <header style={{
      height: 'var(--topbar-h)', background: 'var(--panel)',
      borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 22px', flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--text-dim)' }}>
        <span className="pulse" style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor, display: 'inline-block' }} />
        <span>{phase === 'inference' ? 'Live' : phase === 'training' ? 'Training' : 'Observing'} · {tick}s ago</span>
      </div>
      <div className="mono dim" style={{ fontSize: 10 }}>
        {status ? `v${status.models_version}` : ''}
      </div>
    </header>
  )
}
