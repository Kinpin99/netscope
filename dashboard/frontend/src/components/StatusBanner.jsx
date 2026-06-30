import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useSystem } from '../context/SystemContext'
import { postRetrain } from '../api/system'

export default function StatusBanner() {
  const { status } = useSystem()
  const [retraining, setRetraining] = useState(false)
  const [retrainError, setRetrainError] = useState(null)

  if (!status) return null

  const handleRetrain = async () => {
    setRetraining(true)
    setRetrainError(null)
    try {
      await postRetrain()
    } catch (err) {
      setRetrainError(err.message?.includes('409') ? 'Training already in progress' : err.message)
    } finally {
      setRetraining(false)
    }
  }

  // observation phase
  if (status.phase === 'observation') {
    const obs = status.observation
    const dayPct = Math.min(100, (obs.days_elapsed / obs.days_required) * 100)
    const recPct = Math.min(100, (obs.netflow_records / obs.records_required) * 100)
    const pct = Math.round(Math.min(dayPct, recPct))

    return (
      <div style={{ background: 'color-mix(in srgb, var(--sev-info) 8%, transparent)', borderBottom: '1px solid var(--border)', padding: '14px 22px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--sev-info)' }}>◐ OBSERVATION PHASE</span>
          <span className="dim" style={{ fontSize: 11 }}>· Collecting training data</span>
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
          {obs.days_elapsed.toFixed(1)} / {obs.days_required} days · {obs.netflow_records.toLocaleString()} / {obs.records_required.toLocaleString()} records
        </div>
        <div style={{ height: 4, borderRadius: 2, background: 'var(--border)', marginBottom: 8 }}>
          <div style={{ height: '100%', borderRadius: 2, width: `${pct}%`, background: 'var(--sev-info)', transition: 'width 0.3s' }} />
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          Health scores and alerts are unavailable until training completes. The system will train automatically.
        </div>
      </div>
    )
  }

  // training phase
  if (status.phase === 'training') {
    return (
      <div style={{ background: 'color-mix(in srgb, var(--sev-medium) 8%, transparent)', borderBottom: '1px solid var(--border)', padding: '14px 22px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Loader2 size={14} color="var(--sev-medium)" style={{ animation: 'spin 1s linear infinite' }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--sev-medium)' }}>TRAINING IN PROGRESS</span>
          <span className="dim" style={{ fontSize: 11 }}>· Models are being trained. This typically takes a few minutes.</span>
        </div>
      </div>
    )
  }

  // inference — show banner only if retrain failed or models stale (>7 days)
  const daysSince = status.last_retrain_at ? (Date.now() / 1000 - status.last_retrain_at) / 86400 : null
  const stale = daysSince !== null && daysSince > 7
  const failed = status.last_training_result === 'failed'

  if (!stale && !failed) return null

  return (
    <div style={{ background: 'color-mix(in srgb, var(--sev-high) 8%, transparent)', borderBottom: '1px solid var(--border)', padding: '14px 22px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--sev-high)' }}>
            ⚠ {failed ? 'Last retrain: FAILED' : 'Models may be stale'}
          </span>
        </div>
        <div className="mono dim" style={{ fontSize: 11 }}>
          Version {status.models_version} · Trained {daysSince ? `${Math.round(daysSince)} days ago` : 'never'}
        </div>
        {retrainError && <div style={{ fontSize: 11, color: 'var(--sev-critical)', marginTop: 4 }}>{retrainError}</div>}
      </div>
      <button
        onClick={handleRetrain}
        disabled={retraining}
        style={{
          padding: '6px 14px', fontSize: 11, fontWeight: 600,
          border: '1px solid var(--border)', borderRadius: 'var(--radius)',
          background: 'var(--panel)', color: 'var(--text)',
          opacity: retraining ? 0.5 : 1,
          display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        {retraining && <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} />}
        Retrain Now
      </button>
    </div>
  )
}
